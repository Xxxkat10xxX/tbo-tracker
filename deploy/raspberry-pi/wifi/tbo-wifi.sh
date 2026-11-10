#!/bin/bash
# tbo-wifi: точка доступа сервера (профиль tbo-ap) рядом с клиентом раздачи телефона (профиль phone-hotspot).
#
# * Телефон подключён → вторая виртуальная сеть uap0 на том же радио, точка доступа на канале телефона
#   (одно радио работает только на одном канале — так же устроен дрон: wlan0 + wlan0-sta).
# * Телефона нет дольше WAIT_PHONE секунд → точка доступа на самом wlan0 (резервный режим), чтобы к серверу
#   можно было подключиться без провода. Раз в RETRY секунд, если к точке никто не подключён, — снова попытка телефона.
# eth0 не трогается. Журнал: journalctl -u tbo-wifi
set -u

STA=wlan0
AP=uap0
APCON=tbo-ap
PHONE=phone-hotspot
WAIT_PHONE=${WAIT_PHONE:-90}
RETRY=${RETRY:-300}
PERIOD=${PERIOD:-15}

# Журнал и снимок состояния дублируются на загрузочный раздел, чтобы их можно было прочитать с карты на ПК
# (в них нет паролей: nmcli без --show-secrets). RUN_LOG ограничен ~200 КБ.
BOOTLOG=${TBO_BOOTLOG:-/boot/firmware/tbo-wifi}
RUN_LOG=$BOOTLOG/run.log
DIAG=$BOOTLOG/diag.txt
log() {
    echo "$*"
    if [ -d "$BOOTLOG" ]; then
        [ -f "$RUN_LOG" ] && [ "$(stat -c %s "$RUN_LOG" 2>/dev/null || echo 0)" -gt 200000 ] && mv -f "$RUN_LOG" "$RUN_LOG.1"
        echo "$(date '+%F %T') $*" >>"$RUN_LOG"
    fi
}
diag() {   # diag <причина>
    [ -d "$BOOTLOG" ] || return 0
    {
        echo "=== $(date '+%F %T') $1"
        echo "--- nmcli dev"; nmcli dev 2>&1
        echo "--- активные подключения"; nmcli -t -f NAME,TYPE,DEVICE,STATE con show --active 2>&1
        echo "--- профили"; nmcli -t -f NAME,TYPE,AUTOCONNECT,FILENAME con show 2>&1
        echo "--- tbo-ap"; nmcli -f connection.interface-name,802-11-wireless.mode,802-11-wireless.band,802-11-wireless.channel,ipv4.method,ipv4.addresses con show "$APCON" 2>&1
        echo "--- iw dev"; iw dev 2>&1
        echo "--- iw link"; iw dev "$STA" link 2>&1
        echo "--- комбинации интерфейсов"; iw list 2>&1 | sed -n '/valid interface combinations/,/^[[:space:]]*$/p' | head -12
        echo "--- ip"; ip -br addr 2>&1; ip route 2>&1
        echo "--- rfkill"; rfkill list 2>&1
        echo "--- видимые сети"; nmcli -f SSID,CHAN,SIGNAL,SECURITY dev wifi list ifname "$STA" 2>&1 | head -25
        echo "--- NetworkManager"; journalctl -u NetworkManager -n 80 --no-pager -o short-iso 2>&1
        echo "--- tbo-wifi"; journalctl -u tbo-wifi -n 40 --no-pager -o short-iso 2>&1
    } >"$DIAG.tmp" 2>&1
    mv -f "$DIAG.tmp" "$DIAG"
}
have_con() { nmcli -t -f NAME con show 2>/dev/null | grep -qxF "$1"; }
dev_con() { nmcli -g GENERAL.CONNECTION dev show "$1" 2>/dev/null; }
ap_dev() { nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | awk -F: -v c="$APCON" '$1==c{print $2}'; }
sta_freq() { iw dev "$STA" link 2>/dev/null | awk '/freq:/{printf "%d", $2}'; }
chan() {
    local f=$1
    if [ "$f" -eq 2484 ]; then echo 14
    elif [ "$f" -lt 3000 ]; then echo $(((f - 2407) / 5))
    else echo $(((f - 5000) / 5)); fi
}
has_clients() { ip neigh show dev "$1" 2>/dev/null | grep -qE 'REACHABLE|STALE|DELAY|PROBE'; }

ap_on() {   # ap_on <интерфейс> <band> <канал>; ошибки nmcli — в журнал
    local out
    if ! out=$(nmcli con mod "$APCON" connection.interface-name "$1" 802-11-wireless.band "$2" 802-11-wireless.channel "$3" 2>&1); then
        log "nmcli con mod: $out"
        return 1
    fi
    if ! out=$(nmcli -w 40 con up "$APCON" ifname "$1" 2>&1); then
        log "nmcli con up $APCON ifname $1: $out"
        return 1
    fi
}

last_diag=0
diag_fail() {   # снимок при сбое — не чаще раза в минуту
    local t; t=$(date +%s)
    if [ $((t - last_diag)) -ge 60 ]; then last_diag=$t; diag "сбой: $1"; fi
}

