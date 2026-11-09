/* tbo-tracker · page-board.js — общая область видимости классических скриптов (см. index.html). */
"use strict";

// ------------------------------------------------------------------ Перестановка узлов: drag & drop
function moveResultHTML(r, closable = true) {
  const loc = (c) => (c.location ? `${esc(c.location.aircraft_name)} · ${esc(c.location.position)}` : esc(c.status_label));
  const delta = (a, b, d) => (Math.abs(b - a) > 0.005 ? ` <span class="${b < a ? "neg" : "pos"}">(${b > a ? "+" : ""}${num(b - a, d)})</span>` : "");
  return `<div class="card move-res"><div class="card-head"><h2>✓ ${esc(r.title)}</h2>${closable ? '<button class="sm" id="mr-close">Скрыть</button>' : ""}</div>
    <div class="small muted mb-8">${r.messages.map(esc).join(" · ")}</div>
    <div class="table-wrap"><table><thead><tr><th>Узел (паспорт)</th><th>Местоположение: было → стало</th><th class="num">Наработка, ч</th><th class="num">Циклы</th><th>Остаточный ресурс</th><th>Регламент</th></tr></thead><tbody>
    ${r.components.map(({ before: b, after: a }) => `<tr><td><a href="#/components/${a.id}"><b>${esc(a.serial)}</b></a><div class="small muted mono">${fmtUid(a.uid)}</div></td>
      <td>${loc(b)} → <b>${loc(a)}</b></td><td class="num">${num(a.hours, 2)}${delta(b.hours, a.hours, 2)}</td><td class="num">${num(a.cycles, 0)}${delta(b.cycles, a.cycles, 0)}</td>
      <td>${residualText(a.residual, true)}${residualBar(a.residual)}</td><td>${dueBadge(a.maintenance ? a.maintenance.status : "ok")}</td></tr>`).join("")}</tbody></table></div>
    <div class="hint">История установок в паспорте дополнена; наработка = исходная + налёт бортов за периоды установки + внебортовая. Если дата операции в прошлом, полёты после неё переотнесены.</div></div>`;
}
const nodesSwitch = (active) => `<div class="seg-switch" role="tablist">
  <a href="#/board" class="${active === "board" ? "active" : ""}" role="tab">⇄ Перестановка</a>
  <a href="#/components" class="${active === "components" ? "active" : ""}" role="tab">▣ Узлы и склад</a></div>`;
