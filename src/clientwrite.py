# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Ядро клиентской логики: standalone-режим (своё ядро xray) + режим v2rayN.

FOREST's Proxy Auto-updater (GPL-3.0).

Этот модуль — ТОЧКА ВХОДА для остальных модулей (``import clientwrite as CW``).
Он содержит:

  * переносимую сердцевину (проверки связи/скорости, чёрный список, стор
    состояния, hotcache, генерация конфигов ядра, блокировки, валидация,
    системный прокси через реестр WinINET);
  * **standalone-бэкенд** для Windows: своё ядро ``xray.exe`` запускается/
    останавливается как дочерний процесс, свой файл состояния серверов
    (``state/servers.json``), свой конфиг ядра (``state/forest_xray.json``),
    системный прокси ставится/снимается через HKCU Internet Settings;
  * диспетчер режимов: при ``client.mode == "v2rayn"`` вызовы уходят в
    ``clientwrite_v2rayn`` (работа через базу/ядро установленного v2rayN),
    при ``standalone`` — в реализацию этого модуля.

Логика standalone перенесена из POSIX-версии (``clientwrite_posix.py``)
исходного проекта: там ядро было systemd-сервисом, здесь — обычным процессом
``xray.exe`` (systemctl заменён на запуск/остановку процесса).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import log

CONFIG_TYPE = {"vless": 5, "vmess": 1, "trojan": 6, "ss": 3}
AUTO_TAG = "[AUTO] "
PROBE_URL = "https://www.gstatic.com/generate_204"
PROBE_URL_ALT = "https://cp.cloudflare.com/generate_204"
YOUTUBE_URL = "https://www.youtube.com/"
# Зонд ЗДОРОВЬЯ узлов для балансировщика (observatory). НЕ gstatic: часть узлов
# (типично российские выходы) спокойно отдаёт google/gstatic 204, но рвёт TLS к
# youtube.com — leastPing выбирал именно их, и YouTube падал мгновенно (rc=35,
# ~40 мс). Эндпоинт youtube/generate_204 отдаёт 204 на здоровом узле и рвётся на
# «глушителе» YouTube, поэтому балансировщик такие узлы не берёт. Любой ответ
# (даже 404) считается живым — только транспортная ошибка/TLS-сброс делают узел
# мёртвым, так что вырождение эндпоинта не ломает связь.
PROBE_URL_YT = "https://www.youtube.com/generate_204"
PROBE_DATA_URL = "https://speed.cloudflare.com/__down?bytes=200000"
PROBE_DATA_MIN_BYTES = 50000

DEFAULT_SOCKS_PORT = 10808
DEFAULT_TORRENT_PORT = 10820

# Имя конфига НАШЕГО ядра — уникальное, чтобы kill_kernels снимал только свои
# процессы и не задевал ядро v2rayN (xray_balancer.json / xray_runtime.json).
CORE_CFG_NAME = "forest_xray.json"
CORE_PID_NAME = "core.pid"
CORE_LOG_NAME = "forest_xray.log"

RU_DIRECT_DOMAINS = ["geosite:category-ru", "geosite:tld-ru",
                     "geosite:category-gov-ru", "geosite:category-bank-ru",
                     # ВНИМАНИЕ: использовать только категории, реально присутствующие во вшитом
                     # geosite.dat. Проверено ядром: category-gov-ru / category-bank-ru / tld-ru /
                     # category-ru / private — есть; category-medicine-ru — ОТСУТСТВУЕТ, из-за неё
                     # ядро отвергало весь конфиг (проверять новые категории через `xray run -test`).
                     "geosite:private"]
RU_DIRECT_IPS = ["geoip:ru", "geoip:private"]
LOCAL_DIRECT_DOMAINS = ["full:retracker.local", "domain:local"]

# «РФ — напрямую»: список выше проверяется по реальному содержимому geosite.dat
# (см. ru_direct_domains()). Одна несуществующая категория в этом списке валит
# ВЕСЬ конфиг ядра ("failed to build routing configuration > invalid field rule
# > failed to parse domain"), и пул не применяется вообще — поэтому неизвестные
# категории отбрасываются заранее, с предупреждением в лог. Кэш на процесс.
_RU_DOMAINS_EFFECTIVE = None


def _read_varint(buf: bytes, pos: int) -> tuple:
    """(значение, новая позиция) для protobuf-varint."""
    shift = 0
    val = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 63:
            break
    raise ValueError("bad varint")


def geosite_category_names(path: str) -> set:
    """Имена категорий внутри geosite.dat (минимальный разбор protobuf).

    Формат — обычный protobuf:
        GeoSiteList { repeated GeoSite entry = 1; }
        GeoSite     { string country_code = 1; repeated Domain domain = 2; }
    Полноценный парсер не нужен: идём по length-delimited полям верхнего уровня
    и читаем в каждом первую строку — это и есть имя категории.
    """
    names = set()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return names
    i, n = 0, len(data)
    while i < n:
        try:
            key, i = _read_varint(data, i)
        except Exception:
            break
        field, wire = key >> 3, key & 7
        if wire != 2:                      # не length-delimited — дальше не идём
            break
        try:
            ln, i = _read_varint(data, i)
        except Exception:
            break
        chunk = data[i:i + ln]
        i += ln
        if field == 1 and chunk:
            try:
                k2, p2 = _read_varint(chunk, 0)
                if (k2 >> 3) == 1 and (k2 & 7) == 2:
                    l2, p2 = _read_varint(chunk, p2)
                    names.add(chunk[p2:p2 + l2].decode("utf-8", "replace"))
            except Exception:
                pass
    return names


def _geosite_file_candidates() -> list:
    """Где искать geosite.dat, если каталог гео-баз не передан явно.

    Первым идёт ПОСТОЯННЫЙ каталог ядра (<каталог данных>/bin/xray): ядро и
    гео-базы копируются туда из бандла (config.ensure_core_installed), и оттуда
    же запускается ядро. Каталог распаковки onefile (_MEIPASS) — только
    последняя страховка: читать временный каталог незачем.
    """
    out = []
    env = os.environ.get("XRAY_LOCATION_ASSET")
    if env:
        out.append(os.path.join(env, "geosite.dat"))
    try:
        import config as CFG
        out.append(os.path.join(CFG.core_dir(), "geosite.dat"))
        for base in (getattr(CFG, "PROJECT_DIR", ""),
                     getattr(CFG, "BASE_DIR", "")):
            if base:
                out.append(os.path.join(base, "bin", "xray", "geosite.dat"))
    except Exception:
        pass
    return out


def ru_direct_domains(geo_dir: str = "") -> list:
    """Домены правила «РФ — напрямую», проверенные по вшитому geosite.dat.

    Возвращает только те категории, которые реально есть в файле: остальные
    отбрасываются с предупреждением. Если geosite.dat прочитать не удалось,
    список возвращается как есть (тогда конфиг отсечёт проверка ядром).
    """
    global _RU_DOMAINS_EFFECTIVE
    if _RU_DOMAINS_EFFECTIVE is not None:
        return _RU_DOMAINS_EFFECTIVE
    path = os.path.join(geo_dir, "geosite.dat") if geo_dir else ""
    if not path or not os.path.isfile(path):
        path = ""
        for cand in _geosite_file_candidates():
            if os.path.isfile(cand):
                path = cand
                break
    known = {n.upper() for n in geosite_category_names(path)} if path else set()
    if not known:
        _RU_DOMAINS_EFFECTIVE = list(RU_DIRECT_DOMAINS)
        return _RU_DOMAINS_EFFECTIVE
    out = []
    for d in RU_DIRECT_DOMAINS:
        cat = d.split(":", 1)[1] if isinstance(d, str) and d.startswith("geosite:") else ""
        # имена категорий в geosite.dat — в верхнем регистре, ядро сверяет их
        # без учёта регистра; сверяем так же
        if cat and cat.upper() not in known:
            log.get_logger().warn(
                "geosite: категории '%s' нет в %s — правило пропущено "
                "(с ней ядро отвергло бы весь конфиг)" % (cat, path))
            continue
        out.append(d)
    _RU_DOMAINS_EFFECTIVE = out
    return _RU_DOMAINS_EFFECTIVE


REMOVED_OUT_KEYS = {"lowinsecure", "allowinsecure"}


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ══════════════════════════════════════════ проверки связи/скорости ═══
def _curl_metrics(port: int, url: str, max_time: int) -> tuple:
    """(http_code, time_total, ssl_verify_result, size_download) через socks."""
    cmd = ["curl", "-s", "-o", os.devnull,
           "-w", "%{http_code} %{time_total} %{ssl_verify_result} %{size_download}",
           "--max-time", str(max_time), "-x", f"socks5h://127.0.0.1:{port}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=max_time + 8,
                           creationflags=_no_window())
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
                           creationflags=_no_window())
        out = r.stdout.decode("utf-8", "replace").strip().split()
        code = int(out[0]) if out and out[0].isdigit() else 0
        spd = float(out[1]) if len(out) > 1 and out[1].isdigit() else 0.0
        return code, spd
    except Exception:
        return 0, 0.0


def measure_speed_kbps(client: dict, bytes_n: int = 2000000, timeout: int = 15) -> float:
    """Замер скорости через порт клиента (КБ/с). 0 — если не удалось."""
    port = client.get("socks_port", DEFAULT_SOCKS_PORT)
    url = f"https://speed.cloudflare.com/__down?bytes={int(bytes_n)}"
    code, spd = _curl_speed(port, url, timeout)
    return round(spd / 1024.0, 1) if code == 200 else 0.0


def youtube_probe(port: int, timeout: int = 12) -> tuple:
    """(http_code, rtt_ms) по YouTube через порт. 403/0 = узел не пускает."""
    code, tt, _sslv, _sz = _curl_metrics(port, YOUTUBE_URL, timeout)
    return code, round(tt * 1000, 1)


def port_works(port: int, timeout: int = 8) -> bool:
    """Строгая проверка: через порт идут РЕАЛЬНЫЕ данные и TLS не подменён."""
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

    strict=True: мало получить 204 — проверяем, что данные реально идут и TLS
    не подменён (иначе сервер-«заглушка» проходит как рабочий).
    alt=True — проверять второй эндпоинт (Cloudflare): для повторных попыток.
    """
    port = client.get("socks_port", DEFAULT_SOCKS_PORT)
    code, tt, sslv, _ = _curl_metrics(port, PROBE_URL_ALT if alt else PROBE_URL, timeout)
    if code != 204 or sslv != 0:
        return False, round(tt * 1000, 1)
    rtt = round(tt * 1000, 1)
    if not strict:
        return True, rtt
    if not port_works(port, timeout):
        return False, rtt
    return True, rtt


def port_listening(port: int, timeout: float = 0.5) -> bool:
    """Порт слушается? Быстрая TCP-проверка (~1 мс), без curl."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def port_busy(port: int, timeout: float = 0.4) -> bool:
    """Алиас port_listening (совместимость с POSIX-версией)."""
    return port_listening(port, timeout)


# ══════════════════════════════════════════ чёрный список ═══
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
    """Бан на ВЕСЬ состав пула (заглушки отдают 204, но трафик не везут)."""
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


# ══════════════════════════════════════════ row <-> Server ═══
def _json_safe(v):
    if not v:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {}


def row_to_server(row: dict):
    """Строка стора/БД -> Server (для повторного теста существующих)."""
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
    # Delay — мс, Speed — МБ/с (так пишет server_to_row: speed_kbps / 1024)
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


def server_to_row(s) -> dict:
    """Server -> строка стора (обратная к row_to_server)."""
    proto = getattr(s, "protocol", "") or ""
    pe, te = {}, {}
    if proto == "ss":
        pe["SsMethod"] = getattr(s, "method", "") or ""
    elif proto == "vmess":
        pe["VmessSecurity"] = getattr(s, "method", "auto") or "auto"
        pe["AlterId"] = str(getattr(s, "alter_id", "0") or "0")
    pe["Flow"] = getattr(s, "flow", "") or ""
    net = getattr(s, "network", "raw") or "raw"
    if net == "raw":
        te["RawHeaderType"] = getattr(s, "header_type", "none") or "none"
        te["Host"] = getattr(s, "host", "") or ""
        te["Path"] = getattr(s, "path", "") or ""
    elif net in ("ws", "httpupgrade", "xhttp"):
        te["Host"] = getattr(s, "host", "") or ""
        te["Path"] = getattr(s, "path", "/") or "/"
    elif net == "kcp":
        te["KcpHeaderType"] = getattr(s, "header_type", "none") or "none"
        te["KcpSeed"] = getattr(s, "kcp_seed", "") or ""
    elif net == "grpc":
        te["GrpcAuthority"] = getattr(s, "host", "") or ""
        te["GrpcServiceName"] = getattr(s, "path", "") or ""
        te["GrpcMode"] = getattr(s, "header_type", "gun") or "gun"
    remark = getattr(s, "remark", "") or ""
    return {
        "IndexId": str(getattr(s, "_row_index_id", None) or getattr(s, "index_id", "") or ""),
        "Remarks": remark if remark.startswith(AUTO_TAG) else (AUTO_TAG + remark),
        "Address": getattr(s, "address", "") or "",
        "Port": int(getattr(s, "port", 0) or 0),
        "ConfigType": CONFIG_TYPE.get(proto, 5),
        "Password": getattr(s, "password", "") or "",
        "Network": net,
        "StreamSecurity": getattr(s, "stream_security", "") or "",
        "Sni": getattr(s, "sni", "") or "",
        "Alpn": getattr(s, "alpn", "") or "",
        "Fingerprint": getattr(s, "fingerprint", "") or "",
        "PublicKey": getattr(s, "public_key", "") or "",
        "ShortId": getattr(s, "short_id", "") or "",
        "SpiderX": getattr(s, "spider_x", "") or "",
        "AllowInsecure": "1" if getattr(s, "allow_insecure", False) else "",
        # Speed — МБ/с, Delay — мс: тот же формат, что читает row_to_server.
        "Speed": round(float(getattr(s, "speed_kbps", 0) or 0) / 1024.0, 2),
        "Delay": int(getattr(s, "rtt_ms", 0) or 0),
        "ProtoExtra": pe,
        "TransportExtra": te,
    }


