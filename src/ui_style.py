# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""ОБЩИЙ МОДУЛЬ СТИЛЯ: палитра, геометрия, длительности и кривые — в одном месте.

Зачем один модуль
-----------------
Карточка уведомления (``src/forest_ui.py``, класс :class:`Toast`) и контекстное
меню трея (``src/ui_menu.py``, класс :class:`PopupMenu`) — один и тот же стиль:
одни цвета, одна геометрия, одни кривые и длительности. Пока числа лежали в двух
файлах, они расходились молча: правка макета не доходила до второго места.
Здесь цвета НЕ дублируются: берётся РОВНО ОДНА таблица на тему.

Откуда числа
------------
* Тёмная тема — цвета и геометрия утверждённого макета
  (``docs/notify-mockup.html`` → ``docs/menu-mockup.html``).
* Светлая тема — значения приложения из :mod:`theme` (``PALETTES['light']``):
  ``card_bg`` → фон панели, ``title`` → текст, ``body`` → приглушённый текст,
  ``state_*`` → цвета состояний. Производные токены (поверхность подсветки,
  рамка, маркер) отмечены в таблице словом «выведено»: они зеркалят тёмные
  значения той же роли.

Правило текста (важно)
----------------------
В меню подпись пункта рисуется РОВНО ОДИН раз, а цвет меняется интерполяцией
между ``muted`` и ``cur`` — двух наложенных слоёв и кросс-фейда быть не должно.
Именно два слоя давали «двоение» подписи: приглушённая копия оставалась видимой
под яркой, сдвинутая на подъём строки. Помощник :func:`lerp_color` считает цвет
для одного прохода отрисовки; карточка пользуется им же.

