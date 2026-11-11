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
        # «очередь на борту» (поток main_low): кадры сняты раз в 50 мс, а уходят раз в 100 мс — метки X-Timestamp
        # (steady_clock борта) всё сильнее отстают от времени прихода, как при раздутом буфере TCP на борту
        queued = "image_raw_throttled&" in line
        t0 = time.monotonic() + 1000.0                   # часы «борта» — не часы сервера
        try:
            for i in range(400):                         # ~20 с кадров, пока клиент не закроет соединение
                frame = FRAME[:2] + b"%06d" % i + FRAME[2:]                 # номер кадра — для проверки задержки
                self.server.sent = i
                stamp = t0 + i * 0.05 if queued else time.monotonic() + 1000.0
                self.wfile.write(b"--" + BOUNDARY + b"\r\nContent-type: image/jpeg\r\nContent-Length: "
                                 + str(len(frame)).encode() + b"\r\nX-Timestamp: %.06f\r\n\r\n" % stamp + frame + b"\r\n")
                self.wfile.flush()
                time.sleep(0.1 if queued else 0.05)
        except OSError:
            pass


@pytest.fixture()
def cam(monkeypatch):
    srv = FakeVideoServer(("127.0.0.1", 0), Handler)
    srv.requests, srv.sent = [], -1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(video, "DRONE_VIDEO_PORT", srv.server_address[1])
    monkeypatch.setattr(video, "FIRST_FRAME_S", 1.0)
    monkeypatch.setattr(video, "VIEWER_IDLE_S", 1.0)
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
            if got.count(b"--tboframe") >= 3:
                break
        assert got.count(b"\xff\xd8") >= 2                                             # кадры JPEG идут
        assert video.active_count() == 1
        busy = client.get(f"/api/drones/{lid}/video/frame?stream=main_low")            # второе соединение с бортом
        assert busy.status_code == 429 and "TBO_DRONE_VIDEO_MAX" in busy.json()["detail"]
        assert client.get(f"/api/drones/{lid}/video?stream=main_low&probe=1").status_code == 429
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


def test_video_frames_do_not_lag_behind(client, cam):
    """Кадры по одному: браузер забирает их реже, чем камера снимает (медленная сеть), — и всё равно получает
    последний кадр, а не очередь старых. Прежний поток MJPEG копил кадры в буферах сети: задержка до 30 с."""
    lid = _link(client)
    url = f"/api/drones/{lid}/video/frame?stream=main"
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content.startswith(b"\xff\xd8")
    assert r.headers["cache-control"] == "no-store"
    seq, lags = int(r.headers["x-frame-seq"]), []
    for _ in range(4):
        time.sleep(0.4)                                                               # «медленный» браузер
        r = client.get(f"{url}&after={seq}")
        assert r.status_code == 200 and int(r.headers["x-frame-seq"]) > seq
        seq = int(r.headers["x-frame-seq"])
        lags.append(cam.sent - int(r.content[2:8]))                                  # насколько кадр отстал от камеры
        assert int(r.headers["x-frame-age-ms"]) < 300 and int(r.headers["x-frame-lag-ms"]) < 300
    assert max(lags) <= 3, lags                                                       # не очередь: свежий кадр (по 50 мс)
    none = client.get(f"{url}&after={seq + 10**6}&wait=0")                            # нового кадра нет — 204, без ожидания
    assert none.status_code == 204
    assert video.active_count() == 1                                                  # одно соединение с бортом
    assert _wait(lambda: video.active_count() == 0, 5), "приёмник не закрылся без зрителей"
    with TestClient(app) as anon:
        assert anon.get(url).status_code == 401


def test_split_frames_handles_partial_and_missing_length():
    b = b"--bnd"
    one = b"\xff\xd8one\xff\xd9"
    two = b"\xff\xd8two\xff\xd9"
    data = (b"--bnd\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Timestamp: 1234.500000\r\n\r\n" % len(one)
            + one + b"\r\n"
            + b"--bnd\r\nContent-Type: image/jpeg\r\n\r\n" + two + b"\r\n--bnd\r\nContent-Type: image/jpeg\r\nContent-Le")
    frames, rest = video.split_frames(data[:30], b)                                   # заголовок ещё не пришёл целиком
    assert frames == [] and rest == data[:30]
    frames, rest = video.split_frames(data, b)
    assert frames == [(one, 1234.5), (two, None)] and rest.startswith(b"--bnd")      # без Content-Length — до границы
    frames, rest = video.split_frames(b"garbage" * 10, b)
    assert frames == [] and len(rest) <= len(b)


def test_queue_on_board_is_reset_by_reconnect(client, cam, monkeypatch):
    """Очередь кадров на самом борту (буфер TCP борта при слабом Wi-Fi): метки X-Timestamp всё сильнее отстают от
    прихода. Сторож пересоздаёт соединение — очередь борта сбрасывается, отставание снова около нуля; окно приёма
    от борта маленькое, чтобы буфер борта не раздувался."""
    monkeypatch.setattr(video, "LAG_MAX_S", 0.3)
    monkeypatch.setattr(video, "LAG_HOLD_S", 0.3)
    monkeypatch.setattr(video, "RECONNECT_MIN_S", 0.0)
    lid = _link(client)
    url = f"/api/drones/{lid}/video/frame?stream=main_low"
    seq, seen = 0, []
    end = time.time() + 8
    while time.time() < end:
        r = client.get(f"{url}&after={seq}")
        assert r.status_code == 200, r.text
        seq = int(r.headers["x-frame-seq"])
        seen.append((int(r.headers["x-frame-lag-ms"]), int(r.headers["x-feed-reconnects"])))
        if seen[-1][1] >= 1 and seen[-1][0] < 200:
            break
    assert max(lag for lag, _ in seen) > 300, seen                                   # отставание росло…
    assert seen[-1][1] >= 1 and seen[-1][0] < 200, seen                               # …и сброшено переподключением
    assert sum("image_raw_throttled" in q for q in cam.requests) >= 2                 # новое соединение с бортом
    feed = next(iter(video._feeds.values()))
    rcv = feed.writer.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    assert rcv <= 4 * video.RCVBUF                                                    # окно приёма от борта маленькое


def test_reconnect_is_rare(client, cam, monkeypatch):
    """Пересоздание канала — страховка, не чаще RECONNECT_MIN_S: при провалах Wi-Fi на секунды частые
    пересоздания (7 за 47 с в полёте 11.11.2026) только добавляли секунды без кадров."""
    monkeypatch.setattr(video, "LAG_MAX_S", 0.3)
    monkeypatch.setattr(video, "LAG_HOLD_S", 0.3)
    lid = _link(client)
    url = f"/api/drones/{lid}/video/frame?stream=main_low"
    seq, lags = 0, []
    end = time.time() + 2.5
    while time.time() < end:
        r = client.get(f"{url}&after={seq}")
        assert r.status_code == 200 and r.headers["x-feed-reconnects"] == "0", r.headers
        seq = int(r.headers["x-frame-seq"])
        lags.append(int(r.headers["x-frame-lag-ms"]))
    assert max(lags) > 300 and sum("image_raw_throttled" in q for q in cam.requests) == 1
