/* tbo-tracker · page-flight.js — полёт миссии на реальном борту (агент tbo-agent на бортовом компьютере).
   Общая область видимости классических скриптов (см. index.html). Состояние и команды — на сервере (/api/drones). */
"use strict";

const DRONE_CLS = { offline: "", idle: "blue", preparing: "warn", ready: "ok", starting: "warn", in_flight: "ok", returning: "warn",
  landing: "warn", landed: "blue", uploading: "blue", error: "bad" };
const RUN_CLS = { preparing: "warn", ready: "ok", in_flight: "ok", landed: "blue", completed: "ok", aborted: "bad", failed: "bad", cancelled: "" };
const DRONE_FLYING = ["starting", "in_flight", "returning", "landing"];
// начисленная наработка = длительность полёта: до часа — в минутах и секундах (часы в итоге импорта округлены до 0,01)
const flightTime = (s) => (s.duration_s && s.duration_s < 3600 ? fmtDur(s.duration_s) : hrs(s.hours_added || 0));

function droneTelemetryHTML(d) {
  const t = d.telemetry || {};
  const cell = (label, value) => `<div class="tl"><span>${label}</span><b>${value}</b></div>`;
  const bat = t.battery_pct != null ? `<span class="bat"><span class="${t.battery_pct < 30 ? "bad" : t.battery_pct < 50 ? "warn" : ""}" data-w="${t.battery_pct}"></span></span> ${num(t.battery_pct, 0)}%` : (t.battery_v != null ? `${num(t.battery_v, 1)} В` : "—");
  return `<div class="tl-grid">
    ${cell("Режим", esc(t.mode || "—"))}${cell("Моторы", t.armed ? '<span class="c-bad">включены</span>' : "выключены")}
    ${cell("Высота", t.alt_rel != null ? `${num(t.alt_rel, 1)} м` : "—")}${cell("Скорость", t.speed != null ? `${num(t.speed, 1)} м/с` : "—")}
    ${cell("Курс", t.heading != null ? `${num(((t.heading % 360) + 360) % 360, 0)}°` : "—")}
    ${cell("Батарея", bat)}${cell("GPS", t.sats != null || t.gps_fix != null ? `${t.gps_fix >= 3 ? "3D" : "нет фикс."} · ${t.sats ?? "—"} спутн.` : "—")}
    ${cell("До точки взлёта", t.home_dist_m != null ? `${num(t.home_dist_m, 0)} м` : "—")}${cell("Точка маршрута", t.wp_total ? `${t.wp_current || 0} из ${t.wp_total}` : "—")}
    ${cell("Время полёта", t.flight_time_s != null ? fmtDur(t.flight_time_s) : "—")}${cell("Обмен", d.telemetry_age_s != null ? `${num(d.telemetry_age_s, 0)} с назад` : "—")}
  </div>${t.status_text ? `<div class="small muted mt-8">PX4: ${esc(t.status_text)}</div>` : ""}`;
}

function droneChecksHTML(checks) {
  if (!checks || !checks.length) return "";
  return `<ul class="checks">${checks.map((c) => `<li class="${c.warn ? "warn" : c.ok ? "ok" : "bad"}"><b>${c.warn ? "⚠" : c.ok ? "✓" : "✕"} ${esc(c.name)}</b>${c.detail ? ` <span class="muted">— ${esc(c.detail)}</span>` : ""}</li>`).join("")}</ul>`;
}

function droneRunResultHTML(r) {
  const s = r.summary || {};
  const ok = r.status === "completed";
  return `<div class="notice ${ok ? "ok" : "bad"} mt-8"><b>Полёт R${r.id}: ${esc(r.status_label)}</b>${s.reason ? ` — ${esc(s.reason)}` : s.error ? ` — ${esc(s.error)}` : ""}
    ${s.duration_s ? `<br>Длительность ${fmtDur(s.duration_s)}, путь ${num((s.distance_m || 0) / 1000, 2)} км, макс. высота ${num(s.max_alt_m || 0, 0)} м, точек ${s.wp_reached ?? "—"} из ${s.wp_total ?? "—"}.` : ""}
    ${r.import_id ? `<br>Журнал импортирован автоматически (импорт №${r.import_id}): начислено <b>${flightTime(s)}</b> наработки${s.components ? ` борту и ${s.components} узлам` : ""}${(s.sensor_events || []).length ? `; события датчиков: ${s.sensor_events.length}` : ""}. <a href="#/aircraft/${r.aircraft_id}">Открыть борт →</a>
      <br><a href="/api/runs/${r.id}/report?fmt=md" download>⇩ Отчёт по полёту R${r.id}</a> · <a href="/api/runs/${r.id}/flightlog" download>⇩ Журнал полёта (для повторной загрузки)</a>` : ""}</div>`;
}

