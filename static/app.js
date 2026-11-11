// ═══════════════════════════════════════════════
// LanceDB — Navigation + Pages
// ═══════════════════════════════════════════════

const API = '/api';
let currentPage = 'dashboard';

// Five sections group the pages. Every page id stays a valid URL hash
// (#graph, #stale...), and a section name opens the last view used in it.
const PAGE_GROUP = {
  dashboard: 'overview',
  memories: 'memories', timeline: 'memories', tags: 'memories',
  graph: 'graph', clusters: 'graph',
  conflicts: 'hygiene', duplicates: 'hygiene', stale: 'hygiene', review: 'hygiene',
  embedding: 'health',
};
const GROUP_TITLE = { overview: 'Overview', memories: 'Memories', graph: 'Graph', hygiene: 'Hygiene', health: 'Health' };
const groupLastPage = { overview: 'dashboard', memories: 'memories', graph: 'graph', hygiene: 'conflicts', health: 'embedding' };

function pageFromHash(hash) {
  const key = String(hash || '').replace(/^#\/?/, '').split(/[?&]/)[0];
  if (PAGE_GROUP[key]) return key;
  if (groupLastPage[key]) return groupLastPage[key];
  return null;
}

function switchPage(name) {
  if (!PAGE_GROUP[name]) name = 'dashboard';
  if (name !== 'graph' && typeof pauseGraphPhysics === 'function') pauseGraphPhysics();
  const group = PAGE_GROUP[name];
  groupLastPage[group] = name;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const pageEl = document.getElementById('page-' + name);
  if (pageEl) pageEl.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => {
    const on = n.dataset.group === group;
    n.classList.toggle('active', on);
    if (on) n.setAttribute('aria-current', 'page'); else n.removeAttribute('aria-current');
  });
  document.querySelectorAll('.lens').forEach(l => {
    const on = l.dataset.page === name;
    l.classList.toggle('active', on);
    if (on) l.setAttribute('aria-current', 'page'); else l.removeAttribute('aria-current');
  });
  document.body.dataset.group = group;
  document.body.dataset.page = name;
  setEl('section-title', GROUP_TITLE[group]);
  document.title = GROUP_TITLE[group] + ' · Memory · LanceDB';
  currentPage = name;
  if (pageFromHash(location.hash) !== name) {
    try { history.replaceState(null, '', '#' + name); } catch (e) { /* file:// or sandboxed */ }
  }

  const fleg = document.getElementById('fresh-legend');
  if (fleg) fleg.style.display = name === 'graph' ? 'flex' : 'none';

  if (name === 'dashboard') loadDashboard();
  else if (name === 'memories') loadMemories();
  else if (name === 'timeline') loadTimeline();
  else if (name === 'tags') loadTags();
  else if (name === 'duplicates') loadDuplicates();
  else if (name === 'conflicts') loadConflicts();
  else if (name === 'embedding') {
    restoreCachedHealth();
    loadEmbedding();
  }
  else if (name === 'clusters') loadClusters();
  else if (name === 'stale') loadStale();
  else if (name === 'graph') {
    if (typeof loadGraph === 'function') loadGraph();
    if (typeof resumeGraphPhysics === 'function') resumeGraphPhysics();
  }
  updateTopStats();
}

function updateTopStats() {
  fetch(API + '/stats').then(r => r.json()).then(s => {
    setEl('mem-count', s.total_memories != null ? s.total_memories : '--');
    setEl('ent-count', s.total_entities != null ? s.total_entities : '--');
    setEl('db-size', s.db_size_mb != null ? s.db_size_mb + ' MB' : '');
    if (s.total_edges != null) setEl('edge-count', s.total_edges);
    setEl('nav-count-memories', s.total_memories != null ? s.total_memories : '');
    setEl('nav-db', s.db_size_mb != null ? s.db_size_mb + ' MB' : '');
  }).catch(() => {});
  updateHygieneCount();
}

// Open conflicts are the one hygiene number that is cheap to know: the
// ledger is a table read, not a scan. Duplicates and review stay on request.
let hygieneCountAt = 0;
function updateHygieneCount(force) {
  if (!force && Date.now() - hygieneCountAt < 60000) return;
  hygieneCountAt = Date.now();
  fetch(API + '/conflicts?status=open&limit=200').then(r => r.json()).then(rows => {
    const n = Array.isArray(rows) ? rows.length : 0;
    const label = n ? (n >= 200 ? '200+' : String(n)) : '';
    setEl('nav-count-hygiene', label);
    setEl('lens-n-conflicts', label);
  }).catch(() => {});
}

// Refresh store singleton then reload current page
function refreshData() {
  const btn = document.querySelector('.btn-neon-refresh');
  if (btn) btn.textContent = '⟳';
  fetch('/api/refresh', {method: 'GET'})
    .then(r => r.json())
    .then(() => {
      if (currentPage === 'dashboard') loadDashboard();
      else if (currentPage === 'memories') loadMemories();
      else if (currentPage === 'timeline') loadTimeline();
      else if (currentPage === 'tags') loadTags();
      else if (currentPage === 'duplicates') loadDuplicates();
      else if (currentPage === 'conflicts') loadConflicts();
      else if (currentPage === 'embedding') loadEmbedding();
      else if (currentPage === 'clusters') loadClusters();
      else if (currentPage === 'stale') loadStale();
      else if (currentPage === 'graph' && typeof loadGraph === 'function') loadGraph();
      updateTopStats();
    })
    .catch(e => console.error('Refresh failed:', e))
    .finally(() => { if (btn) setTimeout(() => btn.textContent = '↻', 800); });
}

function catBadge(cat) {
  const category = safeCategory(cat);
  return '<span class="cat-badge cat-' + category + '">' + escapeHtml(colorLabel[category] || 'Fact') + '</span>';
}

function age(ts) {
  if (!ts) return '?';
  const h = (Date.now()/1000 - ts) / 3600;
  if (h < 1) return Math.round(h*60) + 'm';
  if (h < 24) return Math.round(h) + 'h';
  if (h < 720) return Math.round(h/24) + 'd';
  return (h/720).toFixed(1) + 'mo';
}

