# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""Состав индикации: что именно показывают значок, меню и уведомление.

ЭТАЛОН — прежняя надстройка (``D:\\v2ray-auto-updater``, сейчас выключена):
скрипт ``watch.py`` (подсказка значка ``_tip``), ``tray_status.py`` (поля
состояния и описание процесса) и окно индикации в ``tray.ps1`` (заголовок,
подзаголовок, проценты, список этапов с временами, футер).

Здесь состав данных перенесён ДОСЛОВНО и собран в одном месте, чтобы обе
реализации интерфейса (встроенный значок и помощник на Qt) показывали одно и
то же. Менять состав нельзя: улучшаем только плавность и оформление.

Схема состояния (``state/tray_status.json``, пишет сторож):
    ts      время записи (по нему видно, молчит ли сторож)
    state   ok | switch | down | restoring
    tip     готовая подсказка (Windows режет её на 63 символах)
    rtt     задержка, мс
    proc    описание процесса восстановления/переключения:
            kind (restore|switch), cause (outage|scheduled), phase, pct,
            elapsed, eta_s, final, failed, steps [{nm, t, done, failed}]

Состояния значка (цвета — как в эталоне):
    ok         (57, 194, 109)   связь OK
    off        (132, 132, 140)  прокси выключен вручную
    switch     (240, 180, 41)   переключение сервера
    noanswer   (240, 180, 41)   сторож молчит (нет данных)
    down       (229, 72, 77)    НЕТ СВЯЗИ
    restoring  (79, 140, 255)   восстановление связи (спиннер)
