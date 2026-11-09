/* Ролик: время, плавности, камера, общие помощники отрисовки. */
'use strict';
// ====================================================================== время и музыка
const W = 1920, H = 1080, DUR = 15, BPM = 120, BEAT = 60 / BPM;
const SCN = [
  { id: 'hook', t0: 0.0,  t1: 2.0,  world: 'w0' },
  { id: 'move', t0: 2.0,  t1: 5.0,  world: 'w1' },
  { id: 'imp',  t0: 5.0,  t1: 7.5,  world: 'w2' },
  { id: 'rem',  t0: 7.5,  t1: 10.0, world: 'w2' },
  { id: 'pay',  t0: 10.0, t1: 12.0, world: 'w4' },
  { id: 'end',  t0: 12.0, t1: 15.0, world: 'w5' },
];
const HITS = [2.0, 5.0, 7.5, 12.0], DROP = 10.0;      // смены сцен и дроп
const CAP_CLICKS = [2.5, 3.5, 6.0];                     // нажатия на капсулу
const CUR_CLICKS = [2.5, 3.0, 3.5, 5.5, 6.0];           // нажатия курсора (5.5 — отпустил файл)

// ====================================================================== функции времени
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, u) => a + (b - a) * u;
const prog = (t, t0, d) => d <= 0 ? (t >= t0 ? 1 : 0) : clamp((t - t0) / d);
const eOutExpo = (u) => u >= 1 ? 1 : 1 - Math.pow(2, -10 * u);
const eInExpo = (u) => u <= 0 ? 0 : Math.pow(2, 10 * u - 10);
const eInOutExpo = (u) => u <= 0 ? 0 : u >= 1 ? 1 : u < .5 ? Math.pow(2, 20 * u - 10) / 2 : (2 - Math.pow(2, -20 * u + 10)) / 2;
const eInOutCubic = (u) => u < .5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
// экспоненциальная in-out с регулируемой крутизной k (k=10 — классическая; камера — k=4, пик скорости ≈3× средней)
const eInOutExpoK = (k) => (u) => {
  if (u <= 0) return 0; if (u >= 1) return 1;
  const n = 1 - Math.pow(2, -k);
  return u < .5 ? (Math.pow(2, k * (2 * u - 1)) - Math.pow(2, -k)) / (2 * n) : 1 - (Math.pow(2, k * (1 - 2 * u)) - Math.pow(2, -k)) / (2 * n);
};
const eCam = eInOutExpoK(4);
const eOutCubic = (u) => 1 - Math.pow(1 - u, 3);
const eInCubic = (u) => u * u * u;
// пружина: ζ = 0.78 → перелёт ≈ 2 %
function springU(x, dur = 0.45, zeta = 0.78) {
  if (x <= 0) return 0;
  const w = 4.6 / (zeta * dur), wd = w * Math.sqrt(1 - zeta * zeta), e = Math.exp(-zeta * w * x);
  return 1 - e * (Math.cos(wd * x) + (zeta * w / wd) * Math.sin(wd * x));
}
const spring = (t, t0, dur, zeta) => springU(t - t0, dur, zeta);
const springEase = (dur) => (u) => u >= 1 ? 1 : springU(u * dur * 1.5, dur);   // для сегментов длиной 1.5·dur
function pulse(t, t0, d = 0.2) { const u = (t - t0) / d; return u <= 0 || u >= 1 ? 0 : Math.pow(Math.sin(Math.PI * u), 2); }
const E = (id) => document.getElementById(id);
const fmt1 = (v) => v.toFixed(1).replace('.', ',');

