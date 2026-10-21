# -*- mode: python ; coding: utf-8 -*-
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""PyInstaller-спек: один самодостаточный ForestProxyUpdater.exe.

onefile (библиотеки/данные кладутся прямо в EXE, без COLLECT), БЕЗ консольного
окна (``console=False``), с иконкой и ресурсом версии.

Внутрь пакета попадает:
  * весь код из репозиторного ``src/`` (компилируется в PYZ; ``src`` добавлен в
    ``pathex``, потому что модули импортируются по «плоским» именам:
    ``import config``, ``import clientwrite``, …);
  * ядро и гео-базы как ``bin/xray/{xray.exe,geoip.dat,geosite.dat,LICENSE}``.
    Ядро запускается НЕ отсюда: при первом запуске оно (вместе с обеими
    гео-базами) копируется в постоянный каталог данных
    (``config.ensure_core_installed`` -> ``%LOCALAPPDATA%\\ForestProxyUpdater\\
    bin\\xray``), и только оттуда — иначе живое ядро держало бы файл в
    распакованном каталоге _MEIxxxx, уборщик PyInstaller не смог бы его снести
    и показал бы модальное окно «Failed to remove temporary directory»;
  * ``config.example.json`` в корень пакета, чтобы первый запуск без конфига
    и проверка ``example_config_path()`` работали из одного .exe.

