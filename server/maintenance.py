"""Read diagnostics and manual compaction primitives for the viz server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


MAINTENANCE_TABLES = (
    "memories",
    "memory_edges",
    "memory_conflicts",
    "memory_conflicts_archive",
)
COMPACTION_BACKUP_PREFIX = "lancedb-pre-compact-"
COMPACTION_BACKUPS_TO_KEEP = 2
COMPACTION_COOLDOWN = timedelta(seconds=_positive_int_env(
    "LANCEDB_MAINTENANCE_COOLDOWN_SECONDS", 60 * 60
))
ROUTINE_HISTORY_RETENTION = timedelta(seconds=_positive_int_env(
    "LANCEDB_ROUTINE_RETENTION_SECONDS", 24 * 60 * 60
))
CLEANUP_INTERVAL = timedelta(seconds=_positive_int_env(
    "LANCEDB_MAINTENANCE_CLEANUP_INTERVAL_SECONDS", 24 * 60 * 60
))
MAINTENANCE_MAX_FRAGMENTS = 64
MAINTENANCE_STATE_FILE = ".lancedb-maintenance-state.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | str) -> datetime:
    # LanceDB's list_versions() and legacy datetime.now() state use naive LOCAL
    # timestamps. astimezone handles those as well as new explicit UTC state.
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(timezone.utc)


def _history_diagnostics(table: Any, instant: datetime) -> dict[str, Any]:
    versions = table.list_versions()
    result = {"versions": len(versions), "cleanup_eligible_versions": None,
              "next_history_expiration_at": None}
    try:
        tagged = {int(tag["version"]) for tag in table.tags.list().values()}
        current = int(table.version)
        expirations = [
            _utc(version["timestamp"]) + ROUTINE_HISTORY_RETENTION
            for version in versions
            if int(version["version"]) != current and int(version["version"]) not in tagged
        ]
        result["cleanup_eligible_versions"] = sum(value < instant for value in expirations)
        future = [value for value in expirations if value >= instant]
        if future:
            result["next_history_expiration_at"] = min(future).isoformat()
        result["tagged_versions"] = len(tagged)
    except Exception as error:
        # Unknown reachability/age must never authorize cleanup.
        result["cleanup_diagnostics_error"] = str(error)[:300]
    return result


def _table_maintenance_state(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if "table_maintenance" in state:
        return {name: dict(entry) for name, entry in state["table_maintenance"].items()}
    if not state.get("last_success_at"):
        return {}
    # Legacy optimize handled every table and both operations. Migrate ALL of
    # them before a partial run, so untouched tables keep their old cooldowns.
    return {name: {
        "last_cleanup_at": state["last_success_at"],
        "last_compact_at": state["last_success_at"],
        "compacted_version": (state.get("table_versions") or {}).get(name),
    } for name in MAINTENANCE_TABLES}


def _routine_actions(
    diagnostics: dict[str, Any], state: dict[str, Any], instant: datetime,
) -> tuple[dict[str, list[str]], list[str], dict[str, str], str | None]:
    actions, reasons, deferred, upcoming = {}, [], {}, []
    table_state = _table_maintenance_state(state)
    for name, table in (diagnostics.get("tables") or {}).items():
        if name not in MAINTENANCE_TABLES or not isinstance(table, dict):
            continue
        previous = table_state.get(name) or {}
        selected = []
        for action, needed, interval in (
            ("cleanup", (table.get("cleanup_eligible_versions") or 0) > 0, CLEANUP_INTERVAL),
            ("compact", (table.get("fragments") or 0) > MAINTENANCE_MAX_FRAGMENTS, COMPACTION_COOLDOWN),
        ):
            last = previous.get(f"last_{action}_at")
            ready_at = instant
            if last:
                try:
                    ready_at = _utc(last) + interval
                except (ValueError, TypeError, AttributeError):
                    pass
            if not needed:
                if action == "cleanup" and table.get("next_history_expiration_at"):
                    upcoming.append(max(ready_at, _utc(table["next_history_expiration_at"])))
                continue
            metric = "cleanup_eligible_versions" if action == "cleanup" else "fragments"
            reasons.append(f"{name}.{metric}={table[metric]}")
            if ready_at > instant:
                deferred[f"{name}.{action}"] = "cooldown"
                upcoming.append(ready_at)
                continue
            compacted_version = previous.get("compacted_version")
            if action == "compact" and compacted_version is not None and compacted_version == table.get("current_version"):
                deferred[f"{name}.{action}"] = "unchanged_since_last_success"
                continue
            selected.append(action)
        if selected:
            actions[name] = selected
    next_check = min(upcoming).isoformat() if upcoming else None
    return actions, reasons, deferred, next_check


def maintenance_lock_path(db_path: Path) -> Path:
    """Return the lock shared with `LanceDBStore.write_batch()`."""
    db_path = Path(db_path).resolve()
    return db_path / ".write.lock"


def maintenance_state_path(backups_path: Path) -> Path:
    """Keep maintenance accounting outside the measured database directory."""
    return Path(backups_path) / MAINTENANCE_STATE_FILE


def _read_maintenance_state(backups_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(maintenance_state_path(backups_path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_maintenance_state(backups_path: Path, value: dict[str, Any]) -> None:
    path = maintenance_state_path(backups_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _active_index_uuids(table: Any) -> set[str] | None:
    """Return index UUIDs referenced by current Lance metadata, or None.

    ``table.list_indices()`` is tried first because it is the only path that
    works without ``pylance``: the production container has no ``lance``
    module, so ``to_lance()`` raises there and every caller of this helper
    would silently get ``None`` (no orphan detection, no bounded trigger).
    Measured on a real database: both paths return the same active set.
    """
    indices = None
    try:
        indices = list(table.list_indices())
    except Exception:
        indices = None
    if indices is None:
        try:
            dataset = table.to_lance()
            if hasattr(dataset, "describe_indices"):
                descriptions = list(dataset.describe_indices())
                segment_lists = [
                    _value(description, "segments") for description in descriptions
                ]
                if descriptions and any(segments is None for segments in segment_lists):
                    indices = dataset.list_indices()
                else:
                    indices = [
                        segment
                        for segments in segment_lists
                        for segment in (segments or [])
                    ]
            else:
                indices = dataset.list_indices()
        except Exception:
            return None
    return _uuid_set(indices)


def _uuid_set(indices: Any) -> set[str] | None:
    """Collect normalized UUIDs from index descriptors, or None when unreadable."""
    active = set()
    for index in indices:
        value = _value(index, "uuid")
        if value is None:
            value = _value(index, "index_uuid")
        if value is None:
            return None
        try:
            active.add(str(UUID(str(value))))
        except (TypeError, ValueError, AttributeError):
            return None
    return active


def _physical_index_uuids(db_path: Path, table_name: str) -> set[str]:
    """List UUID-shaped physical index directories without opening the table."""
    root = Path(db_path) / f"{table_name}.lance" / "_indices"
    if not root.is_dir():
        return set()
    found = set()
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            found.add(str(UUID(path.name)))
        except (TypeError, ValueError, AttributeError):
            continue
    return found


def _orphan_index_uuids(
    db_path: Path,
    table_name: str,
    table: Any,
) -> set[str] | None:
    active = _active_index_uuids(table)
    if active is None:
        return None
    return _physical_index_uuids(db_path, table_name) - active


def _stat_value(stats: Any, key: str, default: int = 0) -> int:
    """Read one table statistic across LanceDB versions.

    LanceDB 0.30.2 (the container) returns a plain dict from ``table.stats()``,
    while 0.34.0 returns an object with attributes. Support both.
    """
    if stats is None:
        return default
    if isinstance(stats, dict):
        value = stats.get(key, default)
    else:
        value = getattr(stats, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fragment_count(table: Any) -> int | None:
    """Count table fragments, or None when the API is unavailable."""
    try:
        return len(table.to_lance().get_fragments())
    except Exception:
        pass
    stats = None
    try:
        stats = table.stats()
    except Exception:
        return None
    if isinstance(stats, dict):
        fragment_stats = stats.get("fragment_stats") or {}
        if isinstance(fragment_stats, dict):
            count = fragment_stats.get("num_fragments")
            if count is not None:
                try:
                    return int(count)
                except (TypeError, ValueError):
                    return None
    return None


def _fts_index_stats(table: Any) -> dict[str, int] | None:
    """Return FTS row counts, or None when unavailable.

    LanceDB 0.34.0 exposes ``num_indexed_rows``/``num_unindexed_rows`` directly on
    the object returned by ``list_indices()``; 0.30.2 exposes those counts only
    through ``table.index_stats(<name>)``. Never invent the numbers: return None
    when neither path works.
    """
    try:
        indices = list(table.list_indices())
    except Exception:
        return None
    for index in indices:
        try:
            if str(index.index_type).upper() != "FTS" or list(index.columns) != ["content"]:
                continue
        except Exception:
            continue
        indexed = getattr(index, "num_indexed_rows", None)
        unindexed = getattr(index, "num_unindexed_rows", None)
        if indexed is not None and unindexed is not None:
            return {"num_indexed_rows": int(indexed), "num_unindexed_rows": int(unindexed)}
        name = getattr(index, "name", None)
        if not name:
            continue
        try:
            statistics = table.index_stats(name)
            return {
                "num_indexed_rows": int(statistics.num_indexed_rows),
                "num_unindexed_rows": int(statistics.num_unindexed_rows),
            }
        except Exception:
            return None
    return None


def directory_size(path: Path) -> int:
    """Return bytes occupied by regular files below ``path``."""
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _timestamped_backup_path(
    backups_path: Path,
    now: Callable[[], datetime],
) -> Path:
    stamp = now().strftime("%Y%m%d-%H%M%S")
    return backups_path / f"{COMPACTION_BACKUP_PREFIX}{stamp}"


def _compaction_backups(backups_path: Path) -> list[Path]:
    if not backups_path.is_dir():
        return []
    candidates = [
        path for path in backups_path.iterdir()
        if path.is_dir() and path.name.startswith(COMPACTION_BACKUP_PREFIX)
    ]
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def compaction_plan(
    db_path: Path,
    backups_path: Path,
    *,
    estimated_after_bytes: int,
    diagnostics: dict[str, Any] | None = None,
    mode: str = "routine",
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Describe the exact next backup and retention action without writing."""
    if mode not in {"routine", "reclaim"}:
        raise ValueError("mode must be routine or reclaim")
    db_path = Path(db_path)
    backups_path = Path(backups_path)
    backup_to_create = _timestamped_backup_path(backups_path, now)
    existing = _compaction_backups(backups_path)
    delete_count = max(0, len(existing) + 1 - COMPACTION_BACKUPS_TO_KEEP)
    diagnostics = diagnostics or {}
    state = _read_maintenance_state(backups_path)
    actions, trigger_reasons, deferred, next_eligible_at = _routine_actions(
        diagnostics, state, _utc(now())
    )
    if mode == "reclaim":
        actions = {name: ["optimize"] for name in MAINTENANCE_TABLES}
        trigger_reasons, deferred, next_eligible_at = ["explicit_reclaim"], {}, None
    recommended = bool(actions)
    deferred_reason = next(iter(deferred.values()), None) if not recommended else None
    database_bytes = directory_size(db_path)
    overhead_bytes = max(0, database_bytes - max(0, int(estimated_after_bytes)))

    managed_backup_bytes = sum(directory_size(path) for path in existing)
    return {
        "manual": mode == "reclaim",
        "mode": mode,
        "recommended": recommended,
        "deferred_reason": deferred_reason,
        "trigger_reasons": trigger_reasons,
        "actions": actions,
        "deferred_actions": deferred,
        "next_eligible_at": next_eligible_at,
        "thresholds": {
            "max_fragments": MAINTENANCE_MAX_FRAGMENTS,
            "cooldown_seconds": int(COMPACTION_COOLDOWN.total_seconds()),
            "cleanup_interval_seconds": int(CLEANUP_INTERVAL.total_seconds()),
        },
        "history_retention_seconds": (
            int(ROUTINE_HISTORY_RETENTION.total_seconds()) if mode == "routine" else 0
        ),
        "size_before_bytes": database_bytes,
        "database_bytes": database_bytes,
        "managed_backup_bytes": managed_backup_bytes,
        "total_footprint_bytes": database_bytes + managed_backup_bytes,
        "reclaimable_bytes_estimate": None,
        "estimated_after_bytes": None,
        "overhead_bytes_estimate": overhead_bytes,
        "estimate_note": "Overhead includes protected history and indexes; reclaimable bytes are unknown.",
        "backup_to_create": str(backup_to_create) if recommended else None,
        "backups_to_delete": [str(path) for path in existing[:delete_count]] if recommended else [],
        "retained_backup_count": COMPACTION_BACKUPS_TO_KEEP,
        "last_maintenance": state or None,
        "concurrency_warning": (
            "Cooperating LanceDBStore writers share an advisory lock with compaction; "
            "pause any raw external writer that does not use the store batch API."
        ),
    }


