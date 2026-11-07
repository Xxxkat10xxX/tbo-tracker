"""Картинки бортов: набор иллюстраций БПЛА по отраслям и загруженные фото."""
from __future__ import annotations

import uuid

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


def uploaded_list() -> list[dict]:
    if not UPLOADS_DIR.exists():
        return []
    files = sorted(UPLOADS_DIR.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"url": f"/media/{f.name}", "title": "Загруженное фото"} for f in files if f.suffix in (".png", ".jpg", ".webp")]


def validate_url(url: str | None) -> str | None:
    if url in (None, ""):
        return None
    if url in PRESET_URLS:
        return url
    if url.startswith("/media/") and "/" not in url[7:] and (UPLOADS_DIR / url[7:]).is_file():
        return url
    raise DomainError("Неизвестная картинка: выберите из набора или загрузите фото")


def save_upload(content: bytes) -> str:
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
    return f"/media/{name}"
