/* СОКОЛ · ТО по наработке — одностраничный интерфейс без сборки (vanilla JS). */
"use strict";

// ------------------------------------------------------------------ утилиты
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = () => $("#view");

async function api(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { /* пусто */ }
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : `Ошибка ${r.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
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
const hrs = (v) => `${num(v, 1)} ч`;
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
const METRIC = { hours: "Часы", cycles: "Циклы", days: "Календарь" };

function metricHTML(m) {
  const cls = m.status === "overdue" ? "bad" : m.status === "due_soon" ? "warn" : "";
  const w = Math.min(100, m.ratio * 100);
  const rem = m.remaining <= 0 ? `<b style="color:var(--bad)">превышено на ${num(-m.remaining, 1)} ${m.unit}</b>`
    : `осталось ${num(m.remaining, 1)} ${m.unit}`;
  return `<div class="metric"><div class="mt"><span>${METRIC[m.metric]}: <b>${num(m.used, 1)}</b> / ${num(m.limit, 0)} ${m.unit}</span><span class="muted">${rem}</span></div>
    <div class="bar"><span class="${cls}" style="width:${w}%"></span></div></div>`;
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
      <td class="nowrap">${i.status === "overdue" ? '<b style="color:var(--bad)">сейчас</b>' : i.forecast_date ? "≈ " + fmtD(i.forecast_date) : "—"}</td>
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

// ------------------------------------------------------------------ модальные окна
function modal(title, bodyHTML, { okText = "Сохранить", wide = false, onOk, cancelText = "Отмена" } = {}) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal ${wide ? "wide" : ""}"><div class="modal-h"><h2>${esc(title)}</h2><button class="ghost" data-x>✕</button></div>
    <div class="modal-b">${bodyHTML}</div>
    <div class="modal-f">${onOk ? `<button data-x>${cancelText}</button><button class="primary" data-ok>${okText}</button>` : `<button data-x>Закрыть</button>`}</div></div>`;
  document.body.appendChild(bg);
  const close = () => bg.remove();
  $$("[data-x]", bg).forEach((b) => b.addEventListener("click", close));
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) close(); });
  if (onOk) {
    const ok = $("[data-ok]", bg);
    ok.addEventListener("click", async () => {
      ok.disabled = true;
      try { const keep = await onOk(bg); if (keep !== true) close(); } catch (e) { toast(e.message, true); } finally { ok.disabled = false; }
    });
  }
  return bg;
}
const val = (root, name) => { const el = $(`[name="${name}"]`, root); return el ? el.value.trim() : null; };
const orNull = (v) => (v === "" || v === null ? null : v);

let META = null;
async function meta() { if (!META) META = await GET("/api/meta"); return META; }
let AIRCRAFT_CACHE = null;
async function aircraftList(force) { if (!AIRCRAFT_CACHE || force) AIRCRAFT_CACHE = await GET("/api/aircraft"); return AIRCRAFT_CACHE; }

async function openInstallModal({ component, mode, presetAircraft, presetPosition, onDone }) {
  // mode: install | transfer
  const [m, fleet] = await Promise.all([meta(), aircraftList(true)]);
  const typeCode = component.type.code;
  const detail = {};
  const options = fleet.filter((a) => a.status !== "retired").map((a) => `<option value="${a.id}" ${presetAircraft == a.id ? "selected" : ""}>${esc(a.name)} · ${esc(a.serial)}</option>`).join("");
  const body = `
    <div class="notice small">Узел <b>${esc(component.serial)}</b> (${esc(component.type.name)}) · сквозная наработка <b>${hrs(component.hours)}</b>, ${num(component.cycles, 0)} цикл.
      ${mode === "transfer" ? `<br>Сейчас: <b>${esc(component.location.aircraft_name)}</b> (${esc(component.location.position)}). Вся история и наработка остаются за узлом.` : ""}</div>
    <div class="row2"><div class="field"><label>Борт</label><select name="aircraft_id">${options}</select></div>
    <div class="field"><label>Позиция (посадочное место)</label><select name="position"></select></div></div>
    <div class="field"><label>Дата и время операции</label><input type="datetime-local" name="at" value="${toLocalInput(new Date())}">
      <div class="hint">Полёты из журналов начисляются узлу по фактическому составу борта на момент взлёта. Для демо-сценария №2 укажите позавчерашнюю дату.</div></div>
    <div class="row2"><div class="field"><label>Основание</label><input name="reason" placeholder="Напр.: замена неисправного узла"></div>
    <div class="field"><label>Исполнитель</label><input name="performed_by" placeholder="ФИО инженера"></div></div>`;
  const bg = modal(mode === "transfer" ? "Перестановка узла на другой борт" : "Установка узла на борт", body, {
    okText: mode === "transfer" ? "Переставить" : "Установить",
    onOk: async (root) => {
      const payload = { aircraft_id: +val(root, "aircraft_id"), position: val(root, "position"), at: fromLocalInput(val(root, "at")),
        reason: orNull(val(root, "reason")), performed_by: orNull(val(root, "performed_by")) };
      if (!payload.position) throw new Error("Нет свободной совместимой позиции на выбранном борту");
      const r = await POST(`/api/components/${component.id}/${mode}`, payload);
      toast(r.message);
      onDone && onDone();
    },
  });
  const sel = $('[name="aircraft_id"]', bg), pos = $('[name="position"]', bg);
  async function fillPositions() {
    const aid = sel.value;
    if (!detail[aid]) detail[aid] = await GET(`/api/aircraft/${aid}`);
    const slots = detail[aid].configuration.filter((s) => s.component_type === typeCode);
    pos.innerHTML = slots.map((s) => {
      const busy = s.component && s.component.id !== component.id;
      return `<option value="${esc(s.position)}" ${busy ? "disabled" : ""} ${presetPosition === s.position ? "selected" : ""}>${esc(s.label)} (${esc(s.position)})${busy ? " — занято " + esc(s.component.serial) : " — свободно"}</option>`;
    }).join("");
    const firstFree = $$("option:not([disabled])", pos)[0];
    if (!presetPosition && firstFree) firstFree.selected = true;
    if (!slots.length) pos.innerHTML = `<option value="">Нет позиций для типа «${esc(component.type.name)}»</option>`;
  }
  sel.addEventListener("change", () => { presetPosition = null; fillPositions(); });
  fillPositions();
  void m;
}

async function openRemoveModal({ component, onDone }) {
  const body = `<div class="notice small">Снятие <b>${esc(component.serial)}</b> с борта <b>${esc(component.location.aircraft_name)}</b> (${esc(component.location.position)}).
    Наработка, накопленная узлом, сохраняется в его паспорте.</div>
    <div class="row2"><div class="field"><label>Дата и время снятия</label><input type="datetime-local" name="at" value="${toLocalInput(new Date())}"></div>
    <div class="field"><label>Куда</label><select name="to_status"><option value="stock">Резерв / склад</option><option value="maintenance">На обслуживание / ремонт</option><option value="retired">Списать</option></select></div></div>
    <div class="row2"><div class="field"><label>Причина</label><input name="reason" placeholder="Напр.: плановая замена"></div>
    <div class="field"><label>Исполнитель</label><input name="performed_by"></div></div>`;
  modal("Снятие узла с борта", body, {
    okText: "Снять",
    onOk: async (root) => {
      const r = await POST(`/api/components/${component.id}/remove`, { at: fromLocalInput(val(root, "at")), to_status: val(root, "to_status"),
        reason: orNull(val(root, "reason")), performed_by: orNull(val(root, "performed_by")) });
      toast(r.message);
      onDone && onDone();
    },
  });
}

async function openMaintenanceModal({ target, entity, rules, ruleId, onDone }) {
  const m = await meta();
  const statuses = target === "aircraft" ? m.aircraft_statuses : m.component_statuses;
  const isInstalled = target === "component" && entity.status === "installed";
  const stOpts = Object.entries(statuses).filter(([k]) => target === "aircraft" || (isInstalled ? false : k !== "installed"))
    .map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  const periodic = rules.filter((r) => r.kind !== "life");
  const body = `
    <div class="notice small">${target === "aircraft" ? "Борт" : "Узел"} <b>${esc(target === "aircraft" ? entity.name : entity.serial)}</b>.
      Выполнение плановой работы обнуляет межсервисный интервал только по этому регламенту; общая наработка изделия не меняется.</div>
    <div class="field"><label>Работа по регламенту</label><select name="rule_id">
      ${periodic.map((r) => `<option value="${r.id}" ${r.id === ruleId ? "selected" : ""}>${esc(r.name)}</option>`).join("")}
      <option value="" ${ruleId ? "" : "selected"}>— Внеплановая работа / ремонт (без регламента) —</option></select></div>
    <div class="row2"><div class="field"><label>Дата и время выполнения</label><input type="datetime-local" name="performed_at" value="${toLocalInput(new Date())}"></div>
    <div class="field"><label>Вид работы</label><select name="work_type"><option value="scheduled">Плановое ТО</option><option value="inspection">Осмотр / проверка</option><option value="repair">Ремонт</option><option value="unscheduled">Внеплановая работа</option></select></div></div>
    <div class="field"><label>Содержание работ</label><textarea name="description" rows="2" placeholder="Что сделано"></textarea></div>
    <div class="row2"><div class="field"><label>Результат</label><input name="result" value="Выполнено, замечаний нет"></div>
    <div class="field"><label>Исполнитель</label><input name="performed_by" placeholder="ФИО инженера"></div></div>
    <div class="field"><label>Статус после работы</label><select name="new_status"><option value="">не менять (${esc(entity.status_label || "")})</option>${stOpts}</select>
    ${isInstalled ? '<div class="hint">Узел установлен на борт — его статус меняется операцией снятия.</div>' : ""}</div>`;
  modal("Регистрация выполненной работы", body, {
    okText: "Зарегистрировать",
    onOk: async (root) => {
      const r = await POST("/api/maintenance/records", {
        target, entity_id: entity.id, rule_id: val(root, "rule_id") ? +val(root, "rule_id") : null,
        performed_at: fromLocalInput(val(root, "performed_at")), work_type: val(root, "work_type"),
        description: orNull(val(root, "description")), result: orNull(val(root, "result")),
        performed_by: orNull(val(root, "performed_by")), new_status: orNull(val(root, "new_status")),
      });
      toast(r.message);
      refreshNavCount();
      onDone && onDone(r);
    },
  });
}

