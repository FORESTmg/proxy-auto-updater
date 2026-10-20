# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Единый процесс-супервизор FOREST's Proxy Auto-updater (GPL-3.0).

Одна программа вместо трёх: внутри — потоки «сторож» (watch), «гвард»
(guard), «обновление» (update) и «трей». Трей визуальный пока не реализован
(следующий этап, Win32/ctypes) — вместо него поток следит за каналом
состояния, см. ``tray.py``.

Команды:
  --install     раскладка в %LOCALAPPDATA%\\ForestProxyUpdater, ярлык,
                задача Планировщика заданий (автономный надзор), первый цикл
  --uninstall   снять всё (задачу надзора, ярлык, файлы) и вернуть системный
                прокси в исходное состояние
  --run         обычный запуск с треем (супервизор до остановки)
  --ensure      поднять супервизор, если он не работает (для надзора); тихо
  --once        один полный цикл подбора серверов
  --guard       одна быстрая самопроверка/восстановление
  --status      показать текущее состояние
  --selftest    прогнать автотесты
  --watch       только сторож (совместимость)
  --dry-run     цикл без записи и запуска ядра
  --auto        синоним --once
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time

# ── Выбор платформенного бэкенда клиента ──────────────────────────────
# Windows: clientwrite.py — своё ядро xray.exe дочерним процессом, системный
#          прокси через реестр WinINET, процессы через WinAPI.
# POSIX:   clientwrite_posix.py — ТОТ ЖЕ API (тот же модуль-сердцевина плюс
#          POSIX-примитивы: /proc вместо CIM, os.kill вместо TerminateProcess,
#          GSettings вместо WinINET). Windows-поведение не меняется.
if os.name == "nt":
    import clientwrite as CW
else:
    import clientwrite_posix as CW

import config as CFG
import safestdio as SD
import i18n
import log

APP_RUN_VALUE = "ForestProxyUpdater"


# Запись оригинального (юникодного) текста отчёта: включается на время --status.
# Определена ДО :func:`_say` — её читает первая же печать.
_REPORT = None


def _say(text="") -> None:
    """Печать в stdout через безопасный слой (см. src/safestdio.py).

    Оконная сборка не имеет консоли, а её «кодировка» на Windows — cp1251:
    печать символа вне cp1251 давала UnicodeEncodeError и МОДАЛЬНОЕ ОКНО
    PyInstaller на рабочем столе. Никакая печать здесь упасть не может.

    Побочный эффект: :func:`safestdio.say` заменяет не-ASCII на ``?``. Поэтому
    рядом ведётся ЗАПИСЬ оригинальных строк (:data:`_REPORT`) — из неё пишется
    UTF-8-отчёт ``state/status.txt`` (иначе кириллица попадала в файл как «?»).
    """
    SD.say(text)
    if _REPORT is not None:
        try:
            _REPORT.append(str(text))
        except Exception:
            pass


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ══════════════════════════════════════════ платформенный слой Linux ═══
# Модуль linux/posix_platform.py содержит всё, что на Windows делает WinAPI:
# установку (systemd --user вместо реестра/ярлыка), индикацию (файл состояния
# + notify-send вместо трея) и разбор «--status». Импортируется лениво, чтобы
# на Windows этот код не загружался вовсе.
def linux_dir() -> str:
    """Каталог linux/ с платформенными модулями: репозиторий либо бандл."""
    cands = [
        os.path.join(CFG.PROJECT_DIR, "linux"),
        os.path.join(getattr(sys, "_MEIPASS", "") or "", "linux"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "linux"),
    ]
    for d in cands:
        if d and os.path.isdir(d):
            return d
    return ""


def posix_platform():
    """Ленивый импорт linux/posix_platform.py (None на Windows/если нет модуля)."""
    if os.name == "nt":
        return None
    d = linux_dir()
    if d and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import posix_platform as _pp
    except Exception as e:
        log.get_logger().error(f"Платформенный модуль Linux недоступен: {e}")
        return None
    return _pp


# ══════════════════════════════════════════ блокировка цикла ═══
def _lock_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "lock.json")


def acquire_lock(mode: str, stale_s: float = 4 * 3600, cfg: dict = None) -> bool:
    c = cfg or CFG.load_config()
    p = _lock_path(c)
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if time.time() - d.get("ts", 0) < stale_s:
                return False
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "mode": mode, "ts": time.time()}, f)
        return True
    except Exception:
        return True


def release_lock(cfg: dict = None):
    c = cfg or CFG.load_config()
    try:
        os.unlink(_lock_path(c))
    except Exception:
        pass


def _proc_exists(exe_path: str) -> bool:
    name = os.path.basename(exe_path or "xray.exe")
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                           capture_output=True, timeout=15, creationflags=_no_window())
        return name.lower().encode() in (r.stdout or b"").lower()
    except Exception:
        return False


