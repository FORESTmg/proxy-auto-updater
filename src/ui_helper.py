# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""Помощник интерфейса: запуск, остановка и присмотр за процессом ``forest-ui``.

Помощник (Qt: значок в трее, меню, уведомления) — ОТДЕЛЬНАЯ программа. Этот
модуль — единственное место, где она запускается и останавливается, чтобы
порядок был один и тот же у ``--install``, у ``--ensure`` (задача Планировщика)
и у сторожа.

Правила
-------
* нет файла помощника — тихо ничего не делаем (программа работает со встроенным
  значком и запасными уведомлениями ``notify.py``);
* помощник уже работает (пульс ``state/ui_helper.json`` + живой pid) — второй не
  запускаем;
* запуск отвязанный: падение помощника не тянет за собой программу, а сам
  помощник не держит родителя;
* настройка ``ui.helper`` = ``auto`` (по умолчанию) | ``on`` | ``off``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

IS_WIN = sys.platform.startswith("win")
NAME_WIN = "forest-ui.exe"
NAME_POSIX = "forest-ui"
STOP_FLAG = "ui_stop.flag"
# Замок помощника: НАСТОЯЩАЯ эксклюзивная блокировка файла, держится всё время
# жизни помощника (см. instancelock.py). Решает по факту, а не по PID в пульсе:
# раньше два супервизора могли одновременно поднять по помощнику, и в трее
# оказывалось два значка.
LOCK_NAME = "forest-ui.lock"
SPAWN_LOCK_NAME = "forest-ui.ensure.lock"
# Пометка «этот процесс и есть помощник» — единое правило самоконтроля.
HELPER_ENV = "FOREST_UI"


def we_are_helper() -> bool:
    """Мы — процесс помощника? Признак окружения И (в сборке) имя своего файла.

    Одного признака мало: он приходит по наследству вместе с окружением, и
    «помощником» мог бы считать себя супервизор — тогда он никогда не поднял бы
    настоящий помощник и на рабочем столе не было бы значка. В сборке сверяем имя
    исполняемого файла (``forest-ui.exe`` / ``forest-ui``); из исходников признак
    ставит сам помощник в ``forest_ui.main``, и там доверяем ему.
    """
    if os.environ.get(HELPER_ENV) != "1":
        return False
    if not getattr(sys, "frozen", False):
        return True
    try:
        return os.path.basename(sys.executable).lower().startswith("forest-ui")
    except Exception:
        return True


def _state_dir(cfg: dict) -> str:
    try:
        return ((cfg or {}).get("paths") or {}).get("state_dir") or ""
    except Exception:
        return ""


def lock_path(cfg: dict) -> str:
    d = _state_dir(cfg)
    return os.path.join(d, LOCK_NAME) if d else ""


def spawn_lock_path(cfg: dict) -> str:
    d = _state_dir(cfg)
    return os.path.join(d, SPAWN_LOCK_NAME) if d else ""


def helper_lock_held(cfg: dict) -> bool:
    """Занят ли замок помощника живым процессом (источник истины)."""
    if we_are_helper():
        return True
    p = lock_path(cfg)
    if not p:
        return False
    try:
        import instancelock as IL
        return IL.held(p)
    except Exception:
        return False


def helper_name() -> str:
    return NAME_WIN if IS_WIN else NAME_POSIX


def _exists(p: str) -> bool:
    try:
        return bool(p) and os.path.isfile(p)
    except Exception:
        return False


def helper_path(cfg: dict) -> str:
    """Где искать помощника (первым — явный путь из конфига). -> '' если нет."""
    cfgu = (cfg or {}).get("ui") or {}
    cands = []
    for k in ("helper_exe", "helper"):
        v = str(cfgu.get(k) or "")
        if v and v.lower() not in ("auto", "on", "off", "true", "false"):
            cands.append(v)
    try:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                  helper_name()))
    except Exception:
        pass
    try:
        mp = getattr(sys, "_MEIPASS", "") or ""
        if mp:
            cands.append(os.path.join(mp, helper_name()))
    except Exception:
        pass
    try:
        root = ((cfg or {}).get("paths") or {}).get("root") or ""
        if root:
            cands.append(os.path.join(root, helper_name()))
    except Exception:
        pass
    for c in cands:
        if _exists(c):
            return c
    return ""


def dev_script() -> str:
    """Скрипт помощника для режима разработки (без собранного файла)."""
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "forest_ui.py")
    return p if _exists(p) else ""


