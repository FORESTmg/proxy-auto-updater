#!/usr/bin/env bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# build-deb.sh — собирает .deb для Debian / Ubuntu / Linux Mint (и любых
# производных с dpkg).
#
#   sh packaging-linux/build-deb.sh                 # -> packaging-linux/out/
#   FOREST_OUT_DIR=/tmp/out sh build-deb.sh         # свой каталог вывода
#   FOREST_WORK_DIR=/tmp/w  sh build-deb.sh         # свой рабочий каталог
#
# Требуется dpkg-deb (Debian/Ubuntu: предустановлен). Сборка НЕ требует root и
# НЕ требует сети: внутрь кладётся уже готовый ELF со вшитым ядром.
# Сборщик использует ${BASH_SOURCE}: под sh (в Debian/Ubuntu это dash) будет
# «Bad substitution», поэтому перезапускаем себя под bash, как написано в
# README (`sh packaging-linux/build-deb.sh` обязано работать).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -eu

. "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"

DEB_DIR="$PACKAGING_LINUX_DIR/deb"

step "проверка входов"
check_inputs
require_tool dpkg-deb "это Debian/Ubuntu; на другой системе сборку .deb не сделать"
say "ELF:      $ELF ($(du -h "$ELF" | cut -f1))"
say "версия:   $VERSION"
say "вывод:    $OUT_DIR"

ensure_dirs
ROOT="$WORK_DIR/deb/root"

step "раскладка пакета"
stage_payload "$ROOT"
mkdir -p "$ROOT/DEBIAN"
# conffiles: конфиг принадлежит пакету, но правки пользователя dpkg сохраняет
# и удаляет только при purge.
printf '/etc/%s/config.json\n' "$APP" > "$ROOT/DEBIAN/conffiles"

step "control и скрипты пакета"
INSTALLED_SIZE="$(du -sk "$ROOT" | cut -f1)"
sed -e "s|@VERSION@|$VERSION|g" \
    -e "s|@MAINT@|$MAINT|g" \
    -e "s|@HOMEPAGE@|$HOMEPAGE|g" \
    -e "s|@INSTALLED_SIZE@|$INSTALLED_SIZE|g" \
    "$DEB_DIR/control.in" > "$ROOT/DEBIAN/control"
install -m 0755 "$DEB_DIR/postinst" "$ROOT/DEBIAN/postinst"
install -m 0755 "$DEB_DIR/prerm"    "$ROOT/DEBIAN/prerm"
install -m 0755 "$DEB_DIR/postrm"   "$ROOT/DEBIAN/postrm"
say "Installed-Size: ${INSTALLED_SIZE} КиБ"
say "скрипты: postinst, prerm, postrm"

# md5sums по всем файлам данных (без DEBIAN/) — то, что ожидает dpkg-verify.
( cd "$ROOT" && find . -type f ! -path './DEBIAN/*' -printf '%P\0' \
    | sort -z | xargs -0 md5sum ) > "$ROOT/DEBIAN/md5sums"
say "md5sums: $(wc -l < "$ROOT/DEBIAN/md5sums") файлов"

step "сборка dpkg-deb"
DEB="$OUT_DIR/${APP}_${VERSION}_amd64.deb"
rm -f "$DEB"
# -Zxz: сжатие xz понимают все версии dpkg (zstd моложе); --root-owner-group —
# все файлы принадлежат root:root без sudo.
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$DEB" >/dev/null

step "проверка артефакта"
dpkg-deb --info "$DEB" | sed 's/^/  /'
printf '\n  содержимое (первые строки):\n'
dpkg-deb --contents "$DEB" | awk '{print "  " $1, $2, $6}' | head -n 20

report_artifact "$DEB"
