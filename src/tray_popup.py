# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""tray_popup.py — СВОЙ значок в стиле трея вместо виджета панели KDE.

Зачем это вместо плазмоида
--------------------------
Правый клик по ЛЮБОМУ виджету панели KDE Plasma забирает сама панель: в меню
приходят «Настроить виджет / Режим редактирования / Удалить виджет». Это
фундаментальное свойство панели, и ни один плазмоид его не обойдёт. Значок
StatusNotifier (``QSystemTrayIcon``) решает половину задачи, но в Plasma
правый клик по SNI-значку обрабатывает лоток, и в программу он не приходит.

Поэтому значок — СВОЁ маленькое окно, которое рисует программа:

* размер 26 px (настраивается), без рамки, всегда поверх, без фокуса;
* прижато к правому нижнему углу рабочей области — рядом с часами, с отступом;
* **правый клик** — НАШЕ меню (:class:`ui_menu.PopupMenu`) у значка;
* **левый клик** — ничего (как и просили);
* **наведение** — своя подсказка со статусом (цвет состояния + текст);
* в полноэкранных приложениях значок прячется (насколько это видно клиенту).

Где он встаёт на Wayland
------------------------
Wayland не даёт клиенту ставить окно по своим координатам, а layer-shell
(``layer-shell-qt``) — библиотека на C++/QML без привязок к Python, и её
Qt-плагин собран под системный Qt, а не под тот, что внутри нашей сборки
(несовместимый ABI приватного QtWaylandClient). Поэтому значок — обычное
X11-окно в XWayland: KWin (он же оконный менеджер XWayland) ставит его по
нашим координатам, держит поверх и не показывает в списке задач
(``_NET_WM_WINDOW_TYPE_UTILITY`` + ``skip taskbar``). Меню, подсказка и
подменю живут в том же X11-процессе — там позиционирование и захват мыши
работают так же, как на любом X11-столе, поэтому меню встаёт РОВНО у значка.

Публичный API
-------------
* :class:`IconWindow` — само окно-значок;
* :class:`PopupTrayIcon` — обёртка с API ``QSystemTrayIcon``
  (``setIcon`` / ``setToolTip`` / ``geometry`` / ``show`` / ``hide`` /
  ``showMessage`` / сигнал ``activated``): остальной код помощника не знает,
  какой значок у него внутри;
* :func:`probe` — числа для проверок и ``--tray-probe``.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

# Размеры — в логических пикселях (Qt сам умножит на масштаб экрана).
ICON_PX = 26
TIP_GAP = 8
HOVER_DELAY_MS = 260
TIP_HOLD_MS = 7000
FS_POLL_MS = 2000
# Высота панели по умолчанию — НОЛЬ: ничего не выдумываем (D6). Раньше здесь
# стояло 46 px «на случай Plasma», и на машине БЕЗ панели значок отступал на
# 46+6=52 px от края экрана, а на панели LXQt (32 px) зазор был неверным.
# Теперь высота панели берётся из ФАКТА — разницы geometry() и
# availableGeometry() (или _NET_WORKAREA); tray.panel_px остаётся ручным
# запасным значением для тех, у кого панель о себе не сообщает вовсе.
PANEL_PX = 0

# ════════════════════════ какая комбинация флагов окна работает ═══
# Комбинация подбиралась по ФАКТИЧЕСКОМУ снимку экрана, а не по ответам Qt:
# окно может быть «видимым» (isVisible=True), но X-сервер держать его
# НЕотображённым (map_state=0), и тогда на экране его нет.
#
# Проверено на KWin 6 / XWayland (CachyOS, Plasma 6):
#   * ``Qt.Tool`` + frameless + поверх + без фокуса даёт в KWin тип UTILITY и
#     ``_KDE_NET_WM_WINDOW_TYPE_OVERRIDE``: KWin такое окно не композитит
#     (isExposed=False, map_state=0) — значка на экране НЕТ;
#   * ``Qt.Window`` + frameless + поверх (+ WA_ShowWithoutActivating) KWin
#     показывает: тип NORMAL, окно mapped, isExposed=True. Фокус оно не
#     забирает: WA_ShowWithoutActivating + Qt.WindowDoesNotAcceptFocus.
# Поэтому по умолчанию — PRESET_DEFAULT (см. ниже).
#
# Переменная окружения ``FOREST_TRAY_FLAGS`` позволяет проверить другую
# комбинацию БЕЗ пересборки (диагностика: ``forest-ui --tray-demo``):
#   FOREST_TRAY_FLAGS=window_dock forest-ui --tray-icon
# Значение — имя набора из таблицы ниже (см. :func:`window_flags`).
PRESET_DEFAULT = "window"

