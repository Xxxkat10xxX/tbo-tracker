"""Фоновые задачи сервера: проверка уведомлений и рассылка, ежедневная резервная копия.

Работают внутри процесса приложения (один процесс uvicorn), поэтому не нужны cron/systemd-таймеры.
Отключаются переменной окружения ``TBO_SCHEDULER=0`` (используется в тестах).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..timeutil import iso, utcnow
from . import backup, notify, serverconf

log = logging.getLogger("tbo.scheduler")
STATE = {"started_at": None, "last_tick": None, "last_error": None, "last_delivery": None, "last_auto_backup": None}


def _auto_backup_due(conf: dict, now_local: datetime) -> bool:
    b = conf["backup"]
    if not b.get("enabled") or now_local.hour < int(b.get("hour", 3)):
        return False
    today = now_local.strftime("%Y%m%d")
    return not any(x["kind"] == "auto" and x["name"].startswith(f"tbo-{today}") for x in backup.list_backups())


def tick() -> dict:
    """Один проход: уведомления → рассылка → резервная копия по расписанию."""
    from ..db import SessionLocal
    conf = serverconf.load()
    out = {}
    STATE["last_error"] = None
    with SessionLocal() as s:
        from . import auth
        auth.cleanup(s)                       # просроченные сессии и старые записи аудита
        from . import drones
        drones.sweep(s)                       # просроченные команды бортам, зависшие подготовки полёта
        out["new"] = len(notify.scan(s))
        res = notify.deliver_pending(s, conf)
        if res:
            STATE["last_delivery"] = {"at": iso(utcnow()), "result": res}
            out["delivery"] = res
    try:
        if _auto_backup_due(conf, datetime.now()):
            b = backup.create("auto")
            backup.prune(int(conf["backup"].get("keep", 14)))
            STATE["last_auto_backup"] = b["name"]
            out["backup"] = b["name"]
    except Exception as e:  # noqa: BLE001 — сбой копии не должен останавливать проверку уведомлений
        log.exception("auto backup failed")
        STATE["last_error"] = f"резервная копия: {e}"
    STATE["last_tick"] = iso(utcnow())
    return out


async def run_forever(stop: asyncio.Event):
    STATE["started_at"] = iso(utcnow())
    await asyncio.sleep(5)                       # дать серверу подняться
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler tick failed")
            STATE["last_error"] = str(e)
        interval = max(15, int(serverconf.load().get("scan_interval_s", 60)))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
