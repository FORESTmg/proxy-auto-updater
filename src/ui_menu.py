# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Своё всплывающее меню трея вместо ``QMenu`` (макет — docs/menu-mockup.html).

Зачем своё окно
---------------
``QMenu`` — системный виджет: своя отрисовка, нет анимации появления и переезда
подсветки, и он не совпадает со стилем карточки уведомления. Здесь меню нарисовано
САМИ: общий модуль стиля :mod:`ui_style` даёт цвета, геометрию, длительности и
кривые, а состав и поведение пунктов — те же, что были у ``QMenu``.

Что соблюдается
---------------
* ``Qt.Popup`` без системной рамки; скругление 12, полоса акцента слева 3 px,
  рамка 1 px, отступы 13/15/12/14 — числа :mod:`ui_style` (они же числа карточки).
* ОДИН слой текста на пункт: подпись рисуется ОДИН раз, цвет интерполируется
  между ``muted`` и ``cur`` (:func:`ui_style.lerp_color`). Двух наложенных слоёв с
  кросс-фейдом нет — именно они давали «двоение» подписи.
* Подъём строки 3 px — ЦЕЛЫМИ пикселями, координата ложится на пиксель экрана
  (:func:`ui_style.snap`), поэтому глифы остаются резкими и при масштабе монитора
  125/150/175 %.
* За кадр меняются только положение окна (сдвиг), прозрачность, положение
  подсветки и параметры галочки; фон, рамка, полоса акцента и разделители
  нарисованы ОДИН раз в ``QPixmap`` и подставляются целиком — мерцать нечему.
* Кадры идут по АБСОЛЮТНОЙ сетке времени, период выровнен по частоте обновления
  экрана (:func:`ui_style.frame_period`): на 200 Гц — 15 мс (66.7 к/с) вместо
  «пилы» 15/20 мс; плавность не ниже заявленных 60 к/с.
* Меню следует теме (светлая/тёмная/как в системе) — цвета из одной таблицы.
* Позиция — по курсору; экран выбирается ПО КУРСОРУ (у каждого монитора свой
  масштаб и своя частота). У края экрана подменю разворачивается внутрь, знак
  выезда зеркалится.
* Клавиши: вверх/вниз, вправо (подменю), влево/Esc (закрыть подменю или меню),
  Enter (выбрать). Клик вне меню закрывает. Закрытие — с анимацией.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_style as S


def _menu_err(where: str) -> None:
    """Ошибка внутри меню: пишем след в журнал, но НЕ роняем помощник.

    Обработчики событий панели меню и тик анимации вызываются из C++ (Qt).
    Необработанное исключение оттуда попадает в ``sys.excepthook``, а он
    завершает процесс помощника — значок исчезает, и пользователь видит
    «программа упала». Поэтому всех их оборачиваем сюда: худший исход — ошибка
    меню, а не смерть помощника.
    """
    import traceback
    txt = traceback.format_exc()
    try:
        import log as LG
        LG.get_logger().error(f"ui_menu: ошибка меню ({where}):\n{txt}")
    except Exception:
        pass
    try:
        import safestdio as SD
        SD.say(f"ui_menu: ошибка меню ({where}) — помощник продолжает работать")
    except Exception:
        pass


# Во сколько раз чаще периода кадра просыпается таймер: кадр рисуется на первом
# тике после срока, поэтому чем чаще пробуждение, тем точнее кадр ложится на
# сетку (на 200 Гц панели это уменьшает «попадание на соседнее обновление»).
TICK_DIV = 8

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    QT_OK = True
    _QT_ERROR = ""
except Exception as _e:                                   # pragma: no cover
    QT_OK = False
    _QT_ERROR = str(_e)
    QtCore = QtGui = QtWidgets = None


# ══════════════════════════════════════════════════════════════════ модель ═══
class Row:
    """Строка меню: пункт или разделитель.

    ``act`` — QAction-совместимый объект: текст, галочка и доступность читаются
    ИЗ НЕГО, поэтому помощник интерфейса и встроенный трей пользуются одной
    моделью, и подписи не расходятся.
    """

    __slots__ = ("kind", "key", "act", "label", "checked", "enabled", "radio", "sub")

    def __init__(self, kind: str = "item", key: str = "", act=None,
                 label: str = "", checked: bool = False, enabled: bool = True,
                 radio: bool = False, sub=None):
        self.kind = kind                    # item | sep
        self.key = key
        self.act = act
        self.label = label
        self.checked = bool(checked)
        self.enabled = bool(enabled)
        self.radio = bool(radio)
        self.sub = sub                      # MenuGroup | None

    # ── чтение состояния (QAction — источник истины, если он есть) ──
    def text(self) -> str:
        try:
            if self.act is not None:
                return str(self.act.text())
        except Exception:
            pass
        return str(self.label)

    def is_checked(self) -> bool:
        try:
            if self.act is not None:
                return bool(self.act.isChecked())
        except Exception:
            pass
        return bool(self.checked)

    def is_enabled(self) -> bool:
        try:
            if self.act is not None:
                return bool(self.act.isEnabled())
        except Exception:
            pass
        return bool(self.enabled)

    def is_checkable(self) -> bool:
        try:
            if self.act is not None:
                return bool(self.act.isCheckable())
        except Exception:
            pass
        return False

    def has_sub(self) -> bool:
        return self.sub is not None and len(self.sub.rows) > 0


class MenuGroup:
    """Ветка меню: заголовок (у подменю) + строки. Читается так же, как QMenu."""

    def __init__(self, title: str = ""):
        self._title = str(title or "")
        self.rows = []

    def title(self) -> str:
        return self._title

    def actions(self) -> list:
        return [r.act for r in self.rows if r.act is not None]

    def add(self, row: Row) -> Row:
        self.rows.append(row)
        return row

    def add_separator(self) -> Row:
        return self.add(Row(kind="sep"))


def group_for_items(items) -> MenuGroup:
    """Сборка :class:`MenuGroup` из списка (Row | 'sep' | dict) — для проверок."""
    g = MenuGroup()
    for it in items:
        if isinstance(it, Row):
            g.add(it)
        elif it == "sep":
            g.add_separator()
        elif isinstance(it, dict):
            g.add(Row(**it))
    return g


