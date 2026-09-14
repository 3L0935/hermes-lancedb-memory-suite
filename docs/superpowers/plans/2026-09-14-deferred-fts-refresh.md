# Deferred FTS Index Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep normal FTS queries complete while preventing one replacement index generation after every memory mutation.

**Architecture:** Treat an existing compatible FTS index as usable on the normal writer path even when it has unindexed rows, because LanceDB's normal FTS query scans those rows. Preserve full materialization as an explicit `refresh_fts_index()` operation and as part of periodic `optimize()` maintenance.

**Tech Stack:** Python 3.11, LanceDB 0.34.0, PyArrow, unittest/pytest.

---

## File structure

- Modify `plugin/store.py`: separate index existence from forced full refresh.
- Modify `tests/test_store_retention.py`: prove deferred refresh, unindexed-row recall, explicit refresh, and missing-index creation.
- Modify `docs/disk-growth-control-results.md`: replace the superseded conclusion that per-batch replacement has no demonstrated alternative.
- Create `docs/fts-index-rotation-investigation.md`: preserve the dated Q1/Q2/Q3 evidence and operational limits.

### Task 1: Add the failing writer-path regression

**Files:**
- Modify: `tests/test_store_retention.py:215`

- [ ] **Step 1: Replace the old per-batch-refresh expectation with the deferred-refresh contract**

```python
def test_writer_batch_defers_fts_refresh_and_preserves_new_row_recall(self):
    self.add("Project:Baseline state=active [Tier=2]")
    before = storage_snapshot(Path(self.tmp.name))
    before_index = next(
        item for item in self.store._table.list_indices()
        if item.index_type == "FTS" and item.columns == ["content"]
    )

    with self.store.write_batch():
        first_id = self.add("Project:Alpha ultrararetoken=enabled [Tier=2]")
        self.add("Project:Beta state=active [Tier=2]")

    after = storage_snapshot(Path(self.tmp.name))
    unindexed_rows = self.store._table.search(
        "ultrararetoken", query_type="fts"
    ).limit(10).to_list()
    deferred_index = next(
        item for item in self.store._table.list_indices()
        if item.index_type == "FTS" and item.columns == ["content"]
    )

    self.assertIn(first_id, [row["id"] for row in unindexed_rows])
    self.assertGreater(float(unindexed_rows[0]["_score"]), 0.0)
    self.assertEqual(before_index.index_uuid, deferred_index.index_uuid)
    self.assertGreater(deferred_index.num_unindexed_rows, 0)
    self.assertEqual(0, after["index_directories"] - before["index_directories"])

    self.assertTrue(self.store.refresh_fts_index())
    refreshed = storage_snapshot(Path(self.tmp.name))
    refreshed_index = next(
        item for item in self.store._table.list_indices()
        if item.index_type == "FTS" and item.columns == ["content"]
    )
    self.assertEqual(0, refreshed_index.num_unindexed_rows)
    self.assertNotEqual(before_index.index_uuid, refreshed_index.index_uuid)
    self.assertEqual(1, refreshed["index_directories"] - after["index_directories"])
```

- [ ] **Step 2: Run the focused test and prove it is red on the current implementation**

Run:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/test_store_retention.py::StoreRetentionTests::test_writer_batch_defers_fts_refresh_and_preserves_new_row_recall -q
```

Expected: FAIL because the current writer batch replaces the UUID, reports zero unindexed rows, and adds one index directory.

- [ ] **Step 3: Add explicit missing-index coverage**

```python
def test_writer_creates_fts_index_when_missing(self):
    self.assertEqual([], list(self.store._table.list_indices()))

    memory_id = self.add("Project:Alpha missingindexprobe=enabled [Tier=2]")
    index = next(
        item for item in self.store._table.list_indices()
        if item.index_type == "FTS" and item.columns == ["content"]
    )
    rows = self.store._table.search(
        "missingindexprobe", query_type="fts"
    ).limit(10).to_list()

    self.assertEqual(0, index.num_unindexed_rows)
    self.assertIn(memory_id, [row["id"] for row in rows])
```

### Task 2: Defer automatic FTS replacement

**Files:**
- Modify: `plugin/store.py:604-665`
- Test: `tests/test_store_retention.py`

- [ ] **Step 1: Add an explicit refresh flag to `_ensure_fts_index`**

```python
def _ensure_fts_index(self, tbl, *, refresh: bool = False):
    """Ensure content FTS exists, rebuilding partial coverage only on request."""
    try:
        row_count = tbl.count_rows()
        for index in tbl.list_indices():
            if (
                str(index.index_type).upper() == "FTS"
                and list(index.columns) == ["content"]
            ):
                fully_indexed = (
                    index.num_indexed_rows == row_count
                    and index.num_unindexed_rows == 0
                )
                if not refresh or fully_indexed:
                    if fully_indexed:
                        logger.info("FTS index already current on content column")
                    else:
                        logger.info(
                            "FTS index refresh deferred with %s unindexed rows",
                            index.num_unindexed_rows,
                        )
                    return True
        try:
            from lancedb.index import FTS
            tbl.create_index("content", config=FTS(), replace=True)
        except (ImportError, TypeError, AttributeError):
            tbl.create_fts_index("content", replace=True)
        logger.info("FTS index ready on content column")
        return True
    except Exception as e:
        logger.warning("FTS index ensure/refresh failed after memory write: %s", e)
        return False
