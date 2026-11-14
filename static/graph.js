// ═══════════════════════════════════════════════
// LanceDB Memory Graph — Néon Edition
// ═══════════════════════════════════════════════

// State
let nodes = new vis.DataSet();
let edges = new vis.DataSet();
let network = null;
let allData = { nodes: [], edges: [], typed_edges: [] };
let selectedNodeId = null;
let highlightedTypedEdges = new Set(); // node IDs highlighted for typed relations
let graphIntersectionVisible = false;
const MAX_EDITOR_FACTS = 12;
const MAX_EDITOR_RELATIONS = 20;
const GRAPH_LAYOUT_RANDOM_SEED = 20260913;
// Canvas shadowBlur is the most expensive thing vis-network draws, and a freshly
// rebuilt memory is almost entirely "< 1h old": every node glowed, every frame.
// Only the freshest memories keep their halo; the border still carries freshness.
const GRAPH_GLOW_BUDGET = 60;
// Ticks of the off-screen layout pass. The layout is computed once, then frozen.
const GRAPH_STABILIZATION_ITERATIONS = 400;
let glowingIds = new Set();
let nodeById = new Map();
// True once the layout has settled. Physics then stays off: a settled graph costs
// nothing at rest instead of re-simulating and redrawing the canvas forever.
let graphLayoutSettled = false;
// Dragging a memory pulls its neighbourhood along: direct links follow closely,
// second-degree links follow loosely, everything else stays put. Done by moving
// positions directly, not by restarting the simulation, which made the whole
// graph drift whenever one node was moved.
const DRAG_FOLLOW_STRENGTH = [1, 0.75, 0.35];
const DRAG_FOLLOW_MAX = 200;
let dragFollowers = null;
// A new set of links (cluster mode, threshold, typed overlay) is a new layout: it
// is animated live so the memories visibly sort themselves, then frozen once it
// comes to rest or after this long.
const GRAPH_LIVE_LAYOUT_MS = 30000;
let liveLayoutTimer = null;
let lastEdgeSignature = '';

// Category colours: the Phosphor UV palette of tokens.css (--cat-*). The canvas
// cannot read CSS variables, so the values are repeated here. bg is the node core
// (the colour at 14% over the page), glow drives the freshness halo.
const catColors = {
  user_pref:  { bg: '#2B1F35', border: '#E39BFF', label: '#EBB9FF', hex: '#E39BFF', glow: 'rgba(227,155,255,1.0)' },
  project:    { bg: '#182727', border: '#5BD69B', label: '#8CE2B9', hex: '#5BD69B', glow: 'rgba(91,214,155,1.0)' },
  tech:       { bg: '#2F231B', border: '#FFB547', label: '#FFCB7E', hex: '#FFB547', glow: 'rgba(255,181,71,1.0)' },
  correction: { bg: '#2F1822', border: '#FF6B78', label: '#FF97A0', hex: '#FF6B78', glow: 'rgba(255,107,120,1.0)' },
  fact:       { bg: '#18292E', border: '#5FE3D0', label: '#8FEBDE', hex: '#5FE3D0', glow: 'rgba(95,227,208,1.0)' },
  decision:   { bg: '#231D35', border: '#A98BFF', label: '#C3AEFF', hex: '#A98BFF', glow: 'rgba(169,139,255,1.0)' },
  insight:    { bg: '#252920', border: '#B9E26A', label: '#CEEB97', hex: '#B9E26A', glow: 'rgba(185,226,106,1.0)' },
  reference:  { bg: '#1A2335', border: '#6CB6FF', label: '#98CCFF', hex: '#6CB6FF', glow: 'rgba(108,182,255,1.0)' },
  pattern:    { bg: '#2F1F1E', border: '#FF9A5C', label: '#FFB88D', hex: '#FF9A5C', glow: 'rgba(255,154,92,1.0)' },
  question:   { bg: '#272335', border: '#C9B6FF', label: '#D9CCFF', hex: '#C9B6FF', glow: 'rgba(201,182,255,1.0)' },
};
const defaultColor = { bg: '#1E1A2E', border: '#A98BFF', label: '#C9B6FF', hex: '#A98BFF', glow: 'rgba(169,139,255,0.8)' };
const catLabels = {
  user_pref: 'Pref', project: 'Project', tech: 'Tech',
  correction: 'Correction', fact: 'Fact', decision: 'Decision',
  insight: 'Insight', reference: 'Reference', pattern: 'Pattern', question: 'Question',
};
// Hex-only colors for canvas/scatter (used by app.js)
const colorHex = Object.fromEntries(Object.entries(catColors).map(([k,v]) => [k, v.hex]));
const colorLabel = catLabels;
const colorHexDef = defaultColor.hex;

// Single source of truth for a node's resting style. This used to be duplicated in
// three places with different freshness buckets, so deselecting a node could leave
// it styled differently from how it had been drawn.
function restingNodeStyle(n) {
  const isHub = n.node_type === 'hub';
  const c = isHub
    ? { bg: '#1A1825', border: '#A98BFF', label: '#C9B6FF', glow: 'rgba(169,139,255,0.5)' }
    : (catColors[n.category] || defaultColor);

  // Freshness — glow driven by the category colour, the newer the brighter.
  let ageBorder = c.border, ageGlow = c.glow, ageBW = 2, ageShadow = 10, ageLabel = '';
  if (!isHub && n.created_at) {
    const ageHours = (Date.now()/1000 - n.created_at) / 3600;
    if (ageHours < 1)        { ageBorder = '#ffffff'; ageGlow = c.glow.replace('1.0','0.75'); ageBW = 3; ageShadow = 20; ageLabel = ' ◉'; }
    else if (ageHours < 6)   { ageBorder = c.border;  ageGlow = c.glow.replace('1.0','0.55'); ageBW = 3; ageShadow = 14; }
    else if (ageHours < 24)  { ageBorder = c.border;  ageGlow = c.glow.replace('1.0','0.4');  ageBW = 2; ageShadow = 10; }
    else if (ageHours < 72)  { ageBorder = c.border;  ageGlow = c.glow.replace('1.0','0.25'); ageBW = 2; ageShadow = 7; }
    else if (ageHours < 168) { ageBorder = c.border;  ageGlow = c.glow.replace('1.0','0.15'); ageBW = 2; ageShadow = 5; }
    else if (ageHours < 720) { ageBorder = c.border;  ageGlow = c.glow.replace('1.0','0.08'); ageBW = 1.5; ageShadow = 3; }
    else                     { ageBorder = c.border;  ageGlow = 'rgba(0,0,0,0)';             ageBW = 1.5; ageShadow = 0; }
  }

  const sz = isHub ? 32 : Math.max(22, (n.size || 20) + Math.min((n.access_count || 0) * 0.5, 8));

  return {
    id: n.id, label: (n.label || '(empty)') + ageLabel, title: n.title || '',
    color: {
      background: c.bg,
      border: ageBorder,
      highlight: { background: c.bg, border: '#ffffff' },
      hover: { background: c.bg, border: '#ffffff' },
    },
    font: { color: c.label, size: isHub ? 13 : 11, face: 'Geist Mono, ui-monospace, monospace', strokeWidth: 0 },
    size: sz, shape: isHub ? 'box' : 'dot',
    borderWidth: ageBW, borderWidthSelected: 3,
    shadow: { enabled: ageShadow > 0 && (isHub || glowingIds.has(n.id)), color: ageGlow, size: ageShadow, x: 0, y: 0 },
    opacity: 1.0,
    hidden: false,
    _category: n.category, _tier: n.tier || 'none',
    _entities: (n.entities || []).join(' ').toLowerCase(),
    _content: (n.label || '').toLowerCase(), _created: n.created_at || 0,
  };
}

