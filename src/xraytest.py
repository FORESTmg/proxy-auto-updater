# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE or <https://www.gnu.org/licenses/gpl-3.0.html>.
"""Тестирование кандидатов через ОТДЕЛЬНЫЕ экземпляры ядра xray (GPL-3.0).

FOREST's Proxy Auto-updater. Рабочее ядро (клиентский порт) при тестах НЕ
трогается: каждый кандидат поднимается как одноразовый xray с socks-inbound
на своём порту, через который меряются RTT и скорость.

Временные конфиги тестовых ядер имеют префикс ``forest_test_`` — только их
и снимает cleanup_test_kernels (чужие процессы xray, например ядро v2rayN,
не затрагиваются).
"""
import concurrent.futures as cf
import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time

import log
import tray_status as TS

RTT_URL = "https://www.gstatic.com/generate_204"
SPEED_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"

TEST_CFG_PREFIX = "forest_test_"


def _no_window() -> int:
    return (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))


def outbound_json(s):
    """Server -> xray outbound (streamSettings).

    ВАЖНО: v2rayN хранит tcp как 'raw', xray ожидает 'tcp'.
    """
    net = "tcp" if s.network == "raw" else s.network
    ss = {"network": net}
    if s.stream_security == "tls":
        srv = s.sni or s.host or s.address
        tl = {"serverName": srv, "fingerprint": s.fingerprint or "",
              "allowInsecure": bool(s.allow_insecure), "alpn": []}
        if s.alpn:
            tl["alpn"] = [x.strip() for x in s.alpn.split(",") if x.strip()]
        ss["security"] = "tls"
        ss["tlsSettings"] = tl
    elif s.stream_security == "reality":
        ss["security"] = "reality"
        ss["realitySettings"] = {
            "showNotif": False,
            "serverName": s.sni or s.host or s.address,
            "fingerprint": s.fingerprint or "chrome",
            "publicKey": s.public_key,
            "shortId": s.short_id or "",
            "spiderX": s.spider_x or "",
        }
    else:
        ss["security"] = "none"

    if s.network == "ws":
        ws = {"path": s.path or "/"}
        if s.host:
            ws["headers"] = {"Host": s.host}
        ss["wsSettings"] = ws
    elif s.network == "httpupgrade":
        ss["httpupgradeSettings"] = {"path": s.path or "/", "host": s.host or ""}
    elif s.network == "xhttp":
        ss["xhttpSettings"] = {"path": s.path or "/", "host": s.host or ""}
    elif s.network == "grpc":
        ss["grpcSettings"] = {"serviceName": s.path or "", "authority": s.host or ""}
    elif s.network == "kcp":
        ss["kcpSettings"] = {"header": s.header_type or "none", "seed": s.kcp_seed or ""}
    elif s.network == "raw":
        if s.header_type == "http":
            ss["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {"path": [s.path or "/"],
                                "headers": {"Host": [s.host or s.address]}},
                }
            }

    # ВАЖНО: xray для vless/vmess использует settings.vnext (адрес строго внутри).
    if s.protocol == "vless":
        settings = {"vnext": [{
            "address": s.address, "port": s.port,
            "users": [{"id": s.password, "encryption": "none", "flow": s.flow or ""}],
        }]}
        tag = "vless"
    elif s.protocol == "vmess":
        settings = {"vnext": [{
            "address": s.address, "port": s.port,
            "users": [{"id": s.password, "alterId": int(s.alter_id or 0),
                       "security": s.method or "auto"}],
        }]}
        tag = "vmess"
    elif s.protocol == "trojan":
        settings = {"servers": [{
            "address": s.address, "port": s.port, "password": s.password,
            "flow": s.flow or "",
        }]}
        tag = "trojan"
    elif s.protocol == "ss":
        settings = {"servers": [{
            "address": s.address, "port": s.port, "method": s.method,
            "password": s.password,
        }]}
        tag = "shadowsocks"
    else:
        return None

    return {
        "tag": f"{tag}-{s.address}-{s.port}",
        "protocol": tag,
        "settings": settings,
        "streamSettings": ss,
    }


def instance_config(port: int, outbound: dict) -> dict:
    return {
        "log": {"loglevel": "none"},
        "inbounds": [{
            "tag": f"in{port}",
            "port": port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": False, "auth": "noauth"},
        }],
        "outbounds": [outbound],
    }


