"""Безопасность: вход, сессии, CSRF, роли, хранение паролей, перебор, первичная настройка, аудит, заголовки."""
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import AuthSession, User
from app.security import ANY_ROLE_WRITE, PUBLIC_EXACT, required_role
from app.services import auth
from tests.conftest import TEST_ADMIN, TEST_PASSWORD, login

XRW = {"X-Requested-With": "tbo"}


def _user(client, username, role, password="Надёжный-пароль-2026", must_change=False):
    r = client.post("/api/auth/users", json={"username": username, "display_name": username.title(), "role": role,
                                             "password": password, "must_change_password": must_change})
    assert r.status_code == 201, r.text
    return r.json()


def _new_client():
    c = TestClient(app)
    c.__enter__()
    return c


# ------------------------------------------------------------------ без входа
def test_everything_requires_login_except_shell(anon):
    for path in ("/api/aircraft", "/api/components", "/api/board", "/api/server/status", "/api/auth/users", "/api/audit",
                 "/api/notifications/feed", "/media/x.png", "/api/missions/1/export"):
        r = anon.get(path)
        assert r.status_code == 401 and r.json()["code"] == "auth_required", path
    assert anon.get("/").status_code == 200
    assert anon.get("/static/js/core.js").status_code == 200
    assert anon.get("/health").json() == {"status": "ok"}
    st = anon.get("/api/auth/state").json()
    assert st["user"] is None and st["setup_required"] is False and "csrf_token" in st and st["csrf_token"] is None
    assert anon.post("/api/imports", headers=XRW).status_code == 401


def test_every_route_has_an_access_rule():
    """Каждый метод API классифицирован: без входа — только чтение оболочки, вход и настройка."""
    public_writes, public_reads, total = [], [], 0
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            m = method.upper()
            total += 1
            role = required_role(m, path)
            if role is None:
                (public_reads if m == "GET" else public_writes).append(path)
            if path.startswith(("/api/server/", "/api/auth/users", "/api/audit", "/api/demo/", "/api/scenarios")):
                assert role == "admin", (m, path)
            # бортовой агент — только токен устройства; подключение бортов (токены) — только администратор
            assert (role == "device") == path.startswith("/api/agent/"), (m, path)
            if path.startswith("/api/drones") and m != "GET" and not path.endswith(("/prepare", "/start", "/rtl", "/land", "/cancel")):
                assert role == "admin", (m, path)
            if m in ("POST", "PUT", "PATCH", "DELETE") and path not in PUBLIC_EXACT and path not in ANY_ROLE_WRITE \
                    and not path.startswith("/api/agent/"):
                assert role in ("engineer", "admin"), (m, path)
    assert total > 80
    assert sorted(set(public_writes)) == ["/api/auth/login", "/api/auth/setup"]
    assert sorted(set(public_reads)) == ["/api/auth/state"]


# ------------------------------------------------------------------ вход и пароли
def test_password_stored_as_argon2id_and_token_hashed(client):
    with SessionLocal() as s:
        u = s.query(User).filter_by(username=TEST_ADMIN).one()
        assert u.password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=2$") and TEST_PASSWORD not in u.password_hash
        token = client.cookies.get("tbo_session")
        row = s.query(AuthSession).filter_by(user_id=u.id).first()
        assert token and row.token_hash == hashlib.sha256(token.encode()).hexdigest() and token not in row.token_hash


def test_login_cookie_flags_and_generic_errors(anon):
    r = anon.post("/api/auth/login", json={"username": TEST_ADMIN, "password": TEST_PASSWORD}, headers=XRW)
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and "path=/" in sc
    bad1 = anon.post("/api/auth/login", json={"username": TEST_ADMIN, "password": "неверный пароль"}, headers=XRW)
    bad2 = anon.post("/api/auth/login", json={"username": "nobody", "password": "неверный пароль"}, headers=XRW)
    assert bad1.status_code == bad2.status_code == 401 and bad1.json()["detail"] == bad2.json()["detail"]
    assert anon.post("/api/auth/login", json={"username": TEST_ADMIN, "password": TEST_PASSWORD}).status_code == 403   # без X-Requested-With


