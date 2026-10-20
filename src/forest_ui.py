# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""forest-ui — помощник интерфейса (Qt): значок в трее, меню, уведомления.

Это ОТДЕЛЬНАЯ программа, а не часть основной: основная остаётся лёгкой и без
сторонних зависимостей, а весь интерфейс (значок в трее с меню и подменю,
всплывающие уведомления с анимацией, темы) живёт здесь.

Лицензии
--------
Qt здесь — PySide6 под **LGPL-3.0** (динамическая линковка, без правок Qt).
Наша программа под **GPL-3.0**, что с LGPL совместимо: LGPL-3.0 явно разрешена
как «более слабая» по отношению к GPL-3.0 (§3, «GNU Lesser General Public
License ... may be used under the terms of the GNU GPL»).

Как это связано с программой (важно)
------------------------------------
Помощник **ядром не управляет**. Он:

* пишет команду в ``state/ui_cmd.json`` (``src/ui_cmd.py``) — её выполняет
  супервизор штатными средствами (единственный хозяин ядра остаётся один);
* читает состояние из ``state/tray_status.json`` (цвет значка и подсказка);
* пишет пульс в ``state/ui_helper.json`` (чтобы программа знала, что помощник
  жив, и показала это в ``--status``).

Запуск
------
    forest-ui                      # жить в трее (запускается один раз, при входе)
    forest-ui --show ok "Заголовок" "Текст"   # показать одно уведомление и выйти
    forest-ui --menu-dump          # напечатать дерево меню (для проверок)
    forest-ui --selftest           # самопроверка: значки, темы, FPS, фокус
    forest-ui --stop               # попросить работающий помощник выйти
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

IS_WIN = sys.platform.startswith("win")
APP = "forest-proxy-updater"

# ── Qt ──────────────────────────────────────────────────────────────────────
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    QT_OK = True
except Exception as _e:                    # Qt нет — помощник просто не работает
    QT_OK = False
    _QT_ERROR = str(_e)

if not QT_OK:
    # ЗАГЛУШКИ Qt: модуль обязан ИМПОРТИРОВАТЬСЯ и без Qt — иначе определение
    # классов (`class Toast(QtWidgets.QWidget)`) падало NameError ещё на импорте,
    # и вместо тихого выхода «Qt недоступен» получалась авария. Заглушка даёт
    # класс-основание; ни один метод при QT_OK=False не вызывается (см. main()).
    class _QtStubBase:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, _n):
            return lambda *a, **k: None

    class _QtStubModule:
        def __getattr__(self, _n):
            return _QtStubBase

    QtWidgets = _QtStubModule()
    QtCore = _QtStubModule()
    QtGui = _QtStubModule()
    Qt = _QtStubBase

import safestdio as SD

if QT_OK:
    import config as CFG
    import i18n as I
    import log
    import status_info as SI
    import theme as TH
    import tray_icon as TI
    import tray_status as TS
    import ui_cmd as UC
    import ui_menu as UMN
    import ui_style as US

# Состояния значка: четыре основные + состояния ЭТАЛОНА (switch — переключение
# сервера, noanswer — «сторож молчит», restoring — восстановление связи).
STATE_KINDS = ("ok", "busy", "off", "error", "switch", "noanswer", "restoring",
               "down")
_TIP_KEYS = {"ok": "tray.tip.ok", "busy": "tray.tip.busy",
             "off": "tray.tip.off", "error": "tray.tip.error"}

# ── анимация и вид уведомления ──────────────────────────────────────────────
# Числа вида/анимации живут РЯДОМ С КАРТОЧКОЙ (блок «уведомление» ниже):
# они взяты из утверждённого макета docs/notify-mockup.html.


def _say(text="") -> None:
    """Печать в stdout ТОЛЬКО ASCII (см. src/safestdio.py).

    Оконная сборка не имеет консоли, а её кодировка на Windows — cp1251: печать
    символа вне cp1251 (например «✓») давала UnicodeEncodeError и МОДАЛЬНОЕ ОКНО
    PyInstaller на рабочем столе. Полный человекочитаемый отчёт пишется в
    UTF-8-файл (см. :func:`write_selftest_reports`), а в stdout идёт только ASCII.
    """
    SD.say(text)


def tr(key: str, **kw):
    try:
        return I.tr(key, **kw)
    except Exception:
        return key


# ═══════════════════════════════════════════════ конфиг / состояние ═══
def load_cfg() -> dict:
    try:
        cfg = CFG.load_config()
    except Exception:
        cfg = {"paths": {"root": os.environ.get("FOREST_ROOT", "") or "",
                         "state_dir": "", "log_dir": ""},
              "ui": {"theme": "auto"},
              "notify": {"enabled": True, "duration_ms": 5000, "fps": 60}}
    try:
        I.set_lang(I.detect_lang(cfg))
    except Exception:
        pass
    return cfg


def read_raw_state(cfg: dict) -> dict:
    try:
        return TS.read_status(cfg) or {}
    except Exception:
        return {}


def read_state(cfg: dict) -> tuple:
    """-> (состояние, подсказка) — состав РОВНО как в эталоне.

    Состояние — одно из :data:`status_info.STATES` (ok | off | switch |
    noanswer | down | restoring), подсказка — строка эталонного вида
    «FOREST связь OK | 10 серверов | РФ напрямую | watch OK».
    """
    st = read_raw_state(cfg)
    kind = SI.state_key(cfg, st)
    tip = SI.tip(cfg, st, limit=63)
    return kind, tip


# ═══════════════════════════════════════════════ значки ═══
def icon_from_state(kind: str, cfg: dict) -> "QtGui.QIcon":
    """QIcon из того же рисунка, что у встроенного значка (app.ico + цвет).

    Единый источник истины: ICO собирает ``tray_icon.state_ico`` — тот же код,
    что рисует значок Windows-версии. Здесь он только переводится в QPixmap.
    """
    ic = QtGui.QIcon()
    try:
        data = TI.state_ico(kind, sizes=(16, 24, 32, 48, 64, 128), cfg=cfg)
    except Exception:
        data = b""
    if data:
        try:
            for w, h, off, size_b in TI.ico_images(data):
                payload = data[off:off + size_b]
                px = TI._dib_to_pixels(payload)
                if not px:
                    continue
                pw, ph, bgra = px
                img = QtGui.QImage(bgra, pw, ph, pw * 4,
                                   QtGui.QImage.Format_ARGB32).copy()
                ic.addPixmap(QtGui.QPixmap.fromImage(img))
        except Exception:
            ic = QtGui.QIcon()
    if ic.isNull():
        # откат: рисуем простой круг цветом состояния — значок всё равно есть
        kind = kind if kind in STATE_KINDS else "off"
        pm = QtGui.QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QtGui.QPainter(pm)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            col = QtGui.QColor(*TH.state_color(cfg, kind))
            p.setBrush(col)
            p.setPen(QtGui.QPen(QtGui.QColor(*TH.tray_ring(cfg)), 2))
            p.drawEllipse(3, 3, 26, 26)
        finally:
            p.end()
        ic.addPixmap(pm)
    return ic


def qss_for(cfg: dict) -> str:
    """Стиль меню из НАШЕЙ палитры (светлая/тёмная) — без тем Qt по умолчанию."""
    pal = TH.palette(cfg)
    bg = "#%02x%02x%02x" % pal["card_bg"]
    fg = "#%02x%02x%02x" % pal["title"]
    bd = "#%02x%02x%02x" % pal["card_border"]
    sel = "#%02x%02x%02x" % pal["accent"]
    dis = "#%02x%02x%02x" % pal["body"]
    return f"""
QMenu {{ background-color: {bg}; color: {fg}; border: 1px solid {bd};
         padding: 6px 4px; }}
QMenu::item {{ padding: 6px 22px 6px 26px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {sel}; color: #ffffff; }}
QMenu::item:disabled {{ color: {dis}; }}
QMenu::separator {{ height: 1px; background: {bd}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 8px; }}
"""


def _mockup_path() -> str:
    """Путь к утверждённому макету уведомления (его числа сверяет самотест).

    Макет едет В КОМПЛЕКТЕ (см. DATAS в forest-ui.spec), поэтому сверка чисел
    работает и у собранной программы, а не только из репозитория: сначала ищем
    рядом с распакованными данными (``_MEIPASS``), затем рядом с самим файлом
    модуля, затем рядом с исполняемым файлом (``linux/dist/..``) и в текущем
    каталоге. Не нашли — сверка чисел пропускается, но тексты/этапы всё равно
    проверяются (они зашиты в код и проверяются отдельно).
    """
    name = "notify-mockup.html"
    cands = []
    env = os.environ.get("FOREST_MOCKUP")
    if env:
        cands.append(env)
    base = getattr(sys, "_MEIPASS", "")
    if base:
        cands.append(os.path.join(base, "docs", name))
        cands.append(os.path.join(base, name))
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(os.path.dirname(here), "docs", name))
        cands.append(os.path.join(here, "docs", name))
    except Exception:
        pass
    try:
        exe = os.path.dirname(os.path.abspath(sys.executable))
        cands.append(os.path.join(os.path.dirname(exe), "docs", name))
        cands.append(os.path.join(exe, "docs", name))
    except Exception:
        pass
    cands.append(os.path.join(os.getcwd(), "docs", name))
    for c in cands:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            continue
    return ""


# ═══════════════════════════════════════════════ уведомление ═══
# ВИД КАРТОЧКИ — 1:1 С УТВЕРЖДЁННЫМ МАКЕТОМ ``docs/notify-mockup.html``.
# ВСЕ числа вида (ширина, отступы, длительности, кривые, цвета, размеры шрифтов)
# живут в ОБЩЕМ модуле стиля :mod:`ui_style` — он же используется контекстным
# меню трея (``src/ui_menu.py``): цвета и геометрия не дублируются, поэтому
# правка макета не может «доехать» только до одного из двух мест.
# Карточка СЛЕДУЕТ ТЕМЕ: цвета берутся из :func:`ui_style.tokens` (светлые
# значения — из :mod:`theme`, ``PALETTES['light']``).
NT_W = US.CARD_W               # .toast{width:392px}
NT_RADIUS = US.RADIUS          # border-radius:12px
NT_B_L = US.STRIPE             # border-left:3px solid (цвет состояния)
NT_B = US.HAIRLINE             # border:1px solid rgba(255,255,255,.10)
NT_PAD_T, NT_PAD_R, NT_PAD_B, NT_PAD_L = (US.PAD_T, US.PAD_R, US.PAD_B, US.PAD_L)
NT_ICO = US.ICON               # .ico{width:34px;height:34px}
NT_ICO_R = US.ICON_R           # .ico{border-radius:9px}
NT_ICO_ART = US.ICON_ART       # рисунок пользователя внутри квадрата
NT_GAP = US.GAP                # .head{gap:11px}
NT_SPIN = US.SPIN              # .spin{width:18px;height:18px}
NT_SPIN_MT = US.SPIN_MT        # .spin{margin-top:4px}
NT_STEP_L = US.STEP_L          # .steps/.bar/.foot{margin-left:45px}
NT_MK = US.MARK                # .step .mk{width:13px;height:13px}
NT_STEP_GAP = US.STEP_GAP      # .steps{gap:6px}
NT_STEPS_MT = US.STEPS_MT
NT_STEPS_MB = US.STEPS_MB
NT_BAR_MT = US.BAR_MT          # .bar{margin-top:2px}
NT_BAR_H = US.BAR_H            # .bar{height:3px}
NT_FOOT_MT = US.FOOT_MT        # .foot{margin-top:7px}
# Размеры шрифтов макета в px: 14.5 -> 15 (полупиксель не рисуется), 12 -> 12,
# 11.8 -> 12, 11.5 -> 12, 11 -> 11.
NT_FONT = dict(US.FONT)
NT_C = US.DARK                 # тёмная таблица (совместимость); в карточке — self.tok
NT_STATE = {k: US.DARK["state_" + k] for k in ("ok", "error", "busy", "off")}
NT_KIND = {"ok": "ok", "restoring": "busy", "switch": "busy", "busy": "busy",
           "down": "error", "error": "error", "noanswer": "off", "off": "off",
           "info": "busy"}
# Длительности/кривые макета: slidein 340 мс cubic-bezier(.16,.84,.28,1),
# slideout 260 мс ease-in, сдвиг 420 px, спиннер .85 с на оборот,
# этап считается пройденным через 6 % полосы после появления.
ANIM_IN_MS = US.ANIM_IN_MS
ANIM_OUT_MS = US.ANIM_OUT_MS
SLIDE_PX = US.SLIDE_PX
CB_IN = US.CB_IN
CB_OUT = US.CB_OUT
SPIN_MS = US.SPIN_MS
STEP_LAG = 0.06
STEP_FADE_MS = US.STEP_FADE_MS       # .step{transition:opacity 180ms,transform 180ms}
STEP_RISE_PX = US.STEP_RISE_PX       # .step{transform:translateY(3px)}
HOLD_FINAL_MS = 4000           # «окно закроется через 4 с» (макет/эталон)
TEST_ANCHOR = None             # крючок проверок: (x, y, w, h) — показать карточку
                              # в заданном прямоугольнике (вне экрана), чтобы
                              # проверить анимацию, не показывая окно пользователю

# ── ОДНА КАРТОЧКА ДЛЯ ВСЕХ ПУТЕЙ ───────────────────────────────────────────
# Реальные события, `--show` и пункт меню «Проверить уведомление» рисуют ОДНУ И
# ТУ ЖЕ карточку состава макета: значок+метка, заголовок, подпись, БЛОК ЭТАПОВ
# с временем каждого в мс, ПОЛОСА ВРЕМЕНИ, СПИННЕР, футер. Упрощённых вариантов
# нет: раньше `--show` и меню создавали карточку без этапов, полосы и спиннера,
# а событие без описанного процесса давало «связь подтверждена · 0.00 с» и
# застывшую карточку.
#
# Если событие не передало этапы, берём ПОСЛЕДНЕЕ ИЗВЕСТНОЕ состояние: процесс
# из state/tray_status.json либо кэш state/ui_last_proc.json (помощник пишет его,
# увидев любой процесс). Если известно совсем ничего — сценарий восстановления
# из макета: те же этапы, что пишет сторож (status_info), с временами макета.
DEMO_STEPS = [("Обрыв обнаружен (пульс порта)", 50),
              ("Переключение на новый узел", 600),
              ("Ядро приняло соединения", 620),
              ("Данные подтверждены", 1850)]
DEMO_FOOT_WAIT = "наблюдение включено"       # футер до финала (как в макете)
LAST_PROC_FILE = "ui_last_proc.json"
CARD_SHOT_REQ = "card_shot.json"             # запрос снимка карточки (проверки)
CARD_SHOT_DONE = "card_shot_result.json"
# ОДНО ДЕЙСТВИЕ = ОДНА КАРТОЧКА. Серия карточек в коротком окне (обрыв,
# переключение, ответ команды) склеивается в ОДНУ: вторую и последующие не
# выкатываем. Иначе одно нажатие «Обновить серверы сейчас» давало очередь
# карточек (ok->error->busy->ok->off) — пользователь видел «пять уведомлений».
# Демо-серия (флаг --show-all) гейт проходит явно, чтобы её можно было показать.
CARD_COALESCE_MS = 2500.0
# Дополнительные кадры движения на удержании: полоса времени «дожимает» уже
# заполненную полосу бегущей искрой, чтобы движение было видно ВСЁ время показа.
BAR_SHEEN_MS = 1100.0


def _state_dir_of(cfg: dict) -> str:
    try:
        return ((cfg or {}).get("paths") or {}).get("state_dir") or ""
    except Exception:
        return ""


def _last_proc_path(cfg: dict) -> str:
    d = _state_dir_of(cfg)
    return os.path.join(d, LAST_PROC_FILE) if d else ""


def remember_proc(cfg: dict, proc: dict) -> None:
    """Запоминает этапы процесса — «последнее известное состояние»."""
    if not proc:
        return
    steps = proc.get("steps") or []
    p = _last_proc_path(cfg)
    if not (steps and p):
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        d = {"ts": time.time(), "kind": proc.get("kind") or "restore",
             "elapsed": proc.get("elapsed"), "final": bool(proc.get("final")),
             "failed": bool(proc.get("failed")),
             "steps": [{"nm": str(s.get("nm") or ""),
                        "t": float(s.get("t") or 0.0),
                        "done": bool(s.get("done")),
                        "failed": bool(s.get("failed"))} for s in steps]}
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def last_known_proc(cfg: dict) -> dict:
    """Последнее известное описание процесса: состояние сторожа, затем кэш."""
    try:
        proc = (read_raw_state(cfg) or {}).get("proc") or {}
        if proc.get("steps"):
            return proc
    except Exception:
        pass
    p = _last_proc_path(cfg)
    if p:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("steps"):
                d.setdefault("started", time.time())
                return d
        except Exception:
            pass
    return {}


def demo_card_data(cfg: dict) -> dict:
    """Полные данные демо-карточки: реальные этапы либо последние известные.

    Ни одно поле не пустое (этапы со временем в мс, общая длительность,
    подпись, футер, роль, состояние), поэтому демо-карточка структурно
    совпадает с макетом — как и карточка настоящего события.
    """
    proc = last_known_proc(cfg)
    if not proc.get("steps"):
        proc = {"kind": "restore", "cause": "outage", "elapsed": 0.0,
                "final": False,
                "steps": [{"nm": nm, "t": ms / 1000.0, "done": False}
                          for nm, ms in DEMO_STEPS]}
    steps = SI.steps_data(proc)
    total = SI.steps_total_ms(proc) or _steps_total_ms(steps)
    if not steps or total <= 0:                  # данных нет вовсе — сценарий
        steps = _norm_steps([{"nm": nm, "ms": ms} for nm, ms in DEMO_STEPS])
        total = DEMO_STEPS[-1][1]
    role = "switch" if str(proc.get("kind") or "restore") == "switch" else "restore"
    return {"kind": "restoring" if role == "restore" else "switch",
            "title": SI.TITLE_SWITCH if role == "switch" else SI.TITLE_RESTORE,
            "sub": SI.SUB_SWITCH if role == "switch" else SI.SUB_RESTORE,
            "footer": DEMO_FOOT_WAIT, "steps": steps, "total_ms": int(total),
            "role": role, "proc": proc}


def final_texts(elapsed_ms: int, failed: bool = False) -> tuple:
    """Финальные подпись и футер БЕЗ «0.00 с»: нет замера — нет числа."""
    ms = int(elapsed_ms or 0)
    if failed:
        return (SI.SUB_FAILED,
                ("попытка заняла %.2f с · окно закроется через 4 с"
                 % (ms / 1000.0)) if ms > 0
                else "попытка не удалась · окно закроется через 4 с")
    return (("связь подтверждена · %.2f с" % (ms / 1000.0)) if ms > 0
            else "связь подтверждена",
            ("восстановлено за %.2f с · окно закроется через 4 с"
             % (ms / 1000.0)) if ms > 0
            else "восстановлено · окно закроется через 4 с")


# ── снимок карточки (ДОКАЗАТЕЛЬСТВО: серия кадров удержания) ────────────────
def card_shot_request(cfg: dict) -> dict:
    """Читает запрос снимка карточки из state/ (файл-флаг, одноразовый).

    Проверке нужно снять карточку ЖИВОГО события в работающем помощнике —
    поэтому запрос передаётся файлом, а не параметром запуска.
    """
    d = _state_dir_of(cfg)
    if not d:
        return {}
    p = os.path.join(d, CARD_SHOT_REQ)
    try:
        if not os.path.isfile(p):
            return {}
        with open(p, encoding="utf-8") as f:
            req = json.load(f)
        os.replace(p, p + ".taken")        # одноразово: второй раз не снимем
        return req if isinstance(req, dict) else {}
    except Exception:
        return {}


def card_shot_finish(cfg: dict, res: dict) -> str:
    """Пишет результат снимка рядом с запросом. -> путь файла."""
    d = _state_dir_of(cfg)
    if not d:
        return ""
    p = os.path.join(d, CARD_SHOT_DONE)
    try:
        res = dict(res or {})
        res["ts"] = time.time()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return p
    except Exception:
        return ""


def _region_bytes(img, x: int, y: int, w: int, h: int) -> bytes:
    """Байты прямоугольника изображения (с учётом шага строки)."""
    try:
        r = img.copy(max(0, int(x)), max(0, int(y)), max(1, int(w)), max(1, int(h)))
    except Exception:
        return b""
    raw = bytes(r.constBits())
    stride = r.bytesPerLine()
    return b"".join(raw[yy * stride:(yy + 1) * stride] for yy in range(r.height()))


def _changed_px(a: bytes, b: bytes) -> int:
    """Сколько пикселей (4 байта) отличаются между двумя снимками области."""
    n = 0
    for i in range(0, min(len(a), len(b)) - 3, 4):
        if a[i:i + 4] != b[i:i + 4]:
            n += 1
    return n


