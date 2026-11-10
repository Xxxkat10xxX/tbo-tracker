/* tbo-tracker · page-analytics.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
          ${names.map((n, i) => mo.by_aircraft[n] ? `<div class="seg" title="${esc(n)}: ${hrs(mo.by_aircraft[n])}" data-h="${Math.round((mo.by_aircraft[n] / maxTotal) * 170)}" data-bg="${PALETTE[i % PALETTE.length]}"></div>` : "").reverse().join("")}
          <div class="lbl">${MONTHS[+mo.month.slice(5) - 1]}<span class="yr">${mo.month.slice(0, 4)}</span></div></div>`).join("")}</div>
        <div class="legend">${names.map((n, i) => `<span><i data-bg="${PALETTE[i % PALETTE.length]}"></i>${esc(n)}</span>`).join("")}</div>
        <div class="small muted mt-8">Часы налёта, загруженные из журналов эксплуатации.</div></div>
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
