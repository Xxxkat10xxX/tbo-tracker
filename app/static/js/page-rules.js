/* tbo-tracker · page-rules.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ Регламенты
async function pageRules() {
  const [rules, m] = await Promise.all([GET("/api/rules"), meta()]);
  const lim = (r) => [r.interval_hours && `${num(r.interval_hours, 2)} ч`, r.interval_cycles && `${r.interval_cycles} цикл.`, r.interval_days && `${r.interval_days} дн.`, r.interval_events && `${r.interval_events} падение/столкн.`].filter(Boolean).join(" / ");
  const warn = (r) => [r.warn_hours != null && `${num(r.warn_hours, 2)} ч`, r.warn_cycles != null && `${r.warn_cycles} ц.`, r.warn_days != null && `${r.warn_days} дн.`].filter(Boolean).join(" / ") || "10 % интервала";
  view().innerHTML = `<div class="page-head"><div><h1>Регламенты ТО</h1><div class="sub">Правила и пороги планового обслуживания для типов аппаратов и узлов. Срабатывает первый достигнутый порог.</div></div>
    <button class="primary" id="add">+ Новый регламент</button></div>
    <div class="card"><div class="table-wrap"><table><thead><tr><th>Код</th><th>Регламент</th><th>Применяется к</th><th>Вид</th><th>Интервал</th><th>Предупреждать за</th><th></th></tr></thead><tbody>
    ${rules.map((r) => `<tr class="${r.active ? "" : "muted"}"><td class="mono">${esc(r.code)}</td><td><b>${esc(r.name)}</b><div class="small muted">${esc(r.description || "")}</div></td>
      <td>${r.target === "aircraft" ? "Борт: " : "Узел: "}${esc(r.applies_to)}</td><td>${r.kind === "life" ? '<span class="badge bad">Назначенный ресурс</span>' : '<span class="badge blue">Периодическое ТО</span>'}${r.active ? "" : ' <span class="badge">неактивен</span>'}</td>
      <td class="nowrap">${lim(r)}</td><td class="nowrap small">${warn(r)}</td>
      <td class="nowrap"><button class="sm" data-edit="${r.id}">Изменить</button> <button class="sm danger" data-del="${r.id}">✕</button></td></tr>`).join("")}</tbody></table></div></div>
    <div class="card small muted"><b>Как считается.</b> Периодическое ТО: счётчики отсчитываются от даты последнего выполнения этой работы (если работа не выполнялась — от ввода объекта в эксплуатацию с учётом всей наработки). Выполнение работы обнуляет интервал только по этому регламенту — суммарная наработка изделия не меняется.
      Назначенный ресурс: считается от начала эксплуатации и не обнуляется; по достижении узел подлежит замене.</div>`;
  const form = (r = {}) => `
    <div class="row2"><div class="field"><label>Код</label><input name="code" value="${esc(r.code || "")}"></div>
    <div class="field"><label>Вид</label><select name="kind"><option value="periodic" ${r.kind !== "life" ? "selected" : ""}>Периодическое ТО (обнуляется выполнением)</option><option value="life" ${r.kind === "life" ? "selected" : ""}>Назначенный ресурс (не обнуляется)</option></select></div></div>
    <div class="field"><label>Наименование работы</label><input name="name" value="${esc(r.name || "")}"></div>
    <div class="field"><label>Описание</label><textarea name="description" rows="2">${esc(r.description || "")}</textarea></div>
    <div class="row2"><div class="field"><label>Объект</label><select name="target"><option value="component" ${r.target !== "aircraft" ? "selected" : ""}>Узел</option><option value="aircraft" ${r.target === "aircraft" ? "selected" : ""}>Борт</option></select></div>
    <div class="field"><label>Тип</label><select name="type"></select></div></div>
    <div class="row3"><div class="field"><label>Интервал, ч</label><input type="number" step="0.01" min="0" name="interval_hours" value="${r.interval_hours ?? ""}"></div>
    <div class="field"><label>Интервал, циклы</label><input type="number" min="0" name="interval_cycles" value="${r.interval_cycles ?? ""}"></div>
    <div class="field"><label>Интервал, дни</label><input type="number" min="0" name="interval_days" value="${r.interval_days ?? ""}"></div></div>
    <div class="row3"><div class="field"><label>Падений/столкновений (датчики)</label><input type="number" min="0" name="interval_events" value="${r.interval_events ?? ""}" placeholder="напр. 1"></div><div></div><div></div></div>
    <div class="row3"><div class="field"><label>Предупреждать за, ч</label><input type="number" step="0.01" min="0" name="warn_hours" value="${r.warn_hours ?? ""}"></div>
    <div class="field"><label>за, циклов</label><input type="number" min="0" name="warn_cycles" value="${r.warn_cycles ?? ""}"></div>
    <div class="field"><label>за, дней</label><input type="number" min="0" name="warn_days" value="${r.warn_days ?? ""}"></div></div>
    <div class="hint">Пустой порог не используется. Если порог предупреждения не задан — 10 % интервала.</div>
    <label class="mt-10"><input type="checkbox" name="active" ${r.active === false ? "" : "checked"} class="w-auto"> Активен</label>`;
  function open(r) {
    const bg = modal(r ? "Изменение регламента" : "Новый регламент", form(r || {}), {
      okText: "Сохранить",
      onOk: async (root) => {
        const n = (k, int) => { const v = val(root, k); return v === "" ? null : (int ? parseInt(v, 10) : parseFloat(v)); };
        const target = val(root, "target"), type = val(root, "type");
        const body = { code: val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")), target, kind: val(root, "kind"),
          aircraft_type_id: target === "aircraft" && type ? +type : null, component_type_id: target === "component" && type ? +type : null,
          interval_hours: n("interval_hours"), interval_cycles: n("interval_cycles", 1), interval_days: n("interval_days", 1), interval_events: n("interval_events", 1),
          warn_hours: n("warn_hours"), warn_cycles: n("warn_cycles", 1), warn_days: n("warn_days", 1), active: $('[name="active"]', root).checked };
        await api(r ? "PUT" : "POST", r ? `/api/rules/${r.id}` : "/api/rules", body);
        toast("Регламент сохранён");
        reload();
      },
    });
    const tSel = $('[name="target"]', bg), typeSel = $('[name="type"]', bg);
    const fill = () => {
      const isAc = tSel.value === "aircraft";
      const list = isAc ? m.aircraft_types : m.component_types;
      const cur = r ? (isAc ? r.aircraft_type_id : r.component_type_id) : null;
      typeSel.innerHTML = `<option value="">${isAc ? "Все типы БПЛА" : "Все типы узлов"}</option>` +
        typeOptions(list, cur, isAc ? '<option value="__types">+ Новый тип БПЛА… (раздел «Типы»)</option>' : '<option value="__new">+ Новый тип узла…</option>');
      typeSel.dataset.prev = typeSel.value;
    };
    typeSel.onchange = () => {
      if (typeSel.value === "__types") { $$(".modal-bg").forEach((x) => x.remove()); location.hash = "#/types"; return; }
      if (typeSel.value === "__new") { quickComponentType(typeSel, (mm, id) => { m.component_types = mm.component_types; fill(); typeSel.value = id; typeSel.dataset.prev = id; }); return; }
      typeSel.dataset.prev = typeSel.value;
    };
    tSel.onchange = fill;
    fill();
  }
  $("#add").onclick = () => open(null);
  $$("[data-edit]").forEach((b) => b.addEventListener("click", () => open(rules.find((r) => r.id === +b.dataset.edit))));
  $$("[data-del]").forEach((b) => b.addEventListener("click", () => modal("Удаление регламента", "<p>Удалить регламент? Если по нему уже выполнялись работы, он будет деактивирован, история сохранится.</p>", {
    okText: "Удалить", onOk: async () => { const r = await api("DELETE", `/api/rules/${b.dataset.del}`); toast(r.message); reload(); },
  })));
}