# ══════════════════════════════════════════ стор серверов (JSON) ═══
def _store_load(path: str) -> dict:
    """Читает JSON-store серверов. Формат: {next_index, active_id, servers:[]}."""
    if not os.path.isfile(path):
        return {"next_index": 1, "active_id": "", "servers": []}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "servers" in d:
            return d
        if isinstance(d, list):
            return {"next_index": len(d) + 1, "active_id": "", "servers": d}
    except Exception:
        pass
    return {"next_index": 1, "active_id": "", "servers": []}


def _store_save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _sa_read_db_rows(db_path: str) -> list:
    return list(_store_load(db_path).get("servers") or [])


def _sa_write_auto_group(db_path: str, servers: list, update_stats: bool = True) -> int:
    """Пишет [AUTO]-серверы в стор, сохраняя серверы пользователя."""
    data = _store_load(db_path)
    existing = {str(r.get("IndexId")): r for r in (data.get("servers") or [])
                if not (r.get("Remarks") or "").startswith(AUTO_TAG)}
    rows = list(existing.values())
    nxt = int(data.get("next_index", 1))
    written = 0
    for s in servers:
        row = server_to_row(s)
        if not row.get("IndexId"):
            row["IndexId"] = str(nxt)
            nxt += 1
        rows.append(row)
        written += 1
    data["servers"] = rows
    data["next_index"] = nxt
    _store_save(db_path, data)
    return written


def _sa_read_active_id(cfg_path: str):
    store = os.path.join(os.path.dirname(cfg_path) or ".", "servers.json")
    for cand in (store,
                 os.path.normpath(os.path.join(os.path.dirname(cfg_path) or ".",
                                               "..", "state", "servers.json"))):
        if os.path.isfile(cand):
            return _store_load(cand).get("active_id") or None
    return None


def _sa_write_active_id(cfg_path: str, index_id: str) -> None:
    store = os.path.join(os.path.dirname(cfg_path) or ".", "servers.json")
    if not os.path.isfile(store):
        alt = os.path.normpath(os.path.join(os.path.dirname(cfg_path) or ".",
                                            "..", "state", "servers.json"))
        store = alt if os.path.isfile(alt) else store
    data = _store_load(store)
    data["active_id"] = str(index_id)
    _store_save(store, data)


# ══════════════════════════════════════════ генерация конфигов ядра ═══
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
    s = str(err or "")
    out = set()
    m = re.search(r'"?([A-Za-z_][A-Za-z0-9_]*)"?\s+has been removed', s, re.I)
    if m:
        out.add(m.group(1).lower())
    if not out:
        out = {x.lower() for x in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', s)}
    return out


def _build_routing(ru_direct: bool, strategy: dict = None) -> dict:
    """Правила: локальные имена и РФ — напрямую, остальное — через пул."""
    rules = [{"type": "field", "outboundTag": "direct",
              "domain": LOCAL_DIRECT_DOMAINS},
             {"type": "field", "inboundTag": ["torrent-in"],
              "balancerTag": "bal"}]
    if ru_direct:
        rules.append({"type": "field", "outboundTag": "direct",
                      "domain": ru_direct_domains()})
        rules.append({"type": "field", "outboundTag": "direct",
                      "ip": RU_DIRECT_IPS})
    rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "bal"})
    return {
        "domainStrategy": "IPIfNonMatch",
        "balancers": [{"tag": "bal", "selector": ["proxy-out"],
                       "strategy": strategy or {"type": "leastPing"}}],
        "rules": rules,
    }


def _build_dns(ru_direct: bool) -> dict:
    if not ru_direct:
        return {"servers": [{"address": "localhost", "domains": LOCAL_DIRECT_DOMAINS},
                            "1.1.1.1", "8.8.8.8"]}
    return {
        "servers": [
            {"address": "localhost", "domains": LOCAL_DIRECT_DOMAINS},
            {"address": "77.88.8.8", "domains": ru_direct_domains(),
             "expectIPs": RU_DIRECT_IPS},
            {"address": "77.88.8.1", "domains": ru_direct_domains(),
             "expectIPs": RU_DIRECT_IPS},
            "1.1.1.1", "8.8.8.8",
        ],
        "queryStrategy": "UseIPv4",
    }


def balancer_probe_url(cfg: dict) -> str:
    """Зонд здоровья балансировщика из настроек (по умолчанию — YouTube)."""
    try:
        u = str((cfg.get("watch") or {}).get("balancer_probe_url") or "").strip()
        return u or PROBE_URL_YT
    except Exception:
        return PROBE_URL_YT


def balancer_strategy(cfg: dict) -> dict:
    """Стратегия балансировщика из настроек.

    ``leastLoad`` + ``burstObservatory``: нода, начавшая тормозить/отваливаться,
    выпадает из ротации по числу неудач проб и по ``maxRTT`` — а не остаётся
    «живой» по одному удачному медленному зонду (как с ``leastPing``: нода с
    ответом пробы за 5 с считалась здоровой, и браузер на неё вис). Стратегия
    задаётся настройкой ``watch.balancer_strategy`` — откат на ``leastPing`` без
    пересборки.
    """
    try:
        w = cfg.get("watch") or {}
        s = str(w.get("balancer_strategy") or "leastLoad").strip()
        if s not in ("leastPing", "leastLoad", "random"):
            s = "leastLoad"
        st = {"type": s}
        if s == "leastLoad":
            settings = {}
            try:
                mrtt = float(w.get("balancer_max_rtt_ms", 1500) or 0)
            except Exception:
                mrtt = 1500.0
            if mrtt > 0:
                settings["maxRTT"] = f"{int(mrtt)}ms"
            try:
                settings["expected"] = max(1, int(w.get("balancer_expected", 2) or 2))
            except Exception:
                settings["expected"] = 2
            st["settings"] = settings
        return st
    except Exception:
        return {"type": "leastPing"}


def burst_observatory(cfg: dict, probe_url: str) -> dict:
    """burstObservatory: ЧАСТЫЕ пробы с КОРОТКИМ таймаутом — быстрый failover.

    Обычный observatory только «жив/мёртв»; burst измеряет задержку и число
    неудач на несколько проб, поэтому тормозящая нода уходит из ротации за
    секунды, а не через минуты.
    """
    try:
        w = cfg.get("watch") or {}
        tmo = str(w.get("balancer_burst_timeout_s", 3) or 3).rstrip("s") + "s"
        itv = str(w.get("balancer_burst_interval_s", 15) or 15).rstrip("s") + "s"
        try:
            samp = max(1, int(w.get("balancer_burst_sampling", 3) or 3))
        except Exception:
            samp = 3
    except Exception:
        tmo, itv, samp = "3s", "15s", 3
    return {
        "subjectSelector": ["proxy-out"],
        "pingConfig": {
            "destination": probe_url,
            "connectivity": probe_url,
            "interval": itv,
            "sampling": samp,
            "timeout": tmo,
        },
    }


def build_balancer_config(servers: list, port: int, listen: str = "127.0.0.1",
                          probe_url: str = None, ru_direct: bool = None,
                          torrent_port: int = DEFAULT_TORRENT_PORT,
                          strategy: dict = None, burst: dict = None) -> dict:
    """Конфиг ОДНОГО ядра с пулом серверов и нативным балансировщиком xray.

    xray сам следит за живостью узлов (observatory) и направляет новые
    соединения на живой/быстрый (leastPing) — переключение НЕ требует
    перезапуска ядра, поэтому связь не рвётся.
    """
    import xraytest as XT
    outs = []
    for i, s in enumerate(servers):
        # Пустой пароль/ID даёт невалидный outbound («invalid UUID»), который
        # роняет ВЕСЬ конфиг балансировщика. Такой сервер в пул не берём.
        if not (getattr(s, "password", "") or "").strip():
            continue
        ob = XT.outbound_json(s)
        if ob is None:
            continue
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
            "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        }, {
            "tag": "torrent-in", "port": int(torrent_port), "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": True, "auth": "noauth"},
        }],
        "outbounds": outs,
        "dns": _build_dns(ru_direct),
        "observatory": {
            "subjectSelector": ["proxy-out"],
            "probeUrl": probe_url or PROBE_URL_YT,
            "probeInterval": "5s",
            "enableConcurrency": True,
        },
        "burstObservatory": burst or {
            "subjectSelector": ["proxy-out"],
            "pingConfig": {"destination": probe_url or PROBE_URL_YT,
                           "connectivity": probe_url or PROBE_URL_YT,
                           "interval": "15s", "sampling": 3, "timeout": "3s"},
        },
        "routing": _build_routing(ru_direct, strategy=strategy),
    }


def _write_json(path: str, data: dict) -> bool:
    """Записать JSON АТОМАРНО (tmp + os.replace)."""
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.get_logger().error(f"_write_json({path}): {e}")
        return False


# ══════════════════════════════════════ проверка НОВОГО конфига до остановки ядра ═══
def _conf_sha(conf: dict) -> str:
    """Хэш содержимого конфига ядра (канонический JSON, порядок ключей не важен)."""
    try:
        return hashlib.sha1(
            json.dumps(conf, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    except Exception:
        return ""


def _cfg_sha(path: str) -> str:
    """Хэш конфига, лежащего на диске (то, с чем реально работает ядро)."""
    try:
        with open(path, encoding="utf-8") as f:
            return _conf_sha(json.load(f))
    except Exception:
        return ""


def _test_config_by_core(core: str, conf: dict, cfg: dict) -> tuple:
    """Проверка конфига ЯДРОМ через ``xray run -test -config <файл>``.

    Ядро только разбирает конфиг и проверяет его — порты НЕ занимаются,
    живое ядро не трогается, запускается лишь короткий процесс проверки.
    Возвращает (ok, err): ``ok=False`` — конфиг невалиден; второй элемент
    ``"unsupported"`` — ядро не понимает ключ ``-test`` (старая сборка),
    вызывающий обязан откатиться на проверку запуском на свободных портах.
    """
    if not core or not os.path.isfile(core):
        return False, "unsupported"
    # Уникальный временный файл: проверка может идти из разных потоков
    # супервизора (watch/guard/update) — общий путь приводил бы к гонке
    # «файл удалён другим потоком» и ложному «конфиг невалиден».
    try:
        fd, p = tempfile.mkstemp(dir=cfg["paths"]["state_dir"],
                                 prefix="forest_test_config_", suffix=".json")
        os.close(fd)
    except Exception as e:
        return False, f"test config path: {e}"
    try:
        _write_json(p, conf)
    except Exception as e:
        return False, f"test config write: {e}"
    env = dict(os.environ)
    adir = geo_asset_dir(core, cfg)
    if adir:
        env["XRAY_LOCATION_ASSET"] = adir
    try:
        r = subprocess.run([core, "run", "-test", "-config", p],
                           cwd=os.path.dirname(core) or None, env=env,
                           capture_output=True, timeout=25,
                           creationflags=_no_window())
    except Exception as e:
        return False, f"test config: {e}"
    finally:
        try:
            os.unlink(p)
        except Exception:
            pass
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    if r.returncode == 0:
        return True, ""
    low = out.lower()
    if "flag provided but not defined" in low or "unknown flag" in low:
        return False, "unsupported"
    m = re.search(r"(failed to[^\n]{0,200})", out)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return False, (m.group(1) if m else tail[-200:])


def _validate_new_config(core: str, conf: dict, cfg: dict) -> tuple:
    """Проверяет НОВЫЙ конфиг ДО остановки работающего ядра.

    Порядок: быстрая проверка ``xray run -test -config`` (кэш по хэшу
    конфига на час). Если ядро не поддерживает ``-test`` — откат на
    «боевую» проверку ``_validate_config`` (запуск на свободных портах).
    """
    lg = log.get_logger()
    sha = _conf_sha(conf)
    try:
        st = StateStore(cfg)
        if sha and st.data.get("fast_validate_sha") == sha and \
                time.time() - float(st.data.get("fast_validate_ts", 0) or 0) < 3600:
            return True, ""
    except Exception:
        pass
    ok, err = _test_config_by_core(core, conf, cfg)
    if not ok and err == "unsupported":
        lg.warn("ядро не понимает 'run -test' — проверяю конфиг запуском "
                "на свободных портах")
        return _validate_config(core, conf, cfg)
    if ok:
        try:
            st = StateStore(cfg)
            st.data["fast_validate_sha"] = sha
            st.data["fast_validate_ts"] = time.time()
            st.save()
        except Exception:
            pass
    return ok, err


def geo_asset_dir(core: str = None, cfg: dict = None) -> str:
    """Каталог с geosite.dat / geoip.dat (нужны для правил geosite:/geoip:)."""
    if cfg is not None:
        try:
            cached = StateStore(cfg).data.get("geo_asset_dir") or ""
            if cached and (os.path.isfile(os.path.join(cached, "geoip.dat"))
                           or os.path.isfile(os.path.join(cached, "geosite.dat"))):
                return cached
        except Exception:
            pass
    cands = []
    env = os.environ.get("XRAY_LOCATION_ASSET")
    if env:
        cands.append(env)
    if core:
        d = os.path.dirname(core)
        for _ in range(4):
            if d and d not in cands:
                cands.append(d)
            d = os.path.dirname(d)
    extra = []
    for c in list(cands):
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


def ru_direct_enabled(cfg: dict) -> bool:
    """Нужна ли маршрутизация «РФ → напрямую» (только при наличии гео-баз)."""
    want = bool((cfg or {}).get("watch", {}).get("ru_direct", False))
    if not want:
        return False
    if geo_asset_dir(None, cfg):
        return True
    log.get_logger().warn("ru_direct включён, но geosite.dat/geoip.dat не найдены — "
                          "правила РФ→direct отключены (весь трафик через прокси)")
    return False


def _validate_config(core: str, conf: dict, cfg: dict) -> tuple:
    """Проверка конфига ядра ДО снятия рабочего ядра: запуск на свободных портах."""
    import hashlib as _hl
    import socket
    import tempfile
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
    for ib in test.get("inbounds", []):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            ib["port"] = s.getsockname()[1]
        finally:
            s.close()
        ib["listen"] = "127.0.0.1"
    fd, tmp_conf = tempfile.mkstemp(suffix=".json", prefix="forest-validate-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(test, f, ensure_ascii=False)
    log_p = os.path.join(cfg["paths"]["state_dir"], "forest_validate.log")
    try:
        env_v = dict(os.environ)
        adir = geo_asset_dir(core, cfg)
        if adir:
            env_v["XRAY_LOCATION_ASSET"] = adir
        with open(log_p, "w", encoding="utf-8") as lf:
            pv = subprocess.Popen([core, "run", "-c", tmp_conf],
                                  cwd=os.path.dirname(core) or None, env=env_v,
                                  stdout=lf, stderr=subprocess.STDOUT,
                                  creationflags=_no_window())
            tst_ports = [ib.get("port") for ib in test.get("inbounds", []) if ib.get("port")]
            alive = False
            dl = time.time() + 2.5
            while time.time() < dl:
                if pv.poll() is not None:
                    break
                try:
                    if tst_ports and all(port_listening(pp, timeout=0.15) for pp in tst_ports):
                        alive = True
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            if alive:
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


# ══════════════════════════════════════════ блокировки ═══
def _lock_path(cfg: dict, name: str) -> str:
    return os.path.join(cfg["paths"]["state_dir"], f"{name}.lock")


def _lock(cfg: dict, name: str, ttl: float = 180.0, wait: float = 0.0) -> bool:
    """Файловый lock: ядром управляет ТОЛЬКО один поток/процесс."""
    p = _lock_path(cfg, name)
    deadline = time.time() + wait
    while True:
        try:
            if os.path.isfile(p):
                age = time.time() - os.path.getmtime(p)
                if age > ttl:
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


# ══════════════════════════════════════════ процессы (Windows) ═══
def _pid_alive(pid: int) -> bool:
    """Жив ли процесс (ctypes, ~0.1 мс)."""
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
    """PID процесса, слушающего порт (ctypes GetExtendedTcpTable, fallback netstat)."""
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
                           creationflags=_no_window())
        out = r.stdout.decode("cp1251", "replace")
        for line in out.splitlines():
            if "LISTENING" in line and f":{port} " in line:
                parts = line.split()
                if parts:
                    return parts[-1].strip()
    except Exception:
        pass
    return None


def _our_core_pids(cfg: dict) -> list:
    """Все НАШИ ядра xray: в командной строке наш конфиг из state_dir.

    Раньше фильтр искал литерал ``forest_xray.json``, а фактический конфиг —
    ``state_dir/xray_config.json``, поэтому осиротевшие ядра (например, после
    аварийного завершения) не находились и оставались держать клиентский порт.
    """
    try:
        marker = os.path.join(cfg["paths"]["state_dir"], "")
        marker = marker.replace("[", "[[]")
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='xray.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
              "ForEach-Object { $_.ProcessId }")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=25, text=True, errors="replace",
                           creationflags=_no_window())
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def _kill_pid(pid) -> bool:
    """Завершить процесс как можно быстрее.

    Сначала TerminateProcess через ctypes (без запуска taskkill.exe — это
    экономит ~150–300 мс, а на перезапуске ядра время остановки целиком
    входит в разрыв связи). Если не вышло — откат на taskkill.
    """
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0001, False, int(pid))      # PROCESS_TERMINATE
        if h:
            try:
                ok = bool(k.TerminateProcess(h, 1))
            finally:
                k.CloseHandle(h)
            if ok:
                return True
    except Exception:
        pass
    try:
        r = subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"],
                           capture_output=True, timeout=15,
                           creationflags=_no_window())
        return r.returncode == 0
    except Exception:
        return False