def test_secure_cookie_and_hsts_behind_https(anon, monkeypatch):
    import app.security as sec
    monkeypatch.setattr(sec, "PUBLIC_URL", "https://tbo.example.com")
    r = anon.post("/api/auth/login", json={"username": TEST_ADMIN, "password": TEST_PASSWORD}, headers=XRW)
    sc = r.headers["set-cookie"]
    assert sc.startswith("__Host-tbo_session=") and "Secure" in sc and "Domain" not in sc
    assert "max-age=31536000" in anon.get("/health").headers["strict-transport-security"]


def test_bruteforce_lockout_and_unlock(client):
    _user(client, "victim", "engineer")
    c = _new_client()
    for _ in range(4):
        assert c.post("/api/auth/login", json={"username": "victim", "password": "wrong-password-x"}, headers=XRW).status_code == 401
    r = c.post("/api/auth/login", json={"username": "victim", "password": "wrong-password-x"}, headers=XRW)
    assert r.status_code == 429 and "Повторите через" in r.json()["detail"]
    r = c.post("/api/auth/login", json={"username": "victim", "password": "Надёжный-пароль-2026"}, headers=XRW)
    assert r.status_code == 429                                        # верный пароль во время блокировки не помогает
    for _ in range(5):                                                 # несуществующее имя ведёт себя так же
        r = c.post("/api/auth/login", json={"username": "ghost", "password": "wrong-password-x"}, headers=XRW)
    assert r.status_code == 429
    uid = next(u["id"] for u in client.get("/api/auth/users").json() if u["username"] == "victim")
    assert next(u for u in client.get("/api/auth/users").json() if u["username"] == "victim")["locked"]
    assert client.post(f"/api/auth/users/{uid}/unlock").status_code == 200
    auth.ip_limiter.reset("testclient")
    assert c.post("/api/auth/login", json={"username": "victim", "password": "Надёжный-пароль-2026"}, headers=XRW).status_code == 200


def test_ip_rate_limit(anon):
    for i in range(20):
        anon.post("/api/auth/login", json={"username": f"u{i}", "password": "wrong-password-x"}, headers=XRW)
    r = anon.post("/api/auth/login", json={"username": TEST_ADMIN, "password": TEST_PASSWORD}, headers=XRW)
    assert r.status_code == 429 and "адреса" in r.json()["detail"]


@pytest.mark.parametrize("pw,ok", [("короткий", False), ("password123", False), ("qwerty12345", False), ("aaaaaaaaaaaa", False),
                                   ("adminadmin12", False), ("Синий-трактор-едет", True), ("correct horse battery", True)])
def test_password_policy(pw, ok):
    assert (not auth.password_problems(pw, "admin")) is ok


def test_logout_and_password_change_end_sessions(client):
    other = _new_client()
    login(other)
    assert other.get("/api/aircraft").status_code == 200
    r = client.post("/api/auth/password", json={"current_password": "неверно-неверно", "new_password": "Новый-пароль-2026-x"})
    assert r.status_code == 403
    assert client.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "123"}).status_code == 400
    r = client.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "Новый-пароль-2026-x"})
    assert r.status_code == 200
    assert client.get("/api/aircraft").status_code == 200              # текущая сессия продолжается
    assert other.get("/api/aircraft").status_code == 401               # другие устройства вышли
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/aircraft").status_code == 401


