/* tbo-tracker · ui-modal.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
