"""Серверная логика: перестановка одной транзакцией, сценарии, журнал и рассылка уведомлений,
резервные копии, состояние сервера, вход по паролю, расчёты для интерфейса."""
import json
import os
import re

from tests.conftest import by_serial, login


def _slot(board, serial, pos):
    a = next(a for a in board["aircraft"] if a["serial"] == serial)
    return a, next(s for s in a["slots"] if s["position"] == pos)["component"]


# ------------------------------------------------------------------ перестановка на сервере
def test_board_plan_and_atomic_swap(client):
    b = client.get("/api/board").json()
    a1, c1 = _slot(b, "TBT-M4-001", "ESC1")
    a2, c2 = _slot(b, "TBT-M4-002", "ESC1")
    tgt = {"kind": "slot", "aircraft_id": a2["id"], "position": "ESC1"}
    pl = client.post("/api/board/plan", json={"component_id": c1["id"], "target": tgt}).json()
    assert pl["title"].startswith("Обмен") and [s["op"] for s in pl["steps"]] == ["remove", "transfer", "install"]
    r = client.post("/api/board/move", json={"component_id": c1["id"], "target": tgt}).json()
    assert [x["after"]["location"]["aircraft_id"] for x in r["components"]] == [a2["id"], a1["id"]]
    assert r["components"][0]["before"]["hours"] == r["components"][0]["after"]["hours"]       # наработка сохранена
    # сразу обратно — без ошибок про даты (время подбирает сервер)
    back = client.post("/api/board/move", json={"component_id": c1["id"], "target": {"kind": "slot", "aircraft_id": a1["id"], "position": "ESC1"}})
    assert back.status_code == 200
    # из резерва на занятую позицию — замена; затем обратно в резерв
    spare = next(c for c in client.get("/api/board").json()["spare"] if c["serial"] == "ESC40-0324")
    r = client.post("/api/board/move", json={"component_id": spare["id"], "target": {"kind": "slot", "aircraft_id": a1["id"], "position": "ESC2"}}).json()
    assert r["title"].startswith("Замена")
    assert client.post("/api/board/move", json={"component_id": spare["id"], "target": {"kind": "stock"}}).status_code == 200
    assert by_serial(client, "ESC40-0324")["status"] == "stock"


def test_board_move_rejects_and_rolls_back(client):
    b = client.get("/api/board").json()
    a1, fc = _slot(b, "TBT-M4-001", "FC")
    a2, esc = _slot(b, "TBT-M4-002", "ESC2")
    r = client.post("/api/board/move", json={"component_id": fc["id"], "target": {"kind": "slot", "aircraft_id": a2["id"], "position": "ESC2"}})
    assert r.status_code == 400 and "другого типа" in r.json()["detail"]
    # дата раньше последней операции — понятная ошибка, ничего не изменено
    a1b, c1 = _slot(b, "TBT-M4-001", "ESC1")
    r = client.post("/api/board/move", json={"component_id": c1["id"], "at": "2000-01-01T00:00:00Z",
                                             "target": {"kind": "slot", "aircraft_id": a2["id"], "position": "ESC1"}})
    assert r.status_code == 400 and "раньше последней операции" in r.json()["detail"]
    _, still = _slot(client.get("/api/board").json(), "TBT-M4-002", "ESC1")
    assert still["serial"] != c1["serial"]
    # узел в ремонте не устанавливается
    rep = next(c for c in b["spare"] if c["status"] == "maintenance" and c["type"]["code"] == "esc")
    r = client.post("/api/board/move", json={"component_id": rep["id"], "target": {"kind": "slot", "aircraft_id": a2["id"], "position": "ESC1"}})
    assert r.status_code == 400
    assert client.post("/api/board/move", json={"component_id": rep["id"], "target": {"kind": "stock"}}).status_code == 400


# ------------------------------------------------------------------ сценарии на сервере
def test_scenarios_run_on_server(client):
    lst = client.get("/api/scenarios").json()
    assert [x["n"] for x in lst] == list(range(1, 11))
    kinds = {}
    for n in range(1, 11):
        r = client.post(f"/api/scenarios/{n}/run")
        assert r.status_code == 200, (n, r.text)
        kinds[n] = [b["t"] for b in r.json()["blocks"]]
    assert "diff" in kinds[1] and "import" in kinds[1]
    assert "move" in kinds[9]
    s1 = client.post("/api/scenarios/1/run").json()["blocks"]
    assert any(b["t"] == "notice" and "уже был загружен" in b["text"] for b in s1)          # повтор распознан
    assert client.post("/api/scenarios/99/run").status_code == 400


def test_aircraft_readiness_from_server(client):
    a = by_serial(client, "TBT-M4-003")
    assert a["readiness"]["level"] in ("gray", "bad") and a["readiness"]["text"]
    ok = [x for x in client.get("/api/aircraft").json() if x["airworthy"]]
    assert ok and all(x["readiness"]["level"] in ("ok", "warn") for x in ok)


