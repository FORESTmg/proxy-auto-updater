# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""Всплывающие уведомления: слоистое окно, 60 кадров/с, без мерцания.

Как это устроено и почему так:

* Окно СЛОИСТОЕ (``WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE |
  ``WS_EX_TRANSPARENT``) и рисуется ОДНИМ вызовом ``UpdateLayeredWindow`` по
  32-битному DIB с попиксельной прозрачностью. Обычной отрисовки нет вообще:
  ``WM_PAINT`` не используется, ``WM_ERASEBKGND`` возвращает 1, дочерних окон и
  контролов нет. Отсюда отсутствие мерцания: системе нечего перерисовывать
  частями, кадр подменяется целиком.
* Окно НИКОГДА не активируется: показ через ``SW_SHOWNOACTIVATE`` /
  ``SWP_NOACTIVATE`` + ``WS_EX_NOACTIVATE``, поэтому фокус у активного окна
  пользователя не отбирается.
* Анимация: таймер 16 мс (60 кадров/с), плавное появление/удержание/исчезновение.
  Прозрачность кадра задаётся полем ``SourceConstantAlpha`` в BLENDFUNCTION —
  пиксели при этом не пересчитываются, поэтому кадр стоит доли миллисекунды.
* В покое процессор не расходуется: поток ждёт в ``GetMessageW`` (блокирующее
  ожидание), таймер снимается — цикл не крутится вхолостую.
* Нет дисплея/иконки/шрифта — уведомления молча отключаются, программа работает.

Показываем только значимые события (обрыв связи, восстановление, переключение
сервера, ручное выключение, критические ошибки) — не рутину обновления пула.

Модуль Windows-only; на других системах все функции — безвредные заглушки.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time

IS_WIN = sys.platform.startswith("win")

# ── константы WinAPI ────────────────────────────────────────────────────────
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
HWND_TOPMOST = -1
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
WM_APP = 0x8000
WM_WAKE = WM_APP + 1
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
DT_LEFT = 0x00000000
DT_WORDBREAK = 0x00000010
DT_CALCRECT = 0x00000400
DT_NOPREFIX = 0x00000800
DT_END_ELLIPSIS = 0x00008000
TRANSPARENT = 1
DIB_RGB_COLORS = 0
BI_RGB = 0
FW_NORMAL = 400
FW_SEMIBOLD = 600
CLEARTYPE_QUALITY = 5
ANTIALIASED_QUALITY = 4

# ── вид ─────────────────────────────────────────────────────────────────────
# Цвета берутся из общей палитры (src/theme.py) — светлая/тёмная тема одна на
# уведомления и значок трея. Здесь — запасные значения на случай сбоя палитры.
CARD_RGB = (32, 33, 36)
BORDER_RGB = (60, 64, 67)
TITLE_RGB = (241, 243, 244)
BODY_RGB = (189, 193, 198)
ACCENT = {
    "ok": (52, 168, 83),
    "off": (110, 112, 120),
    "error": (217, 48, 37),
    "busy": (249, 171, 0),
    "info": (66, 133, 244),
}


def palette(cfg: dict = None) -> dict:
    """Палитра текущей темы (с запасом на случай недоступности theme.py)."""
    try:
        import theme
        return theme.palette(cfg)
    except Exception:
        return {"card_bg": CARD_RGB, "card_border": BORDER_RGB, "shadow": (0, 0, 0),
                "title": TITLE_RGB, "body": BODY_RGB, "accent": ACCENT["info"],
                "state_ok": ACCENT["ok"], "state_off": ACCENT["off"],
                "state_error": ACCENT["error"], "state_busy": ACCENT["busy"]}


def accent_for(pal: dict, kind: str):
    key = {"ok": "state_ok", "off": "state_off", "error": "state_error",
           "busy": "state_busy", "info": "accent"}.get(kind, "accent")
    return pal.get(key, pal.get("accent", ACCENT["info"]))
MARGIN = 12                       # отступ от края рабочей области, в px@96dpi
GAP = 8                           # зазор между уведомлениями при стопке
RADIUS = 10
ANIM_IN_MS = 260
ANIM_OUT_MS = 200
SLIDE_PX = 42                     # насколько выезжает справа при появлении
DEFAULT_DURATION_MS = 5000
DEFAULT_FPS = 60
MAX_FRAMES_LIMIT = 240            # защита от вечного цикла

DEFAULTS = {
    "enabled": True,
    "duration_ms": DEFAULT_DURATION_MS,
    "fps": DEFAULT_FPS,
    "corner": "bottom-right",
    "on_server_switch": True,
    "show_on_manual_off": True,
    "width": 392,                # ширина карточки — как в макете (и у помощника)
    "card_on_linux": True,       # на Linux рисуем ТУ ЖЕ карточку (откат — D-Bus)
}