class TestInstance:
    """Одноразовый xray-инстанс."""

    def __init__(self, core: str, port: int, outbound: dict):
        self.core = core
        self.port = port
        self.outbound = outbound
        self.proc = None
        self.log_path = None

    def start(self, wait: float = 4.0) -> bool:
        if not self.core or not os.path.isfile(self.core):
            return False
        cfg = instance_config(self.port, self.outbound)
        fd, self.log_path = tempfile.mkstemp(suffix=".xlog", prefix=TEST_CFG_PREFIX)
        os.close(fd)
        cfg_path = self.log_path + ".json"
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        core_dir = os.path.dirname(self.core)
        try:
            logf = open(self.log_path, "w", encoding="utf-8")
            self.proc = subprocess.Popen(
                [self.core, "run", "-c", cfg_path],
                cwd=core_dir, stdout=logf, stderr=subprocess.STDOUT,
                creationflags=_no_window(),
            )
        except Exception:
            self.proc = None
            return False
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            if self._port_open():
                return True
            time.sleep(0.1)
        self.stop()
        return False

    def _port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                return True
        except Exception:
            return False

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        for p in (self.log_path, self.log_path + ".json"):
            try:
                if p:
                    os.unlink(p)
            except Exception:
                pass


class PortPool:
    """Пул портов: каждый тест берёт уникальный (FIFO — иначе порт в TIME_WAIT)."""

    def __init__(self, start: int, count: int):
        import collections
        self._free = collections.deque(range(start, start + count))
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            return self._free.popleft() if self._free else None

    def release(self, port):
        with self._lock:
            self._free.append(port)


def _curl_probe(port: int, url: str, timeout: int) -> tuple:
    cmd = ["curl", "-s", "-o", os.devnull, "-w", "%{http_code} %{time_total}",
           "--max-time", str(timeout), "-x", f"socks5h://127.0.0.1:{port}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10,
                           creationflags=_no_window())
        out = r.stdout.decode("utf-8", "replace").strip()
        parts = out.split()
        code = int(parts[0]) if parts and parts[0].isdigit() else 0
        tt = float(parts[1]) if len(parts) > 1 else 999.0
        return code, tt
    except Exception:
        return 0, 999.0


# ── мульти-доменный TLS-гейт ─────────────────────────────────────────────
# Отказы на живом пуле приходили пачками и валили ОДНОВРЕМЕННО разные домены
# (gstatic, wikipedia, ytimg) с подписью `curl rc=35 schannel: failed to
# receive handshake` за 40-60 мс. Это не проблема одного домена, а узел,
# который вообще не проходит TLS-рукопожатие (или рвёт его на части хостов).
# Поэтому узел проверяется РЕАЛЬНЫМ рукопожатием к нескольким разным доменам:
# мало получить ответ от youtube/gstatic — нужен TLS к разным точкам.
TLS_URLS: list = []
TLS_MAX_S = 3.0        # порог ответа одного рукопожатия (с)
TLS_MIN_OK = 2         # сколько доменов из списка обязаны ответить


def tls_measure(port: int, urls: list = None, timeout: float = None) -> list:
    """Сырые результаты рукопожатий через узел на ``port``: список строк.

    Каждая строка: ``{host, code, tt, ssl, rc, err}``. Отдельная функция нужна,
    чтобы вызывающий мог СРАВНИТЬ узлы между собой (медиана по пулу), а не
    браковать каждый по абсолютному порогу: медленный узел — не обязательно
    плохой, если весь пул сейчас медленный на этом домене.
    """
    urls = list(urls if urls is not None else TLS_URLS)
    if not urls:
        return []
    tmo = float(timeout if timeout is not None else (TLS_MAX_S + 1.0))
    rows = []
    for u in urls:
        host = u.split("/")[2] if "//" in u else u
        cmd = ["curl", "-s", "-S", "-o", os.devnull,
               "-w", "%{http_code}|%{time_total}|%{ssl_verify_result}",
               "--max-time", f"{tmo:.1f}", "-x", f"socks5h://127.0.0.1:{port}", u]
        code, tt, sslv, rc, err = 0, 999.0, 1, 0, ""
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=tmo + 4,
                               creationflags=_no_window())
            rc = r.returncode
            out = r.stdout.decode("utf-8", "replace").strip().split("|")
            code = int(out[0]) if out and out[0].isdigit() else 0
            tt = float(out[1]) if len(out) > 1 else 999.0
            sslv = int(float(out[2])) if len(out) > 2 and out[2] else 1
            err = r.stderr.decode("utf-8", "replace").strip()[:120]
        except Exception as e:
            rc, err = -1, f"{e}"[:120]
        rows.append({"host": host, "url": u, "code": code, "tt": tt,
                     "ssl": sslv, "rc": rc, "err": err})
    return rows