# ══════════════════════════════════════════ полный цикл ═══
def run_cycle(cfg: dict, dry: bool = False, force_discovery: bool = False,
              pool_force: bool = False) -> int:
    lg = log.get_logger()
    import parser as P
    import sources as SRC
    import xraytest as XT
    import netfilters as NF

    client = CFG.find_client(cfg)
    if not client.get("core"):
        lg.error("Ядро xray не найдено — цикл невозможен. Скачайте официальный "
                 "релиз Xray-core и положите xray.exe в bin\\xray рядом с программой.")
        return 2
    CW.ensure_port_free(client, cfg)
    lg.info("=" * 70)
    lg.info(f"Цикл FOREST's Proxy Auto-updater | режим: {client.get('mode')} | "
            f"ядро: {client.get('core')}")
    lg.info(f"Порт клиента: {client.get('socks_port')} | стор: {client.get('db')}")

    proxies = CFG.proxy_for_fetch(cfg)
    st = CW.StateStore(cfg)
    sm = SRC.SourceManager(cfg)

    full_hours = float(cfg.get("cycle", {}).get("full_refresh_hours", 0.5))
    last_full = st.data.get("last_full_refresh", 0)
    force_full = force_discovery or (time.time() - last_full) >= full_hours * 3600
    if force_full:
        lg.info("ПОЛНЫЙ цикл: обновление списков (раз в %.0f ч)" % full_hours)
    else:
        lg.info("БЫСТРЫЙ цикл: только перетест имеющихся серверов")

    cur_ok, cur_rtt = CW.probe_client(client, timeout=12)
    cur_active = CW.read_active_id(client["cfg"])
    lg.info(f"Текущий активный: {cur_active} | работает: {cur_ok} (rtt={cur_rtt} мс)")
    if cur_ok:
        st.data["last_ok"] = time.time()
    if cur_active and cur_ok and not dry:
        st.data["baseline_active"] = cur_active
        st.save()

    # --- 1. источники ---
    changed = []
    if force_full:
        sm.discover_if_due(proxies, force=force_discovery or False)
        lg.info(f"Источников в списке: {len(sm.data['sources'])}")
        changed = sm.refresh(proxies)
    changed += sm.refresh_fixed(proxies)

    # --- 3. кандидаты ---
    rows = CW.read_db_rows(client["db"])
    pool = []
    if changed:
        for src, body in changed:
            found = P.parse_batch(body, source=src["repo"])
            lg.info(f"  {src['repo']}: распознано {len(found)} серверов")
            pool += found
    pool_user = []
    for r in rows:
        s = CW.row_to_server(r)
        if s is not None:
            s._is_auto = (r.get("Remarks") or "").startswith(CW.AUTO_TAG)
            s._row_index_id = str(r["IndexId"])
            pool_user.append(s)
    known_ids = {s.index_id for s in pool_user}
    pool = pool_user + [s for s in pool if s.index_id not in known_ids]
    pool = P.dedupe(pool)

    cap_pre = int(cfg["test"].get("max_candidates", 400))
    if len(pool) < cap_pre:
        lg.warn(f"Пул кандидатов мал ({len(pool)} < {cap_pre}) — добираю из кэша")
        try:
            extra = list(_load_pool_cache(cfg))
            for name, body in sm.load_bodies():
                extra += P.parse_batch(body, source=name)
            have = {s.index_id for s in pool}
            extra = [s for s in extra if s.index_id not in have]
            extra = P.dedupe(extra)
            import random as _rnd
            _rnd.shuffle(extra)
            pool = pool + extra[:max(0, cap_pre - len(pool))]
            lg.info(f"  добрано кандидатов из кэша: {len(pool) - len(pool_user)}")
        except Exception as e:
            lg.warn(f"  добор из кэша не удался: {e}")

    cap = int(cfg["test"].get("max_candidates", 400))
    if len(pool) > cap:
        import random as _rnd
        keep_user = pool_user[:cap]
        keep_ids = {id(s) for s in keep_user}
        rest = [s for s in pool if id(s) not in keep_ids]
        prio_kw = ("rjsxrd", "pawdroid", "fixed")
        prio = [s for s in rest
                if any(k in (getattr(s, "source", "") or "").lower() for k in prio_kw)]
        prio_ids = {id(x) for x in prio}
        other = [s for s in rest if id(s) not in prio_ids]
        _rnd.shuffle(other)
        room = max(0, cap - len(keep_user))
        take_prio = prio[:room]
        pool = keep_user + take_prio + other[:max(0, room - len(take_prio))]
        lg.warn(f"Кандидатов > {cap}; взято {len(pool)}")
    lg.info(f"Кандидатов на тест: {len(pool)}")
    try:
        _save_pool_cache(cfg, pool)
    except Exception as e:
        lg.warn(f"  pool cache save: {e}")

    if not pool:
        lg.error("Нет ни одного кандидата (источники пусты/не парсятся)")
        return 0 if cur_ok else 1

    # --- 4. фильтр Cloudflare + тесты ---
    try:
        before_cf = len(pool)
        pool, cf_drop = NF.drop_cloudflare(pool, cfg)
        if cf_drop:
            lg.info(f"Исключено Cloudflare-узлов: {len(cf_drop)} "
                    f"(осталось {len(pool)} из {before_cf})")
    except Exception as _e:
        lg.warn(f"Фильтр Cloudflare не сработал: {_e}")
    healthy = XT.run_tests(pool, cfg, client["core"])
    if not healthy:
        reserve = [s for s in (getattr(XT, "LAST_RTT_OK", []) or [])
                   if getattr(s, "rtt_ms", 0)
                   and s.rtt_ms <= float(cfg["test"].get("max_rtt_ms", 1500))]
        reserve.sort(key=lambda s: s.rtt_ms)
        if reserve:
            lg.warn(f"Строгий отбор пуст — резерв по RTT: {len(reserve)} серверов")
            healthy = _reserve_with_speed_check(reserve, cfg, client["core"], lg)
        else:
            lg.error("Рабочих серверов не найдено (и резерва по RTT нет)")
            return 1

    top10 = sorted([s for s in healthy if not getattr(s, "_reserve", False)],
                   key=lambda s: s.rtt_ms)[:10]
    if top10:
        lg.info("Повторный RTT топ-10 (стабильность)...")
        XT.retest_rtt(top10, cfg, client["core"])
    healthy = [s for s in healthy if s.ok]
    ranked = XT.best_ranked(healthy, int(cfg["limits"]["max_servers"]))
    try:
        filtered = CW.bl_filter(cfg, ranked)
        if filtered:
            ranked = filtered
    except Exception as e:
        lg.warn(f"bl_filter: {e}")

    reserve_min = int(cfg["test"].get("reserve_min", 12))
    if len(ranked) < reserve_min:
        have = {s.index_id for s in ranked}
        max_rtt = float(cfg["test"].get("max_rtt_ms", 1500))
        added = 0
        for s in list(getattr(XT, "LAST_RTT_OK", []) or []):
            if len(ranked) >= reserve_min:
                break
            if s.index_id in have or not getattr(s, "rtt_ms", 0) or s.rtt_ms > max_rtt:
                continue
            s._reserve = True
            s.ok = True
            have.add(s.index_id)
            ranked.append(s)
            added += 1
        if added:
            lg.warn(f"Добавлен резерв по RTT: +{added} (итого {len(ranked)})")

    # --- кап по подсетям ---
    try:
        _cap = int(cfg.get("diversity", {}).get("per_subnet_cap", 1) or 0)
        _floor = int(cfg.get("diversity", {}).get("min_pool_after_cap", 12) or 0)
        _maxcap = int(cfg.get("diversity", {}).get("max_cap_relax", 4) or 0)
        before = len(ranked)
        capped, used = NF.pick_diverse_floor(ranked, int(cfg["limits"]["max_servers"]),
                                             cap=_cap, floor=_floor,
                                             max_cap=max(_maxcap, _cap))
        if capped:
            ranked = capped
        if used != _cap:
            lg.warn(f"Страховка пула: кап по подсетям ослаблен {_cap} -> {used}; "
                    f"итог {len(ranked)} серверов")
        if len(ranked) != before:
            lg.info(f"Разнообразие пула: {len(ranked)} из {before} "
                    f"(подсетей {len(NF.subnet_stats(ranked))}, кап {used})")
    except Exception as _e:
        lg.warn(f"Кап по подсетям не сработал: {_e}")

    lg.info("Топ-10 по скорости:")
    for i, s in enumerate(ranked[:10]):
        lg.info(f"  #{i+1} {s.summary()} | rtt={s.rtt_ms:.0f} мс "
                f"| speed={s.speed_kbps/1024:.1f} МБ/с")

    try:
        CW.update_hotcache(client, cfg, ranked)
    except Exception as e:
        lg.warn(f"update_hotcache: {e}")

    # --- 5. выбор активного ---
    def eff(s):
        return getattr(s, "_row_index_id", None) or s.index_id

    top1 = ranked[0]
    cur_srv_obj = next((s for s in pool if eff(s) == str(cur_active)), None)
    keep_current = False
    if cur_ok and cur_srv_obj is not None and cur_srv_obj.ok:
        prefer = bool(cfg.get("activation", {}).get("prefer_current_if_healthy", True))
        better = (top1.speed_kbps > cur_srv_obj.speed_kbps * 1.3) or \
                 (top1.rtt_ms < cur_srv_obj.rtt_ms * 0.75)
        keep_current = prefer and not better
        if keep_current:
            lg.info("Текущий сервер здоров и конкурентен — оставляю его активным")
    active_id = str(cur_active) if (keep_current and cur_active) else eff(top1)

    auto_servers = []
    for s in ranked:
        if getattr(s, "_is_auto", False) or not hasattr(s, "_row_index_id"):
            auto_servers.append(s)
    new_auto_ids = {s.index_id for s in auto_servers}
    old_auto_ids = {str(r["IndexId"]) for r in rows
                    if (r.get("Remarks") or "").startswith(CW.AUTO_TAG)}
    need_write = new_auto_ids != old_auto_ids
    if cur_active is not None and active_id != str(cur_active):
        need_write = True
    if dry:
        lg.info("[dry-run] Будет записано [AUTO]-серверов: %d, активный: %s"
                % (len(auto_servers), active_id))
        return 0
    if not need_write and not pool_force:
        lg.info("Изменений нет — запись и перезапуск не требуются")
        return 0

    if CW.proxy_off(cfg):
        try:
            n_w = CW.write_auto_group(client["db"], auto_servers, update_stats=True)
            lg.warn(f"Прокси выключен вручную — активация пропущена (записано {n_w})")
        except Exception as e:
            lg.warn(f"Прокси выключен вручную — активация пропущена ({e})")
        return 0

    lg.info("Пишу стор и активирую (режим балансировщика)...")
    active_server = next((s for s in ranked if eff(s) == active_id), ranked[0])
    res = CW.activate(client, auto_servers, active_id, cfg,
                      active_server=active_server, update_stats=True,
                      force_pool=pool_force)
    if res["ok"]:
        lg.info(f"Активация завершена: {res['msg']} (rtt={res.get('rtt')} мс)")
        st.data["last_active"] = active_id
        st.data["last_cycle"] = time.time()
        if force_full:
            st.data["last_full_refresh"] = time.time()
        st.save()
        _write_sub_file(ranked, cfg)
        return 0
    lg.error(f"Активация НЕ удалась: {res['msg']}")
    return 1