_lock = threading.RLock()
_cfg = dict(DEFAULTS)
_cfg_full: dict = {}
_queue: "queue.Queue" = queue.Queue()
_thread = None
_tid = 0
_stats = {"shown": 0, "frames": 0, "avg_fps": 0.0, "worst_ms": 0.0,
          "last_frame_ms": 0.0, "cpu_ms": 0.0, "errors": [], "dropped": 0}
_journal_path = ""               # файл-журнал показанных уведомлений (для проверок)


def _log(msg: str) -> None:
    try:
        import log as LG
        LG.warn(f"notify: {msg}")
    except Exception:
        pass


# ── настройки ───────────────────────────────────────────────────────────────
def settings(cfg: dict = None) -> dict:
    s = dict(DEFAULTS)
    try:
        src = ((cfg or {}).get("notify") or {})
    except Exception:
        src = {}
    for k, dv in DEFAULTS.items():
        v = src.get(k, dv)
        if isinstance(dv, bool):
            s[k] = bool(v)
        elif isinstance(dv, int):
            try:
                s[k] = int(v)
            except Exception:
                s[k] = dv
        else:
            s[k] = str(v)
    s["duration_ms"] = max(1000, min(60000, s["duration_ms"]))
    s["fps"] = max(10, min(120, s["fps"]))
    s["width"] = max(220, min(720, s["width"]))
    if s["corner"] not in ("bottom-right", "bottom-left", "top-right", "top-left"):
        s["corner"] = "bottom-right"
    return s


def configure(cfg: dict) -> dict:
    global _cfg, _journal_path, _cfg_full
    with _lock:
        _cfg = settings(cfg)
        _cfg_full = dict(cfg or {})
        try:
            root = ((cfg or {}).get("paths") or {}).get("root") or ""
            _journal_path = os.path.join(root, "state", "notify.log") if root else ""
        except Exception:
            _journal_path = ""
    return _cfg


def current_cfg() -> dict:
    with _lock:
        return dict(_cfg_full)


def enabled() -> bool:
    with _lock:
        return bool(_cfg.get("enabled", True))


def stats() -> dict:
    with _lock:
        return dict(_stats)


def reset_stats() -> None:
    with _lock:
        for k in ("shown", "frames", "avg_fps", "worst_ms", "last_frame_ms",
                  "cpu_ms", "dropped"):
            _stats[k] = 0.0 if isinstance(_stats[k], float) else 0
        _stats["errors"] = []


# ── ctypes-структуры ────────────────────────────────────────────────────────
if IS_WIN:
    from ctypes import wintypes

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                    ("SourceConstantAlpha", ctypes.c_byte),
                    ("AlphaFormat", ctypes.c_byte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD), ("pt", POINT)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
    except Exception:
        shcore = None

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.restype = ctypes.c_longlong
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.GetMessageW.restype = ctypes.c_int
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
        wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND

    # ── типы аргументов: без них ctypes режет 64-битные HANDLE до int ──
    HGDIOBJ = ctypes.c_void_p
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.c_void_p, wintypes.DWORD]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateFontW.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPCWSTR]
    gdi32.SetBkMode.restype = ctypes.c_int
    gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.SetTextColor.restype = wintypes.DWORD
    gdi32.SetTextColor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    user32.DrawTextW.restype = ctypes.c_int
    user32.DrawTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int,
                                 ctypes.POINTER(RECT), wintypes.UINT]
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(POINT),
        ctypes.POINTER(SIZE), ctypes.c_void_p, ctypes.POINTER(POINT),
        wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.SetTimer.restype = ctypes.c_void_p
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT,
                                ctypes.c_void_p]
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.RegisterClassW.restype = wintypes.WORD
    # высокоточный таймер ожидания — им и держим честные 60 кадров/с
    kernel32.CreateWaitableTimerExW.restype = ctypes.c_void_p
    kernel32.CreateWaitableTimerExW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                                wintypes.DWORD, wintypes.DWORD]
    kernel32.SetWaitableTimer.restype = wintypes.BOOL
    kernel32.SetWaitableTimer.argtypes = [ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_longlong),
                                          wintypes.LONG, ctypes.c_void_p,
                                          ctypes.c_void_p, wintypes.BOOL]
    kernel32.CancelWaitableTimer.restype = wintypes.BOOL
    kernel32.CancelWaitableTimer.argtypes = [ctypes.c_void_p]
    user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    user32.MsgWaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL,
        wintypes.DWORD, wintypes.DWORD]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]


def set_dpi_awareness() -> bool:
    """Per-monitor DPI awareness: иначе на 125/150 % окно будет мыльным."""
    if not IS_WIN:
        return False
    try:                                        # Windows 10 1703+: PMv2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:                                        # Windows 8.1+
        if shcore and shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except Exception:
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except Exception:
        return False