def tls_evaluate(rows: list, max_s: float = None, min_ok: int = None,
                 hard_s: float = None) -> dict:
    """Оценка узла по результатам :func:`tls_measure`.

    Возвращает ``{ok, kind, why, good, total, worst_s, slow_s, slow_host}``.
    ``kind`` строго разделяет ПРИЧИНУ:

    * ``reset`` / ``unreachable`` / ``mitm`` — ЖЁСТКИЙ брак (узел рвёт или не
      устанавливает TLS): в пул нельзя, лечится только исключением узла;
    * ``few`` — мало успешных рукопожатий (тоже жёсткий, но не «сброс»);
    * ``slow`` — рукопожатия состоялись, но медленнее порога ``max_s``. Это НЕ
      доказательство плохого узла: при общем замедлении линии медленными будут
      все. Решение (медиана по пулу) принимает вызывающий;
    * ``ok`` — всё в пороге.
    """
    lim = float(max_s if max_s is not None else TLS_MAX_S)
    need = max(1, int(min_ok if min_ok is not None else TLS_MIN_OK))
    hard = float(hard_s if hard_s is not None else max(lim * 3.0, 10.0))
    good, worst = 0, 0.0
    slow_s, slow_host = 0.0, ""
    for r in rows:
        code, tt, sslv, rc = r.get("code", 0), float(r.get("tt", 999.0)), \
            int(r.get("ssl", 1)), int(r.get("rc", 0))
        host = r.get("host", "?")
        if code != 0 and sslv == 0:
            if tt <= lim:
                good += 1
                worst = max(worst, tt)
            elif tt <= hard:
                if tt > slow_s:
                    slow_s, slow_host = tt, host
            else:
                # Совсем невероятное время рукопожатия — это уже не «медленно».
                return {"ok": False, "kind": "slow", "why": f"tls_slow_{tt:.1f}s_{host}",
                        "good": good, "total": len(rows), "worst_s": round(max(worst, tt), 2),
                        "slow_s": round(tt, 2), "slow_host": host}
            continue
        # Мгновенный сброс рукопожатия (rc=35 / пустой ответ < 200 мс) —
        # узел-«глушитель»: в пул его нельзя ни при каких условиях.
        if rc == 35 or (code == 0 and tt < 0.2):
            return {"ok": False, "kind": "reset", "why": f"tls_reset_{host}",
                    "good": good, "total": len(rows), "worst_s": round(worst, 2),
                    "slow_s": round(slow_s, 2), "slow_host": slow_host}
        if code == 0:
            return {"ok": False, "kind": "unreachable", "why": f"tls_unreachable_{host}",
                    "good": good, "total": len(rows), "worst_s": round(worst, 2),
                    "slow_s": round(slow_s, 2), "slow_host": slow_host}
        if sslv != 0:
            return {"ok": False, "kind": "mitm", "why": f"tls_mitm_{host}",
                    "good": good, "total": len(rows), "worst_s": round(worst, 2),
                    "slow_s": round(slow_s, 2), "slow_host": slow_host}
    if slow_s:
        return {"ok": False, "kind": "slow",
                "why": f"tls_slow_{slow_s:.1f}s_{slow_host}", "good": good,
                "total": len(rows), "worst_s": round(max(worst, slow_s), 2),
                "slow_s": round(slow_s, 2), "slow_host": slow_host}
    if good < need:
        return {"ok": False, "kind": "few", "why": f"tls_only_{good}of{len(rows)}",
                "good": good, "total": len(rows), "worst_s": round(worst, 2),
                "slow_s": 0.0, "slow_host": ""}
    return {"ok": True, "kind": "ok", "why": "", "good": good,
            "total": len(rows), "worst_s": round(worst, 2), "slow_s": 0.0,
            "slow_host": ""}


