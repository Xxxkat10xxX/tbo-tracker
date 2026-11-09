/* tbo-tracker · page-maintenance.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ ТО
async function pageMaintenance(tab) {
  let filter = "overdue,due_soon";
  tab = ["notif", "due", "rec"].includes(tab) ? tab : "notif";
  view().innerHTML = `<div class="page-head"><div><h1>ТО и уведомления</h1><div class="sub">Уведомления и список работ формируются автоматически по текущей наработке, циклам, календарю и событиям датчиков согласно регламентам</div></div></div>
    <div class="card"><div class="tabs" id="tabs"><button data-t="notif">🔔 Уведомления</button><button data-t="due">Все сроки ТО</button><button data-t="rec">Журнал выполненных работ</button></div>
    <div id="tb"></div></div>`;
  let tabSeq = 0;
  const alive = (my) => my === tabSeq && $("#tb");       // вкладку не сменили и страница ещё открыта
  async function showDue() {
    const my = tabSeq;
    $("#tb").innerHTML = `<div class="toolbar"><div class="chips" id="chips">${[["overdue,due_soon", "Требуют внимания"], ["overdue", "Требуется / просрочено"], ["due_soon", "Приближается"], ["", "Все регламенты"]]
      .map(([k, v]) => `<button data-f="${k}" class="${k === filter ? "active" : ""}">${v}</button>`).join("")}</div></div><div id="list"><div class="loading">Расчёт…</div></div>
      <div class="hint">«Приближается» — остаток по любому счётчику меньше порога предупреждения из регламента (по умолчанию 10 % интервала). «Требуется / просрочено» — порог достигнут. Прогноз даты — линейный, по средней интенсивности за 30 дней.</div>`;
    $$("#chips button").forEach((b) => b.addEventListener("click", () => { filter = b.dataset.f; showDue(); }));
    const items = await GET(`/api/maintenance/due?status=${filter}`);
    if (!alive(my) || !$("#list")) return;
    $("#list").innerHTML = dueTable(items);
    bindDueActions($("#list"), items, showDue);
  }
  async function showRec() {
    const my = tabSeq, rows = await GET("/api/maintenance/records?limit=300");
    if (alive(my)) $("#tb").innerHTML = recordsTable(rows);
  }
  const show = { notif: () => renderNotifications($("#tb"), () => tabSeq), due: showDue, rec: showRec };
  const open = (k) => {
    tabSeq++;
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === k));
    history.replaceState(null, "", k === "notif" ? "#/maintenance" : `#/maintenance?tab=${k}`);
    $("#tb").innerHTML = '<div class="loading">Расчёт…</div>';
    show[k]();
  };
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => open(b.dataset.t)));
  open(tab);
}