# ══════════════════════════════════════════ жизненный цикл ядра ═══
def core_pid_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], CORE_PID_NAME)


def core_config_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], CORE_CFG_NAME)


def _read_core_pid(cfg: dict):
    try:
        return int(open(core_pid_path(cfg), encoding="utf-8").read().strip())
    except Exception:
        return None


def _write_core_pid(cfg: dict, pid):
    try:
        with open(core_pid_path(cfg), "w", encoding="utf-8") as f:
            f.write(str(int(pid)))
    except Exception:
        pass


# ── ПОЧЕМУ ЯДРО НЕ СЛУШАЕТ ПОРТ: одна строка с ПРИЧИНОЙ ────────────────
# В 1.3.5 и раньше в журнал и в карточку попадало только «серверы пула не
# отвечают». По такой строке нельзя отличить отсутствующий бинарь ядра от
# невалидного конфига, занятого порта и упавшего процесса — а именно это и
# нужно знать, когда 10808 не открывается. Причина определяется по факту:
# бинарь -> конфиг -> кто держит порт -> жив ли наш pid -> хвост журнала ядра.
_LAST_CORE_REASON = ""


def _set_core_reason(cfg: dict, reason: str) -> None:
    """Запомнить причину (в модуль и в состояние — её видно в отчёте)."""
    global _LAST_CORE_REASON
    _LAST_CORE_REASON = str(reason or "")[:400]
    try:
        st = StateStore(cfg)
        st.data["core_reason"] = _LAST_CORE_REASON
        st.data["core_reason_ts"] = time.time()
        st.save()
    except Exception:
        pass


def last_core_reason() -> str:
    """Причина последнего отказа ядра ('' — отказов не было)."""
    return _LAST_CORE_REASON


def _core_log_tail(cfg: dict, lines: int = 3) -> str:
    """Хвост журнала ядра (forest_xray.log) — там xray пишет свою ошибку."""
    try:
        p = os.path.join(cfg["paths"]["state_dir"], CORE_LOG_NAME)
        with open(p, encoding="utf-8", errors="replace") as f:
            got = f.readlines()[-lines:]
        return " | ".join(x.strip() for x in got if x.strip())[:300]
    except Exception:
        return ""


def _core_exe(pid) -> str:
    try:
        if os.name == "nt":
            return ""
        return os.path.basename(os.readlink("/proc/%d/exe" % int(pid)))
    except Exception:
        return ""


def core_diag(client: dict, cfg: dict, port: int = 0) -> str:
    """ПОЧЕМУ клиентский порт не слушается — одной строкой, по проверкам.

    Порядок проверок = порядок причин на живой машине: нет бинаря -> нет
    конфига -> порт держит ЧУЖОЙ процесс -> наш процесс жив, но не слушает ->
    наш процесс умер (тогда читается его собственный журнал).
    """
    port = int(port or client.get("socks_port", DEFAULT_SOCKS_PORT))
    core = client.get("core") or ""
    root = ((cfg.get("paths") or {}).get("root")
            or (cfg.get("paths") or {}).get("state_dir") or "<каталог данных>")
    if not core or not os.path.isfile(core):
        return ("ядро xray не найдено (%s) — порт %d открывать некому: положите "
                "xray в %s/bin/xray" % (core or "путь пуст", port, root))
    cfg_path = client.get("cfg") or ""
    if not cfg_path or not os.path.isfile(cfg_path):
        return "конфиг ядра не записан (%s)" % (cfg_path or "путь пуст")
    pid_file = _read_core_pid(cfg)
    owner = None
    try:
        owner = _port_pid(port)
    except Exception:
        owner = None
    if port_listening(port, timeout=0.3):
        if owner and pid_file and int(owner) != int(pid_file):
            return ("порт %d держит ЧУЖОЙ процесс pid=%s (%s), а наше ядро pid=%s"
                    % (port, owner, _core_exe(owner) or "?", pid_file))
        tail = _core_log_tail(cfg, 1)
        return "порт %d слушается, но связь не проходит%s" % (
            port, (" (журнал ядра: %s)" % tail) if tail else "")
    if pid_file and _pid_alive(pid_file):
        return ("наше ядро pid=%s живо, но порт %d не слушает (ядро не открыло "
                "inbound: смотрите %s)" % (pid_file, port,
                                           os.path.join(cfg["paths"]["state_dir"],
                                                        CORE_LOG_NAME)))
    tail = _core_log_tail(cfg, 3)
    if tail:
        return "ядро упало на старте: %s" % tail
    return ("ядро не запущено: pid-файл %s отсутствует, порт %d закрыт (xray "
            "запускается с конфигом %s)" % (core_pid_path(cfg), port, cfg_path))


def stop_core(cfg: dict, why: str = "") -> None:
    """Останавливает НАШЕ ядро (по pid-файлу и по конфигу в командной строке).

    НИКОГДА не трогает чужие процессы xray.exe (например, ядро v2rayN).
    """
    lg = log.get_logger()
    pid = _read_core_pid(cfg)
    killed = False
    if pid and _pid_alive(pid):
        if _kill_pid(pid):
            killed = True
            lg.info(f"stop_core{'(' + why + ')' if why else ''}: снято наше ядро pid={pid}")
    # Осиротевшие наши ядра ищем ТОЛЬКО если живого ядра по pid-файлу не было:
    # поиск идёт через PowerShell CIM (~0.4 с) и на перезапуске целиком входит
    # в разрыв связи. Если осиротевшее ядро держит порт, следующий заход
    # (pid-файл уже удалён) его подберёт.
    if not killed:
        for p in _our_core_pids(cfg):
            if p != pid:
                _kill_pid(p)
                lg.warn(f"stop_core: снято осиротевшее наше ядро pid={p}")
    try:
        os.unlink(core_pid_path(cfg))
    except Exception:
        pass


def start_core(client: dict, cfg: dict) -> bool:
    """Запускает ядро xray с текущим конфигом (client['cfg']). True — порт открыт."""
    lg = log.get_logger()
    core = client.get("core")
    cfg_path = client.get("cfg")
    if not core or not os.path.isfile(core):
        _set_core_reason(cfg, "ядро xray не найдено: ни %s/bin/xray, ни /usr/bin/xray, "
                              "ни в PATH — порт %s открывать некому"
                         % (cfg["paths"]["state_dir"], client.get("socks_port")))
        lg.error("start_core: " + _LAST_CORE_REASON)
        return False
    if not cfg_path or not os.path.isfile(cfg_path):
        _set_core_reason(cfg, "конфиг ядра не записан: %s" % (cfg_path or "путь пуст"))
        lg.error("start_core: " + _LAST_CORE_REASON)
        return False
    env = dict(os.environ)
    adir = geo_asset_dir(core, cfg)
    if adir:
        env["XRAY_LOCATION_ASSET"] = adir
    log_path = os.path.join(cfg["paths"]["state_dir"], CORE_LOG_NAME)
    try:
        logf = open(log_path, "a", encoding="utf-8")
        p = subprocess.Popen([core, "run", "-c", cfg_path],
                             cwd=os.path.dirname(core) or None, env=env,
                             stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=_no_window())
    except Exception as e:
        lg.error(f"start_core: {e}")
        return False
    _write_core_pid(cfg, p.pid)
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    dl = time.time() + 8.0
    while time.time() < dl:
        if p.poll() is not None:
            _set_core_reason(cfg, core_diag(client, cfg, port))
            lg.error("start_core: " + _LAST_CORE_REASON)
            return False
        # мелкий шаг опроса: приём порта замечаем через ~10-20 мс, а не 100-300
        if port_listening(port, timeout=0.05):
            # порт мог держать ЧУЖОЙ/осиротевший процесс, а наш уже упал от
            # «address already in use» — без этой проверки мы бы считали ядро
            # поднятым и не перезапускали его
            time.sleep(0.05)
            if p.poll() is not None:
                _set_core_reason(cfg, core_diag(client, cfg, port))
                lg.error("start_core: " + _LAST_CORE_REASON)
                return False
            return True
        time.sleep(0.01)
    _set_core_reason(cfg, core_diag(client, cfg, port))
    lg.warn("start_core: " + _LAST_CORE_REASON)
    return False


def _restart_core_timed(client: dict, cfg: dict) -> tuple:
    """Перезапуск ядра С ЗАМЕРОМ РАЗРЫВА связи (мс).

    Разрыв = от снятия старого ядра до момента, когда новое ядро реально
    приняло клиентский порт (приём соединений). Фиксированная пауза 300 мс
    убрана: новый процесс стартует сразу, а ожидание идёт по факту
    освобождения порта. Возвращает (ok, разрыв_мс, rtt) — rtt уже
    подтверждённой связи, чтобы вызывающий не проверял её второй раз.
    """
    lg = log.get_logger()
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    t0 = time.time()
    stop_core(cfg, why="restart")
    dl = time.time() + 2.0
    while time.time() < dl and port_listening(port, timeout=0.05):
        time.sleep(0.01)
    if not start_core(client, cfg):
        return False, None, 0.0
    listen_ms = (time.time() - t0) * 1000.0
    ok, rtt = wait_and_probe(client, attempts=4, gap=1.0)
    total_ms = (time.time() - t0) * 1000.0
    if not ok:
        lg.warn(f"перезапуск ядра: связь не подтвердилась за {total_ms:.0f} мс")
        return False, None, 0.0
    lg.info(f"перезапуск ядра: РАЗРЫВ связи {listen_ms:.0f} мс "
            f"(порт принял соединения), подтверждение данных {total_ms:.0f} мс, "
            f"rtt={rtt:.0f} мс")
    return True, listen_ms, float(rtt or 0.0)


def restart_core(client: dict, cfg: dict) -> bool:
    """Перезапуск ядра: снять наше старое -> поднять новое с текущим конфигом."""
    ok, _gap_ms, _rtt = _restart_core_timed(client, cfg)
    return ok


