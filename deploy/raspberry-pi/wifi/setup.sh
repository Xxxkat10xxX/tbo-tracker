#!/bin/bash
# Однократная настройка Wi-Fi сервера при загрузке (запускается из cmdline.txt через systemd.run,
# NetworkManager в этот момент не работает — профили пишутся файлами).
#
# Что делает:
#   * профиль «phone-hotspot» — клиент раздачи телефона на wlan0 (если задан PHONE_PSK);
#   * профиль «tbo-ap» — собственная точка доступа сервера (10.43.0.1/24, DHCP от NetworkManager);
#   * служба tbo-wifi — поднимает точку доступа рядом с клиентом телефона (как на дроне: wlan0 + вторая
#     виртуальная сеть на том же радио) или вместо него, если телефона нет;
#   * удаляет файл с паролями с загрузочного раздела и убирает себя из cmdline.txt.
# eth0 (eth0-static) не трогается.
#
# Пароли берутся из /boot/firmware/tbo-wifi.txt, который пользователь создаёт сам:
#   PHONE_PSK=пароль раздачи телефона
#   AP_PSK=пароль точки доступа сервера (не короче 8 символов)
# В журнал пароли не пишутся. Без файла профили не меняются (повторный запуск обновляет только службу).
#
# Как подключить (карта Pi в компьютере, загрузочный раздел FAT):
#   1. скопировать setup.sh, tbo-wifi.sh, tbo-wifi.service в папку tbo-wifi/ на загрузочном разделе;
#   2. создать tbo-wifi.txt с паролями (см. выше) и безопасно извлечь карту;
#   3. в конец единственной строки cmdline.txt дописать через пробел:
#      systemd.run=/boot/firmware/tbo-wifi/setup.sh systemd.run_success_action=reboot
#      systemd.run_failure_action=reboot systemd.unit=kernel-command-line.target
# Pi загрузится дважды. Журнал настройки — tbo-wifi/setup.log, работы службы — tbo-wifi/run.log и diag.txt
# на том же разделе (читаются с карты на ПК). SSID раздачи телефона — PHONE_SSID ниже.

ROOT=${TBO_ROOT:-}            # для проверки на стенде; на плате пусто
B=$ROOT/boot/firmware
D=$B/tbo-wifi
SEC=$B/tbo-wifi.txt
LOG=$D/setup.log
NMDIR=$ROOT/etc/NetworkManager/system-connections

PHONE_SSID='Nothing Phone(3)_4196'
AP_SSID='tbo-server-ap'
AP_ADDR='10.43.0.1/24'

cleanup() {
    # cmdline.txt — одна строка: убрать только наши параметры, чтобы следующая загрузка была обычной
    if [ -f "$B/cmdline.txt" ]; then
        sed -i -e 's| systemd\.run=[^ ]*||g' -e 's| systemd\.run_success_action=[^ ]*||g' \
               -e 's| systemd\.run_failure_action=[^ ]*||g' -e 's| systemd\.unit=kernel-command-line\.target||g' \
               "$B/cmdline.txt"
    fi
    if [ -f "$SEC" ]; then                      # затереть и удалить файл с паролями
        dd if=/dev/zero of="$SEC" bs=1 count="$(stat -c %s "$SEC")" conv=notrunc 2>/dev/null
        rm -f "$SEC"
    fi
    sync
}
trap cleanup EXIT

exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') tbo-wifi setup"

# --- пароли из файла пользователя (Блокнот: CRLF и BOM допускаются)
PHONE_PSK="" AP_PSK=""
if [ -f "$SEC" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        line=${line#$'\xef\xbb\xbf'}
        case "$line" in
            PHONE_PSK=*) PHONE_PSK=${line#PHONE_PSK=} ;;
            AP_PSK=*)    AP_PSK=${line#AP_PSK=} ;;
        esac
    done <"$SEC"
else
    echo "нет файла tbo-wifi.txt — профили Wi-Fi не меняются (остаются созданные ранее)"
fi

# в keyfile NetworkManager обратная косая черта экранируется
kf_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g'; }

umask 077
mkdir -p "$NMDIR"

if [ -n "$PHONE_PSK" ]; then
    if [ ${#PHONE_PSK} -lt 8 ]; then
        echo "PHONE_PSK короче 8 символов — профиль телефона не создан"
    else
        cat >"$NMDIR/phone-hotspot.nmconnection" <<EOF
[connection]
id=phone-hotspot
type=wifi
interface-name=wlan0
autoconnect=true
autoconnect-priority=10
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=$PHONE_SSID
powersave=2

[wifi-security]
key-mgmt=wpa-psk
psk=$(kf_escape "$PHONE_PSK")

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default
EOF
        chmod 600 "$NMDIR/phone-hotspot.nmconnection"
        echo "создан профиль phone-hotspot (SSID $PHONE_SSID)"
    fi
fi

if [ ${#AP_PSK} -ge 8 ]; then
    cat >"$NMDIR/tbo-ap.nmconnection" <<EOF
[connection]
id=tbo-ap
type=wifi
interface-name=uap0
autoconnect=false

[wifi]
mode=ap
ssid=$AP_SSID
band=bg
channel=6
powersave=2

[wifi-security]
key-mgmt=wpa-psk
proto=rsn
pairwise=ccmp
group=ccmp
psk=$(kf_escape "$AP_PSK")

[ipv4]
method=shared
address1=$AP_ADDR

[ipv6]
method=disabled
EOF
    chmod 600 "$NMDIR/tbo-ap.nmconnection"
    echo "создан профиль tbo-ap (SSID $AP_SSID, $AP_ADDR)"
else
    if [ -f "$NMDIR/tbo-ap.nmconnection" ]; then echo "AP_PSK не задан — профиль tbo-ap оставлен прежний"; else echo "AP_PSK не задан или короче 8 символов — точка доступа не создана"; fi
fi

mkdir -p "$ROOT/usr/local/sbin" "$ROOT/etc/systemd/system/multi-user.target.wants"
install -m 755 "$D/tbo-wifi.sh" "$ROOT/usr/local/sbin/tbo-wifi"
install -m 644 "$D/tbo-wifi.service" "$ROOT/etc/systemd/system/tbo-wifi.service"
ln -sf /etc/systemd/system/tbo-wifi.service $ROOT/etc/systemd/system/multi-user.target.wants/tbo-wifi.service
echo "служба tbo-wifi установлена и включена"

for c in iw dnsmasq; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "/usr/sbin/$c" ]; then echo "есть $c"; else echo "ВНИМАНИЕ: нет $c"; fi
done
echo "готово, перезагрузка"
exit 0
