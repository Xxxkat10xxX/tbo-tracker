"""Резервное копирование: база SQLite + загруженные фото в один zip-архив.

Копия базы снимается штатным механизмом SQLite (online backup) — без остановки сервиса и без
риска получить «половинчатый» файл. Восстановление выполняется так же, в обратную сторону;
перед восстановлением автоматически создаётся копия текущего состояния.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR, DATABASE_URL, UPLOADS_DIR
from ..errors import DomainError, NotFound

BACKUP_DIR = DATA_DIR / "backups"
NAME_RE = re.compile(r"^tbo-\d{8}-\d{6}-(auto|manual|pre-restore|pre-update|upload)\.zip$")


def db_path() -> Path:
    if not DATABASE_URL.startswith("sqlite:///"):
        raise DomainError("Резервное копирование встроено только для SQLite. Для PostgreSQL используйте pg_dump.")
    return Path(DATABASE_URL.removeprefix("sqlite:///"))


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
    if not p.exists():
        raise NotFound("Нет такой резервной копии")
    return p


def _new_path(kind: str) -> Path:
    """Имя по времени; если копия с таким именем уже есть (та же секунда) — ждём следующую секунду."""
    import time
    while True:
        p = BACKUP_DIR / f"tbo-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}.zip"
        if not p.exists():
            return p
        time.sleep(0.2)


def create(kind: str = "manual") -> dict:
    src_path = db_path()
    if not src_path.exists():
        raise DomainError("База данных ещё не создана")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = _new_path(kind)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "tbo.db"
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
            counts = {t: dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]       # noqa: S608 — имена таблиц фиксированы
                      for t in ("aircraft", "components", "flights", "maintenance_records")
                      if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()}
        finally:
            dst.close()
            src.close()
        part = target.with_suffix(".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "tbo.db")
            if UPLOADS_DIR.exists():
                for f in UPLOADS_DIR.rglob("*"):
                    if f.is_file():
                        z.write(f, f"uploads/{f.relative_to(UPLOADS_DIR).as_posix()}")
            z.writestr("manifest.json", json.dumps({"app": "tbo-tracker", "created_at": datetime.now().isoformat(timespec="seconds"),
                                                    "kind": kind, "counts": counts}, ensure_ascii=False, indent=2))
        part.replace(target)
    return {**_info(target), "counts": counts}


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
    _validate(content)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    p = _new_path("upload")
    p.write_bytes(content)
    return _info(p)


def _validate(content: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise DomainError("Это не архив резервной копии tbo-tracker (.zip)") from e
    if "tbo.db" not in z.namelist():
        raise DomainError("В архиве нет базы данных tbo.db")
    for n in z.namelist():
        if n.startswith("/") or ".." in Path(n).parts:
            raise DomainError("Недопустимые пути в архиве")


def restore(name: str) -> dict:
    """Восстанавливает базу и фото из копии. Перед этим сохраняет текущее состояние (pre-restore)."""
    from ..db import Base, engine, migrate
    from .notify import lock
    p = path_of(name)
    content = p.read_bytes()
    _validate(content)
    live = db_path()
    with lock, tempfile.TemporaryDirectory() as tmp:
        z = zipfile.ZipFile(io.BytesIO(content))
        z.extractall(tmp)
        snap = Path(tmp) / "tbo.db"
        chk = sqlite3.connect(snap)
        try:
            if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DomainError("Файл базы в копии повреждён")
            if not chk.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='aircraft'").fetchone():
                raise DomainError("В копии нет таблиц tbo-tracker")
        finally:
            chk.close()
        before = create("pre-restore") if live.exists() else None
        engine.dispose()
        src, dst = sqlite3.connect(snap), sqlite3.connect(live, timeout=30)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        engine.dispose()
        up = Path(tmp) / "uploads"
        if up.exists():
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            for f in UPLOADS_DIR.iterdir():
                if f.is_file():
                    f.unlink()
            for f in up.rglob("*"):
                if f.is_file():
                    dest = UPLOADS_DIR / f.relative_to(up)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
    Base.metadata.create_all(engine)      # копия старой версии — добавить новые таблицы и столбцы
    migrate()
    return {"message": f"Восстановлено из {name}" + (f". Предыдущее состояние сохранено: {before['name']}" if before else ""),
            "pre_restore": before["name"] if before else None}