# ------------------------------------------------------------------ уведомления на сервере
def test_notification_feed_history_and_delivery(client, monkeypatch):
    from app.services import notify, serverconf
    f0 = client.get("/api/notifications/feed").json()
    assert f0["events"] == [] and f0["last_id"] > 0 and f0["counts"]["total"] > 0           # первичное заполнение — тихое
    assert client.get("/api/notifications/history").json() == []
    # каналы рассылки: HTTP перехватываем
    sent = []
    monkeypatch.setattr(notify, "_http_post", lambda url, payload, timeout=10: sent.append((url, payload)) or "ok (200)")
    serverconf.save({"telegram": {"enabled": True, "token": "123:ABC", "chat_id": "42"},
                     "webhook": {"enabled": True, "url": "http://example.invalid/hook"}})
    client.post("/api/demo-logs/07_TBT-VT2-011_padenie.json/import")
    f1 = client.get(f"/api/notifications/feed?after={f0['last_id']}").json()      # проверка + рассылка в фоне
    cats = {e["category"] for e in f1["events"]}
    assert {"overdue", "sensor"} <= cats
    assert any("TBT-VT2-011" in e["title"] for e in f1["events"])
    assert len(sent) == 2                                                         # одно сообщение на канал
    from app.db import SessionLocal
    tg = next(p for u, p in sent if "api.telegram.org/bot123:ABC/sendMessage" in u)
    assert tg["chat_id"] == "42" and "TBT-VT2-011" in tg["text"]
    wh = next(p for u, p in sent if u == "http://example.invalid/hook")
    assert wh["source"] == "tbo-tracker" and wh["events"]
    hist = client.get("/api/notifications/history").json()
    assert hist and hist[0]["delivered_at"] and any(h["delivery"].get("telegram") == "ok (200)" for h in hist)
    with SessionLocal() as s:                     # повторно не отправляется
        assert notify.deliver_pending(s) == {}
    # закрытие: после отката импорта позиция «осмотр после падения» уходит
    imp = client.get("/api/imports").json()[0]
    client.post(f"/api/imports/{imp['id']}/rollback")
    client.get("/api/notifications/feed")
    closed = [h for h in client.get("/api/notifications/history").json() if "TBT-VT2-011" in h["title"] and h["category"] == "overdue"]
    assert closed and closed[0]["resolved_at"]
    serverconf.save({"telegram": {"enabled": False}, "webhook": {"enabled": False}})


def test_server_settings_and_test_message(client, monkeypatch):
    from app.services import notify
    s = client.get("/api/server/settings").json()
    assert s["backup"]["hour"] == 3 and s["telegram"]["has_token"] is False
    r = client.put("/api/server/settings", json={"telegram": {"enabled": True, "token": "999:SECRET", "chat_id": "7"}})
    assert r.status_code == 200 and r.json()["telegram"]["token"].startswith("••••") and "SECRET" not in r.text
    r = client.put("/api/server/settings", json={"telegram": {"enabled": True, "token": "", "chat_id": "7"}})   # пустой токен не стирает
    assert r.json()["telegram"]["has_token"] is True
    r = client.put("/api/server/settings", json={"telegram": {"enabled": True, "chat_id": ""}})
    assert r.status_code == 400
    assert client.get("/api/server/settings").json()["telegram"]["chat_id"] == "7"     # отказ ничего не сохранил
    assert client.put("/api/server/settings", json={"public_url": "javascript:alert(1)"}).status_code == 400
    assert client.put("/api/server/settings", json={"webhook": {"enabled": True, "url": "ftp://x"}}).status_code == 400
    assert client.put("/api/server/settings", json={"scan_interval_s": 5}).status_code == 422
    monkeypatch.setattr(notify, "_http_post", lambda url, payload, timeout=10: "ok (200)")
    assert client.post("/api/server/notify/test").json()["result"] == {"telegram": "ok (200)"}
    client.put("/api/server/settings", json={"telegram": {"enabled": False}})
    assert client.post("/api/server/notify/test").status_code == 400


# ------------------------------------------------------------------ резервные копии и состояние
def test_backup_create_download_restore(client):
    b = client.post("/api/server/backups").json()
    assert b["name"].endswith("-manual.zip") and b["counts"]["aircraft"] == 5
    dl = client.get(f"/api/server/backups/{b['name']}")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    a = by_serial(client, "TBT-M4-001")
    client.patch(f"/api/aircraft/{a['id']}", json={"name": "Изменённый"})
    r = client.post(f"/api/server/backups/{b['name']}/restore").json()
    assert r["pre_restore"].endswith("-pre-restore.zip")
    assert client.get("/api/aircraft").status_code == 401          # после восстановления все сессии завершены
    login(client)
    assert by_serial(client, "TBT-M4-001")["name"] == "Борт 01"
    up = client.post("/api/server/backups/upload", files={"file": ("x.zip", dl.content)}).json()
    assert up["name"].endswith("-upload.zip")
    assert client.post("/api/server/backups/upload", files={"file": ("x.zip", b"not a zip")}).status_code == 400
    assert client.get("/api/server/backups/..%2Ftbo.db").status_code == 404
    assert client.delete(f"/api/server/backups/{up['name']}").status_code == 200
    names = [x["name"] for x in client.get("/api/server/backups").json()]
    assert b["name"] in names and up["name"] not in names



