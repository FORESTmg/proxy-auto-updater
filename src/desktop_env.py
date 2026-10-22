# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""desktop_env.py — какой рабочий стол у пользователя и есть ли ЕГО сеанс.

Зачем отдельный модуль
----------------------
На Linux системный прокси — это настройка рабочего стола, и у каждого стола
свой механизм: GNOME-подобные (GNOME, Cinnamon, MATE, Unity, Budgie, XFCE,
UKUI, DDE) читают GSettings ``org.gnome.system.proxy``, KDE Plasma — файл
``kioslaverc`` (``kwriteconfig6/5``), остальные (i3, sway, голый
openbox) общего механизма не имеют вовсе.

Раньше программа считала, что «есть gsettings → это GNOME». На KDE Plasma
``gsettings`` тоже установлен (его тянет glib2), поэтому KDE определялся как
GNOME: программа каждые 30-60 с пыталась поставить GNOME-прокси, получала
«успех» и никогда не проверяла, что настройка ПРИМЕНИЛАСЬ. Плюс служба
``forest-proxy-updater`` — системная (root, без сеанса пользователя), а
GSettings без сеансовой шины D-Bus уходит в backend ``memory``: запись
возвращает код 0, никуда не сохраняется, и следующий вызов (новый процесс)
читает исходное значение — отсюда вечный цикл «включил прокси» в журнале.

Поэтому здесь две независимые вещи:

* :func:`detect` — какой стол (по ``XDG_CURRENT_DESKTOP``, ``KDE_FULL_SESSION``,
  ``DESKTOP_SESSION``, а при пустом окружении — по именам процессов
  ``kwin_wayland``/``plasmashell``/``gnome-shell``);
* :func:`find_graphical_session` — есть ли ГРАФИЧЕСКИЙ сеанс и от чьего имени
  в него писать. Системная служба работает от root, поэтому настройку надо
  применять в сеансе пользователя (``runuser -u <user>`` + его
  ``DBUS_SESSION_BUS_ADDRESS``/``XDG_RUNTIME_DIR``), иначе браузер гостя
  прокси не увидит.

Модуль чистый (stdlib) и параметризован путями/окружением, поэтому проверяется
без живого KDE — см. ``linux/posix_selftest.py``.
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess

__all__ = ["detect", "session_type", "find_graphical_session", "scan_sessions",
           "describe", "KDE_KEYS", "GNOME_KEYS"]

# Маркеры рабочего стола в переменных окружения.
KDE_KEYS = ("kde", "plasma")
GNOME_KEYS = ("gnome", "ubuntu", "unity", "cinnamon", "budgie", "pop",
              "pantheon", "elementary", "mate", "xfce", "ukui", "dde",
              "deepin", "zorin")

# Имена процессов — резерв, когда переменные окружения пусты (запуск из cron,
# из системной службы, из ssh-сессии: там XDG_CURRENT_DESKTOP не выставлен).
# ВАЖНО: имя процесса берётся из ``/proc/<pid>/comm``, а там не больше 15
# символов. Поэтому «kwin_wayland_wrapper» лежит ОБРЕЗАННЫМ до
# ``kwin_wayland_wr`` — иначе запись не совпала бы никогда.
KDE_PROCS = ("kwin_wayland", "kwin_x11", "kwin", "plasmashell",
             "plasma_session", "startplasma-wayland", "startplasma-x11")
GNOME_PROCS = ("gnome-shell", "gnome-session-binary", "cinnamon", "mate-panel",
               "xfce4-session", "budgie-panel")
# Любой графический сеанс (для поиска сеанса, а не для определения стола).
GRAPHICAL_PROCS = tuple(KDE_PROCS) + tuple(GNOME_PROCS) + (
    "Xorg", "Xwayland", "sway", "i3", "Hyprland", "weston", "labwc",
    "wayfire", "river", "openbox", "kwin_wayland_wr")

ENV_KEYS = ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION",
            "KDE_FULL_SESSION", "KDE_SESSION_VERSION",
            "GNOME_DESKTOP_SESSION_ID", "XDG_SESSION_TYPE", "WAYLAND_DISPLAY",
            "DISPLAY")

# Переменные, которые нужны дочернему процессу, чтобы попасть в сеанс.
SESSION_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                    "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
                    "XDG_SESSION_DESKTOP", "DESKTOP_SESSION", "KDE_FULL_SESSION",
                    "KDE_SESSION_VERSION", "XDG_SESSION_TYPE", "HOME",
                    "LANG", "LC_ALL", "QT_QPA_PLATFORM")


def _tokens(env: dict) -> str:
    """Все «паспортные» значения окружения одной строкой в нижнем регистре.

    Две переменные — ФЛАГИ, а не имена: ``KDE_FULL_SESSION=true`` и
    ``GNOME_DESKTOP_SESSION_ID=this-is-deprecated``. Их ЗНАЧЕНИЕ ничего не
    говорит о столе, поэтому маркер берём из ИМЕНИ (иначе KDE-сеанс с
    ``KDE_FULL_SESSION=true`` определялся как «неизвестный»).
    """
    out = []
    env = env or {}
    for k in ENV_KEYS:
        v = str(env.get(k) or "").strip()
        if v:
            out.append(v.lower())
    for flag, word in (("KDE_FULL_SESSION", "kde"),
                       ("GNOME_DESKTOP_SESSION_ID", "gnome")):
        v = str(env.get(flag) or "").strip().lower()
        if v and v not in ("0", "false", "no", "off"):
            out.append(word)
    return " ".join(out)