# ── отрисовка одного кадра-карточки в 32-битный DIB ─────────────────────────
def _fill_round_rect(buf: bytearray, w: int, h: int, radius: int, rgb,
                     alpha: int, offset: int = 0) -> None:
    """Заливка скруглённого прямоугольника (RGB + альфа) прямо в буфере BGRA."""
    r = max(0, min(radius, min(w, h) // 2))
    r2 = r * r
    for y in range(h):
        for x in range(w):
            if r:
                cx = cy = None
                if x < r and y < r:
                    cx, cy = r - 1, r - 1
                elif x >= w - r and y < r:
                    cx, cy = w - r, r - 1
                elif x < r and y >= h - r:
                    cx, cy = r - 1, h - r
                elif x >= w - r and y >= h - r:
                    cx, cy = w - r, h - r
                if cx is not None:
                    dx, dy = x - cx, y - cy
                    if dx * dx + dy * dy > r2:
                        continue
            i = ((y + offset) * _dib_w + x) * 4
            if i < 0 or i + 3 >= len(buf):
                continue
            buf[i], buf[i + 1], buf[i + 2] = rgb[2], rgb[1], rgb[0]
            buf[i + 3] = alpha


_dib_w = 0


def _render_card(w: int, h: int, dpi: float, kind: str, title: str, body: str,
                 pal: dict, steps=None, footer: str = ""):
    """Рисует карточку в свежий DIB. -> (hbitmap, hdc_mem, old_bitmap, buf_ref)"""
    global _dib_w
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # top-down: строка 0 сверху
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    ppv = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(0, ctypes.byref(bmi), DIB_RGB_COLORS,
                                  ctypes.byref(ppv), None, 0)
    if not hbmp or not ppv:
        return None
    hdc = gdi32.CreateCompatibleDC(0)
    old = gdi32.SelectObject(hdc, hbmp)
    n = w * h * 4
    buf = (ctypes.c_ubyte * n).from_address(ppv.value)
    ctypes.memset(ppv.value, 0, n)       # полностью прозрачно
    _dib_w = w
    pal = pal or palette(None)
    # мягкая тень: концентрические скруглённые прямоугольники позади карточки
    for k in range(4, 0, -1):
        _fill_round_rect(buf, w - k * 2, h - k * 2, RADIUS + k,
                         pal.get("shadow", (0, 0, 0)), 18 * (5 - k), offset=k)
    # карточка + тонкая рамка (чтобы читалась и на светлом, и на тёмном фоне)
    _fill_round_rect(buf, w, h, RADIUS, pal.get("card_bg", CARD_RGB), 255)
    _stroke_round_rect(buf, w, h, RADIUS, pal.get("card_border", BORDER_RGB), 255)
    # цветная полоса слева (акцент состояния)
    acc = accent_for(pal, kind)
    pad = int(14 * dpi)
    bar_w = max(3, int(4 * dpi))
    for y in range(int(pad * 0.6), h - int(pad * 0.6)):
        for x in range(0, bar_w):
            i = (y * w + x) * 4
            buf[i], buf[i + 1], buf[i + 2] = acc[2], acc[1], acc[0]
            buf[i + 3] = 255
    # текст
    gdi32.SetBkMode(hdc, TRANSPARENT)
    text_x = bar_w + pad
    text_w = w - text_x - pad
    title_h = draw_text(hdc, title, text_x, pad, text_w, dpi, True,
                        pal.get("title", TITLE_RGB))
    y = pad + title_h
    if body:
        y += int(4 * dpi)
        y += draw_text(hdc, body, text_x, y, text_w, dpi, False,
                       pal.get("body", BODY_RGB))
    # ЭТАПЫ С ВРЕМЕНАМИ (запасной путь без Qt): та же строка, что в карточке
    # помощника — «✓ имя   NNN мс», но нарисованная GDI-текстом.
    lines = [str(x) for x in (steps or []) if str(x)]
    if lines:
        y += int(round(6 * dpi))
        lh = int(round(15 * dpi))
        for i, line in enumerate(lines):
            draw_text(hdc, line, text_x + int(8 * dpi), y + i * lh, text_w, dpi,
                      False, pal.get("body", BODY_RGB))
        y += len(lines) * lh
    if footer:
        y += int(round(6 * dpi))
        draw_text(hdc, footer, text_x, y, text_w, dpi, False,
                  pal.get("body", BODY_RGB))
    return {"hbmp": hbmp, "hdc": hdc, "old": old, "buf": buf, "ppv": ppv}


def _inside_rr(x: int, y: int, w: int, h: int, r: int) -> bool:
    """Внутри ли точка скруглённого прямоугольника (для рамки)."""
    if x < 0 or y < 0 or x >= w or y >= h:
        return False
    if r <= 0:
        return True
    if x < r and y < r:
        return (x - (r - 1)) ** 2 + (y - (r - 1)) ** 2 <= r * r
    if x >= w - r and y < r:
        return (x - (w - r)) ** 2 + (y - (r - 1)) ** 2 <= r * r
    if x < r and y >= h - r:
        return (x - (r - 1)) ** 2 + (y - (h - r)) ** 2 <= r * r
    if x >= w - r and y >= h - r:
        return (x - (w - r)) ** 2 + (y - (h - r)) ** 2 <= r * r
    return True


def _stroke_round_rect(buf, w: int, h: int, radius: int, rgb, alpha: int) -> None:
    """Тонкая рамка по краю скруглённого прямоугольника."""
    for y in range(h):
        for x in range(w):
            if (_inside_rr(x, y, w, h, radius)
                    and not _inside_rr(x + 1, y + 1, w - 2, h - 2, max(0, radius - 1))):
                i = (y * _dib_w + x) * 4
                if i + 3 >= len(buf):
                    continue
                buf[i], buf[i + 1], buf[i + 2] = rgb[2], rgb[1], rgb[0]
                buf[i + 3] = alpha


def draw_text(hdc, text: str, x: int, y: int, w: int, dpi: float, bold: bool,
              rgb) -> int:
    """Рисует текст GDI-шрифтом; -> высота нарисованного."""
    size = int((15 if bold else 13) * dpi)
    hf = gdi32.CreateFontW(-size, 0, 0, 0, FW_SEMIBOLD if bold else FW_NORMAL,
                           0, 0, 0, 1, 0, 0, CLEARTYPE_QUALITY, 0, "Segoe UI")
    if not hf:
        return 0
    oldf = gdi32.SelectObject(hdc, hf)
    gdi32.SetTextColor(hdc, rgb[0] | (rgb[1] << 8) | (rgb[2] << 16))
    r = RECT(x, y, x + w, y + 4000)
    user32.DrawTextW(hdc, text, -1, ctypes.byref(r),
                     DT_LEFT | DT_WORDBREAK | DT_NOPREFIX)
    h = r.bottom - r.top
    gdi32.SelectObject(hdc, oldf)
    gdi32.DeleteObject(hf)
    return h


def _alpha_cleanup(buf, w: int, h: int, pal: dict) -> None:
    """Гарантирует непрозрачность карточки (GDI альфу не пишет)."""
    _fill_round_rect(buf, w, h, RADIUS, pal.get("card_bg", CARD_RGB), 255)


# ── поток уведомлений ───────────────────────────────────────────────────────
class _Toast:
    __slots__ = ("kind", "title", "body", "duration", "steps", "footer")

    def __init__(self, kind, title, body, duration, steps=None, footer=""):
        self.kind, self.title, self.body, self.duration = kind, title, body, duration
        # ЭТАПЫ С ВРЕМЕНАМИ и итоговая строка — как в карточке помощника
        # (запасной путь без Qt раньше показывал только текст события).
        self.steps = [str(x) for x in (steps or [])]
        self.footer = str(footer or "")


def _work_area_for_cursor():
    """Рабочая область монитора под курсором (без панели задач)."""
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    mon = user32.MonitorFromPoint(pt, 2)          # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def _dpi_for_point(x: int, y: int) -> float:
    try:
        pt = POINT(x, y)
        mon = user32.MonitorFromPoint(pt, 2)
        if mon and hasattr(user32, "GetDpiForMonitor"):
            dx = wintypes.UINT()
            if user32.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(wintypes.UINT())) == 0:
                return dx.value / 96.0
    except Exception:
        pass
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def _measure(w: int, hdc, dpi: float, title: str, body: str, steps=None,
             footer: str = "") -> int:
    pad = int(14 * dpi)
    size_t = int(15 * dpi)
    size_b = int(13 * dpi)
    hf = gdi32.CreateFontW(-size_t, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, 1, 0, 0,
                           CLEARTYPE_QUALITY, 0, "Segoe UI")
    hf2 = gdi32.CreateFontW(-size_b, 0, 0, 0, FW_NORMAL, 0, 0, 0, 1, 0, 0,
                            CLEARTYPE_QUALITY, 0, "Segoe UI")
    bar_w = max(3, int(4 * dpi))
    text_w = w - (bar_w + pad) - pad
    of = gdi32.SelectObject(hdc, hf)
    r = RECT(0, 0, text_w, 0)
    user32.DrawTextW(hdc, title, -1, ctypes.byref(r),
                     DT_LEFT | DT_WORDBREAK | DT_NOPREFIX | DT_CALCRECT)
    th = r.bottom - r.top
    gdi32.SelectObject(hdc, of)
    gdi32.DeleteObject(hf)
    bh = 0
    if body:
        of = gdi32.SelectObject(hdc, hf2)
        r2 = RECT(0, 0, text_w, 0)
        user32.DrawTextW(hdc, body, -1, ctypes.byref(r2),
                         DT_LEFT | DT_WORDBREAK | DT_NOPREFIX | DT_CALCRECT)
        bh = r2.bottom - r2.top
        gdi32.SelectObject(hdc, of)
    gdi32.DeleteObject(hf2)
    # высота блока этапов и футера (по одной строке на этап)
    extra = 0
    lines = [str(x) for x in (steps or []) if str(x)]
    if lines:
        lh = int(round(15 * dpi))
        extra += int(round(6 * dpi)) + len(lines) * lh
    if footer:
        extra += int(round(6 * dpi)) + int(round(14 * dpi))
    return pad * 2 + th + (int(4 * dpi) + bh if body else 0) + extra


class _Notifier:
    def __init__(self):
        self.hwnd = None
        self.tid = 0
        self.wc = None
        self.proc = None
        self.timer_on = False
        self.cur = None            # активное уведомление
        self.card = None           # DIB карточки
        self.cw = self.ch = 0
        self.t0 = 0.0
        self.phase = "in"
        self.last_tick = 0.0
        self.frame_ms = []
        self.x0 = self.y0 = 0
        self.dpi = 1.0
        self.done = False
        self.htimer = None
        self.busy = False
        self.period = 1.0 / DEFAULT_FPS   # длительность кадра, с
        self.next_due = 0.0               # абсолютный срок следующего кадра

    # --- окно -------------------------------------------------------------
    def create_window(self) -> bool:
        hinst = kernel32.GetModuleHandleW(None)
        self.proc = WNDPROC(self.wndproc)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]

        wc = WNDCLASS()
        wc.lpfnWndProc = self.proc
        wc.hInstance = hinst
        wc.lpszClassName = "ForestToastWnd"
        self.wc = wc
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() not in (0, 1410):
            return False
        ex = (WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
              | WS_EX_TRANSPARENT | WS_EX_TOPMOST)
        self.hwnd = user32.CreateWindowExW(ex, wc.lpszClassName, "", WS_POPUP,
                                           0, 0, 10, 10, None, None, hinst, None)
        return bool(self.hwnd)

    def wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_ERASEBKGND:
            return 1                       # фон не стираем — мерцать нечему
        if msg == WM_NCHITTEST:
            return -1                      # HTTRANSPARENT: клики проходят мимо
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_WAKE:
            self.on_wake()
            return 0
        if msg == WM_TIMER:
            self.tick()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    # --- показ ------------------------------------------------------------
    def on_wake(self) -> None:
        self.busy = True
        if self.cur is not None:
            return
        try:
            t = _queue.get_nowait()
        except queue.Empty:
            self._disarm()
            self.timer_on = False
            self.busy = False
            user32.ShowWindow(self.hwnd, 0)
            return
        self.start(t)
        if self.cur is None:          # карточка не нарисовалась — не зависаем
            self.busy = False
            if not _queue.empty():
                user32.PostThreadMessageW(self.tid, WM_WAKE, 0, 0)

    def start(self, t: "_Toast") -> None:
        l, tp, r, b = _work_area_for_cursor()
        self.dpi = _dpi_for_point(l, tp)
        with _lock:
            width_px = int(_cfg["width"] * self.dpi)
            fps = int(_cfg["fps"])
        hdc = gdi32.CreateCompatibleDC(0)
        h = _measure(width_px, hdc, self.dpi, t.title, t.body, t.steps, t.footer)
        gdi32.DeleteDC(hdc)
        card = _render_card(width_px, h, self.dpi, t.kind, t.title, t.body,
                            palette(current_cfg()), t.steps, t.footer)
        if not card:
            _log("не удалось нарисовать карточку — уведомление пропущено")
            with _lock:
                _stats["errors"].append("render")
            self.cur = None
            return
        self.card, self.cw, self.ch = card, width_px, h
        if self.dpi > 1.0:
            try:      # масштаб для монитора с другим DPI
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            except Exception:
                pass
        corner = _cfg.get("corner", "bottom-right")
        m = int(MARGIN * self.dpi)
        x = (r - self.cw - m) if corner.endswith("right") else (l + m)
        y = (b - self.ch - m) if corner.startswith("bottom") else (tp + m)
        self.x0, self.y0 = x, y
        self.cur = t
        self.t0 = time.perf_counter()
        self.phase = "in"
        self.frame_ms = []
        self.last_tick = 0.0
        self.done = False
        self.paint(1.0, 0)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x + SLIDE_PX, y, self.cw,
                            self.ch, SWP_NOACTIVATE | SWP_SHOWWINDOW)
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.timer_on = True
        self.busy = True
        # Кадры идут по АБСОЛЮТНОЙ шкале: срок каждого следующего кадра — это
        # предыдущий срок + период. Так время отрисовки не накапливается и
        # средний FPS остаётся ровно заданным, а не «чуть меньше».
        self.period = 1.0 / max(1, fps)
        self.next_due = time.perf_counter()
        self._arm_next()
        with _lock:
            _stats["shown"] += 1
        self.journal(t)

    def journal(self, t: "_Toast") -> None:
        p = _journal_path
        if not p:
            return
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')}\t{t.kind}\t{t.title}\t"
                        f"{t.body}\n")
        except Exception:
            pass

    def paint(self, alpha: float, dx: int) -> None:
        hdc_src = self.card["hdc"]
        bf = BLENDFUNCTION(AC_SRC_OVER, 0, max(0, min(255, int(alpha * 255))),
                           AC_SRC_ALPHA)
        pt_dst = POINT(self.x0 + dx, self.y0)
        sz = SIZE(self.cw, self.ch)
        pt_src = POINT(0, 0)
        user32.UpdateLayeredWindow(self.hwnd, 0, ctypes.byref(pt_dst),
                                   ctypes.byref(sz), hdc_src, ctypes.byref(pt_src),
                                   0, ctypes.byref(bf), ULW_ALPHA)

    @staticmethod
    def _ease_out(p: float) -> float:
        return 1 - (1 - p) ** 3

    def tick(self) -> None:
        now = time.perf_counter()
        if self.last_tick:
            dt = (now - self.last_tick) * 1000.0
            self.frame_ms.append(dt)
        self.last_tick = now
        size = self.cur.duration if self.cur else 0
        el = (now - self.t0) * 1000.0
        if el < ANIM_IN_MS:
            p = self._ease_out(el / ANIM_IN_MS)
            self.paint(0.15 + 0.85 * p, int(SLIDE_PX * (1 - p)))
        elif el < ANIM_IN_MS + size:
            self.paint(1.0, 0)
        elif el < ANIM_IN_MS + size + ANIM_OUT_MS:
            p = self._ease_out((el - ANIM_IN_MS - size) / ANIM_OUT_MS)
            self.paint(1.0 - p, int(SLIDE_PX * p))
        else:
            self.finish()
            return
        self._arm_next()          # без этого кадры встают после первого же тика

    def _arm_next(self) -> None:
        """Взводит таймер на СЛЕДУЮЩИЙ кадр по абсолютной шкале времени.

        Одноразовый waitable-таймер сам НЕ перевзводится, поэтому срок считаем
        от предыдущего срока, а не от «сейчас»: тогда время отрисовки кадра не
        накапливается и средний FPS остаётся ровно заданным. Если отстали
        больше чем на кадр, отставшие кадры считаем пропущенными и не копим
        долг — иначе анимация поехала бы рывками.
        """
        p = self.period
        if p <= 0 or self.cur is None:
            return
        now = time.perf_counter()
        self.next_due += p
        if self.next_due <= now:
            k = int((now - self.next_due) / p) + 1
            self.next_due += k * p
            with _lock:
                _stats["dropped"] += k
        self._arm((self.next_due - now) * 1000.0)

    def finish(self) -> None:
        self._disarm()
        self.timer_on = False
        self.busy = False
        user32.ShowWindow(self.hwnd, 0)
        fr = self.frame_ms or [0.0]
        avg = sum(fr) / len(fr)
        with _lock:
            _stats["frames"] += len(fr)
            _stats["avg_fps"] = round(1000.0 / avg, 1) if avg else 0.0
            _stats["worst_ms"] = round(max(_stats["worst_ms"], max(fr)), 1)
            _stats["last_frame_ms"] = round(max(fr), 1)
            _stats["cpu_ms"] = round(self.cpu_ms(), 1)
        if self.card:
            try:
                gdi32.SelectObject(self.card["hdc"], self.card["old"])
                gdi32.DeleteObject(self.card["hbmp"])
                gdi32.DeleteDC(self.card["hdc"])
            except Exception:
                pass
            self.card = None
        self.cur = None
        if not _queue.empty():
            user32.PostThreadMessageW(self.tid, WM_WAKE, 0, 0)

    def cpu_ms(self) -> float:
        try:
            class FT(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD),
                            ("dwHighDateTime", wintypes.DWORD)]
            c, e, k, u = FT(), FT(), FT(), FT()
            if kernel32.GetProcessTimes(kernel32.GetCurrentProcess(),
                                        ctypes.byref(c), ctypes.byref(e),
                                        ctypes.byref(k), ctypes.byref(u)):
                v = ((k.dwHighDateTime << 32) | k.dwLowDateTime) + \
                    ((u.dwHighDateTime << 32) | u.dwLowDateTime)
                return v / 10000.0
        except Exception:
            pass
        return 0.0

    # --- цикл потока ------------------------------------------------------
    def run(self) -> None:
        global _tid
        if not set_dpi_awareness():
            pass
        if not self.create_window():
            _log("окно уведомлений не создано — уведомления отключены")
            with _lock:
                _stats["errors"].append("create_window")
            return
        self.tid = kernel32.GetCurrentThreadId()
        _tid = self.tid
        try:
            self.htimer = kernel32.CreateWaitableTimerExW(
                None, None, 0x00000002, 0x001F0003)   # HIGH_RESOLUTION, ALL_ACCESS
        except Exception:
            self.htimer = None
        if self.htimer:
            self._loop_waitable()
        else:
            self._loop_getmessage()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)

    def _loop_waitable(self) -> None:
        """Цикл с высокоточным таймером: ровные кадры и ноль нагрузки в покое.

        Когда уведомлений нет, таймер снят и поток спит в MsgWaitForMultipleObjects
        (не крутится вхолостую). Кадры идут по таймеру 1000/fps мс.
        """
        WAIT_OBJECT_0 = 0
        WAIT_TIMEOUT = 0x00000102
        WAIT_FAILED = 0xFFFFFFFF
        QS_ALLINPUT = 0x04FF
        handles = (wintypes.HANDLE * 1)(self.htimer)
        period_ms = 0
        while True:
            with _lock:
                fps = int(_cfg.get("fps", DEFAULT_FPS))
            period_ms = max(8, int(round(1000.0 / max(10, fps))))
            r = user32.MsgWaitForMultipleObjects(1, handles, False, WAIT_FAILED,
                                                 QS_ALLINPUT)
            if r == WAIT_OBJECT_0:
                if self.cur is not None:
                    self.tick()          # кадр
                else:
                    self._disarm()       # нечего показывать — таймер молчит
                continue
            if r == WAIT_OBJECT_0 + 1:
                if self._drain() == 0:
                    time.sleep(0.005)
                continue
            if r in (WAIT_TIMEOUT, WAIT_FAILED):
                if self._drain() == 0:
                    time.sleep(0.01)
                continue

    def _loop_getmessage(self) -> None:
        """Запасной путь для систем без высокоточного таймера: обычный SetTimer."""
        msg = MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if r in (0, -1):
                break
            # ВАЖНО: сообщения, отправленные в ПОТОК (PostThreadMessage, hwnd=NULL),
            # DispatchMessageW никуда не доставляет — обрабатываем их здесь.
            if msg.message == WM_WAKE:
                self.on_wake()
                continue
            if msg.message == WM_TIMER:
                self.tick()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _drain(self) -> int:
        """Разбирает накопившиеся сообщения потока/окна. -> сколько обработал."""
        msg = MSG()
        n = 0
        PM_REMOVE = 1
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            n += 1
            if msg.message == WM_WAKE:
                self.on_wake()
            elif msg.message == WM_TIMER:
                self.tick()
            elif msg.message == WM_QUIT:
                return n
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        return n

    # --- таймер ----------------------------------------------------------
    def _arm(self, ms: float) -> None:
        """Взводит таймер на ``ms`` миллисекунд вперёд (одноразовый отсчёт)."""
        if not self.htimer:
            if not self.timer_on:
                user32.SetTimer(self.hwnd, 1, max(8, int(round(ms))), None)
                self.timer_on = True
            return
        # не меньше 1 мс: нулевой/отрицательный срок SetWaitableTimer отвергает
        due = ctypes.c_longlong(-max(1, int(round(ms * 1000.0))) * 10)  # 100 нс
        if not kernel32.SetWaitableTimer(self.htimer, ctypes.byref(due), 0,
                                         None, None, False):
            with _lock:
                _stats["errors"].append("arm:%d" % ctypes.get_last_error())

    def _disarm(self) -> None:
        if self.htimer:
            kernel32.CancelWaitableTimer(self.htimer)
        if self.timer_on:
            user32.KillTimer(self.hwnd, 1)
            self.timer_on = False