def _pool_cache_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "pool_cache.json")


def _load_pool_cache_raw(p: str) -> list:
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return [x for x in d if isinstance(x, str)]
    except Exception:
        return []


def _save_pool_cache(cfg: dict, servers: list, max_links: int = 4000):
    import parser as P
    p = _pool_cache_path(cfg)
    old = _load_pool_cache_raw(p)
    seen = set(old)
    fresh = []
    for s in servers:
        try:
            l = P.to_share_link(s)
        except Exception:
            l = None
        if l and l not in seen:
            seen.add(l)
            fresh.append(l)
    merged = (fresh + old)[:max_links]
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)
    os.replace(tmp, p)


def _load_pool_cache(cfg: dict) -> list:
    import parser as P
    links = _load_pool_cache_raw(_pool_cache_path(cfg))
    if not links:
        return []
    try:
        return P.parse_batch("\n".join(links), source="pool-cache")
    except Exception:
        return []


def _write_sub_file(ranked: list, cfg: dict):
    """Пишет state/subscription_base64.txt (для импорта в любой клиент)."""
    import base64
    import parser as P
    lines = [P.to_share_link(s) for s in ranked]
    text = "\n".join(l for l in lines if l)
    p = os.path.join(cfg["paths"]["state_dir"], "subscription_base64.txt")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(base64.b64encode(text.encode("utf-8")).decode())
        log.get_logger().info(f"Подписка для ручного импорта: {p} ({len(ranked)} серверов)")
    except Exception as e:
        log.get_logger().warn(f"_write_sub_file: {e}")


def _reserve_with_speed_check(reserve: list, cfg: dict, core: str, lg) -> list:
    """Резерв по RTT без измеренной скорости активировать нельзя — меряем скорость."""
    import xraytest as XT
    try:
        import netfilters as NF
    except Exception:
        NF = None
    max_srv = int(cfg["limits"]["max_servers"])
    try:
        cap = int(cfg.get("diversity", {}).get("per_subnet_cap", 1) or 0)
        try_n = int(cfg["test"].get("reserve_verify", 20))
        cand = NF.pick_diverse(reserve, max(try_n, max_srv), cap=cap)
        lg.warn(f"Резерв: проверяю скорость у {len(cand)} узлов...")
        good = XT.verify_speed(cand, cfg, core)
        if good:
            # YouTube-гейт: узел может везти трафик (RTT+скорость в норме), но
            # рвать TLS к youtube.com — именно так «глушители» попадали в пул
            # через резервный путь, и leastPing потом выбирал их (YouTube: 000
            # за ~40 мс при живой связи). Пустой результат — берём как есть.
            yt = XT.youtube_filter(good, cfg, core)
            if yt:
                good = yt
            else:
                lg.warn("Резерв: YouTube не открывается ни через один узел — "
                        "оставляю по скорости (YouTube будет нестабилен)")
        out = good[:max_srv] if good else reserve[:max_srv]
        if not good:
            lg.warn("Резерв: скорость не подтвердилась — беру по RTT (крайний случай)")
    except Exception as e:
        lg.warn(f"Проверка скорости резерва не удалась ({e}) — беру по RTT")
        out = reserve[:max_srv]
    for s in out:
        s.ok = True
        s._reserve = True
    return out


# ══════════════════════════════════════════ гвард ═══
def run_guard(cfg: dict) -> int:
    lg = log.get_logger()
    try:
        if CW.proxy_off(cfg):
            lg.info("Guard: прокси выключен вручную — ничего не поднимаю")
            return 0
    except Exception:
        pass
    client = CFG.find_client(cfg)
    if not client.get("core"):
        lg.error("Guard: ядро xray не найдено")
        return 2
    bal = bool(cfg.get("watch", {}).get("balancer_mode", False))
    if bal:
        try:
            if CW.ensure_balancer(client, cfg, n=int(cfg["watch"].get("balancer_pool_n", 10))):
                ok_b, _rtt = CW.probe_client(client, timeout=6, strict=False)
                if ok_b:
                    return 0
        except Exception as e:
            lg.error(f"Guard: ensure_balancer: {e}")
    else:
        CW.ensure_port_free(client, cfg)
        ok, _rtt = CW.probe_client(client, timeout=6)
        if ok:
            return 0
    if not acquire_lock("guard", stale_s=10 * 60, cfg=cfg):
        lg.warn("Guard: уже идёт другой цикл, пропускаю")
        return 0
    try:
        if bal:
            try:
                if CW.ensure_balancer(client, cfg,
                                      n=int(cfg["watch"].get("balancer_pool_n", 10)),
                                      force=True):
                    ok_b, rtt_b = CW.probe_client(client, timeout=8, strict=False)
                    if ok_b:
                        lg.info(f"Guard OK: балансировщик дал связь (rtt={rtt_b} мс)")
                        return 0
            except Exception as e:
                lg.error(f"Guard: ensure_balancer: {e}")
        res = CW.guard_cycle(client, cfg)
    finally:
        release_lock(cfg)
    if res:
        return 0
    lg.warn("Guard: не помогло -> полный цикл")
    try:
        r2 = run_cycle(cfg, dry=False, force_discovery=False)
        if r2 == 0:
            ok9, rtt9 = CW.probe_client(client, timeout=8)
            if ok9:
                lg.info(f"Guard: полный цикл восстановил связь (rtt={rtt9} мс)")
                return 0
    except Exception as e:
        lg.error(f"Guard: экстренный цикл не удался: {e}")
    lg.error("Guard: связь не восстановлена")
    return 1


# ══════════════════════════════════════════ супервизор ═══
def _guard_loop(cfg: dict, stop: threading.Event):
    lg = log.get_logger()
    while not stop.is_set():
        try:
            run_guard(cfg)
        except Exception as e:
            lg.error(f"supervisor: guard исключение: {e}")
        stop.wait(300)


def _update_loop(cfg: dict, stop: threading.Event):
    lg = log.get_logger()
    if os.environ.get("FOREST_NO_CYCLE"):
        lg.info("supervisor: цикл обновления отключён (FOREST_NO_CYCLE)")
        return
    import tray_menu as TM          # потребитель триггера «Обновить серверы сейчас»
    cycle_every_s = 30 * 60         # штатный интервал планового цикла
    poll_s = 5.0                    # как часто проверяем state/refresh.flag
    next_due = 0.0
    while not stop.is_set():
        triggered = False
        try:
            triggered = bool(TM.consume_refresh_trigger(cfg))
        except Exception as e:
            lg.warn(f"supervisor: проверка state/refresh.flag: {e}")
        if triggered:
            lg.info("supervisor: запрос из трея «обновить серверы сейчас» — "
                    "запускаю ПОЛНЫЙ цикл обновления (списки + применение пула)")
        if triggered or time.time() >= next_due:
            # Тот же штатный путь, что и плановый цикл: run_cycle под общим
            # lock-ом (не чаще одного цикла одновременно), без окон консоли.
            # Запрос пользователя = настоящий полный цикл: тянем источники
            # (force_discovery) и применяем свежий пул сразу (pool_force) — иначе
            # кнопка «Обновить серверы сейчас» на деле ничего не меняла
            # («БЫСТРЫЙ цикл» + замороженный пул).
            try:
                if acquire_lock("cycle", stale_s=3 * 3600, cfg=cfg):
                    try:
                        run_cycle(cfg, dry=False, force_discovery=bool(triggered),
                                  pool_force=bool(triggered))
                    finally:
                        release_lock(cfg)
                else:
                    lg.warn("supervisor: другой цикл уже выполняется — пропускаю")
            except Exception as e:
                lg.error(f"supervisor: цикл исключение: {e}")
            next_due = time.time() + cycle_every_s
        stop.wait(poll_s)


