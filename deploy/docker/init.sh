#!/bin/sh
# Подготовка к первому запуску в Docker: .env из примера и случайный пароль PostgreSQL (Docker secret).
# Запуск из корня проекта: sh deploy/docker/init.sh
set -eu
cd "$(dirname "$0")/../.."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан .env из .env.example — проверьте настройки"
fi
umask 077
mkdir -p secrets
chmod 700 secrets
if [ ! -s secrets/db_password.txt ]; then
  # 32 случайных байта в base64 без переводов строк; файл читается контейнерами (каталог secrets закрыт 700)
  head -c 32 /dev/urandom | base64 | tr -d '\n=+/' > secrets/db_password.txt
  chmod 644 secrets/db_password.txt
  echo "Создан secrets/db_password.txt (пароль PostgreSQL). Не добавляйте его в git и храните копию в надёжном месте."
fi
echo "Готово. Запуск: docker compose -f compose.yaml -f compose.postgres.yaml up -d --build"
