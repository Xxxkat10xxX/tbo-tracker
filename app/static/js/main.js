/* tbo-tracker · main.js — запуск: состояние входа → экран входа/настройки или приложение. */
"use strict";

let POLL_TIMER = null;
function startPolling() {
  stopPolling();
  refreshNavCount();
  POLL_TIMER = setInterval(refreshNavCount, 60000);
}
function stopPolling() {
  if (POLL_TIMER) clearInterval(POLL_TIMER);
  POLL_TIMER = null;
}

async function enterApp() {
  if (AUTH.user && AUTH.user.must_change_password) { showPasswordScreen(true); return; }
  AUTH_LOST_SHOWN = false;
  document.body.classList.remove("auth-mode");
  $("#auth-screen").hidden = true;
  $("#auth-screen").innerHTML = "";
  $("#app-layout").hidden = false;
  renderUserBox();
  applyNavRights();
  META = null; AIRCRAFT_CACHE = null; NOTIF_LAST = null;
  startPolling();
  await router();
}

async function resetDemo() {
  modal("Сброс демо-данных", "<p>Пересоздать базу с демонстрационными данными? Все изменения данных будут потеряны (учётные записи сохранятся). Даты демо-данных пересчитаются относительно текущего момента.</p>", {
    okText: "Сбросить",
    onOk: async () => { await POST("/api/demo/reset"); META = null; toast("Демо-данные пересозданы"); refreshNavCount(); reload(); },
  });
}

async function boot() {
  $("#reset-demo").onclick = resetDemo;
  window.addEventListener("hashchange", () => { if (AUTH.user) router(); });
  await refreshAuthState();
  if (AUTH.setup) showSetup();
  else if (!AUTH.user) showLogin();
  else await enterApp();
}
boot();
