# LanceDB Disk Growth Control Implementation Plan

> **For agentic workers:** Execute the checked tasks in order, with verification at each checkpoint. This document proposes implementation and rollout; it does not authorize live deployment, migration, or cleanup.

**Goal:** Keep existing-database reads and unchanged operations physically read-only, reduce necessary write amplification, and reclaim obsolete storage without hiding its cost in backups.

**Architecture:** Retain embedded LanceDB 0.34.0, the shared writer lock, the existing hourly maintenance timer, and the current retrieval engine. First roll out the stable differential link selection already implemented in `ff1bd66`; then eliminate remaining redundant mutations and consolidate real changes into bounded commits. Treat indexing, compaction, version retention, and backup retention as distinct costs.

**Tech Stack:** Python, LanceDB 0.34.0, PyArrow, pytest, Bash, systemd user units; no additional service, database, model, or GPU dependency.

---

## Verified starting point — 2026-09-13

- Repository HEAD: `ff1bd66`. Working tree was clean before this plan.
- Canonical and compatibility runtime `store.py` files differ from the repository. The inspected diff is the stable entity-link selection change. An already running process may retain older imported code even after files are synchronized.
- Reading and reopening no longer write access counters or recreate FTS: this was fixed in `e12969c`. The structured update path already skips unchanged writes; do not reimplement it.
- `memories`: 480 rows, version number 131016, **one retained version**, 2,698,843 logical file bytes. The version number is cumulative, not a count of retained snapshots.
- Four table directories total 2,722,422 bytes, approximately **2.60 MiB**. Managed pre-compaction backups total 39,340,234 bytes, approximately **37.52 MiB**. These are instantaneous logical sizes, not allocated disk blocks or a before/after experiment.
- Current FTS: 480 indexed rows, zero unindexed rows, 431,919 bytes.
- The existing `lancedb-viz-maintenance.timer` is active and checks hourly. It triggers beyond 64 versions, 64 fragments, or four abandoned index directories. These are trigger thresholds, not hard disk-size limits.
- The current maintenance code uses `cleanup_older_than=timedelta(seconds=0)`, copies the complete pre-compaction directory, and keeps two managed backups. Two backups bound count, not bytes.
- Fresh targeted verification: **44 passed, 57 deselected in 3.93s**. These checks cover links, physically read-only access, batches, and maintenance. No live mutation, model inference, or compaction was performed for this plan.

## Root cause and expected behavior

The deployed link builder retains up to eight neighbors using mutable scan order. Fragment rewrites change that order and can change otherwise equivalent derived link sets. Comparing old/new sets does not prevent this feedback loop. `ff1bd66` selects IDs deterministically and writes only changed sets.

Previous measurements documented in `docs/entity-link-migration.md`, **not rerun for this plan**, showed 82–136 redundant updates on repeated rebuilds before the fix and zero after convergence. Necessary updates remain: a shared neighbor deletion changed 52 or 63 other rows in two examples. The current implementation still commits those rows individually.

LanceDB writes versions and files for mutations and index maintenance. Historical files are reclaimed through version cleanup, not simply because a memory was deleted. A modest temporary increase after a real write is expected; growth caused by reads, unchanged values, or unstable derived data is avoidable.

Acceptance boundaries:

| Operation | Required behavior |
| --- | --- |
| Open/search/list/detail/graph on an existing DB | No additional table versions, data files, or index files |
| Unchanged structured write, tags, entities, or link rebuild | Same physical-storage snapshot after the operation |
| Actual mutation | Only changed rows written; one derived-link merge per batch |
| Final writer-batch FTS handling | At most one refresh, and tested recall/calibration preserved |
| Idle maintenance below thresholds | Read-only check; no backup and no optimize |
| Maintenance after a burst | Reclaim eligible history, account for backup bytes, preserve retained snapshots |

## Task 1 — Complete the existing fix's rollout

**Files:** existing `plugin/store.py`, `tests/test_store_links.py`, `docs/entity-link-migration.md`, `scripts/deploy-local.sh`, `scripts/verify-setup.sh`.

- [ ] Repeat the repository/runtime diff immediately before rollout and record the loaded implementation fingerprint for gateway and viz. Do not assume a file copy reloads Python modules.
- [ ] Create a verified frozen copy using the procedure in `docs/entity-link-migration.md`; regenerate the before/after link manifest against current rows. Use a cooperating writer lock during snapshot creation or reject any snapshot whose source hashes changed. Do not reuse the old 482-row manifest on today's 480-row database.
- [ ] Verify that the proposed migration changes only `links`; compare every other column, including vectors, typed relations, IDs, timestamps, and counters. The stable eight-ID policy intentionally changes some retained neighbor subsets.
- [ ] On the copy, run the global rebuild inside `store.write_batch()`. Repeat it twice: the second and third runs must create zero updates, versions, data files, or index files. Record the one-time migration's growth separately.
- [ ] Prepare the deployment using `./scripts/deploy-local.sh --dry-run`. At the rollout checkpoint, synchronize canonical/runtime plugin copies and reload **both** viz and the gateway; the deploy script restarts viz but does not restart the gateway.
- [ ] With a verified backup and a coordinated maintenance window, migrate live derived links using the already tested batch boundary. Verify the manifest and unchanged columns again. Resume readers/writers and verify loaded fingerprints and recall.

