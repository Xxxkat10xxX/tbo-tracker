#!/usr/bin/env bash
# Установка tbo-tracker на Raspberry Pi 5 (Raspberry Pi OS Lite 64-bit) как системного сервиса.
# shellcheck disable=SC1090  # источник настроек — /etc/tbo-tracker/tbo.env
#
#   sudo ./deploy/raspberry-pi/install.sh                 порт 80, демо-данные, пароль генерируется
#   sudo ./deploy/raspberry-pi/install.sh --no-demo       пустая база (рабочая эксплуатация)
#   sudo ./deploy/raspberry-pi/install.sh --port 8080 --password 'МойПароль1'
#
# Повторный запуск безопасен: обновляет код и зависимости, сохраняет данные и настройки.
# Подробно: docs/RASPBERRY_PI.md
set -euo pipefail

APP_DIR=/opt/tbo-tracker
DATA_DIR=/var/lib/tbo-tracker
ETC_DIR=/etc/tbo-tracker
ENV_FILE=$ETC_DIR/tbo.env
SVC=tbo-tracker
SVC_USER=tbo
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

PORT=80
DEMO=1
PASSWORD=""
NO_PASSWORD=0
AUTH_USER='admin'
UPGRADE=0
SKIP_APT=0

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
Параметры:
  --port N           порт веб-интерфейса (по умолчанию 80)
  --no-demo          не заполнять базу демо-данными
  --password ПАРОЛЬ  пароль входа (по умолчанию генерируется); буквы, цифры и ._-@+=:,%/!?#
  --user ИМЯ         имя пользователя для входа (по умолчанию admin)
  --no-password      без пароля (только для изолированной сети)
  --upgrade          обновление: не трогать настройки в /etc/tbo-tracker/tbo.env
  --skip-apt         не устанавливать системные пакеты (они уже есть)
EOF
}

[[ $(id -u) -eq 0 ]] || exec sudo -- "$0" "$@"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT=${2:?}; shift 2 ;;
    --no-demo) DEMO=0; shift ;;
    --password) PASSWORD=${2:?}; shift 2 ;;
    --user) AUTH_USER=${2:?}; shift 2 ;;
    --no-password) NO_PASSWORD=1; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Неизвестный параметр: $1 (см. --help)" ;;
  esac
done

