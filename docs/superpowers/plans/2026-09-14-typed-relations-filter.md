# Typed Relations Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the typed-relation overlay filter correctly and render resolved relation types with a restrained, consistent visual language.

**Architecture:** The server remains authoritative for which declared edges are returned and for edge counts. A small dependency-free JavaScript helper owns query construction and relation visual metadata so it can be unit-tested in Node and reused by the graph and sidebar UI.

**Tech Stack:** Python standard library server, unittest/pytest, browser JavaScript, Node test runner, vis-network, CSS.

---

## File Structure

- Create `static/graph-relations.js`: pure query, palette, and control-state helpers with browser and CommonJS exports.
- Create `tests/static_graph_relations.test.cjs`: Node unit tests for the pure helpers.
- Modify `server/server.py`: filter resolved overview relations and report complete available types plus filtered counts.
- Modify `tests/test_viz_retention.py`: server and static integration regression coverage.
- Modify `static/graph.js`: use the helper for requests, controls, edge rendering, and sidebar accents.
- Modify `static/index.html`: load the helper and group the typed-relation controls.
- Modify `static/style.css`: style the joined control and palette-driven sidebar rows.

### Task 1: Server-side overview filtering

**Files:**
- Modify: `tests/test_viz_retention.py`
- Modify: `server/server.py`

- [ ] **Step 1: Write the failing overview-filter test**

Extend the graph tests with a store containing two resolved types and one unresolved target. Assert that the all-types view returns both resolved edges, while `relation_types={"depends"}` returns only `depends`, preserves both available types, and reports one edge hidden by the filter:

```python
def test_global_graph_filters_resolved_declared_relations_by_type(self):
    nodes = [{"id": "aaaaaaaa-aaa"}, {"id": "bbbbbbbb-bbb"}, {"id": "cccccccc-ccc"}]
    typed = [
        {"from": nodes[0]["id"], "to": nodes[1]["id"], "relation_type": "depends"},
        {"from": nodes[0]["id"], "to": nodes[2]["id"], "relation_type": "extends"},
        {"from": nodes[0]["id"], "to": "", "relation_type": "part_of"},
    ]
    store = SimpleNamespace(get_typed_edges=lambda include_unresolved=False: typed)

    with patch.object(server, "_compute_vector_data",
                      return_value=(nodes, [], {}, None, [], 0.8)), \
         patch.object(server, "_get_store", return_value=store):
        all_relations = server.get_graph_data(threshold=0.8, show_declared=True)
        depends_only = server.get_graph_data(
            threshold=0.8, show_declared=True, relation_types={"depends"}
        )

    self.assertEqual(2, len(all_relations["typed_edges"]))
    self.assertEqual(["depends", "extends"], all_relations["available_relation_types"])
    self.assertEqual(0, all_relations["hidden_by_relation_filter"])
    self.assertEqual(["depends"], [edge["relation_type"] for edge in depends_only["typed_edges"]])
    self.assertEqual(["depends", "extends"], depends_only["available_relation_types"])
    self.assertEqual(1, depends_only["hidden_by_relation_filter"])
    self.assertEqual(1, depends_only["edge_policy"]["declared"]["returned_edges"])
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest -q tests/test_viz_retention.py -k global_graph_filters_resolved_declared_relations_by_type`

Expected: FAIL because the global overview ignores `relation_types` and omits `hidden_by_relation_filter`.

- [ ] **Step 3: Implement resolved-edge collection and filtering**

In the overview branch of `get_graph_data()`, collect drawable relations before applying the optional filter:

```python
resolved_declared: list[dict] = []
available_relation_types: set[str] = set()
if show_declared:
    try:
        for relation in store.get_typed_edges(include_unresolved=True):
            source = str(relation.get("from") or "")
            target = str(relation.get("to") or "")
            if source not in by_id or target not in by_id:
                continue
            relation_type = str(relation.get("relation_type") or "linked")
            available_relation_types.add(relation_type)
            resolved_declared.append({
                "from": source,
                "to": target,
                "kind": "declared",
                "relation_type": relation_type,
                "label": relation_type,
                "directed": True,
            })
    except Exception:
        pass

declared = [
    edge for edge in resolved_declared
    if relation_types is None or edge["relation_type"] in relation_types
]
for edge in declared:
    by_id[edge["from"]]["has_declared_relations"] = True
    by_id[edge["to"]]["has_declared_relations"] = True
hidden_by_relation_filter = len(resolved_declared) - len(declared)
```