def tls_gate(port: int, urls: list = None, timeout: float = None,
             max_s: float = None, min_ok: int = None) -> tuple:
    """Реальные TLS-рукопожатия к РАЗНЫМ доменам через узел на ``port``.

    Возвращает ``(ok, why, good, total)``. Рукопожатие считается состоявшимся,
    если пришёл любой HTTP-код (включая 403/404 — это уже ответ по TLS),
    ``ssl_verify_result == 0`` и ответ уложился в порог. Код 000/rc=35 —
    мгновенный сброс рукопожатия: узел бракуется сразу, без добора минимума.
    """
    urls = list(urls if urls is not None else TLS_URLS)
    if not urls:
        return True, "", 0, 0
    ev = tls_evaluate(tls_measure(port, urls=urls, timeout=timeout),
                      max_s=max_s, min_ok=min_ok)
    return ev["ok"], ev["why"], ev["good"], ev["total"]


def tls_store_path(cfg: dict) -> str:
    return os.path.join(cfg["paths"]["state_dir"], "tls_ok.json")


_TLS_LOCK = None


def _tls_lock():
    global _TLS_LOCK
    if _TLS_LOCK is None:
        _TLS_LOCK = threading.Lock()
    return _TLS_LOCK


def tls_record(cfg: dict, s, ok: bool, why: str = "") -> None:
    """Запомнить результат TLS-гейта узла (для отбора в пул без перепроверки)."""
    if not cfg or not getattr(s, "address", ""):
        return
    try:
        with _tls_lock():          # запись идёт из параллельных потоков отбора
            p = tls_store_path(cfg)
            data = {}
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f) or {}
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            data[f"{s.address}:{s.port}"] = {"ts": time.time(), "ok": bool(ok),
                                            "why": (why or "")[:60]}
            now = time.time()
            data = {k: v for k, v in data.items()
                    if isinstance(v, dict) and now - float(v.get("ts", 0) or 0) < 86400}
            if len(data) > 900:
                data = dict(sorted(data.items(),
                                   key=lambda kv: -float(kv[1].get("ts", 0) or 0))[:900])
            tmp = f"{p}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)
    except Exception as e:
        log.get_logger().warn(f"tls_record: {e}")


def _tcp_probe(s, timeout: float = 1.2) -> bool:
    """Быстрая проверка доступности host:port (DNS+connect). Без прокси."""
    try:
        with socket.create_connection((s.address, s.port), timeout=timeout):
            return True
    except Exception:
        return False


def tcp_prefilter(servers: list, cfg: dict) -> list:
    """Отсеивает host:port, к которым нет TCP-доступа (мёртвые/забаненные)."""
    lg = log.get_logger()
    workers = int(cfg["test"].get("workers", 8))
    alive = []
    with cf.ThreadPoolExecutor(max_workers=workers * 4) as ex:
        futs = {ex.submit(_tcp_probe, s): s for s in servers}
        for f in cf.as_completed(futs):
            s = futs[f]
            try:
                ok = f.result()
            except Exception:
                ok = False
            if ok:
                alive.append(s)
    lg.info(f"TCP-предфильтр: {len(alive)}/{len(servers)} доступны по сети")
    return alive


def test_rtt(pool: PortPool, core: str, s, timeout: int = 8, repeats: int = 1) -> None:
    """RTT-тест: ОДИН инстанс на сервер, несколько curl-замеров через него."""
    lg = log.get_logger()
    inst = None
    port = None
    try:
        ob = outbound_json(s)
        if ob is None:
            s.ok = False
            s.fail_reason = "unsupported"
            return
        samples = []
        started = False
        for attempt in range(3):
            port = pool.acquire()
            if port is None:
                break
            inst = TestInstance(core, port, ob)
            if inst.start():
                started = True
                break
            inst.stop()
            inst = None
            pool.release(port)
            time.sleep(0.3)
        if not started:
            s.ok = False
            s.fail_reason = "core_start_failed"
            return
        for attempt in range(max(1, repeats)):
            code, tt = _curl_probe(port, RTT_URL, timeout)
            if code == 204:
                samples.append(tt * 1000)
            else:
                s.ok = False
                s.fail_reason = f"http_{code}"
                return
            if attempt < repeats - 1:
                time.sleep(0.5)
        if samples:
            s.ok = True
            s.rtt_ms = round(sum(samples) / len(samples), 1)
            s._rtt_jitter = round(max(samples) - min(samples), 1)
            s.fail_reason = ""
    finally:
        if inst:
            inst.stop()
        if port is not None:
            pool.release(port)