# ------------------------------------------------------------------ CSRF и Origin
def test_csrf_token_required_for_changes(client):
    a = client.get("/api/aircraft").json()[0]
    token = client.headers.pop("X-CSRF-Token")
    assert client.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}).json()["code"] == "csrf"
    assert client.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}, headers={"X-CSRF-Token": "forged-token"}).status_code == 403
    # не-ASCII байты в заголовке (сырой HTTP-клиент) — отказ 403, а не ошибка сервера
    assert client.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}, headers={"X-CSRF-Token": b"\xe9\xe9"}).status_code == 403
    assert client.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}, headers={"X-CSRF-Token": token}).status_code == 200
    r = client.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "origin"
    assert client.patch(f"/api/aircraft/{a['id']}", json={"notes": "y"},
                        headers={"X-CSRF-Token": token, "Origin": "http://testserver"}).status_code == 200


# ------------------------------------------------------------------ роли
def test_roles(client):
    _user(client, "viewer1", "viewer")
    _user(client, "engineer1", "engineer")
    v, e = _new_client(), _new_client()
    login(v, "viewer1", "Надёжный-пароль-2026")
    login(e, "engineer1", "Надёжный-пароль-2026")
    a = v.get("/api/aircraft").json()[0]
    assert v.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}).json()["code"] == "forbidden"
    assert v.post("/api/auth/password", json={"current_password": "Надёжный-пароль-2026", "new_password": "Другой-пароль-2026"}).status_code == 200
    assert e.patch(f"/api/aircraft/{a['id']}", json={"notes": "x"}).status_code == 200
    for path in ("/api/server/status", "/api/server/backups", "/api/auth/users", "/api/audit"):
        assert e.get(path).status_code == 403, path
    assert e.post("/api/server/backups").status_code == 403
    assert e.post("/api/demo/reset").status_code == 403
    assert e.post("/api/auth/users", json={"username": "x1", "role": "admin", "password": "Надёжный-пароль-2026"}).status_code == 403
    # смена роли завершает сессии пользователя
    uid = next(u["id"] for u in client.get("/api/auth/users").json() if u["username"] == "engineer1")
    assert client.patch(f"/api/auth/users/{uid}", json={"role": "viewer"}).status_code == 200
    assert e.get("/api/aircraft").status_code == 401


def test_admin_safeguards(client):
    me = next(u for u in client.get("/api/auth/users").json() if u["username"] == TEST_ADMIN)
    assert client.delete(f"/api/auth/users/{me['id']}").status_code == 400
    assert client.patch(f"/api/auth/users/{me['id']}", json={"role": "viewer"}).status_code == 400
    assert client.patch(f"/api/auth/users/{me['id']}", json={"is_active": False}).status_code == 400
    assert client.post("/api/auth/users", json={"username": "admin", "password": "Надёжный-пароль-2026"}).status_code == 409
    assert client.post("/api/auth/users", json={"username": "плохое имя", "password": "Надёжный-пароль-2026"}).status_code == 400
    assert client.post("/api/auth/users", json={"username": "weak1", "password": "12345678"}).status_code == 400
    u = _user(client, "temp1", "engineer")
    assert client.patch(f"/api/auth/users/{u['id']}", json={"is_active": False}).status_code == 200
    c = _new_client()
    r = c.post("/api/auth/login", json={"username": "temp1", "password": "Надёжный-пароль-2026"}, headers=XRW)
    assert r.status_code == 403 and "отключена" in r.json()["detail"]
    assert client.delete(f"/api/auth/users/{u['id']}").status_code == 200


def test_temporary_password_must_be_changed(client):
    _user(client, "newbie", "engineer", must_change=True)
    c = _new_client()
    r = login(c, "newbie", "Надёжный-пароль-2026")
    assert r.json()["user"]["must_change_password"] is True
    assert c.get("/api/aircraft").json()["code"] == "password_change_required"
    assert c.get("/api/auth/me").status_code == 200
    assert c.post("/api/auth/password", json={"current_password": "Надёжный-пароль-2026", "new_password": "Мой-собственный-2026"}).status_code == 200
    assert c.get("/api/aircraft").status_code == 200


