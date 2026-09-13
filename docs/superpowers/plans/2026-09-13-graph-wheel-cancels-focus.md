# Graph Wheel Cancels Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore persistent graph wheel zoom by making wheel input cancel node-focus animation and removing every explicit automatic camera fit outside node clicks.

**Architecture:** Keep vis-network responsible for zoom calculation. A capture-phase wheel listener freezes any in-flight camera animation through the public `moveTo` API before vis-network handles the same wheel event; existing explicit `fitGraph` paths are removed while the initial stabilization framing remains intact.

**Tech Stack:** Vanilla JavaScript, vis-network, Python `unittest` source-contract tests, T3 collaborative browser.

---

### Task 1: Lock the camera interaction contract

**Files:**
- Modify: `tests/test_viz_retention.py`

- [ ] **Step 1: Write the failing regression test**

Add this test next to `test_graph_ui_selects_a_memory_without_reloading_the_graph`:

```python
def test_graph_wheel_cancels_focus_and_only_node_click_moves_camera(self):
    html = (ROOT / "static" / "index.html").read_text()
    graph = (ROOT / "static" / "graph.js").read_text()

    load_graph = re.search(r"async function loadGraph\(\)[\s\S]*?\n}", graph).group(0)
    close_sidebar = re.search(r"function closeSidebar\(\)[\s\S]*?\n}", graph).group(0)
    select_memory = re.search(r"function selectMemory\(nodeId\)[\s\S]*?\n}", graph).group(0)
    cancel_animation = re.search(
        r"function cancelCameraAnimation\(\)[\s\S]*?\n}", graph
    ).group(0)

    self.assertNotIn("fitGraph(", graph)
    self.assertNotIn("network.fit(", load_graph)
    self.assertNotIn("network.fit(", close_sidebar)
    self.assertIn("focusNode(nodeId)", select_memory)
    self.assertIn("network.getViewPosition()", cancel_animation)
    self.assertIn("network.getScale()", cancel_animation)
    self.assertIn("network.moveTo(", cancel_animation)
    self.assertIn("animation: false", cancel_animation)
    self.assertIn(
        "addEventListener('wheel', cancelCameraAnimation, { capture: true, passive: true })",
        graph,
    )
    self.assertIn('/static/graph.js?7', html)
```

- [ ] **Step 2: Run the test and verify the intended failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" \
  "$HOME/.hermes/hermes-agent/venv/bin/python" -m pytest \
  tests/test_viz_retention.py::VizRetentionTests::test_graph_wheel_cancels_focus_and_only_node_click_moves_camera -v
```

Expected: `ERROR` because `cancelCameraAnimation()` does not exist yet, or `FAIL` because `fitGraph(` remains in `graph.js`.

- [ ] **Step 3: Commit the failing contract test**

```bash
git add tests/test_viz_retention.py
git commit -m "test: lock graph camera interaction contract"
```

### Task 2: Make wheel input authoritative

**Files:**
- Modify: `static/graph.js`
- Modify: `static/index.html`

- [ ] **Step 1: Remove automatic camera fitting outside node selection**

Delete the `if (!selectedNodeId) fitGraph();` block at the end of `loadGraph()`, delete the `fitGraph();` call in `closeSidebar()`, and delete the full `fitGraph(animate = true)` helper. Keep this node-selection path unchanged:

```javascript
function selectMemory(nodeId) {
  selectedNodeId = nodeId;
  closeSearchPanel();
  openSidebar(nodeId);
  focusNode(nodeId);
}

function focusNode(nodeId) {
  if (network) network.focus(nodeId, { scale: 1.5, animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
}
```

- [ ] **Step 2: Cancel camera animation before vis-network handles wheel zoom**

Add this helper after `focusNode()`:

```javascript
function cancelCameraAnimation() {
  if (!network) return;
  network.moveTo({
    position: network.getViewPosition(),
    scale: network.getScale(),
    animation: false,
  });
}
```

Register it with the other initialization listeners:

```javascript
document.getElementById('graph-canvas').addEventListener('wheel', cancelCameraAnimation, { capture: true, passive: true });
```

Capture phase ensures cancellation runs before vis-network's target handler. Passive mode ensures the listener cannot consume the native wheel event.

- [ ] **Step 3: Bust the corrected graph script cache**

Change only the graph script URL in `static/index.html`:

```html
<script src="/static/graph.js?7"></script>
```

- [ ] **Step 4: Run the targeted regression test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" \
  "$HOME/.hermes/hermes-agent/venv/bin/python" -m pytest \
  tests/test_viz_retention.py::VizRetentionTests::test_graph_wheel_cancels_focus_and_only_node_click_moves_camera -v
```

Expected: `OK`, one test passed.

- [ ] **Step 5: Run the complete visualizer regression file**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" \
  "$HOME/.hermes/hermes-agent/venv/bin/python" -m pytest tests/test_viz_retention.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Verify the behavior in the browser**

Reload the graph page, invoke `selectMemory(nodes.getIds()[0])`, dispatch a wheel event on `#graph-canvas canvas` before the 400 ms focus finishes, then sample `network.getScale()` through at least 500 ms. Expected: the scale changes on wheel and remains at that value after the focus animation's former completion time. Record the scale immediately before wheel, immediately after wheel, and after 500 ms.

Also capture the scale, call `closeSidebar()`, wait 500 ms, and capture it again. Expected: both values are equal.

- [ ] **Step 7: Inspect and commit the implementation**

```bash
git diff --check
git diff -- static/graph.js static/index.html tests/test_viz_retention.py
git add static/graph.js static/index.html
git commit -m "fix: let graph wheel input cancel node focus"
```
