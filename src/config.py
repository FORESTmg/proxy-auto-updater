# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Конфигурация + автоопределение режима и ядра (GPL-3.0).

FOREST's Proxy Auto-updater. Один конфиг — два режима (переключаются в
config.json, без правки кода):

  * ``client.mode = "standalone"`` (по умолчанию) — программа НЕ требует
    v2rayN: сама хранит список серверов (свой файл состояния), сама пишет
    конфиг ядра xray, сама запускает/останавливает процесс ``xray.exe`` и
    сама ставит/снимает системный прокси.
  * ``client.mode = "v2rayn"`` — поведение как в исходном проекте: работа
    через базу и ядро установленного v2rayN.

Каталог состояния/логов по умолчанию — %LOCALAPPDATA%\\ForestProxyUpdater
(переопределяется ``paths.root`` в конфиге или переменной FOREST_ROOT).
Каталог исходников при этом НЕ засоряется: state/, logs/, config.json в
репозиторий не пишутся.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import log

APP_NAME = "ForestProxyUpdater"
# Имя каталогов на POSIX: строчные буквы через дефис (соглашение Linux,
# совпадает с именами systemd-юнитов forest-proxy-updater.*).
POSIX_APP_NAME = "forest-proxy-updater"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))        # .../src
PROJECT_DIR = os.path.dirname(BASE_DIR)                      # корень репозитория

DEFAULT_SOCKS_PORT = 10808
DEFAULT_TORRENT_PORT = 10820


# ─────────────────────────────────────────────────── каталоги ───
def _xdg(env: str, default_rel: str) -> str:
    """Каталог по XDG Base Directory Specification."""
    v = os.environ.get(env)
    if v and os.path.isabs(v):
        return v
    return os.path.join(os.path.expanduser("~"), *default_rel.split("/"))


def data_root() -> str:
    """Каталог ДАННЫХ программы: бинарь, ядро xray, гео-базы.

    Windows: %LOCALAPPDATA%\\ForestProxyUpdater.
    POSIX:   $XDG_DATA_HOME/forest-proxy-updater (~/.local/share/...).
    """
    if os.name != "nt":
        return os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), POSIX_APP_NAME)
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        la = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(la, APP_NAME)


def default_state_dir() -> str:
    """Каталог состояния (state/логи): Windows — <root>\\state, POSIX — XDG state."""
    if os.name != "nt":
        return os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), POSIX_APP_NAME)
    return os.path.join(app_root(), "state")


def default_log_dir() -> str:
    """Каталог логов: Windows — <root>\\logs, POSIX — <state>/logs."""
    if os.name != "nt":
        return os.path.join(default_state_dir(), "logs")
    return os.path.join(app_root(), "logs")


def default_config_path() -> str:
    """Путь конфига по умолчанию: POSIX держит его отдельно от данных (XDG config)."""
    if os.name != "nt":
        return os.path.join(_xdg("XDG_CONFIG_HOME", ".config"),
                            POSIX_APP_NAME, "config.json")
    return os.path.join(app_root(), "config.json")


def app_root() -> str:
    """Корневой каталог данных программы.

    Приоритет: FOREST_ROOT -> платформенный каталог данных. На POSIX это
    $XDG_DATA_HOME/forest-proxy-updater, на Windows — прежний
    %LOCALAPPDATA%\\ForestProxyUpdater (поведение Windows не менялось).
    """
    r = os.environ.get("FOREST_ROOT")
    if r:
        return os.path.abspath(r)
    return data_root()



def example_config_path():
    for c in (os.path.join(PROJECT_DIR, "config.example.json"),
              os.path.join(BASE_DIR, "config.example.json"),
              os.path.join(app_root(), "config.example.json")):
        if os.path.isfile(c):
            return c
    return None


def ensure_safe_cwd() -> str:
    """Уводит рабочий каталог (CWD) из каталога распаковки onefile ``_MEI*``.

    Зачем: PyInstaller-onefile распаковывает содержимое во временный каталог
    ``_MEIxxxx``. Если CWD процесса (или унаследованный CWD дочернего процесса)
    окажется внутри него, уборщик bootloader'а не сможет снести каталог — и
    пользователю покажется модальное окно «Failed to remove temporary
    directory». Уходить надо ДО запуска любых дочерних процессов.

    Переходим в НАДЁЖНОЕ уже существующее место — каталог самого .exe (или
    системный temp). Ничего не создаём: у автотеста не должно быть побочных
    эффектов. Возвращает новый CWD ('' — не потребовалось/не удалось).
    """
    try:
        meipass = str(getattr(sys, "_MEIPASS", "") or "")
        if not meipass:
            return ""
        cur = os.path.normcase(os.path.abspath(os.getcwd()))
        mp = os.path.normcase(os.path.abspath(meipass))
        if cur != mp and not cur.startswith(mp + os.sep):
            return ""
        cands = []
        try:
            if getattr(sys, "frozen", False) and sys.executable:
                cands.append(os.path.dirname(os.path.abspath(sys.executable)))
        except Exception:
            pass
        cands.append(tempfile.gettempdir())
        for t in cands:
            try:
                if t and os.path.isdir(t):
                    os.chdir(t)
                    return t
            except Exception:
                continue
    except Exception:
        pass
    return ""