// ------------------------------------------------------------------ навигация
const routes = [
  [/^#?\/?$/, "dash", pageDashboard],
  [/^#\/aircraft$/, "aircraft", pageAircraftList],
  [/^#\/aircraft\/(\d+)$/, "aircraft", pageAircraft],
  [/^#\/components(?:\?.*)?$/, "components", pageComponents],
  [/^#\/components\/(\d+)$/, "components", pageComponent],
  [/^#\/imports$/, "imports", pageImports],
  [/^#\/maintenance$/, "maintenance", pageMaintenance],
  [/^#\/rules$/, "rules", pageRules],
  [/^#\/analytics$/, "analytics", pageAnalytics],
  [/^#\/scenarios$/, "scenarios", pageScenarios],
];
async function router() {
  const hash = location.hash || "#/";
  for (const [re, key, fn] of routes) {
    const m = hash.match(re);
    if (m) {
      $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.r === key));
      view().innerHTML = '<div class="loading">Загрузка…</div>';
      try { await fn(...m.slice(1)); } catch (e) { view().innerHTML = `<div class="notice bad">Ошибка: ${esc(e.message)}</div>`; }
      window.scrollTo(0, 0);
      return;
    }
  }
  view().innerHTML = '<div class="notice">Страница не найдена</div>';
}
const reload = () => router();
async function refreshNavCount() {
  try {
    const d = await GET("/api/maintenance/due?status=overdue");
    const el = $("#nav-due");
    el.hidden = !d.length;
    el.textContent = d.length;
  } catch (_) { /* не критично */ }
}

// ------------------------------------------------------------------ Обзор
async function pageDashboard() {
  const d = await GET("/api/dashboard");
  const ready = d.fleet.filter((a) => a.airworthy).length;
  const comp = d.components_by_status;
  view().innerHTML = `
    <div class="page-head"><div><h1>Обзор парка</h1><div class="sub">Состояние бортов и узлов по фактической наработке на ${fmtDT(new Date().toISOString())}</div></div>
      <div class="actions"><a class="btn primary" href="#/imports">⇪ Загрузить журнал</a><a class="btn" href="#/scenarios">✓ Сценарии проверки</a></div></div>
    <div class="kpis">
      <div class="kpi"><div class="v">${d.fleet.length}</div><div class="l">бортов в парке</div></div>
      <div class="kpi ${ready === d.fleet.length ? "ok" : "warn"}"><div class="v">${ready} / ${d.fleet.length}</div><div class="l">допущены к полётам</div></div>
      <div class="kpi bad"><div class="v">${d.counts.overdue}</div><div class="l">работ требуется / просрочено</div></div>
      <div class="kpi warn"><div class="v">${d.counts.due_soon}</div><div class="l">работ приближается</div></div>
      <div class="kpi"><div class="v">${comp.installed.count}</div><div class="l">узлов установлено</div></div>
      <div class="kpi"><div class="v">${comp.stock.count} / ${comp.maintenance.count}</div><div class="l">узлов в резерве / на обслуживании</div></div>
    </div>
    <div class="card"><div class="card-head"><h2>Парк БПЛА</h2><a href="#/aircraft">Все борта →</a></div>${fleetTable(d.fleet)}</div>
    <div class="card"><div class="card-head"><h2>Требуют внимания</h2><a href="#/maintenance">Все напоминания →</a></div><div id="due"></div></div>
    <div class="card"><div class="card-head"><h2>Последние импорты журналов</h2><a href="#/imports">Импорт →</a></div>${importsTable(d.recent_imports, false)}</div>`;
  $("#due").innerHTML = dueTable(d.due, { empty: "Все регламентные работы в норме" });
  bindDueActions($("#due"), d.due, reload);
}

function readiness(a) {
  if (a.status !== "active") return `<span class="dot gray"></span>${esc(a.status_label)}`;
  if (a.slots_filled < a.slots_total) return `<span class="dot bad"></span>Некомплект (${a.slots_filled}/${a.slots_total})`;
  if (a.maintenance.overdue) return `<span class="dot bad"></span>Не допущен: просрочено ТО (${a.maintenance.overdue})`;
  if (a.maintenance.due_soon) return `<span class="dot warn"></span>Допущен, скоро ТО (${a.maintenance.due_soon})`;
  return `<span class="dot"></span>Допущен`;
}

function fleetTable(list) {
  return `<div class="table-wrap"><table><thead><tr><th>Борт</th><th>Тип</th><th>Статус</th><th>Готовность (борт + узлы)</th><th class="num">Налёт</th><th class="num">Циклы</th><th class="num">Полёты</th><th>Базирование</th></tr></thead><tbody>
  ${list.map((a) => `<tr><td><a href="#/aircraft/${a.id}"><b>${esc(a.name)}</b></a><div class="small muted mono">${esc(a.serial)}</div></td>
    <td>${esc(a.type.name)}</td><td>${acBadge(a)}</td><td>${readiness(a)}</td>
    <td class="num">${hrs(a.hours)}</td><td class="num">${num(a.cycles, 0)}</td><td class="num">${num(a.flights, 0)}</td><td class="small">${esc(a.base || "")}</td></tr>`).join("")}
  </tbody></table></div>`;
}

// ------------------------------------------------------------------ Парк
async function pageAircraftList() {
  const list = await aircraftList(true);
  view().innerHTML = `<div class="page-head"><div><h1>Парк БПЛА</h1><div class="sub">Электронные паспорта аппаратов</div></div>
    <button class="primary" id="add">+ Добавить борт</button></div>
    <div class="card">${fleetTable(list)}</div>`;
  $("#add").onclick = openAircraftCreate;
}

async function openAircraftCreate() {
  const m = await meta();
  modal("Новый борт", `
    <div class="row2"><div class="field"><label>Серийный номер</label><input name="serial" placeholder="SK-M4-004"></div>
    <div class="field"><label>Бортовое имя</label><input name="name" placeholder="Борт 04"></div></div>
    <div class="row2"><div class="field"><label>Тип</label><select name="type_id">${m.aircraft_types.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Дата ввода в эксплуатацию</label><input type="datetime-local" name="in_service_at" value="${toLocalInput(new Date())}"></div></div>
    <div class="row2"><div class="field"><label>Эксплуатант</label><input name="operator"></div><div class="field"><label>Место базирования</label><input name="base"></div></div>
    <div class="row2"><div class="field"><label>Начальный налёт, ч</label><input type="number" step="0.1" min="0" name="initial_hours" value="0"></div>
    <div class="field"><label>Начальные циклы</label><input type="number" min="0" name="initial_cycles" value="0"></div></div>`, {
    okText: "Создать",
    onOk: async (root) => {
      const r = await POST("/api/aircraft", { serial: val(root, "serial"), name: val(root, "name"), type_id: +val(root, "type_id"),
        in_service_at: fromLocalInput(val(root, "in_service_at")), operator: orNull(val(root, "operator")), base: orNull(val(root, "base")),
        initial_hours: +val(root, "initial_hours") || 0, initial_cycles: +val(root, "initial_cycles") || 0 });
      toast("Борт создан");
      location.hash = `#/aircraft/${r.id}`;
    },
  });
}

async function pageAircraft(id) {
  const [a, m] = await Promise.all([GET(`/api/aircraft/${id}`), meta()]);
  const own = a.due.filter((i) => i.entity.kind === "aircraft");
  view().innerHTML = `
    <div class="crumbs"><a href="#/aircraft">Парк БПЛА</a> / ${esc(a.name)}</div>
    <div class="page-head"><div><h1>${esc(a.name)} <span class="muted mono" style="font-size:15px">${esc(a.serial)}</span></h1>
      <div class="sub">${esc(a.type.name)} · ${esc(a.operator || "")} · ${esc(a.base || "")} · в эксплуатации с ${fmtD(a.in_service_at)}</div></div>
      <div class="actions"><select id="st" style="width:auto">${Object.entries(m.aircraft_statuses).map(([k, v]) => `<option value="${k}" ${k === a.status ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
      <button id="mnt">⚑ Зарегистрировать работу</button></div></div>
    <div class="card"><div class="stat-line">
      <div class="s"><div class="v">${hrs(a.hours)}</div><div class="l">суммарный налёт</div></div>
      <div class="s"><div class="v">${num(a.cycles, 0)}</div><div class="l">циклов (взлёт-посадка)</div></div>
      <div class="s"><div class="v">${num(a.flights, 0)}</div><div class="l">полётов в журналах</div></div>
      <div class="s"><div class="v">${a.slots_filled}/${a.slots_total}</div><div class="l">позиций укомплектовано</div></div>
      <div class="s"><div class="v" style="font-size:15px;padding-top:4px">${readiness(a)}</div><div class="l">допуск к полётам</div></div>
    </div>${a.notes ? `<div class="notice small" style="margin:12px 0 0">${esc(a.notes)}</div>` : ""}</div>
    <div class="card"><div class="card-head"><h2>Конфигурация борта — установленные экземпляры узлов</h2><a href="#/components?status=stock">Резерв на складе →</a></div>
      <div class="slots">${a.configuration.map(slotHTML).join("")}</div></div>
    <div class="card"><div class="tabs" id="tabs">
      <button data-t="due" class="active">ТО борта и узлов (${a.due.filter((i) => i.status !== "ok").length} требуют внимания)</button>
      <button data-t="rec">История обслуживания (${a.maintenance_records.length})</button>
      <button data-t="cfg">История конфигурации (${a.installations.length})</button>
      <button data-t="fl">Полёты (${a.flight_log.length}${a.flight_log.length >= 200 ? "+" : ""})</button></div><div id="tab"></div></div>`;
  $("#st").onchange = async (e) => {
    try { await api("PATCH", `/api/aircraft/${id}`, { status: e.target.value }); toast("Статус борта обновлён"); reload(); } catch (err) { toast(err.message, true); }
  };
  $("#mnt").onclick = () => openMaintenanceModal({ target: "aircraft", entity: { ...a, kind: "aircraft" }, rules: own.map((i) => i.rule), onDone: reload });
  $$(".slot [data-act]").forEach((b) => b.addEventListener("click", async () => {
    const slot = a.configuration.find((s) => s.position === b.dataset.pos);
    if (b.dataset.act === "install") {
      const stock = await GET(`/api/components?status=stock`);
      const compatible = stock.filter((c) => c.type.code === slot.component_type);
      chooseComponentModal(compatible, slot, a);
    } else {
      const comp = await GET(`/api/components/${slot.component.id}`);
      if (b.dataset.act === "remove") openRemoveModal({ component: comp, onDone: reload });
      else openInstallModal({ component: comp, mode: "transfer", onDone: reload });
    }
  }));
  const tabs = {
    due: () => { $("#tab").innerHTML = dueTable(a.due); bindDueActions($("#tab"), a.due, reload); },
    rec: () => { $("#tab").innerHTML = recordsTable(a.maintenance_records); },
    cfg: () => {
      $("#tab").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Позиция</th><th>Узел</th><th>Установлен</th><th>Снят</th><th class="num">Наработка на этом борту</th><th>Основание</th></tr></thead><tbody>
      ${a.installations.map((i) => `<tr><td>${esc(i.position)}</td><td><a href="#/components/${i.component_id}">${esc(i.serial)}</a><div class="small muted">${esc(i.type)}</div></td>
        <td class="nowrap">${fmtDT(i.installed_at)}</td><td class="nowrap">${i.removed_at ? fmtDT(i.removed_at) : '<span class="badge blue">установлен</span>'}</td>
        <td class="num">${hrs(i.hours)} · ${num(i.cycles, 0)} ц.</td><td class="small">${esc(i.install_reason || "")}${i.remove_reason ? `<br><span class="muted">снят: ${esc(i.remove_reason)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
    },
    fl: () => { $("#tab").innerHTML = flightsTable(a.flight_log); },
  };
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => {
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    tabs[b.dataset.t]();
  }));
  tabs.due();
}

function slotHTML(s) {
  if (!s.component) {
    return `<div class="slot empty"><div class="pos"><span>${esc(s.label)}</span><span>${esc(s.position)}</span></div>
      <div class="ser muted">Не установлен</div><div class="small" style="color:var(--bad)">Позиция пуста — борт некомплектен</div>
      <div class="actions"><button class="sm primary" data-act="install" data-pos="${esc(s.position)}">+ Установить</button></div></div>`;
  }
  const c = s.component;
  return `<div class="slot"><div class="pos"><span>${esc(s.label)}</span><span>${esc(s.position)}</span></div>
    <div class="ser"><a href="#/components/${c.id}">${esc(c.serial)}</a> ${dueBadge(c.maintenance.status)}</div>
    <div class="nums"><span>Всего: <b>${hrs(c.hours)}</b></span><span><b>${num(c.cycles, 0)}</b> ц.</span></div>
    <div class="nums"><span>На этом борту: <b>${hrs(c.on_board_hours)}</b></span></div>
    ${c.maintenance.status !== "ok" ? `<div class="small" style="color:var(--${c.maintenance.status === "overdue" ? "bad" : "warn"})">${esc(c.maintenance.rule)}</div>` : ""}
    <div class="actions"><button class="sm" data-act="remove" data-pos="${esc(s.position)}">Снять</button><button class="sm" data-act="transfer" data-pos="${esc(s.position)}">Переставить</button></div></div>`;
}

function chooseComponentModal(list, slot, aircraft) {
  if (!list.length) { toast(`На складе нет исправных узлов типа для позиции «${slot.label}»`, true); return; }
  const bg = modal(`Установка в позицию «${slot.label}» — ${aircraft.name}`, `
    <p class="muted small">Узлы в резерве, совместимые с позицией. Наработка указана сквозная — с начала эксплуатации узла.</p>
    <table><thead><tr><th>Узел</th><th class="num">Наработка</th><th>ТО</th><th></th></tr></thead><tbody>
    ${list.map((c) => `<tr><td><b>${esc(c.serial)}</b><div class="small muted">${esc(c.model || "")}</div></td><td class="num">${hrs(c.hours)} · ${num(c.cycles, 0)} ц.</td>
      <td>${dueBadge(c.maintenance.status)}</td><td><button class="sm primary" data-pick="${c.id}">Выбрать</button></td></tr>`).join("")}</tbody></table>`, {});
  $$("[data-pick]", bg).forEach((b) => b.addEventListener("click", async () => {
    bg.remove();
    const comp = await GET(`/api/components/${b.dataset.pick}`);
    openInstallModal({ component: comp, mode: "install", presetAircraft: aircraft.id, presetPosition: slot.position, onDone: reload });
  }));
}

function recordsTable(recs) {
  if (!recs.length) return '<div class="empty-state">Работ не зарегистрировано</div>';
  const WT = { scheduled: "Плановое ТО", unscheduled: "Внеплановая", repair: "Ремонт", inspection: "Осмотр" };
  return `<div class="table-wrap"><table><thead><tr><th>Дата</th><th>Объект</th><th>Работа</th><th class="num">Наработка на момент</th><th>Исполнитель / результат</th></tr></thead><tbody>
  ${recs.map((r) => `<tr><td class="nowrap">${fmtDT(r.performed_at)}</td>
    <td><a href="#/${r.target === "aircraft" ? "aircraft/" + r.aircraft_id : "components/" + r.component_id}">${esc(r.target === "aircraft" ? r.entity_name : r.entity_serial)}</a>
      ${r.on_aircraft ? `<div class="small muted">на ${esc(r.on_aircraft.name)}</div>` : ""}</td>
    <td>${r.rule ? esc(r.rule.name) : "<i>Без регламента</i>"}<div class="small muted">${WT[r.work_type] || r.work_type}${r.description ? " · " + esc(r.description) : ""}</div></td>
    <td class="num">${hrs(r.hours_at)} · ${num(r.cycles_at, 0)} ц.</td>
    <td class="small">${esc(r.performed_by || "")}<div class="muted">${esc(r.result || "")}</div></td></tr>`).join("")}</tbody></table></div>`;
}

function flightsTable(fl) {
  if (!fl.length) return '<div class="empty-state">Полётов нет</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Взлёт</th><th>Посадка</th><th>Борт</th><th class="num">Длительность</th><th class="num">Циклы</th><th>Задание</th><th>ID в журнале</th><th>Импорт</th></tr></thead><tbody>
  ${fl.map((f) => `<tr><td class="nowrap">${fmtDT(f.started_at)}</td><td class="nowrap">${fmtDT(f.ended_at)}</td><td><a href="#/aircraft/${f.aircraft_id}">${esc(f.aircraft_name)}</a></td>
    <td class="num">${num(f.duration_h * 60, 0)} мин</td><td class="num">${f.cycles}</td><td class="small">${esc(f.mission || "")}</td><td class="mono">${esc(f.external_id)}</td><td class="small">№${f.import_id ?? "—"}</td></tr>`).join("")}</tbody></table></div>`;
}

// ------------------------------------------------------------------ Узлы
async function pageComponents() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const m = await meta();
  let status = params.get("status") || "", typeId = "", q = "";
  view().innerHTML = `<div class="page-head"><div><h1>Узлы и склад</h1><div class="sub">Паспорта заменяемых компонентов: сквозная наработка, местонахождение, состояние ТО</div></div>
    <button class="primary" id="add">+ Добавить узел</button></div>
    <div class="card"><div class="toolbar"><div class="chips" id="chips">
      ${[["", "Все"], ["installed", "Установлены"], ["stock", "Резерв / склад"], ["maintenance", "На обслуживании"], ["retired", "Списаны"]]
        .map(([k, v]) => `<button data-s="${k}" class="${k === status ? "active" : ""}">${v}</button>`).join("")}</div>
      <select id="type"><option value="">Все типы</option>${m.component_types.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>
      <input id="q" placeholder="Поиск по серийному номеру" style="min-width:220px"></div><div id="list"></div></div>`;
  async function load() {
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (typeId) qs.set("type_id", typeId);
    if (q) qs.set("q", q);
    const list = await GET(`/api/components?${qs}`);
    $("#list").innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>Узел</th><th>Тип</th><th>Статус</th><th>Где сейчас</th><th class="num">Сквозная наработка</th><th class="num">Циклы</th><th>ТО</th></tr></thead><tbody>
      ${list.map((c) => `<tr><td><a href="#/components/${c.id}"><b>${esc(c.serial)}</b></a><div class="small muted">${esc(c.model || "")} · ПО ${esc(c.firmware || "—")}</div></td>
        <td>${esc(c.type.name)}</td><td>${coBadge(c)}</td>
        <td>${c.location ? `<a href="#/aircraft/${c.location.aircraft_id}">${esc(c.location.aircraft_name)}</a> <span class="muted small">${esc(c.location.position)}</span>` : '<span class="muted">—</span>'}</td>
        <td class="num">${hrs(c.hours)}</td><td class="num">${num(c.cycles, 0)}</td>
        <td>${dueBadge(c.maintenance.status)}${c.maintenance.status !== "ok" ? `<div class="small muted">${esc(c.maintenance.rule)}</div>` : ""}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty-state">Ничего не найдено</div>';
  }
  $$("#chips button").forEach((b) => b.addEventListener("click", () => { status = b.dataset.s; $$("#chips button").forEach((x) => x.classList.toggle("active", x === b)); load(); }));
  $("#type").onchange = (e) => { typeId = e.target.value; load(); };
  let t;
  $("#q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { q = e.target.value.trim(); load(); }, 250); };
  $("#add").onclick = openComponentCreate;
  load();
}

async function openComponentCreate() {
  const m = await meta();
  modal("Новый узел (постановка на учёт)", `
    <div class="row2"><div class="field"><label>Серийный номер</label><input name="serial" placeholder="FC7-0108"></div>
    <div class="field"><label>Тип</label><select name="type_id">${m.component_types.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select></div></div>
    <div class="row3"><div class="field"><label>Модель</label><input name="model"></div><div class="field"><label>Децимальный номер</label><input name="part_number"></div>
    <div class="field"><label>Версия ПО</label><input name="firmware"></div></div>
    <div class="row2"><div class="field"><label>Производитель</label><input name="manufacturer" value="ООО «СПУТНИК-РЕД» (КБ «СОКОЛ»)"></div>
    <div class="field"><label>Дата постановки на учёт</label><input type="datetime-local" name="in_service_at" value="${toLocalInput(new Date())}"></div></div>
    <div class="row2"><div class="field"><label>Наработка до постановки на учёт, ч</label><input type="number" min="0" step="0.1" name="initial_hours" value="0">
      <div class="hint">Стендовые испытания, эксплуатация в другом парке (по формуляру)</div></div>
    <div class="field"><label>Циклы до постановки на учёт</label><input type="number" min="0" name="initial_cycles" value="0"></div></div>`, {
    okText: "Поставить на учёт",
    onOk: async (root) => {
      const r = await POST("/api/components", { serial: val(root, "serial"), type_id: +val(root, "type_id"), model: orNull(val(root, "model")),
        part_number: orNull(val(root, "part_number")), firmware: orNull(val(root, "firmware")), manufacturer: orNull(val(root, "manufacturer")),
        in_service_at: fromLocalInput(val(root, "in_service_at")), initial_hours: +val(root, "initial_hours") || 0, initial_cycles: +val(root, "initial_cycles") || 0 });
      toast("Узел поставлен на учёт (резерв)");
      location.hash = `#/components/${r.id}`;
    },
  });
}

async function pageComponent(id) {
  const c = await GET(`/api/components/${id}`);
  const rules = c.due.map((i) => i.rule);
  view().innerHTML = `
    <div class="crumbs"><a href="#/components">Узлы и склад</a> / ${esc(c.serial)}</div>
    <div class="page-head"><div><h1>${esc(c.serial)} ${coBadge(c)}</h1>
      <div class="sub">${esc(c.type.name)} · ${esc(c.model || "")} · ${esc(c.part_number || "")} · ПО ${esc(c.firmware || "—")} · ${esc(c.manufacturer || "")}</div></div>
      <div class="actions" id="acts">
        ${c.status === "installed" ? '<button data-a="transfer" class="primary">⇄ Переставить на другой борт</button><button data-a="remove">Снять с борта</button>' : ""}
        ${c.status === "stock" ? '<button data-a="install" class="primary">+ Установить на борт</button>' : ""}
        ${c.status === "stock" || c.status === "maintenance" ? `<button data-a="status">${c.status === "stock" ? "Отправить на обслуживание" : "Вернуть в резерв"}</button>` : ""}
        <button data-a="mnt">⚑ Зарегистрировать работу</button></div></div>
    <div class="card"><div class="stat-line">
      <div class="s"><div class="v">${hrs(c.hours)}</div><div class="l">сквозная наработка (с начала эксплуатации)</div></div>
      <div class="s"><div class="v">${num(c.cycles, 0)}</div><div class="l">циклов</div></div>
      <div class="s"><div class="v">${num(c.flights, 0)}</div><div class="l">полётов в составе бортов</div></div>
      <div class="s"><div class="v">${c.location ? hrs(c.on_board_hours) : "—"}</div><div class="l">${c.location ? "на текущем борту (" + esc(c.location.aircraft_name) + ")" : "не установлен"}</div></div>
      <div class="s"><div class="v">${c.boards_count}</div><div class="l">бортов в истории</div></div>
    </div>
    <div class="small muted" style="margin-top:10px">Поставлен на учёт ${fmtD(c.in_service_at)}${c.initial_hours || c.initial_cycles ? ` с начальной наработкой ${hrs(c.initial_hours)} / ${c.initial_cycles} ц.` : ""}
      ${c.location ? ` · сейчас: <a href="#/aircraft/${c.location.aircraft_id}">${esc(c.location.aircraft_name)}</a>, позиция ${esc(c.location.position)} с ${fmtDT(c.location.installed_at)}` : ""}${c.notes ? " · " + esc(c.notes) : ""}</div></div>
    <div class="card"><div class="card-head"><h2>Регламенты ТО узла</h2></div><div id="due"></div></div>
    <div class="grid g2">
      <div class="card"><h2>Борта в истории узла</h2>${c.stints.length ? `<table><thead><tr><th>Борт</th><th>Период</th><th class="num">Наработка</th></tr></thead><tbody>
        ${c.stints.map((s) => `<tr><td><a href="#/aircraft/${s.aircraft_id}">${esc(s.aircraft_name)}</a> <span class="small muted">${esc(s.position)}</span></td>
          <td class="small">${fmtD(s.installed_at)} — ${s.removed_at ? fmtD(s.removed_at) : '<span class="badge blue">сейчас</span>'}</td>
          <td class="num">${hrs(s.hours)}<div class="small muted">${num(s.cycles, 0)} ц. · ${s.flights} пол.</div></td></tr>`).join("")}
        ${c.initial_hours ? `<tr><td colspan="2" class="muted small">До постановки на учёт</td><td class="num">${hrs(c.initial_hours)}</td></tr>` : ""}
        <tr><td colspan="2"><b>Итого сквозная наработка</b></td><td class="num"><b>${hrs(c.hours)}</b></td></tr></tbody></table>` : '<div class="empty-state">Узел ещё не устанавливался</div>'}</div>
      <div class="card"><h2>Хронология жизни узла</h2><ul class="timeline">${c.timeline.map((e) => `<li class="k-${e.kind}"><div class="t">${fmtDT(e.at)}</div><div>${esc(e.title)}</div>${e.details ? `<div class="small muted">${esc(e.details)}</div>` : ""}</li>`).join("")}</ul></div>
    </div>
    <div class="card"><div class="tabs" id="tabs"><button class="active" data-t="rec">История обслуживания (${c.maintenance_records.length})</button><button data-t="fl">Начисленные полёты (${c.flight_log.length}${c.flight_log.length >= 200 ? "+" : ""})</button></div><div id="tab"></div></div>`;
  $("#due").innerHTML = dueTable(c.due, { noEntity: true, empty: "Для типа узла не задано регламентов" });
  bindDueActions($("#due"), c.due, reload);
  const tabs = { rec: () => { $("#tab").innerHTML = recordsTable(c.maintenance_records); }, fl: () => { $("#tab").innerHTML = flightsTable(c.flight_log); } };
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => { $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b)); tabs[b.dataset.t](); }));
  tabs.rec();
  $$("#acts [data-a]").forEach((b) => b.addEventListener("click", async () => {
    const a = b.dataset.a;
    if (a === "transfer") openInstallModal({ component: c, mode: "transfer", onDone: reload });
    if (a === "install") openInstallModal({ component: c, mode: "install", onDone: reload });
    if (a === "remove") openRemoveModal({ component: c, onDone: reload });
    if (a === "mnt") openMaintenanceModal({ target: "component", entity: { ...c, kind: "component" }, rules, onDone: reload });
    if (a === "status") {
      try { await api("PATCH", `/api/components/${c.id}`, { status: c.status === "stock" ? "maintenance" : "stock" }); toast("Статус узла обновлён"); reload(); } catch (e) { toast(e.message, true); }
    }
  }));
}

// ------------------------------------------------------------------ Импорт
function importsTable(list, withActions = true) {
  if (!list.length) return '<div class="empty-state">Импортов ещё не было</div>';
  return `<div class="table-wrap"><table><thead><tr><th>№</th><th>Файл</th><th>Загружен</th><th>Результат</th><th class="num">Полётов: новых / дубл. / ошибок</th><th class="num">Начислено</th>${withActions ? "<th></th>" : ""}</tr></thead><tbody>
  ${list.map((i) => `<tr><td>${i.id}</td><td><span class="mono">${esc(i.filename)}</span><div class="small muted">${esc((i.format || "").toUpperCase())} · ${esc(i.imported_by || "")}</div></td>
    <td class="nowrap">${fmtDT(i.imported_at)}</td><td><span class="badge ${IMP_ST[i.status]}">${esc(i.status_label)}</span>${i.duplicate_of_id ? `<div class="small muted">повтор импорта №${i.duplicate_of_id}</div>` : ""}</td>
    <td class="num">${i.flights_new} / ${i.flights_duplicate} / ${i.flights_rejected}</td><td class="num">${hrs(i.hours_added)} · ${i.cycles_added} ц.</td>
    ${withActions ? `<td class="nowrap"><button class="sm ghost" data-show="${i.id}">Детали</button>${["imported", "partial"].includes(i.status) ? `<button class="sm danger" data-rb="${i.id}">Отменить</button>` : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

function importResultHTML(r) {
  const cls = { imported: "ok", partial: "warn", duplicate: "warn", rejected: "bad", rolled_back: "" }[r.status];
  const s = r.summary || {};
  let h = `<div class="notice ${cls}"><b>${r.dry_run ? "Предварительная проверка (данные не записаны): " : ""}${esc(r.status_label)}</b> — файл «${esc(r.filename)}».
    Полётов в файле: ${r.flights_total}; новых: <b>${r.flights_new}</b>; дубликатов: ${r.flights_duplicate}; с ошибками: ${r.flights_rejected}.
    Начислено: <b>${hrs(r.hours_added)}</b>, <b>${r.cycles_added}</b> цикл.
    ${r.messages.length ? `<ul>${r.messages.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}</div>`;
  if (s.aircraft && s.aircraft.length) {
    h += `<h3>Начисление бортам</h3><table><thead><tr><th>Борт</th><th>Период полётов</th><th class="num">Полётов</th><th class="num">Часы</th><th class="num">Циклы</th></tr></thead><tbody>
      ${s.aircraft.map((a) => `<tr><td><a href="#/aircraft/${a.aircraft_id}">${esc(a.name)}</a> <span class="small muted">${esc(a.serial)}</span></td><td class="small">${fmtDT(a.first)} — ${fmtDT(a.last)}</td>
        <td class="num">${a.flights}</td><td class="num">+${num(a.hours, 2)}</td><td class="num">+${a.cycles}</td></tr>`).join("")}</tbody></table>`;
  }
  if (s.components && s.components.length) {
    h += `<h3 style="margin-top:14px">Начисление узлам, установленным на момент полётов</h3><table><thead><tr><th>Узел</th><th>Борт / позиция</th><th class="num">Полётов</th><th class="num">Часы</th><th class="num">Циклы</th></tr></thead><tbody>
      ${s.components.map((c) => `<tr><td><a href="#/components/${c.component_id}">${esc(c.serial)}</a> <span class="small muted">${esc(c.type)}</span></td><td>${esc(c.aircraft)} · ${esc(c.position)}</td>
        <td class="num">${c.flights}</td><td class="num">+${num(c.hours, 2)}</td><td class="num">+${c.cycles}</td></tr>`).join("")}</tbody></table>`;
  }
  if (s.alerts && s.alerts.length) {
    h += `<h3 style="margin-top:14px">Сработавшие напоминания</h3><table><tbody>${s.alerts.map((a) => `<tr><td>${entityLink(a.entity)}</td><td>${esc(a.rule.name)}</td>
      <td class="nowrap">${a.previous_status ? dueBadge(a.previous_status) + " → " : ""}${dueBadge(a.status)}</td><td>${a.driver ? metricHTML(a.driver) : ""}</td></tr>`).join("")}</tbody></table>`;
  }
  return h;
}

async function pageImports() {
  const [list, demo] = await Promise.all([GET("/api/imports"), GET("/api/demo-logs")]);
  view().innerHTML = `<div class="page-head"><div><h1>Импорт журналов эксплуатации</h1><div class="sub">Наработка автоматически начисляется борту и всем узлам, стоявшим на нём в момент каждого полёта</div></div></div>
    <div class="grid g2">
      <div class="card"><h2>Загрузить файл</h2>
        <div class="drop" id="drop"><div style="font-size:26px">⇪</div><div><b>Перетащите файл журнала</b> или нажмите для выбора</div>
          <div class="small muted">JSON «sokol-flightlog/1.0» или CSV · до 20 МБ</div><input type="file" id="file" accept=".json,.csv,.txt" hidden></div>
        <div id="fname" class="small" style="margin:8px 0"></div>
        <div class="actions"><button id="check" disabled>Проверить без записи</button><button class="primary" id="go" disabled>Импортировать</button></div>
        <div class="hint">Защита от двойного начисления: повторная загрузка того же файла (SHA-256) и полёты, уже учтённые из других выгрузок (по ID и пересечению по времени), не начисляются.</div></div>
      <div class="card"><h2>Демонстрационные журналы</h2><div class="small muted" style="margin-bottom:8px">Сгенерированы относительно даты заполнения демо-данных. Можно скачать и загрузить вручную или импортировать сразу.</div>
        <table><tbody>${demo.map((d) => `<tr><td><b>${esc(d.title)}</b><div class="small muted">${esc(d.description)}</div><div class="mono small">${esc(d.file)}</div></td>
          <td class="nowrap"><a class="btn sm" href="/api/demo-logs/${encodeURIComponent(d.file)}" download>Скачать</a>
          <button class="sm primary" data-demo="${esc(d.file)}">Импорт</button></td></tr>`).join("")}</tbody></table></div>
    </div>
    <div class="card" id="result" hidden></div>
    <div class="card"><h2>Журнал импортов</h2><div id="hist">${importsTable(list)}</div></div>`;
  let file = null;
  const drop = $("#drop"), input = $("#file");
  const setFile = (f) => { file = f; $("#fname").innerHTML = f ? `Выбран: <b>${esc(f.name)}</b> (${num(f.size / 1024, 1)} КБ)` : ""; $("#check").disabled = $("#go").disabled = !f; };
  drop.onclick = () => input.click();
  input.onchange = () => setFile(input.files[0]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0]); };
  const show = (r) => { const el = $("#result"); el.hidden = false; el.innerHTML = `<h2>Результат</h2>${importResultHTML(r)}`; el.scrollIntoView({ behavior: "smooth" }); };
  async function upload(dry) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("dry_run", dry ? "true" : "false");
    try { const r = await api("POST", "/api/imports", fd, true); show(r); if (!dry) { await refreshHist(); refreshNavCount(); } } catch (e) { toast(e.message, true); }
  }
  $("#check").onclick = () => upload(true);
  $("#go").onclick = () => upload(false);
  $$("[data-demo]").forEach((b) => b.addEventListener("click", async () => {
    try { const r = await POST(`/api/demo-logs/${encodeURIComponent(b.dataset.demo)}/import`); show(r); await refreshHist(); refreshNavCount(); } catch (e) { toast(e.message, true); }
  }));
  async function refreshHist() { $("#hist").innerHTML = importsTable(await GET("/api/imports")); bindHist(); }
  function bindHist() {
    $$("[data-show]").forEach((b) => b.addEventListener("click", async () => modal(`Импорт №${b.dataset.show}`, importResultHTML(await GET(`/api/imports/${b.dataset.show}`)), { wide: true })));
    $$("[data-rb]").forEach((b) => b.addEventListener("click", () => modal("Отмена импорта", `<p>Отменить импорт №${b.dataset.rb}? Все полёты этого журнала будут удалены, а начисленная наработка — списана с бортов и узлов. Файл можно будет загрузить снова.</p>`, {
      okText: "Отменить импорт",
      onOk: async () => { const r = await POST(`/api/imports/${b.dataset.rb}/rollback`); toast(r.message); await refreshHist(); refreshNavCount(); },
    })));
  }
  bindHist();
}

// ------------------------------------------------------------------ ТО
async function pageMaintenance() {
  let filter = "overdue,due_soon";
  view().innerHTML = `<div class="page-head"><div><h1>ТО и напоминания</h1><div class="sub">Список формируется автоматически по текущей наработке, циклам и календарным срокам согласно регламентам</div></div></div>
    <div class="card"><div class="tabs" id="tabs"><button class="active" data-t="due">Предстоящие и просроченные работы</button><button data-t="rec">Журнал выполненных работ</button></div>
    <div id="tb"></div></div>`;
  async function showDue() {
    $("#tb").innerHTML = `<div class="toolbar"><div class="chips" id="chips">${[["overdue,due_soon", "Требуют внимания"], ["overdue", "Требуется / просрочено"], ["due_soon", "Приближается"], ["", "Все регламенты"]]
      .map(([k, v]) => `<button data-f="${k}" class="${k === filter ? "active" : ""}">${v}</button>`).join("")}</div></div><div id="list"><div class="loading">Расчёт…</div></div>
      <div class="hint">«Приближается» — остаток по любому счётчику меньше порога предупреждения из регламента (по умолчанию 10 % интервала). «Требуется / просрочено» — порог достигнут. Прогноз даты — линейный, по средней интенсивности за 30 дней.</div>`;
    $$("#chips button").forEach((b) => b.addEventListener("click", () => { filter = b.dataset.f; showDue(); }));
    const items = await GET(`/api/maintenance/due?status=${filter}`);
    $("#list").innerHTML = dueTable(items);
    bindDueActions($("#list"), items, showDue);
  }
  async function showRec() { $("#tb").innerHTML = recordsTable(await GET("/api/maintenance/records?limit=300")); }
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => { $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b)); (b.dataset.t === "due" ? showDue : showRec)(); }));
  showDue();
}

// ------------------------------------------------------------------ Регламенты
async function pageRules() {
  const [rules, m] = await Promise.all([GET("/api/rules"), meta()]);
  const lim = (r) => [r.interval_hours && `${num(r.interval_hours, 1)} ч`, r.interval_cycles && `${r.interval_cycles} цикл.`, r.interval_days && `${r.interval_days} дн.`].filter(Boolean).join(" / ");
  const warn = (r) => [r.warn_hours != null && `${num(r.warn_hours, 1)} ч`, r.warn_cycles != null && `${r.warn_cycles} ц.`, r.warn_days != null && `${r.warn_days} дн.`].filter(Boolean).join(" / ") || "10 % интервала";
  view().innerHTML = `<div class="page-head"><div><h1>Регламенты ТО</h1><div class="sub">Правила и пороги планового обслуживания для типов аппаратов и узлов. Срабатывает первый достигнутый порог.</div></div>
    <button class="primary" id="add">+ Новый регламент</button></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Код</th><th>Регламент</th><th>Применяется к</th><th>Вид</th><th>Интервал</th><th>Предупреждать за</th><th></th></tr></thead><tbody>
    ${rules.map((r) => `<tr class="${r.active ? "" : "muted"}"><td class="mono">${esc(r.code)}</td><td><b>${esc(r.name)}</b><div class="small muted">${esc(r.description || "")}</div></td>
      <td>${r.target === "aircraft" ? "Борт: " : "Узел: "}${esc(r.applies_to)}</td><td>${r.kind === "life" ? '<span class="badge bad">Назначенный ресурс</span>' : '<span class="badge blue">Периодическое ТО</span>'}${r.active ? "" : ' <span class="badge">неактивен</span>'}</td>
      <td class="nowrap">${lim(r)}</td><td class="nowrap small">${warn(r)}</td>
      <td class="nowrap"><button class="sm" data-edit="${r.id}">Изменить</button> <button class="sm danger" data-del="${r.id}">✕</button></td></tr>`).join("")}</tbody></table></div></div>
    <div class="card small muted"><b>Как считается.</b> Периодическое ТО: счётчики отсчитываются от даты последнего выполнения этой работы (если работа не выполнялась — от ввода объекта в эксплуатацию с учётом всей наработки). Выполнение работы обнуляет интервал только по этому регламенту — суммарная наработка изделия не меняется.
      Назначенный ресурс: считается от начала эксплуатации и не обнуляется; по достижении узел подлежит замене.</div>`;
  const form = (r = {}) => `
    <div class="row2"><div class="field"><label>Код</label><input name="code" value="${esc(r.code || "")}"></div>
    <div class="field"><label>Вид</label><select name="kind"><option value="periodic" ${r.kind !== "life" ? "selected" : ""}>Периодическое ТО (обнуляется выполнением)</option><option value="life" ${r.kind === "life" ? "selected" : ""}>Назначенный ресурс (не обнуляется)</option></select></div></div>
    <div class="field"><label>Наименование работы</label><input name="name" value="${esc(r.name || "")}"></div>
    <div class="field"><label>Описание</label><textarea name="description" rows="2">${esc(r.description || "")}</textarea></div>
    <div class="row2"><div class="field"><label>Объект</label><select name="target"><option value="component" ${r.target !== "aircraft" ? "selected" : ""}>Узел</option><option value="aircraft" ${r.target === "aircraft" ? "selected" : ""}>Борт</option></select></div>
    <div class="field"><label>Тип</label><select name="type"></select></div></div>
    <div class="row3"><div class="field"><label>Интервал, ч</label><input type="number" step="0.1" min="0" name="interval_hours" value="${r.interval_hours ?? ""}"></div>
    <div class="field"><label>Интервал, циклы</label><input type="number" min="0" name="interval_cycles" value="${r.interval_cycles ?? ""}"></div>
    <div class="field"><label>Интервал, дни</label><input type="number" min="0" name="interval_days" value="${r.interval_days ?? ""}"></div></div>
    <div class="row3"><div class="field"><label>Предупреждать за, ч</label><input type="number" step="0.1" min="0" name="warn_hours" value="${r.warn_hours ?? ""}"></div>
    <div class="field"><label>за, циклов</label><input type="number" min="0" name="warn_cycles" value="${r.warn_cycles ?? ""}"></div>
    <div class="field"><label>за, дней</label><input type="number" min="0" name="warn_days" value="${r.warn_days ?? ""}"></div></div>
    <div class="hint">Пустой порог не используется. Если порог предупреждения не задан — 10 % интервала.</div>
    <label style="margin-top:10px"><input type="checkbox" name="active" style="width:auto" ${r.active === false ? "" : "checked"}> Активен</label>`;
  function open(r) {
    const bg = modal(r ? "Изменение регламента" : "Новый регламент", form(r), {
      okText: "Сохранить",
      onOk: async (root) => {
        const n = (k, int) => { const v = val(root, k); return v === "" ? null : (int ? parseInt(v, 10) : parseFloat(v)); };
        const target = val(root, "target"), type = val(root, "type");
        const body = { code: val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")), target, kind: val(root, "kind"),
          aircraft_type_id: target === "aircraft" && type ? +type : null, component_type_id: target === "component" && type ? +type : null,
          interval_hours: n("interval_hours"), interval_cycles: n("interval_cycles", 1), interval_days: n("interval_days", 1),
          warn_hours: n("warn_hours"), warn_cycles: n("warn_cycles", 1), warn_days: n("warn_days", 1), active: $('[name="active"]', root).checked };
        await api(r ? "PUT" : "POST", r ? `/api/rules/${r.id}` : "/api/rules", body);
        toast("Регламент сохранён");
        reload();
      },
    });
    const tSel = $('[name="target"]', bg), typeSel = $('[name="type"]', bg);
    const fill = () => {
      const isAc = tSel.value === "aircraft";
      const list = isAc ? m.aircraft_types : m.component_types;
      const cur = r ? (isAc ? r.aircraft_type_id : r.component_type_id) : null;
      typeSel.innerHTML = `<option value="">${isAc ? "Все типы БПЛА" : "Все типы узлов"}</option>` + list.map((t) => `<option value="${t.id}" ${t.id === cur ? "selected" : ""}>${esc(t.name)}</option>`).join("");
    };
    tSel.onchange = fill;
    fill();
  }
  $("#add").onclick = () => open(null);
  $$("[data-edit]").forEach((b) => b.addEventListener("click", () => open(rules.find((r) => r.id === +b.dataset.edit))));
  $$("[data-del]").forEach((b) => b.addEventListener("click", () => modal("Удаление регламента", "<p>Удалить регламент? Если по нему уже выполнялись работы, он будет деактивирован, история сохранится.</p>", {
    okText: "Удалить", onOk: async () => { const r = await api("DELETE", `/api/rules/${b.dataset.del}`); toast(r.message); reload(); },
  })));
}

// ------------------------------------------------------------------ Эффект
const PALETTE = ["#2f6fed", "#1f9d55", "#c77700", "#8b5cf6", "#d23b3b", "#0ea5b7", "#64708a"];
async function pageAnalytics() {
  const d = await GET("/api/analytics");
  const k = d.kpi;
  const maxTotal = Math.max(1, ...d.monthly.map((m) => m.total));
  const names = d.aircraft_names;
  const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  view().innerHTML = `<div class="page-head"><div><h1>Эффект для эксплуатанта и сервисной службы</h1>
    <div class="sub">Что даёт поэкземплярный учёт ресурса по сравнению с традиционным учётом «по борту» (формуляр аппарата)</div></div></div>
    <div class="kpis">
      <div class="kpi bad"><div class="v">${k.prevented_misses}</div><div class="l">пропусков ТО предотвращено — учёт «по борту» их бы не увидел</div></div>
      <div class="kpi warn"><div class="v">${hrs(k.hidden_hours_total)}</div><div class="l">«скрытой» наработки у ${k.components_with_hidden_hours} узлов, потерянной при учёте по борту</div></div>
      <div class="kpi"><div class="v">${k.components_moved}</div><div class="l">узлов побывали на 2+ бортах — история сохранена</div></div>
      <div class="kpi"><div class="v">${k.component_operations_90d}</div><div class="l">операций установки/снятия за 90 дней</div></div>
      <div class="kpi ok"><div class="v">${k.duplicates_blocked}</div><div class="l">повторных полётов отсечено при импорте</div></div>
      <div class="kpi"><div class="v">${num(k.flights_90d, 0)}</div><div class="l">полётов за 90 дней учтено автоматически</div></div>
      <div class="kpi ${k.overdue_in_operation ? "bad" : "ok"}"><div class="v">${k.overdue_in_operation}</div><div class="l">просроченных работ на бортах в эксплуатации (риск переработки ресурса)</div></div>
      <div class="kpi"><div class="v">${k.ok}</div><div class="l">пар «объект × регламент» в норме</div></div>
    </div>
    <div class="card"><h2>Предотвращённые пропуски ТО при перестановках</h2>
      <p class="small muted">Узел подходит к порогу (или уже превысил его) по своей фактической наработке, но в формуляре текущего борта он «молодой»: учтены только часы с момента установки. Без поэкземплярного учёта работа была бы пропущена.</p>
      ${d.prevented.length ? `<table><thead><tr><th>Узел</th><th>Регламент</th><th>Учёт по борту</th><th>Фактически (поэкземплярно)</th><th>Счётчик</th></tr></thead><tbody>
        ${d.prevented.map((p) => `<tr><td>${entityLink(p.entity)}</td><td>${esc(p.rule.name)}</td><td>${dueBadge(p.naive_status)}<div class="small muted">${hrs(p.naive_hours)} / ${p.naive_cycles} ц. с установки</div></td>
          <td>${dueBadge(p.real_status)}</td><td>${p.driver ? metricHTML(p.driver) : ""}</td></tr>`).join("")}</tbody></table>`
        : '<div class="empty-state">Сейчас таких случаев нет. Выполните сценарий перестановки (FC7-0102 → «Борт 03») — он появится здесь.</div>'}</div>
    <div class="grid g2">
      <div class="card"><h2>Скрытая наработка установленных узлов</h2>
        <p class="small muted">Сквозная наработка узла минус наработка на текущем борту = часы, которые теряются, если ресурс узла ведётся в формуляре борта.</p>
        ${d.hidden_hours.length ? `<table><thead><tr><th>Узел</th><th>Борт</th><th class="num">На борту</th><th class="num">Фактически</th><th class="num">Скрыто</th></tr></thead><tbody>
        ${d.hidden_hours.map((r) => `<tr><td><a href="#/components/${r.component_id}">${esc(r.serial)}</a><div class="small muted">${esc(r.type)}</div></td><td>${esc(r.aircraft)}</td>
          <td class="num">${hrs(r.on_board_hours)}</td><td class="num">${hrs(r.tsn_hours)}</td><td class="num"><b>${hrs(r.hidden_hours)}</b></td></tr>`).join("")}</tbody></table>` : '<div class="empty-state">Нет</div>'}</div>
      <div class="card"><h2>Налёт парка по месяцам</h2>
        <div class="chart">${d.monthly.map((mo) => `<div class="col"><div class="tot">${num(mo.total, 0)}</div>
          ${names.map((n, i) => mo.by_aircraft[n] ? `<div class="seg" title="${esc(n)}: ${num(mo.by_aircraft[n], 1)} ч" style="height:${(mo.by_aircraft[n] / maxTotal) * 170}px;background:${PALETTE[i % PALETTE.length]}"></div>` : "").reverse().join("")}
          <div class="lbl">${MONTHS[+mo.month.slice(5) - 1]}</div></div>`).join("")}</div>
        <div class="legend">${names.map((n, i) => `<span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(n)}</span>`).join("")}</div>
        <div class="small muted" style="margin-top:8px">Часы налёта, загруженные из журналов эксплуатации.</div></div>
    </div>
    <div class="card"><div class="card-head"><h2>Калькулятор экономического эффекта</h2><button class="sm" id="save">Сохранить параметры</button></div>
      <p class="small muted">Прозрачная оценка по формулам ниже. Параметры — допущения эксплуатанта, их можно изменить; количественные показатели берутся из фактических данных парка.</p>
      <div class="row3" id="params">
        ${[["failure_probability", "Вероятность отказа узла при работе сверх регламента (0–1)", "0.01"],
          ["failure_cost", "Средний ущерб от отказа в полёте, ₽", "1000"],
          ["downtime_days_per_failure", "Простой борта после отказа, дней", "1"],
          ["downtime_cost_per_day", "Упущенная выручка за день простоя, ₽", "1000"],
          ["manual_minutes_per_flight", "Ручной учёт одного полёта (борт + узлы), мин", "0.5"],
          ["manual_minutes_per_operation", "Ручной поиск истории узла на операцию, мин", "1"],
          ["engineer_rate", "Стоимость часа инженера, ₽", "100"]].map(([key, lbl, step]) => `<div class="field"><label>${lbl}</label><input type="number" min="0" step="${step}" data-p="${key}" value="${d.economics[key]}"></div>`).join("")}
      </div><div class="calc-res" id="calc"></div></div>`;
  function calc() {
    const p = {};
    $$("[data-p]").forEach((i) => { p[i.dataset.p] = parseFloat(i.value) || 0; });
    const failLoss = p.failure_cost + p.downtime_days_per_failure * p.downtime_cost_per_day;
    const atRisk = k.prevented_misses + k.overdue_in_operation;
    const risk = atRisk * p.failure_probability * failLoss;
    const flightsYear = k.flights_90d * 365 / 90, opsYear = k.component_operations_90d * 365 / 90;
    const laborH = (flightsYear * p.manual_minutes_per_flight + opsYear * p.manual_minutes_per_operation) / 60;
    const labor = laborH * p.engineer_rate;
    $("#calc").innerHTML = `
      <div class="box"><div class="small muted">Предотвращённый ожидаемый ущерб (текущий срез)</div><div class="big-num">${rub(risk)}</div>
        <div class="formula">(${k.prevented_misses} предотвр. пропусков + ${k.overdue_in_operation} выявленных просрочек) × ${p.failure_probability} × (${rub(p.failure_cost)} + ${p.downtime_days_per_failure} дн × ${rub(p.downtime_cost_per_day)})</div></div>
      <div class="box"><div class="small muted">Экономия трудозатрат на учёт, в год</div><div class="big-num">${rub(labor)}</div>
        <div class="formula">(${num(flightsYear, 0)} полётов × ${p.manual_minutes_per_flight} мин + ${num(opsYear, 0)} операций × ${p.manual_minutes_per_operation} мин) / 60 = ${num(laborH, 0)} ч × ${rub(p.engineer_rate)}</div></div>
      <div class="box total"><div class="small muted">Оценка эффекта для парка из ${k.aircraft} бортов</div><div class="big-num">${rub(risk + labor)}</div>
        <div class="formula">≈ ${rub((risk + labor) / Math.max(1, k.aircraft))} на борт. Не учтены: снижение штрафов за срыв заданий, рост остаточной стоимости узлов с подтверждённой историей, данные для производителя о надёжности.</div></div>`;
    return p;
  }
  $$("[data-p]").forEach((i) => i.addEventListener("input", calc));
  calc();
  $("#save").onclick = async () => { try { await api("PUT", "/api/settings/economics", calc()); toast("Параметры сохранены"); } catch (e) { toast(e.message, true); } };
}