def test_speed(pool: PortPool, core: str, s, timeout: int = 10,
               bytes_n: int = 5242880, url: str = None, rounds: int = 2,
               cfg: dict = None) -> None:
    if not s.ok:
        return
    port = pool.acquire()
    if port is None:
        return
    inst = None
    try:
        ob = outbound_json(s)
        inst = TestInstance(core, port, ob)
        if not inst.start():
            s.ok = False
            s.fail_reason = "core_start_failed"
            return
        # ГЕЙТ (если задан test.gate_urls): узел может везти трафик, но получать
        # 403 на конкретных доменах (типично для российских выходов).
        for gate in list(GATE_URLS):
            gcode = 0
            gtt = 0.0
            for _try in range(2):
                try:
                    gcode, gtt = _curl_probe(port, gate, 12)
                except Exception:
                    gcode, gtt = 0, 0.0
                if gcode != 0:
                    break
            if gcode in (0, 403):
                s.ok = False
                s.fail_reason = "gate_403" if gcode == 403 else "gate_unreachable"
                return
            # Медленный, но формально успешный ответ = ДЕГРАДАЦИЯ: браузер на
            # такой ноде висит («сервер недоступен»), пока соединение не отвалится.
            if GATE_MAX_S > 0 and gtt > GATE_MAX_S:
                s.ok = False
                s.fail_reason = f"gate_slow_{gtt:.1f}s"
                return
        # МУЛЬТИ-ДОМЕННЫЙ TLS-ГЕЙТ: узел обязан пройти рукопожатие к нескольким
        # разным доменам. Гейт по одному домену ловил только его; на живом пуле
        # всплеск валил gstatic+wikipedia+ytimg одновременно.
        if TLS_URLS:
            tok, twhy, tgood, ttotal = tls_gate(port)
            if cfg is not None:
                tls_record(cfg, s, tok, twhy)
            if not tok:
                s.ok = False
                s.fail_reason = twhy or "tls_gate"
                return
            s.tls_ok = True
            s.tls_domains = tgood
        if url is None:
            url = SPEED_URL.format(bytes=bytes_n)
        speeds = []
        for _round in range(max(1, int(rounds))):
            cmd = ["curl", "-s", "-o", os.devnull,
                   "-w", "%{http_code} %{speed_download} %{time_total}",
                   "--max-time", str(timeout), "-x",
                   f"socks5h://127.0.0.1:{port}", url]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10,
                                   creationflags=_no_window())
                out = r.stdout.decode("utf-8", "replace").strip().split()
                code = int(out[0]) if out and out[0].isdigit() else 0
                bps = float(out[1]) if len(out) > 1 and out[1].isdigit() else 0.0
                if code == 200 and bps > 0:
                    speeds.append(bps / 1024.0)
                else:
                    s.ok = False
                    s.fail_reason = f"speed_http_{code}"
                    return
            except Exception:
                s.ok = False
                s.fail_reason = "speed_timeout"
                return
        if speeds:
            s.speed_kbps = round(min(speeds), 1)
    finally:
        if inst:
            inst.stop()
        pool.release(port)


LAST_RTT_OK = []   # все серверы, прошедшие RTT в последнем прогоне (для резерва)

# Домены-«гейты»: заполняется из test.gate_urls. Пустой список = гейт выключен.
GATE_URLS: list = []
# Порог ОТВЕТА гейта (с): узел может отвечать «когда-нибудь», но 5-12 с на пробу
# = деградация (браузер на такой ноде показывает «сервер недоступен», пока
# соединение висит). Медленный ответ гейта бракует узел так же, как 000/403.
GATE_MAX_S = 2.5
# Порог ответа YouTube в финальной проверке (с): медленная нода — не рабочая.
YOUTUBE_MAX_TT = 2.5