def command(cfg: dict) -> list:
    """Команда запуска помощника. -> [] если запускать нечего."""
    exe = helper_path(cfg)
    if exe:
        return [exe]
    if os.environ.get("FOREST_UI_DEV") == "1":
        s = dev_script()
        if s:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            return [pyw if _exists(pyw) else sys.executable, s]
    return []


def mode(cfg: dict) -> str:
    v = str(((cfg or {}).get("ui") or {}).get("helper", "auto") or "auto").lower()
    return v if v in ("auto", "on", "off") else "auto"


def enabled(cfg: dict) -> bool:
    """Нужно ли держать помощник: настройка + наличие файла."""
    m = mode(cfg)
    if m == "off":
        return False
    if m == "on":
        return True
    return bool(command(cfg))


def _pid_alive(pid: int) -> bool:
    """Живой ли процесс: проверяем ЗАВЕРШЕНИЕ нулевым таймаутом, а не OpenProcess.

    Успешный ``OpenProcess`` НЕ доказывает, что процесс жив: он отвечает успехом
    и для завершённого, но ещё не освобождённого процесса, и для переиспользо-
    ванного pid. Реальный случай на живом ПК: в пульсе остался pid 31800, самого
    процесса нет, а ``OpenProcess`` давал хендл — супервизор считал помощник
    работающим и никогда его не поднимал (в трее не было значка).
    ``WaitForSingleObject(h, 0)`` различает состояния: WAIT_TIMEOUT — процесс ещё
    работает, WAIT_OBJECT_0 — уже завершился.
    """
    if pid <= 0:
        return False
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            k.OpenProcess.restype = ctypes.c_void_p
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                      wintypes.DWORD]
            k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            k.WaitForSingleObject.restype = wintypes.DWORD
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            # SYNCHRONIZE (0x00100000) нужен WaitForSingleObject: без него вызов
            # падает и «живой» процесс выглядел бы мёртвым
            h = k.OpenProcess(0x1000 | 0x00100000, False, int(pid))
            if not h:
                return False
            try:
                return k.WaitForSingleObject(h, 0) == 0x00000102   # WAIT_TIMEOUT
            finally:
                k.CloseHandle(h)
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _pid_image(pid: int) -> str:
    """Путь исполняемого файла процесса ('' — узнать не удалось)."""
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            k.OpenProcess.restype = ctypes.c_void_p
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                      wintypes.DWORD]
            k.QueryFullProcessImageNameW.argtypes = [
                ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD)]
            k.QueryFullProcessImageNameW.restype = wintypes.BOOL
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k.OpenProcess(0x1000, False, int(pid))
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return buf.value
            finally:
                k.CloseHandle(h)
        else:
            return os.path.realpath("/proc/%d/exe" % int(pid))
    except Exception:
        return ""
    return ""


def _is_our_helper(pid: int, cfg: dict) -> bool:
    """Действительно ли по этому pid работает НАШ помощник.

    Пульс мог остаться от упавшего помощника, а pid — достаться чужому процессу.
    Сверяем имя файла процесса и, если путь известен, что это ровно наш файл
    помощника. Не смогли убедиться — считаем «не наш»: исключительность держит
    ЗАМОК (``instancelock``), а не эта проверка.
    """
    img = _pid_image(pid)
    if not img:
        return False
    try:
        if os.path.basename(img).lower() != helper_name().lower():
            return False
        want = helper_path(cfg)
        if want and os.path.abspath(img).lower() != os.path.abspath(want).lower():
            return False
    except Exception:
        return False
    return True


def running_pid(cfg: dict) -> int:
    """pid работающего помощника (0 — не работает).

    Смотрим на пульс (``state/ui_helper.json``) И на живой процесс: одного
    пульса мало — файл мог остаться от упавшего помощника.
    """
    try:
        import ui_cmd as UC
        p = UC.ping_path(cfg)
        if not p or not os.path.isfile(p):
            return 0
        import json
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        pid = int(d.get("pid", 0) or 0)
        if pid == os.getpid():
            return 0
        if _pid_alive(pid) and _is_our_helper(pid, cfg):
            return pid
    except Exception:
        return 0
    return 0


def running(cfg: dict) -> bool:
    """Работает ли помощник: ЗАМОК (источник истины) либо живой пульс."""
    if we_are_helper():
        return True
    if helper_lock_held(cfg):
        return True
    return running_pid(cfg) > 0


