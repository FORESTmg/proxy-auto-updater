# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Бэкенд режима v2rayN: работа через базу и ядро установленного v2rayN.

FOREST's Proxy Auto-updater (GPL-3.0).

Этот модуль — интеграция с v2rayN (чтение/запись SQLite guiNDB.db +
guiNConfig.json, бэкапы, активация stop -> write -> start -> verify ->
rollback, режим guard). Он используется ТОЛЬКО при ``client.mode == "v2rayn"``;
по умолчанию программа работает в standalone-режиме (своё ядро xray, см.
``clientwrite.py``). Вызовы диспетчеризуются в ``clientwrite.py``.

Код перенесён из исходного проекта v2ray-auto-updater без изменения поведения
(это проверенная на живом v2rayN логика).

EConfigType (из исходников v2rayN): VMess=1, Shadowsocks=3, SOCKS=4,
VLESS=5, Trojan=6, Hysteria2=7, TUIC=8, WireGuard=9, HTTP=10.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

import log

CONFIG_TYPE = {"vless": 5, "vmess": 1, "trojan": 6, "ss": 3}
AUTO_TAG = "[AUTO] "
PROBE_URL = "https://www.gstatic.com/generate_204"
# Второй, НЕЗАВИСИМЫЙ эндпоинт (Cloudflare) — для повторных попыток пробы.
# Одиночный быстрый отказ узла пула (curl вернул 000 за 150-400 мс) раньше
# считался обрывом связи: 121 из 137 «ударов» за сутки были такими ложными,
# и каждый третий удар перезапускал ядро (окно, в котором падают ВСЕ
# сторонние приложения — проверка обновлений Hermes, браузер, загрузки).
# Заодно это проверка TLS именно к Cloudflare: узел, рвущий рукопожатие к
# CF-хостам, теперь не пройдёт проверку.
PROBE_URL_ALT = "https://cp.cloudflare.com/generate_204"
# YouTube как отдельный критерий: узлы из бесплатных списков часто везут
# трафик, но Google их не пускает — страницы показывают «проверить соединение».
YOUTUBE_URL = "https://www.youtube.com/"
# Проверка РЕАЛЬНОГО трафика: подставные серверы («заглушки») отдают 204 на
# generate_204, но данные не идут (или подменяют TLS-сертификат).
PROBE_DATA_URL = "https://speed.cloudflare.com/__down?bytes=200000"
PROBE_DATA_MIN_BYTES = 50000


