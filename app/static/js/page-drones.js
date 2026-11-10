/* tbo-tracker · page-drones.js — «Борты на связи»: бортовые агенты tbo-agent, их состояние и подключение.
   Общая область видимости классических скриптов (см. index.html). */
"use strict";

function droneRowHTML(l) {
  const t = l.telemetry || {}, adm = can("admin");
  const run = l.run ? `<a href="#/missions/${l.run.mission_id}">${esc(l.run.mission_name || "миссия")}</a><div class="small"><span class="badge ${RUN_CLS[l.run.status] || ""}">${esc(l.run.status_label)}</span></div>` : '<span class="muted small">—</span>';
  return `<tr>
    <td><a href="#/aircraft/${l.aircraft.id}"><b>${esc(l.aircraft.name)}</b></a><div class="small muted">${esc(l.aircraft.serial)}${l.enabled ? "" : ' · <span class="c-bad">отключено</span>'}</div></td>
    <td><span class="dot ${l.online ? "" : "gray"}"></span><span class="badge ${DRONE_CLS[l.state] || ""}">${esc(l.state_label)}</span>
      ${!l.online && l.last_state !== "offline" ? `<div class="small muted">последнее: ${esc(l.last_state_label)}</div>` : ""}<div class="small muted">${esc(l.detail || "")}</div></td>
    <td class="small">${t.battery_pct != null ? `батарея ${num(t.battery_pct, 0)}%` : "—"}<br>${t.sats != null ? `GPS ${t.sats} спутн.` : ""} ${t.mode ? "· " + esc(t.mode) : ""}</td>
    <td class="small">${l.agent.version ? `tbo-agent ${esc(l.agent.version)}<br>${esc(l.agent.executor || "")}` : '<span class="muted">не подключался</span>'}</td>
    <td class="small nowrap">${l.last_seen_at ? fmtDT(l.last_seen_at) : "—"}<br><span class="muted">${esc(l.last_ip || "")}</span></td>
    <td>${run}</td>
    ${adm ? `<td class="nowrap"><button class="sm" data-token="${l.id}">Новый токен</button>
      <button class="sm" data-enable="${l.id}" data-on="${l.enabled ? 0 : 1}">${l.enabled ? "Отключить" : "Включить"}</button>
      <button class="sm danger" data-del="${l.id}">✕</button><div class="small muted">токен ${esc(l.token_hint)}</div></td>` : ""}
  </tr>`;
}

function tokenModal(l, title) {
  const host = location.origin;
  modal(title, `<div class="notice warn"><b>Токен показан один раз.</b> Он даёт управление бортом ${esc(l.aircraft.name)} — не пересылайте его в открытых чатах.</div>
    <div class="field"><label>Токен устройства</label><input class="mono" readonly value="${esc(l.token)}" data-tok></div>
    <button class="sm" data-copy>Копировать</button>
    <h3 class="mt-16">Установка на борт (Orange Pi 5 Pro, Skyris Technic 6S)</h3>
    <ol class="steps">
      <li>Скачайте агент: <a href="/api/downloads/tbo-agent.tar.gz">tbo-agent.tar.gz</a> и скопируйте на борт:
        <pre>scp tbo-agent.tar.gz orangepi@АДРЕС_БОРТА:~</pre></li>
      <li>На борту (ssh orangepi@АДРЕС_БОРТА):<pre>tar xzf tbo-agent.tar.gz &amp;&amp; cd tbo-agent &amp;&amp; sudo ./install.sh</pre>
        Адрес сервера: <code>${esc(host)}</code>; токен вставьте, когда установщик попросит (ввод скрыт).</li>
      <li>Проверка: <code>journalctl -u tbo-agent -f</code> — «подключён к …»; здесь борт появится как «На связи».</li>
    </ol>
    <div class="hint">Подробно — docs/DRONE.md. Без дрона можно проверить симулятором: <code>python3 -m tbo_agent --sim --sim-speed 10</code>.</div>`, {});
  setTimeout(() => {
    const b = $("[data-copy]");
    if (b) b.onclick = async () => {
      const inp = $("[data-tok]");
      inp.select();
      try { await navigator.clipboard.writeText(inp.value); toast("Токен скопирован"); } catch (_) { toast("Выделено — нажмите Ctrl+C"); }
    };
  });
}

