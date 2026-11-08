import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="tbo-test-")
os.environ["TBO_DATA_DIR"] = _tmp
os.environ["TBO_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["TBO_AUTO_SEED"] = "0"
os.environ["TBO_SCHEDULER"] = "0"
os.environ.pop("TBO_AUTH_PASSWORD", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import reset_and_seed  # noqa: E402


@pytest.fixture()
def client():
    import shutil

    from app.services import backup, serverconf
    serverconf.PATH.unlink(missing_ok=True)          # настройки сервера и копии — свои у каждого теста
    shutil.rmtree(backup.BACKUP_DIR, ignore_errors=True)
    reset_and_seed()
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