Модуль без Qt: его можно импортировать и проверять без графической сессии.
"""
from __future__ import annotations

import sys

# ══════════════════════════════════════════════════════════════════════════
#  ГЕОМЕТРИЯ (px макета) — ОДНА таблица на карточку и меню
# ══════════════════════════════════════════════════════════════════════════
# Карточка уведомления (docs/notify-mockup.html)
CARD_W = 392                   # .toast{width:392px}
RADIUS = 12                    # border-radius панели
STRIPE = 3                     # полоса акцента слева (border-left)
HAIRLINE = 1                   # рамка 1 px
PAD_T, PAD_R, PAD_B, PAD_L = 13, 15, 12, 14      # padding панели 13/15/12/14
ICON = 34                      # .ico{width:34px;height:34px} — квадрат значка
ICON_R = 9                     # скругление квадрата значка
ICON_ART = 32                  # рисунок пользователя внутри квадрата
GAP = 11                       # .head{gap:11px} — зазор «маркер|подпись»
SPIN = 18                      # .spin{width:18px;height:18px}
SPIN_MT = 4                    # .spin{margin-top:4px}
STEP_L = 45                    # .steps/.bar/.foot{margin-left:45px}
MARK = 13                      # .step .mk{width:13px;height:13px}
STEP_GAP = 6                   # .steps{gap:6px}
STEPS_MT = 11
STEPS_MB = 9
BAR_MT = 2
BAR_H = 3
FOOT_MT = 7
FONT = {"t1": 15, "t2": 12, "step": 12, "ms": 12, "foot": 11}

# Меню (docs/menu-mockup.html): строка = квадрат значка карточки, поэтому
ROW_H = ICON                   # --row 34 px (высота строки = значок карточки)
ROW_R = ICON_R                 # --row-r 9 px (скругление подсветки)
MENU_INSET = STEP_GAP          # --inset 6 px (врезка подсветки от краёв панели)
SEP_M = STEP_GAP               # --sep-m 6 px (отступы разделителя сверху/снизу)
MENU_FS = FONT["step"]         # --fs 12 px (размер шрифта пункта)
CHEV_W = 7                     # .chev{width:7px} — колонка шеврона подменю
# Тень панели (box-shadow макета). Рисуется ВНЕ панели, в прозрачном поле окна,
# поэтому на геометрию панели не влияет.
SHADOW_PX = 14
SHADOW_ALPHA = 110             # насколько тёмная тень у края панели

# ══════════════════════════════════════════════════════════════════════════
#  ДЛИТЕЛЬНОСТИ И КРИВЫЕ — темп анимации КАРТОЧКИ, тот же у меню
# ══════════════════════════════════════════════════════════════════════════
ANIM_IN_MS = 340               # появление: cubic-bezier(.16,.84,.28,1)
ANIM_OUT_MS = 260              # закрытие: ease-in cubic-bezier(.42,0,1,1)
SLIDE_PX = 420                 # сдвиг при выезде/закрытии
CB_IN = (0.16, 0.84, 0.28, 1.0)
CB_OUT = (0.42, 0.0, 1.0, 1.0)
SPIN_MS = 850
STEP_FADE_MS = 180             # подсветка строки при КЛАВИАТУРНОЙ навигации
HL_MOUSE_MS = 50               # подсветка строки при движении МЫШЬЮ: почти сразу
                               #   (подсветка ПЕРЕНОСИТСЯ мгновенно, а этим темпом
                               #    только проявляется — «мягкое появление»)
SUB_HOVER_OPEN_MS = 150        # подменю открывается НАВЕДЕНИЕМ через эту задержку
                               #   (короткий пронос курсора подменю не раскрывает)
SUB_HOVER_CLOSE_MS = 280       # «льготное» окно: ушли с пункта, но вернулись или
                               #   дошли до подменю по диагонали — оно не мигает
STEP_RISE_PX = 3               # подъём строки при наведении (целые px!)
FPS = 60                       # требуемая плавность

# ══════════════════════════════════════════════════════════════════════════
#  ПАЛИТРЫ: одна таблица на тему (rgba; альфа 255 — непрозрачно)
# ══════════════════════════════════════════════════════════════════════════
DARK = {
    # ── источники: docs/menu-mockup.html:26-64 (MAK/CARD) ──
    "bg": (26, 27, 31, 245),        # rgba(26,27,31,.96)    --card
    "surface": (38, 40, 46, 245),   # rgba(38,40,46,.96)    --card2 (подсветка)
    "txt": (242, 243, 245, 255),    # #f2f3f5               --txt
    "muted": (162, 166, 176, 255),  # #a2a6b0               --muted
    "cur": (232, 234, 238, 255),    # #e8eaee               --cur (наведённая строка)
    "foot": (139, 144, 154, 255),   # #8b909a               --foot («только показ»)
    "ms": (207, 211, 218, 255),     # .step .ms
    "line": (255, 255, 255, 26),    # rgba(255,255,255,.10) --line (рамка/разделитель)
    "mark": (255, 255, 255, 56),    # rgba(255,255,255,.22) --mk (кольцо маркера)
    "track": (255, 255, 255, 26),   # .bar (дорожка полосы времени)
    "spin_bg": (255, 255, 255, 36),  # .spin border rgba(255,255,255,.14)
    "tick": (26, 27, 31, 255),      # «вырез» галочки = фон панели
    "state_ok": (65, 194, 109, 255),    # --ok   #41c26d
    "state_error": (220, 79, 85, 255),  # --err  #dc4f55
    "state_busy": (229, 191, 55, 255),  # --busy #e5bf37
    "state_off": (148, 151, 160, 255),  # --off  #9497a0
    "track_bg": (0, 0, 0, 140),
}
LIGHT = {
    # ── источники: src/theme.py PALETTES['light'] (TH) ──
    "bg": (250, 250, 250, 245),     # TH card_bg (250,250,250)
    "surface": (237, 237, 237, 245),   # выведено: фон −13 (в тёмной +13)
    "txt": (24, 26, 28, 255),       # TH title
    "muted": (70, 74, 78, 255),     # TH body
    "cur": (24, 26, 28, 255),       # роль .cur в светлой = title
    "foot": (108, 110, 116, 255),   # TH state_off — приглушённый
    "ms": (86, 90, 94, 255),        # выведено: между title и body
    "line": (0, 0, 0, 26),          # зеркало --line
    "mark": (0, 0, 0, 56),          # зеркало --mk
    "track": (0, 0, 0, 26),
    "spin_bg": (0, 0, 0, 36),
    "tick": (250, 250, 250, 255),   # «вырез» галочки = фон панели
    "state_ok": (24, 128, 56, 255),     # TH state_ok    (#188038)
    "state_error": (193, 34, 33, 255),  # TH state_error (#c12221)
    "state_busy": (176, 122, 0, 255),   # TH state_busy  (#b07a00)
    "state_off": (108, 110, 116, 255),  # TH state_off   (#6c6e74)
    "track_bg": (0, 0, 0, 90),
}
THEMES = ("dark", "light")


def theme_of(cfg: dict = None) -> str:
    """Тема для отрисовки: по ``ui.theme`` (auto|light|dark), через :mod:`theme`."""
    try:
        import theme as TH
        return "light" if TH.choice(cfg) == "light" else "dark"
    except Exception:
        try:
            v = str(((cfg or {}).get("ui") or {}).get("theme", "auto")).lower()
        except Exception:
            v = "auto"
        return "light" if v == "light" else "dark"


def tokens(cfg: dict = None, theme: str = None) -> dict:
    """ОДНА таблица токенов на тему (копия — вызывающий может её править)."""
    t = str(theme or theme_of(cfg)).lower()
    return dict(LIGHT if t == "light" else DARK)


def rgba(t, alpha: int = None) -> tuple:
    """Токен → (r,g,b[,a]) с ЗАДАННОЙ альфой (None — как в таблице)."""
    v = tuple(int(x) for x in t)
    if alpha is None:
        return v
    return (v[0], v[1], v[2], int(alpha))


# ── смешение цвета: ОДИН проход отрисовки с интерполированным цветом ────────
def lerp(a: float, b: float, k: float) -> float:
    if k <= 0.0:
        return float(a)
    if k >= 1.0:
        return float(b)
    return float(a) + (float(b) - float(a)) * float(k)


def lerp_color(c1, c2, k: float) -> tuple:
    """Цвет между ``c1`` и ``c2`` темпом ``k`` (0..1) — для ОДНОГО слоя текста."""
    k = 0.0 if k < 0.0 else (1.0 if k > 1.0 else float(k))
    a1 = tuple(c1) if len(c1) > 3 else tuple(c1) + (255,)
    a2 = tuple(c2) if len(c2) > 3 else tuple(c2) + (255,)
    return tuple(int(round(lerp(a1[i], a2[i], k))) for i in range(4))


# ── кривые макета (cubic-bezier): у Qt их нет ──────────────────────────────
def bezier_y(t: float, p1x: float, p1y: float, p2x: float, p2y: float) -> float:
    """Значение кривой ``cubic-bezier`` при x=t — как считает браузер в макете.

    Решаем x(u)=t делением отрезка пополам (30 шагов — точнее кадра) и берём y(u).
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(30):
        u = (lo + hi) / 2.0
        x = 3.0 * (1 - u) * (1 - u) * u * p1x + 3.0 * (1 - u) * u * u * p2x + u * u * u
        if x < t:
            lo = u
        else:
            hi = u
    u = (lo + hi) / 2.0
    return (3.0 * (1 - u) * (1 - u) * u * p1y + 3.0 * (1 - u) * u * u * p2y
            + u * u * u)