def config_path() -> str:
    """Путь к рабочему config.json (существующий — иначе цель для создания)."""
    p = os.environ.get("FOREST_CONFIG")
    if p:
        return os.path.abspath(p)
    installed = default_config_path()
    dev = os.path.join(PROJECT_DIR, "config.json")
    if os.path.isfile(installed):
        return installed
    if os.path.isfile(dev):
        return dev
    return installed


# ─────────────────────────────────────────────────── значения по умолчанию ───
# Разумные ДЕФОЛТЫ для чужого пользователя (не личные настройки автора):
# без шлюза Cloudflare, кап подсети 1, минимум скорости 1000 КБ/с,
# «РФ напрямую» ВЫКЛЮЧЕНО (чтобы не ломать чужую маршрутизацию).
DEFAULTS = {
    "ui": {"lang": "", "tray_state_mark": True, "theme": "auto",
           "helper": "auto", "helper_exe": ""},
    "notify": {
        "enabled": True,
        "duration_ms": 5000,
        "fps": 60,
        "corner": "bottom-right",
        "on_server_switch": True,
        "show_on_manual_off": True,
        "width": 360,
    },
    "client": {
        "mode": "standalone",
        "socks_port": DEFAULT_SOCKS_PORT,
        "torrent_port": DEFAULT_TORRENT_PORT,
        "listen": "127.0.0.1",
        "manage_system_proxy": True,
        "core": "",
        # поля режима v2rayn
        "dir": "",
        "exe": "v2rayN.exe",
        "db_rel": "guiConfigs/guiNDB.db",
        "cfg_rel": "guiConfigs/guiNConfig.json",
        "core_rel": "bin/xray/xray.exe",
    },
    "cycle": {"full_refresh_hours": 0.5},
    "test": {
        "port_start": 10810, "workers": 8, "rtt_timeout": 6, "rtt_repeats": 2,
        "speed_timeout": 15, "speed_bytes": 5242880, "min_speed_kbps": 1000,
        "max_rtt_ms": 1500, "max_jitter_ms": 800, "top_for_speed": 60,
        "max_candidates": 400, "tcp_prefilter": True, "final_verify_top": 60,
        "min_pool": 60, "reserve_min": 30, "speed_rounds": 1,
        "gate_urls": ["https://www.youtube.com/generate_204"], "reserve_verify": 20,
        # Медленный ответ гейта/YouTube = деградация: браузер на такой ноде
        # висит («сервер недоступен»), пока соединение не отвалится.
        "gate_max_s": 2.5, "youtube_max_tt_s": 2.5,
        # МУЛЬТИ-ДОМЕННЫЙ TLS-ГЕЙТ: узел проверяется реальным рукопожатием к
        # РАЗНЫМ доменам. Пачки отказов на живом пуле валили одновременно
        # gstatic/wikipedia/ytimg (curl rc=35 за 45 мс), т.е. узел не проходил
        # рукопожатие вообще, а не «не пускал один домен».
        "tls_urls": ["https://www.youtube.com/generate_204",
                     "https://www.gstatic.com/generate_204",
                     "https://ru.wikipedia.org/",
                     "https://i.ytimg.com/generate_204",
                     "https://example.com/"],
        "tls_max_s": 2.5, "tls_min_ok": 3,
        # МЕДЛЕННЫЙ TLS — не приговор узлу: сравниваем с МЕДИАНОЙ по пулу и
        # бракуем только явных лидеров отставания (worst > медиана×factor или
        # медиана+margin). Иначе порог 2.5 с выкидывал узлы за 2.8-3.1 с, и пул
        # съёживался 10 → 5 → 3 → 2 при каждом переотборе.
        "tls_slow_factor": 1.8, "tls_slow_margin_s": 1.0,
        # Жёсткий потолок рукопожатия (с): дольше — уже не «медленно», а сломан.
        "tls_hard_s": 10.0,
        # Порог обрыва для зависшего узла: 20-секундные таймауты означали, что
        # узел принимает соединение и не форвардит; такие вылетают за 5 с.
        "hard_stall_s": 5,
    },
    "limits": {"max_servers": 100},
    "activation": {"prefer_current_if_healthy": True, "max_restart_wait_s": 40},
    "watch": {
        "check_interval_s": 10, "probe_timeout_s": 4, "max_strikes": 3,
        "hotcache_n": 20, "hotcache_ttl_s": 3600, "speed_min_kbps": 1500,
        "speed_check_every_s": 300, "balancer_pool_n": 10, "pool_refresh_s": 600,
        "balancer_mode": True, "probe_retries": 2, "strict_check_every_s": 300,
        "ru_direct": False, "pool_freeze_s": 10800, "min_speed_kbps": 200,
        "youtube_check_every_s": 300,
        # Зонд ЗДОРОВЬЯ узлов для балансировщика (observatory/leastPing). Не
        # gstatic: он не видит узлы, рвущие TLS к YouTube, — leastPing выбирал
        # их, и YouTube мгновенно отказывал (000 за ~40 мс при живой связи).
        "balancer_probe_url": "https://www.youtube.com/generate_204",
        # Рантайм-здоровье пула (см. clientwrite.balancer_strategy/burst_observatory):
        # leastLoad + burstObservatory с коротким таймаутом — тормозящая/мёртвая
        # нода уходит из ротации за секунды. Откат на leastPing — сменой стратегии
        # в конфиге, без пересборки.
        "balancer_strategy": "leastLoad", "balancer_max_rtt_ms": 1500,
        "balancer_expected": 2, "balancer_burst_timeout_s": 2,
        "balancer_burst_interval_s": 2, "balancer_burst_sampling": 2,
        # ПОСТ-ВАЛИДАЦИЯ после применения пула и ПЕРИОДИЧЕСКАЯ параллельная
        # проба: 16 запросов × 2 раунда / 12 запросов раз в минуту. Браузер
        # открывает десятки соединений сразу — одиночный probe пачку не видит.
        "post_validate": True, "post_validate_rounds": 2, "post_validate_delay_s": 3,
        "burst_probe_every_s": 30, "burst_probe_n": 12,
        "burst_probe_timeout_s": 6, "burst_stall_s": 5,
        # МЕДЛЕННЫЕ адреса: их ответ (даже 5-15 с) — свойство сайта/маршрута,
        # а не признак плохого узла. Стопор на них НЕ запускает переотбор.
        "burst_slow_urls": ["github.com"], "burst_slow_stall_s": 15,
        # Пауза сторожа, если атрибуция не нашла виновника в пуле: 5/10/20/30 мин.
        # Без неё переотбор повторялся каждые 30-40 с (~90 раз за сутки).
        "burst_backoff_s": [300, 600, 1200, 1800],
        # Не дёргать пул сразу после пересборки (пересборка = разрыв связи).
        "burst_cooldown_after_rebuild_s": 120,
        # Пол пула: ниже него состав не опускаем (0 = взять diversity.min_pool_after_cap).
        "pool_floor": 0,
        "ban_bad_minutes": 30,
    },
    "diversity": {"per_subnet_cap": 1, "min_pool_after_cap": 12, "max_cap_relax": 4},
    "blacklist": {"cloudflare_nodes": True},
    # ── автономный надзор (задача Планировщика заданий) ──
    # enabled: поднимать программу автоматически (задача --ensure при входе и
    #          каждые interval_min минут). ВЫКЛЮЧЕНО = программа сама не
    #          перезапустится после падения/закрытия.
    "watchdog": {"enabled": True, "interval_min": 5,
                 "task_name": "ForestProxyUpdater-Watchdog"},
}


