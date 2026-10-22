// FOREST's Proxy Auto-updater — чтение состояния БЕЗ запуска процессов
// Copyright (C) 2026 FOREST
// SPDX-License-Identifier: GPL-3.0-only
//
// Зачем отдельный файл: вся логика чтения tray_status.json собрана здесь и
// зависит ТОЛЬКО от QtQuick. Ни одного внешнего исполнителя, ни одной строчки
// Plasma 5 — поэтому этот компонент можно прогнать на стенде (qml6 + offscreen)
// отдельно от панели, что и делает plasma/plasmoid/check-plasmoid.sh.
//
// Как читаем:
//   * XMLHttpRequest к file:// — это разрешённое в QML ЧТЕНИЕ ФАЙЛА, а не
//     запуск программы. Ничего не пишем на диск, ничего не ждём синхронно.
//   * Пути перебираются ПО ОЧЕРЕДИ (тот же набор, что был в read-state.sh),
//     берётся ПЕРВЫЙ успешно прочитанный файл. «Самый свежий по mtime» из QML
//     недоступен, поэтому порядок задан явно и совпадает с read-state.sh:
//     сначала пользовательские каталоги состояния, затем системные.
//   * Пока один запрос в полёте, следующий тик ПРОПУСКАЕТСЯ (busy). Затянувшийся
//     запрос бросается сторожевым таймером — виджет не может «залипнуть».
//   * Любая ошибка (нет файла, битый JSON, нет прав, нет модуля StandardPaths)
//     даёт ПУСТОЕ состояние => нейтральный цвет. Исключения ловятся, наружу не
//     выходят.
import QtQuick