# Имя набора -> (флаги окна, дополнительные атрибуты).
#   flags: Qt.WindowType, которые уходят в конструктор QWidget;
#   attrs: имена атрибутов QWidget.setAttribute(...), включаемых ДО show().
#     dock     — WA_X11NetWmWindowTypeDock (плавающий док, как панели Plasma);
#     nofocus  — WA_X11DoNotAcceptFocus;
#     override — WA_X11NetWmWindowTypeOverride (окно мимо оконного менеджера).
_PRESETS = {
    # Обычное окно без рамки, поверх всех, не забирает фокус (ПО УМОЛЧАНИЮ).
    "window": (
        Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        | Qt.WindowDoesNotAcceptFocus,
        ("nofocus",),
    ),
    # То же, но без флага «не принимать фокус» (на случай капризов KWin).
    "window_plain": (
        Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        (),
    ),
    # Плавающий док: так рисуются панели Plasma. Без _NET_WM_STRUT панель
    # ничего не резервирует — окно просто висит поверх у края.
    "window_dock": (
        Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        ("dock", "nofocus"),
    ),
    # Заставка: KWin показывает её без рамки и поверх.
    "splash": (
        Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        ("nofocus",),
    ),
    # Подсказка: тип TOOLTIP, оконный менеджер её не трогает.
    "tooltip": (
        Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        ("nofocus",),
    ),
    # Диалог без рамки: у KWin это обычное управляемое окно.
    "dialog": (
        Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        ("nofocus",),
    ),
    # Мимо оконного менеджера (override-redirect): X отображает окно сам.
    "bypass": (
        Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        | Qt.BypassWindowManagerHint,
        (),
    ),
    # ПРЕЖНЕЕ поведение (в KWin/XWayland не показывается) — оставлено, чтобы
    # диагностика могла воспроизвести исходную проблему.
    "tool": (
        Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint,
        (),
    ),
}

_ATTRS = {
    "dock": "WA_X11NetWmWindowTypeDock",
    "utility": "WA_X11NetWmWindowTypeUtility",
    "nofocus": "WA_X11DoNotAcceptFocus",
    # Отдельного WA_X11...Override в Qt НЕТ: override-redirect — это флаг окна
    # ``Qt.BypassWindowManagerHint`` (см. набор "bypass").
    "override": "",
}


def window_flags() -> tuple:
    """Флаги и атрибуты окна значка -> (Qt.WindowType, кортеж имён атрибутов).

    Имя набора берётся из ``FOREST_TRAY_FLAGS`` (диагностика) или равно
    :data:`PRESET_DEFAULT`. Неизвестное имя — набор по умолчанию: значок
    обязан появиться, а не упасть из-за опечатки в переменной окружения.
    """
    name = (os.environ.get("FOREST_TRAY_FLAGS") or "").strip().lower()
    flags, attrs = _PRESETS.get(name, _PRESETS[PRESET_DEFAULT])
    return flags, attrs


def apply_attrs(widget, attrs) -> None:
    """Включить атрибуты окна по именам из :data:`_ATTRS` (неизвестные — пропуск).

    ВАЖНО: ``WA_X11NetWmWindowType*`` живут на ``Qt.WidgetAttribute``, а не на
    ``Qt``: ``getattr(Qt, "WA_X11NetWmWindowTypeDock")`` даёт None, и атрибут
    молча не ставится (так наборы ``dock``/``nofocus`` раньше ничего не делали).
    """
    for name in attrs or ():
        key = _ATTRS.get(name, "")
        if not key:
            continue
        attr = getattr(Qt.WidgetAttribute, key, None)
        if attr is None:
            attr = getattr(Qt, key, None)
        if attr is not None:
            widget.setAttribute(attr, True)


def _debug_fill():
    """Цвет сплошной заливки значка для диагностики (``None`` — обычная отрисовка).

    ``FOREST_TRAY_DEBUG_FILL=#ff00aa`` — значок красится одним цветом целиком.
    Так его видно на снимке экрана по цвету: проверка «значок виден» не
    зависит ни от координат, ни от ответов Qt (которые, как показал живой
    случай, могут говорить «видимо», пока окна на экране нет).
    """
    v = (os.environ.get("FOREST_TRAY_DEBUG_FILL") or "").strip()
    if not v:
        return None
    c = QtGui.QColor(v)
    return c if c.isValid() else None


def _cfgget(cfg: dict, *path, default=None):
    cur = cfg or {}
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


# ═══════════════════════════════════════ полноэкранное приложение (X11) ═══
class _XClientMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10),
                ("l", ctypes.c_long * 5)]


class _XClientMessageEvent(ctypes.Structure):
    """``XClientMessageEvent``: так EWMH-клиент просит оконный менеджер."""
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", _XClientMessageData)]