def run_supervisor(cfg: dict) -> int:
    """Один процесс: сторож + гвард + обновление + индикация (до остановки).

    Windows: индикация — значок в области уведомлений (tray.py, WinAPI).
    Linux:   tray.py не работает (WinAPI), поэтому индикация — файл состояния
             state/tray_status.json плюс notify-send для событий (без окон),
             см. linux/posix_platform.py::indicators_loop.
    """
    import watch
    import watchdog as WD
    lg = log.get_logger()
    # Защита от второго экземпляра: живой супервизор этой установки уже есть —
    # второй не запускаем (иначе два ядра на одном порту разорвут связь).
    if not WD.claim_supervisor(cfg):
        lg.info("Супервизор этой установки уже запущен — второй не нужен")
        return 0
    client = CFG.find_client(cfg)
    if not client.get("core"):
        lg.error("Ядро xray не найдено — положите xray рядом с программой "
                 "(bin/xray) или установите в PATH.")
    stop = threading.Event()

    def _full_cycle():
        if os.environ.get("FOREST_NO_CYCLE"):
            return
        if acquire_lock("cycle", stale_s=40 * 60, cfg=cfg):
            try:
                run_cycle(cfg, dry=False)
            finally:
                release_lock(cfg)

    threads = [
        threading.Thread(target=watch.run_watch, name="watch",
                         args=(cfg,), kwargs={"stop_event": stop,
                                              "full_cycle": _full_cycle}, daemon=True),
        threading.Thread(target=_guard_loop, name="guard", args=(cfg, stop), daemon=True),
        threading.Thread(target=_update_loop, name="update", args=(cfg, stop), daemon=True),
    ]
    indicators = "tray"
    if os.name == "nt":
        import tray
        threads.append(threading.Thread(target=tray.run_tray, name="tray",
                                        args=(cfg, stop), daemon=True))
    else:
        pp = posix_platform()
        if pp is not None:
            threads.append(threading.Thread(target=pp.indicators_loop, name="indicators",
                                            args=(cfg, stop), daemon=True))
            indicators = "file+notify-send"
    for t in threads:
        t.start()
    lg.info(f"Супервизор запущен: потоки watch/guard/update/индикация ({indicators})")

    # ограничение времени работы (для автотестов и демонстраций)
    run_s = os.environ.get("FOREST_RUN_SECONDS")
    stop_file = os.path.join(cfg["paths"]["state_dir"], "stop.flag")
    try:
        if run_s:
            deadline = time.time() + float(run_s)
            while time.time() < deadline and not stop.is_set():
                if os.path.isfile(stop_file):
                    break
                time.sleep(0.5)
        else:
            while not stop.is_set():
                if os.path.isfile(stop_file):
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
        try:
            CW.stop_core(cfg, why="supervisor-stop")
        except Exception:
            pass
        try:
            WD.release_supervisor(cfg)
        except Exception:
            pass
        lg.info("Супервизор остановлен")
    return 0


# ══════════════════════════════════════════ автономный надзор ═══
def run_ensure(cfg: dict) -> int:
    """``--ensure``: поднять супервизор, если его нет. Тихо, только в лог.

    Windows — запуск ``--run`` отвязанно от вызывающего процесса.
    Linux — поднять/включить службу systemd (у неё уже Restart=always + таймер).
    """
    if os.name != "nt":
        pp = posix_platform()
        if pp is not None and hasattr(pp, "ensure"):
            rc = pp.ensure(cfg)
            try:                      # на Linux помощник поднимаем так же тихо
                import ui_helper as UH
                UH.ensure(cfg)
            except Exception:
                pass
            return rc
        return 0
    import watchdog
    rc = watchdog.ensure(cfg)
    try:
        # Задача Планировщика зовёт именно --ensure: вместе с программой она
        # поднимает и помощника интерфейса (если он установлен и не выключен).
        import ui_helper as UH
        UH.ensure(cfg)
    except Exception:
        pass
    return rc


def run_watchdog_command(cfg: dict, action: str) -> int:
    """``--watchdog on|off|status``: включить/выключить надзор либо показать его.

    on  — записать watchdog.enabled=true и создать задачу Планировщика;
    off — записать watchdog.enabled=false и УДАЛИТЬ задачу;
    status — честный отчёт: включена ли настройка, есть ли задача, когда она
             последний раз запускалась, работает ли программа сейчас.
    """
    lg = log.get_logger()
    if os.name != "nt":
        # Linux: надзор — это systemd (Restart=always) + таймер; настройка в конфиге.
        pp = posix_platform()
        if action == "status":
            st = {"enabled": bool((cfg.get("watchdog") or {}).get("enabled", True)),
                  "service": (pp.service_state() if pp else "нет модуля"),
                  "supervisor_pid": 0}
            _print_watchdog_status_linux(cfg, st)
            return 0
        if not pp:
            _say("Надзор Linux доступен только на Linux (systemd).")
            return 1
        want = (action == "on")
        _set_config_value(cfg, "watchdog", "enabled", want)
        _say(i18n.t("watchdog.linux_note"))
        return 0

    import watchdog
    if action == "status":
        rep = watchdog.watchdog_report(cfg)
        _print_watchdog_status(cfg, rep)
        return 0
    want = (action == "on")
    ok = _set_config_value(cfg, "watchdog", "enabled", want)
    cfg.setdefault("watchdog", {})["enabled"] = want
    if not ok:
        lg.warn("watchdog: настройка не записана в config.json — применяю как есть")
    res = watchdog.apply_watchdog(cfg)
    if want:
        _say(i18n.t("watchdog.turned_on") if res["ok"] else
              i18n.t("watchdog.turn_on_failed", err=res["detail"]))
    else:
        _say(i18n.t("watchdog.turned_off") if res["ok"] else
              i18n.t("watchdog.turn_off_failed", err=res["detail"]))
    _say(i18n.t("watchdog.off_warning") if not want else i18n.t("watchdog.on_note"))
    return 0 if res["ok"] else 1


def _set_config_value(cfg: dict, section: str, key: str, value) -> bool:
    """Пишет cfg[section][key] в config.json атомарно. True при успехе."""
    p = cfg.get("_config_path") or ""
    data = {}
    try:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    node = data
    if not isinstance(node.get(section), dict):
        node[section] = {}
    node[section][key] = value
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception as e:
        log.get_logger().warn(f"watchdog: запись config.json ({p}): {e}")
        return False


