# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Мгновенный локальный сторож связи (GPL-3.0).

FOREST's Proxy Auto-updater. Работает как поток супервизора (или отдельным
процессом через ``--watch``). Держит на клиентском порту ОДНО ядро xray с
пулом серверов внутри (observatory + balancer): xray сам направляет новые
соединения на живой/быстрый сервер, поэтому при отказе одного узла связь не
рвётся — перезапуска ядра при смене сервера нет.

Страж проверяет, что ядро живо и везёт данные, ставит системный прокси,
следит за скоростью и YouTube-канарейкой; при полном отказе — просит полный
цикл (callback ``full_cycle``).
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# POSIX: тот же API, но примитивы процессов/прокси — POSIX (см. clientwrite_posix)
if os.name == "nt":
    import clientwrite as CW
else:
    import clientwrite_posix as CW
import config as CFG
import log
import tray_status as TS


def _acquire_singleton(cfg: dict) -> bool:
    """Не даём запуститься ВТОРОМУ стражу-процессу (в супервизоре не нужен)."""
    p = os.path.join(cfg["paths"]["state_dir"], "watch.pid")
    try:
        if os.path.isfile(p):
            old = open(p, encoding="utf-8").read().strip()
            if old.isdigit() and int(old) != os.getpid() and CW._pid_alive(int(old)):
                return False
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True


def _tip(state: str, pool_n: int = 10, route: str = None) -> str:
    """Подсказка значка. Состав — как в эталоне: состояние, серверы, маршрут.

    Windows режет подсказку на 63 символах (ограничение системы), поэтому
    строка обрезается — ровно как в прежней надстройке.
    """
    if route is None:
        try:
            import status_info as SI
            route = SI.route_text(_TIP_CFG)
        except Exception:
            route = "РФ напрямую"
    return f"FOREST {state} | {pool_n} серверов | {route} | watch OK"[:63]


_TIP_CFG = {}


def _tray_ok(cfg: dict, rtt=None) -> None:
    try:
        TS.write_status(cfg, state="ok", drop_proc=True, tip=_tip("связь OK"), rtt=rtt)
    except Exception:
        pass


def _tray_settle(cfg: dict, client: dict, proc, lg, rtt=None) -> None:
    """Закрыть индикацию по ФАКТУ: связь есть -> финал-успех, иначе финал-провал."""
    ok = False
    try:
        ok = bool(CW.port_works(int(client.get("socks_port", 10808)), timeout=8))
    except Exception:
        ok = False
    try:
        if ok:
            TS.finish_proc(cfg, proc, "ok", tip=_tip("связь OK"))
            TS.write_status(cfg, state="ok", rtt=rtt)
        elif _fail_card_due(lg, "финал восстановления"):
            reason = _fail_reason()
            lg.error(f"Watch: связь не восстановлена — {reason}")
            TS.fail_proc(cfg, proc, "down", _tip("НЕТ СВЯЗИ"), reason=reason)
        else:
            TS.beat(cfg, "down", _tip("НЕТ СВЯЗИ"))
    except Exception as e:
        lg.error(f"Watch: финал индикации: {e}")


def _fail_reason() -> str:
    """ПОЧЕМУ не вышло — одной строкой: причина ядра, если известна.

    «серверы пула не отвечают» одинаково выглядит при отсутствующем бинаре,
    невалидном конфиге и занятом порте. Если ядро уже сказало причину
    (``clientwrite.core_diag``), показываем ЕЁ — её видно и в журнале, и в
    карточке («Не удалось: …»).
    """
    try:
        r = CW.last_core_reason()
    except Exception:
        r = ""
    return (str(r) or "серверы пула не отвечают")[:220]


def _fail_card_due(lg, what: str = "") -> bool:
    """Пора ли писать ПРОВАЛ в состояние: не чаще раза в FAIL_CARD_MIN_S.

    Раньше 60-секундный порог соблюдался ТОЛЬКО в _outage_done, а _tray_settle
    писал провал напрямую своим новым ``proc.id`` — при мёртвом ядре цикл шёл
    каждые ~10 с, и помощник рисовал по новому id НОВУЮ карточку. Теперь порог
    один на все пути провала.
    """
    global _LAST_FAIL_CARD_TS
    now = time.time()
    if now - _LAST_FAIL_CARD_TS < FAIL_CARD_MIN_S:
        lg.info("Watch: провал%s уже показан %.0f с назад (порог %.0f с) — "
                "карточку не повторяю, только журнал"
                % ((" (%s)" % what) if what else "",
                   now - _LAST_FAIL_CARD_TS, FAIL_CARD_MIN_S))
        return False
    _LAST_FAIL_CARD_TS = now
    return True