class _X11:
    """Минимальный клиент Xlib: «активное окно развёрнуто на весь экран?».

    Нужен только для пункта «не мешать в полноэкранных приложениях». Всё
    обёрнуто в try/except: нет libX11, нет атома, нет свойства — ответ
    ``False``, значок остаётся на месте (лучше видимый значок, чем исключение).
    """

    _NET_ACTIVE_WINDOW = b"_NET_ACTIVE_WINDOW"
    _NET_WM_STATE = b"_NET_WM_STATE"
    _NET_WM_STATE_FULLSCREEN = b"_NET_WM_STATE_FULLSCREEN"

    def __init__(self):
        self.ok = False
        self._dpy = None
        self._root = 0
        self._x = None
        try:
            self._x = ctypes.CDLL("libX11.so.6")
            self._x.XOpenDisplay.restype = ctypes.c_void_p
            self._x.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self._x.XInternAtom.restype = ctypes.c_ulong
            self._x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                            ctypes.c_int]
            self._x.XDefaultRootWindow.restype = ctypes.c_ulong
            self._x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            self._x.XGetWindowProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
                ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
            self._x.XFree.argtypes = [ctypes.c_void_p]
            self._x.XGetWindowAttributes.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
            self._x.XGetWindowAttributes.restype = ctypes.c_int
            self._x.XGetAtomName.restype = ctypes.c_char_p
            self._x.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self._x.XChangeProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
            self._x.XChangeProperty.restype = ctypes.c_int
            self._x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                           ctypes.c_int, ctypes.c_long,
                                           ctypes.c_void_p]
            self._x.XSendEvent.restype = ctypes.c_int
            self._x.XFlush.argtypes = [ctypes.c_void_p]
            self._x.XFlush.restype = ctypes.c_int
            self._dpy = self._x.XOpenDisplay(None)
            if not self._dpy:
                return
            self._root = self._x.XDefaultRootWindow(self._dpy)
            self.ok = True
        except Exception:
            self.ok = False

    def _prop(self, win, name, kind="atom"):
        """Свойство окна: список атомов либо первое число (0 — нет свойства)."""
        try:
            atom = self._x.XInternAtom(self._dpy, name, 0)
            a_type = ctypes.c_ulong()
            a_fmt = ctypes.c_int()
            n_items = ctypes.c_ulong()
            b_after = ctypes.c_ulong()
            data = ctypes.POINTER(ctypes.c_ubyte)()
            rc = self._x.XGetWindowProperty(
                self._dpy, win, atom, 0, 1024, 0, 0, ctypes.byref(a_type),
                ctypes.byref(a_fmt), ctypes.byref(n_items),
                ctypes.byref(b_after), ctypes.byref(data))
            if rc != 0 or not data:
                return []
            try:
                if kind == "cardinal":
                    arr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
                    return [arr[i] for i in range(min(n_items.value, 8))]
                arr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
                return [arr[i] for i in range(min(n_items.value, 64))]
            finally:
                self._x.XFree(data)
        except Exception:
            return []

    def workarea(self):
        """``_NET_WORKAREA`` (x, y, w, h) — экран БЕЗ панелей и доков (EWMH).

        Это второй источник правды о высоте панели: Qt иногда сообщает
        availableGeometry, равный полному экрану (панель о себе не заявила), и
        тогда значок ставился по ВЫДУМАННЫМ 46 px — на панели другой высоты он
        наезжал на неё. Рабочая область от оконного менеджера учитывает настоящие
        страты (struts) панели.
        """
        if not self.ok:
            return None
        try:
            v = self._prop(self._root, "_NET_WORKAREA", "cardinal")
            if len(v) >= 4 and int(v[2]) > 100 and int(v[3]) > 100:
                return QtCore.QRect(int(v[0]), int(v[1]), int(v[2]), int(v[3]))
        except Exception:
            pass
        return None

    def active_fullscreen(self) -> bool:
        if not self.ok:
            return False
        try:
            act = self._prop(self._root, self._NET_ACTIVE_WINDOW, "cardinal")
            if not act or not act[0]:
                return False
            win = act[0]
            states = self._prop(win, self._NET_WM_STATE)
            if not states:
                return False
            fs = self._x.XInternAtom(self._dpy, self._NET_WM_STATE_FULLSCREEN, 0)
            return fs in states
        except Exception:
            return False

    # ── состояние окна, которого нет у Qt ──
    _XA_ATOM = 4
    _PROP_REPLACE = 0
    _CLIENT_MESSAGE = 33
    _SUBSTRUCTURE_REDIRECT = 1 << 20
    _SUBSTRUCTURE_NOTIFY = 1 << 19
    _STATE_ADD = 1

    def set_skip_taskbar(self, win) -> bool:
        """Добавить окну ``_NET_WM_STATE_SKIP_TASKBAR`` — кнопки в панели задач нет.

        Зачем: Qt умеет задавать только ТИП окна (``WA_X11NetWmWindowTypeDock`` и
        т. п.), а состояние ``SKIP_TASKBAR`` не выставляет. Между тем окно
        рабочего типа KWin честно ставит в панель задач — рядом со значком
        появлялась ВТОРАЯ кнопка с эмблемой программы (проверено снимком
        экрана). Порядок — как у EWMH-клиента: сначала меняем свойство
        ``_NET_WM_STATE``, затем отправляем оконному менеджеру ClientMessage
        (одного изменения свойства KWin может не заметить).

        Возврат ``True`` — состояние выставлено (или уже было).
        """
        if not self.ok or not win:
            return False
        try:
            state = self._x.XInternAtom(self._dpy, b"_NET_WM_STATE", 0)
            skip = self._x.XInternAtom(
                self._dpy, b"_NET_WM_STATE_SKIP_TASKBAR", 0)
            if not state or not skip:
                return False
            cur = [a for a in self._prop(win, b"_NET_WM_STATE") if a]
            if skip in cur:
                return True
            arr = (ctypes.c_ulong * (len(cur) + 1))(*cur, skip)
            self._x.XChangeProperty(
                self._dpy, ctypes.c_ulong(win), ctypes.c_ulong(state),
                ctypes.c_ulong(self._XA_ATOM), 32, self._PROP_REPLACE,
                ctypes.cast(arr, ctypes.POINTER(ctypes.c_ubyte)),
                len(cur) + 1)
            ev = _XClientMessageEvent()
            ev.type = self._CLIENT_MESSAGE
            ev.send_event = 1
            ev.display = self._dpy
            ev.window = int(win)
            ev.message_type = int(state)
            ev.format = 32
            ev.data.l[0] = self._STATE_ADD
            ev.data.l[1] = int(skip)
            ev.data.l[2] = 0
            ev.data.l[3] = 1            # источник: обычное приложение
            buf = ctypes.create_string_buffer(192)   # размер XEvent
            ctypes.memmove(buf, ctypes.byref(ev), ctypes.sizeof(ev))
            self._x.XSendEvent(
                self._dpy, ctypes.c_ulong(self._root), 0,
                self._SUBSTRUCTURE_REDIRECT | self._SUBSTRUCTURE_NOTIFY,
                ctypes.byref(buf))
            self._x.XFlush(self._dpy)
            return True
        except Exception:
            return False

    def map_state(self, win) -> int:
        """Отображено ли окно В X-СЕРВЕРЕ: 0=Unmapped, 1=Unviewable, 2=Viewable.

        Это независимая от Qt проверка. Окно может считаться видимым для Qt
        (``isVisible``), но X держит его Unmapped — и тогда на экране его нет.
        Смещение поля ``map_state`` в ``XWindowAttributes`` — 92 байта
        (int x,y; int w,h; int border_width; int depth; Visual*; Window root;
        int class,bit_gravity,win_gravity,backing_store; unsigned long ×2;
        Bool save_under; Colormap; Bool map_installed; int map_state).
        """
        if not self.ok or not win:
            return -1
        try:
            buf = ctypes.create_string_buffer(512)
            if not self._x.XGetWindowAttributes(self._dpy, ctypes.c_ulong(win),
                                                ctypes.byref(buf)):
                return -1
            return int.from_bytes(buf.raw[92:96], "little", signed=True)
        except Exception:
            return -1

    def win_type(self, win) -> str:
        """``_NET_WM_WINDOW_TYPE`` окна строкой ('' — свойства нет)."""
        if not self.ok or not win:
            return ""
        try:
            names = []
            for atom in self._prop(win, b"_NET_WM_WINDOW_TYPE"):
                nm = self._x.XGetAtomName(self._dpy, ctypes.c_ulong(atom))
                if nm:
                    names.append(nm.decode("utf-8", "replace")
                                 .replace("_NET_WM_WINDOW_TYPE_", "")
                                 .replace("_KDE_NET_WM_WINDOW_TYPE_", "KDE:"))
            return ",".join(names)
        except Exception:
            return ""


