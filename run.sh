#!/usr/bin/env bash
# Быстрый запуск без Docker: создаёт venv, ставит зависимости и стартует сервер на :8000
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
