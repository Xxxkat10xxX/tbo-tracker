#!/usr/bin/env bash
# shellcheck disable=SC1090  # источник настроек — /etc/tbo-tracker/tbo.env
# Установка tbo-tracker на Raspberry Pi 5 (Raspberry Pi OS Lite 64-bit) как системного сервиса.
#
#   sudo ./deploy/raspberry-pi/install.sh                 рабочая установка (пустая база, порт 80)
#   sudo ./deploy/raspberry-pi/install.sh --https         + HTTPS через Caddy (собственный УЦ, адрес https://<имя>.local)
#   sudo ./deploy/raspberry-pi/install.sh --demo          + демо-данные и сценарии проверки
#
# Паролей в параметрах нет: пароль администратора вводится скрыто (или задаётся в браузере по одноразовому коду).
# Повторный запуск безопасен: обновляет код и зависимости, сохраняет данные и настройки. Подробно: docs/RASPBERRY_PI.md
set -euo pipefail

APP_DIR=/opt/tbo-tracker
DATA_DIR=/var/lib/tbo-tracker
ETC_DIR=/etc/tbo-tracker
ENV_FILE=$ETC_DIR/tbo.env
SVC=tbo-tracker
SVC_USER=tbo
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

PORT=80
DEMO=0
HTTPS=0
ADMIN='admin'
UPGRADE=0
SKIP_APT=0
EXPLICIT=0          # заданы ли параметры режима (--https/--port/--demo): без них настройки установленной системы не трогаем

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '3,10p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
Параметры:
  --https            HTTPS через Caddy: https://<имя-хоста>.local (сертификат собственного УЦ Caddy)
  --port N           порт веб-интерфейса без HTTPS (по умолчанию 80)
  --demo             демо-данные, сценарии проверки и кнопка «Сбросить демо-данные»
  --admin ИМЯ        имя первого администратора (по умолчанию admin)
  --upgrade          обновление: не трогать настройки в /etc/tbo-tracker/tbo.env
  --skip-apt         не устанавливать системные пакеты (они уже есть)
EOF
}

[[ $(id -u) -eq 0 ]] || exec sudo -- "$0" "$@"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --https) HTTPS=1; EXPLICIT=1; shift ;;
    --port) PORT=${2:?}; EXPLICIT=1; shift 2 ;;
    --demo) DEMO=1; EXPLICIT=1; shift ;;
    --admin) ADMIN=${2:?}; shift 2 ;;
    --upgrade) UPGRADE=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --password|--user|--no-password) die "Параметр $1 удалён: пароли не передаются в командной строке. Администратор создаётся при установке (скрытый ввод)" ;;
    -h|--help) usage; exit 0 ;;
    *) die "Неизвестный параметр: $1 (см. --help)" ;;
  esac
done

[[ -f $SRC_DIR/app/main.py && -f $SRC_DIR/requirements.lock ]] || die "Запускайте из каталога с исходниками tbo-tracker (нет $SRC_DIR/app/main.py)"
[[ $PORT =~ ^[0-9]+$ && $PORT -ge 1 && $PORT -le 65535 ]] || die "Неверный порт: $PORT"
[[ $ADMIN =~ ^[a-z0-9][a-z0-9._-]{1,63}$ ]] || die "Имя администратора: строчная латиница, цифры, . _ - (2–64 символа)"

ARCH=$(uname -m)
MODEL=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)
say "Устройство: ${MODEL:-$ARCH} · $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
[[ $ARCH == aarch64 ]] || warn "Ожидается 64-битная ОС (aarch64), сейчас $ARCH. Для Raspberry Pi 5 используйте Raspberry Pi OS Lite (64-bit)."

# ---------------------------------------------------------------- системные пакеты
if [[ $SKIP_APT -eq 0 ]]; then
  command -v apt-get >/dev/null || die "Нужен Debian / Raspberry Pi OS (apt-get). Для других систем см. docs/DEPLOY.md → Docker."
  say "Системные пакеты (python3-venv, avahi для адреса <имя>.local, rsync)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || warn "Список пакетов apt не обновлён (нет интернета?) — ставим из уже известных"
  PKGS=(python3 python3-venv python3-pip avahi-daemon rsync curl ca-certificates)
  [[ $HTTPS -eq 1 ]] && PKGS+=(caddy)
  apt-get install -y -qq --no-install-recommends "${PKGS[@]}" >/dev/null \
    || die "Не удалось установить системные пакеты (${PKGS[*]}): нет доступа к репозиториям apt. Подключите Pi к интернету на время установки или установите эти пакеты заранее"