def dump_rows(rows) -> str:
    """Текстовый дамп меню (тот же формат, что печатал ``--menu-dump``).

    Отдельная функция, а не только метод панели: дамп нужен и без создания окна
    (проверки и ``--menu-dump`` печатают его на offscreen-платформе).
    """
    out = []
    for r in rows:
        if r.kind == "sep":
            out.append("  ---")
            continue
        if r.has_sub():
            out.append(f"  {r.text()}  [подменю]")
            for s in r.sub.rows:
                if s.kind == "sep":
                    out.append("    ---")
                    continue
                out.append(f"    {'*' if s.is_checked() else ' '} {s.text()}"
                           + ("" if s.is_enabled() else "  (только показ)"))
        else:
            mark = ("[v]" if (r.is_checkable() and r.is_checked())
                    else ("[ ]" if r.is_checkable() else "   "))
            out.append(f"  {mark} {r.text()}"
                       + ("" if r.is_enabled() else "  (только показ)"))
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════ панель меню ═══
class _ClickCatcher(QtWidgets.QWidget if QT_OK else object):
    """Прозрачный слой НА ВЕСЬ ЭКРАН под меню: клик в любом месте закрывает меню.

    Зачем отдельное окно: ``Qt.Popup`` закрывается сам только если система
    отдала нажатие меню (X grab). На Wayland/XWayland захвата нет — клик мимо
    меню уходит в чужое окно, и меню остаётся висеть (именно это и видел
    пользователь: меню закрывалось только у значка). Слой лежит НИЖЕ меню
    (``Qt.Popup`` — override-redirect, он всегда выше управляемых окон) и
    ВЫШЕ остальных окон приложения, поэтому:

    * клик по пункту меню доходит ДО МЕНЮ (слой его не съедает);
    * любой другой клик попадает в слой и закрывает меню.

    Непрозрачность 1 %: слой не виден, но остаётся обычным окном — прозрачность
    окна не отменяет доставку ввода (в отличие от WA_TransparentForMouseEvents).
    """

    clicked = QtCore.Signal()

    def __init__(self, screen):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowOpacity(0.01)
        self.setWindowTitle("FOREST menu catcher")
        try:
            self.setGeometry(screen.geometry())
        except Exception:
            pass

    def paintEvent(self, _ev) -> None:
        p = QtGui.QPainter(self)
        try:
            p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 3))
        finally:
            p.end()

    def mousePressEvent(self, ev) -> None:
        ev.accept()
        self.clicked.emit()

    def mouseReleaseEvent(self, ev) -> None:
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key_Escape, Qt.Key_Enter, Qt.Key_Return):
            ev.accept()
            self.clicked.emit()