_X11S = None


def fullscreen_active() -> bool:
    """Есть ли сейчас полноэкранное X11-приложение (best-effort, см. docstring)."""
    global _X11S
    if _X11S is None:
        _X11S = _X11()
    return _X11S.active_fullscreen()


def hide_from_taskbar(win) -> bool:
    """Убрать окно значка из панели задач (``_NET_WM_STATE_SKIP_TASKBAR``).

    Нужно ровно потому, что значок — СВОЁ окно: без этого состояния KWin
    добавляет в панель задач кнопку с эмблемой программы, и «значков» снова
    два (значок у края + кнопка в панели). Не X11/нет Xlib — тихо ``False``.
    """
    global _X11S
    if _X11S is None:
        _X11S = _X11()
    return _X11S.set_skip_taskbar(win)


def _workarea():
    """Рабочая область от оконного менеджера (``_NET_WORKAREA``) — см. _area()."""
    global _X11S
    if _X11S is None:
        _X11S = _X11()
    try:
        return _X11S.workarea()
    except Exception:
        return None


# ═══════════════════════════════════════════════ подсказка со статусом ═══
class TipWindow(QtWidgets.QWidget):
    """Своя подсказка: заголовок + строка статуса, цвета из нашей палитры."""

    def __init__(self, cfg: dict):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint)
        self.cfg = cfg or {}
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._title = ""
        self._body = ""
        self._accent = (45, 199, 112)
        self._hide_t = QtCore.QTimer(self)
        self._hide_t.setSingleShot(True)
        self._hide_t.timeout.connect(self.hide)

    def set_text(self, title: str, body: str, accent) -> None:
        self._title = str(title or "")
        self._body = str(body or "")
        self._accent = tuple(accent or self._accent)
        self._relayout()
        self.update()

    def _fonts(self):
        f = QtGui.QFont(self.font())
        f.setPointSizeF(9.4)
        fb = QtGui.QFont(f)
        return f, fb

    def _relayout(self) -> None:
        f, _ = self._fonts()
        fm_t = QtGui.QFontMetrics(f)
        w = max(fm_t.horizontalAdvance(self._title),
                fm_t.horizontalAdvance(self._body))
        w = min(max(140, w + 34), 420)
        h = 2 * fm_t.height() + 22
        self.resize(int(w), int(h))
        self.setFont(f)

    def show_near(self, rect: QtCore.QRect) -> None:
        """Показать слева-сверху от значка (над ним, выровнено по правому краю)."""
        scr = QtGui.QGuiApplication.screenAt(rect.center()) \
            or QtGui.QGuiApplication.primaryScreen()
        area = scr.availableGeometry()
        x = rect.right() - self.width() + 1
        y = rect.top() - self.height() - TIP_GAP
        if y < area.top():
            y = rect.bottom() + TIP_GAP
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self._hide_t.start(TIP_HOLD_MS)

    def paintEvent(self, _ev) -> None:
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            r = QtCore.QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            p.setBrush(QtGui.QColor(18, 20, 24, 240))
            p.setPen(QtGui.QPen(QtGui.QColor(120, 126, 138, 190), 1.0))
            p.drawRoundedRect(r, 7.0, 7.0)
            # полоса цвета состояния слева
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(*self._accent))
            p.drawRoundedRect(QtCore.QRectF(1.0, 5.0, 3.0, self.height() - 10.0),
                              1.5, 1.5)
            f, fb = self._fonts()
            fm = QtGui.QFontMetrics(f)
            p.setFont(f)
            p.setPen(QtGui.QColor(232, 234, 238))
            p.drawText(QtCore.QRectF(12, 4, self.width() - 18, 16),
                       int(Qt.AlignLeft | Qt.AlignVCenter), self._title)
            p.setFont(fb)
            p.setPen(QtGui.QColor(168, 174, 186))
            p.drawText(QtCore.QRectF(12, 4 + fm.height() - 2, self.width() - 18,
                                     fm.height() + 2),
                       int(Qt.AlignLeft | Qt.AlignVCenter), self._body)
        finally:
            p.end()