def _print_watchdog_status(cfg: dict, rep: dict) -> None:
    st, task = rep["settings"], rep["task"]
    _say("=" * 60)
    _say(i18n.t("watchdog.status_title"))
    _say("=" * 60)
    _say(i18n.t("watchdog.setting") + ": " +
          (i18n.t("watchdog.enabled") if st["enabled"] else i18n.t("watchdog.disabled")))
    _say(i18n.t("watchdog.interval") % st["interval_min"])
    _say(i18n.t("watchdog.task_name") + f": {st['task_name']}")
    if task.get("exists"):
        _say(i18n.t("watchdog.task_present") + ": " + i18n.t("watchdog.yes") +
              f"  [{task.get('state', '')}]")
        _say(i18n.t("watchdog.task_action") + f": {task.get('action', '')}")
        _say(i18n.t("watchdog.last_run") + f": {task.get('last_run', '') or '—'}")
        _say(i18n.t("watchdog.next_run") + f": {task.get('next_run', '') or '—'}")
    else:
        _say(i18n.t("watchdog.task_present") + ": " + i18n.t("watchdog.no"))
    running = (i18n.t("watchdog.program_running") if rep["supervisor_alive"]
               else i18n.t("watchdog.program_stopped"))
    if not rep["supervisor_alive"]:
        extra = ""
    elif rep["supervisor_pid"]:
        extra = f" (pid {rep['supervisor_pid']})"
    else:
        extra = " (замок занят)"
    _say(i18n.t("watchdog.program") + ": " + running + extra)
    # Честный итог: включено и задача есть / включено, а задачи нет / выключено.
    if not st["enabled"]:
        _say(i18n.t("watchdog.off_warning"))
    elif not task.get("exists"):
        _say(i18n.t("watchdog.task_missing_warn"))
    else:
        _say(i18n.t("watchdog.on_note"))


def _print_watchdog_status_linux(cfg: dict, st: dict) -> None:
    _say("=" * 60)
    _say(i18n.t("watchdog.status_title"))
    _say("=" * 60)
    _say(i18n.t("watchdog.setting") + ": " +
          (i18n.t("watchdog.enabled") if st["enabled"] else i18n.t("watchdog.disabled")))
    _say(i18n.t("watchdog.service") + f": {st.get('service', '')}")
    _say(i18n.t("watchdog.linux_note"))


# ══════════════════════════════════════════ установка / удаление ═══
APP_EXE_NAME = APP_RUN_VALUE + ".exe"

# Ветка реестра автозапуска; для тестов переопределяется FOREST_REG_RUN_KEY,
# чтобы НЕ трогать реальный автозапуск пользователя.
RUN_KEY_DEFAULT = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _shortcut_dir() -> str:
    d = os.environ.get("FOREST_SHORTCUT_DIR")
    if d:
        return d
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def _run_value_name() -> str:
    return os.environ.get("FOREST_RUN_VALUE", APP_RUN_VALUE)


def _run_key_path() -> str:
    return os.environ.get("FOREST_REG_RUN_KEY") or RUN_KEY_DEFAULT


def _pythonw() -> str:
    py = sys.executable
    if os.path.basename(py).lower() == "python.exe":
        alt = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(alt):
            return alt
    return py


def _installed_exe(cfg: dict) -> str:
    """Путь к .exe в каталоге установки (единственный исполняемый файл)."""
    return os.path.join(cfg["paths"]["root"], APP_EXE_NAME)


def _is_pe(path: str) -> bool:
    """Похож ли файл на настоящий Windows-исполняемый (MZ + размер)."""
    try:
        if os.path.getsize(path) < 1024 * 1024:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except Exception:
        return False


def _find_built_exe() -> str:
    """Ищет готовый ForestProxyUpdater.exe (для копирования в каталог установки).

    Порядок: FOREST_EXE_SRC -> сам процесс (frozen onefile) -> рядом с
    репозиторием -> packaging/dist. Возвращает '' если не найден.
    """
    cands = []
    env = os.environ.get("FOREST_EXE_SRC")
    if env:
        cands.append(env)
    if getattr(sys, "frozen", False):
        cands.append(sys.executable)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(here)
        for d in (os.getcwd(), repo, here,
                  os.path.join(repo, "packaging", "dist"),
                  os.path.join(repo, "dist")):
            cands.append(os.path.join(d, APP_EXE_NAME))
    for c in cands:
        if c and os.path.isfile(c) and _is_pe(c):
            return os.path.abspath(c)
    return ""


def prepare_install_launch(cfg: dict) -> tuple:
    """Готовит команду запуска: (target, args, mode).

    Продукт поставляется ОДНИМ .exe, поэтому установка копирует его в каталог
    установки и запускает как ``<каталог установки>\\ForestProxyUpdater.exe
    --run`` — БЕЗ лишних аргументов (иначе ярлык/автозапуск даёт модальное
    окно ошибки) и без зависимости от папки разработки.
    Если .exe не найден (запуск прямо из исходников) — режим ``src``:
    pythonw + установленный скрипт ``<root>\\src\\v2ray_updater.py``.
    """
    lg = log.get_logger()
    root = cfg["paths"]["root"]
    exe_src = _find_built_exe()
    if exe_src:
        dst = _installed_exe(cfg)
        if os.path.abspath(exe_src) != os.path.abspath(dst):
            try:
                shutil.copy2(exe_src, dst)
            except Exception as e:
                lg.warn(f"  копирование .exe в каталог установки не удалось: {e}")
                dst = ""
        if dst and os.path.isfile(dst):
            lg.info(f"  exe в каталоге установки: {dst} "
                    f"({os.path.getsize(dst)} байт)")
            return dst, "--run", "exe"
    entry = os.path.join(root, "src", "v2ray_updater.py")
    lg.warn("  готового .exe не найдено — ярлык через pythonw + скрипт "
            "(режим разработки)")
    return _pythonw(), f'"{entry}" --run', "src"


def _create_shortcut(target: str, args: str, workdir: str, lnk_path: str,
                     icon: str = "") -> bool:
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    icon_ps = f"$s.IconLocation = '{icon},0'; " if icon else ""
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{lnk_path}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = '{args}'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        f"{icon_ps}"
        "$s.Description = \"FOREST's Proxy Auto-updater\"; "
        "$s.Save()"
    )
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=40, text=True, errors="replace",
                           creationflags=_no_window())
        return r.returncode == 0 and os.path.isfile(lnk_path)
    except Exception:
        return False


def _read_shortcut(lnk_path: str) -> tuple:
    """Читает ярлык обратно: (target, args, workdir). ('','','') при ошибке."""
    if not os.path.isfile(lnk_path):
        return "", "", ""
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{lnk_path}'); "
        "Write-Output ($s.TargetPath + \"`n\" + $s.Arguments + \"`n\" + "
        "$s.WorkingDirectory)"
    )
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=40, text=True, errors="replace",
                           creationflags=_no_window())
        parts = (r.stdout or "").replace("\r", "").split("\n")
        while len(parts) < 3:
            parts.append("")
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    except Exception:
        return "", "", ""


def _set_autostart(cmd: str) -> bool:
    import winreg
    try:
        k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _run_key_path(),
                               0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, _run_value_name(), 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False


def _read_autostart() -> str:
    """Читает записанную команду автозапуска обратно (проверка установки)."""
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _run_key_path(), 0, winreg.KEY_READ)
        try:
            v, _t = winreg.QueryValueEx(k, _run_value_name())
            return str(v)
        finally:
            winreg.CloseKey(k)
    except Exception:
        return ""


def _remove_autostart() -> bool:
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _run_key_path(),
                           0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(k, _run_value_name())
        except FileNotFoundError:
            pass
        winreg.CloseKey(k)
        return True
    except Exception:
        return False


def _schedule_delete(paths: list, root: str) -> bool:
    """Удаляет файлы/каталог ПОСЛЕ выхода процесса.

    Windows не даёт удалить запущенный .exe, поэтому удаление откладывается
    отдельному .bat (запускается отдельно от нас): ждёт ~2 с, пока процесс
    выйдет, сносит файлы, затем каталог, затем сам себя.
    Возвращает True, если «чистильщик» поставлен.
    """
    import tempfile as _tf
    if not paths and not root:
        return False
    lines = ["@echo off", "ping -n 3 127.0.0.1 >nul"]
    lines += [f'del /f /q "{p}"' for p in paths if p]
    if root:
        lines.append(f'rmdir /s /q "{root}"')
    lines.append('del /f /q "%~f0" & exit /b 0')
    try:
        fd, bat = _tf.mkstemp(prefix="forest_cleanup_", suffix=".bat")
        with os.fdopen(fd, "w", encoding="cp1251", errors="replace") as f:
            f.write("\r\n".join(lines))
    except Exception:
        return False
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", bat],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | _no_window(),
            close_fds=True)
        return True
    except Exception:
        try:
            os.unlink(bat)
        except Exception:
            pass
        return False