// ------------------------------------------------------------------ Сценарии проверки
async function pageScenarios() {
  const [fleet, comps] = await Promise.all([aircraftList(true), GET("/api/components")]);
  const A = Object.fromEntries(fleet.map((a) => [a.serial, a]));
  const C = Object.fromEntries(comps.map((c) => [c.serial, c]));
  const link = (serial) => (A[serial] ? `<a href="#/aircraft/${A[serial].id}">${esc(A[serial].name)}</a>` : C[serial] ? `<a href="#/components/${C[serial].id}">${esc(serial)}</a>` : esc(serial));
  const d2 = daysAgo(2, 12);
  view().innerHTML = `<div class="page-head"><div><h1>Сценарии проверки</h1><div class="sub">Обязательные сценарии приёмки на демо-данных. Каждый можно пройти вручную через интерфейс или выполнить автоматически с таблицей «до / после».</div></div>
    <button id="reset2">↺ Сбросить демо-данные</button></div>
    <div class="notice small">Рекомендуемый порядок: 1 → 2 → 3 → 4 → 5. Сценарии меняют данные; для повторного прогона нажмите «Сбросить демо-данные».</div>
    ${scenarioCard(1, "Расчёт наработки по журналу эксплуатации", `
      <li>Откройте ${link("SK-M4-001")} и запомните налёт борта и наработку узлов в блоке «Конфигурация борта».</li>
      <li>Раздел <a href="#/imports">Импорт журналов</a> → демо-журнал «Журнал смены «Борт 01»» (<span class="mono">01_SK-M4-001_smena.json</span>) → «Импорт».</li>
      <li>Ожидается: +3,52 ч и +8 циклов у борта и у каждого из 7 установленных узлов; в отчёте — сработавшие напоминания.</li>`)}
    ${scenarioCard(2, "Замена и перестановка узла между бортами", `
      <li>Откройте узел ${link("FC7-0102")} (полётный контроллер на «Борт 02», сквозная наработка ~134 ч, до ТО ~4 ч).</li>
      <li>«Переставить на другой борт» → ${link("SK-M4-003")}, позиция FC (пустует — свой ПК в ремонте). <b>Дата операции: ${fmtD(d2.toISOString())}</b> (позавчера) — полёты журнала выполнены вчера, после перестановки.</li>
      <li>На карточке «Борт 03» смените статус на «В эксплуатации» и импортируйте демо-журнал <span class="mono">02_SK-M4-003_posle_perestanovki.csv</span>.</li>
      <li>Ожидается: наработка узла = прежняя + 4,85 ч; в «Бортах в истории узла» — оба борта; межсервисный интервал не обнулился → ТО ПК «Требуется».</li>`)}
    ${scenarioCard(3, "Повторный импорт одного журнала", `
      <li>Повторно импортируйте <span class="mono">01_SK-M4-001_smena.json</span> или его копию <span class="mono">03_SK-M4-001_smena_POVTOR.json</span> → «Повтор — не начислено», счётчики не меняются.</li>
      <li>Импортируйте <span class="mono">04_SK-M4-001_bortovoi_registrator.csv</span> — другая выгрузка тех же 8 полётов (без ID) + 2 новых: начисляются только 2 новых.</li>
      <li>Ошибочный импорт можно отменить кнопкой «Отменить» в журнале импортов.</li>`)}
    ${scenarioCard(4, "Срабатывание напоминаний по часам, циклам и календарю", `
      <li><b>Часы:</b> ${link("IMU3-0206")} — калибровка ИНС каждые 50 ч: до сценария 1 «Приближается», после — «Требуется».</li>
      <li><b>Циклы:</b> ${link("ESC40-0301")} — диагностика ESC каждые 200 циклов: 195 → 203 после сценария 1.</li>
      <li><b>Календарь:</b> ${link("SK-VT2-011")} — годовое ТО (180 дн.) через ~9 дней; ${link("BVK2-0405")} — ТО вычислителя (120 дн.) просрочено.</li>
      <li><b>Назначенный ресурс:</b> ${link("ESC40-0318")} — 372 из 400 ч. Всё видно в разделе <a href="#/maintenance">ТО и напоминания</a>.</li>`)}
    ${scenarioCard(5, "Проведение обслуживания по напоминанию", `
      <li>В <a href="#/maintenance">ТО и напоминания</a> найдите «ТО бортового вычислителя» для ${link("BVK2-0405")} (${link("SK-VT2-012")}) → «Выполнить».</li>
      <li>Ожидается: напоминание уходит в «В норме», интервал отсчитывается от даты работы, сквозная наработка узла не изменилась, «Борт 12» снова допущен к полётам.</li>
      <li>Аналогично можно выполнить калибровку ${link("IMU3-0206")} после сценария 1.</li>`)}`;
  $("#reset2").onclick = resetDemo;
  const runners = { 1: runS1, 2: runS2, 3: runS3, 4: runS4, 5: runS5 };
  $$("[data-run]").forEach((b) => b.addEventListener("click", async () => {
    const out = $(`#out${b.dataset.run}`);
    b.disabled = true;
    out.innerHTML = '<div class="loading">Выполняется…</div>';
    try { out.innerHTML = await runners[b.dataset.run](); refreshNavCount(); } catch (e) { out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; } finally { b.disabled = false; }
  }));
}

