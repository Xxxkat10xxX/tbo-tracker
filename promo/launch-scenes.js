/* Ролик: сцены (обновление кадра по времени), звук, window.seek(t). */
'use strict';
const UPD = {};

// ---- 0. Хук
UPD.w0 = (t) => {
  maskIn(E('h1a'), t, 0.00, 0.34, 1.50, 0.24, -1);
  maskIn(E('h1b'), t, 0.06, 0.34, 1.53, 0.24, -1);
  maskIn(E('h2a'), t, 0.50, 0.34, 1.58, 0.24, 1);
  maskIn(E('h2b'), t, 0.56, 0.34, 1.61, 0.24, 1);
  const g = eOutCubic(prog(t, 1.04, 0.2));                 // перечёркнутое слово гаснет
  E('h2w').style.color = mixHex('#17202e', '#a3abbb', g);
};

// ---- 1. Перестановка
UPD.w1 = (t) => {
  const w = E('w1');
  const u0 = eOutExpo(prog(t, 2.0, 0.46));
  clipFrom(w, A.w1.w, A.w1.h, A.btnT, u0, 8, 0, -120);
  // модальное окно раскрывается из капсулы и складывается обратно в неё
  const m = E('m1');
  const open = eOutExpo(prog(t, 2.64, 0.34)), close = eInCubic(prog(t, 3.56, 0.24));
  const bg = E('m1bg'), bOpen = eOutExpo(prog(t, 2.56, 0.4)), bClose = eInCubic(prog(t, 3.6, 0.26));
  if (t < 2.56 || bClose >= 1) bg.style.visibility = 'hidden';
  else { bg.style.visibility = 'visible'; clipFrom(bg, 2400, 2400, { x: A.btnT.x + 700, y: A.btnT.y + 700, w: A.btnT.w, h: A.btnT.h }, bOpen * (1 - bClose), 8, 0, 0); }
  if (t < 2.64 || close >= 1) m.style.visibility = 'hidden';
  else { m.style.visibility = 'visible'; clipFrom(m, A.m1.w, A.m1.h, A.mOkInM, open * (1 - close), 8, 12, -70); }
  // фокус на селекте и выбор «Борт 03»
  const foc = pulse(t, 2.98, 0.36);
  E('sel-ac').style.borderColor = mixHex('#d5dbe6', '#17202e', foc * 0.85);
  swapAt(E('sw-ac'), t, 3.03, 0.3);
  swapAt(E('sw-pos'), t, 3.09, 0.3);
  // после «Переставить»: карточка обновляется
  swapAt(E('sw-onb'), t, 3.62);
  swapAt(E('sw-onbl'), t, 3.66);
  swapAt(E('sw-boards'), t, 3.7);
  swapAt(E('sw-now'), t, 3.74);
  swapAt(E('sw-b02'), t, 3.9, 0.32);
  // новая строка «Борт 03» раскрывается
  const f = eOutExpo(prog(t, 3.96, 0.34));
  for (const el of document.querySelectorAll('#r-new .fold')) el.style.height = (A.fold * f) + 'px';
  for (const td of document.querySelectorAll('#r-new td')) {
    td.style.paddingTop = td.style.paddingBottom = (8 * f) + 'px';
    td.style.borderBottomWidth = f > 0.01 ? '1px' : '0px';
  }
  // титр
  const ci = E('cap1').querySelectorAll('.i');
  maskIn(ci[0], t, 4.02, 0.36); maskIn(ci[1], t, 4.1, 0.36);
};

