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
# На Linux системный прокси — это настройка РАБОЧЕГО СТОЛА, и механизм у
# каждого стола свой: GNOME-подобные (GNOME/Unity/Cinnamon/MATE/Budgie/XFCE/
# UKUI/DDE) читают GSettings ``org.gnome.system.proxy``, KDE Plasma — файл
# ``kioslaverc`` (``kwriteconfig6/5``), остальные (i3, sway, openbox) общего
# механизма не имеют вовсе. Подробности — ``desktop_env.py``.
#
# ТРИ ПРАВИЛА, БЕЗ КОТОРЫХ ЭТО ЛОМАЛОСЬ (найдено на живой CachyOS, пакет 1.3.1):
#  1) стол определяется ЯВНО: раньше признаком GNOME считалось наличие
#     ``gsettings``, а он есть и на KDE (его тянет glib2) — KDE определялся как
#     GNOME, и программа каждый цикл ставила GNOME-прокси;
#  2) запись ПРОВЕРЯЕТСЯ ЧТЕНИЕМ. Системная служба работает от root без
#     сеансовой шины D-Bus: GSettings уходит в backend ``memory``, ``set``
#     возвращает код 0 и НИЧЕГО не сохраняет, а следующий процесс читает
#     исходное значение — «успех» без эффекта;
#  3) сообщение пишется ОДИН РАЗ на состояние (плюс общая дедупликация в
#     ``log.py``), а при недоступном механизме программа не пытается снова
#     каждые 30-60 с, а перепроверяет стол/сеанс раз в 10 минут.
#
# Окон/диалогов нет: в минимальной среде (сервер, WSL, контейнер) вызовы
# остаются безвредными no-op — мягкая деградация, программа работает на своём
# порту и честно пишет в журнал одну строку.
_HAS_GSETTINGS = None
_DE_CACHE = None                  # {"session", "info", "at"} — кэш «стол+сеанс»
_DE_TTL = 600.0                   # перепроверка раз в 10 минут, не каждый цикл
_NOTES = {}                       # key -> ts: «сообщить один раз»


def _note_once(key: str, level: str, msg: str, ttl: float = 1800.0) -> bool:
    """Сообщение ОДИН раз (повтор — не раньше ``ttl`` секунд). -> сказали ли."""
    now = time.time()
    prev = _NOTES.get(key)
    if prev is not None and now - prev < ttl:
        return False
    _NOTES[key] = now
    try:
        getattr(log.get_logger(), level if level in ("info", "warn", "error")
                else "info")(msg)
    except Exception:
        pass
    return True


def _reset_proxy_notes() -> None:
    """Забыть «уже сообщали» (проверки и смена состояния)."""
    _NOTES.clear()


def _has_gsettings() -> bool:
    global _HAS_GSETTINGS
    if _HAS_GSETTINGS is None:
        import shutil
        _HAS_GSETTINGS = bool(shutil.which("gsettings"))
    return bool(_HAS_GSETTINGS)