function scenarioCard(n, title, steps) {
  return `<div class="card scenario"><div class="card-head"><h2>${n}. ${esc(title)}</h2><button class="primary sm" data-run="${n}">▶ Выполнить автоматически</button></div>
    <div class="small"><b>Вручную:</b><ol>${steps}</ol></div><div class="out" id="out${n}"></div></div>`;
}

const bySerial = async (serial, kind) => {
  if (kind === "aircraft") { const a = (await aircraftList(true)).find((x) => x.serial === serial); if (!a) throw new Error(`Нет борта ${serial}`); return a; }
  const c = (await GET(`/api/components?q=${encodeURIComponent(serial)}`)).find((x) => x.serial === serial);
  if (!c) throw new Error(`Нет узла ${serial}`);
  return c;
};

function diffTable(rows) {
  return `<table><thead><tr><th>Объект</th><th class="num">До</th><th class="num">После</th><th class="num">Изменение</th></tr></thead><tbody>
    ${rows.map((r) => {
      const dh = r.after.hours - r.before.hours, dc = r.after.cycles - r.before.cycles;
      return `<tr><td>${r.label}</td><td class="num">${hrs(r.before.hours)} · ${r.before.cycles} ц.</td><td class="num">${hrs(r.after.hours)} · ${r.after.cycles} ц.</td>
        <td class="num ${dh || dc ? "delta-up" : "muted"}">${dh || dc ? `+${num(dh, 2)} ч · +${dc} ц.` : "без изменений"}</td></tr>`;
    }).join("")}</tbody></table>`;
}

