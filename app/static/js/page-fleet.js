/* tbo-tracker · page-fleet.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
  return `<div class="table-wrap"><table class="fleet"><thead><tr><th class="w-84"></th><th>Борт</th><th>Тип</th><th>Статус</th><th>Готовность (борт + узлы)</th><th class="num">Налёт</th><th class="num">Циклы</th><th class="num">Полёты</th><th>Базирование</th></tr></thead><tbody>
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
    <div class="row2"><div class="field"><label>Начальный налёт, ч</label><input type="number" step="0.01" min="0" name="initial_hours" value="0"></div>
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
    <div class="page-head"><div><h1>${esc(a.name)} <span class="muted mono fs-15">${esc(a.serial)}</span></h1>
      <div class="sub">${esc(a.type.name)} · ${esc(a.operator || "")} · ${esc(a.base || "")} · в эксплуатации с ${fmtD(a.in_service_at)}</div></div>
      <div class="actions"><select id="st" class="w-auto">${Object.entries(m.aircraft_statuses).map(([k, v]) => `<option value="${k}" ${k === a.status ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
      <button id="res">⚙ Ресурс</button><button id="mnt">⚑ Зарегистрировать работу</button></div></div>
    <div class="card ac-hero"><div class="ac-photo">${droneImg(a, "cover")}<button class="sm ac-photo-btn" id="photo">🖼 Изменить фото</button></div>
    <div class="ac-hero-body"><div class="stat-line">
      <div class="s"><div class="v">${hrs(a.hours)}</div><div class="l">суммарный налёт</div></div>
      <div class="s"><div class="v">${num(a.cycles, 0)}</div><div class="l">циклов (взлёт-посадка)</div></div>
      <div class="s"><div class="v">${num(a.flights, 0)}</div><div class="l">полётов в журналах</div></div>
      <div class="s"><div class="v">${a.slots_filled}/${a.slots_total}</div><div class="l">позиций укомплектовано</div></div>
      <div class="s"><div class="v c-bad"${a.events.fall + a.events.collision ? '' : ""}>${a.events.fall} / ${a.events.collision}</div><div class="l">падений / столкновений</div></div>
      <div class="s"><div class="v fs-15 pt-4">${readiness(a)}</div><div class="l">допуск к полётам</div></div>
    </div><div class="small muted mt-10">Индивидуальный ресурс борта: <b>${resourceText(a.resource) || "не задан"}</b></div>
    ${a.notes ? `<div class="notice small mt-12 mb-0">${esc(a.notes)}</div>` : ""}</div></div>
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
      <div class="ser muted">Не установлен</div>${s.required === false ? '<div class="small muted">Необязательная позиция</div>' : '<div class="small c-bad">Позиция пуста — борт некомплектен</div>'}
      <div class="actions"><button class="sm primary" data-act="install" data-pos="${esc(s.position)}">+ Установить</button></div></div>`;
  }
  const c = s.component;
  return `<div class="slot"><div class="pos"><span>${esc(s.label)}</span><span>${esc(s.position)}</span></div>
    <div class="ser"><a href="#/components/${c.id}">${esc(c.serial)}</a> ${dueBadge(c.maintenance.status)}</div>
    <div class="nums"><span>Всего: <b>${hrs(c.hours)}</b></span><span><b>${num(c.cycles, 0)}</b> ц.</span></div>
    <div class="nums"><span>На этом борту: <b>${hrs(c.on_board_hours)}</b></span></div>
    ${c.maintenance.status !== "ok" ? `<div class="small c-${c.maintenance.status === "overdue" ? "bad" : "warn"}">${esc(c.maintenance.rule)}</div>` : ""}
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
    <div class="row2"><div class="field"><label>Часы</label><input type="number" min="0" step="0.01" name="hours" value="1"></div>
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