def _kwriteconfig() -> str:
    """Утилита записи настроек KDE: ``kwriteconfig6`` (Plasma 6), иначе 5."""
    import shutil
    for name in ("kwriteconfig6", "kwriteconfig5"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def desktop_context(refresh: bool = False) -> dict:
    """Кэш «рабочий стол + графический сеанс» (перепроверка раз в 10 минут)."""
    global _DE_CACHE
    now = time.time()
    if not refresh and _DE_CACHE and now - float(_DE_CACHE.get("at", 0)) < _DE_TTL:
        return _DE_CACHE
    try:
        import desktop_env as DE
        sess = DE.find_graphical_session()
        info = sess.get("desktop") or DE.detect()
        _DE_CACHE = {"session": sess, "info": info, "at": now}
    except Exception as e:                       # модуль есть всегда, но не падаем
        _DE_CACHE = {"session": {"ok": False, "reason": f"desktop_env: {e}",
                                 "prefix": [], "env": {}, "uid": None},
                     "info": {"desktop": "other", "name": "неизвестный"},
                     "at": now}
    return _DE_CACHE


def _reset_desktop_cache() -> None:
    global _DE_CACHE
    _DE_CACHE = None


def _home_of(sess: dict) -> str:
    """Домашний каталог пользователя сеанса (не наш, если работаем от root)."""
    home = (sess or {}).get("home")
    if home:
        return str(home)          # явно заданный каталог (проверки, песочница)
    uid = (sess or {}).get("uid")
    if uid is None:
        return os.path.expanduser("~")
    try:
        import pwd
        return pwd.getpwuid(int(uid)).pw_dir
    except Exception:
        return (sess.get("env") or {}).get("HOME") or os.path.expanduser("~")


def _kioslaverc_path(sess: dict = None) -> str:
    sess = sess if sess is not None else desktop_context()["session"]
    return os.path.join(_home_of(sess), ".config", "kioslaverc")


def _ini_read(path: str, section: str) -> dict:
    """Мини-чтение ini (configparser меняет регистр ключей — нам это нельзя)."""
    out, cur = {}, ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or s.startswith(";"):
                    continue
                if s.startswith("[") and s.endswith("]"):
                    cur = s[1:-1].strip()
                    continue
                if cur == section and "=" in s:
                    k, _, v = s.partition("=")
                    out[k.strip()] = v.strip()
    except Exception:
        return {}
    return out


def _ini_write_keys(path: str, section: str, pairs: dict) -> bool:
    """Записать ключи в ini БЕЗ потери остальных секций. -> получилось ли."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            try:
                import shutil as _sh
                _sh.copy2(path, path + ".bak.forest")
            except Exception:
                pass
        out, cur, written = [], "", False
        for line in lines:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                if cur == section and not written:
                    out += [f"{k}={v}" for k, v in pairs.items()]
                    written = True
                cur = s[1:-1].strip()
                out.append(line)
                continue
            if cur == section and "=" in s:
                k = s.partition("=")[0].strip()
                if k in pairs:
                    continue                     # заменим ниже одним блоком
            out.append(line)
        if cur == section and not written:
            out += [f"{k}={v}" for k, v in pairs.items()]
            written = True
        if not written:
            out += ["", f"[{section}]"] + [f"{k}={v}" for k, v in pairs.items()]
        tmp = path + ".tmp.forest"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
        return True
    except Exception as e:
        _note_once("ini-write:" + str(path), "warn", f"kioslaverc: запись {path}: {e}")
        return False


def _split_proxy(value: str) -> tuple:
    """``http://user:pass@127.0.0.1:10808/`` -> ``("127.0.0.1", "10808")``."""
    v = str(value or "").strip()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.rstrip("/")
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    host, _, port = v.rpartition(":")
    if not host:
        return v, ""
    return host, port


# ── KDE Plasma: kioslaverc ──
def _kde_apply(sess: dict, server: str) -> bool:
    """Прописать прокси в kioslaverc (KDE Plasma) и ПРОВЕРИТЬ чтением."""
    host, prt = _split_proxy(server)
    if not host:
        return False
    url = f"http://{host}:{prt}" if prt else f"http://{host}"
    pairs = {"ProxyType": "1", "httpProxy": url, "httpsProxy": url,
             "ftpProxy": url,
             "socksProxy": f"socks://{host}:{prt}" if prt else f"socks://{host}",
             "NoProxyFor": "localhost,127.0.0.1,::1"}
    ok = False
    kw = _kwriteconfig()
    if kw:
        try:
            import desktop_env as DE
            good = True
            for k, v in pairs.items():
                r = DE.run_in_session([kw, "--file", "kioslaverc", "--group",
                                       "Proxy Settings", "--key", k, str(v)],
                                      sess, timeout=10)
                good = good and getattr(r, "returncode", 1) == 0
            ok = good
        except Exception:
            ok = False
    if not ok:
        # без kwriteconfig (или он не сработал) — прямая запись файла
        ok = _ini_write_keys(_kioslaverc_path(sess), "Proxy Settings", pairs)
    if ok:
        _kde_reparse(sess)
        on, srv = _kde_read(sess)
        want = f"{host}:{prt}" if prt else host
        if not (on and srv == want):
            _note_once("kde-verify:" + want, "warn",
                       f"kioslaverc записан, но прочитался как «{srv or '—'}» "
                       f"вместо «{want}» — прокси KDE может не примениться")
            return False
    return ok


def _kde_read(sess: dict) -> tuple:
    """(включён, «host:port») по kioslaverc."""
    vals = _ini_read(_kioslaverc_path(sess), "Proxy Settings")
    if str(vals.get("ProxyType", "")).strip() != "1":
        return False, ""
    raw = (vals.get("httpProxy") or vals.get("httpsProxy")
           or vals.get("socksProxy") or "")
    host, prt = _split_proxy(raw)
    if not host:
        return False, ""
    return True, (f"{host}:{prt}" if prt else host)


def _kde_reparse(sess: dict) -> None:
    """Заставить KIO перечитать настройки (без этого часть приложений ждёт
    перезапуска). Сигнал есть в Plasma 5/6; ошибку игнорируем."""
    import shutil
    if not shutil.which("dbus-send"):
        return
    try:
        import desktop_env as DE
        DE.run_in_session(["dbus-send", "--type=signal", "/KIO/Scheduler",
                           "org.kde.KIO.Scheduler.reparseSlaveConfiguration",
                           "string:"], sess, timeout=6)
    except Exception:
        pass


def _kde_disable(sess: dict) -> bool:
    """Выключить прокси KDE (ProxyType=0)."""
    kw = _kwriteconfig()
    if kw:
        try:
            import desktop_env as DE
            r = DE.run_in_session([kw, "--file", "kioslaverc", "--group",
                                   "Proxy Settings", "--key", "ProxyType", "0"],
                                  sess, timeout=10)
            if getattr(r, "returncode", 1) == 0:
                _kde_reparse(sess)
                return True
        except Exception:
            pass
    ok = _ini_write_keys(_kioslaverc_path(sess), "Proxy Settings",
                         {"ProxyType": "0"})
    if ok:
        _kde_reparse(sess)
    return ok


# ── GNOME-подобные и всё, что читает GSettings (Chromium/Electron/GTK) ──
def _gsettings_get(sess: dict, schema: str, key: str) -> str:
    import desktop_env as DE
    try:
        r = DE.run_in_session(["gsettings", "get", schema, key], sess, timeout=8)
        return (r.stdout or "").strip().strip("'")
    except Exception:
        return ""


def _gsettings_set(sess: dict, schema: str, key: str, value) -> bool:
    import desktop_env as DE
    try:
        r = DE.run_in_session(["gsettings", "set", schema, key, str(value)],
                              sess, timeout=8)
        return getattr(r, "returncode", 1) == 0
    except Exception:
        return False


def _gs_read(sess: dict) -> tuple:
    """(включён, «host:port») по GSettings (http-прокси — то, что берёт браузер)."""
    if not _has_gsettings():
        return False, ""
    base = "org.gnome.system.proxy"
    if _gsettings_get(sess, base, "mode") != "manual":
        return False, ""
    host = prt = ""
    for proto in ("http", "https", "socks"):
        host = _gsettings_get(sess, f"{base}.{proto}", "host")
        prt = _gsettings_get(sess, f"{base}.{proto}", "port")
        if host:
            break
    return (True, f"{host}:{prt}" if host else "")


def _gs_apply(sess: dict, server: str) -> bool:
    """Поставить прокси в GSettings и ПРОВЕРИТЬ чтением (иначе «успех» врёт)."""
    if not _has_gsettings():
        return False
    host, prt = _split_proxy(server)
    if not host:
        return False
    base = "org.gnome.system.proxy"
    try:
        for proto in ("http", "https", "ftp", "socks"):
            _gsettings_set(sess, f"{base}.{proto}", "host", host)
            try:
                _gsettings_set(sess, f"{base}.{proto}", "port", int(prt or 0))
            except Exception:
                _gsettings_set(sess, f"{base}.{proto}", "port", prt)
        _gsettings_set(sess, base, "ignore-hosts",
                       "['localhost', '127.0.0.1', '::1']")
        _gsettings_set(sess, base, "mode", "manual")
    except Exception as e:
        _note_once("gs-apply", "warn", f"gsettings: {e}")
        return False
    on, srv = _gs_read(sess)
    want = f"{host}:{prt}" if prt else host
    if not (on and srv == want):
        _note_once("gs-verify:" + want, "warn",
                   f"gsettings записан, но прочитался как «{srv or '—'}» вместо "
                   f"«{want}» (нет сеансовой шины D-Bus? тогда GSettings уходит "
                   f"в backend memory и НЕ сохраняется) — системный прокси GNOME "
                   f"не применён")
        return False
    return True


def _posix_sys_proxy_supported() -> tuple:
    """(можно ли управлять прокси ОС, причина отказа). Кэш 10 минут."""
    ctx = desktop_context()
    sess, de = ctx["session"], (ctx["info"] or {}).get("desktop")
    if not sess.get("ok"):
        return False, sess.get("reason") or "нет графического сеанса"
    if de == "kde":
        return True, ""                       # kioslaverc умеем и без kwriteconfig
    if de == "gnome":
        if not _has_gsettings():
            return False, "нет gsettings (пакет glib2 не установлен)"
        return True, ""
    if _has_gsettings():                      # GTK/Chromium читают GSettings
        return True, ""
    return False, (f"рабочий стол «{(ctx['info'] or {}).get('name')}» не имеет "
                   f"общего механизма системного прокси")


def _posix_sys_proxy_state() -> tuple:
    """(включён, сервер) системного прокси — по механизму СВОЕГО стола."""
    ctx = desktop_context()
    sess, de = ctx["session"], (ctx["info"] or {}).get("desktop")
    if sess.get("ok"):
        if de == "kde":
            on, srv = _kde_read(sess)
            if on:
                return True, srv
        if _has_gsettings():
            on, srv = _gs_read(sess)
            if on:
                return True, srv
    env = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
           or os.environ.get("all_proxy") or os.environ.get("ALL_PROXY") or "")
    return bool(env), env


