"""Видео с борта через сервер: поток MJPEG web_video_server (образ Technic, порт 8080) → браузер.

Браузер не обращается к борту напрямую: страница разрешает картинки только с этого сайта (CSP), а адрес борта
в сети меняется. Сервер подключается к адресу, с которого борт последний раз обращался к API (подключение
по токену устройства), — только к порту видеосервера и только к перечисленным потокам (не произвольный прокси).
Ограничения: число одновременных просмотров, длительность одного просмотра, тайм-ауты подключения и простоя.
"""
from __future__ import annotations

import asyncio
import ipaddress
import itertools
import threading
import time
from urllib.parse import urlencode

from ..config import DRONE_VIDEO_MAX, DRONE_VIDEO_MAX_MIN, DRONE_VIDEO_PORT
from ..errors import DomainError

# поток → (тема ROS, подпись, параметры web_video_server)
STREAMS = {
    "main": ("/main_camera/image_raw", "Камера", {"width": 640, "height": 480, "quality": 50}),
    "main_low": ("/main_camera/image_raw_throttled", "Камера (меньше кадров и трафика)",
                 {"width": 480, "height": 360, "quality": 40}),
    "aruco": ("/aruco_detect/debug", "ArUco — распознанные маркеры", {"width": 640, "height": 480, "quality": 50}),
    "flow": ("/optical_flow/debug", "Оптический поток (отладка)", {"width": 320, "height": 240, "quality": 50}),
}
CONNECT_TIMEOUT_S = 3.0
HEADER_TIMEOUT_S = 5.0
IDLE_TIMEOUT_S = 10.0          # нет данных столько секунд — поток с борта оборвался
FIRST_FRAME_S = 6.0            # первый кадр — не позже (иначе тема пуста: камеры нет или узел не публикует)
CHUNK = 64 * 1024

_active: dict[int, float] = {}     # номер просмотра → время начала (monotonic)
_lock = threading.Lock()
_seq = itertools.count(1)


def streams() -> dict:
    return {"streams": [{"id": k, "label": v[1]} for k, v in STREAMS.items()], "max_minutes": DRONE_VIDEO_MAX_MIN,
            "enabled": bool(DRONE_VIDEO_PORT)}


def _target(ip: str | None) -> str:
    if not DRONE_VIDEO_PORT:
        raise DomainError("Видео с борта выключено на сервере (TBO_DRONE_VIDEO_PORT=0)", 409)
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        raise DomainError("Адрес борта неизвестен — борт ещё не выходил на связь", 409) from None
    if addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        raise DomainError("Адрес борта не подходит для видео", 409)
    return str(addr)


def _path(stream: str) -> str:
    if stream not in STREAMS:
        raise DomainError("Неизвестный видеопоток", 400)
    topic, _, opts = STREAMS[stream]
    # «/» в теме — как есть: web_video_server не раскодирует %2F, отвечает 200 и молчит (тема не найдена)
    return "/stream?" + urlencode({"topic": topic, "type": "mjpeg", **opts}, safe="/")


def _acquire() -> int:
    now = time.monotonic()
    with _lock:
        stale = now - (DRONE_VIDEO_MAX_MIN * 60 + 60)      # просмотр, чей поток не закрылся (обрыв без закрытия)
        for k in [k for k, t in _active.items() if t < stale]:
            del _active[k]
        if len(_active) >= DRONE_VIDEO_MAX:
            raise DomainError(f"Видео уже смотрят {len(_active)} — это предел сервера (TBO_DRONE_VIDEO_MAX). "
                              "Закройте лишние вкладки и повторите", 429)
        n = next(_seq)
        _active[n] = now
        return n


def _release(n: int):
    with _lock:
        _active.pop(n, None)


def active_count() -> int:
    with _lock:
        return len(_active)


async def _open(ip: str, stream: str):
    """Подключение к web_video_server: (reader, writer, content_type). Ошибки — DomainError с причиной."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, DRONE_VIDEO_PORT), CONNECT_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        raise DomainError(f"Нет связи с видеосервером борта ({ip}:{DRONE_VIDEO_PORT}) — запущен ли web_video_server "
                          "(служба technic)?", 502) from None
    host = f"[{ip}]" if ":" in ip else ip              # IPv6 — в квадратных скобках
    try:
        writer.write(f"GET {_path(stream)} HTTP/1.0\r\nHost: {host}:{DRONE_VIDEO_PORT}\r\n"
                     "User-Agent: tbo-tracker\r\nConnection: close\r\n\r\n".encode("ascii"))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        raise DomainError("Видеосервер борта не ответил", 502) from None
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    ctype = next((ln.split(":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith("content-type:")), "")[:200]
    if status != 200 or not ctype.lower().startswith("multipart/x-mixed-replace"):
        writer.close()
        raise DomainError(f"Видеосервер борта не отдал поток «{STREAMS[stream][1]}» (ответ {status or '—'}) — "
                          "возможно, этой камеры или темы нет на борту", 502)
    # web_video_server отвечает 200 и на тему, которую никто не публикует: ждём первый кадр
    try:
        first = await asyncio.wait_for(reader.read(CHUNK), FIRST_FRAME_S)
    except (OSError, asyncio.TimeoutError):
        first = b""
    if not first:
        writer.close()
        raise DomainError(f"Камера борта не даёт кадров («{STREAMS[stream][1]}», тема {STREAMS[stream][0]}) — "
                          "узел камеры не публикует изображение", 502)
    return reader, writer, ctype, first


async def probe(ip: str | None, stream: str) -> dict:
    """Проверить, что поток доступен и кадры идут (без показа): понятная ошибка до запуска видео в браузере."""
    host = _target(ip)
    _path(stream)
    n = _acquire()                     # проверка тоже держит соединение с бортом (до FIRST_FRAME_S): в общем пределе
    try:
        _, writer, _, _ = await _open(host, stream)
        writer.close()
    finally:
        _release(n)
    return {"ok": True, "stream": stream, "label": STREAMS[stream][1]}


async def open_stream(ip: str | None, stream: str, is_disconnected):
    """(content_type, async-генератор кусков MJPEG). Просмотр занимает место в пределе до конца генератора."""
    host = _target(ip)
    _path(stream)
    n = _acquire()
    try:
        reader, writer, ctype, first = await _open(host, stream)
    except BaseException:
        _release(n)
        raise

    async def body():
        end = time.monotonic() + DRONE_VIDEO_MAX_MIN * 60
        try:
            yield first
            while time.monotonic() < end:
                try:
                    chunk = await asyncio.wait_for(reader.read(CHUNK), IDLE_TIMEOUT_S)
                except (asyncio.TimeoutError, OSError):     # простой или обрыв соединения с бортом — конец потока
                    break
                if not chunk or await is_disconnected():
                    break
                yield chunk
        finally:
            writer.close()
            _release(n)

    return ctype, body()
