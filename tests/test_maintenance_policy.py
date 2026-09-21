"""Regression coverage for retention-aware, per-table maintenance."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import lancedb
from lancedb.index import FTS
from lancedb.table import LanceTable

from server import maintenance


def create_database(path: Path, *, fragmented: bool = False):
    database = lancedb.connect(str(path))
    for name in maintenance.MAINTENANCE_TABLES:
        table = database.create_table(name, [{"id": "initial", "content": "indexedtoken"}])
        if name == "memories":
            table.create_index("content", config=FTS())
            if fragmented:
                for index in range(65):
                    table.add([{"id": str(index), "content": "unindexedtoken"}])
    return database


class MaintenancePolicyTests(unittest.TestCase):
    def test_local_version_timestamps_do_not_expire_history_early(self):
        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            instant = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
            created = instant - timedelta(hours=23)
            local_timestamp = datetime.fromtimestamp(created.timestamp())
            self.assertIsNone(local_timestamp.tzinfo)
            self.assertEqual(created, maintenance._utc(local_timestamp))
            table = SimpleNamespace(
                version=2,
                tags=SimpleNamespace(list=lambda: {}),
                list_versions=lambda: [
                    {"version": 1, "timestamp": local_timestamp},
                    {"version": 2, "timestamp": instant},
                ],
            )
            result = maintenance._history_diagnostics(table, instant)
            self.assertEqual(0, result["cleanup_eligible_versions"])
            self.assertEqual(
                (instant + timedelta(hours=1)).isoformat(),
                result["next_history_expiration_at"],
            )
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

    def test_unreadable_tags_do_not_authorize_cleanup(self):
        instant = datetime.now(timezone.utc)
        table = SimpleNamespace(
            version=2,
            tags=SimpleNamespace(list=Mock(side_effect=OSError("tags unavailable"))),
            list_versions=lambda: [
                {"version": 1, "timestamp": instant - timedelta(days=3)},
                {"version": 2, "timestamp": instant},
            ],
        )
        history = maintenance._history_diagnostics(table, instant)
        self.assertIsNone(history["cleanup_eligible_versions"])
        self.assertIn("tags unavailable", history["cleanup_diagnostics_error"])
        actions, _, _, _ = maintenance._routine_actions(
            {"tables": {"memories": {**history, "fragments": 1}}}, {}, instant,
        )
        self.assertEqual({}, actions)

    def test_cleanup_and_compaction_cooldowns_are_independent(self):
        instant = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        state = {"table_maintenance": {
            "memories": {
                "last_cleanup_at": (instant - timedelta(hours=2)).isoformat(),
                "last_compact_at": (instant - timedelta(hours=2)).isoformat(),
                "compacted_version": 9,
            },
            "memory_edges": {
                "last_cleanup_at": (instant - timedelta(days=2)).isoformat(),
                "last_compact_at": (instant - timedelta(minutes=30)).isoformat(),
                "compacted_version": 19,
            },
        }}
        diagnostics = {"tables": {
            "memories": {"cleanup_eligible_versions": 3, "fragments": 66,
                         "current_version": 10},
            "memory_edges": {"cleanup_eligible_versions": 3, "fragments": 66,
                             "current_version": 20},
        }}
        actions, _, deferred, next_check = maintenance._routine_actions(
            diagnostics, state, instant,
        )
        self.assertEqual({"memories": ["compact"], "memory_edges": ["cleanup"]}, actions)
        self.assertEqual({"memories.cleanup": "cooldown", "memory_edges.compact": "cooldown"}, deferred)
        self.assertEqual((instant + timedelta(minutes=30)).isoformat(), next_check)

    def test_next_cleanup_waits_for_both_expiration_and_cooldown(self):
        instant = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        state = {"table_maintenance": {"memories": {
            "last_cleanup_at": (instant - timedelta(hours=2)).isoformat(),
        }}}
        diagnostics = {"tables": {"memories": {
            "cleanup_eligible_versions": 0, "fragments": 1,
            "next_history_expiration_at": (instant + timedelta(hours=6)).isoformat(),
        }}}
        actions, _, _, next_check = maintenance._routine_actions(diagnostics, state, instant)
        self.assertEqual({}, actions)
        self.assertEqual((instant + timedelta(hours=22)).isoformat(), next_check)

    def test_compact_preserves_fts_snapshots_and_legacy_cooldowns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, backups = root / "db", root / "backups"
            database = create_database(path, fragmented=True)
            table = database.open_table("memories")
            previous_version = table.version
            self.assertEqual(66, len(table.to_lance().get_fragments()))
            instant = datetime.now(timezone.utc)
            legacy_success = (instant - timedelta(hours=2)).isoformat()
            backups.mkdir()
            maintenance.maintenance_state_path(backups).write_text(json.dumps({
                "last_success_at": legacy_success,
                "table_versions": {name: 1 for name in maintenance.MAINTENANCE_TABLES},
            }))

            result = maintenance.compact_lancedb(path, backups, now=lambda: instant)

            self.assertTrue(result["success"], result)
            self.assertEqual({"memories": ["compact"]}, result["actions"])
            latest = lancedb.connect(str(path)).open_table("memories")
            self.assertLess(len(latest.to_lance().get_fragments()), 66)
            self.assertGreater(latest.version, previous_version)
            self.assertEqual(66, latest.count_rows())
            self.assertEqual(1, len(latest.search("indexedtoken", query_type="fts").to_list()))
            self.assertEqual(65, len(latest.search("unindexedtoken", query_type="fts").limit(100).to_list()))
            latest.checkout(previous_version)
            self.assertEqual(66, latest.count_rows())
            self.assertEqual(65, len(latest.search("unindexedtoken", query_type="fts").limit(100).to_list()))

            state = json.loads(maintenance.maintenance_state_path(backups).read_text())
            self.assertEqual(result["rows"]["memories"]["version"],
                             state["table_maintenance"]["memories"]["compacted_version"])
            plan = maintenance.compaction_plan(path, backups, estimated_after_bytes=0,
                now=lambda: instant + timedelta(hours=1), diagnostics={"tables": {
                    "memory_edges": {"cleanup_eligible_versions": 1, "fragments": 1,
                                     "current_version": 1},
                }})
            self.assertFalse(plan["recommended"], plan)
            self.assertEqual("cooldown", plan["deferred_reason"])

    def test_missing_pylance_fails_before_backup_with_fragment_stats_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root / "db")
            with patch.object(LanceTable, "to_lance", side_effect=ImportError("pylance missing")), \
                 patch.object(LanceTable, "stats", return_value={"fragment_stats": {"num_fragments": 66}}), \
                 patch("server.maintenance.shutil.copytree") as copy:
                result = maintenance.compact_lancedb(root / "db", root / "backups")
            self.assertFalse(result["success"], result)
            self.assertEqual("maintenance_plan", result["failed_step"])
            self.assertIn("pylance missing", result["error"])
            copy.assert_not_called()
            self.assertFalse((root / "backups").exists())

    def test_post_mutation_reconnect_failure_reports_existing_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "db"
            create_database(path, fragmented=True)
            calls = 0

            def connect(location):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("reconnect unavailable")
                return lancedb.connect(location)

            result = maintenance.compact_lancedb(path, root / "backups", connect=connect)
            self.assertFalse(result["success"], result)
            self.assertEqual("verify.connect", result["failed_step"])
            self.assertIn("reconnect unavailable", result["error"])
            self.assertTrue(Path(result["backup_created"]).is_dir())
            self.assertEqual([], result["backups_deleted"])
            self.assertEqual(["memories"], result["compacted_tables"])
            self.assertEqual(66, lancedb.connect(str(path)).open_table("memories").count_rows())


if __name__ == "__main__":
    unittest.main()
