/* tbo-tracker · ui-passport.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ фото бортов
const NO_PHOTO = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500"><rect width="800" height="500" fill="#eef1f6"/><g fill="none" stroke="#b9c2d0" stroke-width="10" stroke-linecap="round"><circle cx="300" cy="200" r="52"/><circle cx="500" cy="200" r="52"/><circle cx="300" cy="320" r="52"/><circle cx="500" cy="320" r="52"/><path d="M336 236 L464 284 M464 236 L336 284"/></g><rect x="370" y="230" width="60" height="60" rx="10" fill="#b9c2d0"/></svg>');
function droneImg(a, mode) {
  const src = a.image || NO_PHOTO;
  return `<img class="drone-${mode}" src="${esc(src)}" alt="${esc(a.name)}" loading="lazy">`;
}
// a — борт, которому выбираем фото; без борта окно работает как менеджер пула
async function openPhotoPicker(a, onDone) {
  const pick = !!a;
  const bg = modal(pick ? `Фото борта ${a.name}` : "Пул фото бортов", '<div class="loading">Загрузка…</div>', { wide: true });
  const body = $(".modal-b", bg);
  const done = (msg) => { toast(msg); bg.remove(); AIRCRAFT_CACHE = null; onDone && onDone(); };
  const usedTxt = (u) => (u.used_by && u.used_by.length ? `на бортах: ${u.used_by.join(", ")}` : "не назначено");
  const tile = (u, own) => `<div class="ph-tile ${pick && a.image === u.url ? "sel" : ""} ${pick ? "pickable" : ""}" data-url="${esc(u.url)}">
      <img src="${esc(u.url)}" alt="" loading="lazy">
      ${own ? `<div class="ph-tools"><button class="sm" data-rename="${esc(u.url)}" title="Подпись">✎</button><button class="sm danger" data-del="${esc(u.url)}" title="Удалить из пула">✕</button></div>` : ""}
      <span class="ph-cap"><b>${esc(u.title)}</b><span>${esc(own ? usedTxt(u) : u.industry)}</span></span></div>`;
  async function render() {
    const lib = await GET("/api/drone-images");
    body.innerHTML = `
      <div class="ph-head"><h3>Мой пул фото <span class="muted">(${lib.uploaded.length})</span></h3>
        <label class="btn primary sm m-0 c-white">＋ Добавить фото в пул<input type="file" id="ph-add" accept="image/jpeg,image/png,image/webp" multiple hidden></label></div>
      ${lib.uploaded.length ? `<div class="ph-grid">${lib.uploaded.map((u) => tile(u, true)).join("")}</div>`
        : `<div class="ph-empty">Загрузите свои фото бортов — до 20 файлов за раз (JPEG, PNG, WebP до 8 МБ). Они появятся здесь, и их можно будет назначать любому борту.</div>`}
      <h3 class="mt-18">Набор иллюстраций по отраслям</h3>
      <div class="ph-grid">${lib.presets.map((u) => tile(u, false)).join("")}</div>
      ${pick ? `<div class="ph-actions"><span class="hint">${pick ? "Нажмите на фото, чтобы назначить его борту. Лучше всего — горизонтальный кадр 16:10." : ""}</span>
        ${a.image ? '<button class="sm danger ml-auto" id="ph-none">Убрать фото у борта</button>' : ""}</div>` : ""}`;
    if (pick) $$(".ph-tile", body).forEach((el) => el.addEventListener("click", async (e) => {
      if (e.target.closest(".ph-tools")) return;
      try { const r = await api("PUT", `/api/aircraft/${a.id}/image`, { image: el.dataset.url }); done(r.message); } catch (err) { toast(err.message, true); }
    }));
    const none = $("#ph-none", body);
    if (none) none.onclick = async () => { try { const r = await api("PUT", `/api/aircraft/${a.id}/image`, { image: null }); done(r.message); } catch (e) { toast(e.message, true); } };
    $("#ph-add", body).onchange = async (e) => {
      const files = [...e.target.files]; if (!files.length) return;
      const fd = new FormData(); files.forEach((f) => fd.append("files", f));
      try { const r = await api("POST", "/api/drone-images", fd, true); toast(r.message, r.errors.length > 0); render(); } catch (err) { toast(err.message, true); }
    };
    $$("[data-del]", body).forEach((b) => b.addEventListener("click", async () => {
      try { const r = await api("DELETE", `/api/drone-images?url=${encodeURIComponent(b.dataset.del)}`); toast(r.message); render(); } catch (err) { toast(err.message, true); }
    }));
    $$("[data-rename]", body).forEach((b) => b.addEventListener("click", () => {
      const cap = b.closest(".ph-tile").querySelector(".ph-cap b");
      const m = modal("Подпись фото", `<div class="field"><label>Подпись</label><input name="title" value="${esc(cap.textContent)}" maxlength="80"></div>`, {
        onOk: async (root) => { const r = await api("PATCH", "/api/drone-images", { url: b.dataset.rename, title: val(root, "title") }); toast(r.message); render(); },
      });
      $('[name="title"]', m).select();
    }));
  }
  render();
}

// ------------------------------------------------------------------ электронный паспорт
const fmtUid = (u) => u ? `${u.slice(0, 8)}-${u.slice(8, 12)}-${u.slice(12, 16)}-${u.slice(16, 20)}-${u.slice(20, 32)}`.toUpperCase() : "—";
function residualText(r, short) {
  if (!r) return short ? "—" : "назначенный ресурс не задан";
  const parts = [r.hours != null && hrs(Math.max(0, r.hours)), r.cycles != null && `${num(Math.max(0, r.cycles), 0)} цикл.`, r.days != null && `${num(Math.max(0, r.days), 0)} дн.`].filter(Boolean);
  return parts.join(" / ") || "—";
}
function residualBar(r) {
  if (!r || r.fraction == null) return "";
  const pct = Math.round(r.fraction * 100), cls = r.fraction <= 0 ? "bad" : r.fraction <= 0.1 ? "warn" : "";
  return `<div class="bar mt-4" title="${pct}% ресурса осталось"><span class="${cls}" data-w="${pct}"></span></div>`;
}
function specRows(specs) {
  return `<table class="spec-edit"><tbody>${(specs.length ? specs : [{ name: "", value: "" }]).map((s) => `<tr>
    <td><input name="spec-name" value="${esc(s.name)}" placeholder="Характеристика"></td><td><input name="spec-value" value="${esc(s.value)}" placeholder="Значение"></td>
    <td class="w-40"><button class="sm danger" data-spec-rm>✕</button></td></tr>`).join("")}</tbody></table>
    <button class="sm mt-6" data-spec-add>+ Характеристика</button>`;
}
function bindSpecs(root) {
  const bind = () => $$("[data-spec-rm]", root).forEach((b) => { b.onclick = () => b.closest("tr").remove(); });
  $("[data-spec-add]", root).onclick = () => {
    $(".spec-edit tbody", root).insertAdjacentHTML("beforeend", `<tr><td><input name="spec-name" placeholder="Характеристика"></td><td><input name="spec-value" placeholder="Значение"></td><td><button class="sm danger" data-spec-rm>✕</button></td></tr>`);
    bind();
  };
  bind();
}
const readSpecs = (root) => $$(".spec-edit tbody tr", root).map((tr) => ({ name: $('[name="spec-name"]', tr).value.trim(), value: $('[name="spec-value"]', tr).value.trim() })).filter((s) => s.name);
function passportHTML(c) {
  const lifeRules = c.due.filter((i) => i.rule.kind === "life").map((i) => `${esc(i.rule.name)}: ${i.metrics.map((m) => `${num(m.limit, 0)} ${m.unit}`).join(" / ")}`);
  return `<div class="card passport"><div class="card-head"><h2>Электронный паспорт</h2><button class="sm" id="edit-pass">✎ Изменить паспорт</button></div>
    <div class="pass-grid">
      <dl class="pass-ids">
        <dt>Уникальный идентификатор</dt><dd class="mono">${fmtUid(c.uid)}</dd>
        <dt>Серийный номер</dt><dd><b>${esc(c.serial)}</b></dd>
        <dt>Тип / модель</dt><dd>${esc(c.type.name)} · ${esc(c.model || "—")}</dd>
        <dt>Децимальный номер</dt><dd>${esc(c.part_number || "—")}</dd>
        <dt>Производитель</dt><dd>${esc(c.manufacturer || "—")}</dd>
        <dt>Дата изготовления</dt><dd>${fmtD(c.manufactured_at)}</dd>
        <dt>Ввод в эксплуатацию</dt><dd>${fmtD(c.in_service_at)}</dd>
        <dt>Версия ПО</dt><dd>${esc(c.firmware || "—")}</dd>
      </dl>
      <div><h3>Основные технические характеристики</h3>
        ${c.specs.length ? `<table class="spec"><tbody>${c.specs.map((s) => `<tr><td class="muted">${esc(s.name)}</td><td>${esc(s.value)}</td></tr>`).join("")}</tbody></table>` : '<div class="muted small">Не заполнены — «✎ Изменить паспорт»</div>'}</div>
      <div><h3>Ресурс</h3>
        <div class="small muted">Установленный эксплуатационный ресурс</div>
        <div class="small">${lifeRules.length ? lifeRules.join("<br>") : "не задан"}</div>
        <div class="small muted mt-8">Наработка с начала эксплуатации</div><div><b>${hrs(c.hours)}</b> · ${num(c.cycles, 0)} цикл.</div>
        <div class="small muted mt-8">Остаточный ресурс${c.residual && c.residual.limit_by ? ` (по «${esc(c.residual.limit_by)}»)` : ""}</div>
        <div class="big-res">${residualText(c.residual)}</div>${residualBar(c.residual)}
      </div></div></div>`;
}
function openPassportModal(c, onDone) {
  const bg = modal(`Электронный паспорт ${c.serial}`, `
    <div class="row3"><div class="field"><label>Модель</label><input name="model" value="${esc(c.model || "")}"></div>
    <div class="field"><label>Децимальный номер</label><input name="part_number" value="${esc(c.part_number || "")}"></div>
    <div class="field"><label>Версия ПО</label><input name="firmware" value="${esc(c.firmware || "")}"></div></div>
    <div class="row2"><div class="field"><label>Производитель</label><input name="manufacturer" value="${esc(c.manufacturer || "")}"></div>
    <div class="field"><label>Дата изготовления</label><input type="date" name="manufactured_at" value="${c.manufactured_at ? c.manufactured_at.slice(0, 10) : ""}"></div></div>
    <h3>Основные технические характеристики</h3>${specRows(c.specs)}
    <div class="hint">Серийный номер, уникальный идентификатор и дата ввода в эксплуатацию — неизменяемые реквизиты паспорта. Ресурс — кнопка «⚙ Ресурс».</div>`, {
    wide: true, okText: "Сохранить паспорт",
    onOk: async (root) => {
      const md = val(root, "manufactured_at");
      await api("PATCH", `/api/components/${c.id}`, { model: orNull(val(root, "model")), part_number: orNull(val(root, "part_number")),
        firmware: orNull(val(root, "firmware")), manufacturer: orNull(val(root, "manufacturer")),
        manufactured_at: md ? md + "T00:00:00Z" : null, specs: readSpecs(root) });
      toast("Паспорт сохранён"); onDone && onDone();
    },
  });
  bindSpecs(bg);
}

// ------------------------------------------------------------------ ресурс и свои типы
const RES_HINT = "Максимальный износ конкретного экземпляра (по паспорту, формуляру, решению эксплуатанта). Считается от начала эксплуатации, работами не обнуляется; срабатывает первый достигнутый порог. Пустое поле — порог не задан.";
function resourceFields(r = {}) {
  return `<div class="row3"><div class="field"><label>Ресурс, ч</label><input type="number" min="0" step="0.01" name="resource_hours" value="${r.hours ?? ""}" placeholder="не задан"></div>
    <div class="field"><label>Ресурс, циклы</label><input type="number" min="0" name="resource_cycles" value="${r.cycles ?? ""}" placeholder="не задан"></div>
    <div class="field"><label>Ресурс, дни</label><input type="number" min="0" name="resource_days" value="${r.days ?? ""}" placeholder="не задан"></div></div>
    <div class="hint mtn-6">${RES_HINT}</div>`;
}
function readResource(root) {
  const n = (k, int) => { const v = val(root, k); if (v === "" || v === null) return null; const x = int ? parseInt(v, 10) : parseFloat(v); if (!(x > 0)) throw new Error("Ресурс должен быть больше нуля (или оставьте поле пустым)"); return x; };
  return { resource_hours: n("resource_hours"), resource_cycles: n("resource_cycles", 1), resource_days: n("resource_days", 1) };
}
function resourceText(r) {
  const parts = [r && r.hours && `${num(r.hours, 2)} ч`, r && r.cycles && `${num(r.cycles, 0)} цикл.`, r && r.days && `${num(r.days, 0)} дн.`].filter(Boolean);
  return parts.length ? parts.join(" / ") : null;
}
function openResourceModal({ kind, entity, onDone }) {
  const name = kind === "aircraft" ? `борта ${entity.name}` : `узла ${entity.serial}`;
  modal(`Индивидуальный ресурс ${name}`, `${resourceFields(entity.resource)}
    <div class="notice small mt-12">Ресурс появится в «ТО и уведомления» как «Индивидуальный ресурс (паспорт / формуляр)»: «Приближается» за 10 % до порога, «Требуется» — по достижении. Типовые регламенты продолжают действовать.</div>`, {
    okText: "Сохранить",
    onOk: async (root) => {
      await api("PATCH", `/api/${kind === "aircraft" ? "aircraft" : "components"}/${entity.id}`, readResource(root));
      toast("Ресурс сохранён"); refreshNavCount(); onDone && onDone();
    },
  });
}
// «+ Новый тип узла…» прямо из выпадающего списка
async function quickComponentType(select, onCreated) {
  modal("Новый тип узла", `<div class="row2"><div class="field"><label>Код (латиница, без пробелов)</label><input name="code" placeholder="gnss"></div>
    <div class="field"><label>Название</label><input name="name" placeholder="GNSS-приёмник"></div></div>
    <div class="field"><label>Описание</label><input name="description"></div>
    <div class="hint">Чтобы узлы нового типа можно было устанавливать на борт, добавьте для него позицию: «Узлы и склад» → «⊞ Свои типы узлов».</div>`, {
    okText: "Создать тип",
    onOk: async (root) => {
      const r = await POST("/api/component-types", { code: val(root, "code"), name: val(root, "name"), description: orNull(val(root, "description")) });
      toast(r.message); META = null; const m = await meta();
      onCreated(m, r.id);
    },
  });
  select.value = select.dataset.prev || select.options[0].value;
}
function typeOptions(list, cur, extra) {
  return list.map((t) => `<option value="${t.id}" ${t.id === cur ? "selected" : ""}>${esc(t.name)}</option>`).join("") + extra;
}
