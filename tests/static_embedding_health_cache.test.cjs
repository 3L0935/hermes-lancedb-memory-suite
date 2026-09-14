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
  assert.equal(parseHealthCache(JSON.stringify({
    version: 2,
    checked_at: '2026-09-14T12:30:00.000Z',
    health: {},
  })), null);
  assert.equal(parseHealthCache(JSON.stringify({version: 1, health: {}})), null);
  assert.equal(parseHealthCache(JSON.stringify({
    version: 1,
    checked_at: 'not-a-date',
    health: {},
  })), null);
  assert.equal(parseHealthCache(JSON.stringify({
    version: 1,
    checked_at: '2026-09-14T12:30:00.000Z',
    health: [],
  })), null);
});

test('storage failures become cache misses without blocking fresh data', () => {
  const storage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };

  assert.equal(readHealthCache(storage), null);
  assert.equal(
    writeHealthCache(storage, {read_only: true}, '2026-09-14T12:30:00.000Z'),
    null,
  );
});
