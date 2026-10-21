# -*- mode: python ; coding: utf-8 -*-
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
"""PyInstaller-спек: один самодостаточный ELF ``forest-proxy-updater`` (Linux).

onefile (библиотеки/данные внутри одного файла, без COLLECT), консольный
(``console=True``): на Linux окон нет — вывод идёт в stdout, который systemd
направляет в journal, а программа дополнительно пишет свой лог в каталог
состояния. Никаких окон и диалогов.

Внутрь пакета попадает:
  * весь код из репозиторного ``src/`` + ``linux/`` (``pathex`` содержит оба
    каталога, потому что модули импортируются по «плоским» именам:
    ``import config``, ``import clientwrite_posix``, ``import posix_platform``);
  * ядро и гео-базы как ``bin/xray/{xray,geoip.dat,geosite.dat,LICENSE}``.
    Ядро идёт в ``binaries`` (не ``datas``), чтобы PyInstaller выставил бит
    исполняемости при распаковке. Запускается оно НЕ отсюда: при первом запуске
    ядро и обе гео-базы копируются в постоянный каталог данных
    (``config.ensure_core_installed`` -> ``$XDG_DATA_HOME/forest-proxy-updater/
    bin/xray``, то есть ``~/.local/share/forest-proxy-updater/bin/xray``), и
    только оттуда — иначе работающее ядро осталось бы в распакованном каталоге
    _MEIxxxx и переживало бы выход программы (временный каталог может быть снесён
    системой при работающем прокси);
  * ``config.example.json`` в корень пакета (первый запуск без конфига);
  * ``linux/systemd/*`` — шаблоны юнитов, их копирует установщик
    (``posix_platform.write_units``).

Запуск — только через build-linux.sh (он гарантирует наличие ядра и что
PyInstaller установлен).
"""
import os

try:
    _HERE = os.path.abspath(SPECPATH)                       # noqa: F821
except NameError:                                          # pragma: no cover
    _HERE = os.path.dirname(os.path.abspath(__file__))

_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
_LINUX = os.path.join(_REPO, "linux")
_UNITS = os.path.join(_LINUX, "systemd")
_VENDOR = os.path.join(_HERE, "vendor-linux", "bin", "xray")
_EXAMPLE = os.path.join(_REPO, "config.example.json")
_LICENSE = os.path.join(_REPO, "LICENSE")

# ── обязательные входы: падаем рано и понятно ──
_required = {
    "src/v2ray_updater.py": os.path.join(_SRC, "v2ray_updater.py"),
    "src/clientwrite_posix.py": os.path.join(_SRC, "clientwrite_posix.py"),
    "linux/posix_platform.py": os.path.join(_LINUX, "posix_platform.py"),
    "linux/posix_selftest.py": os.path.join(_LINUX, "posix_selftest.py"),
    "config.example.json": _EXAMPLE,
} 
for _f in ("xray", "geoip.dat", "geosite.dat", "LICENSE"):
    _required["vendor-linux/bin/xray/" + _f + "  (python3 fetch_core_linux.py)"] = \
        os.path.join(_VENDOR, _f)
for _f in ("forest-proxy-updater.service", "forest-proxy-updater.timer",
           "forest-proxy-updater-refresh.service"):
    _required["linux/systemd/" + _f] = os.path.join(_UNITS, _f)

_missing = [k for k, v in _required.items() if not os.path.isfile(v)]
if _missing:
    raise SystemExit("Не хватает входных файлов для сборки:\n  - "
                     + "\n  - ".join(_missing))

# «Плоские» модули: часть импортируется лениво (watch, selftest, posix_platform,
# posix_selftest внутри функций) — перечисляем явно.
HIDDEN = [
    "clientwrite", "clientwrite_posix", "clientwrite_v2rayn",
    "config", "i18n", "log", "netfilters", "notify", "parser", "selftest",
    "desktop_env", "safestdio", "sources", "theme", "tray", "tray_check", "tray_icon",
    "tray_menu",
    "instancelock", "tray_status", "v2ray_updater", "watch", "watchdog",
    "xraytest",
    "posix_platform", "posix_selftest",
]

# Ядро — в binaries: PyInstaller выставит бит исполняемости при распаковке
# onefile, без чего find_core (проверка os.access X_OK) его не найдёт.
BINARIES = [(os.path.join(_VENDOR, "xray"), "bin/xray")]

DATAS = [(os.path.join(_VENDOR, f), "bin/xray")
         for f in ("geoip.dat", "geosite.dat", "LICENSE")]
DATAS.append((_EXAMPLE, "."))
DATAS.append((_LICENSE, "."))
for _f in ("forest-proxy-updater.service", "forest-proxy-updater.timer",
           "forest-proxy-updater-refresh.service"):
    DATAS.append((os.path.join(_UNITS, _f), "linux/systemd"))

a = Analysis(
    [os.path.join(_SRC, "v2ray_updater.py")],
    pathex=[_SRC, _LINUX],
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PIL", "numpy", "matplotlib", "pandas", "scipy",
              "pydoc_data", "test", "unittest", "pydoc", "doctest", "lib2to3"],
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
    name="forest-proxy-updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,               # окон нет: stdout -> journal systemd
    disable_windowed_traceback=True,  # БЕЗ модального окна с трейсбеком
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