# ── РЕАЛЬНЫЙ ОБРЫВ -> карточка уведомления ────────────────────────────────
# Сторож обязан показать КАЖДЫЙ настоящий обрыв связи, включая быстрый путь
# «пульсом» (сотни миллисекунд). Раньше быстрый путь писал только state="ok"
# по факту восстановления — перехода состояния помощник не видел, и карточки не
# было ВООБЩЕ (замер на живом ПК: разрыв 712 мс, уведомления нет).
#
# Порог (NOTIFY_MIN_GAP_MS): ниже него считаем, что связь для пользователя НЕ
# прерывалась — плановый перезапуск ядра поднимает порт за десятки миллисекунд
# (start_core опрашивает порт шагом 10 мс), данные не теряются, а снятие ядра
# штатно отдельный случай (см. проверку «плановый перезапуск, не обрыв»).
# Числа-ориентиры: плановый перезапуск < 200 мс, замеренный реальный обрыв
# 712 мс; порог 250 мс отделяет одно от другого с запасом ~3x и при этом не
# зависит от частоты опроса (сторож замечает смерть порта за 50-350 мс).
NOTIFY_MIN_GAP_MS = 250.0

# ── ПАУЗА ВМЕСТО ГОРЯЧЕГО ЦИКЛА ───────────────────────────────────────────
# Живой KWin вскрыл: при мёртвом ядре и пустом/нерабочем пуле «пульс порта»
# замечает смерть за 50-350 мс, вызывает полный цикл, тот не находит серверов и
# возвращается — и всё повторяется. В журнале было ×191 за 60 с: это и поток
# карточек, и лишняя нагрузка (перебор источников, запуски ядра). Теперь после
# КАЖДОЙ неудачи восстановления сторож выдерживает паузу 10/20/40/60 с, а не
# крутит цикл подряд. Успех сбрасывает счётчик.
BACKOFF_S = (10.0, 20.0, 40.0, 60.0)
# Провал восстановления показываем карточкой не чаще раза в 60 с.
FAIL_CARD_MIN_S = 60.0
_LAST_FAIL_CARD_TS = 0.0


def _notify_gap_ms(cfg: dict) -> float:
    """Порог показа карточки: настройка watch.notify_min_gap_ms (мс)."""
    try:
        v = float(((cfg or {}).get("watch") or {}).get("notify_min_gap_ms",
                                                       NOTIFY_MIN_GAP_MS))
        return max(0.0, v)
    except Exception:
        return NOTIFY_MIN_GAP_MS


def _outage_gap_ms(cfg: dict, t_lost: float, t_now: float) -> float:
    """Разрыв связи в мс: ЗАМЕР ПЕРЕЗАПУСКА ядра, иначе собственный замер.

    ``balancer_gap_ms`` считает сам перезапуск (от снятия ядра до момента, когда
    новое ядро приняло клиентский порт) — это и есть время, когда данные через
    прокси не шли.
    """
    try:
        v = CW.StateStore(cfg).data.get("balancer_gap_ms")
        if v is not None:
            return max(0.0, float(v))
    except Exception:
        pass
    return max(0.0, (float(t_now) - float(t_lost)) * 1000.0)


def _outage_steps(gap_ms: float, total_ms: float) -> list:
    """Этапы обрыва со ВРЕМЕНЕМ КАЖДОГО В МИЛЛИСЕКУНДАХ (реальные замеры)."""
    gap = max(0.0, float(gap_ms))
    total = max(gap, float(total_ms))
    return [("Обрыв обнаружен (пульс порта)", 0.05),
            ("Разрыв связи: перезапуск ядра", gap / 1000.0),
            ("Данные подтверждены (204 + TLS)", total / 1000.0)]


