"""Перестановка узлов с доски: план операции и его атомарное выполнение на сервере.

Клиент только сообщает «какой узел куда перетащили». Сервер сам решает, что это за операция
(установка, перестановка, замена, обмен, снятие), подбирает время шагов и выполняет все шаги
в одной транзакции: либо применяются все, либо ни один.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import DomainError, NotFound
from ..models import COMPONENT_STATUSES, Aircraft, Component, Installation
from ..timeutil import iso, parse_dt, utcnow
from . import inventory
from .usage import current_installation

OP_LABELS = {"install": "Установка", "remove": "Снятие", "transfer": "Перестановка"}
TARGET_LABELS = {"stock": "резерв", "maintenance": "ремонт"}


def _component(s: Session, cid: int) -> Component:
    c = s.get(Component, cid)
    if c is None:
        raise NotFound("Узел не найден")
    return c


def plan(s: Session, component_id: int, target: dict) -> dict:
    """Определяет операцию по перетаскиванию. target: {kind: slot|stock|maintenance, aircraft_id, position}."""
    c = _component(s, component_id)
    src = current_installation(s, c.id)
    kind = target.get("kind")
    steps: list[dict] = []
    if kind == "slot":
        ta = s.get(Aircraft, target["aircraft_id"]) if target.get("aircraft_id") else None
        if ta is None:
            raise NotFound("Борт не найден")
        pos = str(target.get("position") or "")
        slot = inventory.slot_for(ta, pos)
        if slot is None:
            raise DomainError(f"У борта {ta.name} нет позиции «{pos}»")
        if slot["component_type"] != c.type.code:
            raise DomainError(f"Позиция {pos} борта {ta.name} предназначена для другого типа узлов — "
                              f"узел {c.serial} ({c.type.name}) туда не устанавливается")
        if src and src.aircraft_id == ta.id and src.position == pos:
            raise DomainError("Узел уже стоит в этой позиции")
        if not src and c.status != "stock":
            raise DomainError(f"Узел {c.serial} в статусе «{COMPONENT_STATUSES[c.status]}» — установка невозможна")
        occ = s.scalar(select(Installation).where(Installation.aircraft_id == ta.id, Installation.position == pos,
                                                  Installation.removed_at.is_(None)))
        if occ and not src:
            steps = [_step("remove", occ.component, to="stock"), _step("install", c, ta, pos)]
            title = f"Замена: {occ.component.serial} → резерв, {c.serial} → {ta.name} · {pos}"
        elif occ:
            if inventory.slot_for(src.aircraft, src.position)["component_type"] != occ.component.type.code:
                raise DomainError("Обмен невозможен: узлы разных типов")
            steps = [_step("remove", occ.component, to="stock"), _step("transfer", c, ta, pos),
                     _step("install", occ.component, src.aircraft, src.position)]
            title = f"Обмен: {c.serial} ⇄ {occ.component.serial}"
        elif src:
            steps = [_step("transfer", c, ta, pos)]
            title = f"Перестановка {c.serial}: {src.aircraft.name} · {src.position} → {ta.name} · {pos}"
        else:
            steps = [_step("install", c, ta, pos)]
            title = f"Установка {c.serial} из резерва → {ta.name} · {pos}"
    elif kind in ("stock", "maintenance"):
        if not src:
            if c.status == kind:
                raise DomainError(f"Узел {c.serial} уже в разделе «{TARGET_LABELS[kind]}»")
            raise DomainError("Перевод между резервом и ремонтом выполняется в карточке узла (регистрация работы)")
        steps = [_step("remove", c, to=kind)]
        title = f"Снятие {c.serial} с {src.aircraft.name} · {src.position} → {TARGET_LABELS[kind]}"
    else:
        raise DomainError("Неизвестное место назначения")
    ids = list(dict.fromkeys([c.id] + [st["component_id"] for st in steps]))   # перетаскиваемый узел — первым
    min_at = _min_time(s, ids)
    now = utcnow().replace(microsecond=0)
    return {"title": title, "steps": steps, "component_ids": ids,
            "min_at": iso(min_at), "default_at": iso(max(now, min_at))}


def _step(op: str, c: Component, a: Aircraft | None = None, pos: str | None = None, to: str | None = None) -> dict:
    text = f"{OP_LABELS[op]} {c.serial} → " + (f"{TARGET_LABELS[to]}" if op == "remove" else f"{a.name} · {pos}")
    return {"op": op, "component_id": c.id, "serial": c.serial, "aircraft_id": a.id if a else None,
            "aircraft_name": a.name if a else None, "position": pos, "to_status": to, "text": text}


def _min_time(s: Session, ids: list[int]) -> datetime:
    """Операция не может быть раньше последней установки/снятия задействованных узлов (+1 с)."""
    last_in = s.scalar(select(func.max(Installation.installed_at)).where(Installation.component_id.in_(ids)))
    last_out = s.scalar(select(func.max(Installation.removed_at)).where(Installation.component_id.in_(ids)))
    last = max([d for d in (last_in, last_out) if d is not None], default=None)
    return (last.replace(microsecond=0) + timedelta(seconds=1)) if last else datetime(2000, 1, 1)


def execute(s: Session, component_id: int, target: dict, at: datetime | None = None,
            reason: str | None = None, performed_by: str | None = None) -> dict:
    """Выполняет план одной транзакцией (коммит делает вызывающий; при ошибке — откат всего плана)."""
    from ..api.routes import passport_snapshot
    p = plan(s, component_id, target)
    min_at = parse_dt(p["min_at"])
    if at is None:
        at = parse_dt(p["default_at"])
    else:
        at = parse_dt(at).replace(microsecond=0)
    if at < min_at:
        raise DomainError(f"Дата операции раньше последней операции с узлами ({iso(min_at - timedelta(seconds=1))}) — "
                          "укажите более позднюю дату")
    before = {cid: passport_snapshot(s, cid) for cid in p["component_ids"]}
    messages = []
    for i, st in enumerate(p["steps"]):
        when = at + timedelta(seconds=i)
        c = s.get(Component, st["component_id"])
        try:
            if st["op"] == "remove":
                res = inventory.remove(s, c, when, st["to_status"], reason, performed_by)
            else:
                fn = inventory.transfer if st["op"] == "transfer" else inventory.install
                res = fn(s, c, s.get(Aircraft, st["aircraft_id"]), st["position"], when, reason, performed_by)
        except DomainError as e:
            raise DomainError(f"Шаг {i + 1} «{st['text']}» не выполнен: {e.message}. Ни один шаг не применён.") from e
        messages.append(res["message"])
    s.flush()
    after = {cid: passport_snapshot(s, cid) for cid in p["component_ids"]}
    return {"title": p["title"], "steps": p["steps"], "messages": messages, "at": iso(at),
            "components": [{"before": before[cid], "after": after[cid]} for cid in p["component_ids"]],
            "moved_id": component_id}
