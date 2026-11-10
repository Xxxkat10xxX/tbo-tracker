/* tbo-tracker · page-scenarios.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
    <h2 class="m-section">Критерии конкурсного задания</h2>
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
        <td class="num ${dh || dc ? "delta-up" : "muted"}">${dh || dc ? `+${hrs(dh)} · +${dc} ц.` : "без изменений"}</td></tr>`;
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
      case "h3": return `<h3 class="mt-12">${esc(b.text)}</h3>`;
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
