#!/bin/sh
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# check-plasmoid.sh — самопроверка плазмоида KDE БЕЗ живого сеанса Plasma.
#
#   sh plasma/plasmoid/check-plasmoid.sh
#
# Что делает (ничего не устанавливает, панель пользователя не трогает):
#   1) СТАТИЧЕСКИЙ АУДИТ: ищет запрещённые конструкции (Plasma 5, DataSource,
#      engine:'executable', connectSource, вызовы read-state.sh) и проверяет, что
#      состояние читается асинхронным XMLHttpRequest к file://, а интервалы
#      таймеров длинные;
#   2) qmllint по всем .qml плазмоида (если qmllint есть в системе);
#   3) СТЕНД: запускает StateReader.qml в offscreen-режиме (qml6 без дисплея) на
#      подставных файлах состояния — валидный JSON, битый JSON, массив, число,
#      null, пустой файл, отсутствующий файл, серия кликов.
#
# Код возврата 0 — всё чисто; 1 — есть находки (см. строки FAIL).
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
UI="$HERE/org.forest.proxyupdater/contents/ui"
QML_FILES="$UI/main.qml $UI/StateReader.qml"
FAILS=0
CHECKS=0

pass() { CHECKS=$((CHECKS + 1)); printf '  PASS  %s\n' "$*"; }
fail() { CHECKS=$((CHECKS + 1)); FAILS=$((FAILS + 1)); printf '  FAIL  %s\n' "$*"; }
note() { printf '  ..    %s\n' "$*"; }
head2() { printf '\n== %s\n' "$*"; }

# ── 1. статический аудит ────────────────────────────────────────────────────
head2 "1. статический аудит кода"

# 1.1 запрещённые конструкции
# Комментарии в QML — это документация (в них мы ОБЪЯСНЯЕМ, чего нельзя), поэтому
# аудит смотрит на КОД: комментарии вырезаются во временные копии.
STRIPPED_DIR=${TMPDIR:-/tmp}/forest-plasmoid-audit.$$
mkdir -p "$STRIPPED_DIR"
CODE_FILES=""
for f in $QML_FILES; do
    out="$STRIPPED_DIR/$(basename "$f")"
    sed -e 's|//.*||' "$f" | awk '
        /\/\*/ { inc = 1 }
        { line = $0; if (inc) { p = index(line, "*/"); if (p) { line = substr(line, p + 2); inc = 0 } else { next } } print line }
    ' > "$out"
    CODE_FILES="$CODE_FILES $out"
done
trap 'rm -rf "$STRIPPED_DIR"' EXIT

forbid() {  # forbid <регулярка> <человеческое описание>
    if grep -EIn "$1" $CODE_FILES >/dev/null 2>&1; then
        fail "найдено запрещённое: $2"
        grep -EIn "$1" $CODE_FILES | sed 's/^/        /'
    else
        pass "нет $2"
    fi
}

forbid 'plasma5support|P5Support'                    'модуля Plasma 5 (plasma5support / P5Support)'
forbid 'engine[[:space:]]*:[[:space:]]*"executable"' 'engine:"executable"'
forbid 'DataSource'                                  'любого DataSource'
forbid 'connectSource|disconnectSource'              'connectSource/disconnectSource'
forbid 'read-state\.sh'                              'вызова read-state.sh'
forbid 'Process|executable:|QProcess'                'исполнителей процессов'
forbid 'xhr\.open\([^)]*,[^)]*,[[:space:]]*(false|0)' 'синхронного (blocking) XHR'
forbid 'xhr\.send\([^)]'                             'отправки данных на диск/в сеть (xhr.send с телом)'
forbid 'import[[:space:]]+org\.kde\.plasma\.core'     'импорта org.kde.plasma.core (Units/Theme в Plasma 6 живут в Kirigami)'

# 1.2 обязательные конструкции
if grep -q 'XMLHttpRequest' "$UI/StateReader.qml"; then
    pass "состояние читается через XMLHttpRequest (без процессов)"
else
    fail "в StateReader.qml нет XMLHttpRequest"
fi
if grep -q 'xhr.open("GET", url, true)' "$UI/StateReader.qml"; then
    pass "XHR строго асинхронный (третий аргумент true)"
else
    fail "XHR не подтверждён как асинхронный"
fi
if grep -q 'file:///var/lib/forest-proxy-updater' "$UI/StateReader.qml"; then
    pass "системные пути tray_status.json на месте"
else
    fail "нет системных путей tray_status.json"
fi
if grep -q 'function requestState' "$UI/StateReader.qml" \
   && grep -q 'if (reader.busy)' "$UI/StateReader.qml"; then
    pass "есть защита от наложения запросов (busy)"
else
    fail "нет защиты от наложения запросов"
fi
if grep -q 'import org.kde.plasma.plasmoid' "$UI/main.qml"; then
    pass "импорт Plasma 6: org.kde.plasma.plasmoid"
else
    fail "нет импорта org.kde.plasma.plasmoid"
fi
if grep -q 'Kirigami\.' "$UI/main.qml"; then
    pass "Units/Theme берутся из Kirigami (Plasma 6)"
fi

# 1.3 длинные интервалы: единственный «опросный» таймер + два служебных
POLL=$(grep -Eo 'pollInterval:[[:space:]]*[0-9]+' "$UI/main.qml" | grep -Eo '[0-9]+' | head -n1)
if [ -n "${POLL:-}" ] && [ "$POLL" -ge 15000 ] && [ "$POLL" -le 30000 ]; then
    pass "интервал опроса в панели ${POLL} мс (в коридоре 15–30 с)"