def cleanup_test_kernels():
    """Снимает ОСИРОТЕВШИЕ ТЕСТОВЫЕ ядра xray (temp-конфиги forest_test_*).

    Чужие процессы xray (например, ядро v2rayN или рабочее ядро нашей
    программы) НЕ затрагиваются: фильтр строго по имени нашего временного
    конфига.

    POSIX: обходим /proc и снимаем только процессы с именем xray, у которых
    в командной строке есть префикс нашего тестового конфига (без pgrep -f,
    который мог бы совпасть по чужому процессу).
    """
    lg = log.get_logger()
    if os.name != "nt":
        killed = []
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    if os.path.basename(os.readlink(f"/proc/{entry}/exe")) not in ("xray", "v2ray"):
                        continue
                    with open(f"/proc/{entry}/cmdline", "rb") as f:
                        cmd = f.read().decode("utf-8", "replace")
                except Exception:
                    continue
                if TEST_CFG_PREFIX in cmd:
                    try:
                        os.kill(int(entry), signal.SIGKILL)
                        killed.append(entry)
                    except Exception:
                        pass
        except Exception as e:
            lg.warn(f"cleanup_test_kernels: {e}")
            return 0
        if killed:
            lg.warn(f"cleanup_test_kernels: снято тестовых ядер xray: {len(killed)}")
        return len(killed)
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='xray.exe'\" | "
              "Where-Object { $_.CommandLine -like '*" + TEST_CFG_PREFIX + "*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "
              "Write-Output $_.ProcessId }")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=40, text=True, errors="replace",
                           creationflags=_no_window())
        killed = [x for x in (r.stdout or "").split() if x.strip().isdigit()]
        if killed:
            lg.warn(f"cleanup_test_kernels: снято тестовых ядер xray: {len(killed)}")
        return len(killed)
    except Exception as e:
        lg.warn(f"cleanup_test_kernels: {e}")
        return 0