// ═══════════════════════════════════════════════
// Load + Render
// ═══════════════════════════════════════════════

function showGraphLoadState(message, spinning = true) {
  const loading = document.getElementById('loading');
  loading.style.display = 'block';
  loading.querySelector('.spinner').style.display = spinning ? 'block' : 'none';
  document.getElementById('graph-loading-message').textContent = message;
}

async function loadGraph() {
  showGraphLoadState('Building memory graph...');
  const threshold = document.getElementById('threshold-slider')?.value || 0.8;
  const clusterMode = document.getElementById('cluster-mode')?.value || 'raw';
  const showDeclared = Boolean(document.getElementById('show-declared')?.checked);
  const relationType = document.getElementById('relation-filter')?.value || '';
  syncRelationControl();
  // The graph is ALWAYS the whole corpus. It is never reloaded as a per-memory
  // sub-graph: that replaced the overview with a handful of nodes on every click
  // and left no way back. Selecting a memory opens the sidebar over this graph.
  const query = buildGraphUrl({
    threshold,
    clusterMode,
    showDeclared,
    relationType,
  });
  try {
    const nextData = await fetchJsonWithBusyRetry(query, {
      onBusy: ({retryAfterMs}) => {
        showGraphLoadState('Server busy. Retrying in ' + (retryAfterMs / 1000) + ' s...');
      },
    });
    allData = nextData;
    renderGraph();
    if (selectedNodeId) {
      if (allData.nodes?.some(node => node.id === selectedNodeId)) highlightTypedRelations(selectedNodeId);
      else selectedNodeId = null;
    }
  } catch (e) {
    console.error('Failed to load graph:', e);
    showGraphLoadState(e instanceof BusyHttpError
      ? e.message
      : 'Graph request failed: ' + e.message, false);
  }
}

function renderGraph() {
  document.getElementById('loading').style.display = 'none';

  // Stats
  fetch('/api/stats').then(r => r.json()).then(stats => {
    if (stats.total_memories != null) setEl('mem-count', stats.total_memories);
    if (stats.total_entities != null) setEl('ent-count', stats.total_entities);
    if (stats.total_edges != null) setEl('edge-count', stats.total_edges);
    if (stats.db_size_mb != null) setEl('db-size', stats.db_size_mb + ' MB');
  }).catch(() => {});

  // Global counts come from /api/stats (applied by the fetch above), never from
  // the loaded graph: the header describes the whole database. The sub-label only
  // reports how many memories are currently glowing for the selection.
  const subGraphLabel = document.getElementById('subgraph-count');
  if (subGraphLabel) {
    const glowCount = highlightedTypedEdges.size;
    subGraphLabel.textContent = selectedNodeId && glowCount
      ? 'Selected · ' + glowCount + ' related memories highlighted'
      : '';
  }
  const fleg = document.getElementById('fresh-legend');
  if (fleg) fleg.style.display = 'flex';

  // Build vis.js nodes with neon glow, capped to the freshest memories.
  const memoryNodes = allData.nodes || [];
  nodeById = new Map(memoryNodes.map(n => [n.id, n]));
  glowingIds = new Set(memoryNodes
    .filter(n => n.node_type !== 'hub' && n.created_at)
    .sort((a, b) => b.created_at - a.created_at)
    .slice(0, GRAPH_GLOW_BUDGET)
    .map(n => n.id));
  const visNodes = memoryNodes.map(restingNodeStyle);

  const visEdges = (allData.edges || []).map(e => {
    if (e.kind === 'declared') {
      const relationType = e.relation_type || e.label || 'linked';
      const visual = relationVisual(relationType);
      return {
        from: e.from, to: e.to, label: e.label || '', kind: 'declared',
        color: {
          color: visual.color,
          highlight: visual.color,
          hover: visual.color,
          opacity: 0.72,
        },
        width: 1.4,
        dashes: false,
        // Horizontal labels: text rotated along the edge was unreadable on
        // steep links. The arrowhead is sized to be seen at the default zoom,
        // and the line stops at its base instead of running through it.
        font: {
          color: visual.color,
          size: 9,
          face: 'Geist Mono, ui-monospace, monospace',
          strokeWidth: 0,
          background: 'rgba(13,11,20,0.85)',
          align: 'horizontal',
        },
        arrows: {to: {enabled: true, type: 'arrow', scaleFactor: 0.65}},
        arrowStrikethrough: false,
        smooth: {type: 'curvedCW', roundness: 0.12},
      };
    }
    // Semantic links are drawn plain: no score label (a thousand of them on screen
    // is noise, and the sidebar lists the scores), no dash pattern, no curve. The
    // score stays readable on hover.
    return {
      from: e.from, to: e.to, title: e.label ? 'similarity ' + e.label : '', kind: 'semantic',
      color: {
        color: '#5A5470',
        highlight: '#A98BFF',
        hover: '#C9B6FF',
        opacity: 0.4,
      },
      width: 0.85,
      dashes: false,
      arrows:{to:{enabled:false}}, smooth: false,
    };
  });

  if (network) {
    // Update in-place, batched. These two loops issued one update per node, so a
    // refresh or a graph reload redrew the canvas hundreds of times in a row.
    const oldIds = new Set(nodes.getIds());
    const newIds = new Set(visNodes.map(n => n.id));
    const removed = [...oldIds].filter(id => !newIds.has(id));
    if (removed.length) nodes.remove(removed);
    const added = [], changed = [];
    for (const n of visNodes) { (oldIds.has(n.id) ? changed : added).push(n); }
    if (added.length) nodes.add(added);
    if (changed.length) nodes.update(changed);
    edges.clear(); edges.add(visEdges);
    const signature = graphEdgeSignature(visEdges);
    if (signature !== lastEdgeSignature) {
      lastEdgeSignature = signature;
      startLiveLayout();
    } else if (added.length) {
      // Same links, a few new memories: place them, keep everyone else where they are.
      settleGraphLayout(Math.min(GRAPH_STABILIZATION_ITERATIONS, 150));
    }
  } else {
    lastEdgeSignature = graphEdgeSignature(visEdges);
    nodes.clear(); edges.clear();
    nodes.add(visNodes); edges.add(visEdges);

    network = new vis.Network(document.getElementById('graph-canvas'), { nodes, edges }, {
      nodes: { scaling: { min: 10, max: 45 } },
      physics: {
        enabled: true,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -40,
          centralGravity: 0.005,
          springLength: 160,
          springConstant: 0.004,
          damping: 0.98,
        },
        minVelocity: 0.75,
        stabilization: { iterations: GRAPH_STABILIZATION_ITERATIONS, updateInterval: 50, fit: true },
      },
      // Edges are hidden while the camera moves: panning and zooming stay fluid
      // whatever the number of links.
      interaction: { hover: true, zoomView: true, dragView: true, hideEdgesOnDrag: true, hideEdgesOnZoom: true, tooltipDelay: 200 },
      // improvedLayout runs Kamada-Kawai on a clustered copy of the graph before the
      // first frame; with hundreds of memories that alone took about a second.
      layout: { randomSeed: GRAPH_LAYOUT_RANDOM_SEED, improvedLayout: false },
    });
    graphLayoutSettled = false;

    // The layout is computed off-screen, then frozen.
    network.on('stabilizationIterationsDone', freezeGraphLayout);
    network.on('stabilized', freezeGraphLayout);
    network.on('dragStart', params => {
      dragFollowers = params.nodes.length ? collectDragFollowers(params.nodes[0]) : null;
    });
    network.on('dragging', () => {
      if (!dragFollowers) return;
      const at = network.getPosition(dragFollowers.id);
      const dx = at.x - dragFollowers.origin.x, dy = at.y - dragFollowers.origin.y;
      for (const f of dragFollowers.nodes) network.moveNode(f.id, f.x + dx * f.k, f.y + dy * f.k);
    });
    network.on('dragEnd', () => {
      if (!dragFollowers) return;
      // Persist the moved positions so a later data reload keeps them.
      const ids = [dragFollowers.id, ...dragFollowers.nodes.map(f => f.id)];
      const pos = network.getPositions(ids);
      nodes.update(ids.map(id => ({ id, x: pos[id].x, y: pos[id].y })));
      dragFollowers = null;
    });

    network.on('click', function(params) {
      if (params.nodes.length) {
        selectMemory(params.nodes[0]);
      } else {
        closeSidebar();
      }
    });
  }

  applyFilters();
  setGraphPhysicsActive(graphIntersectionVisible && !document.hidden);
}