// ---- 2–3. Импорт журнала и сработавшее напоминание
UPD.w2 = (t) => {
  const w = E('w2');
  clipFrom(w, A.w2.w, A.w2.h, A.btnGo, eOutExpo(prog(t, 5.0, 0.44)), 8, 0, -120);
  // зона загрузки: файл над ней → отпущен
  const over = clamp(prog(t, 5.24, 0.16) - prog(t, 5.5, 0.14));
  const drop = E('drop');
  drop.style.borderColor = mixHex('#c9d2e2', '#8b95a8', over);
  drop.style.background = mixHex('#fbfcfe', '#eef1f6', over);
  const sel = eOutExpo(prog(t, 5.56, 0.34));
  E('fn-in').style.clipPath = `inset(-4px ${(1 - sel) * 102}% -4px -2px)`;
  E('b-check').style.opacity = lerp(0.5, 1, eOutCubic(prog(t, 5.56, 0.16)));
  // результат раскрывается сверху вниз, строки — по одной
  const res = E('c-res');
  if (t < 6.06) res.style.visibility = 'hidden';
  else {
    res.style.visibility = 'visible';
    let vis = A.tbodyTop * eOutExpo(prog(t, 6.06, 0.36));
    A.rows.forEach((rh, k) => { vis += rh * eOutCubic(prog(t, 6.42 + 0.058 * k, 0.13)); });
    vis += (A.tblBottom - A.tbodyTop - A.rows.reduce((a, b) => a + b, 0)) * eOutCubic(prog(t, 6.84, 0.12));
    vis += (A.res.h - A.tblBottom) * eOutExpo(prog(t, 7.5, 0.34));
    res.style.clipPath = `inset(-40px 0px ${Math.max(-40, A.res.h - vis)}px 0px round 10px)`;
  }
  // блок «Сработавшие напоминания» разворачивается из шкалы
  const rs = E('rem-sec');
  if (t < 7.5) rs.style.visibility = 'hidden';
  else { rs.style.visibility = 'visible'; clipFrom(rs, A.rem.w, A.rem.h, A.remBarInRem, eOutExpo(prog(t, 7.52, 0.36)), 4, 0, -30); }
  const used = 96.07 + (100.92 - 96.07) * eOutExpo(prog(t, 8.0, 0.32));
  E('rem-used').textContent = fmt1(used);
  swapAt(E('sw-rem'), t, 8.16, 0.3);
  E('rem-to').style.clipPath = `inset(-6px ${(1 - eOutExpo(prog(t, 8.5, 0.3))) * 100}% -6px 0)`;
  // титры
  const c2 = E('cap2').querySelectorAll('.i');
  maskIn(c2[0], t, 6.52, 0.36); maskIn(c2[1], t, 7.0, 0.36);
  const c3 = E('cap3').querySelectorAll('.i');
  maskIn(c3[0], t, 8.02, 0.36); maskIn(c3[1], t, 8.52, 0.36);
};

// ---- 4. Эффект: пропуск ТО предотвращён (на дропе)
UPD.w4 = (t) => {
  const c = E('c-prev');
  const s1 = eOutExpo(prog(t, 10.0, 0.26)), s2 = eOutExpo(prog(t, 10.5, 0.36));
  const src = A.prevBarInCard, row = A.rowInCard;
  applyTwoStep(c, src, row, s1, s2);
  const ci = E('cap4').querySelectorAll('.i');
  maskIn(ci[0], t, 11.0, 0.36); maskIn(ci[1], t, 11.08, 0.36);
};
function applyTwoStep(el, src, row, u1, u2) {
  const ew = A.prev.w, eh = A.prev.h;
  const mid = { x: lerp(src.x, row.x, u1), y: lerp(src.y, row.y, u1), w: lerp(src.w, row.w, u1), h: lerp(src.h, row.h, u1) };
  clipFrom(el, ew, eh, mid, u2, lerp(4, 6, u1), 10, -60);
}

// ---- 5. Логотип и карточка
UPD.w5 = (t) => {
  const wx = (x) => -A.wmShift * (1 - eOutExpo(prog(x, 13.0, 0.4)));
  E('wm').style.transform = `translateX(${wx(t)}px)`;
  dirBlur(E('wm'), 'h', Math.abs(wx(t) - wx(t - 1 / 180)));
  maskIn(E('tag').querySelector('.i'), t, 13.5, 0.36);
  maskIn(E('cta').querySelector('.i'), t, 14.0, 0.36);
  E('ul').style.transform = `scaleX(${eOutExpo(prog(t, 14.5, 0.36))})`;
};

// ====================================================================== seek
const WORLDS = ['w0', 'w1', 'w2', 'w4', 'w5'];
window.seek = function (t) {
  t = clamp(t, 0, DUR - 1e-6);
  const sc = SCN[sceneIndexAt(t)];
  for (const id of WORLDS) E('s_' + id).style.display = id === sc.world ? 'block' : 'none';
  const c = camOf(sc.world, t);
  E(sc.world).style.transform = `translate(${W / 2}px,${H / 2}px) scale(${c.s}) translate(${-c.cx}px,${-c.cy}px)`;
  // смаз камеры: смещение за интервал субкадра (1/180 с); панорама — по осям, зум — радиально (средний радиус)
  const dt = 1 / 180, cp = t - dt >= SCN[sceneIndexAt(t)].t0 ? camOf(sc.world, t - dt) : c;
  const zr = Math.abs(Math.log(c.s / cp.s));
  const bx = (Math.abs(c.cx - cp.cx) * c.s + zr * 520) / 3, by = (Math.abs(c.cy - cp.cy) * c.s + zr * 300) / 3;
  setBlur('mb-cam-g', E('s_' + sc.world).style, bx, by);
  UPD[sc.world](t);
  drawCapsule(t);
  drawCursor(t);
};

