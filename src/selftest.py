# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Автотесты FOREST's Proxy Auto-updater (GPL-3.0).

Запуск:  python v2ray_updater.py --selftest   (или  python selftest.py)

Проверяет то, что не требует сети и живого ядра: API-поверхность, стор
серверов, генерацию конфигов ядра и балансировщика, диспетчер режимов,
i18n, первый запуск конфига, поиск ядра. Системный прокси НЕ трогается,
боевые порты НЕ занимаются — всё пишется в отдельный временный каталог.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="forest_selftest_")
os.environ["FOREST_ROOT"] = _TMP          # не трогаем реальный %LOCALAPPDATA%
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json  # noqa: E402

# POSIX: проверяем POSIX-бэкенд, иначе тесты не покрывали бы то, чем реально
# работает Linux-версия.
if os.name == "nt":
    import clientwrite as CW  # noqa: E402
else:
    import clientwrite_posix as CW  # noqa: E402
import config as CFG  # noqa: E402
import safestdio as SD
import i18n  # noqa: E402
import log  # noqa: E402
import parser as P  # noqa: E402

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _say(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def _say(text="") -> None:
    """Печать в stdout через безопасный слой (см. src/safestdio.py).

    Оконная сборка не имеет консоли, а её «кодировка» на Windows — cp1251:
    печать символа вне cp1251 давала UnicodeEncodeError и МОДАЛЬНОЕ ОКНО
    PyInstaller на рабочем столе.
    """
    SD.say(text)


def main() -> int:
    log.reset_logger(os.path.join(_TMP, "logs"))
    _say("=" * 64)
    _say("1. API-поверхность (имена, которые зовут v2ray_updater/watch)")
    _say("=" * 64)
    needed = ["AUTO_TAG", "StateStore", "probe_client", "measure_speed_kbps",
              "ensure_port_free", "ensure_balancer", "update_hotcache",
              "row_to_server", "read_db_rows", "read_active_id", "write_active_id",
              "guard_cycle", "bl_filter", "activate", "kill_kernels",
              "write_core_config", "write_balancer_config", "restart_core",
              "wait_and_probe", "balancer_pool", "server_to_row", "port_busy",
              "build_balancer_config", "geo_asset_dir", "ru_direct_enabled",
              "proxy_off", "set_proxy_off", "save_proxy_orig", "restore_system_proxy",
              "start_core", "stop_core", "ensure_system_proxy", "write_bin_config"]
    for n in needed:
        check(f"есть {n}", hasattr(CW, n))

    _say()
    _say("=" * 64)
    _say("2. server_to_row -> row_to_server (round-trip по протоколам)")
    _say("=" * 64)
    cases = [("vless", "1.2.3.4", 443, "ws"),
             ("vmess", "5.6.7.8", 8443, "raw"),
             ("trojan", "9.9.9.9", 2053, "grpc"),
             ("ss", "10.0.0.1", 8388, "raw")]
    for proto, addr, port, net in cases:
        s = P.Server(protocol=proto, address=addr, port=port)
        s.password = "SECRET"
        s.remark = f"{proto}-node"
        s.network = net
        s.host = "example.com"
        s.path = "/path"
        s.sni = "sni.example.com"
        s.stream_security = "tls"
        s._row_index_id = "42"
        if proto == "ss":
            s.method = "aes-256-gcm"
        if proto == "vmess":
            s.method = "auto"
        row = CW.server_to_row(s)
        s2 = CW.row_to_server(row)
        ok = (s2 is not None and s2.address == addr and s2.port == port
              and s2.protocol == proto and s2.host == s.host and s2.path == s.path
              and s2.sni == s.sni and s2.password == "SECRET")
        check(f"{proto}: адрес/порт/протокол/транспорт", ok,
              f"got {s2.protocol if s2 else None} {s2.address if s2 else None}")

    _say()
    _say("=" * 64)
    _say("3. Стор серверов (запись/чтение, [AUTO]-тег)")
    _say("=" * 64)
    store = os.path.join(_TMP, "servers.json")
    s = P.Server(protocol="vless", address="1.1.1.1", port=443)
    s.password = "uuid-1"
    s.remark = "node A"
    s._row_index_id = "1"
    n = CW.write_auto_group(store, [s], update_stats=False)
    check("write_auto_group вернул 1", n == 1, f"n={n}")
    rows = CW.read_db_rows(store)
    check("store прочитан (1 строка)", len(rows) == 1, f"len={len(rows)}")
    check("[AUTO]-тег выставлен", (rows[0].get("Remarks") or "").startswith(CW.AUTO_TAG),
          rows[0].get("Remarks") if rows else "—")
    with open(store, encoding="utf-8") as f:
        data = json.load(f)
    check("формат store {servers,active_id,next_index}",
          all(k in data for k in ("servers", "active_id", "next_index")))
    CW.write_active_id(os.path.join(_TMP, "forest_xray.json"), "1")
    check("write/read_active_id (standalone)", CW.read_active_id(os.path.join(_TMP, "forest_xray.json")) == "1")

    _say()
    _say("=" * 64)
    _say("4. Генерация конфига ядра (один сервер)")
    _say("=" * 64)
    cfg_path = os.path.join(_TMP, "xray_config.json")
    client = {"cfg": cfg_path, "socks_port": 11808, "torrent_port": 11820, "mode": "standalone"}
    srv = P.Server(protocol="vless", address="1.2.3.4", port=443)
    srv.password = "uuid-x"
    srv.network = "ws"
    srv.host = "h.com"
    srv.path = "/p"
    ok = CW.write_core_config(client, srv, {"client": {}})
    check("write_core_config вернул True", ok)
    with open(cfg_path, encoding="utf-8") as f:
        conf = json.load(f)
    check("есть inbounds (mixed socks на 11808)", any(
        i.get("protocol") == "mixed" and i.get("port") == 11808
        for i in conf.get("inbounds", [])))
    check("есть outbound с тегом proxy", any(
        o.get("tag") == "proxy" for o in conf.get("outbounds", [])))
    check("есть outbound direct (freedom)", any(
        o.get("protocol") == "freedom" for o in conf.get("outbounds", [])))
    ob = [o for o in conf["outbounds"] if o.get("tag") == "proxy"][0]
    check("outbound vless использует settings.vnext", "vnext" in ob.get("settings", {}))

    _say()
    _say("=" * 64)
    _say("5. Генерация конфига балансировщика (пул + observatory)")
    _say("=" * 64)
    pool = []
    for i in range(3):
        x = P.Server(protocol="vless", address=f"10.0.0.{i+1}", port=443)
        x.password = f"uuid-{i}"
        pool.append(x)
    ok = CW.write_balancer_config(client, pool, {"client": {}})
    check("write_balancer_config вернул True", ok)
    with open(cfg_path, encoding="utf-8") as f:
        bconf = json.load(f)
    tags = [o.get("tag") for o in bconf.get("outbounds", [])]
    check("пул из 3 outbound с префиксом proxy-out-",
          sum(1 for t in tags if t and t.startswith("proxy-out-")) == 3, f"tags={tags}")
    check("есть routing.balancers с selector ['proxy-out']",
          any(b.get("selector") == ["proxy-out"]
              for b in bconf.get("routing", {}).get("balancers", [])))
    check("есть observatory (нативный health-check)",
          "observatory" in bconf and
          bconf["observatory"].get("subjectSelector") == ["proxy-out"])
    check("есть правило маршрутизации на балансировщик",
          any(r.get("balancerTag") == "bal" for r in bconf.get("routing", {}).get("rules", [])))
    tin = [i for i in bconf.get("inbounds", []) if i.get("tag") == "torrent-in"]
    check("есть вход torrent-in (порт 11820, socks, только 127.0.0.1)",
          bool(tin) and tin[0].get("port") == 11820
          and tin[0].get("listen") == "127.0.0.1" and tin[0].get("protocol") == "socks")
    check("torrent-in: UDP включён", bool(tin) and tin[0].get("settings", {}).get("udp") is True)
    rules_b = bconf.get("routing", {}).get("rules", [])
    check("ПЕРВОЕ правило — локальные имена напрямую",
          bool(rules_b) and rules_b[0].get("outboundTag") == "direct"
          and "full:retracker.local" in (rules_b[0].get("domain") or []))
    check("ВТОРОЕ правило — torrent-in через балансировщик",
          len(rules_b) > 1 and rules_b[1].get("inboundTag") == ["torrent-in"]
          and rules_b[1].get("balancerTag") == "bal")

    _say()
    _say("=" * 64)
    _say("6. РФ-маршрутизация включается только при наличии гео-баз")
    _say("=" * 64)
    geo_tmp = os.path.join(_TMP, "geo")
    os.makedirs(geo_tmp, exist_ok=True)
    open(os.path.join(geo_tmp, "geosite.dat"), "wb").close()
    open(os.path.join(geo_tmp, "geoip.dat"), "wb").close()
    old_env = os.environ.get("XRAY_LOCATION_ASSET")
    os.environ["XRAY_LOCATION_ASSET"] = geo_tmp
    try:
        check("geo_asset_dir находит базы по XRAY_LOCATION_ASSET",
              CW.geo_asset_dir() == geo_tmp)
        check("ru_direct_enabled=True при наличии баз",
              CW.ru_direct_enabled({"watch": {"ru_direct": True}}) is True)
        check("ru_direct_enabled=False по умолчанию (в конфиге выключено)",
              CW.ru_direct_enabled({"watch": {"ru_direct": False}}) is False)
        CW.write_balancer_config(client, pool, {"client": {}, "watch": {"ru_direct": True}})
        with open(cfg_path, encoding="utf-8") as f:
            ruconf = json.load(f)
        rrules = ruconf["routing"]["rules"]
        check("при ru_direct есть правило direct по доменам",
              any(r.get("outboundTag") == "direct" and r.get("domain") for r in rrules))
        check("при ru_direct есть правило direct по IP",
              any(r.get("outboundTag") == "direct" and r.get("ip") for r in rrules))
        # Метки, которые обязаны присутствовать в РФ-правиле (и не должно быть
        # категорий, отсутствующих во вшитом geosite.dat — иначе конфиг ядра
        # отвергается целиком).
        doms = [d for r in rrules if r.get("outboundTag") == "direct"
                for d in (r.get("domain") or [])]
        ips = [i for r in rrules if r.get("outboundTag") == "direct"
               for i in (r.get("ip") or [])]
        check("РФ-правило содержит geosite:category-ru и geosite:tld-ru",
              "geosite:category-ru" in doms and "geosite:tld-ru" in doms, str(doms))
        check("нет отсутствующих категорий (category-medicine-ru)",
              not any("category-medicine-ru" in d for d in doms), str(doms))
        check("РФ-правило содержит geoip:ru", "geoip:ru" in ips, str(ips))
        check("смена ru_direct меняет ОТПЕЧАТОК конфига (корень дефекта)",
              CW._conf_sha(ruconf) != CW._conf_sha(
                  CW.build_balancer_config(client, pool,
                                           ru_direct=False)))
        check("отпечаток одного пула при этом НЕ меняется (поэтому его мало)",
              CW._pool_sig(pool) == CW._pool_sig(list(pool)))
        check("порядок правил: local -> torrent -> RU",
              rrules[0].get("outboundTag") == "direct"
              and rrules[1].get("inboundTag") == ["torrent-in"])
        check("DNS согласован (expectIPs при ru_direct)",
              "expectIPs" in json.dumps(ruconf.get("dns", {}), ensure_ascii=False))
        check("domainStrategy=IPIfNonMatch",
              ruconf["routing"].get("domainStrategy") == "IPIfNonMatch")
    finally:
        if old_env is None:
            os.environ.pop("XRAY_LOCATION_ASSET", None)
        else:
            os.environ["XRAY_LOCATION_ASSET"] = old_env

    _say()
    _say("=" * 64)
    _say("7. Диспетчер режимов (standalone / v2rayn)")
    _say("=" * 64)
    if os.name == "nt":
        check("_mode_of по client.mode=v2rayn", CW._mode_of({"mode": "v2rayn"}) == "v2rayn")
    else:
        # На Linux v2rayN неприменим — диспетчер принудительно уходит в standalone
        check("_mode_of на POSIX всегда standalone",
              CW._mode_of({"mode": "v2rayn"}) == "standalone")
    check("_mode_of по cfg.client.mode=standalone",
          CW._mode_of(None, {"client": {"mode": "standalone"}}) == "standalone")
    check("_mode_of по умолчанию standalone", CW._mode_of({}) == "standalone")
    check("_is_v2rayn_path: guiNConfig.json", CW._is_v2rayn_path("X/guiNConfig.json") is True)
    check("_is_v2rayn_path: servers.json -> False", CW._is_v2rayn_path("X/servers.json") is False)
    check("read_db_rows(.json) читает наш стор", isinstance(CW.read_db_rows(store), list))
    try:
        import clientwrite_v2rayn as V2
        check("бэкенд v2rayN импортируется", hasattr(V2, "activate") and hasattr(V2, "ensure_balancer"))
    except Exception as e:
        check("бэкенд v2rayN импортируется", False, str(e))
    try:
        import clientwrite_posix as POS
        check("POSIX-бэкенд импортируется", hasattr(POS, "write_balancer_config"))
    except Exception as e:
        check("POSIX-бэкенд импортируется", False, str(e))

    _say()
    _say("=" * 64)
    _say("8. Конфиг: первый запуск создаёт файл из config.example.json")
    _say("=" * 64)
    ex = CFG.example_config_path()
    check("config.example.json доступен", bool(ex), str(ex))
    fresh = os.path.join(_TMP, "fresh_root", "config.json")
    cfg2 = CFG.load_config(fresh)
    check("config.json создан при первом запуске", os.path.isfile(fresh))
    check("дефолт client.mode=standalone", cfg2["client"]["mode"] == "standalone")
    check("дефолт diversity.per_subnet_cap=1", cfg2["diversity"]["per_subnet_cap"] == 1)
    check("дефолт diversity.min_pool_after_cap=12", cfg2["diversity"]["min_pool_after_cap"] == 12)
    check("дефолт watch.ru_direct выключен", cfg2["watch"]["ru_direct"] is False)
    check("дефолт test.min_speed_kbps=1000", int(cfg2["test"]["min_speed_kbps"]) == 1000)
    check("дефолт test.gate_urls пуст (без шлюза Cloudflare)", not cfg2["test"].get("gate_urls"))
    check("порты по умолчанию 10808/10820",
          cfg2["client"]["socks_port"] == 10808 and cfg2["client"]["torrent_port"] == 10820)
    check("paths развёрнуты (state_dir/log_dir)", "state_dir" in cfg2["paths"] and "log_dir" in cfg2["paths"])

    _say()
    _say("=" * 64)
    _say("9. Поиск ядра и прочее")
    _say("=" * 64)
    dummy = os.path.join(_TMP, "xray.exe")
    open(dummy, "wb").close()
    check("find_core берёт явный путь из конфига",
          CFG.find_core({"client": {"core": dummy}}) == dummy)
    check("find_client standalone отдаёт стор и конфиг",
          "servers.json" in CFG.find_client({"client": {"mode": "standalone",
                                                        "core": dummy},
                                             "paths": cfg2["paths"]})["db"])
    sig1 = CW._pool_sig(pool)
    check("_pool_sig стабилен", sig1 == CW._pool_sig(pool))
    check("port_busy(свободный порт 59999)=False", CW.port_busy(59999) is False)
    check("read_active_id без стора -> None/''",
          not CW._sa_read_active_id(os.path.join(_TMP, "nostore", "nope.json")))
    i18n.set_lang("en")
    check("i18n переключается на en", i18n.t("status.title") == "Status")
    i18n.set_lang("ru")
    check("i18n переключается на ru", i18n.t("status.title") == "Статус")
    # системный прокси только ЧИТАЕМ (не меняем)
    en, srv_p = CW._sys_proxy_state()
    check("системный прокси читается (не изменяется)", isinstance(en, bool))

    _say()
    _say("=" * 64)
    _say("10. Бесшовное переключение: хэш конфига, проверка до остановки ядра")
    _say("=" * 64)
    c1 = {"a": 1, "b": [1, 2, {"c": 3}]}
    c2 = {"b": [1, 2, {"c": 3}], "a": 1}
    check("_conf_sha не зависит от порядка ключей",
          CW._conf_sha(c1) == CW._conf_sha(c2) and bool(CW._conf_sha(c1)))
    check("_conf_sha различает разные конфиги",
          CW._conf_sha(c1) != CW._conf_sha({"a": 2, "b": [1, 2, {"c": 3}]}))
    check("_cfg_sha читает конфиг с диска",
          CW._cfg_sha(cfg_path) == CW._conf_sha(ruconf),
          f"{CW._cfg_sha(cfg_path)[:8]} != {CW._conf_sha(ruconf)[:8]}")
    check("_cfg_sha(нет файла) -> ''", CW._cfg_sha(os.path.join(_TMP, "nope.json")) == "")
    check("_test_config_by_core без ядра -> unsupported (есть фолбэк)",
          CW._test_config_by_core("", {"x": 1}, cfg2) == (False, "unsupported"))
    check("_validate_new_config есть (проверка конфига до остановки ядра)",
          callable(CW._validate_new_config))
    check("_restart_core_timed есть (замер разрыва в мс)",
          callable(CW._restart_core_timed))
    check("build_single_config вернул конфиг с inbounds/outbounds",
          isinstance(CW.build_single_config(client, srv, {"client": {}}), dict))

    _say()
    _say("=" * 64)
    _say("11. Установщик: копирование .exe и команда запуска без лишних аргументов")
    _say("=" * 64)
    import v2ray_updater as VU
    check("app_root подменён (FOREST_ROOT)",
          CFG.app_root().replace("\\", "/").lower() == _TMP.replace("\\", "/").lower())
    # изоляция от переменных окружения вызывающей оболочки
    for _v in ("FOREST_REG_RUN_KEY", "FOREST_RUN_VALUE", "FOREST_EXE_SRC",
               "FOREST_SHORTCUT_DIR"):
        os.environ.pop(_v, None)
    check("ветка автозапуска по умолчанию — HKCU\\...\\Run",
          VU._run_key_path() == VU.RUN_KEY_DEFAULT)
    os.environ["FOREST_REG_RUN_KEY"] = r"Software\ForestSelftest\Run"
    check("ветка автозапуска переопределяется (песочница)",
          VU._run_key_path() == r"Software\ForestSelftest\Run")
    os.environ.pop("FOREST_REG_RUN_KEY", None)
    # подделка «собранного .exe»: 2 МБ, начинается с MZ
    fake_exe = os.path.join(_TMP, "fake", "ForestProxyUpdater.exe")
    os.makedirs(os.path.dirname(fake_exe), exist_ok=True)
    with open(fake_exe, "wb") as f:
        f.write(b"MZ" + b"\0" * (2 * 1024 * 1024))
    check("_is_pe принимает MZ+размер", VU._is_pe(fake_exe) is True)
    check("_is_pe отвергает мелкий файл",
          VU._is_pe(os.path.join(_TMP, "xray.exe")) is False)
    os.environ["FOREST_EXE_SRC"] = fake_exe
    try:
        tgt, argstr, mode = VU.prepare_install_launch(cfg2)
    finally:
        os.environ.pop("FOREST_EXE_SRC", None)
    want = os.path.join(cfg2["paths"]["root"], VU.APP_EXE_NAME)
    check("режим exe: цель — exe в каталоге УСТАНОВКИ", tgt == want, tgt)
    check("режим exe: скопирован в каталог установки", os.path.isfile(want))
    check("режим exe: команда запуска ровно '--run'", argstr == "--run", argstr)
    check("режим exe: лишнего аргумента-скрипта нет",
          ".py" not in argstr and argstr.count("--") == 1)
    check("_schedule_delete без путей -> False", VU._schedule_delete([], "") is False)

    _say()
    _say("=" * 64)
    _say("12. StateStore: save() не теряет ключи чужого экземпляра")
    _say("=" * 64)
    st_a = CW.StateStore(cfg2)
    st_a.data["k_from_a"] = 1
    st_a.save()
    st_b = CW.StateStore(cfg2)          # загрузился ДО записи st_a? нет — после
    st_b.data["k_from_b"] = 2
    st_b.data["k_from_a"] = 1           # устаревшая копия-снимок
    st_b.save()
    disk = json.load(open(st_a.path, encoding="utf-8"))
    check("ключи обоих экземпляров на диске",
          disk.get("k_from_a") == 1 and disk.get("k_from_b") == 2, str(disk))
    st_c = CW.StateStore(cfg2)
    check("после merge-записи чтение видит оба ключа",
          st_c.data.get("k_from_b") == 2 and st_c.data.get("k_from_a") == 1)

    _say()
    _say("=" * 64)
    _say("12б. ОБРЫВ СВЯЗИ -> карточка уведомления: порог и РЕАЛЬНЫЕ времена")
    _say("=" * 64)
    # Сторож обязан показать КАЖДЫЙ настоящий обрыв (в т.ч. быстрый «пульсом»),
    # но молчать, если связь для пользователя не прерывалась (короткий
    # ПЛАНОВЫЙ перезапуск ядра). Решение принимается по ЗАМЕРУ разрыва.
    import log as _LG
    import tray_status as TS
    import watch as W

    _lg = _LG.get_logger()
    cfg_w = dict(cfg2)
    cfg_w["paths"] = dict(cfg2["paths"])
    cfg_w["watch"] = dict(cfg2.get("watch") or {})
    stw = CW.StateStore(cfg_w)
    stw.data["balancer_gap_ms"] = 120.0      # плановый перезапуск: порт за 120 мс
    stw.save()
    TS.write_status(cfg_w, state="ok", drop_proc=True, tip="x")
    t0w = time.time() - 0.6
    gap_small = W._outage_done(cfg_w, t0w, time.time(), _lg, ok=True)
    st_after = TS.read_status(cfg_w) or {}
    check("короткий разрыв (< порога) НЕ даёт карточки (нет уведомления-спама)",
          gap_small < W._notify_gap_ms(cfg_w)
          and not (st_after.get("proc") or {}).get("steps"),
          f"gap={gap_small:.0f} мс, proc={bool((st_after.get('proc') or {}))}")
    # РЕАЛЬНЫЙ обрыв: замер 712 мс — карточка с этапами и настоящим временем
    stw = CW.StateStore(cfg_w)
    stw.data["balancer_gap_ms"] = 712.0
    stw.save()
    t0w = time.time() - 3.4
    gap_big = W._outage_done(cfg_w, t0w, time.time(), _lg, ok=True)
    st_after = TS.read_status(cfg_w) or {}
    proc_w = st_after.get("proc") or {}
    steps_w = proc_w.get("steps") or []
    check("реальный разрыв (>= порога) ДАЁТ карточку с этапами",
          gap_big >= W._notify_gap_ms(cfg_w) and len(steps_w) == 3
          and proc_w.get("final") is True and st_after.get("state") == "ok",
          f"gap={gap_big:.0f} мс, steps={len(steps_w)}, final={proc_w.get('final')}")
    check("этапы карточки несут РЕАЛЬНЫЕ времена в мс (50 / разрыв / итог)",
          bool(steps_w) and int(round(float(steps_w[1].get("t") or 0) * 1000)) == 712
          and all(s.get("done") for s in steps_w),
          str([(s.get("nm"), round(float(s.get("t") or 0) * 1000)) for s in steps_w]))
    import status_info as SIw
    n_w = SIw.notif(proc_w)
    check("подпись/футер карточки — с ИТОГОВЫМ временем и без «0.00 с»",
          "0.00" not in n_w["sub"] and "0.00" not in n_w["footer"]
          and ("с" in n_w["sub"]), f'{n_w["sub"]!r} / {n_w["footer"]!r}')
    # ПРОВАЛ восстановления: карточка тоже есть (и с этапами)
    stw = CW.StateStore(cfg_w)
    stw.data["balancer_gap_ms"] = 900.0
    stw.save()
    W._outage_done(cfg_w, time.time() - 5.0, time.time(), _lg, ok=False)
    proc_f = (TS.read_status(cfg_w) or {}).get("proc") or {}
    check("провал восстановления тоже даёт карточку (final+failed, этапы)",
          proc_f.get("final") is True and proc_f.get("failed") is True
          and len(proc_f.get("steps") or []) == 3,
          f"final={proc_f.get('final')}, failed={proc_f.get('failed')}")
    check("порог показа карточки — из настроек (watch.notify_min_gap_ms)",
          float(W._notify_gap_ms({"watch": {"notify_min_gap_ms": 111}})) == 111.0
          and float(W._notify_gap_ms({})) == W.NOTIFY_MIN_GAP_MS,
          str(W.NOTIFY_MIN_GAP_MS))

    _say()
    _say("=" * 64)
    _say("13. Постоянная копия ядра (вне временного _MEI) и уборка распаковок")
    _say("=" * 64)
    # Ядро, запущенное из каталога распаковки onefile (_MEIPASS), переживает
    # выход программы, файл занят — уборщик PyInstaller не может снести каталог
    # (модальное окно). Поэтому ядро и гео-базы обязаны уезжать в ПОСТОЯННЫЙ
    # каталог. Здесь бандл подделывается: sys._MEIPASS -> _TMP/bundle.
    bin_name = CFG.core_binary_names()[0]
    check("core_dir под FOREST_ROOT: <root>/bin/xray",
          os.path.normcase(CFG.core_dir()) ==
          os.path.normcase(os.path.join(_TMP, "bin", "xray")), CFG.core_dir())
    bundle_root = os.path.join(_TMP, "bundle")
    b_core = os.path.join(bundle_root, "bin", "xray")
    os.makedirs(b_core, exist_ok=True)
    payload = {bin_name: b"FAKE-CORE-v1" * 10, "geoip.dat": b"GEOIP-v1" * 5,
               "geosite.dat": b"GEOSITE-v1" * 5, "LICENSE": b"GPLv3"}
    for n, body in payload.items():
        with open(os.path.join(b_core, n), "wb") as f:
            f.write(body)
    had_meipass = hasattr(sys, "_MEIPASS")
    old_meipass = getattr(sys, "_MEIPASS", None)
    sys._MEIPASS = bundle_root
    try:
        check("bundle_core_dir видит bin/xray бандла",
              os.path.normcase(os.path.abspath(CFG.bundle_core_dir())) ==
              os.path.normcase(os.path.abspath(b_core)), CFG.bundle_core_dir())
        got = CFG.ensure_core_installed(force=True)
        want_core = os.path.join(CFG.core_dir(), bin_name)
        check("ensure_core_installed вернул постоянный путь",
              os.path.normcase(got) == os.path.normcase(want_core), str(got))
        same = all(os.path.isfile(os.path.join(CFG.core_dir(), n)) and
                   os.path.getsize(os.path.join(CFG.core_dir(), n)) == len(payload[n])
                   for n in payload)
        check("ядро + обе гео-базы + LICENSE на месте, размеры совпали", same)
        h_src = CFG._sha256_file(os.path.join(b_core, bin_name))
        h_dst = CFG._sha256_file(want_core)
        check("sha256 ядра совпал (копия побитово та же)", h_src == h_dst)
        if os.name != "nt":
            check("на копии выставлен бит исполняемости",
                  os.access(want_core, os.X_OK))
        # второй запуск: содержимое то же -> файл НЕ переписывается
        mt1 = os.stat(want_core).st_mtime_ns
        time.sleep(0.02)
        CFG.ensure_core_installed(force=False)
        check("без изменений в бандле копия не переписывается",
              os.stat(want_core).st_mtime_ns == mt1)
        # обновили бандл (размер другой) -> копия обновляется сама
        with open(os.path.join(b_core, bin_name), "wb") as f:
            f.write(b"FAKE-CORE-v2-LONGER" * 20)
        CFG.ensure_core_installed(force=False)
        check("новая версия в бандле доехала в постоянный каталог",
              CFG._sha256_file(want_core) ==
              CFG._sha256_file(os.path.join(b_core, bin_name)))
        # ГЛАВНОЕ: find_core выбирает постоянную копию, а НЕ путь в _MEIPASS
        fc = CFG.find_core({"client": {}})
        check("find_core отдаёт путь вне _MEIPASS",
              bool(fc) and os.path.normcase(os.path.abspath(fc)).startswith(
                  os.path.normcase(os.path.abspath(CFG.core_dir()))), str(fc))
        check("find_core не вернул каталог распаковки",
              not (fc and os.path.normcase(os.path.abspath(fc)).startswith(
                  os.path.normcase(os.path.abspath(bundle_root)))), str(fc))
    finally:
        if had_meipass:
            sys._MEIPASS = old_meipass
        else:
            try:
                delattr(sys, "_MEIPASS")
            except AttributeError:
                pass
        shutil.rmtree(bundle_root, ignore_errors=True)
    # относительный client.core в конфиге больше не обязателен, но поддержан
    rel = CFG.find_core({"client": {"core": os.path.join("bin", "xray", bin_name)}})
    check("относительный client.core разрешается от каталога данных",
          bool(rel) and os.path.normcase(os.path.abspath(rel)) ==
          os.path.normcase(os.path.join(CFG.core_dir(), bin_name)), str(rel))
    # уборка своих старых распаковок _MEI*: своя удаляется, чужая — нет
    mei_root = os.path.join(_TMP, "mei_scan")
    ours = os.path.join(mei_root, "_MEI_ours_selftest")
    foreign = os.path.join(mei_root, "_MEI_foreign_selftest")
    os.makedirs(os.path.join(ours, "bin", "xray"))
    os.makedirs(os.path.join(foreign, "bin", "other"))
    with open(os.path.join(ours, "bin", "xray", bin_name), "wb") as f:
        f.write(b"core")
    with open(os.path.join(foreign, "bin", "other", "x"), "wb") as f:
        f.write(b"x")
    r = CFG.cleanup_stale_bundles(max_age_s=0, tmp_dir=mei_root)
    check("наша старая распаковка _MEI удалена", not os.path.isdir(ours), str(r))
    check("чужая распаковка _MEI не тронута", os.path.isdir(foreign), str(r))
    shutil.rmtree(mei_root, ignore_errors=True)
    # CWD внутри _MEI недопустим: уборщик PyInstaller не снесёт каталог и
    # покажет окно «Failed to remove temporary directory». ensure_safe_cwd()
    # обязан увести процесс из распаковки ДО запуска дочерних процессов.
    fake_mei = os.path.join(_TMP, "fake_mei_ctx")
    os.makedirs(os.path.join(fake_mei, "sub"), exist_ok=True)
    had_mp = hasattr(sys, "_MEIPASS")
    old_mp = getattr(sys, "_MEIPASS", None)
    old_cwd = os.getcwd()
    sys._MEIPASS = fake_mei
    try:
        os.chdir(fake_mei)
        moved = CFG.ensure_safe_cwd()
        cur = os.path.normcase(os.path.abspath(os.getcwd()))
        mpn = os.path.normcase(os.path.abspath(fake_mei))
        still_inside = (cur == mpn or cur.startswith(mpn + os.sep))
        check("ensure_safe_cwd уводит CWD из каталога распаковки",
              bool(moved) and not still_inside, f"moved={moved!r} cwd={cur}")
        check("ensure_safe_cwd не создаёт пользовательский каталог заново",
              os.path.normcase(os.path.abspath(moved)) != os.path.normcase(
                  os.path.abspath(CFG.data_root())) or os.path.isdir(CFG.data_root()))
        check("ensure_safe_cwd при безопасном CWD ничего не делает",
              CFG.ensure_safe_cwd() == "")
    finally:
        os.chdir(old_cwd)
        if had_mp:
            sys._MEIPASS = old_mp
        else:
            try:
                delattr(sys, "_MEIPASS")
            except AttributeError:
                pass
        shutil.rmtree(fake_mei, ignore_errors=True)
    _say()
    _say("=" * 64)
    _say("14. Автономный надзор: --ensure, защита от второго экземпляра, задача")
    _say("=" * 64)
    import watchdog as WD
    # 14.1 настройки (дефолты и переопределение); тест НЕ трогает систему
    check("watchdog в дефолтах: enabled=True", cfg2["watchdog"]["enabled"] is True)
    check("watchdog в дефолтах: interval_min=5",
          int(cfg2["watchdog"]["interval_min"]) == 5)
    check("watchdog в дефолтах: task_name",
          cfg2["watchdog"]["task_name"] == "ForestProxyUpdater-Watchdog")
    st0 = WD.settings({})
    check("WD.settings(пусто): включено по умолчанию",
          st0["enabled"] is True and st0["interval_min"] == 5)
    st1 = WD.settings({"watchdog": {"enabled": False, "interval_min": 2,
                                    "task_name": "X-Y"}})
    check("WD.settings читает настройки из конфига",
          st1["enabled"] is False and st1["interval_min"] == 2 and st1["task_name"] == "X-Y")
    os.environ["FOREST_TASK_NAME"] = "ForestTest-Task"
    check("имя задачи: env перекрывает конфиг",
          WD.task_name({"watchdog": {"task_name": "X"}}) == "ForestTest-Task")
    os.environ.pop("FOREST_TASK_NAME", None)
    check("имя задачи берётся из конфига",
          WD.task_name({"watchdog": {"task_name": "FromCfg"}}) == "FromCfg")
    check("имя задачи по умолчанию", WD.task_name({}) == WD.TASK_NAME_DEFAULT)

    # 14.2 XML задачи — чистая функция (систему не трогаем)
    xml = WD.task_xml(r"C:\Apps\ForestProxyUpdater.exe",
                      '--ensure --config "C:\\Apps\\config.json"',
                      r"C:\Apps", "PC\\user", interval_min=5)
    check("XML: действие --ensure", "--ensure" in xml)
    check("XML: триггер «при входе в систему»", "<LogonTrigger>" in xml)
    check("XML: триггер повтора (TimeTrigger с StartBoundary)",
          "<TimeTrigger>" in xml and "<StartBoundary>" in xml)
    check("XML: повтор каждые 5 минут", "<Interval>PT5M</Interval>" in xml)
    check("XML: повтор бесконечный (Duration не задан)", "<Duration>" not in xml)
    check("XML: задача скрытая", "<Hidden>true</Hidden>" in xml)
    check("XML: не запускать новый экземпляр (IgnoreNew)",
          "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml)
    check("XML: от имени текущего пользователя",
          "PC\\user" in xml and "<LogonType>InteractiveToken</LogonType>" in xml)
    check("XML: интервал переопределяется из конфига",
          "<Interval>PT2M</Interval>" in WD.task_xml("e", "a", "w", "u", interval_min=2))
    check("XML: спецсимволы в путях экранируются",
          "&amp;" in WD.task_xml("a&b", "c", "w", "u"))
    check("аргументы задачи: --ensure и --config",
          "--ensure" in WD.install_args({"watchdog": {}}) and
          "--config" in WD.install_args({"_config_path": r"C:\x\config.json"}))

    import instancelock as IL
    # 14.3 маркер/ЗАМОК супервизора: эксклюзивная блокировка файла
    me = os.getpid()
    if getattr(sys, "frozen", False):
        root_wd = os.path.dirname(os.path.abspath(sys.executable))
    else:
        root_wd = os.path.join(_TMP, "fresh_root")
    cfg_wd = {"paths": {"root": root_wd,
                        "state_dir": os.path.join(_TMP, "wd_state")},
              "watchdog": cfg2["watchdog"]}
    os.makedirs(cfg_wd["paths"]["state_dir"], exist_ok=True)
    # 14.3a «своя семья»: onefile — это ДВА процесса (bootloader-родитель и
    # Python-потомок) с одинаковой командной строкой. Родителя нельзя принять за
    # чужой живой супервизор, иначе --run откажется стартовать.
    m_exe = WD.our_exe(cfg_wd)
    rows = [(me, 4242, m_exe, f'"{m_exe}" --run'),
            (4242, 1, m_exe, f'"{m_exe}" --run'),
            (9999, 1, r"C:\Other\Other.exe", r'"C:\Other\Other.exe" --run --x')]
    skip = WD._self_family_pids(rows, cfg_wd)
    check("своя семья: родитель-bootloader не считается чужим супервизором",
          me in skip and 4242 in skip)
    check("своя семья: посторонний процесс не исключается", 9999 not in skip)
    # 14.3b сам замок: эксклюзивность подтверждается НЕЗАВИСИМОЙ попыткой
    probe_lock = os.path.join(_TMP, "ilock", "probe.lock")
    ok_x, det_x = IL.exclusivity_check(probe_lock)
    check("замок эксклюзивен: независимая вторая попытка получает отказ",
          ok_x, det_x)
    lk_tmp = IL.InstanceLock(probe_lock)
    check("замок: повторный захват своим объектом идемпотентен",
          lk_tmp.acquire() is True)
    lk_tmp.write_info({"pid": me, "ts": time.time()})
    check("замок: занятость видна пробой (vtoroy дескриптор)", IL.held(probe_lock))
    check("замок: владелец виден как pid", IL.holder_pid(probe_lock) == me,
          str(IL.holder_pid(probe_lock)))
    check("замок: вторая независимая попытка при живом владельце — отказ",
          IL.exclusivity_check(probe_lock)[0] is False)
    lk_tmp.release()
    check("замок: после снятия владельцем файл убран", not os.path.exists(probe_lock))
    check("замок: мёртвого владельца не бывает (ОС снимает) — путь снова свободен",
          not IL.held(probe_lock))
    # 14.3c замок супервизора этой установки
    check("замок супервизора: путь = state/supervisor.lock",
          WD.marker_path(cfg_wd).endswith("supervisor.lock"))
    WD.clear_marker(cfg_wd)
    check("замок супервизора: до захвата не занят",
          WD.supervisor_lock_held(cfg_wd) is False)
    got1 = WD.claim_supervisor(cfg_wd)
    check("claim: первый экземпляр становится супервизором", got1 is True)
    check("claim: замок супервизора занят", WD.supervisor_lock_held(cfg_wd) is True)
    check("claim: процесс помечен как супервизор (единое правило)",
          WD.we_are_supervisor() is True)
    got2 = WD.claim_supervisor(cfg_wd)
    check("claim: второй отказывается (нет второго --run)", got2 is False)
    d = WD.read_marker(cfg_wd)
    check("замок: диагностика владельца записана (pid/root)",
          d.get("pid") == me and d.get("root") == cfg_wd["paths"]["root"], str(d))
    check("pid живого супервизора читается из замка",
          WD._marker_live_pid(cfg_wd) == me)
    check("find_live_supervisor видит супервизора (замок занят)",
          WD.find_live_supervisor(cfg_wd) == me)
    check("find_live_supervisor исключает свой pid",
          WD.find_live_supervisor(cfg_wd, exclude=(me,)) == 0)
    # 14.3d единое правило самоконтроля: супервизор НИКОГДА не поднимает копию
    orig_spawn = WD._spawn_detached
    calls = []
    WD._spawn_detached = lambda *a, **k: (calls.append(a), (False, 0))[1]
    try:
        rc_a = WD.ensure(cfg_wd)
        rc_b = WD.ensure(cfg_wd)
    finally:
        WD._spawn_detached = orig_spawn
    check("ensure: живой супервизор (замок занят) — запуска не было",
          not calls, str(calls))
    check("ensure: тихо возвращает 0", rc_a == 0 and rc_b == 0)
    WD.release_supervisor(cfg_wd)
    check("release: замок освобождён", WD.supervisor_lock_held(cfg_wd) is False)
    check("release: процесс больше не супервизор",
          WD.we_are_supervisor() is False)
    check("release: живого супервизора нет", WD._marker_live_pid(cfg_wd) == 0)

    # 14.4 запуск: один, под замком запуска, и уважает живой замок
    check("ensure без супервизора (пробно, без запуска) -> 0",
          WD.ensure(cfg_wd, spawn=False) == 0)
    calls2 = []
    WD._spawn_detached = lambda *a, **k: (calls2.append(a), (False, 0))[1]
    try:
        rc3 = WD.ensure(cfg_wd, spawn=True)
    finally:
        WD._spawn_detached = orig_spawn
    check("ensure: супервизора нет — запуск вызван ровно один раз",
          len(calls2) == 1, str(len(calls2)))
    lk_hold = IL.InstanceLock(WD.marker_path(cfg_wd))
    check("внешний владелец занял замок супервизора",
          lk_hold.acquire({"pid": 424242}) is True)
    calls3 = []
    WD._spawn_detached = lambda *a, **k: (calls3.append(a), (False, 0))[1]
    try:
        rc4 = WD.ensure(cfg_wd, spawn=True)
    finally:
        WD._spawn_detached = orig_spawn
    check("ensure: живой ЧУЖОЙ замок -> второй экземпляр не поднимается",
          not calls3 and rc4 == 0, str(calls3))
    check("watchdog_report: программа работает (замок занят)",
          WD.watchdog_report(cfg_wd)["supervisor_alive"] is True)
    lk_hold.release(unlink=False)
    # переходный случай: маркер прежней версии без замка
    WD.clear_marker(cfg_wd)
    with open(WD.marker_path(cfg_wd), "w", encoding="utf-8") as f:
        json.dump({"pid": 999999, "ts": time.time(), "exe": WD.our_exe(cfg_wd),
                   "root": cfg_wd["paths"]["root"]}, f)
    check("старый маркер с мёртвым pid -> супервизора нет",
          WD._marker_live_pid(cfg_wd) == 0)
    check("старый маркер с мёртвым pid не мешает запуску (замок свободен)",
          WD.supervisor_lock_held(cfg_wd) is False)

    # 14.5 помощник интерфейса: ОДИН на установку — тоже замок, а не пульс
    import ui_helper as UH
    check("помощник: путь замка = state/forest-ui.lock",
          UH.lock_path(cfg_wd).endswith("forest-ui.lock"))
    check("помощник: замок свободен -> running() = False",
          UH.running(cfg_wd) is False)
    hk = IL.InstanceLock(UH.lock_path(cfg_wd))
    check("помощник: владелец занял замок", hk.acquire({"pid": 424242}) is True)
    check("помощник: running() видит живой замок даже без пульса",
          UH.running(cfg_wd) is True)
    check("помощник: второй экземпляр помощника получает отказ",
          IL.exclusivity_check(UH.lock_path(cfg_wd))[0] is False)
    cfg_wd.setdefault("ui", {})["helper"] = "on"      # запускать разрешено
    orig_launch = UH.launch
    launched = []
    UH.launch = lambda cfg, log_msg=True: (launched.append(1), True)[1]
    try:
        UH.ensure(cfg_wd)
    finally:
        UH.launch = orig_launch
    check("помощник: пока замок занят, второй НЕ запускается", not launched,
          str(launched))
    hk.release(unlink=False)
    launched2 = []
    UH.launch = lambda cfg, log_msg=True: (launched2.append(1), True)[1]
    try:
        UH.ensure(cfg_wd)
    finally:
        UH.launch = orig_launch
    check("помощник: свободный замок -> запуск ровно один раз",
          len(launched2) == 1, str(len(launched2)))
    # Самоопределение помощника: ОДНОГО признака окружения мало — оно достаётся
    # по наследству. В сборке «я помощник» = env + имя своего файла, иначе
    # супервизор (получивший env от помощника) никогда не поднял бы настоящий
    # помощник, и значка в трее не было бы.
    os.environ[UH.HELPER_ENV] = "1"
    frozen_here = bool(getattr(sys, "frozen", False))
    exe_is_helper = os.path.basename(sys.executable).lower().startswith("forest-ui")
    if frozen_here and not exe_is_helper:
        check("помощник: env-признак без имени файла НЕ делает процесс помощником",
              UH.we_are_helper() is False)
        # и тогда ensure() обязан поднять помощник, а не «решить, что я и есть он»
        launched_env = []
        UH.launch = lambda cfg, log_msg=True: (launched_env.append(1), True)[1]
        try:
            UH.ensure(cfg_wd)
        finally:
            UH.launch = orig_launch
            os.environ.pop(UH.HELPER_ENV, None)
        check("помощник: унаследованный env не мешает супервизору поднять помощник",
              len(launched_env) == 1, str(launched_env))
    else:
        check("помощник: процесс-помощник узнаёт себя", UH.we_are_helper() is True)
        launched3 = []
        UH.launch = lambda cfg, log_msg=True: (launched3.append(1), True)[1]
        try:
            UH.ensure(cfg_wd)
        finally:
            UH.launch = orig_launch
            os.environ.pop(UH.HELPER_ENV, None)
        check("помощник: процесс-помощник не поднимает вторую копию", not launched3,
              str(launched3))
    for _l in (UH.lock_path(cfg_wd), UH.spawn_lock_path(cfg_wd)):
        try:
            os.unlink(_l)
        except Exception:
            pass

    # 14.5a ЖИВОСТЬ pid — то, из-за чего на живом ПК не было значка в трее:
    # OpenProcess отвечал успехом для завершённого процесса, пульс с мёртвым pid
    # считался работающим помощником, и супервизор его никогда не поднимал.
    import subprocess as _sp
    import ui_cmd as _UC
    _NO_WIN = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
    check("живость: _pid_alive(свой pid) = True",
          UH._pid_alive(os.getpid()) is True, str(os.getpid()))
    check("живость: _pid_alive(0) и _pid_alive(-7) = False",
          UH._pid_alive(0) is False and UH._pid_alive(-7) is False)
    # живой ЧУЖОЙ процесс: имя файла не forest-ui(.exe) -> _is_our_helper False
    if os.name == "nt":
        _live_cmd = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                  "System32", "ping.exe"), "-n", "40",
                     "127.0.0.1"]
    else:
        _live_cmd = ["sleep", "40"]
    _live = _sp.Popen(_live_cmd, stdin=_sp.DEVNULL, stdout=_sp.DEVNULL,
                      stderr=_sp.DEVNULL, creationflags=_NO_WIN)
    try:
        check("живость: живой чужой pid = True", UH._pid_alive(_live.pid) is True,
              str(_live.pid))
        check("живость: чужой процесс не считается нашим помощником",
              UH._is_our_helper(_live.pid, cfg_wd) is False, str(_live.pid))
        check("живость: путь образа чужого pid читается",
              UH._pid_image(_live.pid).lower().endswith(".exe")
              if os.name == "nt" else bool(UH._pid_image(_live.pid)),
              UH._pid_image(_live.pid))
    finally:
        try:
            _live.kill()
            _live.wait(timeout=10)
        except Exception:
            pass
    # мёртвый pid: процесс завершён, но хендл ещё не освобождён — ровно тот
    # случай, на котором ломался OpenProcess-вариант
    _dead = _sp.Popen([os.environ.get("COMSPEC", "cmd.exe"), "/c", "exit"]
                      if os.name == "nt" else ["true"],
                      stdin=_sp.DEVNULL, stdout=_sp.DEVNULL,
                      stderr=_sp.DEVNULL, creationflags=_NO_WIN)
    _dead.wait(timeout=20)
    check("живость: завершённый pid = False (а не «жив» по хендлу)",
          UH._pid_alive(_dead.pid) is False, str(_dead.pid))

    # полная цепочка: пульс с мёртвым pid НЕ должен считаться работающим
    # помощником, а живой процесс с именем forest-ui.exe — должен.
    _fake_dir = os.path.join(_TMP, "fake_helper")
    _fake_state = os.path.join(_TMP, "fake_state")
    os.makedirs(_fake_dir, exist_ok=True)
    os.makedirs(_fake_state, exist_ok=True)
    _fake = os.path.join(_fake_dir, UH.helper_name())
    _have_fake = False
    _fake_args = []
    try:
        if os.name == "nt":
            shutil.copy2(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                      "System32", "ping.exe"), _fake)
            _fake_args = ["-n", "60", "127.0.0.1"]
            _have_fake = True
        else:
            # На Linux «наш ли это файл» решает путь ОБРАЗА процесса
            # (/proc/<pid>/exe): у скрипта там интерпретатор, а не скрипт, так что
            # проверять надо настоящий ELF под именем помощника — ровно как копия
            # ping.exe -> forest-ui.exe на Windows.
            # Симлинки НЕ годятся: мультиколл-бинарники (uutils coreutils,
            # busybox) выбирают апплет по argv[0] и с чужим именем отказываются
            # стартовать («coreutils: unknown program 'forest-ui'»), а
            # /proc/<pid>/exe у симлинка разрешается в цель, а не в имя.
            _script = os.path.join(_fake_dir, "fake_helper.sh")
            with open(_script, "w", encoding="utf-8") as _f:
                # без shell-оптимизации «exec последней команды» (иначе образ
                # процесса подменился бы на sleep) и без сирот: дочерний sleep
                # живёт максимум секунду
                _f.write("while :; do sleep 1; done\n")
            for _src, _args in ((shutil.which("sleep"), ["60"]),
                                ("/bin/dash", [_script]),
                                ("/bin/bash", [_script]),
                                ("/usr/bin/bash", [_script])):
                if not _src or not os.path.isfile(_src) or os.path.islink(_src):
                    continue
                try:
                    shutil.copy2(_src, _fake)
                    os.chmod(_fake, 0o755)
                except Exception:
                    continue
                _probe = _sp.Popen([_fake] + _args, stdin=_sp.DEVNULL,
                                   stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                                   creationflags=_NO_WIN)
                time.sleep(0.3)
                if _probe.poll() is None:
                    _fake_args = _args
                    _have_fake = True
                    try:
                        _probe.kill()
                        _probe.wait(timeout=10)
                    except Exception:
                        pass
                    break
                try:
                    _probe.wait(timeout=5)
                except Exception:
                    pass
    except Exception:
        _have_fake = False
    cfg_fake = {"paths": {"root": _fake_dir, "state_dir": _fake_state},
                "ui": {"helper": "on", "helper_exe": _fake}}
    if _have_fake:
        check("помощник: helper_path находит наш файл помощника",
              os.path.abspath(UH.helper_path(cfg_fake)).lower()
              == os.path.abspath(_fake).lower(), UH.helper_path(cfg_fake))
        _fh = _sp.Popen([_fake] + _fake_args,
                        stdin=_sp.DEVNULL, stdout=_sp.DEVNULL,
                        stderr=_sp.DEVNULL, creationflags=_NO_WIN)
        try:
            time.sleep(0.4)
            check("помощник: наш файл по pid опознан (_is_our_helper)",
                  UH._is_our_helper(_fh.pid, cfg_fake) is True, str(_fh.pid))
            _UC.write_ping(cfg_fake, _fh.pid, "selftest")
            check("помощник: пульс + живой наш pid -> running_pid = pid",
                  UH.running_pid(cfg_fake) == _fh.pid, str(UH.running_pid(cfg_fake)))
            check("помощник: running() = True по живому пульсу",
                  UH.running(cfg_fake) is True)
        finally:
            try:
                _fh.kill()
                _fh.wait(timeout=10)
            except Exception:
                pass
        # тот же пульс, но процесс уже мёртв: помощника НЕТ, замок свободен
        time.sleep(0.2)
        check("помощник: пульс с МЁРТВЫМ pid -> running_pid = 0 (дефект трея)",
              UH.running_pid(cfg_fake) == 0, str(UH.running_pid(cfg_fake)))
        check("помощник: пульс с мёртвым pid -> running() = False",
              UH.running(cfg_fake) is False)
        # ensure() ОБЯЗАН поднять помощник заново: именно этого не делал
        # супервизор, пока считал мёртвый pid живым
        relaunch = []
        UH.launch = lambda cfg, log_msg=True: (relaunch.append(1), True)[1]
        try:
            UH.ensure(cfg_fake)
        finally:
            UH.launch = orig_launch
        check("помощник: мёртвый пульс -> ensure() снова запускает помощник",
              len(relaunch) == 1, str(relaunch))
    else:
        check("помощник: поддельный файл помощника создан", False, _fake)
    try:
        _UC.clear_ping(cfg_fake)
    except Exception:
        pass
    shutil.rmtree(_fake_dir, ignore_errors=True)
    shutil.rmtree(_fake_state, ignore_errors=True)

    # 14.6 отчёт/статус задачи (только чтение)
    os.environ["FOREST_TASK_NAME"] = "ForestSelftest-NoSuchTask-QZ"
    rep = WD.watchdog_report(cfg_wd)
    os.environ.pop("FOREST_TASK_NAME", None)
    check("watchdog_report: есть settings/task/supervisor_alive",
          all(k in rep for k in ("settings", "task", "supervisor_alive")))
    check("несуществующая задача: exists=False", rep["task"]["exists"] is False)

    # 14.6 выключенный надзор: задачу НЕ создаём, сообщаем честно
    os.environ["FOREST_TASK_NAME"] = "ForestSelftest-Off-QZ"
    cfg_off = dict(cfg_wd)
    cfg_off["watchdog"] = {"enabled": False, "interval_min": 5,
                           "task_name": "ForestSelftest-Off-QZ"}
    res_off = WD.apply_watchdog(cfg_off, log_msg=False)
    os.environ.pop("FOREST_TASK_NAME", None)
    check("enabled=False: задача не создаётся (unchanged)",
          res_off["ok"] and res_off["action"] == "unchanged")
    check("enabled=False: задачи в Планировщике нет",
          not WD.task_exists("ForestSelftest-Off-QZ"))

    # 14.7 строки надзора в каталоге i18n заполнены на обоих языках
    comp = i18n.check_complete()
    wd_keys = [k for k in i18n.all_keys() if "watchdog" in k]
    check("i18n: ключи надзора есть", bool(wd_keys))
    check("i18n: ключи надзора заполнены (ru/en)",
          all(k not in comp["missing"] for k in wd_keys))

    _say()
    _say("=" * 64)
    _say("15. безопасный вывод и защита от необработанных исключений")
    _say("=" * 64)
    raised = None
    try:
        SD.say("✓ символ вне cp1251")      # не должно бросить НИКОГДА
    except Exception as e:                      # pragma: no cover
        raised = e
    check("печать символа вне cp1251 не бросает исключения (нет окна PyInstaller)",
          raised is None, str(raised))
    check("кодировка stdout запомнена до перевода в utf-8",
          isinstance(SD._ORIG_ENCODING, str), SD._ORIG_ENCODING)

    def _boom(_argv=None):
        raise RuntimeError("искусственное исключение проверки")
    rc = SD.guarded(_boom, where="forest-proxy-updater-selftest")
    cpath = os.path.join(SD.log_dir(), "crash.log")
    ctxt = ""
    try:
        ctxt = open(cpath, encoding="utf-8").read()
    except Exception:
        ctxt = ""
    check("guarded(): исключение внутри main -> код 70 (окна не будет)", rc == 70,
          str(rc))
    check("guarded(): трассировка в crash.log (UTF-8)",
          "искусственное исключение проверки" in ctxt, cpath)
    check("глобальный перехватчик исключений установлен",
          callable(getattr(sys, "excepthook", None)))
    _tp = SD.write_report("_probe_utf8_main.txt", "✓ проверка UTF-8 — ок")
    _tt = ""
    try:
        _tt = open(_tp, encoding="utf-8").read()
    except Exception:
        _tt = ""
    check("человекочитаемый отчёт пишется в UTF-8-ФАЙЛ", bool(_tp) and "✓ проверка" in _tt,
          _tp)
    try:
        if _tp:
            os.unlink(_tp)
    except Exception:
        pass

    _say()
    _say("=" * 64)
    if FAILED:
        _say(f"{i18n.t('selftest.failed')}: {len(FAILED)}")
        for f in FAILED:
            _say(f"  - {f}")
        # ASCII-итог для оконной сборки: safe_stdio заменяет кириллицу на «?»,
        # и по выводу frozen-сборки иначе не понять, чем кончилось дело
        _say(f"SELFTEST: FAILED ({len(FAILED)})")
        _cleanup()
        return 1
    _say(i18n.t("selftest.passed"))
    _say("=" * 64)
    _say("SELFTEST: PASSED")
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
