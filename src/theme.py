# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""Единый источник палитр: светлая и тёмная темы.

Тема нужна там, где мы рисуем сами: карточка уведомления, значок трея и цвет
состояния внутри него. Системная тема определяется автоматически:

* Windows — реестр ``HKCU\\...\\Themes\\Personalize`` → ``AppsUseLightTheme``;
* Linux   — ``gsettings get org.gnome.desktop.interface color-scheme``, затем
  ``GTK_THEME`` / ``COLORFGBG`` / ``FOREST_THEME`` как запасные пути.

Пользователь может переопределить выбор: ``ui.theme`` = ``auto`` | ``light`` |
``dark`` (и тумблер в меню трея).

Цвета состояний подобраны так, чтобы читаться и на светлой, и на тёмной панели
задач; в конце модуля есть ``contrast()`` и проверка ``state_contrasts()``,
которой подтверждается контраст по WCAG.
"""

from __future__ import annotations

import os
import sys

THEMES = ("light", "dark")

# ── палитры ─────────────────────────────────────────────────────────────────
# card_* — карточка уведомления; tray_* — значок трея; state_* — цвет состояния.
PALETTES = {
    "dark": {
        "card_bg": (32, 33, 36),
        "card_border": (60, 64, 67),
        "shadow": (0, 0, 0),
        "title": (241, 243, 244),
        "body": (189, 193, 198),
        "accent": (66, 133, 244),
        "state_ok": (57, 194, 109),
        "state_off": (150, 152, 160),
        "state_error": (229, 72, 77),
        "state_busy": (240, 196, 44),
        "tray_rim": (18, 18, 18),
        "tray_none": (120, 122, 128),
    },
    "light": {
        "card_bg": (250, 250, 250),
        "card_border": (200, 204, 208),
        "shadow": (60, 60, 60),
        "title": (24, 26, 28),
        "body": (70, 74, 78),
        "accent": (26, 115, 232),
        "state_ok": (24, 128, 56),
        "state_off": (108, 110, 116),
        "state_error": (193, 34, 33),
        "state_busy": (176, 122, 0),
        "tray_rim": (245, 246, 248),
        "tray_none": (140, 142, 148),
    },
}

# Панель задач: светлая тема Windows — светлая панель, тёмная — тёмная.
PANEL_BG = {"light": (243, 243, 243), "dark": (32, 32, 32)}

STATE_KEYS = {
    "ok": "state_ok",
    "off": "state_off",
    "error": "state_error",
    "busy": "state_busy",
    "info": "accent",
    # состояния эталона (v2ray-auto-updater): переключение, «нет данных»,
    # восстановление связи — цвета те же, что были у прежнего значка
    "switch": "state_busy",
    "noanswer": "state_busy",
    "restoring": "accent",
    "down": "state_error",
}


def contrast(rgb1, rgb2) -> float:
    """Коэффициент контраста WCAG (1..21)."""
    def lum(c):
        def ch(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])
    a, b = lum(rgb1), lum(rgb2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def relative_luminance(rgb) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


# ── определение системной темы ──────────────────────────────────────────────
def _detect_windows() -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion"
                           r"\Themes\Personalize")
        try:
            v, _t = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return "light" if int(v) == 1 else "dark"
        finally:
            winreg.CloseKey(k)
    except Exception:
        return "dark"


def _kde_globals_paths() -> list:
    """Где KDE хранит ``kdeglobals`` (пользователь, затем системный)."""
    home = os.path.expanduser("~")
    out = [os.path.join(home, ".config", "kdeglobals")]
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    if xdg:
        out.insert(0, os.path.join(xdg, "kdeglobals"))
    out += ["/etc/xdg/kdeglobals", "/usr/share/kde4/config/kdeglobals"]
    return out


def _detect_kde() -> str:
    """Тема KDE по ``kdeglobals``: ``dark``/``light`` или '' (не KDE/не прочесть).

    Признаки в порядке надёжности:
      1) ``[General] ColorScheme`` — имя схемы (``BreezeDark``, ``BreezeLight``,
         локализованные «Тёмная/Светлая»);
      2) ``[Colors:Window] BackgroundNormal`` — ФАКТИЧЕСКИЙ цвет фона окна
         (``r,g,b``): по яркости и решаем, если имя схемы ничего не говорит.

    Читается и ``kdeglobals`` из ``~/.config``, и системный: у службы HOME
    может быть свой, поэтому нужен запасной путь, а не только домашний каталог.
    """
    scheme = ""
    bg = ""
    for p in _kde_globals_paths():
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                sect = ""
                for line in f:
                    s = line.strip()
                    if s.startswith("[") and s.endswith("]"):
                        sect = s[1:-1]
                        continue
                    if "=" not in s:
                        continue
                    k, _, v = s.partition("=")
                    k = k.strip()
                    if sect == "General" and k == "ColorScheme" and not scheme:
                        scheme = v.strip().lower()
                    elif sect == "Colors:Window" and k == "BackgroundNormal" and not bg:
                        bg = v.strip()
        except Exception:
            continue
        if scheme or bg:
            break
    text = scheme.replace(" ", "")
    if "dark" in text or "тёмн" in scheme or "темн" in scheme:
        return "dark"
    if "light" in text or "светл" in scheme:
        return "light"
    if bg:
        try:
            r, g, b = [int(x) for x in bg.split(",")[:3]]
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return "dark" if lum < 128 else "light"
        except Exception:
            pass
    # KDE-сеанс, но ни схемы, ни цвета не прочли: KDE по умолчанию тёмный
    # (Breeze Dark в Plasma 6), светлая схема всегда названа явно.
    if _is_kde_session():
        return "dark"
    return ""


def _is_kde_session() -> bool:
    """Идёт ли KDE-сеанс (переменные сеанса либо живой plasmashell/kwin)."""
    if "kde" in str(os.environ.get("XDG_CURRENT_DESKTOP") or "").lower():
        return True
    if str(os.environ.get("KDE_FULL_SESSION") or "").lower() in ("true", "1"):
        return True
    try:
        import subprocess
        r = subprocess.run(["pgrep", "-x", "plasmashell"], capture_output=True,
                           text=True, timeout=3)
        return bool((r.stdout or "").strip())
    except Exception:
        return False


def _clean_env() -> dict:
    """Окружение для СИСТЕМНЫХ утилит — без переменных бандла (D2).

    Почему: собранный PyInstaller'ом ``forest-ui`` выставляет
    ``LD_LIBRARY_PATH=<распакованный каталог /tmp/_MEI…>``. Системный
    ``gsettings`` (и ``xfconf-query``, и любой glib-инструмент) грузит из этого
    каталога glib/gio и начинает отвечать ``'default'`` — то есть «тема не
    известна», хотя система тёмная. Плюс ``PYTHONHOME``/``PYTHONPATH`` бандла
    ломают чужие python-обёртки. Поэтому дочернему процессу отдаём окружение
    без них — ровно тот же приём, что ``env -u LD_LIBRARY_PATH gsettings get …``.
    """
    return {k: v for k, v in os.environ.items()
            if k not in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH")}


def _run_quiet(args: list, timeout: float = 3.0) -> str:
    """stdout внешней команды или '' (никогда не падает, окружение чистое)."""
    try:
        import subprocess
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env=_clean_env())
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _theme_in_name(name: str) -> str:
    """``dark``/``light`` по НАЗВАНИЮ темы, иначе '' (не угадываем)."""
    n = str(name or "").lower()
    if "dark" in n or "тёмн" in n or "темн" in n:
        return "dark"
    if "light" in n or "светл" in n:
        return "light"
    return ""


def _detect_gtk_ini() -> str:
    """Тема из ``gtk-3.0/settings.ini`` (работает и внутри бандла, без процессов).

    ``gtk-application-prefer-dark-theme=1`` — тёмный вариант, ``0`` — светлый.
    Это дополнительный источник после gsettings (у XFCE/LXQt gsettings молчит),
    но ДО вывода «по умолчанию тёмная»: файл есть почти всегда.
    """
    out = ""
    for base in ("gtk-3.0", "gtk-4.0"):
        try:
            p = os.path.join(os.path.expanduser("~"), ".config", base, "settings.ini")
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read().lower()
        except Exception:
            continue
        pref = ""
        name = ""
        for line in txt.splitlines():
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k == "gtk-application-prefer-dark-theme":
                pref = v
            elif k == "gtk-theme-name":
                name = v
        by_name = _theme_in_name(name)
        if by_name:
            return by_name
        if pref in ("1", "true"):
            return "dark"
        if pref in ("0", "false"):
            out = out or "light"
    return out


def _detect_xfconf() -> str:
    """Тема XFCE/LXQt через ``xfconf`` (D3): в gsettings они не пишут вовсе.

    XFCE держит оформление в канале ``xsettings`` (``/Net/ThemeName``), LXQt —
    в ``~/.config/lxqt/lxqt.conf``. ``xfconf-query`` запускается с ЧИСТЫМ
    окружением: тот же бандловый ``LD_LIBRARY_PATH``, что ломал gsettings,
    ломает и его (glib внутри).
    """
    name = ""
    if "xfce" in str(os.environ.get("XDG_CURRENT_DESKTOP") or "").lower():
        name = _run_quiet(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"])
    if not name:
        # LXQt (и XFCE без переменной сеанса): собственный файл настроек
        try:
            p = os.path.join(os.path.expanduser("~"), ".config", "lxqt", "lxqt.conf")
            with open(p, encoding="utf-8", errors="replace") as f:
                sect = ""
                for line in f:
                    s = line.strip()
                    if s.startswith("[") and s.endswith("]"):
                        sect = s[1:-1].lower()
                        continue
                    k, _, v = s.partition("=")
                    if sect == "general" and k.strip() == "theme" and v.strip():
                        name = v.strip()
                        break
        except Exception:
            pass
    if not name:
        # последний источник XFCE: имя GTK-темы в том же канале
        name = _run_quiet(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"])
    return _theme_in_name(name)


def _detect_linux() -> str:
    """Системная тема Linux. Для KDE — ИЗ KDE, а не из gsettings.

    ЖИВОЙ СЛУЧАЙ (CachyOS KDE, Plasma 6): ``gsettings get … color-scheme``
    отвечает ``'default'`` — и прежний код честно возвращал ``light``, хотя
    рабочий стол тёмный. Карточка и значок выходили БЕЛЫМИ при тёмной системе.
    GSettings — настройка GNOME; в KDE тему задаёт ``kdeglobals``
    (``[General] ColorScheme`` / ``[Colors:Window] BackgroundNormal``), поэтому
    при KDE читаем именно его, а gsettings остаётся запасным путём.

    1.3.9 (D2/D3): у бандла был ВТОРОЙ источник той же беды — переменная
    ``LD_LIBRARY_PATH`` от PyInstaller: системный gsettings грузил из неё glib и
    отвечал ``default``. Теперь дочерние процессы получают окружение без неё,
    ``default`` означает «НЕИЗВЕСТНО» (а не «светлая»), а у XFCE/LXQt тема
    читается там, где они её действительно хранят — в xfconf и lxqt.conf.
    """
    env = os.environ.get("FOREST_THEME", "").lower()
    if env in THEMES:
        return env
    kde = _detect_kde()
    if kde:
        return kde
    # XFCE/LXQt тему в gsettings не пишут — сначала их собственный источник
    xf = _detect_xfconf()
    if xf:
        return xf
    # GNOME/GTK: color-scheme. 'default' — «нет предпочтения», НЕ «светлая».
    scheme = _run_quiet(["gsettings", "get", "org.gnome.desktop.interface",
                         "color-scheme"]).strip("'").lower()
    if "dark" in scheme:
        return "dark"
    if "light" in scheme:
        return "light"
    # gsettings молчит про схему — имя GTK-темы (тоже без бандлового окружения)
    gtk = _theme_in_name(_run_quiet(
        ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]))
    if gtk:
        return gtk
    # без процессов: settings.ini (работает и в бандле, и без DBus)
    ini = _detect_gtk_ini()
    if ini:
        return ini
    for var in ("GTK_THEME", "QT_STYLE_OVERRIDE"):
        v = _theme_in_name(os.environ.get(var, ""))
        if v:
            return v
    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return "light" if bg >= 8 else "dark"
        except Exception:
            pass
    return "dark"


def detect() -> str:
    """Системная тема: 'light' | 'dark' (никогда не падает)."""
    try:
        if sys.platform.startswith("win"):
            return _detect_windows()
        return _detect_linux()
    except Exception:
        return "dark"


def choice(cfg: dict = None) -> str:
    """Тема по настройке: ``ui.theme`` = auto|light|dark."""
    try:
        v = str(((cfg or {}).get("ui") or {}).get("theme", "auto") or "auto").lower()
    except Exception:
        v = "auto"
    if v in THEMES:
        return v
    return detect()


def palette(cfg: dict = None) -> dict:
    return dict(PALETTES.get(choice(cfg), PALETTES["dark"]))


def state_color(cfg: dict, state: str):
    p = palette(cfg)
    return p.get(STATE_KEYS.get(state, "accent"), p["accent"])


def tray_ring(cfg: dict):
    p = palette(cfg)
    return p.get("tray_none", (120, 122, 128))


def state_contrasts() -> list:
    """Контраст цвета состояния к панели задач (для отчёта и тестов)."""
    out = []
    for t in THEMES:
        p = PALETTES[t]
        bg = PANEL_BG[t]
        for st, key in (("ok", "state_ok"), ("off", "state_off"),
                        ("error", "state_error"), ("busy", "state_busy")):
            out.append({"theme": t, "state": st, "color": p[key], "panel": bg,
                        "contrast": round(contrast(p[key], bg), 2)})
    return out


def min_contrast() -> float:
    return min(c["contrast"] for c in state_contrasts())


def set_windows_theme(value: str) -> bool:
    """Служебное: переключить AppsUseLightTheme (для проверок обеих тем)."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion"
                           r"\Themes\Personalize", 0, winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD,
                              1 if value == "light" else 0)
            return True
        finally:
            winreg.CloseKey(k)
    except Exception:
        return False


# ── Linux: уведомления через системную службу ───────────────────────────────
def linux_color_scheme_arg() -> str:
    """Что подставить в gsettings, если пользователь выбрал тему вручную."""
    return "prefer-dark" if choice() == "dark" else "prefer-light"