def run_tests(servers: list, cfg: dict, core: str) -> list:
    """Фаза 1: RTT на всех. Фаза 2: скорость на топ-RTT. Возвращает здоровых."""
    global GATE_URLS, GATE_MAX_S, YOUTUBE_MAX_TT, TLS_URLS, TLS_MAX_S, TLS_MIN_OK
    lg = log.get_logger()
    try:
        GATE_URLS = list((cfg.get("test", {}) or {}).get("gate_urls", []) or [])
        try:
            TLS_URLS = list((cfg.get("test", {}) or {}).get("tls_urls", []) or [])
            TLS_MAX_S = float((cfg.get("test", {}) or {}).get("tls_max_s", 3.0) or 3.0)
            TLS_MIN_OK = int((cfg.get("test", {}) or {}).get("tls_min_ok", 2) or 2)
        except Exception:
            TLS_URLS, TLS_MAX_S, TLS_MIN_OK = [], 3.0, 2
        if TLS_URLS:
            lg.info(f"TLS-гейт: {len(TLS_URLS)} домен(ов), порог {TLS_MAX_S:.1f} с, "
                    f"минимум {TLS_MIN_OK} рукопожатий")
        try:
            GATE_MAX_S = float((cfg.get("test", {}) or {}).get(
                "gate_max_s", GATE_MAX_S) or 0)
        except Exception:
            GATE_MAX_S = 2.5
        try:
            YOUTUBE_MAX_TT = float((cfg.get("test", {}) or {}).get(
                "youtube_max_tt_s", YOUTUBE_MAX_TT) or 0)
        except Exception:
            YOUTUBE_MAX_TT = 2.5
        if GATE_URLS:
            lg.info(f"Гейт включён: {len(GATE_URLS)} домен(ов), порог ответа "
                    f"{GATE_MAX_S:.1f} с; YouTube-порог {YOUTUBE_MAX_TT:.1f} с")
    except Exception:
        GATE_URLS = []
    try:
        cleanup_test_kernels()
    except Exception:
        pass
    workers = int(cfg["test"].get("workers", 6))
    start = int(cfg["test"].get("port_start", 10810))
    rtt_to = int(cfg["test"].get("rtt_timeout", 8))
    spd_to = int(cfg["test"].get("speed_timeout", 10))
    top_n = int(cfg["test"].get("top_for_speed", 60))
    min_spd = float(cfg["test"].get("min_speed_kbps", 200))
    max_rtt = float(cfg["test"].get("max_rtt_ms", 2000))

    pool = PortPool(start, workers * 2 + 8)

    lg.info(f"Тест RTT: {len(servers)} серверов, {workers} потоков...")
    for s in servers:
        s.ok = False
    if int(cfg["test"].get("tcp_prefilter", 1)):
        servers = tcp_prefilter(servers, cfg)
        if not servers:
            lg.warn("Все кандидаты отсеяны TCP-предфильтром")
            return []
    lg.info(f"RTT-тест: {len(servers)} прошли предфильтр")
    repeats = int(cfg.get("test", {}).get("rtt_repeats", 1))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(test_rtt, pool, core, s, rtt_to, repeats): s for s in servers}
        done = 0
        for f in cf.as_completed(futs):
            done += 1
            if done % 50 == 0:
                lg.info(f"  RTT: {done}/{len(servers)}")
    ok = [s for s in servers if s.ok]
    try:
        LAST_RTT_OK[:] = ok
    except Exception:
        pass
    bad = len(servers) - len(ok)
    from collections import Counter
    reasons = Counter(s.fail_reason for s in servers if not s.ok)
    lg.info(f"RTT пройдено: {len(ok)}, отсеяно: {bad} | причины: {dict(reasons)}")
    TS.advance(cfg, 0, "Замер скорости лучших")

    jitter_limit = float(cfg["test"].get("max_jitter_ms", 0))
    if jitter_limit > 0:
        stable = [s for s in ok if getattr(s, "_rtt_jitter", 0) <= jitter_limit]
        lg.info(f"Стабильных (джиттер <= {jitter_limit:.0f} мс): {len(stable)}/{len(ok)}")
        ok = stable

    ok = [s for s in ok if s.rtt_ms <= max_rtt]
    ok.sort(key=lambda s: s.rtt_ms)
    try:
        import netfilters as _NF
        _cap = int(cfg.get("diversity", {}).get("per_subnet_cap", 1) or 0)
        top = _NF.pick_diverse(ok, top_n, cap=_cap)
        lg.info(f"Тест скорости: отобрано {len(top)} из {len(ok)} "
                f"(подсетей: {len(_NF.subnet_stats(top))}, кап на подсеть: {_cap or 'без капа'})")
    except Exception as _e:
        top = ok[:top_n]
        lg.warn(f"Отбор по подсетям не сработал ({_e}) — беру топ-{top_n} по RTT")
    TS.advance(cfg, 1, "Финальная проверка RTT")
    spd_url = cfg.get("test", {}).get("speed_url") or SPEED_URL.format(
        bytes=int(cfg["test"].get("speed_bytes", 5242880)))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for s in top:
            ex.submit(test_speed, pool, core, s, spd_to,
                      int(cfg["test"].get("speed_bytes", 5242880)), spd_url,
                      int(cfg["test"].get("speed_rounds", 2)), cfg)
    _wrong = [s for s in top if not s.ok]
    _slow = [s for s in top if s.ok and s.speed_kbps < min_spd]
    _rs = Counter(s.fail_reason for s in _wrong)
    lg.info(f"Скорость: проверено {len(top)}, отсеяно {len(_wrong)}, медленных {len(_slow)}")
    if _rs:
        lg.info(f"  причины отсева: {dict(_rs)}")
    if _slow:
        _sp = sorted((s.speed_kbps for s in _slow), reverse=True)
        lg.info(f"  живы, но ниже порога {min_spd:.0f} КБ/с: {len(_slow)} "
                f"(лучшие: {', '.join(f'{v:.0f}' for v in _sp[:5])} КБ/с)")
    healthy = [s for s in ok if s.ok and s.speed_kbps >= min_spd]
    if healthy:
        _hs = sorted((s.speed_kbps for s in healthy), reverse=True)
        lg.info(f"  прошедших порог: {len(_hs)} (макс {_hs[0]:.0f}, "
                f"медиана {_hs[len(_hs) // 2]:.0f} КБ/с)")
    lg.info(f"Рабочих (скорость >= {min_spd:.0f} КБ/с): {len(healthy)}")

    healthy = final_verify(healthy, cfg, core)
    return healthy


def retest_rtt(servers: list, cfg: dict, core: str) -> None:
    """Повторный RTT небольшого списка (стабильность топ-10)."""
    workers = max(2, min(6, len(servers)))
    pool = PortPool(int(cfg["test"].get("port_start", 10810)), workers * 2 + 4)
    for s in servers:
        test_rtt(pool, core, s, int(cfg["test"].get("rtt_timeout", 8)))


