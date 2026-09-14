const assert = require('node:assert/strict');
const test = require('node:test');

const { formatSearchMetric } = require('../static/search-metric.js');

test('zero cosine distance is displayed as a real distance', () => {
  assert.equal(formatSearchMetric({ distance: 0, score: 1, score_type: 'exact' }), 'distance: 0.00');
});

test('cosine distance keeps its name when an RRF score also exists', () => {
  assert.equal(
    formatSearchMetric(
      { distance: 0.287, score: 0.0328, score_type: 'rrf' },
      'rrf_rank_not_probability',
    ),
    'distance: 0.29',
  );
});

test('lexical and RRF scores state their semantics', () => {
  assert.equal(
    formatSearchMetric({ distance: null, bm25_score: 13.127, score_type: 'bm25' }),
    'BM25 score: 13.13',
  );
  assert.equal(
    formatSearchMetric(
      { distance: null, score: 0.0328, score_type: 'rrf' },
      'rrf_rank_not_probability',
    ),
    'RRF rank score: 0.03',
  );
});

test('missing metrics render no invented value', () => {
  assert.equal(formatSearchMetric({}), '');
  assert.equal(formatSearchMetric({ distance: null, score: null }), '');
});