// Видео с борта (web_video_server на борту → сервер → страница). Включается кнопкой: поток ~2 Мбит/с и нагрузка
// на бортовой компьютер. Элемент <img> пересоздаётся только при смене состояния: иначе поток переподключался бы.
function droneVideoPanel(root) {
  let lid = null, name = "", online = false, playing = false, stream = "main", streams = [], note = "", shownKey = "";
  let enabled = true, maxMin = 30, startedAt = 0, starting = false;
  const alive = () => document.body.contains(root);
  api("GET", "/api/drones/video/streams", undefined, false, true).then((s) => {
    streams = s.streams || []; maxMin = s.max_minutes || 30; enabled = s.enabled !== false; render(true);
  }).catch(() => {});

  function stop(msg) { playing = false; note = msg || ""; render(true); }
  // Кадры по одному: следующий — только после показа предыдущего. Прежний поток MJPEG при медленном Wi-Fi копил
  // кадры в буферах сети, и картинка отставала на десятки секунд; так отставание не больше одного кадра.
  let gen = 0;
  async function pump(my, img, statsEl) {
    let after = 0, prevUrl = null, shown = 0, since = performance.now(), lag = 0, boardLag = null;
    const live = () => my === gen && playing && alive();
    while (live()) {
      const t0 = performance.now();
      let r;
      try {
        r = await fetch(`/api/drones/${lid}/video/frame?stream=${encodeURIComponent(stream)}&after=${after}`,
          { cache: "no-store", credentials: "same-origin" });
      } catch (_) { if (live()) stop("Нет связи с сервером — видео остановлено."); break; }
      if (!live()) break;
      if (r.status === 204) continue;                     // нового кадра пока нет — сервер ждал его несколько секунд
      if (!r.ok) {
        let msg = r.status === 401 ? "Сеанс завершён — войдите снова" : "Поток с борта прервался";
        try { msg = (await r.json()).detail || msg; } catch (_) { /* не JSON */ }
        stop(`${msg}. Нажмите «Показать видео», чтобы подключиться снова.`);
        break;
      }
      after = +r.headers.get("X-Frame-Seq") || after;
      const age = +r.headers.get("X-Frame-Age-Ms") || 0;
      const lagHdr = r.headers.get("X-Frame-Lag-Ms"), reconnects = +r.headers.get("X-Feed-Reconnects") || 0;
      if (lagHdr !== null) boardLag = boardLag === null ? +lagHdr : boardLag * 0.8 + +lagHdr * 0.2;
      const url = URL.createObjectURL(await r.blob());
      await new Promise((res) => { img.onload = res; img.onerror = res; img.src = url; });
      if (prevUrl) URL.revokeObjectURL(prevUrl);
      prevUrl = url; shown += 1;
      lag = lag ? lag * 0.8 + (age + performance.now() - t0) * 0.2 : age + performance.now() - t0;
      const dt = performance.now() - since;
      if (dt >= 1000 && statsEl) {
        // отставание от борта — очередь кадров на борту и в Wi-Fi борт → сервер (по меткам web_video_server)
        statsEl.textContent = `${num(shown * 1000 / dt, 1)} кадр/с`
          + (boardLag !== null ? ` · отставание от борта ≈ ${num(boardLag, 0)} мс` : "")
          + ` · сервер и сеть ≈ ${num(lag, 0)} мс`
          + (reconnects ? ` · канал с бортом пересоздан ${reconnects} раз (слабый Wi-Fi)` : "");
        shown = 0; since = performance.now();
      }
    }
    if (prevUrl) URL.revokeObjectURL(prevUrl);
  }
  async function start() {
    if (starting) return;
    const want = `${lid}|${stream}`;                     // за время проверки борт или поток могли смениться
    starting = true; note = "Подключение к камере борта…"; render(true);
    try {
      await api("GET", `/api/drones/${lid}/video?stream=${encodeURIComponent(stream)}&probe=1`, undefined, false, true);
      if (want === `${lid}|${stream}` && online) { playing = true; note = ""; startedAt = Date.now(); } else note = "";
    } catch (e) { note = e.message; } finally { starting = false; }
    render(true);
  }
  function render(force) {
    if (!alive()) return;
    root.hidden = lid === null || !enabled;
    const key = [lid, online, playing, stream, note, streams.length, starting].join("|");
    if (!force && key === shownKey) return;              // без изменений — не трогаем идущий поток
    shownKey = key;
    const opts = streams.map((s) => `<option value="${esc(s.id)}" ${s.id === stream ? "selected" : ""}>${esc(s.label)}</option>`).join("");
    gen += 1;                                              // прежний цикл кадров завершится сам
    root.innerHTML = `<div class="card-head"><h2>Видео с борта</h2><span class="small muted">${esc(name)}</span></div>
      <div class="video-ctl">${opts ? `<select data-vs class="w-auto" ${playing ? "disabled" : ""}>${opts}</select>` : ""}
        ${playing ? '<button data-vstop>■ Остановить</button>'
          : `<button class="primary" data-vplay ${online && !starting ? "" : "disabled"}>▶ Показать видео</button>`}</div>
      ${!online ? '<div class="notice warn mt-8">Борт не на связи — видео недоступно.</div>' : ""}
      ${note ? `<div class="notice ${/Подключение/.test(note) ? "" : "bad"} mt-8">${esc(note)}</div>` : ""}
      ${playing ? `<div class="video-wrap mt-8"><img data-vimg alt="Видео с борта"></div><div class="small muted mt-8" data-vstat>Ожидание кадра…</div>` : ""}
      <div class="hint">Камера бортового компьютера через сервер (web_video_server, порт 8080), 320×240. Кадры идут по одному: при медленной сети реже, но без отставания. Один просмотр — до ${maxMin} мин.</div>`;
    const sel = $("[data-vs]", root);
    if (sel) sel.onchange = () => { stream = sel.value; render(true); };
    const play = $("[data-vplay]", root);
    if (play) play.onclick = start;
    const st = $("[data-vstop]", root);
    if (st) st.onclick = () => stop("");
    const img = $("[data-vimg]", root);
    if (img) pump(gen, img, $("[data-vstat]", root));
  }
  return {
    update(d) {                                           // состояние борта из панели полёта (раз в 1,5 с)
      const newLid = d ? d.id : null;
      if (newLid !== lid) { lid = newLid; playing = false; note = ""; }
      name = d ? `${d.aircraft.name} · ${d.aircraft.serial}` : "";
      online = !!(d && d.online && d.enabled);
      if (playing && !online) { playing = false; note = "Борт пропал со связи — видео остановлено."; }
      // сервер закрывает просмотр через maxMin минут — браузер при этом «замирает» на последнем кадре
      if (playing && Date.now() - startedAt > maxMin * 60000) { playing = false; note = `Просмотр длится ${maxMin} мин — видео остановлено. Нажмите «Показать видео», чтобы продолжить.`; }
      if (playing && document.hidden) { playing = false; note = "Видео остановлено, пока вкладка была скрыта."; }
      render(false);
    },
  };
}

