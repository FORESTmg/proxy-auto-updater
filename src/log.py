# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Логирование: файл в utf-8 + безопасный вывод в консоль (cp1251-safe).

FOREST's Proxy Auto-updater (GPL-3.0).
Логи по умолчанию пишутся в каталог программы
(%LOCALAPPDATA%\\ForestProxyUpdater\\logs), а не рядом с кодом — чтобы
установленная программа не зависела от прав на каталог исходников и не
засоряла репозиторий.
"""
import os
import sys
import time
import datetime


_BASE = os.path.dirname(os.path.abspath(__file__))


def default_log_dir() -> str:
    """Каталог логов по умолчанию.

    Windows: %LOCALAPPDATA%\\ForestProxyUpdater\\logs.
    POSIX:   $XDG_STATE_HOME/forest-proxy-updater/logs (~/.local/state/...).
    Переопределяется переменной окружения FOREST_ROOT (тесты, установщик,
    песочница) — тогда каталог логов лежит внутри указанного корня.
    """
    root = os.environ.get("FOREST_ROOT")
    if root:
        return os.path.join(root, "logs")
    if os.name != "nt":
        xdg = os.environ.get("XDG_STATE_HOME")
        if not xdg or not os.path.isabs(xdg):
            xdg = os.path.join(os.path.expanduser("~"), ".local", "state")
        return os.path.join(xdg, "forest-proxy-updater", "logs")
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        la = os.path.join(os.path.expanduser("~"), ".local", "share")
    root = os.path.join(la, "ForestProxyUpdater")
    return os.path.join(root, "logs")


def _resolve_dir(log_dir: str) -> str:
    r"""Абсолютный каталог логов: относительный путь считается от каталога кода.

    Иначе при запуске из планировщика (cwd = C:\Windows\System32) путь "logs"
    упирался в PermissionError и процесс падал до первой строки лога.
    """
    if not log_dir:
        return default_log_dir()
    d = log_dir
    if not os.path.isabs(d):
        d = os.path.join(_BASE, d)
    return d


class Logger:
    def __init__(self, log_dir: str = None):
        self.dir = _resolve_dir(log_dir)
        candidates = [self.dir]
        # страховка: каталог кода, затем временный — лог важнее места
        candidates.append(os.path.join(_BASE, "logs"))
        tmp = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or _BASE
        candidates.append(os.path.join(tmp, "ForestProxyUpdater", "logs"))
        self.path = os.path.join(self.dir, "main.log")
        self._fh = None
        # АНТИ-СПАМ: одинаковая строка не пишется чаще, чем раз в dedupe_s;
        # сколько раз её подавили — дописывается к следующей такой строке.
        # Журнал systemd читает человек, и «одна и та же тревога 90 раз за
        # сутки» прячет настоящие события.
        try:
            self.dedupe_s = float(os.environ.get("FOREST_LOG_DEDUPE_S", "60") or 60)
        except Exception:
            self.dedupe_s = 60.0
        self._seen = {}
        for c in candidates:
            try:
                os.makedirs(c, exist_ok=True)
                fh = open(os.path.join(c, "main.log"), "a", encoding="utf-8")
            except Exception:
                continue
            self.dir, self.path, self._fh = c, os.path.join(c, "main.log"), fh
            break

    def log(self, msg: str):
        msg = str(msg)
        now = time.time()
        prev = self._seen.get(msg)
        if self.dedupe_s > 0 and prev is not None:
            if now - prev[0] < self.dedupe_s:
                prev[1] += 1              # подавлено, но посчитано
                return
            if prev[1]:
                msg = (f"{msg}  [повторилось {prev[1]} раз(а) за "
                       f"{int(now - prev[0])} с]")
            prev[0], prev[1] = now, 0
        else:
            self._seen[msg] = [now, 0]
        if len(self._seen) > 4000:        # страховка от роста на длинном прогоне
            self._seen.clear()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        # stdout в собранном приложении — ТОЛЬКО ASCII и без исключений:
        # раньше здесь был print(), и не-ASCII символ в сообщении (на Windows
        # консольная кодировка cp1251) валил программу МОДАЛЬНЫМ ОКНОМ
        # PyInstaller. Полный текст всегда идёт в файл журнала ниже.
        try:
            import safestdio as SD
            SD.say(line)
        except Exception:
            pass
        try:
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()
        except Exception:
            pass

    def info(self, msg: str):
        self.log("INFO  " + msg)

    def warn(self, msg: str):
        self.log("WARN  " + msg)

    def error(self, msg: str):
        self.log("ERROR " + msg)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


_instance = None


def get_logger(log_dir: str = None) -> Logger:
    global _instance
    if _instance is None:
        _instance = Logger(log_dir)
    return _instance


def reset_logger(log_dir: str = None):
    """Пересоздать логгер (нужно тестам и смене каталога состояния)."""
    global _instance
    try:
        if _instance is not None:
            _instance.close()
    except Exception:
        pass
    _instance = Logger(log_dir)
    return _instance
