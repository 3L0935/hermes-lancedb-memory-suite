// README screenshots of the fictional vault (scripts/demo/build-fictional-vault.py).
// Usage: node readme-screenshots.cjs <outdir> [base_url]
// Needs playwright-core (PLAYWRIGHT_CORE=<path> to point at a copy) and a Chromium
// (CHROMIUM=<path>, default /usr/bin/chromium).
const { chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const out = process.argv[2];
const BASE = process.argv[3] || 'http://127.0.0.1:17779/';
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args: ['--no-sandbox'] });
  const desk = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  desk.on('pageerror', (e) => console.log('pageerror', e.message));
  const go = async (p, hash, wait = 1800) => { await p.goto(BASE + '#' + hash); await p.waitForTimeout(wait); };
  const shot = async (p, name) => { await p.screenshot({ path: `${out}/${name}.png` }); console.log('shot', name); };

  await go(desk, 'overview'); await shot(desk, '01-overview');
  await go(desk, 'memories'); await shot(desk, '02-memories');
  await go(desk, 'timeline'); await shot(desk, '03-timeline');
  await go(desk, 'tags'); await shot(desk, '04-tags');
  await go(desk, 'graph', 6000);
  await desk.evaluate(() => network && network.fit({ animation: false }));
  await desk.waitForTimeout(800); await shot(desk, '05-graph');
  await desk.evaluate(() => { const n = allData.nodes.find((x) => (x.label || '').includes('Jellyfin_Transcode')) || allData.nodes[0]; openSidebar(n.id); });
  await desk.waitForTimeout(1500); await shot(desk, '06-graph-selected');
  await desk.evaluate(() => closeSidebar());
  await desk.click('#show-declared'); await desk.waitForTimeout(5000);
  await desk.evaluate(() => network && network.fit({ animation: false }));
  await desk.waitForTimeout(800); await shot(desk, '07-graph-typed');
  await go(desk, 'clusters', 1500); await desk.evaluate(() => { const t = document.getElementById('cl-threshold'); t.value = 0.8; t.dispatchEvent(new Event('input')); }); await desk.waitForTimeout(3000); await shot(desk, '08-clusters');
  await go(desk, 'conflicts'); await shot(desk, '09-conflicts');
  await go(desk, 'duplicates', 2500); await shot(desk, '10-duplicates');
  await go(desk, 'review'); await desk.click('#review-scan'); await desk.waitForTimeout(6000); await shot(desk, '11-review');
  await go(desk, 'health', 1000); await desk.click('#health-scan'); await desk.waitForTimeout(9000); await shot(desk, '12-health');
  await desk.waitForFunction(() => document.querySelector('.scatter-busy') && document.querySelector('.scatter-busy').hidden, null, { timeout: 60000 });
  await desk.evaluate(() => document.getElementById('emb-container').scrollIntoView({ block: 'end' }));
  await desk.waitForTimeout(600); await shot(desk, '15-projection');

  const phone = await (await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })).newPage();
  await go(phone, 'overview'); await shot(phone, '13-phone-overview');
  await go(phone, 'graph', 6000); await shot(phone, '14-phone-graph');
  await b.close();
})();