def test_restore_and_media_only_photos(client):
    """Из копии восстанавливаются только фото; /media отдаёт только фото (HTML/JS с этого же сайта = XSS)."""
    import io
    import zipfile

    from app.config import UPLOADS_DIR
    png = b"\x89PNG\r\n\x1a\n" + bytes(64)
    url = client.post("/api/drone-images", files={"files": ("Мой борт.png", png, "image/png")}).json()["added"][0]
    assert client.get(url).status_code == 200
    assert client.get("/media/index.json").status_code == 404           # служебный файл подписей не отдаётся
    b = client.post("/api/server/backups").json()
    src = zipfile.ZipFile(io.BytesIO(client.get(f"/api/server/backups/{b['name']}").content))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in src.namelist():
            z.writestr(n, src.read(n))
        z.writestr("uploads/evil.html", "<script src=/media/evil.js></script>")
        z.writestr("uploads/evil.js", "alert(1)")
        z.writestr("uploads/sub/" + "a" * 32 + ".png", png)
    (UPLOADS_DIR / "planted.html").write_text("<b>x</b>")              # посторонний файл в каталоге (не через сервер)
    assert client.get("/media/planted.html").status_code == 404
    up = client.post("/api/server/backups/upload", files={"file": ("x.zip", buf.getvalue())}).json()
    client.post(f"/api/server/backups/{up['name']}/restore")
    login(client)
    names = sorted(f.name for f in UPLOADS_DIR.iterdir())      # фото из копии (в т.ч. от других тестов) + подписи
    assert url.rsplit("/", 1)[1] in names and "index.json" in names, names
    assert all(re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|webp)|index\.json", n) for n in names), names   # ни HTML/JS, ни sub/
    assert client.get(url).status_code == 200
    assert client.get("/media/evil.html").status_code == 404 and client.get("/media/evil.js").status_code == 404

def test_backup_prune_keeps_manual(client):
    from app.services import backup
    backup.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        p = backup.BACKUP_DIR / f"tbo-2026010{i + 1}-030000-auto.zip"
        p.write_bytes(b"PK")
    (backup.BACKUP_DIR / "tbo-20260101-120000-manual.zip").write_bytes(b"PK")
    removed = backup.prune(2)
    assert sorted(removed) == ["tbo-20260101-030000-auto.zip", "tbo-20260102-030000-auto.zip"]
    assert (backup.BACKUP_DIR / "tbo-20260101-120000-manual.zip").exists()


def test_scheduler_tick_makes_daily_backup(client, monkeypatch):
    from app.services import scheduler, serverconf
    serverconf.save({"backup": {"enabled": True, "hour": 0, "keep": 3}})
    out = scheduler.tick()
    assert out["backup"].endswith("-auto.zip")
    assert "backup" not in scheduler.tick()                     # второй раз за день — не делает
    assert scheduler.STATE["last_tick"]


def test_server_status(client):
    st = client.get("/api/server/status").json()
    assert st["app"]["version"] and st["memory"]["total"] > 0 and st["storage"]["free"] > 0
    assert st["urls"] and "scheduler" in st and isinstance(st["warnings"], list)


def test_estimates_computed_on_server(client):
    a = client.get("/api/analytics").json()
    e = a["estimate"]
    assert abs(e["total"] - (e["risk"] + e["labor"])) < 0.05
    p = {**a["economics"], "engineer_rate": 0, "failure_probability": 0}
    assert client.post("/api/analytics/estimate", json=p).json()["total"] == 0
    m = client.post("/api/missions/estimate", json={"home_lat": 55, "home_lon": 37, "return_home": False,
                                                    "waypoints": [{"lat": 55.009, "lon": 37, "alt": 30}]}).json()
    assert 990 < m["distance_m"] < 1010 and m["duration_s"] > 100


# ------------------------------------------------------------------ вход по паролю и CLI
def test_admin_cli(client, capsys):
    from app.admin import main
    assert main(["backup"]) == 0
    assert "Создана копия" in capsys.readouterr().out
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["app"]["version"]
    assert main(["backups"]) == 0 and "-manual.zip" in capsys.readouterr().out
    assert main(["restore", "tbo-nope.zip"]) == 1


def test_install_files_present():
    root = os.path.dirname(os.path.dirname(__file__))
    for f in ("deploy/raspberry-pi/install.sh", "deploy/raspberry-pi/tbo-ctl", "deploy/raspberry-pi/tbo-tracker.service"):
        assert os.path.exists(os.path.join(root, f)), f
    unit = open(os.path.join(root, "deploy/raspberry-pi/tbo-tracker.service"), encoding="utf-8").read()
    assert "User=tbo" in unit and "Restart=always" in unit and "--workers" not in unit
