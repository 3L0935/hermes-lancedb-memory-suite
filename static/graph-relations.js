(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  const RELATION_VISUALS = Object.freeze({
    part_of: { color: '#22d3ee', family: 'structure' },
    extends: { color: '#38bdf8', family: 'structure' },
    depends: { color: '#a78bfa', family: 'dependency' },
    requires: { color: '#e879f9', family: 'dependency' },
    runs_on: { color: '#34d399', family: 'integration' },
    uses: { color: '#2dd4bf', family: 'integration' },
    connects_to: { color: '#60a5fa', family: 'integration' },
    supersedes: { color: '#fbbf24', family: 'evolution' },
    invalidates: { color: '#fb7185', family: 'conflict' },
    contradicts: { color: '#f43f5e', family: 'conflict' },
  });
  const FALLBACK_VISUAL = Object.freeze({
    color: '#94a3b8',
    family: 'other',
  });

  function relationVisual(type) {
    return RELATION_VISUALS[type] || FALLBACK_VISUAL;
  }

  function buildGraphUrl({ threshold, clusterMode, showDeclared, relationType }) {
    const params = new URLSearchParams({
      threshold: String(threshold),
      cluster: String(clusterMode),
      show_declared: showDeclared ? '1' : '0',
    });
    if (showDeclared && relationType) {
      params.set('relation_types', relationType);
    }
    return '/api/graph?' + params.toString();
  }

  function relationControlState(showDeclared, relationType) {
    if (!showDeclared) {
      return { disabled: true, accent: '#64748b', family: 'off' };
    }
    if (!relationType) {
      return {
        disabled: false,
        accent: 'linear-gradient(135deg,#22d3ee,#a78bfa 48%,#f43f5e)',
        family: 'all',
      };
    }
    const visual = relationVisual(relationType);
    return {
      disabled: false,
      accent: visual.color,
      family: visual.family,
    };
  }

  return {
    RELATION_VISUALS,
    buildGraphUrl,
    relationControlState,
    relationVisual,
  };
});
