# Stable Entity Links Implementation Plan

**Goal:** Make entity-link rebuilds idempotent regardless of Lance fragment order.

**Architecture:** A pure selector builds candidates from significant-entity signatures,
then selects the first eight unique IDs in ascending order. Both rebuild entry points
share a differential writer. The single-memory path recalculates current candidate
neighbors and old incoming links, without appending reciprocal links.

**Tech stack:** Existing Python, NumPy, LanceDB 0.34.0, pytest and Node syntax checks.

## Execution

- [ ] Add `tests/test_store_links.py`: real neutral rewrites of multiple fixture rows,
  consecutive rebuilds, explicit scan permutations, non-link column preservation,
  and equivalence of single/global paths after insertion, updates, imports and deletes.
- [ ] Run the focused tests against the unchanged implementation and preserve the red
  output under ignored `audit/repro/`.
- [ ] Freeze a verified rsync copy under `/tmp`; record source/copy hashes, a migration
  manifest (ID, stored links, proposed links), and four consecutive baseline deletes.
  Never construct a store on the live source or use the real backups/runtime.
- [ ] Replace scan-dependent selection in `plugin/store.py` with the pure ID-based
  selector, retaining the existing inverted-entity candidate generation and eight-link
  cap. The common differential writer changes only `links`.
- [ ] Run focused tests. On independent copies of the same seed, apply the migration,
  verify all other columns unchanged and rebuilds write nothing, then measure the
  same four deletes. Report genuine neighbor repairs and FTS commits separately.
- [ ] Document the intentional derived-data migration, compatibility boundaries,
  measured counts and commands in `docs/entity-link-migration.md`.
- [ ] Review the diff, run the full suite and JS syntax checks, then commit on `main`
  with the cause and migration in the message. No push.
- [ ] After the last commit, run the full suite and `node --check` for every static JS,
  then verify a clean worktree and unchanged `origin/main`.

## Acceptance commands

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest -q tests/test_store_links.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest -q tests
for file in static/*.js; do node --check "$file" || exit; done
git status --short
git log origin/main..HEAD --oneline
```

The approved task fixes the selection policy and migration scope. No design approval,
worktree, deployment, engine change, threshold change or push is needed.
