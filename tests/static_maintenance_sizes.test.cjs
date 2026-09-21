const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8')
  .replace(/updateTopStats\(\);\s*loadDashboard\(\);\s*$/, '');
const container = {};
const context = vm.createContext({
  document: { getElementById: () => container },
  escapeHtml: String,
  escapeHtmlAttr: String,
});
vm.runInContext(source, context);

test('missing storage measurements stay unknown rather than becoming zero', () => {
  for (const value of [undefined, null, '', NaN]) {
    assert.equal(context.formatBytes(value), 'Unknown');
  }
  assert.equal(context.formatBytes(0), '0 B');
});

test('maintenance reports signed database growth even for legacy zero-reclaimed state', () => {
  assert.equal(context.maintenanceSizeChange({
    database_bytes_before: 1024,
    database_bytes_after: 3072,
    actual_reclaimed_bytes: 0,
  }), '+2.0 KiB database growth');
  assert.equal(context.maintenanceSizeChange({ database_delta_bytes: -1024 }), '−1.0 KiB database reclaimed');
  assert.equal(context.maintenanceSizeChange({ database_delta_bytes: 0 }), '0 B database change');
  assert.equal(context.maintenanceSizeChange({ actual_reclaimed_bytes: 0 }), 'Database change unknown');
});

test('health does not promise recovery of protected history or a smaller database', () => {
  context.renderHealth({
    storage: { database_bytes: 2048, active_bytes_estimate: 1024, overhead_bytes_estimate: 1024 },
    maintenance_estimate: { estimated_after_bytes: null, estimated_reclaimable_bytes: null },
    last_maintenance: { last_success_at: '2026-09-21', database_delta_bytes: 1024, total_footprint_delta_bytes: 2048 },
  }, '2026-09-21T12:00:00Z', true);
  assert.match(container.innerHTML, /1.0 KiB overhead estimate/);
  assert.match(container.innerHTML, /not an estimate of reclaimable space/);
  assert.match(container.innerHTML, /Database size after maintenance is unknown/);
  assert.match(container.innerHTML, /\+1.0 KiB database growth/);
  assert.match(container.innerHTML, /\+2.0 KiB total footprint growth/);
  assert.doesNotMatch(container.innerHTML, /estimated DB after|0 B reclaimed/);
});

test('total footprint measures backups too and stays unknown for legacy missing totals', () => {
  assert.equal(context.maintenanceSizeChange({
    total_footprint_bytes_before: 3072,
    total_footprint_bytes_after: 1024,
  }, 'total_footprint'), '−2.0 KiB total footprint reclaimed');
  assert.equal(context.maintenanceSizeChange({ database_delta_bytes: 0 }, 'total_footprint'), 'Total footprint change unknown');
});