def _outage_done(cfg: dict, t_lost: float, t_now: float, lg, ok: bool = True,
                 why: str = "пульс порта") -> float:
    """Конец РЕАЛЬНОГО обрыва: карточка показывается, если связь ПРЕРЫВАЛАСЬ.

    Возвращает замеренный разрыв в мс. Пишет состояние и описание процесса так,
    чтобы помощник интерфейса показал ОДНУ карточку состава макета (заголовок,
    подпись, этапы с мс, полоса, спиннер, футер) — и по провалу, и по успеху.

    АНТИСПАМ (регрессия на живом KWin): провал восстановления пишется в состояние
    НЕ ЧАЩЕ раза в 60 с. Каждая запись — новый номер процесса, а помощник рисует
    карточку на новый номер; при мёртвом пуле цикл шёл ~3 раза в секунду, и
    пользователь получал поток окон (в журнале ×191 за 60 с). Повтор — только
    строка в журнале и «сердцебиение» состояния.
    """
    global _LAST_FAIL_CARD_TS
    gap = _outage_gap_ms(cfg, t_lost, t_now)
    total = max(gap, (float(t_now) - float(t_lost)) * 1000.0)
    steps = _outage_steps(gap, total)
    if not ok:
        if not _fail_card_due(lg, "обрыв"):
            try:
                TS.beat(cfg, "down", _tip("НЕТ СВЯЗИ"))
            except Exception:
                pass
            return gap
        try:
            proc = TS.make_proc("restore", "outage", steps, eta_s=total / 1000.0)
            proc["started"] = float(t_lost)
            reason = _fail_reason()
            lg.error(f"Watch: обрыв {gap:.0f} мс — причина: {reason}")
            TS.fail_proc(cfg, proc, "down", _tip("НЕТ СВЯЗИ"), reason=reason)
            lg.info(f"Watch: обрыв {gap:.0f} мс — карточка провала показана")
        except Exception as e:
            lg.warn(f"Watch: карточка провала: {e}")
        return gap
    try:
        if gap < _notify_gap_ms(cfg):
            TS.write_status(cfg, state="ok", drop_proc=True, tip=_tip("связь OK"))
            lg.info(f"Watch: разрыв {gap:.0f} мс < порога "
                    f"{_notify_gap_ms(cfg):.0f} мс — связь не прерывалась, "
                    "уведомление не показываем")
            return gap
        proc = TS.make_proc("restore", "outage", steps, eta_s=total / 1000.0)
        proc["started"] = float(t_lost)
        proc["steps"][0]["done"] = True
        TS.finish_proc(cfg, proc, "ok", tip=_tip("связь OK"))
        lg.info(f"Watch: обрыв {gap:.0f} мс — карточка показана "
                f"(этапы: 50 / {gap:.0f} / {total:.0f} мс)")
    except Exception as e:
        lg.warn(f"Watch: карточка обрыва: {e}")
    return gap


