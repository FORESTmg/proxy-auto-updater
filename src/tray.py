# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Настоящий значок в системном трее Windows на ЧИСТОМ Python (ctypes).

Без сторонних пакетов и без консольных окон: всё, что нужно, есть в stdlib и
в user32/shell32/gdi32. Основные элементы:

  * скрытое окно-приёмник (RegisterClassW + CreateWindowExW) — Shell_NotifyIconW
    требует HWND для приёма сообщений мыши и перерисовки значков;
  * значок — ``Shell_NotifyIconW`` с ``NIM_ADD`` / ``NIM_MODIFY`` / ``NIM_DELETE``;
  * контекстное меню — ``CreatePopupMenu`` + ``AppendMenuW`` + ``TrackPopupMenu``
    (``TPM_RETURNCMD``), обработка кодов команд;
  * три состояния значка (зелёный / серый / красный) — иконки генерируются
    программно в :mod:`tray_icon` и превращаются в HICON прямо в памяти;
  * состояние читается из уже существующего канала ``state/tray_status.json``,
    команды передаются ТЕМИ ЖЕ механизмами, что и в проекте (см. :mod:`tray_menu`).

Использование::

    import tray
    tray.run_tray(cfg, stop_event)        # поток супервизора
    python src/tray.py --test             # проверка: значок на 5 с, 3 состояния
    python src/tray.py --run              # штатный запуск (обычно в супервизоре)
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n as I
import log
import tray_icon as TI
import tray_menu as TM

IS_WIN = sys.platform == "win32"


def _notify_details(cfg: dict, ev: str) -> tuple:
    """Этапы с временами и итоговая строка для уведомления (запасной путь).

    Берём из состояния сторожа (``state/tray_status.json``): список этапов
    процесса С ВРЕМЕНЕМ КАЖДОГО В МИЛЛИСЕКУНДАХ и время восстановления связи.
    Без этого запасной путь (без Qt) показывал только текст события — а
    пользователь просил тот же состав, что и в карточке помощника.
    -> (список строк этапов, строка футера)
    """
    try:
        import status_info as SI
        import tray_status as TS
        st = TS.read_status(cfg) or {}
    except Exception:
        return ([], "")
    proc = st.get("proc") or {}
    if not proc or str(proc.get("id") or "") == "":
        # процесса нет — показываем время восстановления, если оно известно
        if ev in ("restored", "on"):
            txt = SI.recovery_text(st)
            return ([], txt)
        return ([], "")
    n = SI.notif(proc)
    lines = []
    for s in SI.steps_data(proc):
        mark = "✗" if s["failed"] else ("✓" if s["done"] else "·")
        lines.append(f"{mark} {s['nm']}   {s['ms']} мс")
    if n["failed"]:
        foot = n["footer"]
    elif n["final"]:
        foot = SI.recovery_ms_text(proc) + " · окно закроется через 4 с"
    else:
        foot = n["footer"]
    return (lines, foot)


# ─────────────────────────────────────────────────────── константы Win32 ───
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
MF_UNCHECKED = 0x00000000
MF_SEPARATOR = 0x00000800
MF_POPUP = 0x00000010

TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040


# ───────────────────────────────────────────────────── типы и структуры ───
if IS_WIN:
    HWND = ctypes.c_void_p
    HICON = ctypes.c_void_p
    HMENU = ctypes.c_void_p
    HINSTANCE = ctypes.c_void_p
    UINT = ctypes.c_uint
    DWORD = ctypes.c_uint32
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    LRESULT = ctypes.c_ssize_t
    BOOL = ctypes.c_int

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", HWND), ("message", UINT), ("wParam", WPARAM),
                    ("lParam", LPARAM), ("time", DWORD), ("pt", POINT),
                    ("lPrivate", DWORD)]

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", HINSTANCE), ("hIcon", HICON),
                    ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p)]

    # NOTIFYICONDATAW (V3): поля до szTip включительно совпадают во всех версиях,
    # хвост (guidItem/hBalloonIcon) нужен только чтобы cbSize совпал с родным.
    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", DWORD),
            ("hWnd", HWND),
            ("uID", UINT),
            ("uFlags", UINT),
            ("uCallbackMessage", UINT),
            ("hIcon", HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", DWORD),
            ("dwStateMask", DWORD),
            ("szInfo", ctypes.c_wchar * 256),
            ("uVersion", UINT),
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", HICON),
        ]


