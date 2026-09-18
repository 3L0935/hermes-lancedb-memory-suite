# FTS index rotation investigation

Measured 2026-09-14 on the repository's pinned `lancedb==0.34.0`, against the
live database at `~/.hermes/lancedb` (read-only) and disposable `/tmp` copies.
Repository HEAD when this report was written: `1939bef`.

## Summary

The rotating index generation is not a maintenance defect and not an orphan
cleanup defect. It is the cost of `_ensure_fts_index()` treating *partial
coverage* as a reason to replace the index after every committed writer batch.
One writer batch cost one index generation, one table version, and ~5.2 KB of
index files. Deferring that replacement removes the rotation entirely while
keeping every write queryable.

## Q1 - why is the index rebuilt so often, and by whom?

### Reproduction of the snapshot

| Measurement | Value |
| --- | ---: |
| Physical index directories (`memories.lance/_indices`) | 305 |
| Non-empty index directories | 303 |
| Retained manifests (`_versions/*.manifest`) | 767 |
| `_indices` bytes | 134,180,065 |
| Reachable from the current version | 2 |
| Reachable from at least one retained manifest | 304 |
| Reachable from no retained manifest | 1 |

Directory mtimes cluster on the two hours of active writing, not on a single
batch:

| Hour (2026-09-14) | Index directories | Manifests | Data fragments |
| --- | ---: | ---: | ---: |
| 12h | 1 | 4 | 3 |
| 13h | 6 | 19 | 12 |
| 14h | 1 | 2 | 1 |
| 15h | 154 | 325 | 170 |
| 16h | 137 | 361 | 224 |
| 17h | 3 | 55 | 52 |

### Attribution

The writer is `plugin/store.py`. Up to `1939bef`, `_ensure_fts_index()` required
`index.num_indexed_rows == row_count and index.num_unindexed_rows == 0`
(`plugin/store.py:604-621` at HEAD), so any batch that changed the table version
fell through to `create_index("content", config=FTS(), replace=True)`
(`plugin/store.py:628`). `write_batch()` calls it on every outermost batch whose
version changed (`plugin/store.py:674`).

Measured A/B on disposable databases, 8 outer writer batches of 3 adds each:

| Arm | Index dirs created | Generations / batch | Manifests created | Rows |
| --- | ---: | ---: | ---: | ---: |
| Writer at `1939bef` | 8 | 1.0 | 56 | 25 |
| Writer with deferred refresh | 0 | 0.0 | 48 | 25 |

So there is no competing writer to find: one generation per batch is the whole
mechanism. The `15h`/`16h` counts are explained by the volume of one-shot
maintenance scripts run against the live database in those two hours
(`/tmp/clean_big.py:78`, `/tmp/split_hub.py:45`, `/tmp/restore_radio.py:28` all
construct `LanceDBStore("$HOME/.hermes/lancedb")` directly, bypassing the
gateway). The gateway writer contributes the "FTS index ready on content column"
lines: 19 of them in `~/.hermes/logs/agent.log`, clustered at 15:47-15:48 (10),
16:12 (4), 16:44-16:45 (3), plus one each at 12:48 and 17:20.

There is no evidence of automatic viz GET-path mutation.

### Consequence for the calibration

The 12.80 admission gate is one global constant
(`plugin/store.py:49`), calibrated on `lancedb==0.34.0` against a gap of 0.0696
between the highest must-abstain score and the lowest answerable score. A writer
batch that rescored already-indexed rows would silently invalidate it.

Measured on a read-only copy of the live corpus (516 rows), 20 frozen questions,
60 neutral rows appended on the writer path:

| State | Max absolute score shift on incumbent rows | New leaks past 12.80 |
| --- | ---: | ---: |
| Deferred coverage (index stale, 66 unindexed rows) | 0.0 | 0 |
| After `refresh_fts_index()` | 0.7442 | 0 |

Deferred coverage leaves incumbent scores bit-identical; the refresh is what
moves them. Freezing the index is therefore not a correctness compromise on the
scoring path, it is the more conservative state.

## Q2 - does each index replace the previous one?

Yes, for a cooperating reader; no, for disk.

`create_index(..., replace=True)` pins the new UUID on the current version and
leaves the previous UUID reachable from every retained manifest that referenced
it. Measured on a full copy of the live database:

| Metric | Live | After `mode=routine` | After `mode=reclaim` |
| --- | ---: | ---: | ---: |
| Database bytes | 157,798,367 | 157,798,367 | 2,895,929 |
| Actual reclaimed | - | 0 | 154,902,438 |
| `memories/_indices` bytes | 134,180,065 | 134,180,065 | 447,983 |
| Physical index directories | 305 | 305 | 305 |
| Non-empty index directories | 303 | 303 | 1 |
| Manifests | 767 | 767 | 1 |
| Data fragments | 508 | 508 | 46 |
| `num_unindexed_rows` | 0 | 0 | 0 |
| `manual_index_cleanup` | - | `skipped_unproven_snapshot_reachability` | same |