// Selecting a memory is purely local: open the sidebar and glow its neighbourhood.
// The graph itself is not reloaded, so the overview stays on screen and you can
// click through memories without ever losing it.
function selectMemory(nodeId) {
  selectedNodeId = nodeId;
  closeSearchPanel();
  openSidebar(nodeId);
  focusNode(nodeId);
}

// Breadth-first over the drawn edges, two levels deep, with the follow strength of
// each level. Bounded so dragging a hub with hundreds of children stays instant.
function collectDragFollowers(id) {
  const start = network.getPositions();
  const seen = new Set([id]);
  const followers = [];
  let frontier = [id];
  for (let depth = 1; depth < DRAG_FOLLOW_STRENGTH.length && frontier.length; depth++) {
    const next = [];
    for (const from of frontier) {
      for (const other of network.getConnectedNodes(from)) {
        if (seen.has(other) || !start[other] || followers.length >= DRAG_FOLLOW_MAX) continue;
        seen.add(other);
        next.push(other);
        followers.push({ id: other, x: start[other].x, y: start[other].y, k: DRAG_FOLLOW_STRENGTH[depth] });
      }
    }
    frontier = next;
  }
  return { id, origin: start[id], nodes: followers };
}

function graphEdgeSignature(visEdges) {
  return visEdges.map(e => e.from + '>' + e.to).sort().join('|');
}

function startLiveLayout() {
  if (!network) return;
  graphLayoutSettled = false;
  clearTimeout(liveLayoutTimer);
  liveLayoutTimer = setTimeout(freezeGraphLayout, GRAPH_LIVE_LAYOUT_MS);
  // Started for real by setGraphPhysicsActive() at the end of renderGraph, which
  // also keeps it paused while the graph is off screen.
}

function freezeGraphLayout() {
  if (!network) return;
  clearTimeout(liveLayoutTimer);
  graphLayoutSettled = true;
  network.stopSimulation();
  network.setOptions({physics: {enabled: false}});
}

function settleGraphLayout(iterations) {
  if (!network) return;
  graphLayoutSettled = false;
  network.setOptions({physics: {enabled: true}});
  network.stabilize(iterations);
}

// Visibility only pauses or resumes a layout that has not settled yet. A settled
// graph never restarts its simulation on its own.
function setGraphPhysicsActive(active) {
  if (!network) return;
  if (active && !graphLayoutSettled) {
    network.setOptions({physics: {enabled: true}});
    network.startSimulation();
  } else {
    network.stopSimulation();
    if (graphLayoutSettled) network.setOptions({physics: {enabled: false}});
  }
}

function pauseGraphPhysics() { setGraphPhysicsActive(false); }
function resumeGraphPhysics() { setGraphPhysicsActive(graphIntersectionVisible && !document.hidden); }

const graphObserver = new IntersectionObserver(entries => {
  graphIntersectionVisible = entries.some(entry => entry.isIntersecting);
  setGraphPhysicsActive(graphIntersectionVisible && !document.hidden);
}, {threshold: 0.01});
graphObserver.observe(document.getElementById('graph-container'));
document.addEventListener('visibilitychange', () => setGraphPhysicsActive(graphIntersectionVisible && !document.hidden));

// ═══════════════════════════════════════════════
// Typed edge highlighting (shown only on selection)
// ═══════════════════════════════════════════════

function highlightTypedRelations(nodeId) {
  // Reset previous highlights
  resetTypedHighlights();
  if (!nodeId) return;

  // Glow the selected node and its neighbours. Nothing is masked: the highlight is
  // additive so the whole graph stays readable while a memory is selected.
  const relatedIds = new Set([nodeId]);
  (allData.edges || []).forEach(e => {
    if (e.from === nodeId && e.to) relatedIds.add(e.to);
    if (e.to === nodeId && e.from) relatedIds.add(e.from);
  });

  // Cap the glow: at a low threshold a node can have hundreds of neighbours and
  // lighting all of them up is noise, not information. slice(1, 26) keeps up to 25
  // neighbours alongside the selected node.
  const ordered = [...relatedIds].slice(1, 26);
  const glowIds = new Set([nodeId, ...ordered]);

  highlightedTypedEdges = glowIds;

  // Apply violet glow to connected nodes. Batched: one update per node made a click
  // on a well-connected memory redraw the canvas 25 times.
  const updates = [];
  glowIds.forEach(id => {
    const node = nodeById.get(id);
    if (!node) return;
    updates.push({
      id: id,
      borderWidth: 3,
      borderWidthSelected: 4,
      shadow: { enabled: true, color: 'rgba(169,139,255,0.55)', size: 14, x: 0, y: 0 },
      color: {
        background: node.node_type === 'hub' ? '#1A1825' : (catColors[node.category] || defaultColor).bg,
        border: '#A98BFF',
        highlight: { border: '#C9B6FF' },
        hover: { border: '#E4DAFF' },
      },
    });
  });
  if (updates.length) nodes.update(updates);
}

