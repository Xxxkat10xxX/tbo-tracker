"""Настройки агента: /etc/tbo-agent/agent.conf (INI) и файл токена устройства (права 600).

Токен не передаётся аргументами и переменными окружения (их видно в списке процессов и журналах).
"""
from __future__ import annotations

import configparser
import os
import stat

DEFAULT_PATH = "/etc/tbo-agent/agent.conf"
EXECUTORS = ("auto", "mission", "offboard")


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, path: str = DEFAULT_PATH, require_token: bool = True):
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        if not cp.read(path, encoding="utf-8"):
            raise ConfigError("Не найден файл настроек %s (образец: agent/tbo-agent.conf.example)" % path)
        self.path = path
        g = cp.get
        self.url = g("server", "url", fallback="").strip().rstrip("/")
        if not self.url.startswith(("http://", "https://")):
            raise ConfigError("server.url должен начинаться с http:// или https:// (сейчас: %r)" % self.url)
        self.token_file = g("server", "token_file", fallback="/etc/tbo-agent/token").strip()
        self.ca_file = g("server", "ca_file", fallback="").strip() or None
        self.timeout_s = cp.getfloat("server", "timeout_s", fallback=15.0)
        self.executor = g("vehicle", "executor", fallback="auto").strip().lower()
        if self.executor not in EXECUTORS:
            raise ConfigError("vehicle.executor: auto, mission или offboard")
        self.fcu_wait_s = cp.getfloat("vehicle", "fcu_wait_s", fallback=60.0)
        self.offboard_tolerance_m = cp.getfloat("vehicle", "offboard_tolerance_m", fallback=1.5)
        self.local_tolerance_m = cp.getfloat("vehicle", "local_tolerance_m", fallback=0.25)
        self.takeoff_speed_ms = cp.getfloat("vehicle", "takeoff_speed_ms", fallback=1.0)
        self.limits = {
            "max_alt_m": cp.getfloat("limits", "max_alt_m", fallback=120.0),
            "max_dist_m": cp.getfloat("limits", "max_dist_m", fallback=1000.0),
            "max_home_offset_m": cp.getfloat("limits", "max_home_offset_m", fallback=30.0),
            "min_battery_pct": cp.getfloat("limits", "min_battery_pct", fallback=50.0),
            "min_sats": cp.getint("limits", "min_sats", fallback=8),
            "min_cell_v": cp.getfloat("limits", "min_cell_v", fallback=3.75),
            "cells": cp.getint("limits", "cells", fallback=6),
            # локальная навигация (Optical Flow / ArUco, без GPS): дальномер VL53L1X меряет до 4 м
            "local_max_alt_m": cp.getfloat("limits", "local_max_alt_m", fallback=3.0),
            "local_max_dist_m": cp.getfloat("limits", "local_max_dist_m", fallback=20.0),
            "local_max_speed_ms": cp.getfloat("limits", "local_max_speed_ms", fallback=2.0),
        }
        self.ready_timeout_s = cp.getfloat("limits", "ready_timeout_min", fallback=10.0) * 60
        self.state_dir = g("agent", "state_dir", fallback="/var/lib/tbo-agent").strip()
        self.log_level = g("agent", "log_level", fallback="info").strip().upper()
        self.token = self._read_token() if require_token else ""

    def _read_token(self) -> str:
        try:
            st = os.stat(self.token_file)
        except OSError as e:
            raise ConfigError("Файл токена %s недоступен: %s" % (self.token_file, e)) from None
        # права файла — только POSIX (борт — Linux); на Windows биты прав не означают доступ других пользователей
        if os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError("Файл токена %s доступен другим пользователям — выполните: chmod 600 %s"
                              % (self.token_file, self.token_file))
        with open(self.token_file, encoding="utf-8") as f:
            token = f.read().strip()
        if not token.startswith("tbo_") or len(token) < 40:
            raise ConfigError("В файле %s нет токена устройства (выпускается в tbo-tracker: «Борты на связи»)" % self.token_file)
        return token
