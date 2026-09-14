# Embedding Health Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the last successful local health inspection on the Embeddings page after browser refreshes and style its operational metadata consistently with the UI.

**Architecture:** Add a small UMD/CommonJS module that owns the versioned `localStorage` record and can be tested directly with Node. Keep health markup in `app.js`, but extract one renderer shared by restored and freshly fetched data; page navigation only reads the cache and never triggers `/api/health`.

**Tech Stack:** Vanilla JavaScript, browser `localStorage`, Node `node:test`, HTML/CSS, pytest UI contract tests.

---

### Task 1: Versioned health cache helper

**Files:**
- Create: `static/embedding-health-cache.js`
- Create: `tests/static_embedding_health_cache.test.cjs`

- [ ] **Step 1: Write the failing cache contract tests**

```javascript
const assert = require('node:assert/strict');
const test = require('node:test');
const {
  HEALTH_CACHE_KEY,
  parseHealthCache,
  readHealthCache,
  writeHealthCache,
} = require('../static/embedding-health-cache.js');

function memoryStorage() {
  const values = new Map();
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, value),
  };
}

test('health cache round trips one successful health payload', () => {
  const storage = memoryStorage();
  const health = {read_only: true, tables: {memories: {state: 'ok'}}};
  const record = writeHealthCache(storage, health, '2026-09-14T12:30:00.000Z');
  assert.equal(record.checked_at, '2026-09-14T12:30:00.000Z');
  assert.deepEqual(readHealthCache(storage), record);
  assert.match(HEALTH_CACHE_KEY, /v1$/);
});

test('health cache rejects malformed and incompatible records', () => {
  assert.equal(parseHealthCache('{'), null);
  assert.equal(parseHealthCache(JSON.stringify({version: 2, checked_at: 'x', health: {}})), null);
  assert.equal(parseHealthCache(JSON.stringify({version: 1, health: {}})), null);
  assert.equal(parseHealthCache(JSON.stringify({version: 1, checked_at: 'not-a-date', health: {}})), null);
  assert.equal(parseHealthCache(JSON.stringify({version: 1, checked_at: 'x', health: []})), null);
});

test('storage failures become cache misses without blocking fresh data', () => {
  const storage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  assert.equal(readHealthCache(storage), null);
  assert.equal(writeHealthCache(storage, {read_only: true}, '2026-09-14T12:30:00.000Z'), null);
});
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `node --test tests/static_embedding_health_cache.test.cjs`

Expected: FAIL with `Cannot find module '../static/embedding-health-cache.js'`.

- [ ] **Step 3: Implement the pure versioned cache module**

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const HEALTH_CACHE_VERSION = 1;
  const HEALTH_CACHE_KEY = 'lancedb-viz.health.v1';

  function validRecord(value) {
    return value && value.version === HEALTH_CACHE_VERSION &&
      typeof value.checked_at === 'string' && !Number.isNaN(Date.parse(value.checked_at)) &&
      value.health && typeof value.health === 'object' && !Array.isArray(value.health);
  }

  function parseHealthCache(raw) {
    if (typeof raw !== 'string' || !raw) return null;
    try {
      const value = JSON.parse(raw);
      return validRecord(value) ? value : null;
    } catch (_) {
      return null;
    }
  }

  function readHealthCache(storage) {
    try {
      return parseHealthCache(storage.getItem(HEALTH_CACHE_KEY));
    } catch (_) {
      return null;
    }
  }

  function writeHealthCache(storage, health, checkedAt) {
    const record = {version: HEALTH_CACHE_VERSION, checked_at: checkedAt, health};
    if (!validRecord(record)) return null;
    try {
      storage.setItem(HEALTH_CACHE_KEY, JSON.stringify(record));
      return record;
    } catch (_) {
      return null;
    }
  }

  return {HEALTH_CACHE_KEY, parseHealthCache, readHealthCache, writeHealthCache};
});
```

- [ ] **Step 4: Run the helper tests**

Run: `node --test tests/static_embedding_health_cache.test.cjs`

Expected: 3 tests pass.

- [ ] **Step 5: Commit the helper**

```bash
git add static/embedding-health-cache.js tests/static_embedding_health_cache.test.cjs
git commit -m "feat(embedding): add persistent health cache"
```