function resetTypedHighlights() {
  if (!highlightedTypedEdges.size) return;
  // One batched update. This ran nodes.update() once per highlighted node, and each
  // call redraws the whole canvas.
  const restored = [];
  highlightedTypedEdges.forEach(id => {
    const n = nodeById.get(id);
    if (n) restored.push(restingNodeStyle(n));
  });
  if (restored.length) nodes.update(restored);
  highlightedTypedEdges.clear();
}

// ═══════════════════════════════════════════════
// Sidebar (inspiré memviz — détail enrichi)
// ═══════════════════════════════════════════════

function openSidebar(nodeId) {
  selectedNodeId = nodeId;
  const sidebar = document.getElementById('sidebar');
  const content = document.getElementById('sidebar-content');
  sidebar.classList.add('open');

  const n = allData.nodes.find(x => x.id === nodeId);
  if (!n) return;

  // Highlight typed relations on graph
  highlightTypedRelations(nodeId);

  if (n.node_type === 'hub') { renderHubSidebar(content, nodeId, n); return; }

  const cat = catLabels[n.category] || n.category || 'Fact';
  const dateStr = n.created_at ? new Date(+n.created_at * 1000).toLocaleString('en-US') : '--';

  // Fetch full detail from API
  fetch('/api/memory?id=' + nodeId)
    .then(r => r.json())
    .then(data => {
      if (data.error) { content.innerHTML = '<div class="detail-meta" style="color:red;">' + escapeHtml(data.error) + '</div>'; return; }
      const fullContent = data.content || n.title || n.label || '';
      const category = data.category || n.category || 'fact';
      const catLabel = catLabels[category] || category || 'Fact';
      const fullEntities = data.entities || n.entities || [];
      const linkedMemories = data.linked_memories || [];
      const quality = typeof data.quality === 'number' ? data.quality : null;
      const accessCount = data.access_count || 0;

      // Read current threshold slider value
      const currentThreshold = parseFloat(document.getElementById('threshold-slider')?.value || 0.8);

      // Build typed edges for this node
      const nodeTypedEdges = (allData.typed_edges || []).filter(e => e.from === nodeId || e.to === nodeId);
      // Build vector edges, filtered by threshold
      const nodeVecEdges = (allData.edges || []).filter(e => {
        if (e.from !== nodeId && e.to !== nodeId) return false;
        const score = parseFloat(e.label) || 0;
        return score >= currentThreshold;
      });

      const typedEdgesCount = nodeTypedEdges.length;
      const nEdges = nodeVecEdges.length;

      renderSidebarContent(content, nodeId, fullContent, category, catLabel, dateStr, fullEntities, linkedMemories, nodeTypedEdges, nodeVecEdges, quality, accessCount, typedEdgesCount, nEdges, data.structured || null, data.updated_at);
    })
    .catch(() => {
      renderSidebarContent(content, nodeId, n.title || n.label || '', n.category || 'fact', cat, dateStr, n.entities || [], [], [], []);
    });
}