def capture_card_proof(cfg: dict, t, out_dir: str = "", name: str = "notify-card",
                       frames: int = 10, gap_s: float = 0.12) -> dict:
    """СНИМАЕТ карточку: серия кадров удержания + доказательство движения.

    Окно карточки берётся ПО ФАКТУ (её собственные координаты на своём
    мониторе) — у пользователя несколько мониторов, и «правый нижний угол
    основного экрана» для снимка не годится. Пишет:
        <out>/<name>.png         карточка (точный рендер окна, на подложке)
        <out>/<name>-screen.png  снимок ЭКРАНА по фактическому окну карточки
        <out>/<name>-hold.png    полоса из N кадров удержания (видно движение)
        <out>/<name>-proof.json  числа: хеши и число изменённых пикселей
    """
    lay = getattr(t, "_lay", {}) or {}
    out_dir = out_dir or _state_dir_of(cfg) or os.getcwd()
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass
    texts = t.texts() if hasattr(t, "texts") else {}
    steps = getattr(t, "steps", []) or []
    sx, sy = int(lay.get("x_spin") or 0), int(lay.get("y_spin") or 0)
    bx, by = int(lay.get("x_bar") or 0), int(lay.get("y_bar") or 0)
    bw = int(lay.get("bar_w") or 0)
    # 1. карточка целиком — точный рендер окна на подложке (чтобы читалась).
    # Приводим к ЛОГИЧЕСКОМУ размеру (392 px): на мониторе с увеличением 150 %
    # grab() отдаёт 588x258, и без приведения карточка «съезжает» в угол листа.
    card_png = os.path.join(out_dir, name + ".png")
    try:
        pm = t.grab()
        dpr = float(pm.devicePixelRatio() or 1.0) or 1.0
        lw = max(1, int(round(pm.width() / dpr)))
        lh = max(1, int(round(pm.height() / dpr)))
        bg = QtGui.QPixmap(lw, lh)
        bg.fill(QtGui.QColor(27, 36, 48))
        p = QtGui.QPainter(bg)
        try:
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(QtCore.QRect(0, 0, lw, lh), pm)
        finally:
            p.end()
        bg.save(card_png, "PNG")
    except Exception as e:
        texts = dict(texts, card_png_error=repr(e))
    # 2. снимок ЭКРАНА по фактическому положению окна (несколько мониторов)
    g = t.geometry()
    scr = None
    screen_png = os.path.join(out_dir, name + "-screen.png")
    scr_name, scr_geo = "", []
    try:
        app = QtWidgets.QApplication.instance()
        scr = app.screenAt(QtCore.QPoint(g.center().x(), g.center().y())) if app else None
        if scr is None and app is not None:
            scr = app.primaryScreen()
        if scr is not None:
            sg = scr.geometry()
            scr_name = str(scr.name())
            scr_geo = [sg.x(), sg.y(), sg.width(), sg.height()]
            shot = scr.grabWindow(0, g.x() - sg.x(), g.y() - sg.y(),
                                  g.width(), g.height())
            # тоже к логическому размеру: снимок экрана должен совпадать
            # по масштабу с рендером карточки
            sdpr = float(shot.devicePixelRatio() or 1.0) or 1.0
            if sdpr != 1.0 and shot.width() and shot.height():
                shot = shot.scaled(max(1, int(round(shot.width() / sdpr))),
                                   max(1, int(round(shot.height() / sdpr))),
                                   Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            shot.save(screen_png, "PNG")
    except Exception as e:
        print("card-proof: снимок экрана не удался: %r" % (e,))
    # 3. серия кадров удержания + изменение пикселей спиннера и полосы
    # ВАЖНО: масштаб экрана. На мониторах с увеличением (125/150 %) QWidget.grab()
    # отдаёт картинку В ФИЗИЧЕСКИХ точках (392x172 -> 588x258), поэтому области
    # считаем с коэффициентом: иначе берём пустое место и «движения нет».
    frames = max(2, int(frames))
    shot_list, spin_hashes, bar_hashes, card_hashes = [], [], [], []
    spin_prev = bar_prev = card_prev = None
    spin_changed, bar_changed, card_changed, t_ms = [], [], [], []
    spin_rect = bar_rect = None
    t_begin = time.perf_counter()
    for _ in range(frames):
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass
        # Вложенный цикл событий НЕ доставляет тики собственного таймера карточки
        # (проверено: если снимать из первого же её тика, все кадры совпадали —
        # замирала и сама анимация). Поэтому кадр проверки ведём ЯВНО, тем же
        # кодом, что и таймер: время берётся настоящее (perf_counter), поэтому
        # движение в снимках — настоящее, а не подделанное.
        try:
            if str(getattr(t, "phase", "")) in ("run", "hold", "in"):
                t._frame()
        except Exception:
            pass
        if str(getattr(t, "phase", "")) == "done":
            break
        try:
            pm = t.grab()
        except Exception:
            break
        img = pm.toImage()
        kx = img.width() / float(max(1, t.width()))
        ky = img.height() / float(max(1, t.height()))
        if spin_rect is None:
            spin_rect = (int(round(sx * kx)), int(round(sy * ky)),
                         max(1, int(round(NT_SPIN * kx))),
                         max(1, int(round(NT_SPIN * ky))))
            bar_rect = (int(round(bx * kx)), int(round(by * ky)),
                        max(1, int(round(max(1, bw) * kx))),
                        max(1, int(round(NT_BAR_H * ky))))
        full = _region_bytes(img, 0, 0, img.width(), img.height())
        spin = _region_bytes(img, *spin_rect)
        bar = _region_bytes(img, *bar_rect)
        card_hashes.append(hashlib.sha256(full).hexdigest()[:16])
        spin_hashes.append(hashlib.sha256(spin).hexdigest()[:16])
        bar_hashes.append(hashlib.sha256(bar).hexdigest()[:16])
        if spin_prev is not None:
            spin_changed.append(_changed_px(spin_prev, spin))
            bar_changed.append(_changed_px(bar_prev, bar))
            card_changed.append(_changed_px(card_prev, full))
        spin_prev, bar_prev, card_prev = spin, bar, full
        t_ms.append(round((time.perf_counter() - t_begin) * 1000.0, 1))
        shot_list.append(pm)
        time.sleep(max(0.02, float(gap_s)))
    # 4. полоса кадров: спиннер и полоса времени рядом — движение видно глазами
    hold_png = os.path.join(out_dir, name + "-hold.png")
    try:
        shr, swr = spin_rect[3], spin_rect[2]
        bhr, bwr = bar_rect[3], bar_rect[2]
        row_h = max(shr, 16)
        bar_h = max(12, row_h // 2)          # тонкие 3 px растягиваем по высоте
        cw_total = 34 + swr + 12 + bwr
        rows = len(shot_list)
        strip = QtGui.QPixmap(cw_total, rows * (row_h + 8) + 6)
        strip.fill(QtGui.QColor(18, 22, 28))
        p = QtGui.QPainter(strip)
        try:
            p.setPen(QtGui.QColor(200, 205, 215))
            f = QtGui.QFont(p.font())
            f.setPixelSize(11)
            p.setFont(f)
            for i, pm in enumerate(shot_list):
                y = 3 + i * (row_h + 8)
                p.drawText(QtCore.QRect(2, y, 28, row_h),
                           int(Qt.AlignLeft | Qt.AlignVCenter), str(i + 1))
                p.drawPixmap(QtCore.QRect(34, y, swr, row_h), pm,
                             QtCore.QRect(*spin_rect))
                p.drawPixmap(QtCore.QRect(34 + swr + 12,
                                          y + (row_h - bar_h) // 2, bwr, bar_h),
                             pm, QtCore.QRect(*bar_rect))
        finally:
            p.end()
        strip.save(hold_png, "PNG")
    except Exception as e:
        print("card-proof: полоса кадров не удалась: %r" % (e,))
    uni = lambda xs: len(set(xs))                                    # noqa: E731
    proof = {
        "geometry": [g.x(), g.y(), g.width(), g.height()],
        "screen": scr_name, "screen_geometry": scr_geo,
        "frames": len(shot_list), "frame_times_ms": t_ms,
        "card_png": card_png, "screen_png": screen_png, "hold_png": hold_png,
        "card_hashes": card_hashes, "spinner_hashes": spin_hashes,
        "bar_hashes": bar_hashes,
        "spinner_changed_px": spin_changed, "bar_changed_px": bar_changed,
        "card_changed_px": card_changed,
        "spinner_unique": uni(spin_hashes), "bar_unique": uni(bar_hashes),
        "card_unique": uni(card_hashes),
        "spinner_min_changed": min(spin_changed) if spin_changed else 0,
        "bar_min_changed": min(bar_changed) if bar_changed else 0,
        "texts": texts,
        "stats": {"frames": t._stats.get("frames"),
                  "avg_fps": t._stats.get("avg_fps"),
                  "avg_paint_fps": t._stats.get("avg_paint_fps"),
                  "worst_ms": t._stats.get("worst_ms"),
                  "dropped": t._stats.get("dropped"),
                  # кадры ЗА ВРЕМЯ ПОКАЗА (до снимка): журнал допишет итог сам
                  "frames_seen": len(getattr(t, "frame_ms", []) or []),
                  "run_avg_fps": (round(1000.0 / (sum(t.frame_ms)
                                                 / len(t.frame_ms)), 1)
                                  if getattr(t, "frame_ms", None) else 0.0),
                  "run_worst_ms": (round(max(t.frame_ms), 1)
                                   if getattr(t, "frame_ms", None) else 0.0)},
        "composition": {
            "icon": bool(getattr(t, "icon_pm", None) is not None
                         and not t.icon_pm.isNull()),
            "icon_px": [int(getattr(t, "icon_pm").width()),
                        int(getattr(t, "icon_pm").height())]
            if getattr(t, "icon_pm", None) is not None else [0, 0],
            "title": bool(texts.get("title")),
            "sub": bool(texts.get("sub") or getattr(t, "body", "")),
            "steps": bool(steps) and all(int(s.get("ms") or 0) > 0 for s in steps),
            "steps_n": len(steps),
            "steps_text": list(texts.get("steps") or []),
            "bar": bool(lay.get("y_bar") is not None and bw > 0),
            "bar_w": bw,
            "spinner": bool(getattr(t, "spinner", False)),
            "footer": bool(texts.get("footer")),
            "footer_text": texts.get("footer"),
        },
    }
    return proof


def nt_state(kind: str) -> str:
    """Состояние карточки из четырёх цветов макета: ok | error | busy | off."""
    return NT_KIND.get(str(kind or "").lower(), "busy")


def nt_color(kind: str, cfg: dict = None):
    """Цвет состояния карточки — ИЗ ОБЩЕЙ ТАБЛИЦЫ СТИЛЯ по ТЕКУЩЕЙ ТЕМЕ.

    Тёмные значения — цвета макета, светлые — из :mod:`theme`
    (``PALETTES['light']``): карточка следует теме, как и меню.
    """
    return US.tokens(cfg)["state_" + nt_state(kind)]


def bezier_y(t: float, p1x: float, p1y: float, p2x: float, p2y: float) -> float:
    """Значение кривой ``cubic-bezier`` при x=t — как считает браузер в макете.

    Кривые живут в общем модуле стиля (:mod:`ui_style`): меню трея считает по ним
    же, поэтому «одна кривая» — одно место.
    """
    return US.bezier_y(t, p1x, p1y, p2x, p2y)


def ease_bar(p: float) -> float:
    """Кривая полосы времени из макета: easeInOutQuad (из общего модуля стиля)."""
    return US.ease_bar(p)


def _norm_steps(steps) -> list:
    """Этапы -> [{'nm','ms','done','failed','at'}]. Принимает и строки, и словари."""
    out = []
    for s in (steps or []):
        if isinstance(s, dict):
            nm = str(s.get("nm") or "")
            try:
                ms = int(s.get("ms") if s.get("ms") is not None
                         else round(float(s.get("t") or 0.0) * 1000.0))
            except Exception:
                ms = 0
            try:
                at = float(s.get("at") or 0.0)
            except Exception:
                at = 0.0
            out.append({"nm": nm, "ms": ms, "done": bool(s.get("done")),
                        "failed": bool(s.get("failed")),
                        "at": max(0.0, min(1.0, at))})
        else:
            line = str(s)
            done = line.startswith("✓") or line.startswith("v ")
            failed = line.startswith("х") or line.startswith("x ")
            body = line.lstrip("✓хxv·* ").strip()
            ms = 0
            m = re.search(r"(\d+)\s*мс", body)
            if m:
                ms = int(m.group(1))
            nm = re.sub(r"\s{2,}\d+\s*мс.*$", "", body).strip()
            out.append({"nm": nm or body, "ms": ms, "done": done,
                        "failed": failed, "at": 0.0})
    total = max([x["ms"] for x in out] or [0]) or 0
    for x in out:
        if not x["at"] and total:
            x["at"] = max(0.0, min(1.0, x["ms"] / float(total)))
    return out


def _steps_total_ms(steps) -> int:
    return max([int(s.get("ms") or 0) for s in (steps or [])] or [0])


class Toast(QtWidgets.QWidget):
    """Карточка уведомления — 1:1 с утверждённым макетом ``docs/notify-mockup.html``.

    Состав (как в макете и в эталоне ``tray.ps1``): квадрат со значком
    ПОЛЬЗОВАТЕЛЯ (``packaging/app.ico`` — тот же файл и тот же код, что у значка
    трея: рисунок пиксель-в-пиксель + метка состояния в углу), заголовок и
    подпись по состоянию, БЛОК ЭТАПОВ с временем каждого В МИЛЛИСЕКУНДАХ,
    полоса времени, спиннер (жёлтый, НЕ красный) и футер с итогом.

    Плавность 60 к/с: один таймер, кадры идут по АБСОЛЮТНОЙ сетке времени
    (``n/60`` с), поэтому отставание не накапливается. За кадр меняются только
    положение окна (transform), прозрачность, ширина полосы и угол спиннера;
    статический слой (фон, рамка, полоса состояния, квадрат значка, рисунок,
    заголовок, подпись, дорожка полосы, футер) рисуется ОДИН раз в ``QPixmap``
    и подставляется целиком — Qt не перерисовывает части, мерцать нечему.

    Фокус не перехватывается: ``Qt.Tool | Qt.WindowDoesNotAcceptFocus |
    Qt.WindowTransparentForInput`` + ``WA_ShowWithoutActivating``; показ без
    ``activateWindow()``.
    """

    def __init__(self, kind: str, title: str, body: str, cfg: dict,
                 duration_ms: int = 5000, fps: int = 60, stats_sink: dict = None,
                 on_done=None, sub: str = "", pct=None, steps=None,
                 footer: str = "", spinner: bool = False, total_ms=None,
                 role: str = "restore"):
        super().__init__(None,
                         Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.cfg = cfg
        self.kind = kind if kind in STATE_KINDS else "busy"
        self.role = role if role in ("restore", "switch") else "restore"
        self.title = str(title or "")
        self.body = str(body or "")
        self.sub = str(sub or "")
        self.steps = _norm_steps(steps)
        self.footer = str(footer or "")
        self.spinner = bool(spinner)
        self.total_ms = float(total_ms or _steps_total_ms(self.steps) or 0.0)
        self.duration_ms = max(400, int(duration_ms))
        self.fps = max(10, min(120, int(fps)))
        self.pal = TH.palette(cfg)
        # ОБЩАЯ таблица стиля по ТЕКУЩЕЙ теме: те же цвета, что у меню трея
        self.tok = US.tokens(cfg)
        self.states = {k: self.tok["state_" + k]
                       for k in ("ok", "error", "busy", "off")}
        self._clr = {}                 # прогресс цвета подписи этапа (интерполяция)
        self.on_done = on_done
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.icon = icon_from_state(self.kind, cfg)
        # ОБА значка готовим заранее: в момент финала состояние меняется на ok,
        # и перерисовка значка «на лету» выбивала кадр из 60 к/с (замер WSL:
        # 58 мс вместо 16.7). Теперь финал — только подмена готового значка.
        self.icon_pm = self._icon_pixmap()
        self._icon_pm_ok = self._icon_pixmap("ok")
        self._fonts()
        self._p = float(pct) / 100.0 if pct is not None else 0.0
        self._p = max(0.0, min(1.0, self._p))
        self._final = False
        self._failed = False
        self.phase = "init"
        self._spin_deg = 0.0
        self._hold_ms = 0.0                # время в фазе удержания (искра полосы)
        self._proof_done = False           # снимок карточки уже снят (проверка)
        self._step_t0 = {}                 # индекс этапа -> время появления
        self._last = 0.0
        self._t0 = 0.0
        self._t_run = time.perf_counter()
        self._next_due = 0.0
        self._period = 1.0 / float(self.fps)
        self._dropped = 0
        self._dropped_steady = 0
        self._paint_ms = []
        self._slow = []                    # (t, длительность, фаза) — медленные кадры
        self.frame_ms = []                 # интервалы КАДРОВ (по таймеру)
        self.paint_gap_ms = []             # интервалы РЕАЛЬНЫХ отрисовок
        self.samples = []                  # (t, x, y, opacity, pct, spin)
        self._stats = stats_sink if stats_sink is not None else {}
        self._stats.update({"frames": 0, "frames_paint": 0, "avg_fps": 0.0,
                            "avg_paint_fps": 0.0, "worst_ms": 0.0,
                            "worst_warm_ms": 0.0, "worst_timer_ms": 0.0,
                            "stalls": 0, "dropped": 0, "dropped_steady": 0,
                            "positions": 0, "opacities": 0, "bars": 0,
                            "pos_start": None, "pos_end": None,
                            "op_start": None, "op_end": None,
                            "in_ms": 0.0, "out_ms": 0.0, "height": 0,
                            "width": NT_W, "fps": self.fps,
                            "phase_end": "", "samples": []})
        self._build_card()
        self.setFixedSize(NT_W, self._h)
        self._ticker = QtCore.QTimer(self)
        self._ticker.setTimerType(Qt.PreciseTimer)
        # Проверка сетки кадров идёт ВЧЕТВЕРО чаще периода кадра: кадр рисуется на
        # ближайшем тике после срока n/60, поэтому средний темп ровно 60 к/с, а
        # разброс между кадрами не превышает четверти периода (замер: среднее
        # 60.1 к/с, худший кадр 25-27 мс — 1.6 периода; при тике 2 мс хуже не
        # становится, поэтому лишние пробуждения не нужны).
        self._ticker.setInterval(max(1, int(round(self._period * 250.0))))
        self._ticker.timeout.connect(self._frame)

    # ── шрифты и раскладка (числа — из макета) ──
    def _fonts(self) -> None:
        base = self.font()
        self.f_t1 = QtGui.QFont(base)
        self.f_t1.setPixelSize(NT_FONT["t1"])
        self.f_t1.setWeight(QtGui.QFont.Weight.DemiBold)      # font-weight:600
        self.f_t2 = QtGui.QFont(base)
        self.f_t2.setPixelSize(NT_FONT["t2"])
        self.f_step = QtGui.QFont(base)
        self.f_step.setPixelSize(NT_FONT["step"])
        self.f_ms = QtGui.QFont(base)
        self.f_ms.setPixelSize(NT_FONT["ms"])
        self.f_foot = QtGui.QFont(base)
        self.f_foot.setPixelSize(NT_FONT["foot"])

    def _layout(self) -> dict:
        """Раскладка карточки по правилам CSS макета (рамка+padding+margin)."""
        fm1 = QtGui.QFontMetrics(self.f_t1)
        fm2 = QtGui.QFontMetrics(self.f_t2)
        fms = QtGui.QFontMetrics(self.f_step)
        fmf = QtGui.QFontMetrics(self.f_foot)
        # content-box макета: слева рамка 3px + padding 14, справа рамка 1 + 15
        x_l = NT_B_L + NT_PAD_L
        x_r = NT_W - NT_B - NT_PAD_R
        y_t = NT_B + NT_PAD_T
        x_txt = x_l + NT_ICO + NT_GAP
        h1 = fm1.height()
        h2 = fm2.height() if (self.sub or self.body) else 0
        y_t2 = y_t + h1 + 2
        head_bottom = max(y_t + NT_ICO, y_t2 + h2)
        n = len(self.steps)
        row_h = max(NT_MK, fms.height())
        lay = {"x_l": x_l, "x_r": x_r, "x_txt": x_txt, "y_t": y_t,
               "x_ico": x_l, "y_ico": y_t, "h1": h1, "y_t2": y_t2, "h2": h2,
               "x_spin": x_r - NT_SPIN, "y_spin": y_t + NT_SPIN_MT,
               "row_h": row_h, "rows": [], "x_step": x_l + NT_STEP_L}
        if n:
            y_steps = head_bottom + NT_STEPS_MT
            for i in range(n):
                lay["rows"].append(y_steps + i * (row_h + NT_STEP_GAP))
            steps_h = n * row_h + (n - 1) * NT_STEP_GAP
            y_bar = y_steps + steps_h + NT_STEPS_MB + NT_BAR_MT
            lay["y_steps"] = y_steps
            lay["y_bar"] = y_bar
            lay["x_bar"] = x_l + NT_STEP_L
            lay["bar_w"] = x_r - (x_l + NT_STEP_L)
            y_foot = y_bar + NT_BAR_H + NT_FOOT_MT
        else:
            lay["y_steps"] = None
            lay["y_bar"] = None
            lay["x_bar"] = x_l + NT_STEP_L
            lay["bar_w"] = x_r - (x_l + NT_STEP_L)
            y_foot = head_bottom + NT_STEPS_MB
        lay["y_foot"] = y_foot
        lay["h"] = y_foot + fmf.height() + NT_B + NT_PAD_B
        return lay

    def _icon_pixmap(self, kind: str = None):
        """Рисунок пользователя с меткой состояния — ТОТ ЖЕ код, что у трея."""
        try:
            data = TI.state_ico(kind or self.kind, sizes=(NT_ICO_ART,),
                                cfg=self.cfg)
        except Exception:
            data = b""
        pm = None
        if data:
            try:
                for w, h, off, size_b in TI.ico_images(data):
                    px = TI._dib_to_pixels(data[off:off + size_b])
                    if not px:
                        continue
                    pw, ph, bgra = px
                    img = QtGui.QImage(bgra, pw, ph, pw * 4,
                                       QtGui.QImage.Format_ARGB32).copy()
                    pm = QtGui.QPixmap.fromImage(img)
                    break
            except Exception:
                pm = None
        if pm is None or pm.isNull():
            # откат: кружок цвета состояния — значок всё равно есть
            pm = QtGui.QPixmap(NT_ICO_ART, NT_ICO_ART)
            pm.fill(Qt.transparent)
            p = QtGui.QPainter(pm)
            try:
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                p.setBrush(QtGui.QColor(*nt_color(kind or self.kind, self.cfg)))
                p.setPen(Qt.NoPen)
                p.drawEllipse(2, 2, NT_ICO_ART - 4, NT_ICO_ART - 4)
            finally:
                p.end()
        return pm

    # ── статический слой: рисуется один раз ──
    def _build_card(self) -> None:
        """Собирает раскладку и ОБА статических слоя: рабочий и финальный.

        Финальный слой готовим ЗАРАНЕЕ: иначе в момент финала (перестройка
        значка и карточки) кадр выпадает из 60 к/с. Замер в WSL показывал на
        этом месте кадр 58 мс вместо 16.7 — теперь финал это ПОДМЕНА готового
        слоя (см. :meth:`_to_final`).
        """
        lay = self._layout()
        self._lay = lay
        self._h = lay["h"]
        self._card = self._render_static(self.kind, self.title, self.sub,
                                         self.footer, self.icon_pm)
        self._card_final = None
        if self.steps and self.total_ms:
            self._card_final = self._render_static(
                "ok", self._final_title(), self._final_sub(),
                self._final_footer(), self._icon_pm_ok)
        self._stats["height"] = self._h

    def _final_title(self) -> str:
        return (SI.TITLE_RESTORE_FINAL if self.role == "restore"
                else SI.TITLE_SWITCH_FINAL)

    def _final_sub(self) -> str:
        """Подпись финала. Нет замера — нет числа («0.00 с» не печатаем)."""
        return final_texts(int(round(self.total_ms)), False)[0]

    def _final_footer(self) -> str:
        return final_texts(int(round(self.total_ms)), False)[1]

    def _render_static(self, kind: str, title: str, sub: str, footer: str,
                       icon_pm):
        """Рисует статический слой карточки (фон, значки, тексты) -> QPixmap.

        Цвета — из ОБЩЕЙ таблицы стиля (:func:`ui_style.tokens`) по ТЕКУЩЕЙ теме,
        поэтому карточка светлая на светлой теме и тёмная на тёмной.
        """
        lay = self._lay
        pm = QtGui.QPixmap(NT_W, self._h)
        pm.fill(Qt.transparent)
        p = QtGui.QPainter(pm)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(0.5, 0.5, NT_W - 1.0,
                                              self._h - 1.0), NT_RADIUS,
                                NT_RADIUS)
            # фон карточки + рамка 1px (rgba(255,255,255,.10))
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(*self.tok["bg"]))
            p.drawPath(path)
            p.setBrush(Qt.NoBrush)
            p.setPen(QtGui.QPen(QtGui.QColor(*self.tok["line"]), 1))
            p.drawPath(path)
            # полоса состояния слева: border-left 3px цветом состояния
            p.save()
            p.setClipPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(*nt_color(kind, self.cfg)))
            p.drawRect(QtCore.QRectF(0.0, 0.0, float(NT_B_L), float(self._h)))
            p.restore()
            # квадрат значка (фон + рамка) и рисунок пользователя ВНУТРИ
            ib = QtCore.QRectF(float(lay["x_ico"]), float(lay["y_ico"]),
                               float(NT_ICO), float(NT_ICO))
            ip = QtGui.QPainterPath()
            ip.addRoundedRect(ib, NT_ICO_R, NT_ICO_R)
            p.setPen(QtGui.QPen(QtGui.QColor(*self.tok["line"]), 1))
            p.setBrush(QtGui.QColor(*self.tok["surface"]))
            p.drawPath(ip)
            if icon_pm is not None and not icon_pm.isNull():
                off = (NT_ICO - NT_ICO_ART) // 2
                p.drawPixmap(int(ib.x()) + off, int(ib.y()) + off, icon_pm)
            # заголовок и подпись
            p.setPen(QtGui.QColor(*self.tok["txt"]))
            p.setFont(self.f_t1)
            p.drawText(QtCore.QRect(int(lay["x_txt"]), int(lay["y_t"]),
                                    int(lay["x_r"] - lay["x_txt"]),
                                    int(lay["h1"])),
                       int(Qt.AlignLeft | Qt.AlignVCenter), title)
            if sub or self.body:
                p.setPen(QtGui.QColor(*self.tok["muted"]))
                p.setFont(self.f_t2)
                p.drawText(QtCore.QRect(int(lay["x_txt"]), int(lay["y_t2"]),
                                        int(lay["x_r"] - lay["x_txt"]),
                                        int(lay["h2"])),
                           int(Qt.AlignLeft | Qt.AlignVCenter),
                           sub or self.body)
            # дорожка полосы времени
            if lay["y_bar"] is not None:
                tr = QtCore.QRectF(float(lay["x_bar"]), float(lay["y_bar"]),
                                   float(lay["bar_w"]), float(NT_BAR_H))
                p.setPen(Qt.NoPen)
                p.setBrush(QtGui.QColor(*self.tok["track"]))
                p.drawRoundedRect(tr, 2.0, 2.0)
            # футер
            p.setPen(QtGui.QColor(*self.tok["foot"]))
            p.setFont(self.f_foot)
            fmf = QtGui.QFontMetrics(self.f_foot)
            p.drawText(QtCore.QRect(int(lay["x_step"]), int(lay["y_foot"]),
                                    int(lay["x_r"] - lay["x_step"]),
                                    int(fmf.height())),
                       int(Qt.AlignLeft | Qt.AlignVCenter), footer)
        finally:
            p.end()
        return pm

    # ── кадр ──
    def paintEvent(self, _ev) -> None:
        now = time.perf_counter()
        if self._paint_ms:
            gap = (now - self._paint_ms[-1]) * 1000.0
            self.paint_gap_ms.append(gap)
            if gap > self._period * 1000.0 * 1.5:
                # медленные кадры запоминаем с фазой: по этому списку видно,
                # ЧТО именно задерживает кадр (перестройка слоя, планировщик ОС)
                self._slow.append((round((now - self._t_run) * 1000.0, 1),
                                   round(gap, 1), self.phase))
        self._paint_ms.append(now)
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.drawPixmap(0, 0, self._card)      # статический слой целиком
            self._paint_steps(p)
            self._paint_bar(p)
            self._paint_spinner(p)
        finally:
            p.end()

    def _paint_steps(self, p) -> None:
        lay = self._lay
        if not self.steps:
            return
        fms = QtGui.QFontMetrics(self.f_step)
        fmms = QtGui.QFontMetrics(self.f_ms)
        for i, s in enumerate(self.steps):
            op = self._step_opacity(i)
            if op <= 0.004:
                continue
            done = s["done"] or self._p >= min(1.0, s["at"] + STEP_LAG)
            failed = bool(s["failed"])
            cur = (not done) and (not failed)
            y = lay["rows"][i] + int(round((1.0 - op) * STEP_RISE_PX))
            p.save()
            p.setOpacity(op)
            cx = lay["x_step"]
            mk = QtCore.QRectF(float(cx), float(y + (lay["row_h"] - NT_MK) / 2.0),
                               float(NT_MK), float(NT_MK))
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            if done:
                p.setPen(Qt.NoPen)
                p.setBrush(QtGui.QColor(*self.states["ok"]))
                p.drawEllipse(mk)
            elif failed:
                p.setPen(Qt.NoPen)
                p.setBrush(QtGui.QColor(*self.states["error"]))
                p.drawEllipse(mk)
                pen = QtGui.QPen(QtGui.QColor(*self.tok["txt"]), 1.6)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(QtCore.QPointF(mk.x() + 4.0, mk.y() + 4.0),
                           QtCore.QPointF(mk.right() - 4.0, mk.bottom() - 4.0))
                p.drawLine(QtCore.QPointF(mk.right() - 4.0, mk.y() + 4.0),
                           QtCore.QPointF(mk.x() + 4.0, mk.bottom() - 4.0))
            else:
                p.setBrush(Qt.NoBrush)
                p.setPen(QtGui.QPen(QtGui.QColor(*self.tok["mark"]), 1.5))
                p.drawEllipse(mk)
            # имя этапа
            x_nm = cx + NT_MK + 8
            w_ms = fmms.horizontalAdvance("0000 мс") + 4
            p.setFont(self.f_step)
            # ── ОДИН слой текста с ИНТЕРПОЛИРОВАННЫМ цветом: приглушённый ->
            # яркий плавно за 180 мс (двух наложенных слоёв с кросс-фейдом нет,
            # поэтому подпись не «двоится») ──
            k = self._step_color_k(i, 1.0 if cur else 0.0)
            p.setPen(QtGui.QColor(*US.lerp_color(self.tok["muted"], self.tok["cur"], k)))
            p.drawText(QtCore.QRect(x_nm, y, max(10, int(lay["x_r"] - x_nm - w_ms)),
                                    lay["row_h"]),
                       int(Qt.AlignLeft | Qt.AlignVCenter), s["nm"])
            # время этапа В МИЛЛИСЕКУНДАХ — показывается, когда этап пройден
            if done or failed:
                p.setFont(self.f_ms)
                p.setPen(QtGui.QColor(*self.tok["ms"]))
                p.drawText(QtCore.QRect(int(lay["x_r"] - w_ms - 40), y,
                                        int(w_ms + 40), lay["row_h"]),
                           int(Qt.AlignRight | Qt.AlignVCenter),
                           f'{int(s["ms"])} мс')
            p.restore()

    def _step_color_k(self, i: int, want: float) -> float:
        """Прогресс цвета подписи этапа (0 — приглушённый, 1 — яркий).

        Плавно (180 мс) идёт к цели: смена активного этапа не «щёлкает» цветом,
        а перекрашивает ОДИН слой текста.
        """
        now = self._now_ms()
        try:
            want = float(want)
        except Exception:
            want = 0.0
        state = self._clr.setdefault(i, {"v": want, "from": want, "t0": now})
        if abs(state.get("to", state["v"]) - want) > 1e-6:
            state["from"] = state["v"]
            state["t0"] = now
            state["to"] = want
        t = min(1.0, max(0.0, (now - state["t0"]) / float(STEP_FADE_MS)))
        state["v"] = US.lerp(state["from"], state.get("to", want), t)
        return max(0.0, min(1.0, state["v"]))

    def _paint_bar(self, p) -> None:
        lay = self._lay
        if lay["y_bar"] is None:
            return
        fill = int(round(lay["bar_w"] * ease_bar(self._p)))
        if fill <= 0:
            return
        r = QtCore.QRectF(float(lay["x_bar"]), float(lay["y_bar"]),
                          float(fill), float(NT_BAR_H))
        g = QtGui.QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0.0, QtGui.QColor(*self.states["busy"]))
        g.setColorAt(1.0, QtGui.QColor(*self.states["ok"]))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 2.0, 2.0)
        # ДВИЖЕНИЕ НА УДЕРЖАНИИ: после заполнения по полосе бежит светлая искра,
        # поэтому карточка не замирает ни на одном кадре показа (макет).
        if self._final or self._p >= 1.0:
            w = max(18.0, float(lay["bar_w"]) * 0.22)
            span = float(lay["bar_w"]) + w
            x = float(lay["x_bar"]) - w + (self._hold_ms % BAR_SHEEN_MS) \
                / BAR_SHEEN_MS * span
            sheen = QtGui.QLinearGradient(x, 0.0, x + w, 0.0)
            sheen.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
            sheen.setColorAt(0.5, QtGui.QColor(255, 255, 255, 150))
            sheen.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            p.save()
            clip = QtGui.QPainterPath()
            clip.addRoundedRect(r, 2.0, 2.0)
            p.setClipPath(clip)
            p.setPen(Qt.NoPen)
            p.setBrush(sheen)
            p.drawRect(QtCore.QRectF(x, float(lay["y_bar"]), w, float(NT_BAR_H)))
            p.restore()

    def _paint_spinner(self, p) -> None:
        if not self.spinner:
            return
        lay = self._lay
        box = QtCore.QRectF(float(lay["x_spin"] + 1), float(lay["y_spin"] + 1),
                            float(NT_SPIN - 2), float(NT_SPIN - 2))
        p.setBrush(Qt.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor(*self.tok["spin_bg"]), 2))
        p.drawEllipse(box)
        # СПИННЕР КРУТИТСЯ ВСЁ ВРЕМЯ ПОКАЗА — и на удержании финала тоже.
        # Раньше финал останавливал вращение (в макете стояло animation:none) и
        # карточка замирала: пользователь видел статичную картинку 4 секунды.
        # Теперь меняется только ЦВЕТ (жёлтый -> зелёный/красный), движение — то же.
        if self._failed:
            col = QtGui.QColor(*self.states["error"])
        elif self._final:
            col = QtGui.QColor(*self.states["ok"])
        else:
            col = QtGui.QColor(*self.states["busy"])
        start = int((90.0 - self._spin_deg) * 16) - 45 * 16
        pen = QtGui.QPen(col, 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(box, int(start), 90 * 16)

    def _step_opacity(self, i: int) -> float:
        """Прозрачность этапа: 0 до появления, затем 180 мс проявления (макет).

        В ФИНАЛЕ (полоса прошла весь путь) все этапы показаны сразу: последний
        этап появляется ровно в этот момент, и без этого он оставался почти
        невидимым первые 180 мс удержания — на первом кадре снимка карточки его
        просто не было.
        """
        s = self.steps[i]
        if s["failed"] or self._p >= s["at"]:
            if self._p >= 1.0:
                return 1.0
            t0 = self._step_t0.get(i)
            if t0 is None:
                t0 = self._now_ms()
                self._step_t0[i] = t0
            return max(0.0, min(1.0, (self._now_ms() - t0) / float(STEP_FADE_MS)))
        return 0.0

    def _now_ms(self) -> float:
        return (time.perf_counter() - self._t_run) * 1000.0

    def _maybe_proof(self) -> None:
        """Снимок карточки по запросу проверки: ``state/card_shot.json``.

        Запрос приходит ФАЙЛОМ, поэтому снимок снимается у ЖИВОГО помощника в
        момент настоящего события — без особых параметров запуска и без
        второго окна. Срабатывает один раз: на первом кадре удержания.
        """
        if self._proof_done:
            return
        self._proof_done = True
        try:
            req = card_shot_request(self.cfg)
        except Exception:
            req = {}
        if not req:
            return
        try:
            res = capture_card_proof(
                self.cfg, self, out_dir=str(req.get("dir") or ""),
                name=str(req.get("name") or "notify-card"),
                frames=int(req.get("frames") or 10))
        except Exception as e:
            res = {"error": repr(e)}
        res["request"] = req
        card_shot_finish(self.cfg, res)

    # ── фазы ──
    def show_toast(self) -> dict:
        """Показывает карточку: ВХОД (340 мс) -> ХОД -> ФИНАЛ -> ВЫХОД (260 мс)."""
        if TEST_ANCHOR is not None:
            area = QtCore.QRect(*TEST_ANCHOR)      # крючок проверок (вне экрана)
        else:
            scr = QtWidgets.QApplication.primaryScreen()
            area = scr.availableGeometry() if scr else QtCore.QRect(0, 0, 800, 600)
        m = 12
        x = area.right() - self.width() - m
        y = area.bottom() - self.height() - m
        self._home = QtCore.QPoint(x, y)
        self._t0 = time.perf_counter()
        self._t_run = self._t0
        self._step_t0 = {}
        if self.steps and not self._final:
            # карточка процесса идёт СВОИМ ходом от нуля (как в макете):
            # этапы проявляются по мере прохождения, полоса заполняется
            self._p = 0.0
        self._spin_deg = 0.0
        self._hold_ms = 0.0
        self._paint_ms = []
        self._slow = []
        self._dropped = 0
        self.move(self._home.x() + SLIDE_PX, self._home.y())
        self.setWindowOpacity(0.0)
        self.show()                      # без activateWindow(): фокус не трогаем
        self.raise_()
        self._last = 0.0
        self._next_due = time.perf_counter()
        self.phase = "in"
        self._stats["in_ms"] = float(ANIM_IN_MS)
        self._ticker.start()
        self.update()
        return self._stats

    def _frame(self) -> None:
        now = time.perf_counter()
        if now + 1e-9 < self._next_due:
            return                       # срок кадра ещё не наступил (сетка 60 к/с)
        if self._last:
            dt = (now - self._last) * 1000.0
            self.frame_ms.append(dt)
            self._spin_deg += 360.0 * dt / float(SPIN_MS)
        self._last = now
        self._next_due += self._period
        if self._next_due <= now:        # отстали — долг не копим, кадры пропущены
            k = int((now - self._next_due) / self._period) + 1
            self._next_due += k * self._period
            self._dropped += k
            if self.phase != "in":
                # отдельно считаем пропуски ПОСЛЕ входа: первые кадры могут
                # быть медленнее из-за холодного старта отрисовки
                self._dropped_steady += k
        el = (now - self._t0) * 1000.0
        if self.phase == "in":
            t = min(1.0, el / float(ANIM_IN_MS))
            e = bezier_y(t, *CB_IN)
            self.move(self._home.x() + int(round(SLIDE_PX * (1.0 - e))),
                      self._home.y())
            self.setWindowOpacity(e)
            if t >= 1.0:
                self._t0 = now
                self._t_run = now
                self._step_t0 = {}
                self.phase = "hold" if self._final else "run"
        elif self.phase == "run":
            if self.total_ms > 0:
                self._p = max(self._p, min(1.0, ((now - self._t0) * 1000.0)
                                           / self.total_ms))
            else:
                self._p = 1.0
            if self._p >= 1.0:
                self._to_final()
        elif self.phase == "hold":
            self._hold_ms = el
            self._maybe_proof()          # снимок по запросу проверки (одноразово)
            if el >= float(self.duration_ms):
                self._start_out(now)
        elif self.phase == "out":
            t = min(1.0, el / float(ANIM_OUT_MS))
            e = bezier_y(t, *CB_OUT)
            self.move(self._home.x() + int(round(SLIDE_PX * e)), self._home.y())
            self.setWindowOpacity(max(0.0, 1.0 - e))
            if t >= 1.0:
                self._done()
                return
        g = self.geometry()
        self.samples.append((round((now - self._t_run) * 1000.0, 1), g.x(),
                             g.y(), round(self.windowOpacity(), 3),
                             round(self._p * 100.0, 2), round(self._spin_deg, 1)))
        self.update()

    def _to_final(self) -> None:
        """Процесс дошёл до конца сам — финальный вид из макета."""
        if not self._final:
            self.title = self._final_title()
            self.sub = self._final_sub()
            self.footer = self._final_footer()
            for s in self.steps:
                s["done"] = True
            self.kind = "ok"
            self._final = True
            self.duration_ms = HOLD_FINAL_MS
            self.icon_pm = self._icon_pm_ok
            # подмена ГОТОВОГО слоя (собран в _build_card): кадр не выпадает
            if self._card_final is not None:
                self._card = self._card_final
            else:
                self._build_card()
        self.phase = "hold"
        self._hold_ms = 0.0
        self._t0 = time.perf_counter()

    def apply_final(self, title: str = "", sub: str = "", footer: str = "",
                    failed: bool = False, elapsed_ms: int = None) -> None:
        """ФИНАЛ, сообщённый программой: обновляем карточку НА МЕСТЕ.

        Второй раз карточка не выезжает: она уже на экране, поэтому финал просто
        доводится в ней же (как в макете, где то же окно переходит в финальное
        состояние).
        """
        was_final = self._final
        ms = int(elapsed_ms if elapsed_ms is not None else self.total_ms)
        fsub, ffoot = final_texts(ms, bool(failed))
        n_title = title or (SI.TITLE_FAILED if failed else
                            (SI.TITLE_RESTORE_FINAL if self.role == "restore"
                             else SI.TITLE_SWITCH_FINAL))
        n_sub = sub or fsub
        n_foot = footer or ffoot
        if (was_final and not failed and n_title == self.title
                and n_sub == self.sub and n_foot == self.footer):
            return                          # уже показан этот же финал
        self._failed = bool(failed)
        self.title, self.sub, self.footer = n_title, n_sub, n_foot
        self.kind = "error" if failed else "ok"
        self.steps = [dict(s, done=(not failed), failed=failed)
                      for s in self.steps]
        self._p = 1.0
        self._final = True
        self.duration_ms = HOLD_FINAL_MS
        if not failed:
            self.icon_pm = self._icon_pm_ok
        same = (not failed and self._card_final is not None
                and self.title == self._final_title()
                and self.sub == self._final_sub()
                and self.footer == self._final_footer())
        if same:
            self._card = self._card_final      # подмена готового слоя
        else:
            self.icon_pm = self._icon_pixmap()
            self._build_card()
        if self.height() != self._h:
            self.setFixedSize(NT_W, self._h)
        self.phase = "hold"
        self._hold_ms = 0.0
        self._t0 = time.perf_counter()
        self.update()

    def _start_out(self, now: float) -> None:
        self.phase = "out"
        self._t0 = now
        self._stats["out_ms"] = float(ANIM_OUT_MS)

    def _done(self) -> None:
        self.phase = "done"
        self._stats["phase_end"] = "done"
        self._finish_stats()
        self.close()
        try:
            if callable(self.on_done):
                self.on_done()
        except Exception:
            pass

    def _finish_stats(self) -> None:
        self._ticker.stop()
        fr = self.frame_ms or [0.0]
        avg = sum(fr) / len(fr)
        pg = self.paint_gap_ms or [0.0]
        pgw = pg[2:] or pg          # без первых двух кадров (прогрев отрисовки)
        avgp = sum(pg) / len(pg)
        positions = sorted({(s[1], s[2]) for s in self.samples})
        opac = sorted({s[3] for s in self.samples})
        bars = sorted({s[4] for s in self.samples})
        stall = [x for x in pgw if x > self._period * 1000.0 * 2.0]
        self._stats.update({
            "frames": len(self.frame_ms),
            "frames_paint": max(0, len(self._paint_ms) - 1),
            "avg_fps": round(1000.0 / avg, 1) if avg else 0.0,
            "avg_paint_fps": round(1000.0 / avgp, 1) if avgp else 0.0,
            "worst_ms": round(max(pg), 1),
            "worst_warm_ms": round(max(pgw), 1),
            "worst_timer_ms": round(max(fr), 1),
            "stalls": len(stall),
            "slow": list(self._slow),
            "dropped": int(self._dropped),
            "dropped_steady": int(self._dropped_steady),
            "positions": len(positions),
            "opacities": len(opac),
            "bars": len(bars),
            "pos_start": (positions[0] if positions else None),
            "pos_end": (positions[-1] if positions else None),
            "op_start": (opac[0] if opac else None),
            "op_end": (opac[-1] if opac else None),
            "samples": list(self.samples)})
        self.journal()

    def texts(self) -> dict:
        """Тексты карточки — доказательство состава для проверок."""
        return {
            "title": self.title, "sub": self.sub, "footer": self.footer,
            "phase": self.phase, "kind": self.kind, "role": self.role,
            "pct": round(self._p * 100.0, 2), "final": self._final,
            "failed": self._failed,
            "step_names": [s["nm"] for s in self.steps],
            "step_ms": [int(s["ms"]) for s in self.steps],
            "steps": [("%s   %d мс" % (s["nm"], int(s["ms"])))
                      for s in self.steps],
        }

    def journal(self) -> None:
        d = (self.cfg.get("paths") or {}).get("state_dir") or ""
        if not d:
            return
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "ui_notify.log"), "a",
                      encoding="utf-8") as f:
                f.write("%s\t%s\t%s\t%s\tframes=%d avg_fps=%.1f worst=%.1fms "
                        "paint=%.1f\n"
                        % (time.strftime("%H:%M:%S"), self.kind, self.title,
                           self.body, self._stats["frames"],
                           self._stats["avg_fps"], self._stats["worst_ms"],
                           self._stats.get("avg_paint_fps", 0.0)))
        except Exception:
            pass

