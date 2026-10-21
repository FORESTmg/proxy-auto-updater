# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""Встроенный помощник интерфейса: распаковка из ОДНОГО .exe и сверка SHA-256.

Зачем
-----
Пользователь скачивает ОДИН файл — ``ForestProxyUpdater.exe``. Помощник
интерфейса (``forest-ui.exe``: значок в трее, меню, уведомления) лежит ВНУТРИ
него как данные пакета (``ui_bundle/forest-ui.exe`` + ``ui_bundle/ui-bundle.json``)
и при первом запуске распаковывается в ПОСТОЯННЫЙ каталог установки.

Почему не запускаем прямо из ``_MEIPASS``
-----------------------------------------
Onefile-пакет распаковывается во временный ``_MEIxxxx``. Помощник — отдельный
процесс, который живёт дольше того, кто его поднял: он держал бы файл в
временном каталоге, уборщик PyInstaller не смог бы снести каталог и показал бы
модальное окно «Failed to remove temporary directory». Плюс на каждый запуск
пришлось бы распаковывать (хешировать) десятки мегабайт заново. Поэтому файл
живёт в постоянном каталоге — рядом с установкой.

Правила
-------
* сверка ВСЕГДА по SHA-256: ``<root>/forest-ui.exe`` сравнивается с хешем из
  манифеста встроенного помощника. Совпал — ничего не копируем и не трогаем
  (повторный запуск не «распаковывается заново»);
* замена атомарная: копия во временный ``.new-<pid>`` -> сверка хеша КОПИИ ->
  ``os.replace`` -> сверка хеша того, что теперь лежит в постоянном каталоге.
  Половинчатого файла не бывает: либо старый, либо целиком новый;
* ничего не поднимает исключений наружу: не удалось — в лог уходит
  предупреждение, функция возвращает ``""``, программа работает со встроенным
  упрощённым значком (``tray.py``) и запасными уведомлениями (``notify.py``).
  Модальных окон нет;
* внутри помощника (``FOREST_UI=1``) ничего не распаковывается: он не должен
  перезаписывать собственный файл.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time

SUB_DIR = "ui_bundle"                 # каталог встроенного помощника в пакете
MANIFEST_NAME = "ui-bundle.json"      # манифест: имя, size, sha256, версия
NAME_WIN = "forest-ui.exe"
NAME_POSIX = "forest-ui"
CHUNK = 1 << 20                       # 1 МиБ: сверка/копирование кусками
HELPER_ENV = "FOREST_UI"              # «этот процесс — помощник» (см. ui_helper)

# «Этот файл уже сверен в ЭТОМ процессе»: путь -> (size, mtime_ns).
# Хеш 37 МБ — доли секунды, но супервизор зовёт ensure() раз в 30 с, и без
# кэша это была бы постоянная читательская нагрузка на диск.
_VERIFIED: dict[str, tuple[int, int]] = {}
_MANIFEST: dict = {}


# ─────────────────────────────────────────────────────── имена и пути ───
def helper_name() -> str:
    """Имя файла помощника на этой платформе."""
    return NAME_WIN if sys.platform.startswith("win") else NAME_POSIX


def frozen_root() -> str:
    """Корень распаковки нашего onefile-пакета ('' — запущены из исходников)."""
    mp = getattr(sys, "_MEIPASS", "") or ""
    try:
        return mp if (mp and os.path.isdir(mp)) else ""
    except Exception:
        return ""


def bundle_dir() -> str:
    """Каталог встроенного помощника ВНУТРИ пакета ('' — его там нет)."""
    mp = frozen_root()
    if not mp:
        return ""
    d = os.path.join(mp, SUB_DIR)
    return d if os.path.isdir(d) else ""


def embedded_path() -> str:
    """Путь к встроенному файлу помощника внутри пакета ('' — нет)."""
    d = bundle_dir()
    if not d:
        return ""
    p = os.path.join(d, helper_name())
    return p if os.path.isfile(p) else ""


