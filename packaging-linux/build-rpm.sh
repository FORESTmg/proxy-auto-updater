#!/usr/bin/env bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# build-rpm.sh — собирает .rpm (Fedora / RHEL / Rocky / Alma / openSUSE).
#
#   sh packaging-linux/build-rpm.sh
#   FOREST_OUT_DIR=/tmp/out sh build-rpm.sh
#
# Требуется rpmbuild: openSUSE — `zypper install rpm-build`, Fedora/RHEL —
# `dnf install rpm-build`. Rocky/CIQ-образ без rpmbuild собрать .rpm не может —
# собирайте в openSUSE или Debian/Ubuntu с пакетом rpm.
# Сборщик использует ${BASH_SOURCE}: под sh (в Debian/Ubuntu это dash) будет
# «Bad substitution», поэтому перезапускаем себя под bash, как написано в
# README (`sh packaging-linux/build-rpm.sh` обязано работать).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -eu

. "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"

RPM_DIR="$PACKAGING_LINUX_DIR/rpm"

step "проверка входов"
check_inputs
require_ui
require_tool rpmbuild "openSUSE: zypper install rpm-build; Fedora: dnf install rpm-build"
require_tool rpm "нужен для проверки результата"
say "ELF:      $ELF ($(du -h "$ELF" | cut -f1))"
say "версия:   $VERSION-$RELEASE"
say "вывод:    $OUT_DIR"

ensure_dirs
TOP="$WORK_DIR/rpm"

step "рабочее дерево rpmbuild"
rm -rf "$TOP"
mkdir -p "$TOP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
# Готовое дерево файлов кладём прямо в SOURCES: спецификация копирует его в
# buildroot. Так же сделано в тестовом пакете, так что путь проверен.
stage_payload "$TOP/SOURCES/payload"
sed -e "s|@VERSION@|$VERSION|g" \
    -e "s|@RELEASE@|$RELEASE|g" \
    -e "s|@SUMMARY@|$SUMMARY|g" \
    -e "s|@HOMEPAGE@|$HOMEPAGE|g" \
    "$RPM_DIR/forest-proxy-updater.spec.in" > "$TOP/SPECS/$APP.spec"
say "spec: $TOP/SPECS/$APP.spec"

step "сборка rpmbuild"
LOG="$WORK_DIR/rpm/rpmbuild.log"
if rpmbuild -bb --define "_topdir $TOP" "$TOP/SPECS/$APP.spec" >"$LOG" 2>&1; then
    say "rpmbuild: OK"
else
    tail -n 40 "$LOG" >&2
    die "rpmbuild не справился (полный лог: $LOG)"
fi

RPM_FILE="$(find "$TOP/RPMS" -name '*.rpm' -type f | head -n1)"
[ -n "$RPM_FILE" ] || die "rpmbuild не создал .rpm"
DEST="$OUT_DIR/$(basename "$RPM_FILE")"
cp -f "$RPM_FILE" "$DEST"

step "проверка артефакта"
rpm -qip "$DEST" | sed 's/^/  /' | head -n 20
printf '\n  файлы в пакете:\n'
rpm -qpl "$DEST" | sed 's/^/  /'

report_artifact "$DEST"