// ====================================================================== сборка таймлайна (замеры → камеры → траектории)
async function init() {
  await document.fonts.ready;
  document.body.classList.add('measure');
  for (const id of WORLDS) { E('s_' + id).style.display = 'block'; E(id).style.transform = 'none'; E(id).style.clipPath = 'none'; }
  // хук по центру кадра
  const hook = E('hook'), hb = hook.getBoundingClientRect();
  hook.style.left = Math.round((W - hb.width) / 2) + 'px'; hook.style.top = Math.round((H - hb.height) / 2 - 8) + 'px';
  measure('strike', '#h2w', 'w0');
  // 1
  A.w1 = { x: 0, y: 0, w: 988, h: E('w1').getBoundingClientRect().height };
  measure('btnT', '#a-transfer', 'w1'); measure('mOk', '#m-ok', 'w1'); measure('m1', '#m1', 'w1');
  measure('selAc', '#sel-ac', 'w1'); measure('nowSlot', '#now-slot', 'w1'); measure('hist', '#c-hist', 'w1');
  measure('cap1', '#cap1', 'w1'); measure('rTot', '#r-tot', 'w1'); measure('r02', '#r-02', 'w1'); measure('stats', '#c-stats', 'w1');
  A.mOkInM = rel(A.mOk, A.m1);
  A.fold = Math.max(...[...document.querySelectorAll('#r-new .fold')].map((el) => el.getBoundingClientRect().height));
  // 2–3
  A.w2 = { x: 0, y: 0, w: 988, h: E('w2').getBoundingClientRect().height };
  measure('btnGo', '#b-go', 'w2'); measure('drop', '#drop', 'w2'); measure('fname', '#fn-in', 'w2'); measure('up', '#c-up', 'w2');
  measure('cap2', '#cap2', 'w2'); measure('res', '#c-res', 'w2'); measure('tcomp', '#t-comp', 'w2'); measure('fcH', '#fc-h', 'w2');
  measure('rem', '#rem-sec', 'w2'); measure('remBar', '#rem-bar', 'w2'); measure('remBadges', '#rem-badges', 'w2');
  measure('remMetric', '#rem-metric', 'w2'); measure('rRem', '#r-rem', 'w2'); measure('cap3', '#cap3', 'w2');
  const tb = E('t-comp').tBodies[0];
  A.tbodyTop = tb.getBoundingClientRect().top - E('c-res').getBoundingClientRect().top;
  A.rows = [...tb.rows].map((r) => r.getBoundingClientRect().height);
  A.tblBottom = E('t-comp').getBoundingClientRect().bottom - E('c-res').getBoundingClientRect().top + 16;
  A.remBarInRem = rel(A.remBar, A.rem);
  // 4
  measure('prev', '#c-prev', 'w4'); measure('prevBar', '#prev-bar', 'w4'); measure('rPrev', '#r-prev', 'w4'); measure('cap4', '#cap4', 'w4');
  measure('pNaive', '#p-naive', 'w4'); measure('pReal', '#p-real', 'w4'); measure('pMetric', '#p-metric', 'w4');
  measure('thead4', '#c-prev thead', 'w4'); measure('thNaive', '#c-prev thead th:nth-child(3)', 'w4');
  A.prevBarInCard = rel(A.prevBar, A.prev);
  A.rowInCard = rel(pad(U(A.thNaive, A.pNaive, A.pMetric, { x: A.pMetric.x, y: A.rPrev.y, w: A.pMetric.w, h: A.rPrev.h }), 2, 0), A.prev);

  // ---------------- камеры
  // 0: лёгкий наезд к зачёркнутому слову
  const st = A.strike, [scx, scy] = ctr(st);
  CAM.w0 = [{ cx: W / 2, cy: H / 2, z: 1 },
    { t: 1.0, d: 0.5, ease: eInOutCubic, cx: lerp(W / 2, scx, 0.2), cy: lerp(H / 2, scy, 0.2), z: 1.06 },
    { t: 1.5, d: 0.5, ease: eInOutExpo, cx: scx, cy: scy, z: 1.85 }];
  const z0end = 1.85;
  // 1: кнопка «Переставить» стоит там, где закончилась капсула-зачёркивание
  const cS = camOf('w0', 1.999), sR = rectW2S(cS, strikeWorld(1.999));
  const [bcx, bcy] = ctr(A.btnT);
  const k1a = camPinned(bcx, bcy, sR.x + sR.w / 2, sR.y + sR.h / 2, z0end, 2.0);
  const fHead = fit({ x: -30, y: 52, w: 900, h: 300 }, 0, 0, 2.45);
  const fModal = fit(A.m1, 70, 60, 2.95, 2.3);
  const fHist = fit(U(A.hist, A.cap1), 40, 50, 4.0);
  const totC = ctr(A.rTot);
  CAM.w1 = [{ ...k1a },
    { t: 2.0, d: 0.46, ...fHead },
    { t: 2.55, d: 0.42, ...fModal },
    { t: 3.58, d: 0.44, ...fHist },
    { t: 4.48, d: 0.5, ease: eInOutCubic, cx: lerp(fHist.cx, totC[0], 0.08), cy: lerp(fHist.cy, totC[1], 0.18), z: fHist.z * 1.05 }];
  const z1end = fHist.z * 1.05;
  // 2: «Импортировать» встаёт на место бейджа «сейчас»
  const cN = camOf('w1', 4.999), nR = rectW2S(cN, A.nowSlot);
  const [gcx, gcy] = ctr(A.btnGo);
  const k2a = camPinned(gcx, gcy, nR.x + nR.w / 2, nR.y + nR.h / 2, z1end, 5.0);
  const fUp = fit({ x: -30, y: -20, w: 960, h: A.up.h + 40 }, 0, 0, 5.4);
  const fRes = fit(U(A.cap2, A.tcomp), 40, 34, 6.6);
  const remU = U(A.rem, A.cap3);
  const fRem = fit({ x: remU.x, y: A.rem.y - 60, w: remU.w, h: remU.y + remU.h + 220 - (A.rem.y - 60) }, 44, 0, 7.9);
  const [bgx, bgy] = ctr(U(A.remBadges, A.remMetric));
  const [rbx, rby] = ctr(A.remBar);
  CAM.w2 = [{ ...k2a },
    { t: 5.0, d: 0.44, ...fUp },
    { t: 6.08, d: 0.52, ...fRes },
    { t: 7.5, d: 0.4, ...fRem },
    { t: 9.0, d: 0.45, ease: eInOutCubic, cx: lerp(fRem.cx, bgx, 0.06), cy: lerp(fRem.cy, bgy, 0.2), z: fRem.z * 1.045 },
    { t: 9.5, d: 0.5, ease: eInCubic, cx: rbx, cy: rby, z: 2.55 }];
  // 4: шкала аналитики встаёт на место шкалы напоминания (дроп)
  const cR = camOf('w2', 9.999), rR = rectW2S(cR, A.remBar);
  const [pbx, pby] = ctr(A.prevBar);
  const k4a = camPinned(pbx, pby, rR.x + rR.w / 2, rR.y + rR.h / 2, 2.55, 10.0);
  const fRow = fit(U(A.thNaive, A.pNaive, A.pMetric), 60, 150, 10.3, 2.7);
  const fCard = fit(A.prev, 36, 40, 10.8);
  const fAll = fit(U(A.cap4, A.prev), 72, 40, 11.2);
  CAM.w4 = [{ ...k4a },
    { t: 10.0, d: 0.42, ease: eOutExpo, ...fRow },
    { t: 10.5, d: 0.45, ...fCard },
    { t: 10.95, d: 0.45, ...fAll },
    { t: 11.5, d: 0.5, ease: eInOutCubic, cx: fAll.cx, cy: lerp(fAll.cy, pby, 0.08), z: fAll.z * 1.05 }];
  const z4end = fAll.z * 1.05;
  // 5: финальная карточка свёрстана в экранных единицах при t = 12
  CAM.w5 = [{ cx: 0, cy: 0, z: z4end }];
  layoutEnd(z4end * push(12.0));

  // ---------------- капсула: сегменты «к какому элементу приклеена»
  SEG = [
    { ts: 1.0, d: 0, target: (t) => slotRaw('w0', strikeWorld(t), t, 'pill') },
    { ts: 1.8, d: 0.42, ease: eInOutExpo, target: (t) => slot('w1', A.btnT, t, { r: 8, lbl: 'T' }) },
    { ts: 2.55, d: 0.32, ease: eInOutExpo, target: (t) => slot('w1', A.mOk, t, { r: 8, lbl: 'OK' }) },
    { ts: 3.84, d: 0.42, ease: eInOutExpo, target: (t) => slot('w1', A.nowSlot, t, { r: 'pill', lbl: 'NOW' }) },
    { ts: 5.0, d: 0.36, ease: eInOutExpo, target: (t) => slot('w2', A.btnGo, t, { r: 8, lbl: 'GO', op: lerp(0.5, 1, eOutCubic(prog(t, 5.56, 0.16))) }) },
    { ts: 6.18, d: 0.5, ease: eInOutExpo, target: (t) => slot('w2', pad(A.fcH, 7, 3), t, { r: 'pill', lbl: 'P485' }) },
    { ts: 7.5, d: 0.3, ease: eInOutExpo, target: (t) => slot('w2', barFill(A.remBar, remFill(t)), t, { r: 'pill' }) },
    { ts: 10.0, d: 0.2, ease: eOutExpo, target: (t) => slot('w4', A.prevBar, t, { r: 'pill' }) },
    { ts: 12.0, d: 0.66, ease: springEase(0.44), target: (t) => logoTarget(t) },
  ];
  // ---------------- курсор
  const off = (x, y) => () => [x, y];
  CUR = [
    { ts: -1, d: 0, target: off(2120, 1240) },
    { ts: 2.06, d: 0.4, target: (t) => ptW('w1', A.btnT, 0.7, 0.66, t) },
    { ts: 2.6, d: 0.36, target: (t) => ptW('w1', A.selAc, 0.42, 0.6, t) },
    { ts: 3.1, d: 0.36, target: (t) => ptW('w1', A.mOk, 0.62, 0.62, t) },
    { ts: 3.62, d: 0.5, target: (t) => ptW('w1', A.r02, 0.16, 0.42, t) },
    { ts: 4.5, d: 0.45, target: off(2120, 1000) },
    { ts: 4.98, d: 0, target: off(1990, 640) },
    { ts: 5.0, d: 0.46, target: (t) => ptW('w2', A.drop, 0.52, 0.56, t) },
    { ts: 5.56, d: 0.38, target: (t) => ptW('w2', A.btnGo, 0.62, 0.62, t) },
    { ts: 6.12, d: 0.55, target: off(2120, 1240) },
  ];
  document.body.classList.remove('measure');
  for (const id of WORLDS) E(id).style.transform = '';
  window.__ready = true;
}
const pad = (r, px, py) => ({ x: r.x - px, y: r.y - py, w: r.w + 2 * px, h: r.h + 2 * py });
const barFill = (r, f) => ({ x: r.x, y: r.y, w: r.w * f, h: r.h });
function remFill(t) { return 0.9607 + (1 - 0.9607) * clamp(spring(t, 8.0, 0.32)); }
function slotRaw(wid, a, t, r) { const c = camOf(wid, t), R = rectW2S(c, a); return { ...R, r: r === 'pill' ? R.h / 2 : r, lbl: null, ls: c.s, op: 1, chev: 0 }; }
function strikeWorld(t) {     // зачёркивание растёт слева направо пружиной
  const s = A.strike, h = 24, full = s.w + 30;
  return { x: s.x - 15, y: s.y + s.h * 0.56 - h / 2, w: full * clamp(spring(t, 1.0, 0.34, 0.8), 0, 1.03), h };
}
function layoutEnd(s0) {
  const u = 1 / s0, D = 196 * u, gap = 40 * u;
  const wm = E('wm'), tag = E('tag'), cta = E('cta');
  wm.style.fontSize = (134 * u) + 'px'; tag.style.fontSize = (34 * u) + 'px'; cta.style.fontSize = (30 * u) + 'px';
  const wmW = wm.getBoundingClientRect().width / 1, tagW = tag.getBoundingClientRect().width, ctaW = cta.getBoundingClientRect().width;
  // замер шёл без масштаба (transform: none), значит ширины уже в мировых единицах
  const rowW = D + gap + wmW, left = -rowW / 2;
  const blockH = D + 30 * u + 34 * u * 1.3 + 54 * u + 30 * u * 1.4;
  const top = -blockH / 2 - 10 * u;
  A.logo = { x: left, y: top, w: D, h: D };
  const textX = left + D + gap;
  const clip = E('wm-clip');
  Object.assign(clip.style, { left: (left + D / 2) + 'px', top: (top + D / 2 - 134 * u * 0.62) + 'px', width: (wmW + gap + D / 2 + 20 * u) + 'px', height: (134 * u * 1.25) + 'px' });
  wm.style.left = (D / 2 + gap) + 'px'; wm.style.top = '0px';
  A.wmShift = wmW + gap + D / 2 + 10 * u;
  Object.assign(tag.style, { left: textX + 'px', top: (top + D + 30 * u) + 'px' });
  Object.assign(cta.style, { left: textX + 'px', top: (top + D + 30 * u + 34 * u * 1.3 + 54 * u) + 'px' });
  const urlR = E('url').getBoundingClientRect(), ctaR = cta.getBoundingClientRect();
  Object.assign(E('ul').style, { left: (urlR.left - ctaR.left) + 'px', width: urlR.width + 'px', top: (30 * u * 1.28) + 'px', height: Math.max(1, 3 * u) + 'px' });
}
function logoTarget(t) { const c = camOf('w5', t), R = rectW2S(c, A.logo); return { ...R, r: R.w / 2, lbl: null, ls: c.s, op: 1, chev: 1 }; }

