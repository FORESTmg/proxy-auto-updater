#!/usr/bin/env bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# build-appimage.sh — собирает универсальный AppImage (установка НЕ требуется).
#
#   sh packaging-linux/build-appimage.sh
#   FOREST_APPIMAGETOOL=/usr/local/bin/appimagetool sh build-appimage.sh
#
# Нужны: python3 (иконка), mksquashfs (squashfs-tools) — и appimagetool
# (скачается сам, ~15 МБ, если не указан FOREST_APPIMAGETOOL; интернет нужен
# только для этого). FUSE не требуется: и сборка, и проверка идут через
# --appimage-extract-and-run.
# Сборщик использует ${BASH_SOURCE}: под sh (в Debian/Ubuntu это dash) будет
# «Bad substitution», поэтому перезапускаем себя под bash.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -eu

. "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"

APPIMAGE_DIR="$PACKAGING_LINUX_DIR/appimage"
APPDIR_NAME="ForestProxyUpdater.AppDir"
FINAL_NAME="ForestProxyUpdater-$VERSION-x86_64.AppImage"

step "проверка входов"
check_inputs
require_tool python3 "нужен для генерации иконки (стандартная библиотека)"
require_tool mksquashfs "squashfs-tools: Debian/Ubuntu: apt install squashfs-tools"
say "ELF:      $ELF ($(du -h "$ELF" | cut -f1))"
say "версия:   $VERSION"
say "вывод:    $OUT_DIR"

ensure_dirs
APPDIR="$WORK_DIR/appimage/$APPDIR_NAME"

step "appimagetool"
TOOL="${FOREST_APPIMAGETOOL:-}"
if [ -z "$TOOL" ]; then
    TOOL="$(command -v appimagetool 2>/dev/null || true)"
fi
if [ -z "$TOOL" ]; then
    VENDOR_DIR="$WORK_DIR/vendor"
    mkdir -p "$VENDOR_DIR"
    TOOL="$VENDOR_DIR/appimagetool-x86_64.AppImage"
    if [ ! -s "$TOOL" ]; then
        require_tool curl "нужен для скачивания appimagetool"
        say "скачиваю appimagetool (~15 МБ)…"
        ok=0
        for url in \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
            "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
        do
            if curl -L -s -m 300 -o "$TOOL.part" "$url" && [ -s "$TOOL.part" ] \
               && head -c 4 "$TOOL.part" | grep -q ELF; then
                mv "$TOOL.part" "$TOOL"; ok=1; say "источник: $url"; break
            fi
        done
        rm -f "$TOOL.part"
        [ "$ok" = 1 ] || die "не удалось скачать appimagetool.
  Скачайте вручную и повторите с FOREST_APPIMAGETOOL=/путь/appimagetool"
    fi
fi
chmod +x "$TOOL"
say "appimagetool: $TOOL"

step "AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
         "$APPDIR/usr/share/doc/$APP" "$APPDIR/usr/share/licenses/$APP" \
         "$APPDIR/usr/share/$APP"

# 1. программа (Python, ядро xray и гео-базы внутри одного ELF)
install -m 0755 "$ELF" "$APPDIR/usr/bin/$APP"
# 2. точка входа
install -m 0755 "$APPIMAGE_DIR/AppRun" "$APPDIR/AppRun"
# 3. .desktop + иконка (без GUI, но AppImage требует их по спецификации)
install -m 0644 "$APPIMAGE_DIR/$APP.desktop" "$APPDIR/$APP.desktop"
install -m 0644 "$APPIMAGE_DIR/$APP.desktop" "$APPDIR/usr/share/applications/$APP.desktop"
python3 "$PACKAGING_LINUX_DIR/scripts/make_icon_linux.py" "$APPDIR/$APP.png" 256
install -m 0644 "$APPDIR/$APP.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP.png"
# 4. документация, лицензия, пример конфига
install -m 0644 "$REPO_DIR/docs/LINUX-INSTALL.md"    "$APPDIR/usr/share/doc/$APP/INSTALL.md"
install -m 0644 "$REPO_DIR/docs/LINUX-INSTALL.ru.md" "$APPDIR/usr/share/doc/$APP/INSTALL.ru.md"
install -m 0644 "$REPO_DIR/docs/TERMS.md"            "$APPDIR/usr/share/doc/$APP/TERMS.md"
install -m 0644 "$REPO_DIR/docs/TERMS.ru.md"         "$APPDIR/usr/share/doc/$APP/TERMS.ru.md"
install -m 0644 "$PACKAGING_LINUX_DIR/doc/PACKAGE-README.md" "$APPDIR/usr/share/doc/$APP/PACKAGE-README.md"
install -m 0644 "$LINUX_README"    "$APPDIR/usr/share/doc/$APP/README.md"
install -m 0644 "$PACKAGING_LINUX_DIR/doc/copyright" "$APPDIR/usr/share/doc/$APP/copyright"
install -m 0644 "$LICENSE_FILE"    "$APPDIR/usr/share/licenses/$APP/COPYING"
install -m 0644 "$LICENSE_FILE"    "$APPDIR/usr/share/doc/$APP/COPYING"
install -m 0644 "$EXAMPLE_CONFIG"  "$APPDIR/usr/share/$APP/config.example.json"
find "$APPDIR" -type d -exec chmod 0755 {} +
say "файлов в AppDir: $(find "$APPDIR" -type f | wc -l)"

step "сборка AppImage (appimagetool, extract-and-run — без FUSE)"
DEST="$OUT_DIR/$FINAL_NAME"
rm -f "$DEST"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream \
    "$APPDIR" "$DEST" >"$WORK_DIR/appimage.log" 2>&1 || {
    tail -n 30 "$WORK_DIR/appimage.log" >&2
    die "appimagetool не справился (полный лог: $WORK_DIR/appimage.log)"
}
[ -s "$DEST" ] || die "appimagetool не создал файл"

step "проверка артефакта"
file "$DEST" | sed 's/^/  /'
# запуск ВНУТРИ AppImage: автотесты продукта (не требует сети и ничего не меняет)
say "запуск автотестов внутри AppImage (--appimage-extract-and-run --selftest)…"
if timeout 600 "$DEST" --appimage-extract-and-run --selftest >"$WORK_DIR/appimage-selftest.log" 2>&1; then
    tail -n 3 "$WORK_DIR/appimage-selftest.log" | sed 's/^/  /'
    say "автотесты внутри AppImage: OK"
    echo "  (полный вывод: $WORK_DIR/appimage-selftest.log)" >/dev/null
else
    tail -n 15 "$WORK_DIR/appimage-selftest.log" | sed 's/^/  /'
    warn "автотесты внутри AppImage не прошли (см. $WORK_DIR/appimage-selftest.log)"
fi

report_artifact "$DEST"