function badgeForTier(t) {
  t = String(t);
  return (t === '1' || t === '2' || t === '3') ? '<span class="tier-badge tier-' + t + '">T' + escapeHtml(t) + '</span>' : '';
}

// ═══════════════════════════════════════════════
// Dashboard
// ═══════════════════════════════════════════════

const tierColor = t => 'var(--tier-' + t + ', var(--acc))';
const fmtInt = n => Number(n || 0).toLocaleString('en-US');

async function loadDashboard() {
  const el = document.getElementById('dash-stats');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Loading...</div></div>';
  try {
    const [stats, dash, timeline] = await Promise.all([
      fetch(API + '/stats').then(r => r.json()),
      fetch(API + '/dashboard').then(r => r.json()),
      fetch(API + '/timeline').then(r => r.json()).catch(() => []),
    ]);

    const tiers = dash.tiers || stats.tiers || {};
    const cats = dash.categories || {};
    const ages = stats.ages || {};
    const access = stats.access || {};
    const total = stats.total_memories || dash.total || 0;
    const fresh = (ages['<1h'] || 0) + (ages['1-24h'] || 0);
    const never = access['0'] || 0;

    const kpi = (label, value, sub, color) =>
      '<div class="kpi"><div class="lbl">' + escapeHtml(label) + '</div>' +
      '<div class="kpi-v"' + (color ? ' style="color:' + color + '"' : '') + '>' + escapeHtml(value) + '</div>' +
      '<div class="kpi-s">' + escapeHtml(sub) + '</div></div>';
    el.innerHTML = [
      kpi('Memories', fmtInt(total), fresh ? fresh + ' written in 24h' : 'none in 24h'),
      kpi('Entities', fmtInt(stats.total_entities), 'named things'),
      kpi('Links', fmtInt(stats.total_edges), fmtInt(stats.total_semantic_edges) + ' by similarity'),
      kpi('Never recalled', fmtInt(never), total ? Math.round(never / total * 100) + '% of memories' : '', never ? 'var(--amber)' : ''),
      kpi('Disk', (stats.db_size_mb != null ? stats.db_size_mb : '?') + ' MB', 'avg quality ' + Math.round((dash.avg_quality || 0) * 100) + '%'),
    ].join('');

    // Categories: one stacked bar and its legend.
    const catEntries = Object.entries(cats).filter(([,v]) => v > 0).sort((a,b) => b[1]-a[1]);
    const catColor = k => 'var(--cat-' + safeCategory(k) + ', ' + (colorHex[safeCategory(k)] || colorHexDef) + ')';
    document.getElementById('dash-categories').innerHTML = catEntries.length
      ? '<div class="stack" role="img" aria-label="Memories by category">' + catEntries.map(([k,v]) =>
          '<span style="flex:' + v + ' 1 0;background:' + catColor(k) + '" title="' + escapeHtmlAttr((colorLabel[safeCategory(k)] || k) + ': ' + v) + '"></span>').join('') + '</div>' +
        '<div class="legend">' + catEntries.map(([k,v]) =>
          '<span><i style="background:' + catColor(k) + '"></i>' + escapeHtml(colorLabel[safeCategory(k)] || k) + ' <b>' + escapeHtml(v) + '</b></span>').join('') + '</div>'
      : '<div class="empty-state">No memories yet</div>';
    setEl('dash-tiers-line', ['1','2','3'].map(t => 'T' + t + ' ' + (tiers[t] || 0)).join(' · ') + (tiers.none ? ' · untiered ' + tiers.none : ''));

    // Tiers
    const tMax = Math.max(1, ...Object.values(tiers));
    document.getElementById('dash-tiers').innerHTML = ['1','2','3'].map(k => {
      const v = tiers[k] || 0;
      const name = {'1':'T1 critical','2':'T2 important','3':'T3 context'}[k];
      return '<div class="bar-row"><span class="bar-label">' + name + '</span><div class="bar-track"><div class="bar-fill" style="width:' + (v/tMax*100).toFixed(0) + '%;background:' + tierColor(k) + '"></div></div><span class="bar-count">' + v + '</span></div>';
    }).join('');

    // Tags
    const tagEntries = Object.entries(dash.tags || {}).sort((a,b) => b[1]-a[1]).slice(0, 16);
    document.getElementById('dash-tags').innerHTML = tagEntries.length
      ? tagEntries.map(([k,v]) => '<span class="chip">' + escapeHtml(k) + '<b>' + escapeHtml(v) + '</b></span>').join('')
      : '<div class="empty-state">No tags</div>';

    // Recently written: newest day groups first.
    let days = timeline;
    if (days && !Array.isArray(days) && Array.isArray(days.timeline)) days = days.timeline;
    const recent = (Array.isArray(days) ? days : []).flatMap(g => g.memories || [])
      .sort((a,b) => (b.created_at || 0) - (a.created_at || 0)).slice(0, 8);
    const row = (m, meta) => '<button class="row-item" onclick="showMemoryDetail(\'' + escapeJsString(m.id) + '\')">' +
      catBadge(m.category) + '<span class="txt">' + escapeHtml((m.content || '').substring(0, 160)) + '</span><span class="meta">' + escapeHtml(meta) + '</span></button>';
    document.getElementById('dash-recent').innerHTML = recent.length
      ? recent.map(m => row(m, age(m.created_at))).join('')
      : '<div class="empty-state">Nothing written yet</div>';

    // Most recalled
    const top = dash.top_accessed || [];
    document.getElementById('dash-accessed').innerHTML = top.length
      ? top.slice(0, 8).map(m => row(m, (m.access_count || 0) + '×')).join('')
      : '<div class="empty-state">No recall recorded yet</div>';

    // Needs attention: cheap numbers only; scans stay on their own page.
    const attention = document.getElementById('dash-attention');
    const tile = (href, n, label, color) => '<a class="tile" href="' + href + '"><div class="tile-v" style="color:' + color + '">' + escapeHtml(n) + '</div><div class="tile-l">' + escapeHtml(label) + '</div></a>';
    const old = ages['>30d'] || 0;
    attention.innerHTML = tile('#conflicts', '…', 'open conflicts', 'var(--mute)') +
      tile('#stale', fmtInt(never), 'never recalled', never ? 'var(--amber)' : 'var(--mute)') +
      tile('#timeline', fmtInt(old), 'older than 30 days', 'var(--dim)');
    fetch(API + '/conflicts?status=open&limit=200').then(r => r.json()).then(rows => {
      const n = Array.isArray(rows) ? rows.length : 0;
      const first = attention.querySelector('.tile');
      if (first) first.outerHTML = tile('#conflicts', n >= 200 ? '200+' : n, n === 1 ? 'open conflict' : 'open conflicts', n ? 'var(--red)' : 'var(--green)');
    }).catch(() => {});

  } catch(e) {
    el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>';
  }
}