function renderSidebarContent(content, nodeId, fullContent, category, catLabel, dateStr, entities, linkedMemories, typedEdges, vectorEdges, quality, accessCount, typedEdgesCount, nEdges, structured, updatedAt) {
  // ─── Stats bar ───
  const n = allData.nodes.find(x => x.id === nodeId);
  const ageHours = n && n.created_at ? (Date.now()/1000 - n.created_at) / 3600 : null;
  let freshnessLabel = 'Old', freshnessColor = '#555';
  if (ageHours !== null) {
    if (ageHours < 1)       { freshnessLabel = '< 1h'; freshnessColor = '#ffffff'; }
    else if (ageHours < 6)  { freshnessLabel = Math.floor(ageHours) + 'h'; freshnessColor = '#C9B6FF'; }
    else if (ageHours < 24) { freshnessLabel = Math.floor(ageHours) + 'h'; freshnessColor = '#A98BFF'; }
    else if (ageHours < 72) { freshnessLabel = Math.floor(ageHours/24) + 'd'; freshnessColor = '#8D88A3'; }
    else                    { freshnessLabel = Math.floor(ageHours/24) + 'd'; freshnessColor = '#6E6A85'; }
  }

  const q = quality !== null ? quality : 0;
  const qColor = q >= 0.8 ? '#5BD69B' : q >= 0.5 ? '#FFB547' : '#FF6B78';
  const relCount = typedEdgesCount + nEdges;

  let statsHtml = '<div class="detail-stats">' +
    '<div class="stat-item"><span class="stat-dot" style="background:' + freshnessColor + ';box-shadow:0 0 8px ' + freshnessColor + ';"></span><span class="stat-val">' + freshnessLabel + '</span><span class="stat-lbl">Freshness</span></div>' +
    '<div class="stat-item"><span class="stat-val" style="color:' + qColor + '">' + Math.round(q * 100) + '%</span><span class="stat-lbl">Quality</span></div>' +
    '<div class="stat-item"><span class="stat-val">' + accessCount + '</span><span class="stat-lbl">Views</span></div>' +
    '<div class="stat-item"><span class="stat-val">' + relCount + '</span><span class="stat-lbl">Links</span></div>' +
    '</div>';

  // Entities
  let entityChips = '';
  if (entities.length) {
    entityChips = '<div class="detail-meta" style="margin-top:8px;">Entities (' + entities.length + ')</div><div class="detail-ents">' +
      entities.map(e => '<span onclick="searchEntity(\'' + escapeJsString(e) + '\')">' + escapeHtml(e) + '</span>').join('') +
      '</div>';
  }

  // ─── Relations: fusionner typedEdges + linkedMemories ───
  // Map typed edges by target ID → relation type
  const typedMap = {};
  typedEdges.forEach(e => {
    const otherId = e.from === nodeId ? e.to : e.from;
    typedMap[otherId] = { type: e.relation_type || e.type || 'linked', dir: e.from === nodeId ? '→' : '←' };
  });

  // Merge linked memories with their type info
  let relatedHtml = '';
  const seenRelated = new Set(); // avoid duplicates
  const relatedItems = [];

  // First: typed edges (have explicit type)
  typedEdges.forEach(e => {
    const otherId = e.from === nodeId ? e.to : e.from;
    if (seenRelated.has(otherId)) return;
    seenRelated.add(otherId);
    const otherNode = nodeById.get(otherId);
    const otherLabel = otherNode ? (otherNode.label || otherNode.title || otherId).substring(0, 50) : otherId;
    const relType = e.relation_type || e.type || 'linked';
    const dir = e.from === nodeId ? '→' : '←';
    relatedItems.push({
      id: otherId, label: otherLabel, type: relType, dir: dir,
      cat: otherNode?.category || 'fact', simScore: null,
    });
  });

  // Then: linked memories NOT already in typed
  (linkedMemories || []).forEach(lm => {
    if (seenRelated.has(lm.id)) return;
    seenRelated.add(lm.id);
    // Find common entities
    const nodeEntities = new Set(entities.map(e => e.toLowerCase()));
    const lmEntities = (lm.entities || []).map(e => e.toLowerCase());
    const common = lmEntities.filter(e => nodeEntities.has(e));
    let relType, dir;
    if (common.length > 0) {
      relType = 'shared';
      dir = common.slice(0, 2).join(', ');
    } else {
      relType = 'linked';
      dir = '';
    }
    relatedItems.push({
      id: lm.id, label: lm.content || '',
      type: relType, dir: dir,
      cat: lm.category || 'fact', simScore: null,
    });
  });

  if (relatedItems.length) {
    relatedHtml = '<div class="detail-links-list"><div class="detail-meta">Related (' + relatedItems.length + ')</div>';
    relatedItems.forEach(item => {
      const typeClass = item.type === 'shared' ? 'typed-tag-shared' : 'typed-tag';
      const relationColor = item.type === 'shared' ? '#5FE3D0' : relationVisual(item.type).color;
      relatedHtml += '<div class="detail-link-item typed-link" style="--relation-color:' + relationColor + '" onclick="openSidebar(\'' + escapeJsString(item.id) + '\')">' +
        '<span class="cat-badge cat-' + safeCategory(item.cat) + '" style="font-size:9px;padding:1px 6px;margin-bottom:0">' + escapeHtml(catLabels[safeCategory(item.cat)] || '?') + '</span>' +
        '<span class="' + typeClass + '">' + escapeHtml(item.type) + '</span>' +
        (item.dir ? ' <span class="typed-arrow">' + escapeHtml(item.dir) + '</span>' : '') +
        ' ' + memoryHtml(item.label, 90) +
        '</div>';
    });
    relatedHtml += '</div>';
  }

  // ─── Similar (vector cosine) — filtered by threshold ───
  let vecHtml = '';
  if (vectorEdges.length) {
    vecHtml = '<div class="detail-links-list"><div class="detail-meta" style="color:var(--acc-2);">Similar by embedding (' + vectorEdges.length + ')</div>';
    vectorEdges.sort((a,b) => {
      const sa = parseFloat(a.label) || 0;
      const sb = parseFloat(b.label) || 0;
      return sb - sa;
    });
    vectorEdges.slice(0, 10).forEach(e => {
      const otherId = e.from === nodeId ? e.to : e.from;
      const otherNode = nodeById.get(otherId);
      const otherLabel = otherNode ? (otherNode.label || otherNode.title || otherId).substring(0, 50) : otherId;
      const simScore = parseFloat(e.label);
      const simColor = simScore > 0.9 ? '#5BD69B' : simScore > 0.8 ? '#C9B6FF' : '#8D88A3';
      vecHtml += '<div class="detail-link-item" onclick="openSidebar(\'' + escapeJsString(otherId) + '\')">' +
        '<span class="cat-badge cat-' + safeCategory(otherNode?.category) + '" style="font-size:9px;padding:1px 6px;margin-bottom:0">' + escapeHtml(catLabels[safeCategory(otherNode?.category)] || '?') + '</span>' +
        escapeHtml(otherLabel) +
        '<span style="float:right;color:' + simColor + ';font-size:10px;font-weight:600;">' + (simScore*100).toFixed(0) + '%</span>' +
        '</div>';
    });
    vecHtml += '</div>';
  }

  content.innerHTML =
    '<div class="cat-badge cat-' + safeCategory(category) + '">' + escapeHtml(catLabel) + '</div>' +
    statsHtml +
    '<div class="detail-content" id="detail-text">' + memoryHtml(fullContent, 0, true) + '</div>' +
    '<div class="detail-meta" style="margin-top:4px;">' + escapeHtml(dateStr) + '</div>' +
    entityChips +
    vecHtml +
    relatedHtml +
    '<div class="detail-actions">' +
      '<button class="btn-focus" onclick="focusNode(\'' + escapeJsString(nodeId) + '\')">Center</button>' +
      '<button class="btn-focus" onclick="toggleEdit()">Edit</button>' +
      '<button class="btn-focus btn-copy" onclick="copyMemoryJSON(\'' + escapeJsString(nodeId) + '\')">Copy JSON</button>' +
      '<button class="btn-delete" onclick="deleteNode(\'' + escapeJsString(nodeId) + '\')">Delete</button>' +
    '</div>';

  content.dataset.nodeId = nodeId;
  content.dataset.fullContent = fullContent;
  content.dataset.category = category;
  content.dataset.entities = JSON.stringify(entities);
  content.dataset.structured = JSON.stringify(structured || {});
  content.dataset.updatedAt = String(updatedAt || 0);
}

function renderHubSidebar(content, hubId, hubNode) {
  const children = [];
  [...(allData.edges || [])].forEach(e => {
    if (e.from === hubId) children.push(e.to);
    if (e.to === hubId) children.push(e.from);
  });
  const seen = new Set();
  const childNodes = [];
  children.forEach(cid => {
    if (seen.has(cid)) return;
    seen.add(cid);
    const cn = nodeById.get(cid);
    if (cn && cn.node_type !== 'hub') childNodes.push(cn);
  });
  childNodes.sort((a,b) => ((a.category || '').localeCompare(b.category || '') || (a.label || '').localeCompare(b.label || '')));

  let itemsHtml = childNodes.length === 0
    ? '<div class="detail-meta" style="color:var(--mute);">No linked nodes.</div>'
    : childNodes.map(cn => {
        const lcat = catLabels[cn.category] || cn.category || 'Fact';
        const disp = (cn.title || cn.label || '')[0] === '?' ? (cn.title || cn.label || '').substring(1).trim() : (cn.title || cn.label || '');
        return '<div class="detail-link-item" onclick="openSidebar(\'' + escapeJsString(cn.id) + '\')">' +
          '<span class="cat-badge cat-' + safeCategory(cn.category) + '" style="font-size:10px;padding:1px 6px;margin-bottom:0">' + escapeHtml(lcat) + '</span>' +
          escapeHtml(disp) + '</div>';
      }).join('');

  content.innerHTML =
    '<div class="cat-badge cat-' + safeCategory(hubNode.category) + '">Hub</div>' +
    '<div style="font-family:var(--mono);font-size:13px;color:var(--acc-2);margin-bottom:6px;">' + escapeHtml(hubNode.label || '') + '</div>' +
    '<div class="detail-meta" style="margin-bottom:12px;">' + escapeHtml(hubNode.title || hubNode.label || '') + '</div>' +
    '<div class="detail-meta">' + childNodes.length + ' linked memories:</div>' +
    '<div class="detail-links-list" style="margin-top:8px;">' + itemsHtml + '</div>' +
    '<div class="detail-actions" style="margin-top:14px;"><button class="btn-focus" onclick="focusNode(\'' + escapeJsString(hubId) + '\')">Center</button></div>';
}

// ═══════════════════════════════════════════════
// Edit / Delete
// ═══════════════════════════════════════════════