# ═══════════════════════════════════════════════════════ окно-значок ═══
class IconWindow(QtWidgets.QWidget):
    """Значок в стиле трея: 26 px, без рамки, поверх, у правого нижнего угла."""

    right_clicked = QtCore.Signal(QtCore.QPoint)
    # все состояния индикации (status_info.STATES) + рабочие синонимы
    KINDS = ("ok", "off", "switch", "noanswer", "down", "restoring",
             "busy", "error")

    def __init__(self, cfg: dict, icon_provider=None):
        flags, attrs = window_flags()
        super().__init__(None, flags)
        self.cfg = cfg or {}
        self._icon_provider = icon_provider
        self._kind = "off"
        self._tip = ""
        self._icon = QtGui.QIcon()
        self._last_click = 0.0
        self._fs_hidden = False
        self._fs_watch = bool(_cfgget(self.cfg, "tray", "hide_fullscreen",
                                      default=True))
        px = int(_cfgget(self.cfg, "tray", "icon_px", default=ICON_PX) or ICON_PX)
        px = max(16, min(64, px))
        self.setFixedSize(px, px)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NativeWindow, True)
        # Тип окна (DOCK/OVERRIDE) и «не принимать фокус» — см. window_flags().
        apply_attrs(self, attrs)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        self.setWindowTitle("FOREST's Proxy Auto-updater")
        self.tip = TipWindow(self.cfg)
        self._fs_t = QtCore.QTimer(self)
        self._fs_t.setInterval(FS_POLL_MS)
        self._fs_t.timeout.connect(self._watch_fullscreen)
        self._place()
        self._sync_screens()

    # ── внешний вид ──
    def set_icon(self, icon: QtGui.QIcon) -> None:
        self._icon = icon
        self.update()

    def set_tip(self, text: str) -> None:
        self._tip = str(text or "")
        if self.tip.isVisible():
            self.tip.set_text(self._title(), self._tip, self.accent())

    def set_state(self, kind: str, text: str = "", icon: QtGui.QIcon = None) -> None:
        # Состояния — ровно те же шесть, что у индикации эталона (status_info):
        # ok | off | switch | noanswer | down | restoring. Раньше список был
        # урезан до ok/error/busy/off, и «связь OK» приезжала сюда как «off» —
        # значок и подсказка красились серым независимо от состояния.
        self._kind = str(kind or "off") if kind in self.KINDS else "off"
        if icon is not None:
            self._icon = icon
        self._tip = str(text or "")
        self.update()
        if self.tip.isVisible():
            self.tip.set_text(self._title(), self._tip, self.accent())

    def _title(self) -> str:
        return "FOREST's Proxy Auto-updater"

    def accent(self):
        try:
            import theme as TH
            return TH.state_color(self.cfg, self._kind)
        except Exception:
            return {"ok": (45, 199, 112), "error": (226, 74, 74),
                    "busy": (216, 160, 40)}.get(self._kind, (120, 122, 128))

    def paintEvent(self, _ev) -> None:
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            # ДИАГНОСТИКА: сплошная заливка одним цветом (``--tray-demo``).
            # Нужна, чтобы найти значок на СНИМКЕ ЭКРАНА по цвету, не полагаясь
            # на координаты и на ответы Qt. В обычной работе не включается.
            dbg = _debug_fill()
            if dbg is not None:
                p.fillRect(self.rect(), dbg)
                return
            side = min(self.width(), self.height())
            if not self._icon.isNull():
                pm = self._icon.pixmap(QtCore.QSize(side, side))
                if not pm.isNull():
                    p.drawPixmap(int((self.width() - pm.width()) / 2),
                                 int((self.height() - pm.height()) / 2), pm)
                    return
            # запасной рисунок: круг цвета состояния с ободком
            p.setBrush(QtGui.QColor(*self.accent()))
            p.setPen(QtGui.QPen(QtGui.QColor(210, 214, 220, 200), 1.4))
            p.drawEllipse(2, 2, side - 4, side - 4)
        finally:
            p.end()

    # ── место ──
    def _screen(self):
        return QtGui.QGuiApplication.primaryScreen()

    def _area(self) -> QtCore.QRect:
        """Рабочая область: сначала система, затем страты панели, затем настройка.

        Порядок важен: availableGeometry точен, когда панель о себе сообщает;
        ``_NET_WORKAREA`` (страты) — когда Qt отдаёт полный экран, а панель всё
        равно занимает место; ``tray.panel_px`` из настроек — последний резерв,
        и только если пользователь задал его САМ.

        1.3.9 (D6): раньше здесь стояло ``PANEL_PX = 46`` «по умолчанию» — на
        машине без панели (голый оконный менеджер) значок вставал с зазором
        52 px от низа экрана, а на панели LXQt 32 px зазор был неверным. Теперь
        по умолчанию НИЧЕГО не выдумываем: высоту панели берём из разницы
        ``geometry() - availableGeometry()`` (это факт от системы), а если
        источников нет — край экрана.
        """
        scr = self._screen()
        if scr is None:
            return QtCore.QRect(0, 0, 1024, 768)
        full = scr.geometry()
        avail = scr.availableGeometry()
        if avail.height() < full.height() - 2 or avail.width() < full.width() - 2:
            return avail
        try:
            wa = _workarea()
        except Exception:
            wa = None
        if wa is not None and wa.width() > 0 and wa.height() > 0 \
                and (wa.height() < full.height() - 2 or wa.width() < full.width() - 2):
            return wa
        panel = int(_cfgget(self.cfg, "tray", "panel_px", default=0) or 0)
        if panel > 0:
            return QtCore.QRect(full.left(), full.top(), full.width(),
                                max(200, full.height() - panel))
        return avail

    def target_pos(self) -> QtCore.QPoint:
        area = self._area()
        mx = int(_cfgget(self.cfg, "tray", "margin_x", default=6) or 0)
        my = int(_cfgget(self.cfg, "tray", "margin_y", default=6) or 0)
        x = area.right() - self.width() - mx + 1
        y = area.bottom() - self.height() - my + 1
        x = max(area.left(), x)
        y = max(area.top(), y)
        # D5/D6: рабочая область может оказаться БОЛЬШЕ экрана — панель сообщает
        # страту в device px, Qt пересчитывает её в логические и прибавляет к
        # y; на 150 % область выходила за низ экрана и значок обрезался.
        # Поэтому место ограничиваем ФАКТИЧЕСКИМ прямоугольником экрана.
        scr = self._screen()
        if scr is not None:
            full = scr.geometry()
            x = min(x, full.right() - self.width() + 1 - mx)
            y = min(y, full.bottom() - self.height() + 1 - my)
            x = max(full.left(), x)
            y = max(full.top(), y)
        return QtCore.QPoint(int(x), int(y))

    def _place(self) -> None:
        self.move(self.target_pos())

    def _sync_screens(self) -> None:
        app = QtGui.QGuiApplication.instance()
        if app is None:
            return
        for sig in (getattr(app, "screenAdded", None),
                    getattr(app, "primaryScreenChanged", None)):
            try:
                if sig is not None:
                    sig.connect(lambda *_: self._place())
            except Exception:
                pass
        try:
            for scr in app.screens():
                scr.geometryChanged.connect(lambda *_: self._place())
                scr.availableGeometryChanged.connect(lambda *_: self._place())
        except Exception:
            pass

    def anchor(self) -> QtCore.QPoint:
        """Точка у значка — её ждёт ``Ui._icon_anchor`` (центр значка)."""
        return self.mapToGlobal(self.rect().center())

    def geometry(self) -> QtCore.QRect:              # noqa: A003 (как у трея)
        return QtCore.QRect(self.mapToGlobal(QtCore.QPoint(0, 0)), self.size())

    # ── мышь ──
    def _emit_right(self) -> None:
        now = time.monotonic()
        if now - self._last_click < 0.35:            # press + contextMenu = один клик
            return
        self._last_click = now
        self.tip.hide()
        self.right_clicked.emit(self.anchor())

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.RightButton:
            ev.accept()
            self._emit_right()
            return
        # ЛЕВЫЙ клик — ничего (так просил пользователь)
        ev.accept()

    def contextMenuEvent(self, ev) -> None:
        ev.accept()
        self._emit_right()

    def enterEvent(self, ev) -> None:
        super().enterEvent(ev)
        QtCore.QTimer.singleShot(HOVER_DELAY_MS, self._maybe_tip)

    def _maybe_tip(self) -> None:
        if not self.underMouse() or not self.isVisible():
            return
        self.tip.set_text(self._title(), self._tip, self.accent())
        self.tip.show_near(self.geometry())

    def leaveEvent(self, ev) -> None:
        super().leaveEvent(ev)
        self.tip.hide()

    # ── показ/скрытие ──
    def show_icon(self) -> None:
        self._place()
        self.show()
        self.raise_()
        # НЕ активируем: значок не должен забирать фокус у активного окна
        # И не должен попадать в панель задач (см. hide_from_taskbar).
        try:
            hide_from_taskbar(int(self.winId()))
        except Exception:
            pass
        if self._fs_watch:
            self._fs_t.start()
            self._watch_fullscreen()

    def hide_icon(self) -> None:
        self.tip.hide()
        self._fs_t.stop()
        self.hide()

    def _watch_fullscreen(self) -> None:
        fs = fullscreen_active()
        if fs and not self._fs_hidden:
            self._fs_hidden = True
            self.tip.hide()
            self.hide()
        elif not fs and self._fs_hidden:
            self._fs_hidden = False
            self.show_icon()