class PopupMenu(QtWidgets.QWidget if QT_OK else object):
    """Одно всплывающее окно меню: корневое меню или подменю."""

    def __init__(self, cfg: dict, group: MenuGroup, on_activate=None,
                 on_toggle=None, state_color=None, parent=None, state_kind="ok"):
        if not QT_OK:                                   # pragma: no cover
            raise RuntimeError("PySide6 недоступен: " + _QT_ERROR)
        # parent — КОРНЕВОЕ меню: подменю остаётся в его ветке, поэтому Qt НЕ
        # закрывает корневое меню при открытии подменю (так же ведёт себя QMenu).
        super().__init__(parent)
        self.cfg = cfg or {}
        self.group = group
        self.on_activate = on_activate        # (key) -> None — команда пункта
        self.on_toggle = on_toggle            # (key, checked) -> None — переключатель
        self.tokens = S.tokens(self.cfg)
        self.state_kind = str(state_kind or "ok")
        if self.state_kind not in ("ok", "error", "busy", "off"):
            self.state_kind = "ok"
        self.state_color = tuple(state_color or self.tokens["state_" + self.state_kind])
        self.rows = list(group.rows)
        self.sub_panel = None
        self.sub_index = -1
        self.hover = -1
        self.sel = -1
        self._phase = "init"                   # init|in|run|out|done
        self._dpr = 1.0
        self._home = None
        self._slide_sign = 1
        self._px = self._py = 0
        self._press = -1
        self._t0 = 0.0
        self._last = 0.0
        self._next_due = 0.0
        self._dropped = 0
        self._row_k = [0.0] * len(self.rows)    # прогресс подсветки/цвета строки
        self._chk_k = [0.0] * len(self.rows)    # прогресс галочки
        self._catchers = []                    # слои «клик мимо меню» (только у корня)
        self._hl_y = 0.0
        self._hl_to = 0.0
        self._hl_op = 0.0
        self._pm_last = 0.0
        self._fade_ms = float(S.STEP_FADE_MS)   # темп подсветки текущего пути
        self.hover_by = "mouse"
        # ── ПОДМЕНЮ ПО НАВЕДЕНИЮ: две задержки, обе из :mod:`ui_style` ──
        # Открытие — с короткой паузой (чтобы простой ПРОНОС курсора по списку
        # не раскрывал подменю), закрытие — с «льготным» окном: курсор успевает
        # дойти до подменю по диагонали, и оно не мигает.
        self.hover_open_ms = int(S.SUB_HOVER_OPEN_MS)
        self.hover_close_ms = int(S.SUB_HOVER_CLOSE_MS)
        self._hov_open = QtCore.QTimer(self)
        self._hov_open.setSingleShot(True)
        self._hov_open.setTimerType(Qt.PreciseTimer)   # задержка без «±5 %»
        self._hov_open.setInterval(self.hover_open_ms)
        self._hov_open.timeout.connect(self._on_hover_open)
        self._hov_close = QtCore.QTimer(self)
        self._hov_close.setSingleShot(True)
        self._hov_close.setTimerType(Qt.PreciseTimer)
        self._hov_close.setInterval(self.hover_close_ms)
        self._hov_close.timeout.connect(self._on_hover_close)
        self._hov_events = []                  # след наведения (для проверок)
        self._sub_by_hover = False
        self.align_frames = True               # период кадров кратен обновлению экрана
        self._period = 1.0 / float(S.FPS)
        self.frame_period = S.frame_period(S.FPS, 0.0)
        self.frame_ms = []
        self.paint_gap_ms = []
        self.stats = {"frames": 0, "avg_fps": 0.0, "worst_ms": 0.0, "jitter_ms": 0.0,
                      "dropped": 0, "period_ms": round(self._period * 1000.0, 4),
                      "refresh_hz": 0.0, "aligned": False, "rows": len(self.rows),
                      "width": 0, "height": 0, "in_ms": S.ANIM_IN_MS,
                      "out_ms": S.ANIM_OUT_MS, "theme": S.theme_of(self.cfg),
                      "hover_open_ms": self.hover_open_ms,
                      "hover_close_ms": self.hover_close_ms,
                      "fade_ms": self._fade_ms,
                      "panel_w": 0, "panel_h": 0, "level": 1 if parent else 0}
        for i, r in enumerate(self.rows):
            self._chk_k[i] = 1.0 if (r.is_checked() and r.is_checkable()) else 0.0
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFont(self._base_font())
        self._layout_rows()
        self._build_static()
        self._ticker = QtCore.QTimer(self)
        self._ticker.setTimerType(Qt.PreciseTimer)
        # Пробуждение в TICK_DIV раз чаще периода кадра: кадр рисуется на первом
        # тике после срока, поэтому разброс попадания — не больше периода/TICK_DIV
        self._ticker.setInterval(max(1, int(round(self._period * 1000.0 / TICK_DIV))))
        self._ticker.timeout.connect(self._tick)

    # ───────────────────────────────────────────────────── шрифт и раскладка ──
    def _base_font(self):
        f = QtGui.QFont(self.font())
        f.setPixelSize(S.MENU_FS)
        return f

    def _layout_rows(self) -> None:
        """Геометрия по правилам макета: рамка + padding + строки 34 px."""
        self.fm = QtGui.QFontMetrics(self._base_font())
        labels = [r.text() for r in self.rows if r.kind == "item"]
        row_w = max([S.MARK + S.GAP + self.fm.horizontalAdvance(t) for t in labels]
                    or [S.MARK + S.GAP + 10])
        for r in self.rows:
            if r.kind == "item" and r.has_sub():
                row_w = max(row_w, S.MARK + S.GAP + self.fm.horizontalAdvance(r.text())
                            + S.GAP + S.CHEV_W)
        self.row_w = int(row_w)
        self.panel_w = int(S.STRIPE + S.PAD_L + self.row_w + S.PAD_R + S.HAIRLINE)
        y = S.HAIRLINE + S.PAD_T
        self._ys = []
        for r in self.rows:
            self._ys.append(y)
            y += S.ROW_H if r.kind == "item" else (S.SEP_M * 2 + S.HAIRLINE)
        self.panel_h = int(y + S.PAD_B + S.HAIRLINE)
        self.stats.update({"panel_w": self.panel_w, "panel_h": self.panel_h,
                           "rows": len(self.rows),
                           "width": self.panel_w + 2 * S.SHADOW_PX,
                           "height": self.panel_h + 2 * S.SHADOW_PX})
        self.resize(self.panel_w + 2 * S.SHADOW_PX,
                    self.panel_h + 2 * S.SHADOW_PX)

    def content_origin(self) -> tuple:
        return (S.STRIPE + S.PAD_L, S.HAIRLINE + S.PAD_T)

    # ────────────────────────────────────────────────────── статический слой ──
    def _build_static(self) -> None:
        """Фон, рамка, полоса акцента, разделители, тень — РИСУЕТСЯ ОДИН РАЗ.

        Рисуем в масштабе экрана (devicePixelRatio): при 100 % числа макета
        попадают в пиксели 1:1, при 125/150/175 % линии не «худеют».
        """
        dpr = float(self._dpr or 1.0)
        w = self.panel_w + 2 * S.SHADOW_PX
        h = self.panel_h + 2 * S.SHADOW_PX
        pm = QtGui.QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pm.fill(Qt.transparent)
        pm.setDevicePixelRatio(dpr)
        p = QtGui.QPainter(pm)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            pr = QtCore.QRectF(float(S.SHADOW_PX), float(S.SHADOW_PX),
                               float(self.panel_w), float(self.panel_h))
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(pr.x() + 0.5, pr.y() + 0.5,
                                              pr.width() - 1.0, pr.height() - 1.0),
                                S.RADIUS, S.RADIUS)
            # ── тень макета: ТОЛЬКО СНАРУЖИ панели (как box-shadow в CSS).
            # Иначе тень просвечивала бы сквозь панель (альфа .96) и цвет фона
            # меню отличался бы от цвета карточки на 1-2 единицы ──
            p.setPen(Qt.NoPen)
            p.save()
            outside = QtGui.QPainterPath()
            outside.addRect(QtCore.QRectF(0.0, 0.0, float(w), float(h)))
            p.setClipPath(outside.subtracted(path))
            for i in range(S.SHADOW_PX, 0, -1):
                a = int(S.SHADOW_ALPHA * (1.0 - i / float(S.SHADOW_PX)) ** 2)
                if a <= 0:
                    continue
                p.setBrush(QtGui.QColor(0, 0, 0, a))
                p.drawRoundedRect(pr.adjusted(-i, -i + 2, i, i + 2),
                                  S.RADIUS + i, S.RADIUS + i)
            p.restore()
            # ── панель: фон + рамка ──
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(*self.tokens["bg"]))
            p.drawPath(path)
            p.setBrush(Qt.NoBrush)
            p.setPen(QtGui.QPen(QtGui.QColor(*self.tokens["line"]), S.HAIRLINE))
            p.drawPath(path)
            p.save()
            # полоса акцента и разделители обрезаются по ПАНЕЛИ БЕЗ полупиксельной
            # врезки: тогда 3 px полосы — ровно 3 px (1:1 с макетом)
            clip = QtGui.QPainterPath()
            clip.addRoundedRect(pr, S.RADIUS, S.RADIUS)
            p.setClipPath(clip)
            # ── полоса акцента слева: РОВНО 3 px цветом состояния (как у карточки).
            # Заливка идёт БЕЗ сглаживания по целым пикселям: тогда полоса — ровно
            # 3 px, а не «2 плюс половина» (обрезка по скруглению — со сглаживанием) ──
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(*self.state_color))
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            p.drawRect(QtCore.QRectF(S.snap(pr.x(), dpr), pr.y(),
                                     float(S.STRIPE), pr.height()))
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            # ── разделители (во всю ширину панели, как margin −padding) ──
            p.setBrush(QtGui.QColor(*self.tokens["line"]))
            for i, r in enumerate(self.rows):
                if r.kind != "sep":
                    continue
                y = S.snap(pr.y() + self._ys[i] + S.SEP_M, dpr)
                p.drawRect(QtCore.QRectF(pr.x() + S.HAIRLINE, y,
                                         pr.width() - 2 * S.HAIRLINE,
                                         float(S.HAIRLINE)))
            p.restore()
        finally:
            p.end()
        self._static = pm

    # ──────────────────────────────────────────────────────────── отрисовка ──
    def paintEvent(self, _ev) -> None:
        """Отрисовка панели ПОД ЗАЩИТОЙ (см. :func:`_menu_err`)."""
        try:
            self._paint(_ev)
        except Exception:
            _menu_err("отрисовка панели меню")

    def _paint(self, _ev) -> None:
        now = time.perf_counter()
        if self._pm_last:
            self.paint_gap_ms.append((now - self._pm_last) * 1000.0)
        self._pm_last = now
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.drawPixmap(0, 0, self._static)
            p.translate(S.SHADOW_PX, S.SHADOW_PX)
            self._paint_highlight(p)
            self._paint_rows(p)
        finally:
            p.end()

    def _paint_highlight(self, p) -> None:
        """ОДИН слой подсветки, который ПЕРЕЕЗЖАЕТ по строкам (как в макете)."""
        if self._hl_op <= 0.004:
            return
        x0 = S.STRIPE + S.MENU_INSET
        w = self.panel_w - S.STRIPE - 2 * S.MENU_INSET - S.HAIRLINE
        y = S.snap(self._hl_y, self._dpr)
        col = QtGui.QColor(*self.tokens["surface"])
        col.setAlpha(int(round(col.alpha() * min(1.0, self._hl_op))))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QtCore.QRectF(float(x0), float(y), float(w),
                                        float(S.ROW_H)), S.ROW_R, S.ROW_R)

    def _paint_rows(self, p) -> None:
        p.setFont(self._base_font())
        for i, r in enumerate(self.rows):
            if r.kind != "item":
                continue
            k = self._row_k[i]
            enabled = r.is_enabled()
            # подъём строки — ЦЕЛЫМИ пикселями (текст не размазывается)
            lift = int(round(S.STEP_RISE_PX * (1.0 - (1.0 - k) ** 3))) if k > 0.004 else 0
            y = self._ys[i] - lift
            if r.is_checkable():
                self._paint_marker(p, i, S.STRIPE + S.PAD_L, y)
            # ── ПОДПИСЬ: ОДИН проход отрисовки, цвет интерполирован ──
            if enabled and r.key != "status":
                base, hot = self.tokens["muted"], self.tokens["cur"]
            else:
                base, hot = self.tokens["foot"], self.tokens["foot"]
            p.setPen(QtGui.QColor(*S.lerp_color(base, hot, k)))
            tx = S.STRIPE + S.PAD_L + S.MARK + S.GAP
            tw = self.panel_w - tx - S.PAD_R - (S.GAP + S.CHEV_W if r.has_sub()
                                                else S.HAIRLINE)
            p.drawText(QtCore.QRect(int(tx), int(y), int(max(10, tw)), int(S.ROW_H)),
                       int(Qt.AlignLeft | Qt.AlignVCenter), r.text())
            if r.has_sub():
                self._paint_chev(p, k, int(y))

    def _paint_marker(self, p, i: int, x: int, y: int) -> None:
        """Кольцо → заливка → галочка: прогресс ``_chk_k`` (180 мс, как макет)."""
        k = self._chk_k[i]
        box = QtCore.QRectF(float(x), float(y + (S.ROW_H - S.MARK) / 2.0),
                            float(S.MARK), float(S.MARK))
        rc = QtGui.QColor(*self.tokens["mark"])
        rc.setAlpha(int(round(rc.alpha() * max(0.0, 1.0 - k))))
        p.setBrush(Qt.NoBrush)
        p.setPen(QtGui.QPen(rc, 1.5))
        p.drawEllipse(box.adjusted(0.75, 0.75, -0.75, -0.75))
        if k <= 0.004:
            return
        fc = QtGui.QColor(*self.tokens["state_ok"])
        fc.setAlpha(int(round(255 * k)))
        p.setPen(Qt.NoPen)
        p.setBrush(fc)
        p.drawEllipse(box.adjusted(0.75, 0.75, -0.75, -0.75))
        sc = 0.4 + 0.6 * (1.0 - (1.0 - k) ** 3)
        tc = QtGui.QColor(*self.tokens["tick"])
        tc.setAlpha(int(round(255 * k)))
        pen = QtGui.QPen(tc, 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        mx, my = box.center().x(), box.center().y() + 0.5
        w, h = 3.0 * sc, 3.0 * sc
        pt = QtGui.QPainterPath()
        pt.moveTo(mx - w, my - h)
        pt.lineTo(mx - w * 0.15, my + h * 0.75)
        pt.lineTo(mx + w, my - h)
        p.drawPath(pt)

    def _paint_chev(self, p, k: float, y: int) -> None:
        """Шеврон подменю: тоже ОДИН слой с интерполированным цветом."""
        col = QtGui.QColor(*S.lerp_color(self.tokens["muted"], self.tokens["cur"], k))
        pen = QtGui.QPen(col, 1.5)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        x = self.panel_w - S.PAD_R - S.CHEV_W - S.HAIRLINE
        cy = y + S.ROW_H // 2
        pt = QtGui.QPainterPath()
        pt.moveTo(float(x), float(cy - 4))
        pt.lineTo(float(x + 4), float(cy))
        pt.lineTo(float(x), float(cy + 4))
        p.drawPath(pt)

    # ─────────────────────────────────────────────────────────────── кадры ──
    def _tick(self) -> None:
        """Тик анимации ПОД ЗАЩИТОЙ: ошибка кадра не убивает помощник.

        Слот, вызванный из C++ (таймер), при необработанном исключении уходит в
        ``sys.excepthook``, а там помощник завершается (код 70) — значок
        пропадает. Так падал правый клик по значку трея; чтобы ЛЮБАЯ ошибка в
        меню оставалась ошибкой меню, все обработчики событий панели проходят
        через эту защиту.
        """
        try:
            self._frame()
        except Exception:
            _menu_err("кадр анимации меню")

    def _frame(self) -> None:
        now = time.perf_counter()
        if now + 1e-9 < self._next_due:
            return                       # срок кадра ещё не наступил (сетка)
        if self._last:
            dt = (now - self._last) * 1000.0
            self.frame_ms.append(dt)
            self._advance(dt)
        self._last = now
        self._next_due += self._period
        if self._next_due <= now:        # отстали — долг не копим
            k = int((now - self._next_due) / self._period) + 1
            self._next_due += k * self._period
            self._dropped += k
        self._step_phase(now)
        self.update()

    def _advance(self, dt_ms: float) -> None:
        """Подсветка, цвет подписи и галочка — темпом :data:`_fade_ms`.

        Темп РАЗНЫЙ у двух путей: мышью подсветка уже перенесена на строку
        мгновенно (:meth:`set_hover`), и этим темпом только проявляется; клавишами
        она скользит к строке плавно (:data:`ui_style.STEP_FADE_MS`).
        """
        step = min(1.0, dt_ms / float(self._fade_ms or S.STEP_FADE_MS))
        for i, r in enumerate(self.rows):
            if r.kind != "item":
                continue
            want = 1.0 if i == self.hover else 0.0
            if self._row_k[i] < want:
                self._row_k[i] = min(want, self._row_k[i] + step)
            elif self._row_k[i] > want:
                self._row_k[i] = max(want, self._row_k[i] - step)
            want_c = 1.0 if (r.is_checked() and r.is_checkable()) else 0.0
            if self._chk_k[i] < want_c:
                self._chk_k[i] = min(want_c, self._chk_k[i] + step)
            elif self._chk_k[i] > want_c:
                self._chk_k[i] = max(want_c, self._chk_k[i] - step)
        self._hl_y = S.lerp(self._hl_y, self._hl_to, step)
        want_op = 1.0 if self.hover >= 0 else 0.0
        if self._hl_op < want_op:
            self._hl_op = min(1.0, self._hl_op + step)
        elif self._hl_op > want_op:
            self._hl_op = max(0.0, self._hl_op - step)

    def _step_phase(self, now: float) -> None:
        if self._phase == "in":
            t = min(1.0, (now - self._t0) * 1000.0 / float(S.ANIM_IN_MS))
            e = S.bezier_y(t, *S.CB_IN)
            self.setWindowOpacity(e)
            if self._home is not None:
                self.move(self._home.x()
                          + int(round(self._slide_sign * S.SLIDE_PX * (1.0 - e))),
                          self._home.y())
            if t >= 1.0:
                self._phase = "run"
                self.setWindowOpacity(1.0)
                if self._home is not None:
                    self.move(self._home)
        elif self._phase == "out":
            t = min(1.0, (now - self._t0) * 1000.0 / float(S.ANIM_OUT_MS))
            e = S.bezier_y(t, *S.CB_OUT)
            self.setWindowOpacity(max(0.0, 1.0 - e))
            if self._home is not None:
                self.move(self._home.x()
                          + int(round(self._slide_sign * S.SLIDE_PX * e)),
                          self._home.y())
            if t >= 1.0:
                self._finish()

    # ──────────────────────────────────────────────────────── показ/скрытие ──
    def screen_for(self, pos=None):
        pos = pos or QtGui.QCursor.pos()
        scr = QtGui.QGuiApplication.screenAt(pos)
        return scr or QtGui.QGuiApplication.primaryScreen()

    def place(self, cursor: QtCore.QPoint = None, near: QtCore.QRect = None) -> None:
        """Ставит окно по курсору НА ТОМ ЭКРАНЕ, ГДЕ КУРСОР (свой масштаб).

        ``near`` — прямоугольник родительского пункта: подменю встаёт сбоку от
        СВОЕЙ строки, а у края экрана разворачивается внутрь (знак выезда
        зеркалится — как записано в макете).
        """
        pos = cursor or QtGui.QCursor.pos()
        scr = self.screen_for(pos)
        dpr = float(scr.devicePixelRatio()) if scr else 1.0
        if abs(dpr - self._dpr) > 1e-6:
            self._dpr = dpr
            self.resize(self.panel_w + 2 * S.SHADOW_PX,
                        self.panel_h + 2 * S.SHADOW_PX)
            self._build_static()
        area = scr.availableGeometry() if scr else QtCore.QRect(0, 0, 1920, 1080)
        m = 6
        if near is None:
            # корневое меню: правый край у курсора (у значка трея), выше курсора
            px = pos.x() + 4 - self.panel_w
            py = pos.y() - 6 - self.panel_h
            if py < area.top() + m:                  # вверх не поместилось — вниз
                py = min(pos.y() + 6, area.bottom() - self.panel_h - m)
            self._slide_sign = 1
        else:
            px = near.right() + 6                    # справа от пункта
            py = near.top() - S.PAD_T
            if px + self.panel_w > area.right() - m:  # у края — влево, зеркально
                px = near.left() - 6 - self.panel_w
                self._slide_sign = -1
            else:
                self._slide_sign = 1
            if py + self.panel_h > area.bottom() - m:
                py = area.bottom() - self.panel_h - m
        self._px = max(area.left() + m, min(px, area.right() - self.panel_w - m))
        self._py = max(area.top() + m, min(py, area.bottom() - self.panel_h - m))
        self._home = QtCore.QPoint(self._px - S.SHADOW_PX, self._py - S.SHADOW_PX)
        self.move(self._home)

    def open_at(self, cursor: QtCore.QPoint = None, near: QtCore.QRect = None) -> dict:
        """Показ с анимацией. Период кадров выравнивается по частоте экрана."""
        scr = self.screen_for(cursor)
        hz = 0.0
        try:
            hz = float(scr.refreshRate()) if scr is not None else 0.0
        except Exception:
            hz = 0.0
        fp = S.frame_period(S.FPS, hz)
        if not self.align_frames:
            # СРАВНЕНИЕ «до выравнивания»: обычная сетка 1/60 с — на панели
            # 200 Гц кадр попадает то на 15, то на 20 мс (видно по «пиле»)
            fp = {"period_ms": round(1000.0 / float(S.FPS), 4),
                  "fps": float(S.FPS), "refresh_hz": round(hz, 2),
                  "aligned": False, "ticks": 1}
        self.frame_period = fp
        self._period = fp["period_ms"] / 1000.0
        self._ticker.setInterval(max(1, int(round(self._period * 1000.0 / TICK_DIV))))
        self.stats.update({"period_ms": fp["period_ms"], "refresh_hz": fp["refresh_hz"],
                           "aligned": fp["aligned"], "fps_target": fp["fps"]})
        self.place(cursor, near)
        self.setWindowOpacity(0.0)
        self._t0 = time.perf_counter()
        self._last = 0.0
        self._next_due = time.perf_counter()
        self._phase = "in"
        if self.parent() is None:
            # корневое меню кладёт под себя прозрачный слой на весь экран;
            # подменю живёт в ветке корня и своего слоя НЕ создаёт
            self._show_catchers(cursor)
        self.show()
        self.raise_()
        self._ticker.start()
        self.update()
        return self.stats

    # ─────────────────────────────── слой «клик мимо меню» (см. _ClickCatcher) ──
    def _show_catchers(self, pos=None) -> None:
        """Поставить прозрачный слой на КАЖДЫЙ экран (клик в любом месте).

        Слой показывается ДО меню, поэтому оказывается ниже него в порядке
        наложения; меню (``Qt.Popup``) рисуется поверх. Снимается вместе с
        меню — в :meth:`_finish` и сразу при начале закрытия.
        """
        self._hide_catchers()
        if not QT_OK:
            return
        try:
            screens = list(QtGui.QGuiApplication.screens())
        except Exception:
            screens = []
        scr = self.screen_for(pos)
        if scr is not None:
            screens = [scr] + [s for s in screens if s is not scr]
        out = []
        for s in screens:
            try:
                c = _ClickCatcher(s)
                c.clicked.connect(self._on_catcher_click)
                c.show()
                c.raise_()
                out.append(c)
            except Exception:
                _menu_err("слой клика вне меню")
        self._catchers = out

    def _hide_catchers(self) -> None:
        for c in (getattr(self, "_catchers", None) or []):
            try:
                c.hide()
                c.deleteLater()
            except Exception:
                pass
        self._catchers = []

    def _on_catcher_click(self) -> None:
        """Клик мимо меню: закрываем меню (и подменю) с анимацией."""
        try:
            if self.sub_panel is not None:
                self.sub_panel._finish()
                self.sub_panel = None
            self.start_out()
        except Exception:
            _menu_err("клик вне меню")

    def start_out(self) -> None:
        """Закрытие С АНИМАЦИЕЙ (260 мс, ease-in) вместо мгновенного hide()."""
        if self._phase in ("out", "done"):
            return
        # слой клика снимаем СРАЗУ: пока идёт затухание, он не должен ловить
        # нажатия (иначе второй клик «съедался» бы уже закрытым меню)
        self._hide_catchers()
        if self.sub_panel is not None:
            self.sub_panel.start_out()
        self._phase = "out"
        self._t0 = time.perf_counter()

    def _finish(self) -> None:
        self._phase = "done"
        self._cancel_hover_open()
        self._cancel_hover_close()
        self._ticker.stop()
        self._hide_catchers()
        self._finish_stats()
        if self.sub_panel is not None:
            self.sub_panel._ticker.stop()
            self.sub_panel.hide()
        self.hide()

    def closeEvent(self, ev) -> None:
        """Закрытие ПОД ЗАЩИТОЙ (см. :func:`_menu_err`).

        Клик вне меню и Esc закрывают его С АНИМАЦИЕЙ, а не мгновенно.
        """
        try:
            self._on_close(ev)
        except Exception:
            _menu_err("закрытие меню")

    def _on_close(self, ev) -> None:
        if self._phase not in ("out", "done"):
            ev.ignore()
            self.start_out()
            return
        ev.accept()

    def _finish_stats(self) -> None:
        fr = list(self.frame_ms)
        if fr:
            avg = sum(fr) / len(fr)
            avg2 = sum(x * x for x in fr) / len(fr)
            jit = max(0.0, avg2 - avg * avg) ** 0.5
        else:
            avg = jit = 0.0
        bad = [x for x in fr if x > self._period * 1000.0 * 1.5]
        self.stats.update({
            "frames": len(fr), "avg_ms": round(avg, 3),
            "avg_fps": round(1000.0 / avg, 2) if avg > 0 else 0.0,
            "worst_ms": round(max(fr) if fr else 0.0, 3),
            "jitter_ms": round(jit, 3), "late_frames": len(bad),
            "dropped": self._dropped,
            "intervals": [round(x, 2) for x in fr],
        })

    # ────────────────────────────────────────────────────────── мышь/клавиши ──
    def row_at(self, pos: QtCore.QPoint) -> int:
        y = pos.y() - S.SHADOW_PX
        x = pos.x() - S.SHADOW_PX
        if x < 0 or x > self.panel_w or y < 0 or y > self.panel_h:
            return -1
        for i, r in enumerate(self.rows):
            if r.kind != "item":
                continue
            if self._ys[i] <= y < self._ys[i] + S.ROW_H:
                return i if (r.is_enabled() and r.key != "status") else -1
        return -1

    def set_hover(self, i: int, by: str = "mouse") -> None:
        """Наведение на строку ``i``: подсветка, цвет подписи и ПОДМЕНЮ.

        Два пути, и они намеренно разные:

        * ``by="mouse"`` — подсветка ПЕРЕНОСИТСЯ МГНОВЕННО (``_hl_y`` сразу на
          строке) и проявляется коротким темпом :data:`ui_style.HL_MOUSE_MS`:
          раньше она догоняла курсор 180 мс и «отставала» глазом. Подменю
          открывается ЧЕРЕЗ ЗАДЕРЖКУ (:data:`ui_style.SUB_HOVER_OPEN_MS`) и
          закрывается через «льготное» окно
          (:data:`ui_style.SUB_HOVER_CLOSE_MS`), поэтому ни простой пронос
          курсора, ни диагональный переход к подменю его не дёргают;
        * ``by="key"`` — прежнее плавное скольжение
          (:data:`ui_style.STEP_FADE_MS`), подменю открывает только клавиша
          «вправо» и закрывает «влево»/Esc: стрелки не раскрывают его сами.
        """
        by_mouse = (by == "mouse")
        r = self.rows[i] if 0 <= i < len(self.rows) else None
        has_sub = bool(r is not None and r.kind == "item" and r.has_sub())
        # ── уход с пункта, для которого открыто подменю ──
        if self.sub_panel is not None and i != self.sub_index:
            if by_mouse:
                self._arm_hover_close()
            else:
                self.close_sub()
        if i == self.hover:
            # курсор вернулся на тот же пункт: отменяем ожидающее закрытие,
            # иначе подменю закроется под стоящим курсором
            if by_mouse:
                self._cancel_hover_close()
                if has_sub and self.sub_panel is None:
                    self._arm_hover_open()
            return
        self.hover = i
        self.hover_by = by
        self._fade_ms = float(S.HL_MOUSE_MS if by_mouse else S.STEP_FADE_MS)
        if i >= 0:
            self.sel = i
            self._hl_to = float(self._ys[i])
            if by_mouse:
                self._hl_y = self._hl_to      # переносим подсветку МГНОВЕННО
        if not by_mouse:
            self._cancel_hover_open()
            return
        self._cancel_hover_open()
        if has_sub:
            self._cancel_hover_close()
            if self.sub_panel is None:
                self._arm_hover_open()
            elif self.sub_index != i:
                self._open_sub(i, by="hover")   # переключение на другой пункт
        elif self.sub_panel is not None:
            self._arm_hover_close()

    # ── подменю по НАВЕДЕНИЮ: задержки ──
    def _arm_hover_open(self) -> None:
        """Взводит открытие подменю ПО НАВЕДЕНИЮ (задержка из стиля)."""
        if not self._hov_open.isActive():
            self._hov_open.start(self.hover_open_ms)

    def _cancel_hover_open(self) -> None:
        self._hov_open.stop()

    def _arm_hover_close(self) -> None:
        """Взводит ЗАКРЫТИЕ подменю с «льготным» окном (задержка из стиля)."""
        if self.sub_panel is not None and not self._hov_close.isActive():
            self._hov_close.start(self.hover_close_ms)

    def _cancel_hover_close(self) -> None:
        self._hov_close.stop()

    def _on_hover_open(self) -> None:
        """Наведение задержалось на пункте с подменю — открываем его."""
        i = self.hover
        if not (0 <= i < len(self.rows)):
            return
        if not (self.rows[i].kind == "item" and self.rows[i].has_sub()):
            return
        if self.sub_panel is not None and self.sub_index == i:
            return
        self._hov_events.append(("open", time.perf_counter()))
        self._open_sub(i, by="hover")

    def _on_hover_close(self) -> None:
        """«Льготное» окно истекло: закрываем подменю, если курсор не в нём.

        Проверяем ФАКТ (где курсор), а не только таймер: за окно курсор мог
        вернуться на свой пункт или уйти в подменю по диагонали.
        """
        if self.sub_panel is None:
            return
        pos = QtGui.QCursor.pos()
        if self.sub_panel.in_global(pos):
            return
        i = self.row_at(self.to_local(pos)) if self.in_global(pos) else -1
        if i >= 0 and i == self.sub_index:
            return                              # вернулся на свой пункт
        self._hov_events.append(("close", time.perf_counter()))
        self.close_sub()

    def _pointer_over_sub(self) -> bool:
        """Курсор в подменю (для проверок и «льготного» окна)."""
        return bool(self.sub_panel is not None
                    and self.sub_panel.in_global(QtGui.QCursor.pos()))

    def in_global(self, pos: QtCore.QPoint) -> bool:
        """Попадает ли точка ЭКРАНА в панель (без каймы тени)."""
        g = self.geometry()
        return (g.x() + S.SHADOW_PX <= pos.x() <= g.right() - S.SHADOW_PX
                and g.y() + S.SHADOW_PX <= pos.y() <= g.bottom() - S.SHADOW_PX)

    def to_local(self, global_pos: QtCore.QPoint) -> QtCore.QPoint:
        g = self.geometry()
        return QtCore.QPoint(global_pos.x() - g.x(), global_pos.y() - g.y())

    def mouseMoveEvent(self, ev) -> None:
        """Движение мыши ПОД ЗАЩИТОЙ (см. :func:`_menu_err`)."""
        try:
            self._on_move(ev)
        except Exception:
            _menu_err("наведение мыши в меню")

    def _on_move(self, ev) -> None:
        root = self.parent()
        gpos = ev.globalPosition().toPoint()
        # Подменю держит захват мыши, поэтому события приходят и когда курсор
        # вернулся в корневое меню: отдаём наведение корню (он сам решает,
        # переключать подменю или держать его в «льготном» окне).
        if isinstance(root, PopupMenu) and not self.in_global(gpos):
            if root.in_global(gpos):
                i = root.row_at(root.to_local(gpos))
                root.set_hover(i)
            else:
                # курсор ушёл И с подменю, И с корневого меню: закрываем с
                # льготной задержкой (возврат внутрь окна её отменит)
                root._arm_hover_close()
            return
        self.set_hover(self.row_at(ev.position().toPoint()))

    def mousePressEvent(self, ev) -> None:
        """Нажатие ПОД ЗАЩИТОЙ (см. :func:`_menu_err`)."""
        try:
            self._on_press(ev)
        except Exception:
            _menu_err("нажатие в меню")

    def _on_press(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        i = self.row_at(ev.position().toPoint())
        if i < 0:
            self.close()                     # клик по кайме тени = «вне меню»
            return
        self._press = i

    def mouseReleaseEvent(self, ev) -> None:
        """Отпускание ПОД ЗАЩИТОЙ (см. :func:`_menu_err`)."""
        try:
            self._on_release(ev)
        except Exception:
            _menu_err("отпускание кнопки в меню")

    def _on_release(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        i = self.row_at(ev.position().toPoint())
        if i >= 0 and i == self._press:
            self.activate(i)

    def activate(self, i: int) -> None:
        """Выбор строки: команда, переключатель или открытие подменю."""
        if not (0 <= i < len(self.rows)):
            return
        r = self.rows[i]
        if r.kind != "item" or not r.is_enabled():
            return
        if r.has_sub():
            # Клик/Enter по пункту с подменю его ОТКРЫВАЕТ. Если подменю уже
            # открыто НАВЕДЕНИЕМ, клик его не захлопывает (иначе «навёл — кликнул
            # — пропало»); открытое клавишей/кликом закрывается повторным выбором,
            # как и было.
            if (self.sub_panel is not None and self.sub_index == i
                    and not self._sub_by_hover):
                self.close_sub()
            else:
                self._open_sub(i)
            return
        if r.is_checkable():
            new = not r.is_checked()
            try:
                if r.act is not None:
                    r.act.setChecked(new)
                else:
                    r.checked = new
            except Exception:
                r.checked = new
            if callable(self.on_toggle):
                try:
                    self.on_toggle(r.key, new)
                except Exception:
                    pass
            return
        if callable(self.on_activate):
            try:
                self.on_activate(r.key)
            except Exception:
                pass
        self.root().start_out()

    # ── подменю ──
    def _open_sub(self, i: int, by: str = "click") -> None:
        """Выезд подменю: ТЕМ ЖЕ входом, что у меню (сдвиг + прозрачность).

        ``by`` — чем открыто: ``"hover"`` (наведение), ``"click"`` (клик/клавиша).
        От этого зависит только одно: захлопывает ли повторный клик подменю
        (см. :meth:`activate`) — поведение, привычное по системным меню.
        """
        r = self.rows[i]
        if r.sub is None:
            return
        if self.sub_panel is not None and self.sub_index == i:
            self.sel = i
            return                       # уже открыто на этом пункте — не мигаем
        self._sub_by_hover = (by == "hover")
        if self.sub_panel is None:
            self.sub_panel = PopupMenu(self.cfg, r.sub, on_activate=self.on_activate,
                                       on_toggle=self.on_toggle,
                                       state_color=self.state_color, parent=self,
                                       state_kind=self.state_kind)
        else:
            self.sub_panel.group = r.sub
            self.sub_panel.rows = list(r.sub.rows)
            self.sub_panel._row_k = [0.0] * len(self.sub_panel.rows)
            self.sub_panel._chk_k = [1.0 if (x.is_checked() and x.is_checkable()) else 0.0
                                     for x in self.sub_panel.rows]
            self.sub_panel.hover = -1
            self.sub_panel.sel = -1
            self.sub_panel._hl_op = 0.0
            self.sub_panel._hl_y = 0.0
            self.sub_panel._hl_to = 0.0
            self.sub_panel._phase = "init"
            self.sub_panel.tokens = S.tokens(self.cfg)
            self.sub_panel._layout_rows()
            self.sub_panel._build_static()
        self.sub_panel._dpr = self._dpr
        self.sub_index = i
        row_rect = QtCore.QRect(self._px + S.STRIPE + S.PAD_L, self._py + self._ys[i],
                                self.row_w, S.ROW_H)
        self.sub_panel.open_at(cursor=QtCore.QPoint(row_rect.right(), row_rect.top()),
                               near=row_rect)
        self.sel = i

    def close_sub(self) -> None:
        self._cancel_hover_open()
        self._cancel_hover_close()
        if self.sub_panel is not None:
            self.sub_panel.start_out()
            self.sub_panel = None
            self.sub_index = -1
        self._sub_by_hover = False

    def root(self):
        p = self
        while isinstance(p.parent(), PopupMenu):
            p = p.parent()
        return p

    # ── клавиши: вверх/вниз, вправо/влево, Enter, Esc ──
    def keyPressEvent(self, ev) -> None:
        """Клавиши (Esc/стрелки) ПОД ЗАЩИТОЙ (см. :func:`_menu_err`)."""
        try:
            self._on_key(ev)
        except Exception:
            _menu_err("клавиша в меню")

    def _on_key(self, ev) -> None:
        key = ev.key()
        panel = self._active()
        if key == Qt.Key_Escape:
            r = panel.root()
            if r.sub_panel is not None:
                r.close_sub()
            else:
                r.start_out()
            return
        if key == Qt.Key_Left:
            r = panel.root()
            if panel is not r:
                r.close_sub()
            return
        if key in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter):
            if key == Qt.Key_Right and 0 <= panel.sel < len(panel.rows) \
                    and panel.rows[panel.sel].has_sub():
                if panel.root().sub_panel is None:
                    panel._open_sub(panel.sel)
                return
            if panel.sel >= 0:
                panel.activate(panel.sel)
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            panel._move_sel(1 if key == Qt.Key_Down else -1)
            return
        super().keyPressEvent(ev)

    def _active(self):
        """Куда идут клавиши: открытое подменю или это меню."""
        return self.sub_panel if self.sub_panel is not None else self

    def _move_sel(self, d: int) -> None:
        n = len(self.rows)
        if not n:
            return
        i = self.sel if self.sel >= 0 else (-1 if d > 0 else 0)
        for _ in range(n):
            i = (i + d) % n
            r = self.rows[i]
            if r.kind == "item" and r.is_enabled() and r.key != "status":
                self.set_hover(i, by="key")
                return

    def focusInEvent(self, ev) -> None:
        super().focusInEvent(ev)
        if self.sel < 0:
            self._move_sel(1)

    # ──────────────────────────────────────────────────── доказательства ──
    def dump_text(self) -> str:
        """Тот же дамп, что печатал ``--menu-dump`` (проверки сверяют дословно)."""
        return dump_rows(self.rows)

    def render_png(self, path: str, theme: str = None, hover_key: str = None,
                   alpha: float = 1.0, dpr: float = 1.0) -> str:
        """Снимок панели БЕЗ показа окна: рисуем в QPixmap и сохраняем в PNG.

        ``hover_key`` — какой пункт показать наведённым (подсветка + цвет подписи
        в конечном состоянии), ``alpha`` — фаза появления (1.0 — меню встало;
        прозрачность эмулируется альфой всего слоя, чтобы снимок можно было
        проверить пиксельно, не открывая окно), ``dpr`` — масштаб экрана.
        """
        if theme:
            self.tokens = S.tokens(self.cfg, theme)
            # полоса акцента — цвет ТЕКУЩЕГО состояния из ТОЙ ЖЕ темы (иначе в
            # светлой теме полоса осталась бы тёмно-зелёной)
            self.state_color = tuple(self.tokens["state_" + self.state_kind])
        # Статический слой строим в ЛОГИЧЕСКИХ пикселях, а масштаб экрана
        # применяем к painter'у ниже — ровно так же, как Qt рисует виджет на
        # экране с масштабом 125/150/175 % (иначе получилась бы двойная шкала)
        self._dpr = 1.0
        self._layout_rows()
        self._build_static()
        for i, r in enumerate(self.rows):
            on = (hover_key is not None and r.key == hover_key)
            self._row_k[i] = 1.0 if on else 0.0
            self._chk_k[i] = 1.0 if (r.is_checked() and r.is_checkable()) else 0.0
            if on:
                self._hl_to = float(self._ys[i])
        self._hl_y = float(self._hl_to)
        self._hl_op = 1.0 if hover_key is not None else 0.0
        self.hover = 1 if hover_key is not None else -1
        w = self.panel_w + 2 * S.SHADOW_PX
        h = self.panel_h + 2 * S.SHADOW_PX
        dpr = float(dpr or 1.0)
        layer = QtGui.QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        layer.fill(Qt.transparent)
        pp = QtGui.QPainter(layer)
        try:
            pp.setRenderHint(QtGui.QPainter.Antialiasing, True)
            if abs(dpr - 1.0) > 1e-6:
                pp.scale(dpr, dpr)          # масштаб экрана (125/150/175 %)
            pp.drawPixmap(0, 0, self._static)
            pp.translate(S.SHADOW_PX, S.SHADOW_PX)
            self._paint_highlight(pp)
            self._paint_rows(pp)
        finally:
            pp.end()
        out = QtGui.QPixmap(layer.size())
        out.fill(Qt.transparent)
        op = QtGui.QPainter(out)
        try:
            op.setOpacity(0.0 if alpha <= 0 else min(1.0, float(alpha)))
            op.drawPixmap(0, 0, layer)
        finally:
            op.end()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        out.save(path, "PNG")
        return path
