# -*- mode: python ; coding: utf-8 -*-
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# forest-ui.spec — сборка ПОМОЩНИКА ИНТЕРФЕЙСА (Qt/PySide6) отдельным файлом.
#
# Почему отдельным артефактом: Qt весит десятки мегабайт. Основная программа
# (ForestProxyUpdater.exe / forest-proxy-updater) остаётся лёгкой и без сторонних
# зависимостей, а весь интерфейс — значок в трее, меню с подменю, уведомления,
# темы — живёт в этом исполняемом файле. Нет помощника — программа работает со
# встроенным значком (ctypes) и запасными уведомлениями (src/notify.py).
#
# Лицензии: PySide6 — LGPL-3.0 (динамическая линковка), программа — GPL-3.0;
# совместимо (LGPL-3.0 §3 разрешает использование под GPL-3.0).
#
# Сборка:  packaging\build-ui.ps1   (Windows)  /  packaging/build-ui.sh (Linux)
import os

_HERE = os.path.abspath(SPECPATH)      # SPECPATH — КАТАЛОГ спеки, не файл
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
_ICON = os.path.join(_HERE, "app.ico")
_EXAMPLE = os.path.join(_REPO, "config.example.json")
# Утверждённый макет вида уведомления едет В КОМПЛЕКТЕ: его числа сверяет
# самотест (--selftest) и у собранной программы, а не только из репозитория.
_MOCKUP = os.path.join(_REPO, "docs", "notify-mockup.html")
# Макет меню трея тоже едет в комплекте: цвета/геометрия меню сверяются с ним.
_MENU_MOCKUP = os.path.join(_REPO, "docs", "menu-mockup.html")

for _f in (_ICON, _EXAMPLE, os.path.join(_SRC, "forest_ui.py")):
    if not os.path.isfile(_f):
        raise SystemExit("Не хватает входного файла для сборки помощника: " + _f)

# Наши модули подключаем явно: часть из них импортируется лениво (внутри функций)
HIDDEN = [
    "config", "i18n", "log", "notify", "theme", "tray_icon", "tray_menu",
    "tray_status", "tray_popup", "ui_cmd", "ui_helper", "ui_bundle", "ui_menu",
    "ui_style",
    "clientwrite", "clientwrite_posix",
    "clientwrite_v2rayn", "instancelock", "watchdog", "parser", "netfilters",
    "desktop_env", "safestdio",
]

DATAS = [(_ICON, "."), (_EXAMPLE, ".")]
if os.path.isfile(_MOCKUP):
    DATAS.append((_MOCKUP, os.path.join("docs")))
if os.path.isfile(_MENU_MOCKUP):
    DATAS.append((_MENU_MOCKUP, os.path.join("docs")))

# QML/Quick/сеть помощнику не нужны: он рисует значок, меню и уведомление.
# Убираем — не из экономии ради экономии, а чтобы в комплект не попадали
# десятки мегабайт неиспользуемых библиотек. Проверяется прогоном собранного
# файла (см. verify: --menu-dump и --selftest).
_EXCLUDES = [
    "tkinter", "unittest", "pydoc_data", "test", "doctest", "lib2to3",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtNetwork", "PySide6.QtWebSockets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtSvgWidgets",
]

_DROP_DLL = ("qt6qml", "qt6quick", "qt6network", "qt6websockets", "qt6sql",
             "qt6test", "qt6designer", "qt6help", "qt6multimedia", "qt6sensors",
             "qt6serialport", "qt6positioning", "qt6location", "qt6bluetooth",
             "qt6charts", "qt6datavisualization", "qt6pdf", "qt6spatialaudio",
             "qt6texttospeech", "qt63d", "qt6remoteobjects", "qt6scxml",
             "qt6statemachine", "qt6webchannel", "qt6webengine")

a = Analysis(
    [os.path.join(_SRC, "forest_ui.py")],
    pathex=[_SRC],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)
# выбрасываем неиспользуемые библиотеки Qt (только они; QtCore/QtGui/QtWidgets
# и их зависимости остаются нетронутыми)
a.binaries = [b for b in a.binaries
              if not any(k in b[0].lower().replace("-", "").replace("_", "")
                         for k in _DROP_DLL)]
# и их плагины (qml/, imageformats оставляем: нужен PNG)
a.datas = [d for d in a.datas
           if not d[0].lower().startswith(("pyside6/qml", "qml/"))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="forest-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,             # оконная программа: без консольного окна
    disable_windowed_traceback=True,  # БЕЗ модального окна с трейсбеком
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)
