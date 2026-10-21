# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""posix_selftest.py — автотесты Linux-специфичной части (GPL-3.0).

Запуск:  python3 src/v2ray_updater.py --selftest
(сначала идут общие тесты src/selftest.py, затем эти).

Проверяется то, чего нет на Windows и что нельзя проверить общими тестами:
выбор POSIX-бэкенда и подмена примитивов, XDG-пути, работа с процессами через
/proc и os.kill, поиск гео-баз, рендер юнитов systemd, мягкая деградация
notify-send. Сеть и боевой порт НЕ занимаются: всё в отдельном временном корне.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="forest_posix_selftest_")
os.environ["FOREST_ROOT"] = _TMP
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

import clientwrite_posix as CW     # noqa: E402
import config as CFG               # noqa: E402
import log                         # noqa: E402
import posix_platform as PP        # noqa: E402

FAILED: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    log.reset_logger(os.path.join(_TMP, "logs"))
    print("=" * 64)
    print("P1. POSIX-бэкенд: подмена примитивов в общей сердцевине")
    print("=" * 64)
    import clientwrite as core
    check("clientwrite_posix импортируется на Linux", CW.IS_LINUX is True)
    check("_kill_pid подменён на POSIX", core._kill_pid is CW._posix_kill_pid)
    check("_pid_alive подменён на POSIX", core._pid_alive is CW._posix_pid_alive)
    check("_port_pid подменён на POSIX", core._port_pid is CW._posix_port_pid)
    check("_our_core_pids подменён на POSIX", core._our_core_pids is CW._posix_our_core_pids)
    check("geo_asset_dir подменён на POSIX", core.geo_asset_dir is CW._posix_geo_asset_dir)
    check("_sys_proxy_state подменён на POSIX",
          core._sys_proxy_state is CW._posix_sys_proxy_state)
    check("_sys_proxy_supported подменён на POSIX (механизм своего стола)",
          core._sys_proxy_supported is CW._posix_sys_proxy_supported)
    check("_mode_of подменён на POSIX (только standalone)",
          core._mode_of is CW._posix_mode_of and CW._mode_of({"mode": "v2rayn"}) == "standalone")
    check("имена с подчёркиванием доступны наружу (mirror)",
          hasattr(CW, "_sa_read_active_id") and hasattr(CW, "_is_v2rayn_path")
          and hasattr(CW, "_cfg_sha"))

    print()
    print("=" * 64)
    print("P2. XDG-пути (данные / конфиг / состояние)")
    print("=" * 64)
    os.environ.pop("FOREST_ROOT", None)
    check("данные: ~/.local/share/forest-proxy-updater",
          CFG.data_root().endswith(".local/share/forest-proxy-updater"), CFG.data_root())
    check("конфиг: ~/.config/forest-proxy-updater/config.json",
          CFG.default_config_path().endswith(".config/forest-proxy-updater/config.json"),
          CFG.default_config_path())
    check("состояние: ~/.local/state/forest-proxy-updater",
          CFG.default_state_dir().endswith(".local/state/forest-proxy-updater"),
          CFG.default_state_dir())
    check("постоянный каталог ядра: ~/.local/share/forest-proxy-updater/bin/xray",
          os.path.normcase(CFG.core_dir()) == os.path.normcase(
              os.path.join(CFG.data_root(), "bin", "xray")), CFG.core_dir())
    check("логи: <состояние>/logs", CFG.default_log_dir().endswith("logs"))
    os.environ["FOREST_ROOT"] = _TMP
    check("FOREST_ROOT перекрывает пути (песочница)",
          CFG.app_root() == os.path.abspath(_TMP))
    cfg = CFG.load_config(os.path.join(_TMP, "config.json"))
    check("load_config: root из FOREST_ROOT", cfg["paths"]["root"] == os.path.abspath(_TMP))
    check("load_config: ядро ищется как 'xray'",
          "xray" in CFG.core_binary_names() and "xray.exe" not in CFG.core_binary_names())

    print()
    print("=" * 64)
    print("P3. Процессы: /proc, os.kill, замер реального завершения")
    print("=" * 64)
    p = subprocess.Popen(["sleep", "120"])
    time.sleep(0.2)
    check("_pid_alive: живой процесс -> True", CW._pid_alive(p.pid) is True)
    t0 = time.time()
    ok_kill = CW._kill_pid(p.pid)
    dt = (time.time() - t0) * 1000.0
    check("_kill_pid: снял процесс по SIGTERM", ok_kill is True)
    check("_kill_pid: завершение быстрое (<1.5 с)", dt < 1500, f"{dt:.0f} мс")
    time.sleep(0.1)
    check("_pid_alive: мёртвый процесс -> False", CW._pid_alive(p.pid) is False)
    check("_pid_alive: чужой pid маловероятен -> False", CW._pid_alive(999999) is False)

    # порт занят реальным процессом — ищем его pid через /proc
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    time.sleep(0.1)
    check("port_listening: порт сервера виден", CW.port_listening(port, timeout=1.0) is True)
    pid_found = CW._posix_port_pid(port)
    check("_port_pid: нашёл PID слушателя через /proc/ss",
          str(pid_found) == str(os.getpid()), f"найден {pid_found}, ожидался {os.getpid()}")
    srv.close()
    time.sleep(0.2)
    check("port_listening: закрытый порт -> False", CW.port_listening(port, timeout=0.3) is False)

    print()
    print("=" * 64)
    print("P4. Поиск гео-баз и ядра")
    print("=" * 64)
    check("geo_asset_dir вызывается без исключения",
          isinstance(CW.geo_asset_dir(None, cfg), str))
    core_p = CFG.find_core(cfg)
    check("find_core не падает (может вернуть None)", core_p is None or os.path.isfile(core_p),
          str(core_p))
    if core_p:
        adir = CW.geo_asset_dir(core_p, cfg)
        check("geo_asset_dir нашёл каталог рядом с ядром или системный",
              bool(adir), adir)
        print(f"       ядро: {core_p}")
        print(f"       гео:  {adir!r}")
    else:
        print("       ядро не найдено в PATH — проверка гео-баз пропущена")

    print()
    print("=" * 64)
    print("P5. Юниты systemd: рендер шаблонов без незаполненных подстановок")
    print("=" * 64)
    tdir = PP.unit_template_dir()
    check("каталог шаблонов найден", bool(tdir), tdir)
    rendered = PP.render_units("/usr/local/bin/forest-proxy-updater", "/root/dir",
                               "/home/u/.config/forest-proxy-updater/config.json")
    check("отрендерены все юниты", set(rendered) == set(PP.UNITS), str(list(rendered)))
    for name, text in rendered.items():
        check(f"{name}: нет незаполненных @…@", "@" not in text.replace("@ROOT@", ""),
              [ln for ln in text.splitlines() if "@" in ln][:2])
    check("служба: ExecStart с --run", "--run" in rendered[PP.UNIT_SERVICE])
    check("служба: Restart=always (повторные попытки)",
          "Restart=always" in rendered[PP.UNIT_SERVICE])
    check("служба: StartLimitIntervalSec=0 (без лимита перезапусков)",
          "StartLimitIntervalSec=0" in rendered[PP.UNIT_SERVICE])
    check("служба: KillMode=control-group (ядро не останется сиротой)",
          "KillMode=control-group" in rendered[PP.UNIT_SERVICE])
    check("служба: FOREST_ROOT передан явно",
          "Environment=FOREST_ROOT=" in rendered[PP.UNIT_SERVICE])
    check("таймер: указывает на refresh-службу и Persistent=true",
          "Unit=forest-proxy-updater-refresh.service" in rendered[PP.UNIT_TIMER]
          and "Persistent=true" in rendered[PP.UNIT_TIMER])
    check("refresh: oneshot + --once",
          "Type=oneshot" in rendered[PP.UNIT_REFRESH]
          and "--once" in rendered[PP.UNIT_REFRESH])

    print()
    print("=" * 64)
    print("P6. Индикация: файл состояния + мягкая деградация notify-send")
    print("=" * 64)
    check("notify_available() возвращает bool", isinstance(PP.notify_available(), bool))
    if not PP.notify_available():
        check("notify-send отсутствует -> notify() тихо возвращает False",
              PP.notify("t", "b") is False)
    else:
        check("notify-send найден -> notify_available True", True)
    ind = PP.indicator_path(cfg)
    PP._write_pulse(cfg, 11808, True)
    check("индикация: файл состояния записан", os.path.isfile(ind), ind)
    d = PP.read_indicator(cfg)
    check("индикация: state=ok и есть подсказка",
          d.get("state") == "ok" and bool(d.get("tip")), str(d))
    check("индикация: путь внутри каталога состояния",
          os.path.dirname(ind) == cfg["paths"]["state_dir"])

    print()
    print("=" * 64)
    print("P7. Проверка конфига ядром до остановки (бесшовность), POSIX")
    print("=" * 64)
    if core_p:
        # конфиг с заведомо свободным портом
        c = {"log": {"loglevel": "warning"},
             "inbounds": [{"tag": "socks-in", "port": 1, "listen": "127.0.0.1",
                           "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}],
             "outbounds": [{"tag": "direct", "protocol": "freedom"}]}
        ok_t, err_t = CW._test_config_by_core(core_p, c, cfg)
        check("_test_config_by_core принимает валидный конфиг", ok_t is True, str(err_t))
        bad = {"inbounds": [{"port": 1, "protocol": "no-such-protocol"}]}
        ok_b, err_b = CW._test_config_by_core(core_p, bad, cfg)
        check("_test_config_by_core отвергает невалидный конфиг", ok_b is False, str(err_b))
        print(f"       ошибка ядра (как её увидит лог): {err_b[:120]!r}")
    else:
        print("       ядро не найдено — проверка ядром пропущена")

    print()
    print("=" * 64)
    print("P8. Systemd: доступность и состояние службы")
    print("=" * 64)
    check("systemd_available() -> bool", isinstance(PP.systemd_available(), bool))
    st = PP.service_state()
    check("service_state() возвращает строку", isinstance(st, str) and bool(st), st)
    print(f"       состояние: {st}")

    print()
    print("=" * 64)
    print("P8б. Одноэкземплярный ЗАМОК на POSIX (flock) — как на Windows")
    print("=" * 64)
    # Здесь важно, что на Linux это ТОТ ЖЕ механизм, что на Windows:
    # эксклюзивная блокировка файла, которую держит ядро ОС и снимает при
    # завершении процесса. Проверяем независимой попыткой (fork).
    import instancelock as IL
    import watchdog as WD
    lockp = os.path.join(_TMP, "p8b", "supervisor.lock")
    ok_x, det_x = IL.exclusivity_check(lockp)
    check("замок эксклюзивен: независимый потомок (fork) получает отказ", ok_x, det_x)
    lk = IL.InstanceLock(lockp)
    check("замок захватывается", lk.acquire({"pid": os.getpid()}) is True)
    check("замок: занятость видна пробой (flock на другом fd)",
          IL.held(lockp) is True)
    check("замок: владелец виден как pid", IL.holder_pid(lockp) == os.getpid(),
          str(IL.holder_pid(lockp)))
    lk.release(unlink=False)
    check("замок: после снятия путь свободен", IL.held(lockp) is False)
    # мёртвый владелец: замок снимает ядро (проверяем потомком, который умирает)
    script = (
        "import os,sys;sys.path.insert(0,sys.argv[1]);import instancelock as IL;"
        "lk=IL.InstanceLock(sys.argv[2]);lk.acquire();"
        "os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", script,
                    os.path.join(os.path.dirname(_HERE), "src"), lockp],
                   timeout=60)
    check("замок: после смерти владельца он свободен (ОС снимает сама)",
          IL.held(lockp) is False)
    cfg_l = {"paths": {"root": _TMP, "state_dir": os.path.dirname(lockp)},
             "watchdog": {"enabled": True, "interval_min": 5,
                          "task_name": "ForestSelftest-Linux-Lock"}}
    check("watchdog: путь замка супервизора — state/supervisor.lock",
          WD.marker_path(cfg_l).endswith("supervisor.lock"))
    check("watchdog (Linux): замок свободен до захвата",
          WD.supervisor_lock_held(cfg_l) is False)
    got1 = WD.claim_supervisor(cfg_l)
    got2 = WD.claim_supervisor(cfg_l)
    check("watchdog (Linux): первый становится супервизором", got1 is True)
    check("watchdog (Linux): второй отказывается", got2 is False)
    check("watchdog (Linux): ensure при живом замке не поднимает копию",
          WD.ensure(cfg_l, spawn=False) == 0)
    WD.release_supervisor(cfg_l)
    check("watchdog (Linux): release освобождает замок",
          WD.supervisor_lock_held(cfg_l) is False)
    try:
        import ui_helper as UH
        check("помощник (Linux): путь замка — forest-ui.lock",
              UH.lock_path(cfg_l).endswith("forest-ui.lock"))
        check("помощник (Linux): до захвата running() = False",
              UH.running(cfg_l) is False)
        hk = IL.InstanceLock(UH.lock_path(cfg_l))
        hk.acquire({"pid": os.getpid()})
        check("помощник (Linux): running() видит живой замок",
              UH.running(cfg_l) is True)
        hk.release(unlink=False)
    except Exception as e:
        check("помощник (Linux): проверки замка", False, str(e))

    print()
    print("=" * 64)
    print("P9. Рабочий стол (KDE/GNOME), системный прокси и журнал UTF-8")
    print("=" * 64)
    import desktop_env as DE
    # ── определение стола (живой дефект: KDE определялся как GNOME) ──
    d_kde = DE.detect({"XDG_CURRENT_DESKTOP": "KDE"})
    check("стол: XDG_CURRENT_DESKTOP=KDE -> kde", d_kde["desktop"] == "kde", d_kde)
    d_kde2 = DE.detect({"DESKTOP_SESSION": "plasmawayland",
                        "KDE_SESSION_VERSION": "6"})
    check("стол: DESKTOP_SESSION=plasmawayland -> kde", d_kde2["desktop"] == "kde")
    d_kde3 = DE.detect({"KDE_FULL_SESSION": "true"})
    check("стол: KDE_FULL_SESSION=true -> kde", d_kde3["desktop"] == "kde")
    d_gn = DE.detect({"XDG_CURRENT_DESKTOP": "GNOME"})
    check("стол: XDG_CURRENT_DESKTOP=GNOME -> gnome", d_gn["desktop"] == "gnome")
    d_gn2 = DE.detect({"XDG_CURRENT_DESKTOP": "X-Cinnamon"})
    check("стол: X-Cinnamon -> gnome-подобный (GSettings)", d_gn2["desktop"] == "gnome")
    d_none = DE.detect({})
    check("стол: пустое окружение без процессов -> other (честный WARN, не GNOME)",
          d_none["desktop"] == "other", d_none)
    d_proc = DE.detect({}, procs=["kwin_wayland", "plasmashell"])
    check("стол: пустое окружение + процессы kwin/plasmashell -> kde",
          d_proc["desktop"] == "kde" and d_proc["source"] == "proc", d_proc)
    d_mix = DE.detect({"XDG_CURRENT_DESKTOP": "KDE:GNOME"})
    check("стол: составное KDE:GNOME -> kde (KDE проверяется первым)",
          d_mix["desktop"] == "kde")
    check("сессия: WAYLAND_DISPLAY -> wayland",
          DE.session_type({"WAYLAND_DISPLAY": "wayland-0"}) == "wayland")
    check("сессия: XDG_SESSION_TYPE=x11 -> x11",
          DE.session_type({"XDG_SESSION_TYPE": "x11"}) == "x11")

    # ── поиск графического сеанса по /proc (фальшивое дерево) ──
    fake = os.path.join(_TMP, "fakeproc")
    fpid = "4242"
    os.makedirs(os.path.join(fake, fpid), exist_ok=True)
    with open(os.path.join(fake, fpid, "comm"), "w", encoding="utf-8") as f:
        f.write("kwin_wayland\n")
    envs = b"USER=forest\x00WAYLAND_DISPLAY=wayland-0\x00" \
           b"XDG_RUNTIME_DIR=/run/user/1000\x00" \
           b"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\x00" \
           b"XDG_CURRENT_DESKTOP=KDE\x00"
    with open(os.path.join(fake, fpid, "environ"), "wb") as f:
        f.write(envs)
    sess_found = DE.scan_sessions(proc_root=fake, skip_uid=None)
    check("сеанс: /proc-скан находит графический сеанс (kwin_wayland + D-Bus)",
          len(sess_found) == 1 and sess_found[0]["comm"] == "kwin_wayland",
          f"найдено {len(sess_found)}")
    if sess_found:
        check("сеанс: стол из окружения процесса сеанса -> kde",
              (sess_found[0]["env"].get("XDG_CURRENT_DESKTOP") == "KDE"))
    nosess = DE.find_graphical_session(env={}, proc_root=os.path.join(_TMP, "noproc"),
                                       me=0)
    check("сеанс: root без сеанса -> ok=False и понятная причина",
          nosess["ok"] is False and "сеанс" in nosess["reason"], nosess.get("reason"))

    # ── журнал: кириллица должна доходить как UTF-8, без '?????' ──
    import safestdio as SD
    check("журнал: stdout на Linux принимает UTF-8 (say не портит кириллицу)",
          SD.unicode_stdout() is True)
    import io as _io
    import contextlib as _ctx
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        SD.say("Проверка кириллицы: связь есть, узлов 10")
    said = buf.getvalue()
    check("say(): кириллица выходит как есть (нет литеральных '?')",
          "Проверка кириллицы" in said and "?????" not in said, repr(said))
    lp = os.path.join(_TMP, "logs_utf8")
    lg_u = log.Logger(lp)
    lg_u.info("Проверка кириллицы в журнале: WARN не было")
    lg_u.close()
    raw = open(os.path.join(lp, "main.log"), "rb").read()
    txt = raw.decode("utf-8")
    check("файл журнала: строка с кириллицей читается как UTF-8",
          "Проверка кириллицы" in txt, txt[-120:])
    check("файл журнала: в нём НЕТ '?????' вместо букв", b"?????" not in raw)
    check("файл журнала: нет ни одного байта-заменителя ошибки декодирования",
          "\ufffd" not in txt)

    # анти-спам журнала: одна и та же строка не повторяется каждые 30-60 с
    lp2 = os.path.join(_TMP, "logs_spam")
    lg_s = log.Logger(lp2)
    for _ in range(8):
        lg_s.warn("Системный прокси (GNOME) установлен на 127.0.0.1:10808")
    lg_s.close()
    spam = open(os.path.join(lp2, "main.log"), encoding="utf-8").read()
    n_spam = spam.count("Системный прокси (GNOME) установлен")
    check("анти-спам журнала: 8 одинаковых строк подряд -> 1 строка",
          n_spam == 1, f"строк {n_spam}")

    # ── системный прокси: недоступный механизм -> один раз и без цикла ──
    real_ctx = CW.desktop_context
    real_gs = getattr(CW, "_HAS_GSETTINGS", None)
    try:
        CW.desktop_context = lambda refresh=False: {
            "session": {"ok": False, "reason": "тест: нет сеанса",
                        "prefix": [], "env": {}, "uid": None},
            "info": {"desktop": "other", "name": "тест-стол"}, "at": time.time()}
        CW._reset_proxy_notes()
        CW._reset_desktop_cache()
        lp3 = os.path.join(_TMP, "logs_proxy")
        log.reset_logger(lp3)
        supported, why = CW._posix_sys_proxy_supported()
        check("прокси: без сеанса пользователя механизм недоступен (не ошибка)",
              supported is False and "сеанс" in why, why)
        res = [CW._posix_sys_proxy_apply("127.0.0.1:10808") for _ in range(4)]
        check("прокси: 4 попытки при недоступном механизме -> ни одна не «успех»",
              res == [False, False, False, False], res)
        log.get_logger().close()
        line_no = open(os.path.join(lp3, "main.log"), encoding="utf-8").read()
        n_msg = line_no.count("Системный прокси: не настраиваю")
        check("прокси: о недоступности сказано РОВНО ОДИН раз (нет спама)",
              n_msg == 1, f"строк {n_msg}")

        # ── KDE: запись kioslaverc + ПРОВЕРКА ЧТЕНИЕМ (без kwriteconfig) ──
        home = os.path.join(_TMP, "kdehome")
        os.makedirs(os.path.join(home, ".config"), exist_ok=True)
        sess_kde = {"ok": True, "uid": None, "home": home, "prefix": [],
                    "env": {"XDG_CURRENT_DESKTOP": "KDE", "HOME": home}}
        real_kw, real_reparse = CW._kwriteconfig, CW._kde_reparse
        CW._kwriteconfig = lambda: ""
        CW._kde_reparse = lambda sess: None
        CW._HAS_GSETTINGS = False        # проверяем ИМЕННО ветку kioslaverc
        try:
            CW.desktop_context = lambda refresh=False: {
                "session": sess_kde,
                "info": {"desktop": "kde", "name": "KDE Plasma"}, "at": time.time()}
            ok_kde = CW._kde_apply(sess_kde, "127.0.0.1:10808")
            on_k, srv_k = CW._kde_read(sess_kde)
            check("прокси KDE: kioslaverc записан и ПРОЧИТАН обратно (ProxyType=1)",
                  ok_kde is True and on_k is True and srv_k == "127.0.0.1:10808",
                  f"ok={ok_kde}, on={on_k}, srv={srv_k!r}")
            kio = open(os.path.join(home, ".config", "kioslaverc"),
                       encoding="utf-8").read()
            check("прокси KDE: в файле есть httpProxy и httpsProxy на наш порт",
                  "ProxyType=1" in kio and "http://127.0.0.1:10808" in kio
                  and "httpsProxy=http://127.0.0.1:10808" in kio, kio[:200])
            check("прокси KDE: состояние читается как наш сервер (повтора не будет)",
                  CW._posix_sys_proxy_state() == (True, "127.0.0.1:10808"))
            CW._reset_proxy_notes()
            n_before = len(open(os.path.join(lp3, "main.log"),
                                encoding="utf-8").read())
            CW._posix_sys_proxy_state()          # повторные вызовы состояния
            n_after = len(open(os.path.join(lp3, "main.log"),
                               encoding="utf-8").read())
            check("прокси KDE: чтение состояния ничего не пишет в журнал",
                  n_after == n_before, f"{n_before} -> {n_after} байт")
            ok_dis = CW._kde_disable(sess_kde)
            check("прокси KDE: disable пишет ProxyType=0 и читается как выключено",
                  ok_dis is True and CW._kde_read(sess_kde)[0] is False)
        finally:
            CW._kwriteconfig, CW._kde_reparse = real_kw, real_reparse
    finally:
        CW.desktop_context = real_ctx
        CW._reset_proxy_notes()
        CW._reset_desktop_cache()
        CW._HAS_GSETTINGS = real_gs
        log.reset_logger(os.path.join(_TMP, "logs"))


    print()
    print("=" * 64)
    if FAILED:
        print(f"POSIX-тесты НЕ пройдены: {len(FAILED)}")
        for f in FAILED:
            print(f"  - {f}")
        _cleanup()
        return 1
    print("POSIX-тесты пройдены.")
    print("=" * 64)
    _cleanup()
    return 0


def _cleanup():
    try:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
