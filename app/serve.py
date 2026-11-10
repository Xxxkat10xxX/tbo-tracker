"""Запуск сервера (Docker, systemd, run.sh): параметры — из переменных окружения TBO_*.

Один процесс: SQLite и фоновые задачи (уведомления, копии) живут внутри него. Заголовки X-Forwarded-*
обрабатывает само приложение и только от доверенных прокси (TBO_TRUSTED_PROXIES).
"""
import logging
import os

import uvicorn

from .config import HOST, PORT

LEVELS = ("critical", "error", "warning", "info", "debug")


def main():
    os.umask(0o077)               # база, копии, фото, файл кода настройки — только для пользователя сервиса
    level = (os.environ.get("TBO_LOG_LEVEL") or "info").strip().lower()
    if level not in LEVELS:       # опечатка в настройке — понятное сообщение, а не трассировка при запуске
        raise SystemExit(f"Неверное значение TBO_LOG_LEVEL={level!r}: допустимо {', '.join(LEVELS)}")
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("app.main:app", host=HOST, port=PORT, workers=1, proxy_headers=False, server_header=False,
                access_log=False, log_level=level, timeout_graceful_shutdown=10, timeout_keep_alive=10)


if __name__ == "__main__":
    main()
