# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""clientwrite_posix.py — POSIX (Linux) слой поверх общей клиентской логики.

FOREST's Proxy Auto-updater (GPL-3.0).

ИДЕЯ. В ``clientwrite.py`` лежит переносимая сердцевина (проверки связи и
скорости, чёрный список, стор серверов, генерация конфигов ядра, блокировки,
проверка нового конфига ядром ``xray run -test`` ДО остановки рабочего ядра,
замер разрыва связи, пул балансировщика, активация, гвард) и ровно два
ОС-зависимых участка: управление процессами и системный прокси. Поэтому
здесь НЕ дублируется логика: модуль импортирует ``clientwrite`` (он
импортируется на Linux без ошибок — все вызовы WinAPI находятся внутри
функций), подменяет только ОС-зависимые примитивы и раздаёт наружу тот же
API. Windows-код при этом не меняется.

ПОЧЕМУ ПОДМЕНА ЧЕРЕЗ ``clientwrite.<имя>``. Функции внутри
``clientwrite`` ищут зависимости в собственном модульном словаре, поэтому
достаточно присвоить ``clientwrite._kill_pid = _posix_kill_pid`` — и все
внутренние вызовы (stop_core -> _pid_alive/_kill_pid, ensure_balancer ->
_restart_core_timed -> stop_core/start_core, restore_system_proxy ->
_sys_proxy_disable) автоматически пойдут в POSIX-реализацию.

ЧТО ИМЕННО ПОДМЕНЯЕТСЯ
  процессы:     _pid_alive, _port_pid, _our_core_pids, _kill_pid
  порт:         ensure_port_free
  прокси:       _sys_proxy_state, _sys_proxy_apply, _sys_proxy_disable
  гео-базы:     geo_asset_dir  (+ системные каталоги Linux и каталог программы)
  режим:        _mode_of       (на Linux существует только standalone)

Что НЕ подменяется намеренно: ``start_core``/``restart_core``/
``_restart_core_timed``/``wait_and_probe``/``_validate_config``/
``_test_config_by_core``/``_curl_*``. Они запускают ядро дочерним процессом
через ``subprocess``, а ``creationflags=0`` на POSIX допустим — код работает
как есть и сохраняет «бесшовность»: валидация нового конфига до снятия
старого ядра, отсутствие перезапуска при неизменном пуле, замер разрыва
связи в миллисекундах.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time

import log

# Общая сердцевина. На Linux модуль импортируется: WinAPI вызывается только
# внутри функций (_pid_alive, _port_pid, _our_core_pids, _kill_pid,
# _sys_proxy_*), а не на уровне модуля.
import clientwrite as _cw
from clientwrite import *          # noqa: F401,F403  (полный API наружу)
from clientwrite import (          # noqa: F401  (явные имена — для статики)
    AUTO_TAG, CONFIG_TYPE, CORE_CFG_NAME, CORE_LOG_NAME, CORE_PID_NAME,
    DEFAULT_SOCKS_PORT, DEFAULT_TORRENT_PORT, LOCAL_DIRECT_DOMAINS,
    PROBE_DATA_MIN_BYTES, PROBE_DATA_URL, PROBE_URL, PROBE_URL_ALT,
    REMOVED_OUT_KEYS, RU_DIRECT_DOMAINS, RU_DIRECT_IPS, YOUTUBE_URL,
    StateStore, _build_dns, _cfg_sha, _conf_sha, _curl_metrics, _curl_speed,
    _fields_from_err, _json_safe, _pool_sig, _restart_core_timed,
    _sa_read_active_id, _sanitize_outbound, _store_load, _store_save,
    _test_config_by_core, _validate_config, _validate_new_config, _v2,
    _write_json, activate, balancer_pool, bl_active, bl_add, bl_add_pool,
    bl_filter, build_balancer_config, build_single_config, core_config_path,
    core_pid_path, ensure_balancer, ensure_system_proxy, guard_cycle,
    hotcache_switch, kill_kernels, lastgood_path, load_lastgood,
    measure_speed_kbps, port_busy, port_listening, port_works, probe_client,
    proxy_off, proxy_off_path, proxy_orig_path, read_active_id, read_db_rows,
    restart_core, restore_system_proxy, row_to_server, ru_direct_enabled,
    save_lastgood, save_proxy_orig, server_to_row, set_proxy_off, start_client,
    start_core, stop_client, stop_core, update_hotcache, wait_and_probe,
    write_active_id, write_auto_group, write_balancer_config, write_bin_config,
    write_core_config, youtube_probe,
    # новые переносимые функции (пост-валидация пула, мгновенный переотбор,
    # мульти-доменный TLS-гейт) — те же реализации, что в Windows-бэкенде:
    # они не содержат Windows-специфики (curl + TestInstance из xraytest).
    parallel_probe, post_validate_pool, attribute_bad_nodes, ban_bad_nodes,
    burst_guard, burst_trigger, cycle_running,
)