**Checkpoint:** neutral rebuilds converge; migrated link semantics are reviewed; all consumers actually run the intended code. Do not make reads perform migration.

## Task 2 — Eliminate remaining unchanged metadata writes

**Files:** modify `plugin/store.py`; add `tests/test_store_write_budget.py`; extend `tests/test_store_retention.py` where existing behavior belongs.

The remaining `update_tags`, `bulk_tag`, `rename_tag`, `merge_tags`, and `update_entities` paths can issue updates even when the effective value is unchanged. The strict `update_memory` path already has a no-op check.

- [ ] Add red-first tests for: identical tags; adding an existing tag; removing a missing tag; renaming a tag to itself; merging into an already equivalent tag set; and identical entity sets. Define tags/entities as sets for this comparison; preserve stored ordering when unchanged.
- [ ] Reuse the physical snapshot concept from `tests/test_store_links.py` and assert both filesystem state and table version, not just a mocked update-call count.
- [ ] Before setting `updated_at`, compare normalized values to stored values. Return the existing success shape for an unchanged single update; bulk `updated` counts must count actual changed rows. Keep all validation and writer locking intact.

Comparison rule for collection fields:

```python
# Both values are already parsed and validated collections of strings.
unchanged = set(requested_values) == set(stored_values)
# If unchanged: retain the stored value and timestamp; issue no table update.
```

- [ ] Verify that a genuinely changed field still commits and that the next identical request is physically read-only. Retain the existing no-embedding behavior for metadata-only changes.
- [ ] Commit this change separately as `perf(store): skip unchanged metadata mutations`.

## Task 3 — Batch the necessary link and bulk mutations

**Files:** modify `plugin/store.py`; extend `tests/test_store_links.py`, `tests/test_store_write_budget.py`, and `tests/test_auto_merge_duplicates.py`.

- [ ] Construct a high-degree synthetic graph where deleting one node necessarily changes many retained neighbor sets. Use the independent all-pairs oracle already in `tests/test_store_links.py` to identify the exact delta.
- [ ] Calculate the complete link delta before writing. Fetch complete current rows for changed IDs under the existing writer lock; replace only their `links` values in the in-memory records. Commit the changed records using one `merge_insert("id").when_matched_update_all().execute(...)` per outer operation.
- [ ] Preserve every column and timestamp. **Do not use a two-column source with `when_matched_update_all`:** the installed API has no selective `when_matched_update`, and omitted fields must not be lost. Do not enable insert-on-miss or delete-on-miss for derived-link updates.
- [ ] Verify the merge behavior on the pinned engine before adoption: untouched row values, vector bytes, relation JSON, IDs and timestamps must match; changed link sets must match the independent oracle. An empty delta must return without calling merge.
- [ ] For bulk delete/tag operations, validate all inputs, collect actual changes, commit the primary batch, then rebuild links once. Preserve per-ID success/error reporting and explicit partial-commit states; do not claim atomicity across the separate tables.
- [ ] Keep one FTS refresh at the outermost batch boundary. For a single deletion that requires link repair, target **one primary memories commit + one link-merge commit + one FTS commit**, independently of the number of affected link rows. Record edge/conflict-table commits separately.
- [ ] Compare 20-row and 200-row synthetic cases with the same changed-row pattern. Judge versions, index generations and files first; no tight timing or exact-byte assertions across platforms. No Ollama calls are needed.
- [ ] Commit separately as `perf(store): batch derived link and bulk mutations`.

## Task 4 — Reduce index cost only after measuring it

**Files:** `plugin/store.py`, `tests/test_engine_calibration.py`, `tests/test_retrieval_benchmark.py`, `tests/test_store_write_budget.py`, existing `audit/repro/measure-write-amplification.py`.

- [ ] Measure actual byte/file/version deltas for isolated add, metadata change, content update, relation-only update and bulk delete. Separate primary data, manifests and index generations.
- [ ] Keep `lancedb==0.34.0` and the existing BM25/cosine gates. Metadata updates may invalidate row IDs covered by FTS even when content is unchanged; skipping index maintenance solely because no text changed is not a proven fix.
- [ ] If replacement FTS remains a material cost after Tasks 1–3, compare the engine's supported incremental maintenance with the current one-refresh-per-batch approach on a disposable copy. Inspect the installed API; do not import a new native package just to gain an optional method.
- [ ] Require identical expected lexical recall and a passing calibrated retrieval suite, including abstention queries. Indexing candidates must not silently run aggressive version cleanup on normal writes.
- [ ] Retain the existing refresh if the alternative lacks a demonstrated storage benefit or changes retrieval behavior. This is a bounded decision checkpoint, not a mandatory index rewrite.

