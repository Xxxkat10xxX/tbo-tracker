from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL, DATA_DIR


class Base(DeclarativeBase):
    pass


def make_engine(url: str = DATABASE_URL):
    if url.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        eng = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


RESOURCE_COLUMNS = {"resource_hours": "FLOAT", "resource_cycles": "INTEGER", "resource_days": "INTEGER"}
EXTRA_COLUMNS = {"aircraft": {"image": "VARCHAR(300)"}}


def migrate(eng=None):
    """Добавляет новые столбцы в базы, созданные прошлыми версиями (create_all их не меняет)."""
    from sqlalchemy import inspect, text
    eng = eng or engine
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in ("aircraft", "components"):
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, typ in {**RESOURCE_COLUMNS, **EXTRA_COLUMNS.get(table, {})}.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