def _deep_merge(dst: dict, src: dict) -> dict:
    """Дополняет dst значениями src (рекурсивно), не затирая уже заданные."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


# ─────────────────────────────────────────────────── загрузка ───
def load_config(path: str = None) -> dict:
    """Читает config.json. Если файла нет — создаёт из config.example.json.

    Возвращает словарь с добавленным блоком ``paths`` (state_dir/backup_dir/
    log_dir). Первый запуск без настроек безопасен: дефолты из config.example
    не содержат личных адресов и «РФ напрямую» выключено.
    """
    p = path or config_path()
    data: dict = {}
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    else:
        ex = example_config_path()
        if ex:
            try:
                with open(ex, encoding="utf-8") as f:
                    data = json.load(f)
                os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, p)
                log.get_logger().info("Конфиг не найден — создан из "
                                      "config.example.json: %s" % p)
            except Exception as e:
                log.get_logger().warn("первый запуск: создать конфиг не удалось "
                                      "(%s) — работаю на дефолтах" % e)
                data = {}

    cfg = _deep_merge(copy.deepcopy(DEFAULTS), data)

    root = (cfg.get("paths", {}) or {}).get("root") or app_root()
    root = os.path.abspath(root)
    given = cfg.get("paths", {}) or {}
    if os.name == "nt":
        # Windows: поведение не менялось — состояние и логи внутри каталога программы
        state_dir = os.path.join(root, "state")
        log_dir = os.path.join(root, "logs")
    else:
        # POSIX: состояние и логи — по XDG (состояние отдельно от данных);
        # явные пути из config.json уважаются
        state_dir = os.path.abspath(given.get("state_dir") or default_state_dir())
        log_dir = os.path.abspath(given.get("log_dir") or default_log_dir())
    cfg["paths"] = {
        "root": root,
        "state_dir": state_dir,
        "backup_dir": os.path.join(state_dir, "backups"),
        "log_dir": log_dir,
    }
    for d in (cfg["paths"]["state_dir"], cfg["paths"]["backup_dir"],
              cfg["paths"]["log_dir"]):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    cfg["_config_path"] = p
    return cfg


# ─────────────────────────────────────────────────── поиск ядра ───
def core_binary_names() -> tuple:
    """Имена бинаря ядра: на Windows xray.exe, на POSIX xray."""
    return ("xray.exe", "xray") if os.name == "nt" else ("xray",)


# Системные каталоги ядра на POSIX (пакет дистрибутива / ручная установка).
SYSTEM_CORE_DIRS = ("/usr/local/bin", "/usr/bin", "/opt/xray", "/opt/xray/bin")


def _core_exec_ok(path: str) -> bool:
    """Исполняемость бинаря: на POSIX проверяем бит X, на Windows — как раньше.

    Если бит X потерян (например, onefile-распаковка не сохранила режим),
    пробуем выставить его сами: файл лежит в нашем каталоге, и без него ядро
    просто не запустится — «мягкая деградация» здесь неуместна.
    """
    if os.name == "nt":
        return True
    try:
        if os.access(path, os.X_OK):
            return True
    except Exception:
        return True
    try:
        os.chmod(path, 0o755)
        log.get_logger().warn(f"ядру возвращён бит исполняемости: {path}")
        return os.access(path, os.X_OK)
    except Exception:
        return False


def _explicit_candidates(explicit: str) -> list:
    """Пути-кандидаты для явного client.core: абсолютный — как есть,
    относительный — от каталога данных, каталога программы и текущего каталога.

    Относительный путь нужен потому, что машинно-зависимый абсолютный путь в
    config.json — лишний: «bin/xray/xray(.exe)» одинаков на всех машинах.
    """
    explicit = str(explicit)
    if os.path.isabs(explicit):
        return [explicit]
    out = []
    for base in (app_root(), PROJECT_DIR, os.getcwd()):
        try:
            cand = os.path.normpath(os.path.join(base, explicit))
        except Exception:
            continue
        if cand not in out:
            out.append(cand)
    return out or [explicit]


def find_core(cfg: dict):
    """Путь к бинарю ядра xray (standalone).

    Порядок (одинаковый на обеих платформах): явный путь из конфига
    (client.core; относительный считается от каталога данных/программы) ->
    ПОСТОЯННЫЙ каталог <данные>/bin/xray (копия из бандла, см.
    ``ensure_core_installed``) -> каталог репозитория (режим разработки) ->
    системные каталоги (POSIX) -> PATH.

    Каталога распаковки onefile (_MEIPASS) в этом списке НЕТ намеренно: ядро,
    запущенное из временного каталога, живёт дольше программы (так и задумано),
    файл занят — уборщик PyInstaller не может снести каталог: показывает
    модальное окно «Failed to remove temporary directory» и оставляет мусор
    _MEI* в temp. Из бандла ядро только КОПИРУЕТСЯ в постоянный каталог.

    Возвращает None, если ядро не найдено — вызывающий код подскажет скачать
    официальный релиз XTLS/Xray-core.
    """
    cl = cfg.get("client", {})
    for key in ("core", "core_rel"):
        explicit = cl.get(key)
        if not explicit:
            continue
        for p in _explicit_candidates(explicit):
            if os.path.isfile(p) and _core_exec_ok(p):
                return p
    # ПОСТОЯННАЯ копия: сверить с бандлом и, если нужно, скопировать. Вызов
    # идёт до перебора каталогов, иначе обновление в бандле никогда бы не
    # доехало до старой копии (стабильный путь нашёлся бы раньше).
    core = ensure_core_installed_once()
    if core and _core_exec_ok(core):
        return core
    names = core_binary_names()
    dirs = [os.path.join(PROJECT_DIR, "bin", "xray"), PROJECT_DIR, app_root()]
    if os.name != "nt":
        dirs = dirs + list(SYSTEM_CORE_DIRS)
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p) and _core_exec_ok(p):
                return p
    for n in ("xray", "xray.exe"):
        p = shutil.which(n)
        if p:
            return p
    return None


# ─────────────────────── постоянная копия ядра (вне временного _MEI) ───
# Пакет onefile распаковывается во временный каталог _MEIxxxx. Ядро, запущенное
# ОТТУДА, переживает выход программы (прокси должен жить), файл в это время
# занят — уборщик PyInstaller не может снести каталог и показывает модальное
# окно. Поэтому при первом запуске (или когда в бандле версия новее) ядро и обе
# гео-базы копируются в ПОСТОЯННЫЙ каталог данных, и ядро запускается только
# оттуда. Windows: %LOCALAPPDATA%\ForestProxyUpdater\bin\xray (либо
# <FOREST_ROOT>\bin\xray), Linux: ~/.local/share/forest-proxy-updater/bin/xray.
CORE_SUBDIR = os.path.join("bin", "xray")
# Что переносим: ядро (имя платформенное) + обе гео-базы + лицензия.
CORE_ASSET_NAMES = ("xray", "xray.exe", "geoip.dat", "geosite.dat", "LICENSE")
MEI_PREFIX = "_MEI"            # имена каталогов распаковки PyInstaller
MEI_MIN_AGE_S = 120.0          # моложе — не трогаем (может быть жив запуск)


def frozen_bundle_root() -> str:
    """Корень распаковки onefile-пакета (sys._MEIPASS); '' — не из бандла."""
    mp = getattr(sys, "_MEIPASS", "") or ""
    try:
        return mp if (mp and os.path.isdir(mp)) else ""
    except Exception:
        return ""


def bundle_core_dir() -> str:
    """Каталог bin/xray ВНУТРИ бандла onefile — источник для копирования."""
    mp = frozen_bundle_root()
    if not mp:
        return ""
    d = os.path.join(mp, CORE_SUBDIR)
    return d if os.path.isdir(d) else ""


def core_dir() -> str:
    """ПОСТОЯННЫЙ каталог ядра: <каталог данных программы>/bin/xray."""
    return os.path.join(app_root(), CORE_SUBDIR)


def installed_core() -> str:
    """Ядро в постоянном каталоге ('' — если его там ещё нет)."""
    d = core_dir()
    for n in core_binary_names():
        p = os.path.join(d, n)
        if os.path.isfile(p):
            return p
    return ""


def _sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _files_same(a: str, b: str) -> bool:
    """Файлы совпадают? Сначала дешёвый размер, sha256 — только при равенстве."""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        return _sha256_file(a) == _sha256_file(b)
    except Exception:
        return False


def ensure_core_installed(force: bool = False) -> str:
    """Копирует ядро и гео-базы из бандла в постоянный каталог.

    Копирование идёт ТОЛЬКО когда файла нет, размер отличается или содержимое
    (sha256) отличается — иначе запуск ничего не читает и не пишет лишнего
    (сверка ~65 МБ занимает доли секунды, копирование — секунды).

    Замена атомарная (temp + os.replace): если ядро сейчас запущено и файл
    занят, копия не портится — старая версия остаётся работать, в лог идёт
    предупреждение.

    Возвращает путь к ядру в постоянном каталоге ('' — если ни бандла, ни
    копии нет). Никогда не поднимает исключений: при неудаче ядро просто не
    найдётся, программа скажет об этом в лог и завершится без окон.
    """
    lg = log.get_logger()
    src_dir = bundle_core_dir()
    if not src_dir:
        return installed_core()            # не из бандла (репозиторий/разработка)
    names = [n for n in CORE_ASSET_NAMES
             if os.path.isfile(os.path.join(src_dir, n))]
    if not names:
        return installed_core()
    dst_dir = core_dir()
    try:
        os.makedirs(dst_dir, exist_ok=True)
    except Exception as e:
        lg.error(f"постоянный каталог ядра недоступен ({dst_dir}): {e}")
        return ""
    copied, failed = [], []
    for n in names:
        src = os.path.join(src_dir, n)
        dst = os.path.join(dst_dir, n)
        if not force and os.path.isfile(dst) and _files_same(src, dst):
            continue
        tmp = dst + ".new"
        try:
            shutil.copy2(src, tmp)
            if os.name != "nt":
                os.chmod(tmp, 0o755 if n in core_binary_names() else 0o644)
            os.replace(tmp, dst)
            copied.append(n)
        except Exception as e:
            failed.append(f"{n} ({e})")
            try:
                os.unlink(tmp)
            except Exception:
                pass
    if copied:
        lg.info("Ядро и гео-базы перенесены в постоянный каталог %s: %s"
                % (dst_dir, ", ".join(copied)))
    if failed:
        lg.warn("ядро в %s не обновлено: %s (запущено? повторится при "
                "следующем старте)" % (dst_dir, "; ".join(failed)))
    return installed_core()


_CORE_INSTALL_DONE = False


def ensure_core_installed_once(force: bool = False) -> str:
    """ensure_core_installed, но не чаще одного раза за процесс.

    find_core зовут из каждого цикла и каждой самопроверки (на Windows — раз в
    10 с), а сверка содержимого читает десятки мегабайт: без этого флага была
    бы лишняя работа на ровном месте.
    """
    global _CORE_INSTALL_DONE
    if _CORE_INSTALL_DONE and not force:
        return installed_core()
    try:
        return ensure_core_installed(force=force)
    finally:
        _CORE_INSTALL_DONE = True


# ─────────────────────── уборка своих старых распаковок _MEI* ───
def _proc_image_paths():
    """Пути исполняемых файлов всех процессов (без прав администратора).

    None — определить не удалось: тогда уборка не делается вовсе (лучше
    оставить мусор в temp, чем снести каталог работающего ядра).
    """
    if os.name != "nt":
        out = []
        try:
            pids = os.listdir("/proc")
        except Exception:
            return None
        for pid in pids:
            if not pid.isdigit():
                continue
            try:
                out.append(os.readlink(f"/proc/{pid}/exe"))
            except OSError:
                continue
            except Exception:
                return None
        return out
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
             "ForEach-Object { $_.ExecutablePath }"],
            capture_output=True, text=True, errors="replace", timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return None
        return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception:
        return None


def _mei_ancestor(path: str) -> str:
    """Ближайший каталог _MEI* в пути файла ('' — если такого нет)."""
    try:
        d = os.path.dirname(os.path.normpath(path))
    except Exception:
        return ""
    while d and d != os.path.dirname(d):
        if os.path.basename(d).startswith(MEI_PREFIX):
            return d
        d = os.path.dirname(d)
    return ""


def _mei_dirs_in_use(proc_paths=None):
    """Каталоги _MEI*, из которых запущены процессы (POSIX-проверка).

    На POSIX удалить файлы работающего процесса МОЖНО, поэтому опираемся на
    список процессов: exe которого лежит внутри каталога. None — список узнать
    не удалось (тогда уборка не делается вовсе).
    """
    paths = _proc_image_paths() if proc_paths is None else proc_paths
    if paths is None:
        return None
    out = set()
    for p in paths:
        d = _mei_ancestor(p)
        if d:
            out.add(os.path.normcase(os.path.abspath(d)))
    return out


# Файлы, которые работающий процесс держит занятыми (загруженные в память
# библиотеки/ядро): по ним и проверяем, «живой» ли каталог распаковки.
_HOT_SUFFIXES = (".dll", ".pyd", ".exe", ".so", ".so.1", "python")


def _dir_locked_windows(d: str, limit: int = 80) -> bool:
    """Windows: занят ли каталог распаковки работающим процессом.

    Запущенный exe/DLL открыт без разрешения на запись, поэтому попытка
    открыть файл на запись (неразрушающая: open+close) — точный признак того,
    что из каталога работает процесс. На чтение Windows отдаёт запущенный файл
    свободно, поэтому проверка на запись, а не на чтение. Ошибка осмотра
    трактуется как «занят» (лучше оставить мусор, чем снести живой каталог).
    """
    hot, rest, total = [], [], 0
    try:
        for root, _dirs, files in os.walk(d):
            for n in files:
                total += 1
                p = os.path.join(root, n)
                low = n.lower()
                if any(k in low for k in _HOT_SUFFIXES):
                    hot.append(p)
                else:
                    rest.append(p)
            if total > 4000:
                break
    except Exception:
        return True
    for p in (hot + rest)[:limit]:
        try:
            fd = os.open(p, os.O_RDWR)
        except PermissionError:
            return True
        except Exception:
            continue
        else:
            try:
                os.close(fd)
            except Exception:
                pass
    return False


def _dir_in_use(d: str, proc_paths) -> bool:
    """Работает ли что-нибудь из этого каталога распаковки."""
    if os.name == "nt":
        return _dir_locked_windows(d)
    if proc_paths is None:
        return True                      # не смогли узнать процессы — не трогаем
    key = os.path.normcase(os.path.abspath(d))
    for p in proc_paths:
        anc = _mei_ancestor(p)
        if anc and os.path.normcase(os.path.abspath(anc)) == key:
            return True
    return False


def _is_our_bundle(d: str) -> bool:
    """Наша ли распаковка: внутри обязана быть наш bin/xray с ядром."""
    sub = os.path.join(d, CORE_SUBDIR)
    if not os.path.isdir(sub):
        return False
    return any(os.path.isfile(os.path.join(sub, n)) for n in core_binary_names())


def cleanup_stale_bundles(max_age_s: float = MEI_MIN_AGE_S,
                          dry: bool = False, tmp_dir: str = None) -> dict:
    """Убирает СВОИ старые каталоги распаковки onefile (_MEI*) в temp.

    Каталог удаляется только если:
      * в нём лежит НАШ bin/xray с ядром (чужие распаковки не трогаем);
      * это не каталог текущего процесса (sys._MEIPASS);
      * он старше max_age_s (защита от одновременного запуска второй копии);
      * из него НЕ запущен ни один процесс (иначе снос задел бы живое ядро,
        в том числе работающее ядро пользователя);
      * список процессов вообще удалось получить (иначе не удаляем ничего).

    ``tmp_dir`` — каталог для обхода (по умолчанию системный temp; параметр
    нужен автотестам, чтобы не трогать чужой temp).

    Возвращает {"removed": [...], "kept": [...], "in_use": [...],
    "skipped": [...]}. Исключений не поднимает.
    """
    lg = log.get_logger()
    res = {"removed": [], "kept": [], "in_use": [], "skipped": []}
    try:
        tmp = tmp_dir or tempfile.gettempdir()
        entries = sorted(os.listdir(tmp))
    except Exception:
        return res
    cur = ""
    try:
        b = frozen_bundle_root()
        cur = os.path.normcase(os.path.abspath(b)) if b else ""
    except Exception:
        cur = ""
    if os.name == "nt":
        # Windows: «занят ли каталог» определяем пробой файлов на запись
        in_use, proc_paths = set(), []
    else:
        proc_paths = _proc_image_paths()
        in_use = _mei_dirs_in_use(proc_paths)
    for name in entries:
        if not name.startswith(MEI_PREFIX):
            continue
        d = os.path.join(tmp, name)
        try:
            if os.path.islink(d) or not os.path.isdir(d):
                continue
        except Exception:
            continue
        key = os.path.normcase(os.path.abspath(d))
        if cur and key == cur:
            res["skipped"].append(d)          # текущая распаковка
            continue
        if not _is_our_bundle(d):
            res["skipped"].append(d)          # не наша распаковка
            continue
        if _dir_in_use(d, proc_paths) or (in_use and key in in_use):
            res["in_use"].append(d)           # оттуда работает ядро/программа
            continue
        try:
            age = time.time() - os.path.getmtime(d)
        except Exception:
            age = 0.0
        if age < max_age_s:
            res["skipped"].append(d)          # свежая — возможно, чужой старт
            continue
        if dry:
            res["removed"].append(d)
            continue
        shutil.rmtree(d, ignore_errors=True)
        if os.path.isdir(d):
            res["kept"].append(d)
        else:
            res["removed"].append(d)
    if res["removed"]:
        lg.info("Убраны свои старые каталоги распаковки (%d): %s"
                % (len(res["removed"]),
                   ", ".join(os.path.basename(x) for x in res["removed"])))
    if res["kept"]:
        lg.warn("каталоги распаковки не удалось убрать (заняты?): %s"
                % ", ".join(res["kept"]))
    return res


def cleanup_stale_bundles_async() -> None:
    """Уборка старых _MEI* в фоне: не задерживает старт и не мешает ядру."""
    try:
        import threading
        threading.Thread(target=cleanup_stale_bundles,
                         name="forest-mei-cleanup", daemon=True).start()
    except Exception:
        pass


def core_pid_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "core.pid")


def core_running(cfg: dict) -> bool:
    """Запущено ли НАШЕ ядро (по pid-файлу)."""
    p = core_pid_path(cfg)
    try:
        pid = int(open(p, encoding="utf-8").read().strip())
    except Exception:
        return False
    if os.name != "nt":
        # POSIX: signal 0 — процесс есть и он наш/доступен
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259       # STILL_ACTIVE
    except Exception:
        # не-Windows / нет прав — считаем живым по факту наличия pid
        return True


# ─────────────────────────────────────────────────── поиск клиента ───
def find_client(cfg: dict) -> dict:
    """Возвращает словарь клиента, зависящий от ``client.mode``.

    Общие поля: dir, exe, core, cfg (конфиг ядра), db (база/стор серверов),
    running, socks_port, mode. В standalone добавляется torrent_port.
    """
    mode = str(cfg.get("client", {}).get("mode") or "standalone").lower()
    if mode == "v2rayn":
        info = _find_client_v2rayn(cfg)
    else:
        info = _find_client_standalone(cfg)
    info["mode"] = mode
    return info


def _find_client_standalone(cfg: dict) -> dict:
    lg = log.get_logger()
    cl = cfg.get("client", {})
    socks_port = int(cl.get("socks_port", DEFAULT_SOCKS_PORT))
    torrent_port = int(cl.get("torrent_port", DEFAULT_TORRENT_PORT))
    state_dir = cfg["paths"]["state_dir"]
    core = find_core(cfg)
    info = {
        "mode": "standalone",
        "service": None,
        "dir": os.path.dirname(core) if core else None,
        "core": core,
        "exe": core or ("xray.exe" if os.name == "nt" else "xray"),
        "cfg": os.path.join(state_dir, "xray_config.json"),
        "db": os.path.join(state_dir, "servers.json"),
        "bin_cfg": os.path.join(state_dir, "xray_config.json"),
        "running": core_running(cfg),
        "socks_port": socks_port,
        "torrent_port": torrent_port,
    }
    if not core:
        if os.name == "nt":
            lg.error("Ядро Xray не найдено. Положите bin\\xray\\xray.exe рядом с "
                     "программой или установите xray в PATH. Скачать официальный "
                     "релиз: https://github.com/XTLS/Xray-core/releases")
        else:
            lg.error("Ядро Xray не найдено. Ожидается bin/xray/xray рядом с "
                     "программой, /usr/bin/xray либо xray в PATH. Скачать "
                     "официальный релиз: https://github.com/XTLS/Xray-core/releases")
    return info


def _find_client_v2rayn(cfg: dict) -> dict:
    """Определение установки v2rayN (режим совместимости, только Windows)."""
    lg = log.get_logger()
    cl = cfg.get("client", {})
    exe_name = cl.get("exe", "v2rayN.exe")
    socks_port = int(cl.get("socks_port", DEFAULT_SOCKS_PORT))
    if os.name != "nt":
        # v2rayN — приложение Windows; на Linux режим неприменим. Работаем
        # standalone и говорим об этом прямо, а не ищем несуществующее.
        lg.error("client.mode=v2rayn поддерживается только на Windows; "
                 "на Linux используется standalone-режим (своё ядро xray).")
        return {"dir": None, "running": False, "socks_port": socks_port,
                "is_running_proc": False, "mode": "standalone"}

    dir_ = None
    running = False
    for p in _proc_paths_by_name(os.path.splitext(exe_name)[0]):
        d = os.path.dirname(p)
        if os.path.isfile(os.path.join(d, exe_name)):
            dir_, running = d, True
            break
    if dir_ is None and cl.get("dir"):
        d = cl["dir"]
        if os.path.isfile(os.path.join(d, exe_name)):
            dir_ = d
    if dir_ is None:
        for cand in _autodiscover():
            if os.path.isfile(os.path.join(cand, exe_name)):
                dir_ = cand
                break

    info = {"dir": dir_, "running": running, "socks_port": socks_port,
            "is_running_proc": running}
    if not dir_:
        lg.error("v2rayN не найден. Укажите client.dir в config.json")
        return info
    info["exe"] = os.path.join(dir_, exe_name)
    info["db"] = os.path.join(dir_, cl.get("db_rel", "guiConfigs/guiNDB.db"))
    info["cfg"] = os.path.join(dir_, cl.get("cfg_rel", "guiConfigs/guiNConfig.json"))
    core = os.path.join(dir_, cl.get("core_rel", "bin/xray/xray.exe"))
    info["core"] = core if os.path.isfile(core) else None
    try:
        with open(info["cfg"], encoding="utf-8") as f:
            cc = json.load(f)
        for i in cc.get("Inbound", []):
            if i.get("Protocol") in ("socks", "http") and i.get("LocalPort"):
                info["socks_port"] = int(i["LocalPort"])
                break
    except Exception:
        pass
    return info


def _proc_paths_by_name(name: str) -> list:
    """Пути запущенных процессов по имени exe (без администратора)."""
    out = []
    if os.name != "nt":
        return out
    try:
        ps = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Get-Process -Name {name} -ErrorAction SilentlyContinue | "
             "ForEach-Object { $_.Path }"],
            capture_output=True, text=True, errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in ps.stdout.splitlines():
            line = line.strip()
            if line:
                out.append(line)
    except Exception:
        pass
    return out


def _autodiscover() -> list:
    candidates = []
    if os.name != "nt":
        return candidates
    try:
        ps = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-ChildItem -Path C:\\\\,D:\\\\ -Depth 3 -Filter v2rayN.exe "
             "-ErrorAction SilentlyContinue -Force | Select-Object -ExpandProperty FullName"],
            capture_output=True, text=True, errors="replace", timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in ps.stdout.splitlines():
            line = line.strip()
            if line:
                candidates.append(os.path.dirname(line))
    except Exception:
        pass
    return candidates


def proxy_for_fetch(cfg: dict) -> list:
    """Цепочка прокси для загрузки подписок: живой socks, затем напрямую.

    Можно задать явно через ``client.fetch_proxies`` (список строк, где
    "direct" — прямое соединение).
    """
    cl = cfg.get("client", {})
    if cl.get("fetch_proxies"):
        return list(cl["fetch_proxies"])
    socks = int(cl.get("socks_port", DEFAULT_SOCKS_PORT))
    return [f"http://127.0.0.1:{socks}", "direct"]
