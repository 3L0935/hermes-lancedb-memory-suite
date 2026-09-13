# Stable entity-link selection and derived-data migration

## Why this changes data

The old global rebuild selected eight qualifying neighbors by Lance scan order.
Writing even an unchanged `links` value can move a row between fragments and change
that order. Comparing stored and computed sets did not prevent the next rebuild
from choosing a different subset. The single-memory path separately truncated the
scan and appended reciprocal links, sometimes writing a value that was already
stored.

The new pure `_select_entity_links()` takes significant-entity signatures, finds
pairs sharing at least two entities through the existing inverted index, and
selects up to **eight IDs in ascending lexicographic order**. It has no store,
clock, embedding call or write. Both `_rebuild_links_for()` and
`_rebuild_all_links()` use it through a shared differential writer.

The single-memory path recalculates the target and neighbors whose old or new
retained links include it. This includes neighbors outside the target's own
eight outgoing links, and removes old incoming links after an entity change.
Other neighbors cannot have their retained subset changed by that target alone.
No reciprocal append is performed. An eight-link cap does not promise symmetric
adjacency; typed relations are separate and unchanged.

This is an **intentional migration of derived subsets**, not a neutral refactor.
Only the `links` column is updated by the rebuild. Text, entities, relations,
vectors, quality, access counters and timestamps are preserved. Equivalent sets
are not rewritten merely to reorder their stored list. Legacy IDs already in the
corpus retain their identity; no ID migration is performed.

## Production rollout — 2026-09-13

The final rollout used a new SHA-256-verified copy of the 480-row database. The
stable policy changed 170 stored link sets. One complete-row merge plus the
outer FTS refresh moved `memories` from version 131016 to 131018 and grew the
database from 2,723,436 to 3,879,105 bytes before cleanup. Every non-link column,
including vectors, relations, timestamps and counters, matched exactly; every
new link set matched the independent all-pairs manifest.

Two immediate global rebuilds kept the same versions, files and byte sizes. The
deployed store fingerprint is
`62ba59ce9a74ac1ea9575892d1ec023a75a50b92a951c20e18ff4ec2c42c4ba6` in the
repository, canonical plugin, compatibility plugin and viz health response.
Both the gateway and viz were restarted after synchronization.

A locked, hash-verified pre-migration backup and a verified post-migration
backup remain under `~/.hermes/backups`. Explicit zero-age reclaim reduced the
live database to 2,683,792 bytes with one retained version and kept 480/480 FTS
rows indexed. It reclaimed 1,195,313 database bytes. The two managed recovery
copies total 6,602,541 bytes, so the database plus managed backups totals
9,286,333 bytes.

## Historical pre-batching measurement on a frozen copy

Source: a hash-verified `rsync -a` copy of the live directory, made at
`2026-09-13T13:33:43.169215+02:00`. All experimentation used independent descendants of
`/tmp/lancedb-stable-links-20260913-tu9hqkkt/seed`. The source was only read, and file hashes matched before/after
copying and against the destination. The first pre-interruption fixture vanished
from `/tmp`; its observations were archived, and the complete matched A/B was
repeated on this new fixture.

- Memories: **482**.
- Memories with more than eight qualifying candidates: **172**.
- Stored link sets changed by the new policy: **172**.
- Applied migration: **172 updates**, touching only `links`.
- All non-link columns matched their pre-migration SHA-256 digest.
- Every applied set matched the independent all-pairs migration manifest.
- Migration cost: **1.2767 s**, **173 memories versions**
  (172 link updates plus one existing writer-batch FTS refresh),
  **172 physical data files**.
  Total DB bytes: **2719151 -> 6743164**.

The complete local manifest contains ID, before, after, candidate count and a
changed flag for every row, without memory text or vectors:
[`audit/repro/stable-links-migration-manifest.json`](../audit/repro/stable-links-migration-manifest.json).
It and the local measurement results are intentionally ignored by Git.

This older run predates complete-row batching and is retained as causal evidence.
The production rollout above regenerated its manifest and used the current batch
boundary. Reads never perform migration.

## Idempotence evidence

The regression tests were written and run before changing the implementation:
**17 failed** against `fcd52b8`. In the neutral-rewrite cases for fixture rows 17
and 21, two unchanged rebuilds issued **22 updates**, where the test required zero.
The cases cover six different rewritten rows, a no-rewrite control, explicit
reversed/rotated scans, reopening, and both rebuild entry points. After the fix:
**17 passed** (4.11 s in the resumed focused run).

The matched corpus runs produced:

| Rebuild pass | Before: updates | After migration: updates | After: new versions | After: new physical data files |
| --- | ---: | ---: | ---: | ---: |
| 1 | 102 | 0 | 0 | 0 |
| 2 | 136 | 0 | 0 | 0 |
| 3 | 127 | 0 | 0 | 0 |
| 4 | 82 | 0 | 0 | 0 |

