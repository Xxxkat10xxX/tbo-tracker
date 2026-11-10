/* tbo-tracker · page-imports.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
    h += `<h3 class="mt-14">Начисление узлам, установленным на момент полётов</h3><table><thead><tr><th>Узел</th><th>Борт / позиция</th><th class="num">Полётов</th><th class="num">Часы</th><th class="num">Циклы</th></tr></thead><tbody>
      ${s.components.map((c) => `<tr><td><a href="#/components/${c.component_id}">${esc(c.serial)}</a> <span class="small muted">${esc(c.type)}</span></td><td>${esc(c.aircraft)} · ${esc(c.position)}</td>
        <td class="num">${c.flights}</td><td class="num">+${num(c.hours, 2)}</td><td class="num">+${c.cycles}</td></tr>`).join("")}</tbody></table>`;
  }
  if (s.sensor_events && s.sensor_events.length) {
    h += `<h3 class="mt-14">Мониторинг датчиков: выявленные события</h3><table><tbody>${s.sensor_events.map((e) => `<tr>
      <td><a href="#/aircraft/${e.aircraft_id}">${esc(e.aircraft)}</a></td><td><span class="badge ${EV_BADGE[e.kind]}">${esc(e.kind_label)}</span></td>
      <td class="nowrap">${fmtDT(e.at)}</td><td class="num">${e.peak_g != null ? num(e.peak_g, 1) + " g" : "—"}</td><td class="small muted">${e.source === "imu" ? "анализ акселерометра" : "из журнала"}</td></tr>`).join("")}</tbody></table>`;
  }
  if (s.alerts && s.alerts.length) {
    h += `<h3 class="mt-14">Сработавшие напоминания</h3><table><tbody>${s.alerts.map((a) => `<tr><td>${entityLink(a.entity)}</td><td>${esc(a.rule.name)}</td>
      <td class="nowrap">${a.previous_status ? dueBadge(a.previous_status) + " → " : ""}${dueBadge(a.status)}</td><td>${a.driver ? metricHTML(a.driver) : ""}</td></tr>`).join("")}</tbody></table>`;
  }
  return h;
}

async function pageImports() {
  // демо-журналы есть только в демо-режиме: в рабочем /api/demo-logs отвечает 404 — страница не должна из-за этого падать
  const [list, demo] = await Promise.all([GET("/api/imports"), AUTH.demo ? GET("/api/demo-logs") : Promise.resolve(null)]);
  view().innerHTML = `<div class="page-head"><div><h1>Импорт журналов эксплуатации</h1><div class="sub">Наработка автоматически начисляется борту и всем узлам, стоявшим на нём в момент каждого полёта</div></div></div>
    <div class="grid g2">
      <div class="card"><div class="card-head"><h2>Загрузить файл</h2><a class="small" href="/api/templates/movements.xlsx" download>⇩ Шаблон журнала движения узлов (Excel)</a></div>
        <div class="drop" id="drop"><div class="fs-26">⇪</div><div><b>Перетащите файл журнала</b> или нажмите для выбора</div>
          <div class="small muted">Полёты: JSON «tbo-flightlog/1.0», CSV, Excel, PDF · Движение узлов (установка / снятие / перестановка): Excel, PDF · до 20 МБ</div>
          <input type="file" id="file" accept=".json,.csv,.txt,.xlsx,.pdf" hidden></div>
        <div id="fname" class="small my-8"></div>
        <div class="actions"><button id="check" disabled>Проверить без записи</button><button class="primary" id="go" disabled>Импортировать</button></div>
        <div class="hint">Защита от двойного начисления: повторная загрузка того же файла (SHA-256) и полёты, уже учтённые из других выгрузок (по ID и пересечению по времени), не начисляются.</div></div>
      ${demo ? `      <div class="card"><h2>Демонстрационные журналы</h2><div class="small muted mb-8">Сгенерированы относительно даты заполнения демо-данных. Можно скачать и загрузить вручную или импортировать сразу.</div>
        <table><tbody>${demo.map((d) => `<tr><td><b>${esc(d.title)}</b><div class="small muted">${esc(d.description)}</div><div class="mono small">${esc(d.file)}</div></td>
          <td class="nowrap"><a class="btn sm" href="/api/demo-logs/${encodeURIComponent(d.file)}" download>Скачать</a>
          <button class="sm primary" data-demo="${esc(d.file)}">Импорт</button></td></tr>`).join("")}</tbody></table></div>` : ""}
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
