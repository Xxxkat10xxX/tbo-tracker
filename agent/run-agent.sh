#!/usr/bin/env bash
# Окружение ROS (Noetic + рабочее пространство Technic) и запуск агента. Используется службой tbo-agent.
set -e
if [ -f /opt/ros/noetic/setup.bash ]; then
  # shellcheck disable=SC1091
  source /opt/ros/noetic/setup.bash
fi
for ws in /home/orangepi/technic_ws /home/orangepi/catkin_ws "${HOME:-/nonexistent}/technic_ws" "${HOME:-/nonexistent}/catkin_ws"; do
  if [ -f "$ws/devel/setup.bash" ]; then
    # shellcheck disable=SC1090,SC1091
    source "$ws/devel/setup.bash"
    break
  fi
done
# Адреса ROS — как у служб Technic (technic.service): master http://127.0.0.1:11311, адрес узла ROS_IP=127.0.0.1.
# Их задаёт сам агент (vehicle_ros.loopback_ros_env), если в /etc/tbo-agent/ros.env не задано своё. Прежнее
# ROS_HOSTNAME=<hostname>.local и master по имени localhost ставили каждый вызов сервиса в зависимость от DNS/mDNS
# по Wi-Fi: при слабой связи — секунды ожидания, борт висел на месте и садился.
export PYTHONPATH="/opt/tbo-agent${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m tbo_agent --config "${TBO_AGENT_CONFIG:-/etc/tbo-agent/agent.conf}" "$@"
