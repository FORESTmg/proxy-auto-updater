#!/bin/bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# forest-clean-desktop — убрать из панели KDE следы СТАРОГО способа показа
# значка: виджет-плазмоид org.forest.proxyupdater и закреплённый ярлык
# помощника. Запускается ОТ ПОЛЬЗОВАТЕЛЯ, root не нужен.
#
#   forest-clean-desktop           # убрать и показать, что сделано
#   forest-clean-desktop --dry-run # только показать, ничего не менять
#
# Зачем: значок теперь — СВОЁ окно программы в стиле трея (см. src/tray_popup.py),
# плазмоид из пакета убран. Если он был добавлен в панель раньше, панель покажет
# «виджет не найден», а убрать его мышью нельзя: правый клик по виджету
# открывает меню самой панели. Скрипт удаляет записи из конфига панели и
# обновляет кеши KDE. Читает и пишет ТЕМИ ЖЕ инструментами KDE
# (kreadconfig6/kwriteconfig6), поэтому формат конфига не ломается.
set -u

DRY=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "неизвестный аргумент: $a" >&2; exit 2 ;;
    esac
done

CFG="${XDG_CONFIG_HOME:-$HOME/.config}/plasma-org.kde.plasma.desktop-appletsrc"
PLUGIN="org.forest.proxyupdater"
LAUNCHERS="applications:forest-proxy-updater-ui.desktop applications:forest-proxy-updater-menu.desktop"
say() { printf '  %s\n' "$*"; }

if ! command -v kreadconfig6 >/dev/null 2>&1 || \
   ! command -v kwriteconfig6 >/dev/null 2>&1; then
    say "нет инструментов KDE (kreadconfig6/kwriteconfig6) — это не Plasma?"
    exit 0
fi
if [ ! -f "$CFG" ]; then
    say "конфига панели нет ($CFG) — убирать нечего"
    exit 0
fi

if [ "$DRY" = 0 ]; then
    BACKUP="$CFG.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "$CFG" "$BACKUP" || { echo "не удалось сделать копию $CFG" >&2; exit 1; }
    say "копия конфига: $BACKUP"
fi

# ── 1. виджет-плазмоид: ищем секции [Containments][C][Applets][N] с нашим plugin
sections="$(grep -oE '^\[Containments\]\[[0-9]+\]\[Applets\]\[[0-9]+\]' "$CFG" \
            | sort -u)"
found=0
for sec in $sections; do
    nums="$(printf '%s' "$sec" | grep -oE '[0-9]+')"
    C="$(printf '%s' "$nums" | head -1)"
    N="$(printf '%s' "$nums" | tail -1)"
    plug="$(kreadconfig6 --file "$CFG" --group Containments --group "$C" \
            --group Applets --group "$N" --key plugin 2>/dev/null || true)"
    [ "$plug" = "$PLUGIN" ] || continue
    found=1
    say "виджет $PLUGIN: Containments[$C] Applets[$N]"
    [ "$DRY" = 0 ] || continue
    kwriteconfig6 --file "$CFG" --group Containments --group "$C" \
        --group Applets --group "$N" --delete-group
    IDS="$(kreadconfig6 --file "$CFG" --group Containments --group "$C" \
           --key appletIds 2>/dev/null || true)"
    if [ -n "${IDS:-}" ]; then
        NEW="$(printf '%s' "$IDS" | tr ',' '\n' | grep -vx "$N" | paste -sd, -)"
        kwriteconfig6 --file "$CFG" --group Containments --group "$C" \
            --key appletIds -- "$NEW"
        say "  id $N убран из appletIds"
    fi
done
[ "$found" = 1 ] || say "виджета $PLUGIN в панели нет"

# ── 2. закреплённый ярлык помощника (второй значок в панели задач) ──
if grep -q 'applications:forest-proxy-updater' "$CFG"; then
    for C in $(grep -oE '^\[Containments\]\[[0-9]+\]' "$CFG" \
               | grep -oE '[0-9]+' | sort -u); do
        L="$(kreadconfig6 --file "$CFG" --group Containments --group "$C" \
             --key launchers 2>/dev/null || true)"
        [ -n "${L:-}" ] || continue
        NEW="$L"
        for one in $LAUNCHERS; do
            NEW="$(printf '%s' "$NEW" | tr ',' '\n' | grep -vxF "$one" \
                   | paste -sd, -)"
        done
        [ "$NEW" != "$L" ] || continue
        say "Containments[$C]: ярлык помощника убран из launchers="
        [ "$DRY" = 0 ] && kwriteconfig6 --file "$CFG" --group Containments \
            --group "$C" --key launchers -- "$NEW"
    done
else
    say "закреплённого ярлыка помощника нет"
fi

# ── 3. кеши KDE и перечитывание конфига ──
if [ "$DRY" = 0 ]; then
    if command -v kbuildsycoca6 >/dev/null 2>&1; then
        kbuildsycoca6 --noincremental >/dev/null 2>&1 && \
            say "кеш приложений обновлён (kbuildsycoca6)"
    fi
    if command -v qdbus6 >/dev/null 2>&1; then
        qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 && \
            say "KWin перечитал настройки"
    fi
    say "готово. Значок программы теперь ОДИН — своё окно в стиле трея;"
    say "оно поднимается само при входе в сеанс (autostart), ярлык не нужен."
else
    say "это был --dry-run: файл не изменён"
fi