def target_path(cfg: dict) -> str:
    """Постоянный каталог установки: <root>/forest-ui.exe.

    ``paths.root`` — тот же корень, куда ``--install`` кладёт программу
    (по умолчанию ``%LOCALAPPDATA%\\ForestProxyUpdater``, либо ``FOREST_ROOT``).
    """
    root = ""
    try:
        root = ((cfg or {}).get("paths") or {}).get("root") or ""
    except Exception:
        root = ""
    if not root:
        try:
            import config as CFG
            root = CFG.app_root()
        except Exception:
            root = ""
    return os.path.join(root, helper_name()) if root else ""


def available() -> bool:
    """Есть ли встроенный помощник (то есть помощник «в комплекте»)."""
    return bool(embedded_path())


# ─────────────────────────────────────────────────────── хеши и манифест ───
def sha256_file(path: str) -> str:
    """SHA-256 файла ('' — прочитать не удалось)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        return h.hexdigest()
    except Exception:
        return ""


def manifest() -> dict:
    """Манифест встроенного помощника ({} — нет или битый)."""
    global _MANIFEST
    if _MANIFEST:
        return _MANIFEST
    d = bundle_dir()
    if not d:
        return {}
    p = os.path.join(d, MANIFEST_NAME)
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f)
        if isinstance(v, dict) and v.get("sha256"):
            _MANIFEST = v
            return _MANIFEST
    except Exception:
        pass
    return {}


def embedded_sha256() -> str:
    """Ожидаемый SHA-256 встроенного помощника.

    Берём из манифеста (записан сборкой ровно для этого файла). Нет манифеста —
    считаем хеш по самому файлу в пакете: правило «сверяем по хешу» не должно
    зависеть от наличия манифеста.
    """
    m = manifest()
    h = str(m.get("sha256") or "")
    if h:
        return h
    return sha256_file(embedded_path())


def embedded_version() -> str:
    """Версия помощника из манифеста ('' — неизвестна)."""
    return str(manifest().get("version") or "")


def _stat_key(path: str) -> tuple[int, int]:
    try:
        st = os.stat(path)
        return (int(st.st_size), int(getattr(st, "st_mtime_ns", 0)
                                      or st.st_mtime * 1e9))
    except Exception:
        return (0, 0)


def _in_verified_cache(path: str) -> bool:
    key = _VERIFIED.get(path)
    return bool(key) and key == _stat_key(path)


def is_current(cfg: dict) -> bool:
    """Лежит ли в постоянном каталоге ровно встроенный помощник (по SHA-256)."""
    tgt = target_path(cfg)
    if not tgt or not os.path.isfile(tgt):
        return False
    want = embedded_sha256()
    if not want:
        return False
    if _in_verified_cache(tgt):
        return True
    got = sha256_file(tgt)
    if got != want:
        return False
    _VERIFIED[tgt] = _stat_key(tgt)
    return True


def _we_are_helper() -> bool:
    """Мы — процесс помощника? Спрашиваем ui_helper (единое правило).

    Импорт ленивый: ui_helper импортирует этот модуль, и обратный импорт на
    уровне модуля дал бы цикл.
    """
    if os.environ.get(HELPER_ENV) != "1":
        return False
    try:
        import ui_helper as UH
        return bool(UH.we_are_helper())
    except Exception:
        return True


# ─────────────────────────────────────────────────────────── распаковка ───
def ensure(cfg: dict, force: bool = False, log_msg: bool = True) -> str:
    """Гарантирует помощника в постоянном каталоге. -> путь ('' — не вышло).

    Порядок: сверить -> (при расхождении) скопировать -> сверить копию ->
    подменить атомарно -> сверить итог. Возвращает путь ТОЛЬКО к файлу,
    чей SHA-256 равен встроенному (либо к уже стоявшей копии, если подменить
    не дал занятый файл — помощник работает, версия обновится при следующем
    старте).
    """
    tgt = target_path(cfg)
    if not tgt:
        return ""
    if _we_are_helper():                 # помощник не перезаписывает сам себя
        return tgt if os.path.isfile(tgt) else ""
    src = embedded_path()
    if not src:                          # сборка без встроенного помощника
        return tgt if os.path.isfile(tgt) else ""
    want = embedded_sha256()
    if not want:
        _warn("встроенный помощник: не удалось определить его SHA-256 — "
              "работаю со встроенным значком")
        return ""

    t0 = time.time()
    if not force and is_current(cfg):
        _info_if(log_msg, "помощник интерфейса: копия на месте и совпадает по "
                          "SHA-256 (%s, %.2f с)" % (want[:12], time.time() - t0))
        return tgt

    if force or os.path.isfile(tgt):
        _info_if(log_msg, "помощник интерфейса: копия отличается от встроенной "
                          "— распаковываю (%s)" % want[:12])
    else:
        _info_if(log_msg, "помощник интерфейса: распаковываю встроенный в %s"
                          % tgt)

    tmp = ""
    try:
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        tmp = tgt + ".new-%d" % os.getpid()
        with open(src, "rb") as f, open(tmp, "wb") as o:
            shutil.copyfileobj(f, o, CHUNK)
            o.flush()
            os.fsync(o.fileno())
        got = sha256_file(tmp)
        if got != want:
            raise ValueError("SHA-256 копии не совпал: %s != %s" % (got, want))
        if os.name != "nt":
            os.chmod(tmp, 0o755)
        os.replace(tmp, tgt)             # атомарная подмена
        got2 = sha256_file(tgt)          # сверка того, что теперь на диске
        if got2 != want:
            raise ValueError("SHA-256 на месте не совпал: %s != %s"
                             % (got2, want))
        _VERIFIED[tgt] = _stat_key(tgt)
        _info_if(log_msg,
                 "помощник интерфейса распакован из встроенного: %s (%d байт, "
                 "%.2f с, SHA-256 %s)"
                 % (tgt, os.path.getsize(tgt), time.time() - t0, want[:12]))
        return tgt
    except Exception as e:
        _warn("встроенный помощник не распакован (%s): %s" % (tgt, e))
        if tmp:
            try:
                if os.path.isfile(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
        # Занятый файл (помощник сейчас работает) или нет прав — не беда:
        # работаем с тем, что уже стоит, и НЕ показываем никаких окон.
        if os.path.isfile(tgt):
            _warn("помощник интерфейса: использую уже установленную копию "
                  "(обновится при следующем запуске)")
            return tgt
        _warn("помощник интерфейса: работаю со встроенным значком")
        return ""


def report(cfg: dict) -> dict:
    """Сводка для ``--status`` и проверок: где встроенный, где копия, совпал ли хеш."""
    tgt = target_path(cfg)
    emb = embedded_path()
    want = embedded_sha256() if emb else ""
    got = sha256_file(tgt) if (tgt and os.path.isfile(tgt)) else ""
    return {
        "embedded": emb,
        "embedded_sha256": want,
        "embedded_version": embedded_version() if emb else "",
        "embedded_size": os.path.getsize(emb) if emb else 0,
        "target": tgt,
        "target_present": bool(tgt and os.path.isfile(tgt)),
        "target_sha256": got,
        "target_size": os.path.getsize(tgt) if (tgt and os.path.isfile(tgt)) else 0,
        "match": bool(want and got and want == got),
    }


def _info_if(on: bool, m: str) -> None:
    if on:
        _info(m)


def _info(m: str) -> None:
    try:
        import log as LG
        LG.get_logger().info(m)
    except Exception:
        pass


def _warn(m: str) -> None:
    try:
        import log as LG
        LG.get_logger().warn(m)
    except Exception:
        pass