Item {
    id: reader

    // ── настройки ───────────────────────────────────────────────────────────
    // Панель: раз в 20 с. Никаких коротких интервалов — панель не должна
    // просыпаться чаще, чем того требует значок состояния.
    property int pollInterval: 20000
    // Сколько ждём один ответ, прежде чем бросить запрос (мс).
    property int requestWatchdog: 8000
    // Только для стенда: подмена списка путей. В панели всегда пусто.
    property var extraPaths: []
    // «Зонд»: файл, который есть на любой установке (metadata.json нашего же
    // пакета). Если он НЕ читается, значит в сеансе запрещено чтение файлов из
    // QML (Qt требует QML_XHR_ALLOW_FILE_READ=1) — тогда подсказка говорит об
    // этом прямо, а цвет остаётся нейтральным. Путь берётся относительно самого
    // плазмоида, поэтому годится и для системной установки, и для ~/.local.
    property string probeUrl: Qt.resolvedUrl("../../metadata.json")
    property string blockedHint: "состояние не читается: нужно QML_XHR_ALLOW_FILE_READ=1 (см. README)"

    // kind — ok|busy|switch|restoring|error|down|off|noanswer, tip — подсказка.
    // Пустые строки означают «данных нет» => нейтральный цвет.
    signal stateUpdated(string kind, string tip)

    property bool busy: false

    property int _epoch: 0          // защита от ответов «прошлой» попытки
    property var _xhr: null
    property var _urls: []
    property int _cursor: 0
    property int _probeIndex: -1
    property bool _probed: false
    property var _std: null         // объект StandardPaths или null
    property bool _pathsTried: false
    property int _emitted: 0        // счётчик выдач (диагностика/стенд)

    // ── пути ────────────────────────────────────────────────────────────────
    // Домашний каталог и $XDG_STATE_HOME из QML без процессов не видны, поэтому
    // берём их у Qt (Qt.labs.platform.StandardPaths). Модуль подтягивается
    // ДИНАМИЧЕСКИ: если его нет в системе, import не убивает весь виджет, а
    // просто оставляет только системные пути.
    function ensurePaths() {
        if (reader._pathsTried)
            return;
        reader._pathsTried = true;
        try {
            reader._std = Qt.createQmlObject(
                "import Qt.labs.platform 1.1 as Platform\n"
                + "Platform.StandardPaths {\n"
                + "    property string homeDir: writableLocation(Platform.StandardPaths.HomeLocation)\n"
                + "    property string stateDir: writableLocation(Platform.StandardPaths.GenericStateLocation)\n"
                + "}", reader, "forestStandardPaths");
        } catch (e) {
            reader._std = null;
        }
    }

    function _toUrl(path) {
        // Пробел и решётка в URL файла должны быть закодированы, иначе QUrl
        // обрежет адрес на '#'. Каталоги состояния таких символов обычно не
        // содержат, но пусть будет.
        return "file://" + String(path).replace(/%/g, "%25")
                                      .replace(/ /g, "%20")
                                      .replace(/#/g, "%23")
                                      .replace(/\?/g, "%3F");
    }

    function candidateUrls() {
        var list = [];
        function add(u) {
            var s = String(u || "");
            if (s.length && list.indexOf(s) === -1)
                list.push(s);
        }

        var i;
        for (i = 0; i < reader.extraPaths.length; ++i)
            add(reader.extraPaths[i]);

        // Те же пути, что перебирал read-state.sh, в том же порядке.
        // 1) $XDG_STATE_HOME/forest-proxy-updater (по умолчанию ~/.local/state/…)
        // 2) ~/.local/state/forest-proxy-updater (когда XDG_STATE_HOME задан)
        try {
            if (reader._std) {
                var st = String(reader._std.stateDir || "");
                if (st.length)
                    add(reader._toUrl(st + "/forest-proxy-updater/tray_status.json"));
                var hm = String(reader._std.homeDir || "");
                if (hm.length)
                    add(reader._toUrl(hm + "/.local/state/forest-proxy-updater/tray_status.json"));
            }
        } catch (e) {
            // StandardPaths не отдал каталоги — остаются системные пути.
        }

        // 3) системная служба из пакета: XDG_STATE_HOME=/var/lib/.../state
        add("file:///var/lib/forest-proxy-updater/state/forest-proxy-updater/tray_status.json");
        add("file:///var/lib/forest-proxy-updater/state/tray_status.json");
        add("file:///var/lib/forest-proxy-updater/tray_status.json");
        return list;
    }

    // ── опрос ───────────────────────────────────────────────────────────────
    function requestState() {
        if (reader.busy)
            return;                       // предыдущий запрос ещё в полёте — тик пропущен
        reader.ensurePaths();
        reader.busy = true;
        reader._epoch += 1;
        reader._urls = reader.candidateUrls();
        reader._cursor = 0;
        reader._probed = false;
        reader._probeIndex = -1;
        reader._next();
    }

    function _next() {
        if (!reader.busy)
            return;
        if (reader._cursor >= reader._urls.length) {
            // Ни один файл состояния не открылся. Прежде чем сказать «данных
            // нет», проверяем зондом, что чтение файлов в этой сессии вообще
            // разрешено (иначе виджет молчал бы всегда — так и было, пока Qt не
            // потребовал QML_XHR_ALLOW_FILE_READ=1).
            if (!reader._probed && reader.probeUrl.length) {
                reader._probed = true;
                reader._urls.push(reader.probeUrl);
                reader._probeIndex = reader._urls.length - 1;
            } else {
                reader._emit("", "");
                return;
            }
        }
        var idx = reader._cursor++;
        var url = reader._urls[idx];
        var epoch = reader._epoch;
        var xhr = null;
        try {
            xhr = new XMLHttpRequest();
        } catch (e) {
            reader._emit("", "");
            return;
        }
        reader._xhr = xhr;
        try {
            xhr.onreadystatechange = function () { reader._onReady(xhr, epoch, idx); };
            xhr.onerror = function () { reader._onFailed(epoch, idx); };
            xhr.ontimeout = function () { reader._onFailed(epoch, idx); };
            // Третий аргумент true — запрос АСИНХРОННЫЙ: поток панели не ждёт.
            xhr.open("GET", url, true);
            xhr.send();
        } catch (e2) {
            reader._onFailed(epoch, idx);
        }
    }

    function _onReady(xhr, epoch, idx) {
        if (epoch !== reader._epoch || !reader.busy)
            return;
        var st = -1;
        try { st = xhr.readyState; } catch (e) { return; }
        if (st !== XMLHttpRequest.DONE)
            return;

        var txt = "";
        try { txt = String(xhr.responseText || ""); } catch (e2) { txt = ""; }

        // У file:// в разных сборках Qt код ответа либо 200, либо 0 — оба
        // означают «файл прочитан». Пустой ответ = файла нет/нечитаем.
        if (txt.length) {
            if (idx === reader._probeIndex) {
                reader._emit("", "");     // зонд прочитан: просто нет файла состояния
                return;
            }
            var parsed = parseState(txt);
            reader._emit(parsed.kind, parsed.tip);
            return;
        }
        if (idx === reader._probeIndex) {
            reader._emit("", reader.blockedHint);   // чтение файлов запрещено в сессии
            return;
        }
        reader._next();                   // пробуем следующий путь
    }

    function _onFailed(epoch, idx) {
        if (epoch !== reader._epoch || !reader.busy)
            return;
        if (idx === reader._probeIndex) {
            reader._emit("", reader.blockedHint);
            return;
        }
        reader._next();                   // этот путь не открылся — следующий
    }

    // Разбор JSON: битый файл, массив, null, чужие типы — всё даёт пустое
    // состояние, а не исключение.
    function parseState(text) {
        var out = { kind: "", tip: "" };
        try {
            var j = JSON.parse(String(text));
            if (j && typeof j === "object" && !(j instanceof Array)) {
                if (j.state !== undefined && j.state !== null)
                    out.kind = String(j.state).substring(0, 32);
                if (j.tip !== undefined && j.tip !== null)
                    out.tip = String(j.tip).substring(0, 200);
            }
        } catch (e) {
            out.kind = "";
            out.tip = "";
        }
        return out;
    }

    function _emit(kind, tip) {
        reader.busy = false;
        reader._xhr = null;
        reader._emitted += 1;
        reader.stateUpdated(String(kind || ""), String(tip || ""));
    }

    // Сторож: запрос, который не ответил за requestWatchdog, бросаем. Иначе
    // busy остался бы true навсегда и опрос тихо остановился.
    function _abandon() {
        var x = reader._xhr;
        reader.busy = false;
        reader._xhr = null;
        reader._epoch += 1;               // поздние ответы уже не применятся
        if (x) {
            try { x.onreadystatechange = null; } catch (e) { }
            try { x.onerror = null; } catch (e2) { }
            try { x.ontimeout = null; } catch (e3) { }
            try { x.abort(); } catch (e4) { }
        }
    }

    Timer {
        interval: reader.pollInterval
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: reader.requestState()
    }

    Timer {
        interval: reader.requestWatchdog
        running: reader.busy
        repeat: false
        onTriggered: reader._abandon()
    }
}