def wait_and_probe(client: dict, attempts: int = 6, gap: float = 3.0) -> tuple:
    """Ждёт подъёма порта и проверяет связь.

    Первые попытки — строгая проверка (реальные данные + TLS). Последние две —
    быстрая (204 + TLS): под нагрузкой строгое скачивание 200 КБ даёт ложные
    отказы (в тестах видели связь, которая работала сразу после), а заглушки
    уже отсеяны на этапе отбора пула.
    """
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    for i in range(attempts):
        if port_listening(port):
            strict = i < max(1, attempts - 2)
            ok, rtt = probe_client(client, timeout=8, strict=strict)
            if ok:
                return True, rtt
        if i < attempts - 1:
            time.sleep(gap)
    return False, 0.0


def kill_kernels(cfg: dict = None, why: str = "") -> None:
    """Standalone: снимает ТОЛЬКО наше ядро (чужое не трогает)."""
    if cfg and _mode_of(None, cfg) == "v2rayn":
        return _v2().kill_kernels(cfg, why=why)
    stop_core(cfg, why=why)


def ensure_port_free(client: dict, cfg: dict) -> bool:
    """Standalone: порт принадлежит нашему ядру.

    Если порт занят НЕ нашим процессом (например, ядром v2rayN) — не убиваем
    чужое, только предупреждаем (standalone работает на своём порту из конфига).
    """
    lg = log.get_logger()
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    if not port_busy(port):
        return True
    pid = _port_pid(port)
    ours = _read_core_pid(cfg)
    if pid and ours and str(pid) == str(ours):
        return True
    if _mode_of(client, cfg) == "v2rayn":
        return _v2().ensure_port_free(client, cfg)
    lg.warn(f"порт {port} занят процессом pid={pid} (не нашим) — не трогаю; "
            f"смените client.socks_port в конфиге, если он конфликтует")
    return True


def start_client(client: dict):
    if _mode_of(client) == "v2rayn":
        return _v2().start_client(client)
    return start_core(client, {"paths": {"state_dir": os.path.dirname(client.get("cfg") or ".")}})


def stop_client(client: dict, wait: float = 20.0):
    if _mode_of(client) == "v2rayn":
        return _v2().stop_client(client, wait=wait)
    sd = os.path.dirname(client.get("cfg") or ".")
    stop_core({"paths": {"state_dir": sd}})


# ══════════════════════════════════════════ системный прокси (WinINET) ═══
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


def _sys_proxy_disable() -> bool:
    """Выключает системный прокси и сообщает WinINET (без окон)."""
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
        wi.InternetSetOptionW(0, 39, 0, 0)
        wi.InternetSetOptionW(0, 37, 0, 0)
    except Exception:
        pass
    return True


def proxy_orig_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "proxy_orig.json")


def save_proxy_orig(cfg: dict) -> None:
    """Запомнить ИСХОДНОЕ состояние системного прокси (для --uninstall)."""
    p = proxy_orig_path(cfg)
    if os.path.isfile(p):
        return
    en, srv = _sys_proxy_state()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"enable": bool(en), "server": srv, "ts": time.time()}, f)
    except Exception:
        pass


def restore_system_proxy(cfg: dict) -> bool:
    """Вернуть системный прокси в состояние ДО первого вмешательства."""
    p = proxy_orig_path(cfg)
    if not os.path.isfile(p):
        return False
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return False
    if d.get("enable") and d.get("server"):
        _sys_proxy_apply(str(d["server"]))
    else:
        _sys_proxy_disable()
    try:
        os.unlink(p)
    except Exception:
        pass
    log.get_logger().info("Системный прокси возвращён в исходное состояние")
    return True


_SP_NOTES = {}


def _sys_proxy_supported() -> tuple:
    """(можно ли вообще управлять прокси ОС, причина). POSIX подменяет свою.

    На Windows механизм всегда есть (реестр WinINET). На Linux прокси — это
    настройка рабочего стола, и в службе без сеанса пользователя её может не
    быть: тогда программа обязана сказать об этом ОДИН раз и не долбить систему
    каждые 30-60 секунд (см. clientwrite_posix._posix_sys_proxy_supported).
    """
    return True, ""


def _sp_note(key: str, level: str, msg: str, ttl: float = 1800.0) -> bool:
    """Служебное сообщение о системном прокси — не чаще раза в ``ttl`` секунд."""
    now = time.time()
    prev = _SP_NOTES.get(key)
    if prev is not None and now - prev < ttl:
        return False
    _SP_NOTES[key] = now
    try:
        getattr(log.get_logger(), level if level in ("info", "warn", "error")
                else "info")(msg)
    except Exception:
        pass
    return True


def reset_sys_proxy_notes() -> None:
    """Забыть «уже сообщали» (проверки, смена состояния)."""
    _SP_NOTES.clear()


def ensure_system_proxy(client: dict, cfg: dict) -> bool:
    """Системный прокси должен смотреть на наш порт.

    Вызывается из ensure_balancer ДО всех ранних return. В режиме v2rayn не
    вмешивается при живом GUI v2rayN (это его забота).

    Три исхода, и все — не ошибка:
      * прокси уже наш — True, молча;
      * механизма нет (Linux без сеанса пользователя, неизвестный рабочий
        стол) — одна честная строка в журнал и True: «не наше дело», а не
        ERROR каждые полминуты;
      * поставили — одна строка на СМЕНУ значения, а не на каждый вызов.
    """
    lg = log.get_logger()
    if proxy_off(cfg):
        return False
    if cfg.get("client", {}).get("manage_system_proxy", True) is False:
        return True
    if str(client.get("mode")) == "v2rayn":
        return _v2().ensure_system_proxy(client, cfg)
    ok_av, why = _sys_proxy_supported()
    if not ok_av:
        _sp_note("unavailable:" + str(why), "info",
                 f"Системный прокси: управление недоступно — {why}. Программа "
                 f"работает на своём порту; прокси в браузере задайте вручную")
        return True
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    want = f"127.0.0.1:{port}"
    en, srv = _sys_proxy_state()
    if en and srv.strip() == want:
        return True
    save_proxy_orig(cfg)      # запомнить исходное состояние (один раз)
    if _sys_proxy_apply(want):
        _sp_note("on:" + want, "info",
                 f"Системный прокси переведён на {want} "
                 f"(был {'включён на ' + (srv or '—') if en else 'выключен'})")
        return True
    _sp_note("fail:" + want, "warn",
             "не удалось включить системный прокси — браузеру задайте прокси "
             "вручную (подробности — в строках «Системный прокси» выше)")
    return False


# ══════════════════════════════════════════ ручное отключение / страховка ═══
def proxy_off_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "proxy_off.flag")


def proxy_off(cfg: dict) -> bool:
    try:
        return os.path.isfile(proxy_off_path(cfg))
    except Exception:
        return False


def set_proxy_off(cfg: dict, on: bool, client: dict = None, why: str = "tray") -> bool:
    """Ручное выключение/включение прокси (флаг — единственный источник истины)."""
    lg = log.get_logger()
    p = proxy_off_path(cfg)
    try:
        if on:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(why)
            lg.warn("Прокси выключен ВРУЧНУЮ: автоподъём остановлен")
        else:
            if os.path.isfile(p):
                os.unlink(p)
            lg.info("Прокси включён вручную: автоподъём возобновлён")
    except Exception as e:
        lg.error(f"set_proxy_off: {e}")
        return False
    if on:
        stop_core(cfg, why="manual-off")
        try:
            _sys_proxy_disable()
        except Exception:
            pass
    return True


def lastgood_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "lastgood_servers.json")


def save_lastgood(cfg: dict, servers: list) -> None:
    """Запомнить последний РАБОЧИЙ состав пула (аварийный конфиг)."""
    try:
        import dataclasses
        _write_json(lastgood_path(cfg),
                    {"ts": time.time(), "servers": [dataclasses.asdict(s) for s in servers]})
    except Exception:
        pass


def load_lastgood(cfg: dict) -> list:
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
    """Аварийный подъём последнего рабочего состава."""
    lg = log.get_logger()
    st = StateStore(cfg)
    if time.time() - float(st.data.get("emergency_ts", 0) or 0) < 120:
        return False
    servers = load_lastgood(cfg)
    if not servers:
        lg.warn("аварийный конфиг: сохранённого рабочего состава нет")
        return False
    st.data["emergency_ts"] = time.time()
    st.save()
    lg.warn(f"АВАРИЙНЫЙ КОНФИГ: живых серверов нет — поднимаю последний рабочий "
            f"состав ({len(servers)} серверов)")
    if not _lock(cfg, "balancer", ttl=240, wait=8):
        return False
    try:
        port_n = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
        conf = build_balancer_config(
            servers, port_n,
            listen=cfg.get("client", {}).get("listen", "127.0.0.1"),
            ru_direct=ru_direct_enabled(cfg),
            torrent_port=int(client.get("torrent_port",
                                        cfg.get("client", {}).get(
                                            "torrent_port", DEFAULT_TORRENT_PORT))),
            probe_url=balancer_probe_url(cfg),
            strategy=balancer_strategy(cfg),
            burst=burst_observatory(cfg, balancer_probe_url(cfg)))
        if conf is None:
            lg.error("аварийный конфиг: пул пуст после сборки")
            return False
        # проверка ядром ДО подмены рабочего конфига
        ok_v, err_v = _validate_new_config(client["core"], conf, cfg)
        if not ok_v:
            lg.error(f"аварийный конфиг невалиден ({err_v}) — не применяю")
            return False
        if not _write_json(client["cfg"], conf):
            return False
        ok_r, gap_ms, _rtt_r = _restart_core_timed(client, cfg)
        if not ok_r:
            return False
        st.data["balancer_cfg_sha"] = _conf_sha(conf)
        st.data["balancer_sig"] = _pool_sig(servers)
        st.data["balancer_ru"] = bool(ru_direct_enabled(cfg))
        st.data["balancer_gap_ms"] = gap_ms
        st.save()
    finally:
        _unlock(cfg, "balancer")
    ok, rtt = wait_and_probe(client, attempts=6, gap=2.0)
    if ok:
        save_lastgood(cfg, servers)
        lg.info(f"аварийный конфиг: связь поднята (rtt={rtt} мс)")
    return ok


# ══════════════════════════════════════════ запись конфигов ядра (standalone) ═══
def build_single_config(client: dict, server, cfg: dict) -> dict:
    """Собирает конфиг ядра с ОДНИМ сервером (без записи на диск)."""
    import xraytest as XT
    ob = XT.outbound_json(server)
    if ob is None:
        log.get_logger().error("build_single_config: outbound не собрался")
        return None
    ob = _sanitize_outbound(dict(ob))
    ob["tag"] = "proxy"
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    listen = cfg.get("client", {}).get("listen", "127.0.0.1")
    ru = ru_direct_enabled(cfg)
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_inbound(port, listen), _inbound_torrent(cfg)],
        "outbounds": [ob, {"tag": "direct", "protocol": "freedom"}],
        "dns": _build_dns(ru),
        "routing": _routing_single(ru),
    }


def write_core_config(client: dict, server, cfg: dict) -> bool:
    """Пишет конфиг ядра с ОДНИМ сервером."""
    conf = build_single_config(client, server, cfg)
    if conf is None:
        return False
    return _write_json(client.get("cfg"), conf)


def _inbound(port: int, listen: str = "127.0.0.1") -> dict:
    return {"tag": "in", "port": int(port), "listen": listen,
            "protocol": "mixed", "settings": {"udp": True, "auth": "noauth"}}


def _inbound_torrent(cfg: dict) -> dict:
    tport = int(cfg.get("client", {}).get("torrent_port", DEFAULT_TORRENT_PORT))
    return {"tag": "torrent-in", "port": tport, "listen": "127.0.0.1",
            "protocol": "socks", "settings": {"udp": True, "auth": "noauth"}}


def _routing_single(ru_direct: bool) -> dict:
    rules = [{"type": "field", "outboundTag": "direct", "domain": LOCAL_DIRECT_DOMAINS},
             {"type": "field", "inboundTag": ["torrent-in"], "outboundTag": "proxy"}]
    if ru_direct:
        rules.append({"type": "field", "outboundTag": "direct", "domain": ru_direct_domains()})
        rules.append({"type": "field", "outboundTag": "direct", "ip": RU_DIRECT_IPS})
    rules.append({"type": "field", "network": "tcp,udp", "outboundTag": "proxy"})
    return {"domainStrategy": "IPIfNonMatch", "rules": rules}


def write_balancer_config(client: dict, servers: list, cfg: dict) -> bool:
    """Пишет конфиг ядра с пулом серверов и нативным балансировщиком xray."""
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    listen = cfg.get("client", {}).get("listen", "127.0.0.1")
    tport = int(client.get("torrent_port",
                           cfg.get("client", {}).get("torrent_port", DEFAULT_TORRENT_PORT)))
    ru = ru_direct_enabled(cfg)
    conf = build_balancer_config(servers, port, listen=listen, ru_direct=ru,
                                 torrent_port=tport,
                                 probe_url=balancer_probe_url(cfg),
                                 strategy=balancer_strategy(cfg),
                                 burst=burst_observatory(cfg, balancer_probe_url(cfg)))
    if conf is None:
        log.get_logger().error("write_balancer_config: пул пуст")
        return False
    return _write_json(client.get("cfg"), conf)


# ══════════════════════ параллельная проверка ЖИВОГО пула ══════════════
# Одиночная проба раз в 10-45 с НЕ воспроизводит браузер: он открывает десятки
# соединений параллельно. Отказы на живом пуле приходили ПАЧКАМИ (6 за один
# раунд на разных сайтах) — значит часть соединений балансировщик отдавал узлу,
# который рвёт TLS-рукопожатие. Проверять пул нужно так же, как его использует
# браузер: 8-16 одновременных запросов к РАЗНЫМ сайтам.
PARALLEL_URLS = [
    "https://www.youtube.com/",
    "https://i.ytimg.com/generate_204",
    "https://www.gstatic.com/generate_204",
    "https://ru.wikipedia.org/",
    "https://example.com/",
    "https://github.com/",
    "https://www.cloudflare.com/",
]