After convergence, six different real-copy rows were each rewritten with their
existing `links` value. For every row, both a single-memory rebuild and a global
rebuild issued **zero updates**, with identical version/file/byte snapshots before
and after the rebuild. The deliberate neutral write itself is outside that count.
The separate seed's hashes remained unchanged throughout.

Behavioral integration tests cover insertion, `update_memory`, `update_entities`,
portable import, delete and bulk delete. Insertion of a low ID into an existing
high-degree group must update all affected incoming neighbors, including those
not among its own eight outgoing links. An entity change repairs old and new
neighbors. A brute-force all-pairs test oracle checks complete link sets; tests
also compare non-link columns and assert the next global rebuild is a no-op.

## Historical four matched delete cycles

Each side starts with a fresh clone of the same seed. The old side uses
`fcd52b8:plugin/store.py` loaded from a temporary module. The new side first applies
and separately accounts for the migration, then deletes the same four existing
IDs consecutively, without reinsertion or intervening compaction.

| Cycle | ID | Seconds before / after | New memories versions before / after | New physical data files before / after | Necessary link repairs after |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | `00229b1a-d0a` | 0.5444 / 0.1331 | 106 / 4 | 104 / 2 | 2 |
| 2 | `007eac83-e47` | 1.5918 / 0.7234 | 139 / 54 | 137 / 52 | 52 |
| 3 | `00a35bd7-d3f` | 1.8215 / 0.9422 | 132 / 65 | 130 / 63 | 63 |
| 4 | `00b8e26c-abb` | 1.2979 / 0.1675 | 89 / 5 | 87 / 3 | 3 |

The remaining **2 / 52 / 63 / 3** link updates exactly match the independent
all-pairs oracle's changed sets after removing each target. Each final corpus
matches that oracle. Each delete also makes one deletion commit and one FTS
refresh commit, so the measured versions are **4 / 54 / 65 / 5**. Removing a low-ID
neighbor from many retained subsets legitimately changes many rows. The fix
eliminates neutral churn; it does not promise one version per business mutation
or batch those genuinely different row updates. The FTS refresh succeeds once
per delete; no index policy or calibrated retrieval constant changed.

Timings are individual host measurements, not percentiles or CPU-isolated
benchmarks. The final after series overlapped the pre-commit test suite. Prefer
version/update/file counts for the causal conclusion; no universal speedup is
claimed. Rebuilds after convergence also leave total bytes unchanged.

## Provenance and reproduction

Measured before: `2026-09-13T13:33:43.169215+02:00` to `2026-09-13T13:33:56.125436+02:00`.
Measured after: `2026-09-13T13:35:42.363161+02:00` to `2026-09-13T13:35:51.570342+02:00`.
Implementation SHA-256 before: `fedb4d0d7b0417a22c5a8e854e7ae05ad26d4e72651d3bdf3fea5a21b91c3dbc`.
Implementation SHA-256 after: `f4c5236789cf34592697a3cccf36689f1815564066c90106ee81fbb24538b07d`.

Commands actually used (from the repository root):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /home/elo/.hermes/hermes-agent/venv/bin/python audit/repro/measure-stable-links.py before /tmp/lancedb-stable-links-20260913-tu9hqkkt --baseline-store /tmp/lancedb-stable-links-20260913-tu9hqkkt/baseline-store.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /home/elo/.hermes/hermes-agent/venv/bin/python audit/repro/measure-stable-links.py after /tmp/lancedb-stable-links-20260913-tu9hqkkt
```

The temporary baseline source was exported with
`git show fcd52b8:plugin/store.py`; it does not replace the worktree or runtime.
For a fresh run, create a new `/tmp` root with `mktemp -d`, export the baseline
there, and substitute that root in both commands. The measurement script rejects
non-temporary destinations and fails if the source changes during copying.
No query text is inserted in any memory; tests use only synthetic content.

Local evidence:

- `audit/repro/stable-links-before.json`
- `audit/repro/stable-links-after.json`
- `audit/repro/stable-links-migration-manifest.json`
- `audit/repro/stable-links-red.txt`
- `audit/repro/measure-stable-links.py`

## Verification and unchanged contracts

After the disk-growth implementation, the complete suite reports **247 passed,
2 subtests passed**;
`node --check` succeeds for every `static/*.js`. The complete suite and JS checks
must also be run after the final commit; their terminal output and local logs are
the final verification record, without making another documentation-only commit.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest -q tests
for file in static/*.js; do node --check "$file" || exit; done
```

`SEARCH_MIN_BM25_SCORE=12.80`, `SEARCH_MAX_COSINE_DISTANCE=0.30`,
`lancedb==0.34.0`, routing, abstention and the read-without-writes contract are
unchanged. No dependency was added. The production migration and verified
reclaim are recorded in the rollout section above.