// ═══════════════════════════════════════════════
// Memories
// ═══════════════════════════════════════════════

let memOffset = 0, MEM_LIMIT = 20, memSelected = new Set();
function toggleSelectAll() {
  const cb = document.getElementById('mem-sel-all');
  document.querySelectorAll('.mem-cb').forEach(c => c.checked = cb.checked);
  updateBulkBar();
}
function updateBulkBar() {
  memSelected = new Set();
  document.querySelectorAll('.mem-cb:checked').forEach(c => memSelected.add(c.value));
  const bar = document.getElementById('mem-bulk-bar');
  if (memSelected.size > 0) { bar.classList.add('visible'); document.getElementById('mem-bulk-count').textContent = memSelected.size + ' selected'; }
  else bar.classList.remove('visible');
}

async function loadMemories() {
  const tbody = document.getElementById('mem-tbody');
  tbody.innerHTML = '<tr><td colspan="7" class="loading">Loading...</td></tr>';
  const params = new URLSearchParams({
    offset: memOffset, limit: MEM_LIMIT,
    search: document.getElementById('mem-search').value,
    category: document.getElementById('mem-category').value,
    type: document.getElementById('mem-type').value,
    tier: document.getElementById('mem-tier').value || '',
  });
  try {
    const r = await fetch(API + '/memories?' + params.toString());
    const data = await r.json();
    const mems = data.memories || [];
    tbody.innerHTML = mems.map(m =>
      '<tr onclick="showMemoryDetail(\'' + escapeJsString(m.id) + '\')" style="cursor:pointer;">' +
        '<td class="cb-col" onclick="event.stopPropagation()"><input type="checkbox" class="mem-cb" value="' + escapeHtmlAttr(m.id) + '" onchange="updateBulkBar()"></td>' +
        '<td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml((m.content || '').substring(0,120)) + '</td>' +
        '<td>' + catBadge(m.category) + '</td>' +
        '<td>' + badgeForTier(m.tier) + '</td>' +
        '<td>' + (m.tags || []).slice(0,3).map(t => '<span class="tag-badge" style="font-size:9px;padding:1px 5px;">' + escapeHtml(t) + '</span>').join('') + '</td>' +
        '<td><div class="quality-bar"><div class="quality-fill" style="width:' + Math.max(4, ((m.quality||0)*100)).toFixed(0) + '%;background:' + (m.quality>=0.7?'var(--green)':m.quality>=0.4?'var(--amber)':'var(--red)') + ';min-width:4px;"></div></div></td>' +
        '<td style="color:var(--muted);font-size:10px;">' + age(m.created_at) + '</td>' +
      '</tr>'
    ).join('');
    const total = data.total || 0;
    const pages = Math.ceil(total / MEM_LIMIT) || 1;
    document.getElementById('mem-pagination').innerHTML =
      '<button class="btn-neon btn-focus" onclick="memOffset=Math.max(0,memOffset-MEM_LIMIT);loadMemories()" ' + (memOffset<=0?'disabled':'') + '>&larr;</button>' +
      '<span class="page-info">' + (Math.floor(memOffset/MEM_LIMIT)+1) + '/' + pages + ' (' + total + ' total)</span>' +
      '<button class="btn-neon btn-focus" onclick="memOffset=' + Math.min(memOffset+MEM_LIMIT,((pages-1)*MEM_LIMIT)) + ';loadMemories()" ' + (memOffset>= ((pages-1)*MEM_LIMIT||0)?'disabled':'') + '>&rarr;</button>';
    document.getElementById('mem-sel-all').checked = false;
  } catch(e) { tbody.innerHTML = '<tr><td colspan="7" class="error-state">Error: ' + escapeHtml(e.message) + '</td></tr>'; }
}