def _curl_metrics(port: int, url: str, max_time: int) -> tuple:
    """(http_code, time_total, ssl_verify_result, size_download) через socks."""
    cmd = ["curl", "-s", "-o", os.devnull,
           "-w", "%{http_code} %{time_total} %{ssl_verify_result} %{size_download}",
           "--max-time", str(max_time), "-x", f"socks5h://127.0.0.1:{port}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=max_time + 8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.decode("utf-8", "replace").strip().split()
        code = int(out[0]) if out and out[0].isdigit() else 0
        tt = float(out[1]) if len(out) > 1 else 999.0
        sslv = int(out[2]) if len(out) > 2 and out[2].lstrip("-").isdigit() else 0
        size = int(out[3]) if len(out) > 3 and out[3].isdigit() else 0
        return code, tt, sslv, size
    except Exception:
        return 0, 999.0, 0, 0


def _curl_speed(port: int, url: str, max_time: int) -> tuple:
    """(http_code, bytes/sec) через socks."""
    cmd = ["curl", "-s", "-o", os.devnull, "-w", "%{http_code} %{speed_download}",
           "--max-time", str(max_time), "-x", f"socks5h://127.0.0.1:{port}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=max_time + 8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.decode("utf-8", "replace").strip().split()
        code = int(out[0]) if out and out[0].isdigit() else 0
        spd = float(out[1]) if len(out) > 1 and out[1].isdigit() else 0.0
        return code, spd
    except Exception:
        return 0, 0.0


def measure_speed_kbps(client: dict, bytes_n: int = 2000000, timeout: int = 15) -> float:
    """Замер скорости через порт клиента (КБ/с). 0 — если не удалось."""
    port = client.get("socks_port", 10808)
    url = f"https://speed.cloudflare.com/__down?bytes={int(bytes_n)}"
    code, spd = _curl_speed(port, url, timeout)
    return round(spd / 1024.0, 1) if code == 200 else 0.0


def youtube_probe(port: int, timeout: int = 12) -> tuple:
    """(http_code, rtt_ms) по YouTube через порт. 403/0 = узел не пускает."""
    code, tt, _sslv, _sz = _curl_metrics(port, YOUTUBE_URL, timeout)
    return code, round(tt * 1000, 1)


def port_works(port: int, timeout: int = 8) -> bool:
    """Строгая проверка: через порт идут РЕАЛЬНЫЕ данные и TLS не подменён.
    Ловит серверы-«заглушки», которые отдают 204, но трафик не пропускают.

    204 принимается от ЛЮБОГО из двух независимых эндпоинтов (Google или
    Cloudflare): узел пула может не пропускать конкретный хост, и одиночный
    отказ такого рода — ложный «обрыв». Данные качаем с Cloudflare, там же
    требуется ssl_verify_result == 0, поэтому узел, рвущий TLS к Cloudflare,
    проверку не проходит."""
    ok204 = False
    for _url in (PROBE_URL, PROBE_URL_ALT):
        code, _tt, sslv, _ = _curl_metrics(port, _url, timeout)
        if code == 204 and sslv == 0:
            ok204 = True
            break
    if not ok204:
        return False
    code2, _t2, sslv2, size = _curl_metrics(port, PROBE_DATA_URL, timeout)
    return sslv2 == 0 and code2 == 200 and size >= PROBE_DATA_MIN_BYTES


def probe_client(client: dict, timeout: int = 10, strict: bool = True,
                 alt: bool = False) -> tuple:
    """(ok, rtt_ms) через socks-порт клиента.

    strict=True (по умолчанию): мало получить 204 — проверяем, что через
    сервер реально ИДУТ данные (скачиваем ~200 КБ) и TLS не подменён
    (ssl_verify_result == 0). Иначе сервер-«заглушка» проходит как рабочий,
    связь «восстанавливается», тут же рвётся — и так по кругу.

    alt=True — проверять ВТОРОЙ эндпоинт (Cloudflare). Так зовут повторные
    попытки: балансировщик выбирает узел на КАЖДЫЙ коннект, поэтому свежий
    запрос часто уходит на живой узел, когда предыдущий оборвался за 200 мс.
    """
    port = client.get("socks_port", 10808)
    code, tt, sslv, _ = _curl_metrics(port, PROBE_URL_ALT if alt else PROBE_URL, timeout)
    if code != 204 or sslv != 0:
        return False, round(tt * 1000, 1)
    rtt = round(tt * 1000, 1)
    if not strict:
        return True, rtt
    if not port_works(port, timeout):
        return False, rtt
    return True, rtt


# ---------------------------------------------------------------------------
# Чёрный список серверов: те, что «отвечают, но не везут» (заглушки/подменённый
# TLS) или на которых уже была потеря связи. Без него скрипт зацикливается:
# восстанавливает связь на том же проблемном сервере, связь снова падает и т.д.
# ---------------------------------------------------------------------------
def bl_add(cfg: dict, key, minutes: float = 60, why: str = ""):
    try:
        st = StateStore(cfg)
        bl = st.data.get("blacklist") or {}
        if not isinstance(bl, dict):
            bl = {}
        bl[str(key)] = {"until": time.time() + minutes * 60, "why": why,
                        "ts": time.time()}
        st.data["blacklist"] = bl
        st.save()
    except Exception:
        pass


def bl_add_pool(cfg: dict, servers: list, minutes: float = 20,
                why: str = "stub_pool"):
    """Бан на ВЕСЬ состав пула.

    Строгая проверка провалилась на поднятом пуле — негодны все его участники
    (типично: CF-заглушки отдают 204, но трафик не везут, а leastPing выбирает
    их по низкому пингу). Раньше банился только активный сервер, и заглушки
    возвращались в пул на следующем цикле — отсюда 2 ч 42 мин без связи.
    """
    for s in servers or []:
        key = getattr(s, "_row_index_id", None) or getattr(s, "index_id", None)
        if key:
            bl_add(cfg, key, minutes=minutes, why=why)


def bl_active(cfg: dict) -> dict:
    try:
        st = StateStore(cfg)
        bl = st.data.get("blacklist") or {}
        now = time.time()
        return {k: v for k, v in bl.items()
                if isinstance(v, dict) and float(v.get("until", 0)) > now}
    except Exception:
        return {}


def bl_filter(cfg: dict, servers: list) -> list:
    """Убирает серверы из активного чёрного списка."""
    act = bl_active(cfg)
    if not act:
        return servers
    out = []
    for s in servers:
        k = str(getattr(s, "_row_index_id", None) or getattr(s, "index_id", ""))
        if k in act:
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------------------
def row_to_server(row: dict):
    """Профиль v2rayN -> Server (для повторного теста существующих)."""
    import parser as P
    ct = row.get("ConfigType")
    proto = {5: "vless", 1: "vmess", 3: "ss", 6: "trojan"}.get(ct)
    if proto is None:
        return None
    s = P.Server(protocol=proto, address=row.get("Address") or "",
                 port=int(row.get("Port") or 0))
    if not s.address or s.port <= 0:
        return None
    s.password = row.get("Password") or ""
    s.remark = row.get("Remarks") or ""
    # Delay — мс, Speed — МБ/с (так пишет write_auto_group: speed_kbps / 1024)
    try:
        s.rtt_ms = float(row.get("_Delay") or row.get("Delay") or 0)
    except Exception:
        s.rtt_ms = 0.0
    try:
        s.speed_kbps = float(row.get("_Speed") or row.get("Speed") or 0) * 1024.0
    except Exception:
        s.speed_kbps = 0.0
    s.network = (row.get("Network") or "raw")
    if s.network in ("", "tcp"):
        s.network = "raw"
    s.stream_security = row.get("StreamSecurity") or ""
    s.sni = row.get("Sni") or ""
    s.alpn = row.get("Alpn") or ""
    s.fingerprint = row.get("Fingerprint") or ""
    s.public_key = row.get("PublicKey") or ""
    s.short_id = row.get("ShortId") or ""
    s.spider_x = row.get("SpiderX") or ""
    s.allow_insecure = (row.get("AllowInsecure") or "") == "1"
    s._row_index_id = str(row.get("IndexId"))
    pe = _json_safe(row.get("ProtoExtra"))
    te = _json_safe(row.get("TransportExtra"))
    if proto == "ss":
        s.method = pe.get("SsMethod", "")
    elif proto == "vmess":
        s.method = pe.get("VmessSecurity", "auto")
        s.alter_id = str(pe.get("AlterId") or "0")
    s.flow = pe.get("Flow", "")
    if s.network == "raw":
        s.header_type = te.get("RawHeaderType", "none")
        s.host = te.get("Host", "")
        s.path = te.get("Path", "")
    elif s.network in ("ws", "httpupgrade", "xhttp"):
        s.host = te.get("Host", "")
        s.path = te.get("Path", "/")
    elif s.network == "kcp":
        s.header_type = te.get("KcpHeaderType", "none")
        s.kcp_seed = te.get("KcpSeed", "")
    elif s.network == "grpc":
        s.host = te.get("GrpcAuthority", "")
        s.path = te.get("GrpcServiceName", "")
        s.header_type = te.get("GrpcMode", "gun")
    return s


def _json_safe(v):
    if not v:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {}


def backup_files(client: dict, backup_dir: str) -> dict:
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = {}
    for key, p in (("db", client["db"]), ("cfg", client["cfg"])):
        if not os.path.isfile(p):
            continue
        dst = os.path.join(backup_dir, f"pre_{ts}_{os.path.basename(p)}")
        shutil.copy2(p, dst)
        out[key] = dst
    return out


def read_db_rows(db_path: str) -> list:
    if not os.path.isfile(db_path):
        return []
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # СКОРОСТЬ И ПИНГ ЛЕЖАТ В ProfileExItem, а не в ProfileItem: без JOIN
        # s.speed_kbps всегда был 0 -> фильтр по скорости в пуле был пустышкой,
        # и в пул балансировщика попадали CF-заглушки (204 отдают, трафик нет).
        try:
            rows = [dict(r) for r in cur.execute(
                "SELECT p.*, e.Delay AS _Delay, e.Speed AS _Speed "
                "FROM ProfileItem p LEFT JOIN ProfileExItem e ON e.IndexId = p.IndexId")]
        except Exception:
            rows = [dict(r) for r in cur.execute("SELECT * FROM ProfileItem")]
        con.close()
        return rows
    except Exception:
        return []


def write_auto_group(db_path: str, servers: list, update_stats: bool = True) -> int:
    """Перезаписывает [AUTO]-группу в БД. Возвращает число строк."""
    import parser as P
    con = sqlite3.connect(db_path, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    try:
        cur = con.cursor()
        old_ids = [r[0] for r in cur.execute(
            "SELECT IndexId FROM ProfileItem WHERE Remarks LIKE ?", (AUTO_TAG + "%",))]
        placeholders = ",".join("?" for _ in old_ids)
        if old_ids:
            cur.execute(f"DELETE FROM ProfileExItem WHERE IndexId IN ({placeholders})", old_ids)
            cur.execute(f"DELETE FROM ProfileItem WHERE IndexId IN ({placeholders})", old_ids)
        n = 0
        for s in servers:
            row = P.to_row_dict(s)
            remark = (row["Remarks"] or row["Address"] or "").replace(AUTO_TAG, "").strip()
            row["Remarks"] = AUTO_TAG + remark
            cols = ", ".join(f'"{c}"' for c in row)
            vals = ", ".join("?" for _ in row)
            cur.execute(f"INSERT OR REPLACE INTO ProfileItem ({cols}) VALUES ({vals})",
                        list(row.values()))
            if update_stats:
                cur.execute(
                    "INSERT OR REPLACE INTO ProfileExItem (IndexId, Delay, Speed, Sort, Message) "
                    "VALUES (?, ?, ?, 0, ?)",
                    (row["IndexId"], -1 if not s.ok else int(s.rtt_ms),
                     0.0 if not s.ok else round(s.speed_kbps / 1024.0, 2), ""))
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def update_existing_stats(db_path: str, tested: dict):
    """tested: {IndexId: (rtt_ms, speed_kbps, ok)} для строк пользователя."""
    if not tested:
        return
    con = sqlite3.connect(db_path, timeout=10)
    try:
        cur = con.cursor()
        for idx, (rtt, spd, ok) in tested.items():
            cur.execute(
                "INSERT OR REPLACE INTO ProfileExItem (IndexId, Delay, Speed, Sort, Message) "
                "VALUES (?, ?, ?, 0, ?)",
                (idx, -1 if not ok else int(rtt),
                 0.0 if not ok else round(spd / 1024.0, 2), ""))
        con.commit()
    finally:
        con.close()


def read_active_id(cfg_path: str):
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f).get("IndexId")
    except Exception:
        return None


def write_active_id(cfg_path: str, index_id: str):
    with open(cfg_path, encoding="utf-8") as f:
        d = json.load(f)
    d["IndexId"] = index_id
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def write_bin_config(client: dict, server, cfg: dict) -> bool:
    """Пишет выбранный сервер в binConfigs/config.json (конфиг ядра GUI).

    Зачем: когда ядро падает, GUI САМ поднимает новое — но из binConfigs,
    т.е. со СТАРЫМ (уже мёртвым) сервером. Из-за этого шла «борьба»: мы
    снимали ядро и поднимали своё, GUI тут же поднимал своё со старым
    сервером. Записав сервер сюда, даём GUI поднимать НУЖНЫЙ сервер.
    """
    import xraytest as XT
    path = client.get("bin_cfg") or os.path.join(client.get("dir", ""),
                                                 "binConfigs", "config.json")
    if not path or not os.path.isdir(os.path.dirname(path)):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {"log": {"loglevel": "warning"},
             "inbounds": [{"tag": "socks",
                           "port": client.get("socks_port", 10808),
                           "listen": "127.0.0.1", "protocol": "mixed",
                           "settings": {"udp": True, "auth": "noauth"}}],
             "outbounds": []}
    ob = XT.outbound_json(server)
    if ob is None:
        return False
    ob = dict(ob)
    ob["tag"] = "proxy"
    outs = [o for o in d.get("outbounds", []) if o.get("tag") != "proxy"]
    d["outbounds"] = [ob] + outs
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def gui_restart_kernel(client: dict, server, cfg: dict, wait_s: int = 25) -> bool:
    """Перезапускает ядро ЧЕРЕЗ GUI (без борьбы за порт 10808).

    Механика v2rayN: если ядро умирает, GUI сам поднимает новое — из
    binConfigs/config.json. Поэтому правильно не поднимать СВОЁ ядро
    (GUI его всё равно вытеснит), а записать нужный сервер в binConfigs
    и снять ядро: GUI поднимет уже НУЖНЫЙ сервер.
    """
    lg = log.get_logger()
    port = client.get("socks_port", 10808)
    if not write_bin_config(client, server, cfg):
        lg.warn("gui_restart_kernel: binConfigs не записан")
    kill_kernels(cfg, why="gui-restart")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if port_works(port, timeout=6):
            lg.info("gui_restart_kernel: GUI поднял ядро, связь есть")
            return True
        time.sleep(1.5)
    lg.warn("gui_restart_kernel: GUI не дал связи за отведённое время")
    return False


def ensure_gui_task(client: dict, cfg: dict) -> bool:
    """Гарантирует задачу планировщика для запуска GUI в сессии пользователя
    (v2rayN не стартует из неинтерактивной сессии, а /IT-задача — стартует)."""
    exe = client.get("exe", "")
    if not exe:
        return False
    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["schtasks", "/Query", "/TN", "v2rayN_gui_run"],
                       capture_output=True, timeout=20, creationflags=nw)
    if r.returncode == 0:
        return True
    try:
        r2 = subprocess.run(["schtasks", "/Create", "/TN", "v2rayN_gui_run",
                             "/TR", f'"{exe}"', "/SC", "ONCE", "/ST", "23:59",
                             "/IT", "/F"],
                            capture_output=True, timeout=25, creationflags=nw)
        return r2.returncode == 0
    except Exception:
        return False


def set_gui_silent_start(client: dict) -> bool:
    """Включает в v2rayN старт БЕЗ показа окна (сразу в трей).

    Нужно, чтобы наши перезапуски GUI (для синхронизации активного сервера)
    были незаметны — окно не выскакивает поверх других приложений.
    """
    try:
        p = client["cfg"]
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        ui = d.get("UiItem") or {}
        if ui.get("AutoHideStartup") is True:
            return True
        ui["AutoHideStartup"] = True
        d["UiItem"] = ui
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def gui_restart_ui(client: dict, cfg: dict, server=None, active_id=None,
                   wait_s: int = 45, min_interval_s: float = 180.0) -> bool:
    """СИНХРОНИЗАЦИЯ с GUI: перезапускает окно v2rayN, чтобы оно подхватило
    НАШ активный сервер.

    v2rayN держит активный сервер в памяти и показывает его же, перечитывая
    guiNConfig.json только при старте — отсюда «в трее один сервер, в GUI
    другой». Перезапуск окна делает систему единой (и ядро GUI слушает
    0.0.0.0, т.е. телефон снова видит прокси).
    Связь страхуем своим ядром на время старта GUI.
    """
    lg = log.get_logger()
    if not _proc_exists(client.get("exe", "")):
        return False
    st = StateStore(cfg)
    last = float(st.data.get("last_gui_sync", 0))
    if time.time() - last < min_interval_s:
        lg.info("gui_restart_ui: недавно синхронизировали — пропускаю")
        return False
    # 1) записать наши конфиги (GUI прочитает их при старте)
    if active_id:
        try:
            write_active_id(client["cfg"], active_id)
        except Exception:
            pass
    if server is not None:
        try:
            write_bin_config(client, server, cfg)
        except Exception:
            pass
    # 2) закрыть GUI (корректно: WM_CLOSE, kill только как fallback)
    ps = ("$p=Get-Process v2rayN -ErrorAction SilentlyContinue;"
          "if($p){$p.CloseMainWindow()|Out-Null;Start-Sleep -Seconds 4;"
          "if(-not $p.HasExited){Stop-Process -Id $p.Id -Force}}")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=45,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        lg.warn(f"gui_restart_ui: close: {e}")
    # 3) страховка: своё ядро, пока GUI поднимается (без разрыва связи)
    if server is not None:
        try:
            start_xray_direct(client, server, cfg, force=True)
        except Exception:
            pass
    # 3b) тихий старт: чтобы окно GUI не выскакивало поверх приложений
    try:
        set_gui_silent_start(client)
    except Exception:
        pass
    # 4) запустить GUI в сессии пользователя
    if ensure_gui_task(client, cfg):
        try:
            subprocess.run(["schtasks", "/Run", "/TN", "v2rayN_gui_run"],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            lg.warn(f"gui_restart_ui: run: {e}")
    # 5) ждём, пока GUI поднимет ядро и связь появится
    port = client.get("socks_port", 10808)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if _proc_exists(client.get("exe", "")) and port_works(port, timeout=6):
            try:
                st.data["last_gui_sync"] = time.time()
                st.save()
            except Exception:
                pass
            lg.info("gui_restart_ui: GUI перезапущен и синхронизирован (связь есть)")
            return True
        time.sleep(2)
    lg.warn("gui_restart_ui: связь после перезапуска GUI не подтвердилась")
    return False


# План А: российские ресурсы — НАПРЯМУЮ (мимо прокси), остальное — через пул.
# Категории в базе v2rayN (проверено): CATEGORY-RU, TLD-RU, CATEGORY-GOV-RU,
# CATEGORY-BANK-RU, PRIVATE и т.д.
RU_DIRECT_DOMAINS = ["geosite:category-ru", "geosite:tld-ru",
                     "geosite:category-gov-ru", "geosite:category-bank-ru",
                     # ВНИМАНИЕ: только категории, реально присутствующие во вшитом
                     # geosite.dat. category-medicine-ru в нём ОТСУТСТВУЕТ, и одна
                     # такая категория валит ВЕСЬ конфиг ядра («failed to parse
                     # domain») — пул не применяется вообще. Проверять новые
                     # категории через `xray run -test`.
                     "geosite:private"]
RU_DIRECT_IPS = ["geoip:ru", "geoip:private"]

# Локальные имена: ретрекер провайдера и mDNS-имена. Доступны ТОЛЬКО напрямую
# (retracker.local живёт внутри сети провайдера, *.local — в локальном сегменте),
# поэтому правило для них идёт ПЕРВЫМ — иначе торрент-вход уводил бы их в прокси.
LOCAL_DIRECT_DOMAINS = ["full:retracker.local", "domain:local"]


def _asset_dir(core: str, cfg: dict = None) -> str:
    """Папка с базами geosite.dat/geoip.dat.

    Ищем: кэш в state -> рядом с ядром и на уровень выше (v2rayN кладёт базы
    в bin/, а ядро в bin/xray/) -> подпапки bin/ и xray/ у кандидатов.
    Раньше при запуске из планировщика ядро не находилось и РФ-правила
    молча отключались (весь трафик шёл через прокси).
    """
    if cfg is not None:
        try:
            cached = StateStore(cfg).data.get("geo_asset_dir") or ""
            if cached and (os.path.isfile(os.path.join(cached, "geoip.dat"))
                           or os.path.isfile(os.path.join(cached, "geosite.dat"))):
                return cached
        except Exception:
            pass
    cands = []
    if core:
        d = os.path.dirname(core)
        for _ in range(4):
            if d and d not in cands:
                cands.append(d)
            d = os.path.dirname(d)
    extra = []
    for c in cands:
        for sub in ("bin", "xray"):
            cand = os.path.join(c, sub)
            if cand not in cands:
                extra.append(cand)
    for d in cands + extra:
        if d and (os.path.isfile(os.path.join(d, "geosite.dat"))
                  or os.path.isfile(os.path.join(d, "geoip.dat"))):
            if cfg is not None:
                try:
                    st = StateStore(cfg)
                    st.data["geo_asset_dir"] = d
                    st.save()
                except Exception:
                    pass
            return d
    return os.path.dirname(core) if core else ""


def ru_direct_enabled(cfg: dict, core: str = "") -> bool:
    """Нужна ли маршрутизация «РФ → напрямую» (только при наличии гео-баз).

    Один источник истины — ``watch.ru_direct`` в конфиге. Раньше сборка конфига
    вызывалась без этого признака, и он по умолчанию был True: в режиме v2rayN
    «РФ → напрямую» работал ВСЕГДА, независимо от настройки.
    """
    want = bool((cfg or {}).get("watch", {}).get("ru_direct", False))
    if not want:
        return False
    try:
        core = core or (cfg.get("client", {}) or {}).get("core") or ""
    except Exception:
        core = ""
    if core and _asset_dir(core, cfg):
        return True
    log.get_logger().warn("ru_direct включён, но geosite.dat/geoip.dat не найдены — "
                          "правила РФ→direct отключены (весь трафик через прокси)")
    return False



# Поля outbound, которые xray УЖЕ УДАЛИЛ: сервер с таким полем роняет старт
# ВСЕГО конфига — одного битого сервера хватало, чтобы потерять весь пул и
# остаться без связи (в логе: «lowInsecure has been removed and migrated»).
REMOVED_OUT_KEYS = {"lowinsecure", "allowinsecure"}


def _sanitize_outbound(ob):
    """Убирает из outbound поля, удалённые в текущем xray (рекурсивно)."""
    if not isinstance(ob, dict):
        return ob
    out = {}
    for k, v in ob.items():
        if str(k).lower() in REMOVED_OUT_KEYS:
            continue
        if isinstance(v, dict):
            v = _sanitize_outbound(v)
        elif isinstance(v, list):
            v = [_sanitize_outbound(x) if isinstance(x, dict) else x for x in v]
        out[k] = v
    return out


def _fields_from_err(err) -> set:
    """Имя УДАЛЁННОГО поля из сообщения ядра.

    Ядро пишет: `lowInsecure" has been removed and migrated to
    "pinnedPeerCertSha256"(pcs)`. Брать все кавычки нельзя — в текст попадут
    НОВЫЕ имена (pinnedPeerCertSha256), и фильтр выкинул бы здоровые серверы.
    """
    s = str(err or "")
    out = set()
    m = re.search(r'"?([A-Za-z_][A-Za-z0-9_]*)"?\s+has been removed', s, re.I)
    if m:
        out.add(m.group(1).lower())
    if not out:
        out = {x.lower() for x in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', s)}
    return out


def _out_has_field(ob, fields: set) -> bool:
    try:
        s = json.dumps(ob).lower()
    except Exception:
        return False
    return any(f'"{f}"' in s for f in fields)


def build_balancer_config(servers: list, port: int, listen: str = "0.0.0.0",
                          probe_url: str = None, ru_direct: bool = None,
                          torrent_port: int = 10820) -> dict:
    """Конфиг ОДНОГО ядра с балансировщиком: внутри несколько серверов,
    xray сам следит за живостью (observatory) и направляет новые соединения
    на живой/быстрый (leastPing). Переключение НЕ требует перезапуска ядра —
    поэтому связь не рвётся."""
    import xraytest as XT
    outs = []
    for i, s in enumerate(servers):
        ob = XT.outbound_json(s)
        if ob is None:
            continue
        # удалённые поля вычищаем ДО записи конфига: один такой сервер роняет
        # весь конфиг, а вместе с ним и весь пул
        ob = _sanitize_outbound(dict(ob))
        ob["tag"] = f"proxy-out-{i}"
        outs.append(ob)
    if not outs:
        return None
    outs.append({"tag": "direct", "protocol": "freedom"})
    outs.append({"tag": "block", "protocol": "blackhole"})
    if ru_direct is None:
        ru_direct = True
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "in", "port": int(port), "listen": listen,
            "protocol": "mixed",
            "settings": {"udp": True, "auth": "noauth"},
            "sniffing": {"enabled": True,
                         "destOverride": ["http", "tls", "quic"]},
        }, {
            # Второй вход — СТРОГО для торрент-клиента (qBittorrent). Трекер-анонсы
            # всегда через пул прокси, без RU-исключений (иначе трекер с российским
            # доменом ушёл бы напрямую и остался заблокированным). Слушает только
            # локально. P2P через него не идёт — клиент не проксирует peer-соединения.
            "tag": "torrent-in", "port": int(torrent_port), "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": True, "auth": "noauth"},
        }],
        "outbounds": outs,
        "dns": _build_dns(ru_direct),
        "observatory": {
            "subjectSelector": ["proxy-out"],
            "probeUrl": probe_url or "https://www.gstatic.com/generate_204",
            "probeInterval": "5s",
            "enableConcurrency": True,
        },
        "routing": _build_routing(ru_direct),
    }