### Task 2: Restore and render the latest successful inspection

**Files:**
- Modify: `static/app.js:1-35,431-463`
- Modify: `static/index.html:180-205,385-391`
- Modify: `tests/test_viz_retention.py:920-943`

- [ ] **Step 1: Write the failing UI integration contract**

Add assertions to the existing embedding UI test:

```python
html = (ROOT / "static" / "index.html").read_text()
app = (ROOT / "static" / "app.js").read_text()

self.assertIn('/static/embedding-health-cache.js?1', html)
self.assertIn('function renderHealth(data, checkedAt, cacheStored)', app)
self.assertIn('function restoreCachedHealth()', app)
self.assertRegex(app, r"name === 'embedding'[\s\S]+restoreCachedHealth\(\)[\s\S]+loadEmbedding\(\)")
self.assertIn("writeHealthCache(window.localStorage", app)
self.assertIn("readHealthCache(window.localStorage", app)
self.assertIn("Last check", app)
self.assertIn("Cached locally", app)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent \
  /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/test_viz_retention.py -k embedding -q
```

Expected: FAIL because the cache script and restore/render functions do not exist.

- [ ] **Step 3: Load the helper and restore on Embeddings navigation**

Load `/static/embedding-health-cache.js?1` before `app.js`, bump the `app.js`
cache key, and change the page branch to:

```javascript
else if (name === 'embedding') {
  restoreCachedHealth();
  loadEmbedding();
}
```

- [ ] **Step 4: Extract the shared renderer and add the Last check card**

Move the successful `loadHealth()` markup into:

```javascript
function renderHealth(data, checkedAt, cacheStored) {
  const container = document.getElementById('health-container');
  const tableRows = Object.entries(data.tables || {}).map(([name, table]) =>
    '<tr><td>' + escapeHtml(name) + '</td><td>' + escapeHtml(table.state) + '</td><td>' + escapeHtml(table.rows ?? '—') + '</td><td>' + escapeHtml(table.current_version ?? '—') + ' / ' + escapeHtml(table.versions ?? '—') + '</td><td>' + escapeHtml(table.fragments ?? '—') + '</td></tr>'
  ).join('');
  const fts = data.fts || {};
  const ollama = data.ollama || {};
  const storage = data.storage || {};
  const estimate = data.maintenance_estimate || {};
  const lastMaintenance = data.last_maintenance || {};
  const pipeline = data.pipeline || {};
  const checkedLabel = new Date(checkedAt).toLocaleString();
  const cacheLabel = cacheStored ? 'Cached locally' : 'Browser cache unavailable';
  container.innerHTML =
    '<div class="health-card health-last-check"><span>Last check</span><b>' + escapeHtml(checkedLabel) + '</b><small>' + escapeHtml(cacheLabel) + '</small></div>' +
    '<div class="health-card"><span>Pipeline</span><b>' + escapeHtml(pipeline.model || 'unknown') + '</b><small>' + escapeHtml((pipeline.dimension || '?') + 'd · contract v' + (pipeline.version || '?') + ' · ' + (pipeline.metric || '?')) + '</small></div>' +
    '<div class="health-card"><span>FTS</span><b class="health-' + escapeHtmlAttr(fts.state || 'missing') + '">' + escapeHtml(fts.state || 'missing') + '</b><small>' + escapeHtml(fts.num_unindexed_rows ?? 'unknown') + ' unindexed rows</small></div>' +
    '<div class="health-card"><span>Database</span><b>' + formatBytes(storage.database_bytes) + '</b><small>' + formatBytes(storage.active_bytes_estimate) + ' active estimate · ' + formatBytes(storage.reclaimable_bytes_estimate) + ' reclaimable estimate</small></div>' +
    '<div class="health-card"><span>Backups</span><b>' + formatBytes(storage.managed_backup_bytes) + '</b><small>' + formatBytes(storage.total_footprint_bytes) + ' database + managed backups</small></div>' +
    '<div class="health-card"><span>Ollama</span><b class="health-' + escapeHtmlAttr(ollama.state || 'error') + '">' + escapeHtml(ollama.state || 'error') + '</b><small>' + escapeHtml(ollama.error || ('HTTP ' + (ollama.status || '?'))) + '</small></div>' +
    '<div class="health-card"><span>Last maintenance</span><b>' + escapeHtml(lastMaintenance.last_success_at || 'Never') + '</b><small>' + (lastMaintenance.last_success_at ? formatBytes(lastMaintenance.actual_reclaimed_bytes) + ' reclaimed · ' + formatBytes(lastMaintenance.backup_created_bytes) + ' backup' : 'No successful run recorded') + '</small></div>' +
    '<div class="health-table"><table class="data-table"><thead><tr><th>Table</th><th>State</th><th>Rows</th><th>Version / history</th><th>Fragments</th></tr></thead><tbody>' + tableRows + '</tbody></table></div>' +
    '<div class="health-estimate">Before maintenance: backup ' + formatBytes(estimate.backup_bytes) + ' · estimated DB after ' + formatBytes(estimate.estimated_after_bytes) + ' · reclaimable ' + formatBytes(estimate.estimated_reclaimable_bytes) + ' (estimate only)</div>';
}
```

