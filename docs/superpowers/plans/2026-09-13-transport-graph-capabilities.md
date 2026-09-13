# Transport, Graph, and Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the visualization server concurrent and bounded, align graph semantics, add correct HTTP compression/validation, ship UMAP in the production image, and make clustering deterministic and diagnostically honest.

**Architecture:** Keep the standard-library HTTP server and make request stores thread-confined. Protect only store/cache publication and heavy-work admission state, leaving calculations outside locks. Preserve every graph node while bounding only semantic edges, and expose sampling/budget/concentration metadata through the existing APIs and vanilla-JS UI.

**Tech Stack:** Python 3 stdlib HTTP server, LanceDB 0.34.0, NumPy, umap-learn 0.5.x, vanilla JavaScript, Docker Compose.

---

### Task 1: P4 concurrent transport and safe shared state

**Files:** `server/server.py`, `tests/test_viz_retention.py`, `tests/test_server_security.py`, `README.md`, and both copies of `lancedb-memory-system`.

- [ ] Add event-driven failing tests for request-local stores, stale cache publication, and bounded heavy-work admission.
- [ ] Run only those tests and record the pre-fix failures.
- [ ] Use `ThreadingHTTPServer`; serialize store construction/reset while returning a request-local handle; implement generation/calculate/publish stats caching; admit at most two graph/projection calculations with a bounded wait and an explicit 503 response.
- [ ] Run focused tests, then an eight-client copied-database benchmark capturing small-route latency, RSS, errors, versions, fragments, and bytes.
- [ ] Update operational documentation and commit once with a message explaining why concurrency is safe and bounded.

### Task 2: P6 bounded semantic graph

**Files:** `server/server.py`, `static/index.html`, `static/graph.js`, `tests/test_viz_retention.py`, `README.md`, and both skill copies.

- [ ] Add failing behavior tests for API default/`th` alias, mutual top-eight semantic edges, isolated nodes, metadata, and hub-mode threshold semantics.
- [ ] Run the focused tests and record failures.
- [ ] Default to `0.8`, accept `threshold` and documented `th`, retain all nodes, and keep only mutual top-eight semantic edges; never budget declared edges or hub connectors.
- [ ] Display the edge policy and returned counts in the graph UI; run focused tests and copied-database measurements at representative thresholds.
- [ ] Update documentation and commit once with a message explaining the semantic-edge budget.

### Task 3: P7 negotiated gzip and validators

**Files:** `server/server.py`, `tests/test_server_security.py`, `README.md`, and both skill copies.

- [ ] Add failing HTTP tests for `Accept-Encoding` quality values, `Vary`, real `Content-Length`, static content ETags, 304 revalidation, and modification invalidation.
- [ ] Run the focused tests and record failures.
- [ ] Serialize compact JSON, negotiate gzip only when acceptable, generate representation-correct ETags, retain `no-store` for memory JSON, and use `private, no-cache` for mutable static URLs.
- [ ] Run focused tests and measure raw/gzip body bytes and local compression time on the copied corpus.
- [ ] Update documentation and commit once with a message explaining transfer reduction without overstating CPU savings.

### Task 4: P5 production UMAP image

**Files:** Hub `requirements.txt`/compose only if required, `static/index.html`, `static/app.js`, `tests/test_viz_retention.py`, `README.md`, and both skill copies.

- [ ] Add a failing contract/UI test that requires the sole optional dependency and visible sample-limit/selection disclosure.
- [ ] Run the focused test and record failure.
- [ ] Keep `umap-learn>=0.5,<0.6` as the only new Python dependency and expose the 500-point deterministic selection policy in API/UI text.
- [ ] Build via the Hub Compose context, verify `import umap` inside the candidate image, recreate the container with its mounts/network/environment preserved, and validate finite bounded points against a copied database.
- [ ] Commit repository/Hub changes once for P5 with a message explaining the stale-image dependency gap.

### Task 5: P10 deterministic and candid clustering

**Files:** `plugin/store.py`, `server/server.py`, `static/app.js`, `tests/test_store_retention.py`, `tests/test_viz_retention.py`, `README.md`, and both skill copies.

- [ ] Add failing behavior tests for stable ID ordering, a local RNG unaffected by global/thread activity, and cluster diagnostics (largest-group concentration, coverage, isolated memories, low-discrimination state).
- [ ] Run focused tests and record failures.
- [ ] Make cluster input/order deterministic, return groups plus diagnostics, and render concentration/coverage/isolation with a low-discrimination warning without recommending a threshold.
- [ ] Run focused tests and copied-database repeated-call measurements.
- [ ] Update documentation, propagate the skill byte-identically, commit once, then run the full Python suite, both JS syntax checks, invariant checks, diff/secret review, and confirm a clean unpushed tree.
