/* tbo-tracker · router.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

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
  [/^#\/users$/, "users", pageUsers],
  [/^#\/audit$/, "audit", pageAudit],
  [/^#\/missions$/, "missions", pageMissions],
  [/^#\/missions\/(\d+)$/, "missions", pageMission],
  [/^#\/drones$/, "drones", pageDrones],
];
async function router() {
  $$(".modal-bg").forEach((m) => m.remove());     // окна не переживают переход на другую страницу
  const my = ++NAV_SEQ;
  const hash = location.hash || "#/";
  for (const [re, key, fn] of routes) {
    const m = hash.match(re);
    if (m) {
      $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.r === key));
      view().innerHTML = '<div class="loading">Загрузка…</div>';
      try { await fn(...m.slice(1)); } catch (e) {
        if (e instanceof StaleNav || my !== NAV_SEQ) return;       // пользователь уже на другой странице
        view().innerHTML = `<div class="notice bad">Ошибка: ${esc(e.message)}</div>`;
      }
      if (my === NAV_SEQ) window.scrollTo(0, 0);
      return;
    }
  }
  view().innerHTML = '<div class="notice">Страница не найдена</div>';
}
const reload = () => router();