def install(cfg: dict) -> int:
    lg = log.get_logger()
    root = cfg["paths"]["root"]
    src_dir = os.path.join(root, "src")
    lg.info(f"Установка в {root}")
    os.makedirs(src_dir, exist_ok=True)

    # 1. сама программа. Продукт — один самодостаточный .exe: копируем его в
    #    каталог установки, чтобы ярлык/автозапуск не зависели от папки
    #    разработки. Если .exe нет (запуск из исходников) — раскладываем .py.
    target, args, mode = prepare_install_launch(cfg)
    if mode == "src":
        code_dir = os.path.dirname(os.path.abspath(__file__))
        copied = 0
        if not getattr(sys, "frozen", False):
            for fn in os.listdir(code_dir):
                if fn.endswith(".py"):
                    try:
                        shutil.copy2(os.path.join(code_dir, fn),
                                     os.path.join(src_dir, fn))
                        copied += 1
                    except Exception as e:
                        lg.warn(f"  копирование {fn}: {e}")
        for extra in ("LICENSE", "README.md", "README.ru.md", "config.example.json"):
            p = os.path.join(os.path.dirname(code_dir), extra)
            if os.path.isfile(p):
                try:
                    shutil.copy2(p, os.path.join(root, extra))
                except Exception:
                    pass
        lg.info(f"  файлов программы скопировано: {copied} -> {src_dir}")

    # 2. конфиг (первый запуск)
    conf = os.path.join(root, "config.json")
    if not os.path.isfile(conf):
        ex = CFG.example_config_path() or ""
        if not ex or not os.path.isfile(ex):
            lg.warn("  config.example.json не найден — конфиг будет создан "
                    "при первом обычном запуске")
        else:
            try:
                shutil.copy2(ex, conf)
                lg.info(f"  конфиг создан: {conf}")
            except Exception as e:
                lg.warn(f"  конфиг не создан: {e}")

    # 3. ярлык (ручной запуск) + автономный надзор через Планировщик заданий
    #    Иконка: источник истины — packaging/app.ico (рисунок пользователя).
    #    Кладём её рядом с программой: её же берёт значок трея и ярлык.
    icon_dst = os.path.join(root, "app.ico")
    if os.path.isfile(icon_dst):
        lg.info(f"  иконка уже на месте: {icon_dst}")
    else:
        cands = []
        try:
            mp = getattr(sys, "_MEIPASS", "") or ""
            if mp:
                cands.append(os.path.join(mp, "app.ico"))
            cands.append(os.path.join(
                os.path.dirname(os.path.abspath(sys.executable)), "app.ico"))
            cands.append(os.path.join(CFG.PROJECT_DIR, "app.ico"))
            cands.append(os.path.join(CFG.BASE_DIR, "app.ico"))
        except Exception:
            pass
        for c in cands:
            try:
                if c and os.path.isfile(c) and os.path.abspath(c) != os.path.abspath(icon_dst):
                    shutil.copy2(c, icon_dst)
                    lg.info(f"  иконка скопирована рядом с программой: {icon_dst} "
                            f"({os.path.getsize(icon_dst)} байт) из {c}")
                    break
            except Exception as e:
                lg.warn(f"  иконка {c}: {e}")
        if not os.path.isfile(icon_dst):
            lg.warn("  app.ico не найден в комплекте — ярлык получит значок .exe")

    lnk = os.path.join(_shortcut_dir(), f"{APP_RUN_VALUE}.lnk")
    icon_for_lnk = icon_dst if os.path.isfile(icon_dst) else target
    ok_lnk = _create_shortcut(target, args, root, lnk, icon=icon_for_lnk)
    lg.info(f"  цель запуска: \"{target}\" {args}   (режим: {mode})")
    lg.info(f"  ярлык: {'создан' if ok_lnk else 'НЕ создан'} ({lnk}); "
            f"иконка ярлыка: {icon_for_lnk}")

    # 3б. старый автозапуск из реестра снимаем: механизм ровно ОДИН — задача
    #     Планировщика заданий (иначе два механизма поднимали бы по экземпляру).
    if _read_autostart():
        _remove_autostart()
        lg.info("  старая запись автозапуска в реестре снята "
                "(теперь механизм один — задача Планировщика)")

    # 3в. надзор по настройке watchdog: enabled -> создать задачу, иначе удалить
    import watchdog
    wd = watchdog.apply_watchdog(cfg)
    lg.info(f"  надзор (watchdog): {wd['action']} — {wd['detail']}")

    # 3г. помощник интерфейса (Qt: значок, меню, уведомления) — если есть в
    #     комплекте. Он ОТДЕЛЬНАЯ программа: кладём рядом с программой, чтобы
    #     её нашли и --ensure (задача Планировщика), и сам супервизор.
    try:
        import ui_helper as UH
        name = UH.helper_name()
        if UH.helper_path(cfg):
            lg.info(f"  помощник интерфейса уже на месте: {UH.helper_path(cfg)}")
        else:
            cands = []
            mp = getattr(sys, "_MEIPASS", "") or ""
            if mp:
                cands.append(os.path.join(mp, name))
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            cands.append(os.path.join(exe_dir, name))
            cands.append(os.path.join(CFG.PROJECT_DIR, "packaging", "dist", name))
            cands.append(os.path.join(CFG.PROJECT_DIR, "linux", "dist", name))
            for c in cands:
                try:
                    if c and os.path.isfile(c):
                        dst = os.path.join(root, name)
                        if os.path.abspath(c) != os.path.abspath(dst):
                            shutil.copy2(c, dst)
                        lg.info(f"  помощник интерфейса установлен: {dst} "
                                f"({os.path.getsize(dst)} байт)")
                        break
                except Exception as e:
                    lg.warn(f"  помощник интерфейса {c}: {e}")
        if UH.enabled(cfg):
            UH.ensure(cfg)
            lg.info(f"  помощник интерфейса: {UH.status_text(cfg)}")
        else:
            lg.info("  помощник интерфейса выключен настройкой ui.helper=off "
                    "или файла нет — интерфейс даёт встроенный значок")
    except Exception as e:
        lg.warn(f"  помощник интерфейса: {e}")

    # 3г. проверка по факту: что реально лежит в ярлыке и в реестре
    lt, la, _lw = _read_shortcut(lnk)
    if ok_lnk:
        if lt == target and la.strip() == args.strip():
            lg.info(f"  проверка ярлыка: OK (\"{lt}\" {la})")
        else:
            lg.warn(f"  проверка ярлыка: расхождение — в ярлыке \"{lt}\" {la}")
    left = _read_autostart()
    if not left:
        lg.info("  проверка автозапуска: в реестре записи нет (OK — механизм один)")
    else:
        lg.warn(f"  проверка автозапуска: в реестре ОСТАЛОСЬ «{left}»")

    # 4. первый цикл (системный прокси на этом шаге НЕ трогается)
    if not os.environ.get("FOREST_SKIP_FIRST_CYCLE"):
        lg.info("  первый цикл (системный прокси не изменяется)...")
        try:
            rc = run_cycle(cfg, dry=False)
            if rc != 0:
                # Установка состоялась (файлы, ярлык, автозапуск), но живых
                # серверов в этот момент не нашлось. Это не отказ установки:
                # сторож/гвард повторят полный цикл и поднимут связь, как
                # только найдутся рабочие узлы.
                lg.warn(f"  первый цикл не дал связи (код {rc}): рабочих серверов "
                        "сейчас нет. Сторожевая петля повторит подбор "
                        "автоматически — вмешательство не требуется.")
        except Exception as e:
            lg.warn(f"  первый цикл: {e}")
    _say(i18n.t("install.done"))
    _say(i18n.t("install.proxy_note"))
    # Понятное предупреждение про надзор — пользователь обязан знать (без англицизмов).
    if wd.get("ok") and wd.get("action") in ("created", "unchanged") and \
            watchdog.settings(cfg)["enabled"]:
        _say(i18n.t("watchdog.install_notice_on"))
    else:
        _say(i18n.t("watchdog.install_notice_off"))
    return 0


