"""HTTP-клиент API сервера tbo-tracker (/api/agent/*) на стандартной библиотеке.

* Токен — заголовок Authorization: Bearer; прокси из окружения не используются, перенаправления запрещены
  (токен не уйдёт на другой адрес).
* Часы борта сверяются с сервером по полю server_time: время полёта в журнале — по часам сервера
  (у Orange Pi без интернета часы могут быть неверными).
"""
from __future__ import annotations

import json
import math
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import FEATURES, VERSION


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "перенаправление запрещено: %s" % newurl, headers, fp)


def clean(obj):
    """NaN/Infinity → None (сервер их не принимает), рекурсивно."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Api:
    def __init__(self, url: str, token: str, ca_file: str = None, timeout: float = 15.0):
        self.url, self.timeout = url.rstrip("/"), timeout
        self._headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                         "User-Agent": "tbo-agent/" + VERSION}
        handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
        if self.url.startswith("https://"):
            ctx = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)
        self.clock_offset = 0.0           # серверное время − время борта, с

    def now(self) -> float:
        """Текущее время по часам сервера (unix, с)."""
        return time.time() + self.clock_offset

    def call(self, method: str, path: str, body=None, timeout: float = None, sync_clock: bool = True):
        data = None if body is None else json.dumps(clean(body), allow_nan=False, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=self._headers)
        sent = time.time()
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}").get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise ApiError(e.code, str(detail or e.reason)) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(0, "нет связи с сервером: %s" % (getattr(e, "reason", None) or e)) from None
        try:
            payload = json.loads(raw or b"null")
        except ValueError:
            raise ApiError(0, "сервер вернул не JSON") from None
        # Сверка часов: середина «отправили — получили» ≈ момент ответа сервера, только если сервер ответил сразу.
        # Долгий опрос команд ждёт до 8 с и ставит время в конце — по нему часы «уезжали» бы на секунды.
        if sync_clock and isinstance(payload, dict) and payload.get("server_time"):
            try:
                mid = (sent + time.time()) / 2
                self.clock_offset = parse_iso(payload["server_time"]).timestamp() - mid
            except ValueError:
                pass
        return payload

    # --- методы API
    def hello(self, executor: str, vehicle: dict):
        return self.call("POST", "/api/agent/hello", {"agent_version": VERSION, "executor": executor, "vehicle": vehicle,
                                                      "features": list(FEATURES)})

    def next_command(self, wait: int = 8):
        return self.call("GET", "/api/agent/commands?wait=%d" % wait, timeout=wait + self.timeout, sync_clock=False).get("command")

    def result(self, cid: int, ok: bool, message: str):
        return self.call("POST", "/api/agent/commands/%d/result" % cid, {"ok": bool(ok), "message": message[:500]})

    def state(self, body: dict):
        return self.call("POST", "/api/agent/state", body)

    def upload_log(self, run_id: int, body: dict):
        return self.call("POST", "/api/agent/runs/%d/log" % run_id, body, timeout=max(60.0, self.timeout))