def launch(cfg: dict, log_msg: bool = True) -> bool:
    """Запускает помощник отвязанно. -> True, если процесс стартовал."""
    if we_are_helper():
        _info("помощник интерфейса: я и есть помощник — вторую копию не запускаю")
        return True
    cmd = command(cfg)
    if not cmd:
        return False
    try:
        cwd = os.path.dirname(os.path.abspath(cmd[0]))
    except Exception:
        cwd = None
    flags = 0
    if IS_WIN:
        # своя группа процессов + не наследовать консоль и задания вызывающего:
        # помощник должен жить и после выхода того, кто его поднял
        flags = (0x00000008 | 0x02000000 | 0x00000200)   # DETACHED_PROCESS |
        # CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP
    try:
        devnull = open(os.devnull, "wb")
    except Exception:
        devnull = None
    try:
        p = subprocess.Popen(cmd, cwd=cwd or None, stdin=subprocess.DEVNULL,
                             stdout=devnull or subprocess.DEVNULL,
                             stderr=subprocess.STDOUT if devnull else subprocess.DEVNULL,
                             close_fds=True, creationflags=flags,
                             start_new_session=(not IS_WIN))
    except Exception as e:
        if log_msg:
            _warn(f"помощник интерфейса не запущен: {e}")
        return False
    finally:
        try:
            if devnull:
                devnull.close()
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < 8.0:
        if running(cfg):
            if log_msg:
                _info(f"помощник интерфейса запущен (pid={running_pid(cfg)}, "
                      f"файл {cmd[0]})")
            return True
        time.sleep(0.2)
    if log_msg:
        _warn("помощник интерфейса запущен, но пульс не появился — "
              "работаю со встроенным значком")
    return True


def stop(cfg: dict, wait_s: float = 8.0) -> bool:
    """Просит помощник выйти (флаг) и, если не вышел, завершает процесс."""
    if not running(cfg):
        _clear(cfg)
        return True
    try:
        d = ((cfg or {}).get("paths") or {}).get("state_dir") or ""
        if d:
            p = os.path.join(d, STOP_FLAG)
            with open(p, "w", encoding="utf-8") as f:
                f.write("stop")
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if not running(cfg):
            _clear(cfg)
            return True
        time.sleep(0.2)
    pid = running_pid(cfg)
    if pid:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=20)
            else:
                os.kill(pid, 15)
        except Exception as e:
            _warn(f"помощник интерфейса не остановлен: {e}")
            return False
    _clear(cfg)
    return True


def _clear(cfg: dict) -> None:
    try:
        import ui_cmd as UC
        UC.clear_ping(cfg)
        d = ((cfg or {}).get("paths") or {}).get("state_dir") or ""
        if d:
            p = os.path.join(d, STOP_FLAG)
            if os.path.isfile(p):
                os.unlink(p)
    except Exception:
        pass


def _spawn_lock(cfg: dict):
    """Короткий замок «сейчас запускаю помощника» (None — запуск уже идёт)."""
    p = spawn_lock_path(cfg)
    if not p:
        return None
    try:
        import instancelock as IL
        return IL.try_spawn_lock(p)
    except Exception:
        return None


def ensure(cfg: dict) -> bool:
    """Держит помощник живым: запускает, если его нет. Тихо, только в лог.

    Единые правила (те же, что у супервизора в ``watchdog.ensure``):

    1. живой замок помощника -> ничего не делаем (лишний вызов безвреден);
    2. этот процесс сам помощник -> вторую копию не поднимаем никогда;
    3. запуск сериализован коротким замком: два супервизора, зовущие ensure()
       одновременно, не поднимут двух помощников (раньше поднимали — отсюда
       два значка в трее).
    """
    if we_are_helper():
        return True
    if not enabled(cfg):
        return False
    if running(cfg):
        return True
    lk = _spawn_lock(cfg)
    if lk is None:
        _info("помощник интерфейса: другой запуск уже идёт — второй не поднимаю")
        return True
    try:
        if running(cfg):            # перепроверка уже под замком запуска
            return True
        return launch(cfg)
    finally:
        lk.release(unlink=False)


def restart(cfg: dict) -> bool:
    stop(cfg)
    return launch(cfg)


def status_text(cfg: dict) -> str:
    """Строка для ``--status``: что с помощником интерфейса."""
    import i18n as I
    if mode(cfg) == "off":
        return I.t("ui.status.off")
    p = helper_path(cfg)
    if not p:
        return I.t("ui.status.absent")
    pid = running_pid(cfg)
    if pid:
        return I.t("ui.status.running", pid=pid)
    return I.t("ui.status.stopped")


def _info(m: str) -> None:
    try:
        import log as LG
        LG.get_logger().info(m)
    except Exception:
        pass


def _warn(m: str) -> None:
    try:
        import log as LG
        LG.get_logger().warn(m)
    except Exception:
        pass
