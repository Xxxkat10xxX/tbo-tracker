/* tbo-tracker · core.js — запросы к серверу, форматирование, общие помощники.
   Интерфейс — набор классических скриптов без сборки с общей областью видимости (порядок — index.html).
   Вся бизнес-логика на сервере; здесь — отображение и ввод. Строгая CSP: никаких inline-стилей и скриптов. */
"use strict";

// Состояние входа: пользователь, CSRF-токен, режимы сервера (заполняется auth.js)
const AUTH = { user: null, csrf: null, demo: false, docs: false, roles: {} };
const can = (role) => AUTH.user && { viewer: 0, engineer: 1, admin: 2 }[AUTH.user.role] >= { viewer: 0, engineer: 1, admin: 2 }[role];

// ------------------------------------------------------------------ утилиты
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = () => $("#view");

// Номер перехода между страницами: ответ GET, пришедший после ухода со страницы, не должен её перерисовать
let NAV_SEQ = 0;
class StaleNav extends Error {}
window.addEventListener("unhandledrejection", (e) => { if (e.reason instanceof StaleNav) e.preventDefault(); });
class AuthLost extends Error {}
window.addEventListener("unhandledrejection", (e) => { if (e.reason instanceof AuthLost) e.preventDefault(); });
async function api(method, url, body, isForm, global = false, retried = false) {
  const seq = NAV_SEQ;
  const opt = { method, headers: { "X-Requested-With": "tbo" }, credentials: "same-origin", cache: "no-store" };
  if (method !== "GET" && AUTH.csrf) opt.headers["X-CSRF-Token"] = AUTH.csrf;
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  let r;
  try { r = await fetch(url, opt); } catch (_) {
    throw new Error("Нет связи с сервером — проверьте сеть и что сервер tbo-tracker запущен");
  }
  let data = null;
  try { data = await r.json(); } catch (_) { /* пусто */ }
  const code = data && data.code;
  if (r.status === 401 && code === "auth_required") { onAuthLost(); throw new AuthLost("Требуется вход"); }
  if (r.status === 403 && code === "password_change_required") { showPasswordScreen(true); throw new AuthLost("Смените пароль"); }
  if (r.status === 403 && code === "csrf" && !retried && method !== "GET") {     // токен устарел (вход в другой вкладке)
    if (await refreshAuthState()) return api(method, url, body, isForm, global, true);
  }
  if (method === "GET" && !global && seq !== NAV_SEQ) throw new StaleNav("переход на другую страницу");
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : `Ошибка ${r.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    if (r.status === 403 && code === "forbidden" && AUTH.user && AUTH.user.role === "viewer") msg += " (роль «Наблюдатель» — только просмотр)";
    throw new Error(msg);
  }
  return data;
}
const GET = (u) => api("GET", u);
const POST = (u, b) => api("POST", u, b);

function toast(msg, isErr) {
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), isErr ? 7000 : 4500);
}

const nf = (d) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const num = (v, d = 1) => (v === null || v === undefined ? "—" : nf(d).format(v));
// часы наработки/налёта — всегда до сотых (0,25 ч = 15 мин): короткие полёты видны в сумме
const nfH = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const hnum = (v) => (v === null || v === undefined ? "—" : nfH.format(v));
const hrs = (v) => `${hnum(v)} ч`;
const rub = (v) => `${nf(0).format(Math.round(v))} ₽`;
function fmtDT(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function fmtD(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}
function toLocalInput(date) {
  const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 16);
}
function fromLocalInput(v) { return v ? new Date(v).toISOString() : null; }
function daysAgo(n, hour = 12) { const d = new Date(); d.setDate(d.getDate() - n); d.setHours(hour, 0, 0, 0); return d; }

const DUE = {
  overdue: ["bad", "Требуется / просрочено"],
  due_soon: ["warn", "Приближается"],
  ok: ["ok", "В норме"],
};
const dueBadge = (s) => `<span class="badge ${DUE[s][0]}">${DUE[s][1]}</span>`;
const AC_ST = { active: "ok", maintenance: "warn", storage: "", retired: "bad" };
const CO_ST = { installed: "blue", stock: "ok", maintenance: "warn", retired: "bad" };
const acBadge = (a) => `<span class="badge ${AC_ST[a.status]}">${esc(a.status_label)}</span>`;
const coBadge = (c) => `<span class="badge ${CO_ST[c.status]}">${esc(c.status_label)}</span>`;
const IMP_ST = { imported: "ok", partial: "warn", duplicate: "blue", rejected: "bad", rolled_back: "" };
const METRIC = { hours: "Часы", cycles: "Циклы", days: "Календарь", events: "Падения/столкн." };
const EV_BADGE = { fall: "bad", collision: "warn" };
function eventsTable(list) {
  if (!list.length) return '<div class="empty-state">Падений и столкновений не зафиксировано</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Когда</th><th>Событие</th><th class="num">Пиковая перегрузка</th><th>Источник</th><th>Полёт</th></tr></thead><tbody>
    ${list.map((e) => `<tr><td class="nowrap">${fmtDT(e.occurred_at)}</td><td><span class="badge ${EV_BADGE[e.kind]}">${esc(e.kind_label)}</span></td>
      <td class="num">${e.peak_g != null ? num(e.peak_g, 1) + " g" : "—"}</td><td class="small">${esc(e.details || e.source)}</td><td class="small">№${e.flight_id}</td></tr>`).join("")}</tbody></table></div>
    <div class="hint">Падение — невесомость |a| &lt; 0,35 g не менее 0,2 с и затем удар ≥ 3 g (или запись оборвалась в невесомости — удар не записан); столкновение — удар ≥ 4 g без невесомости, кроме касания земли при посадке (последние 8 с полёта, слабее 10 g); отскоки в пределах 1 с — одно событие (анализ акселерометра ИНС/ПК).</div>`;
}