async function snapshotAircraft(id) {
  const a = await GET(`/api/aircraft/${id}`);
  const rows = [{ key: "ac", label: `<b>${esc(a.name)}</b> (борт)`, v: { hours: a.hours, cycles: a.cycles } }];
  a.configuration.forEach((s) => { if (s.component) rows.push({ key: s.component.id, label: `${esc(s.component.serial)} <span class="small muted">${esc(s.label)}</span>`, v: { hours: s.component.hours, cycles: s.component.cycles } }); });
  return rows;
}
const mergeSnap = (b, a) => b.map((r) => ({ label: r.label, before: r.v, after: (a.find((x) => x.key === r.key) || r).v }));

async function runS1() {
  const ac = await bySerial("SK-M4-001", "aircraft");
  const before = await snapshotAircraft(ac.id);
  const r = await POST("/api/demo-logs/01_SK-M4-001_smena.json/import");
  const after = await snapshotAircraft(ac.id);
  return `${importResultHTML(r).split("<h3>")[0]}${diffTable(mergeSnap(before, after))}
    ${r.summary && r.summary.alerts && r.summary.alerts.length ? `<h3 style="margin-top:12px">Сработавшие напоминания</h3>${r.summary.alerts.map((a) => `<div>${esc(a.entity.serial)} — ${esc(a.rule.name)}: ${a.previous_status ? dueBadge(a.previous_status) + " → " : ""}${dueBadge(a.status)}</div>`).join("")}` : ""}
    ${r.status === "duplicate" ? '<div class="notice warn" style="margin-top:10px">Журнал уже был загружен ранее — для чистого прогона сбросьте демо-данные.</div>' : ""}`;
}

