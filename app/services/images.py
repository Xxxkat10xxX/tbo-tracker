"""Картинки бортов: набор иллюстраций БПЛА по отраслям и загруженные фото."""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from ..config import MAX_IMAGE_BYTES, UPLOADS_DIR
from ..errors import DomainError

PRESETS = [
    {"id": "inspection", "title": "Инспекция ЛЭП и объектов", "industry": "Энергетика, промышленность",
     "description": "Квадрокоптер с гиростабилизированной камерой"},
    {"id": "mapping", "title": "Картография и геодезия", "industry": "Геодезия, кадастр, строительство",
     "description": "VTOL с вертикальным взлётом и аэрофотокамерой"},
    {"id": "agro", "title": "Сельское хозяйство", "industry": "Агро: внесение СЗР, мониторинг посевов",
     "description": "Гексакоптер-опрыскиватель с баком и штангами"},
    {"id": "delivery", "title": "Доставка грузов", "industry": "Логистика, медицина",
     "description": "Квадрокоптер с грузовым контейнером"},
    {"id": "rescue", "title": "Поиск и спасение", "industry": "МЧС, охрана, мониторинг",
     "description": "Квадрокоптер с тепловизором и прожектором"},
]
PRESET_URLS = {f"/static/drones/{p['id']}.svg": p for p in PRESETS}

# Принимаем только растровые фото (SVG не принимаем: в нём может быть скрипт)
SIGNATURES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}


def preset_list() -> list[dict]:
    return [{**p, "url": f"/static/drones/{p['id']}.svg"} for p in PRESETS]


INDEX = "index.json"   # подписи фото пула: {имя файла: подпись}
_index_lock = threading.Lock()   # чтение-изменение-запись подписей: параллельные загрузки не теряют чужие подписи


def _index() -> dict:
    f = UPLOADS_DIR / INDEX
    try:
        return json.loads(f.read_text("utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def _save_index(data: dict):
    """Атомарно: сбой посреди записи не оставляет испорченный файл (иначе пропали бы все подписи)."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPLOADS_DIR / f".{INDEX}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(UPLOADS_DIR / INDEX)


def _update_index(fn):
    with _index_lock:
        idx = _index()
        fn(idx)
        _save_index(idx)


def uploaded_list(used: dict[str, list[str]] | None = None) -> list[dict]:
    """Пул загруженных фото (новые сверху). used — {url: [имена бортов]}."""
    if not UPLOADS_DIR.exists():
        return []
    idx, used = _index(), used or {}
    files = sorted((f for f in UPLOADS_DIR.iterdir() if f.suffix in (".png", ".jpg", ".webp")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"url": f"/media/{f.name}", "title": idx.get(f.name) or "Своё фото", "used_by": used.get(f"/media/{f.name}", [])}
            for f in files]


def title_from_filename(name: str | None) -> str:
    stem = Path(name or "").stem.replace("_", " ").replace("-", " ").strip()
    return stem[:80] or "Своё фото"


def delete_upload(url: str, used_by: list[str]):
    validate_url(url)
    if not url.startswith("/media/"):
        raise DomainError("Иллюстрации из набора удалить нельзя")
    if used_by:
        raise DomainError(f"Фото назначено бортам: {', '.join(used_by)} — сначала смените им фото", 409)
    name = url[7:]
    (UPLOADS_DIR / name).unlink(missing_ok=True)
    _update_index(lambda idx: idx.pop(name, None))


def rename_upload(url: str, title: str):
    validate_url(url)
    if not url.startswith("/media/"):
        raise DomainError("Подпись можно менять только у своих фото")
    _update_index(lambda idx: idx.__setitem__(url[7:], title.strip()[:80] or "Своё фото"))


def validate_url(url: str | None) -> str | None:
    if url in (None, ""):
        return None
    if url in PRESET_URLS:
        return url
    name = url[7:] if url.startswith("/media/") else ""
    # только сами фото: служебный index.json и прочие файлы каталога картинкой быть не могут
    if name and "/" not in name and "\\" not in name and Path(name).suffix in (".png", ".jpg", ".webp") \
            and (UPLOADS_DIR / name).is_file():
        return url
    raise DomainError("Неизвестная картинка: выберите из набора или загрузите фото")


def save_upload(content: bytes, title: str | None = None) -> str:
    if len(content) > MAX_IMAGE_BYTES:
        raise DomainError("Фото больше 8 МБ", 413)
    ext = next((e for sig, e in SIGNATURES.items() if content.startswith(sig)), None)
    if ext is None and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        ext = "webp"
    if ext is None:
        raise DomainError("Поддерживаются фото JPEG, PNG и WebP")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (UPLOADS_DIR / name).write_bytes(content)
    _update_index(lambda idx: idx.__setitem__(name, (title or "Своё фото")[:80]))
    return f"/media/{name}"