def _build_routing(ru_direct: bool) -> dict:
    """Правила: российское — напрямую (direct), остальное — через пул (bal).

    Порядок ВАЖЕН: правила direct идут первыми; домены приоритетнее IP
    (сайт может жить на зарубежном CDN, но быть российским). domainStrategy
    IPIfNonMatch: если домен не совпал — адрес резолвится и правило по IP
    проверяется повторно.
    """
    rules = [{"type": "field", "outboundTag": "direct",
              "domain": LOCAL_DIRECT_DOMAINS},   # ПЕРВЫМ: локальные имена — напрямую
             {"type": "field", "inboundTag": ["torrent-in"],
              "balancerTag": "bal"}]   # торрент-вход всегда через прокси
    if ru_direct:
        rules.append({"type": "field", "outboundTag": "direct",
                      "domain": RU_DIRECT_DOMAINS})
        rules.append({"type": "field", "outboundTag": "direct",
                      "ip": RU_DIRECT_IPS})
    rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "bal"})
    return {
        "domainStrategy": "IPIfNonMatch",
        "balancers": [{"tag": "bal", "selector": ["proxy-out"],
                       "strategy": {"type": "leastPing"}}],
        "rules": rules,
    }


def _build_dns(ru_direct: bool) -> dict:
    """DNS согласован с маршрутизацией: российские домены резолвит российский
    DNS (Yandex) напрямую, остальное — публичные DNS. Иначе российские сайты
    могут получить «не тот» адрес и уйти через прокси."""
    if not ru_direct:
        return {"servers": [{"address": "localhost", "domains": LOCAL_DIRECT_DOMAINS},
                            "1.1.1.1", "8.8.8.8"]}
    return {
        "servers": [
            # локальные имена резолвит системный резолвер (провайдерский DNS)
            {"address": "localhost", "domains": LOCAL_DIRECT_DOMAINS},
            {"address": "77.88.8.8", "domains": RU_DIRECT_DOMAINS,
             "expectIPs": RU_DIRECT_IPS},
            {"address": "77.88.8.1", "domains": RU_DIRECT_DOMAINS,
             "expectIPs": RU_DIRECT_IPS},
            "1.1.1.1", "8.8.8.8",
        ],
        "queryStrategy": "UseIPv4",
    }



