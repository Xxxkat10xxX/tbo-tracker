#!/usr/bin/env bash
# Установка бортового агента tbo-agent на бортовой компьютер (Orange Pi 5 Pro, Skyris Technic 6S, ROS Noetic).
#
#   sudo ./install.sh            — установка или обновление (спросит адрес сервера и токен устройства)
#   sudo ./install.sh --token    — заменить токен устройства
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
if [[ ! -f "$CONF_DIR/agent.conf" ]]; then
  read -rp "Адрес сервера tbo-tracker (например https://tbo.local или http://192.168.1.10:8000): " URL
  [[ "$URL" =~ ^https?:// ]] || die "адрес должен начинаться с http:// или https://"
  [[ "$URL" == http://* ]] && warn "без HTTPS токен и команды передаются по Wi-Fi открытым текстом"
  sed "s#^url = .*#url = ${URL%/}#" "$SRC/tbo-agent.conf.example" > "$CONF_DIR/agent.conf"
  chmod 644 "$CONF_DIR/agent.conf"
  say "Настройки: $CONF_DIR/agent.conf (пределы полёта и исполнитель — там же)"
else
  say "Настройки сохранены: $CONF_DIR/agent.conf"
fi

if [[ ! -s "$CONF_DIR/token" || "${1:-}" == "--token" ]]; then
  read -rsp "Токен устройства (ввод скрыт): " TOKEN; echo
  [[ "$TOKEN" == tbo_* && ${#TOKEN} -ge 40 ]] || die "это не токен устройства tbo-tracker (начинается с tbo_)"
  ( umask 077; printf '%s\n' "$TOKEN" > "$CONF_DIR/token" )
  unset TOKEN
fi
chown "$RUN_USER" "$CONF_DIR/token"
chmod 600 "$CONF_DIR/token"

install -d -m 700 -o "$RUN_USER" "$STATE_DIR"
install -d -m 700 -o "$RUN_USER" "$STATE_DIR/ros"

say "Проверка связи с сервером"
if ! sudo -u "$RUN_USER" "$PREFIX/run-agent.sh" check; then
  warn "сервер недоступен или токен не принят — служба будет повторять попытки; проверьте адрес, Wi-Fi и токен"
fi

sed "s/^User=.*/User=$RUN_USER/" "$SRC/tbo-agent.service" > /etc/systemd/system/$SVC.service
systemctl daemon-reload
systemctl enable --now $SVC >/dev/null
systemctl restart $SVC
say "Служба $SVC запущена. Журнал: journalctl -u $SVC -f"