Return `sorted(available_relation_types)` and `hidden_by_relation_filter` in the response.

- [ ] **Step 4: Run graph regression tests**

Run: `pytest -q tests/test_viz_retention.py -k graph`

Expected: PASS.

- [ ] **Step 5: Commit the server fix**

```bash
git add server/server.py tests/test_viz_retention.py
git commit -m "fix(graph): filter declared relations by type"
```

### Task 2: Testable query and relation visual helpers

**Files:**
- Create: `static/graph-relations.js`
- Create: `tests/static_graph_relations.test.cjs`
- Modify: `static/index.html`

- [ ] **Step 1: Write failing Node helper tests**

Create `tests/static_graph_relations.test.cjs`:

```javascript
const assert = require('node:assert/strict');
const test = require('node:test');

const {
  buildGraphUrl,
  relationControlState,
  relationVisual,
} = require('../static/graph-relations.js');

test('graph URL sends one selected relation type only for an enabled overlay', () => {
  assert.equal(
    buildGraphUrl({threshold: '0.8', clusterMode: 'raw', showDeclared: true, relationType: 'depends'}),
    '/api/graph?threshold=0.8&cluster=raw&show_declared=1&relation_types=depends',
  );
  assert.equal(
    buildGraphUrl({threshold: '0.8', clusterMode: 'raw', showDeclared: true, relationType: ''}),
    '/api/graph?threshold=0.8&cluster=raw&show_declared=1',
  );
  assert.equal(
    buildGraphUrl({threshold: '0.8', clusterMode: 'raw', showDeclared: false, relationType: 'depends'}),
    '/api/graph?threshold=0.8&cluster=raw&show_declared=0',
  );
});

test('relation visuals group types semantically and retain a fallback', () => {
  assert.equal(relationVisual('part_of').family, 'structure');
  assert.equal(relationVisual('depends').family, 'dependency');
  assert.equal(relationVisual('connects_to').family, 'integration');
  assert.equal(relationVisual('supersedes').family, 'evolution');
  assert.equal(relationVisual('contradicts').family, 'conflict');
  assert.equal(relationVisual('custom_relation').family, 'other');
});

test('typed relation selector is disabled only while the overlay is off', () => {
  assert.deepEqual(relationControlState(false, 'depends'), {
    disabled: true,
    accent: '#64748b',
    family: 'off',
  });
  assert.equal(relationControlState(true, '').family, 'all');
  assert.equal(relationControlState(true, 'depends').family, 'dependency');
});
```

- [ ] **Step 2: Run the Node test and verify it fails**

Run: `node --test tests/static_graph_relations.test.cjs`

Expected: FAIL because `static/graph-relations.js` does not exist.

- [ ] **Step 3: Implement the pure helper module**

Create `static/graph-relations.js` as a browser/CommonJS module. Define the approved palette, URL encoding, fallback, all-types gradient state, and off state:

```javascript
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  const RELATION_VISUALS = Object.freeze({
    part_of: {color: '#22d3ee', family: 'structure'},
    extends: {color: '#38bdf8', family: 'structure'},
    depends: {color: '#a78bfa', family: 'dependency'},
    requires: {color: '#e879f9', family: 'dependency'},
    runs_on: {color: '#34d399', family: 'integration'},
    uses: {color: '#2dd4bf', family: 'integration'},
    connects_to: {color: '#60a5fa', family: 'integration'},
    supersedes: {color: '#fbbf24', family: 'evolution'},
    invalidates: {color: '#fb7185', family: 'conflict'},
    contradicts: {color: '#f43f5e', family: 'conflict'},
  });
  const FALLBACK_VISUAL = Object.freeze({color: '#94a3b8', family: 'other'});

  function relationVisual(type) {
    return RELATION_VISUALS[type] || FALLBACK_VISUAL;
  }

  function buildGraphUrl({threshold, clusterMode, showDeclared, relationType}) {
    const params = new URLSearchParams({
      threshold: String(threshold),
      cluster: String(clusterMode),
      show_declared: showDeclared ? '1' : '0',
    });
    if (showDeclared && relationType) params.set('relation_types', relationType);
    return '/api/graph?' + params.toString();
  }

  function relationControlState(showDeclared, relationType) {
    if (!showDeclared) return {disabled: true, accent: '#64748b', family: 'off'};
    if (!relationType) return {
      disabled: false,
      accent: 'linear-gradient(135deg,#22d3ee,#a78bfa 48%,#f43f5e)',
      family: 'all',
    };
    const visual = relationVisual(relationType);
    return {disabled: false, accent: visual.color, family: visual.family};
  }

  return {RELATION_VISUALS, buildGraphUrl, relationControlState, relationVisual};
});
```

