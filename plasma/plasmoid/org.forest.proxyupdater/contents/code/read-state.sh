#!/bin/sh
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# read-state.sh — печатает САМЫЙ СВЕЖИЙ tray_status.json, который видит
# пользователь панели, и ничего больше. Читает его плазмоид KDE
# (plasma/plasmoid/org.forest.proxyupdater/contents/ui/main.qml).
#
# Почему перебор путей, а не один: файл состояния пишет сторож, а каталог
# состояния зависит от того, КТО его запустил:
#   * помощник/пользовательский запуск — $XDG_STATE_HOME/forest-proxy-updater
#     (по умолчанию ~/.local/state/forest-proxy-updater);
#   * системная служба из пакета — XDG_STATE_HOME=/var/lib/forest-proxy-updater/state
#     (см. packaging-linux/system/forest-proxy-updater.service), то есть
#     /var/lib/forest-proxy-updater/state/forest-proxy-updater;
#   * старые/ручные раскладки — /var/lib/forest-proxy-updater[/state].
# Из найденных берём самый СВЕЖИЙ по mtime: если работают оба (служба и
# помощник), панель показывает то состояние, которое обновлялось последним.
#
# Ничего не нашли — печатаем пусто: плазмоид покажет нейтральный цвет, а не
# выдуманное «связь есть».
set -u

for p in \
    "${FOREST_STATE_DIR:-}/tray_status.json" \
    "${XDG_STATE_HOME:-$HOME/.local/state}/forest-proxy-updater/tray_status.json" \
    "$HOME/.local/state/forest-proxy-updater/tray_status.json" \
    /var/lib/forest-proxy-updater/state/forest-proxy-updater/tray_status.json \
    /var/lib/forest-proxy-updater/state/tray_status.json \
    /var/lib/forest-proxy-updater/tray_status.json
do
    [ -n "$p" ] || continue
    [ -r "$p" ] || continue
    printf '%s\n' "$p"
done | while IFS= read -r f; do
    # «время модификации + путь»: сортируем по времени, берём первый
    if stat -c '%Y %n' "$f" 2>/dev/null; then :; else printf '0 %s\n' "$f"; fi
done | sort -nr | head -n 1 | cut -d' ' -f2- | while IFS= read -r f; do
    [ -n "$f" ] && cat "$f"
done