function metricHTML(m) {
  const cls = m.status === "overdue" ? "bad" : m.status === "due_soon" ? "warn" : "";
  const w = Math.min(100, m.ratio * 100);
  const h = m.metric === "hours", f = (v) => (h ? hnum(v) : num(v, 1));
  const rem = m.remaining < 0 ? `<b class="c-bad">превышено на ${f(-m.remaining)} ${m.unit}</b>`
    : m.remaining === 0 ? '<b class="c-bad">порог достигнут</b>'
    : `осталось ${f(m.remaining)} ${m.unit}`;
  return `<div class="metric"><div class="mt"><span>${METRIC[m.metric]}: <b>${f(m.used)}</b> / ${num(m.limit, h ? 2 : 1)} ${m.unit}</span><span class="muted">${rem}</span></div>
    <div class="bar"><span class="${cls}" data-w="${w}"></span></div></div>`;
}

function entityLink(e) {
  const href = e.kind === "aircraft" ? `#/aircraft/${e.id}` : `#/components/${e.id}`;
  const where = e.kind === "component"
    ? (e.location ? `на <a href="#/aircraft/${e.location.aircraft_id}">${esc(e.location.aircraft_name)}</a> (${esc(e.location.position)})` : esc(e.status_label))
    : esc(e.type);
  return `<a href="${href}"><b>${esc(e.kind === "aircraft" ? e.name : e.serial)}</b></a><div class="small muted">${e.kind === "aircraft" ? "Борт · " + esc(e.serial) : esc(e.type) + " · " + where}</div>`;
}

function dueTable(items, opts = {}) {
  if (!items.length) return `<div class="empty-state">${opts.empty || "Нет работ по выбранному фильтру"}</div>`;
  return `<div class="table-wrap"><table><thead><tr>
    ${opts.noEntity ? "" : "<th>Объект</th>"}<th>Регламент</th><th>Статус</th><th>Счётчики с последнего выполнения</th><th>Последнее выполнение</th><th>Прогноз</th><th></th></tr></thead><tbody>
    ${items.map((i) => `<tr data-key="${esc(i.key)}">
      ${opts.noEntity ? "" : `<td>${entityLink(i.entity)}</td>`}
      <td>${esc(i.rule.name)}<div class="small muted">${esc(i.rule.code)}${i.rule.kind === "life" ? " · назначенный ресурс" : ""}</div></td>
      <td>${dueBadge(i.status)}</td>
      <td>${i.metrics.map(metricHTML).join("")}</td>
      <td class="nowrap">${i.rule.kind === "life" ? '<span class="muted small">с начала эксплуатации</span>' : (i.last_performed_at ? fmtD(i.last_performed_at) : '<span class="muted small">не выполнялось<br>(отсчёт от ввода)</span>')}</td>
      <td class="nowrap">${i.status === "overdue" ? '<b class="c-bad">сейчас</b>' : i.forecast_date ? "≈ " + fmtD(i.forecast_date) : "—"}</td>
      <td>${i.rule.kind === "life"
        ? (i.status !== "ok" && i.entity.kind === "component" ? `<a class="btn sm" href="#/components/${i.entity.id}">Заменить</a>` : "")
        : `<button class="sm ${i.status === "ok" ? "" : "primary"}" data-do="${esc(i.key)}">Выполнить</button>`}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function bindDueActions(root, items, after) {
  $$("[data-do]", root).forEach((b) => b.addEventListener("click", () => {
    const it = items.find((x) => x.key === b.dataset.do);
    openMaintenanceModal({ target: it.entity.kind, entity: it.entity, rules: [it.rule], ruleId: it.rule.id, onDone: after });
  }));
}

// ------------------------------------------------------------------ динамические стили без inline-атрибутов
const SAFE_COLOR = /^#[0-9a-f]{3,8}$/i;
function applyDyn(root) {
  const list = root.matches && root.matches("[data-w],[data-h],[data-bg]") ? [root] : [];
  for (const el of list.concat(Array.from(root.querySelectorAll ? root.querySelectorAll("[data-w],[data-h],[data-bg]") : []))) {
    const d = el.dataset;
    if (d.w !== undefined) el.style.width = `${Math.max(0, Math.min(100, +d.w || 0))}%`;
    if (d.h !== undefined) el.style.height = `${Math.max(0, +d.h || 0)}px`;
    if (d.bg !== undefined && SAFE_COLOR.test(d.bg)) el.style.background = d.bg;
  }
}
new MutationObserver((muts) => {
  for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) applyDyn(n);
}).observe(document.documentElement, { childList: true, subtree: true });