drop_uap() {
    [ "$(ap_dev)" = "$AP" ] && nmcli con down "$APCON" >/dev/null 2>&1
    ip link show "$AP" >/dev/null 2>&1 && iw dev "$AP" del 2>/dev/null
}

if ! have_con "$APCON"; then
    log "нет профиля $APCON — точка доступа не настроена, служба завершена"
    exit 0
fi
HAVE_IW=0
command -v iw >/dev/null 2>&1 && HAVE_IW=1
[ "$HAVE_IW" = 1 ] || log "нет утилиты iw: точка доступа рядом с телефоном невозможна, только резервный режим (sudo apt install iw)"
have_con "$PHONE" || log "нет профиля $PHONE — работает только точка доступа"

nm-online -s -q -t 60 || true
log "служба запущена (iw: $HAVE_IW)"
diag "запуск службы"
started=$(date +%s)
snap60=0 snap180=0
last_phone=$(date +%s)
fallback_since=0
cur=""
fails=0
MAX_FAILS=${MAX_FAILS:-8}      # ≈ 2 мин неудачных попыток рядом с телефоном → резервный режим

while true; do
    now=$(date +%s)
    if [ "$snap60" = 0 ] && [ $((now - started)) -ge 60 ]; then snap60=1; diag "через 1 мин после запуска"; fi
    if [ "$snap180" = 0 ] && [ $((now - started)) -ge 180 ]; then snap180=1; diag "через 3 мин после запуска"; fi
    if [ "$(dev_con "$STA")" = "$PHONE" ]; then
        last_phone=$now
        fallback_since=0
        if [ "$HAVE_IW" = 1 ]; then
            f=$(sta_freq)
            if [ -n "$f" ]; then
                ch=$(chan "$f")
                band=bg
                [ "$f" -ge 3000 ] && band=a
                if [ "$cur" != "$band:$ch" ] || [ "$(ap_dev)" != "$AP" ]; then
                    if ! ip link show "$AP" >/dev/null 2>&1; then
                        out=$(iw dev "$STA" interface add "$AP" type __ap 2>&1) || log "не удалось создать $AP: $out"
                        sleep 3
                    fi
                    out=$(nmcli dev set "$AP" managed yes 2>&1) || log "nmcli dev set $AP managed: $out"
                    if ap_on "$AP" "$band" "$ch"; then
                        cur="$band:$ch"
                        fails=0
                        log "точка доступа на $AP, канал $ch ($band) — как у раздачи телефона"
                        diag "точка доступа поднята рядом с телефоном"
                    else
                        cur=""
                        fails=$((fails + 1))
                        log "не удалось поднять точку доступа рядом с клиентом телефона (попытка $fails) — повтор через $PERIOD с"
                        diag_fail "точка доступа рядом с телефоном"
                        if [ "$fails" -ge "$MAX_FAILS" ]; then
                            # иначе к серверу без провода не подключиться: на время — точка доступа вместо клиента
                            log "точка доступа рядом с телефоном не поднимается — резервный режим на $RETRY с"
                            fails=0
                            drop_uap
                            nmcli con down "$PHONE" >/dev/null 2>&1
                            if ap_on "$STA" bg 6; then
                                fallback_since=$now
                                log "точка доступа на $STA, канал 6 (резервный режим)"
                                diag "резервная точка доступа поднята (не получилось рядом с телефоном)"
                            else
                                log "не удалось поднять точку доступа и на $STA"
                                diag_fail "резервная точка доступа"
                                nmcli con up "$PHONE" >/dev/null 2>&1
                            fi
                        fi
                    fi
                fi
            fi
        fi
    elif [ "$(ap_dev)" = "$STA" ]; then
        # резервный режим: точка доступа вместо клиента телефона
        if [ $((now - fallback_since)) -ge "$RETRY" ] && have_con "$PHONE" && ! has_clients "$STA"; then
            log "резервный режим: клиентов нет — пробую раздачу телефона"
            nmcli con down "$APCON" >/dev/null 2>&1
            nmcli con up "$PHONE" >/dev/null 2>&1
            if [ "$(dev_con "$STA")" != "$PHONE" ]; then
                log "телефон не найден — снова точка доступа на $STA"
                ap_on "$STA" bg 6 || log "не удалось поднять точку доступа на $STA"
                fallback_since=$now
            fi
            cur=""
        fi
    elif [ $((now - last_phone)) -ge "$WAIT_PHONE" ]; then
        if [ "$(ap_dev)" = "$AP" ] && has_clients "$AP"; then
            :   # телефона нет, но к точке рядом подключены — не мешать
        else
            log "раздачи телефона нет $((now - last_phone)) с — точка доступа на $STA вместо клиента"
            drop_uap
            cur=""
            if ap_on "$STA" bg 6; then
                fallback_since=$now
                log "точка доступа на $STA, канал 6 (резервный режим)"
                diag "резервная точка доступа поднята"
            else
                log "не удалось поднять точку доступа на $STA"
                diag_fail "резервная точка доступа"
            fi
        fi
    fi
    sleep "$PERIOD"
done