async function flightPanel(root, ctx) {
  const my = NAV_SEQ;
  const alive = () => my === NAV_SEQ && document.body.contains(root);
  let links;
  try { links = await api("GET", "/api/drones", undefined, false, true); } catch (e) { root.innerHTML = `<h2>Полёт на борту</h2><div class="notice bad">${esc(e.message)}</div>`; return; }
  if (!alive()) return;
  if (!links.length) {
    root.innerHTML = `<h2>Полёт на борту</h2><div class="empty-state">Нет подключённых бортов. ${can("admin") ? '<a href="#/drones">Подключить борт →</a>' : "Подключает администратор: раздел «Борты на связи»."}</div>`;
    return;
  }
  let lid = (links.find((l) => l.aircraft.id === ctx.aircraftId()) || links[0]).id;
  let d = null, track = [], watch = null, result = null, timer = null;

  async function refresh() {
    if (!alive()) { clearInterval(timer); return; }
    try { d = await api("GET", `/api/drones/${lid}`, undefined, false, true); } catch (e) {
      if (e instanceof AuthLost) clearInterval(timer);
      return;
    }
    if (!alive()) return;
    if (ctx.onLink) ctx.onLink(d);
    const t = d.telemetry || {}, run = d.run;
    if (run && run.mission_id === ctx.missionId && ["preparing", "ready", "in_flight", "landed"].includes(run.status)) watch = run.id;
    if (watch && (!run || run.id !== watch)) {                        // полёт завершён: итог из истории миссии
      const m = await api("GET", `/api/missions/${ctx.missionId}`, undefined, false, true).catch(() => null);
      result = m && m.runs.find((x) => x.id === watch);
      watch = null; track = [];
      if (m && ctx.onRunsChanged) ctx.onRunsChanged(m.runs);
    }
    if (run && run.status === "in_flight" && t.lat != null) {
      const last = track[track.length - 1];
      if (!last || last[1] !== t.lat || last[2] !== t.lon) track.push([track.length, t.lat, t.lon, t.alt_rel || 0, t.speed || 0, "mission"]);
    }
    render();
  }

  function actionsHTML() {
    const run = d.run, mine = run && run.mission_id === ctx.missionId, eng = can("engineer");
    if (!d.enabled) return '<div class="notice warn">Подключение борта отключено администратором</div>';
    if (!d.online) {
      const lost = eng && mine && ["in_flight", "landed"].includes(run.status);
      return `<div class="notice warn">Борт не на связи${d.last_seen_at ? ` с ${fmtDT(d.last_seen_at)}` : ""} — проверьте питание, Wi-Fi и службу tbo-agent на борту.</div>
        ${eng && DRONE_FLYING.includes(d.last_state) ? '<button class="danger-solid" data-fl="rtl">⌂ Вернуть домой</button> <span class="small muted">команда будет доставлена при восстановлении связи (до 2 мин)</span>' : ""}
        ${lost ? '<button data-fl="close">Закрыть полёт без журнала</button> <span class="small muted">если связи нет дольше 10 мин</span>' : ""}`;
    }
    if (run && !mine && ["preparing", "ready", "in_flight", "landed"].includes(run.status)) return `<div class="notice">Борт занят миссией «${esc(run.mission_name || "")}» (${esc(run.status_label)}).</div>`;
    if (!eng) return '<div class="small muted">Команды борту отдают инженер и администратор.</div>';
    if (["idle", "error", "ready"].includes(d.state) && mine && run.status === "in_flight")
      return `<div class="notice warn">Борт на связи, но полёт не ведёт (${esc(d.state_label)}) — журнал не поступит.</div>
        <button data-fl="close">Закрыть полёт без журнала</button>`;
    if (["idle", "error"].includes(d.state)) {
      const local = ctx.frame && ctx.frame() === "local";
      return `<button class="primary" data-fl="prepare">⇪ Отправить план на борт</button>
        <div class="hint">${local
          ? "Локальная миссия: борт проверит связь с контроллером, локальную позицию (Optical Flow / ArUco), simple_offboard и заряд. Поставьте дрон в точку взлёта носом в направлении «вверх» карты. «Вернуть домой» — полёт к точке взлёта и посадка программой на борту."
          : "Миссия будет сохранена и загружена в полётный контроллер; борт проверит связь с контроллером, GPS, заряд и положение относительно точки взлёта."} Взлёт — только после кнопки «Старт».</div>`;
    }
    if (d.state === "preparing") return '<div class="notice">Загрузка плана в контроллер и проверки на борту…</div>';
    if (d.state === "ready" && mine && run.status === "ready") return '<button class="go" data-fl="start">▶ Старт</button> <button data-fl="cancel">Отменить подготовку</button>';
    if (DRONE_FLYING.includes(d.state)) return '<button class="danger-solid" data-fl="rtl">⌂ Вернуть домой</button> <button class="danger" data-fl="land">⤓ Посадить на месте</button>';
    if (["landed", "uploading"].includes(d.state)) return '<div class="notice">Посадка выполнена — журнал полёта передаётся на сервер и будет импортирован автоматически.</div>';
    return "";
  }

  function render() {
    const run = d.run, mine = run && run.mission_id === ctx.missionId;
    const sel = links.length > 1 ? `<select data-link class="w-auto">${links.map((l) => `<option value="${l.id}" ${l.id === lid ? "selected" : ""}>${esc(l.aircraft.name)} · ${esc(l.aircraft.serial)}</option>`).join("")}</select>` : `<span class="small muted">${esc(d.aircraft.name)} · ${esc(d.aircraft.serial)}</span>`;
    root.innerHTML = `<div class="card-head"><h2>Полёт на борту</h2>${sel}</div>
      <div class="fl-status"><span class="dot ${d.online ? "" : "gray"}"></span><span class="badge ${DRONE_CLS[d.state] || ""}">${esc(d.state_label)}</span>
        ${mine ? `<span class="badge ${RUN_CLS[run.status] || ""}">Полёт R${run.id}: ${esc(run.status_label)}</span>` : ""}
        <span class="small muted">${esc(d.detail || "")}</span></div>
      ${d.online || d.telemetry_age_s != null ? droneTelemetryHTML(d) : ""}
      ${["preparing", "ready", "idle", "error"].includes(d.state) ? droneChecksHTML(d.checks) : ""}
      ${mine && run.summary && run.summary.last_error ? `<div class="notice bad">Старт не выполнен: ${esc(run.summary.last_error)}</div>` : ""}
      <div class="fl-actions">${actionsHTML()}</div>
      ${result ? droneRunResultHTML(result) : ""}
      <div class="hint">Пульт главнее автопилота: переключение режима на пульте забирает управление. Летайте в пределах прямой видимости и по правилам использования воздушного пространства.</div>`;
    const s = $("[data-link]", root);
    if (s) s.onchange = () => { lid = +s.value; track = []; watch = null; result = null; refresh(); };
    $$("[data-fl]", root).forEach((b) => b.addEventListener("click", () => act(b.dataset.fl)));
    const t = d.telemetry || {};
    if (t.lat != null && t.lon != null && (mine || DRONE_FLYING.includes(d.state))) ctx.drawLive(track, [t.lat, t.lon], t.heading);
  }

  async function act(kind) {
    try {
      if (kind === "prepare") {
        await ctx.save();
        result = null;
        toast((await POST(`/api/drones/${lid}/prepare`, { mission_id: ctx.missionId })).message);
      } else if (kind === "start") {
        const n = (ctx.waypoints() || []).length;
        modal("Старт миссии на борту", `<p>Борт <b>${esc(d.aircraft.name)}</b> взлетит и выполнит миссию: ${n} точек, ${ctx.returnHome() ? "возврат в точку взлёта и посадка" : "посадка в последней точке"}.</p>
          <label class="chk"><input type="checkbox" data-c> Зона взлёта и посадки свободна от людей и препятствий</label>
          <label class="chk"><input type="checkbox" data-c> Пилот с пультом на месте и готов перехватить управление</label>
          <label class="chk"><input type="checkbox" data-c> Полёт разрешён: воздушное пространство, погода, заряд батареи</label>`,
        { okText: "▶ Старт", onOk: async (bg) => {
          if ($$("[data-c]", bg).some((c) => !c.checked)) { toast("Подтвердите все пункты перед стартом", true); return true; }
          toast((await POST(`/api/drones/${lid}/start`, { run_id: d.run.id, plan_hash: d.run.plan_hash, confirm: true })).message);
          refresh();
        } });
        return;
      } else if (kind === "rtl") {
        toast((await POST(`/api/drones/${lid}/rtl`)).message);           // без лишних окон: возврат домой безопасен
      } else if (kind === "land") {
        modal("Посадка на месте", "<p>Борт прервёт маршрут и сядет <b>в текущей точке</b> (AUTO.LAND). Убедитесь, что под бортом безопасно. Для возврата в точку взлёта — «Вернуть домой».</p>",
          { okText: "⤓ Посадить", onOk: async () => { toast((await POST(`/api/drones/${lid}/land`)).message); refresh(); } });
        return;
      } else if (kind === "cancel") {
        toast((await POST(`/api/drones/${lid}/cancel`)).message);
      } else if (kind === "close") {
        modal("Закрыть полёт без журнала", "<p>Связь с бортом потеряна в полёте. Полёт будет закрыт как <b>«не выполнен»</b>, борт освободится для новых полётов.</p><p>Найдите борт и осмотрите его. Если агент на борту позже передаст журнал, наработка будет учтена автоматически.</p>",
          { okText: "Закрыть полёт", onOk: async () => { toast((await POST(`/api/drones/${lid}/cancel`)).message); refresh(); } });
        return;
      }
      refresh();
    } catch (e) { toast(e.message, true); }
  }

  timer = setInterval(refresh, 1500);
  await refresh();
}
