// Стенд для StateReader.qml — НЕ часть пакета, только проверка.
// Copyright (C) 2026 FOREST
// SPDX-License-Identifier: GPL-3.0-only
//
// Запуск (Linux, без графического сеанса, панель не затрагивается):
//   QT_QPA_PLATFORM=offscreen qml6 harness.qml
//
// Что проверяем:
//   1) валидный tray_status.json -> state/tip применяются;
//   2) битый JSON, массив, число, null, пустой файл -> ПУСТОЕ состояние
//      (нейтральный цвет), без исключений и падений;
//   3) файла нет совсем -> пустое состояние;
//   4) первый путь не открылся, второй открылся -> берётся второй;
//   5) серия из 10 вызовов requestState() подряд даёт РОВНО ОДНУ выдачу
//      (защита от наложения запросов работает);
//   6) неизвестное значение state проходит как есть (панель красит его в
//      нейтральный серый — это логика main.qml).
import QtQuick
import "ui" as UI

Item {
    id: h
    width: 8
    height: 8

    property int idx: -1
    property int emissions: 0
    property int fails: 0
    property int passes: 0

    property var cases: [
        { name: "valid",            paths: ["file:///tmp/forest-plasmoid-test/ok.json"],
          kind: "ok", tip: "связь есть" },
        { name: "valid-no-tip",     paths: ["file:///tmp/forest-plasmoid-test/ok-notip.json"],
          kind: "ok", tip: "" },
        { name: "broken-json",      paths: ["file:///tmp/forest-plasmoid-test/broken.json"],
          kind: "", tip: "" },
        { name: "json-array",       paths: ["file:///tmp/forest-plasmoid-test/array.json"],
          kind: "", tip: "" },
        { name: "json-number",      paths: ["file:///tmp/forest-plasmoid-test/number.json"],
          kind: "", tip: "" },
        { name: "json-null",        paths: ["file:///tmp/forest-plasmoid-test/null.json"],
          kind: "", tip: "" },
        { name: "empty-file",       paths: ["file:///tmp/forest-plasmoid-test/empty.json"],
          kind: "", tip: "" },
        { name: "missing-file",     paths: ["file:///tmp/forest-plasmoid-test/none.json"],
          kind: "", tip: "" },
        { name: "missing-file-probe-ok", paths: ["file:///tmp/forest-plasmoid-test/none.json"],
          probe: "file:///tmp/forest-plasmoid-test/ok.json",
          kind: "", tip: "" },
        { name: "xhr-blocked-diagnosed", paths: ["file:///tmp/forest-plasmoid-test/none.json"],
          probe: "file:///tmp/forest-plasmoid-test/also-none.json",
          kind: "", tip: "", tipIsHint: true },
        { name: "fallback-second",  paths: ["file:///tmp/forest-plasmoid-test/none.json",
                                           "file:///tmp/forest-plasmoid-test/ok.json"],
          kind: "ok", tip: "связь есть" },
        { name: "unknown-state",    paths: ["file:///tmp/forest-plasmoid-test/weird.json"],
          kind: "weird-state", tip: "" },
        { name: "no-overlap-10-clicks", clicks: 10,
          paths: ["file:///tmp/forest-plasmoid-test/ok.json"],
          kind: "ok", tip: "связь есть" }
    ]

    UI.StateReader {
        id: rd
        // Стенд опрашивает вручную: длинный интервал, чтобы фоновый таймер
        // не вмешивался в подсчёт выдач.
        pollInterval: 3600000
        requestWatchdog: 3000
        onStateUpdated: function (kind, tip) { h.onUpdate(kind, tip); }
    }

    function check(ok, label, extra) {
        if (ok)
            h.passes += 1;
        else
            h.fails += 1;
        console.log((ok ? "PASS " : "FAIL ") + label + (extra ? "  [" + extra + "]" : ""));
    }

    function onUpdate(kind, tip) {
        if (h.idx < 0 || h.idx >= h.cases.length)
            return;
        var c = h.cases[h.idx];
        h.emissions += 1;
        if (h.emissions > 1) {
            // лишняя выдача — считаем один раз, чтобы не зациклиться
            h.check(false, c.name + ": лишняя выдача", "emissions=" + h.emissions);
            return;
        }
        h.check(h.emissions === 1, c.name + ": один запрос — одна выдача",
                "emissions=" + h.emissions);
        h.check(kind === c.kind, c.name + ": state",
                "got='" + kind + "' want='" + c.kind + "'");
        var wantTip = c.tipIsHint ? rd.blockedHint : c.tip;
        h.check(tip === wantTip, c.name + ": tip",
                "got='" + tip + "' want='" + wantTip + "'");
        h.nextCase();
    }

    function step() {
        h.idx += 1;
        if (h.idx >= h.cases.length) {
            h.done();
            return;
        }
        var c = h.cases[h.idx];
        h.emissions = 0;
        rd.extraPaths = c.paths;
        rd.probeUrl = c.probe !== undefined ? c.probe : "";
        if (rd.busy) {
            h.check(false, c.name + ": reader занят до старта сценария");
            rd._abandon();
        }
        var n = c.clicks ? c.clicks : 1;
        for (var i = 0; i < n; ++i)
            rd.requestState();               // серия вызовов в одном такте
        caseTimeout.restart();
    }

    function nextCase() {
        caseTimeout.stop();
        tick.restart();
    }

    function done() {
        console.log("SUMMARY passes=" + h.passes + " fails=" + h.fails);
        console.log(h.fails === 0 ? "RESULT OK" : "RESULT FAILED");
        Qt.exit(h.fails === 0 ? 0 : 1);
    }

    Timer {
        id: tick
        interval: 200
        repeat: false
        onTriggered: h.step()
    }

    Timer {
        id: caseTimeout
        interval: 4000
        repeat: false
        onTriggered: {
            h.check(false, h.cases[h.idx].name + ": ответа нет за 4 с");
            h.nextCase();
        }
    }

    Component.onCompleted: tick.start()
}
