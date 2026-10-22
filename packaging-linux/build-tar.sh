#!/usr/bin/env bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# build-tar.sh — собирает портативный .tar.gz (любой дистрибутив Linux, x86_64).
#
#   sh packaging-linux/build-tar.sh
#   FOREST_OUT_DIR=/tmp/out sh build-tar.sh
#
# Внутри: самодостаточный ELF (Python, ядро xray и гео-базы внутри), install.sh /
# uninstall.sh, шаблоны юнитов systemd, конфиг по умолчанию, документация,
# лицензия GPL-3.0. Никаких зависимостей, кроме curl, и никакой сети при сборке.
# Сборщик использует ${BASH_SOURCE}: под sh (в Debian/Ubuntu это dash) будет
# «Bad substitution», поэтому перезапускаем себя под bash.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -eu

. "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"

TAR_DIR="$PACKAGING_LINUX_DIR/tar"
ROOTNAME="$APP-$VERSION-linux-x86_64"

step "проверка входов"
check_inputs
require_tool tar
require_tool gzip
say "ELF:      $ELF ($(du -h "$ELF" | cut -f1))"
say "версия:   $VERSION"
say "вывод:    $OUT_DIR"

ensure_dirs
DEST_DIR="$WORK_DIR/tar/$ROOTNAME"

step "раскладка архива"
rm -rf "$WORK_DIR/tar"
mkdir -p "$DEST_DIR/systemd" "$DEST_DIR/doc"
install -m 0755 "$ELF"                 "$DEST_DIR/$APP"
install -m 0755 "$TAR_DIR/install.sh"  "$DEST_DIR/install.sh"
install -m 0755 "$TAR_DIR/uninstall.sh" "$DEST_DIR/uninstall.sh"
install -m 0644 "$EXAMPLE_CONFIG"      "$DEST_DIR/config.example.json"
for u in "$UNIT_SRC_DIR"/*.service "$UNIT_SRC_DIR"/*.timer; do
    install -m 0644 "$u" "$DEST_DIR/systemd/$(basename "$u")"
done
install -m 0644 "$LINUX_README"      "$DEST_DIR/doc/README.md"
install -m 0644 "$REPO_DIR/docs/LINUX-INSTALL.md"    "$DEST_DIR/doc/INSTALL.md"
install -m 0644 "$REPO_DIR/docs/LINUX-INSTALL.ru.md" "$DEST_DIR/doc/INSTALL.ru.md"
install -m 0644 "$REPO_DIR/docs/TERMS.md"            "$DEST_DIR/doc/TERMS.md"
install -m 0644 "$REPO_DIR/docs/TERMS.ru.md"         "$DEST_DIR/doc/TERMS.ru.md"
install -m 0644 "$PACKAGING_LINUX_DIR/doc/PACKAGE-README.md" "$DEST_DIR/doc/PACKAGE-README.md"
install -m 0644 "$PACKAGING_LINUX_DIR/doc/copyright" "$DEST_DIR/doc/copyright"
install -m 0644 "$LICENSE_FILE"      "$DEST_DIR/doc/COPYING"
install -m 0644 "$PACKAGING_LINUX_DIR/doc/PACKAGE-README.md" "$DEST_DIR/README.md"
say "файлов: $(find "$DEST_DIR" -type f | wc -l)"

step "проверка скриптов"
sh -n "$DEST_DIR/install.sh"   || die "синтаксис install.sh"
sh -n "$DEST_DIR/uninstall.sh" || die "синтаксис uninstall.sh"
"$DEST_DIR/install.sh" --help >/dev/null || die "install.sh --help не работает"
say "install.sh / uninstall.sh: синтаксис и --help в порядке"

step "сборка архива"
DEST="$OUT_DIR/$ROOTNAME.tar.gz"
rm -f "$DEST"
# --owner/--group/--numeric-owner: архив не зависит от того, кто собирал
( cd "$WORK_DIR/tar" && tar --owner=0 --group=0 --numeric-owner -czf "$DEST" "$ROOTNAME" ) \
    || die "tar не справился"

step "проверка артефакта"
tar -tzf "$DEST" | sed 's/^/  /'
printf '\n  дерево на верхнем уровне:\n'
tar -tzf "$DEST" | awk -F/ 'NF<=2' | sed 's/^/  /'

report_artifact "$DEST"
