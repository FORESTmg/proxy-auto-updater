// FOREST's Proxy Auto-updater — плазмоид панели KDE (Plasma 6)
// Copyright (C) 2026 FOREST
// SPDX-License-Identifier: GPL-3.0-only
//
// Что это: НАСТОЯЩАЯ иконка программы в панели KDE. Панель KDE работает по
// протоколу StatusNotifier, и там правый клик по значку забирает САМА панель:
// приложение такого клика не получает, поэтому своё окно меню показать не может
// (ограничение протокола). Этот плазмоид закрывает разрыв: он читает состояние
// из tray_status.json, рисует нашу иконку с цветом состояния, а по клику
// запускает `forest-ui --menu-popup` — то есть РОВНО наше меню, с нашими
// анимациями и подменю, а не меню панели.
//
// Кнопки:
//   * ПРАВАЯ кнопка — наше меню (как у значка в трее и как в Windows);
//   * ЛЕВАЯ кнопка — ничего (одинаковое поведение на всех платформах);
//   * Ctrl + ЛЕВАЯ кнопка — наше меню. Это запасной путь: часть панелей KDE
//     перехватывает правый клик своим контекстным меню виджета, и тогда
//     остаётся этот вариант (честно описано в README рядом с плазмоидом).

import QtQuick
import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support

// Идентификаторы внешних объектов (root, art, clickArea) используются внутри
// вложенного компонента compactRepresentation — с этим прагма QML связывает их
// явно, и qmllint не выдаёт предупреждений «Unqualified access».
pragma ComponentBehavior: Bound

PlasmoidItem {
    id: root

    preferredRepresentation: compactRepresentation
    toolTipMainText: "FOREST — обновление прокси"
    toolTipSubText: root.stateText

    // ── состояние из файла программы ───────────────────────────────────────
    property string stateKind: ""          // ok|busy|switch|restoring|error|down|off|noanswer
    property string stateTip: ""           // готовая подсказка сторожа (если есть)
    property bool launching: false         // защита от второго меню по двойному клику

    // Цвета состояний — ТЕ ЖЕ, что в палитре программы (src/ui_style.py,
    // таблица state_*), иначе значок в панели и наше меню разошлись бы цветом.
    readonly property var statePalette: ({
        "ok":        "#41c26d",
        "busy":      "#e5bf37",
        "switch":    "#e5bf37",
        "restoring": "#e5bf37",
        "error":     "#dc4f55",
        "down":      "#dc4f55",
        "off":       "#9497a0",
        "noanswer":  "#9497a0"
    })
    readonly property color stateColor: statePalette[root.stateKind] || "#9aa0a6"
    readonly property string stateText: {
        switch (root.stateKind) {
        case "ok":        return "связь есть";
        case "busy":      return "идёт работа";
        case "switch":    return "переключение сервера";
        case "restoring": return "восстановление связи";
        case "error":
        case "down":      return "нет связи";
        case "off":       return "прокси выключен";
        case "noanswer":  return "сторож молчит";
        default:          return "нет данных о состоянии";
        }
    }
    property bool artReady: false   // наша картинка загрузилась (иначе значок темы)

    // ── меню: отдельный процесс forest-ui без значка в трее ────────────────
    function openMenu() {
        if (root.launching)
            return;
        root.launching = true;
        // Без координат: курсор в момент клика стоит НА иконке, и forest-ui
        // ставит меню по курсору с учётом монитора и масштаба (DPI).
        exec.connectSource("forest-ui --menu-popup");
        launchGuard.restart();
    }

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: function (sourceName, data) {
            root.launching = false;
            exec.disconnectSource(sourceName);
        }
    }

    // Если процесс не ответил (нет forest-ui в PATH) — снимаем блокировку,
    // иначе меню больше не открылось бы вовсе.
    Timer {
        id: launchGuard
        interval: 4000
        repeat: false
        onTriggered: root.launching = false
    }

    // ── чтение состояния: раз в 5 с, плюс при старте ──────────────────────
    function refreshState() {
        var path = String(Qt.resolvedUrl("../code/read-state.sh")).replace("file://", "");
        stateSrc.connectSource("sh '" + path + "' 2>/dev/null");
    }

    function applyState(text) {
        var txt = String(text || "").trim();
        if (!txt.length) {                 // файла нет/нечитаем — нейтральный цвет
            root.stateKind = "";
            root.stateTip = "";
            return;
        }
        try {
            const j = JSON.parse(txt);
            root.stateKind = String(j.state || "");
            root.stateTip = String(j.tip || "");
        } catch (e) {
            root.stateKind = "";
            root.stateTip = "";
        }
    }

    P5Support.DataSource {
        id: stateSrc
        engine: "executable"
        connectedSources: []
        onNewData: function (sourceName, data) {
            var out = (data && data["stdout"]) ? data["stdout"] : "";
            stateSrc.disconnectSource(sourceName);
            root.applyState(out);
        }
    }

    Timer {
        interval: 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refreshState()
    }

    // ── вид значка: наша иконка + метка состояния ─────────────────────────
    compactRepresentation: MouseArea {
        id: clickArea
        implicitWidth: Kirigami.Units.iconSizes.smallMedium
        implicitHeight: Kirigami.Units.iconSizes.smallMedium
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor

        onClicked: function (mouse) {
            if (mouse.button === Qt.RightButton) {
                root.openMenu();                       // правая — наше меню
            } else if (mouse.button === Qt.LeftButton
                       && (mouse.modifiers & Qt.ControlModifier)) {
                root.openMenu();                       // запасной путь: Ctrl+левая
            }
            // левая кнопка без модификатора — НИЧЕГО (как у значка в трее)
        }

        Image {
            id: art
            anchors.fill: parent
            anchors.margins: Math.round(Kirigami.Units.smallSpacing * 0.25)
            source: "file:///usr/share/icons/hicolor/256x256/apps/forest-proxy-updater.png"
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            visible: root.artReady
            // Признак «наша картинка есть» живёт в root и обновляется ОТСЮДА:
            // читать art.status из корня нельзя (id вложенного компонента вне
            // его области видимости — см. pragma ComponentBehavior: Bound).
            onStatusChanged: root.artReady = (status === Image.Ready)
            Component.onCompleted: root.artReady = (status === Image.Ready)
        }

        // Нашей картинки нет (установка без значка) — тематический значок,
        // окрашенный в цвет состояния.
        Kirigami.Icon {
            anchors.fill: art
            visible: !root.artReady
            source: "network-vpn"
            color: root.stateColor
            isMask: true
        }

        // Метка состояния в правом нижнем углу — как у значка в трее Windows
        // (там состояние показывает кружок поверх рисунка пользователя).
        Rectangle {
            width: Math.max(5, Math.round(clickArea.height * 0.34))
            height: width
            radius: width / 2
            color: root.stateColor
            border.width: 1
            border.color: Kirigami.Theme.backgroundColor
            anchors.right: art.right
            anchors.bottom: art.bottom
            anchors.rightMargin: -Math.round(clickArea.height * 0.06)
            anchors.bottomMargin: -Math.round(clickArea.height * 0.06)
        }
    }
}