- [ ] **Step 4: Load the helper before the graph script**

Add `<script src="/static/graph-relations.js?1"></script>` immediately before the cache-busted `graph.js` script in `static/index.html`.

- [ ] **Step 5: Run all static JavaScript tests**

Run: `pytest -q tests/test_static_javascript.py`

Expected: PASS, including the new Node test discovered by `tests/test_static_javascript.py`.

- [ ] **Step 6: Commit the helper**

```bash
git add static/graph-relations.js static/index.html tests/static_graph_relations.test.cjs
git commit -m "feat(graph): add typed relation visual helpers"
```

### Task 3: Integrate filtering and styling into the graph UI

**Files:**
- Modify: `static/graph.js`
- Modify: `static/index.html`
- Modify: `static/style.css`
- Modify: `tests/test_viz_retention.py`

- [ ] **Step 1: Add failing static integration assertions**

Extend `test_graph_ui_selects_a_memory_without_reloading_the_graph()` to assert the helper script and joined control exist, and that `loadGraph()` uses `buildGraphUrl`. Add assertions that the sidebar assigns `--relation-color` and graph rendering calls `relationVisual`.

```python
self.assertIn('id="typed-relation-control"', html)
self.assertIn('/static/graph-relations.js?1', html)
self.assertIn("buildGraphUrl({", load_graph)
self.assertIn("relationVisual(relationType)", graph)
self.assertIn("--relation-color", graph)
```

- [ ] **Step 2: Run the focused UI contract test and verify it fails**

Run: `pytest -q tests/test_viz_retention.py -k graph_ui_selects_a_memory`

Expected: FAIL because the grouped control and helper integration are not present.

- [ ] **Step 3: Group and synchronize the controls**

Wrap the checkbox label and select in `#typed-relation-control`. Add `syncRelationControl()` in `static/graph.js` to set `select.disabled`, `data-family`, and `--relation-accent` from `relationControlState()`. Call it at the beginning of every graph load.

- [ ] **Step 4: Use the helper-built request URL**

Replace manual query concatenation in `loadGraph()` with:

```javascript
const relationType = document.getElementById('relation-filter')?.value || '';
const query = buildGraphUrl({
  threshold,
  clusterMode,
  showDeclared,
  relationType,
});
```

- [ ] **Step 5: Apply the restrained edge styling**

For declared edges, obtain `const visual = relationVisual(e.relation_type || e.label || 'linked')` and use its color with `1.35px` width, `0.72` opacity, `0.38` arrow scale, `0.08` curve roundness, and compact dark-backed labels. Reduce semantic edges to `0.85px` and a quieter dashed indigo-gray.

- [ ] **Step 6: Apply the same palette to sidebar relations**

When rendering each typed relation row, set `style="--relation-color:<escaped color>"`. Replace the fixed violet typed-link, arrow, and badge colors in CSS with `var(--relation-color)` and `color-mix()` variants. Keep shared embedding badges cyan and visually secondary.

- [ ] **Step 7: Style the joined control and bump static cache keys**

Add CSS for a 29px joined dark control, its status dot, enabled accent, disabled state, and internal select border removal. Increment the `style.css` and `graph.js` query versions in `static/index.html` so the deployed browser cannot retain the old rendering.

- [ ] **Step 8: Run focused and full verification**

Run:

```bash
pytest -q tests/test_viz_retention.py -k graph
pytest -q tests/test_static_javascript.py
pytest -q
```

Expected: all commands PASS.

- [ ] **Step 9: Inspect browser states**

Deploy/restart only through the repository's normal local workflow if required, then inspect the collaborative preview in three states: overlay off, all resolved types, and `depends`. Confirm the declared count changes from 84 to 28 on the current corpus, non-`depends` declared colors disappear, semantic context remains, and no browser errors are introduced.

- [ ] **Step 10: Commit the UI integration**

```bash
git add static/graph.js static/index.html static/style.css tests/test_viz_retention.py
git commit -m "feat(graph): integrate typed relation filtering"
```
