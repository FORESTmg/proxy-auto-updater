#!/usr/bin/env bash
# FOREST's Proxy Auto-updater
# Copyright (C) 2026 FOREST
# SPDX-License-Identifier: GPL-3.0-only
#
# check-eol.sh — проверка переводов строк в файлах, которые уходят в пакеты.
#
#   sh packaging-linux/check-eol.sh           # только проверить (код 1, если есть CR)
#   sh packaging-linux/check-eol.sh --fix     # перевести найденное в LF
#
# Зачем: скрипт с CRLF, отданный как `curl … | bash`, падает на первом же
# `$'\r': command not found`, а postinst/.install с CR ломают установку пакета.
# Причина появления — Windows-чекаут с core.autocrlf=true; репозиторий держит
# .gitattributes (`* text=auto eol=lf`), эта проверка страхует от обхода.
set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

# только то, что реально исполняется в Linux или попадает в пакет
list_files() {
    cd "$REPO_DIR" || exit 1
    {
        git ls-files 2>/dev/null
        find . -path ./.git -prune -o -type f \
            \( -name '*.sh' -o -name '*.install' -o -name '*.desktop' \
               -o -name 'postinst' -o -name 'prerm' -o -name 'postrm' \
               -o -name 'preinst' -o -name 'PKGBUILD' \) -print 2>/dev/null \
            | sed 's|^\./||'
    } | grep -viE '\.(png|ico|exe|zst|gz|deb|rpm|jpg|jpeg|bmp|svg|woff2?|ttf)$' \
      | grep -viE '\.build-venv/|node_modules/' | sort -u
}

bad=0
total=0
while IFS= read -r f; do
    [ -f "$REPO_DIR/$f" ] || continue
    case "$f" in *.sh|*.install|*.desktop|postinst|prerm|postrm|preinst|PKGBUILD|*.spec|*.spec.in|*.service|*.timer) ;; *) continue ;; esac
    total=$((total + 1))
    if LC_ALL=C grep -q $'\r' "$REPO_DIR/$f" 2>/dev/null; then
        bad=$((bad + 1))
        if [ "$FIX" = 1 ]; then
            # через python: не зависит от sed -i (в BSD/macOS другой синтаксис)
            python3 - "$REPO_DIR/$f" <<'PY'
import sys
p = sys.argv[1]
b = open(p, "rb").read()
open(p, "wb").write(b.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
PY
            printf '  починено: %s\n' "$f"
        else
            printf '  CRLF: %s\n' "$f"
        fi
    fi
done <<EOF
$(list_files)
EOF

if [ "$bad" != 0 ] && [ "$FIX" = 0 ]; then
    printf '\nОШИБКА: файлов с CR: %s из %s проверенных\n' "$bad" "$total" >&2
    exit 1
fi
if [ "$bad" != 0 ]; then
    printf '\nпереведено в LF: %s (проверьте git diff)\n' "$bad"
else
    printf '  переводы строк: LF, проверено файлов: %s\n' "$total"
fi
