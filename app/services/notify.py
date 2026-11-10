"""Серверные уведомления: периодическая проверка, журнал событий и доставка во внешние каналы.

* ``scan`` сравнивает текущий расчёт (регламенты + события датчиков) с открытыми записями журнала:
  новые позиции записываются, исчезнувшие — закрываются. Первое заполнение пустого журнала
  (новая установка, сброс демо-данных) — «тихое», без рассылки.
* ``deliver_pending`` отправляет новые записи в Telegram и/или на webhook одним сообщением.
* Интерфейс получает новые записи через ``/api/notifications/feed`` и показывает всплывающие сообщения.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Aircraft, NotificationEvent, SensorEvent, Setting
from ..timeutil import iso, utcnow
from . import serverconf
from .maintenance import due_items

CATEGORY_LABELS = {
    "limit": "Достигнут предельный ресурс",
    "overdue": "Просрочены регламентные работы",
    "approaching": "Приближается срок ТО / ресурса",
    "sensor": "Падение / столкновение по датчикам",
}
ICONS = {"limit": "⛔", "overdue": "⚠️", "approaching": "⏳", "sensor": "💥"}
SENSOR_WINDOW_DAYS = 90
BASELINE_KEY = "notify_baseline"
lock = threading.RLock()          # общая блокировка: проверка, рассылка, восстановление копии


METRIC_NAMES = {"hours": "Часы", "cycles": "Циклы", "days": "Календарь", "events": "Падения/столкновения"}


def _num(v, d: int = 1) -> str:
    return (f"{v:.{d}f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)).replace(".", ",")


def _used(d: dict) -> str:
    """Израсходовано по метрике: часы — всегда до сотых, как в интерфейсе."""
    return f"{float(d['used']):.2f}".replace(".", ",") if d["metric"] == "hours" else _num(d["used"])


def _entity_href(e: dict) -> str:
    return f"#/aircraft/{e['id']}" if e["kind"] == "aircraft" else f"#/components/{e['id']}"


def current(s: Session, now: datetime | None = None) -> dict[str, dict]:
    """Все действующие уведомления по ключу «категория|объект:правило»."""
    now = now or utcnow()
    out = {}
    for it in due_items(s, now=now, statuses={"overdue", "due_soon"}):
        cat = "approaching" if it["status"] == "due_soon" else ("limit" if it["rule"]["kind"] == "life" else "overdue")
        e = it["entity"]
        where = ""
        if e["kind"] == "component" and e.get("location"):
            where = f" · {e['location']['aircraft_name']} ({e['location']['position']})"
        d = it["driver"]
        detail = f"{METRIC_NAMES.get(d['metric'], d['metric'])}: {_used(d)} из {_num(d['limit'], 2)} {d['unit']}" if d else ""
        out[f"{cat}|{it['key']}"] = {"category": cat, "title": f"{e.get('serial')}{where} — {it['rule']['name']}",
                                     "text": detail, "href": _entity_href(e)}
    since = now - timedelta(days=SENSOR_WINDOW_DAYS)
    names = {a.id: a.name for a in s.scalars(select(Aircraft))}
    for ev in s.scalars(select(SensorEvent).where(SensorEvent.occurred_at >= since)):
        kind = "Падение" if ev.kind == "fall" else "Столкновение"
        out[f"sensor|{ev.id}"] = {"category": "sensor",
                                  "title": f"{names.get(ev.aircraft_id, '?')} — {kind}" + (f", {ev.peak_g:.1f} g" if ev.peak_g else ""),
                                  "text": f"{ev.occurred_at:%d.%m.%Y %H:%M} UTC · {ev.details or ev.source}", "href": f"#/aircraft/{ev.aircraft_id}"}
    return out


def scan(s: Session, now: datetime | None = None) -> list[NotificationEvent]:
    """Обновляет журнал уведомлений. Возвращает новые (не «тихие») записи."""
    now = now or utcnow()
    with lock:
        # «Тихое» первое заполнение — только один раз после создания базы (метка в settings), а не
        # «пока журнал пуст»: иначе на новой пустой установке первое настоящее уведомление не разослалось бы
        marker = s.get(Setting, BASELINE_KEY)
        baseline = marker is None
        if baseline:
            s.add(Setting(key=BASELINE_KEY, value={"at": iso(now)}))
        open_ = {e.key: e for e in s.scalars(select(NotificationEvent).where(NotificationEvent.resolved_at.is_(None)))}
        cur = current(s, now)
        new = []
        for key, d in cur.items():
            if key in open_:
                continue
            ev = NotificationEvent(key=key, category=d["category"], title=d["title"][:300], text=d["text"], href=d["href"],
                                   created_at=now, silent=baseline,
                                   delivered_at=now if baseline else None, delivery={} if baseline else None)
            s.add(ev)
            if not baseline:
                new.append(ev)
        for key, ev in open_.items():
            if key not in cur:
                ev.resolved_at = now
        s.commit()
        return new


def event_dict(e: NotificationEvent) -> dict:
    return {"id": e.id, "category": e.category, "category_label": CATEGORY_LABELS.get(e.category, e.category),
            "title": e.title, "text": e.text, "href": e.href, "created_at": iso(e.created_at),
            "resolved_at": iso(e.resolved_at), "silent": e.silent, "delivered_at": iso(e.delivered_at),
            "delivery": e.delivery or {}}


# ------------------------------------------------------------------ каналы доставки
def _http_post(url: str, payload: dict, timeout: float = 10) -> str:
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "tbo-tracker"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — адрес задаёт администратор
            return f"ok ({r.status})"
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", "replace")
        return f"ошибка HTTP {e.code}: {body}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"ошибка: {getattr(e, 'reason', e)}"


MESSAGE_MAX = 3800      # Telegram отклоняет сообщения длиннее 4096 символов (HTTP 400) — пакет не дошёл бы никогда


def format_message(events: list[dict], conf: dict) -> str:
    head = f"tbo-tracker: {'новое уведомление' if len(events) == 1 else f'новых уведомлений: {len(events)}'}"
    tail = conf["public_url"].rstrip("/") + "/#/maintenance" if conf.get("public_url") else ""
    lines, size = [head], len(head) + len(tail) + 40
    for i, e in enumerate(events[:20]):
        line = f"{ICONS.get(e['category'], '•')} {e['title']}" + (f" ({e['text']})" if e.get("text") else "")
        line = line[:600]
        if size + len(line) + 1 > MESSAGE_MAX:
            lines.append(f"… и ещё {len(events) - i}")
            break
        lines.append(line)
        size += len(line) + 1
    else:
        if len(events) > 20:
            lines.append(f"… и ещё {len(events) - 20}")
    if tail:
        lines.append(tail)
    return "\n".join(lines)


def send(events: list[dict], conf: dict | None = None) -> dict:
    """Отправляет пакет событий во включённые каналы. Возвращает итог по каждому каналу."""
    conf = conf or serverconf.load()
    res = {}
    tg = conf["telegram"]
    if tg.get("enabled") and tg.get("token") and tg.get("chat_id"):
        res["telegram"] = _http_post(f"https://api.telegram.org/bot{tg['token']}/sendMessage",
                                     {"chat_id": tg["chat_id"], "text": format_message(events, conf),
                                      "disable_web_page_preview": True})
    wh = conf["webhook"]
    if wh.get("enabled") and wh.get("url"):
        res["webhook"] = _http_post(wh["url"], {"source": "tbo-tracker", "sent_at": iso(utcnow()), "events": events,
                                                "text": format_message(events, conf)})
    return res


def deliver_pending(s: Session, conf: dict | None = None) -> dict:
    """Рассылает недоставленные записи (нужных категорий) одним сообщением на канал."""
    conf = conf or serverconf.load()
    with lock:
        pending = list(s.scalars(select(NotificationEvent).where(NotificationEvent.delivered_at.is_(None))
                                 .order_by(NotificationEvent.id)))
        if not pending:
            return {}
        now = utcnow()
        wanted = [e for e in pending if e.category in conf["categories"] and e.created_at >= now - timedelta(days=1)]
        res = send([event_dict(e) for e in wanted], conf) if wanted else {}
        failed = bool(res) and not any(str(v).startswith("ok") for v in res.values())
        for e in pending:
            if e in wanted:
                e.delivery = res
                if failed:          # нет связи / ошибка канала — повторим при следующей проверке (в течение суток)
                    continue
            else:
                e.delivery = {}
            e.delivered_at = now
        s.commit()
        return res


def feed(s: Session, after: int | None) -> dict:
    """Новые записи журнала после указанного id (для всплывающих сообщений в интерфейсе)."""
    scan(s)
    last = s.scalar(select(func.max(NotificationEvent.id))) or 0
    events = []
    if after is not None:
        events = [event_dict(e) for e in s.scalars(select(NotificationEvent).where(
            NotificationEvent.id > after, NotificationEvent.silent.is_(False)).order_by(NotificationEvent.id))]
    return {"last_id": last, "events": events}


def history(s: Session, limit: int = 100) -> list[dict]:
    return [event_dict(e) for e in s.scalars(select(NotificationEvent).where(NotificationEvent.silent.is_(False))
                                             .order_by(NotificationEvent.id.desc()).limit(limit))]