Запуск — только через build.ps1/build.bat (они гарантируют, что ядро и иконка
на месте и что PyInstaller установлен).
"""
import os

# SPECPATH задаёт сам PyInstaller при выполнении спеки.
try:
    _HERE = os.path.abspath(SPECPATH)                       # noqa: F821
except NameError:                                          # pragma: no cover
    _HERE = os.path.dirname(os.path.abspath(__file__))

_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
_VENDOR = os.path.join(_HERE, "vendor", "bin", "xray")
_ICON = os.path.join(_HERE, "app.ico")
_VERSION = os.path.join(_HERE, "version_info.txt")
_EXAMPLE = os.path.join(_REPO, "config.example.json")

# ── встроенный помощник интерфейса (ОДИН exe для пользователя) ──
# forest-ui.exe (Qt: значок в трее, меню, уведомления) больше НЕ отдельный файл
# для скачивания на Windows: он лежит внутри основного .exe как данные
# (`ui_bundle/forest-ui.exe`) и при первом запуске распаковывается в постоянный
# каталог установки со сверкой SHA-256 (см. src/ui_bundle.py). Помощник при этом
# остаётся САМОСТОЯТЕЛЬНЫМ артефактом сборки (forest-ui.spec / build-ui.ps1):
# тот же файл нужен для Linux и для ручной установки.
# Путь можно задать явно: FOREST_HELPER_EXE=...\forest-ui.exe
_HELPER_NAME = "forest-ui.exe"
_HELPER = os.environ.get("FOREST_HELPER_EXE") or os.path.join(_HERE, "dist",
                                                             _HELPER_NAME)

# ── обязательные входы: падаем рано и понятно, а не «странным» exe ──
_required = {
    "src/v2ray_updater.py": os.path.join(_SRC, "v2ray_updater.py"),
    "config.example.json": _EXAMPLE,
    "app.ico (python make_icon.py)": _ICON,
    "version_info.txt": _VERSION,
}
for _f in ("xray.exe", "geoip.dat", "geosite.dat", "LICENSE"):
    _required["vendor/bin/xray/" + _f + "  (python fetch_core.py)"] = \
        os.path.join(_VENDOR, _f)

_missing = [k for k, v in _required.items() if not os.path.isfile(v)]
if not os.path.isfile(_HELPER):
    _missing.append("dist/%s  (powershell -File packaging/build-ui.ps1)"
                    % _HELPER_NAME)
if _missing:
    raise SystemExit("Не хватает входных файлов для сборки:\n  - "
                     + "\n  - ".join(_missing))

# «Плоские» модули src/: собираем явно, т.к. часть из них импортируется лениво
# (import watch внутри функции, import selftest, import tray, …).
HIDDEN = [
    "clientwrite", "clientwrite_posix", "clientwrite_v2rayn",
    "config", "i18n", "log", "netfilters", "notify", "parser", "selftest",
    "safestdio", "sources", "theme", "tray", "tray_check", "tray_icon",
    "tray_menu",
    "instancelock", "tray_status", "ui_bundle", "ui_helper", "ui_cmd",
    "v2ray_updater", "watch", "watchdog",
    "xraytest",
]

# данные, которые должны лежать рядом с exe ВНУТРИ onefile-пакета
DATAS = [(os.path.join(_VENDOR, f), "bin/xray")
         for f in ("xray.exe", "geoip.dat", "geosite.dat", "LICENSE")]
DATAS.append((_EXAMPLE, "."))
# Иконка пользователя: внутрь пакета, чтобы --install положил её рядом с
# программой (её берут значок трея и ярлык) и чтобы работал откат, если файла
# рядом не окажется.
if os.path.isfile(_ICON):
    DATAS.append((_ICON, "."))
else:
    raise SystemExit("Не хватает иконки: " + _ICON + " (положите свой app.ico)")

# ── встроенный помощник + манифест ──
# Манифест — единственный источник ожидаемого SHA-256: его пишет СБОРКА из того
# самого файла, который упаковывается. При запуске ui_bundle сверяет с ним
# распакованную копию и не распаковывает ничего, если хеш совпал.
import hashlib
import json
import re


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


try:
    with open(_VERSION, encoding="utf-8") as _f:
        _vi = _f.read()
    _ver = (re.search(r"u?'ProductVersion',\s*u?'([0-9.]+)'", _vi)
            or re.search(r"prodvers=\(([0-9, ]+)\)", _vi))
    _ver = ((".".join(_ver.group(1).replace(" ", "").split(","))
             if _ver and "," in _ver.group(1) else _ver.group(1))
            if _ver else "")
except Exception:
    _ver = ""

_HELPER_SHA = _sha256(_HELPER)
_HELPER_SIZE = os.path.getsize(_HELPER)
_MANIFEST = os.path.join(_HERE, "dist", "ui-bundle.json")
with open(_MANIFEST, "w", encoding="utf-8") as _f:
    json.dump({
        "name": _HELPER_NAME,
        "version": _ver,
        "sha256": _HELPER_SHA,
        "size": _HELPER_SIZE,
        "source": os.path.abspath(_HELPER),
    }, _f, ensure_ascii=False, indent=2)
DATAS.append((_HELPER, "ui_bundle"))
DATAS.append((_MANIFEST, "ui_bundle"))
print("[spec] встроенный помощник: %s (%d байт, sha256 %s, версия %s)"
      % (_HELPER, _HELPER_SIZE, _HELPER_SHA[:12], _ver or "?"))

# Отладочная сборка с консолью (FOREST_CONSOLE_BUILD=1): тогда ошибки
# bootloader'а (например, неудалённый _MEI) печатаются в stderr, а НЕ показываются
# модальным окном на рабочем столе. Нужна для безопасных проверок в песочнице;
# штатная сборка (переменная не задана) остаётся оконной и без консоли.
_CONSOLE_BUILD = os.environ.get("FOREST_CONSOLE_BUILD") == "1"
_NAME = "ForestProxyUpdater" + ("-console" if _CONSOLE_BUILD else "")

a = Analysis(
    [os.path.join(_SRC, "v2ray_updater.py")],
    pathex=[_SRC],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PIL", "numpy", "matplotlib", "pandas", "scipy",
              "pydoc_data", "test"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=_CONSOLE_BUILD,      # без консольного окна (pythonw-семантика)
    disable_windowed_traceback=True,  # БЕЗ модального окна с трейсбеком
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    version=_VERSION,
)