def _atomic_write_json(path: str, obj) -> None:
    """Записать JSON АТОМАРНО (tmp + os.replace).

    Ядро стартует из этого же файла: обычная запись «на лету» давала
    обрезанный JSON -> «failed to build outbound config», ядро падало,
    а старое уже было убито => связь пропадала.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _validate_config(core: str, conf: dict, cfg: dict):
    """Проверка конфига ядра ДО снятия рабочего ядра: запуск на свободном порту.

    Возвращает (True, "") если ядро стартует, иначе (False, причина).
    Живое ядро не затрагивается: слушаем 127.0.0.1 на случайном порту.
    """
    import hashlib as _hl
    import socket
    import tempfile
    # КЭШ: тот же конфиг (по содержимому) уже проверялся -> не тратим ~2 с на
    # пробный запуск ядра при каждом восстановлении.
    try:
        v_sig = _hl.md5(json.dumps(conf, sort_keys=True,
                                   ensure_ascii=False).encode("utf-8")).hexdigest()
        st_v = StateStore(cfg)
        if st_v.data.get("validate_sig") == v_sig and \
                time.time() - float(st_v.data.get("validate_ts", 0) or 0) < 3600:
            return True, ""
    except Exception:
        v_sig = None
    test = json.loads(json.dumps(conf))
    # каждому входу — СВОЙ свободный порт (иначе два входа на одном порту
    # дают «failed to listen TCP» и валидация ложно проваливается)
    for ib in test.get("inbounds", []):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            ib["port"] = s.getsockname()[1]
        finally:
            s.close()
        ib["listen"] = "127.0.0.1"
    fd, tmp_conf = tempfile.mkstemp(suffix=".json", prefix="xray-validate-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(test, f, ensure_ascii=False)
    log_p = os.path.join(cfg["paths"]["state_dir"], "xray_validate.log")
    try:
        env_v = dict(os.environ)
        env_v["XRAY_LOCATION_ASSET"] = _asset_dir(core, cfg)
        cwd_v = os.path.dirname(core)
        for d in (os.path.dirname(core), os.path.dirname(os.path.dirname(core))):
            if os.path.isfile(os.path.join(d, "geoip.dat")):
                cwd_v = d
                break
        with open(log_p, "w", encoding="utf-8") as lf:
            pv = subprocess.Popen([core, "run", "-c", tmp_conf], cwd=cwd_v, env=env_v,
                                  stdout=lf, stderr=subprocess.STDOUT,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # Ждём готовности вместо глухих 2 с: конфиг валиден, если ядро живо и
            # слушает назначенные порты. Невалидный конфиг падает ДО открытия
            # порта, поэтому «порт открылся» — достаточный признак.
            tst_ports = [ib.get("port") for ib in test.get("inbounds", [])
                         if ib.get("port")]
            alive = False
            dl = time.time() + 2.5
            while time.time() < dl:
                if pv.poll() is not None:
                    break
                try:
                    if tst_ports and all(port_listening(pp, timeout=0.15)
                                         for pp in tst_ports):
                        alive = True
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            if alive:
                # короткая пауза: не падает ли сразу после старта
                time.sleep(0.15)
                alive = pv.poll() is None
            if pv.poll() is None:
                pv.kill()
            pv.wait(timeout=10)
        if alive:
            if v_sig:
                try:
                    st_v = StateStore(cfg)
                    st_v.data["validate_sig"] = v_sig
                    st_v.data["validate_ts"] = time.time()
                    st_v.save()
                except Exception:
                    pass
            return True, ""
        try:
            tail = open(log_p, encoding="utf-8", errors="replace").read()[-300:]
        except Exception:
            tail = ""
        m = re.search(r"(failed to[^\n]{0,180})", tail)
        return False, (m.group(1) if m else tail.strip()[-180:])
    except Exception as e:
        return False, f"validate error: {e}"
    finally:
        try:
            os.unlink(tmp_conf)
        except Exception:
            pass


def _lock_path(cfg: dict, name: str) -> str:
    return os.path.join(cfg["paths"]["state_dir"], f"{name}.lock")


def _lock(cfg: dict, name: str, ttl: float = 180.0, wait: float = 0.0):
    """Простой файловый lock: ядром управляет ТОЛЬКО один процесс.

    watch/guard/auto работали параллельно и перезапускали ядро друг за другом.
    Возвращает True, если блокировка взята (снимать через _unlock).
    """
    p = _lock_path(cfg, name)
    deadline = time.time() + wait
    while True:
        try:
            if os.path.isfile(p):
                age = time.time() - os.path.getmtime(p)
                if age > ttl:          # протухшая блокировка (процесс умер)
                    os.unlink(p)
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            if time.time() >= deadline:
                return False
            time.sleep(0.5)
        except Exception:
            return False


def _unlock(cfg: dict, name: str) -> None:
    try:
        os.unlink(_lock_path(cfg, name))
    except Exception:
        pass



# ---------------------------------------------------------------------------
# Системный прокси (WinINET). v2rayN при закрытии его выключает, и браузер
# уходит напрямую: YouTube/заблокированные сбрасываются, хотя наш туннель жив
# и «связь есть» пишется честно — но система им не пользуется.
# ---------------------------------------------------------------------------

def _sys_proxy_state() -> tuple:
    """(включён, сервер) системного прокси из реестра WinINET."""
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        en, _ = winreg.QueryValueEx(k, "ProxyEnable")
        try:
            srv, _ = winreg.QueryValueEx(k, "ProxyServer")
        except FileNotFoundError:
            srv = ""
        winreg.CloseKey(k)
        return bool(en), str(srv)
    except Exception:
        return False, ""


def _sys_proxy_apply(server: str) -> bool:
    """Включает системный прокси и сообщает WinINET об изменении (без окон)."""
    import ctypes
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                           0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(k)
    except Exception:
        return False
    try:
        wi = ctypes.windll.wininet
        wi.InternetSetOptionW(0, 39, 0, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
        wi.InternetSetOptionW(0, 37, 0, 0)   # INTERNET_OPTION_REFRESH
    except Exception:
        pass
    return True


def ensure_system_proxy(client: dict, cfg: dict) -> bool:
    """Системный прокси должен смотреть на наш балансировщик.

    Вызывается из ensure_balancer ДО всех ранних return: иначе при замороженном
    пуле проверка не выполнялась бы вовсе.
    """
    lg = log.get_logger()
    if proxy_off(cfg):
        # выключено ВРУЧНУЮ: системный прокси не возвращаем
        return False
    if cfg.get("client", {}).get("manage_system_proxy", True) is False:
        return True
    exe_name = cfg["client"]["exe"]
    # GUI v2rayN жив -> прокси его забота, не вмешиваемся (главное правило проекта)
    try:
        # быстрый ctypes-путь (~5 мс); powershell из config.py (~500 мс) —
        # только как аварийный fallback
        try:
            if _proc_paths_by_name_fast(os.path.splitext(exe_name)[0]):
                return True
        except Exception:
            from config import _proc_paths_by_name as _ppbn
            if _ppbn(os.path.splitext(exe_name)[0]):
                return True
    except Exception:
        pass
    port = int(client.get("socks_port", 10808))
    want = f"127.0.0.1:{port}"
    en, srv = _sys_proxy_state()
    if en and srv.strip() == want:
        return True
    if _sys_proxy_apply(want):
        lg.warn(f"Системный прокси был {'включён на ' + (srv or '—') if en else 'ВЫКЛЮЧЕН'} — "
                f"включил на {want} (браузер ходил напрямую)")
        return True
    lg.error("не удалось включить системный прокси")
    return False



def port_listening(port: int, timeout: float = 0.5) -> bool:
    """Порт слушается? Быстрая TCP-проверка (~1 мс), без curl.

    Нужна, чтобы отличить «ядро умерло» (пересборка немедленно) от «ядро живо,
    но трафик не идёт» (обычный цикл восстановления).
    """
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _proc_paths_by_name_fast(name: str) -> list:
    """Пути процессов по имени exe — через ctypes (~5 мс).

    Прежний путь через powershell занимал ~500 мс и вызывался в каждом цикле
    (ensure_system_proxy), съедая больше, чем все сетевые проверки вместе.
    """
    out = []
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        arr = (wintypes.DWORD * 4096)()
        need = wintypes.DWORD(0)
        if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
            return out
        cnt = need.value // ctypes.sizeof(wintypes.DWORD)
        want = name.lower()
        if want.endswith(".exe"):
            want = want[:-4]
        buf = ctypes.create_unicode_buffer(1024)
        for i in range(cnt):
            pid = arr[i]
            if not pid:
                continue
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                continue
            try:
                sz = wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                    base = os.path.splitext(os.path.basename(buf.value))[0].lower()
                    if base == want:
                        out.append(buf.value)
            finally:
                k32.CloseHandle(h)
    except Exception:
        pass
    return out


def _tray_pids() -> list:
    """Живые процессы трея.

    Шаблон собирается в рантайме ('tray' + '.ps1'): иначе запрос находил САМ
    СЕБЯ (в его командной строке есть текст 'tray.ps1') и guard навсегда решал,
    что трей жив, — значок не поднимался после падения.
    """
    try:
        pat = "tray" + ".ps1"
        ps = ("$pat = '" + pat + "'; $me = $PID; "
              "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
              "Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -like ('*' + $pat + '*') } | "
              "ForEach-Object { $_.ProcessId }")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=25, text=True, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def tray_alive(cfg: dict, fresh_s: float = 45.0) -> bool:
    """Жив ли трей: свежий heartbeat (state/tray_beat) или живой pid."""
    try:
        sd = cfg["paths"]["state_dir"]
        try:
            pid = int((open(os.path.join(sd, "tray.pid"), encoding="utf-8").read() or "0").strip())
        except Exception:
            pid = 0
        beat = os.path.join(sd, "tray_beat")
        ts = 0.0
        if os.path.isfile(beat):
            try:
                ts = float((open(beat, encoding="utf-8").read() or "0").strip())
            except Exception:
                ts = 0.0
        # быстрый путь: pid жив и heartbeat свежий
        if pid and _pid_alive(pid) and ts and (time.time() - ts) <= fresh_s:
            return True
        # медленный путь (источник истины): живой процесс трея
        return bool(_tray_pids())
    except Exception:
        return False


def ensure_tray_alive(cfg: dict) -> bool:
    """Поднимает трей, если он умер.

    Без сторожа значок и окно-уведомления пропадали НАВСЕГДА: у трея не было
    ни pid-файла, ни heartbeat, ни перезапуска (жалоба «пропал значок из трея
    и уведомления»).
    """
    lg = log.get_logger()
    if tray_alive(cfg):
        return True
    pids = _tray_pids()
    if pids:
        # трей ЖИВ, протух только pid-файл: починить файл, а не плодить дубли
        try:
            with open(os.path.join(cfg["paths"]["state_dir"], "tray.pid"), "w", encoding="utf-8") as f:
                f.write(str(pids[-1]))
        except Exception:
            pass
        lg.warn(f"ensure_tray_alive: трей жив (pid {pids[-1]}), pid-файл поправлен")
        return True
    vbs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_launch.vbs")
    if not os.path.isfile(vbs):
        return False
    try:
        subprocess.Popen(["wscript.exe", vbs],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lg.warn("ensure_tray_alive: трей не отвечал — перезапущен")
        return True
    except Exception as e:
        lg.error(f"ensure_tray_alive: {e}")
        return False


def _is_our_balancer(cfg: dict, pid) -> bool:
    """Наше ли это ядро-балансировщик (по командной строке процесса).

    Страховка от протухшего `state/balancer.pid`: без неё сторож принимает
    СВОЁ здоровое ядро за чужое и снимает его — лишний обрыв связи.
    """
    try:
        conf = os.path.join(cfg["paths"]["state_dir"], "xray_balancer.json").lower()
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").CommandLine'],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or b"").decode("utf-8", "replace").lower()
        return conf in line
    except Exception:
        return False


def _link_ok(cfg: dict, port: int, fresh_s: float = 45.0) -> bool:
    """Живость связи для решений о заморозке/перезапуске.

    Приоритет отметок сторожа (state):
      1) свежий ПРОВАЛ probe  -> False НЕМЕДЛЕННО: при настоящем обрыве
         пересборка стартует быстрее, чем с дорогой проверкой (та занимала 650 мс);
      2) свежий УСПЕХ probe   -> True без curl (лёгкий путь);
      3) отметок нет/протухли -> полная port_works (как было).
    """
    now = time.time()
    try:
        st = StateStore(cfg)
        fail_ts = float(st.data.get("last_probe_fail_ts", 0) or 0)
        if fail_ts and now - fail_ts <= fresh_s:
            return False
        ok_ts = float(st.data.get("last_probe_ok_ts", 0) or 0)
        if ok_ts and now - ok_ts <= fresh_s:
            return True
    except Exception:
        pass
    ok = port_works(port, timeout=6)
    if ok:
        try:
            st = StateStore(cfg)
            st.data["last_probe_ok_ts"] = time.time()
            st.data["last_probe_fail_ts"] = 0
            st.save()
        except Exception:
            pass
    return ok


def stop_balancer(cfg: dict):
    """Снимает ядро балансировщика (по pid-файлу), если оно наше."""
    p = os.path.join(cfg["paths"]["state_dir"], "balancer.pid")
    try:
        pid = open(p, encoding="utf-8").read().strip()
        if pid.isdigit():
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                           timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    try:
        os.unlink(p)
    except Exception:
        pass


def start_balancer(client: dict, servers: list, cfg: dict, port: int = None,
                   fast_ok: bool = False,
                   listen: str = "0.0.0.0") -> bool:
    """Поднимает ядро-балансировщик на порту клиента (или заданном).

    Возвращает True, если порт открылся и через него есть РЕАЛЬНЫЙ трафик.
    """
    lg = log.get_logger()
    port = int(port or client.get("socks_port", 10808))
    core = client.get("core")
    if not core or not os.path.isfile(core):
        lg.error("start_balancer: ядро не найдено")
        return False
    conf = build_balancer_config(servers, port, listen=listen,
                                 ru_direct=ru_direct_enabled(cfg, core))
    if conf is None:
        lg.error("start_balancer: нет валидных outbound")
        return False
    conf_path = os.path.join(cfg["paths"]["state_dir"], "xray_balancer.json")
    # ПРОВЕРКА ДО СНЯТИЯ СТАРОГО ЯДРА: невалидный конфиг больше не оставляет
    # систему без связи — прежнее ядро продолжает работать.
    # НО если прежнее ядро уже мертво (порт свободен) — снимать нечего, и ждать
    # проверку незачем: она уходит в фон, а связь поднимается сразу. Битый конфиг
    # виден по p.poll() ниже — цикл уйдёт на полный перебор серверов.
    # ВСЕГДА синхронно: фоновая проверка давала худший исход — пул с сервером,
    # который ядро не переваривает (XHTTP), поднимался, падал, и восстановление
    # уходило в полный перебор на минуты. Живость важнее 0.4 с экономии.
    ok_v, err_v = _validate_config(core, conf, cfg)
    if not ok_v:
        # Ядро назвало удалённое поле — выкидываем ТОЛЬКО серверы с ним и
        # проверяем снова: иначе один битый сервер в пуле = нет связи у всех.
        import xraytest as XT
        for _try in range(3):
            fields = _fields_from_err(err_v)
            if not fields:
                break
            keep = [s for s in servers if not _out_has_field(XT.outbound_json(s) or {}, fields)]
            if len(keep) == len(servers) or not keep:
                break
            lg.warn(f"start_balancer: из пула исключены {len(servers) - len(keep)} сервер(ов) "
                    f"с удалёнными полями {sorted(fields)}")
            servers = keep
            conf = build_balancer_config(servers, port, listen=listen,
                                         ru_direct=ru_direct_enabled(cfg, core))
            if conf is None:
                break
            ok_v, err_v = _validate_config(core, conf, cfg)
            if ok_v:
                break
    if not ok_v:
        lg.error(f"start_balancer: новый конфиг невалиден ({err_v}) — прежнее ядро оставлено")
        return False
    _atomic_write_json(conf_path, conf)
    log_path = os.path.join(cfg["paths"]["state_dir"], "xray_balancer.log")
    cwd = os.path.dirname(core)
    for d in (os.path.dirname(core), os.path.dirname(os.path.dirname(core))):
        if os.path.isfile(os.path.join(d, "geoip.dat")):
            cwd = d
            break
    # Порт мог быть занят чужим ядром (GUI поднимает своё при Reload/старте):
    # освобождаем перед запуском, иначе bind fail и связи нет.
    ours_pid = None
    try:
        ours_pid = open(os.path.join(cfg["paths"]["state_dir"], "balancer.pid"),
                        encoding="utf-8").read().strip()
    except Exception:
        pass
    # Торрент-вход того же ядра (10820) тоже должен быть свободен: если его
    # держит ДРУГОЕ ядро, старт упадёт с «failed to listen TCP on 10820» и
    # связь не поднимется. Диагностируем явно, чтобы причина была видна.
    tport = int(conf.get("inbounds", [{}])[-1].get("port", 0) or 0)
    if tport:
        tp = _port_pid(tport)
        if tp and str(tp) != str(ours_pid or ""):
            lg.warn(f"start_balancer: торрент-порт {tport} занят другим ядром (pid {tp}) — "
                    f"старт может упасть")
    for _ in range(3):
        cur = _port_pid(port)
        if not cur:
            break
        if ours_pid and str(cur) == str(ours_pid):
            # это НАШ прежний балансировщик — снимаем мягко, не «чужим ядром»
            lg.info(f"start_balancer: останавливаю прежний балансировщик (pid {cur})")
            try:
                subprocess.run(["taskkill", "/PID", str(cur), "/F"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
        else:
            lg.warn(f"start_balancer: порт {port} занят чужим ядром (pid {cur}) — освобождаю")
            kill_kernels(cfg, why="balancer-bind")
        # ждём освобождения порта шагами по 0.1 с (вместо глухих 1.5 с): при
        # мёртвом ядре порт свободен сразу, и пересборка не ждёт впустую
        for _ in range(30):
            if not _port_pid(port):
                break
            time.sleep(0.1)
    try:
        env_b = dict(os.environ)
        adir = _asset_dir(core, cfg)
        env_b["XRAY_LOCATION_ASSET"] = adir
        p = subprocess.Popen([core, "run", "-c", conf_path], cwd=cwd, env=env_b,
                             stdout=open(log_path, "w", encoding="utf-8"),
                             stderr=subprocess.STDOUT,
                             creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                            | getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)))
    except Exception as e:
        lg.error(f"start_balancer: Popen: {e}")
        return False
    try:
        with open(os.path.join(cfg["paths"]["state_dir"], "balancer.pid"), "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    deadline = time.time() + 45
    while time.time() < deadline:
        if p.poll() is not None:
            tail = ""
            try:
                tail = open(log_path, encoding="utf-8", errors="replace").read()[-400:]
            except Exception:
                pass
            lg.error("start_balancer: ядро упало; " + tail)
            return False
        if fast_ok:
            # ГОРЯЧИЙ путь восстановления: порт открылся -> связь поднимаем сразу,
            # подтверждение трафика делает сторож (probe) в ту же секунду. Ждать
            # curl здесь = отдавать ещё ~0.3 с обрыва на пустом месте.
            if port_listening(port, timeout=0.2):
                lg.info(f"start_balancer: балансировщик поднят на {listen}:{port} "
                        f"({len(servers)} серверов), порт открыт (подтверждение — probe)")
                return True
            time.sleep(0.1)
            continue
        # быстрая проверка (204 + валидный TLS): требование скачать 200 КБ
        # здесь давало ложное «трафик не подтвердился» на медленных серверах
        code_b, _tb, sslv_b, _ = _curl_metrics(port, PROBE_URL, 6)
        if code_b == 204 and sslv_b == 0:
            lg.info(f"start_balancer: балансировщик поднят на {listen}:{port} "
                    f"({len(servers)} серверов), связь есть")
            save_lastgood(cfg, servers)     # страховка: запомнить рабочий состав
            return True
        time.sleep(0.15)
    lg.warn("start_balancer: порт открылся, но трафик не подтвердился")
    return False


def balancer_pool(client: dict, cfg: dict, n: int = 10, min_speed_kbps: float = 0) -> list:
    """Пул серверов для балансировщика: из БД, без чёрного списка,
    сначала с реальной скоростью."""
    import xraytest as XT
    rows = [r for r in read_db_rows(client["db"])
            if (r.get("Remarks") or "").startswith(AUTO_TAG)]
    bl = bl_active(cfg)
    out = []
    for r in rows:
        if str(r.get("IndexId")) in bl:
            continue
        s = row_to_server(r)
        if s is None:
            continue
        s._row_index_id = str(r.get("IndexId"))
        # Скорость 0 = через сервер НЕ шёл трафик (CF-заглушка отдаёт 204, но
        # данных не везёт). В пул балансировщика такие нельзя: leastPing берёт их
        # по низкому пингу, ядро «работает», а связи нет — 81% таких записей в
        # топ-10 дали 2 ч 42 мин простоя (30.09.2026).
        if (s.speed_kbps or 0) <= 0:
            continue
        if min_speed_kbps and (s.speed_kbps or 0) < min_speed_kbps:
            continue
        # XHTTP-транспорт ядро не переваривает: конфиг валиден на вид, но ядро
        # падает на старте -> связь уходит в полный перебор на минуты. В пул нельзя.
        try:
            if "xhttp" in json.dumps(XT.outbound_json(s)).lower():
                continue
        except Exception:
            pass
        out.append(s)
    out.sort(key=lambda s: -(s.speed_kbps or 0))
    return out[:n]


def _pool_sig(pool: list) -> str:
    """Подпись состава пула по адресам (IndexId в БД меняются при перезаписи,
    а адрес сервера — стабилен; иначе ядро перезапускалось бы каждый цикл)."""
    return ",".join(sorted(f"{s.address}:{s.port}" for s in pool))


def _our_core_pids(cfg: dict) -> list:
    """Все НАШИ ядра xray (в командной строке наш конфиг балансировщика).

    Нужен при ручном выключении: осиротевшее ядро держит торрент-порт 10820 и
    роняет следующую пересборку («failed to listen TCP on 10820»).
    """
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='xray.exe'\" | "
              "Where-Object { $_.CommandLine -like '*xray_balancer.json*' } | "
              "ForEach-Object { $_.ProcessId }")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=25, text=True, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def proxy_off_path(cfg: dict) -> str:
    """Флаг РУЧНОГО отключения прокси (ставится из меню трея)."""
    return os.path.join(cfg["paths"]["state_dir"], "proxy_off.flag")


def proxy_off(cfg: dict) -> bool:
    """Прокси выключен вручную: автоподъём запрещён во всех циклах."""
    try:
        return os.path.isfile(proxy_off_path(cfg))
    except Exception:
        return False


def _sys_proxy_disable() -> bool:
    """Выключить системный прокси (браузер пойдёт напрямую)."""
    import ctypes
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                           0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(k)
    except Exception:
        return False
    try:
        wi = ctypes.windll.wininet
        wi.InternetSetOptionW(0, 39, 0, 0)   # SETTINGS_CHANGED
        wi.InternetSetOptionW(0, 37, 0, 0)   # REFRESH
    except Exception:
        pass
    return True


def set_proxy_off(cfg: dict, on: bool, client: dict = None, why: str = "tray") -> bool:
    """Ручное выключение/включение прокси (меню трея).

    on=True:  флаг -> остановить НАШЕ ядро -> выключить системный прокси.
              Сторож и цикл после этого ничего не поднимают: пользователь сам
              решил выйти в интернет напрямую (например, живых серверов нет).
    on=False: снять флаг -> вернуть системный прокси; ядро поднимет сторож.
    """
    lg = log.get_logger()
    sd = cfg["paths"]["state_dir"]
    client = client or {}
    port = int(client.get("socks_port", 10808))
    if on:
        try:
            with open(proxy_off_path(cfg), "w", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "by": why}, ensure_ascii=False))
        except Exception as e:
            lg.error(f"set_proxy_off: флаг не записан: {e}")
            return False
        killed = []
        for cp in set(_our_core_pids(cfg) + ([_port_pid(port)] if _port_pid(port) else [])):
            if not cp:
                continue
            if cp == _port_pid(port) and not _is_our_balancer(cfg, cp):
                continue          # на порту чужое ядро — не трогаем
            try:
                subprocess.run(["taskkill", "/PID", str(cp), "/F"], capture_output=True,
                               timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                killed.append(cp)
            except Exception:
                pass
        if killed:
            lg.warn(f"Прокси выключен вручную: наши ядра остановлены ({killed})")
        _sys_proxy_disable()
        lg.warn("ПРОКСИ ВЫКЛЮЧЕН ВРУЧНУЮ: автоподъём приостановлен (меню трея)")
        try:
            import tray_status as TS
            TS.beat(cfg, "off", tip="Прокси выключен вручную")
        except Exception:
            pass
        return True
    try:
        os.remove(proxy_off_path(cfg))
    except Exception:
        pass
    ensure_system_proxy(client, cfg)
    lg.warn("Прокси включён вручную: поднимаю связь")
    try:
        # не ждём следующего тика сторожа (до 30-60 с): поднимаем сразу
        exe = sys.executable or "pythonw.exe"
        if exe.lower().endswith("python.exe"):
            exe = exe[:-len("python.exe")] + "pythonw.exe"
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2ray_updater.py")
        subprocess.Popen([exe, script, "--guard"], cwd=os.path.dirname(script),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000008)
    except Exception as e:
        lg.warn(f"set_proxy_off: не смог запустить guard сразу: {e}")
    try:
        import tray_status as TS
        TS.beat(cfg, "restoring", tip="Прокси включён — поднимаю связь")
    except Exception:
        pass
    return True


def lastgood_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "lastgood_servers.json")


def save_lastgood(cfg: dict, servers: list) -> None:
    """Запомнить последний РАБОЧИЙ состав пула (для аварийного подъёма)."""
    try:
        import dataclasses
        data = {"ts": time.time(), "servers": [dataclasses.asdict(s) for s in servers]}
        _atomic_write_json(lastgood_path(cfg), data)
    except Exception:
        pass


def load_lastgood(cfg: dict) -> list:
    """Восстановить последний рабочий состав пула."""
    try:
        import dataclasses
        import parser as P
        d = json.load(open(lastgood_path(cfg), encoding="utf-8"))
        fields = {f.name for f in dataclasses.fields(P.Server)}
        out = []
        for row in (d.get("servers") or []):
            try:
                out.append(P.Server(**{k: v for k, v in row.items() if k in fields}))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _emergency_restore(client: dict, cfg: dict, port: int) -> bool:
    """Аварийный подъём последнего рабочего состава.

    Нужен, когда серверов с подтверждённой скоростью нет ВООБЩЕ и связи тоже
    нет: вместо многочасового простоя поднимаем последний рабочий состав и
    продолжаем искать живые серверы полным циклом.
    """
    lg = log.get_logger()
    st = StateStore(cfg)
    if time.time() - float(st.data.get("emergency_ts", 0) or 0) < 120:
        return False            # не долбим аварийный конфиг каждые 10 с
    servers = load_lastgood(cfg)
    if not servers:
        lg.warn("аварийный конфиг: сохранённого рабочего состава нет")
        return False
    st.data["emergency_ts"] = time.time()
    st.save()
    lg.warn(f"АВАРИЙНЫЙ КОНФИГ: живых серверов нет — поднимаю последний рабочий "
            f"состав ({len(servers)} серверов)")
    try:
        import tray_status as TS
        TS.beat(cfg, "restoring",
                tip="Живых серверов нет — поднимаю последний рабочий конфиг")
    except Exception:
        pass
    if not _lock(cfg, "balancer", ttl=240, wait=8):
        return False
    try:
        ok = start_balancer(client, servers, cfg, port=port, listen="0.0.0.0", fast_ok=False)
    finally:
        _unlock(cfg, "balancer")
    if ok:
        save_lastgood(cfg, servers)
        lg.info("аварийный конфиг: связь поднята на последнем рабочем составе")
    return ok


def ensure_balancer(client: dict, cfg: dict, n: int = 10, force: bool = False,
                    fast_ok: bool = False) -> bool:
    """Держит на порту клиента ЯДРО-БАЛАНСИРОВЩИК (несколько серверов внутри).

    xray сам переключает соединения на живой/быстрый сервер — поэтому связь
    НЕ рвётся при смене сервера (нет перезапуска ядра при отказе одного из пула).
    Перезапускает ядро только если состав пула изменился (или по force).
    """
    lg = log.get_logger()
    port = client.get("socks_port", 10808)
    if proxy_off(cfg):
        # РУЧНОЕ отключение: не трогаем ни ядро, ни системный прокси
        return True
    # ДО всех ранних return: иначе при замороженном пуле системный прокси
    # остался бы выключенным (v2rayN гасит его при закрытии) и браузер ходил
    # бы напрямую — «связь есть» в логе при нерабочем интернете.
    ensure_system_proxy(client, cfg)
    pool = balancer_pool(client, cfg, n)
    if not pool:
        lg.warn("ensure_balancer: пул пуст (нет [AUTO] вне чёрного списка)")
        return False
    # ФИЛЬТР ПО СКОРОСТИ: серверы с нулевой скоростью (CF-заглушки) только
    # портят пул; берём их лишь если быстрых совсем нет.
    min_kbps = float(cfg.get("watch", {}).get("min_speed_kbps", 0) or 0)
    if min_kbps:
        fast = [s for s in pool if (s.speed_kbps or 0) >= min_kbps]
        if fast:
            if len(fast) != len(pool):
                lg.info(f"ensure_balancer: отсеяны медленные ({len(pool) - len(fast)} шт, "
                        f"< {min_kbps:.0f} КБ/с)")
            pool = fast
        else:
            # Ни одного сервера с подтверждённой скоростью. Добивать пул
            # заглушками НЕЛЬЗЯ — именно это дало 2 ч 42 мин без связи.
            # Работающее ядро не трогаем; вызывающий уйдёт в полный цикл.
            lg.warn("ensure_balancer: нет серверов с подтверждённой скоростью — "
                    "пул не пересобираю (заглушки в пул не идут)")
            if not _link_ok(cfg, port):
                # СТРАХОВКА: проверенных серверов нет И связи нет — поднимаем
                # последний рабочий конфиг, чтобы не сидеть без связи
                if _emergency_restore(client, cfg, port):
                    return True
            return bool(_link_ok(cfg, port))
    pid_file = os.path.join(cfg["paths"]["state_dir"], "balancer.pid")
    st = StateStore(cfg)
    sig = _pool_sig(pool)
    # Признак «РФ → напрямую» влияет на конфиг ядра, поэтому смена настройки
    # обязана применяться НЕМЕДЛЕННО, а не после смены состава пула.
    want_ru = ru_direct_enabled(cfg, (client or {}).get("core"))
    # Ключа ещё нет (обновление со старой версии) -> считаем настройку
    # изменившейся один раз, чтобы применить её сразу.
    ru_changed = bool(st.data.get("balancer_ru", not want_ru)) != bool(want_ru)
    cur = _port_pid(port)
    ours = None
    try:
        ours = open(pid_file, encoding="utf-8").read().strip()
    except Exception:
        pass
    same_owner = bool(cur and ours and str(cur) == str(ours))
    if cur and not same_owner and _is_our_balancer(cfg, cur):
        # pid-файл устарел, а на порту НАШЕ ядро (тесты/падения могли переписать
        # файл): усыновляем ДО проверок заморозки, иначе здоровое ядро снимается
        # и связь рвётся на ровном месте
        lg.warn(f"ensure_balancer: pid-файл устарел — принимаю своё ядро {cur}")
        try:
            with open(pid_file, "w", encoding="utf-8") as f:
                f.write(str(cur))
            ours, same_owner = str(cur), True
        except Exception:
            pass
    if same_owner and not force:
        age = time.time() - float(st.data.get("balancer_ts", 0) or 0)
        max_age = float(cfg.get("watch", {}).get("balancer_max_age_s", 3600))
        if age < max_age and _link_ok(cfg, port) and not ru_changed:
            # состав мог измениться, но ядро работает и молодо: НЕ трогаем —
            # каждый перезапуск = короткий разрыв связи. Смена НАСТРОЙКИ
            # маршрутизации сюда не попадает: она применяется сразу.
            return True
        if ru_changed:
            lg.info("ensure_balancer: изменилась настройка РФ→напрямую — "
                    "перегенерирую конфиг ядра")
        if st.data.get("balancer_sig") != sig:
            lg.info("ensure_balancer: состав пула изменился — обновляю балансировщик")
    # ЗАМОРОЗКА ПУЛА: пока ядро живо и связь есть — состав не перетасовываем.
    # Пересчёт скоростей каждые 5 мин менял «десятку» => 228 перезапусков/сутки,
    # каждый из них = короткий разрыв связи.
    freeze_s = float(cfg.get("watch", {}).get("pool_freeze_s", 10800))
    age_now = time.time() - float(st.data.get("balancer_ts", 0) or 0)
    if same_owner and st.data.get("balancer_sig") and age_now < freeze_s \
            and _link_ok(cfg, port) and not ru_changed:
        if st.data.get("balancer_sig") != sig:
            lg.info("ensure_balancer: состав пула изменился, но ядро работает — "
                    f"пул заморожен ещё на {int((freeze_s - age_now) / 60)} мин")
        return True
    if same_owner and force:
        if st.data.get("balancer_sig") == sig and _link_ok(cfg, port) \
                and not ru_changed:
            return True
    elif cur and not same_owner:
        lg.warn(f"ensure_balancer: порт {port} занят чужим ядром ({cur}) — снимаю")
        kill_kernels(cfg, why="balancer-takeover")
    # один управляющий: без lock watch/guard/auto рвали ядро друг за другом
    if not _lock(cfg, "balancer", ttl=240, wait=8):
        lg.warn("ensure_balancer: другое управление ядром уже идёт — пропускаю цикл")
        return True
    try:
        ok = start_balancer(client, pool, cfg, port=port, listen="0.0.0.0",
                            fast_ok=fast_ok)
    finally:
        _unlock(cfg, "balancer")
    if ok:
        try:
            st.data["balancer_sig"] = sig
            st.data["balancer_n"] = len(pool)
            st.data["balancer_ru"] = bool(want_ru)
            st.data["balancer_ts"] = time.time()
            st.data["emergency_ts"] = 0        # нормальный пул — аварийный режим снят
            st.save()
        except Exception:
            pass
    return ok


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс (ctypes, ~0.1 мс).

    Точнее и быстрее, чем «порт не слушается»: порт мог мигнуть при перезапуске,
    а мёртвый процесс — уже не миг. При невозможности открыть процесс считаем
    живым, чтобы не провоцировать лишнюю пересборку.
    """
    try:
        import ctypes
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not h:
            return True
        try:
            return k32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(h)
    except Exception:
        return True