else
    fail "интервал опроса вне коридора 15–30 с (найдено: ${POLL:-нет})"
fi
if grep -q 'interval: reader.pollInterval' "$UI/StateReader.qml"; then
    pass "таймер опроса берёт интервал из pollInterval (а не литерал)"
else
    fail "таймер опроса не связан с pollInterval"
fi

# Любые другие литеральные интервалы допустимы только как служебные (сторож
# запроса 8000 мс, антидребезг меню 2500 мс) — они НИЧЕГО не запускают.
BAD_INTERVALS=$(grep -Eho 'interval:[[:space:]]*[0-9]+' $QML_FILES \
    | grep -Eo '[0-9]+' | sort -u | grep -vx -e 8000 -e 2500 || true)
if [ -z "$BAD_INTERVALS" ]; then
    pass "других литеральных интервалов нет (только сторож 8 с и антидребезг 2.5 с)"
else
    fail "неожиданные интервалы таймеров: $(printf '%s' "$BAD_INTERVALS" | tr '\n' ' ')"
fi

# 1.4 панель не пишет на диск
if grep -q 'Component.onCompleted: .*write\|writeFile\|Qt.createQmlObject(.*File' $QML_FILES 2>/dev/null; then
    fail "похоже на запись файла из QML"
else
    pass "записи на диск из QML нет"
fi

# ── 2. qmllint ──────────────────────────────────────────────────────────────
head2 "2. qmllint"
QMLLINT=""
for cand in qmllint qmllint6 /usr/lib/qt6/bin/qmllint; do
    if command -v "$cand" >/dev/null 2>&1; then QMLLINT="$cand"; break; fi
done
if [ -z "$QMLLINT" ]; then
    note "qmllint не найден — проверка синтаксиса пропущена (это НЕ успех)"
else
    for f in $QML_FILES; do
        OUT=$($QMLLINT -I /usr/lib/qt6/qml "$f" 2>&1)
        if [ -z "$OUT" ]; then
            pass "qmllint: $(basename "$f") — без замечаний"
        else
            fail "qmllint: $(basename "$f")"
            printf '%s\n' "$OUT" | sed 's/^/        /'
        fi
    done
fi

# ── 3. стенд в offscreen ────────────────────────────────────────────────────
head2 "3. стенд StateReader в offscreen-режиме (панель не затрагивается)"
QMLRUN=""
for cand in qml6 qml /usr/lib/qt6/bin/qml6; do
    if command -v "$cand" >/dev/null 2>&1; then QMLRUN="$cand"; break; fi
done
FIX=/tmp/forest-plasmoid-test
RUN=/tmp/forest-plasmoid-run
if [ -z "$QMLRUN" ]; then
    note "qml6 не найден — стенд пропущен (это НЕ успех)"
else
    rm -rf "$FIX" "$RUN"
    mkdir -p "$FIX" "$RUN/ui"
    printf '%s' '{"state":"ok","tip":"связь есть"}'            > "$FIX/ok.json"
    printf '%s' '{"state":"ok"}'                              > "$FIX/ok-notip.json"
    printf '%s' '{"state": "ok", "tip": "обрыв'               > "$FIX/broken.json"
    printf '%s' '[1,2,3]'                                     > "$FIX/array.json"
    printf '%s' '42'                                          > "$FIX/number.json"
    printf '%s' 'null'                                        > "$FIX/null.json"
    : > "$FIX/empty.json"
    printf '%s' '{"state":"weird-state","tip":""}'            > "$FIX/weird.json"
    cp "$UI/StateReader.qml" "$RUN/ui/StateReader.qml"
    cp "$HERE/test/harness.qml" "$RUN/harness.qml"
    cp "$HERE/test/harness-blocked.qml" "$RUN/harness-blocked.qml"
    # Стенд А: сеанс, где чтение файлов из QML разрешено (QML_XHR_ALLOW_FILE_READ=1).
    OUT=$(cd "$RUN" && QT_QPA_PLATFORM=offscreen QML_XHR_ALLOW_FILE_READ=1 \
          "$QMLRUN" harness.qml 2>&1)
    printf '%s\n' "$OUT" | sed 's/^/        /'
    if printf '%s' "$OUT" | grep -q 'RESULT OK'; then
        N=$(printf '%s' "$OUT" | grep -o 'SUMMARY passes=[0-9]*' | head -n1 | grep -o '[0-9]*$')
        pass "стенд А: $N проверок пройдены (валидный/битый/пустой JSON, нет файла, серия кликов)"
    else
        fail "стенд А не прошёл"
    fi
    # Стенд Б: дефолтный сеанс БЕЗ переменной — так панель видит по умолчанию.
    OUT2=$(cd "$RUN" && env -u QML_XHR_ALLOW_FILE_READ QT_QPA_PLATFORM=offscreen \
           "$QMLRUN" harness-blocked.qml 2>&1)
    printf '%s\n' "$OUT2" | sed 's/^/        /'
    if printf '%s' "$OUT2" | grep -q 'RESULT OK'; then
        pass "стенд Б: при запрещённом чтении виджет не падает и молчит (нейтральный цвет)"
    else
        fail "стенд Б не прошёл"
    fi
fi

# ── итог ────────────────────────────────────────────────────────────────────
printf '\n== ИТОГ\n'
printf '  проверок: %s, провалов: %s\n' "$CHECKS" "$FAILS"
if [ "$FAILS" -eq 0 ]; then
    printf '  OK — плазмоид не содержит запуска процессов и модулей Plasma 5\n'
    exit 0
fi
printf '  ЕСТЬ ПРОБЛЕМЫ — см. FAIL выше\n'
exit 1
