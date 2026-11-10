"""Видео с борта через сервер: поддельный web_video_server борта (MJPEG) на 127.0.0.1.

Права (вход обязателен, наблюдателю можно), только борт на связи, только перечисленные потоки, понятные ошибки,
предел одновременных просмотров, освобождение места после закрытия просмотра.
"""
import socket
import socketserver
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import DroneLink
from app.services import video
from app.timeutil import utcnow
from tests.conftest import login

BOUNDARY = b"boundarydonotcross"
FRAME = b"\xff\xd8" + b"jpeg" * 50 + b"\xff\xd9"


class FakeVideoServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode("latin-1")
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        self.server.requests.append(line)
        self.wfile.write(b"HTTP/1.0 200 OK\r\nServer: web_video_server\r\n"
                         b"Content-type: multipart/x-mixed-replace;boundary=" + BOUNDARY + b"\r\n\r\n")
        self.wfile.flush()
        # как настоящий web_video_server: на тему, которую никто не публикует (или «%2F» в имени), — 200 и тишина
        if "topic=/main_camera/image_raw&" not in line and "topic=/main_camera/image_raw_throttled&" not in line:
            time.sleep(3)
            return
        try:
            for _ in range(400):                         # ~20 с кадров, пока клиент не закроет соединение
                self.wfile.write(b"--" + BOUNDARY + b"\r\nContent-type: image/jpeg\r\nContent-Length: "
                                 + str(len(FRAME)).encode() + b"\r\n\r\n" + FRAME + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass


@pytest.fixture()
def cam(monkeypatch):
    srv = FakeVideoServer(("127.0.0.1", 0), Handler)
    srv.requests = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(video, "DRONE_VIDEO_PORT", srv.server_address[1])
    monkeypatch.setattr(video, "FIRST_FRAME_S", 1.0)
    yield srv
    srv.shutdown()
    srv.server_close()


def _link(client, online=True, ip="127.0.0.1"):
    a = next(x for x in client.get("/api/aircraft").json() if x["serial"] == "TBT-M4-001")
    lid = client.post("/api/drones", json={"aircraft_id": a["id"]}).json()["id"]
    with SessionLocal() as s:
        link = s.get(DroneLink, lid)
        link.last_ip = ip
        link.last_seen_at = utcnow() if online else None
        s.commit()
    return lid


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_video_access_and_states(client, cam):
    lid = _link(client)
    with TestClient(app) as anon:
        assert anon.get(f"/api/drones/{lid}/video?probe=1").status_code == 401       # без входа — нет
        assert anon.get(f"/api/drones/{lid}/video").status_code == 401
    info = client.get("/api/drones/video/streams").json()
    assert {s["id"] for s in info["streams"]} >= {"main", "aruco", "flow"} and info["max_minutes"] == 30 and info["enabled"]
    r = client.get(f"/api/drones/{lid}/video?probe=1")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert client.get(f"/api/drones/{lid}/video?stream=../../etc&probe=1").status_code == 400   # только свои потоки
    bad = client.get(f"/api/drones/{lid}/video?stream=aruco&probe=1")                  # тема молчит
    assert bad.status_code == 502 and "не даёт кадров" in bad.json()["detail"] and "ArUco" in bad.json()["detail"], bad.text
    assert "topic=/main_camera/image_raw&" in cam.requests[0]                         # «/» в теме не кодируется
    assert all(req.startswith("GET /stream?topic=") for req in cam.requests)          # только видеосервер борта
    with SessionLocal() as s:
        s.get(DroneLink, lid).last_seen_at = None
        s.commit()
    off = client.get(f"/api/drones/{lid}/video?probe=1")
    assert off.status_code == 409 and "не на связи" in off.json()["detail"]


def test_video_stream_viewer_and_limit(client, cam, monkeypatch):
    lid = _link(client)
    client.post("/api/auth/users", json={"username": "watcher", "display_name": "W", "role": "viewer",
                                         "password": "Надёжный-пароль-2026", "must_change_password": False})
    viewer = TestClient(app)
    login(viewer, "watcher", "Надёжный-пароль-2026")
    monkeypatch.setattr(video, "DRONE_VIDEO_MAX", 1)
    with viewer.stream("GET", f"/api/drones/{lid}/video?stream=main") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
        got = b""
        for chunk in r.iter_bytes():
            got += chunk
            if got.count(b"--" + BOUNDARY) >= 3:
                break
        assert got.count(b"\xff\xd8") >= 2                                             # кадры JPEG идут
        assert video.active_count() == 1
        busy = client.get(f"/api/drones/{lid}/video?stream=main")                      # предел просмотров
        assert busy.status_code == 429 and "TBO_DRONE_VIDEO_MAX" in busy.json()["detail"]
        assert client.get(f"/api/drones/{lid}/video?probe=1").status_code == 429       # и проверок (держат соединение)
    assert _wait(lambda: video.active_count() == 0), "место просмотра не освободилось после закрытия"


def test_video_unreachable_board_and_disabled(client, monkeypatch):
    with socket.socket() as s:                                                         # заведомо закрытый порт
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(video, "DRONE_VIDEO_PORT", port)
    lid = _link(client)
    r = client.get(f"/api/drones/{lid}/video?probe=1")
    assert r.status_code == 502 and "web_video_server" in r.json()["detail"]
    assert video.active_count() == 0
    r = client.get(f"/api/drones/{lid}/video")
    assert r.status_code == 502 and video.active_count() == 0                          # неудачная попытка не занимает место
    monkeypatch.setattr(video, "DRONE_VIDEO_PORT", 0)
    assert client.get(f"/api/drones/{lid}/video?probe=1").status_code == 409
    assert client.patch(f"/api/drones/{lid}", json={"enabled": False}).status_code == 200
    monkeypatch.setattr(video, "DRONE_VIDEO_PORT", port)
    assert "отключено" in client.get(f"/api/drones/{lid}/video?probe=1").json()["detail"]