def _ensure_thread() -> bool:
    global _thread, _tid
    if not IS_WIN:
        return False
    with _lock:
        if _thread and _thread.is_alive() and _tid:
            return True
        if _thread and _thread.is_alive():
            return True
        n = _Notifier()
        _NOTIFIER["n"] = n
        _thread = threading.Thread(target=n.run, name="forest-notify", daemon=True)
        _thread.start()
        t0 = time.time()
        while time.time() - t0 < 3.0 and not _tid:
            time.sleep(0.02)
        return bool(_tid)


_NOTIFIER = {"n": None}


def find_notify_icon() -> str:
    """PNG для системы уведомлений Linux (наша иконка)."""
    cands = []
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands += [os.path.join(exe_dir, "forest-proxy-updater.png"),
                  os.path.join(exe_dir, "app.png")]
    except Exception:
        pass
    try:
        mp = getattr(sys, "_MEIPASS", "") or ""
        if mp:
            cands.append(os.path.join(mp, "forest-proxy-updater.png"))
    except Exception:
        pass
    cands += ["/usr/share/icons/hicolor/256x256/apps/forest-proxy-updater.png"]
    try:
        import config as CFG
        cands.append(os.path.join(CFG.PROJECT_DIR, "packaging-linux", "icons",
                                  "forest-proxy-updater.png"))
    except Exception:
        pass
    for c in cands:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            continue
    return ""