[[ -f $SRC_DIR/app/main.py && -f $SRC_DIR/requirements.txt ]] || die "Запускайте из каталога с исходниками tbo-tracker (нет $SRC_DIR/app/main.py)"
[[ $PORT =~ ^[0-9]+$ && $PORT -ge 1 && $PORT -le 65535 ]] || die "Неверный порт: $PORT"
[[ $AUTH_USER =~ ^[A-Za-z0-9._-]{1,32}$ ]] || die "Имя пользователя: латиница, цифры, . _ - (до 32 символов)"
if [[ -n $PASSWORD && ! $PASSWORD =~ ^[A-Za-z0-9._@+=:,%/!?#-]{6,64}$ ]]; then
  die "Пароль: 6–64 символа, латиница, цифры и ._-@+=:,%/!?# (без пробелов и кавычек)"
fi

ARCH=$(uname -m)
MODEL=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)
say "Устройство: ${MODEL:-$ARCH} · $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
[[ $ARCH == aarch64 ]] || warn "Ожидается 64-битная ОС (aarch64), сейчас $ARCH. Для Raspberry Pi 5 используйте Raspberry Pi OS Lite (64-bit)."

# ---------------------------------------------------------------- системные пакеты
if [[ $SKIP_APT -eq 0 ]]; then
  command -v apt-get >/dev/null || die "Нужен Debian / Raspberry Pi OS (apt-get). Для других систем см. docs/RASPBERRY_PI.md → Docker."
  say "Системные пакеты (python3-venv, avahi для адреса <имя>.local, rsync, sqlite3)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends python3 python3-venv python3-pip avahi-daemon rsync curl sqlite3 ca-certificates >/dev/null
fi
python3 - <<'PY' || die "Нужен Python 3.10 или новее"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

# ---------------------------------------------------------------- пользователь и каталоги
if ! id "$SVC_USER" &>/dev/null; then
  say "Системный пользователь $SVC_USER"
  useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin --user-group "$SVC_USER"
fi
getent group video >/dev/null && usermod -aG video "$SVC_USER"      # vcgencmd: напряжение и троттлинг
install -d -m 0755 -o root -g root "$APP_DIR"
install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC_DIR"

# ---------------------------------------------------------------- код и зависимости
say "Копирование приложения в $APP_DIR"
if command -v rsync >/dev/null; then
  rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'data/' --exclude 'promo/' \
        --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'tests/' "$SRC_DIR/" "$APP_DIR/"
else
  find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +
  (cd "$SRC_DIR" && tar --exclude=.git --exclude=.venv --exclude=./data --exclude=./promo --exclude=__pycache__ --exclude=./tests -cf - .) | tar -C "$APP_DIR" -xf -
fi
chown -R root:root "$APP_DIR"

say "Виртуальное окружение Python и зависимости (первый раз — несколько минут)"
[[ -x $APP_DIR/.venv/bin/python ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q --retries 5 -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/app" >/dev/null || true

# ---------------------------------------------------------------- настройки
GENERATED=""
if [[ $UPGRADE -eq 1 && -f $ENV_FILE ]]; then
  say "Настройки сохранены: $ENV_FILE"
else
  if [[ -f $ENV_FILE ]]; then
    cp -a "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
  fi
  if [[ $NO_PASSWORD -eq 0 && -z $PASSWORD ]]; then
    PASSWORD=$(tr -dc 'A-HJ-NP-Za-km-z2-9' </dev/urandom | head -c 12 || true)
    GENERATED=1
  fi
  [[ $NO_PASSWORD -eq 1 ]] && PASSWORD=""
  umask 027
  cat >"$ENV_FILE" <<EOF
# tbo-tracker — настройки сервиса. После изменения: sudo systemctl restart $SVC
# (пароль и порт удобнее менять командами: sudo tbo-ctl password / sudo tbo-ctl port N)
TBO_HOST='0.0.0.0'
TBO_PORT='$PORT'
TBO_DATA_DIR='$DATA_DIR'
TBO_AUTO_SEED='$DEMO'
TBO_AUTH_USER='$AUTH_USER'
TBO_AUTH_PASSWORD='$PASSWORD'
TBO_SCHEDULER='1'
EOF
  chown root:"$SVC_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
  umask 022
fi
printf 'SRC_DIR=%q\n' "$SRC_DIR" >"$ETC_DIR/install.conf"

# ---------------------------------------------------------------- утилита и сервис
install -m 0755 "$SRC_DIR/deploy/raspberry-pi/tbo-ctl" /usr/local/bin/tbo-ctl
install -m 0644 "$SRC_DIR/deploy/raspberry-pi/tbo-tracker.service" "/etc/systemd/system/$SVC.service"

say "Инициализация базы данных"
runuser -u "$SVC_USER" -- bash -c 'set -a; . "$0"; set +a; cd /opt/tbo-tracker; exec .venv/bin/python -m app.admin init' "$ENV_FILE"

say "Запуск сервиса $SVC (автозапуск при включении)"
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"

PORT_NOW=$(. "$ENV_FILE"; echo "$TBO_PORT")
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT_NOW/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT_NOW/health" >/dev/null 2>&1 || die "Сервис не ответил. Журнал: sudo journalctl -u $SVC -n 50"

SUFFIX=""; [[ $PORT_NOW == 80 ]] || SUFFIX=":$PORT_NOW"
echo
say "tbo-tracker работает"
echo "   Откройте в браузере на любом устройстве в этой сети:"
echo "     http://$(hostname).local$SUFFIX"
for ip in $(hostname -I 2>/dev/null); do [[ $ip == *:* ]] || echo "     http://$ip$SUFFIX"; done
AUTH_NOW=$(. "$ENV_FILE"; echo "${TBO_AUTH_PASSWORD:-}")
if [[ -n $AUTH_NOW ]]; then
  echo "   Вход: пользователь $(. "$ENV_FILE"; echo "$TBO_AUTH_USER")${GENERATED:+, пароль $AUTH_NOW (сгенерирован — сохраните; сменить: sudo tbo-ctl password)}"
else
  warn "Вход без пароля. Включить: sudo tbo-ctl password"
fi
echo "   Управление: sudo tbo-ctl help   ·   Настройки и резервные копии: страница «Сервер» в веб-интерфейсе"