def ease_out_cubic(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - pow(1.0 - t, 3.0)


def ease_bar(p: float) -> float:
    """Кривая полосы времени карточки: easeInOutQuad."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return 2.0 * p * p if p < 0.5 else 1.0 - pow(-2.0 * p + 2.0, 2.0) / 2.0


# ── геометрия: целые пиксели устройства (без «поехавших» полупикселей) ─────
def snap(v: float, dpr: float = 1.0) -> float:
    """Округляет координату до пикселя ЭКРАНА (с учётом масштаба монитора).

    При масштабе 100 % (dpr=1) это обычное округление: все числа макета
    остаются целыми. При 125/150/175 % координата ложится на пиксель экрана,
    поэтому 1 px рамки и 3 px полоса не «худеют» и не размазываются.
    """
    try:
        d = float(dpr) or 1.0
    except Exception:
        d = 1.0
    return round(float(v) * d) / d


def frame_period(fps: int = FPS, refresh_hz: float = 0.0) -> dict:
    """Период кадров, ВЫРОВНЕННЫЙ по частоте обновления экрана.

    Требование пользователя — 60 кадров/с. Но кадр, попадающий между
    обновлениями панели, показывается не сразу: на 200 Гц панели период
    16.667 мс даёт «пилу» из 15 и 20 мс (дёрганье). Поэтому берём НАИБОЛЬШЕЕ
    целое число периодов обновления, укладывающееся в 1/60 с: кадры идут
    равномерно, а плавность не ниже заявленной (200 Гц → 15 мс → 66.7 к/с).

    -> {"period_ms", "fps", "refresh_hz", "aligned", "ticks"}
    """
    want_ms = 1000.0 / float(max(1, int(fps)))
    out = {"period_ms": round(want_ms, 4), "fps": float(fps), "refresh_hz": 0.0,
           "aligned": False, "ticks": 1}
    try:
        hz = float(refresh_hz or 0.0)
    except Exception:
        hz = 0.0
    if hz < 30.0:
        return out                     # частоту не знаем — обычная сетка 60 к/с
    ref_ms = 1000.0 / hz
    ticks = int(want_ms / ref_ms)
    if ticks < 1:
        ticks = 1
    period = ref_ms * ticks
    if period > want_ms * 1.25:        # запас не больше 25 % — иначе медленнее 48 к/с
        ticks = 1
        period = ref_ms
    out.update({"period_ms": round(period, 4),
                "fps": round(1000.0 / period, 2),
                "refresh_hz": round(hz, 2),
                "ticks": ticks})
    out["aligned"] = True
    return out


def selfcheck() -> list:
    """Проверки самого модуля (без Qt): таблицы тем, кривые, выравнивание кадров."""
    res = []

    def chk(name, ok, detail=""):
        res.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    need = set(DARK)
    chk("таблицы тем имеют одинаковый набор ключей",
        set(LIGHT) == need, str(sorted(need ^ set(LIGHT))))
    chk("тёмная панель = цвета макета (26,27,31,.96)",
        DARK["bg"] == (26, 27, 31, 245) and DARK["surface"] == (38, 40, 46, 245),
        f'{DARK["bg"]} / {DARK["surface"]}')
    chk("светлая панель = card_bg из theme.py", LIGHT["bg"][:3] == (250, 250, 250),
        str(LIGHT["bg"]))
    try:
        import theme as TH
        p = TH.PALETTES["light"]
        chk("светлые состояния = theme.py дословно",
            LIGHT["state_ok"][:3] == tuple(p["state_ok"])
            and LIGHT["state_error"][:3] == tuple(p["state_error"])
            and LIGHT["state_busy"][:3] == tuple(p["state_busy"])
            and LIGHT["state_off"][:3] == tuple(p["state_off"])
            and LIGHT["txt"][:3] == tuple(p["title"])
            and LIGHT["muted"][:3] == tuple(p["body"]),
            f'{LIGHT["state_ok"][:3]} {LIGHT["txt"][:3]} {LIGHT["muted"][:3]}')
    except Exception as e:
        chk("theme.py доступен", False, str(e))
    chk("кривая входа монотонна и доходит до 1",
        all(bezier_y(i / 20.0, *CB_IN) <= bezier_y((i + 1) / 20.0, *CB_IN) + 1e-9
            for i in range(20)) and abs(bezier_y(1.0, *CB_IN) - 1.0) < 1e-9,
        f"y(1)={bezier_y(1.0, *CB_IN):.6f}")
    chk("кривая закрытия (ease-in) стартует медленно",
        bezier_y(0.25, *CB_OUT) < 0.25, f"y(0.25)={bezier_y(0.25, *CB_OUT):.4f}")
    f200 = frame_period(60, 200.0)
    chk("на 200 Гц период кратен обновлению и не хуже 60 к/с",
        f200["aligned"] and abs(f200["period_ms"] - 15.0) < 0.01 and f200["fps"] >= 60.0,
        f'{f200["period_ms"]} мс, {f200["fps"]} к/с')
    f60 = frame_period(60, 60.0)
    chk("на 60 Гц период равен 16.667 мс (ровно 60 к/с)",
        abs(f60["period_ms"] - 1000.0 / 60.0) < 0.01,
        str(f60["period_ms"]))
    chk("без данных о частоте период = 1/60 с",
        abs(frame_period(60, 0.0)["period_ms"] - 1000.0 / 60.0) < 0.001,
        str(frame_period(60, 0.0)["period_ms"]))
    chk("целые пиксели при 100 %: snap не сдвигает числа макета",
        snap(34, 1.0) == 34 and snap(12, 1.0) == 12 and snap(3, 1.0) == 3,
        f'{snap(34, 1.0)}/{snap(12, 1.0)}/{snap(3, 1.0)}')
    chk("при 150 % координата ложится на пиксель экрана",
        abs(snap(34, 1.5) * 1.5 - round(34 * 1.5)) < 1e-9,
        f'{snap(34, 1.5)} (device {snap(34, 1.5) * 1.5})')
    chk("интерполяция цвета: k=0 и k=1 дают концы, k=0.5 — середину",
        lerp_color((0, 0, 0), (100, 200, 40), 0.0) == (0, 0, 0, 255)
        and lerp_color((0, 0, 0), (100, 200, 40), 1.0) == (100, 200, 40, 255)
        and lerp_color((0, 0, 0), (100, 200, 40), 0.5) == (50, 100, 20, 255),
        str(lerp_color((0, 0, 0), (100, 200, 40), 0.5)))
    # Наведение мышью: задержки подменю и темп подсветки — в требуемых окнах.
    # Мусор в этих числах ломает ровно то, о чём просил пользователь: подменю
    # мигает при проносе курсора, а подсветка отстаёт.
    chk("подменю по наведению: задержка 120-180 мс, льготное окно 250-300 мс",
        120 <= SUB_HOVER_OPEN_MS <= 180 and 250 <= SUB_HOVER_CLOSE_MS <= 300,
        f"открытие {SUB_HOVER_OPEN_MS} мс, закрытие {SUB_HOVER_CLOSE_MS} мс")
    chk("подсветка мышью переносится мгновенно: темп 40-60 мс, клавиатура плавно",
        40 <= HL_MOUSE_MS <= 60 and STEP_FADE_MS >= HL_MOUSE_MS * 2,
        f"мышь {HL_MOUSE_MS} мс, клавиши {STEP_FADE_MS} мс")
    return res


if __name__ == "__main__":       # тест без Qt: python src/ui_style.py
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    rows = selfcheck()
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print(("OK   " if r["ok"] else "СБОЙ ") + r["name"] + (("  " + r["detail"]) if r["detail"] else ""))
    print(f"ИТОГ: {'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' if not bad else 'ПРОВАЛЕНО ' + str(len(bad))}")
    sys.exit(1 if bad else 0)
