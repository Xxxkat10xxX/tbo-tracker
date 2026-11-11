import logging
import time

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_PASSWORD_FILE, DATABASE_URL

log = logging.getLogger("tbo.db")


class Base(DeclarativeBase):
    pass


def database_url():
    """URL базы; пароль PostgreSQL — из файла-секрета (не в переменных окружения и не в URL)."""
    url = make_url(DATABASE_URL)
    if url.password and not DATABASE_PASSWORD_FILE:
        log.warning("Пароль базы указан прямо в TBO_DATABASE_URL — он виден в окружении процесса. "
                    "Используйте TBO_DATABASE_PASSWORD_FILE (файл с правами 600 или Docker secret).")
    if DATABASE_PASSWORD_FILE:
        try:
            with open(DATABASE_PASSWORD_FILE, encoding="utf-8") as f:
                url = url.set(password=f.read().strip("\r\n"))
        except OSError as e:
            raise SystemExit(f"TBO_DATABASE_PASSWORD_FILE: не удалось прочитать {DATABASE_PASSWORD_FILE}: {e}") from e
    return url


def make_engine(url=None):
    url = url or database_url()
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            from pathlib import Path
            try:
                Path(url.database).parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass        # нет прав — понятное сообщение даст проверка каталога при запуске (main._check_data_dir)
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")   # безопасно в режиме WAL, меньше записей на SD/SSD
            cur.execute("PRAGMA busy_timeout=10000")   # фоновая проверка и запросы не мешают друг другу
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def is_sqlite() -> bool:
    return engine.url.get_backend_name() == "sqlite"


def wait_for_database(timeout_s: int = 90):
    """PostgreSQL может подниматься дольше приложения (docker compose, перезагрузка сервера) — ждём."""
    deadline = time.monotonic() + timeout_s
    if is_sqlite() and engine.url.database and engine.url.database != ":memory:":
        from pathlib import Path
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as e:  # noqa: BLE001
            if is_sqlite() or time.monotonic() > deadline:
                raise SystemExit(f"База данных недоступна: {e}") from e
            log.warning("База данных недоступна, повтор через 2 с: %s", str(e).splitlines()[0][:200])
            time.sleep(2)


RESOURCE_COLUMNS = {"resource_hours": "FLOAT", "resource_cycles": "INTEGER", "resource_days": "INTEGER"}
EXTRA_COLUMNS = {
    "aircraft": {"image": "VARCHAR(300)"},
    "components": {"uid": "VARCHAR(40)", "specs": "JSON"},
    "component_types": {"spec_fields": "JSON"},
    "maintenance_rules": {"interval_events": "INTEGER", "warn_events": "INTEGER"},
    "mission_runs": {"source": "VARCHAR(20)"},
    "missions": {"frame": "VARCHAR(10)", "yaw_mode": "VARCHAR(10)"},
    "component_usage_entries": {"aircraft_entry_id": "INTEGER"},
}


def migrate(eng=None):
    """Добавляет новые столбцы в базы, созданные прошлыми версиями (create_all их не меняет)."""
    from sqlalchemy import inspect, text
    eng = eng or engine
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in ("aircraft", "components", "component_types", "maintenance_rules", "mission_runs", "missions",
                      "component_usage_entries"):
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            cols = {**(RESOURCE_COLUMNS if table in ("aircraft", "components") else {}), **EXTRA_COLUMNS.get(table, {})}
            for col, typ in cols.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
        if insp.has_table("components"):   # старым паспортам — уникальные идентификаторы (переносимо: SQLite и PostgreSQL)
            import uuid
            for (cid,) in conn.execute(text("SELECT id FROM components WHERE uid IS NULL")).fetchall():
                conn.execute(text("UPDATE components SET uid = :u WHERE id = :i"), {"u": uuid.uuid4().hex, "i": cid})
    if insp.has_table("installations"):        # базам прошлых версий — уникальность открытых установок (models.Installation)
        for name, cols in (("ux_install_open_component", "component_id"), ("ux_install_open_position", "aircraft_id, position")):
            try:
                with eng.begin() as conn:
                    conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON installations ({cols}) "
                                      "WHERE removed_at IS NULL"))
            except Exception as e:  # noqa: BLE001 — в базе уже есть дубли: работаем, но сообщаем
                log.error("Индекс %s не создан — в истории установок есть одновременные открытые установки: %s",
                          name, str(e).splitlines()[0][:200])


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