def uninstall(cfg: dict) -> int:
    lg = log.get_logger()
    root = cfg["paths"]["root"]
    lg.info(f"Удаление из {root}")
    # 0. остановить помощник интерфейса (он отдельный процесс и держал бы файлы)
    try:
        import ui_helper as UH
        stopped = UH.stop(cfg)
        lg.info(f"  помощник интерфейса: {'остановлен' if stopped else 'НЕ остановлен'}")
    except Exception as e:
        lg.warn(f"  помощник интерфейса: {e}")
    # 1. остановить наше ядро (только наше)
    try:
        CW.stop_core(cfg, why="uninstall")
    except Exception:
        pass
    # 2. вернуть системный прокси в исходное состояние
    try:
        CW.restore_system_proxy(cfg)
    except Exception as e:
        lg.warn(f"  восстановление прокси: {e}")
    # 3. снять надзор (задачу Планировщика), автозапуск и ярлык
    import watchdog
    ok_t, detail_t = watchdog.delete_task(cfg=cfg)
    lg.info(f"  надзор (задача Планировщика): "
            f"{'снят' if ok_t else 'ОШИБКА'} — {detail_t}")
    _say(i18n.t("watchdog.uninstall_notice") if ok_t
          else i18n.t("watchdog.uninstall_failed", err=detail_t))
    _remove_autostart()
    left = _read_autostart()
    lg.info(f"  автозапуск (HKCU\\{_run_key_path()}\\{_run_value_name()}): "
            f"{'снят' if not left else 'ОСТАЛСЯ = ' + left}")
    lnk = os.path.join(_shortcut_dir(), f"{APP_RUN_VALUE}.lnk")
    try:
        if os.path.isfile(lnk):
            os.unlink(lnk)
            lg.info(f"  ярлык удалён: {lnk}")
        else:
            lg.info(f"  ярлыка нет: {lnk}")
    except Exception as e:
        lg.warn(f"  ярлык: {e}")
    # 3б. убрать установленный .exe (до rmtree: так видно, если он занят)
    exe = _installed_exe(cfg)
    self_locked = bool(getattr(sys, "frozen", False)
                       and os.path.abspath(sys.executable) == os.path.abspath(exe))
    if self_locked and os.path.isfile(exe):
        # себя удалить нельзя — откладываем нашим внутренним «чистильщику»
        if _schedule_delete([exe], root):
            lg.info(f"  exe удалится после выхода программы (запущен сейчас): {exe}")
            _say(i18n.t("install.uninstalled"))
            _say(i18n.t("install.proxy_note"))
            return 0
        lg.warn(f"  exe занят запущенным процессом — удалите вручную: {exe}")
    else:
        try:
            if os.path.isfile(exe):
                os.unlink(exe)
                lg.info(f"  exe удалён: {exe}")
            else:
                lg.info(f"  exe уже отсутствует: {exe}")
        except Exception as e:
            lg.warn(f"  exe не удалён: {e}")
    # 4. убрать файлы программы
    # закрыть свой лог-файл: иначе он держит каталог и rmtree не удалит logs/
    try:
        log.get_logger().close()
    except Exception:
        pass
    try:
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
            if os.path.isdir(root):
                lg.warn(f"  каталог частично остался (файлы заняты): {root}")
            else:
                lg.info(f"  каталог удалён: {root}")
    except Exception as e:
        lg.warn(f"  каталог: {e}")
    _say(i18n.t("install.uninstalled"))
    _say(i18n.t("install.proxy_note"))
    return 0


# ══════════════════════════════════════════ статус ═══
def show_status(cfg: dict) -> int:
    """``--status``: печатает отчёт И кладёт его файлом в каталог состояния.

    Файл нужен по двум причинам: у оконной сборки нет консоли (``sys.stdout``
    равен None и _say() уходит в никуда), а в прежней надстройке пункт меню
    «Открыть статус» открывал именно файл отчёта — состав данных тот же.
    """
    import contextlib
    import io

    buf = io.StringIO()
    real = sys.stdout            # ДО подмены: иначе Tee пишет сам в себя

    class _Tee:
        def write(self, s):
            buf.write(s)
            try:
                if real is not None and real is not self:
                    real.write(s)
            except Exception:
                pass
            return len(s)

        def flush(self):
            try:
                if real is not None:
                    real.flush()
            except Exception:
                pass

    # ЗАПИСЬ ОРИГИНАЛЬНОГО ТЕКСТА: _say кладёт строки сюда ДО замены не-ASCII на
    # «?» (см. safestdio.say), поэтому отчёт в файле — настоящая кириллица.
    global _REPORT
    _REPORT = []
    try:
        with contextlib.redirect_stdout(_Tee()):
            rc = _show_status(cfg)
        lines = list(_REPORT)
    finally:
        _REPORT = None
    text = "\n".join(lines)
    if not text.strip():
        text = buf.getvalue()          # запасной путь: только то, что ушло в stdout
    full = ""
    try:
        d = (cfg.get("paths") or {}).get("state_dir") or ""
        if d:
            os.makedirs(d, exist_ok=True)
            full = os.path.join(d, "status.txt")
            with open(full, "w", encoding="utf-8") as f:
                f.write(text if text.endswith("\n") else text + "\n")
    except Exception:
        full = ""
    # stdout в собранном приложении — ТОЛЬКО ASCII (см. src/safestdio.py),
    # поэтому полный человекочитаемый отчёт лежит файлом в UTF-8.
    if full:
        _say(f"full report (UTF-8): {full}")
    return rc


def _show_status(cfg: dict) -> int:
    client = CFG.find_client(cfg)
    st = CW.StateStore(cfg)
    pool_n = int(st.data.get("balancer_n", 0) or 0)
    core = client.get("core")
    core_run = CFG.core_running(cfg)
    en, srv = CW._sys_proxy_state()
    port = int(client.get("socks_port", 10808))
    link = ""
    if core_run and CW.port_listening(port, timeout=0.5):
        ok, rtt = CW.probe_client(client, timeout=6, strict=False)
        link = (i18n.t("status.link_ok") + f" (rtt={rtt} мс)") if ok else i18n.t("status.link_down")
    _say("=" * 60)
    _say(f"{i18n.t('app.name')} — {i18n.t('status.title')}")
    _say("=" * 60)
    _say(f"{i18n.t('status.mode')}: {client.get('mode')}")
    _say(f"{i18n.t('status.core')}: {core or i18n.t('status.core_missing')}")
    _say(f"core dir: {CFG.core_dir()}")
    if core_run:
        _say(f"{i18n.t('status.core_running')}: {CFG.core_pid_path(cfg)} -> да")
    else:
        _say(i18n.t("status.core_stopped"))
    _say(f"{i18n.t('status.pool')}: {pool_n}")
    _say(f"{i18n.t('status.proxy')}: "
          f"{i18n.t('status.proxy_on') + ' ' + srv if en else i18n.t('status.proxy_off')}")
    if link:
        _say(f"{i18n.t('status.link')}: {link}")
    _say(f"config: {cfg.get('_config_path')}")
    _say(f"root:   {cfg['paths']['root']}")
    try:                      # помощник интерфейса: есть ли, работает ли
        import ui_helper as UH
        _say(f"{i18n.t('ui.helper')}: {UH.status_text(cfg)}")
    except Exception as e:
        _say(f"{i18n.t('ui.helper')}: {i18n.t('error.generic', err=e)}")
    if os.name == "nt":
        # Честная строка про надзор: включён ли, есть ли задача, работает ли программа.
        try:
            import watchdog
            rep = watchdog.watchdog_report(cfg)
            st, task = rep["settings"], rep["task"]
            _say(i18n.t("watchdog.status_line") + ": " +
                  ((i18n.t("watchdog.enabled") + f", {i18n.t('watchdog.task_word')}: " +
                    (i18n.t("watchdog.yes") if task.get("exists") else i18n.t("watchdog.no")))
                   if st["enabled"] else i18n.t("watchdog.disabled")))
            _say("  " + i18n.t("watchdog.program") + ": " +
                  (i18n.t("watchdog.program_running") if rep["supervisor_alive"]
                   else i18n.t("watchdog.program_stopped")))
        except Exception as e:
            _say(f"{i18n.t('watchdog.status_line')}: {i18n.t('error.generic', err=e)}")
    if os.name != "nt":
        # Linux: вместо трея — состояние systemd-юнита и файл индикации
        pp = posix_platform()
        _say(f"state:  {cfg['paths']['state_dir']}")
        _say(f"logs:   {cfg['paths']['log_dir']}")
        if pp is not None:
            _say(f"systemd: {pp.service_state()}")
            ind = pp.indicator_path(cfg)
            if os.path.isfile(ind):
                _say(f"indicator: {ind}")
            if not pp.notify_available():
                _say("notify-send: недоступен — уведомления только в лог "
                      "(мягкая деградация)")
    return 0