def detect(env: dict = None, procs=()) -> dict:
    """Определяет рабочий стол: ``{"desktop", "name", "source", "raw"}``.

    ``desktop`` — ``"kde"``, ``"gnome"`` (GNOME-подобные, читают GSettings) или
    ``"other"`` (общего механизма системного прокси нет).

    Порядок: сначала переменные окружения (их выставляет сам сеанс), затем —
    только если они пусты — имена процессов. KDE проверяется ПЕРВЫМ: в
    модифицированных сборках ``XDG_CURRENT_DESKTOP`` бывает составным
    (``KDE:GNOME``), и тогда gsettings-ветка снова «победила» бы неправильно.
    """
    env = os.environ if env is None else (env or {})
    text = _tokens(env)
    raw = "; ".join(f"{k}={env.get(k)}" for k in ENV_KEYS if env.get(k)) or "—"
    if text:
        for key in KDE_KEYS:
            if key in text:
                return {"desktop": "kde", "name": _name_of(env, "kde"),
                        "source": "env", "raw": raw}
        for key in GNOME_KEYS:
            if key in text:
                return {"desktop": "gnome", "name": _name_of(env, "gnome"),
                        "source": "env", "raw": raw}
            # подстрока в составном значении (X-Cinnamon, ubuntu:GNOME)
    procs_l = [str(p).lower() for p in (procs or ())]
    if procs_l:
        if any(p in KDE_PROCS for p in procs_l):
            return {"desktop": "kde", "name": "KDE Plasma", "source": "proc",
                    "raw": raw + " | процессы: kwin/plasmashell"}
        if any(p in GNOME_PROCS for p in procs_l):
            return {"desktop": "gnome", "name": "GNOME-подобный", "source": "proc",
                    "raw": raw + " | процессы: gnome-shell"}
    return {"desktop": "other", "name": "неизвестный рабочий стол",
            "source": "none", "raw": raw}


def _name_of(env: dict, kind: str) -> str:
    """Человеческое имя стола для журнала."""
    cur = str((env or {}).get("XDG_CURRENT_DESKTOP") or "").strip()
    ses = str((env or {}).get("XDG_SESSION_DESKTOP") or "").strip()
    name = cur or ses or ("KDE Plasma" if kind == "kde" else "GNOME")
    return name