Implement restoration without a request:

```javascript
function restoreCachedHealth() {
  const record = readHealthCache(window.localStorage);
  if (record) renderHealth(record.health, record.checked_at, true);
}
```

On a successful fetch, capture `new Date().toISOString()`, call
`writeHealthCache(window.localStorage, data, checkedAt)`, and pass whether that
write returned a record to `renderHealth`. Leave the `catch` branch unchanged so
failures render an error but never overwrite storage.

- [ ] **Step 5: Run the focused integration and helper tests**

Run:

```bash
node --test tests/static_embedding_health_cache.test.cjs
PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent \
  /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/test_viz_retention.py -k embedding -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit health restoration**

```bash
git add static/app.js static/index.html tests/test_viz_retention.py
git commit -m "feat(embedding): restore last health inspection"
```

### Task 3: Integrate the projection policy visually and verify end to end

**Files:**
- Modify: `static/index.html:9,193-200`
- Modify: `static/style.css:382-390,559-564`
- Modify: `tests/test_viz_retention.py:920-943`

- [ ] **Step 1: Write the failing policy styling contract**

```python
css = (ROOT / "static" / "style.css").read_text()
self.assertIn('id="projection-policy" class="projection-policy"', html)
self.assertIn('.projection-policy {', css)
self.assertIn('health-last-check', css)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent \
  /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/test_viz_retention.py -k embedding -q
```

Expected: FAIL because `.projection-policy` is not defined.

- [ ] **Step 3: Add the compact policy badge and last-check accent**

Add `class="projection-policy"` to `#projection-policy`, bump the stylesheet cache
key, and add:

```css
.projection-policy {
  margin-left: auto;
  padding: 4px 8px;
  border: 1px solid rgba(99,102,241,.16);
  border-radius: 999px;
  background: rgba(99,102,241,.045);
  color: var(--dim);
  font-size: 9px;
  letter-spacing: .02em;
  white-space: nowrap;
}
.health-last-check > b { color: var(--accent); }
```

Add the narrow-screen override:

```css
@media(max-width:640px) {
  .projection-policy { margin-left:0; white-space:normal; }
}
```

- [ ] **Step 4: Run JavaScript syntax, focused, and full tests**

Run:

```bash
node --check static/embedding-health-cache.js
node --check static/app.js
node --test tests/static_*.test.cjs
PYTHONPATH=$PWD:/home/elo/.hermes/hermes-agent \
  /home/elo/.hermes/hermes-agent/venv/bin/python -m pytest -q
git diff --check
```

Expected: JavaScript checks pass, all Node subtests pass, the full pytest suite
passes, and `git diff --check` prints nothing.

- [ ] **Step 5: Deploy and verify persistence in the browser**

Run:

```bash
./scripts/deploy-local.sh --dry-run
./scripts/deploy-local.sh
./scripts/verify-setup.sh
```

In the collaborative browser on port 7777:

1. Open Embeddings and run `Inspect local health`.
2. Confirm `Last check` appears with a local date/time.
3. Reload the browser.
4. Reopen Embeddings and confirm the same inspection is restored immediately.
5. Confirm the projection policy is visually muted and no new console error is
   present.

- [ ] **Step 6: Commit the UI integration**

```bash
git add static/index.html static/style.css tests/test_viz_retention.py
git commit -m "style(embedding): integrate health metadata"
```