# ------------------------------------------------------------------ первичная настройка
def test_first_run_setup_with_one_time_code(anon):
    with SessionLocal() as s:
        s.query(AuthSession).delete()
        s.query(User).delete()
        s.commit()
        code = auth.ensure_setup_code(s)
    assert auth.SETUP_FILE.read_text(encoding="utf-8").strip() == code
    if os.name == "posix":                      # права файлов — только POSIX (сервер — Linux / Raspberry Pi)
        assert oct(auth.SETUP_FILE.stat().st_mode)[-3:] == "600"
    assert anon.get("/api/auth/state").json()["setup_required"] is True
    bad = anon.post("/api/auth/setup", json={"code": "WRONG-CODE", "username": "boss", "password": "Начальник-смены-2026"}, headers=XRW)
    assert bad.status_code == 403
    r = anon.post("/api/auth/setup", json={"code": code.lower(), "username": "Boss", "password": "Начальник-смены-2026"}, headers=XRW)
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin" and r.json()["user"]["username"] == "boss"
    assert not auth.SETUP_FILE.exists()
    anon.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert anon.get("/api/auth/users").status_code == 200
    again = anon.post("/api/auth/setup", json={"code": code, "username": "evil", "password": "Захват-сервера-2026"}, headers=XRW)
    assert again.status_code == 409


def test_bootstrap_admin_from_secret_file(tmp_path, monkeypatch):
    f = tmp_path / "admin_password"
    f.write_text("Пароль-из-секрета-2026\n", encoding="utf-8")       # секрет Docker / systemd — UTF-8 на любой ОС
    monkeypatch.setattr(auth, "ADMIN_PASSWORD_FILE", str(f))
    with SessionLocal() as s:
        s.query(AuthSession).delete()
        s.query(User).delete()
        s.commit()
        assert auth.bootstrap_admin_from_file(s) is True
        assert auth.bootstrap_admin_from_file(s) is False                 # только при пустой базе
        u = s.query(User).one()
        assert u.role == "admin" and auth.verify_password(u.password_hash, "Пароль-из-секрета-2026")


# ------------------------------------------------------------------ аудит, заголовки, лимиты
def test_audit_log(client):
    a = client.get("/api/aircraft").json()[0]
    client.patch(f"/api/aircraft/{a['id']}", json={"notes": "проверка аудита"})
    TestClient(app).post("/api/auth/login", json={"username": TEST_ADMIN, "password": "неверный-пароль"}, headers=XRW)
    rows = client.get("/api/audit?limit=50").json()
    acts = [(r["action"], r["username"], r["target"], r["status"]) for r in rows]
    assert ("api_write", TEST_ADMIN, f"PATCH /api/aircraft/{a['id']}", 200) in acts
    assert any(x[0] == "login_failed" and x[1] == TEST_ADMIN for x in acts)
    assert any(x[0] == "login" for x in acts)
    assert all(TEST_PASSWORD not in str(r) for r in rows)


def test_security_headers(client):
    r = client.get("/api/aircraft")
    h = r.headers
    assert "default-src 'self'" in h["content-security-policy"] and "'unsafe-inline'" not in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store" and "server" not in {k.lower() for k in h if k.lower() == "server" and "uvicorn" in h[k]}
    assert client.get("/").headers["content-security-policy"].startswith("default-src 'self'")


def test_body_size_limits(client):
    big = b"x" * (26 * 1024 * 1024)
    r = client.post("/api/imports", files={"file": ("big.csv", big)})
    assert r.status_code == 413


def test_docs_disabled_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_demo_endpoints_off_without_demo_mode(client, monkeypatch):
    import app.config
    monkeypatch.setattr(app.config, "DEMO_MODE", False)
    assert client.post("/api/demo/reset").status_code == 404
    assert client.get("/api/demo-logs").status_code == 404
    assert client.post("/api/scenarios/1/run").status_code == 404


def test_demo_reset_keeps_users(client):
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get("/api/auth/users").status_code == 200                 # сессия и учётные записи сохранились


