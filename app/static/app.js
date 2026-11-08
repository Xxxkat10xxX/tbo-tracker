/* tbo-tracker · ТО по наработке — одностраничный интерфейс без сборки (vanilla JS). */
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
const METRIC = { hours: "Часы", cycles: "Циклы", days: "Календарь", events: "Падения/столкн." };
const EV_BADGE = { fall: "bad", collision: "warn" };
function eventsTable(list) {
  if (!list.length) return '<div class="empty-state">Падений и столкновений не зафиксировано</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Когда</th><th>Событие</th><th class="num">Пиковая перегрузка</th><th>Источник</th><th>Полёт</th></tr></thead><tbody>
    ${list.map((e) => `<tr><td class="nowrap">${fmtDT(e.occurred_at)}</td><td><span class="badge ${EV_BADGE[e.kind]}">${esc(e.kind_label)}</span></td>
      <td class="num">${e.peak_g != null ? num(e.peak_g, 1) + " g" : "—"}</td><td class="small">${esc(e.details || e.source)}</td><td class="small">№${e.flight_id}</td></tr>`).join("")}</tbody></table></div>
    <div class="hint">Падение — невесомость |a| &lt; 0,35 g не менее 0,2 с и затем удар ≥ 3 g; столкновение — удар ≥ 4 g без невесомости (анализ акселерометра ИНС/ПК).</div>`;
}

function metricHTML(m) {
  const cls = m.status === "overdue" ? "bad" : m.status === "due_soon" ? "warn" : "";
  const w = Math.min(100, m.ratio * 100);
  const rem = m.remaining < 0 ? `<b style="color:var(--bad)">превышено на ${num(-m.remaining, 1)} ${m.unit}</b>`
    : m.remaining === 0 ? '<b style="color:var(--bad)">порог достигнут</b>'
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
  $$("[data-close]", bg).forEach((b) => b.addEventListener("click", close));
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

// ------------------------------------------------------------------ фото бортов
const NO_PHOTO = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500"><rect width="800" height="500" fill="#eef1f6"/><g fill="none" stroke="#b9c2d0" stroke-width="10" stroke-linecap="round"><circle cx="300" cy="200" r="52"/><circle cx="500" cy="200" r="52"/><circle cx="300" cy="320" r="52"/><circle cx="500" cy="320" r="52"/><path d="M336 236 L464 284 M464 236 L336 284"/></g><rect x="370" y="230" width="60" height="60" rx="10" fill="#b9c2d0"/></svg>');
function droneImg(a, mode) {
  const src = a.image || NO_PHOTO;
  return `<img class="drone-${mode}" src="${esc(src)}" alt="${esc(a.name)}" loading="lazy">`;
}
// a — борт, которому выбираем фото; без борта окно работает как менеджер пула
async function openPhotoPicker(a, onDone) {
  const pick = !!a;
  const bg = modal(pick ? `Фото борта ${a.name}` : "Пул фото бортов", '<div class="loading">Загрузка…</div>', { wide: true });
  const body = $(".modal-b", bg);
  const done = (msg) => { toast(msg); bg.remove(); AIRCRAFT_CACHE = null; onDone && onDone(); };
  const usedTxt = (u) => (u.used_by && u.used_by.length ? `на бортах: ${u.used_by.join(", ")}` : "не назначено");
  const tile = (u, own) => `<div class="ph-tile ${pick && a.image === u.url ? "sel" : ""} ${pick ? "pickable" : ""}" data-url="${esc(u.url)}">
      <img src="${esc(u.url)}" alt="" loading="lazy">
      ${own ? `<div class="ph-tools"><button class="sm" data-rename="${esc(u.url)}" title="Подпись">✎</button><button class="sm danger" data-del="${esc(u.url)}" title="Удалить из пула">✕</button></div>` : ""}
      <span class="ph-cap"><b>${esc(u.title)}</b><span>${esc(own ? usedTxt(u) : u.industry)}</span></span></div>`;
  async function render() {
    const lib = await GET("/api/drone-images");
    body.innerHTML = `
      <div class="ph-head"><h3>Мой пул фото <span class="muted">(${lib.uploaded.length})</span></h3>
        <label class="btn primary sm" style="margin:0;color:#fff">＋ Добавить фото в пул<input type="file" id="ph-add" accept="image/jpeg,image/png,image/webp" multiple hidden></label></div>
      ${lib.uploaded.length ? `<div class="ph-grid">${lib.uploaded.map((u) => tile(u, true)).join("")}</div>`
        : `<div class="ph-empty">Загрузите свои фото бортов — до 20 файлов за раз (JPEG, PNG, WebP до 8 МБ). Они появятся здесь, и их можно будет назначать любому борту.</div>`}
      <h3 style="margin-top:18px">Набор иллюстраций по отраслям</h3>
      <div class="ph-grid">${lib.presets.map((u) => tile(u, false)).join("")}</div>
      ${pick ? `<div class="ph-actions"><span class="hint">${pick ? "Нажмите на фото, чтобы назначить его борту. Лучше всего — горизонтальный кадр 16:10." : ""}</span>
        ${a.image ? '<button class="sm danger" id="ph-none" style="margin-left:auto">Убрать фото у борта</button>' : ""}</div>` : ""}`;
    if (pick) $$(".ph-tile", body).forEach((el) => el.addEventListener("click", async (e) => {
      if (e.target.closest(".ph-tools")) return;
      try { const r = await api("PUT", `/api/aircraft/${a.id}/image`, { image: el.dataset.url }); done(r.message); } catch (err) { toast(err.message, true); }
    }));
    const none = $("#ph-none", body);
    if (none) none.onclick = async () => { try { const r = await api("PUT", `/api/aircraft/${a.id}/image`, { image: null }); done(r.message); } catch (e) { toast(e.message, true); } };
    $("#ph-add", body).onchange = async (e) => {
      const files = [...e.target.files]; if (!files.length) return;
      const fd = new FormData(); files.forEach((f) => fd.append("files", f));
      try { const r = await api("POST", "/api/drone-images", fd, true); toast(r.message, r.errors.length > 0); render(); } catch (err) { toast(err.message, true); }
    };
    $$("[data-del]", body).forEach((b) => b.addEventListener("click", async () => {
      try { const r = await api("DELETE", `/api/drone-images?url=${encodeURIComponent(b.dataset.del)}`); toast(r.message); render(); } catch (err) { toast(err.message, true); }
    }));
    $$("[data-rename]", body).forEach((b) => b.addEventListener("click", () => {
      const cap = b.closest(".ph-tile").querySelector(".ph-cap b");
      const m = modal("Подпись фото", `<div class="field"><label>Подпись</label><input name="title" value="${esc(cap.textContent)}" maxlength="80"></div>`, {
        onOk: async (root) => { const r = await api("PATCH", "/api/drone-images", { url: b.dataset.rename, title: val(root, "title") }); toast(r.message); render(); },
      });
      $('[name="title"]', m).select();
    }));
  }
  render();
}

// ------------------------------------------------------------------ электронный паспорт
const fmtUid = (u) => u ? `${u.slice(0, 8)}-${u.slice(8, 12)}-${u.slice(12, 16)}-${u.slice(16, 20)}-${u.slice(20, 32)}`.toUpperCase() : "—";
function residualText(r, short) {
  if (!r) return short ? "—" : "назначенный ресурс не задан";
  const parts = [r.hours != null && `${num(Math.max(0, r.hours), 1)} ч`, r.cycles != null && `${num(Math.max(0, r.cycles), 0)} цикл.`, r.days != null && `${num(Math.max(0, r.days), 0)} дн.`].filter(Boolean);
  return parts.join(" / ") || "—";
}
function residualBar(r) {
  if (!r || r.fraction == null) return "";
  const pct = Math.round(r.fraction * 100), cls = r.fraction <= 0 ? "bad" : r.fraction <= 0.1 ? "warn" : "";
  return `<div class="bar" style="margin-top:4px" title="${pct}% ресурса осталось"><span class="${cls}" style="width:${pct}%"></span></div>`;
}
function specRows(specs) {
  return `<table class="spec-edit"><tbody>${(specs.length ? specs : [{ name: "", value: "" }]).map((s) => `<tr>
    <td><input name="spec-name" value="${esc(s.name)}" placeholder="Характеристика"></td><td><input name="spec-value" value="${esc(s.value)}" placeholder="Значение"></td>
    <td style="width:40px"><button class="sm danger" data-spec-rm>✕</button></td></tr>`).join("")}</tbody></table>
    <button class="sm" data-spec-add style="margin-top:6px">+ Характеристика</button>`;
}
function bindSpecs(root) {
  const bind = () => $$("[data-spec-rm]", root).forEach((b) => { b.onclick = () => b.closest("tr").remove(); });
  $("[data-spec-add]", root).onclick = () => {
    $(".spec-edit tbody", root).insertAdjacentHTML("beforeend", `<tr><td><input name="spec-name" placeholder="Характеристика"></td><td><input name="spec-value" placeholder="Значение"></td><td><button class="sm danger" data-spec-rm>✕</button></td></tr>`);
    bind();
  };
  bind();
}
const readSpecs = (root) => $$(".spec-edit tbody tr", root).map((tr) => ({ name: $('[name="spec-name"]', tr).value.trim(), value: $('[name="spec-value"]', tr).value.trim() })).filter((s) => s.name);
function passportHTML(c) {
  const lifeRules = c.due.filter((i) => i.rule.kind === "life").map((i) => `${esc(i.rule.name)}: ${i.metrics.map((m) => `${num(m.limit, 0)} ${m.unit}`).join(" / ")}`);
  return `<div class="card passport"><div class="card-head"><h2>Электронный паспорт</h2><button class="sm" id="edit-pass">✎ Изменить паспорт</button></div>
    <div class="pass-grid">
      <dl class="pass-ids">
        <dt>Уникальный идентификатор</dt><dd class="mono">${fmtUid(c.uid)}</dd>
        <dt>Серийный номер</dt><dd><b>${esc(c.serial)}</b></dd>
        <dt>Тип / модель</dt><dd>${esc(c.type.name)} · ${esc(c.model || "—")}</dd>
        <dt>Децимальный номер</dt><dd>${esc(c.part_number || "—")}</dd>
        <dt>Производитель</dt><dd>${esc(c.manufacturer || "—")}</dd>
        <dt>Дата изготовления</dt><dd>${fmtD(c.manufactured_at)}</dd>
        <dt>Ввод в эксплуатацию</dt><dd>${fmtD(c.in_service_at)}</dd>
        <dt>Версия ПО</dt><dd>${esc(c.firmware || "—")}</dd>
      </dl>
      <div><h3>Основные технические характеристики</h3>
        ${c.specs.length ? `<table class="spec"><tbody>${c.specs.map((s) => `<tr><td class="muted">${esc(s.name)}</td><td>${esc(s.value)}</td></tr>`).join("")}</tbody></table>` : '<div class="muted small">Не заполнены — «✎ Изменить паспорт»</div>'}</div>
      <div><h3>Ресурс</h3>
        <div class="small muted">Установленный эксплуатационный ресурс</div>
        <div class="small">${lifeRules.length ? lifeRules.join("<br>") : "не задан"}</div>
        <div class="small muted" style="margin-top:8px">Наработка с начала эксплуатации</div><div><b>${hrs(c.hours)}</b> · ${num(c.cycles, 0)} цикл.</div>
        <div class="small muted" style="margin-top:8px">Остаточный ресурс${c.residual && c.residual.limit_by ? ` (по «${esc(c.residual.limit_by)}»)` : ""}</div>
        <div class="big-res">${residualText(c.residual)}</div>${residualBar(c.residual)}
      </div></div></div>`;
}
function openPassportModal(c, onDone) {
  const bg = modal(`Электронный паспорт ${c.serial}`, `
    <div class="row3"><div class="field"><label>Модель</label><input name="model" value="${esc(c.model || "")}"></div>
    <div class="field"><label>Децимальный номер</label><input name="part_number" value="${esc(c.part_number || "")}"></div>
    <div class="field"><label>Версия ПО</label><input name="firmware" value="${esc(c.firmware || "")}"></div></div>
    <div class="row2"><div class="field"><label>Производитель</label><input name="manufacturer" value="${esc(c.manufacturer || "")}"></div>
    <div class="field"><label>Дата изготовления</label><input type="date" name="manufactured_at" value="${c.manufactured_at ? c.manufactured_at.slice(0, 10) : ""}"></div></div>
    <h3>Основные технические характеристики</h3>${specRows(c.specs)}
    <div class="hint">Серийный номер, уникальный идентификатор и дата ввода в эксплуатацию — неизменяемые реквизиты паспорта. Ресурс — кнопка «⚙ Ресурс».</div>`, {
    wide: true, okText: "Сохранить паспорт",
    onOk: async (root) => {
      const md = val(root, "manufactured_at");
      await api("PATCH", `/api/components/${c.id}`, { model: orNull(val(root, "model")), part_number: orNull(val(root, "part_number")),
        firmware: orNull(val(root, "firmware")), manufacturer: orNull(val(root, "manufacturer")),
        manufactured_at: md ? md + "T00:00:00Z" : null, specs: readSpecs(root) });
      toast("Паспорт сохранён"); onDone && onDone();
    },
  });
  bindSpecs(bg);
}

// ------------------------------------------------------------------ ресурс и свои типы
const RES_HINT = "Максимальный износ конкретного экземпляра (по паспорту, формуляру, решению эксплуатанта). Считается от начала эксплуатации, работами не обнуляется; срабатывает первый достигнутый порог. Пустое поле — порог не задан.";
function resourceFields(r = {}) {
  return `<div class="row3"><div class="field"><label>Ресурс, ч</label><input type="number" min="0" step="0.1" name="resource_hours" value="${r.hours ?? ""}" placeholder="не задан"></div>
    <div class="field"><label>Ресурс, циклы</label><input type="number" min="0" name="resource_cycles" value="${r.cycles ?? ""}" placeholder="не задан"></div>
    <div class="field"><label>Ресурс, дни</label><input type="number" min="0" name="resource_days" value="${r.days ?? ""}" placeholder="не задан"></div></div>
    <div class="hint" style="margin-top:-6px">${RES_HINT}</div>`;
}
function readResource(root) {
  const n = (k, int) => { const v = val(root, k); if (v === "" || v === null) return null; const x = int ? parseInt(v, 10) : parseFloat(v); if (!(x > 0)) throw new Error("Ресурс должен быть больше нуля (или оставьте поле пустым)"); return x; };
  return { resource_hours: n("resource_hours"), resource_cycles: n("resource_cycles", 1), resource_days: n("resource_days", 1) };
}
function resourceText(r) {
  const parts = [r && r.hours && `${num(r.hours, 1)} ч`, r && r.cycles && `${num(r.cycles, 0)} цикл.`, r && r.days && `${num(r.days, 0)} дн.`].filter(Boolean);
  return parts.length ? parts.join(" / ") : null;
}
function openResourceModal({ kind, entity, onDone }) {
  const name = kind === "aircraft" ? `борта ${entity.name}` : `узла ${entity.serial}`;
  modal(`Индивидуальный ресурс ${name}`, `${resourceFields(entity.resource)}
    <div class="notice small" style="margin-top:12px">Ресурс появится в «ТО и уведомления» как «Индивидуальный ресурс (паспорт / формуляр)»: «Приближается» за 10 % до порога, «Требуется» — по достижении. Типовые регламенты продолжают действовать.</div>`, {
    okText: "Сохранить",
    onOk: async (root) => {
      await api("PATCH", `/api/${kind === "aircraft" ? "aircraft" : "components"}/${entity.id}`, readResource(root));
      toast("Ресурс сохранён"); refreshNavCount(); onDone && onDone();
    },
  });
}
// «+ Новый тип узла…» прямо из выпадающего списка
async function quickComponentType(select, onCreated) {
  modal("Новый тип узла", `<div class="row2"><div class="field"><label>Код (латиница, без пробелов)</label><input name="code" placeholder="gnss"></div>
    <div class="field"><label>Название</label><input name="name" placeholder="GNSS-приёмник"></div></div>
    <div class="field"><label>Описание</label><input name="description"></div>
    <div class="hint">Чтобы узлы нового типа можно было устанавливать на борт, добавьте для него позицию: «Узлы и склад» → «⊞ Свои типы узлов».</div>`, {
    okText: "Создать тип",
    onOk: async (root) => {
      const r = await POST("/api/component-types", { code: val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")) });
      toast(r.message); META = null; const m = await meta();
      onCreated(m, r.id);
    },
  });
  select.value = select.dataset.prev || select.options[0].value;
}
function typeOptions(list, cur, extra) {
  return list.map((t) => `<option value="${t.id}" ${t.id === cur ? "selected" : ""}>${esc(t.name)}</option>`).join("") + extra;
}

// ------------------------------------------------------------------ Миссии: маршрут и автономный полёт
const R_EARTH = 6371000;
const toXY = (h, lat, lon) => [((lon - h.lon) * Math.PI / 180) * R_EARTH * Math.cos(h.lat * Math.PI / 180), ((lat - h.lat) * Math.PI / 180) * R_EARTH];
const toLL = (h, x, y) => [h.lat + (y / R_EARTH) * 180 / Math.PI, h.lon + (x / (R_EARTH * Math.cos(h.lat * Math.PI / 180))) * 180 / Math.PI];
const PHASE_RU = { takeoff: "Взлёт", mission: "Полёт по маршруту", hold: "Удержание на точке", rtl: "Возврат домой", land: "Посадка", crash: "Аварийное завершение" };

const fmtDur = (s) => `${Math.floor(s / 60)} мин ${String(Math.round(s % 60)).padStart(2, "0")} с`;

async function pageMissions() {
  const list = await GET("/api/missions");
  view().innerHTML = `<div class="page-head"><div><h1>Миссии и автономный полёт</h1>
    <div class="sub">Маршрут из точек (WGS-84, высота над точкой взлёта) → автономный полёт в симуляторе автопилота → полёт в журнале, наработка, датчики. Обмен с QGroundControl, ArduPilot, PX4.</div></div>
    <div class="actions"><label class="btn" style="margin:0">⇪ Импорт .plan / .waypoints<input type="file" id="m-imp" accept=".plan,.waypoints,.txt,.json" hidden></label>
    <button class="primary" id="m-new">+ Новая миссия</button></div></div>
    <div class="card">${list.length ? `<div class="table-wrap"><table><thead><tr><th>Миссия</th><th>Борт</th><th class="num">Точек</th><th class="num">Маршрут</th><th class="num">Время (оценка)</th><th>Последний полёт</th></tr></thead><tbody>
      ${list.map((m) => `<tr><td><a href="#/missions/${m.id}"><b>${esc(m.name)}</b></a><div class="small muted">${esc(m.notes || "")}</div></td>
        <td>${m.aircraft_id ? `<a href="#/aircraft/${m.aircraft_id}">${esc(m.aircraft_name)}</a>` : '<span class="muted">не назначен</span>'}</td>
        <td class="num">${m.waypoints.length}</td><td class="num">${m.stats ? num(m.stats.distance_m / 1000, 2) + " км" : "—"}</td>
        <td class="num">${m.stats ? fmtDur(m.stats.duration_s) : "—"}</td>
        <td>${m.last_run ? `<span class="badge ${m.last_run.status === "completed" ? "ok" : "bad"}">${m.last_run.status === "completed" ? "выполнена" : "прервана"}</span> <span class="small muted">${fmtDT(m.last_run.ended_at)}</span>` : '<span class="muted small">не выполнялась</span>'}</td></tr>`).join("")}
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
  const st = { ...m, waypoints: m.waypoints.map((w) => ({ ...w })) };
  const W = 760, H = 520;
  let view = null;         // {cx, cy, span} в метрах
  const home = () => ({ lat: +st.home_lat, lon: +st.home_lon });
  const fit = () => {
    const pts = [[0, 0]].concat(st.waypoints.map((w) => toXY(home(), w.lat, w.lon)));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const span = Math.max(300, (Math.max(...xs) - Math.min(...xs)) * 1.3, (Math.max(...ys) - Math.min(...ys)) * 1.3 * W / H);
    view = { cx, cy, span };
  };
  fit();
  const sx = (x) => (x - view.cx) / view.span * W + W / 2;
  const sy = (y) => H / 2 - (y - view.cy) / view.span * W;
  const wx = (px) => (px - W / 2) / W * view.span + view.cx;
  const wy = (py) => (H / 2 - py) / W * view.span + view.cy;

  view_().innerHTML = `<div class="crumbs"><a href="#/missions">Миссии</a> / ${esc(m.name)}</div>
    <div class="page-head"><div><h1>${esc(m.name)}</h1><div class="sub">Щелчок по карте — новая точка, перетаскивание — перенос точки. Координаты WGS-84, высоты относительно точки взлёта.</div></div>
    <div class="actions"><button id="m-save" class="primary">Сохранить</button><button id="m-exp-plan">⇩ .plan (ArduPilot)</button><button id="m-exp-px4">⇩ .plan (PX4)</button><button id="m-exp-wpl">⇩ .waypoints</button><button class="danger" id="m-del">Удалить</button></div></div>
    <div class="mission-grid">
      <div class="card" style="padding:12px"><div class="map-wrap"><svg id="map" viewBox="0 0 ${W} ${H}"></svg>
        <div class="map-tools"><button class="sm" id="z-in">+</button><button class="sm" id="z-out">−</button><button class="sm" id="z-fit">Вписать</button></div>
        <div class="telemetry" id="telemetry" hidden></div></div>
        <div class="small muted" id="m-stats" style="margin-top:8px"></div></div>
      <div>
        <div class="card"><h2>Параметры миссии</h2>
          <div class="field"><label>Название</label><input name="name" value="${esc(st.name)}"></div>
          <div class="row2"><div class="field"><label>Борт</label><select name="aircraft_id"><option value="">— не назначен —</option>${fleet.map((a) => `<option value="${a.id}" ${a.id === st.aircraft_id ? "selected" : ""}>${esc(a.name)} · ${esc(a.serial)}</option>`).join("")}</select></div>
          <div class="field"><label>Крейсерская скорость, м/с</label><input type="number" min="0.5" max="40" step="0.5" name="speed_ms" value="${st.speed_ms}"></div></div>
          <div class="row3"><div class="field"><label>Точка взлёта: широта</label><input type="number" step="0.000001" name="home_lat" value="${st.home_lat}"></div>
          <div class="field"><label>долгота</label><input type="number" step="0.000001" name="home_lon" value="${st.home_lon}"></div>
          <div class="field"><label>Высота взлёта, м</label><input type="number" min="2" max="500" name="takeoff_alt" value="${st.takeoff_alt}"></div></div>
          <label><input type="checkbox" name="return_home" style="width:auto" ${st.return_home ? "checked" : ""}> Возврат в точку взлёта (RTL) и посадка</label></div>
        <div class="card"><div class="card-head"><h2>Точки маршрута</h2><span class="small muted" id="wp-count"></span></div><div id="wp-table"></div></div>
        <div class="card"><h2>Автономный полёт</h2>
          <div class="row3"><div class="field"><label>Ветер, м/с</label><input type="number" min="0" max="20" step="0.5" name="wind_ms" value="3"></div>
          <div class="field"><label>Ветер откуда, °</label><input type="number" min="0" max="360" name="wind_from" value="250"></div>
          <div class="field"><label>Отказ (проверка)</label><select name="fault"><option value="">без отказов</option></select></div></div>
          <button class="primary" id="m-run">▶ Выполнить автономный полёт</button>
          <div class="hint">Перед вылетом выполняется предполётная проверка допуска борта (комплектность, просроченные работы). Полёт выполняет симулятор автопилота; результат записывается в журнал и начисляет наработку.</div></div>
      </div></div>
    <div class="card" id="run-result" hidden></div>
    <div class="card"><h2>История выполнения</h2><div id="runs"></div></div>`;

  const map = $("#map");
  function drawMap(track, dronePos) {
    const h = home();
    const step = [10, 20, 50, 100, 200, 500, 1000, 2000].find((s) => view.span / s <= 12) || 5000;
    let g = "";
    const x0 = Math.floor(wx(0) / step) * step, x1 = wx(W), y0 = Math.floor(wy(H) / step) * step, y1 = wy(0);
    for (let x = x0; x <= x1; x += step) g += `<line x1="${sx(x)}" y1="0" x2="${sx(x)}" y2="${H}" class="grid"/>`;
    for (let y = y0; y <= y1; y += step) g += `<line x1="0" y1="${sy(y)}" x2="${W}" y2="${sy(y)}" class="grid"/>`;
    const pts = st.waypoints.map((w) => toXY(h, w.lat, w.lon));
    const path = [[0, 0]].concat(pts).map((p) => `${sx(p[0])},${sy(p[1])}`).join(" ");
    const back = pts.length && st.return_home ? `<line x1="${sx(pts[pts.length - 1][0])}" y1="${sy(pts[pts.length - 1][1])}" x2="${sx(0)}" y2="${sy(0)}" class="rtl"/>` : "";
    let tr = "";
    if (track && track.length) tr = `<polyline class="flown" points="${track.map((p) => { const q = toXY(h, p[1], p[2]); return `${sx(q[0])},${sy(q[1])}`; }).join(" ")}"/>`;
    const sb = step;
    map.innerHTML = `<rect width="${W}" height="${H}" class="map-bg"/>${g}
      <polyline class="route" points="${path}"/>${back}${tr}
      <g transform="translate(${sx(0)},${sy(0)})"><rect x="-11" y="-11" width="22" height="22" rx="4" class="home"/><text y="5" class="home-t">H</text></g>
      ${pts.map((p, i) => `<g class="wp" data-i="${i}" transform="translate(${sx(p[0])},${sy(p[1])})"><circle r="13"/><text y="4.5">${i + 1}</text>
        <text class="wp-alt" y="-19">${num(st.waypoints[i].alt, 0)} м${+st.waypoints[i].hold_s ? ` · ${st.waypoints[i].hold_s} с` : ""}</text></g>`).join("")}
      ${dronePos ? `<g transform="translate(${dronePos[0]},${dronePos[1]})"><circle r="9" class="drone"/><circle r="17" class="drone-ring"/></g>` : ""}
      <g transform="translate(${W - 30},34)"><path d="M0,-16 L7,6 L0,1 L-7,6 Z" class="north"/><text y="22" class="north-t">С</text></g>
      <g transform="translate(16,${H - 18})"><line x1="0" y1="0" x2="${sb / view.span * W}" y2="0" class="scale"/><text x="0" y="-6" class="scale-t">${sb >= 1000 ? sb / 1000 + " км" : sb + " м"}</text></g>`;
    bindDrag();
  }
  let dragging = null, suppressClick = false;
  const svgPt = (e) => { const r = map.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H]; };
  function bindDrag() {
    $$(".wp", map).forEach((g) => g.addEventListener("pointerdown", (e) => { e.stopPropagation(); dragging = +g.dataset.i; map.setPointerCapture(e.pointerId); }));
  }
  map.addEventListener("pointermove", (e) => {
    if (dragging === null) return;
    const [px, py] = svgPt(e); const [lat, lon] = toLL(home(), wx(px), wy(py));
    st.waypoints[dragging].lat = +lat.toFixed(7); st.waypoints[dragging].lon = +lon.toFixed(7);
    drawMap(); drawStats();
  });
  map.addEventListener("pointerup", () => { if (dragging !== null) { dragging = null; suppressClick = true; drawTable(); } });
  map.addEventListener("click", (e) => {
    if (suppressClick) { suppressClick = false; return; }
    if (e.target.closest(".wp")) return;
    const [px, py] = svgPt(e); const [lat, lon] = toLL(home(), wx(px), wy(py));
    const alt = st.waypoints.length ? st.waypoints[st.waypoints.length - 1].alt : +st.takeoff_alt;
    st.waypoints.push({ lat: +lat.toFixed(7), lon: +lon.toFixed(7), alt, hold_s: 0 });
    drawAll();
  });
  function drawTable() {
    $("#wp-count").textContent = `${st.waypoints.length} точек`;
    $("#wp-table").innerHTML = st.waypoints.length ? `<table class="wp-table"><thead><tr><th>№</th><th>Широта</th><th>Долгота</th><th>Высота, м</th><th>Удерж., с</th><th></th></tr></thead><tbody>
      ${st.waypoints.map((w, i) => `<tr><td>${i + 1}</td><td><input data-k="lat" data-i="${i}" type="number" step="0.000001" value="${w.lat}"></td><td><input data-k="lon" data-i="${i}" type="number" step="0.000001" value="${w.lon}"></td>
        <td><input data-k="alt" data-i="${i}" type="number" min="2" max="500" value="${w.alt}"></td><td><input data-k="hold_s" data-i="${i}" type="number" min="0" max="600" value="${w.hold_s || 0}"></td>
        <td><button class="sm danger" data-rm="${i}">✕</button></td></tr>`).join("")}</tbody></table>` : '<div class="empty-state">Щёлкните по карте, чтобы добавить первую точку</div>';
    $$("#wp-table input").forEach((inp) => inp.addEventListener("change", () => { st.waypoints[+inp.dataset.i][inp.dataset.k] = +inp.value; drawMap(); drawStats(); }));
    $$("#wp-table [data-rm]").forEach((b) => b.addEventListener("click", () => { st.waypoints.splice(+b.dataset.rm, 1); drawAll(); }));
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
      const s = await POST("/api/missions/estimate", { home_lat: +st.home_lat, home_lon: +st.home_lon, takeoff_alt: +st.takeoff_alt,
        speed_ms: +st.speed_ms, return_home: st.return_home, waypoints: st.waypoints.map((w) => ({ lat: +w.lat, lon: +w.lon, alt: +w.alt, hold_s: +w.hold_s || 0 })) })
        .catch(() => null);
      if (!s || my !== statsSeq || !$("#m-stats")) return;
      $("#m-stats").innerHTML = `Маршрут <b>${num(s.distance_m / 1000, 2)} км</b> · оценка времени <b>${fmtDur(s.duration_s)}</b> · точек ${s.waypoints} · макс. высота ${num(s.max_alt_m, 0)} м`;
    }, 120);
  }
  function drawAll() { drawMap(); drawTable(); drawStats(); }
  const readForm = () => {
    const r = view_();
    st.name = val(r, "name"); st.aircraft_id = val(r, "aircraft_id") ? +val(r, "aircraft_id") : null; st.speed_ms = +val(r, "speed_ms");
    st.home_lat = +val(r, "home_lat"); st.home_lon = +val(r, "home_lon"); st.takeoff_alt = +val(r, "takeoff_alt");
    st.return_home = $('[name="return_home"]').checked;
  };
  ["speed_ms", "home_lat", "home_lon", "takeoff_alt", "return_home"].forEach((n) => $(`[name="${n}"]`).addEventListener("change", () => { readForm(); drawAll(); }));
  async function save() {
    readForm();
    const body = { name: st.name, aircraft_id: st.aircraft_id, home_lat: st.home_lat, home_lon: st.home_lon, takeoff_alt: st.takeoff_alt,
      speed_ms: st.speed_ms, return_home: st.return_home, waypoints: st.waypoints, notes: st.notes };
    await api("PUT", `/api/missions/${id}`, body);
  }
  $("#m-save").onclick = async () => { try { await save(); toast("Миссия сохранена"); } catch (e) { toast(e.message, true); } };
  const dl = (fmt, fw) => async () => { try { await save(); location.href = `/api/missions/${id}/export?fmt=${fmt}&firmware=${fw || "ardupilot"}`; } catch (e) { toast(e.message, true); } };
  $("#m-exp-plan").onclick = dl("plan", "ardupilot"); $("#m-exp-px4").onclick = dl("plan", "px4"); $("#m-exp-wpl").onclick = dl("waypoints");
  $("#m-del").onclick = () => modal("Удаление миссии", "<p>Удалить миссию? Выполненные полёты останутся в журнале.</p>", { okText: "Удалить", onOk: async () => { await api("DELETE", `/api/missions/${id}`); location.hash = "#/missions"; } });
  $("#z-in").onclick = () => { view.span /= 1.5; drawMap(); };
  $("#z-out").onclick = () => { view.span *= 1.5; drawMap(); };
  $("#z-fit").onclick = () => { fit(); drawMap(); };

  function drawRuns(runs) {
    $("#runs").innerHTML = runs.length ? `<table><thead><tr><th>Окончание</th><th>Борт</th><th>Итог</th><th class="num">Длительность</th><th class="num">Путь</th><th class="num">Макс. боковое откл.</th><th>Журнал</th></tr></thead><tbody>
      ${runs.map((r) => `<tr><td class="nowrap">${fmtDT(r.ended_at)}</td><td><a href="#/aircraft/${r.aircraft_id}">${esc(r.summary.aircraft_name || "")}</a></td>
        <td><span class="badge ${r.status === "completed" ? "ok" : "bad"}">${r.status === "completed" ? "Выполнена" : "Прервана"}</span></td>
        <td class="num">${fmtDur(r.summary.duration_s || 0)}</td><td class="num">${num((r.summary.distance_m || 0) / 1000, 2)} км</td><td class="num">${num(r.summary.max_xte_m || 0, 1)} м</td>
        <td class="small">импорт №${r.import_id}</td></tr>`).join("")}</tbody></table>` : '<div class="empty-state">Миссия ещё не выполнялась</div>';
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
}
const view_ = () => $("#view");

// ------------------------------------------------------------------ Перестановка узлов: drag & drop
function moveResultHTML(r, closable = true) {
  const loc = (c) => (c.location ? `${esc(c.location.aircraft_name)} · ${esc(c.location.position)}` : esc(c.status_label));
  const delta = (a, b, d) => (Math.abs(b - a) > 0.005 ? ` <span class="${b < a ? "neg" : "pos"}">(${b > a ? "+" : ""}${num(b - a, d)})</span>` : "");
  return `<div class="card move-res"><div class="card-head"><h2>✓ ${esc(r.title)}</h2>${closable ? '<button class="sm" id="mr-close">Скрыть</button>' : ""}</div>
    <div class="small muted" style="margin-bottom:8px">${r.messages.map(esc).join(" · ")}</div>
    <div class="table-wrap"><table><thead><tr><th>Узел (паспорт)</th><th>Местоположение: было → стало</th><th class="num">Наработка, ч</th><th class="num">Циклы</th><th>Остаточный ресурс</th><th>Регламент</th></tr></thead><tbody>
    ${r.components.map(({ before: b, after: a }) => `<tr><td><a href="#/components/${a.id}"><b>${esc(a.serial)}</b></a><div class="small muted mono">${fmtUid(a.uid)}</div></td>
      <td>${loc(b)} → <b>${loc(a)}</b></td><td class="num">${num(a.hours, 2)}${delta(b.hours, a.hours, 2)}</td><td class="num">${num(a.cycles, 0)}${delta(b.cycles, a.cycles, 0)}</td>
      <td>${residualText(a.residual, true)}${residualBar(a.residual)}</td><td>${dueBadge(a.maintenance ? a.maintenance.status : "ok")}</td></tr>`).join("")}</tbody></table></div>
    <div class="hint">История установок в паспорте дополнена; наработка = исходная + налёт бортов за периоды установки + внебортовая. Если дата операции в прошлом, полёты после неё переотнесены.</div></div>`;
}
const nodesSwitch = (active) => `<div class="seg-switch" role="tablist">
  <a href="#/board" class="${active === "board" ? "active" : ""}" role="tab">⇄ Перестановка</a>
  <a href="#/components" class="${active === "components" ? "active" : ""}" role="tab">▣ Узлы и склад</a></div>`;
async function pageBoard() {
  let data = await GET("/api/board");
  let picked = null;           // выбор щелчком (для тачскринов и без мыши)
  let lastMove = null;
  const chip = (c, from) => {
    const st = c.maintenance && c.maintenance.status !== "ok" ? c.maintenance.status : "";
    const movable = c.status === "installed" || c.status === "stock";
    return `<div class="chip-c ${st} ${movable ? "" : "locked"} ${lastMove && lastMove.id === c.id ? "flash" : ""}" ${movable ? 'draggable="true"' : ""}
      data-cid="${c.id}" data-type="${esc(c.type.code)}" data-from="${esc(from)}" title="${movable ? "Перетащите на позицию другого борта, в резерв или в ремонт" : "Узел в ремонте — установка недоступна"}">
      <div class="cc-top"><b>${esc(c.serial)}</b>${st ? `<span class="cc-st ${st}">${st === "overdue" ? "ТО просрочено" : "скоро ТО"}</span>` : ""}</div>
      <div class="cc-sub">${hrs(c.hours)} · ${num(c.cycles, 0)} цикл.</div>
      ${c.residual ? `<div class="cc-res">ост. ${residualText(c.residual, true)}</div>${residualBar(c.residual)}` : ""}</div>`;
  };
  function render() {
    const spareStock = data.spare.filter((c) => c.status === "stock"), spareRep = data.spare.filter((c) => c.status === "maintenance");
    view().innerHTML = `${nodesSwitch("board")}<div class="page-head"><div><h1>Перестановка узлов</h1>
        <div class="sub">Перетащите узел на позицию соседнего борта, в резерв или в ремонт. Совместимые позиции подсвечиваются; занятая позиция — обмен узлами.
        После операции наработка и паспорт узла пересчитываются по истории установок (налёт до даты операции остаётся за прежним бортом).</div></div></div>
      <div id="move-result"></div>
      <div class="board">
        <div class="board-side">
          <div class="card bcol dz" data-drop="stock"><h3>Резерв (склад) <span class="muted">${spareStock.length}</span></h3>
            <div class="chips-col">${spareStock.map((c) => `<div class="small muted">${esc(c.type.name)}</div>${chip(c, "stock")}`).join("") || '<div class="empty-state small">пусто</div>'}</div></div>
          <div class="card bcol dz" data-drop="maintenance"><h3>Ремонт / обслуживание <span class="muted">${spareRep.length}</span></h3>
            <div class="chips-col">${spareRep.map((c) => chip(c, "maintenance")).join("") || '<div class="empty-state small">пусто</div>'}</div></div>
        </div>
        <div class="board-fleet">${data.aircraft.map((a) => `<div class="card bac">
          <div class="bac-head">${droneImg(a, "thumb")}<div><a href="#/aircraft/${a.id}"><b>${esc(a.name)}</b></a> <span class="small muted">${esc(a.serial)}</span>
            <div class="small">${readiness(a)} · ${hrs(a.hours)} · ${a.slots_filled}/${a.slots_total}</div></div></div>
          ${a.slots.map((sl) => `<div class="bslot dz" data-drop="slot" data-aid="${a.id}" data-pos="${esc(sl.position)}" data-type="${esc(sl.component_type)}">
            <div class="bslot-l"><b>${esc(sl.position)}</b><span>${esc(sl.label)}</span></div>
            ${sl.component ? chip(sl.component, `${a.id}|${sl.position}`) : `<div class="bslot-empty">${sl.required ? "не укомплектовано" : "пусто"}</div>`}</div>`).join("")}
        </div>`).join("")}</div>
      </div>`;
    if (lastMove) {
      $("#move-result").innerHTML = lastMove.html;
      $("#mr-close").onclick = () => { lastMove = null; $("#move-result").innerHTML = ""; };
    }
    bind();
  }
  const clearHi = () => $$(".dz").forEach((d) => d.classList.remove("ok-target", "no-target", "over"));
  const highlight = (type, from) => $$(".dz").forEach((d) => {
    const ok = d.dataset.drop === "slot" ? d.dataset.type === type && `${d.dataset.aid}|${d.dataset.pos}` !== from : d.dataset.drop !== from;
    d.classList.add(ok ? "ok-target" : "no-target");
  });
  function bind() {
    $$(".chip-c[draggable]").forEach((el) => {
      el.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", el.dataset.cid); e.dataTransfer.effectAllowed = "move";
        picked = null; el.classList.add("dragging"); highlight(el.dataset.type, el.dataset.from);
      });
      el.addEventListener("dragend", () => { el.classList.remove("dragging"); clearHi(); });
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const target = el.closest(".dz.ok-target");
        if (picked && picked !== +el.dataset.cid && target) { const cid = picked; picked = null; clearHi(); askMove(cid, target); return; }
        clearHi(); $$(".chip-c.picked").forEach((x) => x.classList.remove("picked"));
        if (picked === +el.dataset.cid) { picked = null; return; }
        picked = +el.dataset.cid; el.classList.add("picked"); highlight(el.dataset.type, el.dataset.from);
      });
    });
    $$(".dz").forEach((d) => {
      d.addEventListener("dragover", (e) => { if (d.classList.contains("ok-target")) { e.preventDefault(); d.classList.add("over"); } });
      d.addEventListener("dragleave", () => d.classList.remove("over"));
      d.addEventListener("drop", (e) => { e.preventDefault(); const cid = +e.dataTransfer.getData("text/plain"); clearHi(); askMove(cid, d); });
      d.addEventListener("click", () => { if (picked && d.classList.contains("ok-target")) { const cid = picked; picked = null; clearHi(); askMove(cid, d); } });
    });
  }
  // Операцию определяет и выполняет сервер: клиент сообщает только «какой узел куда»
  async function askMove(cid, d) {
    const target = d.dataset.drop === "slot" ? { kind: "slot", aircraft_id: +d.dataset.aid, position: d.dataset.pos } : { kind: d.dataset.drop };
    let pl;
    try { pl = await POST("/api/board/plan", { component_id: cid, target }); } catch (e) { toast(e.message, true); return; }
    const defLocal = toLocalInput(new Date(pl.default_at));
    modal("Подтверждение операции с узлом", `<p><b>${esc(pl.title)}</b></p>
      <ol class="small">${pl.steps.map((s) => `<li>${esc(s.text)}</li>`).join("")}</ol>
      <div class="row2"><div class="field"><label>Дата и время операции</label><input type="datetime-local" name="at" value="${defLocal}"></div>
      <div class="field"><label>Исполнитель</label><input name="by" placeholder="ФИО"></div></div>
      <div class="field"><label>Основание</label><input name="reason" placeholder="напр. замена по результатам диагностики"></div>
      <div class="hint">Все шаги выполняются на сервере одной операцией: либо применяются все, либо ни один. Если указать прошедшую дату,
        полёты после неё будут отнесены к новой установке, и наработка узлов пересчитается. Не раньше: ${fmtDT(pl.min_at)}.</div>`, {
      okText: "Выполнить", onOk: async (root) => {
        const raw = val(root, "at");
        if (!raw) throw new Error("Укажите дату и время операции");
        const r = await POST("/api/board/move", { component_id: cid, target, at: raw === defLocal ? null : fromLocalInput(raw),
          reason: val(root, "reason") || null, performed_by: val(root, "by") || null });
        data = await GET("/api/board");
        lastMove = { id: r.moved_id, html: moveResultHTML(r) };
        AIRCRAFT_CACHE = null; refreshNavCount(); render(); toast("Операция выполнена, данные пересчитаны");
      },
    });
  }
  render();
}

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
      <div class="card"><div class="card-head"><h2>Резервные копии</h2><div class="actions"><label class="btn sm" style="margin:0">⇪ Загрузить .zip<input type="file" id="bk-up" accept=".zip" hidden></label>
        <button class="primary sm" id="bk-new">+ Создать копию</button></div></div><div id="srv-backups"><div class="loading">Загрузка…</div></div></div>
    </div>
    <div class="card"><h2>Командная строка (SSH)</h2>
      <div class="small muted" style="margin-bottom:8px">Установка, обновление, пароль и восстановление при недоступном веб-интерфейсе — утилита <span class="mono">tbo-ctl</span> (см. docs/RASPBERRY_PI.md).</div>
      <table class="small"><tbody>
        ${[["sudo tbo-ctl status", "состояние сервиса, температура, питание, адреса"], ["sudo tbo-ctl logs", "журнал сервиса в реальном времени"],
          ["sudo tbo-ctl backup", "резервная копия сейчас"], ["sudo tbo-ctl restore ИМЯ.zip", "восстановление (сервис останавливается на время операции)"],
          ["sudo tbo-ctl update", "обновление из git с копией перед обновлением"], ["sudo tbo-ctl password", "сменить пароль входа"],
          ["sudo tbo-ctl port 8080", "сменить порт"], ["sudo tbo-ctl restart", "перезапуск сервиса"]].map(([c, d]) => `<tr><td class="mono nowrap">${c}</td><td>${d}</td></tr>`).join("")}
      </tbody></table></div>`;

  async function loadStatus() {
    const st = await GET("/api/server/status");
    if (!$("#srv-status")) return;
    const h = st.host, m = st.memory, sg = st.storage, d = st.data, sc = st.scheduler;
    const memUsed = m.total ? 1 - m.available / m.total : 0, diskUsed = sg.total ? sg.used / sg.total : 0;
    const bar = (k) => `<div class="bar" style="margin-top:4px"><span class="${k > 0.9 ? "bad" : k > 0.75 ? "warn" : ""}" style="width:${Math.round(k * 100)}%"></span></div>`;
    $("#srv-status").innerHTML = `${st.warnings.map((w) => `<div class="notice warn">⚠ ${esc(w)}</div>`).join("")}
      <div class="kpis">
        <div class="kpi"><div class="v" style="font-size:16px">${esc(h.model)}</div><div class="l">${esc(h.os)} · ${esc(h.arch)} · ${h.cpus} ядра</div></div>
        <div class="kpi ${h.cpu_temp_c >= 75 ? "bad" : h.cpu_temp_c >= 65 ? "warn" : "ok"}"><div class="v">${h.cpu_temp_c != null ? num(h.cpu_temp_c, 1) + " °C" : "—"}</div><div class="l">температура процессора</div></div>
        <div class="kpi"><div class="v">${h.load ? num(h.load[0], 2) : "—"}</div><div class="l">загрузка (1 мин) · работает ${fmtUptime(h.uptime_s)}</div></div>
        <div class="kpi"><div class="v">${fmtBytes(m.available)}</div><div class="l">свободно ОЗУ из ${fmtBytes(m.total)}</div>${bar(memUsed)}</div>
        <div class="kpi ${sg.kind === "microSD" ? "warn" : ""}"><div class="v">${fmtBytes(sg.free)}</div><div class="l">свободно на ${esc(sg.kind)} из ${fmtBytes(sg.total)}</div>${bar(diskUsed)}</div>
        <div class="kpi ${h.throttled && h.throttled.flags.length ? "bad" : "ok"}"><div class="v" style="font-size:16px">${h.throttled ? (h.throttled.flags.length ? "есть замечания" : "в норме") : "—"}</div><div class="l">питание и охлаждение (vcgencmd)</div></div>
      </div>
      <div class="grid g2">
        <div class="card"><h2>Доступ</h2>
          <p>Откройте с любого компьютера, планшета или телефона в той же сети:</p>
          <ul>${st.urls.map((u) => `<li><a href="${esc(u)}" class="mono">${esc(u)}</a></li>`).join("")}</ul>
          <p class="small">${st.app.auth ? `Вход по паролю <b>включён</b> (пользователь <span class="mono">${esc(st.app.auth_user)}</span>). Сменить пароль: <span class="mono">sudo tbo-ctl password</span>.`
            : 'Вход по паролю <b>выключен</b> — любой в сети может вносить изменения. Включить: <span class="mono">sudo tbo-ctl password</span>.'}</p></div>
        <div class="card"><h2>Сервис</h2><table class="small"><tbody>
          <tr><td class="muted">Версия tbo-tracker</td><td>${esc(st.app.version)} · Python ${esc(st.app.python)}</td></tr>
          <tr><td class="muted">Сервис работает</td><td>${fmtUptime(st.app.uptime_s)}</td></tr>
          <tr><td class="muted">Каталог данных</td><td class="mono">${esc(st.app.data_dir)}</td></tr>
          <tr><td class="muted">База / фото / копии</td><td>${fmtBytes(d.db_bytes)} / ${fmtBytes(d.uploads_bytes)} / ${d.backups} шт., ${fmtBytes(d.backups_bytes)}</td></tr>
          <tr><td class="muted">Последняя копия</td><td>${d.last_backup ? `${esc(d.last_backup.name)}` : "нет"}</td></tr>
          <tr><td class="muted">Фоновая проверка</td><td>${sc.last_tick ? `последняя: ${fmtDT(sc.last_tick)}` : sc.started_at ? "запущена" : '<span class="muted">выключена</span>'}${sc.last_error ? `<div class="small" style="color:var(--bad)">${esc(sc.last_error)}</div>` : ""}</td></tr>
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
      <div class="small muted" style="margin-bottom:10px">Сервер проверяет регламенты и датчики сам и отправляет новые уведомления одним сообщением — даже когда никто не открыл сайт.</div>
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

// ------------------------------------------------------------------ навигация
const routes = [
  [/^#?\/?$/, "dash", pageDashboard],
  [/^#\/aircraft$/, "aircraft", pageAircraftList],
  [/^#\/aircraft\/(\d+)$/, "aircraft", pageAircraft],
  [/^#\/components(?:\?.*)?$/, "board", pageComponents],
  [/^#\/components\/(\d+)(?:\?.*)?$/, "board", pageComponent],
  [/^#\/generator$/, "generator", pageGenerator],
  [/^#\/types$/, "board", pageTypes],
  [/^#\/imports$/, "imports", pageImports],
  [/^#\/maintenance(?:\?tab=(\w+))?$/, "maintenance", pageMaintenance],
  [/^#\/notifications$/, "maintenance", () => pageMaintenance("notif")],
  [/^#\/board$/, "board", pageBoard],
  [/^#\/rules$/, "rules", pageRules],
  [/^#\/analytics$/, "analytics", pageAnalytics],
  [/^#\/scenarios$/, "scenarios", pageScenarios],
  [/^#\/server$/, "server", pageServer],
  [/^#\/missions$/, "missions", pageMissions],
  [/^#\/missions\/(\d+)$/, "missions", pageMission],
];
async function router() {
  $$(".modal-bg").forEach((m) => m.remove());     // окна не переживают переход на другую страницу
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
    const d = await GET(`/api/notifications/feed${since !== null ? `?after=${since}` : ""}`);
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
setInterval(refreshNavCount, 60000);

async function renderNotifications(root) {
  await refreshNavCount();
  const [d, hist] = await Promise.all([GET("/api/notifications"), GET("/api/notifications/history?limit=50")]);
  const c = d.counts;
  const perm = window.Notification ? Notification.permission : "unsupported";
  root.innerHTML = `<div class="toolbar"><div class="small muted">Формируются автоматически при каждом изменении наработки (импорт журнала, автономный полёт, ручная запись) и по календарю. Проверка выполняется сервером каждую минуту, даже когда браузер закрыт; доставка в Telegram / webhook — раздел <a href="#/server">«Сервер»</a>.</div>
      <div>${perm === "default" ? '<button class="sm" id="n-perm">🔔 Уведомления в браузере</button>' : perm === "granted" ? '<span class="badge ok">уведомления браузера включены</span>' : ""}</div></div>
    <div class="kpis">
      ${["limit", "overdue", "approaching"].map((k) => `<div class="kpi clickable ${c[k] ? NOTIF_CAT[k].cls : "ok"}" data-scroll="n-${k}"><div class="v">${c[k]}</div><div class="l">${esc(d.categories[k])}</div></div>`).join("")}
      <div class="kpi clickable ${c.sensor_events ? "warn" : "ok"}" data-scroll="n-sens"><div class="v">${c.sensor_events}</div><div class="l">Падений и столкновений за ${d.days} дн.</div></div>
    </div>
    ${["limit", "overdue", "approaching"].map((k) => `<div class="card flat notif-${NOTIF_CAT[k].cls}" id="n-${k}"><div class="card-head"><h2>${NOTIF_CAT[k].icon} ${esc(d.categories[k])} <span class="muted">(${c[k]})</span></h2></div>
      <div class="small muted" style="margin:-6px 0 10px">${NOTIF_CAT[k].hint}</div><div data-cat="${k}">${dueTable(d.items[k], { empty: "Нет уведомлений" })}</div></div>`).join("")}
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

function readiness(a) { return readinessHTML(a.readiness); }   // уровень и текст допуска рассчитывает сервер

function fleetTable(list) {
  return `<div class="table-wrap"><table class="fleet"><thead><tr><th style="width:84px"></th><th>Борт</th><th>Тип</th><th>Статус</th><th>Готовность (борт + узлы)</th><th class="num">Налёт</th><th class="num">Циклы</th><th class="num">Полёты</th><th>Базирование</th></tr></thead><tbody>
  ${list.map((a) => `<tr><td><a href="#/aircraft/${a.id}">${droneImg(a, "thumb")}</a></td><td><a href="#/aircraft/${a.id}"><b>${esc(a.name)}</b></a><div class="small muted mono">${esc(a.serial)}</div></td>
    <td>${esc(a.type.name)}</td><td>${acBadge(a)}</td><td>${readiness(a)}</td>
    <td class="num">${hrs(a.hours)}</td><td class="num">${num(a.cycles, 0)}</td><td class="num">${num(a.flights, 0)}</td><td class="small">${esc(a.base || "")}</td></tr>`).join("")}
  </tbody></table></div>`;
}

// ------------------------------------------------------------------ Парк
async function pageAircraftList() {
  const list = await aircraftList(true);
  view().innerHTML = `<div class="page-head"><div><h1>Парк БПЛА</h1><div class="sub">Электронные паспорта аппаратов</div></div>
    <div class="actions"><button id="pool">🖼 Пул фото</button><button class="primary" id="add">+ Добавить борт</button></div></div>
    <div class="fleet-grid">${list.map((a) => `<a class="fleet-card" href="#/aircraft/${a.id}">
      <div class="fc-img">${droneImg(a, "cover")}<span class="fc-status">${acBadge(a)}</span></div>
      <div class="fc-body"><div class="fc-title"><b>${esc(a.name)}</b><span class="mono muted small">${esc(a.serial)}</span></div>
        <div class="small muted">${esc(a.type.name)} · ${esc(a.base || "")}</div>
        <div class="fc-ready small">${readiness(a)}</div>
        <div class="fc-nums"><div><b>${hrs(a.hours)}</b><span>налёт</span></div><div><b>${num(a.cycles, 0)}</b><span>циклов</span></div><div><b>${a.slots_filled}/${a.slots_total}</b><span>позиций</span></div></div>
      </div></a>`).join("")}</div>`;
  $("#add").onclick = openAircraftCreate;
  $("#pool").onclick = () => openPhotoPicker(null, reload);
}

async function openAircraftCreate() {
  const m = await meta();
  modal("Новый борт", `
    <div class="row2"><div class="field"><label>Серийный номер</label><input name="serial" placeholder="TBT-M4-004"></div>
    <div class="field"><label>Бортовое имя</label><input name="name" placeholder="Борт 04"></div></div>
    <div class="row2"><div class="field"><label>Тип</label><select name="type_id">${m.aircraft_types.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>
      <div class="hint">Нужен свой тип с позициями узлов? <a href="#/types" data-close>Типы бортов и узлов →</a></div></div>
    <div class="field"><label>Дата ввода в эксплуатацию</label><input type="datetime-local" name="in_service_at" value="${toLocalInput(new Date())}"></div></div>
    <div class="row2"><div class="field"><label>Эксплуатант</label><input name="operator"></div><div class="field"><label>Место базирования</label><input name="base"></div></div>
    <div class="row2"><div class="field"><label>Начальный налёт, ч</label><input type="number" step="0.1" min="0" name="initial_hours" value="0"></div>
    <div class="field"><label>Начальные циклы</label><input type="number" min="0" name="initial_cycles" value="0"></div></div>
    ${resourceFields()}`, {
    okText: "Создать",
    onOk: async (root) => {
      const r = await POST("/api/aircraft", { serial: val(root, "serial"), name: val(root, "name"), type_id: +val(root, "type_id"),
        in_service_at: fromLocalInput(val(root, "in_service_at")), operator: orNull(val(root, "operator")), base: orNull(val(root, "base")),
        initial_hours: +val(root, "initial_hours") || 0, initial_cycles: +val(root, "initial_cycles") || 0, ...readResource(root) });
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
      <button id="res">⚙ Ресурс</button><button id="mnt">⚑ Зарегистрировать работу</button></div></div>
    <div class="card ac-hero"><div class="ac-photo">${droneImg(a, "cover")}<button class="sm ac-photo-btn" id="photo">🖼 Изменить фото</button></div>
    <div class="ac-hero-body"><div class="stat-line">
      <div class="s"><div class="v">${hrs(a.hours)}</div><div class="l">суммарный налёт</div></div>
      <div class="s"><div class="v">${num(a.cycles, 0)}</div><div class="l">циклов (взлёт-посадка)</div></div>
      <div class="s"><div class="v">${num(a.flights, 0)}</div><div class="l">полётов в журналах</div></div>
      <div class="s"><div class="v">${a.slots_filled}/${a.slots_total}</div><div class="l">позиций укомплектовано</div></div>
      <div class="s"><div class="v"${a.events.fall + a.events.collision ? ' style="color:var(--bad)"' : ""}>${a.events.fall} / ${a.events.collision}</div><div class="l">падений / столкновений</div></div>
      <div class="s"><div class="v" style="font-size:15px;padding-top:4px">${readiness(a)}</div><div class="l">допуск к полётам</div></div>
    </div><div class="small muted" style="margin-top:10px">Индивидуальный ресурс борта: <b>${resourceText(a.resource) || "не задан"}</b></div>
    ${a.notes ? `<div class="notice small" style="margin:12px 0 0">${esc(a.notes)}</div>` : ""}</div></div>
    <div class="card"><div class="card-head"><h2>Конфигурация борта — установленные экземпляры узлов</h2><a href="#/components?status=stock">Резерв на складе →</a></div>
      <div class="slots">${a.configuration.map(slotHTML).join("")}</div></div>
    <div class="card"><div class="tabs" id="tabs">
      <button data-t="due" class="active">ТО борта и узлов (${a.due.filter((i) => i.status !== "ok").length} требуют внимания)</button>
      <button data-t="rec">История обслуживания (${a.maintenance_records.length})</button>
      <button data-t="cfg">История конфигурации (${a.installations.length})</button>
      <button data-t="fl">Полёты (${a.flight_log.length}${a.flight_log.length >= 200 ? "+" : ""})</button>
      <button data-t="sens">Датчики: падения и столкновения (${a.sensor_events.length})</button></div><div id="tab"></div></div>`;
  $("#st").onchange = async (e) => {
    try { await api("PATCH", `/api/aircraft/${id}`, { status: e.target.value }); toast("Статус борта обновлён"); reload(); } catch (err) { toast(err.message, true); }
  };
  $("#mnt").onclick = () => openMaintenanceModal({ target: "aircraft", entity: { ...a, kind: "aircraft" }, rules: own.map((i) => i.rule), onDone: reload });
  $("#res").onclick = () => openResourceModal({ kind: "aircraft", entity: a, onDone: reload });
  $("#photo").onclick = () => openPhotoPicker(a, reload);
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
    sens: () => { $("#tab").innerHTML = eventsTable(a.sensor_events); },
    cfg: () => {
      $("#tab").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Позиция</th><th>Узел</th><th>Установлен</th><th>Снят</th><th class="num">Наработка на этом борту</th><th>Основание</th></tr></thead><tbody>
      ${a.installations.map((i) => `<tr><td>${esc(i.position)}</td><td><a href="#/components/${i.component_id}">${esc(i.serial)}</a><div class="small muted">${esc(i.type)}</div></td>
        <td class="nowrap">${fmtDT(i.installed_at)}</td><td class="nowrap">${i.removed_at ? fmtDT(i.removed_at) : '<span class="badge blue">установлен</span>'}</td>
        <td class="num">${hrs(i.hours)} · ${num(i.cycles, 0)} ц.</td><td class="small">${esc(i.install_reason || "")}${i.remove_reason ? `<br><span class="muted">снят: ${esc(i.remove_reason)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
    },
    fl: () => {
      $("#tab").innerHTML = flightsTable(a.flight_log, { toolbar: '<button class="primary sm" id="add-fl">+ Записать полёт вручную</button><span class="small muted">Показаны последние 200 полётов</span>' });
      $("#add-fl").onclick = () => openManualFlightModal({ aircraftId: a.id, onDone: reload });
      bindFlightDeletes($("#tab"), null, reload);
    },
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
      <div class="ser muted">Не установлен</div>${s.required === false ? '<div class="small muted">Необязательная позиция</div>' : '<div class="small" style="color:var(--bad)">Позиция пуста — борт некомплектен</div>'}
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

function flightsTable(fl, opts = {}) {
  const head = opts.toolbar ? `<div class="toolbar">${opts.toolbar}</div>` : "";
  if (!fl.length) return head + '<div class="empty-state">Полётов нет</div>';
  const src = (f) => f.source === "entry" ? '<span class="badge warn">вне борта</span>'
    : f.manual ? '<span class="badge blue">ручной ввод</span>' : `<span class="small">журнал №${f.import_id ?? "—"}</span>`;
  const del = (f) => f.source === "entry" ? `<button class="sm danger" data-del-entry="${f.id}" title="Удалить запись">✕</button>`
    : f.manual ? `<button class="sm danger" data-del-flight="${f.id}" title="Удалить ручной полёт">✕</button>` : "";
  return `${head}<div class="table-wrap"><table><thead><tr><th>Взлёт / начало</th><th>Посадка</th><th>Борт</th><th class="num">Длительность</th><th class="num">Циклы</th><th>Задание / примечание</th><th>ID в журнале</th><th>Датчики</th><th>Источник</th><th></th></tr></thead><tbody>
  ${fl.map((f) => `<tr><td class="nowrap">${fmtDT(f.started_at)}</td><td class="nowrap">${fmtDT(f.ended_at)}</td>
    <td>${f.aircraft_id ? `<a href="#/aircraft/${f.aircraft_id}">${esc(f.aircraft_name)}</a>` : `<span class="muted">${esc(f.aircraft_name)}</span>`}</td>
    <td class="num">${f.duration_h < 1 ? num(f.duration_h * 60, 0) + " мин" : num(f.duration_h, 2) + " ч"}</td><td class="num">${f.cycles}</td>
    <td class="small">${esc(f.mission || "")}</td><td class="mono">${esc(f.external_id)}</td><td>${f.events ? `<span class="badge bad">${f.events} соб.</span>` : '<span class="muted small">—</span>'}</td><td>${src(f)}</td><td>${del(f)}</td></tr>`).join("")}</tbody></table></div>`;
}

function bindFlightDeletes(root, componentId, after) {
  $$("[data-del-flight]", root).forEach((b) => b.addEventListener("click", () => modal("Удаление ручного полёта",
    "<p>Удалить полёт, записанный вручную? Наработка будет списана с борта и всех узлов, которым он был начислен.</p>", {
      okText: "Удалить", onOk: async () => { const r = await api("DELETE", `/api/flights/${b.dataset.delFlight}`); toast(r.message); refreshNavCount(); after(); },
    })));
  $$("[data-del-entry]", root).forEach((b) => b.addEventListener("click", () => modal("Удаление записи",
    "<p>Удалить запись внебортовой наработки узла?</p>", {
      okText: "Удалить", onOk: async () => { const r = await api("DELETE", `/api/components/${componentId}/usage/${b.dataset.delEntry}`); toast(r.message); refreshNavCount(); after(); },
    })));
}

async function openManualFlightModal({ aircraftId, component, onDone }) {
  const fleet = await aircraftList(true);
  const end = new Date(); end.setMinutes(0, 0, 0);
  const start = new Date(end.getTime() - 30 * 60000);
  const preset = aircraftId || (component && component.location ? component.location.aircraft_id : null);
  const body = `
    <div class="notice small">Полёт начисляется борту и <b>всем узлам, установленным на борт в момент взлёта</b> — так же, как при импорте журнала.
      Действует та же защита от дублей и пересечений по времени.${component ? `<br>Внесение из карточки узла <b>${esc(component.serial)}</b>: система проверит, что узел стоял на выбранном борту в момент взлёта. Для стенда используйте «Наработка вне борта».` : ""}</div>
    <div class="field"><label>Борт</label><select name="aircraft_id">${fleet.filter((a) => a.status !== "retired").map((a) => `<option value="${a.id}" ${a.id === preset ? "selected" : ""}>${esc(a.name)} · ${esc(a.serial)}</option>`).join("")}</select></div>
    <div class="row2"><div class="field"><label>Взлёт</label><input type="datetime-local" name="start" value="${toLocalInput(start)}"></div>
    <div class="field"><label>Посадка</label><input type="datetime-local" name="end" value="${toLocalInput(end)}"></div></div>
    <div class="row3"><div class="field"><label>Циклы</label><input type="number" min="0" name="cycles" value="1"></div>
    <div class="field"><label>ID полёта (необязательно)</label><input name="flight_id" placeholder="авто"></div>
    <div class="field"><label>Пилот</label><input name="pilot"></div></div>
    <div class="field"><label>Задание</label><input name="mission" placeholder="Напр.: облёт после ТО"></div>`;
  modal("Записать полёт вручную", body, {
    okText: "Записать полёт",
    onOk: async (root) => {
      const r = await POST("/api/flights", { aircraft_id: +val(root, "aircraft_id"), start: fromLocalInput(val(root, "start")), end: fromLocalInput(val(root, "end")),
        cycles: parseInt(val(root, "cycles") || "1", 10), flight_id: orNull(val(root, "flight_id")), pilot: orNull(val(root, "pilot")),
        mission: orNull(val(root, "mission")), component_id: component ? component.id : null });
      toast(r.message);
      ((r.summary && r.summary.alerts) || []).forEach((a) => toast(`Напоминание: ${a.entity.serial} — ${a.rule.name}: ${DUE[a.status][1]}`));
      refreshNavCount();
      onDone && onDone();
    },
  });
}

function openUsageEntryModal({ component, onDone }) {
  const body = `
    <div class="notice small">Наработка <b>вне борта</b> начисляется только узлу ${esc(component.serial)} (не борту и не другим узлам):
      стендовые прогоны, испытания, перенос наработки из бумажного формуляра. Учитывается во всех регламентах узла.</div>
    <div class="row2"><div class="field"><label>Вид</label><select name="kind"><option value="bench">Стендовый прогон</option><option value="test">Испытания / проверка</option>
      <option value="correction">Корректировка по формуляру</option></select><div class="hint">Стенд и испытания нельзя записать на период, когда узел стоял на борту.</div></div>
    <div class="field"><label>Дата и время</label><input type="datetime-local" name="started_at" value="${toLocalInput(new Date())}"></div></div>
    <div class="row2"><div class="field"><label>Часы</label><input type="number" min="0" step="0.1" name="hours" value="1"></div>
    <div class="field"><label>Циклы</label><input type="number" min="0" name="cycles" value="0"></div></div>
    <div class="row2"><div class="field"><label>Примечание</label><input name="note" placeholder="Напр.: прогон после замены разъёма"></div>
    <div class="field"><label>Исполнитель</label><input name="created_by"></div></div>`;
  modal("Наработка узла вне борта", body, {
    okText: "Записать",
    onOk: async (root) => {
      const r = await POST(`/api/components/${component.id}/usage`, { kind: val(root, "kind"), started_at: fromLocalInput(val(root, "started_at")),
        hours: parseFloat(val(root, "hours") || "0"), cycles: parseInt(val(root, "cycles") || "0", 10), note: orNull(val(root, "note")), created_by: orNull(val(root, "created_by")) });
      toast(r.message);
      refreshNavCount();
      onDone && onDone();
    },
  });
}

// ------------------------------------------------------------------ Генератор журналов
async function pageGenerator() {
  const fleet = await aircraftList(true);
  const today = new Date();
  const from = new Date(today.getTime() - 2 * 86400000);
  const d = (x) => toLocalInput(x).slice(0, 10);
  view().innerHTML = `<div class="page-head"><div><h1>Генератор журналов эксплуатации</h1>
    <div class="sub">Создаёт реалистичные журналы в формате tbo-flightlog/1.0 (JSON или CSV) для проверки импорта и расчёта наработки.
    Тот же модуль доступен из командной строки: <span class="mono">python -m app.loggen --help</span></div></div></div>
    <div class="grid g2">
      <div class="card"><h2>Параметры</h2>
        <div class="field"><label>Борта (Ctrl/⌘ — несколько)</label><select name="aircraft" multiple size="${Math.min(6, fleet.length)}">
          ${fleet.filter((a) => a.status !== "retired").map((a, i) => `<option value="${esc(a.serial)}" ${i === 0 ? "selected" : ""}>${esc(a.name)} · ${esc(a.serial)} (${esc(a.status_label)})</option>`).join("")}</select></div>
        <div class="row3"><div class="field"><label>С даты</label><input type="date" name="from" value="${d(from)}"></div>
          <div class="field"><label>По дату (включ.)</label><input type="date" name="to" value="${d(today)}"></div>
          <div class="field"><label>Формат</label><select name="format"><option value="json">JSON</option><option value="csv">CSV</option></select></div></div>
        <div class="row3"><div class="field"><label>Вероятность лётного дня</label><input type="number" name="prob" min="0" max="1" step="0.05" value="0.8"></div>
          <div class="field"><label>Полётов в день: от</label><input type="number" name="fmin" min="0" max="20" value="1"></div>
          <div class="field"><label>до</label><input type="number" name="fmax" min="0" max="20" value="4"></div></div>
        <div class="row3"><div class="field"><label>Длительность, мин: от</label><input type="number" name="dmin" min="1" max="600" value="15"></div>
          <div class="field"><label>до</label><input type="number" name="dmax" min="1" max="600" value="45"></div>
          <div class="field"><label>Первый взлёт, час UTC</label><input type="number" name="hour" min="0" max="23" value="6"></div></div>
        <div class="row3"><div class="field"><label>Вероятность 2 циклов</label><input type="number" name="tg" min="0" max="1" step="0.05" value="0.05"></div>
          <div class="field"><label>Добавить дублей учтённых полётов</label><input type="number" name="dups" min="0" max="500" value="0"></div>
          <div class="field"><label>Seed (повторяемость)</label><input type="number" name="seed" placeholder="случайно"></div></div>
        <label><input type="checkbox" name="ids" style="width:auto" checked> Указывать flight_id</label>
        <label><input type="checkbox" name="overlap" style="width:auto" checked> Не пересекаться с уже учтёнными полётами</label>
        <label><input type="checkbox" name="errors" style="width:auto"> Добавить ошибочные строки (неизвестный борт, посадка раньше взлёта, будущее, битая дата)</label>
        <div class="actions" style="margin-top:14px"><button id="gen">Сгенерировать</button><button id="dl" disabled>⇩ Скачать файл</button>
          <button id="check" disabled>Проверить импорт (без записи)</button><button class="primary" id="imp" disabled>⇪ Импортировать</button></div>
        <div class="hint">Полёты в будущем и до ввода борта в эксплуатацию не генерируются.</div></div>
      <div class="card"><h2>Результат</h2><div id="gres" class="empty-state">Задайте параметры и нажмите «Сгенерировать»</div></div>
    </div>
    <div class="card" id="gimp" hidden></div>`;
  let last = null;
  const root = view();
  const params = () => {
    const g = (n) => $(`[name="${n}"]`, root);
    const sel = Array.from(g("aircraft").selectedOptions).map((o) => o.value);
    return { aircraft_serials: sel, date_from: g("from").value + "T00:00:00Z", date_to: g("to").value + "T00:00:00Z", format: g("format").value,
      fly_probability: +g("prob").value, flights_min: +g("fmin").value, flights_max: +g("fmax").value, duration_min: +g("dmin").value,
      duration_max: +g("dmax").value, first_takeoff_hour: +g("hour").value, touch_and_go_probability: +g("tg").value,
      duplicates: +g("dups").value || 0, seed: g("seed").value ? +g("seed").value : null, with_ids: g("ids").checked,
      avoid_overlap: g("overlap").checked, errors: g("errors").checked };
  };
  const showGen = (r) => {
    last = r;
    $("#dl").disabled = $("#check").disabled = $("#imp").disabled = false;
    $("#gres").className = "";
    $("#gres").innerHTML = `<div class="notice ok">Файл <b class="mono">${esc(r.filename)}</b>: строк ${r.stats.flights}, из них новых полётов ${r.stats.new_flights}
      (${hrs(r.stats.hours_new)}), дублей ${r.stats.duplicates}, ошибочных ${r.stats.errors}.
      ${r.notes.length ? `<ul>${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</div>
      <details><summary>Содержимое файла</summary><pre class="mono" style="max-height:360px;overflow:auto;background:#f7f9fc;padding:10px;border-radius:8px">${esc(r.content.slice(0, 20000))}</pre></details>`;
  };
  $("#gen").onclick = async () => { try { showGen(await POST("/api/log-generator", params())); } catch (e) { toast(e.message, true); } };
  $("#dl").onclick = () => {
    const blob = new Blob([last.content], { type: last.format === "json" ? "application/json" : "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = last.filename; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const doImport = async (dry) => {
    const fd = new FormData();
    fd.append("file", new Blob([last.content], { type: "text/plain" }), last.filename);
    fd.append("dry_run", dry ? "true" : "false");
    fd.append("imported_by", "генератор журналов");
    try {
      const r = await api("POST", "/api/imports", fd, true);
      const el = $("#gimp"); el.hidden = false; el.innerHTML = `<h2>${dry ? "Проверка импорта" : "Результат импорта"}</h2>${importResultHTML(r)}`;
      el.scrollIntoView({ behavior: "smooth" });
      if (!dry) refreshNavCount();
    } catch (e) { toast(e.message, true); }
  };
  $("#check").onclick = () => doImport(true);
  $("#imp").onclick = () => doImport(false);
}

// ------------------------------------------------------------------ Типы бортов и узлов
async function pageTypes() {
  META = null;
  const m = await meta();
  view().innerHTML = `<div class="crumbs"><a href="#/components">Узлы и склад</a> / Свои типы</div><div class="page-head"><div><h1>Типы бортов и узлов</h1><div class="sub">Справочники: добавьте свои типы узлов (GNSS, АКБ, моторы, подвесы…) и посадочные места для них в типах БПЛА.
    Затем — «Узлы и склад» → «+ Добавить узел» и регламенты в разделе «Регламенты».</div></div></div>
    <div class="card"><div class="card-head"><h2>Типы узлов</h2><button class="primary sm" id="add-ct">+ Тип узла</button></div>
      <table><thead><tr><th>Код</th><th>Название</th><th>Описание</th><th>Используется в позициях</th><th></th></tr></thead><tbody>
      ${m.component_types.map((t) => {
        const used = m.aircraft_types.map((a) => [a.code, a.slots.filter((s) => s.component_type === t.code).map((s) => s.position)])
          .filter(([, ps]) => ps.length).map(([code, ps]) => `${code}: ${ps.join(", ")}`);
        return `<tr><td class="mono">${esc(t.code)}</td><td><b>${esc(t.name)}</b></td><td class="small">${esc(t.description || "")}</td>
          <td class="small">${used.length ? esc(used.join(", ")) : '<span style="color:var(--warn)">нет позиций — узлы этого типа некуда установить</span>'}</td>
          <td class="nowrap"><button class="sm" data-ct-edit="${t.id}">Изменить</button> <button class="sm danger" data-ct-del="${t.id}">✕</button></td></tr>`;
      }).join("")}</tbody></table></div>
    <div class="card"><div class="card-head"><h2>Типы БПЛА и посадочные места</h2><button class="primary sm" id="add-at">+ Тип БПЛА</button></div>
      ${m.aircraft_types.map((a) => `<div class="card flat"><div class="card-head"><h3 style="margin:0">${esc(a.name)} <span class="mono muted small">${esc(a.code)}</span></h3>
        <div class="actions"><button class="sm" data-at-edit="${a.id}">Изменить позиции</button><button class="sm danger" data-at-del="${a.id}">✕</button></div></div>
        <div class="chips">${a.slots.map((s) => `<span class="badge ${s.required === false ? "" : "blue"}">${esc(s.position)} · ${esc(s.label)}${s.required === false ? " · необязат." : ""}</span>`).join("")}</div></div>`).join("")}</div>`;
  const ctForm = (t = {}) => `<div class="row2"><div class="field"><label>Код (латиница, без пробелов)</label><input name="code" value="${esc(t.code || "")}" ${t.id ? "disabled" : ""} placeholder="gnss"></div>
    <div class="field"><label>Название</label><input name="name" value="${esc(t.name || "")}" placeholder="GNSS-приёмник"></div></div>
    <div class="field"><label>Описание</label><input name="description" value="${esc(t.description || "")}"></div>
    <div class="field"><label>Шаблон характеристик паспорта (по одной на строку)</label><textarea name="spec_fields" rows="4" placeholder="Ёмкость&#10;Напряжение&#10;Масса">${esc((t.spec_fields || []).join("\n"))}</textarea>
      <div class="hint">Эти поля подставляются в паспорт при постановке узла этого типа на учёт.</div></div>`;
  const openCt = (t) => modal(t ? "Изменение типа узла" : "Новый тип узла", ctForm(t || {}), {
    onOk: async (root) => {
      const body = { code: t ? t.code : val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")),
        spec_fields: (val(root, "spec_fields") || "").split("\n").map((x) => x.trim()).filter(Boolean) };
      const r = await api(t ? "PUT" : "POST", t ? `/api/component-types/${t.id}` : "/api/component-types", body);
      toast(r.message); pageTypes();
    },
  });
  const slotRow = (s = {}) => `<tr><td><input name="position" value="${esc(s.position || "")}" placeholder="GNSS"></td><td><input name="label" value="${esc(s.label || "")}" placeholder="GNSS-приёмник"></td>
    <td><select name="ctype">${m.component_types.map((c) => `<option value="${esc(c.code)}" ${c.code === s.component_type ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></td>
    <td style="text-align:center"><input type="checkbox" name="required" style="width:auto" ${s.required === false ? "" : "checked"}></td>
    <td><button class="sm danger" data-rm>✕</button></td></tr>`;
  const openAt = (a) => {
    const bg = modal(a ? `Тип БПЛА: ${a.name}` : "Новый тип БПЛА", `
      <div class="row3"><div class="field"><label>Код</label><input name="code" value="${esc(a ? a.code : "")}" ${a ? "disabled" : ""} placeholder="TBT-H6"></div>
      <div class="field"><label>Название</label><input name="name" value="${esc(a ? a.name : "")}" placeholder="TBT-H6 (гексакоптер)"></div>
      <div class="field"><label>Категория</label><select name="category">${["multirotor", "vtol", "fixed-wing", "helicopter", "ground"].map((c) => `<option ${a && a.category === c ? "selected" : ""}>${c}</option>`).join("")}</select></div></div>
      <div class="field"><label>Производитель</label><input name="manufacturer" value="${esc(a ? a.manufacturer || "" : "")}"></div>
      <h3>Посадочные места</h3><table><thead><tr><th>Позиция</th><th>Название</th><th>Тип узла</th><th title="Без узла в обязательной позиции борт не допускается к полётам">Обязат.</th><th></th></tr></thead><tbody id="slots">${(a ? a.slots : [{}]).map(slotRow).join("")}</tbody></table>
      <button class="sm" id="add-slot" style="margin-top:8px">+ Позиция</button>
      <div class="hint">Нельзя удалить позицию или сменить её тип, пока в ней на каком-либо борту стоит узел.</div>`, {
      wide: true,
      onOk: async (root) => {
        const slots = $$("#slots tr", root).map((tr) => ({ position: $('[name="position"]', tr).value.trim(), label: $('[name="label"]', tr).value.trim(), component_type: $('[name="ctype"]', tr).value, required: $('[name="required"]', tr).checked }))
          .filter((s) => s.position);
        const body = { code: a ? a.code : val(root, "code"), name: val(root, "name"), category: val(root, "category"), manufacturer: orNull(val(root, "manufacturer")), slots };
        const r = await api(a ? "PUT" : "POST", a ? `/api/aircraft-types/${a.id}` : "/api/aircraft-types", body);
        toast(r.message); pageTypes();
      },
    });
    const bindRm = () => $$("[data-rm]", bg).forEach((b) => { b.onclick = () => b.closest("tr").remove(); });
    $("#add-slot", bg).onclick = () => { $("#slots", bg).insertAdjacentHTML("beforeend", slotRow()); bindRm(); };
    bindRm();
  };
  $("#add-ct").onclick = () => openCt(null);
  $("#add-at").onclick = () => openAt(null);
  $$("[data-ct-edit]").forEach((b) => b.addEventListener("click", () => openCt(m.component_types.find((t) => t.id === +b.dataset.ctEdit))));
  $$("[data-at-edit]").forEach((b) => b.addEventListener("click", () => openAt(m.aircraft_types.find((t) => t.id === +b.dataset.atEdit))));
  const del = (url) => modal("Удаление", "<p>Удалить тип? Это возможно, только если он нигде не используется.</p>", {
    okText: "Удалить", onOk: async () => { const r = await api("DELETE", url); toast(r.message); pageTypes(); },
  });
  $$("[data-ct-del]").forEach((b) => b.addEventListener("click", () => del(`/api/component-types/${b.dataset.ctDel}`)));
  $$("[data-at-del]").forEach((b) => b.addEventListener("click", () => del(`/api/aircraft-types/${b.dataset.atDel}`)));
}

// ------------------------------------------------------------------ Узлы
async function pageComponents() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const m = await meta();
  let status = params.get("status") || "", typeId = "", q = "";
  view().innerHTML = `${nodesSwitch("components")}<div class="page-head"><div><h1>Узлы и склад</h1><div class="sub">Паспорта заменяемых компонентов: сквозная наработка, местонахождение, состояние ТО</div></div>
    <div class="actions"><a class="btn" href="#/types">⊞ Свои типы узлов</a><button class="primary" id="add">+ Добавить узел</button></div></div>
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
    $("#list").innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>Узел</th><th>Тип</th><th>Статус</th><th>Где сейчас</th><th class="num">Сквозная наработка</th><th class="num">Циклы</th><th>Остаточный ресурс</th><th>ТО</th></tr></thead><tbody>
      ${list.map((c) => `<tr><td><a href="#/components/${c.id}"><b>${esc(c.serial)}</b></a><div class="small muted">${esc(c.model || "")} · ПО ${esc(c.firmware || "—")}</div></td>
        <td>${esc(c.type.name)}</td><td>${coBadge(c)}</td>
        <td>${c.location ? `<a href="#/aircraft/${c.location.aircraft_id}">${esc(c.location.aircraft_name)}</a> <span class="muted small">${esc(c.location.position)}</span>` : '<span class="muted">—</span>'}</td>
        <td class="num">${hrs(c.hours)}</td><td class="num">${num(c.cycles, 0)}</td>
        <td class="small nowrap" style="min-width:150px">${residualText(c.residual, true)}${residualBar(c.residual)}</td>
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
  const bg = modal("Новый узел (постановка на учёт)", `
    <div class="row2"><div class="field"><label>Серийный номер</label><input name="serial" placeholder="FC7-0108"></div>
    <div class="field"><label>Тип</label><select name="type_id">${typeOptions(m.component_types, null, '<option value="__new">+ Новый тип узла…</option>')}</select></div></div>
    <div class="row3"><div class="field"><label>Модель</label><input name="model"></div><div class="field"><label>Децимальный номер</label><input name="part_number"></div>
    <div class="field"><label>Версия ПО</label><input name="firmware"></div></div>
    <div class="row2"><div class="field"><label>Производитель</label><input name="manufacturer" value="tbo-tracker (демо)"></div>
    <div class="field"><label>Дата постановки на учёт</label><input type="datetime-local" name="in_service_at" value="${toLocalInput(new Date())}"></div></div>
    <div class="row2"><div class="field"><label>Наработка до постановки на учёт, ч</label><input type="number" min="0" step="0.1" name="initial_hours" value="0">
      <div class="hint">Стендовые испытания, эксплуатация в другом парке (по формуляру)</div></div>
    <div class="field"><label>Циклы до постановки на учёт</label><input type="number" min="0" name="initial_cycles" value="0"></div></div>
    <div class="field"><label>Дата изготовления</label><input type="date" name="manufactured_at" style="max-width:220px"></div>
    <h3>Основные технические характеристики</h3><div id="spec-box"></div>
    <h3 style="margin-top:14px">Установленный ресурс</h3>${resourceFields()}`, {
    okText: "Поставить на учёт", wide: true,
    onOk: async (root) => {
      const r = await POST("/api/components", { serial: val(root, "serial"), type_id: +val(root, "type_id"), model: orNull(val(root, "model")),
        part_number: orNull(val(root, "part_number")), firmware: orNull(val(root, "firmware")), manufacturer: orNull(val(root, "manufacturer")),
        in_service_at: fromLocalInput(val(root, "in_service_at")), initial_hours: +val(root, "initial_hours") || 0, initial_cycles: +val(root, "initial_cycles") || 0,
        manufactured_at: val(root, "manufactured_at") ? val(root, "manufactured_at") + "T00:00:00Z" : null, specs: readSpecs(root), ...readResource(root) });
      toast("Узел поставлен на учёт (резерв)");
      location.hash = `#/components/${r.id}`;
    },
  });
  const ts = $('[name="type_id"]', bg);
  let types = m.component_types;
  const fillSpecs = () => {   // шаблон характеристик из типа узла
    const tp = types.find((x) => String(x.id) === ts.value);
    $("#spec-box", bg).innerHTML = specRows((tp && tp.spec_fields || []).map((n) => ({ name: n, value: "" })));
    bindSpecs($("#spec-box", bg));
  };
  ts.dataset.prev = ts.value;
  fillSpecs();
  ts.onchange = () => {
    if (ts.value !== "__new") { ts.dataset.prev = ts.value; fillSpecs(); return; }
    quickComponentType(ts, (mm, id) => { types = mm.component_types; ts.innerHTML = typeOptions(mm.component_types, id, '<option value="__new">+ Новый тип узла…</option>'); ts.dataset.prev = id; fillSpecs(); });
  };
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
        <button data-a="res">⚙ Ресурс</button><button data-a="mnt">⚑ Зарегистрировать работу</button></div></div>
    <div class="card"><div class="stat-line">
      <div class="s"><div class="v">${hrs(c.hours)}</div><div class="l">сквозная наработка (с начала эксплуатации)</div></div>
      <div class="s"><div class="v">${num(c.cycles, 0)}</div><div class="l">циклов</div></div>
      <div class="s"><div class="v">${num(c.flights, 0)}</div><div class="l">полётов в составе бортов</div></div>
      <div class="s"><div class="v">${c.location ? hrs(c.on_board_hours) : "—"}</div><div class="l">${c.location ? "на текущем борту (" + esc(c.location.aircraft_name) + ")" : "не установлен"}</div></div>
      <div class="s"><div class="v">${c.boards_count}</div><div class="l">бортов в истории</div></div>
    </div>
    <div class="small muted" style="margin-top:10px">Индивидуальный ресурс: <b>${resourceText(c.resource) || "не задан"}</b> · Поставлен на учёт ${fmtD(c.in_service_at)}${c.initial_hours || c.initial_cycles ? ` с начальной наработкой ${hrs(c.initial_hours)} / ${c.initial_cycles} ц.` : ""}
      ${c.location ? ` · сейчас: <a href="#/aircraft/${c.location.aircraft_id}">${esc(c.location.aircraft_name)}</a>, позиция ${esc(c.location.position)} с ${fmtDT(c.location.installed_at)}` : ""}${c.notes ? " · " + esc(c.notes) : ""}</div></div>
    ${passportHTML(c)}
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
    <div class="card"><div class="tabs" id="tabs"><button class="active" data-t="rec">История обслуживания (${c.maintenance_records.length})</button><button data-t="fl">Начисленные полёты и наработка (${c.flight_log.length})</button></div><div id="tab"></div></div>`;
  $("#due").innerHTML = dueTable(c.due, { noEntity: true, empty: "Для типа узла не задано регламентов" });
  bindDueActions($("#due"), c.due, reload);
  const back = () => { const h = `#/components/${c.id}?tab=fl`; if (location.hash === h) reload(); else location.hash = h; };
  const tabs = {
    rec: () => { $("#tab").innerHTML = recordsTable(c.maintenance_records); },
    fl: () => {
      $("#tab").innerHTML = flightsTable(c.flight_log, { toolbar: '<button class="primary sm" id="add-fl">+ Записать полёт</button><button class="sm" id="add-entry">+ Наработка вне борта (стенд, формуляр)</button>' });
      $("#add-fl").onclick = () => openManualFlightModal({ component: c, onDone: back });
      $("#add-entry").onclick = () => openUsageEntryModal({ component: c, onDone: back });
      bindFlightDeletes($("#tab"), c.id, back);
    },
  };
  const pick = (k) => { $$("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === k)); tabs[k](); };
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => pick(b.dataset.t)));
  pick(new URLSearchParams(location.hash.split("?")[1] || "").get("tab") === "fl" ? "fl" : "rec");
  $("#edit-pass").onclick = () => openPassportModal(c, reload);
  $$("#acts [data-a]").forEach((b) => b.addEventListener("click", async () => {
    const a = b.dataset.a;
    if (a === "transfer") openInstallModal({ component: c, mode: "transfer", onDone: reload });
    if (a === "install") openInstallModal({ component: c, mode: "install", onDone: reload });
    if (a === "remove") openRemoveModal({ component: c, onDone: reload });
    if (a === "mnt") openMaintenanceModal({ target: "component", entity: { ...c, kind: "component" }, rules, onDone: reload });
    if (a === "res") openResourceModal({ kind: "component", entity: c, onDone: reload });
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
  if (s.movements && s.movements.length) {
    h += `<h3>Применённые операции с узлами</h3><table><thead><tr><th>Дата</th><th>Операция</th><th>Узел</th><th>Борт / позиция</th><th>Результат</th></tr></thead><tbody>
      ${s.movements.map((m) => `<tr><td class="nowrap">${fmtDT(m.at)}</td><td><span class="badge ${m.op === "transfer" ? "blue" : m.op === "remove" ? "warn" : "ok"}">${esc(m.op_label)}</span></td>
        <td><a href="#/components/${m.component_id}">${esc(m.serial)}</a></td><td>${m.aircraft ? `<a href="#/aircraft/${m.aircraft_id}">${esc(m.aircraft)}</a> · ${esc(m.position)}` : "—"}</td>
        <td class="small muted">${esc(m.message)}</td></tr>`).join("")}</tbody></table>`;
  }
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
  if (s.sensor_events && s.sensor_events.length) {
    h += `<h3 style="margin-top:14px">Мониторинг датчиков: выявленные события</h3><table><tbody>${s.sensor_events.map((e) => `<tr>
      <td><a href="#/aircraft/${e.aircraft_id}">${esc(e.aircraft)}</a></td><td><span class="badge ${EV_BADGE[e.kind]}">${esc(e.kind_label)}</span></td>
      <td class="nowrap">${fmtDT(e.at)}</td><td class="num">${e.peak_g != null ? num(e.peak_g, 1) + " g" : "—"}</td><td class="small muted">${e.source === "imu" ? "анализ акселерометра" : "из журнала"}</td></tr>`).join("")}</tbody></table>`;
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
      <div class="card"><div class="card-head"><h2>Загрузить файл</h2><a class="small" href="/api/templates/movements.xlsx" download>⇩ Шаблон журнала движения узлов (Excel)</a></div>
        <div class="drop" id="drop"><div style="font-size:26px">⇪</div><div><b>Перетащите файл журнала</b> или нажмите для выбора</div>
          <div class="small muted">Полёты: JSON «tbo-flightlog/1.0», CSV, Excel, PDF · Движение узлов (установка / снятие / перестановка): Excel, PDF · до 20 МБ</div>
          <input type="file" id="file" accept=".json,.csv,.txt,.xlsx,.pdf" hidden></div>
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
async function pageMaintenance(tab) {
  let filter = "overdue,due_soon";
  tab = ["notif", "due", "rec"].includes(tab) ? tab : "notif";
  view().innerHTML = `<div class="page-head"><div><h1>ТО и уведомления</h1><div class="sub">Уведомления и список работ формируются автоматически по текущей наработке, циклам, календарю и событиям датчиков согласно регламентам</div></div></div>
    <div class="card"><div class="tabs" id="tabs"><button data-t="notif">🔔 Уведомления</button><button data-t="due">Все сроки ТО</button><button data-t="rec">Журнал выполненных работ</button></div>
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
  const show = { notif: () => renderNotifications($("#tb")), due: showDue, rec: showRec };
  const open = (k) => {
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === k));
    history.replaceState(null, "", k === "notif" ? "#/maintenance" : `#/maintenance?tab=${k}`);
    $("#tb").innerHTML = '<div class="loading">Расчёт…</div>';
    show[k]();
  };
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => open(b.dataset.t)));
  open(tab);
}

// ------------------------------------------------------------------ Регламенты
async function pageRules() {
  const [rules, m] = await Promise.all([GET("/api/rules"), meta()]);
  const lim = (r) => [r.interval_hours && `${num(r.interval_hours, 1)} ч`, r.interval_cycles && `${r.interval_cycles} цикл.`, r.interval_days && `${r.interval_days} дн.`, r.interval_events && `${r.interval_events} падение/столкн.`].filter(Boolean).join(" / ");
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
    <div class="row3"><div class="field"><label>Падений/столкновений (датчики)</label><input type="number" min="0" name="interval_events" value="${r.interval_events ?? ""}" placeholder="напр. 1"></div><div></div><div></div></div>
    <div class="row3"><div class="field"><label>Предупреждать за, ч</label><input type="number" step="0.1" min="0" name="warn_hours" value="${r.warn_hours ?? ""}"></div>
    <div class="field"><label>за, циклов</label><input type="number" min="0" name="warn_cycles" value="${r.warn_cycles ?? ""}"></div>
    <div class="field"><label>за, дней</label><input type="number" min="0" name="warn_days" value="${r.warn_days ?? ""}"></div></div>
    <div class="hint">Пустой порог не используется. Если порог предупреждения не задан — 10 % интервала.</div>
    <label style="margin-top:10px"><input type="checkbox" name="active" style="width:auto" ${r.active === false ? "" : "checked"}> Активен</label>`;
  function open(r) {
    const bg = modal(r ? "Изменение регламента" : "Новый регламент", form(r || {}), {
      okText: "Сохранить",
      onOk: async (root) => {
        const n = (k, int) => { const v = val(root, k); return v === "" ? null : (int ? parseInt(v, 10) : parseFloat(v)); };
        const target = val(root, "target"), type = val(root, "type");
        const body = { code: val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")), target, kind: val(root, "kind"),
          aircraft_type_id: target === "aircraft" && type ? +type : null, component_type_id: target === "component" && type ? +type : null,
          interval_hours: n("interval_hours"), interval_cycles: n("interval_cycles", 1), interval_days: n("interval_days", 1), interval_events: n("interval_events", 1),
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
      typeSel.innerHTML = `<option value="">${isAc ? "Все типы БПЛА" : "Все типы узлов"}</option>` +
        typeOptions(list, cur, isAc ? '<option value="__types">+ Новый тип БПЛА… (раздел «Типы»)</option>' : '<option value="__new">+ Новый тип узла…</option>');
      typeSel.dataset.prev = typeSel.value;
    };
    typeSel.onchange = () => {
      if (typeSel.value === "__types") { $$(".modal-bg").forEach((x) => x.remove()); location.hash = "#/types"; return; }
      if (typeSel.value === "__new") { quickComponentType(typeSel, (mm, id) => { m.component_types = mm.component_types; fill(); typeSel.value = id; typeSel.dataset.prev = id; }); return; }
      typeSel.dataset.prev = typeSel.value;
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
  view().innerHTML = `<div class="page-head"><div><h1>Статистика</h1>
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
      <div class="card"><h2>Налёт парка по месяцам${d.monthly.length ? `, ${d.monthly[0].month.slice(0, 4) === d.monthly[d.monthly.length - 1].month.slice(0, 4) ? d.monthly[0].month.slice(0, 4) : `${d.monthly[0].month.slice(0, 4)}–${d.monthly[d.monthly.length - 1].month.slice(0, 4)}`} г.` : ""}</h2>
        <div class="chart">${d.monthly.map((mo) => `<div class="col"><div class="tot">${num(mo.total, 0)}</div>
          ${names.map((n, i) => mo.by_aircraft[n] ? `<div class="seg" title="${esc(n)}: ${num(mo.by_aircraft[n], 1)} ч" style="height:${(mo.by_aircraft[n] / maxTotal) * 170}px;background:${PALETTE[i % PALETTE.length]}"></div>` : "").reverse().join("")}
          <div class="lbl">${MONTHS[+mo.month.slice(5) - 1]}<span class="yr">${mo.month.slice(0, 4)}</span></div></div>`).join("")}</div>
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
  // Формулы считает сервер (services/analytics.estimate); здесь только ввод параметров и вывод
  const params = () => { const p = {}; $$("[data-p]").forEach((i) => { p[i.dataset.p] = parseFloat(i.value) || 0; }); return p; };
  function showEstimate(e) {
    const p = e.params;
    $("#calc").innerHTML = `
      <div class="box"><div class="small muted">Предотвращённый ожидаемый ущерб (текущий срез)</div><div class="big-num">${rub(e.risk)}</div>
        <div class="formula">(${e.prevented_misses} предотвр. пропусков + ${e.overdue_in_operation} выявленных просрочек) × ${p.failure_probability} × (${rub(p.failure_cost)} + ${p.downtime_days_per_failure} дн × ${rub(p.downtime_cost_per_day)})</div></div>
      <div class="box"><div class="small muted">Экономия трудозатрат на учёт, в год</div><div class="big-num">${rub(e.labor)}</div>
        <div class="formula">(${num(e.flights_year, 0)} полётов × ${p.manual_minutes_per_flight} мин + ${num(e.ops_year, 0)} операций × ${p.manual_minutes_per_operation} мин) / 60 = ${num(e.labor_hours, 0)} ч × ${rub(p.engineer_rate)}</div></div>
      <div class="box total"><div class="small muted">Оценка эффекта для парка из ${e.aircraft} бортов</div><div class="big-num">${rub(e.total)}</div>
        <div class="formula">≈ ${rub(e.per_aircraft)} на борт. Не учтены: снижение штрафов за срыв заданий, рост остаточной стоимости узлов с подтверждённой историей, данные для производителя о надёжности.</div></div>`;
  }
  let timer = null, seq = 0;
  const recalc = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const my = ++seq;
      try { const e = await POST("/api/analytics/estimate", params()); if (my === seq) showEstimate(e); } catch (err) { $("#calc").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
    }, 350);
  };
  $$("[data-p]").forEach((i) => i.addEventListener("input", recalc));
  showEstimate(d.estimate);
  $("#save").onclick = async () => { try { await api("PUT", "/api/settings/economics", params()); toast("Параметры сохранены"); } catch (e) { toast(e.message, true); } };
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
    <div class="notice small">Рекомендуемый порядок: 1 → 10. Сценарии меняют данные; для повторного прогона нажмите «Сбросить демо-данные».</div>
    ${scenarioCard(1, "Расчёт наработки по журналу эксплуатации", `
      <li>Откройте ${link("TBT-M4-001")} и запомните налёт борта и наработку узлов в блоке «Конфигурация борта».</li>
      <li>Раздел <a href="#/imports">Импорт журналов</a> → демо-журнал «Журнал смены «Борт 01»» (<span class="mono">01_TBT-M4-001_smena.json</span>) → «Импорт».</li>
      <li>Ожидается: +3,52 ч и +8 циклов у борта и у каждого из 7 установленных узлов; в отчёте — сработавшие напоминания.</li>`)}
    ${scenarioCard(2, "Замена и перестановка узла между бортами", `
      <li>Откройте узел ${link("FC7-0102")} (полётный контроллер на «Борт 02», сквозная наработка ~134 ч, до ТО ~4 ч).</li>
      <li>«Переставить на другой борт» → ${link("TBT-M4-003")}, позиция FC (пустует — свой ПК в ремонте). <b>Дата операции: ${fmtD(d2.toISOString())}</b> (позавчера) — полёты журнала выполнены вчера, после перестановки.</li>
      <li>На карточке «Борт 03» смените статус на «В эксплуатации» и импортируйте демо-журнал <span class="mono">02_TBT-M4-003_posle_perestanovki.csv</span>.</li>
      <li>Ожидается: наработка узла = прежняя + 4,85 ч; в «Бортах в истории узла» — оба борта; межсервисный интервал не обнулился → ТО ПК «Требуется».</li>`)}
    ${scenarioCard(3, "Повторный импорт одного журнала", `
      <li>Повторно импортируйте <span class="mono">01_TBT-M4-001_smena.json</span> или его копию <span class="mono">03_TBT-M4-001_smena_POVTOR.json</span> → «Повтор — не начислено», счётчики не меняются.</li>
      <li>Импортируйте <span class="mono">04_TBT-M4-001_bortovoi_registrator.csv</span> — другая выгрузка тех же 8 полётов (без ID) + 2 новых: начисляются только 2 новых.</li>
      <li>Ошибочный импорт можно отменить кнопкой «Отменить» в журнале импортов.</li>`)}
    ${scenarioCard(4, "Срабатывание напоминаний по часам, циклам и календарю", `
      <li><b>Часы:</b> ${link("IMU3-0206")} — калибровка ИНС каждые 50 ч: до сценария 1 «Приближается», после — «Требуется».</li>
      <li><b>Циклы:</b> ${link("ESC40-0301")} — диагностика ESC каждые 200 циклов: 195 → 203 после сценария 1.</li>
      <li><b>Календарь:</b> ${link("TBT-VT2-011")} — годовое ТО (180 дн.) через ~9 дней; ${link("BVK2-0405")} — ТО вычислителя (120 дн.) просрочено.</li>
      <li><b>Назначенный ресурс:</b> ${link("ESC40-0318")} — 372 из 400 ч. Всё видно в разделе <a href="#/maintenance">ТО и уведомления</a>.</li>`)}
    ${scenarioCard(5, "Проведение обслуживания по напоминанию", `
      <li>В <a href="#/maintenance?tab=due">ТО и уведомления → Все сроки ТО</a> найдите «ТО бортового вычислителя» для ${link("BVK2-0405")} (${link("TBT-VT2-012")}) → «Выполнить».</li>
      <li>Ожидается: напоминание уходит в «В норме», интервал отсчитывается от даты работы, сквозная наработка узла не изменилась, «Борт 12» снова допущен к полётам.</li>
      <li>Аналогично можно выполнить калибровку ${link("IMU3-0206")} после сценария 1.</li>`)}
    <h2 style="margin:22px 0 10px">Критерии конкурсного задания</h2>
    ${scenarioCard(6, "Автономный полёт по заданному маршруту (К1, К4)", `
      <li>Раздел <a href="#/missions">Миссии и автономный полёт</a> → «Облёт ЛЭП, пролёты 1–4» (${link("TBT-M4-001")}). Щелчком по карте добавьте точку, перетащите её, задайте высоту и удержание.</li>
      <li>«▶ Выполнить автономный полёт»: предполётная проверка допуска → взлёт, полёт по точкам с удержанием, возврат (RTL), посадка — трек на карте и телеметрия.</li>
      <li>Ожидается: полёт записан в журнал, борту и всем установленным узлам начислены часы и 1 цикл; файл миссии выгружается в .plan / .waypoints (QGroundControl, ArduPilot, PX4).</li>`)}
    ${scenarioCard(7, "Падение по данным датчиков и запрет вылета (К3, К7)", `
      <li><a href="#/imports">Импорт журналов</a> → <span class="mono">07_TBT-VT2-011_padenie.json</span>: в полёте записан акселерометр 50 Гц.</li>
      <li>Ожидается: выявлено падение (невесомость → удар ≈ 7,6 g), счётчик «падений / столкновений» ${link("TBT-VT2-011")} = 1, уведомления «Внеплановый осмотр» (борт) и «Проверка ИНС после удара» (${link("IMU3-0204")}).</li>
      <li>В <a href="#/missions">Миссиях</a> полёт «Аэрофотосъёмка участка №12» на «Борт 11» отклоняется предполётной проверкой до выполнения осмотра.</li>`)}
    ${scenarioCard(8, "Журнал установок/снятий/перестановок из Excel и PDF (К5, К6)", `
      <li><a href="#/imports">Импорт журналов</a> → <span class="mono">08_dvizhenie_uzlov.xlsx</span>: 6 строк — 5 операций применены в хронологическом порядке, строка с несуществующим узлом FC7-9999 отклонена с причиной.</li>
      <li>Затем <span class="mono">09_dvizhenie_uzlov.pdf</span> — тот же журнал в PDF: все строки распознаны как уже применённые (повтор), ничего не задвоено.</li>
      <li>Ожидается: ${link("ESC40-0320")} на «Борт 11 · ESC4», в истории узла оба борта; ${link("ESC40-0302")} в ремонте; наработка и остаточный ресурс пересчитаны.</li>`)}
    ${scenarioCard(9, "Интерактивная перестановка узла между карточками бортов (К10)", `
      <li><a href="#/board">Перестановка узлов</a> → перетащите регулятор ${link("ESC40-0301")} (Борт 01 · ESC1) на позицию ESC1 «Борт 02» — подсвечиваются только совместимые позиции; занятая позиция — обмен узлами.</li>
      <li>Подтвердите дату и основание. Ожидается: карточки обоих бортов обновлены, таблица «было → стало» по паспортам: местоположение изменилось, наработка и UID сохранены, остаточный ресурс пересчитан.</li>
      <li>С прошедшей датой операции полёты после неё переотносятся — наработка узлов меняется соответственно.</li>`)}
    ${scenarioCard(10, "Автоматические уведомления (К7)", `
      <li>Раздел <a href="#/maintenance">ТО и уведомления</a> → «Уведомления»: «Достигнут предельный ресурс» (${link("ESC40-0315")} — индивидуальный ресурс 150 ч исчерпан), «Просрочено» (${link("BVK2-0405")}), «Приближается» (${link("ESC40-0318")}, ${link("IMU3-0206")} …).</li>
      <li>После импорта журнала или автономного полёта новые позиции появляются всплывающим сообщением (и уведомлением браузера, если оно разрешено); счётчик в меню обновляется каждую минуту.</li>`)}`;
  $("#reset2").onclick = resetDemo;
  $$("[data-run]").forEach((b) => b.addEventListener("click", async () => {
    const out = $(`#out${b.dataset.run}`);
    b.disabled = true;
    out.innerHTML = '<div class="loading">Выполняется…</div>';
    try { const r = await POST(`/api/scenarios/${b.dataset.run}/run`); out.innerHTML = renderBlocks(r.blocks); AIRCRAFT_CACHE = null; refreshNavCount(); } catch (e) { out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; } finally { b.disabled = false; }
  }));
}

function scenarioCard(n, title, steps) {
  return `<div class="card scenario"><div class="card-head"><h2>${n}. ${esc(title)}</h2><button class="primary sm" data-run="${n}">▶ Выполнить автоматически</button></div>
    <div class="small"><b>Вручную:</b><ol>${steps}</ol></div><div class="out" id="out${n}"></div></div>`;
}

// Результат сценария строит сервер (app/services/scenarios.py); здесь только отображение блоков
function diffTable(rows) {
  return `<table><thead><tr><th>Объект</th><th class="num">До</th><th class="num">После</th><th class="num">Изменение</th></tr></thead><tbody>
    ${rows.map((r) => {
      const dh = r.after.hours - r.before.hours, dc = r.after.cycles - r.before.cycles;
      const label = r.href ? `<a href="${esc(r.href)}"><b>${esc(r.label)}</b></a>` : `<b>${esc(r.label)}</b>`;
      return `<tr><td>${label}${r.sub ? ` <span class="small muted">${esc(r.sub)}</span>` : ""}</td><td class="num">${hrs(r.before.hours)} · ${r.before.cycles} ц.</td><td class="num">${hrs(r.after.hours)} · ${r.after.cycles} ц.</td>
        <td class="num ${dh || dc ? "delta-up" : "muted"}">${dh || dc ? `+${num(dh, 2)} ч · +${dc} ц.` : "без изменений"}</td></tr>`;
    }).join("")}</tbody></table>`;
}
function readinessHTML(r) { return `<span class="dot ${r.level === "ok" ? "" : r.level}"></span>${esc(r.text)}`; }
function cellHTML(c) {
  if (c === null || c === undefined) return "";
  if (typeof c !== "object") return esc(c);
  let h;
  if (c.due) h = dueBadge(c.due);
  else if (c.readiness) h = readinessHTML(c.readiness);
  else if (c.metric) h = metricHTML(c.metric);
  else if (c.entity) h = entityLink(c.entity);
  else if (c.imp_status) h = `<span class="badge ${IMP_ST[c.imp_status]}">${esc(c.text)}</span>`;
  else if (c.dt) h = fmtDT(c.dt);
  else h = esc(c.text ?? "");
  if (c.href) h = `<a href="${esc(c.href)}">${h}</a>`;
  if (c.bold) h = `<b>${h}</b>`;
  if (c.mono) h = `<span class="mono">${h}</span>`;
  return h + (c.sub ? `<div class="small muted">${esc(c.sub)}</div>` : "");
}
function renderBlocks(blocks) {
  const link = (b) => (b.href ? ` <a href="${esc(b.href)}">${esc(b.link || "Открыть →")}</a>` : "");
  return blocks.map((b) => {
    switch (b.t) {
      case "notice": return `<div class="notice ${b.level || ""}">${esc(b.text)}${link(b)}</div>`;
      case "p": return `<p class="${b.small ? "small" : ""}">${esc(b.text)}${link(b)}</p>`;
      case "h3": return `<h3 style="margin-top:12px">${esc(b.text)}</h3>`;
      case "diff": return diffTable(b.rows);
      case "table": return `<div class="table-wrap"><table><thead><tr>${b.head.map((x) => `<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>
        ${b.rows.map((r) => `<tr>${r.map((c) => `<td>${cellHTML(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      case "import": {
        const h = importResultHTML(b.data);
        if (b.part === "full") return h;
        if (b.part === "sensors") return h.split("<h3").slice(1).filter((x) => /Мониторинг|Сработавшие/.test(x)).map((x) => "<h3" + x.replace(/<\/div>\s*$/, "")).join("");
        return h.split("<h3")[0];
      }
      case "move": return moveResultHTML(b.data, false);
      default: return "";
    }
  }).join("");
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