# Заведомо МЕДЛЕННЫЕ адреса. github.com через бесплатные узлы стабильно отвечает
# 5-6 с (код 000, curl rc=28) — это свойство сайта/маршрута, а НЕ признак
# плохого узла. Раньше такой ответ считался «сбросом TLS» и каждые 30-40 с
# запускал переотбор пула (~90 раз за сутки) при полностью живой связи.
SLOW_URLS = ["github.com"]

# «Сброс» — только БЫСТРЫЙ отказ транспорта. Ответ 000 за 6 с по таймауту — это
# стопор/медленный сайт, а не узел, рвущий рукопожатие.
RESET_FAST_MS = 1500


def _slow_hosts(cfg: dict = None) -> list:
    try:
        u = list(((cfg or {}).get("watch") or {}).get("burst_slow_urls") or [])
    except Exception:
        u = []
    return [str(x).strip().lower().lstrip(".") for x in (u or SLOW_URLS) if str(x).strip()]


def _is_slow_host(host: str, slow: list) -> bool:
    h = (host or "").lower()
    return any(h == s or h.endswith("." + s) for s in (slow or []))


def _parallel_urls(cfg: dict = None) -> list:
    try:
        u = list(((cfg or {}).get("watch") or {}).get("burst_urls") or [])
        return u or list(PARALLEL_URLS)
    except Exception:
        return list(PARALLEL_URLS)


def _curl_live(port: int, url: str, timeout: float) -> dict:
    """Один запрос ЧЕРЕЗ живой порт: код, время, TLS-verify, подпись ошибки."""
    t0 = time.time()
    cmd = ["curl", "-s", "-S", "-o", os.devnull, "-m", f"{float(timeout):.1f}",
           "-x", f"http://127.0.0.1:{int(port)}",
           "-w", "%{http_code}|%{time_total}|%{ssl_verify_result}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 4,
                           creationflags=_no_window())
        out = r.stdout.decode("utf-8", "replace").strip().split("|")
        code = out[0] if out and out[0].isdigit() else "000"
        tt = float(out[1]) if len(out) > 1 else 999.0
        sslv = int(float(out[2])) if len(out) > 2 and out[2] else 1
        return {"url": url, "code": code, "ms": int((time.time() - t0) * 1000),
                "tt": tt, "ssl": sslv, "rc": r.returncode,
                "err": r.stderr.decode("utf-8", "replace").strip()[:140]}
    except Exception as e:
        return {"url": url, "code": "000", "ms": int((time.time() - t0) * 1000),
                "tt": 999.0, "ssl": 1, "rc": -1, "err": str(e)[:140]}


def parallel_probe(client: dict, cfg: dict, rounds: int = 2,
                   per_round: int = 16, timeout: float = None,
                   stall_s: float = None) -> dict:
    """Параллельная проба ЖИВОГО пула (как браузер). Возвращает статистику.

    Отказ делим на ДВЕ РАЗНЫЕ категории — от этого зависит, трогаем ли пул:

    * ``reset`` — быстрый отказ транспорта (``rc=35``/``rc=56`` или ``000``
      быстрее :data:`RESET_FAST_MS`): узел рвёт рукопожатие. Повод для переотбора;
    * ``stall`` — таймаут/медленный ответ. Сам по себе не говорит, что виноват
      узел: медленным может быть сайт или маршрут. Стопоры на адресах из
      ``SLOW_URLS`` (github.com) в триггер НЕ идут вовсе, а обычные — только
      если стопорят ДВА РАЗНЫХ домена (один медленный сайт пул не рвёт).
    """
    import concurrent.futures as cf
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    urls = _parallel_urls(cfg)
    tc = cfg.get("test") or {}
    w = cfg.get("watch") or {}
    if stall_s is None:
        stall_s = float(w.get("burst_stall_s") or tc.get("hard_stall_s", 5) or 5)
    if timeout is None:
        timeout = float(w.get("burst_probe_timeout_s")
                        or max(6.0, stall_s + 1))
    stall_s = max(1.0, float(stall_s))
    # Медленным адресам — свой, заведомо больший порог: успеть ответить за 10-15 с
    # и НЕ считаться ни отказом, ни стопором.
    slow_limit = float(w.get("burst_slow_stall_s", 15) or 15)
    slow_limit = max(stall_s + 1.0, slow_limit)
    slow_hosts = _slow_hosts(cfg)
    per_round = max(2, int(per_round))
    rounds = max(1, int(rounds))

    def _tmo(u):
        host = u.split("/")[2]
        return slow_limit + 1.0 if _is_slow_host(host, slow_hosts) else timeout

    rows, bad, slow, total, points = [], 0, 0, 0, []
    reset_n, stall_n, site_slow = 0, 0, 0
    reset_hosts, stall_hosts = set(), set()
    for _r in range(rounds):
        tasks = [urls[i % len(urls)] for i in range(per_round)]
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=min(16, len(tasks))) as ex:
            res = list(ex.map(lambda u: _curl_live(port, u, _tmo(u)), tasks))
        rb = rs = 0
        for r in res:
            total += 1
            host = r["url"].split("/")[2]
            slow_host = _is_slow_host(host, slow_hosts)
            # СБРОС: только быстрый транспортный отказ. Долгий 000 (rc=28) —
            # это таймаут, а не сброс рукопожатия.
            is_reset = (r["rc"] in (35, 56)
                        or (r["code"] in ("000", "") and r["ms"] <= RESET_FAST_MS))
            # На ЗАВЕДОМО МЕДЛЕННОМ адресе (github.com) медленный отказ — тоже
            # его собственное свойство: 19 с и «Recv failure» там видели при
            # полностью живой связи. Виновником узла это не считается.
            if slow_host and r["ms"] > RESET_FAST_MS:
                is_reset = False
            lim_s = slow_limit if slow_host else stall_s
            is_stall = (not is_reset) and (
                (r["rc"] == 28 and r["ms"] >= lim_s * 1000 * 0.8)
                or r["ms"] >= lim_s * 1000)
            if is_reset:
                reset_n += 1
                reset_hosts.add(host)
            if is_stall:
                stall_n += 1
                if slow_host:
                    site_slow += 1     # github.com и подобные — не вина узла
                else:
                    stall_hosts.add(host)
            is_bad = is_reset or is_stall
            is_slow = (not is_bad) and r["ms"] > lim_s * 1000 * 0.8
            if is_bad and not (is_stall and slow_host):
                bad += 1
                rb += 1
                rows.append(f"{host} код={r['code']} {r['ms']}мс rc={r['rc']} "
                            f"{r['err'][:100]}")
            elif is_slow:
                slow += 1
                rs += 1
        points.append({"round_ms": int((time.time() - t0) * 1000),
                       "bad": rb, "slow": rs})
    return {"bad": bad, "slow": slow, "total": total, "rows": rows,
            "points": points, "reset": reset_n, "stall": stall_n,
            "reset_hosts": sorted(reset_hosts), "stall_hosts": sorted(stall_hosts),
            "site_slow": site_slow, "slow_hosts": list(slow_hosts),
            "burst": bad > 0}


def burst_trigger(res: dict) -> bool:
    """Пора ли переотбирать пул.

    Только: (1) СБРОС TLS хотя бы на одном запросе, либо (2) стопоры на ДВУХ
    РАЗНЫХ доменах (одиночный медленный сайт — не повод рвать пул). Стопоры на
    заведомо медленных адресах (``SLOW_URLS``) не считаются вообще.
    """
    if not res:
        return False
    if res.get("reset", 0) > 0:
        return True
    return len(res.get("stall_hosts") or ()) >= 2


def _bl_has(bl: dict, s) -> bool:
    """Узел в чёрном списке? Ключи: IndexId и ``address:port``."""
    if not bl:
        return False
    for k in (getattr(s, "_row_index_id", None), getattr(s, "index_id", None),
              f"{getattr(s, 'address', '')}:{getattr(s, 'port', '')}"):
        if k and str(k) in bl:
            return True
    return False


def _tls_map(cfg: dict, ttl_s: float = 21600.0, full: bool = False) -> dict:
    """Свежие результаты TLS-гейта по узлам: ``{addr:port: ok_bool}``.

    ``full=True`` — полные записи ``{ok, ts, why}`` (нужны, чтобы отличать
    «узел рвёт TLS» от «узел просто медленный»: второе не повод выкидывать).
    """
    try:
        import xraytest as XT
        p = XT.tls_store_path(cfg)
        with open(p, encoding="utf-8") as f:
            data = json.load(f) or {}
        now = time.time()
        rec = {k: v for k, v in data.items()
               if isinstance(v, dict) and now - float(v.get("ts", 0) or 0) <= ttl_s}
        return rec if full else {k: bool(v.get("ok")) for k, v in rec.items()}
    except Exception:
        return {}


# Причины брака TLS, которые НЕ доказывают плохой узел: он медленный, а не
# рвущий рукопожатие. Такие узлы годятся для добора пула до пола.
SOFT_TLS_PREFIXES = ("tls_slow_", "tls_ok_slow", "gate_slow_", "slow_")


def _soft_why(why: str) -> bool:
    return str(why or "").startswith(SOFT_TLS_PREFIXES)


def attribute_bad_nodes(client: dict, cfg: dict, pool: list) -> list:
    """Проверяет КАЖДЫЙ узел пула отдельным тестовым ядром (TLS-гейт).

    Это и есть «немедленный переотбор»: после пачки отказов мы не ждём
    плановой проверки и не баним весь пул, а находим КОНКРЕТНЫЕ узлы, которые
    не проходят рукопожатие, и исключаем только их.

    МЕДЛЕННЫЙ узел бракуется только как ЯВНЫЙ ВЫБРОС относительно медианы по
    пулу (``test.tls_slow_factor``/``tls_slow_margin_s``). Раньше абсолютный
    порог 2.5 с выкидывал узлы за 2.8-3.1 с, хотя весь пул в этот момент был
    медленным, — пул съёживался 10 → 5 → 3 → 2 с разрывом связи на каждой
    пересборке.
    """
    import concurrent.futures as cf
    import statistics
    import xraytest as XT
    lg = log.get_logger()
    if not pool:
        return []
    tc = cfg.get("test") or {}
    urls = list(tc.get("tls_urls") or [])
    if not urls:
        return []
    max_s = float(tc.get("tls_max_s", 3.0) or 3.0)
    hard_s = float(tc.get("tls_hard_s", 0) or 0) or max(10.0, max_s * 3.0)
    min_ok = int(tc.get("tls_min_ok", 2) or 2)
    factor = float(tc.get("tls_slow_factor", 1.8) or 1.8)
    margin = float(tc.get("tls_slow_margin_s", 1.0) or 0.0)
    start = int(tc.get("port_start", 10810))
    ports = XT.PortPool(start, max(10, len(pool) * 2 + 4))

    def _one(s):
        port = ports.acquire()
        inst = None
        try:
            ob = XT.outbound_json(s)
            if ob is None:
                return (s, None, "unsupported")
            inst = XT.TestInstance(client["core"], port, ob)
            if not inst.start():
                return (s, None, "core_start_failed")   # не вина узла
            rows = XT.tls_measure(port, urls=urls, timeout=hard_s + 1.0)
            ev = XT.tls_evaluate(rows, max_s=max_s, min_ok=min_ok, hard_s=hard_s)
            return (s, ev, None)
        except Exception as e:
            return (s, None, f"exception:{e}")          # ошибку теста не баним
        finally:
            if inst:
                inst.stop()
            ports.release(port)

    out = []
    workers = max(2, min(8, len(pool)))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, s) for s in pool]
        for f in cf.as_completed(futs):
            try:
                out.append(f.result())
            except Exception:
                pass
    hard_bad = [(s, err or ev["why"])
                for (s, ev, err) in out
                if err or (ev and not ev["ok"] and ev["kind"] != "slow")]
    soft = [(s, ev) for (s, ev, err) in out
            if ev and not ev["ok"] and ev["kind"] == "slow"]
    vals = [ev["worst_s"] for (_s, ev, _e) in out
            if ev and ev["worst_s"] and ev["kind"] in ("ok", "slow")]
    med = float(statistics.median(vals)) if vals else 0.0
    tol = max(med * factor, med + margin) if med else 0.0
    bad, rescued = list(hard_bad), []
    for s, ev in soft:
        worst = float(ev.get("worst_s") or 0)
        if tol and worst <= tol:
            # Медленный, но в общей картине пула — НЕ виновник.
            rescued.append(s)
            with_level = "tls_ok_slow_median"
            try:
                XT.tls_record(cfg, s, True, with_level)
            except Exception:
                pass
            continue
        bad.append((s, f"tls_slow_outlier_{worst:.1f}s_{ev.get('slow_host') or '?'}"))
    for (s, ev, err) in out:
        if err or ev is None:
            continue
        if ev["ok"]:
            try:
                XT.tls_record(cfg, s, True, ev["why"] or "ok")
            except Exception:
                pass
    lg.info(f"Переотбор узлов: проверено {len(out)}, брак {len(bad)}"
            + (f" (жёстких {len(hard_bad)}, медленных-выбросов "
               f"{len(bad) - len(hard_bad)})" if bad else "")
            + (f"; медиана TLS {med:.2f} с, порог выброса {tol:.2f} с" if med else "")
            + (f"; прощено медленных (в общей картине): {len(rescued)}"
               if rescued else "")
            + (f" [{', '.join(w for _s, w in bad[:6])}]" if bad else ""))
    return bad


def ban_bad_nodes(cfg: dict, bad: list, minutes: float = 30.0) -> int:
    """Банит узлы, не прошедшие TLS-гейт (по IndexId И по address:port)."""
    n = 0
    for s, why in bad or []:
        key = getattr(s, "_row_index_id", None) or getattr(s, "index_id", None)
        if key:
            bl_add(cfg, key, minutes=minutes, why=str(why)[:60])
        if getattr(s, "address", ""):
            bl_add(cfg, f"{s.address}:{s.port}", minutes=minutes,
                   why=str(why)[:60])
        n += 1
    return n


