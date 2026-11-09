# tbo-agent — бортовой агент tbo-tracker

Работает на бортовом компьютере дрона (Skyris Technic 6S: Orange Pi 5 Pro, ROS Noetic, MAVROS, PX4):

* получает с сервера tbo-tracker план миссии;
* проверяет борт и загружает план в полётный контроллер;
* выполняет полёт по кнопке «Старт» оператора, передаёт телеметрию;
* после посадки отправляет журнал полёта — сервер импортирует его автоматически.

Полное руководство — [docs/DRONE.md](../docs/DRONE.md).

## Установка на борт

1. В tbo-tracker: «Борты на связи» → «+ Подключить борт» → токен (показывается один раз) и «⇩ Агент для борта».
2. На борту:

   ```bash
   tar xzf tbo-agent.tar.gz && cd tbo-agent && sudo ./install.sh
   ```

   Установщик спросит адрес сервера и токен (скрытый ввод), проверит связь и запустит службу `tbo-agent`.
3. Журнал службы: `journalctl -u tbo-agent -f`.

## Файлы

| Файл | Назначение |
|---|---|
| `tbo_agent/agent.py` | команды сервера, телеметрия, наблюдение за полётом, журнал, восстановление после перезапуска |
| `tbo_agent/vehicle_ros.py` | MAVROS + PX4: миссия (AUTO.MISSION) или программа OFFBOARD на simple_offboard Technic |
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
