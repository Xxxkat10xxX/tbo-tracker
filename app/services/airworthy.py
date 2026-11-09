"""Допуск борта к полёту: статус, комплектность обязательных позиций, просроченные работы ТО.

Общая проверка для симулятора миссий и для реальных полётов (команда «Старт» борту).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AIRCRAFT_STATUSES, Aircraft
from .maintenance import due_items
from .usage import current_installations


def preflight(s: Session, a: Aircraft) -> list[str]:
    """Пустой список — борт допущен; иначе причины отказа (для оператора)."""
    problems = []
    if a.status != "active":
        problems.append(f"борт в статусе «{AIRCRAFT_STATUSES.get(a.status, a.status)}», а не «В эксплуатации»")
    filled = {i.position for i in current_installations(s).values() if i.aircraft_id == a.id}
    empty = [sl["label"] for sl in a.type.slots or [] if sl.get("required", True) and sl["position"] not in filled]
    if empty:
        problems.append("не установлены узлы: " + ", ".join(empty))
    for it in due_items(s, aircraft_id=a.id, statuses={"overdue"}):
        who = it["entity"]["name"] if it["entity"]["kind"] == "aircraft" else it["entity"]["serial"]
        problems.append(f"просрочено: {it['rule']['name']} ({who})")
    return problems