function toggleEdit() {
  const content = document.getElementById('sidebar-content');
  const nodeId = content.dataset.nodeId;
  let current = {};
  try { current = JSON.parse(content.dataset.structured || '{}'); } catch(e) {}
  if (!current.domain) {
    content.innerHTML = '<div class="version-conflict">This legacy row cannot be edited structurally until its format is reviewed.</div>' +
      '<div class="detail-actions"><button class="btn-focus" onclick="openSidebar(\'' + escapeJsString(nodeId) + '\')">Back</button></div>';
    return;
  }
  const relations = JSON.stringify(current.relations || [], null, 2);
  const categories = ['fact','user_pref','project','tech','correction','decision','insight','reference','pattern','question'];

  content.innerHTML =
    '<div class="detail-meta editor-heading">Structured memory · preview required</div>' +
    '<div class="structured-editor" oninput="invalidateEditPreview()">' +
      '<label>Domain<input id="edit-domain" value="' + escapeHtmlAttr(current.domain) + '"></label>' +
      '<label>Subject<input id="edit-subject" value="' + escapeHtmlAttr(current.subject) + '"></label>' +
      '<label>Facts · one per line (max ' + MAX_EDITOR_FACTS + ')<textarea id="edit-facts">' + escapeHtml((current.facts || []).join('\n')) + '</textarea></label>' +
      '<div class="editor-row"><label>Tier<select id="edit-tier">' + [1,2,3].map(t => '<option value="' + t + '" ' + (current.tier === t ? 'selected' : '') + '>' + t + '</option>').join('') + '</select></label>' +
      '<label>Category<select id="edit-category">' + categories.map(c => '<option value="' + c + '" ' + (current.category === c ? 'selected' : '') + '>' + escapeHtml(catLabels[c] || c) + '</option>').join('') + '</select></label></div>' +
      '<label>Relations JSON (max ' + MAX_EDITOR_RELATIONS + ')<textarea id="edit-relations">' + escapeHtml(relations) + '</textarea></label>' +
    '</div>' +
    '<div id="version-conflict" class="version-conflict" hidden></div>' +
    '<div id="edit-diff" class="edit-diff" hidden><div><span>Old</span><pre id="previous-preview"></pre></div><div><span>Canonical new</span><pre id="canonical-preview"></pre></div></div>' +
    '<div class="detail-actions" style="margin-top:12px;">' +
      '<button class="btn-focus" onclick="previewEdit(\'' + escapeJsString(nodeId) + '\')">Preview</button>' +
      '<button id="save-edit" class="btn-save" onclick="saveEdit(\'' + escapeJsString(nodeId) + '\')" disabled>Save canonical</button>' +
      '<button class="btn-focus" onclick="openSidebar(\'' + escapeJsString(nodeId) + '\')">Cancel</button>' +
    '</div>';
  content.dataset.previewReady = '0';
}

function invalidateEditPreview() {
  const content = document.getElementById('sidebar-content');
  content.dataset.previewReady = '0';
  const save = document.getElementById('save-edit');
  if (save) save.disabled = true;
}

function editorPayload() {
  const facts = document.getElementById('edit-facts').value.split('\n').map(v => v.trim()).filter(Boolean);
  const relations = JSON.parse(document.getElementById('edit-relations').value || '[]');
  if (facts.length > MAX_EDITOR_FACTS) throw new Error('At most ' + MAX_EDITOR_FACTS + ' facts are allowed.');
  if (!Array.isArray(relations) || relations.length > MAX_EDITOR_RELATIONS) throw new Error('Relations must be an array of at most ' + MAX_EDITOR_RELATIONS + ' items.');
  return {
    domain: document.getElementById('edit-domain').value,
    subject: document.getElementById('edit-subject').value,
    facts: facts,
    tier: Number(document.getElementById('edit-tier').value),
    category: document.getElementById('edit-category').value,
    relations: relations,
    base_updated_at: Number(document.getElementById('sidebar-content').dataset.updatedAt || 0),
  };
}

function showEditError(data) {
  const error = document.getElementById('version-conflict');
  error.hidden = false;
  error.textContent = data.code === 'version_conflict'
    ? 'Version conflict: the memory changed. Reopen it before editing.'
    : (data.error || 'Invalid structured memory.');
}

async function previewEdit(nodeId) {
  try {
    const response = await fetch('/api/memories/' + nodeId + '/preview', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(editorPayload())});
    const data = await response.json();
    if (data.error) { showEditError(data); return; }
    document.getElementById('version-conflict').hidden = true;
    document.getElementById('previous-preview').textContent = data.previous_content || '';
    document.getElementById('canonical-preview').textContent = data.canonical_content || '';
    document.getElementById('edit-diff').hidden = false;
    document.getElementById('sidebar-content').dataset.previewReady = '1';
    document.getElementById('save-edit').disabled = false;
  } catch(e) { showEditError({error: e.message}); }
}

async function saveEdit(nodeId) {
  if (document.getElementById('sidebar-content').dataset.previewReady !== '1') return;
  try {
    const response = await fetch('/api/memories/' + nodeId, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(editorPayload())});
    const data = await response.json();
    if (data.error) { showEditError(data); invalidateEditPreview(); return; }
    loadGraph(); openSidebar(nodeId);
  } catch(e) { showEditError({error: e.message}); }
}

function closeSidebar() {
  selectedNodeId = null;
  resetTypedHighlights();
  document.getElementById('sidebar').classList.remove('open');
  if (network) network.unselectAll();
  // No applyFilters() here: clicking empty space fires this on every stray click, and
  // the filter state has not changed. Restoring the ~25 glowing nodes already redraws
  // what needs redrawing.
}

