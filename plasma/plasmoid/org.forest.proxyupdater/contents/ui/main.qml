// FOREST's Proxy Auto-updater — плазмоид панели KDE (Plasma 6)
// Copyright (C) 2026 FOREST
// SPDX-License-Identifier: GPL-3.0-only
//
// Что это: значок программы в панели KDE. Панель KDE работает по протоколу
// StatusNotifier и забирает правый клик по значку себе, поэтому своё окно меню
// приложение показать не может. Плазмоид закрывает разрыв: рисует нашу иконку с
// цветом состояния и по клику открывает НАШЕ меню (forest-ui --menu-popup).
//
// ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ МОЖЕТ (после инцидента в 1.3.2):
//   * Plasma5Support / P5Support и любых DataSource с engine:'executable';
//   * запуска процессов из QML по таймеру;
//   * коротких интервалов опроса (только один длинный, 20 с);
//   * синхронного ввода-вывода и записи на диск.
// Состояние читается XMLHttpRequest'ом к file:// (см. StateReader.qml) — это
// чтение файла, а не запуск программы: один асинхронный запрос раз в 20 секунд,
// тик пропускается, если предыдущий запрос ещё не ответил.
//
// Кнопки:
//   * ПРАВАЯ — наше меню;
//   * ЛЕВАЯ — ничего (одинаково на всех платформах);
//   * Ctrl + ЛЕВАЯ — наше меню, запасной путь (часть панелей перехватывает
//     правый клик своим контекстным меню виджета).

import QtQuick
import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami

// Идентификаторы внешних объектов (root и вложенные элементы) используются
// внутри compactRepresentation — прагма связывает их явно, и qmllint не ругается
// на «Unqualified access».
pragma ComponentBehavior: Bound

PlasmoidItem {
    id: root

    preferredRepresentation: compactRepresentation
    toolTipMainText: "FOREST — обновление прокси"
    toolTipSubText: root.stateTip.length ? root.stateTip : root.stateText

    // ── состояние из файла программы ────────────────────────────────────────
    property string stateKind: ""      // ok|busy|switch|restoring|error|down|off|noanswer
    property string stateTip: ""       // готовая подсказка сторожа (если есть)
    property bool launching: false     // защита от второго меню по двойному клику
    property bool artReady: false      // наша картинка загрузилась

    // Ярлык, который открывает наше меню. Это .desktop из нашего же пакета
    // (NoDisplay=true, Exec=… --menu-popup): QML просит рабочий стол запустить
    // приложение и сразу возвращает управление. Ни ожидания процесса, ни
    // оболочки команд, ни DataSource — подвиснуть нечему.
    readonly property string menuLauncher: "file:///usr/share/applications/forest-proxy-updater-menu.desktop"

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
    // Неизвестное/пустое состояние — НЕЙТРАЛЬНЫЙ серый, а не выдуманный цвет.
    readonly property color stateColor: root.statePalette[root.stateKind] || "#9aa0a6"

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

    // Чтение состояния: асинхронный XHR к file://, раз в 20 с, без процессов.
    StateReader {
        id: reader
        pollInterval: 20000
        onStateUpdated: function (kind, tip) {
            root.stateKind = kind;
            root.stateTip = tip;
        }
    }

    // ── меню: просьба рабочему столу открыть наш ярлык меню ─────────────────
    function openMenu() {
        if (root.launching)
            return;
        root.launching = true;
        menuGuard.restart();
        var ok = false;
        try {
            ok = Qt.openUrlExternally(root.menuLauncher);
        } catch (e) {
            ok = false;                  // нет обработчика/портала — просто ничего
        }
        if (!ok)
            root.launching = false;
    }

    // Снимаем блокировку через 2.5 с: даже если ярлык не открылся, следующий
    // клик снова сработает. Таймер короткий, но он НИЧЕГО не запускает.
    Timer {
        id: menuGuard
        interval: 2500
        repeat: false
        onTriggered: root.launching = false
    }

    // ── вид значка: наша иконка + метка состояния ───────────────────────────
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
            // левая кнопка без модификатора — НИЧЕГО
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

        // Нашей картинки нет (установка без значка) — простой кружок цвета
        // состояния. Никаких иконочных API: QtQuick рисует сам, и отказать
        // здесь нечему.
        Rectangle {
            anchors.fill: art
            visible: !root.artReady
            radius: width / 2
            color: "transparent"
            border.width: Math.max(2, Math.round(width * 0.22))
            border.color: root.stateColor
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