def _posix_sys_proxy_apply(server: str) -> bool:
    """Поставить системный прокси механизмом СВОЕГО рабочего стола.

    Возврат False не является ошибкой: на сервере/WSL прокси ОС не управляется,
    программа продолжает работать на своём порту. Каждое состояние
    сообщается в журнал ОДИН раз (без повторов каждые 30-60 с).
    """
    ok_av, why = _posix_sys_proxy_supported()
    if not ok_av:
        host, prt = _split_proxy(server)
        _note_once("proxy-unavailable", "info",
                   f"Системный прокси: не настраиваю — {why}. Задайте прокси в "
                   f"браузере вручную: {host}:{prt or '—'} (или запустите "
                   f"помощник внутри сеанса пользователя)")
        return False
    ctx = desktop_context()
    sess, de = ctx["session"], (ctx["info"] or {}).get("desktop")
    done, ok = [], False
    if de == "kde":
        if _kde_apply(sess, server):
            done.append("KDE kioslaverc")
            ok = True
        # Chromium/Electron/GTK читают GSettings даже на KDE — ставим и его
        if _gs_apply(sess, server):
            done.append("gsettings для Chromium/GTK")
            ok = True
    else:
        if _gs_apply(sess, server):
            done.append("gsettings")
            ok = True
    if ok:
        _note_once("proxy-on:" + str(server), "info",
                   f"Системный прокси {server} установлен ({', '.join(done)}; "
                   f"стол: {(ctx['info'] or {}).get('name')})")
        return True
    _note_once("proxy-fail:" + str(server), "warn",
               f"Системный прокси {server} применить не удалось "
               f"(стол: {(ctx['info'] or {}).get('name')}) — браузеру задайте "
               f"прокси вручную")
    return False


def _posix_sys_proxy_disable() -> bool:
    """Снять системный прокси механизмом своего стола."""
    ctx = desktop_context()
    sess, de = ctx["session"], (ctx["info"] or {}).get("desktop")
    ok = False
    if sess.get("ok"):
        if de == "kde":
            ok = _kde_disable(sess) or ok
        if _has_gsettings():
            ok = _gsettings_set(sess, "org.gnome.system.proxy", "mode", "none") or ok
    _note_once("proxy-off", "info", "Системный прокси снят")
    return ok


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
    "_sys_proxy_supported": _posix_sys_proxy_supported,
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
_sys_proxy_supported = _posix_sys_proxy_supported
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