def run_selftest() -> int:
    """Автотесты. На Linux к общим тестам добавляются POSIX-специфичные."""
    import selftest
    rc = selftest.main()
    if os.name == "nt":
        return rc
    pp = posix_platform()
    if pp is None:
        return rc
    try:
        import posix_selftest
        rc2 = posix_selftest.main()
    except Exception as e:
        log.get_logger().error(f"posix_selftest: {e}")
        return rc or 1
    return rc or rc2


# ══════════════════════════════════════════ условия использования ═══
# Оговорка показывается ОДИН РАЗ на установку — в журнал (файл лога + stdout →
# journal), и никогда не мешает работе: ни окон, ни вопросов, ни пауз. Полный
# текст лежит рядом с программой: docs/TERMS.ru.md и docs/TERMS.md
# (в пакетах — /usr/share/doc/forest-proxy-updater/).
TERMS_NOTICE = (
    "Условия использования: программа даёт доступ ко всем сайтам, включая "
    "заблокированные. Ответственность за соблюдение законов своей юрисдикции и "
    "правил своего интернет-провайдера лежит на пользователе.\n"
    "Terms of use: the program provides access to all websites, including "
    "blocked ones; complying with the laws of your jurisdiction is your "
    "responsibility.\n"
    "Полный текст: docs/TERMS.ru.md / docs/TERMS.md "
    "(в установленных пакетах — /usr/share/doc/forest-proxy-updater/)."
)


def show_terms_notice(cfg: dict) -> None:
    """Один раз на установку пишет оговорку в лог и ставит метку.

    Метка (``terms_notice_seen`` в каталоге состояния) не даёт повторять
    уведомление при каждом запуске. Любая ошибка здесь не должна влиять на
    работу программы — поэтому всё под try/except.
    """
    try:
        state = cfg["paths"]["state_dir"]
        marker = os.path.join(state, "terms_notice_seen")
        if os.path.isfile(marker):
            return
        os.makedirs(state, exist_ok=True)
        log.get_logger().info(TERMS_NOTICE)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    except Exception:
        pass


# ══════════════════════════════════════════ main ═══
def main(argv=None) -> int:
    SD.safe_stdio()
    # КРЮЧОК ПРОВЕРКИ: искусственное исключение внутри main — им проверяют, что
    # на рабочем столе НЕ появляется модальное окно PyInstaller, а трассировка
    # уходит в журнал (crash.log) и код возврата ненулевой.
    if os.environ.get("FOREST_TEST_CRASH") == "1":
        raise RuntimeError("FOREST_TEST_CRASH: искусственное исключение внутри main")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    # Уходим из каталога распаковки onefile ДО запуска любых дочерних процессов
    # (иначе уборщик PyInstaller не снесёт _MEI и покажет окно ошибки).
    moved_cwd = CFG.ensure_safe_cwd()

    ap = argparse.ArgumentParser(
        description="FOREST's Proxy Auto-updater",
        epilog="Условия использования (terms of use): docs/TERMS.ru.md / "
               "docs/TERMS.md; в установленных пакетах — "
               "/usr/share/doc/forest-proxy-updater/TERMS.ru.md")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--run", action="store_true", help="супервизор с треем")
    ap.add_argument("--ensure", action="store_true",
                    help="поднять супервизор, если он не работает (для надзора)")
    ap.add_argument("--watchdog", choices=["on", "off", "status"], default=None,
                    help="автономный надзор: on | off | status")
    ap.add_argument("--once", action="store_true", help="один полный цикл")
    ap.add_argument("--guard", action="store_true", help="одна самопроверка")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--watch", action="store_true", help="только сторож")
    ap.add_argument("--auto", action="store_true", help="синоним --once")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=None, help="путь к config.json")
    args = ap.parse_args(argv)

    # --selftest обрабатываем ДО загрузки конфига: тест сам задаёт свой
    # временный корень и не должен создавать рабочий каталог программы.
    if args.selftest:
        return run_selftest()

    cfg = CFG.load_config(args.config)
    log.reset_logger(cfg["paths"]["log_dir"])
    i18n.set_lang(i18n.detect_lang(cfg))
    lg = log.get_logger()
    lg.info("Запуск FOREST's Proxy Auto-updater (аргументы: %s)"
            % " ".join(a for a in (argv if argv is not None else sys.argv[1:])))
    lg.info("cwd=%s | _MEIPASS=%s | уход из распаковки: %s"
            % (os.getcwd(), getattr(sys, "_MEIPASS", "") or "—",
               moved_cwd or "не требовался"))
    # Ядро и гео-базы работают из ПОСТОЯННОГО каталога (копия из бандла при
    # первом запуске: config.ensure_core_installed — зовётся из find_core).
    # Свои прошлые каталоги распаковки _MEI* убираем в фоне: иначе они копятся
    # в temp, а занятое живым ядром ядро не даёт уборщику PyInstaller снести
    # каталог (модальное окно «Failed to remove temporary directory»).
    if getattr(sys, "frozen", False):
        CFG.cleanup_stale_bundles_async()
    show_terms_notice(cfg)

    if args.install:
        pp = posix_platform()
        if pp is not None:
            return pp.install(cfg)
        return install(cfg)
    if args.uninstall:
        pp = posix_platform()
        if pp is not None:
            return pp.uninstall(cfg)
        return uninstall(cfg)
    if args.watchdog:
        return run_watchdog_command(cfg, args.watchdog)
    if args.ensure:
        return run_ensure(cfg)
    if args.status:
        return show_status(cfg)
    if args.guard:
        return run_guard(cfg)
    if args.watch:
        import watch
        return watch.main()
    if args.run:
        return run_supervisor(cfg)
    if args.once or args.auto or args.dry_run:
        if not acquire_lock("cycle", stale_s=3 * 3600, cfg=cfg):
            lg.warn("Другой цикл уже выполняется — выход")
            return 0
        try:
            return run_cycle(cfg, dry=args.dry_run)
        finally:
            release_lock(cfg)
    ap.print_help()
    return 0


if __name__ == "__main__":
    # ЛЮБОЕ необработанное исключение -> журнал + код 70, БЕЗ модального окна
    # PyInstaller: см. src/safestdio.py.
    sys.exit(SD.guarded(main, where="forest-proxy-updater"))