def _posix_notify(kind: str, title: str, body: str, cfg: dict) -> bool:
    """Уведомление средствами системы: D-Bus org.freedesktop.Notifications.

    Свой анимированный кадр на Linux не рисуем — оформление, анимацию и тему
    даёт сама система. Сначала пробуем D-Bus через ``gdbus`` (glib, обычно уже
    есть), затем ``notify-send``. Ни того, ни другого — молча пишем в лог.
    """
    import shutil
    import subprocess
    s = settings(cfg)
    icon = find_notify_icon()
    urgency = {"error": "2", "busy": "1", "ok": "1", "off": "0", "info": "1"}
    u = urgency.get(kind, "1")
    exp = int(s["duration_ms"])
    attempts = []
    gdbus = shutil.which("gdbus")
    if gdbus:
        hints = (f'{{"urgency": <byte {u}>, "desktop-entry": <"forest-proxy-updater">}}')
        attempts.append([gdbus, "call", "--session",
                         "--dest", "org.freedesktop.Notifications",
                         "--object-path", "/org/freedesktop/Notifications",
                         "--method", "org.freedesktop.Notifications.Notify",
                         "forest-proxy-updater", "0", icon or "", title, body,
                         "[]", hints, str(exp)])
    ns = shutil.which("notify-send")
    if ns:
        cmd = [ns, "--app-name=forest-proxy-updater", f"--urgency="
               + {"2": "critical", "1": "normal", "0": "low"}.get(u, "normal"),
               f"--expire-time={exp}"]
        if icon:
            cmd.append(f"--icon={icon}")
        attempts.append(cmd + [title, body])
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=6)
            if r.returncode == 0:
                return True
        except Exception as e:
            _log(f"{cmd[0]}: {e}")
    _log("служба уведомлений недоступна (нет gdbus/notify-send или сессии D-Bus) — "
         "уведомление только в логе")
    try:
        import log as LG
        LG.warn(f"уведомление: {title} | {body}")
    except Exception:
        pass
    return False