The growth is structural while the replacement rate stays at one generation per
batch: 24 hours of routine retention (`server/maintenance.py:36`, applied at
`server/maintenance.py:573`) retains every manifest written in that window, and
each retained manifest keeps its index generation reachable.

`optimize()` is the catch-up mechanism, not a solution to the directory count:
`create_index(..., replace=True)` is what rotates the UUID, and `optimize()`
merely adds unindexed rows to the current generation and drops manifests outside
the retention window. A `reclaim` is what collapses the manifest set, and it does
reclaim the bytes (154.9 MB measured).

Zero-age cleanup is reserved for an explicit `reclaim` after old readers drain.
The route requires `confirmed: true` (`server/server.py:1579`) and validates the
mode against `{routine, reclaim}` (`server/server.py:1585-1586`).

## Q3 - is `skipped_unproven_snapshot_reachability` too conservative?

The guard is correct as written, but it under-reports a real cost once a reclaim
has run.

On the live database the guard is justified by measurement: 304 of 305
directories are reachable from at least one retained manifest, so deleting them
would break retained snapshots. 1 directory is reachable from nothing and is
empty.

After a reclaim, reachability collapses but the directories do not:

| State | Physical dirs | Empty dirs | Manifests | Unreachable from all manifests | Unreachable and empty |
| --- | ---: | ---: | ---: | ---: | ---: |
| Live | 305 | 2 | 767 | 1 | 1 |
| Post-reclaim | 305 | 304 | 1 | 303 | 303 |

Every unreachable directory is empty in both states, and no unreachable
directory holds `part_N_docs` payloads. LanceDB 0.34.0's cleanup removes the file
contents but leaves the directory shells.

Two consequences measured on a post-reclaim copy:

1. The byte cost is gone (447,983 bytes of index files) but the inode/listing
   cost is not: 305 directories, 1 non-empty.
2. `/api/maintenance/compact/plan` still recommends compaction on that copy,
   with `memories.orphan_index_directories=304>4` as the trigger
   (`server/maintenance.py:312-319`, threshold `MAINTENANCE_MAX_ORPHAN_INDEX_DIRECTORIES = 4`
   at `server/maintenance.py:41`). A compacted database therefore keeps asking to
   be compacted.

### Official drop paths on 0.34.0

Probed on throwaway tables:

| Call | `list_indices()` after | Physical dirs after | Lexical query after |
| --- | ---: | ---: | --- |
| `drop_index("content_idx")` | 0 | 1 | raises: no INVERTED index on any column |
| `optimize(cleanup_older_than=0)` | 1 | 2 | works |
| `optimize(cleanup_older_than=0, delete_unverified=True)` | 1 | 2 | works |

`drop_index` does what it says (the index is gone, and lexical search correctly
fails afterwards) but it is not a cleanup route: it removes the index from the
current version while leaving the directory on disk, and it destroys lexical
recall. `delete_unverified=True` changes nothing about the directory count in this
probe, so there is no measured advantage to adopting it; the current code does not
call it and this report does not recommend it.

No official API in 0.34.0 removes the empty directory shells left after a
reclaim. The guard stays.

## What the source change does

`_ensure_fts_index(tbl, *, refresh=False)` now means "ensure a compatible content
FTS index exists". An existing FTS index on `["content"]` is reused regardless of
its coverage; only an explicit `refresh_fts_index()`, which passes
`refresh=True`, rebuilds partial coverage. `write_batch()` still calls the helper
after a committed mutation, so a new or repaired table still gets an index.

This is safe because normal FTS queries in 0.34.0 include an unindexed
flat-search branch. Measured: a row added on the deferred writer path is returned
by a plain `query_type="fts"` search while `num_unindexed_rows` is non-zero, with
a positive score, and the same row is still returned after the refresh.

## Limitations

- The corpus is live and grows between measurements; every figure above is dated
  and was re-measured for this report rather than carried over.
- Attribution to the `/tmp` maintenance scripts rests on their source
  (they construct the live store directly) and on hourly manifest/mtime counts,
  not on a per-call audit log. No call-level trace was available.
- The reclaimed figures come from a disposable copy. The live reclaim endpoint was
  not invoked.
- The 12.80 gate was re-checked for score drift under deferred coverage, not
  re-calibrated. A corpus change that moves must-abstain or answerable scores
  still requires a new calibration run.
- Deploying the writer change to the gateway requires a copy to
  `~/.hermes/plugins/lancedb-suite/` and a gateway restart outside this run. The
  deployed plugin still holds the pre-change writer.