```

- [ ] **Step 2: Preserve the explicit refresh API**

```python
def refresh_fts_index(self) -> bool:
    """Explicitly refresh content FTS as one cooperating writer operation."""
    with self.write_batch():
        return self._ensure_fts_index(self._table, refresh=True)
```

- [ ] **Step 3: Update the writer-batch documentation**

```python
@contextmanager
def write_batch(self):
    """Serialize a writer batch and ensure FTS remains available.

    Nested public mutations share the outer batch. The table version is the
    source of truth, so conflict-only operations do not inspect the memories
    index. Partial FTS coverage remains queryable and is materialized by an
    explicit refresh or periodic maintenance.
    """
```

- [ ] **Step 4: Run the focused retention tests**

Run:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_store_retention.py -q
```

Expected: all tests in `test_store_retention.py` pass.

- [ ] **Step 5: Run the write-budget and maintenance tests**

Run:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/test_store_write_budget.py tests/test_maintenance.py -q
```

Expected: all selected tests pass; maintenance still finishes with zero FTS unindexed rows.

- [ ] **Step 6: Run the complete suite and commit the implementation**

Run:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q
git diff --check
git add plugin/store.py tests/test_store_retention.py
git commit -m "fix: defer automatic FTS index refresh"
```

Expected: `266 passed, 4 subtests passed`, then a clean commit.

### Task 3: Record the corrected diagnosis

**Files:**
- Modify: `docs/disk-growth-control-results.md:14-50`
- Create: `docs/fts-index-rotation-investigation.md`

- [ ] **Step 1: Correct the prior write-amplification conclusion**

Replace the statement that skipping metadata refresh lacks a supported correctness path with the measured LanceDB 0.34.0 behavior: normal FTS queries include unindexed rows, automatic writer refresh is deferred, and explicit refresh/maintenance restores full coverage.

- [ ] **Step 2: Write the dated Q1/Q2/Q3 report**

The report must include:

- the 2026-09-14 17:32:48 Europe/Paris snapshot and raw UUID reachability counts;
- the 15:00-15:59 and 16:00-16:59 physical-directory and gateway-log counts;
- attribution to the gateway writer, `/tmp/clean_big.py`, and the relations batch, with no evidence of automatic viz GET-path mutation;
- the stale-index recall experiment and post-fix regression-test result;
- routine versus reclaim byte, manifest, and backup measurements;
- the official roles of `drop_index`, `optimize(cleanup_older_than=...)`, and `delete_unverified`;
- real post-change `file:line` citations and explicit limitations.

- [ ] **Step 3: Verify documentation and the complete suite before committing**

Run:

```bash
rg -n 'T[B]D|T[O]DO' docs/fts-index-rotation-investigation.md \
  docs/disk-growth-control-results.md
git diff --check
$HOME/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q
git add docs/disk-growth-control-results.md docs/fts-index-rotation-investigation.md
git commit -m "docs: report FTS index rotation findings"
```

Expected: no placeholders or whitespace errors, the complete suite passes, and the report commit contains documentation only.

### Task 4: Deployment boundary and final verification

**Files:**
- Verify only; do not modify the live database or deployed plugin.

- [ ] **Step 1: Confirm the repository diff and commit sequence**

Run:

```bash
git status --short
git log --oneline d2016a4..HEAD
```

Expected: clean worktree and separate design, plan, implementation, and report commits.

- [ ] **Step 2: Confirm all three runtimes remain pinned**

Run:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python -c \
  'import lancedb; print(lancedb.__version__)'
docker exec lancedb-viz python -c \
  'import lancedb; print(lancedb.__version__)'
rg -n '^lancedb==0\.34\.0$' requirements.txt
```

Expected: host and container print `0.34.0`; `requirements.txt` contains the unchanged pin.

- [ ] **Step 3: Run the final complete suite**

Run:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 4: Report the deployment limitation**

Do not copy `plugin/store.py` to `~/.hermes/plugins/lancedb/` and do not restart the
gateway. State that deploying the plugin change requires a gateway restart outside
this run. Do not invoke the live reclaim endpoint; the measured reclaim result came
from a disposable copy.