async function pageDrones() {
  const my = NAV_SEQ;
  const [links, fleet] = await Promise.all([GET("/api/drones"), aircraftList(true)]);
  const adm = can("admin");
  view().innerHTML = `<div class="page-head"><div><h1>Борты на связи</h1>
    <div class="sub">Бортовой агент tbo-agent на бортовом компьютере (Orange Pi 5 Pro, PX4, MAVROS) получает план миссии, выполняет полёт по команде оператора и после посадки передаёт журнал — наработка начисляется автоматически. Полётом управляют на странице миссии.</div></div>
    <div class="actions">${adm ? '<button class="primary" id="d-new">+ Подключить борт</button>' : ""}<a class="btn" href="/api/downloads/tbo-agent.tar.gz">⇩ Агент для борта</a></div></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Борт</th><th>Состояние</th><th>Телеметрия</th><th>Агент</th><th>Последний обмен</th><th>Полёт</th>${adm ? "<th></th>" : ""}</tr></thead>
      <tbody id="d-rows">${links.map(droneRowHTML).join("") || `<tr><td colspan="7"><div class="empty-state">Бортов на связи нет${adm ? " — «Подключить борт»" : ""}</div></td></tr>`}</tbody></table></div></div>
    <div class="card"><h2>Как это работает</h2><ol class="steps">
      <li>Администратор подключает борт: сервер выпускает токен устройства (в базе хранится только его хеш).</li>
      <li>На бортовом компьютере ставится агент (<code>sudo ./install.sh</code>) — он сам обращается к серверу по Wi-Fi; на дроне не нужны открытые порты.</li>
      <li>На странице миссии: «Отправить план на борт» → борт загружает план в PX4 и проверяет GPS, заряд, положение → «Старт» с подтверждением.</li>
      <li>В полёте — телеметрия на карте, «Вернуть домой» и «Посадить на месте». После посадки журнал импортируется: наработка, циклы, удары по акселерометру, напоминания ТО.</li>
    </ol></div>`;
  async function reloadRows() {
    if (my !== NAV_SEQ) return;
    const fresh = await api("GET", "/api/drones", undefined, false, true).catch(() => null);
    if (fresh && my === NAV_SEQ && $("#d-rows")) { $("#d-rows").innerHTML = fresh.map(droneRowHTML).join("") || ""; bind(); }
  }
  function bind() {
    $$("[data-token]").forEach((b) => b.onclick = () => modal("Новый токен", "<p>Прежний токен перестанет действовать — агент на борту отключится, пока вы не введёте новый токен (<code>sudo ./install.sh --token</code>).</p>",
      { okText: "Выпустить", onOk: async () => { const r = await POST(`/api/drones/${b.dataset.token}/token`); tokenModal(r, "Новый токен устройства"); reloadRows(); } }));
    $$("[data-enable]").forEach((b) => b.onclick = async () => {
      try { await api("PATCH", `/api/drones/${b.dataset.enable}`, { enabled: b.dataset.on === "1" }); reloadRows(); } catch (e) { toast(e.message, true); }
    });
    $$("[data-del]").forEach((b) => b.onclick = () => modal("Удалить подключение", "<p>Токен перестанет действовать. Учтённые полёты и журналы останутся.</p>",
      { okText: "Удалить", onOk: async () => { toast((await api("DELETE", `/api/drones/${b.dataset.del}`)).message); reloadRows(); } }));
  }
  bind();
  if ($("#d-new")) $("#d-new").onclick = () => {
    const taken = new Set(links.map((l) => l.aircraft.id));
    const free = fleet.filter((a) => !taken.has(a.id) && a.status !== "retired");
    if (!free.length) { toast("Все борты уже подключены", true); return; }
    modal("Подключить борт", `<div class="field"><label>Борт</label><select name="aircraft">${free.map((a) => `<option value="${a.id}">${esc(a.name)} · ${esc(a.serial)}</option>`).join("")}</select></div>
      <div class="hint">Будет выпущен токен устройства для бортового агента. Один борт — одно подключение.</div>`,
    { okText: "Выпустить токен", onOk: async (bg) => { const r = await POST("/api/drones", { aircraft_id: +val(bg, "aircraft") }); tokenModal(r, "Борт подключён"); reloadRows(); } });
  };
  const timer = setInterval(() => { if (my !== NAV_SEQ) clearInterval(timer); else reloadRows(); }, 3000);
}
