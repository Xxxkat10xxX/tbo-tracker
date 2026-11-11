"""Видео с борта через сервер: поток MJPEG web_video_server (образ Technic, порт 8080) → последний кадр → браузер.

Браузер не обращается к борту напрямую: страница разрешает картинки только с этого сайта (CSP), а адрес борта
в сети меняется. Сервер подключается к адресу, с которого борт последний раз обращался к API (подключение
по токену устройства), — только к порту видеосервера и только к перечисленным потокам (не произвольный прокси).

Задержка. Прежде сервер пересылал MJPEG как есть: когда Wi-Fi (борт → точка доступа → сервер → браузер) не
успевал за потоком, кадры копились в буферах TCP обоих участков, и картинка отставала на десятки секунд. Теперь:
* «приёмник» (Feed) непрерывно вычитывает поток с борта и хранит только последний кадр — на участке борт → сервер
  ничего не копится;
* браузер забирает кадры по одному (frame): следующий — только после показа предыдущего, с долгим ожиданием
  нового кадра. Задержка — не больше одного кадра и одного обмена с сервером; при медленной сети падает частота
  кадров, а не растёт отставание;
* размер кадра — родной для камеры Technic (320×240): прежние 640×480 вчетверо раздували кадр.

Один приёмник на (борт, поток) обслуживает всех зрителей; он закрывается, если кадры никто не запрашивал
VIEWER_IDLE_S секунд. Предел DRONE_VIDEO_MAX — число приёмников (соединений с бортами).
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from urllib.parse import urlencode

from ..config import DRONE_VIDEO_MAX, DRONE_VIDEO_MAX_MIN, DRONE_VIDEO_PORT
from ..errors import DomainError

# поток → (тема ROS, подпись, параметры web_video_server)
STREAMS = {
    "main": ("/main_camera/image_raw", "Камера", {"width": 320, "height": 240, "quality": 60}),
    "main_low": ("/main_camera/image_raw_throttled", "Камера (5 кадров/с, меньше трафика)",
                 {"width": 320, "height": 240, "quality": 50}),
    "aruco": ("/aruco_detect/debug", "ArUco — распознанные маркеры", {"width": 320, "height": 240, "quality": 60}),
    "flow": ("/optical_flow/debug", "Оптический поток (отладка)", {"width": 320, "height": 240, "quality": 50}),
}
CONNECT_TIMEOUT_S = 3.0
HEADER_TIMEOUT_S = 5.0
IDLE_TIMEOUT_S = 10.0          # нет данных столько секунд — поток с борта оборвался
FIRST_FRAME_S = 6.0            # первый кадр — не позже (иначе тема пуста: камеры нет или узел не публикует)
VIEWER_IDLE_S = 10.0           # кадры никто не запрашивал столько секунд — приёмник закрывается
FRAME_WAIT_S = 4.0             # запрос кадра ждёт новый кадр не дольше (затем 204 — браузер спросит снова)
CHUNK = 64 * 1024
MAX_PART = 2 * 1024 * 1024     # кадр больше — поток испорчен, буфер сбрасывается

_feeds: dict[tuple[str, str], "Feed"] = {}
_lock_state: dict = {}                 # блокировка создания приёмников — своя для каждого цикла asyncio


def _feeds_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _lock_state.get("loop") is not loop:
        _lock_state.update(loop=loop, lock=asyncio.Lock())
    return _lock_state["lock"]


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


def active_count() -> int:
    """Открытые соединения с видеосерверами бортов (приёмники)."""
    return sum(1 for f in _feeds.values() if f.alive())


async def _open(ip: str, stream: str):
    """Подключение к web_video_server: (reader, writer, content_type, первые байты). Ошибки — DomainError."""
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
    # web_video_server отвечает 200 и на тему, которую никто не публикует: ждём первые данные кадра
    try:
        first = await asyncio.wait_for(reader.read(CHUNK), FIRST_FRAME_S)
    except (OSError, asyncio.TimeoutError):
        first = b""
    if not first:
        writer.close()
        raise DomainError(f"Камера борта не даёт кадров («{STREAMS[stream][1]}», тема {STREAMS[stream][0]}) — "
                          "узел камеры не публикует изображение", 502)
    return reader, writer, ctype, first


def _boundary(ctype: str) -> bytes:
    m = re.search(r'boundary="?([^";]+)"?', ctype, re.I)
    b = (m.group(1) if m else "boundarydonotcross").strip()
    return (b if b.startswith("--") else "--" + b).encode("latin-1")


def split_frames(buf: bytes, boundary: bytes):
    """Разбор multipart/x-mixed-replace: (готовые кадры JPEG, необработанный остаток буфера)."""
    frames = []
    while True:
        start = buf.find(boundary)
        if start < 0:
            return frames, buf[-len(boundary):] if len(buf) > len(boundary) else buf
        head_end = buf.find(b"\r\n\r\n", start)
        if head_end < 0:
            return frames, buf[start:]
        headers = buf[start + len(boundary):head_end].decode("latin-1", "replace").lower()
        body = head_end + 4
        m = re.search(r"content-length:\s*(\d+)", headers)
        if m:
            end = body + int(m.group(1))
            if len(buf) < end:
                return frames, buf[start:]
        else:
            end = buf.find(boundary, body)
            if end < 0:
                return frames, buf[start:]
            while end > body and buf[end - 1:end] in (b"\r", b"\n"):
                end -= 1
        data = buf[body:end]
        if data.startswith(b"\xff\xd8"):
            frames.append(data)
        buf = buf[end:]


class Feed:
    """Приёмник потока одного борта: держит соединение с web_video_server и последний кадр."""

    def __init__(self, key, reader, writer, ctype: str, first: bytes):
        self.key, self.reader, self.writer = key, reader, writer
        self.boundary = _boundary(ctype)
        self.frame, self.seq, self.frame_at = None, 0, 0.0
        self.last_req = time.monotonic()
        self.closed, self.error = False, None
        self._new = asyncio.Event()
        self._buf = first
        self.loop = asyncio.get_running_loop()
        self.task = self.loop.create_task(self._run())

    def alive(self) -> bool:
        return not self.closed and not self.loop.is_closed()

    def _take(self):
        frames, self._buf = split_frames(self._buf, self.boundary)
        if len(self._buf) > MAX_PART:
            self._buf = b""
        if frames:
            self.frame, self.seq, self.frame_at = frames[-1], self.seq + 1, time.monotonic()
            self._new.set()
            self._new = asyncio.Event()

    async def _run(self):
        try:
            self._take()
            while time.monotonic() - self.last_req < VIEWER_IDLE_S:
                try:
                    chunk = await asyncio.wait_for(self.reader.read(CHUNK), IDLE_TIMEOUT_S)
                except (asyncio.TimeoutError, OSError):
                    chunk = b""
                if not chunk:
                    self.error = "Поток с борта прервался"
                    break
                self._buf += chunk
                self._take()
        finally:
            self.closed = True
            self._new.set()
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001
                pass
            if _feeds.get(self.key) is self:
                del _feeds[self.key]

    async def next_frame(self, after: int, wait: float):
        """Кадр новее after: (jpeg, seq, возраст в мс) или None, если за wait секунд нового не было."""
        self.last_req = time.monotonic()
        end = time.monotonic() + wait
        while not (self.frame is not None and self.seq > after):
            if self.closed:
                raise DomainError(self.error or "Поток с борта закрыт", 502)
            left = end - time.monotonic()
            if left <= 0:
                return None
            ev = self._new
            try:
                await asyncio.wait_for(ev.wait(), left)
            except asyncio.TimeoutError:
                return None
        self.last_req = time.monotonic()
        return self.frame, self.seq, int((time.monotonic() - self.frame_at) * 1000)


async def _feed(ip: str | None, stream: str) -> Feed:
    host = _target(ip)
    _path(stream)
    key = (host, stream)
    async with _feeds_lock():
        f = _feeds.get(key)
        if f is not None and f.alive() and f.loop is asyncio.get_running_loop():
            f.last_req = time.monotonic()
            return f
        for k in [k for k, x in _feeds.items() if not x.alive()]:     # приёмники закрытых циклов (перезапуск)
            del _feeds[k]
        if active_count() >= DRONE_VIDEO_MAX:
            raise DomainError(f"Открыто {active_count()} видеопотоков с бортов — это предел сервера (TBO_DRONE_VIDEO_MAX). "
                              "Закройте лишние вкладки или потоки и повторите", 429)
        reader, writer, ctype, first = await _open(host, stream)
        f = _feeds[key] = Feed(key, reader, writer, ctype, first)
        return f


async def probe(ip: str | None, stream: str) -> dict:
    """Проверить, что поток доступен и кадры идут: понятная ошибка до запуска видео в браузере. Открытый приёмник
    остаётся — первый кадр показа берётся из него без нового подключения к борту."""
    f = await _feed(ip, stream)
    if await f.next_frame(0, FIRST_FRAME_S) is None:
        raise DomainError(f"Камера борта не даёт кадров («{STREAMS[stream][1]}», тема {STREAMS[stream][0]})", 502)
    return {"ok": True, "stream": stream, "label": STREAMS[stream][1]}


async def frame(ip: str | None, stream: str, after: int = 0, wait: float = FRAME_WAIT_S):
    """Последний кадр новее after: (jpeg, seq, возраст в мс) или None (нового кадра за wait секунд не было)."""
    f = await _feed(ip, stream)
    return await f.next_frame(after, max(0.0, min(wait, FRAME_WAIT_S)))


async def open_stream(ip: str | None, stream: str, is_disconnected):
    """MJPEG для внешних программ (VLC и т. п.): кадры из приёмника, только новые, не дольше DRONE_VIDEO_MAX_MIN.
    Страница tbo-tracker использует frame() — так задержка не накапливается на медленной сети."""
    f = await _feed(ip, stream)
    boundary = b"tboframe"

    async def body():
        end, seq = time.monotonic() + DRONE_VIDEO_MAX_MIN * 60, 0
        while time.monotonic() < end and not await is_disconnected():
            try:
                got = await f.next_frame(seq, FRAME_WAIT_S)
            except DomainError:
                break
            if got is None:
                continue
            jpeg, seq, _ = got
            yield (b"--" + boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                   + b"\r\n\r\n" + jpeg + b"\r\n")

    return "multipart/x-mixed-replace;boundary=" + boundary.decode(), body()