def post_validate_pool(client: dict, cfg: dict, pool: list = None,
                       rounds: int = None, per_round: int = 16) -> dict:
    """Проверка пула СРАЗУ после применения: 16 параллельных × 2-3 раунда.

    Возвращает ``{ok, bad, banned, res}``. Если отказы есть — находит виновника
    отдельными тестовыми ядрами и банит ТОЛЬКО его (вызывающий пересобирает пул
    без перезапуска по расписанию).
    """
    lg = log.get_logger()
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    if proxy_off(cfg) or not port_listening(port, 0.5):
        return {"ok": False, "bad": 0, "banned": [], "res": None,
                "skipped": "no_core"}
    w = cfg.get("watch") or {}
    try:
        rounds = int(rounds if rounds is not None
                     else (w.get("post_validate_rounds", 2) or 2))
    except Exception:
        rounds = 2
    timeout = float(w.get("burst_probe_timeout_s", 6) or 6)
    stall = float(w.get("burst_stall_s", 5) or 5)
    res = parallel_probe(client, cfg, rounds=rounds,
                         per_round=int(per_round), timeout=timeout, stall_s=stall)
    if not burst_trigger(res):
        if res["bad"]:
            lg.warn(f"Пост-валидация: {res['bad']} стопор(ов) из {res['total']} "
                    f"(одиночный — не повод рвать пул): {res['rows'][:2]}")
        return {"ok": True, "bad": res["bad"], "banned": [], "res": res}
    lg.warn(f"Пост-валидация пула: сбросов TLS {res['reset']}, доменов со "
            f"стопором {len(res.get('stall_hosts') or [])} из {res['total']} — "
            f"ищу виновника ({res['rows'][:3]})")
    pl = pool if pool is not None else balancer_pool(
        client, cfg, n=int(w.get("balancer_pool_n", 10) or 10))
    bad = attribute_bad_nodes(client, cfg, pl)
    if not bad:
        # Узлы пула целы: причина вне пула (сайт/маршрут). Ставим паузу, чтобы
        # сторож не запускал ту же атрибуцию каждые 30-40 с.
        secs = _burst_backoff_bump(cfg, "пост-валидация: виновник не в пуле")
        lg.warn(f"Пост-валидация: виновник не в пуле — пауза "
                f"{max(1, secs // 60)} мин, пул не трогаю")
        return {"ok": res["bad"] == 0, "bad": res["bad"], "banned": [], "res": res}
    banned = ban_bad_nodes(cfg, bad, minutes=float(w.get("ban_bad_minutes", 30) or 30))
    if banned:
        lg.warn(f"Пост-валидация: исключено узлов {banned} — пул будет пересобран")
    return {"ok": res["bad"] == 0, "bad": res["bad"], "banned": bad, "res": res}