IS_LINUX = os.name != "nt"

# Имена ядра на POSIX (xray из официального релиза XTLS/Xray-core).
CORE_NAMES = ("xray", "v2ray")

# Где дистрибутивы кладут geosite.dat/geoip.dat.
SYSTEM_GEO_DIRS = (
    "/usr/share/xray", "/usr/local/share/xray", "/usr/share/v2ray",
    "/usr/local/share/v2ray", "/opt/xray/share", "/opt/xray",
    "/etc/xray", "/usr/share/Xray-core",
)


# ══════════════════════════════════════════ процессы (POSIX) ═══
def _proc_state(pid: int) -> str:
    """Состояние процесса из /proc/<pid>/stat: R/S/D/Z/T… ('' — нет процесса).

    Нужно, потому что zombie (Z) уже МЁРТВ, но os.kill(pid, 0) по нему
    успешен: без этой проверки снятое ядро считалось бы живым вечно, а его
    pid-файл — актуальным.
    """
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            data = f.read().decode("ascii", "replace")
        # поле 2 — имя в скобках, поэтому режем по ПОСЛЕДНЕЙ ")" и берём 1-е поле
        return data.rsplit(")", 1)[1].split()[0]
    except Exception:
        return ""


def _reap_pid(pid: int) -> None:
    """Забрать zombie-ребёнка (иначе он висит в таблице процессов).

    Только WNOHANG по КОНКРЕТНОМУ pid: глобальный waitpid(-1) украл бы статус
    у тестовых ядер xraytest, которые ждут своего Popen.
    """
    try:
        os.waitpid(int(pid), os.WNOHANG)
    except (ChildProcessError, OSError, ValueError, TypeError):
        pass


