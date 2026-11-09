/* tbo-tracker · page-admin.js — учётные записи и журнал аудита (только администратор). */
"use strict";

const ACTION_RU = {
  login: "Вход", login_failed: "Неудачный вход", logout: "Выход", account_locked: "Блокировка после неудачных попыток",
  setup_failed: "Неверный код настройки", user_created: "Создан пользователь", user_updated: "Изменён пользователь",
  user_deleted: "Удалён пользователь", user_unlocked: "Разблокирован", password_changed: "Смена пароля",
  password_change_failed: "Смена пароля отклонена", password_reset: "Пароль задан администратором", api_write: "Изменение данных",
  cli_passwd: "Пароль (командная строка)", cli_unlock: "Разблокировка (командная строка)",
  cli_disable: "Отключение (командная строка)", cli_enable: "Включение (командная строка)",
};

function randomPassword() {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const buf = new Uint32Array(16);
  crypto.getRandomValues(buf);
  return Array.from(buf, (x) => abc[x % abc.length]).join("").replace(/(.{4})(?!$)/g, "$1-");
}

function passwordFields(prefix) {
  return `<div class="row2"><div class="field"><label>Пароль</label><input type="text" name="${prefix}pw" autocomplete="new-password" maxlength="128"></div>
    <div class="field"><label>&nbsp;</label><button type="button" class="sm" data-gen="${prefix}pw">Сгенерировать</button></div></div>
    <label class="chk"><input type="checkbox" name="${prefix}tmp" checked> Временный — сменить при первом входе</label>
    <div class="hint">${PASSWORD_HINT} Передайте пароль сотруднику лично, не по почте и не в чате.</div>`;
}
function bindGen(root) {
  $$("[data-gen]", root).forEach((b) => { b.onclick = () => { $(`[name="${b.dataset.gen}"]`, root).value = randomPassword(); }; });
}

async function pageUsers() {
  const list = await GET("/api/auth/users");
  const roleOpts = (cur) => Object.entries(AUTH.roles).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
  view().innerHTML = `<div class="page-head"><div><h1>Пользователи</h1><div class="sub">Роли: администратор — всё, включая пользователей, сервер и копии; инженер — работа с данными;
    наблюдатель — только просмотр. Пароли хранятся только в виде хеша Argon2id.</div></div><button class="primary" id="u-add">+ Пользователь</button></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Роль</th><th>Состояние</th><th>Последний вход</th><th>Пароль сменён</th><th></th></tr></thead><tbody>
    ${list.map((u) => `<tr><td><b>${esc(u.display_name)}</b><div class="small muted mono">${esc(u.username)}</div></td>
      <td><select data-role="${u.id}" ${u.id === AUTH.user.id ? "disabled" : ""}>${roleOpts(u.role)}</select></td>
      <td>${u.is_active ? '<span class="badge ok">активен</span>' : '<span class="badge">отключён</span>'}
        ${u.locked ? `<span class="badge bad">заблокирован до ${fmtDT(u.locked_until)}</span>` : ""}
        ${u.must_change_password ? '<span class="badge warn">временный пароль</span>' : ""}</td>
      <td class="small nowrap">${fmtDT(u.last_login_at)}</td><td class="small nowrap">${fmtDT(u.password_changed_at)}</td>
      <td class="nowrap"><button class="sm" data-pw="${u.id}">Пароль</button>
        ${u.locked ? `<button class="sm" data-unlock="${u.id}">Разблокировать</button>` : ""}
        ${u.id === AUTH.user.id ? "" : `<button class="sm" data-active="${u.id}">${u.is_active ? "Отключить" : "Включить"}</button>
        <button class="sm danger" data-del-user="${u.id}">✕</button>`}</td></tr>`).join("")}
    </tbody></table></div></div>`;
  const byId = (id) => list.find((u) => u.id === +id);
  $("#u-add").onclick = () => {
    const bg = modal("Новый пользователь", `<div class="row2"><div class="field"><label>Имя пользователя (вход)</label><input name="username" maxlength="64" placeholder="напр. petrov"></div>
      <div class="field"><label>ФИО</label><input name="display_name" maxlength="200"></div></div>
      <div class="field"><label>Роль</label><select name="role">${roleOpts("engineer")}</select></div>${passwordFields("n")}`, {
      okText: "Создать", onOk: async (root) => {
        const r = await POST("/api/auth/users", { username: val(root, "username"), display_name: val(root, "display_name"), role: val(root, "role"),
          password: $('[name="npw"]', root).value, must_change_password: $('[name="ntmp"]', root).checked });
        toast(r.message); reload();
      },
    });
    bindGen(bg);
  };
  $$("[data-role]").forEach((s) => s.addEventListener("change", async () => {
    try { const r = await api("PATCH", `/api/auth/users/${s.dataset.role}`, { role: s.value }); toast(`${r.message}: ${r.username} — ${r.role_label}`); reload(); }
    catch (e) { toast(e.message, true); reload(); }
  }));
  $$("[data-pw]").forEach((b) => b.addEventListener("click", () => {
    const u = byId(b.dataset.pw);
    const bg = modal(`Пароль: ${u.username}`, `${passwordFields("r")}<p class="small muted">Все сессии пользователя будут завершены.</p>`, {
      okText: "Задать пароль", onOk: async (root) => {
        const r = await POST(`/api/auth/users/${u.id}/password`, { password: $('[name="rpw"]', root).value, must_change_password: $('[name="rtmp"]', root).checked });
        toast(r.message); reload();
      },
    });
    bindGen(bg);
  }));
  $$("[data-unlock]").forEach((b) => b.addEventListener("click", async () => {
    try { toast((await POST(`/api/auth/users/${b.dataset.unlock}/unlock`)).message); reload(); } catch (e) { toast(e.message, true); }
  }));
  $$("[data-active]").forEach((b) => b.addEventListener("click", async () => {
    const u = byId(b.dataset.active);
    try { toast((await api("PATCH", `/api/auth/users/${u.id}`, { is_active: !u.is_active })).message); reload(); } catch (e) { toast(e.message, true); }
  }));
  $$("[data-del-user]").forEach((b) => b.addEventListener("click", () => {
    const u = byId(b.dataset.delUser);
    modal("Удаление пользователя", `<p>Удалить учётную запись <b>${esc(u.username)}</b>? Записи журнала аудита сохранятся.</p>`, {
      okText: "Удалить", onOk: async () => { toast((await api("DELETE", `/api/auth/users/${u.id}`)).message); reload(); },
    });
  }));
}