def cycle_running(cfg: dict, stale_s: float = 1800.0) -> bool:
    """Идёт ли ПОЛНЫЙ цикл обновления (по замку цикла).

    Периодическую параллельную пробу в это время не гоняем: цикл уже поднимает
    десятки тестовых ядер и грузит CPU, и любая проба покажет отказы от
    конкуренции за процессор, а не от плохих узлов (реальный источник ложных
    «ударов» в проекте). Пост-валидация после применения пула НЕ пропускается —
    она идёт сразу за подъёмом ядра.
    """
    try:
        p = os.path.join(cfg["paths"]["state_dir"], "lock.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return (time.time() - float(d.get("ts", 0) or 0)) < stale_s
    except Exception:
        return False


def _burst_backoff(cfg: dict) -> tuple:
    """Текущая пауза сторожа: ``(until_ts, step)``. step=0 — паузы нет."""
    try:
        st = StateStore(cfg)
        return (float(st.data.get("burst_backoff_until", 0) or 0),
                int(st.data.get("burst_backoff_step", 0) or 0))
    except Exception:
        return 0.0, 0


def _burst_backoff_bump(cfg: dict, reason: str = "") -> int:
    """Увеличивает паузу: 5 → 10 → 20 → 30 мин (максимум 30). Возвращает секунды.

    Нужна на случай «атрибуция не нашла виновника в пуле»: причина вне пула
    (медленный сайт, маршрут, провайдер), и долбить переотбор каждые 30-40 с
    бессмысленно — за сутки так набегало ~90 срабатываний при живой связи.
    """
    try:
        st = StateStore(cfg)
        steps = [int(x) for x in ((cfg.get("watch") or {}).get(
            "burst_backoff_s") or [300, 600, 1200, 1800])]
        steps = sorted(s for s in steps if s > 0) or [300, 600, 1200, 1800]
        step = int(st.data.get("burst_backoff_step", 0) or 0)
        secs = steps[min(step, len(steps) - 1)]
        st.data["burst_backoff_step"] = step + 1
        st.data["burst_backoff_until"] = time.time() + secs
        st.data["burst_backoff_reason"] = str(reason or "")[:120]
        st.data["burst_backoff_ts"] = time.time()
        st.save()
        return secs
    except Exception:
        return 0


def _burst_backoff_clear(cfg: dict) -> None:
    """Сбрасывает паузу после чистой пробы (связь в порядке)."""
    try:
        st = StateStore(cfg)
        if st.data.get("burst_backoff_step") or st.data.get("burst_backoff_until"):
            st.data["burst_backoff_step"] = 0
            st.data["burst_backoff_until"] = 0
            st.data["burst_backoff_reason"] = ""
            st.save()
    except Exception:
        pass


def burst_guard(client: dict, cfg: dict, n: int = 10) -> str:
    """Периодическая параллельная проба + мгновенный переотбор по РЕАЛЬНОЙ причине.

    Цель — окно деградации меньше 10 с: заметили пачку сбросов TLS, нашли узел,
    сняли его и пересобрали пул, не дожидаясь плановой проверки. Медленный ответ
    (в том числе github.com за 5-6 с) причиной переотбора НЕ является; если
    атрибуция не нашла виновника в пуле — включается пауза 5/10/20/30 мин.
    """
    lg = log.get_logger()
    w = cfg.get("watch") or {}
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    if proxy_off(cfg):
        return "skip"
    if not port_listening(port, 0.5):
        return "skip"            # смерть ядра — забота пульса, не этой пробы
    if cycle_running(cfg):
        return "cycle"           # идёт полный цикл: проба покажет конкуренцию за CPU
    now = time.time()
    until, step = _burst_backoff(cfg)
    if until > now:
        return "backoff"         # пауза после «виновник не в пуле» — молча
    try:
        cooldown = float(w.get("burst_cooldown_after_rebuild_s", 120) or 0)
        last_rb = float(StateStore(cfg).data.get("burst_rebuild_ts", 0) or 0)
        if cooldown > 0 and last_rb and (now - last_rb) < cooldown:
            return "cooldown"    # только что пересобрали — не дёргаем пул снова
    except Exception:
        pass
    res = parallel_probe(client, cfg, rounds=1,
                         per_round=int(w.get("burst_probe_n", 12) or 12),
                         timeout=float(w.get("burst_probe_timeout_s", 6) or 6),
                         stall_s=float(w.get("burst_stall_s", 5) or 5))
    if not burst_trigger(res):
        if res["bad"]:
            lg.warn(f"Параллельная проба: {res['bad']} отказ(ов) из "
                    f"{res['total']}, но причины для переотбора нет "
                    f"(сбросов {res['reset']}, доменов со стопором "
                    f"{len(res.get('stall_hosts') or [])}) — пул не трогаю: "
                    f"{res['rows'][:2]}")
        if res.get("site_slow"):
            lg.info(f"Параллельная проба: {res['site_slow']} медленных ответ(ов) "
                    f"на заведомо медленные адреса "
                    f"({', '.join(res.get('slow_hosts') or [])}) — не вина узлов")
        if not res["bad"] and step:
            _burst_backoff_clear(cfg)
        return "ok"
    lg.warn(f"Параллельная проба: сбросов TLS {res['reset']}, доменов со "
            f"стопором {len(res.get('stall_hosts') or [])} "
            f"({', '.join(res.get('stall_hosts') or []) or '—'}) из "
            f"{res['total']} — переотбор ({res['rows'][:3]})")
    pool = balancer_pool(client, cfg, n=int(w.get("balancer_pool_n", n) or n))
    bad = attribute_bad_nodes(client, cfg, pool)
    if not bad:
        secs = _burst_backoff_bump(cfg, "виновник не в пуле")
        when = time.strftime("%H:%M", time.localtime(time.time() + secs)) if secs else "?"
        lg.warn(f"Переотбор: виновник не в пуле (узлы целы) — "
                f"пауза {max(1, secs // 60)} мин до {when}, пул не трогаю")
        return "burst_unattributed"
    ban_bad_nodes(cfg, bad, minutes=float(w.get("ban_bad_minutes", 30) or 30))
    ok = ensure_balancer(client, cfg, n=n, force=True, fast_ok=True,
                         post_retry=True)
    try:
        st = StateStore(cfg)
        st.data["burst_rebuild_ts"] = time.time()
        st.save()
    except Exception:
        pass
    _burst_backoff_clear(cfg)
    lg.warn(f"Переотбор: снято {len(bad)}, пул пересобран — "
            f"{'связь OK' if ok else 'связи нет'}")
    return f"rebuild_{len(bad)}" + ("_ok" if ok else "_fail")


# ══════════════════════════════════════════ пул балансировщика ═══
def _pool_floor(cfg: dict, n: int) -> int:
    """Пол пула: ниже него состав не опускаем (0 = пол выключен).

    Берётся из ``diversity.min_pool_after_cap`` (та же страховка, что в полном
    цикле) либо ``watch.pool_floor``; больше целевого размера пула пол смысла
    не имеет, поэтому ограничивается ``n``.
    """
    try:
        div = cfg.get("diversity") or {}
        floor = int(div.get("min_pool_after_cap", 0) or 0)
        floor = int((cfg.get("watch") or {}).get("pool_floor", 0) or 0) or floor
    except Exception:
        floor = 0
    return max(0, min(int(n or 0), floor))


def balancer_pool(client: dict, cfg: dict, n: int = 10, min_speed_kbps: float = 0) -> list:
    """Топ-N серверов для пула: из hotcache, иначе из [AUTO]-стора.

    Без подтверждённой скорости сервер в пул не берём (CF-заглушки отдают 204,
    но данных не везут — leastPing выбрал бы именно их).
    """
    import xraytest as XT
    st = StateStore(cfg)
    pool = []
    seen = set()
    bl = bl_active(cfg)
    tls = _tls_map(cfg)
    # TTL hotcache: записи старше срока НЕ используем — иначе давно протухший
    # узел (или «глушитель» YouTube) живёт в пуле бесконечно. В реальном
    # state.json запись-глушитель была возрастом 69 ч и всё равно бралась.
    try:
        hot_ttl = float(cfg.get("watch", {}).get("hotcache_ttl_s", 3600) or 0)
    except Exception:
        hot_ttl = 3600.0
    now = time.time()
    for e in (st.data.get("hotcache") or []):
        if not e.get("speed_kbps"):
            continue
        if hot_ttl > 0 and (now - float(e.get("ts", 0) or 0)) > hot_ttl:
            continue
        # Явно помеченный «YouTube не открывается» узел в пул не берём.
        if e.get("youtube_ok") is False:
            continue
        if min_speed_kbps and e["speed_kbps"] < min_speed_kbps:
            continue
        # Чёрный список проверяем И для hotcache: раньше бан обходился, потому
        # что быстрый путь отдавал узел из кэша без проверки IndexId.
        if bl and (str(e.get("index_id") or "") in bl
                   or f"{e.get('address','')}:{e.get('port','')}" in bl):
            continue
        # Узел, не прошедший TLS-рукопожатие к разным доменам, в пул не берём.
        if tls.get(f"{e.get('address','')}:{e.get('port','')}") is False:
            continue
        s = _row_to_server_from_cache(e)
        if s is not None and s.address and s.port and (s.password or "").strip() \
                and (s.address, s.port) not in seen:
            seen.add((s.address, s.port))
            pool.append(s)
        if len(pool) >= n:
            return pool
    bl = bl_active(cfg)
    rows = [r for r in read_db_rows(client["db"])
            if (r.get("Remarks") or "").startswith(AUTO_TAG)]
    for r in rows:
        if str(r.get("IndexId")) in bl:
            continue
        s = row_to_server(r)
        if s is None:
            continue
        s._row_index_id = str(r.get("IndexId"))
        if (s.address, s.port) in seen:
            continue
        if _bl_has(bl, s):
            continue
        if (s.speed_kbps or 0) <= 0:
            continue
        if min_speed_kbps and (s.speed_kbps or 0) < min_speed_kbps:
            continue
        try:
            if "xhttp" in json.dumps(XT.outbound_json(s)).lower():
                continue
        except Exception:
            pass
        if tls.get(f"{s.address}:{s.port}") is False:
            continue
        seen.add((s.address, s.port))
        pool.append(s)
        if len(pool) >= n:
            break
    # Подтверждённые мульти-доменным TLS-гейтом узлы идут первыми: скорость
    # важна, но узел, рвущий рукопожатие, не должен попасть в пул даже быстрым.
    pool.sort(key=lambda s: (0 if tls.get(f"{s.address}:{s.port}") is True else 1,
                             -(s.speed_kbps or 0)))
    # ── ПОЛ ПУЛА ────────────────────────────────────────────────────────────
    # diversity.min_pool_after_cap раньше действовал ТОЛЬКО в полном цикле, а
    # пул балансировщика собирался без пола: после каждого переотбора он
    # съёживался 10 → 5 → 3 → 2, и каждая пересборка рвала связь на 250-330 мс.
    # Теперь пул ниже пола добирается узлами, исключёнными ЛИШЬ за медленность
    # (не за сброс TLS и не за заглушку), и каждая усадка пишется в лог WARN.
    floor = _pool_floor(cfg, n)
    prev_n = int(st.data.get("pool_size_last", 0) or 0)
    rescued = []
    try:
        if floor and len(pool) < floor:
            tlsf = _tls_map(cfg, full=True)

            def _ap(s):
                return f"{getattr(s, 'address', '')}:{getattr(s, 'port', '')}"

            def _tls_soft(s):
                rec = tlsf.get(_ap(s)) or {}
                return bool(rec) and not rec.get("ok") and _soft_why(rec.get("why"))

            def _tls_hard(s):
                rec = tlsf.get(_ap(s)) or {}
                return bool(rec) and not rec.get("ok") and not _soft_why(rec.get("why"))

            def _bl_soft(s):
                for k in (getattr(s, "_row_index_id", None),
                          getattr(s, "index_id", None), _ap(s)):
                    rec = bl.get(str(k)) if k else None
                    if rec and _soft_why(rec.get("why")):
                        return True
                return False

            def _bl_hard(s):
                for k in (getattr(s, "_row_index_id", None),
                          getattr(s, "index_id", None), _ap(s)):
                    rec = bl.get(str(k)) if k else None
                    if rec and not _soft_why(rec.get("why")):
                        return True
                return False

            def _usable(s):
                return bool(s and s.address and s.port
                            and (s.password or "").strip()
                            and (s.speed_kbps or 0) > 0
                            and (not min_speed_kbps
                                 or (s.speed_kbps or 0) >= min_speed_kbps)
                            and not _tls_hard(s) and not _bl_hard(s)
                            and (_tls_soft(s) or _bl_soft(s)))

            for e in (st.data.get("hotcache") or []):
                if len(pool) >= floor:
                    break
                if not e.get("speed_kbps") or e.get("youtube_ok") is False:
                    continue
                if hot_ttl > 0 and (now - float(e.get("ts", 0) or 0)) > hot_ttl:
                    continue
                s = _row_to_server_from_cache(e)
                if not s or (s.address, s.port) in seen or not _usable(s):
                    continue
                seen.add((s.address, s.port))
                pool.append(s)
                rescued.append(_ap(s))
            for r in read_db_rows(client["db"]):
                if len(pool) >= floor:
                    break
                if not (r.get("Remarks") or "").startswith(AUTO_TAG):
                    continue
                s = row_to_server(r)
                if s is None:
                    continue
                s._row_index_id = str(r.get("IndexId"))
                if (s.address, s.port) in seen or not _usable(s):
                    continue
                seen.add((s.address, s.port))
                pool.append(s)
                rescued.append(_ap(s))
    except Exception as _e:
        log.get_logger().warn(f"balancer_pool: добор до пола не удался: {_e}")
    size = len(pool)
    lg = log.get_logger()
    if prev_n and size < prev_n:
        lg.warn(f"ПУЛ УСАЖЕН: {prev_n} → {size} узлов (пол {floor or '—'}; "
                f"добрано медленных {len(rescued)})")
    elif floor and size < floor:
        lg.warn(f"ПУЛ НИЖЕ ПОЛА: {size} < {floor} — подходящих узлов больше нет "
                f"(медленные тоже исчерпаны), пересборка не поможет")
    if size != prev_n:
        try:
            st.data["pool_size_last"] = size
            st.save()
        except Exception:
            pass
    return pool[:max(size, floor)] if floor else pool[:n]


def _row_to_server_from_cache(e: dict):
    """Собирает Server из записи hotcache.

    В hotcache хранится ПОЛНЫЙ сервер (asdict) — иначе у восстановленного
    сервера нет пароля/ID и outbound выходит невалидным («invalid UUID»).
    """
    import parser as P
    full = e.get("server")
    if isinstance(full, dict):
        try:
            import dataclasses
            fields = {f.name for f in dataclasses.fields(P.Server)}
            return P.Server(**{k: v for k, v in full.items() if k in fields})
        except Exception:
            pass
    try:
        s = P.Server(protocol=e.get("protocol", "vless"),
                     address=e.get("address", ""), port=int(e.get("port", 0)))
        s.remark = e.get("remark", "")
        s.rtt_ms = e.get("rtt_ms", 0)
        s.speed_kbps = e.get("speed_kbps", 0)
        return s if s.address and s.port > 0 else None
    except Exception:
        return None


def _pool_sig(pool: list) -> str:
    return ",".join(sorted(f"{s.address}:{s.port}" for s in pool))


def _link_ok(cfg: dict, port: int, fresh_s: float = 45.0) -> bool:
    """Живость связи по отметкам сторожа (дёшево), иначе — probe."""
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
    try:
        ok, _rtt = probe_client({"socks_port": int(port)}, timeout=8)
        return bool(ok)
    except Exception:
        return False


def ensure_balancer(client: dict, cfg: dict, n: int = 10,
                    force: bool = False, fast_ok: bool = False,
                    post_retry: bool = False) -> bool:
    """Поднимает пул серверов с балансировщиком xray. True — связь есть.

    ``post_retry`` — служебный флаг: пул уже пересобран после пост-валидации,
    повторную проверку не запускаем (защита от петли пересборок).
    """
    if _mode_of(client, cfg) == "v2rayn":
        return _v2().ensure_balancer(client, cfg, n=n, force=force, fast_ok=fast_ok)
    lg = log.get_logger()
    socks_port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    if proxy_off(cfg):
        return True
    pool = balancer_pool(client, cfg, n=n,
                         min_speed_kbps=float(cfg.get("watch", {}).get(
                             "speed_min_kbps", 0) or 0))
    if not pool:
        lg.warn("ensure_balancer: пул пуст")
        if not _link_ok(cfg, socks_port) and _emergency_restore(client, cfg, socks_port):
            return True
        # ПРИЧИНА в состояние, даже когда ядро не трогали: «пул пуст» — это НЕ
        # причина закрытого порта, а следствие. Без этой строки в отчёте
        # оставалось `core_reason: пустой`, и по нему нельзя было понять, что
        # порт закрыт потому, что запускать ядро было не с чем.
        try:
            _set_core_reason(cfg, core_diag(client, cfg, socks_port))
            lg.error("ensure_balancer: порт %d не открыт — ПРИЧИНА: %s"
                     % (socks_port, _LAST_CORE_REASON))
        except Exception:
            pass
        return False
    ensure_system_proxy(client, cfg)
    sig = _pool_sig(pool)
    st = StateStore(cfg)
    live_sha = _cfg_sha(client["cfg"]) if os.path.isfile(client["cfg"]) else ""
    owned_sha = str(st.data.get("balancer_cfg_sha") or "")
    pid = _read_core_pid(cfg)
    ours = bool(pid and _pid_alive(pid))
    live = bool(ours and port_listening(socks_port, timeout=0.3))
    link = _link_ok(cfg, socks_port) if live else False
    # Работающее ядро наше и конфиг на диске — тот, что мы записали:
    # только тогда его можно считать «своим» и не трогать.
    same_running = bool(live and owned_sha and owned_sha == live_sha)

    # КОРЕНЬ. ЖЕЛАЕМЫЙ конфиг собираем ДО решения «перезапускать или нет».
    # Раньше решение опиралось на отпечаток ОДНОГО ПУЛА (_pool_sig), который не
    # учитывает НАСТРОЙКИ: включение watch.ru_direct не меняло отпечаток, и
    # функция выходила по «состав пула не изменился» — настройка молча не
    # применялась до следующей смены состава пула. Теперь сравниваем сам
    # конфиг (он включает маршрутизацию, DNS, порты, outbound'ы).
    port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    listen = cfg.get("client", {}).get("listen", "127.0.0.1")
    tport = int(client.get("torrent_port",
                           cfg.get("client", {}).get("torrent_port",
                                                     DEFAULT_TORRENT_PORT)))
    want_ru = ru_direct_enabled(cfg)
    conf = build_balancer_config(pool, port, listen=listen, ru_direct=want_ru,
                                 torrent_port=tport,
                                 probe_url=balancer_probe_url(cfg),
                                 strategy=balancer_strategy(cfg),
                                 burst=burst_observatory(cfg, balancer_probe_url(cfg)))
    if conf is None:
        lg.warn("ensure_balancer: пул пуст после сборки outbound'ов")
        return bool(link)
    cand_sha = _conf_sha(conf)
    if live and link and same_running:
        if cand_sha == live_sha:
            lg.info("ensure_balancer: конфиг совпадает с работающим — перезапуска нет")
            st.data["balancer_sig"] = sig
            st.data["balancer_cfg_sha"] = cand_sha
            st.data["balancer_ru"] = bool(want_ru)
            st.data["balancer_n"] = len(pool)
            st.save()
            return True
        age = time.time() - float(st.data.get("balancer_ts", 0) or 0)
        freeze_s = float(cfg.get("watch", {}).get("pool_freeze_s", 10800))
        pool_changed = st.data.get("balancer_sig") != sig
        # Заморозка нужна ТОЛЬКО против перестановки узлов (чтобы не рвать связь
        # ради смены состава). Смена НАСТРОЙКИ маршрутизации должна применяться
        # сразу, не дожидаясь цикла обновления пула.
        if pool_changed and st.data.get("balancer_sig") and age < freeze_s and not force:
            lg.info("ensure_balancer: состав пула изменился, но ядро работает — "
                    f"пул заморожен ещё на {int((freeze_s - age) / 60)} мин "
                    "(перезапуска нет)")
            return True
        if not pool_changed:
            lg.info("ensure_balancer: изменилась настройка маршрутизации (РФ→напрямую) "
                    "— перегенерирую конфиг ядра и перезапускаю ядро сейчас")
    if not _lock(cfg, "balancer", ttl=240, wait=8):
        lg.warn("ensure_balancer: другое управление ядром уже идёт — пропускаю цикл")
        return True
    restarted = False
    rtt_restart = 0.0
    try:
        # (а) НОВЫЙ конфиг проверяется ядром ДО остановки рабочего ядра:
        # мёртвый конфиг не должен уносить связь.
        ok_v, err_v = _validate_new_config(client["core"], conf, cfg)
        if not ok_v:
            _set_core_reason(cfg, core_diag(client, cfg, socks_port)
                             if not client.get("core") else
                             "новый конфиг отвергнут ядром: %s" % err_v)
            lg.error(f"ensure_balancer: новый конфиг невалиден ({err_v}) — "
                     f"живое ядро НЕ трогаю. ПРИЧИНА: {_LAST_CORE_REASON}")
            return bool(link)
        if live and link and cand_sha == live_sha:
            lg.info("ensure_balancer: конфиг совпадает с работающим — "
                    "перезапуска нет")
            st.data["balancer_sig"] = sig
            st.data["balancer_cfg_sha"] = cand_sha
            st.data["balancer_ru"] = bool(want_ru)
            st.data["balancer_n"] = len(pool)
            st.save()
            return True
        if not _write_json(client["cfg"], conf):
            lg.error("ensure_balancer: не удалось записать конфиг ядра")
            return False
        ok_r, gap_ms, rtt_r = _restart_core_timed(client, cfg)
        if not ok_r:
            lg.error("ensure_balancer: ядро не поднялось. ПРИЧИНА: %s"
                     % (last_core_reason() or "причина не определена"))
            return False
        restarted, rtt_restart = True, rtt_r
        st.data["balancer_cfg_sha"] = cand_sha
        st.data["balancer_ru"] = bool(want_ru)
        st.data["balancer_gap_ms"] = gap_ms
        st.save()
    finally:
        _unlock(cfg, "balancer")
    if restarted:
        # связь уже подтверждена внутри перезапуска — не проверяем второй раз
        ok, rtt = True, rtt_restart
    elif fast_ok and port_listening(socks_port):
        ok, rtt = True, 0.0
    else:
        ok, rtt = wait_and_probe(client, attempts=6, gap=2.0)
    if ok:
        st.data["balancer_sig"] = sig
        st.data["balancer_n"] = len(pool)
        st.data["balancer_ru"] = bool(want_ru)
        st.data["balancer_ts"] = time.time()
        st.data["emergency_ts"] = 0
        st.save()
        save_lastgood(cfg, pool)
        extra = ""
        try:
            gm = st.data.get("balancer_gap_ms")
            extra = f", разрыв {float(gm):.0f} мс" if gm is not None else ""
        except Exception:
            extra = ""
        lg.info(f"Балансировщик поднят: {len(pool)} серверов (rtt={rtt} мс{extra})")
        lg.info(f"ПУЛ ПРИМЕНЁН: {len(pool)} узлов, sha={cand_sha[:8]}{extra}")
        # ПОСТ-ВАЛИДАЦИЯ СРАЗУ ПОСЛЕ ПРИМЕНЕНИЯ ПУЛА. Одиночная проба ловит
        # «связь есть», но не ловит узел, который рвёт TLS на ЧАСТИ соединений:
        # браузер открывает десятки параллельно, и часть уходит на него. Поэтому
        # после подъёма/переключения гоняем те же 16 параллельных запросов и при
        # отказах сразу ищем виновника и пересобираем пул — не ждём плановой
        # проверки (окно деградации было 30-180 с).
        if restarted and not post_retry and bool(
                (cfg.get("watch") or {}).get("post_validate", 1)):
            # Пауза на «прогрев» наблюдателя: сразу после старта ядра у leastLoad
            # ещё нет замеров, и первые соединения могут уйти на узел, который
            # burstObservatory через 2-4 с признает мёртвым. Без паузы первая же
            # параллельная проба даёт ложные сбросы сразу после перезапуска.
            try:
                time.sleep(float((cfg.get("watch") or {}).get(
                    "post_validate_delay_s", 3) or 0))
            except Exception:
                pass
            try:
                pv = post_validate_pool(client, cfg, pool=pool)
                if pv.get("banned"):
                    lg.warn("Пост-валидация: пул с мёртвыми узлами — пересобираю "
                            "сразу, без ожидания расписания")
                    return ensure_balancer(client, cfg, n=n, force=True,
                                           fast_ok=True, post_retry=True)
                if pv.get("ok"):
                    lg.info("Пост-валидация: пул чист (0 отказов в параллельной "
                            "пробе)")
            except Exception as e:
                lg.warn(f"Пост-валидация пула: {e}")
    else:
        lg.warn("ensure_balancer: связь не появилась")
    return ok


# ══════════════════════════════════════════ активация ═══
def activate(client: dict, servers: list, active_id: str, cfg: dict,
             active_server=None, update_stats: bool = True, existing: list = None,
             restart_client: bool = True, force_pool: bool = False) -> dict:
    """Полный цикл активации: запись стора -> конфиг -> запуск ядра -> проверка."""
    if _mode_of(client, cfg) == "v2rayn":
        return _v2().activate(client, servers, active_id, cfg,
                              active_server=active_server, update_stats=update_stats,
                              existing=existing, restart_client=restart_client,
                              force_pool=force_pool)
    lg = log.get_logger()
    switched = str(read_active_id(client["cfg"])) != str(active_id)
    try:
        n = write_auto_group(client["db"], servers, update_stats)
        write_active_id(client["cfg"], active_id)
        lg.info(f"Стор обновлён: {n} [AUTO]-серверов, активный: {active_id}")
    except Exception as e:
        lg.error(f"Запись стора не удалась: {e}")
        return {"ok": False, "switched": switched, "rtt": 0, "msg": f"write_error: {e}"}

    if bool(cfg.get("watch", {}).get("balancer_mode", False)):
        # force_pool — только для ЯВНОГО запроса пользователя («Обновить
        # серверы сейчас»): тогда свежий состав пула применяется сразу, не
        # дожидаясь заморозки. При одинаковом конфиге перезапуска всё равно нет
        # (ensure_balancer сверит sha конфига).
        if ensure_balancer(client, cfg,
                           n=int(cfg["watch"].get("balancer_pool_n", 10)),
                           force=force_pool):
            ok2, rtt2 = probe_client(client, timeout=8)
            if ok2:
                return {"ok": True, "switched": switched, "rtt": rtt2,
                        "msg": "balancer_ok"}
        lg.warn("Балансировщик не дал связи — пробую одиночный сервер")

    srv = active_server
    if srv is None:
        for r in read_db_rows(client["db"]):
            if str(r.get("IndexId")) == str(active_id):
                srv = row_to_server(r)
                break
    if srv is None:
        return {"ok": False, "switched": switched, "rtt": 0, "msg": "server_not_found"}
    # НОВЫЙ конфиг собираем и проверяем ядром ДО остановки рабочего ядра
    conf_s = build_single_config(client, srv, cfg)
    if conf_s is None:
        return {"ok": False, "switched": switched, "rtt": 0, "msg": "config_write_failed"}
    ok_v, err_v = _validate_new_config(client["core"], conf_s, cfg)
    if not ok_v:
        lg.error(f"активация: конфиг одиночного сервера невалиден ({err_v}) — "
                 "рабочее ядро НЕ трогаю")
        return {"ok": False, "switched": switched, "rtt": 0,
                "msg": f"invalid_config: {err_v}"}
    socks_port = int(client.get("socks_port", DEFAULT_SOCKS_PORT))
    live_sha = _cfg_sha(client["cfg"]) if os.path.isfile(client["cfg"]) else ""
    if live_sha and _conf_sha(conf_s) == live_sha and _link_ok(cfg, socks_port):
        lg.info("Активация: конфиг совпадает с работающим — перезапуска нет")
        return {"ok": True, "switched": switched, "rtt": 0, "msg": "already_running"}
    if not _write_json(client["cfg"], conf_s):
        return {"ok": False, "switched": switched, "rtt": 0, "msg": "config_write_failed"}
    if restart_core(client, cfg):
        ok, rtt = wait_and_probe(client, attempts=6, gap=2.0)
        if ok:
            lg.info(f"Активация OK: {active_id} (rtt={rtt} мс)")
            return {"ok": True, "switched": switched, "rtt": rtt, "msg": "activated"}
    lg.warn("Связи нет — пробую аварийный состав (last-good)")
    if _emergency_restore(client, cfg, int(client.get("socks_port", DEFAULT_SOCKS_PORT))):
        return {"ok": True, "switched": switched, "rtt": 0, "msg": "emergency_ok"}
    return {"ok": False, "switched": switched, "rtt": 0, "msg": "activate_failed"}


# ══════════════════════════════════════════ сторож (guard) ═══
def guard_cycle(client: dict, cfg: dict, probe_timeout: float = 6.0) -> bool:
    """Быстрый самоконтроль: порт жив? иначе failover по [AUTO]-серверам."""
    if _mode_of(client, cfg) == "v2rayn":
        return _v2().guard_cycle(client, cfg, probe_timeout=probe_timeout)
    lg = log.get_logger()
    ok, rtt = probe_client(client, timeout=probe_timeout)
    if ok:
        return True
    port = client.get("socks_port", DEFAULT_SOCKS_PORT)
    lg.warn(f"Порт {port} не отвечает ({rtt} мс); запускаю failover…")

    if bool(cfg.get("watch", {}).get("balancer_mode", False)):
        if ensure_balancer(client, cfg,
                           n=int(cfg["watch"].get("balancer_pool_n", 10)), force=True):
            ok_b, rtt_b = probe_client(client, timeout=8)
            if ok_b:
                lg.info(f"Guard OK: балансировщик дал связь (rtt={rtt_b} мс)")
                return True

    cands = []
    for r in read_db_rows(client.get("db", "")):
        if not (r.get("Remarks") or "").startswith(AUTO_TAG):
            continue
        s = row_to_server(r)
        if s is not None:
            cands.append(s)
    try:
        cands = bl_filter(cfg, cands)
    except Exception:
        pass
    lg.info(f"Failover: [AUTO]-кандидатов: {len(cands)}")
    if cands:
        import xraytest as XT
        pool = XT.PortPool(int(cfg["test"].get("port_start", 10810)),
                           int(cfg["test"].get("workers", 8)) * 2 + 4)
        try:
            cands = XT.tcp_prefilter(cands, cfg)
        except Exception:
            pass
        for s in cands:
            try:
                XT.test_rtt(pool, client["core"], s, 6)
            except Exception:
                pass
        alive = [s for s in cands if getattr(s, "ok", False)]
        alive.sort(key=lambda s: (s.rtt_ms or 9999, -(s.speed_kbps or 0)))
        lg.info(f"Failover: живых [AUTO]: {len(alive)}")
        for s in alive[:8]:
            conf_f = build_single_config(client, s, cfg)
            if conf_f is None:
                continue
            ok_v, err_v = _validate_new_config(client["core"], conf_f, cfg)
            if not ok_v:
                lg.warn(f"Failover: конфиг {s.address}:{s.port} невалиден "
                        f"({err_v}) — пробую следующий")
                continue
            if not _write_json(client["cfg"], conf_f):
                continue
            if not restart_core(client, cfg):
                continue
            ok2, rtt2 = wait_and_probe(client, attempts=5, gap=2.0)
            if ok2:
                sid = getattr(s, "_row_index_id", None) or getattr(s, "index_id", None)
                if sid:
                    write_active_id(client["cfg"], sid)
                lg.info(f"Failover OK: переключились на {s.address}:{s.port} "
                        f"(rtt={rtt2} мс)")
                return True
            try:
                bl_add(cfg, getattr(s, "_row_index_id", None) or getattr(s, "index_id", ""),
                       60, "failover_no_connect")
            except Exception:
                pass
    else:
        lg.warn("Failover: нет [AUTO]-серверов в сторе")

    if _emergency_restore(client, cfg, int(port)):
        return True
    # ПОЧЕМУ не вышло — В СОСТОЯНИЕ, а не только в журнал: аварийный конфиг не
    # поднялся, и в отчёте `core_reason` должен отвечать на «почему 10808
    # закрыт» фактом (нет бинаря / нет конфига / порт держит чужой / ядро упало).
    try:
        _set_core_reason(cfg, core_diag(client, cfg, int(port)))
        lg.error("Guard: ПРИЧИНА невосстановленной связи: %s" % _LAST_CORE_REASON)
    except Exception:
        pass
    lg.error("Guard: восстановить связь не удалось")
    return False


# ══════════════════════════════════════════ стор состояния + hotcache ═══
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

    def save(self, merge: bool = True):
        """Атомарная запись состояния.

        ``merge=True`` не теряет ключи, которые параллельно записал другой
        экземпляр StateStore (в цикле состояния живут два: свой в run_cycle и
        свой в ensure_balancer) — без этого «отпечаток» пула и его подпись,
        записанные при подъёме балансировщика, затирались финальным
        ``save()`` цикла, и заморозка пула переставала работать.
        """
        data = dict(self.data)
        if merge and os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    disk = json.load(f)
                for k, v in (disk or {}).items():
                    if k not in data:
                        data[k] = v
            except Exception:
                pass
        tmp = self.path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        # Запись состояния идёт из НЕСКОЛЬКИХ потоков супервизора (watch/guard/
        # update). Общий путь ``state.json.tmp`` + ``os.replace`` давала гонку:
        # «[WinError 5] Отказано в доступе: state.json.tmp -> state.json» (реальный
        # случай 19.10.2026 — подпись пула/заморозка терялись). Пишем в свой
        # временный файл и повторяем замену при занятости.
        last = None
        for attempt in range(6):
            try:
                os.replace(tmp, self.path)
                last = None
                break
            except Exception as e:
                last = e
                time.sleep(0.05 * (attempt + 1))
        if last is not None:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise last
        self.data = data


def update_hotcache(client: dict, cfg: dict, servers: list):
    """Сохраняет проверенно-живые серверы в hotcache (быстрый failover)."""
    st = StateStore(cfg)
    hot_n = int(cfg.get("watch", {}).get("hotcache_n", 5))
    if not servers or hot_n <= 0:
        return
    alive = [s for s in servers
             if getattr(s, "ok", False) and getattr(s, "rtt_ms", 0) > 0
             and (getattr(s, "speed_kbps", 0) or 0) > 0]
    alive.sort(key=lambda s: (-(s.speed_kbps or 0), s.rtt_ms))
    import dataclasses
    cache = []
    for s in alive[:hot_n]:
        idx = getattr(s, "_row_index_id", None) or getattr(s, "index_id", "")
        try:
            full = dataclasses.asdict(s)
        except Exception:
            full = {}
        cache.append({
            "address": s.address, "port": s.port, "protocol": s.protocol,
            "remark": getattr(s, "remark", "") or "",
            "rtt_ms": s.rtt_ms, "speed_kbps": s.speed_kbps,
            "index_id": idx, "ts": time.time(),
            # Признак «YouTube через узел открывается». У Server это поле
            # динамическое и в asdict не попадает — храним отдельно, чтобы при
            # сборке пула отсеять узлы-«глушители» YouTube.
            "youtube_ok": getattr(s, "youtube_ok", None),
            "server": full,          # полные данные для быстрого восстановления
        })
    st.data["hotcache"] = cache
    st.save()
    log.get_logger().info(f"hotcache обновлён: {len(cache)} серверов")


def hotcache_switch(client: dict, cfg: dict, hot_n: int = 5,
                    hot_ttl: float = 600.0, probe_timeout: float = 5.0) -> str:
    """Быстрое переключение на сервер из hotcache (без полного теста)."""
    lg = log.get_logger()
    st = StateStore(cfg)
    cache = st.data.get("hotcache") or []
    now = time.time()
    fresh = [e for e in cache if (now - float(e.get("ts", 0) or 0)) < hot_ttl
             and e.get("speed_kbps")]
    if not fresh:
        return ""
    servers = [s for s in (_row_to_server_from_cache(e) for e in fresh[:hot_n]) if s]
    if not servers:
        return ""
    if ensure_balancer(client, cfg, n=max(3, len(servers)), force=True, fast_ok=True):
        ok, rtt = probe_client(client, timeout=probe_timeout)
        if ok:
            lg.info(f"hotcache-переключение удалось (rtt={rtt} мс)")
            return "hotcache_ok"
    return ""


# ══════════════════════════════════════════ диспетчер режимов ═══
_V2 = None


def _v2():
    """Ленивая загрузка бэкенда v2rayN (работа через базу/ядро v2rayN)."""
    global _V2
    if _V2 is None:
        import clientwrite_v2rayn as _m
        _V2 = _m
    return _V2


def _is_v2rayn_path(path: str) -> bool:
    return bool(path) and os.path.basename(str(path)).lower().startswith("guinconfig")


def _mode_of(client: dict = None, cfg: dict = None) -> str:
    if client and client.get("mode"):
        return str(client["mode"]).lower()
    if cfg:
        return str(cfg.get("client", {}).get("mode") or "standalone").lower()
    return "standalone"


def read_db_rows(db_path: str) -> list:
    """Чтение серверов: .db -> SQLite v2rayN, иначе наш JSON-store."""
    if str(db_path).lower().endswith(".db"):
        return _v2().read_db_rows(db_path)
    return _sa_read_db_rows(db_path)


def write_auto_group(db_path: str, servers: list, update_stats: bool = True) -> int:
    if str(db_path).lower().endswith(".db"):
        return _v2().write_auto_group(db_path, servers, update_stats)
    return _sa_write_auto_group(db_path, servers, update_stats)


def read_active_id(cfg_path: str):
    if _is_v2rayn_path(cfg_path):
        return _v2().read_active_id(cfg_path)
    return _sa_read_active_id(cfg_path)


def write_active_id(cfg_path: str, index_id: str) -> None:
    if _is_v2rayn_path(cfg_path):
        return _v2().write_active_id(cfg_path, index_id)
    return _sa_write_active_id(cfg_path, index_id)


def write_bin_config(client: dict, server, cfg: dict) -> bool:
    if _mode_of(client, cfg) == "v2rayn":
        return _v2().write_bin_config(client, server, cfg)
    return write_core_config(client, server, cfg)