async function runS2() {
  const fc = await GET(`/api/components/${(await bySerial("FC7-0102")).id}`);
  const a3 = await bySerial("SK-M4-003", "aircraft");
  const before = { hours: fc.hours, cycles: fc.cycles };
  const fcDueBefore = fc.due.find((i) => i.rule.code === "FC-CHK");
  let out = "";
  if (fc.location && fc.location.aircraft_id === a3.id) {
    out += '<div class="notice warn">FC7-0102 уже стоит на «Борт 03» — перестановка пропущена.</div>';
  } else {
    const r = await POST(`/api/components/${fc.id}/transfer`, { aircraft_id: a3.id, position: "FC", at: daysAgo(2, 12).toISOString(), reason: "Демо-сценарий: перестановка ПК с «Борт 02» на «Борт 03»", performed_by: "Демо" });
    out += `<div class="notice ok">${esc(r.message)}</div>`;
  }
  await api("PATCH", `/api/aircraft/${a3.id}`, { status: "active" });
  const mid = await GET(`/api/components/${fc.id}`);
  const imp = await POST("/api/demo-logs/02_SK-M4-003_posle_perestanovki.csv/import");
  const after = await GET(`/api/components/${fc.id}`);
  const fcDueAfter = after.due.find((i) => i.rule.code === "FC-CHK");
  return `${out}${importResultHTML(imp).split("<h3>")[0]}
    ${diffTable([{ label: "FC7-0102 — сквозная наработка", before, after: { hours: after.hours, cycles: after.cycles } }])}
    <p class="small">Сразу после перестановки наработка узла: <b>${hrs(mid.hours)}</b> (не изменилась), на новом борту — ${hrs(mid.on_board_hours)}. После журнала на «Борт 03»: на новом борту <b>${hrs(after.on_board_hours)}</b>.</p>
    <h3>Борта в истории узла</h3><table><tbody>${after.stints.map((s) => `<tr><td>${esc(s.aircraft_name)} (${esc(s.position)})</td><td class="small">${fmtDT(s.installed_at)} — ${s.removed_at ? fmtDT(s.removed_at) : "сейчас"}</td><td class="num">${hrs(s.hours)}</td></tr>`).join("")}</tbody></table>
    <h3 style="margin-top:12px">Межсервисный интервал «ТО полётного контроллера» не обнулился при перестановке</h3>
    <div>${fcDueBefore ? dueBadge(fcDueBefore.status) + " " + num(fcDueBefore.driver.used, 1) + " ч" : ""} → ${fcDueAfter ? dueBadge(fcDueAfter.status) + " " + num(fcDueAfter.driver.used, 1) + " ч из " + fcDueAfter.driver.limit : ""}
    · <a href="#/analytics">в разделе «Эффект» это предотвращённый пропуск ТО</a></div>`;
}

