"""Настройки сервера (каналы уведомлений, резервное копирование, периодичность проверки).

Хранятся в файле ``<TBO_DATA_DIR>/server.json`` (права 600), а не в базе: сброс демо-данных
и восстановление резервной копии их не затрагивают. Меняются на странице «Сервер» или через API.
"""
from __future__ import annotations

import copy
import json
import os
import threading

from ..config import DATA_DIR

PATH = DATA_DIR / "server.json"
DEFAULTS = {
    "scan_interval_s": 60,
    "categories": ["limit", "overdue", "sensor"],   # что рассылать во внешние каналы
    "telegram": {"enabled": False, "token": "", "chat_id": ""},
    "webhook": {"enabled": False, "url": ""},
    "backup": {"enabled": True, "hour": 3, "keep": 14},
    "public_url": "",                                # адрес для ссылок в сообщениях, напр. http://tbo-tracker.local
}
_lock = threading.Lock()


def _merge(base: dict, upd: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (upd or {}).items():
        if k not in base:
            continue
        out[k] = _merge(base[k], v) if isinstance(base[k], dict) and isinstance(v, dict) else v
    return out


def load() -> dict:
    with _lock:
        try:
            data = json.loads(PATH.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
    return _merge(DEFAULTS, data)


def save(update: dict) -> dict:
    cur = load()
    # пустой или маскированный токен из формы не затирает сохранённый
    tg = (update.get("telegram") or {})
    if "token" in tg and (not tg["token"] or str(tg["token"]).startswith("•")):
        tg.pop("token")
    new = _merge(cur, update)
    with _lock:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(PATH)
    return new


def masked(conf: dict | None = None) -> dict:
    c = copy.deepcopy(conf or load())
    tok = c["telegram"].get("token") or ""
    c["telegram"]["token"] = ("•••• " + tok[-4:]) if tok else ""
    c["telegram"]["has_token"] = bool(tok)
    return c