def _journal(kind: str, title: str, body: str) -> None:
    p = _journal_path
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}\t{kind}\t{title}\t{body}\n")
    except Exception:
        pass


def notify(kind: str, title: str, body: str = "", force: bool = False,
           duration_ms: int = None, steps=None, footer: str = "") -> bool:
    """Показать уведомление. Возвращает True, если оно поставлено в очередь.

    Windows — своё слоистое окно; Linux — системная служба уведомлений.
    ``force`` — для служебных проверок (игнорирует настройку ``enabled``).
    ``steps`` — строки этапов С ВРЕМЕНАМИ (например «✓ Обрыв обнаружен   50 мс»),
    ``footer`` — итоговая строка («восстановлено за 1.85 с (1850 мс) · окно
    закроется через 4 с»). Так запасной путь без Qt показывает тот же состав,
    что и карточка помощника, а не только текст события.
    """
    with _lock:
        if not _cfg.get("enabled", True) and not force:
            return False
        cfg = dict(_cfg_full)
        dur = int(duration_ms or _cfg.get("duration_ms", DEFAULT_DURATION_MS))
    step_list = [str(x) for x in (steps or []) if str(x)]
    if not IS_WIN:
        text = body
        if step_list:
            text = (text + "\n" if text else "") + "\n".join(step_list)
        if footer:
            text = (text + "\n" if text else "") + str(footer)
        ok = _posix_notify(kind, title, text, cfg)
        _journal(kind, title, text)
        with _lock:
            _stats["shown"] += 1
            if not ok:
                _stats["errors"].append("no-dbus")
        return ok
    if not _ensure_thread():
        return False
    _queue.put(_Toast(kind, title, body, dur, steps=step_list, footer=footer))
    if _tid:
        user32.PostThreadMessageW(_tid, WM_WAKE, 0, 0)
    return True