"""
from __future__ import annotations

import time

# ── состояния значка и их цвета (эталон: tray.ps1, New-Icon) ────────────────
STATES = {
    "ok": {"rgb": (57, 194, 109), "ru": "связь OK", "en": "link OK"},
    "off": {"rgb": (132, 132, 140), "ru": "выключено вручную",
            "en": "switched off by hand"},
    "switch": {"rgb": (240, 180, 41), "ru": "переключение", "en": "switching"},
    "noanswer": {"rgb": (240, 180, 41), "ru": "сторож молчит (нет данных)",
                 "en": "watchdog silent (no data)"},
    "down": {"rgb": (229, 72, 77), "ru": "НЕТ СВЯЗИ", "en": "NO CONNECTION"},
    "restoring": {"rgb": (79, 140, 255), "ru": "восстановление связи",
                  "en": "restoring the link"},
    # синонимы «рабочих» состояний программы (те же цвета, что у значка)
    "busy": {"rgb": (240, 180, 41), "ru": "переключение", "en": "switching"},
    "error": {"rgb": (229, 72, 77), "ru": "НЕТ СВЯЗИ", "en": "NO CONNECTION"},
}
STATE_ORDER = ("ok", "off", "switch", "noanswer", "down", "restoring")

STALE_S = 90.0          # столько без записи — считаем «сторож молчит»

# Точные формулировки эталона (tray.ps1, Render-Frame)
TITLE_RESTORE = "Восстановление связи"
TITLE_RESTORE_FINAL = "Связь восстановлена"
TITLE_SWITCH = "Переключение сервера"
TITLE_SWITCH_FINAL = "Переключение завершено"
TITLE_FAILED = "Восстановить не удалось"
SUB_FAILED = "попытки исчерпаны, повтор автоматически"
SUB_RESTORE = "обрыв обнаружен · пульс порта"
SUB_SWITCH = "подбор живого сервера"
TIP_OFF_RU = "прокси ВЫКЛЮЧЕН вручную"
TIP_NODATA_RU = "сторож молчит (нет данных)"


def _cfg_paths(cfg: dict) -> dict:
    return (cfg or {}).get("paths") or {}


def route_text(cfg: dict, lang: str = "ru") -> str:
    """Маршрут РФ — как в подсказке эталона ('РФ напрямую')."""
    try:
        on = bool(((cfg or {}).get("watch") or {}).get("ru_direct", False))
    except Exception:
        on = False
    if lang == "en":
        return "RU direct" if on else "RU via proxy"
    return "РФ напрямую" if on else "РФ через прокси"


def pool_n(cfg: dict, st: dict = None) -> int:
    """Количество серверов в пуле.

    В эталоне подсказка всегда показывала «10 серверов» (значение по умолчанию
    аргумента) — то есть поле было, а число не обновлялось. Поле оставлено тем
    же, но показываем НАСТОЯЩЕЕ число: сначала из состояния, затем из состояния
    стора (`balancer_n`), затем из настроек.
    """
    if st:
        for k in ("pool_n", "pool", "balancer_n"):
            try:
                if st.get(k) is not None:
                    return int(st[k])
            except Exception:
                continue
    try:
        import clientwrite as CW
        d = getattr(CW.StateStore(cfg), "data", {}) or {}
        n = int(d.get("balancer_n", 0) or 0)
        if n > 0:
            return n
    except Exception:
        pass
    try:
        return int(((cfg or {}).get("watch") or {}).get("balancer_pool_n", 10))
    except Exception:
        return 10


def age_of(st: dict) -> float:
    """Возраст записи состояния в секундах; <0 — данных нет вовсе.

    Порядок: поле ``ts`` (его пишет сторож), затем ``_mtime`` файла (его кладёт
    ``tray_status.read_status``, если в записи нет ts — старая версия программы
    или чужой файл), и только потом «данных нет». Без этой подстраховки запись
    без ``ts`` считалась вечно устаревшей и значок показывал «сторож молчит»,
    хотя состояние было свежим.
    """
    st = st or {}
    if not st:
        return -1.0
    try:
        ts = float(st.get("ts") or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        try:
            ts = float(st.get("_mtime") or 0)
        except Exception:
            ts = 0.0
    if ts <= 0:
        return -1.0
    return max(0.0, time.time() - ts)


def state_key(cfg: dict, st: dict) -> str:
    """Одно из шести состояний значка.

    Логика эталона: молчащий сторож -> noanswer; ручное выключение -> off;
    идущее восстановление -> restoring; иначе — поле state.
    """
    st = st or {}
    age = age_of(st)
    if age < 0:
        return "noanswer"
    if age > STALE_S:
        return "noanswer"
    proc = st.get("proc") or {}
    if proc and not proc.get("final") and proc.get("kind") == "restore":
        return "restoring"
    s = str(st.get("state") or "").lower()
    if s in ("ok", "off", "switch", "down", "restoring"):
        return s
    return "ok"


def state_color(key: str):
    return STATES.get(key, STATES["ok"])["rgb"]


# ── подсказка значка (эталон: watch._tip) ──────────────────────────────────
def tip(cfg: dict, st: dict, limit: int = 63, lang: str = "ru") -> str:
    """'FOREST связь OK | 10 серверов | РФ напрямую | watch OK'.

    ``limit`` — обрезка: у значка Windows подсказка ограничена 63 символами
    (ограничение системы, поэтому в эталоне стояло ``[:63]``). Помощник на Qt в
    Windows-трее тоже ограничен, но в документации/меню строку показываем
    целиком — данные не теряем.
    """
    st = st or {}
    age = age_of(st)
    if age < 0 or age > STALE_S:
        return (f"FOREST: {TIP_NODATA_RU}" if lang == "ru"
                else "FOREST: watchdog silent (no data)")
    key = state_key(cfg, st)
    if key == "off":
        return (f"FOREST: {TIP_OFF_RU}" if lang == "ru"
                else "FOREST: proxy switched off by hand")
    s_txt = STATES[key]["ru"] if lang == "ru" else STATES[key]["en"]
    text = (f"FOREST {s_txt} | {pool_n(cfg, st)} серверов | "
            f"{route_text(cfg, lang)} | watch OK")
    return text[:limit] if limit else text


# ── окно индикации (эталон: tray.ps1, Render-Frame) ────────────────────────
def notif(proc: dict) -> dict:
    """Заголовок, подзаголовок, проценты, этапы и футер — как в эталоне."""
    proc = proc or {}
    is_restore = str(proc.get("kind") or "restore") == "restore"
    final = bool(proc.get("final"))
    failed = bool(proc.get("failed"))
    try:
        elapsed = float(proc.get("elapsed") or 0.0)
    except Exception:
        elapsed = 0.0
    try:
        eta_s = float(proc.get("eta_s") or 0.0)
    except Exception:
        eta_s = 0.0
    try:
        pct = int(proc.get("pct") or 0)
    except Exception:
        pct = 0
    if failed:
        title = TITLE_FAILED
    elif final:
        title = TITLE_RESTORE_FINAL if is_restore else TITLE_SWITCH_FINAL
    else:
        title = TITLE_RESTORE if is_restore else TITLE_SWITCH
    if failed:
        sub = SUB_FAILED
    elif final:
        # «0.00 с» не печатаем: если время неизвестно, числа просто нет
        sub = (f"связь подтверждена · {elapsed:.2f} с" if elapsed > 0
               else "связь подтверждена")
    elif is_restore:
        sub = SUB_RESTORE
    else:
        sub = SUB_SWITCH
    steps = []
    for s in (proc.get("steps") or []):
        steps.append({"nm": str(s.get("nm") or ""),
                      "t": float(s.get("t") or 0.0),
                      "done": bool(s.get("done")),
                      "failed": bool(s.get("failed"))})
    if failed:
        # «0.00 с» не печатаем: нет замера — нет числа
        footer = (f"попытка заняла {elapsed:.2f} с · окно закроется через 4 с"
                  if elapsed > 0 else
                  "попытка не удалась · окно закроется через 4 с")
    elif final:
        footer = (f"восстановлено за {elapsed:.2f} с · окно закроется через 4 с"
                  if elapsed > 0 else "восстановлено · окно закроется через 4 с")
    else:
        footer = (f"прошло {elapsed:.2f} с · ожидание ~{eta_s:.1f} с"
                  if elapsed > 0 else
                  (f"ожидание ~{eta_s:.1f} с" if eta_s > 0
                   else "наблюдение включено"))
    return {"title": title, "sub": sub, "pct": max(0, min(100, pct)),
            "steps": steps, "footer": footer, "elapsed": elapsed,
            "eta_s": eta_s, "final": final, "failed": failed,
            "kind": "restore" if is_restore else "switch",
            # ДОБАВЛЕНО (состав эталона не меняем): те же числа в миллисекундах —
            # их просил показывать пользователь, а эталон показывал только секунды.
            "elapsed_ms": int(round(elapsed * 1000.0)),
            "eta_ms": int(round(eta_s * 1000.0))}


def step_lines(proc: dict) -> list:
    """Список этапов строками: '✓ Обрыв обнаружен (пульс порта)   0.05 с'."""
    out = []
    for s in (notif(proc).get("steps") or []):
        mark = "х" if s["failed"] else ("v" if s["done"] else "·")
        out.append(f"{mark} {s['nm']}   {s['t']:.2f} с")
    return out


def step_lines_ms(proc: dict) -> list:
    """Список этапов строками с длительностью В МИЛЛИСЕКУНДАХ (как просил
    пользователь): '✓ Обрыв обнаружен (пульс порта)   50 мс'.

    Состав данных тот же, что в :func:`step_lines` (эталон), меняется только
    единица измерения — секунды эталона тут же, рядом, в скобках.
    """
    out = []
    for s in (notif(proc).get("steps") or []):
        mark = "х" if s["failed"] else ("✓" if s["done"] else "·")
        ms = int(round(s["t"] * 1000.0))
        out.append(f"{mark} {s['nm']}   {ms} мс ({s['t']:.2f} с)")
    return out


def steps_data(proc: dict) -> list:
    """Этапы процесса СТРУКТУРОЙ — для карточки уведомления (1:1 с макетом).

    ``[{'nm', 'ms', 'done', 'failed', 'at'}]``: время каждого этапа В
    МИЛЛИСЕКУНДАХ, ``at`` — доля общего времени (время этапа / время последнего
    этапа). По ``at`` карточка решает, когда этап показать: ровно так же считает
    макет (``at = ms / TOTAL``), поэтому порядок и моменты появления совпадают.
    """
    steps = notif(proc).get("steps") or []
    total = max([float(s["t"]) for s in steps] or [0.0])
    out = []
    for s in steps:
        ms = int(round(float(s["t"]) * 1000.0))
        at = (float(s["t"]) / total) if total > 0 else 0.0
        out.append({"nm": s["nm"], "ms": ms, "done": bool(s["done"]),
                    "failed": bool(s["failed"]),
                    "at": max(0.0, min(1.0, at))})
    return out


def steps_total_ms(proc: dict) -> int:
    """Общая длительность процесса для полосы времени: время последнего этапа."""
    steps = notif(proc).get("steps") or []
    return int(round(max([float(s["t"]) for s in steps] or [0.0]) * 1000.0))


def recovery_ms_text(proc: dict, lang: str = "ru") -> str:
    """ИТОГОВОЕ ВРЕМЯ ВОССТАНОВЛЕНИЯ СВЯЗИ — и в секундах, и в миллисекундах."""
    n = notif(proc)
    ms = n["elapsed_ms"]
    if lang == "en":
        return f"recovered in {n['elapsed']:.2f} s ({ms} ms)"
    return f"восстановлено за {n['elapsed']:.2f} с ({ms} мс)"


# ── сведения для меню / «Открыть статус» ──────────────────────────────────
def recovery_text(st: dict, lang: str = "ru") -> str:
    """ВРЕМЯ ВОССТАНОВЛЕНИЯ СВЯЗИ — то, что показывал эталон.

    Берём из состояния: ``last_restore_s`` пишет сторож в момент успешного
    восстановления (``tray_status.finish_proc``); если восстановления ещё не
    было, честно говорим, что данных нет.
    """
    st = st or {}
    try:
        s = st.get("last_restore_s")
    except Exception:
        s = None
    if s is None:
        try:
            proc = st.get("proc") or {}
            if proc.get("final") and not proc.get("failed"):
                s = proc.get("elapsed")
        except Exception:
            s = None
    if s is None:
        return "время восстановления связи: данных ещё нет" if lang == "ru" \
            else "link recovery time: no data yet"
    try:
        v = float(s)
    except Exception:
        return str(s)
    ts = ""
    try:
        t = float(st.get("last_restore_ts") or 0)
        if t:
            ts = " (" + time.strftime("%H:%M:%S", time.localtime(t)) + ")"
    except Exception:
        ts = ""
    return (f"время восстановления связи: {v:.2f} с{ts}" if lang == "ru"
            else f"link recovery time: {v:.2f} s{ts}")


def info_lines(cfg: dict, st: dict, lang: str = "ru") -> list:
    """Полный состав сведений (для меню и «Сведения») — по эталону.

    Порядок и формулировки взяты из подсказки значка и окна индикации эталона:
    состояние, серверы, маршрут, сторож, задержка, время восстановления,
    при провале/переключении — причина и текущая фаза с этапами.
    """
    st = st or {}
    ru = (lang == "ru")
    key = state_key(cfg, st)
    rows = []
    rows.append(("состояние связи", STATES[key]["ru"] if ru else STATES[key]["en"]))
    rows.append(("серверов в пуле", str(pool_n(cfg, st))))
    rows.append(("маршрут", route_text(cfg, lang)))
    try:
        age = age_of(st)
    except Exception:
        age = -1
    if age < 0:
        rows.append(("сторож", "нет данных" if ru else "no data"))
    elif age > STALE_S:
        rows.append(("сторож", ("молчит {:.0f} с".format(age)) if ru
                     else ("silent {:.0f} s".format(age))))
    else:
        rows.append(("сторож", ("отвечал {:.0f} с назад".format(age)) if ru
                     else ("last seen {:.0f} s ago".format(age))))
    rtt = st.get("rtt")
    if rtt not in (None, "", 0):
        rows.append(("задержка (RTT)", f"{rtt} мс" if ru else f"{rtt} ms"))
    rows.append((recovery_text(st, lang).split(":")[0],
                 recovery_text(st, lang).split(":", 1)[-1].strip()))
    proc = st.get("proc") or {}
    if proc:
        n = notif(proc)
        rows.append(("процесс", ("восстановление" if n["kind"] == "restore"
                                 else "переключение сервера") if ru
                     else n["kind"]))
        rows.append(("причина", "обрыв связи" if str(proc.get("cause")) == "outage"
                     else str(proc.get("cause") or "—") ))
        rows.append(("фаза", str(proc.get("phase") or n["title"])))
        rows.append(("прошло", f"{n['elapsed']:.2f} с" if ru
                     else f"{n['elapsed']:.2f} s"))
        for line in step_lines(proc):
            rows.append(("", line))
    if str(st.get("state") or "") == "off" or key == "off":
        rows.append(("прокси", "выключен вручную — автозапуск не мешает" if ru
                     else "switched off by hand — autostart keeps off"))
    return rows


def info_text(cfg: dict, st: dict, lang: str = "ru") -> str:
    """Сведения одной строкой (подсказка значка + ключевые поля)."""
    rows = info_lines(cfg, st, lang)
    body = " · ".join(f"{k}: {v}" if k else v for k, v in rows)
    return tip(cfg, st, limit=0, lang=lang) + "\n" + body