def _port_pid(port: int):
    """PID процесса, слушающего порт.

    Быстрый путь — GetExtendedTcpTable через ctypes (~1 мс, не зависит от числа
    соединений, в отличие от парсинга всего `netstat -ano` ~41 мс при торрентах).
    Любая ошибка -> прежний netstat-путь.
    """
    try:
        import ctypes
        from ctypes import wintypes
        AF_INET = 2
        TCP_TABLE_OWNER_PID_LISTENER = 3

        class _ROW(ctypes.Structure):
            _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD),
                        ("dwLocalPort", wintypes.DWORD), ("dwRemoteAddr", wintypes.DWORD),
                        ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD)]

        iphlp = ctypes.windll.iphlpapi
        size = wintypes.DWORD(0)
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET,
                                  TCP_TABLE_OWNER_PID_LISTENER, 0)
        buf = ctypes.create_string_buffer(size.value)
        if iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET,
                                     TCP_TABLE_OWNER_PID_LISTENER, 0) == 0:
            n = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
            rows = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
                               ctypes.POINTER(_ROW))
            want = int(port)
            for i in range(n):
                r = rows[i]
                p = ((r.dwLocalPort & 0xFF) << 8) | ((r.dwLocalPort >> 8) & 0xFF)
                if p == want:
                    return str(r.dwOwningPid)
            return None
    except Exception:
        pass
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.decode("cp1251", "replace")
        for line in out.splitlines():
            if "LISTENING" in line and f":{port} " in line:
                parts = line.split()
                if parts:
                    return parts[-1].strip()
    except Exception:
        pass
    return None