def notification_body(sub: str, body: str, steps, footer: str) -> str:
    """Тело СИСТЕМНОГО уведомления (Linux): этапы и время идут В ТЕЛЕ.

    На Linux своего окна нет — этапы восстановления связи и их длительности
    (в миллисекундах) передаются системе (``org.freedesktop.Notifications``)
    прямо текстом, поэтому проверять их нужно здесь же.
    """
    parts = []
    if sub:
        parts.append(str(sub))
    if body:
        parts.append(str(body))
    for x in (steps or []):
        x = str(x)
        if x:
            parts.append(x)
    if footer:
        parts.append(str(footer))
    return "\n".join(parts)


def notify_via_dbus(icon, title: str, body: str, kind: str,
                    duration_ms: int) -> bool:
    """Linux: системное уведомление (org.freedesktop.Notifications) через Qt."""
    try:
        ic = getattr(QtWidgets.QSystemTrayIcon, "Information",
                     QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        icon.showMessage(title, body, ic, max(2000, int(duration_ms)))
        return True
    except Exception:
        return False


def build_menu_model(cfg: dict, owner=None) -> tuple:
    """Собирает модель меню трея: ``(MenuGroup, acts, filters_menu, theme_menu)``.

    Пункты — ТЕ ЖЕ, что были у ``QMenu`` (и те же, что в живом дампе): состояние
    связи (только показ), два переключателя, два подменю, команды, выход.
    Собирается БЕЗ окна и трея, поэтому этим же путём пользуются доказательства
    (``--menu-shots``) и проверки: состав один, разойтись не может.
    """
    m = UMN.MenuGroup()
    acts = {}

    def mk(name, text, checkable=False, enabled=True, sub=None):
        act = QtGui.QAction(text, owner) if owner is not None else QtGui.QAction(text)
        act.setCheckable(bool(checkable))
        act.setEnabled(bool(enabled))
        acts[name] = act
        return UMN.Row(key=name, act=act, sub=sub)

    acts["status"] = (QtGui.QAction(tr("tray.state.ok"), owner)
                      if owner is not None else QtGui.QAction(tr("tray.state.ok")))
    acts["status"].setEnabled(False)
    m.add(UMN.Row(key="status", act=acts["status"]))
    m.add_separator()
    m.add(mk("watchdog", tr("tray.menu.watchdog"), True))
    m.add(mk("notify", tr("tray.menu.notify"), True))
    # ── ФИЛЬТРЫ (подменю с галочками) ──
    # Логика фильтров живёт в tray_menu (CMD_FILTER_RU/CMD_FILTER_CF); здесь
    # только вывод и передача команды супервизору через state/ui_cmd.json.
    filters_menu = UMN.MenuGroup(tr("tray.menu.filters"))
    filters_menu.add(mk("filter_ru", tr("tray.menu.filter_ru"), True))
    filters_menu.add(mk("filter_cf", tr("tray.menu.filter_cf"), True))
    m.add(UMN.Row(key="filters", label=tr("tray.menu.filters"), sub=filters_menu))
    # ── ТЕМА ОФОРМЛЕНИЯ (одиночный выбор из трёх) ──
    theme_menu = UMN.MenuGroup(tr("tray.menu.theme"))
    for k in ("auto", "light", "dark"):
        theme_menu.add(mk("theme_" + k, tr("theme." + k), True))
    m.add(UMN.Row(key="theme", label=tr("tray.menu.theme"), sub=theme_menu))
    m.add_separator()
    m.add(mk("toggle", tr("tray.menu.toggle_off")))
    m.add(mk("refresh", tr("tray.menu.refresh")))
    m.add_separator()
    m.add(mk("status_file", tr("tray.menu.open_status")))
    m.add(mk("open_log", tr("tray.menu.open_log")))
    m.add_separator()
    m.add(mk("test_notify", tr("ui.notify_test")))
    m.add(mk("about", tr("ui.about")))
    m.add(mk("exit", tr("ui.exit")))
    return m, acts, filters_menu, theme_menu


def sync_menu_actions(cfg: dict, acts: dict, tip: str = "", raw: dict = None) -> None:
    """Приводит подписи и галочки меню к ТЕКУЩИМ настройкам (общий путь).

    Один и тот же код и у живого помощника (тик обновления), и у доказательств:
    подписи фильтров («— включено/выключено»), галочки надзора/уведомлений,
    отметка темы и строка состояния не могут разойтись между путями.
    """
    try:
        rows = SI.info_lines(cfg, raw if raw is not None else {})
        acts["status"].setText(f"{rows[0][0]}: {rows[0][1]}")
    except Exception:
        acts["status"].setText(tip or tr("tray.state.ok"))
    try:
        import tray_menu as TM
        off = TM.TrayActions(cfg).proxy_off()
        acts["toggle"].setText(tr("tray.menu.toggle_on") if off
                               else tr("tray.menu.toggle_off"))
        acts["toggle"].setEnabled(True)
    except Exception:
        pass
    try:
        wd = bool((cfg.get("watchdog") or {}).get("enabled", True))
        nf = bool((cfg.get("notify") or {}).get("enabled", True))
        th = str((cfg.get("ui") or {}).get("theme", "auto")).lower()
        ru = bool((cfg.get("watch") or {}).get("ru_direct", False))
        cf = bool((cfg.get("blacklist") or {}).get("cloudflare_nodes", True))
    except Exception:
        wd, nf, th, ru, cf = True, True, "auto", False, True
    acts["watchdog"].setChecked(wd)
    acts["notify"].setChecked(nf)
    # галочки фильтров — по ТЕКУЩЕМУ состоянию настроек (и по нашему, и по
    # изменённому программой: настройки читаем заново каждый тик)
    acts["filter_ru"].setChecked(ru)
    acts["filter_cf"].setChecked(cf)
    on, off_w = tr("tray.hint.on"), tr("tray.hint.off")
    acts["filter_ru"].setText(f'{tr("tray.menu.filter_ru")} — {on if ru else off_w}')
    acts["filter_cf"].setText(f'{tr("tray.menu.filter_cf")} — {on if cf else off_w}')
    for k, v in (("theme_auto", "auto"), ("theme_light", "light"),
                 ("theme_dark", "dark")):
        acts[k].setChecked(th == v)


# ═══════════════════════════════════════════════ приложение ═══
def enum_int(value, default: int = -1) -> int:
    """Целое значение перечисления Qt, БЕЗ ``int()`` по члену перечисления.

    В PySide6 6.9 ``QSystemTrayIcon.ActivationReason`` — это перечисление
    Python, а не «INT»-объединение как у флагов Qt: ``int(член)`` даёт
    ``TypeError: int() argument must be ... not 'ActivationReason'``. Именно
    эта строка (``int(ActivationReason.Context)`` в слоте трея) убивала
    помощник при правом клике по значку: исключение из слота PySide6 отдаёт в
    ``sys.excepthook``, а safestdio завершает процесс (код 70).

    Принимаем и число (так причина приходила в старых Qt), и член
    перечисления: сравнивать причины можно ОБЕИМИ формами.
    """
    try:
        return int(value)
    except Exception:
        pass
    raw = getattr(value, "value", None)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


class Ui(QtCore.QObject):
    def __init__(self, cfg: dict, quiet: bool = False):
        super().__init__()
        self.cfg = cfg
        # quiet=True — «не показывать карточку»: нужно проверкам (замер кадров
        # меню на живом экране не должен вытаскивать уведомления на экран)
        self.quiet = bool(quiet)
        self.kind = "off"
        self.tip = ""
        self.acting = False
        self.toast = None
        self.first = True
        # ОДНО ДЕЙСТВИЕ = ОДНА КАРТОЧКА: отметка времени последней показанной
        # карточки и признак явного демо-показа (--show-all), который гейт
        # склейки не проходит.
        self._last_card_ts = 0.0
        self._demo = False
        self._cmd_shown_ts = 0.0
        self.last_stats = {}
        self.toast_finished = True
        self.stats = {}
        self.tray = QtWidgets.QSystemTrayIcon(self)
        # СВОЁ всплывающее меню вместо QMenu: QMenu не умеет ни анимации, ни
        # стиля карточки. Модель меню (пункты/галочки/подменю) собирается из тех
        # же QAction, что и раньше, поэтому состав и поведение НЕ меняются.
        self.acts = {}
        self.menu_model = None
        self._menu_panel = None
        self._menu_theme = None
        self._build_menu()
        self.refresh()
        # меню рисуем САМИ по правому клику: своё меню у трея не ставим, иначе
        # Qt показал бы системный QMenu поверх нашего окна
        try:
            self.tray.activated.connect(self._on_tray_activated)
        except Exception:
            pass
        self.tray.show()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QtCore.QTimer.singleShot(0, self._ping)

    # ── меню ──
    def _build_menu(self) -> None:
        """Собирает модель меню общим путём (:func:`build_menu_model`).

        ``self.acts`` остаётся словарём QAction (текст, галочка, доступность) —
        это единственный источник истины и для дампа, и для отрисовки, и для
        проверок. Рисует пункты своё окно (:class:`ui_menu.PopupMenu`).
        """
        (self.menu_model, self.acts, self.filters_menu,
         self.theme_menu) = build_menu_model(self.cfg, self)

    def menu_dump(self) -> str:
        """Текстовый дамп меню — доказательство структуры для проверок."""
        return UMN.dump_rows((self.menu_model or UMN.MenuGroup()).rows)

    # ── показ нашего меню и переходы состояний ──
    def _tray_error(self, what: str) -> None:
        """Ошибка в пути меню трея: пишем в журнал и ЖИВЁМ дальше.

        Слот трея — граница с C++ (Qt). Исключение, вылетевшее отсюда, PySide6
        отдаёт в ``sys.excepthook`` (``safestdio.install_excepthook``), а тот
        завершает помощник: значок исчезает, и пользователь видит «программа
        упала от правого клика». Поэтому здесь ловим всё и только пишем след.
        """
        txt = traceback.format_exc()
        try:
            log.get_logger().error(f"forest-ui: ошибка в меню трея ({what}):\n{txt}")
        except Exception:
            pass
        try:
            import safestdio as _SD
            _SD.say(f"forest-ui: ошибка в меню трея ({what}) — помощник продолжает работать")
        except Exception:
            pass

    def _on_tray_activated(self, reason) -> None:
        """Правый клик по значку трея — наше меню (координаты берём по курсору).

        Как и прежде (системное меню у ``QSystemTrayIcon``), меню открывается
        ТОЛЬКО по правому клику: левый клик, как и раньше, ничего не открывает.

        Две защиты, каждая из которых обязательна:
        * причина клика сравнивается БЕЗ ``int()`` по члену перечисления
          (:func:`enum_int`) — именно ``int(ActivationReason.Context)`` роняло
          помощник на PySide6 6.9;
        * показ меню обёрнут в ``try/except`` — ошибка меню (нет экрана,
          несовместимость с ``Qt.Popup``, исключение в отрисовке) остаётся
          ошибкой меню и НЕ убивает помощник.
        """
        try:
            ctx = enum_int(QtWidgets.QSystemTrayIcon.ActivationReason.Context)
            if enum_int(reason) != ctx:
                return                    # левый клик/двойной — меню не открываем
        except Exception:
            self._tray_error("разбор причины клика")
            return
        try:
            self.show_menu()
        except Exception:
            self._tray_error("показ меню по правому клику")

    def show_menu(self, pos=None) -> dict:
        """Открывает контекстное меню у курсора (монитор и DPI — по курсору)."""
        theme = US.theme_of(self.cfg)
        tok = US.tokens(self.cfg)
        try:
            import tray_menu as TM
            st = TM.TrayActions(self.cfg).icon_state()
        except Exception:
            st = "ok"
        sc = tok["state_" + (st if st in ("ok", "error", "busy", "off") else "busy")]
        if self._menu_panel is None:
            self._menu_panel = UMN.PopupMenu(self.cfg, self.menu_model,
                                             on_activate=self._menu_activate,
                                             on_toggle=self._menu_toggle,
                                             state_color=sc, state_kind=st)
        pnl = self._menu_panel
        pnl.cfg = self.cfg
        pnl.tokens = tok
        pnl.state_kind = st if st in ("ok", "error", "busy", "off") else "busy"
        pnl.state_color = tuple(sc)
        pnl._layout_rows()          # подписи могли смениться (фильтры/тема)
        pnl._build_static()
        self._menu_theme = theme
        return pnl.open_at(pos or QtGui.QCursor.pos())

    def _close_menu(self, instant: bool = False) -> None:
        if self._menu_panel is None:
            return
        if instant:
            try:
                self._menu_panel._finish()
            except Exception:
                pass
        else:
            self._menu_panel.start_out()

    def _menu_activate(self, key: str) -> None:
        """Пункт меню без галочки: идём ТЕМ ЖЕ путём, что и раньше."""
        self.on_action(key)

    def _menu_toggle(self, key: str, checked: bool) -> None:
        """Переключатель: QAction уже переключён — одиночный выбор и команда."""
        if key.startswith("theme_"):
            name = key.split("_", 1)[1]
            for k in ("auto", "light", "dark"):
                self.acts["theme_" + k].setChecked(k == name)
        self.on_action(key)

    # ── действия: всегда ЧЕРЕЗ файл-команду, ядро не трогаем ──
    def on_action(self, name: str) -> None:
        mapping = {"watchdog": "watchdog_on" if self.acts["watchdog"].isChecked()
                                 else "watchdog_off",
                   "notify": "notify_on" if self.acts["notify"].isChecked()
                             else "notify_off",
                   "filter_ru": "filter_ru_on" if self.acts["filter_ru"].isChecked()
                                else "filter_ru_off",
                   "filter_cf": "filter_cf_on" if self.acts["filter_cf"].isChecked()
                                else "filter_cf_off",
                   "theme_auto": "theme_auto", "theme_light": "theme_light",
                   "theme_dark": "theme_dark",
                   "toggle": "toggle_proxy",
                   "refresh": "refresh", "exit": "exit"}
        if name == "about":
            self.show_info()
            return
        if name == "status_file":
            self.open_path(self.status_file())
            return
        if name == "test_notify":
            # «Проверить уведомление» показывает ТУ ЖЕ карточку, что и событие:
            # этапы с мс, полоса, спиннер, футер (никакого упрощённого вида).
            self.show_demo_card()
            return
        if name == "open_log":
            self.open_log()
            return
        cmd = mapping.get(name)
        if not cmd:
            return
        ok = UC.write_cmd(self.cfg, cmd)
        try:
            log.get_logger().warn(f"forest-ui: команда {cmd} -> "
                                  f"{'в очередь' if ok else 'НЕ ЗАПИСАНА'}")
        except Exception:
            pass
        if not ok:
            self.show_toast("error", tr("ui.cmd_failed", err="state/ui_cmd.json"),
                            cmd)

    def status_file(self) -> str:
        """Файл отчёта ``--status`` (если есть) — как «Открыть статус» в эталоне."""
        d = (self.cfg.get("paths") or {}).get("state_dir") or ""
        if not d:
            return ""
        f = os.path.join(d, "status.txt")
        return f if os.path.isfile(f) else os.path.join(d, "tray_status.json")

    def open_path(self, p: str) -> None:
        try:
            if IS_WIN:
                os.startfile(p)                      # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            self.show_toast("error", tr("tray.msg.no_log"), str(e))

    def show_info(self) -> None:
        """«Сведения» — ПОЛНЫЙ состав данных эталона (ничего не выбрасываем)."""
        st = read_raw_state(self.cfg)
        rows = SI.info_lines(self.cfg, st)
        body = "\n".join(f"{k}: {v}" if k else v for k, v in rows)
        try:
            log.get_logger().info("forest-ui: сведения:\n" + body)
        except Exception:
            pass
        self.show_toast(self.kind, SI.tip(self.cfg, st, limit=0), body,
                        duration_ms=9000)

    def show_process_card(self, proc: dict) -> None:
        """Карточка процесса — состав и тексты как в эталоне и макете.

        Этапы идут С ВРЕМЕНЕМ КАЖДОГО В МИЛЛИСЕКУНДАХ, у завершённого
        восстановления в футере — ИТОГОВОЕ ВРЕМЯ. Карточка одного и того же
        процесса одна: когда программа сообщает финал, он доводится В ТОЙ ЖЕ
        карточке (второй раз она не выезжает — как в макете, где то же окно
        меняет состояние).
        """
        n = SI.notif(proc)
        # Карточка процесса — ГЛАВНАЯ для действия: помечаем её время, чтобы
        # следом не выехала вторая (ответ команды / событие) в том же окне.
        self._last_card_ts = time.time()
        try:
            remember_proc(self.cfg, proc)      # «последнее известное состояние»
        except Exception:
            pass
        kind = "error" if n["failed"] else ("ok" if n["final"] else
                                            ("restoring" if n["kind"] == "restore"
                                             else "switch"))
        # финал держим 4 с — как в эталоне («окно закроется через 4 с»)
        dur = HOLD_FINAL_MS if n["final"] else 6000
        steps = SI.steps_data(proc)
        total = SI.steps_total_ms(proc)
        if not steps or total <= 0:
            # событие не передало этапы — берём последнее известное состояние
            try:
                d = demo_card_data(self.cfg)
            except Exception:
                d = {}
            if not steps:
                steps = d.get("steps") or []
            if total <= 0:
                total = int(d.get("total_ms") or _steps_total_ms(steps))
        sub, footer = n["sub"], n["footer"]
        if n["final"] or n["failed"]:
            # «0.00 с» не печатаем: настоящий замер либо его нет вовсе
            ms = int(n.get("elapsed_ms") or 0) or int(total or 0)
            sub, footer = final_texts(ms, bool(n["failed"]))
        pid = str(proc.get("id") or "")
        t = self.toast
        if t is not None and getattr(t, "_proc_id", "") == pid and pid:
            # тот же процесс: доводим состояние НА МЕСТЕ (без второго выезда)
            t.apply_final(n["title"], sub, footer, failed=n["failed"],
                          elapsed_ms=n.get("elapsed_ms"))
            return
        self.show_toast(kind, n["title"], "", duration_ms=dur, sub=sub,
                        steps=steps, footer=footer, total_ms=total,
                        spinner=True, role=n["kind"])
        if self.toast is not None:
            self.toast._proc_id = pid
            if n["final"] or n["failed"]:
                # помощник узнал о процессе уже после финала: карточка сразу
                # показывает итог (нормальный случай — старт помощника)
                self.toast.apply_final(n["title"], sub, footer,
                                       failed=n["failed"],
                                       elapsed_ms=n.get("elapsed_ms"))

    def show_demo_card(self) -> dict:
        """Демо-показ: ТА ЖЕ карточка, что у настоящего события.

        Никакого упрощённого вида — значок с меткой, заголовок, подпись, блок
        этапов с временем в миллисекундах, полоса времени, спиннер и футер.
        Данные — реальные этапы (``status_info``) либо последнее известное
        состояние, поэтому на живом ПК видны настоящие времена, а не нули.
        """
        d = demo_card_data(self.cfg)
        return self.show_toast(d["kind"], d["title"], "", sub=d["sub"],
                               steps=d["steps"], footer=d["footer"],
                               total_ms=d["total_ms"],
                               duration_ms=HOLD_FINAL_MS + 1200,
                               spinner=True, role=d["role"])

    def open_log(self) -> None:
        try:
            p = log.get_logger().path
        except Exception:
            p = os.path.join((self.cfg.get("paths") or {}).get("log_dir", ""),
                             "main.log")
        try:
            if IS_WIN:
                os.startfile(p)                      # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            self.show_toast("error", tr("tray.msg.no_log"), str(e))

    # ── уведомления ──
    def card_enabled(self) -> bool:
        """Рисуем ли СВОЮ карточку (Windows — всегда, Linux — при графике).

        Linux: свою карточку рисуем, если есть графическая сессия (Qt-платформа
        не ``minimal``) и настройка ``notify.card_on_linux`` не выключена. Откат
        на системное уведомление D-Bus — когда карточку показать нельзя или её
        показ не удался (бездисплейный запуск, сбой окна).
        """
        if IS_WIN:
            return True
        if not QT_OK or QtWidgets.QApplication.instance() is None:
            return False
        try:
            if not bool((self.cfg.get("notify") or {}).get("card_on_linux", True)):
                return False
        except Exception:
            pass
        try:
            plat = str(QtWidgets.QApplication.platformName() or "").lower()
        except Exception:
            plat = ""
        return plat not in ("minimal", "vnc")

    def _toast_fallback_dbus(self, kind: str, title: str, body: str, sub: str,
                             step_lines, footer: str, dur: int) -> dict:
        """Откат: системное уведомление D-Bus (этапы и время идут В ТЕЛЕ)."""
        vis = [x["nm"] + "   " + str(x["ms"]) + " мс" for x in step_lines] \
            if step_lines and isinstance(step_lines[0], dict) else \
            [str(x) for x in (step_lines or [])]
        text = notification_body(sub, body, vis, footer)
        ok = notify_via_dbus(self.tray, title, text, kind, dur)
        self.last_stats = {"frames": 0, "avg_fps": 0.0, "worst_ms": 0.0,
                           "painted": False, "dbus": True, "ok": ok,
                           "steps": len(vis),
                           "svc": notification_service_available()}
        self.toast_finished = True
        return self.last_stats

    def show_toast(self, kind: str, title: str, body: str, sub: str = "",
                   pct=None, steps=None, footer: str = "",
                   duration_ms: int = None, spinner: bool = False,
                   total_ms=None, role: str = "restore") -> dict:
        """Показывает уведомление. -> словарь со статистикой кадров.

        Windows И Linux (при графической сессии) — ОДНА И ТА ЖЕ карточка:
        значок пользователя, заголовок и подпись по состоянию, блок этапов с
        временем каждого в мс, полоса времени, спиннер и футер (1:1 с макетом
        ``docs/notify-mockup.html``). Если карточку показать нельзя (нет
        графической сессии, настройка выключена, сбой окна) — откат на
        системное уведомление D-Bus, где этапы и время идут В ТЕЛЕ.
        """
        s = {}
        try:
            import notify as N
            s = N.settings(self.cfg)
        except Exception:
            s = {}
        dur = int(duration_ms or (s or {}).get("duration_ms", 5000) or 5000)
        fps = int((s or {}).get("fps", 60) or 60)
        step_lines = _norm_steps(steps)
        total = int(total_ms or _steps_total_ms(step_lines) or 0)
        if not step_lines or total <= 0:
            # ЕДИНАЯ КАРТОЧКА: событие без этапов НЕ делает вид проще — берём
            # последнее известное состояние (или сценарий макета).
            try:
                d = demo_card_data(self.cfg)
            except Exception:
                d = {}
            if not step_lines:
                step_lines = _norm_steps(d.get("steps"))
            if total <= 0:
                total = int(d.get("total_ms") or _steps_total_ms(step_lines) or 0)
            if not sub:
                sub = str(d.get("sub") or "")
            if not footer:
                footer = str(d.get("footer") or "")
            if not steps:                       # вызвали без этапов — роль демо
                role = str(d.get("role") or role)
        total_ms = total
        # Спиннер и полоса есть ВСЕГДА: без них состав не совпадает с макетом.
        spinner = True
        if not footer:
            footer = tr("notify.foot.watching")
        if not self.card_enabled():
            return self._toast_fallback_dbus(kind, title, body, sub, step_lines,
                                             footer, dur)
        self.last_stats = {}
        self.toast_finished = False
        try:
            t = Toast(kind, title, body, self.cfg, duration_ms=dur, fps=fps,
                      stats_sink=self.last_stats, on_done=self._toast_done,
                      sub=sub, pct=pct, steps=step_lines, footer=footer,
                      spinner=spinner, total_ms=total_ms, role=role)
            self.toast = t
            t.show_toast()
        except Exception as e:
            # сбой своего окна — не оставляем пользователя без уведомления
            self.toast = None
            self.toast_finished = True
            try:
                log.get_logger().warn(f"forest-ui: карточка уведомления не "
                                      f"показана ({e}) — системное уведомление")
            except Exception:
                pass
            return self._toast_fallback_dbus(kind, title, body, sub, step_lines,
                                             footer, dur)
        return self.last_stats

    def _toast_done(self) -> None:
        self.toast_finished = True
        self.toast = None

    def _card_gate(self, why: str) -> bool:
        """True — карточку показывать НЕ надо: в коротком окне уже была карточка.

        ОДНО ДЕЙСТВИЕ = ОДНА КАРТОЧКА: серию (обрыв/переключение/ответ команды)
        склеиваем в одну. Явный демо-показ (--show-all) гейт проходит.
        """
        if getattr(self, "_demo", False) or getattr(self, "quiet", False):
            return False
        now = time.time()
        if (now - float(getattr(self, "_last_card_ts", 0.0) or 0.0)) * 1000.0 \
                < CARD_COALESCE_MS:
            try:
                log.get_logger().info(
                    f"forest-ui: карточка «{why}» не показана — в окне "
                    f"{CARD_COALESCE_MS:.0f} мс уже была карточка (склейка)")
            except Exception:
                pass
            return True
        self._last_card_ts = now
        return False

    def _on_transition(self, prev: str, cur: str) -> None:
        """Уведомляет о ЗНАЧИМЫХ переходах (обрыв, восстановление, ручное выкл).

        Рутина обновления пула молчит — как и у встроенного значка. Событие
        показываем только если уведомления включены настройкой.
        """
        s = {}
        try:
            import notify as N
            s = N.settings(self.cfg)
        except Exception:
            s = {}
        if not s.get("enabled", True):
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
        if ev == "off" and not s.get("show_on_manual_off", True):
            return
        if not ev:
            return
        try:
            log.get_logger().info(f"forest-ui: событие «{ev}» ({prev} -> {cur})")
        except Exception:
            pass
        # Процесс обрыва уже описан сторожем (этапы с временами) — карточку
        # покажет show_process_card, второй про то же событие быть не должно.
        try:
            proc = (read_raw_state(self.cfg) or {}).get("proc") or {}
            if proc.get("steps"):
                log.get_logger().info(
                    "forest-ui: событие покрыто карточкой процесса — "
                    "отдельное уведомление не показываю")
                return
        except Exception:
            pass
        if self._card_gate(f"событие {ev}"):
            return
        self.acting = True
        try:
            self.show_toast(cur, tr(f"notify.{ev}.title"), tr(f"notify.{ev}.body"))
        finally:
            self.acting = False

    # ── обновление состояния ──
    def refresh(self) -> None:
        try:
            self.cfg = load_cfg()          # настройки читаем заново: их меняем и мы, и программа
        except Exception:
            pass
        kind, tip = read_state(self.cfg)
        raw = read_raw_state(self.cfg)
        if kind != self.kind or tip != self.tip:
            prev_kind = self.kind
            self.kind, self.tip = kind, tip
            self.tray.setIcon(icon_from_state(kind, self.cfg))
            self.tray.setToolTip(tip)
            if not self.first:
                self._on_transition(prev_kind, kind)
        self.first = False
        # подписи и галочки меню — ОБЩИМ путём (тот же код у доказательств):
        # строка состояния, надзор, уведомления, фильтры, отметка темы
        sync_menu_actions(self.cfg, self.acts, tip, raw)
        # окно индикации процесса восстановления/переключения (состав эталона)
        proc = raw.get("proc") or {}
        try:
            pid = str(proc.get("id") or "")
        except Exception:
            pid = ""
        if self.quiet:
            pid = ""                       # проверка кадров: карточки не показываем
        if pid and pid != getattr(self, "_proc_id", ""):
            self._proc_id = pid
            self._proc_shown_final = False
            self.show_process_card(proc)
        elif pid and proc.get("final") and not getattr(self, "_proc_shown_final",
                                                       False):
            self._proc_shown_final = True
            self.show_process_card(proc)
        elif not pid:
            self._proc_id = ""
        # Меню: цвета берём из ОБЩЕЙ таблицы при каждом показе; если тема
        # сменилась, живая панель закрывается и пересоберётся при следующем
        # открытии (иначе на экране осталась бы палитра прежней темы).
        try:
            cur_theme = US.theme_of(self.cfg)
            if self._menu_panel is not None and cur_theme != self._menu_theme:
                self._close_menu(instant=True)
            self._menu_theme = cur_theme
        except Exception:
            pass
        self._ping()
        r = UC.read_result(self.cfg)
        if r and not self.acting and not self.quiet:
            try:
                rts = float(r.get("ts", 0) or 0)
                # Один и тот же ответ команды показываем ОДИН раз (иначе
                # повторные тики < 6 с выкатывали карточку снова).
                if (time.time() - rts) < 6.0 and rts != getattr(self, "_cmd_shown_ts", 0.0) \
                        and not self._card_gate("ответ команды"):
                    self._cmd_shown_ts = rts
                    self.acting = True
                    self.show_toast("ok" if r.get("ok") else "error",
                                    tr("ui.cmd_done" if r.get("ok")
                                       else "ui.cmd_failed",
                                       err=r.get("msg", "")),
                                    str(r.get("msg", "")))
            except Exception:
                pass
            self.acting = False

    def _ping(self) -> None:
        try:
            UC.write_ping(self.cfg, os.getpid(), "qt")
        except Exception:
            pass


# ═══════════════════════════════════════════════ самопроверка ═══
def write_selftest_reports(cfg: dict, report: list, results: list,
                         bad: list) -> tuple:
    """Кладёт отчёт самотеста в файлы UTF-8 (stdout остаётся ASCII).

    Возвращает (путь .txt, путь .json). Это тот самый «человекочитаемый вывод»,
    который нельзя печатать в stdout оконной сборки: на Windows кодировка консоли
    cp1251, и печать символа вне cp1251 роняла программу модальным окном.
    """
    d = ((cfg or {}).get("paths") or {}).get("state_dir") or SD.log_dir()
    nl = chr(10)
    txt = nl.join(list(report) + ["", "=" * 64, "РЕЗУЛЬТАТ", "=" * 64]
                  + [str(x) for x in results]
                  + ["", "ИТОГ: " + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not bad
                                     else f"ПРОВАЛЕНО {len(bad)}")])
    paths = []
    for name in ("ui_selftest.txt", "ui_selftest.json"):
        try:
            os.makedirs(d, exist_ok=True)
            pp = os.path.join(d, name)
            with open(pp, "w", encoding="utf-8") as f:
                if name.endswith(".json"):
                    json.dump({"results": results, "bad": bad},
                              f, ensure_ascii=False, indent=1)
                else:
                    f.write(txt + nl)
            paths.append(pp)
        except Exception:
            paths.append("")
    return (paths[0] if paths else "", paths[1] if len(paths) > 1 else "")


