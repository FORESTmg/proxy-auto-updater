# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
# -*- coding: utf-8 -*-
"""Канал «скрипт -> значок трея»: одно состояние + текущий процесс.

Сторож пишет сюда всё, что нужно для индикации (значок + окно уведомления),
а трей читает файл и НЕ делает собственных сетевых проверок: правду знает
только сторож, дублирование давало отставание индикации на 10 с.

Файл маленький, пишется атомарно (tmp -> os.replace): трей читает его каждые
~0.2 с по mtime, поэтому запись должна быть либо целиком, либо никакой.

Схема:
    ts          время последней записи (для определения «сторож мёртв»)
    state       ok | switch | down | restoring
    server      активный сервер, rtt, speed, pool — для подсказки значка
    tip         готовая строка подсказки (<=63 символов, лимит Windows)
    proc        текущий процесс для окна-прогрессбара либо None:
                  id, kind (restore|switch), cause (outage|scheduled),
                  started, eta_s, pct, phase, elapsed, final,
                  steps [{nm, t, done}]
"""
import json
import os
import time

import log

_lg = log.get_logger()
NAME = "tray_status.json"


def _cfg_or_default(cfg) -> dict:
    """cfg может быть None: длинный цикл (xraytest) не знает об индикации, а
    этапы индикации должны продвигаться и оттуда. По умолчанию — каталог
    состояния программы (Windows: %LOCALAPPDATA%\\ForestProxyUpdater\\state,
    POSIX: $XDG_STATE_HOME/forest-proxy-updater), НЕ каталог исходников: иначе
    в репозитории появлялся бы личный state/."""
    if isinstance(cfg, dict) and cfg.get("paths", {}).get("state_dir"):
        return cfg
    root = os.environ.get("FOREST_ROOT")
    if not root:
        if os.name != "nt":
            xdg = os.environ.get("XDG_STATE_HOME")
            if not xdg or not os.path.isabs(xdg):
                xdg = os.path.join(os.path.expanduser("~"), ".local", "state")
            root = os.path.join(xdg, "forest-proxy-updater")
            return {"paths": {"state_dir": root}}
        la = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"),
                                                            ".local", "share")
        root = os.path.join(la, "ForestProxyUpdater")
    return {"paths": {"state_dir": os.path.join(root, "state")}}


def status_path(cfg: dict) -> str:
    return os.path.join(_cfg_or_default(cfg)["paths"]["state_dir"], NAME)


def state_dirs(cfg: dict) -> list:
    """Все каталоги, где МОЖЕТ лежать состояние, — в порядке приоритета.

    Зачем список: служба и помощник интерфейса могут получить РАЗНОЕ окружение
    (служба задаёт ``XDG_STATE_HOME``/``FOREST_ROOT`` в юните, помощник
    поднимается в сеансе пользователя и наследует уже своё). Когда пути
    расходились, значок читал несуществующий файл и показывал «сторож молчит
    (нет данных)», хотя служба писала свежий статус рядом. Поэтому читаем ВСЕ
    известные места и берём самый свежий файл, а пишем всегда в основной.
    """
    out = []

    def add(d):
        if not d:
            return
        try:
            d = os.path.abspath(d)
        except Exception:
            return
        if d not in out:
            out.append(d)

    try:
        add((cfg or {}).get("paths", {}).get("state_dir"))
    except Exception:
        pass
    try:
        add(_cfg_or_default(cfg)["paths"]["state_dir"])
    except Exception:
        pass
    root = os.environ.get("FOREST_ROOT")
    if root:
        add(os.path.join(root, "state"))
    if os.name != "nt":
        # системный каталог службы (юнит: StateDirectory + XDG_STATE_HOME)
        add(os.path.join("/var/lib", "forest-proxy-updater", "state",
                         "forest-proxy-updater"))
        add(os.path.join("/var/lib", "forest-proxy-updater", "state"))
        add(os.path.join("/var/lib", "forest-proxy-updater"))
        add("/run/forest-proxy-updater")
        xdg = os.environ.get("XDG_STATE_HOME")
        if not xdg or not os.path.isabs(xdg):
            xdg = os.path.join(os.path.expanduser("~"), ".local", "state")
        add(os.path.join(xdg, "forest-proxy-updater"))
        add(os.path.join(xdg, "forest-proxy-updater", "state"))
    return out


def _read_file(p: str) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _stamp(st: dict, path: str) -> float:
    """«Возраст» записи: поле ts, иначе время изменения файла."""
    try:
        ts = float(st.get("ts") or 0)
    except Exception:
        ts = 0.0
    if ts > 0:
        return ts
    try:
        return float(os.path.getmtime(path))
    except Exception:
        return 0.0


def read_status(cfg: dict) -> dict:
    """Читает состояние — САМЫЙ СВЕЖИЙ из всех известных каталогов.

    Пустой словарь — если ни одного файла нет (или все битые). В результат
    добавляются служебные поля ``_path`` и ``_mtime``: по ним видно, какой
    именно файл прочитан (это печатает диагностика и пишется в журнал).
    """
    best, best_ts, best_p = {}, -1.0, ""
    for d in state_dirs(cfg):
        p = os.path.join(d, NAME)
        if not os.path.isfile(p):
            continue
        st = _read_file(p)
        if not st:
            continue
        ts = _stamp(st, p)
        if ts > best_ts:
            best, best_ts, best_p = st, ts, p
    if not best:
        return {}
    best = dict(best)
    best["_path"] = best_p
    try:
        best["_mtime"] = float(os.path.getmtime(best_p))
    except Exception:
        best["_mtime"] = 0.0
    return best