async function runS3() {
  const ac = await bySerial("SK-M4-001", "aircraft");
  const steps = [["01_SK-M4-001_smena.json", "Тот же файл ещё раз"], ["03_SK-M4-001_smena_POVTOR.json", "Копия под другим именем"], ["04_SK-M4-001_bortovoi_registrator.csv", "Другая выгрузка тех же полётов + 2 новых"]];
  let html = "<table><thead><tr><th>Загрузка</th><th>Результат</th><th class='num'>Новых / дубл.</th><th class='num'>Налёт борта до → после</th></tr></thead><tbody>";
  for (const [file, title] of steps) {
    const b = (await GET(`/api/aircraft/${ac.id}`)).hours;
    const r = await POST(`/api/demo-logs/${file}/import`);
    const a = (await GET(`/api/aircraft/${ac.id}`)).hours;
    html += `<tr><td><b>${esc(title)}</b><div class="mono small">${esc(file)}</div></td><td><span class="badge ${IMP_ST[r.status]}">${esc(r.status_label)}</span><div class="small muted">${esc((r.messages[0] || "").slice(0, 140))}</div></td>
      <td class="num">${r.flights_new} / ${r.flights_duplicate}</td><td class="num">${hrs(b)} → ${hrs(a)}</td></tr>`;
  }
  return html + "</tbody></table><p class='small muted'>Если первая строка показывает «Импортирован», журнал №1 ещё не загружался (сценарий 1 не выполнен) — это и есть первичная загрузка; последующие строки демонстрируют защиту.</p>";
}