def ensure_port_free(client: dict, cfg: dict) -> bool:
    """ГАРАНТИЯ: порт клиента (10808) принадлежит GUI v2rayN.

    Если GUI-процесс существует, а на порту висит НАШ runtime-xray
    (state/xray_runtime.pid) — убиваем его и ждём освобождения порта.
    Если GUI НЕ запущен — порт остаётся (наш xray может работать для связи).
    Возвращает True, если порт свободен ИЛИ принадлежит GUI\\ядру.
    """
    import time as _t
    lg = log.get_logger()
    port = client.get("socks_port", 10808)

    def _gui_running() -> bool:
        exe_name = os.path.basename(client.get("exe", "v2rayN.exe"))
        try:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}",
                                "/FO", "CSV", "/NH"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return exe_name.lower().encode() in (r.stdout or b"").lower()
        except Exception:
            return False

    if not _gui_running():
        return True  # GUI нет — наш xray вправе держать порт

    # GUI есть: убираем ТОЛЬКО наш xray (конфиг в state_dir: runtime/switch)
    # PID-файл может быть устаревшим (ядро перезапущено), поэтому собираем
    # ВСЕ процессы, слушающие порт, и проверяем их командную строку.
    our_pids = []
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.decode("cp1251", "replace")
        for line in out.splitlines():
            if "LISTENING" in line and f":{port} " in line:
                parts = line.split()
                if parts and parts[-1].strip().isdigit():
                    p = int(parts[-1].strip())
                    if p not in our_pids:
                        our_pids.append(p)
    except Exception:
        pass
    killed = False
    for our_pid in our_pids:
        try:
            # проверяем, что это действительно наш xray (по командной строке)
            r = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={our_pid}\").CommandLine"],
                               capture_output=True, timeout=15, text=True, errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            cmd = (r.stdout or "").strip()
            if ("xray.exe" in cmd.lower() and
                    ("xray_runtime.json" in cmd or "xray_switch.json" in cmd)):
                # ВАЖНО: если GUI своё ядро не поднял, а связь держит НАШ xray —
                # убивать его нельзя (иначе рвём рабочий интернет каждые 5 мин).
                alive, _rr = probe_client(client, timeout=5)
                if alive:
                    lg.info(f"GUI запущен, но связь держит наш xray ({our_pid}) — оставляю")
                    continue
                lg.warn(f"GUI запущен, а на {port} наш xray ({our_pid}) — убиваю его")
                subprocess.run(["taskkill", "/PID", str(our_pid), "/F"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                killed = True
        except Exception:
            pass
    # дожидаемся, пока порт освободится (наш xray умрёт) либо займётся GUI-ядром
    deadline = _t.time() + 10
    while _t.time() < deadline:
        pid = _port_pid(port)
        if pid is None:
            return True
        try:
            p = int(pid)
        except Exception:
            return True
        if killed and p in our_pids:
            _t.sleep(0.5)
            continue
        return True
    lg.warn("ensure_port_free: порт всё ещё занят нашим xray после kill")
    return True


def stop_client(client: dict, wait: float = 20.0):
    import time as _t
    _nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    exe_name = os.path.basename(client["exe"].split("\\")[-1])
    # Защита: если GUI реально работает — НЕ убиваем (вернём False, вызовут soft)
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                           capture_output=True, timeout=15, creationflags=_nw)
        if exe_name.lower().encode() in (r.stdout or b"").lower():
            return False
    except Exception:
        pass
    subprocess.run(["taskkill", "/IM", exe_name, "/T", "/F"],
                   capture_output=True, timeout=20, creationflags=_nw)
    # убить и ядро (xray), чтобы порт гарантированно освободился
    subprocess.run(["taskkill", "/IM", "xray.exe", "/F"],
                   capture_output=True, timeout=20, creationflags=_nw)
    deadline = _t.time() + wait
    port = client.get("socks_port", 10808)
    while _t.time() < deadline:
        pid = _port_pid(port)
        if pid is None:
            return True
        if pid:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=10,
                           creationflags=_nw)
        _t.sleep(1)
    return _port_pid(port) is None


def start_client(client: dict):
    """Запуск GUI v2rayN. НЕ используется для активации (GUI не стартует
    из неинтерактивной сессии) - оставлен для ручного вызова.
    НЕ запускает второй экземпляр, если GUI уже работает."""
    exe = client["exe"]
    if not os.path.isfile(exe):
        return False
    # если GUI уже запущен — ничего не делаем (иначе второй экземпляр
    # перехватит фокус окном)
    try:
        name = os.path.basename(exe)
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if name.lower().encode() in (r.stdout or b"").lower():
            return True
    except Exception:
        pass
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


def kill_kernels(cfg: dict = None, why: str = "") -> None:
    """Снимает процессы ЯДРА xray.exe (ядро GUI и/или наши сироты).

    v2rayN.exe (GUI) НЕ трогаем — окно пользователя остаётся. Нужно, когда
    ядро GUI зависло/держится за мёртвый сервер: без этого порт 10808 занят
    нерабочим процессом и связь восстановить нечем.
    """
    lg = log.get_logger()
    try:
        r = subprocess.run(["taskkill", "/F", "/IM", "xray.exe"], capture_output=True,
                           timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"").decode("utf-8", "replace").strip().replace("\n", " | ")
        lg.warn(f"kill_kernels{'(' + why + ')' if why else ''}: ядро xray.exe снято: {out[:220]}")
    except Exception as e:
        lg.warn(f"kill_kernels: {e}")
    # ждём освобождения порта (до 6 с)
    import socket
    port = 10808
    try:
        port = int((cfg or {}).get("client", {}).get("socks_port", 10808)) or 10808
    except Exception:
        pass
    for _ in range(12):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                time.sleep(0.5)
        except Exception:
            return
    lg.warn(f"kill_kernels: порт {port} всё ещё занят")