def test_trusted_proxy_client_ip(monkeypatch):
    import ipaddress

    import app.security as sec
    from starlette.requests import Request

    def req(peer, xff):
        return Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", xff.encode())],
                        "client": (peer, 1234), "scheme": "http", "server": ("x", 80), "query_string": b""})
    monkeypatch.setattr(sec, "TRUSTED_PROXIES", [])
    assert sec.client_ip(req("10.0.0.5", "1.2.3.4")) == "10.0.0.5"          # чужим заголовкам не верим
    monkeypatch.setattr(sec, "TRUSTED_PROXIES", [ipaddress.ip_network("10.0.0.5/32")])
    assert sec.client_ip(req("10.0.0.5", "6.6.6.6, 1.2.3.4")) == "1.2.3.4"


def test_password_change_bruteforce_ends_session(client):
    for _ in range(5):
        client.post("/api/auth/password", json={"current_password": "подбор-пароля-xx", "new_password": "Новый-пароль-2026-x"})
    r = client.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "Новый-пароль-2026-x"})
    assert r.status_code == 401
    assert client.get("/api/aircraft").status_code == 401
    auth.password_limiter.reset(TEST_ADMIN)


def test_login_replaces_previous_session_cookie(client):
    old = client.cookies.get("tbo_session")
    login(client)
    assert client.cookies.get("tbo_session") != old
    with SessionLocal() as s:
        assert s.query(AuthSession).filter_by(token_hash=hashlib.sha256(old.encode()).hexdigest()).count() == 0


def test_generator_cannot_write_fake_flights_in_production(client, monkeypatch):
    import app.config
    monkeypatch.setattr(app.config, "DEMO_MODE", False)
    body = {"aircraft_serials": ["TBT-M4-001"], "date_from": "2026-11-01", "date_to": "2026-11-02", "import_now": True}
    assert client.post("/api/log-generator", json=body).status_code == 403
    assert client.post("/api/log-generator", json={**body, "dry_run": True}).status_code == 200
    assert client.post("/api/log-generator", json={**body, "import_now": False}).status_code == 200


def test_rate_limiter_memory_is_bounded():
    """Распределённый перебор (тысячи адресов/имён) не раздувает память процесса."""
    from app.services.auth import RateLimiter
    lim = RateLimiter(3, 600)
    lim.MAX_KEYS = 100
    for i in range(5000):
        lim.hit(f"10.0.{i // 250}.{i % 250}")
    assert len(lim._hits) <= 100
    for _ in range(3):
        lim.hit("attacker")
    assert lim.blocked("attacker") > 0


def test_allowed_hosts(anon, monkeypatch):
    """TBO_ALLOWED_HOSTS: чужое имя сайта в Host (DNS rebinding) → 400; localhost и разрешённые имена — работают."""
    from app import security
    monkeypatch.setattr(security, "ALLOWED_HOSTS", {"tbo.local", "192.168.1.50"})
    st = lambda host: anon.get("/api/auth/state", headers={"Host": host}).status_code  # noqa: E731
    assert st("evil.example") == 400 and st("tbo.local.evil.example") == 400 and st("x@tbo.local") == 400
    assert st("tbo.local") == 200 and st("TBO.local:8000") == 200 and st("192.168.1.50:8000") == 200
    assert st("127.0.0.1:8000") == 200 and st("localhost") == 200 and st("[::1]:8000") == 200
    r = anon.get("/health", headers={"Host": "rebind.attacker.example"})
    assert r.status_code == 400 and r.json()["code"] == "host"
    # за HTTPS-прокси (Caddy передаёт исходный Host): имя из TBO_PUBLIC_URL разрешено без перечисления
    monkeypatch.setattr(security, "PUBLIC_URL", "https://tbo.example.com")
    assert st("tbo.example.com") == 200 and st("tbo.example.com:443") == 200 and st("evil.example") == 400
    monkeypatch.setattr(security, "ALLOWED_HOSTS", set())
    assert st("anything.example") == 200