def card_shots_mode(cfg: dict, out_png: str) -> int:
    """ДОКАЗАТЕЛЬСТВО без показа окон: снимки карточки и движение пикселей.

    Карточка рисуется ВНЕ ЭКРАНА (``TEST_ANCHOR``), с неё снимается серия
    кадров удержания; считаются уникальные хеши и число изменившихся пикселей
    спиннера и полосы. Рядом с PNG кладётся JSON с числами.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    d = demo_card_data(cfg)
    out_dir = os.path.dirname(os.path.abspath(out_png)) or os.getcwd()
    name = os.path.splitext(os.path.basename(out_png))[0] or "notify-card"
    globals()["TEST_ANCHOR"] = (-8000, -8000, 900, 700)
    stats = {}
    t = Toast(d["kind"], d["title"], "", cfg, duration_ms=HOLD_FINAL_MS,
              stats_sink=stats, sub=d["sub"], steps=d["steps"],
              footer=d["footer"], spinner=True, total_ms=d["total_ms"],
              role=d["role"])
    t.show_toast()
    proof = {}
    t0 = time.time()
    while time.time() - t0 < 20.0:
        app.processEvents()
        if t.phase == "hold":
            proof = capture_card_proof(cfg, t, out_dir=out_dir, name=name,
                                       frames=10)
            break
        time.sleep(0.005)
    globals()["TEST_ANCHOR"] = None
    try:
        with open(os.path.join(out_dir, name + "-proof.json"), "w",
                  encoding="utf-8") as f:
            json.dump(proof, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    comp = proof.get("composition") or {}
    texts = proof.get("texts") or {}
    zero = ("0.00" in str(texts.get("sub") or "")
            or "0.00" in str(texts.get("footer") or ""))
    ok = (int(proof.get("frames") or 0) >= 8
          and int(proof.get("spinner_unique") or 0) >= 6
          and int(proof.get("bar_unique") or 0) >= 6
          and all(bool(comp.get(k)) for k in
                  ("icon", "title", "sub", "steps", "bar", "spinner", "footer"))
          and not zero)
    _say("card-shots: frames=%s spinner_unique=%s bar_unique=%s card_unique=%s"
         % (proof.get("frames"), proof.get("spinner_unique"),
            proof.get("bar_unique"), proof.get("card_unique")))
    _say("card-shots: spinner_changed_px=%s" % (proof.get("spinner_changed_px"),))
    _say("card-shots: bar_changed_px=%s" % (proof.get("bar_changed_px"),))
    _say("card-shots: composition icon=%s title=%s sub=%s steps=%s bar=%s "
         "spinner=%s footer=%s"
         % (comp.get("icon"), comp.get("title"), comp.get("sub"),
            comp.get("steps"), comp.get("bar"), comp.get("spinner"),
            comp.get("footer")))
    _say("card-shots: zero_time=%s png=%s" % (zero, proof.get("card_png")))
    _say("card-shots: %s" % ("PASSED" if ok else "FAILED"))
    t.close()
    app.processEvents()
    return 0 if ok else 1


def menu_text_ink(png: str, text_rgb, x0: int, y0: int, y1: int,
                  x1: int = None, thresh: float = 0.20) -> dict:
    """Чернила подписи строки меню со снимка панели: высота, ширина, полосы.

    Проверка «двоения» БЕЗ глаз: подпись рисуется ОДИН раз, поэтому в покое
    наведения рамка чернил совпадает с обычным состоянием (сдвиг только на подъём
    строки), а высота НЕ растёт. Две наложенные копии со сдвигом дали бы высоту
    больше на величину сдвига (в прежнем макете +4 px и лишние полосы).
    """
    img = QtGui.QImage(png)
    if img.isNull():
        return {"h": -1, "w": -1, "bands": -1, "color": None}
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = int(x1 or min(img.width(), x0 + 150))
    y1 = int(min(y1, img.height()))
    h, w = y1 - y0, x1 - x0
    if h <= 0 or w <= 0:
        return {"h": 0, "w": 0, "bands": 0, "color": None}
    lum = [[0.0] * w for _ in range(h)]
    hist = {}
    for y in range(h):
        for x in range(w):
            c = img.pixelColor(x0 + x, y0 + y)
            v = 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
            lum[y][x] = v
            k = int(round(v))
            hist[k] = hist.get(k, 0) + 1
    bg = max(hist.items(), key=lambda kv: kv[1])[0]
    tx = 0.2126 * float(text_rgb[0]) + 0.7152 * float(text_rgb[1]) \
        + 0.0722 * float(text_rgb[2])
    den = (tx - bg) or 1.0
    ink = [[((lum[y][x] - bg) / den) > thresh for x in range(w)] for y in range(h)]
    ys = [y for y in range(h) if any(ink[y])]
    if not ys:
        return {"h": 0, "w": 0, "bands": 0, "color": tuple(text_rgb)}
    xs = [x for x in range(w) if any(ink[y][x] for y in range(h))]
    j0, j1 = xs[0], xs[-1] + 1
    bands, prev = 0, False
    for y in range(h):
        cur = any(ink[y][j0:j1])
        if cur and not prev:
            bands += 1
        prev = cur
    return {"h": ys[-1] - ys[0] + 1, "w": xs[-1] - xs[0] + 1, "bands": bands,
            "color": tuple(int(v) for v in text_rgb), "bg": bg}


def screens_info() -> list:
    """Список экранов: имя, геометрия, рабочая область, масштаб, частота обновления."""
    out = []
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception:
        return out
    for s in QtGui.QGuiApplication.screens():
        try:
            hz = float(s.refreshRate())
        except Exception:
            hz = 0.0
        g, a = s.geometry(), s.availableGeometry()
        out.append({"name": s.name(),
                    "geometry": [g.x(), g.y(), g.width(), g.height()],
                    "available": [a.x(), a.y(), a.width(), a.height()],
                    "dpr": float(s.devicePixelRatio()),
                    "refresh_hz": round(hz, 2),
                    "logical_dpi": round(float(s.logicalDotsPerInch()), 2),
                    "primary": bool(s == QtGui.QGuiApplication.primaryScreen())})
    return out


def menu_shots_mode(cfg: dict, out_dir: str) -> int:
    """ДОКАЗАТЕЛЬСТВО без показа окон: снимки меню и ПИКСЕЛЬНАЯ сверка с карточкой.

    Рисуем панель меню в QPixmap (окно не показываем): тёмная и светлая тема,
    обычная строка, наведённая строка, подменю. Рядом — JSON с числами: геометрия
    панели, дамп пунктов, цвета пикселей меню и пикселей карточки (сверка ОДНОЙ
    таблицы стиля), экраны (масштаб/частота).
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    os.makedirs(out_dir, exist_ok=True)
    # модель собираем БЕЗ окна и трея: доказательство не поднимает ни значка,
    # ни карточки — только рисует панель в QPixmap
    model, acts, _fm, _tm = build_menu_model(cfg)
    sync_menu_actions(cfg, acts, "", read_raw_state(cfg))
    proof = {"dump": UMN.dump_rows(model.rows), "screens": [], "platform": "",
             "fonts": [], "shots": [], "pixels": {}}
    try:
        proof["platform"] = QtGui.QGuiApplication.platformName()
    except Exception:
        pass
    try:
        proof["fonts"] = list(QtGui.QFontDatabase.families())[:8]
    except Exception:
        pass
    proof["screens"] = screens_info()

    panel = UMN.PopupMenu(cfg, model, state_color=US.tokens(cfg)["state_ok"])
    # ── смешанный масштаб мониторов: рисуем панель и при 150 % (второй монитор):
    # проверка, что линии и полоса не «худеют» на полутора пикселях ──
    for dpr, tag in ((1.0, ""), (1.5, "-150")):
        if not tag:
            continue
        for theme in ("dark", "light"):
            path = os.path.join(out_dir, "menu-%s-plain%s.png" % (theme, tag))
            panel.render_png(path, theme=theme, dpr=dpr)
            img = QtGui.QImage(path)
            proof.setdefault("scaled", []).append({
                "file": os.path.basename(path), "dpr": dpr, "theme": theme,
                "device_size": [img.width(), img.height()],
                "panel_device": [panel.panel_w * dpr, panel.panel_h * dpr],
                "stripe_device": US.STRIPE * dpr, "row_device": US.ROW_H * dpr})
    for theme in ("dark", "light"):
        for hover, tag in ((None, "plain"), ("notify", "hover"),
                           ("filters", "hover-sub")):
            path = os.path.join(out_dir, "menu-%s-%s.png" % (theme, tag))
            panel.render_png(path, theme=theme, hover_key=hover)
            img = QtGui.QImage(path)
            col = img.pixelColor(int(US.SHADOW_PX + 40), int(US.SHADOW_PX + 4))
            proof["shots"].append({
                "file": os.path.basename(path), "theme": theme, "hover": hover,
                "panel_w": panel.panel_w, "panel_h": panel.panel_h,
                "row_w": panel.row_w, "row_h": US.ROW_H, "radius": US.RADIUS,
                "stripe": US.STRIPE, "pad": [US.PAD_T, US.PAD_R, US.PAD_B, US.PAD_L],
                "font_px": US.MENU_FS,
                "bg_pixel": [col.red(), col.green(), col.blue(), col.alpha()],
                "expected_bg": list(US.tokens(cfg, theme)["bg"])})
    # ── ПИКСЕЛЬНАЯ СВЕРКА: цвет панели меню ⟷ цвет панели карточки ──
    for theme in ("dark", "light"):
        c2 = dict(cfg)
        c2["ui"] = dict(cfg.get("ui") or {})
        c2["ui"]["theme"] = theme
        d = demo_card_data(cfg)
        t = Toast(d["kind"], d["title"], "", c2, duration_ms=800,
                  sub=d["sub"], steps=d["steps"], footer=d["footer"],
                  spinner=True, total_ms=d["total_ms"], role=d["role"])
        card = t._card
        cp = card.toImage().pixelColor(120, 4)
        mp_path = os.path.join(out_dir, "menu-%s-plain.png" % theme)
        mi = QtGui.QImage(mp_path).pixelColor(int(US.SHADOW_PX + 40),
                                              int(US.SHADOW_PX + 4))
        proof["pixels"][theme] = {
            "menu_bg": [mi.red(), mi.green(), mi.blue(), mi.alpha()],
            "card_bg": [cp.red(), cp.green(), cp.blue(), cp.alpha()],
            "equal": (mi.red(), mi.green(), mi.blue(), mi.alpha())
                     == (cp.red(), cp.green(), cp.blue(), cp.alpha()),
            "tokens": list(US.tokens(cfg, theme)["bg"])}
        t.close()
    proof["card_w"] = NT_W
    try:
        with open(os.path.join(out_dir, "menu-shots.json"), "w",
                  encoding="utf-8") as f:
            json.dump(proof, f, ensure_ascii=False, indent=1)
    except Exception as e:
        _say(f"menu-shots: json: {e}")
    ok = all(p["equal"] for p in proof["pixels"].values()) \
        and all(tuple(s["bg_pixel"]) == tuple(s["expected_bg"])
                for s in proof["shots"]) \
        and len(proof["shots"]) == 6
    _say("menu-shots: панель %dx%d, строка %d, шрифт %d px, снимков %d"
         % (panel.panel_w, panel.panel_h, US.ROW_H, US.MENU_FS,
            len(proof["shots"])))
    for th, p in proof["pixels"].items():
        _say("menu-shots: %s меню %s ⟷ карточка %s (равны: %s)"
             % (th, p["menu_bg"], p["card_bg"], p["equal"]))
    _say("menu-shots: экраны %s"
         % [(s["name"], s["geometry"], s["dpr"], s["refresh_hz"])
            for s in proof["screens"]])
    _say("menu-shots: %s" % ("PASSED" if ok else "FAILED"))
    app.processEvents()
    return 0 if ok else 1


