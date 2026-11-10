# tbo-agent — бортовой агент tbo-tracker

Работает на бортовом компьютере дрона (Skyris Technic 6S: Orange Pi 5 Pro, ROS Noetic, MAVROS, PX4):

* получает с сервера tbo-tracker план миссии — по GPS или локальный (в помещении: Optical Flow / ArUco, заводская
  настройка Technic);
* проверяет борт и загружает план в полётный контроллер;
* выполняет полёт по кнопке «Старт» оператора, передаёт телеметрию;
* после посадки отправляет журнал полёта — сервер импортирует его автоматически.

Полное руководство — [docs/DRONE.md](../docs/DRONE.md).

## Установка на борт

**Одной командой вместе с сервером** — с компьютера в той же сети, что и обе платы:

```bash
./deploy/field/deploy.sh --server user@<адрес Raspberry Pi> --drone orangepi@<адрес Orange Pi>
```

Сценарий ставит сервер, подключает борт, передаёт токен на борт без вывода на экран и ставит агента. Подробно —
[docs/DRONE.md](../docs/DRONE.md), раздел «Развёртывание одной командой».

**Вручную:**

1. В tbo-tracker: «Борты на связи» → «+ Подключить борт» → токен (показывается один раз) и «⇩ Агент для борта».
2. На борту:

   ```bash
   tar xzf tbo-agent.tar.gz && cd tbo-agent && sudo ./install.sh
   ```

   Установщик спросит адрес сервера и токен (скрытый ввод), проверит связь и запустит службу `tbo-agent`.
   Без вопросов: `sudo ./install.sh --server http://192.168.50.10 --token-file ~/token`, где `~/token` — файл
   с токеном. Установщик переносит токен в `/etc/tbo-agent/token` с правами 600 и удаляет исходный файл.
   Для сайта с HTTPS добавьте `--ca-file root.crt`: корневой сертификат Caddy.
3. Журнал службы: `journalctl -u tbo-agent -f`.

**ROS.** `run-agent.sh` подключает `/opt/ros/noetic` и `~/technic_ws/devel/setup.bash` и задаёт
`ROS_HOSTNAME=$(hostname).local`, как в примерах Skyris. Свои значения задаются в `/etc/tbo-agent/ros.env`.

## Файлы

| Файл | Назначение |
|---|---|
| `tbo_agent/agent.py` | команды сервера, телеметрия, наблюдение за полётом, журнал, восстановление после перезапуска |
| `tbo_agent/vehicle_ros.py` | MAVROS + PX4: миссия (AUTO.MISSION), параметры PX4, проверки образа Technic (`technic-show`) |
| `tbo_agent/offboard.py` | программа OFFBOARD на simple_offboard Technic: по GPS (`navigate_global`) и локально в кадре `map` |
| `tbo_agent/vehicle_sim.py` | симулятор борта — проверка без дрона (`--sim`) |
| `tbo_agent/checks.py`, `plan.py` | предполётные проверки; проверка плана: хеш, борт, пределы |
| `tbo_agent/recorder.py` | трек, акселерометр 25 Гц с сохранением пиков, итог полёта |
| `install.sh`, `tbo-agent.service`, `run-agent.sh` | установка и служба systemd (окружение ROS Noetic и Technic) |
| `tbo-agent.conf.example` | образец `/etc/tbo-agent/agent.conf`: сервер, исполнитель, пределы борта |

## Проверка без дрона

Симулятор вместо ROS, время ×10:

```bash
python3 -m tbo_agent --config agent.conf --sim --sim-speed 10
```

Тесты на модели PX4/MAVROS и на симуляторе проходят и под Python 3.8, как на борту:

```bash
python3 -m pytest agent/agent_tests
```

Требования: Python 3.8+ (стандартная библиотека). Для работы с дроном нужны также ROS Noetic, `mavros_msgs` и пакет
`technic` (или `clover`) из образа Orange Pi.