def run_watch(cfg: dict, stop_event=None, full_cycle=None, once: bool = False,
              client: dict = None) -> int:
    """Основной цикл сторожа. ``stop_event`` — threading.Event для остановки."""
    lg = log.get_logger()
    client = client or CFG.find_client(cfg)
    if not client.get("core"):
        # ПРИЧИНА в журнал, а не только «не найдено»: путь, где искали, и что
        # мешает (каталог только для чтения, нет файла, нет прав на запуск).
        try:
            reason = CW.core_diag(client, cfg)
            CW._set_core_reason(cfg, reason)
            lg.error("Watch: ядро xray не найдено — сторож остановлен. ПРИЧИНА: "
                     + reason)
        except Exception:
            lg.error("Watch: ядро xray не найдено — сторож остановлен")
        return 2

    wc = cfg.get("watch", {})
    interval = float(wc.get("check_interval_s", 10))
    probe_timeout = float(wc.get("probe_timeout_s", 4))
    max_strikes = int(wc.get("max_strikes", 3))
    bal_n = int(wc.get("balancer_pool_n", 10))
    spd_min = float(wc.get("speed_min_kbps", 900))
    spd_every_ticks = max(3, int(float(wc.get("speed_check_every_s", 300)) / max(1.0, interval)))
    pool_every_ticks = max(3, int(float(wc.get("pool_refresh_s", 600)) / max(1.0, interval)))
    yt_every_ticks = max(3, int(float(wc.get("youtube_check_every_s", 300)) / max(1, int(interval))))
    strict_every_ticks = max(3, int(float(wc.get("strict_check_every_s", 300)) / max(1.0, interval)))
    burst_every_ticks = max(1, int(float(wc.get("burst_probe_every_s", 60)) / max(1.0, interval)))
    yt_fails = 0
    fail_streak = 0                # неудач восстановления ПОДРЯД (для паузы)
    port = int(client.get("socks_port", 10808))

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _sleep(sec: float) -> None:
        if stop_event is not None:
            stop_event.wait(sec)
        else:
            time.sleep(sec)

    def _recover_pause() -> float:
        """ПАУЗА после неудачи восстановления — вместо горячего цикла.

        Возвращает выдержанные секунды. Ряд 10/20/40/60: первая пауза короткая
        (пул мог моргнуть), дальше — чтобы не жечь процессор, не дёргать
        источники и не плодить записи состояния.
        """
        nonlocal fail_streak
        fail_streak += 1
        sec = BACKOFF_S[min(fail_streak - 1, len(BACKOFF_S) - 1)]
        try:
            lg.warn(f"Watch: восстановление не удалось ({fail_streak}-й раз "
                    f"подряд) — пауза {sec:.0f} с (без горячего цикла)")
        except Exception:
            pass
        _sleep(sec)
        return sec

    lg.info(f"Watch: сторож-балансировщик запущен (интервал {interval:.0f}s, "
            f"probe {probe_timeout:.0f}s, пул {bal_n} серверов)")
    try:                          # подсказка значка строит маршрут из настроек
        _TIP_CFG.clear()
        _TIP_CFG.update(cfg)
    except Exception:
        pass
    try:
        CW.ensure_balancer(client, cfg, n=bal_n)
    except Exception as e:
        lg.error(f"Watch: стартовый ensure_balancer: {e}")
    try:
        TS.write_status(cfg, state="ok", drop_proc=True, tip=_tip("связь OK"))
    except Exception:
        pass

    strikes = 0
    ticks = 0
    slow_hits = 0
    last_ok_mark = 0.0
    last_off_beat = 0.0
    last_ui_check = 0.0

    def _service_ui() -> None:
        """Разбирает команды помощника интерфейса и держит его живым.

        Команды (state/ui_cmd.json) выполняются ШТАТНЫМИ средствами — теми же,
        что у встроенного меню трея, поэтому ядро по-прежнему один хозяин.
        """
        nonlocal last_ui_check
        try:
            import ui_cmd as UC
            rec = UC.consume(cfg)
            if rec:
                ok, msg = UC.apply(cfg, rec)
                lg.info(f"помощник интерфейса: команда «{rec.get('cmd')}» — "
                        f"{'OK' if ok else 'ОШИБКА'} {msg}")
                if rec.get("cmd") == "exit":
                    if stop_event is not None:
                        stop_event.set()
                    return
            if time.time() - last_ui_check > 30:
                last_ui_check = time.time()
                import ui_helper as UH
                UH.ensure(cfg)
        except Exception as e:
            lg.warn(f"помощник интерфейса: {e}")

    # помощник интерфейса поднимаем сразу: он должен работать, пока работает
    # программа (отдельного «своего» автозапуска у него нет)
    try:
        import ui_helper as UH
        UH.ensure(cfg)
    except Exception as e:
        lg.warn(f"помощник интерфейса: старт: {e}")
    _service_ui()

    while not _stopped():
        try:
            _service_ui()
            if CW.proxy_off(cfg):
                if time.time() - last_off_beat > 15:
                    last_off_beat = time.time()
                    try:
                        TS.beat(cfg, "off", tip="Прокси выключен вручную")
                    except Exception:
                        pass
                ticks += 1
                _sleep(max(1.0, interval))
                if once:
                    break
                continue
            ok, rtt = False, 0
            strict_now = bool(strict_every_ticks and ticks % strict_every_ticks == 0)
            port_dead = False
            try:
                port_dead = not CW.port_listening(port, timeout=0.5)
            except Exception:
                pass
            for attempt in (() if port_dead else range(int(wc.get("probe_retries", 2)) + 1)):
                ok, rtt = CW.probe_client(client, timeout=probe_timeout,
                                          strict=strict_now, alt=bool(attempt))
                if ok:
                    break
                try:
                    if not CW.port_listening(port, timeout=0.5):
                        break
                except Exception:
                    pass
                if attempt < int(wc.get("probe_retries", 2)):
                    _sleep(2)
        except Exception as e:
            lg.error(f"Watch: ошибка probe: {e}")
            ok, rtt = False, 0

        if ticks % 6 == 0:
            try:
                CW.ensure_system_proxy(client, cfg)
            except Exception as e:
                lg.error(f"Watch: системный прокси: {e}")

        if ok:
            strikes = 0
            fail_streak = 0            # связь есть — паузы сбрасываем
            ticks += 1
            try:
                if time.time() - last_ok_mark > 2:
                    last_ok_mark = time.time()
                    st_m = CW.StateStore(cfg)
                    st_m.data["last_probe_ok_ts"] = time.time()
                    st_m.data["last_probe_fail_ts"] = 0
                    st_m.save()
                    _tray_ok(cfg, rtt)
            except Exception:
                pass
            if pool_every_ticks and ticks % pool_every_ticks == 0:
                try:
                    CW.ensure_balancer(client, cfg, n=bal_n)
                except Exception as e:
                    lg.error(f"Watch: обновление пула: {e}")
            # ПАРАЛЛЕЛЬНАЯ ПРОБА (раз в 1-2 мин): браузер открывает десятки
            # соединений сразу, одиночный probe этого не видит. Пачка 000/rc=35
            # -> немедленный переотбор узлов и пересборка пула (окно < 10 с).
            if burst_every_ticks and ticks and ticks % burst_every_ticks == 0:
                try:
                    r_burst = CW.burst_guard(client, cfg, n=bal_n)
                    if r_burst.startswith("rebuild_"):
                        lg.warn(f"Watch: параллельная проба — {r_burst}")
                    elif r_burst == "burst_unattributed":
                        lg.warn("Watch: параллельная проба — виновник не в пуле "
                                "(пауза включена, пул не трогаю)")
                except Exception as e:
                    lg.error(f"Watch: параллельная проба: {e}")
            if yt_every_ticks and ticks % yt_every_ticks == 0:
                try:
                    ycode, ytt = CW.youtube_probe(port, timeout=12)
                    if ycode in (200, 301, 302):
                        if yt_fails:
                            lg.info(f"Watch: YouTube снова отвечает (было провалов: {yt_fails})")
                        yt_fails = 0
                    else:
                        yt_fails += 1
                        lg.warn(f"Watch: YouTube не отвечает через пул "
                                f"(код {ycode}, {ytt} мс) {yt_fails}/3")
                        if yt_fails >= 3:
                            yt_fails = 0
                            lg.warn("Watch: YouTube стабильно недоступен — полный цикл")
                            if full_cycle:
                                full_cycle()
                except Exception as e:
                    lg.error(f"Watch: канарейка YouTube: {e}")
            if spd_min > 0 and spd_every_ticks and ticks % spd_every_ticks == 0:
                try:
                    sp = CW.measure_speed_kbps(client, 2000000, 12)
                    if sp and sp < spd_min:
                        slow_hits += 1
                        lg.warn(f"Watch: скорость {sp:.0f} КБ/с < {spd_min:.0f} ({slow_hits}/2)")
                        if slow_hits >= 2:
                            slow_hits = 0
                            lg.warn("Watch: пул медленный — пересобираю балансировщик")
                            if not CW.ensure_balancer(client, cfg, n=bal_n, force=True):
                                if full_cycle:
                                    full_cycle()
                    else:
                        slow_hits = 0
                except Exception as e:
                    lg.error(f"Watch: контроль скорости: {e}")
        else:
            strikes += 1
            lg.warn(f"Watch: связь не отвечает (удар {strikes}/{max_strikes}, rtt={rtt})")
            try:
                TS.beat(cfg, "down", _tip("НЕТ СВЯЗИ"))
            except Exception:
                pass
            port_dead = False
            try:
                port_dead = not CW.port_listening(port)
            except Exception:
                pass
            if strikes >= max_strikes or port_dead:
                try:
                    st_f = CW.StateStore(cfg)
                    st_f.data["last_probe_fail_ts"] = time.time()
                    st_f.save()
                except Exception:
                    pass
                lg.warn("Watch: обрыв — пересобираю балансировщик")
                # Обрыв начался не сейчас, а когда исчезла связь: берём время
                # последней УСПЕШНОЙ отметки «связь OK», иначе — окно ударов.
                t_lost = float(last_ok_mark or (time.time() - strikes * interval))
                try:
                    TS.write_status(cfg, state="down", tip=_tip("НЕТ СВЯЗИ"))
                except Exception:
                    pass
                recovered = False
                try:
                    recovered = CW.ensure_balancer(client, cfg, n=bal_n,
                                                   force=True, fast_ok=True)
                except Exception as e:
                    lg.error(f"Watch: ensure_balancer: {e}")
                if recovered:
                    ok2, rtt2 = CW.probe_client(client, timeout=probe_timeout)
                    if ok2:
                        gap2 = _outage_done(cfg, t_lost, time.time(), lg, ok=True)
                        lg.info(f"Watch: связь восстановлена балансировщиком "
                                f"(rtt={rtt2}, разрыв {gap2:.0f} мс)")
                        try:
                            TS.write_status(cfg, state="ok", rtt=rtt2)
                        except Exception:
                            pass
                    else:
                        recovered = False
                        try:
                            CW.bl_add_pool(cfg, CW.balancer_pool(client, cfg, n=bal_n),
                                           minutes=20, why="stub_pool")
                        except Exception as e:
                            lg.warn(f"Watch: bl_add_pool: {e}")
                if not recovered:
                    _outage_done(cfg, t_lost, time.time(), lg, ok=False)
                    lg.warn("Watch: пул не помог — полный цикл (свежие серверы)")
                    if full_cycle:
                        full_cycle()
                    _recover_pause()
                else:
                    fail_streak = 0
                strikes = 0

        if once:
            break

        # ПУЛЬС: пока спим — следим за портом (смерть ядра видна за 1-2 с)
        pid_file = os.path.join(cfg["paths"]["state_dir"], "core.pid")
        dead = False
        kpid = None
        for _ in range(max(1, int(interval * 20))):
            if _stopped():
                break
            time.sleep(0.05)
            try:
                if CW.port_listening(port, timeout=0.3):
                    continue
                try:
                    kpid = int(open(pid_file, encoding="utf-8").read().strip())
                except Exception:
                    kpid = 0
                if kpid and not CW._pid_alive(kpid):
                    dead = True
                    break
                time.sleep(0.3)
                if not CW.port_listening(port, timeout=0.3):
                    dead = True
                    break
            except Exception:
                pass
        if dead and not _stopped():
            time.sleep(0.1)
            try:
                npid = int(open(pid_file, encoding="utf-8").read().strip())
                if npid != kpid or CW.port_listening(port, timeout=0.2):
                    lg.info("Watch: пульс — плановый перезапуск, не обрыв")
                    strikes = 0
                    continue
            except Exception:
                pass
            lg.warn("Watch: пульс — ядро не слушает порт, пересборка немедленно")
            t_lost = time.time()
            # РЕАЛЬНЫЙ ОБРЫВ. Сразу помечаем состояние «восстановление связи»:
            # помощник видит, что связь пропала, а карточку рисуем ПО ЗАМЕРУ на
            # восстановлении — разрыв короче порога уведомления не даёт.
            try:
                TS.beat(cfg, "restoring", tip=_tip("восстановление связи"))
            except Exception:
                pass
            try:
                st_p = CW.StateStore(cfg)
                st_p.data["last_probe_fail_ts"] = time.time()
                st_p.save()
            except Exception:
                pass
            rec = False
            try:
                rec = CW.ensure_balancer(client, cfg, n=bal_n, force=True, fast_ok=True)
            except Exception as e:
                lg.error(f"Watch: пульс-восстановление: {e}")
            if rec:
                ok_p, rtt_p = CW.probe_client(client, timeout=probe_timeout)
                if ok_p:
                    gap = _outage_done(cfg, t_lost, time.time(), lg, ok=True,
                                       why="пульс порта")
                    lg.info(f"Watch: связь восстановлена по пульсу (rtt={rtt_p}, "
                            f"разрыв {gap:.0f} мс)")
                    try:
                        TS.write_status(cfg, state="ok", rtt=rtt_p)
                    except Exception:
                        pass
                else:
                    rec = False
            if not rec:
                _outage_done(cfg, t_lost, time.time(), lg, ok=False,
                             why="пульс порта")
                if full_cycle:
                    lg.warn("Watch: пульс не помог — полный цикл")
                    full_cycle()
                _recover_pause()
            else:
                fail_streak = 0
            strikes = 0
    return 0


def main() -> int:
    cfg = CFG.load_config()
    log.reset_logger(cfg["paths"]["log_dir"])
    lg = log.get_logger()
    if not _acquire_singleton(cfg):
        lg.warn("Watch: страж уже запущен — выходим (защита от дублей)")
        return 0
    import v2ray_updater as VU

    def _full_cycle():
        if VU.acquire_lock("cycle", stale_s=40 * 60, cfg=cfg):
            try:
                VU.run_cycle(cfg, dry=False)
            finally:
                VU.release_lock(cfg)
    return run_watch(cfg, full_cycle=_full_cycle)


if __name__ == "__main__":
    main()
