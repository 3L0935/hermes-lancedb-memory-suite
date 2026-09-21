# Retention-aware maintenance implementation plan

**Goal:** Stop repeated backups and data/index rewrites driven by empty index
directories, protected history, and misleading reclaim estimates.

**Architecture:** Keep the existing locked, verified-backup execution boundary.
Routine maintenance selects cleanup and fragment compaction independently for
each table. Lance owns file reachability; no manual index deletion is introduced.
Explicit zero-age reclaim remains a separate manual operation.

**Tech stack:** Python, LanceDB 0.34.0, pylance 9.0.0, unittest/pytest, systemd.

## Evidence and decisions

- Live snapshot: 21,269,159 bytes, 349 index directories, 337 without payload.
- Disposable-copy full optimize: 24,458,315 bytes; cleanup only: 21,135,148.
- Both host and container use LanceDB 0.34.0; only the host has pylance 9.0.0.
- Use the tested pylance public API for cleanup and fragment compaction;
  declare the dependency and refuse unsupported execution before backup.
- Cleanup excludes the current version and tagged versions, and requires an
  expired version. Run at most once per table per 24 hours by default.
- Compact only above 64 fragments, with a separate one-hour cooldown.
- Recheck policy inside the writer lock before creating a backup.
- A routine skip creates no backup and does not rewrite maintenance state.
- The difference between disk size and active table statistics is overhead,
  not a promise of reclaimable bytes. Report an unknown reclaim estimate.

## Work

- [x] Add regression tests for the existing false-positive triggers, expiration
  without new writes, recent/tagged versions, independent cooldowns and no-op
  execution before backup. Run them against the existing code to confirm failures.
- [x] Implement timestamp-aware diagnostics and per-table policy in
  `server/maintenance.py`; keep legacy fields where harmless and explain unknowns.
- [x] Implement cleanup/compact actions under the existing lock and backup
  verification; record signed database and total-footprint changes. Preserve
  partial-action failure details and tag/snapshot safety.
- [x] Update timer action reporting, UI wording, dependency and deployment docs.
- [x] Run `PYTHONPATH=$PWD:$HOME/.hermes/hermes-agent OPENBLAS_NUM_THREADS=1
  $HOME/.hermes/hermes-agent/venv/bin/python -m pytest -q
  tests/test_maintenance.py tests/test_compact_if_needed.py
  tests/test_viz_retention.py tests/test_deploy_contract.py` and relevant JS checks.
- [x] Replay the new policy on a disposable live snapshot, including a second
  invocation and a small intervening write. Verify snapshots, rows and FTS recall.
- [x] Review diff, integrate the verified changes and deploy the concrete fix.
  Check the production plan without forcing a live zero-age reclaim.

## Acceptance

Hundreds of empty directories, >64 protected versions and a high overhead ratio
alone cause no routine mutation. Expired untagged history can be cleaned without
new writes. A second unchanged invocation creates no backup. A cleanup-only run
does not rebuild index/data files. Significant fragmentation still gets repaired.
24-hour snapshots and tags remain readable; new unindexed rows remain searchable.
Report initial deployment checks separately from the future 48-hour observation.
