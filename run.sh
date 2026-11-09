#!/usr/bin/env bash
# Локальный запуск для разработки и демонстрации (Linux / macOS): venv, зависимости, сервер на http://127.0.0.1:8000
# Демо-режим включён (демо-данные и сценарии). При первом запуске в консоли появится одноразовый код настройки —
# введите его в браузере и создайте администратора. Для сервера: docs/DEPLOY.md (Docker) или docs/RASPBERRY_PI.md.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
export TBO_DEMO_MODE="${TBO_DEMO_MODE:-1}" TBO_HOST="${TBO_HOST:-127.0.0.1}" TBO_PORT="${PORT:-8000}"
exec python -m app.serve