fi
python3 - <<'PY' || die "Нужен Python 3.10 или новее"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

# ---------------------------------------------------------------- пользователь и каталоги
if ! id "$SVC_USER" &>/dev/null; then
  say "Системный пользователь $SVC_USER"
  useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin --user-group "$SVC_USER"
fi
if getent group video >/dev/null; then usermod -aG video "$SVC_USER"; fi      # vcgencmd: напряжение и троттлинг
install -d -m 0755 -o root -g root "$APP_DIR"
install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC_DIR"

# ---------------------------------------------------------------- код и зависимости
say "Копирование приложения в $APP_DIR"
rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'data/' --exclude 'promo/' --exclude 'secrets/' \
      --exclude '.env' --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'tests/' --exclude 'agent_tests/' \
      --exclude 'wheels/' "$SRC_DIR/" "$APP_DIR/"
chown -R root:root "$APP_DIR"

say "Виртуальное окружение Python и зависимости (точные версии из requirements.lock)"
[[ -x $APP_DIR/.venv/bin/python ]] || python3 -m venv "$APP_DIR/.venv"
if compgen -G "$SRC_DIR/wheels/*.whl" >/dev/null; then
  # без интернета: пакеты привёз deploy/field/deploy.sh (скачаны на компьютере, SHA-256 сверены)
  say "Зависимости — из локальных пакетов $SRC_DIR/wheels (без интернета)"
  PIP_SRC=(--no-index --find-links "$SRC_DIR/wheels")
else
  "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
  PIP_SRC=(--retries 5)
fi
"$APP_DIR/.venv/bin/pip" install -q "${PIP_SRC[@]}" --no-deps -r "$APP_DIR/requirements.lock" \
  || die "Не удалось установить зависимости Python. Нет интернета на Pi? Запустите установку через deploy/field/deploy.sh — он привезёт пакеты с компьютера"
"$APP_DIR/.venv/bin/pip" check >/dev/null || die "Конфликт зависимостей (pip check)"
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/app" >/dev/null || true

# ---------------------------------------------------------------- настройки (без паролей)
HOSTN=$(hostname)
# Повторный запуск без параметров режима — обновление: настройки (пределы бортов, TBO_ALLOWED_HOSTS, HTTPS…) сохраняются
if [[ -f $ENV_FILE && $EXPLICIT -eq 0 ]]; then UPGRADE=1; fi
if [[ $UPGRADE -eq 1 && -f $ENV_FILE ]]; then
  say "Настройки сохранены: $ENV_FILE"
  # старые версии: вход по общему паролю из файла настроек заменён учётными записями
  sed -i '/^TBO_AUTH_USER=/d; /^TBO_AUTH_PASSWORD=/d' "$ENV_FILE"
  sed -i 's/^TBO_AUTO_SEED=/TBO_DEMO_MODE=/' "$ENV_FILE"
else
  OLD_ENV=""
  if [[ -f $ENV_FILE ]]; then OLD_ENV="$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"; cp -a "$ENV_FILE" "$OLD_ENV"; fi
  if [[ $HTTPS -eq 1 ]]; then
    BIND=127.0.0.1; PORT=8000; PUBLIC="https://$HOSTN.local"; TRUSTED=127.0.0.1
  else
    BIND=0.0.0.0; PUBLIC=""; TRUSTED=""
  fi
  umask 027
  cat >"$ENV_FILE" <<EOF
# tbo-tracker — настройки сервиса. После изменения: sudo systemctl restart $SVC
# Паролей здесь нет: учётные записи — в базе (хеш Argon2id), управление — sudo tbo-ctl user …
TBO_HOST='$BIND'
TBO_PORT='$PORT'
TBO_DATA_DIR='$DATA_DIR'
TBO_DEMO_MODE='$DEMO'
TBO_PUBLIC_URL='$PUBLIC'
TBO_TRUSTED_PROXIES='$TRUSTED'
TBO_SCHEDULER='1'
TBO_LOG_LEVEL='warning'
EOF
  if [[ -n $OLD_ENV ]]; then     # свои настройки (не управляемые установщиком) переносятся из прежнего файла
    grep -vE '^(TBO_HOST|TBO_PORT|TBO_DATA_DIR|TBO_DEMO_MODE|TBO_PUBLIC_URL|TBO_TRUSTED_PROXIES|TBO_SCHEDULER|TBO_LOG_LEVEL)=|^[[:space:]]*(#|$)' \
      "$OLD_ENV" >>"$ENV_FILE" || true
  fi
  chown root:"$SVC_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
  umask 022