def _failure(step: str, error: str, started: float, **details) -> dict[str, Any]:
    return {
        "success": False,
        "failed_step": step,
        "error": error,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        **details,
    }


def compact_lancedb(
    db_path: Path,
    backups_path: Path,
    *,
    mode: str = "routine",
    disk_usage: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
    connect: Callable[[str], Any] | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Acquire the writer lock, then run backup-first compaction."""
    started = time.perf_counter()
    if mode not in {"routine", "reclaim"}:
        return _failure("preflight", "mode must be routine or reclaim", started)
    resolved_db_path = Path(db_path).resolve()
    if not resolved_db_path.is_dir():
        return _failure(
            "preflight", f"database directory does not exist: {resolved_db_path}", started
        )
    lock_file = None
    try:
        lock_file = maintenance_lock_path(resolved_db_path).open("a+")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if lock_file is not None:
            lock_file.close()
        return _failure(
            "lock",
            "another cooperating writer or compaction holds the database lock",
            started,
            code="maintenance_lock_busy",
        )
    except Exception as error:
        if lock_file is not None:
            lock_file.close()
        return _failure("lock", f"maintenance lock failed: {error}", started)
    try:
        return _compact_lancedb_locked(
            resolved_db_path,
            backups_path,
            mode=mode,
            disk_usage=disk_usage,
            connect=connect,
            now=now,
            started=started,
        )
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def _compact_lancedb_locked(
    db_path: Path,
    backups_path: Path,
    *,
    mode: str = "routine",
    disk_usage: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
    connect: Callable[[str], Any] | None = None,
    now: Callable[[], datetime] = _utc_now,
    started: float | None = None,
) -> dict[str, Any]:
    """Back up, verify, compact with explicit retention, then verify tables."""
    if started is None:
        started = time.perf_counter()
    db_path = Path(db_path).resolve()
    backups_path = Path(backups_path).resolve()
    if not db_path.is_dir():
        return _failure("preflight", f"database directory does not exist: {db_path}", started)
    try:
        backups_path.relative_to(db_path)
    except ValueError:
        pass
    else:
        return _failure("preflight", "backup directory must be outside the database", started)

    if connect is None:
        import lancedb
        connect = lancedb.connect
    try:
        database = connect(str(db_path))
        available = set(database.list_tables().tables)
        missing = [name for name in MAINTENANCE_TABLES if name not in available]
        if missing:
            return _failure(
                "table_precheck", f"required tables are missing: {', '.join(missing)}", started
            )
        tables = {name: database.open_table(name) for name in MAINTENANCE_TABLES}
        rows = {name: {"before": int(table.count_rows())} for name, table in tables.items()}
        samples = {}
        for name, table in tables.items():
            columns = [column for column in ("id", "content") if column in table.schema.names]
            samples[name] = (
                table.search().select(columns).limit(3).to_list() if columns else []
            )
    except Exception as error:
        return _failure("table_precheck", f"table precheck failed: {error}", started)

    actions = {name: ["optimize"] for name in MAINTENANCE_TABLES}
    datasets = {}
    if mode == "routine":
        try:
            diagnostics = {"tables": {
                name: {"current_version": int(table.version),
                       "fragments": _fragment_count(table),
                       **_history_diagnostics(table, _utc(now()))}
                for name, table in tables.items()
            }}
            plan = compaction_plan(db_path, backups_path, estimated_after_bytes=0,
                                   diagnostics=diagnostics, mode=mode, now=now)
            actions = plan["actions"]
            if not actions:
                return {"success": True, "action": "not_needed", "mode": mode,
                        "actions": {}, "plan": plan, "backup_created": None,
                        "backups_deleted": [],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2)}
            # Public split operations require pylance. Refuse before backup if
            # deployment is incomplete; never fall back to full optimize().
            datasets = {name: tables[name].to_lance() for name in actions}
        except Exception as error:
            return _failure("maintenance_plan", f"routine preflight failed: {error}", started)

    size_before = directory_size(db_path)
    backup_usage_path = backups_path if backups_path.exists() else backups_path.parent
    try:
        backup_free_bytes = int(disk_usage(backup_usage_path).free)
        database_free_bytes = int(disk_usage(db_path).free)
        same_filesystem = os.stat(backup_usage_path).st_dev == os.stat(db_path).st_dev
    except Exception as error:
        return _failure("disk_space", f"disk-space check failed: {error}", started)
    required_backup_bytes = size_before
    required_scratch_bytes = size_before
    if same_filesystem:
        required_combined_bytes = required_backup_bytes + required_scratch_bytes
        enough_space = backup_free_bytes >= required_combined_bytes
    else:
        required_combined_bytes = None
        enough_space = (
            backup_free_bytes >= required_backup_bytes
            and database_free_bytes >= required_scratch_bytes
        )
    if not enough_space:
        return _failure(
            "disk_space",
            "insufficient disk space for backup plus compaction scratch",
            started,
            size_before_bytes=size_before,
            same_filesystem=same_filesystem,
            backup_free_bytes=backup_free_bytes,
            database_free_bytes=database_free_bytes,
            required_combined_bytes=required_combined_bytes,
            required_backup_bytes=required_backup_bytes,
            required_scratch_bytes=required_scratch_bytes,
        )

    backup_bytes_before = sum(directory_size(path) for path in _compaction_backups(backups_path))
    backup_created = _timestamped_backup_path(backups_path, now)
    temp_backup = backups_path / f".{backup_created.name}.tmp-{uuid4().hex}"
    if backup_created.exists():
        return _failure("backup", f"backup already exists: {backup_created}", started)
    try:
        backups_path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(db_path, temp_backup, symlinks=True, copy_function=shutil.copy2)
        os.replace(temp_backup, backup_created)
    except Exception as error:
        if temp_backup.exists():
            shutil.rmtree(temp_backup)
        return _failure("backup", f"backup failed: {error}", started)

    try:
        backup_database = connect(str(backup_created))
        backup_available = set(backup_database.list_tables().tables)
        for name in MAINTENANCE_TABLES:
            if name not in backup_available:
                raise ValueError(f"required table missing from backup: {name}")
            backup_table = backup_database.open_table(name)
            if int(backup_table.count_rows()) != rows[name]["before"]:
                raise ValueError(f"row count mismatch in backup table: {name}")
            columns = [column for column in ("id", "content") if column in backup_table.schema.names]
            backup_sample = (
                backup_table.search().select(columns).limit(3).to_list() if columns else []
            )
            if backup_sample != samples[name]:
                raise ValueError(f"key content mismatch in backup table: {name}")
    except Exception as error:
        return _failure(
            "backup_verify", f"backup verification failed: {error}", started,
            backup_created=str(backup_created), backups_deleted=[],
        )

    backups_deleted = []
    try:
        candidates = _compaction_backups(backups_path)
        for old_backup in candidates[:-COMPACTION_BACKUPS_TO_KEEP]:
            shutil.rmtree(old_backup)
            backups_deleted.append(str(old_backup))
    except Exception as error:
        return _failure(
            "backup_retention", f"backup retention failed: {error}", started,
            backup_created=str(backup_created), backups_deleted=backups_deleted,
        )

    compacted_tables = []
    operation_stats = {}
    cleanup_older_than = (
        ROUTINE_HISTORY_RETENTION if mode == "routine" else timedelta(seconds=0)
    )
    for name, selected in actions.items():
        operation_stats[name] = {}
        for action in selected:
            try:
                if action == "optimize":
                    tables[name].optimize(cleanup_older_than=cleanup_older_than)
                    stats = None
                elif action == "cleanup":
                    stats = datasets[name].cleanup_old_versions(
                        cleanup_older_than, error_if_tagged_old_versions=False,
                    )
                else:
                    stats = datasets[name].optimize.compact_files()
                fields = ("bytes_removed", "old_versions", "fragments_removed",
                          "fragments_added", "files_removed", "files_added")
                operation_stats[name][action] = {
                    field: int(getattr(stats, field)) for field in fields if hasattr(stats, field)
                }
            except Exception as error:
                return _failure(
                    f"{action}.{name}", f"{action} failed for {name}: {error}", started,
                    backup_created=str(backup_created), backups_deleted=backups_deleted,
                    compacted_tables=compacted_tables, actions=actions,
                    operation_stats=operation_stats, rows=rows,
                )
        compacted_tables.append(name)

    # A LanceDB handle can cache the version preceding a pylance mutation.
    try:
        database = connect(str(db_path))
    except Exception as error:
        return _failure(
            "verify.connect", f"post-maintenance reconnect failed: {error}", started,
            backup_created=str(backup_created), backups_deleted=backups_deleted,
            actions=actions, operation_stats=operation_stats, rows=rows,
            compacted_tables=compacted_tables,
        )
    # A directory absent from the current version can still belong to a retained
    # or tagged snapshot. Lance owns reachability; manual deletion is unsafe.
    orphan_index_directories_removed = {name: None for name in MAINTENANCE_TABLES}

    fts_unindexed = None
    for name in MAINTENANCE_TABLES:
        try:
            table = database.open_table(name)
            after = int(table.count_rows())
            readable_version = int(table.version)
            rows[name].update({
                "after": after,
                "version": readable_version,
                "version_readable": True,
            })
            active_uuids = _active_index_uuids(table)
            if active_uuids is not None:
                physical_uuids = _physical_index_uuids(db_path, name)
                missing_active = active_uuids - physical_uuids
                if missing_active:
                    return _failure(
                        f"verify.{name}.indices",
                        (
                            f"index directory verification failed for {name}: "
                            f"missing_active={sorted(missing_active)}"
                        ),
                        started,
                        backup_created=str(backup_created),
                        backups_deleted=backups_deleted,
                        compacted_tables=compacted_tables,
                        orphan_index_directories_removed=orphan_index_directories_removed,
                        rows=rows,
                    )
            if after != rows[name]["before"]:
                return _failure(
                    f"verify.{name}.rows",
                    f"row count changed for {name}: {rows[name]['before']} -> {after}",
                    started,
                    backup_created=str(backup_created), backups_deleted=backups_deleted,
                    compacted_tables=compacted_tables, rows=rows,
                )
            if name == "memories":
                fts_stats = _fts_index_stats(table)
                if fts_stats is None:
                    return _failure(
                        "verify.memories.fts", "content FTS index is missing", started,
                        backup_created=str(backup_created), backups_deleted=backups_deleted,
                        compacted_tables=compacted_tables, rows=rows,
                    )
                fts_unindexed = fts_stats["num_unindexed_rows"]
                if mode == "reclaim" and fts_unindexed != 0:
                    return _failure(
                        "verify.memories.fts",
                        f"content FTS has {fts_unindexed} unindexed rows", started,
                        backup_created=str(backup_created), backups_deleted=backups_deleted,
                        compacted_tables=compacted_tables, rows=rows,
                        fts_num_unindexed_rows=fts_unindexed,
                    )
        except Exception as error:
            return _failure(
                f"verify.{name}", f"post-compaction verification failed for {name}: {error}",
                started, backup_created=str(backup_created), backups_deleted=backups_deleted,
                compacted_tables=compacted_tables, rows=rows,
            )

    size_after = directory_size(db_path)
    completed_at = _utc(now())
    previous_state = _read_maintenance_state(backups_path)
    table_maintenance = _table_maintenance_state(previous_state)
    for name, selected in actions.items():
        entry = dict(table_maintenance.get(name) or {})
        for action in selected:
            if action in {"cleanup", "optimize"}:
                entry["last_cleanup_at"] = completed_at.isoformat()
            if action in {"compact", "optimize"}:
                entry["last_compact_at"] = completed_at.isoformat()
                entry["compacted_version"] = int(rows[name]["version"])
        table_maintenance[name] = entry
    managed_backup_bytes = sum(
        directory_size(path) for path in _compaction_backups(backups_path)
    )
    delta = size_after - size_before
    footprint_before = size_before + backup_bytes_before
    footprint_after = size_after + managed_backup_bytes
    accounting = {
        "database_delta_bytes": delta,
        "actual_growth_bytes": max(0, delta),
        "total_footprint_bytes_before": footprint_before,
        "total_footprint_bytes_after": footprint_after,
        "total_footprint_delta_bytes": footprint_after - footprint_before,
    }
    state = {
        **accounting,
        "table_maintenance": table_maintenance,
        "actions": actions,
        "operation_stats": operation_stats,
        "last_success_at": completed_at.isoformat(),
        "mode": mode,
        "history_retention_seconds": int(cleanup_older_than.total_seconds()),
        "table_versions": {name: int(rows[name]["version"]) for name in MAINTENANCE_TABLES},
        "database_bytes_before": size_before,
        "database_bytes_after": size_after,
        "actual_reclaimed_bytes": max(0, size_before - size_after),
        "backup_created_bytes": directory_size(backup_created),
    }
    try:
        _write_maintenance_state(backups_path, state)
    except Exception as error:
        return _failure(
            "maintenance_state", f"maintenance state write failed: {error}", started,
            backup_created=str(backup_created), backups_deleted=backups_deleted,
            compacted_tables=compacted_tables, rows=rows,
        )
    return {
        "success": True,
        "action": "maintained" if mode == "routine" else "compacted",
        "actions": actions,
        "operation_stats": operation_stats,
        **accounting,
        "mode": mode,
        "history_retention_seconds": int(cleanup_older_than.total_seconds()),
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "actual_reclaimed_bytes": max(0, size_before - size_after),
        "managed_backup_bytes": managed_backup_bytes,
        "total_footprint_bytes": size_after + managed_backup_bytes,
        "completed_at": completed_at.isoformat(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "backup_created": str(backup_created),
        "backups_deleted": backups_deleted,
        "rows": rows,
        "compacted_tables": compacted_tables,
        "orphan_index_directories_removed": orphan_index_directories_removed,
        "manual_index_cleanup": "skipped_unproven_snapshot_reachability",
        "fts_num_unindexed_rows": fts_unindexed,
        "manual_concurrency_limit": (
            "Cooperating store writers are locked; pause raw external writers."
        ),
    }


def probe_ollama(embed_url: str, timeout: float = 2.0) -> dict[str, Any]:
    """Probe Ollama without embedding text or mutating model state."""
    parsed = urlsplit(embed_url)
    tags_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
    try:
        request = Request(tags_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            return {"state": "ok", "status": response.status}
    except Exception as error:
        return {"state": "error", "error": str(error)[:300]}


def collect_health_diagnostics(
    db_path: Path,
    database,
    *,
    backups_path: Path | None = None,
    pipeline: dict[str, Any],
    implementation: dict[str, Any] | None = None,
    ollama_probe: Callable[[], dict[str, Any]],
    table_names: Iterable[str] = MAINTENANCE_TABLES,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Collect bounded, read-only health and pre-maintenance estimates."""
    db_path = Path(db_path)
    available = set(database.list_tables().tables)
    selected = [name for name in table_names if name in available]
    tables: dict[str, dict[str, Any]] = {}
    useful_bytes = 0
    fts = {
        "state": "missing",
        "num_indexed_rows": 0,
        "num_unindexed_rows": None,
        "active_index_directories": None,
        "physical_index_directories": 0,
        "orphan_index_directories": None,
    }

    for name in selected:
        try:
            table = database.open_table(name)
            stats = table.stats()
            table_bytes = _stat_value(stats, "total_bytes")
            useful_bytes += table_bytes
            fragments = _fragment_count(table)
            tables[name] = {
                "state": "ok",
                "rows": int(table.count_rows()),
                "current_version": int(table.version),
                **_history_diagnostics(table, _utc(now())),
                "fragments": fragments,
                "useful_bytes": table_bytes,
            }
            if name == "memories":
                fts_stats = _fts_index_stats(table)
                active_uuids = _active_index_uuids(table)
                physical_uuids = _physical_index_uuids(db_path, name)
                orphan_uuids = (
                    None if active_uuids is None else physical_uuids - active_uuids
                )
                if fts_stats is None:
                    fts = {
                        "state": "missing",
                        "num_indexed_rows": 0,
                        "num_unindexed_rows": None,
                        "active_index_directories": (
                            None if active_uuids is None else len(active_uuids)
                        ),
                        "physical_index_directories": len(physical_uuids),
                        "orphan_index_directories": (
                            None if orphan_uuids is None else len(orphan_uuids)
                        ),
                    }
                else:
                    fts = {
                        "state": "current" if fts_stats["num_unindexed_rows"] == 0 else "lagging",
                        "num_indexed_rows": fts_stats["num_indexed_rows"],
                        "num_unindexed_rows": fts_stats["num_unindexed_rows"],
                        "active_index_directories": (
                            None if active_uuids is None else len(active_uuids)
                        ),
                        "physical_index_directories": len(physical_uuids),
                        "orphan_index_directories": (
                            None if orphan_uuids is None else len(orphan_uuids)
                        ),
                    }
        except Exception as error:
            tables[name] = {"state": "error", "error": str(error)[:300]}

    index_root = db_path / "memories.lance" / "_indices"
    index_sizes = {uuid: directory_size(index_root / uuid)
                   for uuid in _physical_index_uuids(db_path, "memories")}
    fts["empty_index_directories"] = sum(size == 0 for size in index_sizes.values())
    fts["nonempty_index_directories"] = sum(size > 0 for size in index_sizes.values())
    fts["index_bytes"] = sum(index_sizes.values())
    # Legacy 'orphan' means absent from CURRENT metadata, not safe to delete.
    fts["noncurrent_index_directories"] = fts["orphan_index_directories"]
    disk_bytes = directory_size(db_path)
    history_bytes = max(0, disk_bytes - useful_bytes)
    backups_path = Path(backups_path) if backups_path is not None else db_path.parent / "backups"
    managed_backup_bytes = sum(
        directory_size(path) for path in _compaction_backups(backups_path)
    )
    maintenance_state = _read_maintenance_state(backups_path)
    ollama = ollama_probe()
    return {
        "read_only": True,
        "tables": tables,
        "fts": fts,
        "storage": {
            "database_bytes": disk_bytes,
            "managed_backup_bytes": managed_backup_bytes,
            "total_footprint_bytes": disk_bytes + managed_backup_bytes,
            "active_bytes_estimate": useful_bytes,
            "reclaimable_bytes_estimate": None,
            "overhead_bytes_estimate": history_bytes,
            "estimate_only": True,
            "disk_bytes": disk_bytes,
            "useful_bytes": useful_bytes,
            "history_bytes": history_bytes,
        },
        "ollama": ollama,
        "pipeline": dict(pipeline),
        "implementation": dict(implementation or {}),
        "maintenance_estimate": {
            "current_bytes": disk_bytes,
            "backup_bytes": disk_bytes,
            "estimated_after_bytes": None,
            "estimated_reclaimable_bytes": None,
            "overhead_bytes_estimate": history_bytes,
            "estimate_only": True,
        },
        "last_maintenance": maintenance_state or None,
        "budgets": {"tables": len(selected)},
    }
