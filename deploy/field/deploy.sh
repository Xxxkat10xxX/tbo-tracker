#!/usr/bin/env bash
# Развёртывание tbo-tracker на двух платах с вашего компьютера (Linux, macOS или Windows WSL) в той же сети:
#   сервер — Raspberry Pi (Raspberry Pi OS 64-bit): сайт, база, приём журналов;
#   борт   — Orange Pi 5 Pro дрона Skyris Technic 6S (образ Technic: ROS Noetic, MAVROS, служба technic): агент tbo-agent.
#
#   ./deploy/field/deploy.sh --server user@192.168.50.10 --drone orangepi@10.255.37.62
#
# Параметры:
#   --serial СЕРИЙНЫЙ   номер борта в tbo-tracker (по умолчанию — имя хоста Orange Pi, например technic-2306)
#   --name ИМЯ          название борта (по умолчанию «Technic 6S»)
#   --https             сервер с HTTPS (Caddy, собственный УЦ); борт обращается к https://<адрес сервера> с этим сертификатом
#   --port N            порт сайта без HTTPS (по умолчанию 80)
#   --demo              сервер с демо-данными и сценариями проверки
#   --url URL           адрес сервера, видимый с борта (по умолчанию http://<адрес из --server>)
#   --server-only | --drone-only   только одна из частей (повторная установка агента — --drone-only)
#
# Пароли вводятся только в запросах ssh и sudo: скрипт их не видит, не хранит и не передаёт аргументами.
# Токен устройства идёт с сервера на борт через этот компьютер — в памяти и во временных файлах с правами 600,
# которые сразу удаляются; на экран он не выводится. Подробно: docs/DRONE.md, раздел «Развёртывание».
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER="" DRONE="" SERIAL="" NAME="Technic 6S" HTTPS=0 DEMO=0 URL="" PART="all" PORT=""
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) WIN=1 ;; *) WIN=0 ;; esac   # Git Bash на Windows
die()  {
  printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2
  # на Windows окно Git Bash / git-cmd закрывается сразу — даём прочитать сообщение
  if [[ $WIN -eq 1 && -t 0 ]]; then read -rp "Нажмите Enter для выхода " _ || true; fi
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="${2:-}"; shift 2 ;;
    --drone) DRONE="${2:-}"; shift 2 ;;
    --serial) SERIAL="${2:-}"; shift 2 ;;
    --name) NAME="${2:-}"; shift 2 ;;
    --https) HTTPS=1; shift ;;
    --demo) DEMO=1; shift ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --url) URL="${2:-}"; shift 2 ;;
    --server-only) PART="server"; shift ;;
    --drone-only) PART="drone"; shift ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "неизвестный параметр $1 (см. --help)" ;;
  esac
done

[[ -n "$SERVER" ]] || die "укажите сервер: --server user@192.168.50.10"
[[ "$PART" == server || -n "$DRONE" ]] || die "укажите борт: --drone orangepi@10.255.37.62"
[[ -f "$ROOT/app/main.py" && -f "$ROOT/agent/tbo_agent/__init__.py" ]] || die "запускайте из репозитория tbo-tracker"
for cmd in ssh tar; do command -v "$cmd" >/dev/null || die "нужна программа $cmd"; done
# клон на Windows с core.autocrlf=true: скрипты с CRLF на Linux-платах не запускаются
if grep -lq $'\r' "$ROOT/deploy/raspberry-pi/install.sh" "$ROOT/deploy/raspberry-pi/tbo-ctl" "$ROOT/agent/install.sh" \
    "$ROOT/agent/run-agent.sh" 2>/dev/null; then
  die "скрипты в репозитории с переводами строк Windows (CRLF). Выполните: git config core.autocrlf false && git rm -rq --cached . && git reset -q --hard"