async function runS4() {
  const items = await GET("/api/maintenance/due?status=");
  const watch = [["IMU3-0206", "IMU-CAL", "часы"], ["ESC40-0301", "ESC-DIAG", "циклы"], ["SK-VT2-011", "AC-ANNUAL", "календарь"], ["BVK2-0405", "BVK-SVC", "календарь"], ["ESC40-0318", "ESC-LIFE", "назначенный ресурс"], ["FC7-0102", "FC-CHK", "часы (после перестановки)"]];
  const rows = watch.map(([s, code, kind]) => [items.find((i) => i.entity.serial === s && i.rule.code === code), kind]).filter(([i]) => i);
  return `<table><thead><tr><th>Объект</th><th>Регламент</th><th>Тип порога</th><th>Статус</th><th>Счётчик</th></tr></thead><tbody>
    ${rows.map(([i, kind]) => `<tr><td>${entityLink(i.entity)}</td><td>${esc(i.rule.name)}</td><td>${kind}</td><td>${dueBadge(i.status)}</td><td>${metricHTML(i.driver)}</td></tr>`).join("")}</tbody></table>
    <p class="small"><a href="#/maintenance">Полный список предстоящих и просроченных работ →</a></p>`;
}

async function runS5() {
  const c = await GET(`/api/components/${(await bySerial("BVK2-0405")).id}`);
  const ac = c.location ? await GET(`/api/aircraft/${c.location.aircraft_id}`) : null;
  const it = c.due.find((i) => i.rule.code === "BVK-SVC");
  const r = await POST("/api/maintenance/records", { target: "component", entity_id: c.id, rule_id: it.rule.id, performed_by: "Демо: инженер Смирнов Д. А.",
    work_type: "scheduled", description: "Проверка SMART, чистка радиатора, обновление ПО", result: "Выполнено, замечаний нет" });
  const c2 = await GET(`/api/components/${c.id}`);
  const ac2 = ac ? await GET(`/api/aircraft/${ac.id}`) : null;
  const it2 = c2.due.find((i) => i.rule.code === "BVK-SVC");
  return `<div class="notice ok">${esc(r.message)}</div>
    <table><thead><tr><th></th><th>До</th><th>После</th></tr></thead><tbody>
    <tr><td>Статус работы «${esc(it.rule.name)}»</td><td>${dueBadge(it.status)}<div class="small">${it.metrics.map((m) => `${METRIC[m.metric]}: ${num(m.used, 1)}/${m.limit}`).join("; ")}</div></td>
      <td>${dueBadge(it2.status)}<div class="small">${it2.metrics.map((m) => `${METRIC[m.metric]}: ${num(m.used, 1)}/${m.limit}`).join("; ")}</div></td></tr>
    <tr><td>Сквозная наработка узла</td><td>${hrs(c.hours)}</td><td>${hrs(c2.hours)} <span class="small muted">(не изменилась)</span></td></tr>
    ${ac ? `<tr><td>Допуск ${esc(ac.name)}</td><td>${readiness(ac)}</td><td>${readiness(ac2)}</td></tr>` : ""}
    </tbody></table>`;
}

// ------------------------------------------------------------------ запуск
async function resetDemo() {
  modal("Сброс демо-данных", "<p>Пересоздать базу с демонстрационными данными? Все изменения будут потеряны. Даты демо-данных пересчитаются относительно текущего момента.</p>", {
    okText: "Сбросить",
    onOk: async () => { await POST("/api/demo/reset"); META = null; toast("Демо-данные пересозданы"); refreshNavCount(); reload(); },
  });
}
$("#reset-demo").onclick = resetDemo;
window.addEventListener("hashchange", router);
router();
refreshNavCount();
