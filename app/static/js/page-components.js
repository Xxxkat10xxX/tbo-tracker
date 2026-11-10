/* tbo-tracker · page-components.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
      <input id="q" placeholder="Поиск по серийному номеру" class="minw-220"></div><div id="list"></div></div>`;
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
        <td class="small nowrap minw-150">${residualText(c.residual, true)}${residualBar(c.residual)}</td>
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
    <div class="row2"><div class="field"><label>Наработка до постановки на учёт, ч</label><input type="number" min="0" step="0.01" name="initial_hours" value="0">
      <div class="hint">Стендовые испытания, эксплуатация в другом парке (по формуляру)</div></div>
    <div class="field"><label>Циклы до постановки на учёт</label><input type="number" min="0" name="initial_cycles" value="0"></div></div>
    <div class="field"><label>Дата изготовления</label><input type="date" name="manufactured_at" class="maxw-220"></div>
    <h3>Основные технические характеристики</h3><div id="spec-box"></div>
    <h3 class="mt-14">Установленный ресурс</h3>${resourceFields()}`, {
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
    <div class="small muted mt-10">Индивидуальный ресурс: <b>${resourceText(c.resource) || "не задан"}</b> · Поставлен на учёт ${fmtD(c.in_service_at)}${c.initial_hours || c.initial_cycles ? ` с начальной наработкой ${hrs(c.initial_hours)} / ${c.initial_cycles} ц.` : ""}
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