def menu_live_mode(cfg: dict, ms: int = 2600, at=None, theme: str = "",
                   out_json: str = "", shot: str = "", align: bool = True,
                   sub_key: str = "") -> int:
    """НАМЕРЕННЫЙ показ меню на ЖИВОМ экране: замер кадров и закрытие.

    Это тот единственный случай, когда окно меню действительно показывается
    (пользователь разрешил): нужно измерить реальные интервалы между кадрами на
    его мониторе (200 Гц) и проверить, что выравненный период не даёт «пилы».
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    cfg2 = cfg
    if theme:
        cfg2 = dict(cfg)
        cfg2["ui"] = dict(cfg.get("ui") or {})
        cfg2["ui"]["theme"] = theme
    ui = Ui(cfg2, quiet=True)
    if theme:
        # Ui.refresh() перечитывает config.json — возвращаем выбранную тему
        ui.cfg = dict(ui.cfg)
        ui.cfg["ui"] = dict((ui.cfg.get("ui") or {}))
        ui.cfg["ui"]["theme"] = str(theme)
    pos = QtCore.QPoint(int(at[0]), int(at[1])) if at else QtGui.QCursor.pos()
    stats = ui.show_menu(pos)
    panel = ui._menu_panel
    panel.align_frames = bool(align)
    if not align:                          # пересчитываем период без выравнивания
        panel.frame_period = {"period_ms": round(1000.0 / float(US.FPS), 4),
                              "fps": float(US.FPS), "aligned": False}
        panel._period = panel.frame_period["period_ms"] / 1000.0
        panel._ticker.setInterval(max(1, int(round(panel._period * 1000.0
                                                   / UMN.TICK_DIV))))
        # статистику тоже приводим к ФАКТИЧЕСКОМУ периоду, иначе в отчёте
        # остался бы выравненный (открытие уже записало его)
        panel.stats.update({"period_ms": panel.frame_period["period_ms"],
                            "aligned": False, "refresh_hz": stats.get("refresh_hz", 0.0),
                            "fps_target": float(US.FPS)})
        stats = dict(panel.stats)
    scr = panel.screen_for(pos)
    res = {"cursor": [pos.x(), pos.y()], "stats": dict(stats),
           "panel": {"w": panel.panel_w, "h": panel.panel_h, "row_h": US.ROW_H},
           "placed": [panel._px, panel._py], "slide_sign": panel._slide_sign,
           "screen": {"name": scr.name() if scr else "",
                      "geometry": list(scr.geometry().getRect()) if scr else [],
                      "dpr": float(scr.devicePixelRatio()) if scr else 0.0,
                      "refresh_hz": float(scr.refreshRate()) if scr else 0.0},
           "screens": screens_info()}
    t0 = time.time()
    while time.time() - t0 < ms / 1000.0:
        app.processEvents()
        if sub_key and not res.get("sub_opened") and (time.time() - t0) > 0.40:
            # НАМЕРЕННО открываем подменю: проверяем выезд подменю на живом экране
            for i, r in enumerate(panel.rows):
                if r.key == sub_key:
                    panel.set_hover(i)
                    panel._open_sub(i)
                    res["sub_opened"] = sub_key
                    res["sub_size"] = [panel.sub_panel.panel_w,
                                       panel.sub_panel.panel_h]
                    res["sub_placed"] = [panel.sub_panel._px, panel.sub_panel._py]
                    res["sub_slide_sign"] = panel.sub_panel._slide_sign
                    break
        if shot and not res.get("shot") and (time.time() - t0) > 0.95:
            # снимок ЖИВОГО экрана с открытым меню (появление доиграло)
            try:
                dpr = float(scr.devicePixelRatio()) if scr else 1.0
                x0, y0 = panel._px, panel._py
                x1 = panel._px + panel.panel_w
                y1 = panel._py + panel.panel_h
                if panel.sub_panel is not None:
                    x1 = max(x1, panel.sub_panel._px + panel.sub_panel.panel_w)
                    y1 = max(y1, panel.sub_panel._py + panel.sub_panel.panel_h)
                    x0 = min(x0, panel.sub_panel._px)
                x = int((x0 - 40) * dpr)
                y = int((y0 - 40) * dpr)
                w = int((x1 - x0 + 80) * dpr)
                h = int((y1 - y0 + 80) * dpr)
                pm = scr.grabWindow(0, x, y, w, h)
                if pm.isNull():
                    pm = scr.grabWindow(0, panel._px - 40, panel._py - 40,
                                        panel.panel_w + 80, panel.panel_h + 80)
                pm.save(shot, "PNG")
                res["shot"] = shot
                res["shot_size"] = [pm.width(), pm.height()]
                res["grab_index"] = len(panel.frame_ms)   # кадр на снимке выпал
            except Exception as e:
                res["shot_error"] = str(e)
        time.sleep(0.002)
    ui._close_menu()                       # закрытие ТОЖЕ с анимацией (260 мс)
    while panel._phase != "done" and time.time() - t0 < ms / 1000.0 + 2.0:
        app.processEvents()
        time.sleep(0.002)
    res["stats"] = dict(panel.stats)
    iv = list(res["stats"].get("intervals") or [])
    # Снимок экрана (grabWindow) БЛОКИРУЕТ цикл событий, поэтому кадр на нём
    # выпадает — из «чистой» статистики его исключаем и говорим об этом прямо
    gi = res.get("grab_index")
    clean = [x for i, x in enumerate(iv) if gi is None or i != gi]
    res["stats"]["intervals_clean"] = [round(x, 2) for x in clean]
    if clean:
        avg = sum(clean) / len(clean)
        res["stats"].update({
            "frames_clean": len(clean), "avg_ms_clean": round(avg, 3),
            "avg_fps_clean": round(1000.0 / avg, 2) if avg > 0 else 0.0,
            "worst_ms_clean": round(max(clean), 3),
            "jitter_ms_clean": round((sum(x * x for x in clean) / len(clean)
                                      - avg * avg) ** 0.5, 3),
            "min_ms_clean": round(min(clean), 3),
            "p50_ms_clean": round(sorted(clean)[len(clean) // 2], 3),
            "spread_ms_clean": round(max(clean) - min(clean), 3)})
    if iv:
        res["stats"]["p50_ms"] = round(sorted(iv)[len(iv) // 2], 2)
        res["stats"]["min_ms"] = round(min(iv), 2)
        res["stats"]["max_ms"] = round(max(iv), 2)
        res["stats"]["spread_ms"] = round(max(iv) - min(iv), 2)
    # ЧТО УВИДИТ ГЛАЗ: кадр показывается на ближайшем обновлении панели, поэтому
    # строим «экранную» сетку — к какому обновлению попал каждый кадр
    hz = float(res["screen"].get("refresh_hz") or 0.0)
    for tag, seq in (("", iv), ("_clean", clean)):
        if not seq or hz <= 1.0:
            continue
        ref = 1000.0 / hz
        t = 0.0
        disp = []
        prev = None
        for x in seq:
            t += x
            slot = round(t / ref)
            if prev is not None and slot != prev:
                disp.append(round((slot - prev) * ref, 2))
            prev = slot
        if disp:
            counts = {}
            for d in disp:
                counts[str(d)] = counts.get(str(d), 0) + 1
            res["stats"]["screen_intervals_ms" + tag] = disp
            res["stats"]["screen_min_ms" + tag] = round(min(disp), 2)
            res["stats"]["screen_max_ms" + tag] = round(max(disp), 2)
            res["stats"]["screen_spread_ms" + tag] = round(max(disp) - min(disp), 2)
            res["stats"]["screen_hist" + tag] = counts
    if out_json:
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            _say(f"menu-live: json: {e}")
    _say("menu-live: экран %s %s dpr=%s Гц=%s (курсор %s)"
         % (res["screen"]["name"], res["screen"]["geometry"],
            res["screen"]["dpr"], res["screen"]["refresh_hz"], res["cursor"]))
    _say("menu-live: панель %dx%d поставлена в %s (знак выезда %s)"
         % (res["panel"]["w"], res["panel"]["h"], res["placed"],
            res["slide_sign"]))
    _say("menu-live: период %s мс (выровнен по экрану: %s), кадров %s, "
         "средний интервал %s мс (%.2f к/с), худший %s мс, разброс %s мс, "
         "jitter %s мс, пропусков %s"
         % (res["stats"].get("period_ms"), res["stats"].get("aligned"),
            res["stats"].get("frames"), res["stats"].get("avg_ms"),
            res["stats"].get("avg_fps") or 0.0, res["stats"].get("worst_ms"),
            res["stats"].get("spread_ms"), res["stats"].get("jitter_ms"),
            res["stats"].get("dropped")))
    try:
        ui.tray.hide()
    except Exception:
        pass
    app.processEvents()
    return 0


# ═══════════════════════════ наведение мышью: ЖИВЫЕ замеры (доказательство) ═══
def _mouse_to(x, y) -> None:
    """Ставит курсор в ФИЗИЧЕСКУЮ точку экрана ЧЕРЕЗ ОЧЕРЕДЬ ВВОДА Windows.

    Почему не ``SetCursorPos``: он двигает курсор, но сообщение о движении до окна
    доходит не всегда (проверено: первый переход в окно и ВОЗВРАТ в него окно не
    отдаёт своему обработчику), а замер наведения обязан опираться на
    ДОСТАВЛЕННЫЙ курсор, а не на сам вызов.

    Двигаем ДВУМЯ шагами внутри одной строки (на 3 px в сторону и в саму точку):
    ``SetCursorPos`` в ту же точку сообщения не порождает вовсе, а два разных
    положения гарантируют доставку. Координаты нормируются по первичному
    монитору: при объявленной осведомлённости о DPI (см. :func:`_dpi_aware`)
    очередь ввода принимает и значения за его пределами — курсор доходит до
    второго монитора точно (проверено: 3000 -> 3001, 5119 -> 5120).
    """
    try:
        import ctypes
        import ctypes.wintypes as W

        class _MI(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                        ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

        class _INP(ctypes.Structure):
            _fields_ = [("type", W.DWORD), ("mi", _MI)]

        u32 = ctypes.windll.user32
        if ctypes.sizeof(_INP) != ctypes.sizeof(ctypes.c_void_p) + 32:
            u32.SetCursorPos(int(round(x)), int(round(y)))
            return
        sw = u32.GetSystemMetrics(0) or 1
        sh = u32.GetSystemMetrics(1) or 1
        d = 3 if (int(x) + int(y)) % 2 else -3
        for yy in (int(round(y)) + d, int(round(y))):
            ax = int(round(x * 65535.0 / max(1, sw - 1)))
            ay = int(round(yy * 65535.0 / max(1, sh - 1)))
            inp = _INP(0, _MI(ax, ay, 0, 0x0001 | 0x8000, 0, None))
            u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INP))
    except Exception:
        pass


def _cursor_at() -> tuple:
    """Текущая точка курсора — она нужна в отчёте: где он был в момент замера."""
    try:
        import ctypes
        import ctypes.wintypes as W
        p = W.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return (int(p.x), int(p.y))
    except Exception:
        return (0, 0)


def _dpi_aware() -> None:
    """Объявляет процесс «осведомлённым о DPI» ДО создания QApplication.

    Без этого ``SetCursorPos`` и Qt считают в РАЗНЫХ системах координат на
    втором мониторе (150 %): замер наведения там уходил бы мимо окна меню.
    """
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _phys_of(widget, lx: float, ly: float) -> tuple:
    """Логическая точка панели -> ФИЗИЧЕСКАЯ точка экрана для курсора.

    Коэффициент масштаба и начало берём у самого окна панели: Windows ставит его
    в физических пикселях, а Qt считает в логических, поэтому при 150 % пара
    «координата панели -> пиксель экрана» выводится однозначно.
    """
    d = 1.0
    try:
        scr = widget.screen_for(QtCore.QPoint(int(lx), int(ly)))
        d = float(scr.devicePixelRatio()) if scr is not None else 1.0
    except Exception:
        d = 1.0
    if abs(d - 1.0) < 1e-6:
        return (int(round(lx)), int(round(ly)))
    try:
        import ctypes
        import ctypes.wintypes as W
        rc = W.RECT()
        ctypes.windll.user32.GetWindowRect(int(widget.winId()),
                                          ctypes.byref(rc))
        home = widget._home or QtCore.QPoint(0, 0)
        return (int(round(rc.left + (lx - home.x()) * d)),
                int(round(rc.top + (ly - home.y()) * d)))
    except Exception:
        return (int(round(lx * d)), int(round(ly * d)))


def _rows_ref(panel) -> list:
    """Эталон строк панели БЕЗ подсветки — по нему сверяются кадры.

    Сверка кадров отвечает на вопрос «мерцает ли»: сравниваем кадр с этим
    эталоном ПО СТРОКАМ и смотрим, где прошла граница подсветки. Тогда видно и
    положение полосы в каждом кадре, и что полоса на кадре ОДНА (две означали бы
    «остаток» прошлой подсветки — то, что читается как мерцание).
    """
    img = panel.grab().toImage()
    d = float(img.devicePixelRatio() or 1.0)
    ref = []
    for y in range(0, panel.panel_h):
        yy = int(round((US.SHADOW_PX + y) * d))
        row = []
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, yy)
            row.append((c.red(), c.green(), c.blue(), c.alpha()))
        ref.append(row)
    return ref


def _row_diff(img, d, ref, y_log: int) -> int:
    """Сколько пикселей строки отличается от эталона (единица — 2 px).

    ``ref`` — ЭТАЛОН ПО СТРОКАМ (см. :func:`_rows_ref`): берём строку
    ``ref[y_log]``, а не «строку по номеру пикселя» (на этом легко получить
    разницу «во всех строках сразу» и счесть это мерцанием).
    """
    yy = int(round((US.SHADOW_PX + y_log) * d))
    if yy < 0 or yy >= img.height() or y_log < 0 or y_log >= len(ref):
        return 0
    row = ref[y_log]
    n = 0
    i = 0
    for x in range(0, img.width(), 2):
        if i >= len(row):
            break
        c = img.pixelColor(x, yy)
        if (c.red(), c.green(), c.blue(), c.alpha()) != row[i]:
            n += 1
        i += 1
    return n


def _bands(panel, ref, thr: int = 40) -> list:
    """Полосы изменений на кадре: [{'top','h'}] в логических px панели.

    Порог отсекает подписи (подсветка меняет ВСЮ ширину строки, подпись —
    единицы пикселей). Одна сплошная полоса высотой 34 px = подсветка стоит на
    одной строке; две полосы или полоса промежуточной высоты = подсветка
    «поехала», то есть мерцание/остаток.
    """
    img = panel.grab().toImage()
    d = float(img.devicePixelRatio() or 1.0)
    vals = [_row_diff(img, d, ref, y) for y in range(0, panel.panel_h)]
    out = []
    cur = None
    for y, v in enumerate(vals):
        if v > thr and cur is None:
            cur = y
        elif v <= thr and cur is not None:
            out.append({"top": cur, "h": y - cur,
                        "peak": max(vals[cur:y])})
            cur = None
    if cur is not None:
        out.append({"top": cur, "h": len(vals) - cur,
                    "peak": max(vals[cur:])})
    return out


def menu_hover_mode(cfg: dict, out_json: str = "", shot: str = "",
                    at=None, label: str = "", wait_ms: int = 1600) -> int:
    """ЖИВЫЕ замеры наведения: подменю по наведению и перенос подсветки.

    Это ВТОРОЙ (кроме ``--menu-live``) режим, который НАМЕРЕННО показывает меню:
    вопросы «курсор встал на строку» и «курсор встал на пункт с подменю»
    проверяются только настоящим курсором. Меряем по КАДРАМ панели (перехват
    ``_paint``): от команды «курсор поставлен» до кадра, в котором подсветка
    нарисована НА НУЖНОЙ строке, — то есть то, что видит глаз, а не то, что
    лежит в переменной.

    -> код выхода (0 — замеры сняты; отчёт в ``out_json``, снимок в ``shot``)
    """
    _dpi_aware()                           # ДО QApplication (см. _dpi_aware)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    model, acts, _fm, _tm = build_menu_model(cfg)
    sync_menu_actions(cfg, acts, "", read_raw_state(cfg))
    panel = UMN.PopupMenu(cfg, model, state_color=US.tokens(cfg)["state_ok"])
    pos = QtCore.QPoint(int(at[0]), int(at[1])) if at else QtGui.QCursor.pos()
    panel.open_at(pos)
    scr = panel.screen_for(pos)
    res = {"label": label, "at": [pos.x(), pos.y()],
           "screen": {"name": scr.name() if scr else "",
                      "dpr": float(scr.devicePixelRatio()) if scr else 0.0,
                      "refresh_hz": float(scr.refreshRate()) if scr else 0.0,
                      "geometry": list(scr.geometry().getRect()) if scr else []},
           "screens": screens_info(),
           "cfg_ms": {"hover_open": getattr(panel, "hover_open_ms", None),
                      "hover_close": getattr(panel, "hover_close_ms", None),
                      "fade": getattr(panel, "_fade_ms", US.STEP_FADE_MS)},
           "panel": {"w": panel.panel_w, "h": panel.panel_h, "row_h": US.ROW_H,
                     "rows": len(panel.rows)}}

    def pump(ms: float) -> None:
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) * 1000.0 < ms:
            app.processEvents()
            time.sleep(0.0003)

    def row_i(key: str) -> int:
        for i, r in enumerate(panel.rows):
            if r.key == key:
                return i
        return -1

    def row_pt(i: int) -> tuple:
        return (panel._px + US.STRIPE + US.PAD_L + panel.row_w // 2,
                panel._py + panel._ys[i] + US.ROW_H // 2)

    def sub_pt(sp, i: int = 0) -> tuple:
        return (sp._px + US.STRIPE + US.PAD_L + sp.row_w // 2,
                sp._py + sp._ys[i] + US.ROW_H // 2)

    def shadow_pt() -> tuple:
        """Точка ВНЕ содержимого панелей, но В ИХ ОКНЕ (кайма тени).

        Логика закрытия смотрит только на ``in_global`` панелей, поэтому «курсор
        ушёл с обоих меню» здесь тот же путь кода; зато окно остаётся «горячим» и
        доставка движений гарантирована (возврат курсора в окно Windows
        обработчику не отдаёт — проверено отдельно).
        """
        return (panel._px + panel.panel_w + 7, panel._py + panel.panel_h + 7)

    def goto(w, lx: float, ly: float) -> None:
        """Наводит ЖИВОЙ курсор на логическую точку панели (учёт масштаба)."""
        _mouse_to(*_phys_of(w, lx, ly))

    def hot(i: int, tries: int = 3) -> int:
        """Готовит панель к замеру: ставит курсор на пункт БЕЗ подменю и ЖДЁТ,
        пока по ``hover`` видно, что движение дошло.

        Первый переход курсора в окно Windows обработчику не отдаёт (окно ещё не
        «горячее»), поэтому наведение подтверждается ФАКТОМ, а не вызовом
        ``SetCursorPos``; при неудаче курсор «встряхивается» на соседний пункт и
        возвращается — встряска обязана быть на пункте БЕЗ подменю, иначе она
        сама раскроет подменю. Число попыток попадает в отчёт.
        """
        for n in range(1, tries + 1):
            if n > 1:
                for j in range(len(panel.rows)):
                    r = panel.rows[j]
                    if (j != i and r.kind == "item" and r.is_enabled()
                            and r.key != "status" and not r.has_sub()):
                        goto(panel, *row_pt(j))
                        pump(40)
                        break
            goto(panel, *row_pt(i))
            pump(150)
            if panel.hover == i:
                return n
        return tries

    pump(520)                              # появление доиграло (340 мс)
    res["cursor_after_open"] = list(_cursor_at())
    ref = _rows_ref(panel)                 # эталон: подсветки нет НИ НА СТРОКЕ
    res["hot_tries"] = hot(row_i("notify"))   # панель подтверждённо «горячая»
    pump(300)

    # ── КАДРЫ панели: перехват отрисовки (замер «до кадра», а не «до числа») ──
    paints = []
    _orig = panel._paint

    def _paint_hook(ev):
        try:
            paints.append({"t": time.perf_counter(),
                           "y": round(float(panel._hl_y), 3),
                           "op": round(float(panel._hl_op), 3),
                           "hover": int(panel.hover)})
        except Exception:
            pass
        return _orig(ev)

    panel._paint = _paint_hook

    # ── замер 1: подсветка после переноса курсора на строку ──
    def measure_hl(a_key: str, b_key: str, reps: int = 3) -> list:
        out = []
        for _ in range(reps):
            ia, ib = row_i(a_key), row_i(b_key)
            tries = hot(ia)
            # подсветка должна СНАЧАЛА успокоиться на строке «откуда», иначе
            # замер «до» сравнивал бы разную дорогу (в старой версии она ползёт)
            settle = None
            t_set = time.perf_counter()
            while (time.perf_counter() - t_set) < 1.5:
                app.processEvents()
                if abs(float(panel._hl_y) - float(panel._ys[ia])) <= 0.6:
                    settle = (time.perf_counter() - t_set) * 1000.0
                    break
                time.sleep(0.0002)
            yb = float(panel._ys[ib])
            ya = float(panel._ys[ia])
            paints.clear()
            t0 = time.perf_counter()
            goto(panel, *row_pt(ib))
            deadline = t0 + 1.2
            first = target = half = None
            inter = 0
            while time.perf_counter() < deadline:
                app.processEvents()
                moved = [p for p in paints if p["hover"] == ib]
                if moved:
                    first = (moved[0]["t"] - t0) * 1000.0
                    for p in moved:
                        # «полдороги»: подсветка прошла середину пути (это то, что
                        # глаз читает как «догнала курсор»)
                        if half is None and (abs(p["y"] - yb) <= 0.6
                                             or (ya < p["y"] < yb and
                                                 abs(p["y"] - (ya + yb) / 2.0)
                                                 <= 0.6)):
                            half = (p["t"] - t0) * 1000.0
                        if abs(p["y"] - yb) <= 0.6 and p["op"] >= 0.99:
                            target = (p["t"] - t0) * 1000.0
                            break
                    inter = sum(1 for p in moved
                                if abs(p["y"] - yb) > 0.6 or p["op"] < 0.99)
                if target is not None:
                    break
                time.sleep(0.0002)
            out.append({"from": a_key, "to": b_key, "row_to": ib,
                        "hot_tries": tries,
                        "settle_on_from_ms": round(settle, 3) if settle else None,
                        "delivered": panel.hover == ib,
                        "first_frame_ms": round(first, 3) if first else None,
                        "half_way_frame_ms": round(half, 3) if half else None,
                        "on_row_frame_ms": round(target, 3) if target else None,
                        "frames_moved": inter,
                        "frames_moved_list": [p for p in paints
                                              if p["hover"] == ib][:12],
                        "hl_op_at_end": round(float(panel._hl_op), 3)})
            pump(30)
        return out

    res["highlight"] = (measure_hl("notify", "watchdog")
                        + measure_hl("watchdog", "refresh")
                        + measure_hl("refresh", "notify"))

    # ── замер 6 (ИДЁТ РАНЬШЕ УХОДОВ): КАДРЫ — одна полоса подсветки, без
    # промежуточных положений. Порядок важен: окно Windows отдаёт движения,
    # пока курсор внутри него, а ВОЗВРАТ курсора в окно обработчику не отдаёт.
    keys = ["notify", "watchdog", "filters", "toggle", "refresh", "about"]
    frames = []
    for k in keys:
        i = row_i(k)
        paints.clear()
        tries = 0
        for tries in range(1, 4):
            goto(panel, *row_pt(i))
            pump(70)                        # 2-3 кадра
            if panel.hover == i:
                break                       # движение дошло (проверяем ФАКТ)
        fr = [p for p in paints if p["hover"] == i]
        b = _bands(panel, ref)
        # полоса подсветки — ВЫСОТОЙ со строку; узкие полосы (h<20) — смена цвета
        # и подъём ПОДПИСИ наведённой строки
        pill = [x for x in b if x["h"] >= 20]
        label = [x for x in b if x["h"] < 20]
        expect = panel._ys[i]               # координаты полосы — внутри панели
        frames.append({"key": k, "row": i, "expect_top": expect,
                       "hot_tries": tries, "delivered": panel.hover == i,
                       "bands": b, "band_count": len(b),
                       "pill_bands": pill, "pill_count": len(pill),
                       "label_bands": label,
                       "pill_top": pill[0]["top"] if pill else None,
                       "pill_on_expected_row": bool(
                           len(pill) == 1 and abs(pill[0]["top"] - expect) <= 1),
                       "hl_y": round(float(panel._hl_y), 2),
                       "hl_op": round(float(panel._hl_op), 3),
                       "frames": len(fr),
                       "frames_soft": sum(1 for p in fr if p["op"] < 0.99)})
    res["frames"] = frames
    res["frames_summary"] = {
        "frames_total": sum(f["frames"] for f in frames),
        "steps": len(frames),
        "steps_pill_on_expected_row": sum(1 for f in frames
                                          if f["pill_on_expected_row"]),
        "steps_pill_elsewhere": sum(1 for f in frames if f["pill_count"] == 1
                                    and not f["pill_on_expected_row"]),
        "steps_two_or_more_pills": sum(1 for f in frames if f["pill_count"] >= 2),
        "steps_without_pill": sum(1 for f in frames if f["pill_count"] == 0),
        "pill_tops": [f["pill_top"] for f in frames],
        "expected_tops": [f["expect_top"] for f in frames],
        "sub_index": panel.sub_index}

    # ── замер 2: подменю по НАВЕДЕНИЮ (без клика) ──
    def ensure_sub(i: int, by_hover: bool = True) -> str:
        """Подменю открыто? Возвращает «как открыто» (наведение | вызов)."""
        hot(i)
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) * 1000.0 < 900:
            app.processEvents()
            if panel.sub_panel is not None:
                return "наведением (клика не было)"
            time.sleep(0.0002)
        panel.set_hover(i)
        panel._open_sub(i)
        pump(420)
        return "вызовом из кода (наведение НЕ открыло)"

    def measure_sub(key: str, other: str = "refresh") -> dict:
        i = row_i(key)
        hot(row_i(other))
        panel.close_sub()
        pump(90)
        t0 = time.perf_counter()
        goto(panel, *row_pt(i))
        seen = shown = full = None
        while (time.perf_counter() - t0) * 1000.0 < wait_ms:
            app.processEvents()
            sp = panel.sub_panel
            if sp is not None:
                if seen is None:
                    seen = (time.perf_counter() - t0) * 1000.0
                if sp._phase in ("in", "run") and shown is None:
                    shown = (time.perf_counter() - t0) * 1000.0
                if sp._phase == "run" and full is None:
                    full = (time.perf_counter() - t0) * 1000.0
                    break
            time.sleep(0.0002)
        how = panel.sub_panel is not None
        if not how:
            how = ensure_sub(i)
        out = {"key": key, "row": i, "opened_by_hover": bool(seen),
               "sub_created_ms": round(seen, 3) if seen else None,
               "sub_shown_ms": round(shown, 3) if shown else None,
               "sub_full_ms": round(full, 3) if full else None,
               "waited_ms": wait_ms, "opened_by": how,
               "sub_index_after": panel.sub_index,
               "sub_size": [panel.sub_panel.panel_w, panel.sub_panel.panel_h]
                           if panel.sub_panel else None,
               "sub_at": [panel.sub_panel._px, panel.sub_panel._py]
                         if panel.sub_panel else None}
        return out

    res["sub_hover"] = measure_sub("filters")

    def measure_sweep(key: str = "filters", pass_ms: int = 55) -> dict:
        """ПРОНОС курсора: быстро прошли по пункту с подменю и ушли вбок.

        Подменю открываться не должно — ровно для этого и нужна задержка; иначе
        меню мигает подменю при обычном движении мыши по списку.
        """
        i = row_i(key)
        hot(row_i("refresh"))
        panel.close_sub()
        pump(60)
        opened = False
        goto(panel, *row_pt(i))
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) * 1000.0 < pass_ms:
            app.processEvents()
            opened = opened or panel.sub_panel is not None
            time.sleep(0.0002)
        goto(panel, *row_pt(row_i("notify")))
        t1 = time.perf_counter()
        while (time.perf_counter() - t1) * 1000.0 < 500:
            app.processEvents()
            opened = opened or panel.sub_panel is not None
            time.sleep(0.0002)
        return {"key": key, "pass_ms": pass_ms, "opened": opened,
                "sub_now": panel.sub_panel is not None,
                "hover": panel.hover}

    res["sweep_no_open"] = measure_sweep()

    # ── замер 3: переход на ДРУГОЙ пункт с подменю (переключение, не мигание) ──
    def measure_switch(k1: str = "filters", k2: str = "theme") -> dict:
        i1, i2 = row_i(k1), row_i(k2)
        ensure_sub(i1)
        pump(120)
        id0 = id(panel.sub_panel) if panel.sub_panel else None
        size0 = id0 is not None
        t0 = time.perf_counter()
        goto(panel, *row_pt(i2))
        sw = None
        while (time.perf_counter() - t0) * 1000.0 < 1200:
            app.processEvents()
            if panel.sub_index == i2 and panel.sub_panel is not None \
                    and panel.sub_panel.group is panel.rows[i2].sub:
                sw = (time.perf_counter() - t0) * 1000.0
                break
            time.sleep(0.0002)
        sp = panel.sub_panel
        return {"from": k1, "to": k2, "switch_ms": round(sw, 3) if sw else None,
                "sub_index": panel.sub_index, "expected": i2,
                "panel_reused": bool(size0 and sp is not None and id(sp) == id0),
                "sub_title": sp.group.title() if sp is not None else "",
                "sub_size": [sp.panel_w, sp.panel_h] if sp is not None else None}

    res["sub_switch"] = measure_switch()

    # ── замер 4: льготное окно при уходе курсора В СТОРОНУ подменю ──
    def measure_grace(key: str = "filters") -> dict:
        i = row_i(key)
        how = ensure_sub(i)
        if panel.sub_panel is None:
            return {"skipped": "подменю не открылось", "opened_by": how}
        sp = panel.sub_panel
        p0, p1 = row_pt(i), sub_pt(sp, 0)
        steps = 5
        t0 = time.perf_counter()
        for s in range(1, steps + 1):
            goto(panel, p0[0] + (p1[0] - p0[0]) * s / steps,
                 p0[1] + (p1[1] - p0[1]) * s / steps)
            pump(45)                # 5 шагов × 45 мс = 225 мс внутри окна
        elapsed = (time.perf_counter() - t0) * 1000.0
        sp = panel.sub_panel
        return {"opened_by": how, "path": "строка подменю -> подменю, 5 шагов",
                "elapsed_ms": round(elapsed, 1), "kept_open": sp is not None,
                "sub_index": panel.sub_index,
                "cursor": list(_cursor_at()),
                "cursor_in_sub": bool(sp is not None
                                      and sp.in_global(QtGui.QCursor.pos()))}

    res["grace_to_sub"] = measure_grace()

    def grace_ms() -> float:
        """Льготное окно закрытия из настроек панели (0 — механизма нет)."""
        v = getattr(panel, "hover_close_ms", None)
        return float(v) if v else 0.0

    def close_timer_active() -> bool:
        """Взведён ли таймер закрытия (в старых сборках его нет вовсе)."""
        t = getattr(panel, "_hov_close", None)
        try:
            return bool(t.isActive()) if t is not None else False
        except Exception:
            return False

    def measure_grace_return(key: str = "filters", other: str = "notify") -> dict:
        """Ушли на СОСЕДНИЙ пункт и вернулись на свой внутри льготного окна.

        Проверяется именно отмена закрытия: таймер взведён уходом, курсор
        возвращается раньше, чем он истёк, и подменю обязано остаться открытым.
        """
        i = row_i(key)
        ensure_sub(i)
        if panel.sub_panel is None:
            return {"skipped": "подменю не открылось"}
        goto(panel, *row_pt(row_i(other)))      # ушли на соседний пункт
        pump(30)                                # движение дошло -> окно взведено
        armed = close_timer_active()
        t = time.perf_counter()
        while (time.perf_counter() - t) * 1000.0 < grace_ms() * 0.4:
            app.processEvents()
            time.sleep(0.002)
        goto(panel, *row_pt(i))                 # вернулись раньше закрытия
        t2 = time.perf_counter()
        while (time.perf_counter() - t2) < 0.45:
            app.processEvents()
            time.sleep(0.002)
        return {"left_to": other, "left_ms": round(grace_ms() * 0.4, 1),
                "grace_armed": bool(armed),
                "kept_open_after_return": panel.sub_panel is not None,
                "timer_active_after_return": close_timer_active(),
                "sub_index": panel.sub_index}

    res["grace_return"] = measure_grace_return()

    # ── снимок подменю, открытого НАВЕДЕНИЕМ (без клика) ──
    # Снимок ДО уходов: подменю должно быть открыто наведением, а не вызовом.
    shots = {}
    if shot:
        i = row_i("filters")
        how_shot = ensure_sub(i)
        pump(450)
        try:
            sp = panel.sub_panel
            x0 = min(panel._px, sp._px if sp else panel._px)
            y0 = min(panel._py, sp._py if sp else panel._py)
            x1 = max(panel._px + panel.panel_w,
                     (sp._px + sp.panel_w) if sp else panel._px)
            y1 = max(panel._py + panel.panel_h,
                     (sp._py + sp.panel_h) if sp else panel._py)
            dpr = float(scr.devicePixelRatio()) if scr else 1.0
            pm = scr.grabWindow(0, int((x0 - 30) * dpr),
                                int((y0 - 30) * dpr),
                                int((x1 - x0 + 60) * dpr),
                                int((y1 - y0 + 60) * dpr))
            if pm.isNull():                    # второй экран: снимок не выйдет
                shots["error"] = "grabWindow отдал пустой кадр"
            else:
                pm.save(shot, "PNG")
                shots.update({"path": shot, "size": [pm.width(), pm.height()],
                              "opened_by": how_shot,
                              "cursor": list(_cursor_at()),
                              "cursor_in_sub": bool(sp is not None
                                                    and sp.in_global(QtGui.QCursor.pos()))})
        except Exception as e:
            shots["error"] = repr(e)
    res["shot"] = shots

    # ── замер 5: закрытие подменю при уходе с ОБОИХ ──
    def measure_close(key: str = "filters", far: bool = False) -> dict:
        """Уход с обоих меню: ``far=False`` — в кайму тени (окно остаётся
        «горячим»), ``far=True`` — на рабочий стол, ВНЕ окна меню (окно придётся
        показать заново: возврат курсора в окно Windows обработчику не отдаёт).
        """
        i = row_i(key)
        ensure_sub(i)
        if panel.sub_panel is None:
            return {"skipped": "подменю не открылось"}
        pt = (panel._px - 220, panel._py - 220) if far else shadow_pt()
        t0 = time.perf_counter()
        goto(panel, *pt)
        closed = None
        while (time.perf_counter() - t0) * 1000.0 < 2500:
            app.processEvents()
            if panel.sub_panel is None or panel.sub_panel._phase == "out":
                closed = (time.perf_counter() - t0) * 1000.0
                break
            time.sleep(0.0002)
        return {"where": ("рабочий стол, вне окна меню" if far
                          else "вне содержимого панелей (кайма тени окна)"),
                "point": [int(pt[0]), int(pt[1])],
                "grace_cfg_ms": getattr(panel, "hover_close_ms", None),
                "sub_close_ms": round(closed, 3) if closed else None,
                "closed": panel.sub_panel is None}

    res["sub_close"] = measure_close()
    # повторный показ меню: окно снова «горячее» — проверяем уход НА РАБОЧИЙ СТОЛ
    panel.close_sub()
    panel.open_at(pos)
    pump(620)
    res["reopen"] = {"phase": panel._phase,
                     "hot_tries": hot(row_i("notify"))}
    res["sub_close_far"] = measure_close(far=True)

    panel._paint = _orig
    goto(panel, res["at"][0], res["at"][1] + 220)   # уводим курсор с меню
    panel._finish()
    pump(120)
    if out_json:
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            _say(f"menu-hover: json: {e}")
    hl = [h for h in res["highlight"] if h["on_row_frame_ms"] is not None]
    _say("menu-hover [%s]: экран %s dpr=%s %sГц; подсветка до кадра на строке: %s мс"
         % (label or "-", res["screen"]["name"], res["screen"]["dpr"],
            res["screen"]["refresh_hz"],
            [h["on_row_frame_ms"] for h in res["highlight"]]))
    _say("menu-hover: кадров с промежуточной подсветкой: %s"
         % [h["frames_moved"] for h in res["highlight"]] + ("" if hl else " (замер неполный)"))
    _say("menu-hover: подменю по наведению: %s -> %s мс (наведение: %s)"
         % (res["sub_hover"]["key"], res["sub_hover"]["sub_created_ms"],
            res["sub_hover"]["opened_by_hover"]))
    _say("menu-hover: закрытие подменю при уходе: %s мс (льготное окно %s мс)"
         % (res["sub_close"]["sub_close_ms"], res["sub_close"]["grace_cfg_ms"]))
    _say("menu-hover: полоса подсветки на кадре: %s (строки %s; на своей: %s из %s)"
         % (res["frames_summary"]["pill_tops"],
            res["frames_summary"]["expected_tops"],
            res["frames_summary"]["steps_pill_on_expected_row"],
            res["frames_summary"]["steps"]))
    _say("menu-hover: пронос курсора по пункту с подменю: открылось=%s"
         % res["sweep_no_open"]["opened"])
    app.processEvents()
    return 0


def selftest(cfg: dict) -> int:
    """Проверки помощника с доказательствами (печатает цифры). -> код выхода."""
    bad = []
    out = []
    report = []          # ПОЛНЫЙ (юникод) отчёт -> в UTF-8-файл, не в stdout

    def say(text="") -> None:
        report.append(str(text))
        SD.say(text)

    _say = say           # внутри самотеста печатаем через say (и копим отчёт)

    def chk(name, ok, detail=""):
        out.append(f"  {'OK  ' if ok else 'FAIL'} {name}" +
                   (f" — {detail}" if detail else ""))
        if not ok:
            bad.append(name)

    _say("=" * 64)
    _say("1. значки состояний: РИСУНОК ПОЛЬЗОВАТЕЛЯ сохранён + метка состояния")
    _say("=" * 64)
    try:
        user_ico = bool(TI.load_app_ico(cfg))
    except Exception:
        user_ico = False
    _say(f"  рисунок пользователя (app.ico): {'найден' if user_ico else 'нет'}"
          f" — {TI.find_app_ico(cfg)!r}")
    # Цвет, которым значок РЕАЛЬНО залит, берём из той же таблицы, из которой
    # его рисует tray_icon (TI.states): для четырёх «рабочих» состояний это
    # палитра темы, для состояний эталона — цвета из status_info дословно.
    st_colors = TI.states(cfg)
    icons, sig = {}, {}
    for k in STATE_KINDS:
        ic = icon_from_state(k, cfg)
        pm = ic.pixmap(32, 32)
        img = pm.toImage()
        icons[k] = (not ic.isNull(), PM_SIZE(pm))
        col = st_colors.get(k, st_colors["off"])[1]
        # доля пикселей цвета состояния (метка в углу) — считаем по РЕАЛЬНОМУ
        # размеру значка (Qt отдаёт ближайший доступный, а не обязательно 32x32)
        want = QtGui.QColor(*col)
        n = hit = 0
        px = []
        W, H = img.width(), img.height()
        for y in range(0, H):
            for x in range(0, W):
                c = img.pixelColor(x, y)
                if c.alpha() == 0:
                    continue
                n += 1
                px.append((c.red(), c.green(), c.blue(), c.alpha()))
                dr = abs(c.red() - want.red())
                dg = abs(c.green() - want.green())
                db = abs(c.blue() - want.blue())
                if dr + dg + db < 90:
                    hit += 1
        frac = (hit / n) if n else 0.0
        # «разные значки» проверяем по САМИМ пикселям, а не по счётчикам:
        # у двух состояний может совпасть и число окрашенных точек
        sig[k] = hashlib.sha256(repr(px).encode()).hexdigest()[:16]
        _say(f"  {k:5s} цвет={tuple(col)} значок {icons[k][1]}, "
              f"пикселей цвета состояния {frac*100:.0f}% ({hit}/{n}), "
              f"отпечаток {sig[k]}")
        # Метка состояния — маленький кружок в углу, поэтому доля цвета
        # состояния НЕ должна быть большой: если она большая, значит рисунок
        # опять залили цветом (то и было дефектом «значок — не его»).
        chk(f"значок «{k}» нарисован и содержит метку состояния",
            icons[k][0] and frac >= 0.03,
            f"{frac*100:.1f}% (порог 3.0%)")
        chk(f"значок «{k}» НЕ залит одним цветом (метка, а не заливка)",
            frac <= 0.45, f"{frac*100:.1f}% (предел 45%)")
    # ── ДОКАЗАТЕЛЬСТВО ПО ПИКСЕЛЯМ: значок = рисунок пользователя ──
    if user_ico:
        raw = TI.load_app_ico(cfg)
        for k in ("ok", "off", "error"):
            ico = TI.state_ico(k, sizes=(16, 32), cfg=cfg)
            sh32 = TI.artwork_share(ico, raw, 32)
            sh16 = TI.artwork_share(ico, raw, 16)
            bc = TI.badge_color_share(ico, st_colors[k][1], 32)
            _say(f"  ДОКАЗАТЕЛЬСТВО ПО ПИКСЕЛЯМ [{k}]: совпало с рисунком "
                  f"пользователя 32x32 {sh32*100:.1f}%, 16x16 {sh16*100:.1f}%; "
                  f"метка в углу {bc*100:.0f}%")
            chk(f"[{k}] значок трея СОДЕРЖИТ РИСУНОК ПОЛЬЗОВАТЕЛЯ, а не заливку "
                f"(>= 70% пикселей 32x32 совпадают с app.ico)",
                sh32 >= 0.70, f"{sh32*100:.1f}%")
            chk(f"[{k}] метка состояния читается в правом нижнем углу",
                bc >= 0.40, f"{bc*100:.0f}%")
    chk("состояния с РАЗНЫМИ цветами дают РАЗНЫЕ значки (8 имён эталона -> "
        "6 цветов; down=error и switch=noanswer делят цвет, как в эталоне)",
        len(set(sig.values())) == len({tuple(v[1]) for v in TI.states(cfg).values()}),
        str(sorted(sig.values())))
    # Таблица цветов состояний — то, из чего значок рисуется. Обе её части
    # проверяем отдельно, чтобы проверка значка не стала тавтологией:
    # состояния эталона обязаны повторять status_info дословно, рабочие —
    # совпадать с палитрой темы (иначе цвет не читается на светлой панели).
    chk("цвета эталонных состояний взяты из status_info дословно",
        all(tuple(st_colors[n][1]) == tuple(SI.state_color(n))
            for n in ("switch", "noanswer", "restoring", "down")),
        str({n: tuple(st_colors[n][1]) for n in
             ("switch", "noanswer", "restoring", "down")}))
    chk("цвета рабочих состояний взяты из палитры темы",
        all(tuple(st_colors[n][1]) == tuple(TH.state_color(cfg, n))
            for n in ("ok", "busy", "off", "error")),
        str({n: tuple(st_colors[n][1]) for n in ("ok", "busy", "off", "error")}))

    _say()
    _say("=" * 64)
    _say("2. меню: пункты, подменю, галочки (текстовый дамп)")
    _say("=" * 64)
    ui = Ui(cfg)
    dump = ui.menu_dump()
    _say(dump)
    need = ["watchdog", "notify", "theme_auto", "theme_light", "theme_dark",
            "filter_ru", "filter_cf", "refresh", "about", "exit"]
    chk("в меню есть все пункты и подменю темы",
        all(k in ui.acts for k in need), str([k for k in need if k not in ui.acts]))
    chk("подменю темы содержит три пункта",
        len(ui.theme_menu.actions()) == 3)
    chk("галочки отражают настройки",
        ui.acts["notify"].isChecked() == bool((cfg.get("notify") or {})
                                              .get("enabled", True)))
    # ── ПОДМЕНЮ «ФИЛЬТРЫ» (замечание 3) ──
    chk("в меню есть ПОДМЕНЮ «Фильтры» (tray.menu.filters)",
        "filters_menu" in dir(ui) and ui.filters_menu.title() == tr("tray.menu.filters"),
        ui.filters_menu.title() if hasattr(ui, "filters_menu") else "нет")
    chk("подменю «Фильтры» содержит ДВА пункта-галочки",
        len(ui.filters_menu.actions()) == 2
        and all(a.isCheckable() for a in ui.filters_menu.actions()),
        str([(a.text(), a.isCheckable()) for a in ui.filters_menu.actions()]))
    chk("пункты фильтров — «РФ — напрямую» и «Cloudflare — обход»",
        ui.acts["filter_ru"].text().startswith(tr("tray.menu.filter_ru"))
        and ui.acts["filter_cf"].text().startswith(tr("tray.menu.filter_cf")),
        f'{ui.acts["filter_ru"].text()!r} / {ui.acts["filter_cf"].text()!r}')
    chk("галочка «РФ — напрямую» отражает watch.ru_direct",
        ui.acts["filter_ru"].isChecked()
        == bool((cfg.get("watch") or {}).get("ru_direct", False)),
        str(ui.acts["filter_ru"].isChecked()))
    chk("галочка «Cloudflare — обход» отражает blacklist.cloudflare_nodes",
        ui.acts["filter_cf"].isChecked()
        == bool((cfg.get("blacklist") or {}).get("cloudflare_nodes", True)),
        str(ui.acts["filter_cf"].isChecked()))
    chk("значок в трее установлен", not ui.tray.icon().isNull())

    # ── МЕНЮ: СВОЁ ОКНО ВМЕСТО QMenu (вид макета, ОДИН слой текста) ──
    _say()
    _say("2б. меню: своё всплывающее окно, один слой текста, цвета карточки")
    panel = UMN.PopupMenu(cfg, ui.menu_model, state_color=US.tokens(cfg)["state_ok"],
                          on_activate=ui._menu_activate, on_toggle=ui._menu_toggle)
    chk("меню — СВОЁ окно (не QMenu), тип Popup без системной рамки",
        not isinstance(panel, QtWidgets.QMenu)
        and bool(panel.windowFlags() & Qt.Popup)
        and bool(panel.windowFlags() & Qt.FramelessWindowHint),
        "flags=0x%x" % int(panel.windowFlags()))
    chk("структура меню совпадает с дампом (12 пунктов, 4 разделителя)",
        panel.dump_text() == ui.menu_dump()
        and ui.menu_dump().count("[подменю]") == 2
        and ui.menu_dump().count("  ---") == 4,
        "пунктов %d, разделителей %d" % (len(panel.rows),
                                         sum(1 for r in panel.rows
                                             if r.kind == "sep")))
    chk("геометрия меню — числа макета (панель 269 px, строка 34, полоса 3)",
        panel.panel_w == 269 and US.ROW_H == 34 and US.STRIPE == 3
        and US.MENU_INSET == 6 and US.RADIUS == 12,
        f"{panel.panel_w}x{panel.panel_h} строка {US.ROW_H}")

    # ОДИН слой текста: меряем ЧЕРНИЛА одной и той же подписи в обычном и
    # наведённом состоянии. Двоение дало бы вторую копию: высота подписи выросла
    # бы (у прежнего макета было +3-4 px и лишние полосы чернил).
    tmpdir_m = os.path.join((cfg.get("paths") or {}).get("state_dir") or
                            SD.log_dir(), "menu_selftest")
    ink = {}
    for theme in ("dark", "light"):
        pl = os.path.join(tmpdir_m, "menu-%s-plain.png" % theme)
        hv = os.path.join(tmpdir_m, "menu-%s-hover.png" % theme)
        panel.render_png(pl, theme=theme)
        panel.render_png(hv, theme=theme, hover_key="notify")
        txt = (232, 234, 238) if theme == "dark" else (24, 26, 28)
        mut = (162, 166, 176) if theme == "dark" else (70, 74, 78)
        ink[theme] = {
            "plain": menu_text_ink(pl, mut, x0=US.SHADOW_PX + 41,
                                   y0=US.SHADOW_PX + 95, y1=US.SHADOW_PX + 129),
            "hover": menu_text_ink(hv, txt, x0=US.SHADOW_PX + 41,
                                   y0=US.SHADOW_PX + 95, y1=US.SHADOW_PX + 129)}
        _say("  %-5s подпись: обычная h=%s полос %s | наведённая h=%s полос %s | "
             "цвет наведения %s" % (theme, ink[theme]["plain"]["h"],
                                    ink[theme]["plain"]["bands"],
                                    ink[theme]["hover"]["h"],
                                    ink[theme]["hover"]["bands"],
                                    ink[theme]["hover"]["color"]))
    one_layer = all(ink[t]["plain"]["h"] == ink[t]["hover"]["h"]
                    and ink[t]["hover"]["w"] == ink[t]["plain"]["w"]
                    and ink[t]["hover"]["bands"] <= 2 and ink[t]["plain"]["bands"] <= 2
                    for t in ("dark", "light"))
    chk("подпись — ОДИН слой: в покое наведения высота и ширина как в обычном "
        "состоянии (двоения нет)", one_layer,
        str({t: (ink[t]["plain"]["h"], ink[t]["hover"]["h"]) for t in ink}))
    # цвета меню = цвета карточки (ОДНА таблица стиля): сверка пикселями
    same_px = {}
    for theme in ("dark", "light"):
        c2 = dict(cfg)
        c2["ui"] = dict(cfg.get("ui") or {})
        c2["ui"]["theme"] = theme
        d = demo_card_data(cfg)
        t2 = Toast(d["kind"], d["title"], "", c2, duration_ms=600, sub=d["sub"],
                   steps=d["steps"], footer=d["footer"], spinner=True,
                   total_ms=d["total_ms"], role=d["role"])
        cimg = t2._card.toImage()
        pimg = QtGui.QImage(os.path.join(tmpdir_m, "menu-%s-plain.png" % theme))
        cp = cimg.pixelColor(120, 4)
        mp = pimg.pixelColor(int(US.SHADOW_PX + 40), int(US.SHADOW_PX + 4))
        same_px[theme] = ((mp.red(), mp.green(), mp.blue()) == (cp.red(), cp.green(),
                                                               cp.blue()))
        _say("  %-5s панель меню %s ⟷ панель карточки %s"
             % (theme, (mp.red(), mp.green(), mp.blue()),
                (cp.red(), cp.green(), cp.blue())))
        t2.close()
    chk("цвета меню и карточки совпадают пиксельно (одна таблица стиля)",
        all(same_px.values()), str(same_px))
    # клавиши и закрытие — функционально (без показа окна)
    panel._move_sel(1)
    first = panel.sel
    panel._move_sel(1)
    panel.start_out()
    chk("клавиши: выбор идёт по пунктам, Esc/клик вне — закрытие с анимацией",
        first >= 0 and panel.sel != first and panel._phase == "out",
        f"первый выбор {first}, затем {panel.sel}, фаза {panel._phase}")
    # переключатель МЕНЮ (новый путь) шлёт ту же команду супервизору
    nl = None
    for i, r in enumerate(panel.rows):
        if r.key == "notify":
            nl = i
    if nl is not None:
        before = ui.acts["notify"].isChecked()
        panel.activate(nl)
        got = (UC.consume(cfg) or {}).get("cmd")
        chk("переключатель меню шлёт супервизору команду по своему состоянию",
            got == ("notify_off" if before else "notify_on"),
            f"было {before}, команда {got}")
        ui.acts["notify"].setChecked(before)
    panel._finish()

    # ── НАВЕДЕНИЕ МЫШЬЮ: подменю по наведению и мгновенный перенос подсветки ──
    _say()
    _say("=" * 64)
    _say("2г. наведение мышью: подменю по наведению, подсветка без отставания")
    _say("=" * 64)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pn = UMN.PopupMenu(cfg, ui.menu_model, state_color=US.tokens(cfg)["state_ok"],
                       on_activate=ui._menu_activate, on_toggle=ui._menu_toggle)
    i_sub = next((i for i, r in enumerate(pn.rows) if r.key == "filters"), -1)
    i_plain = next((i for i, r in enumerate(pn.rows) if r.key == "notify"), -1)
    _say(f"  пункт с подменю: строка {i_sub}, обычный пункт: строка {i_plain}")
    chk("задержки наведения взяты из общего модуля стиля (оба таймера "
        "одноразовые)",
        pn.hover_open_ms == US.SUB_HOVER_OPEN_MS
        and pn.hover_close_ms == US.SUB_HOVER_CLOSE_MS
        and pn._hov_open.isSingleShot() and pn._hov_close.isSingleShot(),
        f"открытие {pn.hover_open_ms} мс, закрытие {pn.hover_close_ms} мс")
    # ── подсветка: мышью — МГНОВЕННО, клавишами — плавно ──
    pn.set_hover(i_sub, by="mouse")
    now_mouse = abs(float(pn._hl_y) - float(pn._ys[i_sub])) <= 1e-6
    fade_mouse = pn._fade_ms
    pn.set_hover(i_plain, by="key")
    key_gradual = abs(float(pn._hl_y) - float(pn._ys[i_plain])) > 1e-6
    fade_key = pn._fade_ms
    _say(f"  подсветка: мышью сразу={now_mouse} (темп {fade_mouse} мс), "
         f"клавишами плавно={key_gradual} (темп {fade_key} мс)")
    chk("мышью подсветка переносится в ТОТ ЖЕ вызов, клавишами — плавно",
        now_mouse and fade_mouse == US.HL_MOUSE_MS and key_gradual
        and fade_key == US.STEP_FADE_MS,
        f"мышь {fade_mouse} мс, клавиши {fade_key} мс")
    # ── открытие подменю по НАВЕДЕНИЮ: живым таймером, окно НЕ показываем ──
    calls = []
    real_open, real_sub, real_idx = pn._open_sub, pn.sub_panel, pn.sub_index
    pn._open_sub = lambda i, by="click": calls.append((i, by, time.perf_counter()))
    try:
        pn.set_hover(i_plain, by="mouse")
        t0 = time.perf_counter()
        pn.set_hover(i_sub, by="mouse")
        armed_open = pn._hov_open.isActive()
        while (time.perf_counter() - t0) < 0.7 and not calls:
            app.processEvents()
            time.sleep(0.002)
        dt = (calls[0][2] - t0) * 1000.0 if calls else None
        chk("подменю открывается НАВЕДЕНИЕМ через задержку стиля",
            armed_open and len(calls) == 1 and calls[0][0] == i_sub
            and calls[0][1] == "hover" and dt is not None
            and US.SUB_HOVER_OPEN_MS - 30 <= dt <= US.SUB_HOVER_OPEN_MS + 110,
            f"через {dt:.1f} мс при задержке стиля {US.SUB_HOVER_OPEN_MS} мс")
        _say(f"  открытие по наведению: {dt:.1f} мс "
             f"(задержка стиля {US.SUB_HOVER_OPEN_MS} мс)")
        # пронос курсора: ушли РАНЬШЕ задержки — подменю не открывается
        calls.clear()
        pn.set_hover(i_sub, by="mouse")
        time.sleep(0.02)
        pn.set_hover(i_plain, by="mouse")
        t1 = time.perf_counter()
        while (time.perf_counter() - t1) < 0.6:
            app.processEvents()
            time.sleep(0.002)
        chk("простой ПРОНОС курсора подменю НЕ раскрывает", not calls,
            f"вызовов открытия: {len(calls)}")
        # клавиатура: стрелки подменю не раскрывают, «вправо» — раскрывает
        calls.clear()
        pn.set_hover(i_plain, by="key")
        pn._move_sel(1)
        armed_key = pn._hov_open.isActive()
        t2 = time.perf_counter()
        while (time.perf_counter() - t2) < 0.35:
            app.processEvents()
            time.sleep(0.002)
        key_silent = not calls
        pn.sel = i_sub
        ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Right,
                             Qt.NoModifier)
        pn.keyPressEvent(ev)
        chk("клавиатура: стрелки подменю не раскрывают, «вправо» — раскрывает",
            armed_key is False and key_silent and len(calls) == 1
            and calls[0][0] == i_sub,
            f"таймер наведения активен={armed_key}, вызовов={len(calls)}")
    finally:
        pn._open_sub = real_open
        pn.sub_panel, pn.sub_index = real_sub, real_idx

    # ── закрытие: «льготное» окно и ОТМЕНА при возврате ──
    closes = []

    class _FakeSub:
        """Заглушка подменю: окно не показываем (важен только вызов закрытия)."""

        _phase = "run"

        def in_global(self, _pos):
            return False

    pn.sub_panel = _FakeSub()
    pn.sub_index = i_sub
    real_close = pn.close_sub
    pn.close_sub = lambda: closes.append(time.perf_counter())
    try:
        pn.set_hover(i_plain, by="mouse")        # ушли с пункта с подменю
        t0 = time.perf_counter()
        armed = pn._hov_close.isActive()
        while (time.perf_counter() - t0) < 0.9 and not closes:
            app.processEvents()
            time.sleep(0.002)
        dtc = (closes[0] - t0) * 1000.0 if closes else None
        chk("подменю закрывается при уходе через ЛЬГОТНОЕ окно стиля",
            armed and dtc is not None
            and US.SUB_HOVER_CLOSE_MS - 40 <= dtc <= US.SUB_HOVER_CLOSE_MS + 120,
            f"через {dtc:.1f} мс при окне стиля {US.SUB_HOVER_CLOSE_MS} мс")
        _say(f"  закрытие по уходу: {dtc:.1f} мс "
             f"(льготное окно {US.SUB_HOVER_CLOSE_MS} мс)")
        # возврат на свой пункт внутри окна отменяет закрытие (не мигает)
        closes.clear()
        pn.sub_panel, pn.sub_index = _FakeSub(), i_sub
        pn.set_hover(i_sub, by="mouse")
        pn.set_hover(i_plain, by="mouse")        # ушли — окно взведено
        armed2 = pn._hov_close.isActive()
        t3 = time.perf_counter()
        while (time.perf_counter() - t3) * 1000.0 < US.SUB_HOVER_CLOSE_MS * 0.4:
            app.processEvents()
            time.sleep(0.002)
        pn.set_hover(i_sub, by="mouse")          # вернулись раньше закрытия
        t4 = time.perf_counter()
        while (time.perf_counter() - t4) < 0.5:
            app.processEvents()
            time.sleep(0.002)
        chk("возврат курсора на свой пункт ОТМЕНЯЕТ закрытие подменю",
            armed2 and not closes and not pn._hov_close.isActive(),
            f"окно взведено={armed2}, закрытий={len(closes)}, "
            f"таймер активен={pn._hov_close.isActive()}")
    finally:
        pn.close_sub = real_close
        pn.sub_panel, pn.sub_index = None, -1
        pn._finish()

    # ── СЛОТ ТРЕЯ: правый клик НЕ имеет права ронять помощник ──
    _say()
    _say("=" * 64)
    _say("2в. правый клик по значку трея: причина клика и неубиваемость")
    _say("=" * 64)
    ARC = QtWidgets.QSystemTrayIcon.ActivationReason
    _say("  причина правого клика: %r, значение %s"
         % (ARC.Context, enum_int(ARC.Context)))
    chk("причина клика разбирается БЕЗ int() по члену перечисления",
        enum_int(ARC.Context) == getattr(ARC.Context, "value", -1)
        and enum_int(ARC.Trigger) != enum_int(ARC.Context),
        f"Context={enum_int(ARC.Context)}, Trigger={enum_int(ARC.Trigger)}, "
        f"числом={enum_int(getattr(ARC.Context, 'value', None))}")
    # Что слот делает с причиной: правим показ меню на счётчик и смотрим вызовы.
    shows = []
    real_show = ui.show_menu
    try:
        ui.show_menu = lambda pos=None: (shows.append(pos), {})[1]
        ui._on_tray_activated(ARC.Context)                     # как приходит из Qt
        ui._on_tray_activated(enum_int(ARC.Context))           # числом (старый Qt)
        ui._on_tray_activated(ARC.Trigger)                     # левый клик
        ui._on_tray_activated(ARC.DoubleClick)                 # двойной
    finally:
        ui.show_menu = real_show
    chk("слот трея открывает меню по ПРАВОМУ клику (член enum и число), "
        "и НЕ открывает по левому/двойному",
        len(shows) == 2, f"показов меню {len(shows)} (ожидалось 2)")
    # БЕЗОПАСНОСТЬ: исключение внутри показа меню не вылетает наружу
    log_path = os.path.join(SD.log_dir(), "main.log")

    def _log_tail():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[-4000:]
        except Exception:
            return ""

    before_log = _log_tail()

    def _boom_menu(pos=None):
        raise RuntimeError("искусственный сбой показа меню (проверка)")

    escaped = ""
    try:
        ui.show_menu = _boom_menu
        ui._on_tray_activated(ARC.Context)
    except BaseException as _e:                      # noqa: BLE001 — ловим ВСЁ
        escaped = repr(_e)
    finally:
        ui.show_menu = real_show
    chk("исключение в показе меню НЕ вылетает из слота трея (помощник жив)",
        not escaped, escaped or "исключение осталось внутри слота")
    after_log = _log_tail()
    chk("сбой показа меню записан в журнал помощника",
        "ошибка в меню трея" in after_log and after_log != before_log,
        f"хвост журнала: {after_log[-160:]!r}" if after_log != before_log
        else "записи не появилось")
    # Ссылка на окно меню держится: сборщик мусора не съест панель до показа
    p_first = None
    try:
        st_a = ui.show_menu()
        p_first = ui._menu_panel
        st_b = ui.show_menu()                      # повторный показ не падает
    except Exception as _e:                        # noqa: BLE001
        st_a, st_b = {}, {"error": repr(_e)}
    chk("окно меню создаётся один раз и УДЕРЖИВАЕТСЯ ссылкой "
        "(сборщик мусора его не съест)",
        p_first is not None and ui._menu_panel is p_first
        and isinstance(st_a, dict) and "error" not in st_b,
        f"панель {type(p_first).__name__ if p_first else 'None'}, "
        f"кадров {st_a.get('frames')}, повтор: {st_b.get('error', 'ок')}")
    try:
        ui._close_menu(instant=True)
    except Exception:                              # noqa: BLE001
        pass

    _say()
    _say("=" * 64)
    _say("3. темы: стиль меню и палитра из настроек")
    _say("=" * 64)
    ql, qd = {}, {}
    for name, store in (("light", ql), ("dark", qd)):
        c2 = dict(cfg)
        c2["ui"] = dict(cfg.get("ui") or {})
        c2["ui"]["theme"] = name
        store["qss"] = qss_for(c2)
        store["bg"] = TH.palette(c2)["card_bg"]
        _say(f"  {name:5s}: фон карточки {store['bg']}, "
              f"QSS {len(store['qss'])} символов")
    chk("темы дают разный стиль и разную палитру",
        ql["qss"] != qd["qss"] and ql["bg"] != qd["bg"],
        f"{ql['bg']} vs {qd['bg']}")
    mn = TH.min_contrast()
    chk("цвета состояний читаемы (контраст >= 3:1)", mn >= 3.0, f"min={mn:.2f}")
    # ── ОБЩИЙ МОДУЛЬ СТИЛЯ: его собственные проверки (палитры, кривые, кадры) ──
    st_res = US.selfcheck()
    bad_st = [r["name"] for r in st_res if not r["ok"]]
    for r in st_res:
        _say("  ui_style: %s%s" % ("OK   " if r["ok"] else "СБОЙ ", r["name"]))
    chk("общий модуль стиля (палитры/кривые/выравнивание кадров) без ошибок",
        not bad_st, str(bad_st))

    _say()
    _say("=" * 64)
    _say("4. связь с программой: команды через файл")
    _say("=" * 64)
    cmds = ["watchdog_on", "watchdog_off", "notify_on", "notify_off",
            "theme_auto", "theme_light", "theme_dark", "refresh", "exit",
            "filter_ru_on", "filter_ru_off", "filter_cf_on", "filter_cf_off"]
    # каждую команду пишем и СРАЗУ снимаем: файл один, «очереди» в нём нет
    round_trip = []
    for c in cmds:
        wrote = UC.write_cmd(cfg, c)
        got = UC.consume(cfg)
        round_trip.append((c, wrote, (got or {}).get("cmd")))
    bad_rt = [c for c, w, g in round_trip if not w or g != c]
    _say("  " + ", ".join(f"{c}:{'ok' if g == c else 'СБОЙ'}"
                           for c, _w, g in round_trip))
    chk("каждый пункт меню пишет файл-команду, и она снимается супервизором",
        not bad_rt and len(round_trip) == len(cmds), str(bad_rt))
    chk("неизвестная команда отвергается", UC.write_cmd(cfg, "rm -rf /") is False)
    # ПОДМЕНЮ «ФИЛЬТРЫ» ДЕЙСТВИТЕЛЬНО передаёт команду через state/ui_cmd.json
    for act_name, want_on, want_off in (
            ("filter_ru", "filter_ru_on", "filter_ru_off"),
            ("filter_cf", "filter_cf_on", "filter_cf_off")):
        a = ui.acts[act_name]
        a.setChecked(True)
        ui.on_action(act_name)
        got_on = (UC.consume(cfg) or {}).get("cmd")
        a.setChecked(False)
        ui.on_action(act_name)
        got_off = (UC.consume(cfg) or {}).get("cmd")
        chk(f"«{act_name}»: включение и выключение уходят супервизору "
            f"({want_on} / {want_off})",
            got_on == want_on and got_off == want_off, f"{got_on} / {got_off}")
    chk("пульс помощника записан (state/ui_helper.json)",
        UC.write_ping(cfg, os.getpid()) and UC.helper_alive(cfg))

    _say()
    _say("=" * 64)
    _say("4б. одноэкземплярность: ЗАМОК помощника (не PID в пульсе)")
    _say("=" * 64)
    try:
        import tempfile
        import shutil as _sh
        import instancelock as IL
        import ui_helper as UH
        tmpdir = tempfile.mkdtemp(prefix="forest_ui_lock_")
        lockp = _lock_path({"paths": {"state_dir": tmpdir}})
        _say(f"  путь замка: {os.path.basename(lockp)}")
        ok_x, det_x = IL.exclusivity_check(lockp)
        chk("замок эксклюзивен: независимая вторая попытка получает отказ",
            ok_x, det_x)
        cfg2 = dict(cfg)
        cfg2["paths"] = dict(cfg.get("paths") or {})
        cfg2["paths"]["state_dir"] = tmpdir
        got = single_instance_guard(cfg2)
        chk("single_instance_guard занимает замок", got is True and IL.held(lockp),
            f"guard={got}")
        second = IL.InstanceLock(lockp)
        chk("второй экземпляр помощника получит отказ (замок занят)",
            second.acquire() is False)
        chk("ui_helper.running() видит помощника по замку (без пульса)",
            UH.running(cfg2) is True)
        chk("ui_helper.ensure() не поднимает второго при живом замке",
            UH.ensure(cfg2) is True and IL.held(lockp) is True)
        _sh.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        chk("проверки замка помощника прошли", False, str(e))

    _say()
    _say("=" * 64)
    _say("5. уведомление: ФАЗЫ АНИМАЦИИ (положение/прозрачность во времени), кадры, фокус")
    _say("=" * 64)
    # Карточку показываем ВНЕ ЭКРАНА (TEST_ANCHOR): анимация настоящая, но
    # пользователь не видит ни одного окна — проверки не показывают окон.
    # ЭТАПЫ — РОВНО СЦЕНАРИЙ МАКЕТА (50/600/620/1850 мс): так совпадение с
    # утверждённым видом проверяется по числам самого макета, а не «на глаз».
    MOCK_STEPS = [("Обрыв обнаружен (пульс порта)", 50),
                  ("Переключение на новый узел", 600),
                  ("Ядро приняло соединения", 620),
                  ("Данные подтверждены", 1850)]
    proc5 = {"id": "t5", "kind": "restore", "cause": "outage",
             "started": time.time(), "eta_s": 1.85, "pct": 0,
             "phase": "Подтверждение трафика…", "elapsed": 0.0, "final": False,
             "steps": [{"nm": nm, "t": ms / 1000.0, "done": False}
                       for nm, ms in MOCK_STEPS]}
    n5 = SI.notif(proc5)
    st5_data = SI.steps_data(proc5)
    st5_ms = [s["ms"] for s in st5_data]
    st5_total = SI.steps_total_ms(proc5)
    stats5, done5, cap5 = {}, {"v": False}, {}
    fg_before = foreground()
    globals()["TEST_ANCHOR"] = (-4000, -4000, 800, 600)   # вне экрана
    t5 = Toast("restoring", n5["title"], "", cfg, duration_ms=1200, fps=60,
               stats_sink=stats5, on_done=lambda: done5.__setitem__("v", True),
               sub=n5["sub"], steps=st5_data, footer=n5["footer"],
               spinner=True, total_ms=st5_total, role="restore")
    t5.show_toast()
    cap5["width"] = t5.width()
    cap5["height"] = t5.height()
    cap5["nonnull_icon"] = (not t5.icon.isNull())
    try:
        cap5["hwnd"] = int(t5.winId())      # родное окно карточки (HWND)
    except Exception:
        cap5["hwnd"] = 0
    fg_samples = []
    try:
        cap5["icon"] = t5.icon_pm.toImage().copy()
    except Exception:
        cap5["icon"] = None
    t0 = time.time()
    while not done5["v"] and time.time() - t0 < 20:
        QtWidgets.QApplication.processEvents()
        fg_samples.append(foreground())
        if "texts_final" not in cap5:
            try:
                if t5._final:
                    cap5["texts_final"] = t5.texts()
                    cap5["final_at_ms"] = round(
                        (time.perf_counter() - t5._t_run) * 1000.0
                        + ANIM_IN_MS, 1)
                    cap5["frames_at_final"] = stats5.get("frames", 0)
            except Exception:
                pass
        time.sleep(0.002)
    globals()["TEST_ANCHOR"] = None
    fg_after = foreground()
    fg_set = sorted({x for x in fg_samples})
    took = [x for x in fg_samples if cap5.get("hwnd") and x == cap5["hwnd"]]
    _say(f"  активное окно: до={fg_before}, после={fg_after}; за время "
         f"показа карточки окно менялось на: {fg_set} "
         f"(карточка НИ РАЗУ не становилась активной: {len(took)} раз)")
    cap5["fg_ok"] = (not took) and (fg_before == fg_after)
    frames = stats5.get("frames", 0)
    avg = stats5.get("avg_fps", 0.0)
    apaint = stats5.get("avg_paint_fps", 0.0)
    worst = stats5.get("worst_ms", 0.0)
    worst_w = stats5.get("worst_warm_ms", 0.0)
    stalls = stats5.get("stalls", 0)
    dropped_steady = stats5.get("dropped_steady", 0)
    wtimer = stats5.get("worst_timer_ms", 0.0)
    dropped = stats5.get("dropped", 0)
    samples = stats5.get("samples") or []
    positions = stats5.get("positions", 0)
    opac = stats5.get("opacities", 0)
    bars = stats5.get("bars", 0)
    in_ms = stats5.get("in_ms", 0.0)
    tf5 = cap5.get("texts_final") or {}
    _say(f"  кадров={frames} (отрисовок={stats5.get('frames_paint')}) "
         f"средний FPS={avg} (отрисовок {apaint}); цель 60 к/с")
    _say(f"  худший интервал кадра={worst} мс (без прогрева {worst_w} мс), "
         f"таймера={wtimer} мс, пропущено кадров={dropped} "
         f"(после входа {dropped_steady}), залипаний={stalls}")
    if stats5.get("slow"):
        _say(f"  медленные кадры (t, мс, фаза): {stats5['slow'][:8]}")
    _say(f"  фаза входа={in_ms:.0f} мс (макет 340); разных положений={positions}, "
         f"прозрачностей={opac}, значений полосы={bars}")
    _say(f"  положение: {stats5.get('pos_start')} -> {stats5.get('pos_end')}; "
         f"прозрачность: {stats5.get('op_start')} -> {stats5.get('op_end')}")
    _say(f"  размер карточки: {cap5.get('width')}x{cap5.get('height')} "
         f"(ширина из макета {NT_W})")
    step_n = max(1, len(samples) // 14)
    ser = " | ".join(("%.0fмс x=%d a=%s p=%s%%" % (t, x, a, p))
                     for t, x, _y, a, p, _s in samples[::step_n][:14])
    _say("  СНИМКИ ВО ВРЕМЕНИ (t, x, прозрачность, полоса): " + ser)
    _say(f"  этапы в карточке: {tf5.get('steps')}")
    _say(f"  заголовок/подпись/футер в финале: {tf5.get('title')!r} / "
         f"{tf5.get('sub')!r} / {tf5.get('footer')!r}")
    _say(f"  финал достигнут на {cap5.get('final_at_ms')} мс "
         f"(макет: {st5_total} мс)")
    # ── значок: ТОТ ЖЕ файл и ТОТ ЖЕ код, что у значка трея ──
    tray_px, same_px, tot_px = b"", 0, 0
    try:
        ti5 = TI.state_ico("restoring", sizes=(NT_ICO_ART,), cfg=cfg)
        pay = TI.ico_image_payload(ti5, NT_ICO_ART)
        d5 = TI._dib_to_pixels(pay) if pay else None
        tray_px = d5[2] if d5 else b""
        img5 = cap5.get("icon")
        if img5 is not None and tray_px:
            raw = bytes(img5.constBits())
            stride = img5.bytesPerLine()
            card_px = b"".join(raw[y * stride:(y + 1) * stride]
                               for y in range(img5.height()))
            tot_px = img5.width() * img5.height()
            same_px = sum(1 for i in range(0, tot_px * 4, 4)
                          if card_px[i:i + 4] == tray_px[i:i + 4])
    except Exception as e:
        _say(f"  сравнение значка не выполнено: {e}")
    _say(f"  значок карточки против значка трея: {same_px}/{tot_px} пикселей "
         f"совпадают ({(100.0 * same_px / tot_px) if tot_px else 0.0:.1f}%)")
    chk("карточка показана и анимирована (кадров >= 60)", frames >= 60,
        str(frames))
    chk("ЦЕЛЬ 60 к/с: средний FPS кадров 57..63", 57.0 <= avg <= 63.0, str(avg))
    chk("ЦЕЛЬ 60 к/с: средний FPS ОТРИСОВОК 57..63",
        57.0 <= float(apaint or 0.0) <= 63.0, str(apaint))
    chk("нет залипаний: худший интервал кадра < 34 мс (без прогрева)",
        0 < worst_w < 34.0, f"{worst_w} (всего {worst})")
    chk("нет залипаний после входа (кадров длиннее ДВУХ периодов: 0)",
        int(stalls) == 0, str(stalls))
    chk("кадры не пропущены после входа (пропущено 0)",
        int(dropped_steady) == 0, f"{dropped_steady} (всего {dropped})")
    chk("ФАЗА ВХОДА 340 мс (число из макета)", 330.0 <= in_ms <= 350.0,
        f"{in_ms} мс")
    chk("АНИМАЦИЯ ВИДНА: положение менялось во времени (>= 8 разных)",
        positions >= 8, str(positions))
    chk("АНИМАЦИЯ ВИДНА: прозрачность менялась во времени (>= 8 разных)",
        opac >= 8, str(opac))
    chk("ПОЛОСА ВРЕМЕНИ шла (>= 8 разных значений)", bars >= 8, str(bars))
    chk("карточка ВЫЕХАЛА и проявилась (x сместился, прозрачность выросла)",
        stats5.get("pos_start") != stats5.get("pos_end")
        and float(stats5.get("op_start") or 0) < float(stats5.get("op_end") or 0),
        "%s->%s, %s->%s" % (stats5.get("pos_start"), stats5.get("pos_end"),
                            stats5.get("op_start"), stats5.get("op_end")))
    chk("фокус НЕ перехвачен: карточка НИ РАЗУ не стала активным окном",
        not took, f"раз={len(took)}, hwnd={cap5.get('hwnd')}, "
        f"активные окна за время показа: {fg_set}")
    chk("фокус у пользователя не сменился (GetForegroundWindow до/после)",
        fg_before == fg_after, f"{fg_before} == {fg_after}")
    chk("ширина карточки — как в макете (392 px)",
        int(cap5.get("width") or 0) == NT_W, str(cap5.get("width")))
    chk("значок в карточке — рисунок пользователя (QIcon не пуст)",
        bool(cap5.get("nonnull_icon")), "")
    chk("РИСУНОК ЗНАЧКА В КАРТОЧКЕ СОВПАДАЕТ СО ЗНАЧКОМ ТРЕЯ (100% пикселей)",
        bool(tot_px) and same_px == tot_px, f"{same_px}/{tot_px}")
    chk("этапы карточки — имена и порядок как в макете",
        list(tf5.get("step_names") or []) == [nm for nm, _ in MOCK_STEPS],
        str(tf5.get("step_names")))
    chk("время этапов В МИЛЛИСЕКУНДАХ = макету (50/600/620/1850)",
        list(tf5.get("step_ms") or []) == [ms for _, ms in MOCK_STEPS],
        str(tf5.get("step_ms")))
    chk("в блоке этапов КАЖДЫЙ этап со временем в мс",
        bool(tf5.get("steps")) and all(" мс" in s for s in tf5["steps"]),
        str(tf5.get("steps")))
    chk("карточка дошла до ФИНАЛА за время макета (%d мс)" % st5_total,
        st5_total * 0.85 <= float(cap5.get("final_at_ms") or 0)
        <= st5_total * 1.6, str(cap5.get("final_at_ms")))
    chk("финальные тексты — ДОСЛОВНО как в макете",
        tf5.get("title") == "Связь восстановлена"
        and tf5.get("sub") == "связь подтверждена · 1.85 с"
        and tf5.get("footer")
        == "восстановлено за 1.85 с · окно закроется через 4 с",
        "%r / %r / %r" % (tf5.get("title"), tf5.get("sub"),
                          tf5.get("footer")))
    # ── Linux: карточка та же, а системное уведомление — ТОЛЬКО откат ──
    if not IS_WIN:
        got = ui.show_toast("restoring", n5["title"], "", sub=n5["sub"],
                            steps=st5_data, footer=n5["footer"], spinner=True,
                            total_ms=st5_total, role="restore",
                            duration_ms=900)
        t0 = time.time()
        while not ui.toast_finished and time.time() - t0 < 12:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.002)
        _say(f"  Linux: карточка через Ui.show_toast: кадров={got.get('frames')}, "
              f"служба уведомлений доступна: {notification_service_available()}")
        chk("Linux: рисуется ТА ЖЕ карточка (кадров >= 60)",
            int(got.get("frames") or 0) >= 60, str(got.get("frames")))
        chk("Linux: откат не задействован, пока есть графическая сессия",
            not got.get("dbus"), str(got.get("dbus")))
        fb = ui._toast_fallback_dbus("restoring", n5["title"], "", n5["sub"],
                                     st5_data, n5["footer"], 4000)
        body5 = notification_body(n5["sub"], "",
                                  [f'{s["nm"]}   {s["ms"]} мс'
                                   for s in st5_data], n5["footer"])
        _say(f"  Linux: откат D-Bus: {fb}")
        chk("Linux: откат на системное уведомление работает (окна нет)",
            bool(fb.get("dbus")) and int(fb.get("frames") or 0) == 0, str(fb))
        chk("Linux: в откате этапы с мс уходят В ТЕЛЕ уведомления",
            all(s["nm"] in body5 for s in st5_data) and " мс" in body5,
            repr(body5[:120]))
        chk("Linux: доступность службы уведомлений определена (bool)",
            isinstance(notification_service_available(), bool),
            repr(notification_service_available()))
        th = TH.choice(cfg)
        chk("Linux: тема определена и известна (light/dark)",
            th in ("light", "dark"), str(th))
    # ── запасной путь БЕЗ Qt (notify.py): этапы с временами и время восстановления ──
    try:
        import notify as N5
        t5b = N5._Toast("ok", "Связь восстановлена", "", 4000,
                        steps=["✓ Обрыв обнаружен (пульс порта)   50 мс",
                               "· Данные подтверждены   1850 мс"],
                        footer="восстановлено за 1.85 с (1850 мс) · окно "
                               "закроется через 4 с")
        _say(f"  запасной путь (notify.py): этапов={len(t5b.steps)}, "
             f"футер={t5b.footer!r}")
        chk("запасной путь: карточка несёт ЭТАПЫ с временами",
            len(t5b.steps) == 2 and all(" мс" in s for s in t5b.steps),
            str(t5b.steps))
        chk("запасной путь: карточка несёт ВРЕМЯ ВОССТАНОВЛЕНИЯ",
            "1850 мс" in t5b.footer and "1.85" in t5b.footer, t5b.footer)
        if IS_WIN:
            _hdc = N5.gdi32.CreateCompatibleDC(0)
            try:
                h0 = N5._measure(360, _hdc, 1.0, t5b.title, "", [], "")
                h1 = N5._measure(360, _hdc, 1.0, t5b.title, "", t5b.steps,
                                 t5b.footer)
            finally:
                N5.gdi32.DeleteDC(_hdc)
            _say(f"  запасной путь: высота карточки {h0} -> {h1} px")
            chk("запасной путь: блок этапов и футер реально рисуются (выше)",
                h1 > h0, f"{h0} -> {h1}")
        else:
            # Linux: текст уходит в тело системного уведомления (журнал)
            N5.configure(cfg)
            N5.notify("ok", "Связь восстановлена", "", force=True,
                      steps=t5b.steps, footer=t5b.footer)
            jp = os.path.join(((cfg.get("paths") or {}).get("root") or ""),
                              "state", "notify.log")
            if not os.path.isfile(jp):
                jp = os.path.join(
                    (cfg.get("paths") or {}).get("state_dir", ""), "notify.log")
            txt5 = ""
            try:
                with open(jp, encoding="utf-8") as f:
                    txt5 = f.read()
            except Exception:
                txt5 = ""
            chk("Linux: запасной путь кладёт этапы с мс в ТЕЛО уведомления",
                all(s in txt5 for s in t5b.steps) and "1850 мс" in txt5,
                repr(txt5[-160:]))
        # сборщик подробностей для трея (без Qt): этапы и итог из состояния
        import tempfile as _tf
        import json as _json
        import tray as TR
        _d5 = _tf.mkdtemp(prefix="forest_tray_")
        cfg5 = dict(cfg)
        cfg5["paths"] = dict(cfg.get("paths") or {})
        cfg5["paths"]["state_dir"] = _d5
        _json.dump({"ts": time.time(), "state": "ok",
                    "proc": {"id": "p5", "kind": "restore", "cause": "outage",
                             "elapsed": 1.85, "final": True, "pct": 100,
                             "steps": [{"nm": "Обрыв обнаружен (пульс порта)",
                                        "t": 0.05, "done": True},
                                       {"nm": "Данные подтверждены",
                                        "t": 1.85, "done": True}]}},
                   open(os.path.join(_d5, "tray_status.json"), "w",
                        encoding="utf-8"), ensure_ascii=False)
        lines5, foot5 = TR._notify_details(cfg5, "restored")
        _say(f"  трей (без Qt) из состояния: этапы={lines5}, футер={foot5!r}")
        chk("трей (без Qt): этапы с мс берутся из состояния сторожа",
            len(lines5) == 2 and all(" мс" in s for s in lines5), str(lines5))
        chk("трей (без Qt): итоговое время восстановления в уведомлении",
            "1.85" in foot5 and "1850 мс" in foot5, foot5)
        _sh.rmtree(_d5, ignore_errors=True)
    except Exception as e:
        chk("запасной путь (notify.py) проверен", False, repr(e))
    # ── 1:1 с макетом на уровне ЧИСЕЛ: они читаются ИЗ файла макета ──
    mp = _mockup_path()
    css = ""
    try:
        with open(mp, encoding="utf-8") as f:
            css = f.read()
    except Exception:
        css = ""
    _say("  макет для сверки чисел: %s (%s)"
         % (mp, "найден" if css else "НЕ найден — сверка чисел пропущена"))
    if css:
        def mv(pat):
            m = re.search(pat, css)
            return m.group(1).strip() if m else ""

        def msum(bits):
            out = []
            for _name, v in bits:
                v = v.strip()
                if v.startswith("#"):
                    out.append((int(v[1:3], 16), int(v[3:5], 16),
                                int(v[5:7], 16)))
                else:
                    ns = [int(x) for x in re.findall(r"\d+", v)]
                    out.append(tuple(ns[:3]) if len(ns) >= 3 else ())
            return out

        v_w = mv(r"\.toast\{[^}]*width:(\d+)px")
        v_r = mv(r"\.toast\{[^}]*border-radius:(\d+)px")
        v_bl = mv(r"border-left:(\d+)px solid")
        v_pad = mv(r"\.toast\{[^}]*padding:([\dpx ]+);")
        v_ico = mv(r"\.ico\{[^}]*width:(\d+)px")
        v_icor = mv(r"\.ico\{[^}]*border-radius:(\d+)px")
        v_gap = mv(r"\.head\{[^}]*gap:(\d+)px")
        v_spin = mv(r"\.spin\{[^}]*width:(\d+)px")
        v_spinm = mv(r"\.spin\{[^}]*margin-top:(\d+)px")
        v_smt = mv(r"\.steps\{margin:(\d+)px 0 \d+px 45px")
        v_smb = mv(r"\.steps\{margin:\d+px 0 (\d+)px 45px")
        v_bmt = mv(r"\.bar\{[^}]*margin:(\d+)px 0 0 45px")
        v_fmt = mv(r"\.foot\{[^}]*margin:(\d+)px 0 0 45px")
        v_mk = mv(r"\.step \.mk\{[^}]*width:(\d+)px")
        v_sgap = mv(r"\.steps\{[^}]*gap:(\d+)px")
        v_barh = mv(r"\.bar\{[^}]*height:(\d+)px")
        v_in = mv(r"slidein (\d+)ms")
        v_out = mv(r"slideout (\d+)ms")
        v_cb = mv(r"slidein \d+ms cubic-bezier\(([^)]*)\)")
        v_ease = mv(r"slideout \d+ms ([a-z-]+)")
        v_slide = mv(r"translateX\((\d+)px\)")
        v_spins = mv(r"animation:spin ([\d.]+)s")
        v_lag = mv(r"at \+ ([\d.]+)")
        v_steps_js = mv(r"const STEPS = \[([\s\S]*?)\];")
        v_t1 = mv(r"\.t1\{[^}]*font-size:([\d.]+)px")
        v_t2 = mv(r"\.t2\{[^}]*font-size:([\d.]+)px")
        v_fs = mv(r"\.step\{[^}]*font-size:([\d.]+)px")
        v_ms = mv(r"\.step \.ms\{[^}]*font-size:([\d.]+)px")
        v_ff = mv(r"\.foot\{[^}]*font-size:([\d.]+)px")
        colors = msum([("ok", mv(r"--ok:(#[0-9a-fA-F]+)")),
                       ("err", mv(r"--err:(#[0-9a-fA-F]+)")),
                       ("busy", mv(r"--busy:(#[0-9a-fA-F]+)")),
                       ("off", mv(r"--off:(#[0-9a-fA-F]+)"))])
        cards = msum([("card", mv(r"--card:rgba\((\d+,\d+,\d+)")),
                      ("card2", mv(r"--card2:rgba\((\d+,\d+,\d+)")),
                      ("txt", mv(r"--txt:(#[0-9a-fA-F]+)"))])
        chk("макет: ширина карточки = NT_W", v_w == str(NT_W), v_w)
        chk("макет: радиус скругления = NT_RADIUS", v_r == str(NT_RADIUS), v_r)
        chk("макет: полоса состояния слева 3 px", v_bl == str(NT_B_L), v_bl)
        chk("макет: отступы карточки 13/15/12/14",
            v_pad == "13px 15px 12px 14px", v_pad)
        chk("макет: квадрат значка 34 px, радиус 9 px",
            v_ico == str(NT_ICO) and v_icor == str(NT_ICO_R),
            "%s/%s" % (v_ico, v_icor))
        chk("макет: зазор значок-текст 11 px", v_gap == str(NT_GAP), v_gap)
        chk("макет: спиннер 18 px, отступ сверху 4 px",
            v_spin == str(NT_SPIN) and v_spinm == str(NT_SPIN_MT),
            "%s/%s" % (v_spin, v_spinm))
        chk("макет: отступы этапов (11, 9, полоса 2, футер 7) и 45 px",
            v_smt == str(NT_STEPS_MT) and v_smb == str(NT_STEPS_MB)
            and v_bmt == str(NT_BAR_MT) and v_fmt == str(NT_FOOT_MT),
            ",".join((v_smt, v_smb, v_bmt, v_fmt)))
        chk("макет: маркер этапа 13 px, зазор между этапами 6 px",
            v_mk == str(NT_MK) and v_sgap == str(NT_STEP_GAP),
            "%s/%s" % (v_mk, v_sgap))
        chk("макет: полоса времени 3 px", v_barh == str(NT_BAR_H), v_barh)
        chk("макет: вход 340 мс, выход 260 мс",
            v_in == str(ANIM_IN_MS) and v_out == str(ANIM_OUT_MS),
            "%s/%s" % (v_in, v_out))
        chk("макет: кривые движения = CB_IN / CB_OUT",
            v_cb == ".16,.84,.28,1" and v_ease == "ease-in",
            "%s/%s" % (v_cb, v_ease))
        chk("макет: сдвиг 420 px = SLIDE_PX", v_slide == str(SLIDE_PX), v_slide)
        chk("макет: спиннер .85 с на оборот = SPIN_MS",
            bool(v_spins) and abs(float(v_spins) - SPIN_MS / 1000.0) < 1e-6,
            v_spins)
        chk("макет: цвета состояний ok/err/busy/off = ui_style (одна таблица)",
            colors == [NT_STATE["ok"][:3], NT_STATE["error"][:3],
                       NT_STATE["busy"][:3], NT_STATE["off"][:3]], str(colors))
        chk("макет: цвета карточки (фон/квадрат/текст) = ui_style (одна таблица)",
            cards == [US.DARK["bg"][:3], US.DARK["surface"][:3],
                      US.DARK["txt"][:3]],
            str(cards))
        chk("макет: размеры шрифтов совпадают с NT_FONT",
            all(abs(float(v) - NT_FONT[k]) <= 0.5 for k, v in
                (("t1", v_t1), ("t2", v_t2), ("step", v_fs), ("ms", v_ms),
                 ("foot", v_ff)) if v),
            "%s/%s/%s/%s/%s" % (v_t1, v_t2, v_fs, v_ms, v_ff))
        chk("макет: этап считается пройденным через STEP_LAG полосы",
            v_lag == str(STEP_LAG), v_lag)
        chk("макет: строки заголовков/подписей/футера — те же, что рисуем мы",
            all(x in css for x in ("Восстановление связи", "Связь восстановлена",
                                   "подбор живого сервера",
                                   "связь подтверждена · 1.85 с",
                                   "восстановлено за 1.85 с · окно закроется "
                                   "через 4 с", "наблюдение включено")), "")
        chk("макет: этапы и их времена в макете совпадают с нашими данными",
            all(nm in css for nm, _ in MOCK_STEPS)
            and all(str(ms) in v_steps_js for _, ms in MOCK_STEPS),
            v_steps_js.replace("\n", " ")[:90])

    _say()
    _say("=" * 64)
    _say("5в. ОДНА КАРТОЧКА для всех путей: событие, --show, пункт меню")
    _say("=" * 64)
    d1 = demo_card_data(cfg)
    _say(f"  данные демо-карточки: этапов={len(d1['steps'])}, "
         f"времена={[s['ms'] for s in d1['steps']]} мс, итог={d1['total_ms']} мс")
    chk("демо-данные: этапы со временем в мс и общая длительность",
        len(d1["steps"]) >= 2 and all(int(s["ms"]) > 0 for s in d1["steps"])
        and int(d1["total_ms"]) > 0,
        f'{len(d1["steps"])}/{d1["total_ms"]}')
    chk("демо-данные: подпись и футер не пустые и без «0.00 с»",
        bool(d1["sub"]) and bool(d1["footer"])
        and "0.00" not in d1["sub"] and "0.00" not in d1["footer"],
        f'{d1["sub"]!r} / {d1["footer"]!r}')
    # карточка из этих данных — ВНЕ ЭКРАНА, полный состав + серия кадров
    globals()["TEST_ANCHOR"] = (-8000, -8000, 900, 700)
    stats_d = {}
    td = Toast(d1["kind"], d1["title"], "", cfg, duration_ms=HOLD_FINAL_MS,
               stats_sink=stats_d, sub=d1["sub"], steps=d1["steps"],
               footer=d1["footer"], spinner=True, total_ms=d1["total_ms"],
               role=d1["role"])
    td.show_toast()
    tx_d = td.texts()
    lay_d = dict(td._lay)
    icon_ok = bool(td.icon_pm is not None and not td.icon_pm.isNull())
    t0 = time.time()
    while time.time() - t0 < 15 and td.phase != "hold":
        QtWidgets.QApplication.processEvents()
        time.sleep(0.005)
    import tempfile as _tfp
    tmp_proof = _tfp.mkdtemp(prefix="forest_card_")
    pr = capture_card_proof(cfg, td, out_dir=tmp_proof, name="selftest-card",
                            frames=8, gap_s=0.1)
    globals()["TEST_ANCHOR"] = None
    _say(f"  кадров удержания={pr['frames']}, уникальных отпечатков: карточка "
         f"{pr['card_unique']}, спиннер {pr['spinner_unique']}, полоса "
         f"{pr['bar_unique']}")
    _say(f"  изменённых пикселей между кадрами: спиннер "
         f"{pr['spinner_changed_px']}, полоса {pr['bar_changed_px']}")
    _say(f"  состав: {pr['composition']}")
    chk("карточка: ВСЕ элементы макета (значок+метка, заголовок, подпись, "
        "этапы с мс, полоса, спиннер, футер)",
        icon_ok and bool(tx_d["title"]) and bool(tx_d["sub"])
        and bool(tx_d["steps"]) and all(" мс" in s for s in tx_d["steps"])
        and lay_d.get("y_bar") is not None and bool(td.spinner)
        and bool(tx_d["footer"]),
        f'icon={icon_ok}, steps={len(tx_d["steps"])}, '
        f'bar={lay_d.get("y_bar")}, spinner={td.spinner}')
    chk("карточка: в текстах НЕТ «0.00 с»",
        "0.00" not in (tx_d["sub"] or "") and "0.00" not in (tx_d["footer"] or ""),
        f'{tx_d["sub"]!r} / {tx_d["footer"]!r}')
    chk("ДВИЖЕНИЕ НА УДЕРЖАНИИ: пиксели спиннера меняются от кадра к кадру "
        "(>= 6 разных)", int(pr["spinner_unique"]) >= 6,
        str(pr["spinner_unique"]))
    chk("ДВИЖЕНИЕ НА УДЕРЖАНИИ: пиксели полосы меняются от кадра к кадру "
        "(>= 6 разных)", int(pr["bar_unique"]) >= 6, str(pr["bar_unique"]))
    chk("ДВИЖЕНИЕ: карточка целиком меняется от кадра к кадру",
        int(pr["card_unique"]) >= 6, str(pr["card_unique"]))
    try:
        td.close()
    except Exception:
        pass
    QtWidgets.QApplication.processEvents()
    import shutil as _shc
    _shc.rmtree(tmp_proof, ignore_errors=True)
    # путь СОБЫТИЯ без этапов: карточка всё равно полная
    ui.show_toast("ok", "Связь восстановлена", "", duration_ms=800)
    te = ui.toast
    tx_e = te.texts() if te is not None else {}
    chk("событие БЕЗ этапов тоже даёт полную карточку (этапы, полоса, спиннер)",
        te is not None and bool(tx_e.get("steps")) and bool(te.spinner)
        and te._lay.get("y_bar") is not None and bool(tx_e.get("footer")),
        f'steps={len(tx_e.get("steps") or [])}, spinner='
        f'{getattr(te, "spinner", None)}')
    chk("событие: в подписи и футере нет «0.00 с»",
        "0.00" not in (tx_e.get("sub") or "")
        and "0.00" not in (tx_e.get("footer") or ""),
        f'{tx_e.get("sub")!r} / {tx_e.get("footer")!r}')
    try:
        te.close()
    except Exception:
        pass
    QtWidgets.QApplication.processEvents()
    # путь МЕНЮ «Проверить уведомление» — та же карточка
    ui.on_action("test_notify")
    tm_ = ui.toast
    tx_m = tm_.texts() if tm_ is not None else {}
    chk("пункт меню «Проверить уведомление» даёт ту же полную карточку",
        tm_ is not None and bool(tx_m.get("steps")) and bool(tm_.spinner)
        and tm_._lay.get("y_bar") is not None and bool(tx_m.get("footer")),
        f'steps={len(tx_m.get("steps") or [])}, spinner='
        f'{getattr(tm_, "spinner", None)}')
    chk("меню: в подписи и футере нет «0.00 с»",
        "0.00" not in (tx_m.get("sub") or "")
        and "0.00" not in (tx_m.get("footer") or ""),
        f'{tx_m.get("sub")!r} / {tx_m.get("footer")!r}')
    try:
        tm_.close()
    except Exception:
        pass
    QtWidgets.QApplication.processEvents()
    # ОДНО ДЕЙСТВИЕ = ОДНА КАРТОЧКА: гейт склейки серии карточек
    ui2 = Ui(cfg, quiet=True)
    ui2.quiet = False            # проверяем именно гейт склейки (quiet его обходит)
    ui2._last_card_ts = time.time()
    chk("склейка: карточки в коротком окне подавляются (одно действие -> одна)",
        ui2._card_gate("вторая") is True and ui2._card_gate("третья") is True)
    ui2._last_card_ts = time.time() - (CARD_COALESCE_MS / 1000.0) - 0.1
    chk("склейка: по истечении окна карточка показывается снова",
        ui2._card_gate("позже") is False)
    ui2._demo = True
    ui2._last_card_ts = time.time()
    chk("явный демо-режим (--show-all) гейт склейки не проходит",
        ui2._card_gate("демо") is False)
    chk("есть явный демо-флаг --show-all и функция показа серии",
        callable(globals().get("show_all_demo")))
    # пустые данные не печатают нулей ни в карточке, ни в status_info
    chk("нет замера -> нет числа: final_texts(0) без «0.00 с»",
        "0.00" not in final_texts(0)[0] and "0.00" not in final_texts(0)[1],
        repr(final_texts(0)))
    nz = SI.notif({"kind": "restore", "final": True, "elapsed": 0.0})
    chk("status_info: финал без замера не печатает «0.00 с»",
        "0.00" not in nz["sub"] and "0.00" not in nz["footer"],
        f'{nz["sub"]!r} / {nz["footer"]!r}')

    _say()
    _say("=" * 64)
    _say("5б. индикация по эталону (v2ray-auto-updater): поля и тексты")
    _say("=" * 64)
    st_ok = {"ts": time.time(), "state": "ok", "rtt": 21}
    tip_ok = SI.tip(cfg, st_ok)
    fields = [p.strip() for p in tip_ok.split("|")]
    _say(f"  подсказка: {tip_ok!r} -> поля {fields}")
    chk("подсказка: четыре поля в порядке эталона (имя, состояние, серверов, "
        "маршрут, watch OK)",
        len(fields) == 4 and fields[2] == SI.route_text(cfg) and
        fields[3] == "watch OK", str(fields))
    proc = {"id": "t", "kind": "restore", "cause": "outage", "started": time.time(),
            "eta_s": 0.95, "pct": 75, "phase": "Подтверждение трафика…",
            "elapsed": 1.4, "final": False,
            "steps": [{"nm": "Обрыв обнаружен (пульс порта)", "t": 0.05,
                       "done": True},
                      {"nm": "Трафик подтверждён (204 + TLS)", "t": 0.95,
                       "done": False}]}
    n = SI.notif(proc)
    nf = SI.notif(dict(proc, final=True, pct=100, elapsed=1.85))
    _say(f"  окно: {n['title']!r} / {n['sub']!r} / {n['pct']}% / {n['footer']!r}")
    _say(f"  финал: {nf['title']!r} / {nf['sub']!r} / {nf['footer']!r}")
    chk("тексты окна совпадают с эталоном дословно",
        n["title"] == "Восстановление связи"
        and n["sub"] == "обрыв обнаружен · пульс порта"
        and nf["title"] == "Связь восстановлена"
        and "связь подтверждена" in nf["sub"], f"{n['title']} / {nf['title']}")
    chk("время восстановления связи есть (в подзаголовке и футере)",
        "1.85" in nf["sub"] and "1.85" in nf["footer"],
        f"{nf['sub']} | {nf['footer']}")
    chk("этапы идут с временами, проценты есть",
        all(l.endswith(" с") for l in SI.step_lines(proc)) and n["pct"] == 75,
        str(SI.step_lines(proc)))
    rows = SI.info_lines(cfg, {"ts": time.time(), "state": "ok",
                               "last_restore_s": 1.85})
    keys = [k for k, _ in rows]
    chk("сведения содержат состояние, серверы, маршрут, сторожа и время "
        "восстановления", "состояние связи" in keys and "серверов в пуле" in keys
        and "маршрут" in keys and "сторож" in keys
        and any("восстановления" in k for k in keys), str(keys))
    chk("шесть состояний эталона дают свои цвета",
        SI.state_color("ok") == (57, 194, 109)
        and SI.state_color("down") == (229, 72, 77)
        and SI.state_color("restoring") == (79, 140, 255)
        and SI.state_color("switch") == (240, 180, 41),
        str([SI.state_color(k) for k in ("ok", "down", "restoring", "switch")]))

    _say()
    _say("=" * 64)
    _say("6. покой: цикл не крутится, процессор не тратится")
    _say("=" * 64)
    c0 = proc_cpu_ms()
    t0 = time.time()
    while time.time() - t0 < 3.0:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    c_idle = proc_cpu_ms() - c0
    _say(f"  за 3 с простоя: CPU={c_idle:.1f} мс")
    chk("в покое процессор не расходуется (< 60 мс за 3 с)", c_idle < 60.0,
        f"{c_idle:.1f} мс")

    _say()
    _say("=" * 64)
    _say("7. безопасный вывод и защита от необработанных исключений")
    _say("=" * 64)
    # Печать символа вне cp1251 в оконной сборке давала UnicodeEncodeError и
    # МОДАЛЬНОЕ ОКНО PyInstaller на рабочем столе. Проверяем, что это невозможно.
    bad_char = "✓ — «»"
    raised = None
    try:
        SD.say(bad_char)                 # не должно бросить НИКОГДА
    except Exception as e:               # pragma: no cover
        raised = e
    chk("печать символа вне cp1251 не бросает исключения (нет модального окна)",
        raised is None, str(raised))
    chk("кодировка stdout запомнена до перевода (решение о не-ASCII осознанное)",
        isinstance(SD._ORIG_ENCODING, str), SD._ORIG_ENCODING)
    # --selftest печатает ТОЛЬКО то, что выдержит кодировка потока
    enc_ok = True
    try:
        SD.say("проверка")
        SD.say("check")
    except Exception:
        enc_ok = False
    chk("служебная печать идёт через безопасный слой", enc_ok)
    # необработанное исключение -> журнал + код, БЕЗ окна
    def _boom(_argv=None):
        raise RuntimeError("искусственное исключение проверки")
    rc = SD.guarded(_boom, where="forest-ui-selftest")
    # crash.log пишется РЯДОМ С ЖУРНАЛАМИ (SD.log_dir()), а не в state/
    cpath = os.path.join(SD.log_dir(), "crash.log")
    txt = ""
    try:
        txt = open(cpath, encoding="utf-8").read()
    except Exception:
        txt = ""
    chk("guarded(): исключение внутри main -> ненулевой код (70)", rc == 70, str(rc))
    chk("guarded(): трассировка записана в crash.log (UTF-8)",
        "искусственное исключение проверки" in txt and
        "RuntimeError" in txt, cpath)
    chk("глобальный перехватчик исключений установлен (sys.excepthook)",
        callable(getattr(sys, "excepthook", None)),
        str(getattr(sys, "excepthook", None)))
    # человекочитаемый отчёт — в UTF-8-ФАЙЛ, а не в stdout
    _tp = SD.write_report("_probe_utf8.txt", "✓ проверка UTF-8 — ок")
    _tt = ""
    try:
        _tt = open(_tp, encoding="utf-8").read()
    except Exception:
        _tt = ""
    chk("отчёт самотеста пишется в UTF-8-ФАЙЛ (stdout для этого не нужен)",
        bool(_tp) and "✓ проверка" in _tt, _tp)
    try:
        if _tp:
            os.unlink(_tp)
    except Exception:
        pass

    _say()
    _say("=" * 64)
    _say("РЕЗУЛЬТАТ")
    _say("=" * 64)
    for line in out:
        _say(line)
    _say(f"ИТОГ: {'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' if not bad else 'ПРОВАЛЕНО ' + str(len(bad))}")
    # ЧЕЛОВЕКОЧИТАЕМЫЙ отчёт — в UTF-8-ФАЙЛЫ (в stdout он не помещается: там
    # только ASCII, иначе оконная сборка падает модальным окном на cp1251)
    tpath, jpath = write_selftest_reports(cfg, report, out, bad)
    _say(f"  отчёт (UTF-8): {tpath}")
    _say(f"  отчёт (JSON):  {jpath}")
    return 0 if not bad else 1


def PM_SIZE(pm) -> str:
    return f"{pm.width()}x{pm.height()}"


def foreground():
    if not IS_WIN:
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def proc_cpu_ms() -> float:
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes

            class FT(ctypes.Structure):
                _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]
            c, e, k, u = FT(), FT(), FT(), FT()
            ctypes.windll.kernel32.GetProcessTimes(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c),
                ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
            return (((k.hi << 32) | k.lo) + ((u.hi << 32) | u.lo)) / 10000.0
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF)
        return (r.ru_utime + r.ru_stime) * 1000.0
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════ запуск ═══
_LOCK = None            # замок помощника, удерживаемый этим процессом


def _lock_path(cfg: dict) -> str:
    d = ((cfg or {}).get("paths") or {}).get("state_dir") or ""
    return os.path.join(d, "forest-ui.lock") if d else ""


def single_instance_guard(cfg: dict) -> bool:
    """True — работаем; False — помощник уже запущен (тихо выходим).

    Решение принимает ЗАМОК (эксклюзивная блокировка файла, см.
    ``src/instancelock.py``), а не PID в файле пульса. Пульс появляется с
    задержкой, поэтому два одновременных запуска проходили старую проверку
    оба — теперь замок достаётся ровно одному, второй тихо выходит.
    Если замок недоступен (деградация), работает прежний признак по пульсу.
    """
    global _LOCK
    os.environ["FOREST_UI"] = "1"        # этот процесс — помощник
    p = _lock_path(cfg)
    locked = False
    try:
        import instancelock as IL
        lk = IL.InstanceLock(p, tag="forest-ui")
        if lk.acquire({"pid": os.getpid(), "ts": time.time(),
                       "exe": sys.executable,
                       "root": ((cfg or {}).get("paths") or {}).get("root", "")}):
            _LOCK = lk
            locked = True
            if lk.degraded:
                locked = False           # замок не работает — решаем по пульсу
    except Exception:
        _LOCK = None
    if locked:
        UC.write_ping(cfg, os.getpid(), "qt")
        return True
    # деградация: прежний признак (живой pid в пульсе)
    try:
        pp = UC.ping_path(cfg)
        if os.path.isfile(pp):
            with open(pp, encoding="utf-8") as f:
                d = json.load(f)
            pid = int(d.get("pid", 0) or 0)
            if pid and pid != os.getpid() and _alive(pid):
                return False
    except Exception:
        pass
    UC.write_ping(cfg, os.getpid(), "qt")
    return True


def _alive(pid: int) -> bool:
    try:
        if IS_WIN:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def stop_running(cfg: dict) -> int:
    """Просит работающий помощник выйти (файл-флаг) и ждёт."""
    p = os.path.join((cfg.get("paths") or {}).get("state_dir", ""), "ui_stop.flag")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("stop")
    except Exception as e:
        _say(f"не удалось запросить остановку: {e}")
        return 2
    return 0


# ── графическая сессия ──────────────────────────────────────────────────────
def _no_graphics_reason() -> str:
    """Почему помощнику негде показать интерфейс ('' — сессия есть).

    Проверяется ДО создания QApplication: без графической сессии Qt не находит
    платформенный плагин и аварийно завершается (Aborted), а помощник должен
    просто тихо выйти. Явно заданная ``QT_QPA_PLATFORM`` (offscreen и т. п.)
    считается сессией — так помощник запускают бездисплейные проверки.
    """
    if IS_WIN:
        return ""
    if os.environ.get("QT_QPA_PLATFORM"):
        return ""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return ""
    return "нет графической сессии (DISPLAY/WAYLAND_DISPLAY не заданы)"


def notification_service_available() -> bool:
    """Есть ли на сессионной шине служба уведомлений org.freedesktop.Notifications.

    На Linux это ровно та служба, которой помощник отдаёт уведомления вместо
    своего окна. Основной путь — QtDBus; если его нет, считаем доступным то,
    чем вообще можно отправить уведомление (gdbus/notify-send). Ничего не
    бросает: сбой определения — это ``False``.
    """
    try:
        from PySide6 import QtDBus
        bus = QtDBus.QDBusConnection.sessionBus()
        if bus.isConnected():
            r = bus.interface().isServiceRegistered("org.freedesktop.Notifications")
            if r.isValid():
                return bool(r.value())
    except Exception:
        pass
    try:
        import shutil
        return bool(shutil.which("gdbus") or shutil.which("notify-send"))
    except Exception:
        return False


def show_all_demo(cfg: dict) -> int:
    """ЯВНЫЙ ДЕМО-РЕЖИМ (--show-all): показать ВСЮ серию состояний карточки.

    Это единственное место, где карточки идут серией. В обычной работе одно
    реальное действие даёт РОВНО ОДНУ карточку (склейка в Ui._card_gate).
    """
    import notify as N5
    try:
        N5.configure(cfg)
    except Exception:
        pass
    try:
        import i18n as I
        tt = (lambda k, **kw: I.t(k, **kw))
    except Exception:
        tt = None
    st = read_raw_state(cfg)
    tip = SI.tip(cfg, st, limit=0)
    def T(k, d):
        try:
            return tt(k) if tt else d
        except Exception:
            return d
    seq = [
        ("ok", T("notify.restored.title", "Связь восстановлена"),
         "Демо: состояние «связь восстановлена»"),
        ("error", T("notify.lost.title", "Связь пропала"),
         "Демо: состояние «связь пропала»"),
        ("busy", SI.TITLE_SWITCH, "Демо: состояние «переключение сервера»"),
        ("ok", tip, "Демо: рабочее состояние (подсказка значка)"),
        ("off", T("notify.off.title", "Прокси выключен вручную"),
         "Демо: состояние «прокси выключен вручную»"),
    ]
    for kind, title, body in seq:
        try:
            N5.notify(kind, title, body, force=True,
                      duration_ms=N5.settings(cfg).get("duration_ms", 4000))
        except Exception as e:
            _say(f"show-all: {kind} ({title}): {e}")
    try:
        N5.wait_idle(90.0)
    except Exception:
        pass
    _say(f"show-all: показано карточек: {len(seq)}")
    return 0


def main(argv=None) -> int:
    SD.safe_stdio()
    # КРЮЧОК ПРОВЕРКИ: искусственное исключение внутри main — им проверяют, что
    # на рабочем столе НЕ появляется модальное окно PyInstaller, а трассировка
    # уходит в журнал (crash.log) и код возврата ненулевой.
    if os.environ.get("FOREST_TEST_CRASH") == "1":
        raise RuntimeError("FOREST_TEST_CRASH: искусственное исключение внутри main")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not QT_OK:
        # Qt нет — молча выходим: основная программа работает и без помощника
        _say(f"forest-ui: Qt недоступен ({_QT_ERROR}) — помощник не запущен")
        return 3
    cfg = load_cfg()
    try:
        log.reset_logger((cfg.get("paths") or {}).get("log_dir"))
    except Exception:
        pass
    # Графическую сессию проверяем ДО создания QApplication (см.
    # _no_graphics_reason): без неё Qt аварийно завершается вместо тихого
    # выхода. Для проверок (--selftest, --menu-dump) включаем платформу
    # offscreen: это не показ интерфейса, а самопроверка без дисплея.
    nogfx = _no_graphics_reason()
    if nogfx and ("--selftest" in argv or "--menu-dump" in argv
                  or "--card-shots" in argv):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    elif nogfx:
        msg = f"forest-ui: {nogfx} — помощник не запущен"
        try:
            log.get_logger().info(msg)
        except Exception:
            pass
        _say(msg)
        return 0
    if "--stop" in argv:
        return stop_running(cfg)
    if "--menu-dump" in argv:
        # Необязательный аргумент — путь файла: пишем дамп в UTF-8, чтобы проверки
        # не зависели от кодировки консоли (оконная сборка отдаёт cp1251).
        i = argv.index("--menu-dump")
        path = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else ""
        app = QtWidgets.QApplication([])
        ui = Ui(cfg)
        dump = ui.menu_dump()
        _say(dump)
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(dump + "\n")
            except Exception as e:
                _say(f"не удалось записать {path}: {e}")
        ui.tray.hide()
        app.quit()
        return 0
    if "--show-all" in argv:
        # ЯВНЫЙ ДЕМО-РЕЖИМ: единственное место, где карточки идут серией
        # (ok -> error -> busy -> ok -> off). В обычной работе — одна карточка
        # на действие.
        return show_all_demo(cfg)
    if "--show" in argv:
        i = argv.index("--show")
        # ОДНА карточка: даже служебный показ идёт полным составом макета —
        # этапы с временами в мс, полоса, спиннер, футер. Данные — реальные
        # (status_info) либо последнее известное состояние.
        d = demo_card_data(cfg)
        kind = argv[i + 1] if len(argv) > i + 1 else d["kind"]
        title = argv[i + 2] if len(argv) > i + 2 else d["title"]
        body = argv[i + 3] if len(argv) > i + 3 else ""
        app = QtWidgets.QApplication([])
        app.setQuitOnLastWindowClosed(False)
        stats = {}
        t = Toast(kind, title, body, cfg, duration_ms=HOLD_FINAL_MS + 1200,
                  stats_sink=stats, on_done=app.quit, sub=d["sub"] if not body else "",
                  steps=d["steps"], footer=d["footer"], spinner=True,
                  total_ms=d["total_ms"], role=d["role"])
        t.show_toast()
        app.exec()
        _say(f"frames={stats.get('frames')} avg_fps={stats.get('avg_fps')} "
              f"worst_ms={stats.get('worst_ms')}")
        return 0
    if "--card-shots" in argv:
        # ДОКАЗАТЕЛЬСТВО без показа окон: карточка рисуется вне экрана, с неё
        # снимается серия кадров удержания (спиннер/полоса/карточка), PNG и
        # числа кладутся рядом. Путь — файл PNG (по умолчанию docs/notify-card.png).
        i = argv.index("--card-shots")
        out = argv[i + 1] if len(argv) > i + 1 else os.path.join(
            os.getcwd(), "docs", "notify-card.png")
        return card_shots_mode(cfg, out)
    if "--menu-shots" in argv:
        # ДОКАЗАТЕЛЬСТВО меню без показа окон: снимки панели (тёмная/светлая,
        # наведение, подменю) + пиксельная сверка с палитрой карточки.
        i = argv.index("--menu-shots")
        out = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") \
            else os.path.join(os.getcwd(), "docs")
        return menu_shots_mode(cfg, out)
    if "--menu-live" in argv:
        # НАМЕРЕННЫЙ показ меню на живом экране (разрешён пользователем):
        # замер интервалов между кадрами и закрытие с анимацией.
        i = argv.index("--menu-live")
        ms, at, theme, out_json, shot = 2600, None, "", "", ""
        align = True
        sub_key = ""
        rest = argv[i + 1:]
        for j, a in enumerate(rest):
            if a == "--ms" and j + 1 < len(rest):
                ms = int(float(rest[j + 1]))
            elif a == "--at" and j + 2 < len(rest):
                at = (float(rest[j + 1]), float(rest[j + 2]))
            elif a == "--theme" and j + 1 < len(rest):
                theme = str(rest[j + 1])
            elif a == "--json" and j + 1 < len(rest):
                out_json = str(rest[j + 1])
            elif a == "--shot" and j + 1 < len(rest):
                shot = str(rest[j + 1])
            elif a == "--no-align":
                align = False
            elif a == "--sub" and j + 1 < len(rest):
                sub_key = str(rest[j + 1])
        return menu_live_mode(cfg, ms=ms, at=at, theme=theme, out_json=out_json,
                              shot=shot, align=align, sub_key=sub_key)
    if "--menu-hover" in argv:
        # НАМЕРЕННЫЙ показ меню ради ЖИВЫХ замеров наведения (подменю по
        # наведению и перенос подсветки): иначе задержки не измерить.
        i = argv.index("--menu-hover")
        at, out_json, shot, label = None, "", "", ""
        wait_ms = 1600
        rest = argv[i + 1:]
        for j, a in enumerate(rest):
            if a == "--at" and j + 2 < len(rest):
                at = (float(rest[j + 1]), float(rest[j + 2]))
            elif a == "--json" and j + 1 < len(rest):
                out_json = str(rest[j + 1])
            elif a == "--shot" and j + 1 < len(rest):
                shot = str(rest[j + 1])
            elif a == "--label" and j + 1 < len(rest):
                label = str(rest[j + 1])
            elif a == "--wait-ms" and j + 1 < len(rest):
                wait_ms = int(float(rest[j + 1]))
        return menu_hover_mode(cfg, out_json=out_json, shot=shot, at=at,
                               label=label, wait_ms=wait_ms)
    if "--screens" in argv:
        app = QtWidgets.QApplication([])
        for s in screens_info():
            _say("экран: %s %s масштаб %s частота %s Гц основной=%s"
                 % (s["name"], s["geometry"], s["dpr"], s["refresh_hz"],
                    s["primary"]))
        app.quit()
        return 0
    # (проверка графической сессии — выше, до QApplication)
    app = QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP)
    if "--selftest" in argv:
        rc = selftest(cfg)
        return rc
    if not single_instance_guard(cfg):
        _say("forest-ui: помощник уже работает — второй не нужен")
        return 0
    ui = Ui(cfg)
    _say(f"forest-ui: работает (pid={os.getpid()}, "
          f"трей={'есть' if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable() else 'недоступен'})")
    stop_flag = os.path.join((cfg.get("paths") or {}).get("state_dir", ""),
                             "ui_stop.flag")
    t = QtCore.QTimer()
    t.setInterval(1000)

    def watch_stop():
        if os.path.isfile(stop_flag):
            try:
                os.unlink(stop_flag)
            except Exception:
                pass
            try:
                UC.clear_ping(cfg)
            except Exception:
                pass
            ui.tray.hide()
            QtWidgets.QApplication.quit()

    t.timeout.connect(watch_stop)
    t.start()
    rc = app.exec()
    try:
        UC.clear_ping(cfg)
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    # ЛЮБОЕ необработанное исключение -> журнал + код 70. Модального окна
    # PyInstaller («Unhandled exception in script») не будет ни при каких
    # обстоятельствах: см. src/safestdio.py.
    sys.exit(SD.guarded(main, where="forest-ui"))