def session_type(env: dict = None) -> str:
    """``"wayland"``, ``"x11"`` или ``""`` — по окружению/наличию сокета."""
    env = os.environ if env is None else (env or {})
    st = str(env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if st in ("wayland", "x11"):
        return st
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return ""


def describe(info: dict = None, sess: dict = None) -> str:
    """Одна строка для журнала: «KDE Plasma (wayland), сеанс пользователя forest»."""
    info = info or {}
    parts = [str(info.get("name") or "рабочий стол не определён")]
    st = session_type()
    if st:
        parts.append(st)
    if sess and sess.get("ok"):
        parts.append(f"сеанс пользователя {sess.get('user')}" if sess.get("uid") != 0
                     else "сеанс текущего пользователя")
    else:
        parts.append("сеанса нет")
    return ", ".join(parts)


# ══════════════════════════════════════ поиск графического сеанса ═══
def _read_environ(pid, proc_root: str) -> dict:
    """Окружение процесса из ``/proc/<pid>/environ`` ('' при отказе доступа)."""
    path = os.path.join(proc_root, str(pid), "environ")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return {}
    out = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        k, _, v = item.partition(b"=")
        try:
            out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
        except Exception:
            continue
    return out


def _read_comm(pid, proc_root: str) -> str:
    for name in ("comm",):
        try:
            with open(os.path.join(proc_root, str(pid), name), "r",
                      encoding="utf-8", errors="replace") as f:
                return (f.read() or "").strip()
        except Exception:
            pass
    return ""


def _uid_of(pid, proc_root: str):
    try:
        return os.stat(os.path.join(proc_root, str(pid))).st_uid
    except Exception:
        return None


def _user_name(uid) -> str:
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except Exception:
        return str(uid)


def scan_sessions(proc_root: str = "/proc", graphical_only: bool = True,
                  skip_uid: int = None) -> list:
    """Список найденных графических сеансов: ``[{pid, user, uid, comm, env}]``.

    Ищутся процессы с ``DBUS_SESSION_BUS_ADDRESS`` и дисплеем, чьё имя похоже на
    компонент рабочего стола. ``skip_uid`` — исключить (обычно 0, root).
    """
    found = []
    try:
        entries = os.listdir(proc_root)
    except Exception:
        return found
    for pid in entries:
        if not str(pid).isdigit():
            continue
        comm = _read_comm(pid, proc_root)
        if graphical_only and comm not in GRAPHICAL_PROCS:
            continue
        uid = _uid_of(pid, proc_root)
        if uid is None or (skip_uid is not None and uid == skip_uid):
            continue
        env = _read_environ(pid, proc_root)
        if not (env.get("DBUS_SESSION_BUS_ADDRESS") or env.get("XDG_RUNTIME_DIR")):
            continue
        # ДИСПЛЕЙ ОБЯЗАТЕЛЕН. Процесс сеанса без своего DISPLAY/
        # WAYLAND_DISPLAY — не тот, от чьего имени можно показать окно:
        # ``kwin_wayland`` (композитор) живёт в сеансе и имеет шину D-Bus, но
        # дисплея у него НЕТ (он его сам создаёт). Пока фильтр пропускал такие
        # процессы, помощник интерфейса поднимался с окружением композитора —
        # без DISPLAY — и честно выходил («нет графической сессии»), а значка
        # на экране не было. Живой случай: Plasma 6 на Wayland, KWin 6.
        if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
            continue
        found.append({"pid": int(pid), "uid": uid,
                      "user": env.get("USER") or _user_name(uid),
                      "comm": comm,
                      "env": {k: env.get(k, "") for k in SESSION_ENV_KEYS if env.get(k)}})
    # KDE/GNOME-сеанс важнее прочих (Xwayland и т. п.)
    found.sort(key=lambda d: (0 if d["comm"] in KDE_PROCS + GNOME_PROCS else 1,
                              -d["pid"]))
    return found


def _session_prefix(uid: int) -> list:
    """Чем запустить команду от имени пользователя сеанса (root -> user)."""
    if uid == os.getuid():
        return []
    user = _user_name(uid)
    if shutil.which("runuser"):
        return ["runuser", "-u", user, "--"]
    if shutil.which("setpriv"):
        return ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--init-groups"]
    return ["runuser", "-u", user, "--"]      # runuser есть в util-linux всегда


def find_graphical_session(env: dict = None, proc_root: str = "/proc",
                           me: int = None) -> dict:
    """Есть ли графический сеанс и как в него писать.

    Возврат: ``{"ok", "user", "uid", "env", "prefix", "reason", "desktop"}``.
    ``prefix`` — префикс команды (``runuser -u <user> --``), когда программа
    работает от root, а настройку надо применить в сеансе пользователя.
    """
    env = os.environ if env is None else (env or {})
    me = os.getuid() if me is None else int(me)
    has_bus = bool(env.get("DBUS_SESSION_BUS_ADDRESS"))
    has_display = bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))
    if has_bus and has_display:
        return {"ok": True, "user": env.get("USER") or _user_name(me), "uid": me,
                "env": {k: env.get(k, "") for k in SESSION_ENV_KEYS if env.get(k)},
                "prefix": [], "reason": "", "desktop": detect(env)}
    cands = scan_sessions(proc_root=proc_root, skip_uid=(0 if me == 0 else None))
    if me != 0:
        # не root: сеанс должен быть свой
        cands = [c for c in cands if c["uid"] == me]
    if cands:
        c = cands[0]
        return {"ok": True, "user": c["user"], "uid": c["uid"], "env": c["env"],
                "prefix": _session_prefix(c["uid"]), "reason": "",
                "desktop": detect(c["env"], procs=[c["comm"]]),
                "source_pid": c["pid"]}
    reason = ("системная служба работает от root, графического сеанса "
              "пользователя не найдено" if me == 0
              else "нет графического сеанса (DISPLAY/WAYLAND_DISPLAY и "
                   "D-Bus сеанса не заданы)")
    return {"ok": False, "user": "", "uid": None, "env": {}, "prefix": [],
            "reason": reason, "desktop": detect(env)}


def run_in_session(cmd: list, sess: dict, timeout: int = 8) -> subprocess.CompletedProcess:
    """Выполнить команду в сеансе пользователя (root -> ``runuser``, env сеанса).

    D2 (1.3.9): окружение НАСЛЕДУЕТСЯ, кроме переменных PyInstaller-бандла.
    ``LD_LIBRARY_PATH=/tmp/_MEI…`` заставлял системный ``gsettings`` грузить
    glib из распакованного бандла — он отвечал ``default`` и не видел тему, а
    настройки прокси «записывались», но не читались. ``PYTHONHOME``/``PYTHONPATH``
    бандла ломают чужие python-обёртки в сеансе. Значения из env САМОГО сеанса
    (``sess["env"]``) при этом сохраняются — это окружение рабочего стола.
    """
    full = list(sess.get("prefix") or []) + list(cmd)
    env = {k: v for k, v in os.environ.items()
           if k not in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH")}
    env.update({k: v for k, v in (sess.get("env") or {}).items() if v})
    uid = sess.get("uid")
    if uid not in (None, 0) and _safe_pw(int(uid)):
        env["HOME"] = pwd.getpwuid(int(uid)).pw_dir
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout,
                          env=env)


def _safe_pw(uid: int) -> bool:
    try:
        pwd.getpwuid(uid)
        return True
    except Exception:
        return False
