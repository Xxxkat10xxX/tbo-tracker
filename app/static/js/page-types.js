/* tbo-tracker · page-types.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ Типы бортов и узлов
async function pageTypes() {
  META = null;
  const m = await meta();
  view().innerHTML = `<div class="crumbs"><a href="#/components">Узлы и склад</a> / Свои типы</div><div class="page-head"><div><h1>Типы бортов и узлов</h1><div class="sub">Справочники: добавьте свои типы узлов (GNSS, АКБ, моторы, подвесы…) и посадочные места для них в типах БПЛА.
    Затем — «Узлы и склад» → «+ Добавить узел» и регламенты в разделе «Регламенты».</div></div></div>
    <div class="card"><div class="card-head"><h2>Типы узлов</h2><button class="primary sm" id="add-ct">+ Тип узла</button></div>
      <table><thead><tr><th>Код</th><th>Название</th><th>Описание</th><th>Используется в позициях</th><th></th></tr></thead><tbody>
      ${m.component_types.map((t) => {
        const used = m.aircraft_types.map((a) => [a.code, a.slots.filter((s) => s.component_type === t.code).map((s) => s.position)])
          .filter(([, ps]) => ps.length).map(([code, ps]) => `${code}: ${ps.join(", ")}`);
        return `<tr><td class="mono">${esc(t.code)}</td><td><b>${esc(t.name)}</b></td><td class="small">${esc(t.description || "")}</td>
          <td class="small">${used.length ? esc(used.join(", ")) : '<span class="c-warn">нет позиций — узлы этого типа некуда установить</span>'}</td>
          <td class="nowrap"><button class="sm" data-ct-edit="${t.id}">Изменить</button> <button class="sm danger" data-ct-del="${t.id}">✕</button></td></tr>`;
      }).join("")}</tbody></table></div>
    <div class="card"><div class="card-head"><h2>Типы БПЛА и посадочные места</h2><button class="primary sm" id="add-at">+ Тип БПЛА</button></div>
      ${m.aircraft_types.map((a) => `<div class="card flat"><div class="card-head"><h3 class="m-0">${esc(a.name)} <span class="mono muted small">${esc(a.code)}</span></h3>
        <div class="actions"><button class="sm" data-at-edit="${a.id}">Изменить позиции</button><button class="sm danger" data-at-del="${a.id}">✕</button></div></div>
        <div class="chips">${a.slots.map((s) => `<span class="badge ${s.required === false ? "" : "blue"}">${esc(s.position)} · ${esc(s.label)}${s.required === false ? " · необязат." : ""}</span>`).join("")}</div></div>`).join("")}</div>`;
  const ctForm = (t = {}) => `<div class="row2"><div class="field"><label>Код (латиница, без пробелов)</label><input name="code" value="${esc(t.code || "")}" ${t.id ? "disabled" : ""} placeholder="gnss"></div>
    <div class="field"><label>Название</label><input name="name" value="${esc(t.name || "")}" placeholder="GNSS-приёмник"></div></div>
    <div class="field"><label>Описание</label><input name="description" value="${esc(t.description || "")}"></div>
    <div class="field"><label>Шаблон характеристик паспорта (по одной на строку)</label><textarea name="spec_fields" rows="4" placeholder="Ёмкость&#10;Напряжение&#10;Масса">${esc((t.spec_fields || []).join("\n"))}</textarea>
      <div class="hint">Эти поля подставляются в паспорт при постановке узла этого типа на учёт.</div></div>`;
  const openCt = (t) => modal(t ? "Изменение типа узла" : "Новый тип узла", ctForm(t || {}), {
    onOk: async (root) => {
      const body = { code: t ? t.code : val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")),
        spec_fields: (val(root, "spec_fields") || "").split("\n").map((x) => x.trim()).filter(Boolean) };
      const r = await api(t ? "PUT" : "POST", t ? `/api/component-types/${t.id}` : "/api/component-types", body);
      toast(r.message); pageTypes();
    },
  });
  const slotRow = (s = {}) => `<tr><td><input name="position" value="${esc(s.position || "")}" placeholder="GNSS"></td><td><input name="label" value="${esc(s.label || "")}" placeholder="GNSS-приёмник"></td>
    <td><select name="ctype">${m.component_types.map((c) => `<option value="${esc(c.code)}" ${c.code === s.component_type ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></td>
    <td class="ta-c"><input type="checkbox" name="required" ${s.required === false ? "" : "checked"} class="w-auto"></td>
    <td><button class="sm danger" data-rm>✕</button></td></tr>`;
  const openAt = (a) => {
    const bg = modal(a ? `Тип БПЛА: ${a.name}` : "Новый тип БПЛА", `
      <div class="row3"><div class="field"><label>Код</label><input name="code" value="${esc(a ? a.code : "")}" ${a ? "disabled" : ""} placeholder="TBT-H6"></div>
      <div class="field"><label>Название</label><input name="name" value="${esc(a ? a.name : "")}" placeholder="TBT-H6 (гексакоптер)"></div>
      <div class="field"><label>Категория</label><select name="category">${["multirotor", "vtol", "fixed-wing", "helicopter", "ground"].map((c) => `<option ${a && a.category === c ? "selected" : ""}>${c}</option>`).join("")}</select></div></div>
      <div class="field"><label>Производитель</label><input name="manufacturer" value="${esc(a ? a.manufacturer || "" : "")}"></div>
      <h3>Посадочные места</h3><table><thead><tr><th>Позиция</th><th>Название</th><th>Тип узла</th><th title="Без узла в обязательной позиции борт не допускается к полётам">Обязат.</th><th></th></tr></thead><tbody id="slots">${(a ? a.slots : [{}]).map(slotRow).join("")}</tbody></table>
      <button class="sm mt-8" id="add-slot">+ Позиция</button>
      <div class="hint">Нельзя удалить позицию или сменить её тип, пока в ней на каком-либо борту стоит узел.</div>`, {
      wide: true,
      onOk: async (root) => {
        const slots = $$("#slots tr", root).map((tr) => ({ position: $('[name="position"]', tr).value.trim(), label: $('[name="label"]', tr).value.trim(), component_type: $('[name="ctype"]', tr).value, required: $('[name="required"]', tr).checked }))
          .filter((s) => s.position);
        const body = { code: a ? a.code : val(root, "code"), name: val(root, "name"), category: val(root, "category"), manufacturer: orNull(val(root, "manufacturer")), slots };
        const r = await api(a ? "PUT" : "POST", a ? `/api/aircraft-types/${a.id}` : "/api/aircraft-types", body);
        toast(r.message); pageTypes();
      },
    });
    const bindRm = () => $$("[data-rm]", bg).forEach((b) => { b.onclick = () => b.closest("tr").remove(); });
    $("#add-slot", bg).onclick = () => { $("#slots", bg).insertAdjacentHTML("beforeend", slotRow()); bindRm(); };
    bindRm();
  };
  $("#add-ct").onclick = () => openCt(null);
  $("#add-at").onclick = () => openAt(null);
  $$("[data-ct-edit]").forEach((b) => b.addEventListener("click", () => openCt(m.component_types.find((t) => t.id === +b.dataset.ctEdit))));
  $$("[data-at-edit]").forEach((b) => b.addEventListener("click", () => openAt(m.aircraft_types.find((t) => t.id === +b.dataset.atEdit))));
  const del = (url) => modal("Удаление", "<p>Удалить тип? Это возможно, только если он нигде не используется.</p>", {
    okText: "Удалить", onOk: async () => { const r = await api("DELETE", url); toast(r.message); pageTypes(); },
  });
  $$("[data-ct-del]").forEach((b) => b.addEventListener("click", () => del(`/api/component-types/${b.dataset.ctDel}`)));
  $$("[data-at-del]").forEach((b) => b.addEventListener("click", () => del(`/api/aircraft-types/${b.dataset.atDel}`)));
}
