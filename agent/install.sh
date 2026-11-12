#!/usr/bin/env bash
# Установка бортового агента tbo-agent на бортовой компьютер (Orange Pi 5 Pro, Skyris Technic 6S, ROS Noetic).
#
#   sudo ./install.sh            — установка или обновление (спросит адрес сервера и токен устройства)
#   sudo ./install.sh --token    — заменить токен устройства
#   sudo ./install.sh --server URL --token-file ФАЙЛ [--ca-file CA.crt]   — без вопросов (скрипт развёртывания);
#                                файл токена удаляется; CA — корневой сертификат сервера (HTTPS через Caddy «tls internal»)
#
# Токен выпускается в tbo-tracker: «Борты на связи» → «Подключить борт». Вводится скрытно и хранится в
# /etc/tbo-agent/token (права 600, владелец — пользователь службы); в аргументах и истории команд его нет.
set -euo pipefail

PREFIX=/opt/tbo-agent
CONF_DIR=/etc/tbo-agent
STATE_DIR=/var/lib/tbo-agent
SVC=tbo-agent
RUN_USER="${TBO_AGENT_USER:-orangepi}"
SRC="$(cd "$(dirname "$0")" && pwd)"
URL_ARG="" TOKEN_FILE_ARG="" CA_ARG="" NEW_TOKEN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) URL_ARG="${2:-}"; shift 2 ;;
    --token-file) TOKEN_FILE_ARG="${2:-}"; shift 2 ;;
    --ca-file) CA_ARG="${2:-}"; shift 2 ;;
    --token) NEW_TOKEN=1; shift ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите через sudo: sudo $0"
id "$RUN_USER" >/dev/null 2>&1 || die "нет пользователя $RUN_USER (другой пользователь: sudo TBO_AGENT_USER=имя $0)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || die "нужен Python 3.8 или новее"
[[ -f /opt/ros/noetic/setup.bash ]] || warn "ROS Noetic не найден — агент сможет работать только с симулятором (--sim)"
[[ -f "$SRC/tbo_agent/__init__.py" ]] || die "запускайте из каталога agent (рядом должен быть tbo_agent/)"

say "Файлы агента → $PREFIX"
install -d -m 755 "$PREFIX"
rm -rf "$PREFIX/tbo_agent"
cp -r "$SRC/tbo_agent" "$PREFIX/"
find "$PREFIX/tbo_agent" -name '__pycache__' -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$PREFIX/tbo_agent"
install -m 755 "$SRC/run-agent.sh" "$PREFIX/run-agent.sh"

