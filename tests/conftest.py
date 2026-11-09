import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="tbo-test-")
os.environ["TBO_DATA_DIR"] = _tmp
os.environ["TBO_DATABASE_URL"] = os.environ.get("TBO_TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["TBO_DEMO_MODE"] = "1"
os.environ["TBO_SCHEDULER"] = "0"
for _k in ("TBO_AUTO_SEED", "TBO_PUBLIC_URL", "TBO_TRUSTED_PROXIES", "TBO_ADMIN_PASSWORD_FILE", "TBO_DATABASE_PASSWORD_FILE"):
    os.environ.pop(_k, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import reset_and_seed  # noqa: E402

# Учётные данные только для тестовой базы во временном каталоге
TEST_ADMIN, TEST_PASSWORD = "admin", "Тестовый-полёт-2026!"


def reset_all():
    import shutil

    from app.services import auth, backup, serverconf
    auth.flush_audit()
    serverconf.PATH.unlink(missing_ok=True)          # настройки сервера и копии — свои у каждого теста
    shutil.rmtree(backup.BACKUP_DIR, ignore_errors=True)
    Base.metadata.drop_all(engine)
    reset_and_seed()
    for lim in (auth.ip_limiter, auth.name_limiter, auth.setup_limiter, auth.password_limiter):
        lim._hits.clear()
    with SessionLocal() as s:
        auth.create_user(s, TEST_ADMIN, TEST_PASSWORD, "admin", "Тестовый администратор")
        s.commit()


def login(c: TestClient, username: str = TEST_ADMIN, password: str = TEST_PASSWORD):
    c.headers.pop("X-CSRF-Token", None)
    r = c.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-Requested-With": "tbo"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


@pytest.fixture()
def client():
    reset_all()
    with TestClient(app) as c:
        login(c)
        yield c


@pytest.fixture()
def anon():
    """Клиент без входа (база заполнена, администратор есть)."""
    reset_all()
    with TestClient(app) as c:
        yield c


def by_serial(client, serial):
    for a in client.get("/api/aircraft").json():
        if a["serial"] == serial:
            return a
    for c in client.get("/api/components", params={"q": serial}).json():
        if c["serial"] == serial:
            return c
    raise KeyError(serial)


def due(client, serial, code):
    for i in client.get("/api/maintenance/due", params={"status": ""}).json():
        if i["entity"]["serial"] == serial and i["rule"]["code"] == code:
            return i
    raise KeyError((serial, code))