function focusNode(nodeId) {
  if (network) network.focus(nodeId, { scale: 1.5, animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
}

// User camera input always wins over an animated node focus. The capture-phase wheel
// listener calls this before vis-network handles the same event, freezing the current
// camera without consuming the wheel zoom that follows.
function cancelCameraAnimation() {
  if (!network) return;
  network.moveTo({
    position: network.getViewPosition(),
    scale: network.getScale(),
    animation: false,
  });
}

function searchEntity(entity) {
  document.getElementById('search').value = entity;
  document.getElementById('cat-filter').value = '';
  applyFilters();
}

async function deleteNode(nodeId) {
  if (!confirm('Supprimer cette mémoire ?')) return;
  try {
    const resp = await fetch('/api/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({memory_id: nodeId}) });
    if ((await resp.json()).error) return;
    closeSidebar(); selectedNodeId = null; loadGraph();
  } catch(e) { console.error('Delete error:', e); }
}

// ═══════════════════════════════════════════════
// Filters — with tier ghost mode (opacity 30%)
// ═══════════════════════════════════════════════

function applyFilters() {
  if (!network) return;
  const searchText = document.getElementById('search').value.toLowerCase().trim();
  const catFilter = document.getElementById('cat-filter').value;
  const t1 = document.getElementById('tier1')?.checked ?? true;
  const t2 = document.getElementById('tier2')?.checked ?? true;
  const t3 = document.getElementById('tier3')?.checked ?? true;

  // The graph always shows every memory. Filters never remove nodes from view:
  // a search or a category highlights what matches and dims the rest, so the
  // overall shape of the corpus stays visible while you look for something.
  const matches = new Set();
  const active = Boolean(searchText) || Boolean(catFilter);
  (allData.nodes || []).forEach(n => {
    const tier = n.tier || 'none';
    const tierOk = (t1 && tier === '1') || (t2 && tier === '2') ||
                   (t3 && tier === '3') || tier === 'none';
    const content = ((n.label || '') + ' ' + (n.title || '')).toLowerCase();
    const entities = (n.entities || []).join(' ').toLowerCase();
    const searchOk = !searchText || content.includes(searchText) || entities.includes(searchText);
    const catOk = !catFilter || n.category === catFilter;
    if (tierOk && searchOk && catOk) matches.add(n.id);
  });

  // One batched update, and only for the nodes whose look actually changes: a
  // keystroke in the search box used to restyle (and redraw) the whole corpus.
  const updates = [];
  nodes.get({fields: ['id', 'opacity', 'borderWidth']}).forEach(node => {
    const isMatch = matches.has(node.id);
    const opacity = active && !isMatch ? 0.18 : 1.0;
    // Highlighted nodes get a thicker border so a search reads at a glance.
    const borderWidth = active && isMatch ? 2.5 : 1;
    if (node.opacity !== opacity || node.borderWidth !== borderWidth) {
      updates.push({ id: node.id, hidden: false, opacity, borderWidth });
    }
  });
  if (updates.length) nodes.update(updates);
  updateBudgetLabel(matches.size, active);
}

function updateBudgetLabel(matchCount, filtered) {
  const el = document.getElementById('hidden-neighbor-count');
  if (!el) return;
  const total = (allData.nodes || []).filter(node => node.node_type !== 'hub').length;
  const policy = allData.edge_policy || {};
  const semantic = policy.semantic || {};
  const declared = policy.declared || {};
  const hubs = policy.hubs || {};
  const hidden = allData.hidden_neighbor_count || 0;
  const byFilter = allData.hidden_by_relation_filter || 0;
  let text = total + ' memories · ' + (semantic.returned_edges || 0) +
    ' semantic links (mutual top ' + (semantic.top_k || 8) +
    ', max ' + (semantic.max_edges || 1200) + ')';
  if (declared.returned_edges) text += ' · ' + declared.returned_edges + ' declared';
  if (hubs.returned_edges) text += ' · ' + hubs.returned_edges + ' hub links';
  if (filtered) text += ' · ' + matchCount + ' match';
  if (hidden) text += ' · ' + hidden + ' hidden by budget';
  if (byFilter) text += ' · ' + byFilter + ' hidden by relation filter';
  el.textContent = text;
}

function syncRelationControl() {
  const control = document.getElementById('typed-relation-control');
  const checkbox = document.getElementById('show-declared');
  const select = document.getElementById('relation-filter');
  if (!control || !checkbox || !select) return;
  const state = relationControlState(checkbox.checked, select.value);
  select.disabled = state.disabled;
  control.dataset.family = state.family;
  control.style.setProperty('--relation-accent', state.accent);
}

function countEntities(nodesArr) {
  if (!nodesArr) return '—';
  const ents = new Set();
  nodesArr.forEach(n => (n.entities || []).forEach(e => ents.add(e)));
  return ents.size;
}

// ═══════════════════════════════════════════════
// Semantic search
// ═══════════════════════════════════════════════

async function doSemanticSearch() {
  const query = document.getElementById('search').value.trim();
  if (!query) return;
  const panel = document.getElementById('search-panel');
  closeWhyPanel();
  panel.classList.add('visible');
  panel.innerHTML = '<div class="search-empty">Searching...</div>';
  try {
    const resp = await fetch('/api/search?q=' + encodeURIComponent(query) + '&top_k=15&diagnostics=1');
    const data = await resp.json();
    if (data.error) { panel.innerHTML = '<div class="search-empty">Error: ' + escapeHtml(data.error) + '</div>'; return; }
    if (!data.results.length) {
      lastSearchDiagnostics = data;
      panel.innerHTML = '<div class="search-empty">No reliable result — ' +
        escapeHtml(data.abstention_reason || 'below_calibrated_evidence') + '. ' +
        '<button class="btn-neon btn-focus" onclick="explainResult(null)">Why?</button></div>';
      return;
    }
    lastSearchDiagnostics = data;
    panel.innerHTML = data.results.map(r => {
      const cat = catLabels[r.category] || r.category || 'Fact';
      const metric = formatSearchMetric(r, data.diagnostics?.score_semantics);
      return '<div class="search-item">' +
        '<span class="s-cat cat-' + safeCategory(r.category) + '">' + escapeHtml(cat) + '</span>' +
        (metric ? '<span class="s-meta">' + metric + '</span>' : '') +
        '<span class="s-why"><button class="why-btn" title="Why this result?" onclick="event.stopPropagation();explainResult(\'' + escapeJsString(r.id) + '\')">?</button></span>' +
        '<div class="s-content" onclick="selectMemory(\'' + escapeJsString(r.id) + '\')">' + memoryHtml(r.content, 140) + '</div></div>';
    }).join('');
  } catch(e) { panel.innerHTML = '<div class="search-empty">Search failed.</div>'; }
}

// ═══════════════════════════════════════════════
// D6 — Why this result? (consumes the C2 diagnostics as-is)
// ═══════════════════════════════════════════════

// The panel renders at most this many result rows; the diagnostics block is a
// fixed contract, so the surface stays bounded like the other viz panels.
const WHY_PANEL_MAX_ROWS = 5;

let lastSearchDiagnostics = null;

function closeWhyPanel() {
  const panel = document.getElementById('why-panel');
  if (!panel) return;
  panel.classList.remove('visible');
  panel.innerHTML = '';
}

/**
 * Explain one search outcome using only what the server already computed.
 * `resultId` selects a row; null explains the abstention of the whole query.
 * Nothing is recomputed here: no cosine similarity, no BM25 scoring.
 */
function explainResult(resultId) {
  const panel = document.getElementById('why-panel');
  if (!panel) return;
  const data = lastSearchDiagnostics;
  if (!data) { return; }
  const diagnostics = data.diagnostics || {};
  const rows = (data.results || []);
  const selected = resultId ? rows.find(r => String(r.id) === String(resultId)) : null;

  const line = (label, value) =>
    '<div class="why-row"><span class="why-label">' + escapeHtml(label) + '</span>' +
    '<span class="why-value">' + escapeHtml(String(value)) + '</span></div>';

  let body = '<div class="why-head"><b>Why this result?</b>' +
    '<button class="why-close" onclick="closeWhyPanel()" aria-label="Close">×</button></div>';

  body += '<div class="why-section"><h4>Route</h4>';
  body += line('route', data.route || 'unknown');
  body += line('degraded', data.degraded ? (data.degraded_reason || 'yes') : 'no');
  body += line('abstained', data.abstained ? (data.abstention_reason || 'yes') : 'no');
  body += '</div>';

  if (selected) {
    body += '<div class="why-section"><h4>This row</h4>';
    body += line('retrieval_source', selected.retrieval_source || 'direct');
    body += line('match_type', selected.match_type || diagnostics.metric || 'bm25');
    body += line('metric', selected.metric || diagnostics.metric || 'bm25');
    body += line('distance', selected.distance === null || selected.distance === undefined ? 'n/a' : selected.distance);
    body += line('search_ms', selected.search_ms === undefined ? 'n/a' : selected.search_ms);
    body += '</div>';
  }

  body += '<div class="why-section"><h4>Calibrated evidence</h4>';
  body += line('max_cosine_distance', diagnostics.max_cosine_distance);
  body += line('min_bm25_score', diagnostics.min_bm25_score);
  body += line('metric', diagnostics.metric);
  body += line('neighbor_budget', diagnostics.neighbor_budget);
  body += line('history_limit', diagnostics.history_limit);
  body += '</div>';

  // RRF produces a rank, not a probability: say it explicitly, never as a score.
  body += '<div class="why-section"><h4>Score semantics</h4>';
  body += line('score_semantics', diagnostics.score_semantics || 'rrf_rank_not_probability');
  body += '<p class="why-note">Rank fusion orders candidates; it is not a probability and not a distance. Evidence is gated on the calibrated thresholds above.</p>';
  body += '</div>';

  if (data.timings) {
    body += '<div class="why-section"><h4>Timings</h4>';
    body += line('embedding_ms', data.timings.embedding_ms);
    body += line('search_ms', data.timings.search_ms);
    body += '</div>';
  }

  const others = rows.filter(r => !selected || String(r.id) !== String(selected.id)).slice(0, WHY_PANEL_MAX_ROWS);
  if (others.length) {
    body += '<div class="why-section"><h4>Other candidates (max ' + WHY_PANEL_MAX_ROWS + ')</h4>';
    body += others.map(r => '<div class="why-row why-other" onclick="selectMemory(\'' + escapeJsString(r.id) + '\')">' +
      '<span class="why-label">' + escapeHtml(r.category || 'fact') + '</span>' +
      '<span class="why-value">' + memoryHtml(r.content, 80) + '</span></div>').join('');
    body += '</div>';
  }

  panel.innerHTML = body;
  panel.classList.add('visible');
}

function closeSearchPanel() {
  document.getElementById('search-panel').classList.remove('visible');
  closeWhyPanel();
}

// ═══════════════════════════════════════════════
// Export / Import / Copy
// ═══════════════════════════════════════════════

function exportMemories() {
  fetch('/api/export').then(r => r.json()).then(data => {
    if (data.error) { alert('Export failed: ' + data.error); return; }
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'lancedb-memories.json'; a.click();
    URL.revokeObjectURL(a.href);
  }).catch(e => alert('Export failed: ' + e));
}

function importMemories() { document.getElementById('import-file').click(); }

async function handleImportFile(input) {
  if (!input.files.length) return;
  try {
    const data = JSON.parse(await input.files[0].text());
    if (!data.memories) { alert('Invalid format: expected {"memories": [...]}'); return; }
    const resp = await fetch('/api/import', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
    const result = await resp.json();
    if (result.error) alert('Import error: ' + result.error);
    else { alert('Imported: ' + result.imported + ', skipped: ' + result.skipped); loadGraph(); }
  } catch(e) { alert('Import failed: ' + e.message); }
  input.value = '';
}

async function copyMemoryJSON(nodeId) {
  const n = allData.nodes.find(x => x.id === nodeId);
  if (!n) return;
  try {
    const data = await (await fetch('/api/memory?id=' + nodeId)).json();
    await navigator.clipboard.writeText(JSON.stringify({ id: data.id || nodeId, content: data.content || n.title || '', category: data.category || n.category || 'fact', entities: data.entities || [] }, null, 2));
    const btn = document.querySelector('.btn-copy');
    if (btn) { const orig = btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = orig; }, 1200); }
  } catch(e) { alert('Copy failed: ' + e.message); }
}

// ═══════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════

// Debounced: filtering on every keystroke restyled the graph mid-word.
let filterDebounceTimer = null;
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(filterDebounceTimer);
  filterDebounceTimer = setTimeout(applyFilters, 120);
});
document.getElementById('search').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') { e.preventDefault(); closeSearchPanel(); doSemanticSearch(); }
});
document.addEventListener('click', function(e) {
  const panel = document.getElementById('search-panel');
  if (panel.classList.contains('visible') && e.target !== document.getElementById('search') && !panel.contains(e.target)) closeSearchPanel();
});
document.getElementById('cat-filter').addEventListener('change', applyFilters);
document.getElementById('relation-filter').addEventListener('change', () => { closeSidebar(); loadGraph(); });
document.getElementById('refresh-btn').addEventListener('click', () => { closeSearchPanel(); closeSidebar(); loadGraph(); });
document.getElementById('sidebar-close').addEventListener('click', closeSidebar);
document.getElementById('graph-canvas').addEventListener('wheel', cancelCameraAnimation, { capture: true, passive: true });
// Escape is the obvious way out of a selection; without it the only exit was
// clicking empty canvas, which is not discoverable.
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    const panel = document.getElementById('search-panel');
    if (panel && panel.classList.contains('visible')) { closeSearchPanel(); return; }
    if (selectedNodeId) closeSidebar();
  }
});