install -d -m 755 "$CONF_DIR"
if [[ ! -f "$CONF_DIR/agent.conf" || -n "$URL_ARG" ]]; then
  URL="$URL_ARG"
  [[ -n "$URL" ]] || read -rp "Адрес сервера tbo-tracker (например https://tbo.local или http://192.168.1.10:8000): " URL
  [[ "$URL" =~ ^https?:// ]] || die "адрес должен начинаться с http:// или https://"
  [[ "$URL" == http://* ]] && warn "без HTTPS токен и команды передаются по Wi-Fi открытым текстом"
  if [[ -f "$CONF_DIR/agent.conf" ]]; then
    sed -i "s#^url = .*#url = ${URL%/}#" "$CONF_DIR/agent.conf"
  else
    sed "s#^url = .*#url = ${URL%/}#" "$SRC/tbo-agent.conf.example" > "$CONF_DIR/agent.conf"
  fi
  chmod 644 "$CONF_DIR/agent.conf"
  say "Настройки: $CONF_DIR/agent.conf (пределы полёта и исполнитель — там же)"
else
  say "Настройки сохранены: $CONF_DIR/agent.conf"
fi

if [[ -n "$TOKEN_FILE_ARG" ]]; then
  [[ -f "$TOKEN_FILE_ARG" ]] || die "нет файла токена $TOKEN_FILE_ARG"
  TOKEN="$(head -c 200 "$TOKEN_FILE_ARG" | tr -d '\r\n ')"
  rm -f "$TOKEN_FILE_ARG"
  [[ "$TOKEN" == tbo_* && ${#TOKEN} -ge 40 ]] || die "в файле не токен устройства tbo-tracker"
  ( umask 077; printf '%s\n' "$TOKEN" > "$CONF_DIR/token" )
  unset TOKEN
elif [[ ! -s "$CONF_DIR/token" || $NEW_TOKEN -eq 1 ]]; then
  read -rsp "Токен устройства (ввод скрыт): " TOKEN; echo
  [[ "$TOKEN" == tbo_* && ${#TOKEN} -ge 40 ]] || die "это не токен устройства tbo-tracker (начинается с tbo_)"
  ( umask 077; printf '%s\n' "$TOKEN" > "$CONF_DIR/token" )
  unset TOKEN
fi
chown "$RUN_USER" "$CONF_DIR/token"
chmod 600 "$CONF_DIR/token"

if [[ -n "$CA_ARG" ]]; then
  [[ -f "$CA_ARG" ]] && grep -q "BEGIN CERTIFICATE" "$CA_ARG" || die "$CA_ARG — не сертификат (PEM)"
  install -m 644 "$CA_ARG" "$CONF_DIR/ca.crt"
  sed -i "s#^ca_file =.*#ca_file = $CONF_DIR/ca.crt#" "$CONF_DIR/agent.conf"
  say "Корневой сертификат сервера: $CONF_DIR/ca.crt"
fi

install -d -m 700 -o "$RUN_USER" "$STATE_DIR"
install -d -m 700 -o "$RUN_USER" "$STATE_DIR/ros"

# Системные пакеты, без которых борт работает с перебоями (найдено на лётных испытаниях):
#   libnss-mdns — без него борт не находит сервер по имени *.local (http://rpi.local);
#   ros-noetic-compressed-image-transport — без него узлы сжатия видео образа Technic перезапускаются ~15 раз/с,
#     Orange Pi перегружен, телеметрия опаздывает на 1–6 с, и дрон садится посреди маршрута.
ensure_pkgs() {
  local missing=() p log=/tmp/tbo-agent-apt.log
  for p in "$@"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
  done
  [[ ${#missing[@]} -eq 0 ]] && { say "Системные пакеты на месте: $*"; return 0; }
  say "Системные пакеты: ${missing[*]}"
  if timeout 300 apt-get install -y --no-install-recommends "${missing[@]}" >"$log" 2>&1 \
     || { timeout 300 apt-get update >>"$log" 2>&1 && timeout 300 apt-get install -y --no-install-recommends "${missing[@]}" >>"$log" 2>&1; }; then
    say "Установлены: ${missing[*]}"
  else
    warn "не удалось установить ${missing[*]} (нет интернета?) — позже: sudo apt-get install ${missing[*]} (журнал: $log)"
  fi
}
PKGS=(libnss-mdns)
[[ -f /opt/ros/noetic/setup.bash ]] && PKGS+=(ros-noetic-compressed-image-transport)
if command -v dpkg-query >/dev/null && command -v apt-get >/dev/null; then
  ensure_pkgs "${PKGS[@]}"
fi

say "Проверка связи с сервером"
if ! sudo -u "$RUN_USER" "$PREFIX/run-agent.sh" check; then
  warn "сервер недоступен или токен не принят — служба будет повторять попытки; проверьте адрес, Wi-Fi и токен"
fi

sed "s/^User=.*/User=$RUN_USER/" "$SRC/tbo-agent.service" > /etc/systemd/system/$SVC.service
systemctl daemon-reload
systemctl enable --now $SVC >/dev/null
systemctl restart $SVC
# корень на борту смонтирован с commit=600: без sync файлы последних минут теряются при отключении питания
sync
say "Служба $SVC запущена. Журнал: journalctl -u $SVC -f"