def _bind():
    """Загружает функции Win32 и задаёт их типы (только Windows)."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    s = ctypes.WinDLL("shell32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)

    u.RegisterClassW.restype = ctypes.c_ushort
    u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u.UnregisterClassW.restype = BOOL
    u.UnregisterClassW.argtypes = [ctypes.c_wchar_p, HINSTANCE]
    u.CreateWindowExW.restype = HWND
    u.CreateWindowExW.argtypes = [DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                  DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, HWND, HMENU,
                                  HINSTANCE, ctypes.c_void_p]
    u.DefWindowProcW.restype = LRESULT
    u.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    u.DestroyWindow.restype = BOOL
    u.DestroyWindow.argtypes = [HWND]
    u.GetMessageW.restype = BOOL
    u.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
    u.TranslateMessage.restype = BOOL
    u.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    u.DispatchMessageW.restype = LRESULT
    u.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    u.PostQuitMessage.restype = None
    u.PostQuitMessage.argtypes = [ctypes.c_int]
    u.PostMessageW.restype = BOOL
    u.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    u.SetTimer.restype = ctypes.c_size_t
    u.SetTimer.argtypes = [HWND, ctypes.c_size_t, UINT, ctypes.c_void_p]
    u.KillTimer.restype = BOOL
    u.KillTimer.argtypes = [HWND, ctypes.c_size_t]
    u.CreatePopupMenu.restype = HMENU
    u.CreatePopupMenu.argtypes = []
    u.AppendMenuW.restype = BOOL
    u.AppendMenuW.argtypes = [HMENU, UINT, ctypes.c_size_t, ctypes.c_wchar_p]
    u.DestroyMenu.restype = BOOL
    u.DestroyMenu.argtypes = [HMENU]
    u.TrackPopupMenu.restype = ctypes.c_int
    u.TrackPopupMenu.argtypes = [HMENU, UINT, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, HWND, ctypes.c_void_p]
    u.SetForegroundWindow.restype = BOOL
    u.SetForegroundWindow.argtypes = [HWND]
    u.GetCursorPos.restype = BOOL
    u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u.MessageBoxW.restype = ctypes.c_int
    u.MessageBoxW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_wchar_p, UINT]
    u.RegisterWindowMessageW.restype = UINT
    u.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]

    s.Shell_NotifyIconW.restype = BOOL
    s.Shell_NotifyIconW.argtypes = [DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

    k.GetModuleHandleW.restype = HINSTANCE
    k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    return u, s, k


class _TrayHost:
    """Значок трея: скрытое окно + сообщения + контекстное меню."""

    UID = 1
    TIMER_ID = 1

    def __init__(self, actions: "TM.TrayActions", poll_s: float = 2.0,
                 stop_event=None, log_tag: str = "трей"):
        self.actions = actions
        self.poll_s = max(0.3, float(poll_s or 2.0))
        self.stop_event = stop_event
        self.tag = log_tag
        self.lg = log.get_logger()
        self.u, self.s, self.k = _bind()
        self.hwnd = None
        self.hinstance = None
        self.class_name = f"ForestTrayWnd_{os.getpid()}_{id(self)}"
        self.wm_taskbar_created = 0
        self.hicons = {}
        self._state = None
        self._wp = None            # держим ссылку на WndProc (иначе GC убьёт окно)
        self._quitting = False
        self._timer = None
        self._on_test_done = None
        # для --test
        self._test_until = None
        self._test_steps = None
        self._test_start = None
        self._test_i = 0
        self._theme_cur = None

    # ────────────────────────────────────────── уведомления и тема ───
    def _setup_services(self) -> None:
        """Настройка уведомлений и запоминание текущей темы."""
        cfg = getattr(self.actions, "cfg", None) or {}
        try:
            import notify as N
            N.configure(cfg)
            self.lg.info(f"{self.tag}: уведомления "
                         f"{'включены' if N.enabled() else 'выключены'}")
        except Exception as e:
            self.lg.warn(f"{self.tag}: уведомления не настроены: {e}")
        try:
            import theme
            self._theme_cur = theme.choice(cfg)
            self.lg.info(f"{self.tag}: тема оформления: {self._theme_cur}; "
                         f"минимальный контраст цветов состояния к панели: "
                         f"{theme.min_contrast():.2f}")
        except Exception as e:
            self.lg.warn(f"{self.tag}: тема не определена: {e}")

    def _reload_theme_if_needed(self) -> None:
        """Тема сменилась (в меню трея или в системе) — пересобрать значки."""
        try:
            import theme
            now = theme.choice(getattr(self.actions, "cfg", None) or {})
        except Exception:
            return
        if not self._theme_cur or now == self._theme_cur:
            return
        self._theme_cur = now
        try:
            for h in list(self.hicons.values()):
                TI.destroy_hicon(h)
            self.hicons = {}
            self._make_hicons()
            self._modify_icon()
            self.lg.info(f"{self.tag}: тема -> {now}, значки пересобраны")
        except Exception as e:
            self.lg.warn(f"{self.tag}: пересборка значков после смены темы: {e}")

    def _notify_transition(self, prev, cur) -> None:
        """Уведомляем ТОЛЬКО о значимом: обрыв, восстановление, ручное
        выключение, критическая ошибка. Рутина обновления пула молчит."""
        if prev is None or prev == cur:
            return
        ev = None
        if cur == "error" and prev != "error":
            ev = "lost"
        elif cur == "ok" and prev == "error":
            ev = "restored"
        elif cur == "off" and prev != "off":
            ev = "off"
        elif cur == "ok" and prev == "off":
            ev = "on"
        elif cur == "busy" and prev == "ok":
            ev = "switched"
        if not ev:
            return
        # Уведомления о переходах показывает тот, у кого интерфейс: если работает
        # помощник (forest-ui), он и показывает — иначе пользователь получил бы
        # два уведомления об одном и том же событии.
        try:
            import ui_helper as UH
            if UH.running(getattr(self.actions, "cfg", None) or {}):
                self.lg.info(f"{self.tag}: уведомление о «{ev}» показывает "
                             f"помощник интерфейса")
                return
        except Exception:
            pass
        try:
            import notify as N
            steps, foot = _notify_details(getattr(self.actions, "cfg", None) or {},
                                          ev)
            N.notify_event(ev, getattr(self.actions, "cfg", None),
                           steps=steps, footer=foot)
            self.lg.info(f"{self.tag}: уведомление о событии «{ev}» "
                         f"({prev} -> {cur}); этапов: {len(steps)}")
        except Exception as e:
            self.lg.warn(f"{self.tag}: уведомление ({ev}): {e}")

    # ────────────────────────────────────────────── HICON-ы ───
    def _make_hicons(self):
        icos = TI.all_state_icos(cfg=getattr(self.actions, "cfg", None))
        for name, data in icos.items():
            h, err = TI.make_hicon(data, 16)
            if not h:
                raise RuntimeError(f"иконка {name}: {err}")
            self.hicons[name] = h

    # ────────────────────────────────────────────── окно ───
    def _create_window(self):
        self.hinstance = self.k.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wp
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self.hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self.class_name
        if not self.u.RegisterClassW(ctypes.byref(wc)):
            raise RuntimeError(f"RegisterClassW: ошибка {ctypes.get_last_error()}")
        # обычное (не message-only) окно, НИКОГДА не показывается: получает и
        # широковещательные сообщения оболочки (TaskbarCreated) тоже
        ctypes.set_last_error(0)
        self.hwnd = self.u.CreateWindowExW(
            0, self.class_name, "FOREST Tray", 0,
            0, 0, 0, 0, None, None, self.hinstance, None)
        if not self.hwnd:
            raise RuntimeError(f"CreateWindowExW: ошибка {ctypes.get_last_error()}")
        self.wm_taskbar_created = self.u.RegisterWindowMessageW("TaskbarCreated")

    # ────────────────────────────────────────────── значок ───
    def _nid(self, state: str):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.UID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicons.get(state) or 0
        nid.szTip = self.actions.tip()[:127]
        return nid

    def _add_icon(self):
        state = self.actions.icon_state()
        nid = self._nid(state)
        ctypes.set_last_error(0)
        ok = bool(self.s.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        err = ctypes.get_last_error()
        self._state = state
        self.lg.info(f"{self.tag}: NIM_ADD state={state} -> "
                     f"{'OK' if ok else 'FAIL err=' + str(err)}")
        return ok, err

    def _modify_icon(self):
        state = self.actions.icon_state()
        nid = self._nid(state)
        ctypes.set_last_error(0)
        ok = bool(self.s.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        err = ctypes.get_last_error()
        changed = state != self._state
        self._state = state
        return ok, err, changed

    def _delete_icon(self):
        nid = self._nid(self._state or "off")
        ctypes.set_last_error(0)
        ok = bool(self.s.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid)))
        err = ctypes.get_last_error()
        self.lg.info(f"{self.tag}: NIM_DELETE -> {'OK' if ok else 'FAIL err=' + str(err)}")
        return ok, err

    # ────────────────────────────────────────────── меню ───
    def _build_menu(self):
        labels = self.actions.menu_labels()
        u = self.u
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, MF_STRING, TM.CMD_TOGGLE_PROXY, labels["toggle"])
        u.AppendMenuW(menu, MF_STRING, TM.CMD_STATUS, labels["status"])
        u.AppendMenuW(menu, MF_STRING, TM.CMD_REFRESH, labels["refresh"])
        u.AppendMenuW(menu, MF_STRING, TM.CMD_OPEN_LOG, labels["open_log"])
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        # подменю «Фильтры» с двумя переключателями (галочка = включено)
        sub = u.CreatePopupMenu()
        ru_flags = MF_STRING | (MF_CHECKED if self.actions.ru_direct() else MF_UNCHECKED)
        cf_flags = MF_STRING | (MF_CHECKED if self.actions.cloudflare() else MF_UNCHECKED)
        u.AppendMenuW(sub, ru_flags, TM.CMD_FILTER_RU, labels["filter_ru"])
        u.AppendMenuW(sub, cf_flags, TM.CMD_FILTER_CF, labels["filter_cf"])
        u.AppendMenuW(menu, MF_POPUP | MF_STRING, int(sub), labels["filters"])
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        # подменю «Надзор» — переключатель автоподъёма программы (гачка)
        wsub = u.CreatePopupMenu()
        wd_flags = MF_STRING | (MF_CHECKED if self.actions.watchdog_enabled() else MF_UNCHECKED)
        u.AppendMenuW(wsub, wd_flags, TM.CMD_WATCHDOG, labels["watchdog"])
        u.AppendMenuW(menu, MF_POPUP | MF_STRING, int(wsub), labels["watchdog_section"])
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        # подменю «Уведомления» — сами уведомления и тема оформления
        nsub = u.CreatePopupMenu()
        n_flags = MF_STRING | (MF_CHECKED if self.actions.notify_enabled() else MF_UNCHECKED)
        u.AppendMenuW(nsub, n_flags, TM.CMD_NOTIFY, labels["notify"])
        u.AppendMenuW(nsub, MF_STRING, TM.CMD_THEME, labels["theme"])
        u.AppendMenuW(menu, MF_POPUP | MF_STRING, int(nsub), labels["notify_section"])
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, TM.CMD_EXIT, labels["exit"])
        return menu

    def _show_menu(self):
        menu = self._build_menu()
        pt = POINT()
        self.u.GetCursorPos(ctypes.byref(pt))
        self.u.SetForegroundWindow(self.hwnd)
        cmd = self.u.TrackPopupMenu(
            menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD,
            pt.x, pt.y, 0, self.hwnd, None)
        self.u.DestroyMenu(menu)
        # обязательный трюк: иначе меню «залипает» при клике вне его
        self.u.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        if cmd:
            self._dispatch(int(cmd))

    def _dispatch(self, cmd: int):
        a = self.actions
        if cmd == TM.CMD_EXIT:
            self.stop()
            return

        def work():
            try:
                if cmd == TM.CMD_TOGGLE_PROXY:
                    msg = a.toggle_proxy()
                elif cmd == TM.CMD_REFRESH:
                    msg = a.request_refresh()
                elif cmd == TM.CMD_FILTER_RU:
                    msg = a.toggle_ru_direct()
                elif cmd == TM.CMD_FILTER_CF:
                    msg = a.toggle_cloudflare()
                elif cmd == TM.CMD_WATCHDOG:
                    msg = a.toggle_watchdog()
                elif cmd == TM.CMD_NOTIFY:
                    msg = a.toggle_notify()
                elif cmd == TM.CMD_THEME:
                    msg = a.cycle_theme()
                else:
                    msg = ""
                if msg:
                    self.lg.info(f"{self.tag}: {msg}")
                self._modify_icon()
            except Exception as e:
                self.lg.warn(f"{self.tag}: команда {cmd}: {e}")

        if cmd == TM.CMD_STATUS:
            self.u.MessageBoxW(self.hwnd, a.status_text(), I.tr("tray.menu.status"),
                               MB_OK | MB_ICONINFORMATION)
        elif cmd == TM.CMD_OPEN_LOG:
            msg = a.open_log()
            if msg:
                self.lg.info(f"{self.tag}: {msg}")
        elif cmd in (TM.CMD_TOGGLE_PROXY, TM.CMD_REFRESH,
                     TM.CMD_FILTER_RU, TM.CMD_FILTER_CF, TM.CMD_WATCHDOG,
                     TM.CMD_NOTIFY, TM.CMD_THEME):
            # тяжёлые операции (снятие/перезапуск ядра, запись конфига) — в
            # отдельном потоке, чтобы не подвешивать цикл сообщений трея
            threading.Thread(target=work, daemon=True).start()

    # ────────────────────────────────────────────── WndProc ───
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TIMER:
                self._on_timer()
                return 0
            if msg == WM_TRAY:
                ev = int(lparam) & 0xFFFF
                if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif ev == WM_LBUTTONDBLCLK:
                    self._dispatch(TM.CMD_STATUS)
                return 0
            if msg == WM_DESTROY:
                self.u.PostQuitMessage(0)
                return 0
            if self.wm_taskbar_created and msg == self.wm_taskbar_created:
                # проводник перезапустился — значок надо добавить заново
                self._add_icon()
                return 0
        except Exception as e:
            self.lg.warn(f"{self.tag}: WndProc({msg}): {e}")
        return self.u.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_timer(self):
        if self.stop_event is not None and self.stop_event.is_set():
            self.stop()
            return
        if self._test_steps is not None:
            self._run_test_step()
            return
        try:
            self._reload_theme_if_needed()
            prev = self._state
            ok, err, changed = self._modify_icon()
            if changed:
                self.lg.info(f"{self.tag}: NIM_MODIFY -> state={self._state} "
                             f"{'OK' if ok else 'FAIL err=' + str(err)}")
                self._notify_transition(prev, self._state)
        except Exception as e:
            self.lg.warn(f"{self.tag}: обновление значка: {e}")

    # ────────────────────────────────────────────── жизненный цикл ───
    def run(self) -> int:
        self._wp = WNDPROC(self._wndproc)
        self._setup_services()
        self._make_hicons()
        self._create_window()
        self._add_icon()
        self._timer = self.u.SetTimer(self.hwnd, self.TIMER_ID,
                                      int(self.poll_s * 1000), None)
        msg = MSG()
        while True:
            r = self.u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0:            # WM_QUIT
                break
            if r == -1:
                self.lg.warn(f"{self.tag}: GetMessageW ошибка {ctypes.get_last_error()}")
                break
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))
        self._cleanup()
        return 0

    def stop(self):
        if self._quitting:
            return
        self._quitting = True
        try:
            if self.hwnd and self._timer:
                self.u.KillTimer(self.hwnd, self.TIMER_ID)
        except Exception:
            pass
        try:
            self._delete_icon()
        except Exception:
            pass
        if self.hwnd:
            self.u.DestroyWindow(self.hwnd)     # -> WM_DESTROY -> PostQuitMessage
        else:
            self.u.PostQuitMessage(0)

    def _cleanup(self):
        try:
            for h in self.hicons.values():
                TI.destroy_hicon(h)
            self.hicons.clear()
        except Exception:
            pass
        try:
            if self.hwnd:
                self.u.UnregisterClassW(self.class_name, self.hinstance)
            self.hwnd = None
        except Exception:
            pass
        self.lg.info(f"{self.tag}: значок остановлен")

    # ───────────────────────────────────── тестовый сценарий ───
    def set_test_plan(self, steps, duration: float, on_done=None):
        """steps — [("ok", 0.0), ("off", 1.5), ("error", 3.0)]."""
        self._test_steps = list(steps)
        self._test_start = time.monotonic()
        self._test_until = self._test_start + duration
        self._on_test_done = on_done
        self._test_i = 0

    def _run_test_step(self):
        now = time.monotonic()
        while self._test_i < len(self._test_steps) and \
                now - self._test_start >= self._test_steps[self._test_i][1]:
            state = self._test_steps[self._test_i][0]
            nid = self._nid(state)
            nid.szTip = f"FOREST test: {state}"[:127]
            ctypes.set_last_error(0)
            ok = bool(self.s.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
            err = ctypes.get_last_error()
            print(f"  NIM_MODIFY state={state:5s} -> "
                  f"{'OK' if ok else 'FAIL err=' + str(err)}")
            self._state = state
            self._test_i += 1
        if now >= self._test_until:
            print("  время вышло — NIM_DELETE и выход")
            self.stop()


# ─────────────────────────────────────────── публичный запуск ───
def _run_tray_fallback(cfg, stop_event=None, poll_s: float = 2.0) -> int:
    """Не-Windows: значка нет, поток просто следит за каналом состояния (лог)."""
    lg = log.get_logger()
    lg.info("Трей: значок доступен только на Windows — слежу за tray_status.json.")
    last = None
    while not (stop_event is not None and stop_event.is_set()):
        try:
            st = TM.TS.read_status(cfg)
            key = (st.get("state"), st.get("tip"))
            if key != last and key[0] is not None:
                last = key
                lg.info(f"tray: состояние={key[0]} | {key[1]}")
        except Exception as e:
            lg.warn(f"tray: чтение статуса: {e}")
        if stop_event is not None:
            stop_event.wait(poll_s)
        else:
            time.sleep(poll_s)
    return 0


def run_tray(cfg: dict, stop_event=None, poll_s: float = 2.0,
             on_refresh=None) -> int:
    """Поток трея супервизора: значок + чтение состояния + команды меню.

    ``stop_event`` — ``threading.Event``: его установка убирает значок и
    завершает цикл сообщений. ``on_refresh`` — необязательный колбэк,
    вызываемый по пункту «Обновить серверы сейчас» (триггер всё равно пишется).

    Правило «один значок»: интерфейс ведёт помощник (forest-ui). Пока он жив,
    встроенный значок НЕ создаётся; если помощника нет — создаём встроенный и,
    как только помощник появится, свой значок убираем. Иначе в трее оказалось бы
    два значка (помощник поднимается на секунду-две позже старта супервизора).
    """
    lg = log.get_logger()
    if not IS_WIN:
        return _run_tray_fallback(cfg, stop_event, poll_s)
    try:
        import ui_helper as UH
        if UH.enabled(cfg):
            # Помощник поднимается самим супервизором сразу после старта: даём
            # ему срок объявиться, прежде чем рисовать свой значок.
            t0 = time.time()
            while not UH.running(cfg) and time.time() - t0 < 20.0:
                if stop_event is not None and stop_event.is_set():
                    return 0
                time.sleep(0.5)
        if UH.running(cfg):
            lg.info("трей: интерфейс ведёт помощник (forest-ui) — встроенный "
                    "значок ждёт в резерве")
            while UH.running(cfg):
                if stop_event is not None and stop_event.is_set():
                    return 0
                time.sleep(max(1.0, poll_s))
            lg.warn("трей: помощник интерфейса остановился — "
                    "включаю встроенный значок")
    except Exception as e:
        lg.warn(f"трей: проверка помощника интерфейса: {e}")
    actions = TM.TrayActions(cfg, on_refresh=on_refresh)
    host = _TrayHost(actions, poll_s=poll_s, stop_event=stop_event)
    actions.on_exit = host.stop

    def _watch_helper():
        """Помощник появился уже при работающем встроенном значке — убираем свой."""
        while not (stop_event is not None and stop_event.is_set()):
            try:
                import ui_helper as _UH
                if _UH.running(cfg):
                    lg.info("трей: помощник интерфейса поднялся — убираю "
                            "встроенный значок (интерфейс один)")
                    host.stop()
                    return
            except Exception:
                pass
            time.sleep(1.0)

    try:
        threading.Thread(target=_watch_helper, name="tray-handover",
                         daemon=True).start()
    except Exception:
        pass
    try:
        return host.run()
    except Exception as e:
        lg.error(f"трей: {e}")
        return 1


# ─────────────────────────────────────────── проверка значка ───
def run_test(duration: float = 5.0) -> int:
    """Самотест: добавить значок, сменить 3 состояния, корректно удалить."""
    if not IS_WIN:
        print("тест значка доступен только на Windows")
        return 2
    print("=" * 60)
    print("ТЕСТ ЗНАЧКА ТРЕЯ (Shell_NotifyIconW)")
    print("=" * 60)

    # 1. генерация иконок и HICON
    print("1) иконки (чистый Python, ICO из пикселей):")
    ok_icons = True
    for name in ("ok", "busy", "off", "error"):
        data = TI.state_ico(name)
        sizes = [w for w, _h, _o, _s in TI.ico_images(data)]
        h, err = TI.make_hicon(data, 16)
        good = bool(h) and sizes == [16, 32]
        ok_icons = ok_icons and good
        print(f"   {name:5s}: .ico {len(data):5d} байт, размеры {sizes}, "
              f"HICON={'OK' if h else 'FAIL ' + err}")
        TI.destroy_hicon(h)

    # 2. меню (структура без показа)
    print("2) контекстное меню (CreatePopupMenu/AppendMenuW):")
    state_dir = (os.environ.get("FOREST_TEST_STATE")
                 or os.path.join(os.environ.get("TEMP") or ".", "forest_tray_test"))
    cfg = {"paths": {"state_dir": state_dir}}
    os.makedirs(cfg["paths"]["state_dir"], exist_ok=True)
    actions = TM.TrayActions(cfg)
    labels = actions.menu_labels()
    for k in ("toggle", "status", "refresh", "open_log", "filters", "filter_ru",
              "filter_cf", "watchdog_section", "watchdog", "exit"):
        print(f"   {k:10s}: {labels[k]}")
    print(f"   подсказка значка: {actions.tip()}")

    # 3. живой значок: смена состояний и удаление
    print(f"3) значок в трее на {duration:.0f} с (3 состояния):")
    host = _TrayHost(actions, poll_s=0.5)
    steps = [("ok", 0.0), ("off", 1.5), ("error", 3.0)]
    host.set_test_plan(steps, duration=duration)
    rc = host.run()
    print("=" * 60)
    print("ИТОГ:", "OK" if (ok_icons and rc == 0) else "FAIL")
    print("=" * 60)
    return 0 if ok_icons and rc == 0 else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import config as CFG
        cfg = CFG.load_config()
        log.reset_logger(cfg["paths"].get("log_dir"))
    except Exception:
        cfg = {"paths": {"state_dir": os.environ.get("TEMP") or ".",
                         "log_dir": os.environ.get("TEMP") or "."}}
    I.set_lang(I.detect_lang(cfg))
    if "--test" in argv:
        return run_test(float(os.environ.get("FOREST_TRAY_TEST_S", "5")))
    if "--run" in argv:
        return run_tray(cfg)
    print(__doc__)
    print(I.tr("cli.usage_hint"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
