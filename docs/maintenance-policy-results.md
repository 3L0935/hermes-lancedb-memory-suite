# Retention-aware maintenance verification

Measured on 2026-09-21 with LanceDB 0.34.0 and pylance 9.0.0. All database
mutation experiments below used disposable copies or synthetic databases.

## Root cause

The hourly policy treated physical index-directory count, total retained-version
count and `disk - table.stats().total_bytes` as independent cleanup triggers.
None establishes that files can be removed under the 24-hour retention window.
A fresh writer version bypassed unchanged-version suppression and triggered
full `optimize()` on all four tables, including fragment and index rewrites.

The live snapshot contained 21,269,159 bytes and 349 physical index directories;
337 directories contained no payload bytes. `memories` had 97 retained versions
and 18 current fragments.

| Same disposable snapshot | Database bytes after | Delta |
| --- | ---: | ---: |
| Full optimize with 24-hour retention | 24,458,315 | +3,189,156 |
| Cleanup-only with 24-hour retention | 21,135,148 | -134,011 |

The full operation grew both index storage (6,378,892 → 7,463,144 bytes) and
data storage (14,347,545 → 16,470,357 bytes) in `memories`. Cleanup alone kept
18 fragments and did not change index storage. Both arms preserved row counts.

## Corrected policy replay

A later replay using the new backup-first execution path cleaned 20 expired
`memories` versions, 10 `memory_edges` versions and 4 `memory_conflicts` versions.
The slightly larger cleanup is expected because more history expired between
the two probes.

- Database: 21,269,159 → 21,118,247 bytes (-150,912 bytes).
- Actions: cleanup only on the three affected tables; no action on the archive.
- Full row fingerprints were identical before and after cleanup.
- A second call returned `not_needed`.
- A small subsequent write followed by another call also returned `not_needed`.
- Exactly one pre-maintenance backup was created across those three calls.
- The newly added, unindexed test row was found by FTS.
- With the copied production cooldown state present, the initial plan selected
  no actions. Only that disposable copy's state was removed to exercise cleanup.

## Regression coverage

`tests/test_maintenance_policy.py` exercises real 66-fragment compaction while
preserving indexed/unindexed FTS recall and a recent snapshot. It also covers
non-UTC timestamps, unreadable tags, independent action cooldowns, state migration,
missing pylance before backup and post-mutation reconnect failures.

`tests/test_maintenance.py` covers protected history, empty-directory/overhead
false positives, expiration without new writes, tagged snapshots, no-op runs,
backup verification/rotation, disk budgets and shared writer locking.

`tests/test_compact_if_needed.py` covers a stale GET plan followed by a locked
no-op response. `tests/static_maintenance_sizes.test.cjs` covers unknown sizes
and signed database/total-footprint changes, including legacy maintenance state.

These checks establish the policy and engine behavior. They do not establish
48 hours of production stability; that observation starts after deployment.

Validation commands and observed output before deployment:

```text
PYTHONPATH=$PWD:$HOME/.hermes/hermes-agent OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  $HOME/.hermes/hermes-agent/venv/bin/python -m pytest -q tests
296 passed, 4 subtests passed in 15.39s

node --test tests/static_*.test.cjs
18 passed, 0 failed
```

## Deployment verification

The fix was integrated locally as `8f7893b`. The canonical Hub image was rebuilt
from the synchronized repository requirements and the viz container recreated.
Only maintenance/server/UI files were deployed; no manual reclaim was invoked.

- Running image: LanceDB 0.34.0, pylance 9.0.0; Docker health: healthy.
- Seven policy regression tests also passed inside the rebuilt image.
- Production plan: `recommended=false`, `actions={}`, reclaim estimate `null`.
- Two timer-script checks returned `not_needed`; the two managed backup names
  and maintenance-state digest were identical before and after the checks.
- The systemd maintenance service was then run once and also returned `not_needed`.
- The hourly maintenance timer remains active.

The previous timer ran once more during preparation, before deployment; the
production database was 24,441,419 bytes at the final check. This patch does not
force removal of protected history to make that number immediately smaller.
The plan reported its next possible cleanup at 2026-09-22 12:04:41 UTC, subject
to subsequent writes and independent fragmentation needs.
