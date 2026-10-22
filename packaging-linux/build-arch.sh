#!/usr/bin/env bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# build-arch.sh — собирает пакет Arch (`.pkg.tar.zst`) через makepkg.
#
#   sh packaging-linux/build-arch.sh                 # от обычного пользователя
#   FOREST_OUT_DIR=/tmp/out sh build-arch.sh
#   FOREST_MAKEPKG_USER=tester sudo -E sh build-arch.sh   # если запуск от root
#
# Требуется makepkg + fakeroot (Arch: pacman -S --needed base-devel fakeroot или
# хотя бы fakeroot). makepkg ОТКАЗЫВАЕТСЯ работать от root — поэтому при запуске
# от root скрипт сам понижает права до непривилегированного пользователя.
# Сборщик использует ${BASH_SOURCE}: под sh (в Debian/Ubuntu это dash) будет
# «Bad substitution», поэтому перезапускаем себя под bash.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -eu

. "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"

ARCH_DIR="$PACKAGING_LINUX_DIR/arch"

step "проверка входов"
check_inputs
require_ui
require_tool makepkg "это сборка Arch: нужен пакет makepkg (base-devel)"
require_tool fakeroot "Arch: pacman -S fakeroot"
say "ELF:      $ELF ($(du -h "$ELF" | cut -f1))"
say "версия:   $VERSION-$RELEASE"
say "вывод:    $OUT_DIR"

ensure_dirs
STAGE="$WORK_DIR/arch-payload"
SRC="$WORK_DIR/arch-src"

# ── makepkg нельзя от root ──────────────────────────────────────────────────
BUILD_USER=""
if [ "$(id -u)" = 0 ]; then
    BUILD_USER="${FOREST_MAKEPKG_USER:-}"
    if [ -z "$BUILD_USER" ]; then
        BUILD_USER="$(awk -F: '$3 >= 1000 && $3 < 60000 { print $1; exit }' /etc/passwd)"
    fi
    [ -n "$BUILD_USER" ] || die "запуск от root, а непривилегированного пользователя нет.
  Создайте пользователя или запустите сборку от него: bash build-arch.sh"
    id "$BUILD_USER" >/dev/null 2>&1 || die "нет пользователя '$BUILD_USER'"
    say "запуск от root -> makepkg пойдёт от пользователя '$BUILD_USER' (makepkg от root запрещён)"
fi

run_makepkg() {   # run_makepkg <аргументы makepkg...>
    if [ -z "$BUILD_USER" ]; then
        ( cd "$SRC" && makepkg "$@" )
    else
        runuser -u "$BUILD_USER" -- sh -c "cd '$SRC' && exec makepkg $*"
    fi
}

step "раскладка пакета"
stage_payload "$STAGE"
rm -rf "$SRC"
mkdir -p "$SRC"
# PKGBUILD ожидает «плоские» файлы рядом с собой — кладём их из общего дерева,
# чтобы раскладка внутри пакета была ровно та же, что в .deb и .rpm.
install -m 0755 "$STAGE$LIBDIR/$APP"                 "$SRC/forest-proxy-updater"
install -m 0644 "$STAGE$UNITDIR/$APP.service"        "$SRC/forest-proxy-updater.service"
install -m 0644 "$STAGE$UNITDIR/$APP-refresh.service" "$SRC/forest-proxy-updater-refresh.service"
install -m 0644 "$STAGE$UNITDIR/$APP.timer"          "$SRC/forest-proxy-updater.timer"
install -m 0644 "$STAGE$CONFDIR/config.json"         "$SRC/config.json"
install -m 0644 "$STAGE$DOCDIR/README.md"            "$SRC/README.md"
install -m 0644 "$STAGE$DOCDIR/INSTALL.md"           "$SRC/INSTALL.md"
install -m 0644 "$STAGE$DOCDIR/INSTALL.ru.md"        "$SRC/INSTALL.ru.md"
install -m 0644 "$STAGE$DOCDIR/PACKAGE-README.md"    "$SRC/PACKAGE-README.md"
install -m 0644 "$STAGE$DOCDIR/copyright"            "$SRC/copyright"
install -m 0644 "$STAGE$DOCDIR/TERMS.md"             "$SRC/TERMS.md"
install -m 0644 "$STAGE$DOCDIR/TERMS.ru.md"          "$SRC/TERMS.ru.md"
install -m 0644 "$STAGE$LICDIR/COPYING"              "$SRC/COPYING"
install -m 0644 "$ARCH_DIR/PKGBUILD"                 "$SRC/PKGBUILD"
install -m 0644 "$ARCH_DIR/forest-proxy-updater.install" "$SRC/forest-proxy-updater.install"
# Помощник интерфейса, ярлыки и значок — «плоско», как и остальные входы.
install -m 0755 "$STAGE$LIBDIR/$UI_NAME"                    "$SRC/forest-ui"
install -m 0644 "$STAGE$PREFIX/share/applications/$APP.desktop" \
                                                  "$SRC/forest-proxy-updater.desktop"
install -m 0644 "$STAGE$PREFIX/share/applications/$APP-ui.desktop" \
                                                  "$SRC/forest-proxy-updater-ui.desktop"
install -m 0644 "$STAGE$PREFIX/share/icons/hicolor/256x256/apps/$APP.png" \
                                                  "$SRC/forest-proxy-updater.png"
# Автозапуска у значка нет: его поднимает САМА программа в сеансе пользователя
# (watch.py -> ui_helper.ensure). Уборка панели — «плоско», раскладку делает package().
install -m 0755 "$STAGE$LIBDIR/$CLEAN_NAME"       "$SRC/forest-clean-desktop"
# версия из окружения (если переопределена)
sed -i "s|^pkgver=.*|pkgver=$VERSION|; s|^pkgrel=.*|pkgrel=$RELEASE|" "$SRC/PKGBUILD"
say "PKGBUILD: $SRC/PKGBUILD (pkgver=$VERSION, pkgrel=$RELEASE)"

if [ -n "$BUILD_USER" ]; then
    chown -R "$BUILD_USER" "$SRC"
fi

step "реальные контрольные суммы (makepkg -g)"
SUMS="$(run_makepkg -g 2>/dev/null)" || die "makepkg -g не смог посчитать хеши"
case "$SUMS" in
    *sha256sums=*) printf '%s\n' "$SUMS" >> "$SRC/PKGBUILD"
                   say "хеши дописаны в PKGBUILD (последнее присваивание побеждает)" ;;
    *) die "неожиданный ответ makepkg -g: $SUMS" ;;
esac

step "сборка makepkg"
run_makepkg -f --noconfirm >"$WORK_DIR/makepkg.log" 2>&1 || {
    tail -n 40 "$WORK_DIR/makepkg.log" >&2
    die "makepkg не справился (полный лог: $WORK_DIR/makepkg.log)"
}

PKGFILE="$(find "$SRC" -maxdepth 1 -name "*.pkg.tar.zst" -type f | head -n1)"
[ -n "$PKGFILE" ] || die "makepkg не создал .pkg.tar.zst"
DEST="$OUT_DIR/$(basename "$PKGFILE")"
cp -f "$PKGFILE" "$DEST"

step "проверка артефакта"
if command -v pacman >/dev/null 2>&1; then
    pacman -Qip "$DEST" | sed 's/^/  /' | head -n 25
    printf '\n  файлы в пакете:\n'
    # pacman 7: для работы с файлом нужен флаг -p (--file)
    pacman -Qlp "$DEST" | awk '{print "  " $2}'
else
    say "pacman недоступен — проверка метаданных пропущена"
fi

report_artifact "$DEST"
