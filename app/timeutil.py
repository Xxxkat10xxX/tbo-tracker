"""Работа со временем: внутри системы всё хранится в UTC без tzinfo."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def parse_dt(value) -> datetime:
    """ISO-8601 (с 'Z', смещением или без) -> naive UTC. Без зоны считаем UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            raise ValueError("пустая дата")
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        s = s.replace(" ", "T", 1) if "T" not in s and " " in s else s
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(microsecond=0).isoformat() + "Z"
