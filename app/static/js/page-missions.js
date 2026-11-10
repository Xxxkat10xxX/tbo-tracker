/* tbo-tracker · page-missions.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ Миссии: маршрут и автономный полёт
const R_EARTH = 6371000;
const toXY = (h, lat, lon) => [((lon - h.lon) * Math.PI / 180) * R_EARTH * Math.cos(h.lat * Math.PI / 180), ((lat - h.lat) * Math.PI / 180) * R_EARTH];
const toLL = (h, x, y) => [h.lat + (y / R_EARTH) * 180 / Math.PI, h.lon + (x / (R_EARTH * Math.cos(h.lat * Math.PI / 180))) * 180 / Math.PI];
const PHASE_RU = { takeoff: "Взлёт", mission: "Полёт по маршруту", hold: "Удержание на точке", rtl: "Возврат домой", land: "Посадка", crash: "Аварийное завершение", manual: "Ручное управление (пульт)" };
const runBadge = (r) => `<span class="badge ${RUN_CLS[r.status] || ""}">${esc(r.status_label || r.status)}</span>`;

const fmtDur = (s) => `${Math.floor(s / 60)} мин ${String(Math.round(s % 60)).padStart(2, "0")} с`;

async function pageMissions() {
  const list = await GET("/api/missions");
  view().innerHTML = `<div class="page-head"><div><h1>Миссии и автономный полёт</h1>
    <div class="sub">Маршрут из точек (WGS-84, высота над точкой взлёта) → автономный полёт в симуляторе автопилота → полёт в журнале, наработка, датчики. Обмен с QGroundControl, ArduPilot, PX4.</div></div>
    <div class="actions"><label class="btn m-0">⇪ Импорт .plan / .waypoints<input type="file" id="m-imp" accept=".plan,.waypoints,.txt,.json" hidden></label>
    <button class="primary" id="m-new">+ Новая миссия</button></div></div>
    <div class="card">${list.length ? `<div class="table-wrap"><table><thead><tr><th>Миссия</th><th>Борт</th><th class="num">Точек</th><th class="num">Маршрут</th><th class="num">Время (оценка)</th><th>Последний полёт</th></tr></thead><tbody>
      ${list.map((m) => `<tr><td><a href="#/missions/${m.id}"><b>${esc(m.name)}</b></a>${m.frame === "local" ? ' <span class="badge blue">локальная</span>' : ""}<div class="small muted">${esc(m.notes || "")}</div></td>
        <td>${m.aircraft_id ? `<a href="#/aircraft/${m.aircraft_id}">${esc(m.aircraft_name)}</a>` : '<span class="muted">не назначен</span>'}</td>
        <td class="num">${m.waypoints.length}</td><td class="num">${m.stats ? num(m.stats.distance_m / 1000, 2) + " км" : "—"}</td>
        <td class="num">${m.stats ? fmtDur(m.stats.duration_s) : "—"}</td>
        <td>${m.last_run ? `${runBadge(m.last_run)} <span class="small muted">${m.last_run.source === "drone" ? "борт · " : ""}${fmtDT(m.last_run.ended_at)}</span>` : '<span class="muted small">не выполнялась</span>'}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty-state">Миссий пока нет</div>'}</div>`;
  $("#m-new").onclick = async () => {
    const base = list[0] || { home_lat: 54.9512, home_lon: 38.2034 };
    const m = await POST("/api/missions", { name: "Новая миссия", home_lat: base.home_lat, home_lon: base.home_lon, takeoff_alt: 30, speed_ms: 8, return_home: true, waypoints: [] });
    location.hash = `#/missions/${m.id}`;
  };
  $("#m-imp").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("POST", "/api/missions/import", fd, true); toast(r.message); location.hash = `#/missions/${r.id}`; } catch (err) { toast(err.message, true); }
  };
}

async function pageMission(id) {
  const [m, fleet] = await Promise.all([GET(`/api/missions/${id}`), aircraftList(true)]);
  const st = { ...m, frame: m.frame || "global", yaw_mode: m.yaw_mode || "keep", waypoints: m.waypoints.map((w) => ({ ...w })) };
  const isLocal = () => st.frame === "local";
  const W = 760, H = 520;
  let view = null;         // {cx, cy, span} в метрах
  const home = () => ({ lat: +st.home_lat, lon: +st.home_lon });
  const fit = () => {
    const pts = [[0, 0]].concat(st.waypoints.map((w) => toXY(home(), w.lat, w.lon)));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const span = Math.max(isLocal() ? 12 : 300, (Math.max(...xs) - Math.min(...xs)) * 1.3, (Math.max(...ys) - Math.min(...ys)) * 1.3 * W / H);
    view = { cx, cy, span };
  };
  fit();
  const sx = (x) => (x - view.cx) / view.span * W + W / 2;
  const sy = (y) => H / 2 - (y - view.cy) / view.span * W;
  const wx = (px) => (px - W / 2) / W * view.span + view.cx;
  const wy = (py) => (H / 2 - py) / W * view.span + view.cy;

  view_().innerHTML = `<div class="crumbs"><a href="#/missions">Миссии</a> / ${esc(m.name)}</div>
    <div class="page-head"><div><h1>${esc(m.name)}</h1><div class="sub">Щелчок по карте — новая точка, перетаскивание — перенос. Щелчок по точке — выбрать (удалить — кнопкой «✕ Точка» или клавишей Delete), правый щелчок по точке — удалить. Координаты WGS-84, высоты относительно точки взлёта.</div></div>
    <div class="actions"><button id="m-save" class="primary">Сохранить</button><button id="m-exp-plan" data-exp>⇩ .plan (ArduPilot)</button><button id="m-exp-px4" data-exp>⇩ .plan (PX4)</button><button id="m-exp-wpl" data-exp>⇩ .waypoints</button><button class="danger" id="m-del">Удалить</button></div></div>
    <div class="mission-grid">
      <div><div class="card p-12"><div class="map-wrap"><svg id="map" viewBox="0 0 ${W} ${H}"></svg>
        <div class="map-tools"><button class="sm" id="z-in">+</button><button class="sm" id="z-out">−</button><button class="sm" id="z-fit">Вписать</button>
          <button class="sm danger" id="wp-del" hidden></button><button class="sm" id="wp-clear" hidden>Очистить маршрут</button></div>
        <div class="telemetry" id="telemetry" hidden></div></div>
        <div class="small muted mt-8" id="m-stats"></div></div>
        <div class="card" id="video-panel" hidden></div></div>
      <div>
        <div class="card" id="flight-panel"></div>
        <div class="card"><h2>Параметры миссии</h2>
          <div class="field"><label>Название</label><input name="name" value="${esc(st.name)}"></div>
          <div class="row2"><div class="field"><label>Борт</label><select name="aircraft_id"><option value="">— не назначен —</option>${fleet.map((a) => `<option value="${a.id}" ${a.id === st.aircraft_id ? "selected" : ""}>${esc(a.name)} · ${esc(a.serial)}</option>`).join("")}</select></div>
          <div class="field"><label>Крейсерская скорость, м/с</label><input type="number" min="0.2" max="40" step="0.1" name="speed_ms" value="${st.speed_ms}"></div></div>
          <div class="field"><label>Навигация</label><select name="frame">
            <option value="global" ${isLocal() ? "" : "selected"}>GPS: широта и долгота (полёт на улице)</option>
            <option value="local" ${isLocal() ? "selected" : ""}>Локальная: метры от точки взлёта (в помещении, Optical Flow / ArUco, без GPS)</option></select>
            <div class="hint" id="frame-hint"></div></div>
          <div class="row3"><div class="field ll-field"><label>Точка взлёта: широта</label><input type="number" step="0.000001" name="home_lat" value="${st.home_lat}"></div>
          <div class="field ll-field"><label>долгота</label><input type="number" step="0.000001" name="home_lon" value="${st.home_lon}"></div>
          <div class="field"><label>Высота взлёта, м</label><input type="number" min="0.5" max="500" step="0.1" name="takeoff_alt" value="${st.takeoff_alt}"></div></div>
          <div class="field"><label>Курс дрона (поворот вокруг своей оси)</label><select name="yaw_mode">
            <option value="keep" ${st.yaw_mode === "route" ? "" : "selected"}>Не менять — поворот только в точках с заданным курсом</option>
            <option value="route" ${st.yaw_mode === "route" ? "selected" : ""}>По линии маршрута — нос по направлению полёта</option></select>
            <div class="hint" id="yaw-hint"></div></div>
          <label><input type="checkbox" name="return_home" ${st.return_home ? "checked" : ""} class="w-auto"> <span id="rtl-label">Возврат в точку взлёта (RTL) и посадка</span></label></div>
        <div class="card"><div class="card-head"><h2>Точки маршрута</h2><span class="small muted" id="wp-count"></span></div><div id="wp-table"></div></div>
        <div class="card"><h2>Симулятор автопилота</h2>
          <div class="row3"><div class="field"><label>Ветер, м/с</label><input type="number" min="0" max="20" step="0.5" name="wind_ms" value="3"></div>
          <div class="field"><label>Ветер откуда, °</label><input type="number" min="0" max="360" name="wind_from" value="250"></div>
          <div class="field"><label>Отказ (проверка)</label><select name="fault"><option value="">без отказов</option></select></div></div>
          <button class="primary" id="m-run">▶ Выполнить автономный полёт</button>
          <div class="hint">Перед вылетом выполняется предполётная проверка допуска борта (комплектность, просроченные работы). Полёт выполняет симулятор автопилота; результат записывается в журнал и начисляет наработку.</div></div>
      </div></div>
    <div class="card" id="run-result" hidden></div>
    <div class="card"><h2>История выполнения</h2><div id="runs"></div></div>`;

  const map = $("#map");
  // курс: градусы по часовой стрелке от верха карты (SVG rotate — тоже по часовой); пусто — не задан
  const hasYaw = (w) => w.yaw_deg !== null && w.yaw_deg !== undefined && w.yaw_deg !== "";
  const yaw360 = (v) => ((+v % 360) + 360) % 360;
  function legMarks(list) {      // «по линии маршрута»: шевроны посередине отрезков — нос по направлению полёта
    let out = "";
    for (let k = 1; k < list.length; k++) {
      const ax = sx(list[k - 1][0]), ay = sy(list[k - 1][1]), bx = sx(list[k][0]), by = sy(list[k][1]);
      if (Math.hypot(bx - ax, by - ay) < 40) continue;
      out += `<path class="leg-dir" transform="translate(${(ax + bx) / 2},${(ay + by) / 2}) rotate(${Math.atan2(bx - ax, ay - by) * 180 / Math.PI})" d="M-6,4 L0,-4 L6,4"/>`;
    }
    return out;
  }
  function wpMark(p, i) {
    const w = st.waypoints[i], yaw = hasYaw(w) ? yaw360(w.yaw_deg) : null;
    const below = yaw !== null && (yaw < 60 || yaw > 300);          // стрелка вверх — подпись под точкой
    return `<g class="wp${i === selected ? " sel" : ""}" data-i="${i}" transform="translate(${sx(p[0])},${sy(p[1])})">
      ${yaw !== null ? `<path class="wp-yaw" transform="rotate(${yaw})" d="M0,-27 L6.5,-14 L-6.5,-14 Z"/>` : ""}<circle r="13"/><text y="4.5">${i + 1}</text><title>Точка ${i + 1}${yaw !== null ? `, курс ${num(yaw, 0)}°` : ""}: щелчок — выбрать, правый щелчок — удалить</title>
      <text class="wp-alt" y="${below ? 30 : -19}">${num(w.alt, 0)} м${+w.hold_s ? ` · ${w.hold_s} с` : ""}${yaw !== null ? ` · ${num(yaw, 0)}°` : ""}</text></g>`;
  }
  function drawMap(track, dronePos, heading) {
    const h = home();
    const step = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000].find((s) => view.span / s <= 12) || 5000;
    let g = "";
    const x0 = Math.floor(wx(0) / step) * step, x1 = wx(W), y0 = Math.floor(wy(H) / step) * step, y1 = wy(0);
    for (let x = x0; x <= x1; x += step) g += `<line x1="${sx(x)}" y1="0" x2="${sx(x)}" y2="${H}" class="grid"/>`;
    for (let y = y0; y <= y1; y += step) g += `<line x1="0" y1="${sy(y)}" x2="${W}" y2="${sy(y)}" class="grid"/>`;
    const pts = st.waypoints.map((w) => toXY(h, w.lat, w.lon));
    const path = [[0, 0]].concat(pts).map((p) => `${sx(p[0])},${sy(p[1])}`).join(" ");
    const back = pts.length && st.return_home ? `<line x1="${sx(pts[pts.length - 1][0])}" y1="${sy(pts[pts.length - 1][1])}" x2="${sx(0)}" y2="${sy(0)}" class="rtl"/>` : "";
    const legs = st.yaw_mode === "route" && pts.length ? legMarks([[0, 0]].concat(pts, st.return_home ? [[0, 0]] : [])) : "";
    let tr = "";
    if (track && track.length) tr = `<polyline class="flown" points="${track.map((p) => { const q = toXY(h, p[1], p[2]); return `${sx(q[0])},${sy(q[1])}`; }).join(" ")}"/>`;
    const sb = step;
    map.innerHTML = `<rect width="${W}" height="${H}" class="map-bg"/>${g}
      <polyline class="route" points="${path}"/>${back}${legs}${tr}
      <g class="home-g" transform="translate(${sx(0)},${sy(0)})"><rect x="-11" y="-11" width="22" height="22" rx="4" class="home"/><text y="5" class="home-t">${isLocal() ? "Т" : "H"}</text><title>Точка взлёта</title></g>
      ${pts.map(wpMark).join("")}
      ${dronePos ? `<g transform="translate(${dronePos[0]},${dronePos[1]})">${heading != null ? `<path class="drone-hdg" transform="rotate(${yaw360(heading)})" d="M0,-31 L8,-14 L-8,-14 Z"><title>Курс ${num(yaw360(heading), 0)}°</title></path>` : ""}<circle r="9" class="drone"/><circle r="17" class="drone-ring"/></g>` : ""}
      <g transform="translate(${W - 30},34)"><path d="M0,-16 L7,6 L0,1 L-7,6 Z" class="north"/><text y="22" class="north-t">${isLocal() ? "нос" : "С"}</text></g>
      <g transform="translate(16,${H - 18})"><line x1="0" y1="0" x2="${sb / view.span * W}" y2="0" class="scale"/><text x="0" y="-6" class="scale-t">${sb >= 1000 ? sb / 1000 + " км" : sb + " м"}</text></g>`;
    bindDrag();
    const del = $("#wp-del");
    del.hidden = selected === null;
    if (selected !== null) del.textContent = `✕ Точка ${selected + 1}`;
    $("#wp-clear").hidden = !st.waypoints.length;
  }
  // drag: нажатие на точку; перенос начинается, только если указатель сдвинулся (иначе это щелчок — выбор точки)
  // suppressUntil — щелчок, который браузер шлёт вслед за отпусканием точки, не должен ставить новую точку;
  // по времени, а не флагом: если щелчка не будет (касание со сдвигом), следующий настоящий щелчок не потеряется
  let drag = null, selected = null, suppressUntil = 0;
  const suppressClick = () => { suppressUntil = performance.now() + 400; };
  const svgPt = (e) => { const r = map.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H]; };
  function removeWp(i) {
    if (i === null || i < 0 || i >= st.waypoints.length) return;
    st.waypoints.splice(i, 1);
    if (selected === i) selected = null;
    else if (selected !== null && selected > i) selected -= 1;
    drawAll();
    toast(`Точка ${i + 1} удалена — сохраните миссию`);
  }
  let lastPointer = "mouse";
  function bindDrag() {
    $$(".wp", map).forEach((g) => {
      g.addEventListener("pointerdown", (e) => {
        lastPointer = e.pointerType || "mouse";
        if (e.button !== 0) return;
        e.stopPropagation();
        drag = { i: +g.dataset.i, x: e.clientX, y: e.clientY, moved: false };
        try { map.setPointerCapture(e.pointerId); } catch (_) { /* указатель уже отпущен */ }
      });
      g.addEventListener("contextmenu", (e) => {
        e.preventDefault(); e.stopPropagation();
        // на сенсорном экране «контекстное меню» — это долгое нажатие перед перетаскиванием: только выбрать точку
        if (lastPointer !== "mouse") {
          drag = null; suppressClick();             // отпускание пальца после долгого нажатия не снимает выбор
          selected = +g.dataset.i; drawMap(); drawTable();
          return;
        }
        removeWp(+g.dataset.i);
      });
    });
  }
  map.addEventListener("pointermove", (e) => {
    if (drag === null) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
    drag.moved = true;
    const [px, py] = svgPt(e); const [lat, lon] = toLL(home(), wx(px), wy(py));
    st.waypoints[drag.i].lat = +lat.toFixed(7); st.waypoints[drag.i].lon = +lon.toFixed(7);
    drawMap(); drawStats();
  });
  map.addEventListener("pointerup", () => {
    if (drag === null) return;
    const { i, moved } = drag;
    drag = null; suppressClick();
    if (moved) drawTable();
    else { selected = selected === i ? null : i; drawMap(); drawTable(); }
  });
  map.addEventListener("click", (e) => {
    if (performance.now() < suppressUntil) { suppressUntil = 0; return; }
    if (e.target.closest(".wp")) return;
    if (e.target.closest(".home-g")) { toast("Это точка взлёта. Новую точку ставьте щелчком по свободному месту карты"); return; }
    if (selected !== null) { selected = null; drawMap(); drawTable(); return; }   // первый щелчок мимо точки снимает выбор
    const [px, py] = svgPt(e); const [lat, lon] = toLL(home(), wx(px), wy(py));
    const alt = st.waypoints.length ? st.waypoints[st.waypoints.length - 1].alt : (isLocal() ? Math.min(+st.takeoff_alt, 1.5) : +st.takeoff_alt);
    st.waypoints.push({ lat: +lat.toFixed(7), lon: +lon.toFixed(7), alt, hold_s: 0 });
    drawAll();
  });
  function drawTable() {
    $("#wp-count").textContent = `${st.waypoints.length} точек`;
    const loc = isLocal();
    // локально: «вперёд» — север на карте (нос дрона при взлёте), «вправо» — восток; хранятся те же широта/долгота
    const xy = (w) => toXY(home(), w.lat, w.lon);
    $("#wp-table").innerHTML = st.waypoints.length ? `<table class="wp-table"><thead><tr><th>№</th>${loc ? "<th>Вперёд, м</th><th>Вправо, м</th>" : "<th>Широта</th><th>Долгота</th>"}<th>Высота, м</th><th>Удерж., с</th><th title="Курс в точке: градусы по часовой стрелке от верха карты (${loc ? "0° — вперёд по носу при взлёте, 90° — вправо" : "0° — север, 90° — восток"}). Пусто — не задан">Курс, °</th><th></th></tr></thead><tbody>
      ${st.waypoints.map((w, i) => `<tr${i === selected ? ' class="sel"' : ""}><td>${i + 1}</td>${loc
        ? `<td><input data-k="fwd" data-i="${i}" type="number" step="0.1" value="${xy(w)[1].toFixed(2)}"></td><td><input data-k="right" data-i="${i}" type="number" step="0.1" value="${xy(w)[0].toFixed(2)}"></td>`
        : `<td><input data-k="lat" data-i="${i}" type="number" step="0.000001" value="${w.lat}"></td><td><input data-k="lon" data-i="${i}" type="number" step="0.000001" value="${w.lon}"></td>`}
        <td><input data-k="alt" data-i="${i}" type="number" min="${loc ? 0.5 : 2}" max="${loc ? 10 : 500}" step="${loc ? 0.1 : 1}" value="${w.alt}"></td><td><input data-k="hold_s" data-i="${i}" type="number" min="0" max="600" value="${w.hold_s || 0}"></td>
        <td><input data-k="yaw_deg" data-i="${i}" type="number" min="0" max="359" step="1" placeholder="—" value="${hasYaw(w) ? w.yaw_deg : ""}"></td>
        <td><button class="sm danger" data-rm="${i}">✕</button></td></tr>`).join("")}</tbody></table>` : '<div class="empty-state">Щёлкните по карте, чтобы добавить первую точку</div>';
    $$("#wp-table input").forEach((inp) => inp.addEventListener("change", () => {
      const w = st.waypoints[+inp.dataset.i], k = inp.dataset.k;
      if (k === "fwd" || k === "right") {
        const [e, n] = xy(w);
        const [lat, lon] = toLL(home(), k === "right" ? +inp.value : e, k === "fwd" ? +inp.value : n);
        w.lat = +lat.toFixed(8); w.lon = +lon.toFixed(8);
      } else if (k === "yaw_deg") {                 // пусто — курс не задан; иначе 0–359,9° (−90 → 270, 360 → 0)
        const y = inp.value.trim() === "" || !Number.isFinite(+inp.value) ? null : +yaw360(+inp.value).toFixed(1);
        w.yaw_deg = y === null ? null : y % 360;
        inp.value = w.yaw_deg === null ? "" : w.yaw_deg;
      } else w[k] = +inp.value;
      drawMap(); drawStats();
    }));
    $$("#wp-table [data-rm]").forEach((b) => b.addEventListener("click", () => removeWp(+b.dataset.rm)));
    const f = $('[name="fault"]');
    f.innerHTML = `<option value="">без отказов</option>` + st.waypoints.map((_, i) => `<option value="${i + 1}">столкновение у точки ${i + 1}</option>`).join("");
  }
  // Длину маршрута и время считает сервер (autopilot.plan_stats) — тот же расчёт, что при выполнении миссии
  let statsTimer = null, statsSeq = 0;
  function drawStats() {
    if (!st.waypoints.length) { $("#m-stats").textContent = "Маршрут пуст"; return; }
    clearTimeout(statsTimer);
    statsTimer = setTimeout(async () => {
      const my = ++statsSeq;
      const s = await POST("/api/missions/estimate", { home_lat: +st.home_lat, home_lon: +st.home_lon, takeoff_alt: +st.takeoff_alt, frame: st.frame,
        speed_ms: +st.speed_ms, return_home: st.return_home, yaw_mode: st.yaw_mode,
        waypoints: st.waypoints.map((w) => ({ lat: +w.lat, lon: +w.lon, alt: +w.alt, hold_s: +w.hold_s || 0, yaw_deg: hasYaw(w) ? +w.yaw_deg : null })) })
        .catch(() => null);
      if (!s || my !== statsSeq || !$("#m-stats")) return;
      $("#m-stats").innerHTML = `Маршрут <b>${isLocal() ? num(s.distance_m, 1) + " м" : num(s.distance_m / 1000, 2) + " км"}</b> · оценка времени <b>${fmtDur(s.duration_s)}</b> · точек ${s.waypoints} · макс. высота ${num(s.max_alt_m, 0)} м`;
    }, 120);
  }
  function applyFrame() {
    const loc = isLocal();
    $$(".ll-field").forEach((el) => { el.hidden = loc; });
    $$("[data-exp]").forEach((el) => { el.hidden = loc; });
    // без GPS у PX4 нет RTL: в помещении к точке взлёта возвращает программа на борту
    $("#rtl-label").textContent = loc ? "Возврат к точке взлёта и посадка (программой на борту)" : "Возврат в точку взлёта (RTL) и посадка";
    $("#frame-hint").textContent = loc
      ? "Technic 6S в помещении: верх карты — вперёд по носу дрона в момент взлёта, вправо — вправо от дрона; высоты от точки взлёта до 3 м (дальномер VL53L1X — до 4 м), скорость до 2 м/с. Точки — в метрах в таблице или щелчком по карте."
      : "Точки по GPS: на борту нужен модуль GPS с компасом и GPS в навигации PX4 (параметры Technic по умолчанию — для полётов в помещении).";
    const zero = loc ? "0° — вперёд по носу дрона при взлёте, 90° — вправо" : "0° — север, 90° — восток";
    $("#yaw-hint").textContent = (st.yaw_mode === "route"
      ? "Перед каждым отрезком дрон поворачивается носом к следующей точке (при повороте больше 30° — сначала на месте), на отрезках вертикально вверх-вниз курс не меняется. "
      : "Дрон держит курс взлёта. ")
      + `Курс в точке (столбец «Курс, °» в таблице; ${zero}): прилетев, дрон развернётся на месте, затем удержание`
      + (st.yaw_mode === "route" ? ", а следующий отрезок — снова носом вперёд." : "; этот курс сохранится и дальше.");
  }
  function drawAll() { applyFrame(); drawMap(); drawTable(); drawStats(); }
  const readForm = () => {
    const r = view_();
    st.name = val(r, "name"); st.aircraft_id = val(r, "aircraft_id") ? +val(r, "aircraft_id") : null; st.speed_ms = +val(r, "speed_ms");
    st.home_lat = +val(r, "home_lat"); st.home_lon = +val(r, "home_lon"); st.takeoff_alt = +val(r, "takeoff_alt");
    st.return_home = $('[name="return_home"]').checked;
    st.frame = val(r, "frame") || "global";
    st.yaw_mode = val(r, "yaw_mode") || "keep";
  };
  ["speed_ms", "home_lat", "home_lon", "takeoff_alt", "return_home", "yaw_mode"].forEach((n) => $(`[name="${n}"]`).addEventListener("change", () => { readForm(); drawAll(); }));
  $('[name="frame"]').addEventListener("change", () => {
    readForm();
    if (isLocal() && +st.takeoff_alt > 3) { st.takeoff_alt = 1.5; $('[name="takeoff_alt"]').value = 1.5; }
    if (isLocal() && +st.speed_ms > 2) { st.speed_ms = 0.8; $('[name="speed_ms"]').value = 0.8; }
    fit(); drawAll();
    if (isLocal() && st.waypoints.some((w) => +w.alt > 3)) toast("Проверьте высоты точек: для локальной навигации — до 3 м", true);
  });
  async function save() {
    readForm();
    const body = { name: st.name, aircraft_id: st.aircraft_id, home_lat: st.home_lat, home_lon: st.home_lon, takeoff_alt: st.takeoff_alt,
      speed_ms: st.speed_ms, return_home: st.return_home, frame: st.frame, yaw_mode: st.yaw_mode, waypoints: st.waypoints, notes: st.notes };
    await api("PUT", `/api/missions/${id}`, body);
  }
  $("#m-save").onclick = async () => { try { await save(); toast("Миссия сохранена"); } catch (e) { toast(e.message, true); } };
  const dl = (fmt, fw) => async () => { try { await save(); location.href = `/api/missions/${id}/export?fmt=${fmt}&firmware=${fw || "ardupilot"}`; } catch (e) { toast(e.message, true); } };
  $("#m-exp-plan").onclick = dl("plan", "ardupilot"); $("#m-exp-px4").onclick = dl("plan", "px4"); $("#m-exp-wpl").onclick = dl("waypoints");
  $("#m-del").onclick = () => modal("Удаление миссии", "<p>Удалить миссию? Выполненные полёты останутся в журнале.</p>", { okText: "Удалить", onOk: async () => { await api("DELETE", `/api/missions/${id}`); location.hash = "#/missions"; } });
  $("#z-in").onclick = () => { view.span /= 1.5; drawMap(); };
  $("#z-out").onclick = () => { view.span *= 1.5; drawMap(); };
  $("#z-fit").onclick = () => { fit(); drawMap(); };
  $("#wp-del").onclick = () => removeWp(selected);
  $("#wp-clear").onclick = () => modal("Очистить маршрут", `<p>Удалить все точки маршрута (${st.waypoints.length})? Изменения вступят в силу после «Сохранить».</p>`,
    { okText: "Очистить", onOk: async () => { st.waypoints = []; selected = null; drawAll(); } });
  const onKey = (e) => {
    if (!document.body.contains(map)) { document.removeEventListener("keydown", onKey); return; }   // ушли со страницы
    const t = e.target instanceof Element ? e.target : null;       // клавиша может прийти и на сам document
    if (selected === null || !["Delete", "Backspace"].includes(e.key) || (t && t.closest("input, select, textarea, [contenteditable]")) || $(".modal-bg")) return;
    e.preventDefault();
    removeWp(selected);
  };
  document.addEventListener("keydown", onKey);

  function drawRuns(runs) {
    $("#runs").innerHTML = runs.length ? `<div class="table-wrap"><table><thead><tr><th>Окончание</th><th>Борт</th><th>Где</th><th>Итог</th><th class="num">Длительность</th><th class="num">Путь</th><th class="num">Макс. высота</th><th>Журнал</th></tr></thead><tbody>
      ${runs.map((r) => `<tr><td class="nowrap">${fmtDT(r.ended_at)}</td><td><a href="#/aircraft/${r.aircraft_id}">${esc(r.summary.aircraft_name || "")}</a></td>
        <td><span class="badge ${r.source === "drone" ? "blue" : ""}">${r.source === "drone" ? "Борт" : "Симулятор"}</span></td>
        <td>${runBadge(r)}${r.summary.reason || r.summary.error ? `<div class="small muted">${esc(r.summary.reason || r.summary.error)}</div>` : ""}</td>
        <td class="num">${r.summary.duration_s ? fmtDur(r.summary.duration_s) : "—"}</td><td class="num">${r.summary.distance_m ? num(r.summary.distance_m / 1000, 2) + " км" : "—"}</td>
        <td class="num">${r.summary.max_alt_m != null ? num(r.summary.max_alt_m, 0) + " м" : "—"}</td>
        <td class="small">${r.import_id ? `импорт №${r.import_id}` : "—"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty-state">Миссия ещё не выполнялась</div>';
  }
  drawRuns(m.runs);

  $("#m-run").onclick = async () => {
    const btn = $("#m-run"); btn.disabled = true;
    try {
      await save();
      const r = await POST(`/api/missions/${id}/run`, { aircraft_id: st.aircraft_id, wind_ms: +val(view_(), "wind_ms"), wind_from_deg: +val(view_(), "wind_from"),
        fault_at_wp: val(view_(), "fault") ? +val(view_(), "fault") : null });
      await playTrack(r);
      showResult(r);
      drawRuns((await GET(`/api/missions/${id}`)).runs);
      refreshNavCount(); AIRCRAFT_CACHE = null;
    } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  };
  function playTrack(r) {
    const tr = r.track, h = home(), tel = $("#telemetry");
    tel.hidden = false;
    const total = tr[tr.length - 1][0], ms = Math.min(9000, Math.max(3500, total * 25));
    return new Promise((resolve) => {
      const t0 = performance.now();
      const frame = (now) => {
        const k = Math.min(1, (now - t0) / ms), tt = k * total;
        let i = tr.findIndex((p) => p[0] >= tt); if (i < 0) i = tr.length - 1;
        const p = tr[i], q = toXY(h, p[1], p[2]);
        drawMap(tr.slice(0, i + 1), [sx(q[0]), sy(q[1])]);
        tel.innerHTML = `<b>${PHASE_RU[p[5]] || p[5]}</b><span>t = ${fmtDur(p[0])}</span><span>высота ${num(p[3], 1)} м</span><span>скорость ${num(p[4], 1)} м/с</span>`;
        if (k < 1) requestAnimationFrame(frame); else resolve();
      };
      requestAnimationFrame(frame);
    });
  }
  function showResult(r) {
    const s = r.summary, el = $("#run-result");
    el.hidden = false;
    el.innerHTML = `<h2>Результат автономного полёта</h2>
      <div class="notice ${r.status === "completed" ? "ok" : "bad"}"><b>${r.status === "completed" ? "Миссия выполнена" : "Миссия прервана"}</b> — ${esc(s.aircraft_name)}:
        ${fmtDur(s.duration_s)}, путь ${num(s.distance_m / 1000, 2)} км, макс. высота ${num(s.max_alt_m, 0)} м, макс. боковое отклонение ${num(s.max_xte_m, 1)} м (ветер ${s.wind.speed_ms} м/с с ${s.wind.from_deg}°).
        ${s.events.length ? `<ul>${s.events.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>
      <p>Полёт записан в журнал (импорт №${r.import_id}): начислено <b>${hrs(s.hours_added)}</b> борту и ${s.components} узлам.
        <a href="#/aircraft/${r.aircraft_id}">Открыть борт →</a></p>
      ${importResultHTML(r.import).split("<h3").slice(1).filter((x) => /Начисление узлам|Мониторинг|Сработавшие/.test(x)).map((x) => "<h3" + x.replace(/<\/div>\s*$/, "")).join("")}`;
    el.scrollIntoView({ behavior: "smooth" });
  }
  drawAll();
  const video = droneVideoPanel($("#video-panel"));
  flightPanel($("#flight-panel"), {
    onLink: (d) => video.update(d),
    missionId: +id, aircraftId: () => st.aircraft_id, waypoints: () => st.waypoints, returnHome: () => st.return_home, save,
    frame: () => st.frame,
    drawLive: (track, ll, heading) => { const q = toXY(home(), ll[0], ll[1]); drawMap(track, [sx(q[0]), sy(q[1])], heading); },
    onRunsChanged: (runs) => { drawRuns(runs); refreshNavCount(); AIRCRAFT_CACHE = null; },
  });
}
const view_ = () => $("#view");