def _posix_pid_alive(pid: int) -> bool:
    """Жив ли процесс (POSIX-аналог WaitForSingleObject): signal 0 + /proc.

    Это горячий путь сторожевой петли, поэтому os.kill, а не запуск ps.
    Zombie считается мёртвым: он не работает и порт не держит.
    """
    try:
        pid = int(pid)
    except (ValueError, OverflowError, TypeError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                      # процесс есть, но не наш (EPERM)
    except Exception:
        return True
    st = _proc_state(pid)
    if st == "Z":
        return False
    return True


def _read_proc_exe(pid: int) -> str:
    """Имя исполняемого файла процесса по /proc (без lsof/pgrep)."""
    try:
        return os.path.basename(os.readlink(f"/proc/{int(pid)}/exe"))
    except Exception:
        return ""


def _read_proc_cmdline(pid: int) -> str:
    """Командная строка процесса (NUL -> пробел), '' если недоступна."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return f.read().decode("utf-8", "replace").replace("\0", " ").strip()
    except Exception:
        return ""


def _posix_port_pid(port: int):
    """PID процесса, слушающего TCP-порт.

    Порядок: /proc/net/tcp -> inode -> /proc/<pid>/fd (без подпроцессов),
    затем ``ss``, затем ``lsof``. Разбор /proc не требует установленных
    утилит; ``ss -p`` нужен там, где сокет принадлежит другому пользователю.
    """
    want = int(port)
    inode = None
    for proc_net in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_net, encoding="ascii", errors="replace") as f:
                next(f, None)
                for line in f:
                    cols = line.split()
                    if len(cols) < 10 or cols[3] != "0A":    # 0A = LISTEN
                        continue
                    try:
                        if int(cols[1].rsplit(":", 1)[1], 16) == want:
                            inode = cols[9]
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        if inode:
            break
    if inode and inode != "0":
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                fd_dir = f"/proc/{entry}/fd"
                try:
                    fds = os.listdir(fd_dir)
                except Exception:
                    continue
                for fd in fds:
                    try:
                        if os.readlink(os.path.join(fd_dir, fd)) == f"socket:[{inode}]":
                            return entry
                    except Exception:
                        continue
        except Exception:
            pass
    for cmd in (["ss", "-lptnH", f"sport = :{want}"], ["lsof", "-ti", f":{want}"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        except Exception:
            continue
        for line in (r.stdout or "").splitlines():
            if "pid=" in line:
                seg = line.split("pid=")[1].split(",")[0].strip()
                if seg.isdigit():
                    return seg
            if line.strip().isdigit():
                return line.strip()
    return None


def _posix_our_core_pids(cfg: dict) -> list:
    """Наши живые ядра xray: имя процесса xray И наш каталог в командной строке.

    Только чтение /proc: PowerShell/CIM на Linux нет, а ``pgrep -f`` дал бы
    ложные совпадения. Фильтр по имени процесса гарантирует, что чужое ядро
    (например, из пакета дистрибутива) снято не будет.
    """
    if not cfg:
        return []
    try:
        marker = os.path.join(cfg["paths"]["state_dir"], "")
    except Exception:
        return []
    if not marker:
        return []
    out = []
    try:
        entries = os.listdir("/proc")
    except Exception:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        if _read_proc_exe(int(entry)) not in CORE_NAMES:
            continue
        if marker in _read_proc_cmdline(int(entry)):
            out.append(int(entry))
    return out


def _posix_kill_pid(pid, grace: float = 1.5) -> bool:
    """Завершить процесс: SIGTERM, при необходимости SIGKILL, затем забрать zombie.

    xray корректно закрывает сокеты по SIGTERM, поэтому мягкое завершение
    быстрее освобождает клиентский порт, чем немедленный SIGKILL — именно на
    этом держится короткий разрыв связи при переключении пула.
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _reap_pid(pid)
        return True
    except Exception:
        return False
    deadline = time.time() + max(0.0, grace)
    while time.time() < deadline:
        if not _posix_pid_alive(pid):
            _reap_pid(pid)
            return True
        time.sleep(0.02)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        _reap_pid(pid)
        return True
    except Exception:
        return False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not _posix_pid_alive(pid):
            _reap_pid(pid)
            return True
        time.sleep(0.02)
    alive = _posix_pid_alive(pid)
    if not alive:
        _reap_pid(pid)
    return not alive


# ══════════════════════════════════════════ гео-базы ═══
def _posix_geo_asset_dir(core: str = None, cfg: dict = None) -> str:
    """Каталог с geosite.dat/geoip.dat.

    Порядок: кэш состояния -> XRAY_LOCATION_ASSET -> каталог ядра и его
    подкаталоги (bin/xray рядом с программой — так собран бандл) -> каталог
    установки программы -> системные каталоги дистрибутива.
    """
    if cfg is not None:
        try:
            cached = StateStore(cfg).data.get("geo_asset_dir") or ""
            if cached and (os.path.isfile(os.path.join(cached, "geoip.dat"))
                           or os.path.isfile(os.path.join(cached, "geosite.dat"))):
                return cached
        except Exception:
            pass
    cands = []
    env = os.environ.get("XRAY_LOCATION_ASSET")
    if env:
        cands.append(env)
    if core:
        d = os.path.dirname(os.path.abspath(core))
        for _ in range(3):
            if d and d not in cands:
                cands.append(d)
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    roots = []
    if cfg is not None:
        try:
            roots.append((cfg.get("paths") or {}).get("root"))
        except Exception:
            pass
    roots.append(os.path.dirname(os.path.abspath(__file__)))
    for root in roots:
        if not root:
            continue
        for sub in ("", "bin", os.path.join("bin", "xray")):
            cand = os.path.join(root, sub) if sub else root
            if cand not in cands:
                cands.append(cand)
    for d in SYSTEM_GEO_DIRS:
        if d not in cands:
            cands.append(d)
    for d in cands:
        if d and (os.path.isfile(os.path.join(d, "geosite.dat"))
                  or os.path.isfile(os.path.join(d, "geoip.dat"))):
            if cfg is not None:
                try:
                    st = StateStore(cfg)
                    st.data["geo_asset_dir"] = d
                    st.save()
                except Exception:
                    pass
            return d
    return os.path.dirname(os.path.abspath(core)) if core else ""


# ══════════════════════════════════════════ системный прокси (POSIX) ═══
# На Linux системный прокси — это настройка рабочего стола (GNOME/GSettings)
# либо переменные окружения. Окон/диалогов нет: при отсутствии gsettings
# вызовы становятся безвредными no-op, чтобы программа работала и в
# минимальной среде (сервер, WSL, контейнер) — мягкая деградация.
_HAS_GSETTINGS = None


def _has_gsettings() -> bool:
    global _HAS_GSETTINGS
    if _HAS_GSETTINGS is None:
        import shutil
        _HAS_GSETTINGS = bool(shutil.which("gsettings"))
    return bool(_HAS_GSETTINGS)


def _gsettings_get(schema: str, key: str) -> str:
    try:
        r = subprocess.run(["gsettings", "get", schema, key],
                           capture_output=True, text=True, timeout=8)
        return (r.stdout or "").strip().strip("'")
    except Exception:
        return ""


def _gsettings_set(schema: str, key: str, value) -> bool:
    try:
        r = subprocess.run(["gsettings", "set", schema, key, str(value)],
                           capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def _posix_sys_proxy_state() -> tuple:
    """(включён, сервер) системного прокси: GSettings, иначе переменные среды."""
    if _has_gsettings():
        mode = _gsettings_get("org.gnome.system.proxy", "mode")
        host = _gsettings_get("org.gnome.system.proxy.socks", "host")
        prt = _gsettings_get("org.gnome.system.proxy.socks", "port")
        if mode == "manual":
            return True, (f"{host}:{prt}" if host else "")
        return False, ""
    env = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
           or os.environ.get("all_proxy") or os.environ.get("ALL_PROXY") or "")
    return bool(env), env


def _posix_sys_proxy_apply(server: str) -> bool:
    """Поставить системный прокси (GNOME/GSettings), иначе мягкий отказ.

    Возврат False не является ошибкой: на сервере/WSL прокси ОС не
    управляется, программа продолжает работать на своём порту.
    """
    lg = log.get_logger()
    if not _has_gsettings():
        lg.info("Системный прокси: gsettings недоступен (нет рабочего стола GNOME) — "
                "управление прокси ОС пропущено, трафик идёт через порт программы")
        return False
    try:
        host, _, prt = str(server).rpartition(":")
        if not host:
            host, prt = "127.0.0.1", str(server)
        _gsettings_set("org.gnome.system.proxy.socks", "host", host)
        try:
            _gsettings_set("org.gnome.system.proxy.socks", "port", int(prt))
        except Exception:
            _gsettings_set("org.gnome.system.proxy.socks", "port", prt)
        ok = _gsettings_set("org.gnome.system.proxy", "mode", "manual")
        if ok:
            lg.warn(f"Системный прокси (GNOME) установлен на {server}")
        return ok
    except Exception as e:
        lg.warn(f"системный прокси (GNOME): {e}")
        return False


def _posix_sys_proxy_disable() -> bool:
    """Снять системный прокси (GNOME/GSettings)."""
    if not _has_gsettings():
        return False
    try:
        return _gsettings_set("org.gnome.system.proxy", "mode", "none")
    except Exception:
        return False


# ══════════════════════════════════════════ режим и порт ═══
def _posix_mode_of(client: dict = None, cfg: dict = None) -> str:
    """На Linux существует только режим standalone.

    ``v2rayn`` — работа через базу и GUI Windows-приложения; на Linux он
    неприменим, поэтому диспетчер принудительно уходит в standalone-ветку
    (иначе вызовы попали бы в код, зависящий от v2rayN).
    """
    return "standalone"


def _posix_ensure_port_free(client: dict, cfg: dict) -> bool:
    """Порт занят нашим ядром — норма (это рестарт), чужим — предупреждаем."""
    lg = log.get_logger()
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    if not port_listening(port):
        return True
    pid = _posix_port_pid(port)
    try:
        ours = _cw._read_core_pid(cfg)
    except Exception:
        ours = None
    if pid and ours and str(pid) == str(ours):
        return True
    if pid and str(pid).isdigit() and pid and _read_proc_exe(int(pid)) in CORE_NAMES:
        return True
    lg.warn(f"порт {port} занят процессом pid={pid} (не нашим) — не трогаю; "
            f"смените client.socks_port в config.json, если он конфликтует")
    return True


# ══════════════════════════════════════════ патч общей сердцевины ═══
_PATCHES = {
    "_pid_alive": _posix_pid_alive,
    "_port_pid": _posix_port_pid,
    "_our_core_pids": _posix_our_core_pids,
    "_kill_pid": _posix_kill_pid,
    "geo_asset_dir": _posix_geo_asset_dir,
    "_sys_proxy_state": _posix_sys_proxy_state,
    "_sys_proxy_apply": _posix_sys_proxy_apply,
    "_sys_proxy_disable": _posix_sys_proxy_disable,
    "_mode_of": _posix_mode_of,
    "ensure_port_free": _posix_ensure_port_free,
}


def apply_patches() -> None:
    """Вживить POSIX-примитивы в общий модуль (идемпотентно).

    На Windows подмена НЕ выполняется: модуль существует ради единого API
    (его проверяет ``--selftest``), но заменить Windows-реализацию процессов
    и системного прокси на POSIX нельзя ни при каких обстоятельствах.
    """
    if os.name == "nt":
        log.get_logger().warn("clientwrite_posix импортирован на Windows — "
                              "подмена примитивов пропущена (Windows-путь цел)")
        return
    for name, fn in _PATCHES.items():
        setattr(_cw, name, fn)


apply_patches()

# Имена, видные наружу именно как POSIX-версии (``from clientwrite import *``
# выше скопировал ссылки ДО патча, поэтому переназначаем явно).
_pid_alive = _posix_pid_alive
_port_pid = _posix_port_pid
_our_core_pids = _posix_our_core_pids
_kill_pid = _posix_kill_pid
geo_asset_dir = _posix_geo_asset_dir
_sys_proxy_state = _posix_sys_proxy_state
_sys_proxy_apply = _posix_sys_proxy_apply
_sys_proxy_disable = _posix_sys_proxy_disable
_mode_of = _posix_mode_of
ensure_port_free = _posix_ensure_port_free


# ЗЕРКАЛО НС. ``from clientwrite import *`` не переносит имена с подчёркиванием
# (``_pid_alive``, ``_sa_read_active_id``, ``_cfg_sha``, ``_is_v2rayn_path``…),
# а вызывающие модули (watch.py, selftest.py, v2ray_updater.py) ими пользуются.
# Копируем ОСТАВШИЕСЯ имена из общего модуля — уже после патча, поэтому здесь
# оказываются именно POSIX-версии примитивов.
def mirror_module() -> int:
    n = 0
    for name, value in list(vars(_cw).items()):
        if name.startswith("__") or name in globals():
            continue
        globals()[name] = value
        n += 1
    return n


mirror_module()


# ══════════════════════════════════════════ вспомогательное для индикации ═══
def systemd_available() -> bool:
    """Работает ли systemd (иначе автозапуск через юниты недоступен)."""
    try:
        return os.path.isdir("/run/systemd/system")
    except Exception:
        return False


def service_active(name: str = "forest-proxy-updater") -> str:
    """Состояние systemd --user юнита: active/inactive/failed/unknown."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", name],
                           capture_output=True, text=True, timeout=8)
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def tcp_open(host: str = "127.0.0.1", port: int = 0, timeout: float = 0.4) -> bool:
    """Быстрая проверка «в порт можно соединиться» (для индикатора состояния)."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False