def notify_event(event: str, cfg: dict = None, title: str = "", body: str = "",
                 force: bool = False, steps=None, footer: str = "") -> bool:
    """Событие -> уведомление. Рутина обновления пула молчит по определению."""
    try:
        import i18n
        T = (lambda k, **kw: i18n.t(k, **kw)) if hasattr(i18n, "t") else None
    except Exception:
        T = None
    s = settings(cfg if cfg is not None else _cfg)
    KINDS = {
        "lost": ("error", "notify.lost.title", "notify.lost.body"),
        "restored": ("ok", "notify.restored.title", "notify.restored.body"),
        "switched": ("info", "notify.switched.title", "notify.switched.body"),
        "off": ("off", "notify.off.title", "notify.off.body"),
        "on": ("ok", "notify.on.title", "notify.on.body"),
        "error": ("error", "notify.error.title", "notify.error.body"),
    }
    if event == "switched" and not s.get("on_server_switch", True):
        return False
    if event == "off" and not s.get("show_on_manual_off", True):
        return False
    kind, tk, bk = KINDS.get(event, ("info", "", ""))
    if T:
        t_ = title or T(tk)
        b_ = body or T(bk)
    else:
        t_, b_ = title or event, body
    return notify(kind, t_, b_, force=force, steps=steps, footer=footer)


def wait_idle(timeout: float = 30.0) -> bool:
    """Ждёт, пока очередь и показ опустеют (для тестов и аккуратного выхода)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = _NOTIFIER.get("n")
        if _queue.empty() and (n is None or
                               (not getattr(n, "busy", False) and n.cur is None)):
            return True
        time.sleep(0.05)
    return False


def stop() -> None:
    if not IS_WIN or not _tid:
        return
    try:
        user32.PostThreadMessageW(_tid, WM_DESTROY, 0, 0)
    except Exception:
        pass
