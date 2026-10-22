// Стенд «запрещённое чтение файлов» — НЕ часть пакета, только проверка.
// Copyright (C) 2026 FOREST
// SPDX-License-Identifier: GPL-3.0-only
//
// Запуск БЕЗ QML_XHR_ALLOW_FILE_READ — это ровно то, что видит панель по
// умолчанию (plasmashell эту переменную не выставляет). Проверяем обещание:
// виджет не падает, ничего не виснет, состояние просто остаётся нейтральным.
//
//   QT_QPA_PLATFORM=offscreen qml6 harness-blocked.qml
import QtQuick
import "ui" as UI

Item {
    id: h
    width: 8
    height: 8

    property int emissions: 0
    property int fails: 0

    UI.StateReader {
        id: rd
        pollInterval: 3600000
        requestWatchdog: 3000
        extraPaths: ["file:///tmp/forest-plasmoid-test/ok.json"]
        onStateUpdated: function (kind, tip) {
            // Сюда попадать не должны: чтение файлов запрещено, ответа не будет.
            h.emissions += 1;
            console.log("INFO  выдача при запрещённом чтении: kind='" + kind + "' tip='" + tip + "'");
        }
    }

    Component.onCompleted: rd.requestState()

    Timer {
        interval: 4000
        running: true
        repeat: false
        onTriggered: {
            // Запрос «висит» (Qt не зовёт ни одного обработчика) — и это НЕ
            // зависание оболочки: сторож снял busy, панель продолжает жить.
            var noEmission = (h.emissions === 0);
            console.log((noEmission ? "PASS " : "FAIL ")
                        + "при запрещённом чтении нет выдачи состояния (busy=" + rd.busy
                        + ", выдач=" + h.emissions + ")");
            if (!noEmission)
                h.fails += 1;
            console.log(h.fails === 0 ? "RESULT OK" : "RESULT FAILED");
            Qt.exit(h.fails === 0 ? 0 : 1);
        }
    }
}
