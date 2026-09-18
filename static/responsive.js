// ═══════════════════════════════════════════════
// LanceDB — Adaptations appareil (telephone / tablette / fold)
// ═══════════════════════════════════════════════
//
// Ce module ne dessine rien : il expose l'etat reel de l'appareil au reste de
// la page. Le CSS fait le travail visuel via les media queries ; ici on gere
// ce que le CSS ne peut pas faire seul :
//   1. la hauteur reelle de la topbar (elle change de 1 a 3 lignes selon la
//      largeur, donc toute valeur fixe finit par mentir) ;
//   2. le tiroir de navigation (etat ouvert/ferme, Echap, scrim) ;
//   3. le recadrage du graphe quand la taille utile change (rotation, pliage,
//      deverrouillage du pli, barre d'URL qui se replie) ;
//   4. la remontee de l'etat a l'agent via des attributs data-* sur <html>.

(function () {
  'use strict';

  var root = document.documentElement;
  var body = document.body;

  // ── 1. Etat d'appareil, expose en data-* pour le CSS et le debug ──────────
  function segments() {
    try {
      var vp = window.viewport;
      if (vp && Array.isArray(vp.segments) && vp.segments.length) return vp.segments;
    } catch (e) { /* API absente : on retombe sur le CSS seul */ }
    return null;
  }

  function postureType() {
    try {
      if (navigator.devicePosture && navigator.devicePosture.type) return navigator.devicePosture.type;
    } catch (e) { /* non supporte */ }
    return null;
  }

  function classify() {
    var segs = segments();
    var w = window.innerWidth;
    var count = segs ? segs.length : 1;

    // Un pli vertical (2 segments cote a cote) = mode livre / desktop.
    // Un pli horizontal (2 segments empiles) = mode passeport.
    var mode = 'single';
    if (count >= 2 && segs) {
      var a = segs[0], b = segs[1];
      mode = (Math.abs(a.y - b.y) > Math.abs(a.x - b.x)) ? 'passport' : 'book';
    }

    var kind;
    if (count >= 2) kind = mode;                    // le pli prime sur la largeur
    else if (w >= 1024) kind = 'desktop';
    else if (w >= 768) kind = 'tablet';
    else if (w >= 640) kind = 'fold-open';
    else kind = 'phone';

    root.setAttribute('data-device', kind);
    root.setAttribute('data-segments', String(count));
    root.setAttribute('data-posture', postureType() || 'unknown');
  }

  // ── 2. Hauteur reelle de la topbar ───────────────────────────────────────
  // La topbar passe de 54px a plus de 100px sur un telephone. Les panneaux
  // flottants et le canvas se positionnent dessus, donc on mesure et on publie
  // --tb-h plutot que de coder une constante.
  var topbar = document.getElementById('topbar');
  var lastTbH = null;
  function measureTopbar() {
    if (!topbar) return;
    var h = Math.round(topbar.getBoundingClientRect().height);
    if (h && h !== lastTbH) {
      lastTbH = h;
      root.style.setProperty('--tb-h', h + 'px');
      root.setAttribute('data-tb-h', String(h));
    }
  }

  // ── 3. Tiroir de navigation ──────────────────────────────────────────────
  function navIsDrawer() {
    var nav = document.getElementById('nav');
    if (!nav) return false;
    return getComputedStyle(nav).position === 'fixed';
  }

  function setNavOpen(open) {
    if (!navIsDrawer()) { body.classList.remove('nav-open'); syncToggle(false); return; }
    body.classList.toggle('nav-open', !!open);
    syncToggle(!!open);
  }

  function syncToggle(open) {
    var btn = document.getElementById('nav-toggle');
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  window.openNav = function () { setNavOpen(true); };
  window.closeNav = function () { setNavOpen(false); };
  window.toggleNav = function () { setNavOpen(!body.classList.contains('nav-open')); };

  // ── 4. Panneau de filtres du graphe (telephone) ──────────────────────────
  // Sur telephone les filtres sont VISIBLES par defaut ; le bouton sert a les
  // replier pour degager la carte. Il porte un point d'etat pour qu'un filtre
  // actif reste visible meme replie : sinon on oublie qu'un filtre est pose et
  // le graphe parait faux.
  var DEFAULT_FILTERS = {
    'cat-filter': '',
    'cluster-mode': 'raw',
    'show-declared': false,
    'relation-filter': '',
    'threshold-slider': '0.8',
    'tier1': true, 'tier2': true, 'tier3': true,
  };

  function readFilterState() {
    var s = {};
    Object.keys(DEFAULT_FILTERS).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      s[id] = el.type === 'checkbox' ? !!el.checked : String(el.value);
    });
    return s;
  }

  function filtersDirty() {
    var s = readFilterState();
    return Object.keys(DEFAULT_FILTERS).some(function (id) {
      return Object.prototype.hasOwnProperty.call(s, id) && s[id] !== DEFAULT_FILTERS[id];
    });
  }

  function syncFiltersIndicator() {
    var btn = document.getElementById('filters-toggle');
    if (!btn) return;
    btn.setAttribute('data-active', filtersDirty() ? 'true' : 'false');
  }

  function setFiltersCollapsed(collapsed) {
    var panel = document.getElementById('ctl-filters');
    var btn = document.getElementById('filters-toggle');
    if (!panel || !btn) return;
    // Le panneau est ouvert par defaut : on pilote la classe `collapsed` et non
    // une classe `open`, pour que l'etat initial soit correct avant tout JS.
    panel.classList.toggle('collapsed', !!collapsed);
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    var label = btn.querySelector('.filters-label');
    if (label) label.textContent = collapsed ? 'Show filters' : 'Hide filters';
    scheduleRefit();
  }

  function toggleFilters(force) {
    var panel = document.getElementById('ctl-filters');
    if (!panel) return;
    var collapsed = typeof force === 'boolean' ? force : !panel.classList.contains('collapsed');
    setFiltersCollapsed(collapsed);
  }

  // Compat : les appels historiques passaient `true` pour OUVRIR.
  window.toggleFilters = function (open) {
    toggleFilters(typeof open === 'boolean' ? !open : undefined);
  };
  window.setFiltersCollapsed = setFiltersCollapsed;

  var filtersBtn = document.getElementById('filters-toggle');
  if (filtersBtn) filtersBtn.addEventListener('click', function () { toggleFilters(); });

  // Replier en changeant de page, et recalculer le point d'etat quand un
  // controle bouge. On ecoute au niveau du conteneur plutot que sur chaque
  // champ : les selects declenchent deja leurs propres handlers inline.
  var ctlFilters = document.getElementById('ctl-filters');
  if (ctlFilters) {
    ctlFilters.addEventListener('change', function () { syncFiltersIndicator(); scheduleRefit(); });
    ctlFilters.addEventListener('input', function () { syncFiltersIndicator(); });
    syncFiltersIndicator();
  }

  // ── 5. Recadrage du graphe ───────────────────────────────────────────────
  // vis-network lit la taille de son conteneur a l'init : quand la zone change,
  // il faut lui redonner la mesure, sinon le canvas reste au format precedent.
  var refitTimer = null;
  function scheduleRefit() {
    if (refitTimer) clearTimeout(refitTimer);
    refitTimer = setTimeout(function () {
      refitTimer = null;
      if (typeof network === 'undefined' || !network) return;
      try {
        network.setSize('100%', '100%');
        network.redraw();
        network.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
      } catch (e) { /* graphe detruit entre-temps */ }
    }, 180);
  }
  window.__vizRefit = scheduleRefit;

  function onViewportChange() {
    classify();
    measureTopbar();
    // Le tiroir n'a pas de sens si on repasse en mode rail/desktop.
    if (!navIsDrawer()) setNavOpen(false);
    if (document.getElementById('page-graph') &&
        document.getElementById('page-graph').classList.contains('active')) {
      scheduleRefit();
    }
  }

  // ── 6. Cablage ───────────────────────────────────────────────────────────
  var toggle = document.getElementById('nav-toggle');
  if (toggle) toggle.addEventListener('click', window.toggleNav);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && body.classList.contains('nav-open')) {
      setNavOpen(false);
      if (e.stopPropagation) e.stopPropagation();
    }
  });

  // Un changement de page ferme le tiroir : on a navigue, il n'a plus d'objet.
  var originalSwitch = window.switchPage;
  if (typeof originalSwitch === 'function') {
    window.switchPage = function (name) {
      var r = originalSwitch.apply(this, arguments);
      if (body.classList.contains('nav-open')) setNavOpen(false);
      measureTopbar();
      if (name === 'graph') scheduleRefit();
      return r;
    };
  }

  window.addEventListener('resize', onViewportChange, { passive: true });
  window.addEventListener('orientationchange', onViewportChange, { passive: true });

  try {
    if (navigator.devicePosture && navigator.devicePosture.addEventListener) {
      navigator.devicePosture.addEventListener('change', onViewportChange);
    }
  } catch (e) { /* API absente */ }

  if (window.viewport && typeof window.viewport.addEventListener === 'function') {
    try { window.viewport.addEventListener('scroll', measureTopbar, { passive: true }); } catch (e) {}
  }

  // La topbar change de hauteur quand les stats arrivent ou que la police se
  // charge : on l'observe plutot que de la mesurer une seule fois au boot.
  if (topbar && typeof ResizeObserver === 'function') {
    try { new ResizeObserver(measureTopbar).observe(topbar); } catch (e) {}
  }

  classify();
  measureTopbar();

  // Expose l'etat pour la verification automatique.
  window.__vizDevice = function () {
    var segs = segments();
    return {
      kind: root.getAttribute('data-device'),
      segments: segs ? segs.length : 1,
      posture: root.getAttribute('data-posture'),
      tbH: lastTbH,
      navDrawer: navIsDrawer(),
      navOpen: body.classList.contains('nav-open'),
      vw: window.innerWidth,
      vh: window.innerHeight,
    };
  };
})();