# ═════════════════════════════ обёртка с API QSystemTrayIcon ═══
class PopupTrayIcon(QtCore.QObject):
    """Значок-окно с API ``QSystemTrayIcon``: помощнику всё равно, кто внутри."""

    activated = QtCore.Signal(object)

    def __init__(self, cfg: dict, icon_provider=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.win = IconWindow(self.cfg, icon_provider=icon_provider)
        self.win.right_clicked.connect(self._on_right)
        self._icon = QtGui.QIcon()
        self._tip = ""
        self._kind = "off"      # цвет значка: обновляет Ui.refresh каждые 2 с

    def _on_right(self, _pos) -> None:
        # Тот же сигнал и та же причина клика, что у QSystemTrayIcon: помощник
        # обрабатывает правый клик ОДНИМ кодом на всех платформах.
        try:
            r = QtWidgets.QSystemTrayIcon.ActivationReason.Context
        except Exception:
            r = "Context"
        self.activated.emit(r)

    # ── API ──
    def setIcon(self, icon) -> None:
        self._icon = icon
        self.win.set_icon(icon)

    def setToolTip(self, text: str) -> None:
        self._tip = str(text or "")
        self.win.set_tip(self._tip)

    def geometry(self):
        return self.win.geometry()

    def isVisible(self) -> bool:
        return self.win.isVisible()

    def show(self) -> None:
        kind = getattr(self, "_kind", "off")
        self.win.set_state(kind, self._tip, self._icon)
        self.win.show_icon()

    def hide(self) -> None:
        self.win.hide_icon()

    def showMessage(self, title: str, body: str, icon=None, msec: int = 4000) -> bool:
        """Системное уведомление через D-Bus (у значка-окна нет своего лотка)."""
        return _dbus_notify(title, body, int(msec or 4000))

    def icon_kind(self) -> str:
        return self.win._kind

    def set_state(self, kind: str, tip: str = "", icon=None) -> None:
        self._kind = str(kind or "off")
        self.win.set_state(kind, tip, icon)

    def destroy(self) -> None:
        """Снять значок СОВСЕМ (переключение режима): окно, подсказка, таймеры.

        Нужно, чтобы при переключении own <-> sni на экране не оставалось
        второго значка: hide() оставил бы живое окно, которое снова покажется
        при следующем show_icon().
        """
        w = self.win
        try:
            w.hide_icon()
        except Exception:
            pass
        try:
            w._fs_t.stop()
        except Exception:
            pass
        try:
            w.tip._hide_t.stop()
            w.tip.hide()
            w.tip.deleteLater()
        except Exception:
            pass
        try:
            w.hide()
            w.deleteLater()
        except Exception:
            pass
        self.win = None


def _dbus_notify(title: str, body: str, msec: int) -> bool:
    """org.freedesktop.Notifications через QtDBus; без QtDBus — ``gdbus``."""
    try:
        from PySide6 import QtDBus
        bus = QtDBus.QDBusConnection.sessionBus()
        if bus.isConnected():
            msg = QtDBus.QDBusMessage.createMethodCall(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify")
            args = ["forest-proxy-updater", 0, "forest-proxy-updater",
                    str(title), str(body), [], {}, msec]
            for a in args:
                msg << a
            rep = bus.call(msg, QtDBus.QDBus.BlockMode.Block,
                           int(msec) // 4 + 1000)
            return not rep.type() == QtDBus.QDBusMessage.MessageType.ErrorMessage
    except Exception:
        pass
    try:
        import shutil
        import subprocess
        if shutil.which("gdbus"):
            subprocess.run(["gdbus", "call", "--session",
                            "--dest", "org.freedesktop.Notifications",
                            "--object-path", "/org/freedesktop/Notifications",
                            "--method", "org.freedesktop.Notifications.Notify",
                            "forest-proxy-updater", "0", "forest-proxy-updater",
                            str(title), str(body), "[]", "{}", str(msec)],
                           timeout=8, capture_output=True, check=False)
            return True
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════════════ проверки ═══
def measured_panel_px(scr=None) -> int:
    """Высота панели по ФАКТУ: ``geometry() - availableGeometry()`` (D6).

    Ничего не выдумывает: если рабочий стол совпадает с экраном, панели
    «не видно» и результат — 0. Для боковой панели разница будет по ширине.
    """
    try:
        scr = scr or QtGui.QGuiApplication.primaryScreen()
        if scr is None:
            return 0
        g, a = scr.geometry(), scr.availableGeometry()
        return max(0, int(g.height() - a.height()), int(g.width() - a.width()))
    except Exception:
        return 0


def probe(cfg: dict) -> dict:
    """Числа для ``--tray-probe``: экран, место значка, полноэкранный режим."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    scr = QtGui.QGuiApplication.primaryScreen()
    win = IconWindow(cfg or {})
    cfg_panel = int(_cfgget(cfg, "tray", "panel_px", default=0) or 0)
    meas = measured_panel_px(scr)
    out = {
        "platform": QtGui.QGuiApplication.platformName(),
        "screen": str(scr.geometry()) if scr else "",
        "available": str(scr.availableGeometry()) if scr else "",
        "dpr": float(scr.devicePixelRatio()) if scr else 0.0,
        "icon_px": win.width(),
        "target": [win.target_pos().x(), win.target_pos().y()],
        "anchor": [win.target_pos().x() + win.width() // 2,
                   win.target_pos().y() + win.height() // 2],
        # D6: печатаем ИЗМЕРЕННУЮ высоту панели, а не константу 46 из кода
        "panel_px": meas if meas > 0 else cfg_panel,
        "panel_source": "availableGeometry" if meas > 0 else (
            "config" if cfg_panel > 0 else "none"),
        "x11_fullscreen_now": bool(fullscreen_active()),
        "x11_available": bool(_X11().ok),
        "screens": [str(s.geometry()) for s in app.screens()],
    }
    win.deleteLater()
    return out


def _say(text: str = "") -> None:
    """Строка диагностики в stdout (режимы --tray-probe/--tray-demo)."""
    try:
        sys.stdout.write(str(text) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def demo(cfg: dict, seconds: float = 25.0) -> int:
    """ДИАГНОСТИКА: показать окно значка и контрольное окно, ждать N секунд.

    Печатает, СЧИТАЕТ ЛИ КОМПОЗИТОР окно показанным (``isExposed``): окно может
    существовать и быть «видимым» для Qt, но не отрисоваться — тогда на экране
    его нет. Контрольное окно — обычное, НЕпрозрачное: если видно его, а значка
    нет, дело в прозрачности/отрисовке, а не в позиционировании.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = IconWindow(cfg or {})
    win.set_state("ok", "диагностика значка")
    win.show_icon()
    ctl = QtWidgets.QWidget(None, Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
    ctl.setFixedSize(60, 60)
    ctl.setStyleSheet("background:#ff00aa;")
    ctl.move(win.target_pos().x() - 80, win.target_pos().y())
    ctl.show()
    for _ in range(40):
        app.processEvents()
        time.sleep(0.05)
    h = win.windowHandle()
    xs = _X11()
    wid = int(win.winId()) if win.winId() else 0
    out = {
        "platform": QtGui.QGuiApplication.platformName(),
        "flags": (os.environ.get("FOREST_TRAY_FLAGS") or PRESET_DEFAULT),
        "icon_pos": [win.x(), win.y()],
        "icon_visible": bool(win.isVisible()),
        "icon_exposed": bool(h.isExposed()) if h else None,
        # НЕзависимая от Qt проверка: отображено ли окно в самом X-сервере.
        "x11_map_state": xs.map_state(wid),
        "x11_type": xs.win_type(wid),
        "x11_ok": bool(xs.ok),
        "ctl_pos": [ctl.x(), ctl.y()],
        "ctl_exposed": bool(ctl.windowHandle().isExposed())
        if ctl.windowHandle() else None,
        "screen": str(win._screen().name()) if win._screen() else "",
        "x11_fullscreen": bool(fullscreen_active()),
    }
    _say("tray-demo: " + repr(out))
    t0 = time.time()
    while time.time() - t0 < max(0.0, seconds):
        app.processEvents()
        time.sleep(0.05)
        if not win.isVisible() and not ctl.isVisible():
            break
    return 0


def describe_probe(p: dict) -> str:
    return ("tray-probe: платформа={platform} экран={screen} рабочая={available} "
            "масштаб={dpr:.2f} значок={icon_px}px место=({target[0]},{target[1]}) "
            "якорь=({anchor[0]},{anchor[1]}) панель={panel_px}px({panel_source}) "
            "X11={x11_available} полноэкранное_сейчас={x11_fullscreen_now}"
            .format(**p))