fi
# имя попадает в команду на сервере в одинарных кавычках: без кавычек и управляющих символов (кириллица — можно)
[[ -n "$NAME" && ${#NAME} -le 200 && "$NAME" != *[\'\"\\\`\$]* && "$NAME" != *[[:cntrl:]]* ]] \
  || die "название борта: до 200 символов, без кавычек, \\, \`, \$ и управляющих символов"
[[ -z "$SERIAL" || "$SERIAL" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "серийный номер: латиница, цифры, . _ -"
[[ -z "$URL" || "$URL" =~ ^https?://[A-Za-z0-9.:-]+(/)?$ ]] || die "адрес сервера: http://АДРЕС[:ПОРТ] или https://ИМЯ"
[[ -z "$PORT" || ( "$PORT" =~ ^[0-9]+$ && $PORT -ge 1 && $PORT -le 65535 ) ]] || die "порт: 1–65535"
[[ -z "$PORT" || $HTTPS -eq 0 ]] || die "--port только без --https (с HTTPS сайт на порту 443)"
HOST="${SERVER#*@}"
URL="${URL%/}"
if [[ -z "$URL" ]]; then           # адрес сервера для борта: по умолчанию тот же, что для SSH
  if [[ $HTTPS -eq 1 ]]; then URL="https://$HOST"; else URL="http://$HOST${PORT:+:$PORT}"; fi   # сертификат Caddy выпущен и на IP
fi
[[ "$URL" =~ ^https?://[A-Za-z0-9.:-]+$ ]] || die "адрес сервера $URL: ожидается http://АДРЕС[:ПОРТ] (укажите --url)"

export COPYFILE_DISABLE=1                          # macOS: без служебных файлов ._* в архивах
# одно SSH-соединение на плату: пароль вводится один раз за запуск. Каталог в /tmp, а не в $TMPDIR: путь сокета
# ограничен ~104 байтами, а $TMPDIR на macOS длинный
CTL="$(mktemp -d /tmp/tbo-deploy.XXXXXX)"
# ssh на Windows (Git для Windows, OpenSSH Windows) не поддерживает общее соединение (ControlMaster): без него пароль
# спрашивается при каждом обращении к плате — лучше заранее настроить вход по ключу (docs/DRONE.md, раздел 1а)
MUX="${TBO_SSH_MUX:-$((1 - WIN))}"
SSHO=(-o ServerAliveInterval=10 -o ConnectTimeout=15)
if [[ $MUX -eq 1 ]]; then
  SSHO+=(-o ControlMaster=auto -o "ControlPath=$CTL/%C" -o ControlPersist=900)
else
  warn "общее SSH-соединение отключено: пароль платы будет спрашиваться несколько раз (лучше — вход по ключу)"
fi
SRV_TMP=0 DRN_TMP=0          # 1 — на сервере / борте могут лежать временные файлы с токеном или сертификатом
cleanup() {                  # при любом выходе, в том числе по ошибке и Ctrl+C
  if [[ $SRV_TMP -eq 1 ]]; then on "$SERVER" 'rm -f ~/.tbo-drone-token.tmp ~/.tbo-ca.crt' >/dev/null 2>&1 || true; fi
  if [[ $DRN_TMP -eq 1 ]]; then on "$DRONE" 'rm -f ~/.tbo-token ~/.tbo-ca.crt' >/dev/null 2>&1 || true; fi
  if [[ $MUX -eq 1 ]]; then
    for h in "$SERVER" "$DRONE"; do [[ -n "$h" ]] && ssh "${SSHO[@]}" -O exit "$h" >/dev/null 2>&1 || true; done
  fi
  rm -f "$CTL/ca.crt" "$CTL"/wheels/*.whl
  rmdir "$CTL/wheels" "$CTL" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
on() { local host="$1"; shift; ssh "${SSHO[@]}" "$host" "$@"; }
tty_on() { local host="$1"; shift; ssh -t "${SSHO[@]}" "$host" "$@"; }

TOKEN=""
# корневой сертификат Caddy (сайт с HTTPS) — борту, чтобы проверять сервер
ca=":"
[[ $HTTPS -eq 1 ]] && ca="sudo cat /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt > ~/.tbo-ca.crt"
take_token() {      # токен — из временного файла на сервере в память; файл сразу удаляется
  TOKEN="$(on "$SERVER" 'cat ~/.tbo-drone-token.tmp 2>/dev/null; rm -f ~/.tbo-drone-token.tmp' | tr -d '\r\n ')"
  [[ "$TOKEN" == tbo_* && ${#TOKEN} -ge 40 ]] || die "сервер не выдал токен устройства (борт подключён? sudo tbo-ctl drone list)"
}
take_ca() {
  on "$SERVER" 'cat ~/.tbo-ca.crt 2>/dev/null; rm -f ~/.tbo-ca.crt' > "$CTL/ca.crt"
  grep -q "BEGIN CERTIFICATE" "$CTL/ca.crt" || die "не удалось получить корневой сертификат Caddy с сервера"
}

if [[ "$PART" != drone ]]; then
  say "Сервер $SERVER: подключение (пароль пользователя сервера)"
  on "$SERVER" 'uname -m; . /etc/os-release && echo "$PRETTY_NAME"' || die "нет SSH-доступа к $SERVER. Если обычный ssh $SERVER работает — запустите с TBO_SSH_MUX=0 (без общего соединения)"
  if [[ -z "$SERIAL" && -n "$DRONE" ]]; then
    say "Борт $DRONE: подключение (пароль пользователя orangepi)"
    SERIAL="$(on "$DRONE" hostname | tr -cd 'A-Za-z0-9._-')"
  fi
  [[ -n "$SERIAL" || "$PART" == server ]] || die "не удалось определить серийный номер — укажите --serial"

  say "Сервер: копирование исходников в ~/tbo-tracker"
  tar czf - -C "$ROOT" --exclude=./.git --exclude=./data --exclude=./.venv --exclude=./promo --exclude=./secrets \
      --exclude=./tests --exclude=./agent/agent_tests --exclude='*/__pycache__' --exclude=./.env . \
    | on "$SERVER" 'mkdir -p ~/tbo-tracker && tar xzf - -C ~/tbo-tracker && rm -f ~/tbo-tracker/wheels/*.whl'

  # Сервер без интернета (часто в полевой сети): пакеты Python скачиваются здесь, сверяются по SHA-256 и везутся на Pi
  # TBO_OFFLINE=1 — везти пакеты в любом случае
  if [[ "${TBO_OFFLINE:-0}" == 1 ]] || ! on "$SERVER" 'python3 -c "import socket; socket.create_connection((\"pypi.org\", 443), 6).close()"' >/dev/null 2>&1; then
    pyv="$(on "$SERVER" 'python3 -c "import sys, platform; print(\"cp%d%d-%s\" % (sys.version_info[0], sys.version_info[1], platform.machine()))"' | tr -d '\r')"
    list="$ROOT/deploy/raspberry-pi/offline/wheels-$pyv.txt"
    [[ -f "$list" ]] || die "у сервера нет интернета, а готового набора пакетов для $pyv нет — подключите сервер к интернету на время установки"
    command -v curl >/dev/null && command -v sha256sum >/dev/null || die "нужны curl и sha256sum (есть в Git Bash и Linux)"
    say "У сервера нет интернета: скачиваю пакеты Python ($pyv) на этот компьютер и везу на сервер"
    mkdir -p "$CTL/wheels"
    while read -r sum file url; do
      [[ -z "$sum" || "$sum" == \#* ]] && continue
      [[ "$file" =~ ^[A-Za-z0-9._+-]+\.whl$ && "$url" == https://files.pythonhosted.org/* ]] || die "неверная строка в $list: $file"
      curl -fsSL --retry 3 -o "$CTL/wheels/$file" "$url" || die "не удалось скачать $file (есть ли интернет на этом компьютере?)"
      printf '%s  %s\n' "$sum" "$CTL/wheels/$file" | sha256sum -c --quiet - || die "контрольная сумма $file не совпала"
    done < <(tr -d '\r' < "$list")
    tar czf - -C "$CTL" wheels | on "$SERVER" 'tar xzf - -C ~/tbo-tracker'
    rm -f "$CTL"/wheels/*.whl
  fi

  flags=""; [[ $HTTPS -eq 1 ]] && flags+=" --https"; [[ $DEMO -eq 1 ]] && flags+=" --demo"; [[ -n "$PORT" ]] && flags+=" --port $PORT"
  register=":"
  if [[ -n "$SERIAL" ]]; then          # подключение борта и токен — в той же сессии sudo; токен в файл 600, не на экран
    register="(umask 077; { sudo tbo-ctl drone add '$SERIAL' '$NAME' --token-only 2>/dev/null \
|| sudo tbo-ctl drone token '$SERIAL' --token-only; } > ~/.tbo-drone-token.tmp)"
  fi
  say "Сервер: установка (запросит пароль sudo; при первой установке — создание администратора, ввод скрыт)"
  # umask только для файла токена: установщик под umask 077 создал бы окружение Python, недоступное службе
  SRV_TMP=1
  tty_on "$SERVER" "cd ~/tbo-tracker && sudo ./deploy/raspberry-pi/install.sh$flags && $register && $ca" \
    || die "установка сервера не завершилась — причина в сообщениях выше"
  if [[ -n "$SERIAL" ]]; then
    take_token
    say "Сервер: борт $SERIAL подключён, токен получен (на экран не выводится)"
  fi
  if [[ $HTTPS -eq 1 ]]; then take_ca; fi
  SRV_TMP=0
fi

[[ "$PART" == server ]] && { say "Готово: сервер установлен. Откройте $URL"; exit 0; }
if [[ "$PART" == drone ]]; then
  say "Борт $DRONE: подключение (пароль пользователя orangepi)"
  on "$DRONE" true || die "нет SSH-доступа к $DRONE"
fi
[[ -n "$SERIAL" ]] || SERIAL="$(on "$DRONE" hostname | tr -cd 'A-Za-z0-9._-')"
[[ -n "$SERIAL" ]] || die "не удалось определить серийный номер — укажите --serial"

say "Борт $DRONE: проверка образа Technic"
on "$DRONE" 'test -f /opt/ros/noetic/setup.bash && echo "ROS Noetic: есть" || echo "ROS Noetic: НЕТ";
  test -f ~/technic_ws/devel/setup.bash && echo "technic_ws: есть" || echo "technic_ws: НЕТ";
  echo "служба technic: $(systemctl is-active technic 2>/dev/null)"; echo "technic-show: $(systemctl is-active technic-show 2>/dev/null)";
  python3 -c "import sys; print(\"Python\", sys.version.split()[0])"'

CA_REMOTE=""
put_ca() { DRN_TMP=1; on "$DRONE" 'umask 022; cat > ~/.tbo-ca.crt' < "$CTL/ca.crt"; CA_REMOTE="--ca-file ~/.tbo-ca.crt"; }
# GET /health с борта. С сертификатом сервера — полная проверка; без него (--drone-only с HTTPS: сертификат ещё не
# получен) — только доступность, без секретов в запросе; сертификат и токен затем проверяет установщик агента
# shellcheck disable=SC2016  # выражение раскрывается на борту
probe='import os, ssl, sys, urllib.request
ca, u = os.path.expanduser("~/.tbo-ca.crt"), sys.argv[1]
ctx = ssl.create_default_context(cafile=ca) if os.path.exists(ca) else (ssl._create_unverified_context() if u.startswith("https:") else None)
try:
    urllib.request.urlopen(u + "/health", timeout=8, context=ctx)
except Exception as e:
    sys.exit("нет ответа: %s" % e)
print("сервер отвечает")'
if [[ -f "$CTL/ca.crt" ]]; then put_ca; fi
say "Борт: связь с сервером $URL"
if ! on "$DRONE" "python3 -c '$probe' '$URL'"; then
  die "борт не видит сервер $URL. Борт и сервер должны быть в одной сети (общий роутер — docs.skyris.ru → «Подключение к Wi-Fi сети») или маршрутизироваться; проверьте с борта: python3 -c \"import urllib.request; urllib.request.urlopen('$URL/health')\" — и запустите снова с --drone-only"
fi

if [[ -z "$TOKEN" ]]; then        # --drone-only: новый токен — после проверки связи, чтобы прежний не пропал зря
  say "Сервер: новый токен для борта $SERIAL (пароль sudo на сервере)"
  SRV_TMP=1
  tty_on "$SERVER" "(umask 077; sudo tbo-ctl drone token '$SERIAL' --token-only > ~/.tbo-drone-token.tmp) && $ca" \
    || die "сервер не выдал токен (борт подключён? sudo tbo-ctl drone list)"
  take_token
  if [[ $HTTPS -eq 1 ]]; then take_ca; put_ca; fi
  SRV_TMP=0
fi

say "Борт: копирование агента в ~/tbo-agent"
tar czf - --exclude='__pycache__' -C "$ROOT/agent" tbo_agent install.sh run-agent.sh tbo-agent.service tbo-agent.conf.example README.md \
  | on "$DRONE" 'mkdir -p ~/tbo-agent && tar xzf - -C ~/tbo-agent'
DRN_TMP=1
printf '%s\n' "$TOKEN" | on "$DRONE" 'umask 077; cat > ~/.tbo-token'
TOKEN=""
say "Борт: установка агента (запросит пароль sudo на борту)"
# временные файлы удаляются при любом исходе; код возврата — от установщика
tty_on "$DRONE" "cd ~/tbo-agent && sudo ./install.sh --server '$URL' --token-file ~/.tbo-token $CA_REMOTE; rc=\$?; rm -f ~/.tbo-token ~/.tbo-ca.crt; exit \$rc" \
  || die "установка агента на борту не завершилась — причина в сообщениях выше (повторить: --drone-only)"
DRN_TMP=0

say "Готово. Откройте $URL → «Борты на связи»: борт $SERIAL должен быть «На связи»."
warn "Смените пароли по умолчанию: на борту (passwd для orangepi — заводской пароль известен всем) и на сервере;"
warn "лучше входить по SSH-ключам (docs.skyris.ru → «Подключение к Orange Pi с использованием SSH-ключей»)."
