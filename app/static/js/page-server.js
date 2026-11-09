/* tbo-tracker · page-server.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ Сервер (Raspberry Pi): состояние, уведомления вне браузера, резервные копии
const fmtBytes = (n) => {
  if (n == null) return "—";
  const u = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${num(n, i ? 1 : 0)} ${u[i]}`;
};
const fmtUptime = (s) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? `${d} д ` : "") + `${h} ч ${m} мин`;
};
const BK_KIND = { auto: "по расписанию", manual: "вручную", "pre-restore": "перед восстановлением", "pre-update": "перед обновлением", upload: "загружена" };

async function pageServer() {
  view().innerHTML = `<div class="page-head"><div><h1>Сервер</h1><div class="sub">Состояние Raspberry Pi, уведомления вне браузера и резервные копии. Вся логика выполняется на сервере; эта страница — пульт администратора.</div></div>
    <div class="actions"><button id="srv-refresh">↻ Обновить</button></div></div>
    <div id="srv-status"><div class="loading">Загрузка…</div></div>
    <div class="grid g2">
      <div class="card"><h2>Уведомления вне браузера</h2><div id="srv-notify"><div class="loading">Загрузка…</div></div></div>
      <div class="card"><div class="card-head"><h2>Резервные копии</h2><div class="actions"><label class="btn sm m-0">⇪ Загрузить .zip<input type="file" id="bk-up" accept=".zip" hidden></label>
        <button class="primary sm" id="bk-new">+ Создать копию</button></div></div><div id="srv-backups"><div class="loading">Загрузка…</div></div></div>
    </div>
    <div class="card"><h2>Командная строка</h2>
      <div class="small muted mb-8">Учётные записи, обновление и восстановление, когда веб-интерфейс недоступен. Raspberry Pi — <span class="mono">sudo tbo-ctl …</span>
        (docs/RASPBERRY_PI.md), Docker — <span class="mono">docker compose exec app python -m app.admin …</span> (docs/DEPLOY.md).</div>
      <table class="small"><tbody>
        ${[["sudo tbo-ctl status", "состояние сервиса, температура, питание, адреса"], ["sudo tbo-ctl logs", "журнал сервиса в реальном времени"],
          ["sudo tbo-ctl user add ИМЯ admin", "создать пользователя (пароль — скрытый ввод)"], ["sudo tbo-ctl user passwd ИМЯ", "задать пароль, если забыт"],
          ["sudo tbo-ctl user unlock ИМЯ", "снять блокировку после неудачных входов"], ["sudo tbo-ctl backup", "резервная копия сейчас"],
          ["sudo tbo-ctl restore ИМЯ.zip", "восстановление (сервис останавливается на время операции)"],
          ["sudo tbo-ctl update", "обновление из git с копией перед обновлением"], ["sudo tbo-ctl restart", "перезапуск сервиса"]].map(([c, d]) => `<tr><td class="mono nowrap">${c}</td><td>${d}</td></tr>`).join("")}
      </tbody></table></div>`;

  async function loadStatus() {
    const st = await GET("/api/server/status");
    if (!$("#srv-status")) return;
    const h = st.host, m = st.memory, sg = st.storage, d = st.data, sc = st.scheduler;
    const memUsed = m.total ? 1 - m.available / m.total : 0, diskUsed = sg.total ? sg.used / sg.total : 0;
    const bar = (k) => `<div class="bar mt-4"><span class="${k > 0.9 ? "bad" : k > 0.75 ? "warn" : ""}" data-w="${Math.round(k * 100)}"></span></div>`;
    $("#srv-status").innerHTML = `${st.warnings.map((w) => `<div class="notice warn">⚠ ${esc(w)}</div>`).join("")}
      <div class="kpis">
        <div class="kpi"><div class="v fs-16">${esc(h.model)}</div><div class="l">${esc(h.os)} · ${esc(h.arch)} · ${h.cpus} ядра</div></div>
        <div class="kpi ${h.cpu_temp_c >= 75 ? "bad" : h.cpu_temp_c >= 65 ? "warn" : "ok"}"><div class="v">${h.cpu_temp_c != null ? num(h.cpu_temp_c, 1) + " °C" : "—"}</div><div class="l">температура процессора</div></div>
        <div class="kpi"><div class="v">${h.load ? num(h.load[0], 2) : "—"}</div><div class="l">загрузка (1 мин) · работает ${fmtUptime(h.uptime_s)}</div></div>
        <div class="kpi"><div class="v">${fmtBytes(m.available)}</div><div class="l">свободно ОЗУ из ${fmtBytes(m.total)}</div>${bar(memUsed)}</div>
        <div class="kpi ${sg.kind === "microSD" ? "warn" : ""}"><div class="v">${fmtBytes(sg.free)}</div><div class="l">свободно на ${esc(sg.kind)} из ${fmtBytes(sg.total)}</div>${bar(diskUsed)}</div>
        <div class="kpi ${h.throttled && h.throttled.flags.length ? "bad" : "ok"}"><div class="v fs-16">${h.throttled ? (h.throttled.flags.length ? "есть замечания" : "в норме") : "—"}</div><div class="l">питание и охлаждение (vcgencmd)</div></div>
      </div>
      <div class="grid g2">
        <div class="card"><h2>Доступ</h2>
          <p>Откройте с любого компьютера, планшета или телефона в той же сети:</p>
          <ul>${st.urls.map((u) => `<li><a href="${esc(u)}" class="mono">${esc(u)}</a></li>`).join("")}</ul>
          <p class="small">Вход — по учётным записям (раздел <a href="#/users">«Пользователи»</a>), пароли хранятся как хеш Argon2id.
            Соединение: ${st.app.https ? '<span class="badge ok">HTTPS</span>' : '<span class="badge warn">HTTP без шифрования</span>'} ·
            база: <b>${esc(st.app.database)}</b> · демо-режим: ${st.app.demo_mode ? "включён" : "выключен"}</p></div>
        <div class="card"><h2>Сервис</h2><table class="small"><tbody>
          <tr><td class="muted">Версия tbo-tracker</td><td>${esc(st.app.version)} · Python ${esc(st.app.python)}</td></tr>
          <tr><td class="muted">Сервис работает</td><td>${fmtUptime(st.app.uptime_s)}</td></tr>
          <tr><td class="muted">Каталог данных</td><td class="mono">${esc(st.app.data_dir)}</td></tr>
          <tr><td class="muted">База / фото / копии</td><td>${fmtBytes(d.db_bytes)} / ${fmtBytes(d.uploads_bytes)} / ${d.backups} шт., ${fmtBytes(d.backups_bytes)}</td></tr>
          <tr><td class="muted">Последняя копия</td><td>${d.last_backup ? `${esc(d.last_backup.name)}` : "нет"}</td></tr>
          <tr><td class="muted">Фоновая проверка</td><td>${sc.last_tick ? `последняя: ${fmtDT(sc.last_tick)}` : sc.started_at ? "запущена" : '<span class="muted">выключена</span>'}${sc.last_error ? `<div class="small c-bad">${esc(sc.last_error)}</div>` : ""}</td></tr>
          <tr><td class="muted">Последняя рассылка</td><td>${sc.last_delivery ? `${fmtDT(sc.last_delivery.at)}: ${Object.entries(sc.last_delivery.result).map(([k, v]) => `${esc(k)} — ${esc(v)}`).join("; ")}` : "—"}</td></tr>
          <tr><td class="muted">Хранилище</td><td>${esc(sg.device || "—")}</td></tr>
        </tbody></table></div>
      </div>`;
  }

  async function loadNotify() {
    const c = await GET("/api/server/settings");
    const CATS = [["limit", "Достигнут предельный ресурс"], ["overdue", "Просроченные работы"], ["sensor", "Падения и столкновения"], ["approaching", "Приближение срока"]];
    const root = $("#srv-notify");
    root.innerHTML = `
      <div class="small muted mb-10">Сервер проверяет регламенты и датчики сам и отправляет новые уведомления одним сообщением — даже когда никто не открыл сайт.</div>
      <div class="field"><label>Что рассылать</label><div class="chips">${CATS.map(([k, l]) => `<label class="chk"><input type="checkbox" name="cat" value="${k}" ${c.categories.includes(k) ? "checked" : ""}> ${l}</label>`).join("")}</div></div>
      <h3>Telegram</h3>
      <label class="chk"><input type="checkbox" name="tg_on" ${c.telegram.enabled ? "checked" : ""}> Отправлять в Telegram</label>
      <div class="row2"><div class="field"><label>Токен бота (от @BotFather)</label><input name="tg_token" placeholder="${c.telegram.has_token ? esc(c.telegram.token) + " — сохранён" : "123456:ABC…"}" autocomplete="off"></div>
        <div class="field"><label>chat_id (личный чат или группа)</label><input name="tg_chat" value="${esc(c.telegram.chat_id)}" placeholder="напр. 123456789 или -100…"></div></div>
      <div class="hint">Создайте бота у @BotFather, напишите ему любое сообщение, chat_id — в ответе https://api.telegram.org/bot&lt;токен&gt;/getUpdates. Нужен доступ Raspberry Pi в интернет.</div>
      <h3>Webhook</h3>
      <label class="chk"><input type="checkbox" name="wh_on" ${c.webhook.enabled ? "checked" : ""}> POST JSON на адрес (диспетчерская, Mattermost, n8n, SCADA…)</label>
      <div class="field"><input name="wh_url" value="${esc(c.webhook.url)}" placeholder="http://192.168.1.20:5678/webhook/tbo"></div>
      <div class="row3"><div class="field"><label>Адрес сайта для ссылок в сообщениях</label><input name="public_url" value="${esc(c.public_url)}" placeholder="http://tbo-tracker.local"></div>
        <div class="field"><label>Проверка каждые, с</label><input type="number" min="15" max="3600" name="scan" value="${c.scan_interval_s}"></div><div></div></div>
      <h3>Резервные копии по расписанию</h3>
      <div class="row3"><label class="chk"><input type="checkbox" name="bk_on" ${c.backup.enabled ? "checked" : ""}> Каждый день</label>
        <div class="field"><label>Час (местное время)</label><input type="number" min="0" max="23" name="bk_hour" value="${c.backup.hour}"></div>
        <div class="field"><label>Хранить автокопий</label><input type="number" min="1" max="365" name="bk_keep" value="${c.backup.keep}"></div></div>
      <div class="actions"><button class="primary" id="ntf-save">Сохранить настройки</button><button id="ntf-test">Отправить тестовое сообщение</button></div>
      <div id="ntf-res"></div>`;
    const form = () => ({
      scan_interval_s: +val(root, "scan") || 60, public_url: val(root, "public_url") || "",
      categories: $$('[name="cat"]', root).filter((x) => x.checked).map((x) => x.value),
      telegram: { enabled: $('[name="tg_on"]', root).checked, token: val(root, "tg_token") || "", chat_id: val(root, "tg_chat") || "" },
      webhook: { enabled: $('[name="wh_on"]', root).checked, url: val(root, "wh_url") || "" },
      backup: { enabled: $('[name="bk_on"]', root).checked, hour: +val(root, "bk_hour"), keep: +val(root, "bk_keep") },
    });
    $("#ntf-save").onclick = async () => { try { await api("PUT", "/api/server/settings", form()); toast("Настройки сервера сохранены"); loadNotify(); } catch (e) { toast(e.message, true); } };
    $("#ntf-test").onclick = async () => {
      try {
        await api("PUT", "/api/server/settings", form());
        const r = await POST("/api/server/notify/test");
        $("#ntf-res").innerHTML = `<div class="notice ${Object.values(r.result).every((v) => v.startsWith("ok")) ? "ok" : "bad"}">${Object.entries(r.result).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join("<br>")}</div>`;
      } catch (e) { $("#ntf-res").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    };
  }

  async function loadBackups() {
    const list = await GET("/api/server/backups");
    const root = $("#srv-backups");
    root.innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>Копия</th><th>Тип</th><th class="num">Размер</th><th></th></tr></thead><tbody>
      ${list.map((b) => `<tr><td><span class="mono small">${esc(b.name)}</span><div class="small muted">${fmtDT(b.created_at)}</div></td><td class="small">${esc(BK_KIND[b.kind] || b.kind)}</td>
        <td class="num">${fmtBytes(b.size)}</td><td class="nowrap"><a class="btn sm" href="/api/server/backups/${encodeURIComponent(b.name)}">⇩</a>
        <button class="sm" data-restore="${esc(b.name)}">Восстановить</button> <button class="sm danger" data-del-bk="${esc(b.name)}">✕</button></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty-state">Копий пока нет</div>';
    root.insertAdjacentHTML("beforeend", '<div class="hint">Копия — архив с базой и загруженными фото. Перед восстановлением текущее состояние сохраняется автоматически. Скачивайте копии на другой носитель.</div>');
    $$("[data-restore]", root).forEach((b) => b.addEventListener("click", () => modal("Восстановление из копии",
      `<p>Заменить все данные содержимым копии <b class="mono">${esc(b.dataset.restore)}</b>?</p><p class="small muted">Текущее состояние будет сохранено как копия «перед восстановлением».</p>`,
      { okText: "Восстановить", onOk: async () => { const r = await POST(`/api/server/backups/${encodeURIComponent(b.dataset.restore)}/restore`); toast(r.message); META = null; AIRCRAFT_CACHE = null; refreshNavCount(); loadAll(); } })));
    $$("[data-del-bk]", root).forEach((b) => b.addEventListener("click", () => modal("Удаление копии", `<p>Удалить <b class="mono">${esc(b.dataset.delBk)}</b>?</p>`,
      { okText: "Удалить", onOk: async () => { const r = await api("DELETE", `/api/server/backups/${encodeURIComponent(b.dataset.delBk)}`); toast(r.message); loadBackups(); } })));
  }
  const loadAll = () => Promise.all([loadStatus(), loadBackups()]).catch((e) => toast(e.message, true));
  $("#srv-refresh").onclick = loadAll;
  $("#bk-new").onclick = async (e) => {
    e.target.disabled = true;
    try { const r = await POST("/api/server/backups"); toast(r.message); loadAll(); } catch (err) { toast(err.message, true); } finally { e.target.disabled = false; }
  };
  $("#bk-up").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("POST", "/api/server/backups/upload", fd, true); toast(r.message); loadBackups(); } catch (err) { toast(err.message, true); }
  };
  await Promise.all([loadStatus(), loadNotify(), loadBackups()]);
  const timer = setInterval(() => { if (!$("#srv-status")) { clearInterval(timer); return; } loadStatus().catch(() => {}); }, 15000);
}