// ====================================================================== камера
function sceneIndexAt(t) { for (let i = SCN.length - 1; i >= 0; i--) if (t >= SCN[i].t0) return i; return 0; }
// непрерывный наезд: +0.04 за сцену, каждая сцена начинается с зума, на котором кончилась прошлая
function push(t) { const i = sceneIndexAt(t), s = SCN[i]; return 1 + 0.04 * (i + clamp((t - s.t0) / (s.t1 - s.t0))); }
const CAM = {};
function camEval(keys, t) {
  let cur = { cx: keys[0].cx, cy: keys[0].cy, z: keys[0].z };
  for (let i = 1; i < keys.length; i++) {
    const k = keys[i];
    if (t <= k.t) break;
    const u = (k.ease || eCam)(clamp((t - k.t) / k.d));
    cur = { cx: lerp(cur.cx, k.cx, u), cy: lerp(cur.cy, k.cy, u), z: Math.exp(lerp(Math.log(cur.z), Math.log(k.z), u)) };
  }
  return cur;
}
function camOf(wid, t) { const c = camEval(CAM[wid], t); return { cx: c.cx, cy: c.cy, s: c.z * push(t), z: c.z }; }
const w2s = (c, x, y) => [(x - c.cx) * c.s + W / 2, (y - c.cy) * c.s + H / 2];
function rectW2S(c, r) { const [x, y] = w2s(c, r.x, r.y); return { x, y, w: r.w * c.s, h: r.h * c.s }; }
function fit(r, padX, padY, tAt, zMax = 2.6) {
  const p = push(tAt);
  const z = Math.min(W / (r.w + 2 * padX), H / (r.h + 2 * padY)) / p;
  return { cx: r.x + r.w / 2, cy: r.y + r.h / 2, z: Math.min(z, zMax) };
}
function U(...rs) {
  const x0 = Math.min(...rs.map((r) => r.x)), y0 = Math.min(...rs.map((r) => r.y));
  const x1 = Math.max(...rs.map((r) => r.x + r.w)), y1 = Math.max(...rs.map((r) => r.y + r.h));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
// камера, ставящая мировую точку (wx, wy) в экранную (sx, sy) при зуме z в момент t
function camPinned(wx, wy, sx, sy, z, t) { const s = z * push(t); return { cx: wx - (sx - W / 2) / s, cy: wy - (sy - H / 2) / s, z }; }

// ====================================================================== замеры (один раз, в покое)
const A = {};
function measure(name, el, wid) {
  if (typeof el === 'string') el = document.querySelector(el);
  const wr = E(wid).getBoundingClientRect(), r = el.getBoundingClientRect();
  A[name] = { x: r.left - wr.left, y: r.top - wr.top, w: r.width, h: r.height };
}
const rel = (a, b) => ({ x: a.x - b.x, y: a.y - b.y, w: a.w, h: a.h });     // a в координатах b
const ctr = (r) => [r.x + r.w / 2, r.y + r.h / 2];

// ====================================================================== примитивы движения
function maskY(t, t0, d, tOut, dOut, outDir) {
  return t < tOut ? 118 * (1 - eOutExpo(prog(t, t0, d))) : outDir * 118 * eInCubic(prog(t, tOut, dOut));
}
function dirBlur(el, axis, px) {           // px — смещение за интервал субкадра (локальные px)
  const n = Math.min(14, Math.round(px / 3));
  el.style.filter = n >= 1 ? `url(#mb${axis}${n})` : 'none';
}
function maskIn(el, t, t0, d = 0.36, tOut = Infinity, dOut = 0.24, outDir = -1) {
  const y = maskY(t, t0, d, tOut, dOut, outDir), yp = maskY(t - 1 / 180, t0, d, tOut, dOut, outDir);
  el.style.transform = `translateY(${y}%)`;
  dirBlur(el, 'v', Math.abs(y - yp) / 100 * (el.__h || (el.__h = el.offsetHeight)));
}
function swapAt(el, t, ts, d = 0.34) {          // старое уходит вверх, новое приходит снизу — со своим таймингом
  const [a, b] = el.children;
  a.style.transform = `translateY(${-125 * eInCubic(prog(t, ts, d * 0.45))}%)`;
  b.style.transform = `translateY(${125 * (1 - eOutExpo(prog(t, ts + d * 0.4, d * 0.6)))}%)`;
}
function clipFrom(el, ew, eh, src, u, rA, rB, fin = -70) {
  if (u >= 1 && fin <= -70) { el.style.clipPath = `inset(${fin}px round ${rB}px)`; return; }
  const top = lerp(src.y, fin, u), left = lerp(src.x, fin, u);
  const right = lerp(ew - (src.x + src.w), fin, u), bottom = lerp(eh - (src.y + src.h), fin, u);
  el.style.clipPath = `inset(${top}px ${right}px ${bottom}px ${left}px round ${lerp(rA, rB, u)}px)`;
}
function setBlur(gid, elStyle, sx, sy) {
  sx = Math.min(28, sx); sy = Math.min(28, sy);
  if (sx < 0.35 && sy < 0.35) { elStyle.filter = 'none'; return; }
  E(gid).setAttribute('stdDeviation', `${sx.toFixed(2)} ${sy.toFixed(2)}`);
  elStyle.filter = `url(#${gid.replace('-g', '')})`;
}

// ====================================================================== сквозной объект: синяя капсула
const LBL = {
  T: ['⇄ Переставить на другой борт', 14, 500], OK: ['Переставить', 14, 500], NOW: ['сейчас', 12, 600],
  GO: ['Импортировать', 14, 500], P485: ['+4,85', 14, 700],
};
let SEG = [], CUR = [];
function slot(wid, a, t, o = {}) {
  const c = camOf(wid, t), R = rectW2S(c, a);
  return { x: R.x, y: R.y, w: R.w, h: R.h, r: o.r === 'pill' ? R.h / 2 : (o.r || 0) * c.s, lbl: o.lbl || null, ls: c.s, op: o.op ?? 1, chev: 0 };
}
function mixS(a, b, u) {
  return { x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), w: lerp(a.w, b.w, u), h: lerp(a.h, b.h, u), r: lerp(a.r, b.r, u),
    op: lerp(a.op, b.op, u), chev: lerp(a.chev || 0, b.chev || 0, u) };
}
function capState(t) {
  let i = -1;
  for (let k = 0; k < SEG.length; k++) if (t >= SEG[k].ts) i = k;
  if (i < 0) return null;
  const sg = SEG[i], cur = sg.target(t);
  if (i > 0 && sg.d > 0 && t < sg.ts + sg.d) {
    const prev = SEG[i - 1].target(t), u = clamp(sg.ease(clamp((t - sg.ts) / sg.d)), -0.2, 1.2);
    return { ...mixS(prev, cur, u), A: prev, B: cur, u };
  }
  return { ...cur, A: cur, B: cur, u: 1 };
}
function drawLabel(el, key, scale, yPct) {
  const sp = el.firstChild;
  if (!key) { el.style.display = 'none'; return; }
  const [text, size, weight] = LBL[key];
  if (sp.textContent !== text) sp.textContent = text;
  el.style.display = 'block';
  el.style.fontSize = size + 'px'; el.style.fontWeight = weight;
  el.style.transform = `translate(-50%,-50%) scale(${scale})`;
  sp.style.transform = `translateY(${yPct}%)`;
}
function drawCapsule(t) {
  const cap = E('cap'), st = capState(t);
  if (!st || st.w < 0.6 || st.h < 0.6) { cap.style.display = 'none'; return; }
  let press = 0;
  for (const tc of CAP_CLICKS) press = Math.max(press, pulse(t, tc - 0.02, 0.22));
  const k = 1 - 0.055 * press;
  const x = st.x + st.w * (1 - k) / 2, y = st.y + st.h * (1 - k) / 2, w = st.w * k, h = st.h * k;
  Object.assign(cap.style, { display: 'block', left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px',
    borderRadius: Math.min(st.r * k, h / 2, w / 2) + 'px', opacity: st.op, background: press > 0.02 ? mixHex('#2f6fed', '#255fd6', press) : '#2f6fed' });
  // подписи: старая уходит вверх, новая приходит снизу (маска — сама капсула)
  const la = E('clA'), lb = E('clB');
  // дистанция ухода/прихода — до полного выхода за кромку капсулы (в % от высоты подписи)
  const away = (key, ls) => { if (!key) return 0; const lh = LBL[key][1] * 1.2 * ls * k; return 100 * (0.5 + h / (2 * lh)) + 10; };
  if (st.A.lbl === st.B.lbl || st.u >= 1) {
    drawLabel(la, null); drawLabel(lb, st.B.lbl, lerp(st.A.ls, st.B.ls, clamp(st.u)) * k, 0);
  } else {
    drawLabel(la, st.A.lbl, st.A.ls * k, -away(st.A.lbl, st.A.ls) * eInCubic(clamp(st.u / 0.45)));
    drawLabel(lb, st.B.lbl, st.B.ls * k, away(st.B.lbl, st.B.ls) * (1 - eOutExpo(clamp((st.u - 0.5) / 0.5))));
  }
  // шеврон логотипа
  const ch = E('chev');
  if (t >= 12.0) {
    const rise = spring(t, 12.5, 0.42), nudge = 1.6 * (spring(t, 14.5, 0.22) - spring(t, 14.62, 0.3));
    const dy = 24 * (1 - rise) - nudge;     // в единицах viewBox (радиус круга = 15)
    Object.assign(ch.style, { display: 'block', width: w + 'px', height: h + 'px' });
    E('chevP').setAttribute('transform', `translate(0 ${dy.toFixed(3)})`);
  } else ch.style.display = 'none';
  // смаз по скорости и направлению
  const p = capState(t - 1 / 120);
  if (p) setBlur('mb-cap-g', cap.style, Math.abs((st.x + st.w / 2) - (p.x + p.w / 2)) * 0.42, Math.abs((st.y + st.h / 2) - (p.y + p.h / 2)) * 0.42);
}
function mixHex(a, b, u) {
  const pa = [1, 3, 5].map((i) => parseInt(a.substr(i, 2), 16)), pb = [1, 3, 5].map((i) => parseInt(b.substr(i, 2), 16));
  return 'rgb(' + pa.map((v, i) => Math.round(lerp(v, pb[i], u))).join(',') + ')';
}

