// Запись демо-ролика tbo-tracker: реальный прототип, Playwright + CDP screencast → кадры с метками времени.
// node record.js <setup-code-file>
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const FR = path.join(__dirname, 'frames');
fs.rmSync(FR, { recursive: true, force: true }); fs.mkdirSync(FR, { recursive: true });
const BASE = process.env.TBO_DEMO_URL || 'http://127.0.0.1:8766';
const log = [];

const OVERLAY = () => {
  if (document.getElementById('vx-root')) return;
  const root = document.createElement('div'); root.id = 'vx-root';
  Object.assign(root.style, { position: 'fixed', inset: '0', pointerEvents: 'none', zIndex: '2147483000', fontFamily: 'Inter, "Segoe UI", system-ui, sans-serif' });
  // подпись
  const cap = document.createElement('div'); cap.id = 'vx-cap';
  Object.assign(cap.style, { position: 'absolute', left: '50%', bottom: '28px', transform: 'translateX(-50%)', maxWidth: '1100px', background: 'rgba(14,26,43,0.94)', color: '#F3F1EB', borderRadius: '14px', padding: '14px 26px', fontSize: '22px', lineHeight: '1.35', fontWeight: '600', boxShadow: '0 12px 40px rgba(0,0,0,.35)', opacity: '0', transition: 'opacity .35s ease', display: 'flex', gap: '16px', alignItems: 'center' });
  cap.innerHTML = '<span id="vx-step" style="background:#E8601C;color:#0E1A2B;border-radius:8px;padding:3px 10px;font-size:18px;font-weight:800;white-space:nowrap"></span><span id="vx-text"></span>';
  // курсор
  const cur = document.createElement('div'); cur.id = 'vx-cur';
  Object.assign(cur.style, { position: 'absolute', left: '960px', top: '540px', width: '26px', height: '26px', transition: 'none' });
  cur.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 22 L8.5 16.5 L12.5 25 L16 23.5 L12 15 L20 15 Z" fill="#0E1A2B" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/></svg>';
  // подсветка
  const hl = document.createElement('div'); hl.id = 'vx-hl';
  Object.assign(hl.style, { position: 'absolute', border: '4px solid #E8601C', borderRadius: '12px', boxShadow: '0 0 0 9999px rgba(14,26,43,0.18)', opacity: '0', transition: 'all .45s ease' });
  // титульная карточка
  const card = document.createElement('div'); card.id = 'vx-card';
  Object.assign(card.style, { position: 'absolute', inset: '0', background: '#0E1A2B', color: '#F3F1EB', display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 120px', opacity: '0', transition: 'opacity .5s ease' });
  root.append(hl, cap, cur, card); document.body.append(root);
  const st = document.createElement('style'); st.textContent = '.toast-wrap{top:18px!important;bottom:auto!important}'; document.head.append(st);
  window.vx = {
    caption(step, text) { const c = document.getElementById('vx-cap'); document.getElementById('vx-step').textContent = step; document.getElementById('vx-text').textContent = text; c.style.opacity = text ? '1' : '0'; },
    hide() { document.getElementById('vx-cap').style.opacity = '0'; },
    card(html) { const k = document.getElementById('vx-card'); if (html) { k.innerHTML = html; k.style.opacity = '1'; } else k.style.opacity = '0'; },
    hl(sel, pad = 8) { const h = document.getElementById('vx-hl'); if (!sel) { h.style.opacity = '0'; return; } const el = typeof sel === 'string' ? document.querySelector(sel) : sel; if (!el) return; const r = el.getBoundingClientRect(); Object.assign(h.style, { left: r.left - pad + 'px', top: r.top - pad + 'px', width: r.width + 2 * pad + 'px', height: r.height + 2 * pad + 'px', opacity: '1' }); },
    hlRect(r) { const h = document.getElementById('vx-hl'); Object.assign(h.style, { left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px', opacity: '1' }); },
    move(x, y, ms = 700) {
      const c = document.getElementById('vx-cur'); const x0 = parseFloat(c.style.left), y0 = parseFloat(c.style.top); const t0 = performance.now();
      return new Promise((res) => { const f = (t) => { const k = Math.min(1, (t - t0) / ms); const e = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; c.style.left = x0 + (x - x0) * e + 'px'; c.style.top = y0 + (y - y0) * e + 'px'; k < 1 ? requestAnimationFrame(f) : res(); }; requestAnimationFrame(f); });
    },
    ripple(x, y) { const r = document.createElement('div'); Object.assign(r.style, { position: 'absolute', left: x - 22 + 'px', top: y - 22 + 'px', width: '44px', height: '44px', borderRadius: '50%', border: '4px solid #E8601C', opacity: '1', transform: 'scale(.4)', transition: 'all .5s ease-out' }); document.getElementById('vx-root').append(r); requestAnimationFrame(() => { r.style.transform = 'scale(1.4)'; r.style.opacity = '0'; }); setTimeout(() => r.remove(), 600); },
    ghost(srcEl, x1, y1, ms = 1200) {
      const r = srcEl.getBoundingClientRect(); const g = srcEl.cloneNode(true); Object.assign(g.style, { position: 'absolute', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', opacity: '0.92', boxShadow: '0 16px 40px rgba(0,0,0,.35)', transform: 'rotate(-2deg)', background: '#fff', borderRadius: '10px' });
      document.getElementById('vx-root').append(g); const x0 = r.left, y0 = r.top, t0 = performance.now(); const c = document.getElementById('vx-cur');
      return new Promise((res) => { const f = (t) => { const k = Math.min(1, (t - t0) / ms); const e = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; const nx = x0 + (x1 - x0) * e, ny = y0 + (y1 - y0) * e; g.style.left = nx + 'px'; g.style.top = ny + 'px'; c.style.left = nx + 30 + 'px'; c.style.top = ny + 20 + 'px'; k < 1 ? requestAnimationFrame(f) : (g.remove(), res()); }; requestAnimationFrame(f); });
    },
  };
};

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1920 / 1366, locale: 'ru-RU', bypassCSP: true });
  const p = await ctx.newPage();
  await p.goto(BASE + '/'); await p.waitForTimeout(1500);
  const code = fs.readFileSync(process.argv[2], 'utf8').trim();
  await p.evaluate(async (c) => { try { await api('POST', '/api/auth/setup', { code: c, username: 'admin', display_name: 'Инженер ТО', password: 'Demo-Passw0rd-2026!' }); } catch (e) { } }, code);
  await p.goto(BASE + '/'); await p.waitForTimeout(2000);
  await p.evaluate(OVERLAY);
  await p.evaluate(() => window.vx.card('<div style="font-size:30px;color:#F07A35;font-weight:700;letter-spacing:2px">LIVE PROTOTYPE</div><div style="font-size:120px;font-weight:800;letter-spacing:-3px;margin:10px 0 18px">tbo-tracker</div><div style="font-size:38px;color:#B7C3D0">Usage tracking for every drone component</div>'));
  await p.waitForTimeout(800);

  // --- запись
  const cdp = await ctx.newCDPSession(p);
  let n = 0; const T0 = Date.now(); const stamps = [];
  cdp.on('Page.screencastFrame', async (f) => {
    const i = n++; fs.writeFileSync(path.join(FR, `f${String(i).padStart(6, '0')}.jpg`), Buffer.from(f.data, 'base64'));
    stamps.push([i, Date.now() - T0]);
    try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) { }
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 90, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  const t = () => (Date.now() - T0) / 1000;
  const W = (ms) => p.waitForTimeout(ms);
  const mark = (name) => { log.push([name, +t().toFixed(1)]); console.log(name, t().toFixed(1)); };
  const cap = (s, x) => p.evaluate(([a, b]) => window.vx.caption(a, b), [s, x]);
  const hl = (sel, pad) => p.evaluate(([s, d]) => window.vx.hl(s, d), [sel, pad]);
  const center = async (loc) => { const b = await loc.boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; };
  const clickVis = async (loc) => { await loc.scrollIntoViewIfNeeded(); const [x, y] = await center(loc); await p.evaluate(([a, b]) => window.vx.move(a, b), [x, y]); await p.evaluate(([a, b]) => window.vx.ripple(a, b), [x, y]); await W(150); await loc.click(); };
  const nav = async (hash) => { await p.evaluate((h) => { location.hash = h; }, hash); await W(1200); await p.evaluate(() => window.scrollTo(0, 0)); };
  const scrollTo = (sel, block = 'start') => p.evaluate(([s, b]) => { const e = document.querySelector(s); if (e) e.scrollIntoView({ behavior: 'smooth', block: b }); }, [sel, block]);

  // 0 — титр
  mark('title'); await W(3200);
  await p.evaluate(() => window.vx.card(null)); await W(500);

  // 1 — обзор парка
  mark('overview');
  await cap('1 · Fleet overview', 'Airworthiness counts every installed part');
  await hl('table', 6);
  await W(2500);
  const row12 = p.locator('tr:has-text("Борт 12")').first();
  await p.evaluate(() => { const r = [...document.querySelectorAll('tr')].find((x) => x.textContent.includes('Борт 12')); window.vx.hl(r, 4); });
  const [rx, ry] = await center(row12); await p.evaluate(([a, b]) => window.vx.move(a, b), [rx + 120, ry]);
  await cap('1 · Fleet overview', 'Drone 12 is grounded: one of its parts is overdue');
  await W(3500);

  // 2 — паспорт борта
  mark('aircraft');
  await hl(null); await nav('#/aircraft/1');
  await cap('2 · Aircraft record', 'Each part: total hours vs. hours on this drone');
  await p.evaluate(() => { const h = [...document.querySelectorAll('h2,h3')].find((x) => /Конфигурация борта/.test(x.textContent)); if (h) h.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' }); }); await W(900);
  await p.evaluate(() => { const h = [...document.querySelectorAll('h2,h3')].find((x) => /Конфигурация борта/.test(x.textContent)); if (h) window.vx.hl(h.closest('.card'), 4); });
  await W(4200);

  // 3 — импорт журнала
  mark('import');
  await hl(null); await nav('#/imports');
  await cap('3 · Flight log import', 'Upload one shift log from the ground station');
  const b1 = p.locator('[data-demo="01_TBT-M4-001_smena.json"]');
  await W(800); await clickVis(b1); await W(900);
  await scrollTo('#result'); await W(900);
  await cap('3 · Flight log import', '+3.52 h credited to the drone and all 7 installed parts');
  await p.evaluate(() => { const t = [...document.querySelectorAll('#result h3, #result h4, #result b, #result div')].find((x) => /Начисление узлам/.test(x.textContent) && x.textContent.length < 80); const tbl = t && t.nextElementSibling; window.vx.hl(tbl && tbl.tagName === 'TABLE' ? tbl : (t ? t.parentElement : document.querySelector('#result')), 6); });
  await W(3800);
  await cap('3 · Flight log import', 'Maintenance alerts fire automatically');
  await p.evaluate(() => { const t = [...document.querySelectorAll('#result *')].find((x) => /Сработавшие напоминания/.test(x.textContent) && x.textContent.length < 40); if (t) { t.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(() => window.vx.hl(t.nextElementSibling || t, 6), 500); } });
  await W(3800);

  // 4 — повторная загрузка
  mark('duplicate');
  await hl(null); await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await W(600);
  await cap('4 · Duplicate upload', 'Same log uploaded again …');
  await clickVis(p.locator('[data-demo="03_TBT-M4-001_smena_POVTOR.json"]')); await W(700);
  await scrollTo('#result'); await W(700);
  await W(500); await cap('4 · Duplicate upload', '… 0 hours credited — never double-counted');
  await hl('#result .notice', 8); await W(3400);

  // 5 — журнал с ошибками
  mark('errors');
  await hl(null); await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await W(600);
  await cap('5 · Faulty log', 'A log with bad rows …');
  await clickVis(p.locator('[data-demo="06_s_oshibkami.csv"]')); await W(700);
  await scrollTo('#result'); await W(700);
  await W(500); await cap('5 · Faulty log', '… bad rows rejected with a reason, valid rows imported');
  await hl('#result .notice', 8); await W(3600);

  // 6 — падение
  mark('crash');
  await hl(null); await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await W(600);
  await cap('6 · Crash detection', 'Accelerometer data in the log …');
  await clickVis(p.locator('[data-demo="07_TBT-VT2-011_padenie.json"]')); await W(700);
  await p.evaluate(() => { const t = [...document.querySelectorAll('#result *')].find((x) => /Мониторинг датчиков/.test(x.textContent) && x.textContent.length < 60); if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  await W(800);
  await cap('6 · Crash detection', '… fall detected → unscheduled inspection required');
  await p.evaluate(() => { const t = [...document.querySelectorAll('#result *')].find((x) => /Мониторинг датчиков/.test(x.textContent) && x.textContent.length < 60); if (t) { const a = t.getBoundingClientRect(); const s = [...document.querySelectorAll('#result *')].find((x) => /Сработавшие напоминания/.test(x.textContent) && x.textContent.length < 40); const tb = s && s.nextElementSibling ? s.nextElementSibling.getBoundingClientRect() : a; window.vx.hlRect({ x: Math.min(a.left, tb.left) - 6, y: a.top - 6, w: Math.max(a.right, tb.right) - Math.min(a.left, tb.left) + 12, h: tb.bottom - a.top + 12 }); } });
  await W(4000);

  // 7 — перестановка перетаскиванием
  mark('swap');
  await hl(null); await nav('#/board');
  await cap('7 · Part swap', 'Drag a spare flight controller onto Drone 03');
  await W(900);
  const src = p.locator('.chip-c[draggable]:has-text("FC7-0105")').first();
  const dst = p.locator('.bslot:has-text("не укомплектовано")').first();
  const [sx, sy] = await center(src); const dbox = await dst.boundingBox();
  await p.evaluate(([a, b]) => window.vx.move(a, b), [sx, sy]); await W(300);
  await p.evaluate(([x1, y1]) => { const el = [...document.querySelectorAll('.chip-c[draggable]')].find((e) => e.textContent.includes('FC7-0105')); return window.vx.ghost(el, x1, y1, 1400); }, [dbox.x + 10, dbox.y + 4]);
  await src.dragTo(dst); await W(900);
  await cap('7 · Part swap', 'Confirm — the part keeps its history');
  await hl('.modal', 6); await W(1800);
  const ok = p.locator('.modal [data-ok]'); await clickVis(ok); await W(1200);
  await cap('7 · Part swap', 'Drone 03 is complete; due dates recalculated instantly');
  await p.evaluate(() => { const c = [...document.querySelectorAll('.card')].find((x) => x.textContent.includes('Борт 03')); if (c) window.vx.hl(c, 4); });
  await W(3800);

  // 8 — выполнение ТО
  mark('maintenance');
  await hl(null); await nav('#/');
  await cap('8 · Maintenance', 'Complete the overdue task on Drone 12 …');
  const rowB = p.locator('tr:has-text("BVK2-0405")').first(); await rowB.scrollIntoViewIfNeeded(); await W(500);
  await p.evaluate(() => { const r = [...document.querySelectorAll('tr')].find((x) => x.textContent.includes('BVK2-0405') && x.querySelector('[data-do]')); window.vx.hl(r, 4); });
  await W(1200);
  await clickVis(rowB.locator('[data-do]').first()); await W(1200);
  await hl('.modal', 6); await W(1500);
  await clickVis(p.locator('.modal [data-ok]')); await W(1300);
  await hl(null); await nav('#/');
  await p.evaluate(() => { const r = [...document.querySelectorAll('tr')].find((x) => x.textContent.includes('Борт 12')); if (r) window.vx.hl(r, 4); });
  await cap('8 · Maintenance', '… Drone 12 is cleared to fly again');
  await W(3800);

  // 9 — сценарии проверки
  mark('scenarios');
  await hl(null); await nav('#/scenarios');
  await cap('9 · Acceptance tests', 'Each test scenario from the brief runs with one click');
  await W(600);
  const runs = p.locator('[data-run]');
  await clickVis(p.locator('[data-run="4"]')); await W(1700);
  await p.evaluate(() => { const o = document.querySelector('#out4'); if (o) { o.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(() => window.vx.hl(o.closest('.card'), 4), 600); } });
  await W(3200);

  // 10 — финал
  mark('end');
  await hl(null); await cap('', '');
  await p.evaluate(() => window.vx.card('<div style="font-size:84px;font-weight:800;letter-spacing:-2px;margin-bottom:28px">tbo-tracker</div><div style="font-size:40px;line-height:1.5;color:#B7C3D0">Every part tracked · any drone brand<br>Cloud or air-gapped · 10 acceptance scenarios</div>'));
  await W(4000);
  mark('stop');
  await cdp.send('Page.stopScreencast'); await W(300);
  fs.writeFileSync(path.join(__dirname, 'stamps.json'), JSON.stringify(stamps));
  fs.writeFileSync(path.join(__dirname, 'timeline.json'), JSON.stringify(log));
  await browser.close();
  console.log('frames', n);
})();
