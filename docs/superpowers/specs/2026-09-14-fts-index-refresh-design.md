# Deferred FTS Index Refresh Design

## Goal

Stop replacing the `content` FTS index after every memory mutation while
preserving complete lexical recall on LanceDB 0.34.0.

## Measured problem

`LanceDBStore.write_batch()` currently calls `_ensure_fts_index()` whenever the
memories table version changes. `_ensure_fts_index()` treats any non-zero
`num_unindexed_rows` value as a reason to call `create_index(..., replace=True)`.
This produces one replacement index generation and one additional table version
per outer writer batch.

A read-only snapshot of the live database taken on 2026-09-14 at
17:32:48 Europe/Paris contained 516 memory rows, 767 retained manifests, 305
physical index directories, and 134,180,065 bytes under `_indices`. Raw UUID
matching found 304 directory UUIDs in at least one retained manifest.

Experiments on disposable copies established the behavior that matters for this
change:

- after a content update, the active FTS index reported 515 indexed rows and one
  unindexed row;
- a normal FTS query found a token introduced by that update before any index
  refresh;
- calling the current `_ensure_fts_index()` then replaced the UUID and added one
  physical index directory;
- LanceDB `optimize()` also brought the index back to 516 indexed rows and zero
  unindexed rows;
- a zero-age maintenance reclaim reduced retained manifests from 767 to one and
  index file bytes from 134,180,065 to 447,983 without manual directory removal.

This matches the LanceDB query contract: normal FTS searches include an
unindexed flat-search branch. Only the opt-in `fast_search()` path skips
unindexed rows. This repository does not use `fast_search()`.

## Design

### Writer path

`_ensure_fts_index(table)` will mean exactly "ensure a compatible FTS index
exists." If `list_indices()` contains an FTS index whose columns are
`["content"]`, the method returns success regardless of its indexed-row
coverage. If no compatible index exists, it creates one with the existing
LanceDB 0.34.0-compatible fallback.

`write_batch()` will keep calling `_ensure_fts_index()` after a committed memory
mutation. The call remains useful for a new or repaired table, but becomes
physically read-only when a compatible index already exists.

### Explicit refresh

`refresh_fts_index()` is an explicit administrative operation and must preserve
its current promise of full index coverage. It will request a forced refresh
through `_ensure_fts_index(..., refresh=True)`. A fully covered compatible index
is reused; a missing or partially covered compatible index is rebuilt.

The distinction is deliberately expressed as a keyword argument on the existing
helper instead of adding another index-management abstraction.

### Maintenance and retention

Maintenance remains the periodic index catch-up mechanism. `Table.optimize()`
adds unindexed data to the index and `compact_lancedb()` verifies that the final
`num_unindexed_rows` value is zero.

Routine history retention remains 24 hours. That duration is not coherent with
one replacement generation per writer batch, but becomes bounded enough once
replacement frequency is tied to maintenance instead of mutation frequency.
Explicit `mode=reclaim` remains the operator-controlled way to remove all
non-current versions and their index files.

No code will manually remove `_indices/<uuid>` directories. LanceDB 0.34.0's
cleanup can leave empty local-filesystem directory shells after safely removing
their contents; those empty directories are an inode/listing concern, not the
measured disk-space cause.

## Error handling

The existing compatibility fallback and failure contract remain unchanged:

- prefer `create_index("content", config=FTS(), replace=True)`;
- fall back to `create_fts_index("content", replace=True)` only for the existing
  import/signature compatibility exceptions;
- log and return `False` for an indexing failure so a successful memory commit is
  not reported as failed after the fact.

## Verification

The regression test must prove all of the following against the pinned LanceDB
0.34.0 runtime:

1. an outer writer batch that adds a row leaves the existing FTS UUID and
   physical index directory count unchanged;
2. the added row is still returned by a normal FTS query while
   `num_unindexed_rows` is non-zero;
3. `refresh_fts_index()` restores zero unindexed rows and creates at most one
   replacement generation;
4. initial creation still creates a usable FTS index when none exists;
5. the complete repository test suite remains green.

## Non-goals

- changing `lancedb==0.34.0`;
- changing routine or reclaim retention values;
- invoking `delete_unverified=True`;
- deleting index directories manually;
- deploying the plugin or restarting the Hermes gateway as part of the source
  change.