def write_status(cfg: dict, state: str = None, proc: dict = None,
                 tip: str = None, drop_proc: bool = False, **fields) -> None:
    """Атомарно обновляет состояние, не теряя прошлые поля.

    drop_proc=True — убрать окно-прогрессбар (процесс завершён).
    """
    p = status_path(cfg)
    # читаем ИМЕННО свой файл: read_status() отдаёт самый свежий из всех
    # каталогов и добавляет служебные поля — в запись они попадать не должны
    d = _read_file(p)
    for k in ("_path", "_mtime"):
        d.pop(k, None)
    d["ts"] = time.time()
    if state:
        d["state"] = state
    for k, v in fields.items():
        d[k] = v
    if tip is not None:
        d["tip"] = tip[:63]
    if drop_proc:
        old = d.get("proc") or {}
        # финал должен успеть показаться: сторож убирает процесс сразу после
        # восстановления, а трей держит итог ~4 с — без этой отсрочки окно
        # исчезало, не показав завершённую последнюю стадию
        if old.get("final") and (time.time() - float(old.get("fin") or 0)) < 5.0:
            drop_proc = False
        else:
            d["proc"] = None
    elif proc is not None:
        d["proc"] = proc
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        _lg.warn(f"tray_status: запись не удалась: {e}")


def make_proc(kind: str, cause: str, steps: list, eta_s: float,
              phase: str = "", pid: str = None) -> dict:
    """Создаёт описание процесса. steps — [(название, время_от_начала)]."""
    now = time.time()
    return {
        "id": pid or f"{now:.3f}",
        "kind": kind,              # restore | switch
        "cause": cause,            # outage | scheduled
        "started": now,
        "eta_s": float(eta_s),
        "pct": 0,
        "phase": phase or (steps[0][0] if steps else ""),
        "elapsed": 0.0,
        "final": False,
        "steps": [{"nm": nm, "t": float(t), "done": False} for nm, t in steps],
    }


def step_proc(cfg: dict, proc: dict, index: int = None, phase: str = None,
              final: bool = False, pct: float = None) -> dict:
    """Отмечает этап процесса пройденным и пересчитывает проценты.

    index=None — просто обновить фазу/процент, не отмечая новый этап.
    """
    if not proc:
        return proc
    steps = proc.get("steps") or []
    if index is not None and 0 <= index < len(steps):
        steps[index]["done"] = True
        if index + 1 < len(steps):
            phase = phase or steps[index + 1]["nm"]
    if final:
        for s in steps:
            s["done"] = True
        phase = phase or "Готово"
    proc["steps"] = steps
    if phase:
        proc["phase"] = phase
    proc["elapsed"] = max(0.0, time.time() - float(proc.get("started") or 0))
    if pct is not None:
        proc["pct"] = max(0, min(100, int(pct)))
    else:
        done = sum(1 for s in steps if s.get("done"))
        proc["pct"] = int(done / len(steps) * 100) if steps else 0
    proc["final"] = bool(final)
    write_status(cfg, proc=proc)
    return proc


def finish_proc(cfg: dict, proc: dict, state: str, tip: str = None) -> None:
    """Завершает процесс: помечает финал и убирает окно (трей покажет итог).

    Дополнительно записываем ВРЕМЯ ВОССТАНОВЛЕНИЯ СВЯЗИ (``last_restore_s`` /
    ``last_restore_ts``): эталон показывал его в окне индикации, и оно должно
    остаться доступным в подсказке/меню и после того, как окно закрылось.
    """
    if proc:
        proc = dict(proc)
        proc["final"] = True
        proc["pct"] = 100
        proc["elapsed"] = max(0.0, time.time() - float(proc.get("started") or 0))
        for s in proc.get("steps") or []:
            s["done"] = True
        proc["phase"] = "Готово"
        proc["fin"] = time.time()
    extra = {}
    if proc and str(state) == "ok":
        extra = {"last_restore_s": round(float(proc.get("elapsed") or 0.0), 2),
                 "last_restore_ts": time.time(),
                 "last_restore_kind": proc.get("kind")}
    write_status(cfg, state=state, proc=proc, tip=tip, **extra)

def fail_proc(cfg: dict, proc: dict, state: str, tip: str = None,
              reason: str = "") -> None:
    """Завершает процесс ПРОВАЛОМ.

    Без этого окно навсегда застывало на середине (например, «Подтверждение
    трафика…», 75%): статус писался только в ветке успеха, и последняя стадия
    не показывалась никогда.
    """
    if not proc:
        return
    proc = dict(proc)
    proc["final"] = True
    proc["failed"] = True
    proc["elapsed"] = max(0.0, time.time() - float(proc.get("started") or 0))
    steps = proc.get("steps") or []
    for s in steps:
        if not s.get("done"):
            s["failed"] = True
            break
    proc["steps"] = steps
    proc["phase"] = ("Не удалось: " + reason) if reason else "Не удалось"
    proc["fin"] = time.time()
    write_status(cfg, state=state, proc=proc, tip=tip)


def advance(cfg: dict, index: int, phase: str = None, final: bool = False,
            pct: float = None) -> None:
    """Продвинуть текущий процесс, если он есть в статусе.

    Вызывается из длинного цикла пересборки пула: цикл не знает о трее, а
    индикация должна идти по его фазам.
    """
    d = read_status(cfg)
    proc = d.get("proc")
    if not proc:
        return
    step_proc(cfg, proc, index=index, phase=phase, final=final, pct=pct)


def beat(cfg: dict, state: str, tip: str = None, **fields) -> None:
    """Heartbeat: сторож жив. Держит возраст статуса свежим, не трогая процесс.

    Без heartbeat трей через 90 с считал сторожа молчащим и показывал «нет
    данных», хотя связь могла быть в порядке.
    """
    write_status(cfg, state=state, tip=tip, **fields)
