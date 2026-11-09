/* tbo-tracker · notify.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ автоматические уведомления
const NOTIF_CAT = {
  limit: { icon: "⛔", cls: "bad", hint: "Регламент вида «назначенный ресурс» или индивидуальный ресурс экземпляра исчерпан — замена / списание / ремонт у изготовителя." },
  overdue: { icon: "⚠", cls: "bad", hint: "Периодическая работа не выполнена к сроку: по часам, циклам, календарю или после падения/столкновения." },
  approaching: { icon: "⏳", cls: "warn", hint: "Остаток до срока меньше порога предупреждения регламента (по умолчанию 10 % интервала)." },
};
// Журнал уведомлений ведёт сервер (проверка раз в минуту, рассылка в Telegram/webhook).
// Браузер получает новые записи ленты и показывает их всплывающим сообщением.
let NOTIF_LAST = null, NOTIF_QUEUE = Promise.resolve();
// Запросы ленты выполняются строго по очереди: каждое событие показывается один раз
function refreshNavCount() {
  const run = NOTIF_QUEUE.then(pollFeed, pollFeed);
  NOTIF_QUEUE = run.catch(() => null);
  return run;
}
async function pollFeed() {
  try {
    const since = NOTIF_LAST;
    const d = await api("GET", `/api/notifications/feed${since !== null ? `?after=${since}` : ""}`, undefined, false, true);
    NOTIF_LAST = d.last_id;            // запросы идут по очереди; после сброса демо-данных счётчик начинается заново
    const c = d.counts, bad = c.limit + c.overdue;
    const nb = $("#nav-notif");
    nb.hidden = !c.total; nb.textContent = c.total; nb.classList.toggle("warn", !bad);
    const fresh = d.events.filter((e) => e.category !== "approaching" && (since === null || e.id > since));
    if (fresh.length) {
      const msg = fresh.slice(0, 3).map((e) => e.title).join("; ") + (fresh.length > 3 ? ` и ещё ${fresh.length - 3}` : "");
      toast(`🔔 Новые уведомления (${fresh.length}): ${msg}`, true);
      if (window.Notification && Notification.permission === "granted") {
        try { new Notification("tbo-tracker: требуется обслуживание", { body: msg }); } catch (_) { /* не поддерживается */ }
      }
    }
    return d;
  } catch (_) { return null; /* не критично */ }
}
// опрос ленты запускает main.js после входа

async function renderNotifications(root, seqOf) {
  const my = seqOf ? seqOf() : null;
  await refreshNavCount();
  const [d, hist] = await Promise.all([GET("/api/notifications"), GET("/api/notifications/history?limit=50")]);
  if ((seqOf && seqOf() !== my) || !document.body.contains(root)) return;   // вкладку переключили, пока шёл запрос
  const c = d.counts;
  const perm = window.Notification ? Notification.permission : "unsupported";
  root.innerHTML = `<div class="toolbar"><div class="small muted">Формируются автоматически при каждом изменении наработки (импорт журнала, автономный полёт, ручная запись) и по календарю. Проверка выполняется сервером каждую минуту, даже когда браузер закрыт; доставка в Telegram / webhook — раздел <a href="#/server">«Сервер»</a>.</div>
      <div>${perm === "default" ? '<button class="sm" id="n-perm">🔔 Уведомления в браузере</button>' : perm === "granted" ? '<span class="badge ok">уведомления браузера включены</span>' : ""}</div></div>
    <div class="kpis">
      ${["limit", "overdue", "approaching"].map((k) => `<div class="kpi clickable ${c[k] ? NOTIF_CAT[k].cls : "ok"}" data-scroll="n-${k}"><div class="v">${c[k]}</div><div class="l">${esc(d.categories[k])}</div></div>`).join("")}
      <div class="kpi clickable ${c.sensor_events ? "warn" : "ok"}" data-scroll="n-sens"><div class="v">${c.sensor_events}</div><div class="l">Падений и столкновений за ${d.days} дн.</div></div>
    </div>
    ${["limit", "overdue", "approaching"].map((k) => `<div class="card flat notif-${NOTIF_CAT[k].cls}" id="n-${k}"><div class="card-head"><h2>${NOTIF_CAT[k].icon} ${esc(d.categories[k])} <span class="muted">(${c[k]})</span></h2></div>
      <div class="small muted m-hint">${NOTIF_CAT[k].hint}</div><div data-cat="${k}">${dueTable(d.items[k], { empty: "Нет уведомлений" })}</div></div>`).join("")}
    <div class="card flat" id="n-sens"><h2>Мониторинг датчиков: падения и столкновения</h2>
      ${d.sensor_events.length ? `<div class="table-wrap"><table><thead><tr><th>Когда</th><th>Борт</th><th>Событие</th><th class="num">Перегрузка</th><th>Источник</th></tr></thead><tbody>
        ${d.sensor_events.map((e) => `<tr><td class="nowrap">${fmtDT(e.occurred_at)}</td><td>${e.aircraft ? `<a href="#/aircraft/${e.aircraft.id}">${esc(e.aircraft.name)}</a> <span class="small muted">${esc(e.aircraft.serial)}</span>` : "—"}</td>
          <td><span class="badge ${EV_BADGE[e.kind]}">${esc(e.kind_label)}</span></td><td class="num">${e.peak_g != null ? num(e.peak_g, 1) + " g" : "—"}</td><td class="small">${esc(e.details || e.source)}</td></tr>`).join("")}
        </tbody></table></div>` : `<div class="empty-state">За ${d.days} дней падений и столкновений не зафиксировано</div>`}</div>
    <div class="card flat"><h2>Журнал уведомлений сервера</h2>
      ${hist.length ? `<div class="table-wrap"><table><thead><tr><th>Появилось</th><th>Категория</th><th>Что</th><th>Закрыто</th><th>Доставка</th></tr></thead><tbody>
        ${hist.map((e) => `<tr><td class="nowrap">${fmtDT(e.created_at)}</td><td><span class="badge ${e.category === "approaching" ? "warn" : "bad"}">${esc(e.category_label)}</span></td>
          <td>${e.href ? `<a href="${esc(e.href)}">${esc(e.title)}</a>` : esc(e.title)}${e.text ? `<div class="small muted">${esc(e.text)}</div>` : ""}</td>
          <td class="nowrap small">${e.resolved_at ? fmtDT(e.resolved_at) : '<span class="muted">действует</span>'}</td>
          <td class="small">${Object.keys(e.delivery).length ? Object.entries(e.delivery).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join("<br>") : e.delivered_at ? '<span class="muted">только в интерфейсе</span>' : '<span class="muted">ожидает</span>'}</td></tr>`).join("")}
        </tbody></table></div>` : '<div class="empty-state">Новых уведомлений с момента заполнения базы не было. Импортируйте журнал или выполните миссию — здесь появятся записи.</div>'}</div>`;
  ["limit", "overdue", "approaching"].forEach((k) => bindDueActions($(`[data-cat="${k}"]`), d.items[k], () => { refreshNavCount(); renderNotifications(root); }));
  $$("[data-scroll]").forEach((k) => k.addEventListener("click", () => document.getElementById(k.dataset.scroll).scrollIntoView({ behavior: "smooth" })));
  const pb = $("#n-perm");
  if (pb) pb.onclick = async () => { await Notification.requestPermission(); renderNotifications(root); };
}