def start_xray_direct(client: dict, server, cfg: dict, force: bool = False) -> bool:
    """Запуск xray напрямую (без GUI): socks на порту клиента + outbound.

    Запускает фоновый поток-дозорный, который убивает этот xray, как только
    появляется процесс v2rayN.exe (GUI) — чтобы не висеть на 10808 рядом с GUI.
    Возвращает True, если порт открылся и xray жив."""
    import xraytest as XT
    import socket
    import threading as _th
    lg = log.get_logger()
    # ГАРАНТИЯ ПОРТА: если GUI запущен — НЕ поднимаем своё ядро
    # (порт принадлежит GUI; иначе два процесса на 10808 и связи нет).
    exe_name_ = os.path.basename(client.get("exe", "v2rayN.exe"))
    try:
        _r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name_}", "/FO", "CSV", "/NH"],
                            capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if exe_name_.lower().encode() in (_r.stdout or b"").lower():
            if not force:
                lg.warn("start_xray_direct: GUI v2rayN запущен — своё ядро НЕ поднимаю "
                        "(порт принадлежит GUI)")
                return False
            # force: ядро GUI мертво/на мёртвом сервере — снимаем ТОЛЬКО ядро
            lg.warn("start_xray_direct(force): снимаю ядро GUI, поднимаю своё")
            try:
                write_bin_config(client, server, cfg)   # GUI поднимет нужный сервер
            except Exception:
                pass
            kill_kernels(cfg, why="force-failover")
    except Exception:
        pass
    ob = XT.outbound_json(server)
    if ob is None:
        lg.error("start_xray_direct: outbound не собрался")
        return False
    cfg_x = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "in",
            "port": client.get("socks_port", 10808),
            "listen": "0.0.0.0",
            "protocol": "mixed",
            "settings": {"udp": True, "auth": "noauth"},
        }],
        "outbounds": [ob],
    }
    conf_path = os.path.join(cfg["paths"]["state_dir"], "xray_runtime.json")
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump(cfg_x, f)
    core = client.get("core")
    if not core or not os.path.isfile(core):
        lg.error("start_xray_direct: ядро не найдено")
        return False
    cwd = os.path.dirname(core)
    for d in (os.path.dirname(core), os.path.dirname(os.path.dirname(core))):
        if os.path.isfile(os.path.join(d, "geoip.dat")):
            cwd = d
            break
    log_path = os.path.join(cfg["paths"]["state_dir"], "xray_runtime.log")
    # Порт мог быть занят чужим ядром (GUI поднимает своё при Reload/старте):
    # освобождаем перед запуском, иначе bind fail и связи нет.
    ours_pid = None
    try:
        ours_pid = open(os.path.join(cfg["paths"]["state_dir"], "balancer.pid"),
                        encoding="utf-8").read().strip()
    except Exception:
        pass
    for _ in range(3):
        cur = _port_pid(port)
        if not cur:
            break
        if ours_pid and str(cur) == str(ours_pid):
            # это НАШ прежний балансировщик — снимаем мягко, не «чужим ядром»
            lg.info(f"start_balancer: останавливаю прежний балансировщик (pid {cur})")
            try:
                subprocess.run(["taskkill", "/PID", str(cur), "/F"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
        else:
            lg.warn(f"start_balancer: порт {port} занят чужим ядром (pid {cur}) — освобождаю")
            kill_kernels(cfg, why="balancer-bind")
        time.sleep(1.5)
    try:
        env_b = dict(os.environ)
        adir = _asset_dir(core, cfg)
        env_b["XRAY_LOCATION_ASSET"] = adir
        p = subprocess.Popen([core, "run", "-c", conf_path], cwd=cwd, env=env_b,
                             stdout=open(log_path, "w", encoding="utf-8"),
                             stderr=subprocess.STDOUT,
                             creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                            | getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)))
        # PID-файл: чтобы ЛЮБОЙ следующий запуск/guard мог найти и убить
        # нашего runtime-xray, если он конфликтует с GUI (порт 10808).
        try:
            with open(os.path.join(cfg["paths"]["state_dir"], "xray_runtime.pid"),
                      "w") as f:
                f.write(str(p.pid))
        except Exception:
            pass
    except Exception as e:
        lg.error(f"start_xray_direct: {e}")
        return False
    # дозорный поток: если появился v2rayN.exe — завершаем наш xray (порт освободится);
    # если GUI НЕ появился за max_idle_sec — завершаем тоже (не оставлять сироту).
    exe_name = os.path.basename(client.get("exe", "v2rayN.exe")).lower()
    max_idle_sec = int(cfg.get("activation", {}).get("direct_idle_timeout_s", 3600))

    def _watchdog():
        import subprocess as sp
        t0 = time.time()
        while True:
            try:
                if not force:
                    r = sp.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
                    if exe_name.encode() in (r.stdout or b"").lower():
                        lg.info("Обнаружен GUI v2rayN — мой runtime-xray завершается (освобождаю порт)")
                        try:
                            p.terminate()
                        except Exception:
                            pass
                        return
                else:
                    # force: GUI запущен, но его ядро мертво. Уступаем порт ТОЛЬКО
                    # если GUI поднял СВОЁ ядро (появился чужой процесс xray.exe).
                    r = sp.run(["tasklist", "/FI", "IMAGENAME eq xray.exe", "/FO", "CSV", "/NH"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
                    out = (r.stdout or b"").decode("utf-8", "replace")
                    foreign = [ln for ln in out.splitlines()
                               if "xray.exe" in ln.lower() and str(p.pid) not in ln]
                    if foreign:
                        lg.info("GUI поднял своё ядро — мой runtime-xray уступает порт")
                        try:
                            p.terminate()
                        except Exception:
                            pass
                        return
                if time.time() - t0 > max_idle_sec:
                    lg.info(f"GUI не появился за {max_idle_sec}с — runtime-xray самоуничтожается")
                    try:
                        p.terminate()
                    except Exception:
                        pass
                    return
            except Exception:
                pass
            time.sleep(5)

    _th.Thread(target=_watchdog, daemon=True).start()

    deadline = time.time() + 15
    port = client.get("socks_port", 10808)
    while time.time() < deadline:
        if p.poll() is not None:
            tail = open(log_path, encoding="utf-8", errors="replace").read()[-500:]
            lg.error("xray упал при старте; лог: " + tail)
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except Exception:
            time.sleep(0.4)
    lg.error("xray не открыл порт")
    return False


def wait_and_probe(client: dict, attempts: int = 6, gap: float = 3.0) -> tuple:
    port = client.get("socks_port", 10808)
    for _ in range(attempts):
        ok, rtt = probe_client(client)
        if ok:
            return True, rtt
        time.sleep(gap)
    return False, 0.0


class StateStore:
    def __init__(self, cfg: dict):
        self.path = os.path.join(cfg["paths"]["state_dir"], "state.json")
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def record_last_good(self, client: dict, backup_dir: str, active_id: str):
        """Копирует проверенно-рабочие db/cfg в last_good."""
        gdir = os.path.join(backup_dir, "last_good")
        os.makedirs(gdir, exist_ok=True)
        db_dst = os.path.join(gdir, "guiNDB.db")
        cfg_dst = os.path.join(gdir, "guiNConfig.json")
        try:
            shutil.copy2(client["db"], db_dst)
            shutil.copy2(client["cfg"], cfg_dst)
        except Exception:
            return False
        self.data["last_good"] = {"db": db_dst, "cfg": cfg_dst, "active_id": active_id}
        self.data["last_good_ts"] = time.time()
        self.save()
        return True


def update_hotcache(client: dict, cfg: dict, servers: list):
    """Сохраняет проверенно-живые серверы в hotcache (state.json).

    Вызывается после успешного теста/цикла: кэш топ-N серверов с rtt/speed,
    чтобы при обрыве страж мог переключиться БЕЗ полного теста (быстро).

    servers: список объектов Server с ok=True, rtt_ms, speed_kbps.
    """
    st = StateStore(cfg)
    hot_n = int(cfg.get("watch", {}).get("hotcache_n", 5))
    if not servers or hot_n <= 0:
        return
    # Только серверы с ПОДТВЕРЖДЁННЫМ трафиком: hotcache — быстрый путь
    # восстановления, и заглушка в нём означает мгновенный обрыв снова
    # (в реальном hotcache было 14 из 18 записей со speed=0).
    alive = [s for s in servers
             if getattr(s, "ok", False) and getattr(s, "rtt_ms", 0) > 0
             and (getattr(s, "speed_kbps", 0) or 0) > 0]
    alive.sort(key=lambda s: (-(s.speed_kbps or 0), s.rtt_ms))
    cache = []
    for s in alive[:hot_n]:
        entry = {
            "address": s.address, "port": s.port, "protocol": s.protocol,
            "remark": getattr(s, "remark", "") or "",
            "rtt_ms": s.rtt_ms, "speed_kbps": s.speed_kbps,
            "ts": time.time(),
        }
        # пометка [AUTO] — сервер уже в БД с этим IndexId
        idx = getattr(s, "_row_index_id", None) or getattr(s, "_index_id_in_db", None)
        if idx is None:
            # создаём identity как index_id — на случай записи
            idx = getattr(s, "index_id", "")
        entry["index_id"] = idx
        cache.append(entry)
    st.data["hotcache"] = cache
    st.save()
    lg = log.get_logger()
    lg.info(f"hotcache обновлён: {len(cache)} серверов")


def hotcache_switch(client: dict, cfg: dict, hot_n: int = 5,
                    hot_ttl: float = 600.0, probe_timeout: float = 5.0) -> str:
    """Быстрое переключение на сервер из hotcache (без полного теста).

    Берёт свежие (ts < now - ttl) серверы из кэша, для каждого делает
    один быстрый probe (TCP + 204 через временный xray) и пробует
    тихо переключить (switch_quiet). Возвращает remark успешного
    сервера, или "" если никто не подошёл.
    """
    import json as _json
    import time as _t
    lg = log.get_logger()
    st = StateStore(cfg)
    cache = st.data.get("hotcache", [])
    if not cache:
        return ""
    fresh = [c for c in cache if _t.time() - c.get("ts", 0) <= hot_ttl]
    if not fresh:
        lg.warn(f"hotcache: все записи старше {hot_ttl:.0f}с — пропускаю")
        return ""
    fresh.sort(key=lambda c: c.get("rtt_ms", 9999))
    # исключаем серверы из чёрного списка (те, что уже подвели)
    try:
        bl = bl_active(cfg)
        if bl:
            fresh = [c for c in fresh
                     if str(c.get("index_id", "")) not in bl]
    except Exception:
        pass
    if not fresh:
        lg.warn("hotcache: всё в чёрном списке — пропускаю быстрый путь")
        return ""
    lg.info(f"hotcache: пробую {len(fresh)} серверов из кэша...")

    import xraytest as XT
    pool = XT.PortPool(int(cfg["test"].get("port_start", 10810)) + 40,
                       int(cfg["test"].get("workers", 20)) + 2)
    for c in fresh:
        # пытаемся собрать Server из строки БД (address+port / index_id)
        try:
            row = None
            for r in read_db_rows(client["db"]):
                if str(r.get("IndexId")) == str(c.get("index_id", "")):
                    row = r
                    break
            if row is None:
                for r in read_db_rows(client["db"]):
                    if str(r.get("Address")) == str(c["address"]) \
                       and str(r.get("Port")) == str(c["port"]):
                        row = r
                        break
            if row is None:
                lg.warn(f"hotcache: сервер {c['address']} не найден в БД — пропуск")
                continue
            s = row_to_server(row)
            if s is None:
                continue
            s._row_index_id = str(row.get("IndexId"))
        except Exception as e:
            lg.warn(f"hotcache: parse ошибка: {e}")
            continue
        # быстрый probe сервера
        XT.tcp_prefilter([s], cfg)
        if not s.ok:
            continue
        XT.test_rtt(pool, client["core"], s, int(probe_timeout))
        if not s.ok:
            continue
        # переключаем тихо
        new_id = getattr(s, "_row_index_id", None) or c.get("index_id", "")
        if switch_quiet(client, s, cfg, write_first=True, new_active=new_id):
            ok2, rtt2 = probe_client(client, timeout=probe_timeout)
            if ok2:
                lg.info(f"hotcache_switch OK: {c['address']} (rtt={rtt2} мс)")
                return f"{c['protocol']} {c['address']}:{c['port']}"
            lg.warn("hotcache_switch: порт поднялся, но связь не прошла")
    return ""


def _gui_alive(client: dict) -> bool:
    """GUI v2rayN запущен?"""
    import subprocess as sp
    try:
        name = os.path.basename(client.get("exe", "v2rayN.exe"))
        r = sp.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                   capture_output=True, timeout=15,
                   creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
        return name.lower().encode() in (r.stdout or b"").lower()
    except Exception:
        return False


def switch_quiet(client: dict, server, cfg: dict,
                 write_first: bool = True, new_active: str = None) -> bool:
    """ТИХОЕ переключение активного сервера БЕЗ падения связи.

    Последовательность (разрыв минимален):
    1) (опционально) записывает new_active (IndexId нового сервера) в guiNConfig
    2) поднимает СВОЁ ядро xray на новом сервере на порту 10901 и проверяет
       связь через него
    3) плавно: закрывает ядро на 10808 и поднимает своё на новом сервере
    Возвращает True, если порт 10808 стал рабочим с новым сервером.
    """
    import json, time as _t
    lg = log.get_logger()
    port = client.get("socks_port", 10808)

    # Если GUI запущен — он владеет портом; своё ядро НЕ поднимаем.
    # Записываем IndexId (GUI применит после Reload) и проверяем порт.
    if _proc_exists(client.get("exe", "")):
        if write_first and new_active:
            try:
                with open(client["cfg"], encoding="utf-8") as f:
                    d = json.load(f)
                d["IndexId"] = new_active
                with open(client["cfg"], "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                lg.info(f"switch_quiet: GUI жив — только IndexId записан: {new_active}")
            except Exception as e:
                lg.error(f"switch_quiet: запись IndexId: {e}")
        ok, _ = probe_client(client, timeout=6)
        if ok:
            return True
        # Ядро GUI висит (порт слушается, но трафик не идёт / мёртвый сервер).
        # НЕ воюем за порт: пишем сервер в конфиг GUI и снимаем ядро —
        # v2rayN сам поднимет НУЖНЫЙ сервер (иначе была «борьба» ядер).
        lg.warn("switch_quiet: порт мёртв — синхронизирую с GUI")
        if gui_restart_ui(client, cfg, server=server, active_id=new_active,
                          wait_s=45):
            return True
        if gui_restart_kernel(client, server, cfg, wait_s=25):
            return True
        lg.warn("switch_quiet: через GUI не вышло — пробую своё ядро")
        if start_xray_direct(client, server, cfg, force=True):
            ok2, rtt2 = probe_client(client, timeout=8)
            if ok2:
                lg.info(f"switch_quiet: своё ядро поднято, связь есть (rtt={rtt2} мс)")
                return True
        return False

    # 1) Записать новый активный IndexId
    if write_first and new_active:
        try:
            with open(client["cfg"], encoding="utf-8") as f:
                d = json.load(f)
            d["IndexId"] = new_active
            with open(client["cfg"], "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            lg.info(f"switch_quiet: IndexId записан: {new_active}")
        except Exception as e:
            lg.error(f"switch_quiet: запись IndexId: {e}")

    # 2) Поднять своё ядро на 10901 (предварительно, проверка)
    import xraytest as XT
    tmp_port = 10901
    ob = XT.outbound_json(server)
    if ob is None:
        lg.error("switch_quiet: outbound не собрался")
        return False
    cfg_x = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": "in", "port": tmp_port, "listen": "0.0.0.0",
                      "protocol": "mixed", "settings": {"udp": True, "auth": "noauth"}}],
        "outbounds": [ob],
    }
    import os as _os
    conf_path = _os.path.join(cfg["paths"]["state_dir"], "xray_switch.json")
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump(cfg_x, f)
    core = client.get("core")
    cwd = _os.path.dirname(core)
    log_path = _os.path.join(cfg["paths"]["state_dir"], "xray_switch.log")
    try:
        import subprocess as sp
        env_b = dict(os.environ)
        adir = _asset_dir(core, cfg)
        env_b["XRAY_LOCATION_ASSET"] = adir
        p = subprocess.Popen([core, "run", "-c", conf_path], cwd=cwd, env=env_b,
                             stdout=open(log_path, "w", encoding="utf-8"),
                             stderr=subprocess.STDOUT,
                             creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        lg.error(f"switch_quiet: Popen: {e}")
        return False
    # ждать порт 10901
    import socket
    deadline = _t.time() + 10
    while _t.time() < deadline:
        if p.poll() is not None:
            lg.error("switch_quiet: ядро упало; " +
                     open(log_path, encoding="utf-8", errors="replace").read()[-300:])
            return False
        try:
            with socket.create_connection(("127.0.0.1", tmp_port), timeout=0.3):
                break
        except Exception:
            _t.sleep(0.2)
    else:
        lg.error("switch_quiet: порт 10901 не открылся")
        return False

    # 3) СТРОГАЯ проверка через 10901: 204 + реальные данные + валидный TLS.
    #    Обычный 204 не годится: серверы-«заглушки» его отдают, а трафик не идёт.
    if not port_works(tmp_port, timeout=8):
        lg.error("switch_quiet: сервер НЕ прошёл строгую проверку "
                 "(нет данных/подменён TLS) — пропускаю")
        p.terminate()
        try:
            bl_add(cfg, getattr(server, "_row_index_id", None) or
                   getattr(server, "index_id", ""), 60, "fail_strict_probe")
        except Exception:
            pass
        return False
    lg.info("switch_quiet: новый сервер на 10901 прошёл строгую проверку")

    # 4) Теперь плавно перенаправляем: останавливаем GUI-ядро на 10808 и
    #    поднимаем наше на 10808. Для минимального разрыва — максимально быстро.
    #    Останавливаем GUI-ядро (только xray, GUI-процесс не трогаем).
    pid = _port_pid(port)
    if pid:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                       timeout=10, creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
    # 5) поднять наше ядро на 10808 (занимаем порт сразу)
    cfg_x["inbounds"][0]["port"] = port
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump(cfg_x, f)
    # перезапустить наш процесс с новым портом: убьём 10901 и поднимем на 10808
    p.terminate()
    _t.sleep(0.3)
    try:
        p2 = subprocess.Popen([core, "run", "-c", conf_path], cwd=cwd,
                              stdout=open(log_path, "w", encoding="utf-8"),
                              stderr=subprocess.STDOUT,
                              creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        lg.error(f"switch_quiet: Popen на 10808: {e}")
        return False
    # ждать порт 10808
    deadline = _t.time() + 10
    while _t.time() < deadline:
        if p2.poll() is not None:
            lg.error("switch_quiet: ядро на 10808 упало")
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except Exception:
            _t.sleep(0.2)
    else:
        lg.error("switch_quiet: 10808 не открылся")
        return False
    lg.info(f"switch_quiet: порт {port} подхвачен ядром (новый сервер)")
    return True


def gui_reload(client: dict, timeout: int = 20) -> bool:
    """Программно нажимает 'Reload' в окне v2rayN (UI Automation).
    Заставляет GUI перечитать активный сервер из guiNConfig.json и плавно
    переключить ядро (без падения порта, без мигания окна).
    Возвращает True, если Reload нажат (порт после этого отвечает).
    """
    import subprocess as sp
    lg = log.get_logger()
    ps = (
        "Add-Type -AssemblyName UIAutomationClient; "
        "Add-Type -AssemblyName UIAutomationTypes; "
        "$p = Get-Process -Name v2rayN -ErrorAction SilentlyContinue; "
        "if (-not $p) { Write-Output 'NO_GUI'; exit; } "
        "$root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle); "
        "$cond = [System.Windows.Automation.Condition]::TrueCondition; "
        "$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond); "
        "for ($i=0; $i -lt $all.Count; $i++) { "
        "  $el = $all.Item($i); "
        "  if ($el.Current.Name -eq 'Reload') { "
        "    try { "
        "      $inv = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern); "
        "      $inv.Invoke(); Write-Output 'RELOAD_OK'; exit; "
        "    } catch { "
        "      try { $el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern) | Out-Null; Write-Output 'FOUND_NO_INVOKE'; exit; } catch {} "
        "    } "
        "  } "
        "} "
        "Write-Output 'RELOAD_NOT_FOUND'"
    )
    try:
        r = sp.run(["powershell.exe", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=timeout,
                   creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        lg.info(f"gui_reload: {out}")
        if "RELOAD_OK" in out:
            # дать GUI время переключить ядро, затем проверить порт
            import time as _t
            ok, rtt = probe_client(client)
            if not ok:
                _t.sleep(4)
                ok, rtt = probe_client(client)
            return ok
        return False
    except Exception as e:
        lg.error(f"gui_reload: {e}")
        return False


def activate(client: dict, servers: list, active_id: str, cfg: dict,
             active_server=None, update_stats: bool = True, existing: list = None,
             restart_client: bool = True, force_pool: bool = False) -> dict:
    """Полный цикл активации с бэкапами и откатом.

    Сначала пишет [AUTO]-группу, затем активирует active_id.
    restart_client=False: GUI пользователя НЕ останавливаем — только запись
    БД + IndexId (применится после Reload/перезапуска v2rayN).
    restart_client=True: ядро (xray) запускается НАПРЯМУЮ (без GUI, который
    не стартует из неинтерактивной сессии).
    Возвращает {"ok":, "switched":, "rtt":, "msg":}.
    """
    lg = log.get_logger()
    backup_dir = cfg["paths"]["backup_dir"]
    backups = backup_files(client, backup_dir)
    switched = read_active_id(client["cfg"]) != active_id

    # ГАРАНТИЯ: никогда не убивать GUI, если он реально запущен.
    # Даже если вызывающий просил restart_client=True — переопределяем на SOFT.
    if restart_client:
        exe_name = os.path.basename(client.get("exe", "v2rayN.exe"))
        try:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if exe_name.lower().encode() in (r.stdout or b"").lower():
                lg.info("Обнаружен GUI v2rayN — принудительно МЯГКАЯ активация (GUI не трогаю)")
                restart_client = False
        except Exception:
            pass

    # РЕЖИМ БАЛАНСИРОВЩИКА: не «активируем один сервер» (это перезапускало бы
    # ядро и рвало связь), а записываем БД и обновляем пул балансировщика.
    if bool(cfg.get("watch", {}).get("balancer_mode", False)):
        try:
            # БД переписываем только при смене состава (или раз в 15 мин):
            # лишний дисковый I/O и обновление статистики ни на что не влияют,
            # пока пул заморожен.
            db_sig = ",".join(sorted(f"{s.address}:{s.port}" for s in servers))
            st_db = StateStore(cfg)
            prev_sig = st_db.data.get("db_written_sig") or ""
            prev_ts = float(st_db.data.get("db_written_ts", 0) or 0)
            if db_sig and db_sig == prev_sig and (time.time() - prev_ts) < 900:
                lg.info("БД не переписываю: состав серверов не изменился")
            else:
                n = write_auto_group(client["db"], servers, update_stats)
                lg.info(f"Записано в БД: {n} серверов [AUTO] (режим балансировщика)")
                st_db.data["db_written_sig"] = db_sig
                st_db.data["db_written_ts"] = time.time()
                st_db.save()
            if active_id:
                write_active_id(client["cfg"], active_id)
        except Exception as e:
            lg.error(f"Запись БД не удалась: {e}")
            return {"ok": False, "switched": switched, "rtt": 0,
                    "msg": f"write_error: {e}"}
        ok_b = False
        try:
            ok_b = ensure_balancer(client, cfg,
                                   n=int(cfg["watch"].get("balancer_pool_n", 10)),
                                   force=False)
        except Exception as e:
            lg.error(f"ensure_balancer (activate): {e}")
        if ok_b:
            # Проверка ПОСЛЕ СТАРТА — быстрая и с повторами. Строгая (скачать
            # 200 КБ) сразу после старта ложно падала: балансировщику нужно
            # несколько секунд, чтобы leastPing выбрал живой узел. Итог был
            # тяжёлым: «не дал связи» -> бан ВСЕГО пула -> ядро поднималось
            # ВТОРОЙ раз. Это давало 134 лишних перезапуска ядра в сутки.
            ok2, rtt2 = False, 0
            for _try in range(3):
                ok2, rtt2 = probe_client(client, timeout=8, strict=False,
                                         alt=bool(_try))
                if ok2:
                    break
                time.sleep(0.5)
            if not ok2:
                # быстрая не прошла трижды — подтверждаем данными (2 попытки)
                for _try in range(2):
                    ok2, rtt2 = probe_client(client, timeout=10, strict=True,
                                             alt=bool(_try))
                    if ok2:
                        break
                    time.sleep(1.0)
            if ok2:
                lg.info(f"Балансировщик обновлён, связь есть (rtt={rtt2} мс)")
                return {"ok": True, "switched": switched, "rtt": rtt2,
                        "msg": "balancer_ok"}
        # Пул поднялся, но проверка не прошла НИ разу (ни быстрая, ни с
        # данными, все с повторами): негодны ВСЕ участники (заглушки отдают
        # 204 и молчат на данных) — баним состав целиком, иначе они вернутся
        # в пул на следующем цикле.
        try:
            bl_add_pool(cfg, balancer_pool(client, cfg,
                                           n=int(cfg["watch"].get("balancer_pool_n", 10))),
                        minutes=20, why="stub_pool")
        except Exception as e:
            lg.warn(f"bl_add_pool: {e}")
        lg.error("Балансировщик не дал связи — оставляю как есть")
        return {"ok": False, "switched": switched, "rtt": 0,
                "msg": "balancer_failed"}

    # прежний сервер для отката (из текущей БД до записи)
    old_server = None
    old_active = read_active_id(client["cfg"])
    for r in read_db_rows(client["db"]):
        if str(r.get("IndexId")) == str(old_active):
            old_server = row_to_server(r)
            break

    # НИКОГДА не останавливаем GUI/ядро. Сначала убираем своего сироту
    # с порта, если GUI появился. Пишем БД сразу.
    ensure_port_free(client, cfg)
    try:
        n = write_auto_group(client["db"], servers, update_stats)
        lg.info(f"Записано в БД: {n} серверов [AUTO], активный: {active_id}")
        write_active_id(client["cfg"], active_id)
    except Exception as e:
        lg.error(f"Запись БД/конфига не удалась: {e}")
        return {"ok": False, "switched": switched, "rtt": 0, "msg": f"write_error: {e}"}

    # Мягкая проверка: если порт жив — отлично. Если сервер сменился:
    # используем ТИХОЕ переключение (своё ядро на 10808) — без ручного Reload.
    ok, rtt = probe_client(client)
    if ok:
        if switched:
            lg.info("Сервер сменился — тихое переключение через своё ядро")
            ok_sw = switch_quiet(client, active_server, cfg,
                                 write_first=True, new_active=active_id)
            if ok_sw:
                # синхронизация GUI: чтобы в списке v2rayN был тот же сервер,
                # что реально обслуживает трафик (и ядро слушало 0.0.0.0/LAN)
                try:
                    gui_restart_ui(client, cfg, server=active_server,
                                   active_id=active_id, wait_s=45)
                except Exception as e:
                    lg.warn(f"gui_restart_ui (activate): {e}")
                return {"ok": True, "switched": True, "rtt": rtt,
                        "msg": "quiet_switch_ok"}
            lg.warn("Тихое переключение не удалось — активный записан в файл")
        who = "GUI/ядро"
        lg.info(f"Активация (мягкая) OK: {active_id} (rtt={rtt} мс); "
                f"{who} жив, активный записан")
        return {"ok": True, "switched": switched, "rtt": rtt, "msg": "soft_ok_no_restart"}

    # Порт НЕ слушается (нет связи: ни GUI, ни ядро).
    lg.warn("Порт прокси не отвечает — восстанавливаю")
    if active_server is not None:
        # если GUI жив — пусть он поднимет ядро с нужным сервером
        if _proc_exists(client.get("exe", "")):
            if gui_restart_kernel(client, active_server, cfg, wait_s=25):
                ok2, rtt2 = probe_client(client, timeout=8)
                if ok2:
                    lg.info(f"Активация через GUI-перезапуск OK: {active_id} "
                            f"(rtt={rtt2} мс)")
                    return {"ok": True, "switched": switched, "rtt": rtt2,
                            "msg": "ok_gui_restart"}
        if start_xray_direct(client, active_server, cfg, force=True):
            ok2, rtt2 = probe_client(client)
            if ok2:
                lg.info(f"Активация через прямое ядро OK: {active_id} (rtt={rtt2} мс)")
                return {"ok": True, "switched": switched, "rtt": rtt2,
                        "msg": "ok_xray_direct"}
            lg.error("Ядро поднято, но порт всё равно не отвечает")
        else:
            lg.error("Поднять ядро напрямую не удалось; оставляю конфиг записанным, "
                     "запусти v2rayN вручную — он подхватит [AUTO]")
    return {"ok": False, "switched": switched, "rtt": 0,
            "msg": "no_connect_after_write"}


def _rollback(client: dict, backups: dict, cfg: dict = None,
              fallback_server=None) -> bool:
    lg = log.get_logger()
    for key, src in backups.items():
        dst = client[key]
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            lg.error(f"Откат {key}: {e}")
            return False
    # 1) Если порт уже жив (сирота/живой процесс) — связь есть, не трогаем
    ok, _ = probe_client(client)
    if ok:
        lg.info("Откат: связь уже работает (порт жив) — процессы не трогаю")
        return True
    # 2) иначе поднимаем xray напрямую на прежнем сервере
    if fallback_server is not None and cfg is not None:
        lg.info("Откат: поднимаю xray напрямую на прежнем сервере...")
        if start_xray_direct(client, fallback_server, cfg):
            return True
        lg.error("Откат: xray на прежнем сервере не поднялся")
        return False
    if stop_client(client):
        start_client(client)
    return True


def _proc_exists(exe_path: str) -> bool:
    """Есть ли процесс по имени exe (безопасно, без окна)."""
    import subprocess as _sp
    try:
        name = os.path.basename(exe_path or "v2rayN.exe")
        r = _sp.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                    capture_output=True, timeout=15,
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        return name.lower().encode() in (r.stdout or b"").lower()
    except Exception:
        return False


def guard_cycle(client: dict, cfg: dict, probe_timeout: float = 6.0):
    """Быстрый локальный самоконтроль: порт жив? иначе failover.

    Failover: берём [AUTO]-серверы из БД (проверенные скриптом), тестируем
    (RTT), выбираем лучший живой по (rtt, speed) и переключаемся на него.
    Если ни один [AUTO] не жив — пробуем last_good, затем start_client.
    """
    lg = log.get_logger()
    ok, rtt = probe_client(client, timeout=probe_timeout)
    if ok:
        return True
    st = StateStore(cfg)
    lg.warn(f"Порт {client.get('socks_port', 10808)} не отвечает ({rtt} мс); "
            f"запускаю failover...")
    # Если GUI запущен — порт не на без нас: он сам управляет ядром.
    # Не поднимаем прямой xray, только пробуем Reload в GUI.
    ensure_port_free(client, cfg)
    if _proc_exists(client.get("exe", "")):
        lg.warn("Guard: GUI запущен, порт мёртв — жму Reload в v2rayN")
        if gui_reload(client, timeout=25):
            ok_g, rtt_g = probe_client(client, timeout=8)
            if ok_g:
                lg.info(f"Guard OK: Reload в GUI восстановил связь (rtt={rtt_g} мс)")
                return True
        lg.warn("Guard: Reload не помог — снимаю мёртвое ядро GUI и беру failover")
        # НЕ выходим: ядро GUI держится за мёртвый сервер. Идём обычной логикой —
        # перебор [AUTO]-серверов и подъём СВОЕГО ядра (force) на лучшем.

    # --- 1. кандидаты: проверенные [AUTO]-серверы из БД ---
    rows = read_db_rows(client["db"])
    cands = []
    for r in rows:
        if not (r.get("Remarks") or "").startswith(AUTO_TAG):
            continue
        s = row_to_server(r)
        if s is not None:
            s._row_index_id = str(r["IndexId"])
            cands.append(s)
    lg.info(f"Failover: [AUTO]-кандидатов в БД: {len(cands)}")
    # не возвращаемся на серверы из чёрного списка (те, что уже подвели)
    try:
        before = len(cands)
        cands = bl_filter(cfg, cands)
        if before != len(cands):
            lg.warn(f"Failover: исключено чёрным списком: {before - len(cands)}")
    except Exception:
        pass

    if cands:
        import xraytest as XT
        pool = XT.PortPool(int(cfg["test"].get("port_start", 10810)),
                           int(cfg["test"].get("workers", 20)) * 2 + 4)
        # быстрый RTT всех кандидатов (каждый — отдельный xray, 6 сек)
        cands = XT.tcp_prefilter(cands, cfg)  # возвращает вЫживших, выставляет доступ
        for s in cands:
            XT.test_rtt(pool, client["core"], s, 6)
        alive = [s for s in cands if s.ok]
        alive.sort(key=lambda s: (s.rtt_ms, -s.speed_kbps if s.speed_kbps else 0))
        lg.info(f"Failover: живых [AUTO]: {len(alive)}")
        for s in alive[:8]:
            # пробуем тихо переключить порт на лучшего
            # ВАЖНО: передаём new_active — иначе guiNConfig.IndexId не обновится
            # и GUI будет показывать другой сервер (рассинхрон с треем).
            sid = getattr(s, "_row_index_id", None) or getattr(s, "index_id", None)
            if switch_quiet(client, s, cfg, write_first=True, new_active=sid):
                ok2, rtt2 = probe_client(client, timeout=8)
                if ok2:
                    lg.info(f"Failover OK: переключились на {s.summary()[:45]} "
                            f"(rtt={rtt2} мс)")
                    return True
                lg.warn("Failover: порт поднялся, но связь не прошла — "
                        "сервер в чёрный список")
                try:
                    bl_add(cfg, getattr(s, "_row_index_id", None) or s.index_id,
                           60, "failover_no_connect")
                except Exception:
                    pass
    else:
        lg.warn("Failover: нет [AUTO]-серверов в БД (или все без теста)")

    # --- 2. fallback: last_good ---
    lg.info("Failover: пробую last-good...")
    lg_ = st.data.get("last_good")
    restored = False
    if lg_:
        for key in ("db", "cfg"):
            src = lg_.get(key)
            if src and os.path.isfile(src):
                try:
                    shutil.copy2(src, client[key])
                    restored = True
                except Exception as e:
                    lg.error(f"guard restore {key}: {e}")
    if restored:
        lg.info("Восстановлен last-good конфиг")
    act = read_active_id(client["cfg"])
    srv = None
    rows = read_db_rows(client["db"])
    for r in rows:
        if str(r.get("IndexId")) == str(act):
            srv = row_to_server(r)
            break
    if srv is not None:
        if start_xray_direct(client, srv, cfg, force=True):
            lg.info("Guard OK: связь восстановлена (напрямую, без GUI)")
            return True
    if start_client(client):
        ok2, rtt2 = wait_and_probe(client, attempts=8)
        if ok2:
            lg.info(f"Guard OK: связь восстановлена (rtt={rtt2} мс)")
            return True
    lg.error("Guard: восстановить связь не удалось")
    return False