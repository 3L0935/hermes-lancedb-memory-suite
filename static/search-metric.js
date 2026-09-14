(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  Object.assign(root, api);
})(typeof globalThis === 'object' ? globalThis : this, function () {
  function formatSearchMetric(result, scoreSemantics = '') {
    if (Number.isFinite(result?.distance)) {
      return 'distance: ' + result.distance.toFixed(2);
    }
    if (Number.isFinite(result?.bm25_score)) {
      return 'BM25 score: ' + result.bm25_score.toFixed(2);
    }
    if (!Number.isFinite(result?.score)) return '';
    if (result.score_type === 'bm25') {
      return 'BM25 score: ' + result.score.toFixed(2);
    }
    if (result.score_type === 'rrf' || scoreSemantics === 'rrf_rank_not_probability') {
      return 'RRF rank score: ' + result.score.toFixed(2);
    }
    return 'score: ' + result.score.toFixed(2);
  }

  return { formatSearchMetric };
});
