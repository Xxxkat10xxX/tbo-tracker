/* tbo-tracker · auth.js — вход, первичная настройка, смена пароля, блок пользователя.
   Пароли проверяет и хранит (Argon2id) только сервер; браузер получает HttpOnly-cookie сессии и CSRF-токен. */
"use strict";

const PASSWORD_HINT = "Не короче 10 символов, не из списка распространённых, без имени пользователя. Удобно — фраза из нескольких слов.";

function authScreen(html) {
  document.body.classList.add("auth-mode");
  $("#app-layout").hidden = true;
  const box = $("#auth-screen");
  box.hidden = false;
  box.innerHTML = `<div class="auth-card"><div class="auth-brand"><b>tbo-tracker</b><span>Обслуживание БПЛА по фактической наработке</span></div>${html}</div>`;
  const first = $("input", box);
  if (first) first.focus();
  return box;
}

function formError(box, msg) {
  const el = $(".auth-error", box);
  el.textContent = msg || "";
  el.hidden = !msg;
}

async function refreshAuthState() {
  try {
    const st = await api("GET", "/api/auth/state", undefined, false, true);
    Object.assign(AUTH, { user: st.user, csrf: st.csrf_token, demo: st.demo_mode, docs: st.api_docs, roles: st.roles, setup: st.setup_required });
    return !!st.user;
  } catch (_) { return false; }
}

let AUTH_LOST_SHOWN = false;
function onAuthLost() {
  if (AUTH_LOST_SHOWN) return;
  AUTH_LOST_SHOWN = true;
  AUTH.user = null; AUTH.csrf = null;
  stopPolling();
  showLogin("Сессия завершена — войдите снова");
}

function showLogin(note) {
  const box = authScreen(`<h1>Вход</h1>
    ${note ? `<div class="notice warn small">${esc(note)}</div>` : ""}
    <form id="login-form" method="post" action="/" autocomplete="on" novalidate>
      <div class="field"><label for="lg-user">Имя пользователя</label><input id="lg-user" name="username" autocomplete="username" required maxlength="64"></div>
      <div class="field"><label for="lg-pass">Пароль</label><input id="lg-pass" name="password" type="password" autocomplete="current-password" required maxlength="256"></div>
      <div class="auth-error notice bad small" hidden></div>
      <button class="primary wide" type="submit">Войти</button>
    </form>
    <p class="small muted mt-12">Нет учётной записи — обратитесь к администратору. Забыли пароль — администратор задаст новый.</p>`);
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", box);
    btn.disabled = true;
    formError(box, "");
    try {
      const r = await api("POST", "/api/auth/login", { username: val(box, "username"), password: $("#lg-pass").value }, false, true);
      AUTH.user = r.user; AUTH.csrf = r.csrf_token;
      await enterApp();
    } catch (err) {
      formError(box, err.message);
      $("#lg-pass").value = "";
      $("#lg-pass").focus();
    } finally { btn.disabled = false; }
  });
}

function showSetup() {
  const box = authScreen(`<h1>Первичная настройка</h1>
    <p class="small">Учётных записей ещё нет. Создайте администратора. Одноразовый <b>код настройки</b> напечатан в журнале сервиса
      и записан в файл <span class="mono">setup-code.txt</span> каталога данных:</p>
    <ul class="small">
      <li>Docker: <span class="mono">docker compose logs app | grep -A1 "код настройки"</span></li>
      <li>Raspberry Pi: <span class="mono">sudo tbo-ctl setup-code</span></li>
    </ul>
    <form id="setup-form" method="post" action="/" novalidate>
      <div class="field"><label for="st-code">Код настройки</label><input id="st-code" name="code" autocomplete="one-time-code" required maxlength="64" placeholder="XXXX-XXXX-XXXX-XXXX-XXXX"></div>
      <div class="row2"><div class="field"><label for="st-user">Имя пользователя</label><input id="st-user" name="username" autocomplete="username" required maxlength="64" placeholder="напр. ivanov"></div>
        <div class="field"><label for="st-name">Как вас называть</label><input id="st-name" name="display_name" maxlength="200" placeholder="Иванов И. И."></div></div>
      <div class="row2"><div class="field"><label for="st-p1">Пароль</label><input id="st-p1" type="password" autocomplete="new-password" required maxlength="128"></div>
        <div class="field"><label for="st-p2">Ещё раз</label><input id="st-p2" type="password" autocomplete="new-password" required maxlength="128"></div></div>
      <div class="hint">${PASSWORD_HINT}</div>
      <div class="auth-error notice bad small" hidden></div>
      <button class="primary wide" type="submit">Создать администратора и войти</button>
    </form>`);
  $("#setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("#st-p1").value !== $("#st-p2").value) { formError(box, "Пароли не совпадают"); return; }
    const btn = $("button[type=submit]", box);
    btn.disabled = true;
    try {
      const r = await api("POST", "/api/auth/setup", { code: val(box, "code"), username: val(box, "username"),
        display_name: val(box, "display_name"), password: $("#st-p1").value }, false, true);
      AUTH.user = r.user; AUTH.csrf = r.csrf_token;
      await enterApp();
      toast("Администратор создан. Добавьте сотрудников в разделе «Пользователи»");
    } catch (err) { formError(box, err.message); } finally { btn.disabled = false; }
  });
}

