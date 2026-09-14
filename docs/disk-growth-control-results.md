# LanceDB disk-growth control results

Measured on 2026-09-13 with the repository's pinned `lancedb==0.34.0`, fake
deterministic 768-dimensional embeddings, and a disposable local database. The
reproduction command is:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
"$HOME/.hermes/hermes-agent/venv/bin/python" \
audit/repro/measure-write-amplification.py
```

## Isolated operation costs

| Operation | DB bytes | Data bytes | Manifest bytes | Index bytes | Versions | FTS generations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Add | +26,201 | +16,052 | +4,848 | +4,011 | +3 | +1 |
| Metadata update | +16,165 | +8,090 | +3,365 | +4,021 | +2 | +1 |
| Content update | +17,003 | +8,090 | +3,367 | +4,021 | +2 | +1 |
| Relation-only update | +18,840 | +8,154 | +3,365 | +4,011 | +2 | +1 |
| Bulk delete of an unlinked row | +2,842 | 0 | +1,297 | 0 | +1 | 0 |
| Reopen and lexical search loop | 0 | 0 | 0 | 0 | 0 | 0 |

The engine gave identical lexical recall before and after the outer writer-batch
refresh for the synthetic rare token. The calibrated retrieval tests also pass.

**Superseded (2026-09-14):** the earlier conclusion here was that skipping the
writer-path refresh "has no demonstrated correctness or storage advantage". That is
no longer accurate. Measured on
`lancedb==0.34.0`: one writer batch cost one replacement index generation, one
table version, and ~5.2 KB of index files (8 batches produced 8 generations,
41,644 index bytes, on a disposable database), and a row added without a refresh
is still returned by a normal `query_type="fts"` search while
`num_unindexed_rows` is non-zero. Incumbent BM25 scores are also invariant under
deferred coverage (max absolute shift 0.0 across 20 frozen questions) and move
only when an index is materialized (max 0.7442), so deferring the refresh is the
more conservative state for the calibrated 12.80 admission gate. The writer now
reuses any compatible FTS index and rebuilds partial coverage only on an explicit
`refresh_fts_index()` or through maintenance `optimize()`. See
`docs/fts-index-rotation-investigation.md` for the dated measurements.

## Batching and cleanup

Deleting a node that repairs many derived links uses one primary delete, one
complete-row link merge, and one FTS refresh. The same three-version budget was
verified with 20 and 200 rows while the independent all-pairs oracle confirmed
the final links.

The disposable zero-age reclaim reduced the database from 250,040 bytes and 31
retained memory manifests to 24,115 bytes and one retained manifest. It reclaimed
225,925 database bytes. The verified pre-maintenance backup cost 250,040 bytes,
so the measured total database-plus-backup footprint immediately after the run
was 274,155 bytes. This distinction is why the health API and UI report database,
managed backups, total footprint, and estimated reclaimable bytes separately.

Routine maintenance retains 24 hours of history. It may initially increase the
database while recent versions remain protected; the recorded table versions and
one-hour cooldown prevent an unchanged database from producing repeated backups.
Manual index-directory deletion stays disabled because current-version metadata
cannot prove that a directory is unreachable from retained or tagged snapshots.