// ====================================================================== курсор и файл
function curPos(t) {
  let i = -1;
  for (let k = 0; k < CUR.length; k++) if (t >= CUR[k].ts) i = k;
  if (i < 0) return null;
  const sg = CUR[i], b = sg.target(t);
  if (i > 0 && sg.d > 0 && t < sg.ts + sg.d) {
    const a = CUR[i - 1].target(t), u = eInOutCubic(clamp((t - sg.ts) / sg.d));
    const dx = b[0] - a[0], dy = b[1] - a[1], arc = Math.sin(Math.PI * u) * 0.07;
    return [lerp(a[0], b[0], u) + dy * arc, lerp(a[1], b[1], u) - dx * arc];
  }
  return b;
}
function ptW(wid, a, fx, fy, t) { const c = camOf(wid, t); return w2s(c, a.x + a.w * fx, a.y + a.h * fy); }
function curScale(t) { const sc = SCN[sceneIndexAt(t)]; return clamp(camOf(sc.world, t).s, 1.3, 2.4); }
function drawCursor(t) {
  const el = E('cursor'), chip = E('chip');
  const p = (t >= 2.0 && t < 7.5) ? curPos(t) : null;
  if (!p || p[0] > W + 60 || p[1] > H + 60) { el.style.display = 'none'; }
  else {
    let dip = 0;
    for (const tc of CUR_CLICKS) dip = Math.max(dip, pulse(t, tc - 0.04, 0.2));
    const k = curScale(t) * (1 - 0.14 * dip);
    Object.assign(el.style, { display: 'block', left: (p[0] - 5 * k) + 'px', top: (p[1] - 2.4 * k) + 'px', width: 24 * k + 'px', height: 24 * k + 'px' });
    const q = curPos(t - 1 / 120);
    if (q) setBlur('mb-cur-g', el.style, Math.abs(p[0] - q[0]) * 0.4, Math.abs(p[1] - q[1]) * 0.4);
  }
  // файл журнала тянется курсором 5.0–5.5 и втягивается в зону загрузки
  if (t >= 5.0 && t < 5.66 && p) {
    const k = curScale(t);
    let x = p[0] + 16 * k, y = p[1] + 18 * k, sc = k * 0.82;
    if (t >= 5.5) {
      const u = eInOutCubic(prog(t, 5.5, 0.16)), f = ptW('w2', A.fname, 0.18, 0.5, t);
      x = lerp(x, f[0], u); y = lerp(y, f[1] - 10 * k, u); sc = sc * (1 - 0.82 * u);
    }
    Object.assign(chip.style, { display: 'flex', left: x + 'px', top: y + 'px', transform: `scale(${sc})` });
    const q = curPos(t - 1 / 120);
    if (q && t < 5.5) setBlur('mb-chip-g', chip.style, Math.min(9, Math.abs(p[0] - q[0]) * 0.16), Math.min(9, Math.abs(p[1] - q[1]) * 0.16));
    else chip.style.filter = 'none';
  } else chip.style.display = 'none';
}

// ====================================================================== сцены