async function deleteSingleMemory(memoryId) {
  if (!confirm("Delete this memory?")) return;
  try {
    const r = await fetch("/api/delete", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({memory_id: memoryId})
    });
    const d = await r.json();
    if (d.error) { alert("Error: " + d.error); return; }
    loadDuplicates();
  } catch(e) { alert("Delete failed: " + e.message); }
}
async function bulkDeleteMemories() {
  if (memSelected.size === 0) return;
  if (!confirm('Delete ' + memSelected.size + ' memories?')) return;
  try {
    await fetch(API + '/memories/bulk-delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({memory_ids: [...memSelected]}) });
    memSelected = new Set(); loadMemories(); updateTopStats();
  } catch(e) { alert('Error: '+e.message); }
}

function showMemoryDetail(id) {
  fetch(API + '/memory?id=' + encodeURIComponent(id)).then(r => r.json()).then(m => {
    if (m.error) { alert(m.error); return; }
    const html =
      '<div class="modal-overlay" onclick="if(event.target===this)this.remove()">' +
        '<div class="modal">' +
          '<div class="modal-title">' + catBadge(m.category) + ' <span style="color:var(--muted);font-size:11px;">' + escapeHtml(m.type || '') + '</span></div>' +
          '<div style="background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:12px;font-family:var(--mono);font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;min-height:100px;max-height:400px;overflow-y:auto;">' + escapeHtml(m.content || '') + '</div>' +
          '<div style="margin-top:10px;color:var(--muted);font-size:11px;">Quality: ' + ((m.quality||0)*100).toFixed(0) + '% · Accessed: ' + (m.access_count||0) + 'x · Created: ' + new Date((m.created_at||0)*1000).toLocaleString() + '</div>' +
          '<div style="margin-top:8px;">' + (m.entities||[]).map(e => '<span class="tag-badge">' + escapeHtml(e) + '</span>').join('') + '</div>' +
          '<div style="margin-top:8px;">' + (m.tags||[]).map(t => '<span class="tag-badge">' + escapeHtml(t) + '</span>').join('') + '</div>' +
          '<div class="modal-actions"><button class="btn-neon btn-focus" onclick="this.closest(\'.modal-overlay\').remove()">Close</button></div>' +
        '</div>' +
      '</div>';
    document.body.insertAdjacentHTML('beforeend', html);
  }).catch(e => alert('Error: ' + e.message));
}

// ═══════════════════════════════════════════════
// Tags
// ═══════════════════════════════════════════════

async function loadTags() {
  const el = document.getElementById('tags-container');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Loading...</div></div>';
  try {
    const r = await fetch(API + '/tags');
    let tags = await r.json();
    if (tags && typeof tags.tags === 'object' && !Array.isArray(tags.tags)) tags = tags.tags;
    const entries = Object.entries(tags).sort((a,b) => b[1]-a[1]);
    const maxCount = Math.max(1, ...entries.map(([,v])=>v));
    el.innerHTML = entries.length
      ? entries.map(([tag, count]) =>
          '<div class="dup-group" style="display:flex;align-items:center;gap:10px;padding:8px 14px;">' +
            '<span class="tag-badge" style="font-size:12px;padding:4px 12px;min-width:80px;text-align:center;">' + escapeHtml(tag) + '</span>' +
            '<div style="flex:1;"><div class="quality-bar" style="width:100%;"><div class="quality-fill" style="width:' + (count/maxCount*100).toFixed(0) + '%;background:var(--accent)"></div></div></div>' +
            '<span style="color:var(--muted);font-size:11px;min-width:28px;text-align:right;">' + count + '</span>' +
            '<button class="btn-neon btn-delete" onclick="deleteTag(\'' + escapeJsString(tag) + '\')">Delete</button>' +
          '</div>'
        ).join('')
      : '<div class="empty-state">No tags</div>';
  } catch(e) { el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>'; }
}

async function deleteTag(tag) {
  if (!confirm('Delete tag "' + tag + '"?')) return;
  try {
    await fetch(API + '/tags/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tag}) });
    loadTags();
  } catch(e) { alert('Error: '+e.message); }
}

// ═══════════════════════════════════════════════
// Timeline
// ═══════════════════════════════════════════════

async function loadTimeline() {
  const el = document.getElementById('tl-container');
  const catFilter = document.getElementById('tl-category').value;
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Loading...</div></div>';
  try {
    const r = await fetch(API + '/timeline');
    let data = await r.json();
    if (data && !Array.isArray(data) && Array.isArray(data.timeline)) data = data.timeline;
    if (!Array.isArray(data)) data = [];
    if (catFilter) data = data.map(g => ({ ...g, memories: (g.memories||[]).filter(m => m.category === catFilter), count: (g.memories||[]).filter(m => m.category === catFilter).length })).filter(g => g.count > 0);
    const total = data.reduce((s,g) => s + g.count, 0);
    setEl('tl-count', total + ' memories');
    if (!data.length) { el.innerHTML = '<div class="empty-state">No memories</div>'; return; }
    el.innerHTML = '<div class="timeline"><div class="timeline-line"></div>' +
      data.map(g =>
        '<div class="timeline-group"><div class="timeline-dot"></div><div class="timeline-date">' + escapeHtml(g.date) + ' <span style="font-weight:400;color:var(--muted);font-size:11px;">(' + escapeHtml(g.count) + ')</span></div>' +
        (g.memories||[]).slice(0,10).map(m =>
          '<div class="timeline-item" onclick="showMemoryDetail(\'' + escapeJsString(m.id) + '\')">' + catBadge(m.category) + ' ' + escapeHtml((m.content||'').substring(0,120)) + ' <span style="color:var(--muted);font-size:10px;">' + age(m.created_at) + '</span></div>'
        ).join('') +
        (g.count > 10 ? '<div style="color:var(--muted);font-size:10px;padding:4px 12px;">+' + (g.count-10) + ' more</div>' : '') +
        '</div>'
      ).join('') +
    '</div>';
  } catch(e) { el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>'; }
}

// ═══════════════════════════════════════════════
// Duplicates
// ═══════════════════════════════════════════════

async function loadDuplicates() {
  const el = document.getElementById('dup-container');
  const threshold = document.getElementById('dup-threshold').value;
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Scanning...</div></div>';
  try {
    const r = await fetch(API + '/duplicates?threshold=' + threshold);
    let groups = await r.json();
    if (groups && !Array.isArray(groups) && Array.isArray(groups.duplicates)) groups = groups.duplicates;
    if (!groups || !Array.isArray(groups) || !groups.length) { el.innerHTML = '<div class="empty-state">No duplicates</div>'; return; }
    el.innerHTML = groups.map((g, gi) =>
      '<div class="dup-group">' +
        '<div class="dup-group-header"><span style="font-size:13px;font-weight:600;color:var(--accent);">Group ' + (gi+1) + ' <span style="font-weight:400;color:var(--muted);font-size:11px;">(' + g.size + ')</span></span></div>' +
        (g.memories||[]).map((m, mi) =>
          '<div class="dup-item" onclick="showMemoryDetail(\'' + escapeJsString(m.id) + '\')">' +
            '<span style="color:' + (mi===0?'var(--accent)':'var(--muted)') + ';font-size:10px;min-width:20px;">#' + (mi+1) + '</span>' +
            catBadge(m.category) +
            '<span style="flex:1;">' + escapeHtml((m.content||'').substring(0,150)) + '</span>' +
            '<span style="color:var(--muted);font-size:10px;">' + ((m.quality||0)*100).toFixed(0) + '%</span>' +
            '<button class="btn-neon btn-delete" style="padding:2px 8px;font-size:10px;margin-left:4px;" onclick="event.stopPropagation();deleteSingleMemory(\'' + escapeJsString(m.id) + '\')">✕</button>' +
          '</div>'
        ).join('') +
      '</div>'
    ).join('');
  } catch(e) { el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>'; }
}

// Conflicts

async function loadConflicts() {
  const el = document.getElementById('conflict-container');
  const status = document.getElementById('conflict-status').value;
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Checking explicit claims...</div></div>';
  try {
    const params = new URLSearchParams({status, limit: 200});
    const r = await fetch(API + '/conflicts?' + params.toString());
    const rows = await r.json();
    if (!Array.isArray(rows)) throw new Error(rows.error || 'Invalid conflict response');
    setEl('conflict-count', rows.length + ' records');
    if (!rows.length) {
      el.innerHTML = '<div class="empty-state">No ' + (status || '') + ' conflicts</div>';
      return;
    }
    el.innerHTML = rows.map(c =>
      '<div class="conflict-card conflict-' + (c.status === 'resolved' || c.status === 'archived' ? c.status : 'open') + '">' +
        '<div class="conflict-head"><span class="typed-tag">' + escapeHtml(c.claim_key || 'claim') + '</span>' +
        '<span class="conflict-subject">' + escapeHtml(c.subject || '') + '</span>' +
        '<span class="conflict-status">' + escapeHtml(c.status || 'open') + '</span></div>' +
        '<div class="conflict-pair">' +
          '<button class="conflict-memory" onclick="showMemoryDetail(\'' + escapeJsString(c.memory_a_id) + '\')"><b>' + escapeHtml(c.value_a || '') + '</b><span>' + escapeHtml((c.memory_a_content || '').substring(0, 180)) + '</span></button>' +
          '<span class="conflict-vs">vs</span>' +
          '<button class="conflict-memory" onclick="showMemoryDetail(\'' + escapeJsString(c.memory_b_id) + '\')"><b>' + escapeHtml(c.value_b || '') + '</b><span>' + escapeHtml((c.memory_b_content || '').substring(0, 180)) + '</span></button>' +
        '</div>' +
      '</div>'
    ).join('');
  } catch(e) {
    el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>';
  }
}

// Review inbox — deliberately invoked only by the page button.
const reviewReasonLabels = {
  format: 'Format', contradiction: 'Contradiction',
  broken_reference: 'Broken reference', near_duplicate: 'Near duplicate',
};

async function loadReviewInbox() {
  const button = document.getElementById('review-scan');
  const container = document.getElementById('review-container');
  button.disabled = true;
  button.textContent = 'Scanning projected fields...';
  container.innerHTML = '<div class="loading"><div class="spinner"></div><div>Read-only scan in progress</div></div>';
  try {
    const response = await fetch(API + '/review');
    const data = await response.json();
    if (data.error) throw new Error(data.error);
    const findings = (data.findings || []).slice(0, 100);
    setEl('review-count', findings.length + ' reasons · read only');
    container.innerHTML = findings.length ? findings.map(finding => {
      const reason = reviewReasonLabels[finding.reason] || 'Review';
      const related = finding.related_memory_id ? ' · related ' + finding.related_memory_id : '';
      const target = finding.target_label ? ' · target ' + finding.target_label : '';
      const score = finding.similarity != null ? ' · cosine ' + Number(finding.similarity).toFixed(3) : '';
      const open = finding.memory_id ? ' onclick="showMemoryDetail(\'' + escapeJsString(finding.memory_id) + '\')"' : '';
      return '<button class="review-card review-' + escapeHtmlAttr(finding.reason) + '"' + open + '>' +
        '<span class="review-reason">' + escapeHtml(reason) + '</span>' +
        '<span class="review-id">' + escapeHtml((finding.memory_id || 'unknown') + related + target + score) + '</span>' +
        '<span class="review-message">' + escapeHtml(finding.message || '') + '</span>' +
      '</button>';
    }).join('') : '<div class="empty-state">No review reason found within the declared scan budgets.</div>';
  } catch(e) {
    container.innerHTML = '<div class="error-state">Review scan failed: ' + escapeHtml(e.message) + '</div>';
    setEl('review-count', 'Scan failed');
  } finally {
    button.disabled = false;
    button.textContent = 'Run read-only scan';
  }
}

function formatBytes(value) {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return 'Unknown';
  const bytes = Number(value);
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KiB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MiB';
}

function maintenanceSizeChange(maintenance, metric = 'database') {
  const label = metric === 'database' ? 'Database' : 'Total footprint';
  let delta = maintenance[metric + '_delta_bytes'];
  const before = maintenance[metric + '_bytes_before'];
  const after = maintenance[metric + '_bytes_after'];
  if (delta == null && before != null && after != null) {
    delta = after - before;
  }
  if (delta == null || !Number.isFinite(Number(delta))) return label + ' change unknown';
  if (Number(delta) > 0) return '+' + formatBytes(delta) + ' ' + label.toLowerCase() + ' growth';
  if (Number(delta) < 0) return '−' + formatBytes(-delta) + ' ' + label.toLowerCase() + ' reclaimed';
  return '0 B ' + label.toLowerCase() + ' change';
}

// Backup paths are long and the interesting part is at the end. Showing the full
// absolute path inline pushed the rest of the panel around; the caller keeps the
// complete path in a title attribute.
function shortPath(path) {
  const parts = String(path == null ? '' : path).split('/').filter(Boolean);
  if (parts.length <= 2) return path || '';
  return parts.slice(-2).join('/');
}

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
    '<div class="health-card"><span>Database</span><b>' + formatBytes(storage.database_bytes) + '</b><small>' + formatBytes(storage.active_bytes_estimate) + ' active estimate · ' + formatBytes(storage.overhead_bytes_estimate) + ' overhead estimate</small></div>' +
    '<div class="health-card"><span>Backups</span><b>' + formatBytes(storage.managed_backup_bytes) + '</b><small>' + formatBytes(storage.total_footprint_bytes) + ' database + managed backups</small></div>' +
    '<div class="health-card"><span>Ollama</span><b class="health-' + escapeHtmlAttr(ollama.state || 'error') + '">' + escapeHtml(ollama.state || 'error') + '</b><small>' + escapeHtml(ollama.error || ('HTTP ' + (ollama.status || '?'))) + '</small></div>' +
    '<div class="health-card"><span>Last maintenance</span><b>' + escapeHtml(lastMaintenance.last_success_at || 'Never') + '</b><small>' + (lastMaintenance.last_success_at ? maintenanceSizeChange(lastMaintenance) + ' · ' + maintenanceSizeChange(lastMaintenance, 'total_footprint') + ' · ' + formatBytes(lastMaintenance.backup_created_bytes) + ' backup' : 'No successful run recorded') + '</small></div>' +
    '<div class="health-table"><table class="data-table"><thead><tr><th>Table</th><th>State</th><th>Rows</th><th>Version / history</th><th>Fragments</th></tr></thead><tbody>' + tableRows + '</tbody></table></div>' +
    '<div class="health-estimate">Overhead includes retained history and index overhead; it is not an estimate of reclaimable space. Backup estimate before maintenance: ' + formatBytes(estimate.backup_bytes) + '. Database size after maintenance is unknown.</div>';
}

function restoreCachedHealth() {
  const record = readHealthCache(window.localStorage);
  if (record) renderHealth(record.health, record.checked_at, true);
}

async function loadHealth() {
  const button = document.getElementById('health-scan');
  const container = document.getElementById('health-container');
  button.disabled = true;
  button.textContent = 'Inspecting...';
  try {
    const data = await fetch(API + '/health').then(response => response.json());
    if (data.error) throw new Error(data.error);
    const checkedAt = new Date().toISOString();
    const record = writeHealthCache(window.localStorage, data, checkedAt);
    renderHealth(data, checkedAt, Boolean(record));
  } catch(e) {
    container.innerHTML = '<div class="error-state">Health inspection failed: ' + escapeHtml(e.message) + '</div>';
  } finally {
    button.disabled = false;
    button.textContent = 'Inspect local health';
  }
}

let compactionPlanReady = false;

async function loadCompactionPlan() {
  const button = document.getElementById('compact-plan-button');
  const panel = document.getElementById('compact-plan');
  const content = document.getElementById('compact-plan-content');
  button.disabled = true;
  button.textContent = 'Planning...';
  panel.hidden = false;
  compactionPlanReady = false;
  try {
    const plan = await fetch(API + '/maintenance/compact/plan?mode=reclaim').then(response => response.json());
    if (plan.error) throw new Error(plan.error);
    const deleted = (plan.backups_to_delete || []).length
      ? (plan.backups_to_delete || []).map(path => '<li><code>' + escapeHtml(shortPath(path)) + '</code></li>').join('')
      : '<li>None</li>';
    content.innerHTML =
      '<div class="compact-metrics"><span>Database <b>' + formatBytes(plan.database_bytes) + '</b></span><span>Managed backups <b>' + formatBytes(plan.managed_backup_bytes) + '</b></span><span>Total <b>' + formatBytes(plan.total_footprint_bytes) + '</b></span><span>Overhead estimate <b>' + formatBytes(plan.overhead_bytes_estimate) + '</b></span></div>' +
      '<p>Overhead includes retained history and index overhead; it is not an estimate of reclaimable space. Database size after maintenance is unknown.</p>' +
      '<p>Backup to create</p><ul class="compact-paths"><li><code title="' + escapeHtmlAttr(plan.backup_to_create || '') + '">' + escapeHtml(shortPath(plan.backup_to_create)) + '</code></li></ul>' +
      '<p>Old managed backups to delete</p><ul class="compact-paths">' + deleted + '</ul>' +
      '<label class="compact-confirm"><input id="compact-confirm" type="checkbox" onchange="document.getElementById(\'compact-run\').disabled=!this.checked"> I drained old readers and confirm the zero-age reclaim with the verified backup above.</label>' +
      '<button id="compact-run" class="btn-neon btn-delete" onclick="runCompaction()" disabled>Reclaim database history</button>' +
      '<pre id="compact-result" class="compact-result" hidden></pre>';
    compactionPlanReady = true;
  } catch(e) {
    content.innerHTML = '<div class="error-state">Compaction plan failed: ' + escapeHtml(e.message) + '</div>';
  } finally {
    button.disabled = false;
    button.textContent = 'Plan compaction';
  }
}

async function runCompaction() {
  const confirmed = document.getElementById('compact-confirm');
  const button = document.getElementById('compact-run');
  const output = document.getElementById('compact-result');
  if (!compactionPlanReady || !confirmed?.checked) return;
  if (!confirm('Create the displayed backup, purge only the listed managed backups, then reclaim eligible database history?')) return;
  button.disabled = true;
  button.textContent = 'Compacting...';
  output.hidden = false;
  output.textContent = 'Backup and compaction in progress. Do not start another write.';
  try {
    const response = await fetch(API + '/maintenance/compact', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({confirmed: true, mode: 'reclaim'}),
    });
    const result = await response.json();
    output.textContent = JSON.stringify(result, null, 2);
    if (result.success) {
      compactionPlanReady = false;
      await loadHealth();
    }
  } catch(e) {
    output.textContent = 'Compaction request failed: ' + e.message;
  } finally {
    button.textContent = 'Reclaim database history';
    button.disabled = true;
  }
}

// Embeddings (UMAP projection)
// ═══════════════════════════════════════════════

async function loadEmbedding() {
  const canvas = document.getElementById('emb-canvas');
  const ctx = canvas.getContext('2d');
  const container = document.getElementById('emb-container');
  const tooltip = document.getElementById('emb-tooltip');
  canvas.width = container.clientWidth || 800;
  canvas.height = 500;
  const nNeighbors = document.getElementById('emb-neighbors').value;
  const minDist = document.getElementById('emb-mindist').value;
  setEl('emb-count', 'Computing…');
  // UMAP runs server-side and takes seconds: say so over the empty canvas.
  // Moving a slider starts a new run; only the latest one may draw.
  const run = (loadEmbedding.run = (loadEmbedding.run || 0) + 1);
  let busy = container.querySelector('.scatter-busy');
  if (!busy) {
    busy = document.createElement('div');
    busy.className = 'scatter-busy';
    busy.setAttribute('role', 'status');
    container.appendChild(busy);
  }
  busy.innerHTML = '<div class="spinner"></div><div>Computing the UMAP projection…</div><small>neighbors ' + escapeHtml(nNeighbors) + ' · min dist ' + escapeHtml(minDist) + ' · usually a few seconds</small>';
  busy.hidden = false;
  container.classList.add('is-busy');
  const done = () => { if (run === loadEmbedding.run) { busy.hidden = true; container.classList.remove('is-busy'); } };

  try {
    const projection = await fetchJsonWithBusyRetry(
      API + '/projection?n_neighbors=' + nNeighbors + '&min_dist=' + minDist,
      {
        onBusy: ({retryAfterMs}) => setEl(
          'emb-count',
          'Server busy. Retrying in ' + (retryAfterMs / 1000) + ' s...',
        ),
      },
    );
    if (run !== loadEmbedding.run) return;
    done();
    const stateMessages = {
      dependency_missing: 'Projection unavailable: umap-learn is not installed.',
      no_data: 'No projection: at least 3 valid embeddings are required.',
      projection_failed: 'Projection failed: ' + (projection.error || 'unknown error'),
    };
    if (!Array.isArray(projection) && projection.state && projection.state !== 'ready') {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#8D88A3'; ctx.font = '12px Geist, sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(stateMessages[projection.state] || projection.message || 'Projection unavailable.', canvas.width/2, canvas.height/2);
      setEl('emb-count', projection.state); return;
    }
    let points = Array.isArray(projection) ? projection : projection.points;
    if (!points || !Array.isArray(points) || !points.length) {
      ctx.fillStyle = '#8D88A3'; ctx.font = '12px Geist, sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('Not enough data (need 3+ memories)', canvas.width/2, canvas.height/2);
      setEl('emb-count', '0 points'); return;
    }
    const budget = Number(projection.budget) || 500;
    const sampleState = projection.truncated
      ? ' · first ' + budget + ' stored rows sampled'
      : ' · up to ' + budget + ' stored rows considered';
    setEl('emb-count', points.length + ' points' + sampleState);

    const xs = points.map(p => p.x), ys = points.map(p => p.y);
    const xMin = Math.min(...xs), xMax = Math.max(...xs), yMin = Math.min(...ys), yMax = Math.max(...ys);
    const xRange = xMax - xMin || 1, yRange = yMax - yMin || 1;
    const pad = 40, w = canvas.width - pad*2, h = canvas.height - pad*2;

    const categories = [...new Set(points.map(p => p.category))];
    document.getElementById('emb-legend').innerHTML = categories.map(c =>
      '<span style="display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted);"><span style="width:10px;height:10px;border-radius:50%;background:' + (colorHex[safeCategory(c)] || colorHexDef) + '"></span>' + escapeHtml(colorLabel[safeCategory(c)] || 'Fact') + '</span>'
    ).join('');

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const pointData = points.map(p => ({
      x: pad + ((p.x - xMin) / xRange) * w, y: pad + ((p.y - yMin) / yRange) * h,
      r: 5, color: colorHex[p.category] || colorHexDef, id: p.id, content: p.content
    }));
    pointData.forEach(pt => {
      ctx.beginPath(); ctx.arc(pt.x, pt.y, pt.r, 0, Math.PI*2);
      ctx.fillStyle = pt.color; ctx.globalAlpha = 0.7; ctx.fill(); ctx.globalAlpha = 1;
      ctx.strokeStyle = 'rgba(13,11,20,0.9)'; ctx.lineWidth = 1; ctx.stroke();
    });

    container.onmousemove = function(e) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      let found = null;
      for (const pt of pointData) { if ((mx-pt.x)**2 + (my-pt.y)**2 < 100) { found = pt; break; } }
      if (found) {
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX - rect.left + 12) + 'px';
        tooltip.style.top = (e.clientY - rect.top - 10) + 'px';
        tooltip.textContent = (found.content || '').substring(0,120);
      } else tooltip.style.display = 'none';
    };
    container.onclick = function(e) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      for (const pt of pointData) { if ((mx-pt.x)**2 + (my-pt.y)**2 < 100) { showMemoryDetail(pt.id); return; } }
    };
  } catch(e) {
    if (run !== loadEmbedding.run) return;
    const msg = e instanceof BusyHttpError ? e.message : 'Projection request failed: ' + e.message;
    setEl('emb-count', msg);
    busy.innerHTML = '<div class="error-state">' + escapeHtml(msg) + '</div>';
    busy.hidden = false;
    container.classList.remove('is-busy');
  }
}

