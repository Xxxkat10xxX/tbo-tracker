/* tbo-tracker · page-generator.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
        <label><input type="checkbox" name="ids" checked class="w-auto"> Указывать flight_id</label>
        <label><input type="checkbox" name="overlap" checked class="w-auto"> Не пересекаться с уже учтёнными полётами</label>
        <label><input type="checkbox" name="errors" class="w-auto"> Добавить ошибочные строки (неизвестный борт, посадка раньше взлёта, будущее, битая дата)</label>
        <div class="actions mt-14"><button id="gen">Сгенерировать</button><button id="dl" disabled>⇩ Скачать файл</button>
          <button id="check" disabled>Проверить импорт (без записи)</button><button class="primary" id="imp" disabled data-demo>⇪ Импортировать</button></div>
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
      <details><summary>Содержимое файла</summary><pre class="mono scroll-box">${esc(r.content.slice(0, 20000))}</pre></details>`;
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
