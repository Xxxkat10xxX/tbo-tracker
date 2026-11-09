"""Резервное копирование: логическая выгрузка всех таблиц в JSON + загруженные фото, один zip-архив.

Формат ``tbo-backup/2`` не зависит от СУБД: копию SQLite можно восстановить в PostgreSQL и наоборот
(перенос с Raspberry Pi на сервер). Выгрузка — в одной транзакции (согласованный снимок без остановки сервиса),
восстановление — тоже в одной транзакции: при любой ошибке база остаётся прежней. Перед восстановлением
автоматически делается копия текущего состояния. Старые копии (``tbo.db`` внутри архива) тоже принимаются.

Сессии входа и журнал аудита в копию не входят. После восстановления все пользователи выходят из системы.
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, create_engine, event, inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError

from ..config import DATA_DIR, UPLOADS_DIR, VERSION
from ..errors import DomainError, NotFound

log = logging.getLogger("tbo.backup")

BACKUP_DIR = DATA_DIR / "backups"
FORMAT = "tbo-backup/2"
NAME_RE = re.compile(r"^tbo-\d{8}-\d{6}-(auto|manual|pre-restore|pre-update|upload)\.zip$")
EXCLUDED = {"auth_sessions", "audit_log"}          # не переносим: сессии и журнал безопасности
MAX_UNPACKED = 2 * 1024 ** 3                        # защита от «zip-бомб»


def _tables():
    from ..db import Base
    from .. import models  # noqa: F401 — регистрирует все таблицы
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED]


def _info(p: Path) -> dict:
    st = p.stat()
    kind = NAME_RE.match(p.name).group(1)
    return {"name": p.name, "kind": kind, "size": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")}


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    files = [p for p in BACKUP_DIR.iterdir() if NAME_RE.match(p.name)]
    return [_info(p) for p in sorted(files, key=lambda p: p.name, reverse=True)]


def path_of(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise NotFound("Нет такой резервной копии")
    p = BACKUP_DIR / name
    if not p.is_file():
        raise NotFound("Нет такой резервной копии")
    return p


def _new_path(kind: str) -> Path:
    """Имя по времени; если копия с таким именем уже есть (та же секунда) — ждём следующую секунду."""
    while True:
        p = BACKUP_DIR / f"tbo-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}.zip"
        if not p.exists():
            return p
        time.sleep(0.2)


def _jsonable(v):
    return v.isoformat() if isinstance(v, datetime) else v


def export_tables() -> dict[str, list[dict]]:
    """Согласованный снимок всех таблиц данных (одна транзакция)."""
    from ..db import engine, is_sqlite
    conn = engine.connect()
    if not is_sqlite():
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
    with conn, conn.begin():
        out = {}
        for t in _tables():
            q = select(t).order_by(*t.primary_key.columns)
            out[t.name] = [{k: _jsonable(v) for k, v in row.items()} for row in conn.execute(q).mappings()]
        return out


def create(kind: str = "manual") -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = _new_path(kind)
    data = export_tables()
    counts = {k: len(v) for k, v in data.items()}
    part = target.with_suffix(".part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for name, rows in data.items():
                z.writestr(f"tables/{name}.json", json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
            if UPLOADS_DIR.exists():
                for f in UPLOADS_DIR.rglob("*"):
                    if f.is_file():
                        z.write(f, f"uploads/{f.relative_to(UPLOADS_DIR).as_posix()}")
            from ..db import engine
            z.writestr("manifest.json", json.dumps({
                "format": FORMAT, "app": "tbo-tracker", "version": VERSION, "kind": kind,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "database": engine.url.get_backend_name(), "counts": counts}, ensure_ascii=False, indent=2))
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    try:
        target.chmod(0o600)                      # в копии — хеши паролей
    except OSError:
        pass
    return {**_info(target), "counts": {k: counts.get(k, 0) for k in ("aircraft", "components", "flights", "maintenance_records", "users")}}


def prune(keep: int) -> list[str]:
    """Удаляет старые автоматические копии сверх ``keep`` (ручные не трогает)."""
    autos = [b for b in list_backups() if b["kind"] == "auto"]
    removed = []
    for b in autos[max(1, keep):]:
        (BACKUP_DIR / b["name"]).unlink(missing_ok=True)
        removed.append(b["name"])
    return removed


def delete(name: str):
    path_of(name).unlink()


def save_upload(content: bytes) -> dict:
    _open_checked(content)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    p = _new_path("upload")
    p.write_bytes(content)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return _info(p)


def _open_checked(content: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise DomainError("Это не архив резервной копии tbo-tracker (.zip)") from e
    names = z.namelist()
    if "manifest.json" not in names and "tbo.db" not in names:
        raise DomainError("В архиве нет данных tbo-tracker (manifest.json или tbo.db)")
    total = 0
    for info in z.infolist():
        n = info.filename
        if n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n:
            raise DomainError("Недопустимые пути в архиве")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise DomainError("Архив слишком большой после распаковки")
    return z


UPLOAD_NAME = re.compile(r"uploads/(?:[0-9a-f]{32}\.(?:png|jpg|webp)|index\.json)")


def _upload_name_ok(n: str) -> bool:
    """Из копии восстанавливаются только фото (имена, которые даёт сервер) и их подписи — никаких HTML/JS и подкаталогов:
    всё из uploads/ отдаётся браузеру с этого же сайта."""
    return UPLOAD_NAME.fullmatch(n) is not None


def _payload_v2(z: zipfile.ZipFile) -> dict[str, list[dict]]:
    try:
        manifest = json.loads(z.read("manifest.json"))
    except (KeyError, ValueError) as e:
        raise DomainError("Повреждён manifest.json") from e
    if manifest.get("format") != FORMAT:
        raise DomainError(f"Неизвестный формат копии: {manifest.get('format')}")
    known = {t.name for t in _tables()}
    out = {}
    for n in z.namelist():
        m = re.fullmatch(r"tables/([a-z_]+)\.json", n)
        if not m or m.group(1) not in known:
            continue
        try:
            rows = json.loads(z.read(n))
        except ValueError as e:                  # обрезанный или испорченный файл в архиве — не 500
            raise DomainError(f"Повреждена таблица {m.group(1)} в копии") from e
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise DomainError(f"Повреждена таблица {m.group(1)} в копии")
        out[m.group(1)] = rows
    return out


def _payload_sqlite(db_file: Path) -> dict[str, list[dict]]:
    """Старый формат (файл SQLite) → те же строки, что и в формате 2 (подходит для любой СУБД)."""
    chk = sqlite3.connect(db_file)
    try:
        chk.execute("PRAGMA trusted_schema=OFF")      # файл из чужой копии: никаких функций из схемы
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DomainError("Файл базы в копии повреждён")
    finally:
        chk.close()
    eng = create_engine(f"sqlite:///{db_file}")

    @event.listens_for(eng, "connect")
    def _readonly(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA trusted_schema=OFF")
        dbapi_conn.execute("PRAGMA query_only=ON")

    try:
        insp = inspect(eng)
        have = set(insp.get_table_names())
        if "aircraft" not in have:
            raise DomainError("В копии нет таблиц tbo-tracker")
        out = {}
        with eng.connect() as conn:
            for t in _tables():
                if t.name not in have:
                    continue
                cols = {c["name"] for c in insp.get_columns(t.name)}
                # типизированная выборка (даты, JSON, логические) — только столбцы, существовавшие в старой версии
                q = select(*[c for c in t.columns if c.name in cols])
                out[t.name] = [dict(r) for r in conn.execute(q).mappings()]
        return out
    finally:
        eng.dispose()


def _convert(table, rows: list[dict]) -> list[dict]:
    cols = {c.name: c for c in table.columns}
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            c = cols.get(k)
            if c is None:
                continue
            if v is not None and isinstance(c.type, DateTime) and isinstance(v, str):
                try:
                    v = datetime.fromisoformat(v.replace("Z", "").replace(" ", "T"))
                except ValueError as e:
                    raise DomainError(f"Неверная дата в таблице {table.name}: {v}") from e
            d[k] = v
        out.append(d)
    return out


def _apply(payload: dict[str, list[dict]]) -> dict:
    """Заменяет все данные содержимым копии в одной транзакции."""
    from ..db import engine, is_sqlite
    from ..models import AuthSession
    tables = _tables()
    users = payload.get("users") or []
    keep_users = not any(u.get("role") == "admin" and u.get("is_active", True) for u in users)
    with engine.begin() as conn:
        for t in reversed(tables):
            if t.name == "users" and keep_users:
                continue
            conn.execute(t.delete())
        conn.execute(AuthSession.__table__.delete())             # все выходят: учётные записи могли смениться
        counts = {}
        for t in tables:
            if t.name == "users" and keep_users:
                continue
            rows = _convert(t, payload.get(t.name, []))
            if rows:
                pk = [c.name for c in t.primary_key.columns]
                rows.sort(key=lambda r: tuple((r.get(k) is None, r.get(k)) for k in pk))
                for i in range(0, len(rows), 1000):
                    conn.execute(t.insert(), rows[i:i + 1000])
            counts[t.name] = len(rows)
        if not is_sqlite():
            for t in tables:
                if "id" in t.columns and t.columns["id"].primary_key:
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "                        # noqa: S608
                        f"COALESCE((SELECT MAX(id) FROM \"{t.name}\"), 0) + 1, false)"))
    return {"counts": counts, "users_kept": keep_users}


def restore(name: str) -> dict:
    """Восстанавливает данные и фото из копии. Перед этим сохраняет текущее состояние (pre-restore)."""
    from .notify import lock
    p = path_of(name)
    content = p.read_bytes()
    z = _open_checked(content)
    with lock, tempfile.TemporaryDirectory() as tmp:
        if "manifest.json" in z.namelist():
            payload = _payload_v2(z)
        else:
            snap = Path(tmp) / "tbo.db"
            snap.write_bytes(z.read("tbo.db"))
            payload = _payload_sqlite(snap)
        before = create("pre-restore")
        try:
            res = _apply(payload)
        except (IntegrityError, DataError) as e:   # транзакция откатилась целиком — база прежняя
            raise DomainError(f"Копия {name} не восстановлена: данные в ней нарушают правила целостности "
                              f"({str(e.orig).splitlines()[0][:200]}). Текущая база не изменена.") from e
        ups = [n for n in z.namelist() if n.startswith("uploads/") and not n.endswith("/")]
        skipped = [n for n in ups if not _upload_name_ok(n)]
        if skipped:
            log.warning("Восстановление: пропущены посторонние файлы в uploads/: %s", ", ".join(skipped[:10]))
        ups = [n for n in ups if _upload_name_ok(n)]
        if ups:
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            for f in UPLOADS_DIR.iterdir():
                if f.is_file():
                    f.unlink()
            for n in ups:
                dest = UPLOADS_DIR / Path(n).name
                with z.open(n) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    msg = f"Восстановлено из {name}. Предыдущее состояние сохранено: {before['name']}."
    msg += (" Учётные записи сохранены текущие (в копии нет администратора)." if res["users_kept"]
            else " Учётные записи — как в копии; все пользователи вышли из системы.")
    return {"message": msg, "pre_restore": before["name"], "counts": res["counts"], "users_kept": res["users_kept"]}