async function pageAudit() {
  let rows = [], filter = { username: "", action: "" };
  view().innerHTML = `<div class="page-head"><div><h1>Журнал аудита</h1><div class="sub">Входы и неудачные попытки, учётные записи, все изменения данных через API —
    кто, когда, с какого адреса и с каким результатом. Срок хранения — TBO_AUDIT_RETENTION_DAYS (по умолчанию 365 дней).</div></div></div>
    <div class="card"><div class="toolbar"><input name="a-user" placeholder="пользователь" maxlength="64" class="maxw-220">
      <select name="a-action"><option value="">все события</option>${Object.entries(ACTION_RU).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
      <button id="a-go">Показать</button></div><div id="a-list"><div class="loading">Загрузка…</div></div>
      <button id="a-more" hidden>Показать ещё</button></div>`;
  async function load(more) {
    const q = new URLSearchParams({ limit: "200" });
    if (filter.username) q.set("username", filter.username);
    if (filter.action) q.set("action", filter.action);
    if (more && rows.length) q.set("before_id", rows[rows.length - 1].id);
    const page = await GET(`/api/audit?${q}`);
    rows = more ? rows.concat(page) : page;
    $("#a-list").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Когда</th><th>Пользователь</th><th>Адрес</th><th>Событие</th><th>Объект</th><th>Итог</th></tr></thead><tbody>
      ${rows.map((a) => `<tr><td class="nowrap small">${fmtDT(a.at)}</td><td class="mono small">${esc(a.username || "—")}</td><td class="mono small">${esc(a.ip || "—")}</td>
        <td>${esc(ACTION_RU[a.action] || a.action)}${a.details ? `<div class="small muted">${esc(a.details)}</div>` : ""}</td>
        <td class="small mono">${esc(a.target || "")}</td>
        <td>${a.status ? `<span class="badge ${a.status < 300 ? "ok" : a.status < 500 ? "warn" : "bad"}">${a.status}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="empty-state">Записей нет</div>';
    $("#a-more").hidden = page.length < 200;
  }
  $("#a-go").onclick = () => { filter = { username: val(view(), "a-user"), action: val(view(), "a-action") }; load(false); };
  $("#a-more").onclick = () => load(true);
  await load(false);
}