def verify_speed(servers: list, cfg: dict, core: str) -> list:
    """Замер СКОРОСТИ у заданных серверов (RTT у них уже пройден)."""
    lg = log.get_logger()
    if not servers:
        return []
    workers = int(cfg["test"].get("workers", 6))
    pool = PortPool(int(cfg["test"].get("port_start", 10810)), workers * 2 + 8)
    spd_to = int(cfg["test"].get("speed_timeout", 15))
    bytes_n = int(cfg["test"].get("speed_bytes", 5242880))
    rounds = int(cfg["test"].get("speed_rounds", 1))
    min_spd = float(cfg["test"].get("min_speed_kbps", 1000))
    spd_url = cfg.get("test", {}).get("speed_url") or SPEED_URL.format(bytes=bytes_n)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for s in servers:
            ex.submit(test_speed, pool, core, s, spd_to, bytes_n, spd_url, rounds, cfg)
    good = [s for s in servers if s.ok and s.speed_kbps >= min_spd]
    lg.info(f"Проверка скорости резерва: {len(good)} из {len(servers)} прошли порог {min_spd:.0f} КБ/с")
    return good


def best_ranked(servers: list, top_n: int = 100) -> list:
    """Ранжирование: скорость (убыв.), затем RTT (возр.)."""
    return sorted(servers, key=lambda s: (-s.speed_kbps, s.rtt_ms))[:top_n]


def test_youtube(pool: PortPool, core: str, s, timeout: int = 10) -> None:
    """Проверка доступа к YouTube через тестовый xray-инстанс сервера."""
    if not s.ok:
        return
    port = pool.acquire()
    if port is None:
        return
    inst = None
    try:
        ob = outbound_json(s)
        inst = TestInstance(core, port, ob)
        if not inst.start():
            s.youtube_ok = False
            return
        code, tt = _curl_probe(port, "https://www.youtube.com", timeout)
        # Формально код 200, но ответ за 5-9 с = деградация: видео/страница не
        # откроются, браузер покажет «сервер недоступен». Такую ноду не берём.
        s.youtube_ok = code in (200, 301, 302) and \
            (YOUTUBE_MAX_TT <= 0 or tt <= YOUTUBE_MAX_TT)
        s.youtube_tt = tt
    finally:
        if inst:
            inst.stop()
        pool.release(port)


def youtube_filter(servers: list, cfg: dict, core: str) -> list:
    """Оставляет только серверы, через которые ОТКРЫВАЕТСЯ YouTube.

    Нужно резервному пути: узлы, прошедшие RTT и скорость, но рвущие TLS к
    youtube.com (типично российские выходы — google/gstatic отдают 204, а
    YouTube мгновенно сбрасывается), в пул попадать не должны, иначе leastPing
    выберет их и YouTube откажет. Резерв без этой проверки был ОСНОВНОЙ дорогой
    «глушителей» в пул.
    """
    lg = log.get_logger()
    if not servers:
        return servers
    workers = int(cfg.get("test", {}).get("workers", 8))
    pool = PortPool(int(cfg["test"].get("port_start", 10810)), workers * 2 + 8)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for s in servers:
            s.ok = True                 # test_youtube выходит при s.ok == False
            ex.submit(test_youtube, pool, core, s, 10)
    good = [s for s in servers if getattr(s, "youtube_ok", False)]
    lg.info(f"YouTube-гейт: открывается через {len(good)}/{len(servers)} узлов")
    return good


def final_verify(servers: list, cfg: dict, core: str) -> list:
    """Финальная проверка топ-N: повторный RTT + доступность YouTube."""
    lg = log.get_logger()
    if not servers:
        return servers
    workers = int(cfg["test"].get("workers", 8))
    pool = PortPool(int(cfg["test"].get("port_start", 10810)), workers * 2 + 8)
    rtt_to = int(cfg["test"].get("rtt_timeout", 8))
    top_n = int(cfg["test"].get("final_verify_top", 30))
    top = sorted(servers, key=lambda s: -s.speed_kbps)[:top_n]
    lg.info(f"Финал. проверка {len(top)}: RTT повторно + YouTube")
    for s in top:
        test_rtt(pool, core, s, rtt_to)
    top = [s for s in top if s.ok and s.rtt_ms <= float(cfg["test"].get("max_rtt_ms", 1500))]
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for s in top:
            ex.submit(test_youtube, pool, core, s, 10)
    good = [s for s in top if s.ok and getattr(s, "youtube_ok", False)]
    lg.info(f"Финальный отбор: {len(good)}/{len(top)} прошли RTT+YouTube")
    TS.advance(cfg, 2, "Подъём балансировщика…")
    return good