// ====================================================================== саундтрек (OfflineAudioContext → WAV)
window.renderSoundtrack = async function () {
  const SR = 48000, N = Math.round(SR * DUR);
  const ctx = new OfflineAudioContext(2, N, SR);
  const master = ctx.createGain(); master.gain.setValueAtTime(0.82, 0); master.gain.setValueAtTime(0.82, 14.72); master.gain.linearRampToValueAtTime(0.0, 14.995);
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -14; comp.knee.value = 8; comp.ratio.value = 3.2; comp.attack.value = 0.004; comp.release.value = 0.14;
  master.connect(comp); comp.connect(ctx.destination);
  const drums = ctx.createGain(); drums.gain.value = 0.95; drums.connect(master);
  const fx = ctx.createGain(); fx.gain.value = 0.85; fx.connect(master);
  const music = ctx.createGain(); music.gain.value = 0.5; music.connect(master);
  const duck = ctx.createGain(); duck.gain.setValueAtTime(1, 0); duck.connect(music);
  const pan = (node, p) => { const sp = ctx.createStereoPanner(); sp.pan.value = p; node.connect(sp); return sp; };
  // детерминированный шум
  const nb = ctx.createBuffer(1, SR * 4, SR), nd = nb.getChannelData(0);
  let seed = 1234567;
  for (let i = 0; i < nd.length; i++) { seed = (seed * 1103515245 + 12345) >>> 0; nd[i] = ((seed >>> 8) / 8388608) - 1; }
  const noise = (t, dur) => { const s = ctx.createBufferSource(); s.buffer = nb; s.start(t, (t * 0.37) % 1.0, dur + 0.05); return s; };
  const env = (g, t, a, peak, dec, floor = 0.0001) => { g.gain.setValueAtTime(floor, t); g.gain.linearRampToValueAtTime(peak, t + a); g.gain.exponentialRampToValueAtTime(floor, t + a + dec); };

  function kick(t, amp = 1) {
    const o = ctx.createOscillator(); o.type = 'sine';
    o.frequency.setValueAtTime(165, t); o.frequency.exponentialRampToValueAtTime(54, t + 0.085); o.frequency.exponentialRampToValueAtTime(43, t + 0.36);
    const g = ctx.createGain(); env(g, t, 0.003, amp, 0.42);
    o.connect(g).connect(drums); o.start(t); o.stop(t + 0.5);
    const n = noise(t, 0.02), hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 2500;
    const ng = ctx.createGain(); env(ng, t, 0.001, 0.22 * amp, 0.018);
    n.connect(hp).connect(ng).connect(drums);
    duck.gain.setValueAtTime(1, t); duck.gain.linearRampToValueAtTime(0.32, t + 0.012); duck.gain.linearRampToValueAtTime(1, t + 0.24);
  }
  function clap(t, amp = 0.5) {
    const bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 1250; bp.Q.value = 0.9;
    const g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t);
    for (const o of [0, 0.011, 0.022]) { g.gain.setValueAtTime(amp, t + o); g.gain.exponentialRampToValueAtTime(amp * 0.25, t + o + 0.009); }
    g.gain.setValueAtTime(amp * 0.8, t + 0.033); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.24);
    noise(t, 0.26).connect(bp).connect(g).connect(drums);
  }
  function hat(t, amp = 0.12, open = false, p = 0.2) {
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 7600;
    const g = ctx.createGain(); env(g, t, 0.001, amp, open ? 0.14 : 0.034);
    pan(noise(t, open ? 0.2 : 0.06).connect(hp).connect(g), p).connect(drums);
  }
  function bass(t, dur, f, amp = 0.5, bright = 1) {
    const o = ctx.createOscillator(); o.type = 'sawtooth'; o.frequency.value = f;
    const sub = ctx.createOscillator(); sub.type = 'sine'; sub.frequency.value = f / 2;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 5;
    lp.frequency.setValueAtTime(260 + 1100 * bright, t); lp.frequency.exponentialRampToValueAtTime(220, t + dur * 0.9);
    const g = ctx.createGain(); env(g, t, 0.006, amp, dur);
    const sg = ctx.createGain(); env(sg, t, 0.006, amp * 0.9, dur);
    o.connect(lp).connect(g).connect(duck); sub.connect(sg).connect(duck);
    o.start(t); o.stop(t + dur + 0.05); sub.start(t); sub.stop(t + dur + 0.05);
  }
  function pad(t, dur, fs, amp = 0.07) {
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 1500; lp.Q.value = 0.4;
    const g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.linearRampToValueAtTime(amp, t + 0.08);
    g.gain.setValueAtTime(amp, t + dur - 0.12); g.gain.linearRampToValueAtTime(0.0001, t + dur);
    lp.connect(g).connect(duck);
    fs.forEach((f, i) => { for (const det of [-7, 7]) { const o = ctx.createOscillator(); o.type = 'sawtooth'; o.frequency.value = f; o.detune.value = det + i; pan(o, det > 0 ? 0.35 : -0.35).connect(lp); o.start(t); o.stop(t + dur + 0.02); } });
  }
  function stab(t, fs, amp = 0.16, dur = 0.32) {
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 1.2;
    lp.frequency.setValueAtTime(4200, t); lp.frequency.exponentialRampToValueAtTime(700, t + dur);
    const g = ctx.createGain(); env(g, t, 0.004, amp, dur);
    lp.connect(g).connect(fx);
    fs.forEach((f) => { for (const [type, det] of [['sawtooth', -9], ['square', 9]]) { const o = ctx.createOscillator(); o.type = type; o.frequency.value = f; o.detune.value = det; o.connect(lp); o.start(t); o.stop(t + dur + 0.02); } });
  }
  function pluck(t, f, amp = 0.09) {
    const o = ctx.createOscillator(); o.type = 'triangle'; o.frequency.value = f;
    const o2 = ctx.createOscillator(); o2.type = 'sawtooth'; o2.frequency.value = f * 2; o2.detune.value = 5;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.setValueAtTime(3400, t); lp.frequency.exponentialRampToValueAtTime(600, t + 0.16);
    const g = ctx.createGain(); env(g, t, 0.002, amp, 0.17);
    const g2 = ctx.createGain(); g2.gain.value = 0.3;
    o.connect(lp); o2.connect(g2).connect(lp); lp.connect(g); pan(g, Math.sin(t * 7) * 0.45).connect(duck);
    o.start(t); o.stop(t + 0.22); o2.start(t); o2.stop(t + 0.22);
  }
  function impact(t, size = 1) {
    const o = ctx.createOscillator(); o.type = 'sine';
    o.frequency.setValueAtTime(96, t); o.frequency.exponentialRampToValueAtTime(31, t + 0.55 * size);
    const g = ctx.createGain(); env(g, t, 0.004, 0.85 * size, 0.85 * size);
    o.connect(g).connect(fx); o.start(t); o.stop(t + 1.1 * size);
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.setValueAtTime(5200, t); lp.frequency.exponentialRampToValueAtTime(380, t + 0.4 * size);
    const ng = ctx.createGain(); env(ng, t, 0.002, 0.42 * size, 0.42 * size);
    noise(t, 0.6 * size).connect(lp).connect(ng).connect(fx);
    // вдох перед ударом
    const sw = ctx.createBiquadFilter(); sw.type = 'bandpass'; sw.Q.value = 1.4;
    sw.frequency.setValueAtTime(500, t - 0.28); sw.frequency.exponentialRampToValueAtTime(5200, t);
    const sg = ctx.createGain(); sg.gain.setValueAtTime(0.0001, t - 0.28); sg.gain.exponentialRampToValueAtTime(0.22 * size, t - 0.01); sg.gain.linearRampToValueAtTime(0.0001, t + 0.004);
    noise(t - 0.28, 0.3).connect(sw).connect(sg).connect(fx);
  }
  function uiClick(t, amp = 0.22) {
    const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.setValueAtTime(2300, t); o.frequency.exponentialRampToValueAtTime(1400, t + 0.02);
    const g = ctx.createGain(); env(g, t, 0.001, amp, 0.03);
    o.connect(g).connect(fx); o.start(t); o.stop(t + 0.05);
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 5000;
    const ng = ctx.createGain(); env(ng, t, 0.0005, amp * 0.6, 0.012);
    noise(t, 0.02).connect(hp).connect(ng).connect(fx);
  }
  function thud(t) {
    const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.setValueAtTime(150, t); o.frequency.exponentialRampToValueAtTime(70, t + 0.12);
    const g = ctx.createGain(); env(g, t, 0.003, 0.35, 0.18); o.connect(g).connect(fx); o.start(t); o.stop(t + 0.25);
  }
  function crash(t, amp = 0.2, dur = 1.6) {
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 4800;
    const g = ctx.createGain(); env(g, t, 0.003, amp, dur);
    pan(noise(t, dur + 0.1).connect(hp).connect(g), 0.15).connect(fx);
  }
  function riser(t0, t1) {
    const bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.Q.value = 2.2;
    bp.frequency.setValueAtTime(320, t0); bp.frequency.exponentialRampToValueAtTime(7200, t1);
    const g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.32, t1 - 0.02); g.gain.linearRampToValueAtTime(0.0001, t1);
    noise(t0, t1 - t0).connect(bp).connect(g).connect(fx);
    const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.setValueAtTime(190, t0); o.frequency.exponentialRampToValueAtTime(980, t1);
    const og = ctx.createGain(); og.gain.setValueAtTime(0.0001, t0); og.gain.exponentialRampToValueAtTime(0.09, t1 - 0.02); og.gain.linearRampToValueAtTime(0.0001, t1);
    o.connect(og).connect(fx); o.start(t0); o.stop(t1 + 0.02);
  }

  // ---------- аранжировка: фа минор, i–VI–III–VII
  const CH = { Fm: [174.61, 207.65, 261.63], Db: [138.59, 174.61, 207.65], Ab: [207.65, 261.63, 311.13], Eb: [155.56, 196.0, 233.08] };
  const RT = { Fm: 87.31, Db: 69.3, Ab: 103.83, Eb: 77.78 };
  const BAR = ['Fm', 'Db', 'Ab', 'Eb', 'Fm', 'Db', 'Ab', 'Fm'];
  const chordAt = (t) => BAR[Math.min(7, Math.floor(t / 2))];
  for (let n = 0; n < 30; n++) kick(n * BEAT, n === 20 ? 1.15 : n === 24 ? 1.05 : 0.95);   // бочка на каждую долю
  // хук
  stab(0.0, CH.Fm.map((f) => f * 2), 0.2, 0.38); stab(0.5, CH.Fm.map((f) => f * 2), 0.14, 0.3);
  clap(1.0, 0.55); crash(1.0, 0.07, 0.5); bass(0.0, 1.9, RT.Fm / 2, 0.38, 0.2);
  for (const t of [1.5, 1.625, 1.75, 1.875]) hat(t, 0.1, false, 0.3);
  // грув 2–10 с
  for (let t = 2.0; t < 10.0; t += 0.25) {
    const bar = Math.floor(t / 2), inBar = t - bar * 2;
    if (Math.abs(inBar % 0.5 - 0.25) < 1e-6) hat(t, inBar > 1.7 ? 0.11 : 0.12, inBar > 1.7, 0.25);
    if (t >= 8.0 && t < 9.0 && Math.abs(inBar % 0.5) < 1e-6) hat(t, 0.06, false, -0.25);
    const ch = chordAt(t), r = RT[ch];
    if (t < 9.875) bass(t, 0.2, [r, r, r * 2, r][Math.round(t * 4) % 4], 0.34, 0.5);
    if (Math.abs(inBar - 0.5) < 1e-6 || Math.abs(inBar - 1.5) < 1e-6) if (t < 9.0) clap(t, 0.45);
  }
  pad(4.0, 2.0, CH.Ab); pad(6.0, 2.0, CH.Eb); pad(8.0, 1.9, CH.Fm, 0.085);
  for (let t = 6.0; t < 9.9; t += 0.125) { const fs = CH[chordAt(t)], i = Math.round(t * 8) % 4; pluck(t, [fs[0] * 2, fs[1] * 2, fs[2] * 2, fs[1] * 4][i], t < 8 ? 0.07 : 0.09); }
  for (let t = 9.0; t < 9.875; t += t < 9.5 ? 0.125 : 0.0625) clap(t, 0.12 + 0.4 * (t - 9.0));          // дробь
  riser(8.0, 9.98);
  // смены сцен и дроп
  impact(2.0, 0.85); impact(5.0, 0.85); impact(7.5, 0.8); impact(12.0, 0.95);
  impact(10.0, 1.35); crash(10.0, 0.22, 1.9); stab(10.0, CH.Db.map((f) => f * 2), 0.22, 0.5);
  stab(2.0, CH.Db.map((f) => f * 2), 0.12); stab(5.0, CH.Ab.map((f) => f * 2), 0.12); stab(7.5, CH.Eb.map((f) => f * 2), 0.12);
  for (const t of [2.5, 3.0, 3.5, 6.0]) uiClick(t);
  thud(5.5);
  // дроп 10–12 с
  for (let t = 10.0; t < 12.0; t += 0.125) hat(t, Math.abs(t * 2 % 1 - 0.5) < 1e-6 ? 0.12 : 0.07, false, (Math.round(t * 8) % 2 ? 0.3 : -0.3));
  for (let t = 10.0; t < 12.0; t += 0.25) bass(t, 0.2, [RT.Db, RT.Db * 2, RT.Db, RT.Db * 1.5][Math.round(t * 4) % 4], 0.42, 1.2);
  for (const t of [10.5, 11.5]) clap(t, 0.5);
  for (const t of [10.75, 11.25, 11.75]) stab(t, CH.Db.map((f) => f * 2), 0.09, 0.2);
  pad(10.0, 2.0, CH.Db, 0.08);
  // финал 12–15 с
  for (let t = 12.0; t < 14.0; t += 0.5) hat(t + 0.25, 0.1, false, 0.25);
  for (const t of [12.5, 13.5]) clap(t, 0.4);
  for (let t = 12.0; t < 14.0; t += 0.5) bass(t, 0.4, RT.Ab, 0.32, 0.5);
  pad(12.0, 2.0, CH.Ab, 0.075);
  stab(13.0, CH.Ab.map((f) => f * 2), 0.1, 0.3);
  stab(14.0, CH.Fm.map((f) => f * 2), 0.2, 0.9); pad(14.0, 0.98, CH.Fm, 0.09); bass(14.0, 0.95, RT.Fm / 2, 0.36, 0.3);
  impact(14.5, 0.45);

  const buf = await ctx.startRendering();
  const L = buf.getChannelData(0), R = buf.getChannelData(1);
  let peak = 1e-9;
  for (let i = 0; i < L.length; i++) peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]));
  const g = 0.89 / peak;                                    // −1 dBFS
  const bytes = new Uint8Array(44 + L.length * 4), dv = new DataView(bytes.buffer);
  const wr = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  wr(0, 'RIFF'); dv.setUint32(4, 36 + L.length * 4, true); wr(8, 'WAVE'); wr(12, 'fmt '); dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true); dv.setUint16(22, 2, true); dv.setUint32(24, SR, true); dv.setUint32(28, SR * 4, true);
  dv.setUint16(32, 4, true); dv.setUint16(34, 16, true); wr(36, 'data'); dv.setUint32(40, L.length * 4, true);
  for (let i = 0, o = 44; i < L.length; i++, o += 4) {
    dv.setInt16(o, Math.round(clamp(L[i] * g, -1, 1) * 32767), true);
    dv.setInt16(o + 2, Math.round(clamp(R[i] * g, -1, 1) * 32767), true);
  }
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
};

init().then(() => window.seek(0));
