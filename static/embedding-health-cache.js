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
      typeof value.checked_at === 'string' &&
      !Number.isNaN(Date.parse(value.checked_at)) &&
      value.health && typeof value.health === 'object' &&
      !Array.isArray(value.health);
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
    const record = {
      version: HEALTH_CACHE_VERSION,
      checked_at: checkedAt,
      health,
    };
    if (!validRecord(record)) return null;
    try {
      storage.setItem(HEALTH_CACHE_KEY, JSON.stringify(record));
      return record;
    } catch (_) {
      return null;
    }
  }

  return {
    HEALTH_CACHE_KEY,
    parseHealthCache,
    readHealthCache,
    writeHealthCache,
  };
});