## Task 5 — Refine the existing maintenance and disk reporting

**Files:** `server/maintenance.py`, `server/server.py`, `scripts/compact-if-needed.py`, `systemd/lancedb-viz-maintenance.timer`, `tests/test_maintenance.py`, `tests/test_compact_if_needed.py`, `static/app.js`, `README.md`.

- [ ] Report separate `database_bytes`, `managed_backup_bytes`, total footprint and `reclaimable_bytes_estimate`. Keep estimates visibly distinct from measured post-cleanup savings. Report retained version count separately from cumulative version number.
- [ ] Reuse the existing timer; do not run optimize after each tool call. Add a minimum interval of one hour between successful maintenance runs and track the last examined table versions. Avoid repeated backups when retention prevents any further reclamation and there have been no new writes.
- [ ] Retain the existing count triggers initially. Add an explicit byte-growth trigger, starting with **at least 16 MiB estimated reclaimable overhead and a total DB size at least four times estimated active storage**. Treat these as configurable starting values to validate after Tasks 1–3, not a universal optimal policy or a hard size cap.
- [ ] Separate routine compaction/index maintenance from destructive historical cleanup. Propose a **24-hour historical retention window** for routine cleanup, with a minimum age longer than supported active queries; preserve tagged versions. Keep zero-age cleanup for a coordinated explicit reclaim operation with old readers drained and a verified backup.
- [ ] Before introducing nonzero retention, remove the assumption that an index directory absent from the current version is orphaned. It may be needed by a retained/tagged version. Prefer engine-managed cleanup; if complete reachability cannot be proven, skip manual directory deletion and report the uncertainty.
- [ ] Validate backup readability and key content before rotating older managed backups. Keep two verified recovery copies; never delete the last known-good copy to satisfy a cosmetic size target. Show the total backup bytes and warn when the configured storage budget cannot be met without changing retention.
- [ ] Preflight capacity for **backup plus compaction scratch**, checking DB and backup filesystems separately if they differ. The current `free_bytes >= database_bytes` check only budgets the copy.
- [ ] Add tests for below-threshold zero-write behavior, cooldown, held writer lock, active retained snapshots, insufficient scratch space, unreadable backup, failed verification and unchanged-data retries. Simulate time; do not sleep or fill the disk.
- [ ] Reuse the existing health/maintenance UI to show the last run's timestamp, reason, actual bytes reclaimed and backup cost. No new always-running collector.
- [ ] Commit maintenance policy and UI reporting separately from store mutation changes.

## Final verification and delivery

Run from the repository root, using the existing Hermes environment:

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
"$HOME/.hermes/hermes-agent/venv/bin/python" -m pytest -q tests

for script in static/*.js; do node --check "$script" || exit; done
```

- [ ] On a disposable fixture, open/search/list/detail 20 times, including FTS-current and FTS-stale states: assert zero filesystem/version growth. Embed queries with deterministic fake vectors.
- [ ] Repeat unchanged metadata and converged link rebuilds 20 times: zero growth.
- [ ] Execute a short matched add/update/delete workload with a high-degree link fixture; verify required changes and commit budgets. Measure migration and maintenance separately.
- [ ] Open a retained snapshot after routine maintenance and confirm its index/data files remain readable. Verify current retrieval and restores from the retained backup.
- [ ] Confirm fresh runtime fingerprints and run read-only health checks after rollout. Observe existing hourly maintenance over normal use; avoid automatic stress workloads.
- [ ] Deliver before/after deltas for data, history, indexes and backups, test output, and the retained recovery path. Do not promise a fixed total size while the user continues adding durable data.

## Recommendation

Start with **Task 1**, because its root-cause fix already exists but is not synchronized into the inspected runtime files. Follow with **Tasks 2–3** to reduce amplification that remains even with correct link sets. Improve maintenance accounting and retention next; explore another index-maintenance strategy only if measured residual cost justifies it. No engine replacement, persistent GPU workload, or new database is warranted by the evidence.

## References

- [Stable entity-link migration and earlier measurements](../../entity-link-migration.md).
- [Earlier write-amplification plan](2026-09-12-write-amplification.md).
- [LanceDB: index maintenance and compaction](https://docs.lancedb.com/indexing/reindexing).
- [LanceDB: versioning and retained snapshots](https://docs.lancedb.com/tables/versioning).

The earlier migration measurements are historical evidence. Today's runtime diff, storage snapshot and targeted test result were checked while writing this document; application changes and live rollout remain unexecuted.