function showPasswordScreen(forced) {
  const html = `<h1>${forced ? "Смените временный пароль" : "Смена пароля"}</h1>
    ${forced ? '<p class="small">Пароль задан администратором. Придумайте свой — после этого откроется работа с данными.</p>' : ""}
    <form id="pw-form" method="post" action="/" novalidate>
      <input type="text" name="username" autocomplete="username" value="${esc(AUTH.user ? AUTH.user.username : "")}" hidden>
      <div class="field"><label for="pw-cur">Текущий пароль</label><input id="pw-cur" type="password" autocomplete="current-password" required maxlength="256"></div>
      <div class="row2"><div class="field"><label for="pw-1">Новый пароль</label><input id="pw-1" type="password" autocomplete="new-password" required maxlength="128"></div>
        <div class="field"><label for="pw-2">Ещё раз</label><input id="pw-2" type="password" autocomplete="new-password" required maxlength="128"></div></div>
      <div class="hint">${PASSWORD_HINT} Сессии на других устройствах будут завершены.</div>
      <div class="auth-error notice bad small" hidden></div>
      <div class="actions"><button class="primary" type="submit">Сменить пароль</button>${forced ? '<button type="button" id="pw-logout">Выйти</button>' : '<button type="button" id="pw-cancel">Отмена</button>'}</div>
    </form>`;
  const box = forced ? authScreen(html) : modal("Смена пароля", html.replace(/<h1>.*?<\/h1>/, ""), {});
  const root = forced ? box : $(".modal-b", box);
  if (!forced) $(".modal-f", box).hidden = true;
  $("#pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("#pw-1").value !== $("#pw-2").value) { formError(root, "Пароли не совпадают"); return; }
    try {
      const r = await api("POST", "/api/auth/password", { current_password: $("#pw-cur").value, new_password: $("#pw-1").value }, false, true);
      toast(r.message);
      if (AUTH.user) AUTH.user.must_change_password = false;
      if (forced) await enterApp(); else box.remove();
    } catch (err) { formError(root, err.message); }
  });
  const lo = $("#pw-logout");
  if (lo) lo.onclick = logout;
  const cancel = $("#pw-cancel");
  if (cancel) cancel.onclick = () => box.remove();
}

async function logout() {
  try { await api("POST", "/api/auth/logout", undefined, false, true); } catch (_) { /* сессия уже завершена */ }
  AUTH.user = null; AUTH.csrf = null;
  stopPolling();
  location.hash = "#/";
  showLogin("Вы вышли из системы");
}

function renderUserBox() {
  const u = AUTH.user;
  $("#user-box").innerHTML = `<div class="user-name">${esc(u.display_name)}</div>
    <div class="small user-role">${esc(u.role_label)}${u.role === "viewer" ? " · только просмотр" : ""}</div>
    <div class="actions"><button class="sm" id="ub-pass">Сменить пароль</button><button class="sm" id="ub-out">Выйти</button></div>`;
  $("#ub-pass").onclick = () => showPasswordScreen(false);
  $("#ub-out").onclick = logout;
}

// Пункты меню по ролям и режимам: data-need="admin|engineer", data-demo, data-docs
function applyNavRights() {
  $$("[data-need], [data-demo], [data-docs]").forEach((el) => {
    const ok = (!el.dataset.need || can(el.dataset.need)) && (el.dataset.demo === undefined || AUTH.demo)
      && (el.dataset.docs === undefined || AUTH.docs);
    el.hidden = !ok;
  });
}