// ═══════════════════════════════════════════════
// Clusters (named from common entities)
// ═══════════════════════════════════════════════

function clusterName(members) {
  // Find most common entity across members
  const wordCounts = {};
  (members || []).forEach(m => {
    const text = (m.content || '') + ' ' + (m.entities || []).join(' ');
    text.toLowerCase().split(/[\s,;:|]+/).filter(w => w.length > 2 && !['the','and','for','with','est','les','des','une','que','pas','sur','dans','par','plus','the'].includes(w)).forEach(w => {
      wordCounts[w] = (wordCounts[w] || 0) + 1;
    });
  });
  const sorted = Object.entries(wordCounts).sort((a,b) => b[1]-a[1]);
  if (sorted.length >= 2) return sorted[0][0] + ' · ' + sorted[1][0];
  if (sorted.length === 1) return sorted[0][0];
  return 'Cluster';
}

async function loadClusters() {
  const el = document.getElementById('cl-container');
  const threshold = document.getElementById('cl-threshold').value;
  const minSize = document.getElementById('cl-minsize').value;
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Clustering...</div></div>';
  try {
    const r = await fetch(API + '/clusters?threshold=' + threshold + '&min_size=' + minSize);
    const payload = await r.json();
    const diagnostics = !Array.isArray(payload) ? (payload.diagnostics || {}) : {};
    let clusters = Array.isArray(payload) ? payload : payload.clusters;
    if (!Array.isArray(clusters)) clusters = [];
    const largestShare = Number(diagnostics.largest_group_share) || 0;
    const clusterCoverage = Number(diagnostics.cluster_coverage) || 0;
    const vectorCoverage = Number(diagnostics.vector_coverage) || 0;
    const isolated = Number(diagnostics.isolated_memories) || 0;
    const diagnosticEl = document.getElementById('cluster-diagnostics');
    if (diagnosticEl) {
      const warning = diagnostics.low_discrimination
        ? '<strong>Low discrimination:</strong> one group contains ' + Math.round(largestShare * 100) + '% of vectorized memories. Cluster count alone is not evidence of useful structure. · '
        : '';
      diagnosticEl.innerHTML = warning +
        'Largest group ' + Math.round(largestShare * 100) + '% · ' +
        'cluster coverage ' + Math.round(clusterCoverage * 100) + '% · ' +
        'vector coverage ' + Math.round(vectorCoverage * 100) + '% · ' +
        isolated + ' isolated/unvectorized';
    }
    setEl('cl-count', clusters.length + ' displayed clusters');
    if (!clusters.length) { el.innerHTML = '<div class="empty-state">No clusters</div>'; return; }
    const clColors = ['#A98BFF','#5FE3D0','#FFB547','#5BD69B','#FF6B78','#6CB6FF','#E39BFF','#B9E26A','#FF9A5C','#8D88A3'];
    el.innerHTML = clusters.map((c, i) => {
      const name = clusterName(c.members || []);
      return '<div class="dup-group">' +
        '<div class="dup-group-header"><span style="font-size:13px;font-weight:600;color:' + clColors[i%clColors.length] + '">' + escapeHtml(name) + ' <span style="font-weight:400;color:var(--muted);font-size:11px;">(' + c.size + ')</span></span></div>' +
        (c.members||[]).slice(0,15).map(m =>
          '<div class="dup-item" onclick="showMemoryDetail(\'' + escapeJsString(m.id) + '\')">' + catBadge(m.category) + ' ' + escapeHtml((m.content||'').substring(0,130)) + '</div>'
        ).join('') +
        (c.size > 15 ? '<div style="color:var(--muted);font-size:10px;padding:4px 8px;">+' + (c.size-15) + ' more</div>' : '') +
      '</div>';
    }).join('');
  } catch(e) { el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>'; }
}

// ═══════════════════════════════════════════════
// Stale
// ═══════════════════════════════════════════════

async function loadStale() {
  const el = document.getElementById('stale-container');
  const days = document.getElementById('stale-days').value;
  const quality = document.getElementById('stale-quality').value;
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div>Scanning...</div></div>';
  try {
    const r = await fetch(API + '/stale?days=' + days + '&quality_max=' + quality);
    let stale = await r.json();
    if (stale && !Array.isArray(stale) && Array.isArray(stale.memories)) stale = stale.memories;
    if (!Array.isArray(stale)) stale = [];
    if (!stale.length) { el.innerHTML = '<div class="empty-state">No stale memories</div>'; return; }
    el.innerHTML = stale.map(m =>
      '<div class="dup-item" onclick="showMemoryDetail(\'' + escapeJsString(m.id) + '\')" style="display:flex;align-items:center;gap:8px;">' +
        catBadge(m.category) +
        '<span style="flex:1;">' + escapeHtml((m.content||'').substring(0,140)) + '</span>' +
        '<span style="color:var(--muted);font-size:10px;">' + age(m.created_at) + '</span>' +
        '<span style="font-size:10px;color:var(--red);">' + ((m.quality||0)*100).toFixed(0) + '%</span>' +
      '</div>'
    ).join('');
  } catch(e) { el.innerHTML = '<div class="error-state">Error: ' + escapeHtml(e.message) + '</div>'; }
}

async function deleteAllStale() {
  const days = document.getElementById('stale-days').value;
  const quality = document.getElementById('stale-quality').value;
  try {
    const r = await fetch(API + '/stale?days=' + days + '&quality_max=' + quality);
    let stale = await r.json();
    if (stale && !Array.isArray(stale) && Array.isArray(stale.memories)) stale = stale.memories;
    if (!Array.isArray(stale)) stale = [];
    if (!stale.length) { alert('Nothing to delete'); return; }
    if (!confirm('Delete ' + stale.length + ' stale memories?')) return;
    await fetch(API + '/memories/bulk-delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({memory_ids: stale.map(m => m.id)}) });
    loadStale(); updateTopStats();
  } catch(e) { alert('Error: ' + e.message); }
}

// ═══════════════════════════════════════════════
// Boot (skipped when the file is loaded without a browser, as in the tests)
// ═══════════════════════════════════════════════

if (typeof window !== 'undefined' && typeof document.addEventListener === 'function') {
  // Section links are plain anchors (they work without scripts and can be
  // opened in a new tab). A click reopens the last view used in that section:
  // "memories" and "graph" are both a section and a page, so the hash alone
  // cannot tell "the section" from "its first view".
  document.addEventListener('click', (e) => {
    const link = e.target.closest && e.target.closest('.nav-item[data-group]');
    if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const page = groupLastPage[link.dataset.group];
    if (page !== currentPage) window.switchPage(page);
    else if (typeof closeNav === 'function') closeNav();
  });

  window.addEventListener('hashchange', () => {
    const page = pageFromHash(location.hash);
    if (page && page !== currentPage) window.switchPage(page);
  });

  switchPage(pageFromHash(location.hash) || 'dashboard');
}