async function pageBoard() {
  let data = await GET("/api/board");
  let picked = null;           // выбор щелчком (для тачскринов и без мыши)
  let lastMove = null;
  const chip = (c, from) => {
    const st = c.maintenance && c.maintenance.status !== "ok" ? c.maintenance.status : "";
    const movable = c.status === "installed" || c.status === "stock";
    return `<div class="chip-c ${st} ${movable ? "" : "locked"} ${lastMove && lastMove.id === c.id ? "flash" : ""}" ${movable ? 'draggable="true"' : ""}
      data-cid="${c.id}" data-type="${esc(c.type.code)}" data-from="${esc(from)}" title="${movable ? "Перетащите на позицию другого борта, в резерв или в ремонт" : "Узел в ремонте — установка недоступна"}">
      <div class="cc-top"><b>${esc(c.serial)}</b>${st ? `<span class="cc-st ${st}">${st === "overdue" ? "ТО просрочено" : "скоро ТО"}</span>` : ""}</div>
      <div class="cc-sub">${hrs(c.hours)} · ${num(c.cycles, 0)} цикл.</div>
      ${c.residual ? `<div class="cc-res">ост. ${residualText(c.residual, true)}</div>${residualBar(c.residual)}` : ""}</div>`;
  };
  function render() {
    const spareStock = data.spare.filter((c) => c.status === "stock"), spareRep = data.spare.filter((c) => c.status === "maintenance");
    view().innerHTML = `${nodesSwitch("board")}<div class="page-head"><div><h1>Перестановка узлов</h1>
        <div class="sub">Перетащите узел на позицию соседнего борта, в резерв или в ремонт. Совместимые позиции подсвечиваются; занятая позиция — обмен узлами.
        После операции наработка и паспорт узла пересчитываются по истории установок (налёт до даты операции остаётся за прежним бортом).</div></div></div>
      <div id="move-result"></div>
      <div class="board">
        <div class="board-side">
          <div class="card bcol dz" data-drop="stock"><h3>Резерв (склад) <span class="muted">${spareStock.length}</span></h3>
            <div class="chips-col">${spareStock.map((c) => `<div class="small muted">${esc(c.type.name)}</div>${chip(c, "stock")}`).join("") || '<div class="empty-state small">пусто</div>'}</div></div>
          <div class="card bcol dz" data-drop="maintenance"><h3>Ремонт / обслуживание <span class="muted">${spareRep.length}</span></h3>
            <div class="chips-col">${spareRep.map((c) => chip(c, "maintenance")).join("") || '<div class="empty-state small">пусто</div>'}</div></div>
        </div>
        <div class="board-fleet">${data.aircraft.map((a) => `<div class="card bac">
          <div class="bac-head">${droneImg(a, "thumb")}<div><a href="#/aircraft/${a.id}"><b>${esc(a.name)}</b></a> <span class="small muted">${esc(a.serial)}</span>
            <div class="small">${readiness(a)} · ${hrs(a.hours)} · ${a.slots_filled}/${a.slots_total}</div></div></div>
          ${a.slots.map((sl) => `<div class="bslot dz" data-drop="slot" data-aid="${a.id}" data-pos="${esc(sl.position)}" data-type="${esc(sl.component_type)}">
            <div class="bslot-l"><b>${esc(sl.position)}</b><span>${esc(sl.label)}</span></div>
            ${sl.component ? chip(sl.component, `${a.id}|${sl.position}`) : `<div class="bslot-empty">${sl.required ? "не укомплектовано" : "пусто"}</div>`}</div>`).join("")}
        </div>`).join("")}</div>
      </div>`;
    if (lastMove) {
      $("#move-result").innerHTML = lastMove.html;
      $("#mr-close").onclick = () => { lastMove = null; $("#move-result").innerHTML = ""; };
    }
    bind();
  }
  const clearHi = () => $$(".dz").forEach((d) => d.classList.remove("ok-target", "no-target", "over"));
  const highlight = (type, from) => $$(".dz").forEach((d) => {
    const ok = d.dataset.drop === "slot" ? d.dataset.type === type && `${d.dataset.aid}|${d.dataset.pos}` !== from : d.dataset.drop !== from;
    d.classList.add(ok ? "ok-target" : "no-target");
  });
  function bind() {
    $$(".chip-c[draggable]").forEach((el) => {
      el.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", el.dataset.cid); e.dataTransfer.effectAllowed = "move";
        picked = null; el.classList.add("dragging"); highlight(el.dataset.type, el.dataset.from);
      });
      el.addEventListener("dragend", () => { el.classList.remove("dragging"); clearHi(); });
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const target = el.closest(".dz.ok-target");
        if (picked && picked !== +el.dataset.cid && target) { const cid = picked; picked = null; clearHi(); askMove(cid, target); return; }
        clearHi(); $$(".chip-c.picked").forEach((x) => x.classList.remove("picked"));
        if (picked === +el.dataset.cid) { picked = null; return; }
        picked = +el.dataset.cid; el.classList.add("picked"); highlight(el.dataset.type, el.dataset.from);
      });
    });
    $$(".dz").forEach((d) => {
      d.addEventListener("dragover", (e) => { if (d.classList.contains("ok-target")) { e.preventDefault(); d.classList.add("over"); } });
      d.addEventListener("dragleave", () => d.classList.remove("over"));
      d.addEventListener("drop", (e) => { e.preventDefault(); const cid = +e.dataTransfer.getData("text/plain"); clearHi(); askMove(cid, d); });
      d.addEventListener("click", () => { if (picked && d.classList.contains("ok-target")) { const cid = picked; picked = null; clearHi(); askMove(cid, d); } });
    });
  }
  // Операцию определяет и выполняет сервер: клиент сообщает только «какой узел куда»
  async function askMove(cid, d) {
    const target = d.dataset.drop === "slot" ? { kind: "slot", aircraft_id: +d.dataset.aid, position: d.dataset.pos } : { kind: d.dataset.drop };
    let pl;
    try { pl = await POST("/api/board/plan", { component_id: cid, target }); } catch (e) { toast(e.message, true); return; }
    const defLocal = toLocalInput(new Date(pl.default_at));
    modal("Подтверждение операции с узлом", `<p><b>${esc(pl.title)}</b></p>
      <ol class="small">${pl.steps.map((s) => `<li>${esc(s.text)}</li>`).join("")}</ol>
      <div class="row2"><div class="field"><label>Дата и время операции</label><input type="datetime-local" name="at" value="${defLocal}"></div>
      <div class="field"><label>Исполнитель</label><input name="by" placeholder="ФИО"></div></div>
      <div class="field"><label>Основание</label><input name="reason" placeholder="напр. замена по результатам диагностики"></div>
      <div class="hint">Все шаги выполняются на сервере одной операцией: либо применяются все, либо ни один. Если указать прошедшую дату,
        полёты после неё будут отнесены к новой установке, и наработка узлов пересчитается. Не раньше: ${fmtDT(pl.min_at)}.</div>`, {
      okText: "Выполнить", onOk: async (root) => {
        const raw = val(root, "at");
        if (!raw) throw new Error("Укажите дату и время операции");
        const r = await POST("/api/board/move", { component_id: cid, target, at: raw === defLocal ? null : fromLocalInput(raw),
          reason: val(root, "reason") || null, performed_by: val(root, "by") || null });
        data = await GET("/api/board");
        lastMove = { id: r.moved_id, html: moveResultHTML(r) };
        AIRCRAFT_CACHE = null; refreshNavCount(); render(); toast("Операция выполнена, данные пересчитаны");
      },
    });
  }
  render();
}