// Helpers
function escapeHtml(str) { const d = document.createElement('div'); d.textContent = str == null ? '' : String(str); return d.innerHTML; }
// Stored content is "Domain:Subject fact ::fact:: fact [Tier=N]". Show it the way
// a person reads it: the key, then the facts. lines=true puts one fact per line.
function memoryParts(content) {
  const text = String(content == null ? '' : content).replace(/\s*\[\s*tier\s*=\s*\d\s*\]\s*$/i, '');
  const match = text.match(/^([^\s:]+:\S+)\s+([\s\S]*)$/);
  // Previews arrive cut by the server: drop a marker left half-written at the end.
  const body = (match ? match[2] : text).replace(/\s*\[[^\]]*$/, '').replace(/\s*:{1,2}\s*f?a?c?t?:{0,2}\s*$/i, '');
  return { key: match ? match[1] : '', facts: body.split(/\s*::\s*fact\s*::\s*/i).filter(Boolean) };
}
function memoryHtml(content, max, lines) {
  const parts = memoryParts(content);
  let body = parts.facts.join(lines ? '\n' : ' · ');
  if (max && body.length > max) body = body.substring(0, max - 1) + '…';
  return (parts.key ? '<b class="mem-key">' + escapeHtml(parts.key) + '</b>' + (lines ? '\n' : ' ') : '') + escapeHtml(body);
}
function escapeHtmlAttr(str) { return String(str == null ? '' : str).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escapeJsString(str) { const value = String(str == null ? '' : str); let out = ''; for (let i = 0; i < value.length; i++) out += '\\u' + value.charCodeAt(i).toString(16).padStart(4, '0'); return out; }
function safeCategory(value) { return Object.prototype.hasOwnProperty.call(catLabels, value) ? value : 'fact'; }
function setEl(id, val) { const el = document.getElementById(id); if (el) el.textContent = val; }