fi
printf 'SRC_DIR=%q\n' "$SRC_DIR" >"$ETC_DIR/install.conf"

# ---------------------------------------------------------------- утилита, база, администратор
install -m 0755 "$SRC_DIR/deploy/raspberry-pi/tbo-ctl" /usr/local/bin/tbo-ctl
install -m 0644 "$SRC_DIR/deploy/raspberry-pi/tbo-tracker.service" "/etc/systemd/system/$SVC.service"

admin_cli() {
  # shellcheck disable=SC2016  # переменные раскрывает внутренний bash
  runuser -u "$SVC_USER" -- bash -c 'umask 077; set -a; . "$0"; set +a; cd /opt/tbo-tracker; exec .venv/bin/python -m app.admin "$@"' "$ENV_FILE" "$@"
}
say "Инициализация базы данных"
admin_cli init >/dev/null

USERS=$(admin_cli users --json 2>/dev/null | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)
CREATED=""
if [[ $USERS -eq 0 && -t 0 ]]; then
  say "Создание администратора «$ADMIN» (пароль не отображается; не короче 10 символов, удобно — фраза из нескольких слов)"
  for _ in 1 2 3; do
    read -rsp "Пароль администратора: " P1; echo
    read -rsp "Ещё раз: " P2; echo
    if [[ "$P1" != "$P2" ]]; then warn "Пароли не совпадают"; continue; fi
    if printf '%s\n' "$P1" | admin_cli create-user "$ADMIN" --role admin --name "Администратор" --password-stdin; then
      CREATED=1; break
    fi
  done
  unset P1 P2
  [[ -n $CREATED ]] || warn "Администратор не создан — создайте его в браузере по коду настройки (sudo tbo-ctl setup-code)"
fi

# ---------------------------------------------------------------- HTTPS (Caddy)
if [[ $HTTPS -eq 1 ]]; then
  command -v caddy >/dev/null || die "Caddy не установлен (apt-get install caddy)"
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  say "HTTPS: Caddy, адреса https://$HOSTN.local${IP:+ и https://$IP}"
  if [[ -f /etc/caddy/Caddyfile ]]; then cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"; fi
  cat >/etc/caddy/Caddyfile <<EOF
# tbo-tracker: HTTPS с сертификатом собственного УЦ Caddy (локальная сеть). Создан install.sh.
https://$HOSTN.local${IP:+, https://$IP} {
	tls internal
	encode zstd gzip
	request_body {
		max_size 600MB
	}
	reverse_proxy 127.0.0.1:8000
	header -Server
}
EOF
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || die "Ошибка в /etc/caddy/Caddyfile"
  systemctl enable caddy >/dev/null 2>&1 || true
  systemctl restart caddy
fi

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

echo
say "tbo-tracker работает"
PUB=$(. "$ENV_FILE"; echo "${TBO_PUBLIC_URL:-}")
if [[ -n $PUB ]]; then
  echo "   Откройте: $PUB  (браузер предупредит о сертификате, пока на устройство не установлен корневой"
  echo "   сертификат Caddy: sudo tbo-ctl https-ca — см. docs/RASPBERRY_PI.md)"
else
  SUFFIX=""; [[ $PORT_NOW == 80 ]] || SUFFIX=":$PORT_NOW"
  echo "   Откройте в браузере на любом устройстве в этой сети:"
  echo "     http://$HOSTN.local$SUFFIX"
  for ip in $(hostname -I 2>/dev/null); do [[ $ip == *:* ]] || echo "     http://$ip$SUFFIX"; done
  warn "Без HTTPS пароли передаются по сети открыто. Для защищённого доступа: sudo ./deploy/raspberry-pi/install.sh --https"
fi
if [[ -n $CREATED ]]; then
  echo "   Вход: $ADMIN и заданный вами пароль. Сотрудников добавьте в разделе «Пользователи»."
elif [[ $USERS -eq 0 ]]; then
  echo "   Первичная настройка в браузере — одноразовый код: sudo tbo-ctl setup-code"
fi
echo "   Управление: sudo tbo-ctl help"
