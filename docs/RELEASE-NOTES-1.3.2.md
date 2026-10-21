# v1.3.2 — однофайловый Windows-релиз, меню по правой кнопке (XEmbed и плазмоид KDE), гейт уведомлений / one-file Windows release, right-click menu (XEmbed and KDE plasmoid), notification gate

**Дата / Date:** 2026-10-21 · **Тег / Tag:** `v1.3.2`
**Артефакты / Artifacts:** `ForestProxyUpdater.exe` (Windows — **один** файл,
помощник интерфейса внутри), `forest-ui.exe` (помощник Windows, отдельным ассетом
для ручных установок), `forest-proxy-updater-linux-x86_64` (Linux ELF),
`forest-ui-linux-x86_64` (помощник Linux), пакеты `.deb` / `.rpm` / Arch
`.pkg.tar.zst` / AppImage / `.tar.gz`, сводная `SHA256SUMS`.
Версия сведена к одной — **1.3.2**: `packaging/version_info.txt`
(`filevers`/`prodvers` = `1, 3, 2, 0`, `FileVersion` = `1.3.2.0`,
`ProductVersion` = `1.3.2`) и `packaging-linux/lib/common.sh` (`VERSION="1.3.2"`).

> **ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** проверка Linux-пакетов выполнялась в среде **WSL**
> (Linux внутри Windows), а не на установленной системе на реальном железе. Меню и
> уведомления графического интерфейса на Linux проверены логически, в offscreen-режиме
> (Qt 6.9) и на **живой XEmbed-панели** (Xvfb + stalonetray), но **НЕ проверены на
> живом рабочем столе KDE Plasma / GNOME** — в том числе плазмоид и правая кнопка в
> панели Plasma. AppImage проверен распаковкой (`--appimage-extract-and-run`): штатное
> монтирование (FUSE) в WSL недоступно и не проверялось. Windows-часть проверена на
> живом рабочем столе.
>
> **TESTING SCOPE (EN):** the Linux packages were verified inside **WSL** (Linux on
> Windows), not on a real installation on bare metal. The GUI menu and notifications were
> verified logically, in offscreen mode (Qt 6.9) and on a **live XEmbed panel**
> (Xvfb + stalonetray), but were **NOT verified on a live KDE Plasma / GNOME desktop** —
> including the plasmoid and the right click inside a Plasma panel. The AppImage was
> verified by extraction (`--appimage-extract-and-run`); normal FUSE mounting is
> unavailable in WSL and was not tested. The Windows part was verified on a live desktop.

---

## 🇷🇺 Русский

### Что изменилось

1. **Windows теперь — ОДИН файл.** Помощник интерфейса (`forest-ui.exe`, ~36 МБ:
   значок в трее, меню, уведомления) больше не второй файл рядом, а данные внутри
   `ForestProxyUpdater.exe` (`ui_bundle/forest-ui.exe` + `ui_bundle/ui-bundle.json`).
   Пользователь скачивает, запускает и получает значок с первого запуска.
2. **Распаковка помощника со сверкой SHA-256.** При `--run`/`--ensure`/`--install`
   программа сравнивает `<root>\forest-ui.exe` с хешем из встроенного манифеста.
   Совпало — копии нет (одна строка в лог, ~0,05 с). Не совпало или файла нет —
   копия в `.new-<pid>`, проверка хеша КОПИИ, `os.replace`, проверка хеша того, что
   уже на диске, и запуск. Занятый или недоступный на запись файл не превращается в
   диалог ошибки: одна строка в лог и работа со **встроенным значком**.
3. **Правый клик по значку снова показывает НАШЕ меню.** В протоколе
   StatusNotifier панель забирает правый клик себе (просит у приложения описание
   меню через `com.canonical.dbusmenu` и рисует его сама), поэтому помощник больше
   **не передаёт `QMenu` в трей** (`QSystemTrayIcon.setContextMenu` убран). На
   **XEmbed**-панелях (XFCE, MATE, Cinnamon, LXQt) правый клик (`ActivationReason.Context`)
   открывает наше анимированное окно `ui_menu.PopupMenu` у геометрии значка — как в
   Windows; левый клик по-прежнему не открывает ничего.
4. **Для KDE Plasma — панельный виджет (плазмоид).** Там, где панель оставляет
   правый клик себе, наш значок и меню даёт виджет
   `plasma/plasmoid/org.forest.proxyupdater` (ставится в
   `/usr/share/plasma/plasmoids/`): он рисует наш значок с цветом состояния из
   `tray_status.json` и запускает новый режим `forest-ui --menu-popup [x y]` —
   отдельный процесс БЕЗ второго значка в трее, который показывает НАШЕ меню.
   Если тема оставляет правый клик меню самого виджета — `Ctrl+левый клик`
   задокументированный запасной путь.
5. **`notify.enabled = false` действительно глушит всё.** Настройка проверялась в одном
   месте, поэтому карточка провала восстановления, карточка процесса или ошибки команды
   всё равно появлялась. Теперь ВСЕ карточки проходят один гейт в `Ui.show_toast()`:
   при выключенных уведомлениях не показывается ничего (события, карточки процессов,
   ошибки службы), и только явное действие пользователя (`--show`/`--show-all`,
   «Проверить уведомление», «Сведения») карточку рисует.
6. **«Нет Qt» больше не роняет помощник трейсбеком.** `import ui_style as US` стоял
   внутри `if QT_OK:`, а модульный блок `NT_* = US.*` ниже выполнялся всегда — без Qt
   импорт `forest_ui` падал `NameError: name 'US' is not defined`. `ui_style` —
   чистый stdlib, поэтому импорт вынесен из-под условия: помощник печатает
   `forest-ui: Qt недоступен (…) — помощник не запущен` и выходит с кодом 3 (как и
   ожидает `src/ui_helper.py`). `libglvnd` остаётся в `optdepends`, теперь с прямо
   названным следствием.
7. **Журнал (journald) стал читаемым: UTF-8 вместо `?????`.** `safestdio.say()`
   кодировал строку в `ascii + errors="replace"` (защита эпохи cp1251-консоли
   Windows). На Linux такой консоли нет: journald ждёт UTF-8, поэтому каждая русская
   строка превращалась в `?????`, хотя файловый лог оставался корректным. Теперь
   `say()` печатает UTF-8 на POSIX (и на Windows — если поток уже UTF-8), ASCII-фолбэк
   сохранён для cp1251-сборки и отключается принудительно через `FOREST_STDOUT_ASCII=1`.
8. **KDE определяется как KDE, а не как GNOME.** Старая проверка «это GNOME» была
   «есть `gsettings`» — на KDE он тоже есть (glib2), поэтому система-служба каждые
   30–60 с настраивала системный прокси не тем механизмом и писала предупреждение,
   которого никто не видел: `gsettings set` под root без шины сессии молча уходил в
   бэкенд `memory`. Теперь рабочий стол определяется явно (`XDG_CURRENT_DESKTOP`,
   `KDE_FULL_SESSION`, `DESKTOP_SESSION`, а при пустом окружении — по процессам
   `kwin_wayland`/`plasmashell`/`gnome-shell`), **запись проверяется чтением обратно**,
   KDE настраивается через `kioslaverc` (`kwriteconfig6/5` + сигнал KIO-перечитывания)
   в сессии ПОЛЬЗОВАТЕЛЯ (root → `runuser -u`), GNOME-подобные — через GSettings; если
   сессии нет или общего механизма нет (i3, sway, контейнер, WSL) программа говорит об
   этом **один раз** и перестаёт повторять каждый цикл (перепроверка раз в 10 минут).
9. **Шумные `WARN` стали `INFO` и говорят словами.** `Кандидатов > 400; взято 400` —
   это нормальный лимит `test.max_candidates`, а `Добавлен резерв по RTT` — нормальный
   добор при строгом отборе; оба больше не выглядят аварией.
10. **Одинаковые строки в журнале больше не повторяются десятками.** Дедупликация в
    `log.py` (окно по умолчанию 60 с, `FOREST_LOG_DEDUPE_S`), к следующей такой же
    строке дописывается счётчик (`… [повторилось 124 раз(а) за 60 с]`) — информация не
    теряется.

### Что проверено в этом выпуске

| Проверка | Результат |
| --- | --- |
| Windows: `ForestProxyUpdater.exe --selftest` (однофайловая сборка) | **238 проверок, PASSED** |
| Windows: сэндбокс-прогон (пустая папка, свой `FOREST_ROOT`, порт 11830, циклы и системный прокси выключены) | помощник **распакован** (`root\forest-ui.exe`, 37 731 130 Б, SHA-256 `7848453b…`, **0,20 с**), хеш **совпал** с встроенным, помощник **запущен** (один процесс), старт **≈3 с** |
| Windows: повторный запуск | копия не перезаписывается (mtime тот же), «копия на месте и совпадает по SHA-256», помощник поднят на **≈4-й секунде** |
| Windows: цель распаковки недоступна (на месте файла — каталог) | **без краха и без окон**: одна строка `WARN`, значок — встроенный, выход **0** |
| Linux, живой XEmbed-стек (Xvfb + stalonetray + xdotool) на собранном `forest-ui` | значок встроен 24×24; **левый** клик → 0 окон меню; **правый** клик → наше окно 380×515 у значка; повторный правый клик окно не дублирует; `Esc` и клик вне окна возвращают `IsUnMapped` |
| Linux, ветка меню Qt с `os.name="posix"` (Qt 6.9) | `QMenu` в трей не передаётся, правый клик → наше меню, левый → ничего, `--menu-popup` без второго значка |
| Плазмоид KDE | `qmllint` по живым модулям Plasma 6 — 0 предупреждений; `kpackagetool6 --type Plasma/Applet --install` принимает пакет (и из репозитория, и после установки) |
| Гейт уведомлений на РЕАЛЬНЫХ файлах конфига | `enabled=false` — ни одной карточки (провал процесса, событие `ok→error`, ошибка службы); `enabled=true` — карточка есть; ручной показ работает и при выключенных |
| Linux: `posix_selftest.py` | 89 проверок, все зелёные |
| Linux: собранный `forest-proxy-updater --selftest` (offscreen) | **328 проверок, 0 провалов** (включая 89 POSIX-тестов) |
| Linux: собранный `forest-ui --selftest` (offscreen) | **158 OK / 0 провалов по существу**: 2 известных артефакта СТЕНДА — ширина меню `352 px` вместо `269 px` (в WSL нет Segoe UI, метрики DejaVu Sans шире; на живом Windows 269×487, 304 проверки зелёные) и проверка «сбой показа меню записан в журнал» читает `main.log` корня `FOREST_ROOT`, тогда как строка пишется в лог каталога состояния (оба файла на месте, маркер в логе есть). В прогоне из исходников (venv, Qt 6.9) второго артефакта нет |
| `packaging/check_notify_gate.py` | 9 проверок, PASSED |
| `packaging/check_linux_tray.py` | 10 проверок, PASSED |
| Остатки состояния стенда | в общем `$HOME` демо-карточка брала «последний известный процесс» из `ui_last_proc.json` и давала 1 этап (`1/100000`); при изолированном `HOME`/`XDG_*` проверка зелёная — это остаток прежних прогонов, а не поведение продукта |

### Матрица: 5 форматов × (установка → служба активна → РЕАЛЬНЫЙ 204 → чистое удаление)

Стенд `linux/tests/wsl-matrix.sh --isolate --port 11808`, **по одному дистрибутиву за
раз**; живой прокси хоста (порт 10808) не задействован. «РЕАЛЬНЫЙ 204» — это ответ,
прошедший **через туннель самого пакета**:
`curl -x http://127.0.0.1:11808 https://www.gstatic.com/generate_204` → `204`.

| Формат | Дистрибутив | Шагов | PASS | 204 получен через | После удаления |
| --- | --- | --- | --- | --- | --- |
| `.deb` | Debian GNU/Linux 13 (trixie) | 13 | **13** | ~252 с | пакет и файлы ушли, юнитов нет, порт `11808` закрыт |
| `.tar.gz` | Ubuntu 26.04 LTS | 13 | **13** | ~252 с | чисто (свой `uninstall.sh`) |
| `.AppImage` | Ubuntu 26.04 LTS (установка от пользователя `tester`) | 14 | **14** | ~249 с | чисто |
| `.rpm` | openSUSE Tumbleweed 20260723 | 13 | **13** | ~267 с | чисто |
| Arch `.pkg.tar.zst` | Arch Linux (rolling) | 13 | **13** | ~258 с | чисто |

Итоги всех пяти прогонов: **66 PASS / 0 FAIL / 0 SKIP**. Проверяемые шаги одинаковы для
всех форматов: окружение; systemd запущен; тип и версия пакета (`1.3.2`); копирование в
дистрибутив; установка штатным менеджером; юнит виден системе; служба **активна**;
**трафик через прокси → 204**; удаление; отсутствие регистрации в базе менеджера;
отсутствие файлов; отсутствие юнитов/автозапуска; порт `11808` закрыт. У AppImage
добавляется шаг установки от пользователя.

Пометка про определение версии: у `.tar.gz` шаг «тип пакета» печатает `tar … 1.3`
(в архиве нет строки версии, которую стенд умеет читать) — это ограничение стенда, а не
пакета; имя самого файла и `install.sh` содержат `1.3.2`.

### Содержимое пакетов совпадает с деревом сборки

| Формат | Чем распаковано | `forest-proxy-updater` | `forest-ui` | Плазмоид |
| --- | --- | --- | --- | --- |
| `.deb` | `dpkg-deb -x` | `5a47d9fa…` ✔ | `6f644111…` ✔ | 3 файла = репозиторию ✔ |
| `.rpm` | `rpm2cpio \| cpio -idm` | `5a47d9fa…` ✔ | `6f644111…` ✔ | 3 файла = репозиторию ✔ |
| Arch `.pkg.tar.zst` | `tar --zstd -xf` | `5a47d9fa…` ✔ | `6f644111…` ✔ | 3 файла = репозиторию ✔ |
| `.tar.gz` | `tar xzf` | `5a47d9fa…` ✔ | помощник к архиву прилагается отдельно | — (портативная сборка, ничего не ставит в `/usr`) |
| `.AppImage` | `--appimage-extract` | `5a47d9fa…` ✔ | помощник к AppImage прилагается отдельно | — (то же) |

Плазмоид во всех трёх системных пакетах лежит ровно в
`/usr/share/plasma/plasmoids/org.forest.proxyupdater/` и побайтово равен репозиторному
(`metadata.json` `8150000e…`, `contents/ui/main.qml` `5e3ffcc9…`,
`contents/code/read-state.sh` `1145afa4…`). В `.deb` есть оба симлинка в `PATH`
(`/usr/bin/forest-proxy-updater` и `/usr/bin/forest-ui` → `/usr/lib/forest-proxy-updater/`),
юниты `forest-proxy-updater.service` / `-refresh.service` / `.timer`, оба `.desktop`,
значок 256×256, документация (`README.md`, `INSTALL.md`, `INSTALL.ru.md`, `TERMS.md`,
`TERMS.ru.md`, `copyright`, `COPYING`).

**Пометки про WSL и непроверенный живой рабочий стол** — внутри всех форматов: `.deb` и
`.rpm` — в описании пакета (`dpkg-deb -I` / `rpm -qip`, блоки «ОГРАНИЧЕНИЯ ПРОВЕРКИ» и
«TESTING SCOPE»), Arch — в `pkgdesc` и комментарием, `.tar.gz` и `.AppImage` — в
`doc/INSTALL*.md` внутри архива.

### Артефакты и контрольные суммы

| Файл | Размер | SHA-256 |
| --- | --- | --- |
| `ForestProxyUpdater.exe` (Windows, один файл) | 68 246 890 Б (65,09 МиБ) | `2d14562e…` |
| `forest-ui.exe` (помощник Windows, отдельный ассет) | 37 731 130 Б (35,98 МиБ) | `7848453b…` |
| `forest-proxy-updater-linux-x86_64` | 34 368 848 Б (32,78 МиБ) | `5a47d9fa…` |
| `forest-ui-linux-x86_64` | 64 432 720 Б (61,45 МиБ) | `6f644111…` |
| `forest-proxy-updater_1.3.2_amd64.deb` | 97 234 188 Б (92,73 МиБ) | `388d7062…` |
| `forest-proxy-updater-1.3.2-1.x86_64.rpm` | 97 372 275 Б (92,86 МиБ) | `cadf2f33…` |
| `forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst` | 98 109 601 Б (93,56 МиБ) | `f29b4cfe…` |
| `forest-proxy-updater-1.3.2-linux-x86_64.tar.gz` | 33 817 248 Б (32,25 МиБ) | `2ec6f9f7…` |
| `ForestProxyUpdater-1.3.2-x86_64.AppImage` | 34 789 880 Б (33,18 МиБ) | `fb1b2255…` |

Полные суммы — в `SHA256SUMS` (901 Б, 9 записей) рядом с этой страницей; каждый артефакт
имеет свой файл `.sha256`. Проверка: `sha256sum -c SHA256SUMS`.

### Установка

```bash
sudo apt install ./forest-proxy-updater_1.3.2_amd64.deb                    # Debian/Ubuntu/Mint
sudo dnf install ./forest-proxy-updater-1.3.2-1.x86_64.rpm                  # Fedora/Rocky/Alma (openSUSE: zypper)
sudo pacman -U forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst              # Arch/Manjaro/EndeavourOS
chmod +x ForestProxyUpdater-1.3.2-x86_64.AppImage && ./ForestProxyUpdater-1.3.2-x86_64.AppImage --install
tar xzf forest-proxy-updater-1.3.2-linux-x86_64.tar.gz && cd forest-proxy-updater-1.3.2-linux-x86_64 && sudo ./install.sh
```

После установки: `curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n'
https://www.gstatic.com/generate_204` → `204` (первые минуты уходят на подбор рабочих
серверов).

### Честно: что НЕ проверено

* Живой рабочий стол **KDE Plasma**: правая кнопка в панели, плазмоид в настоящей теме
  и настоящем `plasmashell` (на стенде Plasma нет). Это то, что должен посмотреть
  пользователь на VM.
* Живой **GNOME / XFCE / MATE / Cinnamon / LXQt** рабочий стол (кроме XEmbed-панели
  `stalonetray` в Xvfb).
* **FUSE-монтирование AppImage** — только распаковка.
* **SmartScreen/антивирус** для 65-МБ однофайловой сборки (бинарник не подписан).
* **Обновление поверх РАБОТАЮЩЕЙ двухфайловой 1.3.1**: замена помощника спроектирована
  как пропускаемая, пока `forest-ui.exe` занят (старая копия продолжает работать, новая
  ложится при следующем запуске) — проверено только имитацией недоступной цели, не живым
  обновлением.
* Шаг «тип пакета» у `.tar.gz` не читает версию из архива (ограничение стенда).

---

## 🇬🇧 English

### What changed

1. **Windows is now ONE file.** The interface helper (`forest-ui.exe`, ~36 MB: tray
   icon, menu, notifications) is no longer a second file next to the program — it
   travels inside `ForestProxyUpdater.exe` (`ui_bundle/forest-ui.exe` +
   `ui_bundle/ui-bundle.json`). Download one file, run it, and the tray icon is there
   from the first run.
2. **The helper unpacks with a SHA-256 check.** On `--run`/`--ensure`/`--install` the
   program compares `<root>\forest-ui.exe` with the hash from the embedded manifest.
   Match — no copy (one log line, ~0.05 s). Missing or mismatched — copy to
   `.new-<pid>`, verify the **copy's** hash, `os.replace`, verify what is on disk now,
   then launch. A locked or unwritable file never becomes an error dialog: one log
   line and the program works with its **built-in tray icon**.
3. **Right-click shows OUR menu again.** Under StatusNotifier the panel owns the right
   click (it asks the application for a `com.canonical.dbusmenu` description and draws
   it itself), so the helper no longer hands a `QMenu` to the tray
   (`QSystemTrayIcon.setContextMenu` is gone). On **XEmbed** panels (XFCE, MATE,
   Cinnamon, LXQt) the right click (`ActivationReason.Context`) opens our animated
   `ui_menu.PopupMenu` at the icon geometry, exactly as on Windows; the left click
   still opens nothing.
4. **KDE Plasma gets a panel widget (plasmoid).** Where the panel keeps the right
   click, our icon and menu come from `plasma/plasmoid/org.forest.proxyupdater`
   (installed to `/usr/share/plasma/plasmoids/`): it draws our icon with the state
   colour from `tray_status.json` and runs the new `forest-ui --menu-popup [x y]` — a
   process with **no second tray icon** that shows OUR menu. If your theme keeps the
   right click for the widget's own menu, `Ctrl+left click` is the documented fallback.
5. **`notify.enabled = false` really silences everything.** The setting was checked in
   one place only, so the failed-restore card, a process card or a command-error card
   still appeared. Every card now passes one gate in `Ui.show_toast()`: with
   notifications off nothing is drawn (events, process cards, service errors), and only
   an explicit user action (`--show`/`--show-all`, "Проверить уведомление",
   "Сведения") still shows a card.
6. **A missing Qt no longer crashes the helper with a traceback.** It now prints
   `forest-ui: Qt недоступен (…) — помощник не запущен` and exits with code 3, which
   is what `src/ui_helper.py` expects (`ui_style` is pure stdlib and is imported
   outside the `if QT_OK:` guard). `libglvnd` stays in `optdepends`, with the
   consequence spelled out.
7. **The journal is readable: UTF-8 instead of `?????`.** `safestdio.say()` encoded
   with `ascii + errors="replace"` (a Windows cp1251-console guard); on Linux journald
   wants UTF-8, so every Russian line became `?????` while the file log stayed correct.
   `say()` now prints UTF-8 on POSIX (and on Windows when the stream is already UTF-8);
   the ASCII fallback remains for the cp1251 build and can be forced with
   `FOREST_STDOUT_ASCII=1`.
8. **KDE is detected as KDE, not as GNOME.** The old "this is GNOME" test was "gsettings
   exists" — it exists on KDE too (glib2), so the system service set the proxy by the
   wrong mechanism every 30–60 s and the warning about it repeated forever (`gsettings
   set` as root without a session bus silently falls back to the `memory` backend).
   The desktop is now detected explicitly, **the write is verified by reading it back**,
   KDE is configured through `kioslaverc` in the *user's* session (root → `runuser -u`),
   GNOME-like desktops through GSettings, and when no mechanism is available the
   program says so **once** (re-checked every 10 minutes).
9. **Noisy `WARN`s became `INFO` and say so in words** (`test.max_candidates` load
   limit, RTT reserve top-up).
10. **Identical journal lines no longer repeat dozens of times** (`log.py`
   de-duplication, default 60 s window, `FOREST_LOG_DEDUPE_S`, suppressed count
   appended to the next identical line).

### Verified in this release

* Windows `ForestProxyUpdater.exe --selftest` (the one-file build): **238 checks,
  PASSED**.
* Windows sandbox run (empty folder, own `FOREST_ROOT`, port 11830, cycles and system
  proxy off): helper **unpacked** (`root\forest-ui.exe`, 37 731 130 B, SHA-256
  `7848453b…`, **0.20 s**), hash **matched** the embedded copy, helper **launched**
  (single process), startup **≈3 s**. Second run: the copy is left untouched (same
  mtime), `SHA-256 matches` in the log, helper up at **~4 s**. With the target
  deliberately unavailable (a directory in place of the file): **no crash, no
  windows**, one `WARN` line, built-in icon, exit **0**.
* Linux live XEmbed stack (Xvfb + stalonetray + xdotool, the built `forest-ui`): icon
  embedded 24×24; **left** click → 0 menu windows; **right** click → our 380×515 window
  at the icon; a second right click does not duplicate it; `Esc` and an outside click
  return it to `IsUnMapped`.
* Qt menu branch with `os.name="posix"` (Qt 6.9): no `QMenu` handed to the tray, right
  click → our menu, left → nothing, `--menu-popup` with no second icon.
* Plasmoid: `qmllint` against the real Plasma 6 modules — 0 warnings;
  `kpackagetool6 --type Plasma/Applet --install` accepts the package (from the
  repository and after installation).
* Notification gate on **real config files**: `enabled=false` suppresses every card
  (process failure, `ok→error` event, service error); `enabled=true` shows the same
  card; the manual "test notification" works with notifications off.
* Built Linux artifacts, offscreen: `forest-proxy-updater --selftest` — **328 checks,
  0 failures** (including the 89 POSIX ones); `forest-ui --selftest` — **158 OK** with
  **two known artifacts of the stand**: the menu width `352 px` instead of the
  mock-up's `269 px` (no Segoe UI in WSL — DejaVu Sans is wider; on a live Windows the
  same menu is 269×487 with 304 checks green) and the "menu failure is written to the
  log" check, which reads `main.log` under `FOREST_ROOT` while the line is written to
  the state directory's log (both files exist, the marker is in the log); the
  source-run (venv, Qt 6.9) shows only the geometry artifact.
  `check_notify_gate.py` — 9 checks PASSED; `check_linux_tray.py` — 10 checks PASSED.
  In a shared `$HOME` the demo card picked the "last known process" from
  `ui_last_proc.json` and produced a single step (`1/100000`); with an isolated
  `HOME`/`XDG_*` that check is green — leftover stand state, not product behaviour.
* 5-format matrix (port 11808, one distro at a time): **66 PASS / 0 FAIL / 0 SKIP**
  on the final artifacts, a real `204` through every package — `.deb` (Debian 13)
  ~252 s, `.tar.gz` (Ubuntu 26.04) ~252 s, `.AppImage` (Ubuntu 26.04, user install)
  ~249 s, `.rpm` (openSUSE Tumbleweed) ~267 s, Arch `.pkg.tar.zst` (Arch rolling)
  ~258 s. After each removal: no package, no files, no units, port closed.

### Install

```bash
sudo apt install ./forest-proxy-updater_1.3.2_amd64.deb                    # Debian/Ubuntu/Mint
sudo dnf install ./forest-proxy-updater-1.3.2-1.x86_64.rpm                  # Fedora/Rocky/Alma (openSUSE: zypper)
sudo pacman -U forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst              # Arch/Manjaro/EndeavourOS
chmod +x ForestProxyUpdater-1.3.2-x86_64.AppImage && ./ForestProxyUpdater-1.3.2-x86_64.AppImage --install
tar xzf forest-proxy-updater-1.3.2-linux-x86_64.tar.gz && cd forest-proxy-updater-1.3.2-linux-x86_64 && sudo ./install.sh
```

Then: `curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n'
https://www.gstatic.com/generate_204` → `204` (the first minutes go to finding working
servers).

### Honestly NOT verified

* A live **KDE Plasma** desktop: the right click inside a real panel, the plasmoid in a
  real theme and a real `plasmashell` (no Plasma on the stand) — this is what the user
  should check on the VM.
* A live **GNOME / XFCE / MATE / Cinnamon / LXQt** desktop (other than the XEmbed
  `stalonetray` panel inside Xvfb).
* **FUSE mounting** of the AppImage (extraction only).
* **SmartScreen/antivirus** behaviour of the 65 MB one-file build (unsigned binary).
* **Upgrading over a RUNNING two-file 1.3.1 install**: the helper replacement is
  designed to be skipped while `forest-ui.exe` is locked (the old copy keeps running,
  the new one lands on the next start) — checked by simulating an unavailable target,
  not by upgrading a live installation.
* The `.tar.gz` "package type" step cannot read the version out of the archive (a
  limitation of the stand).

---

## Артефакты и контрольные суммы / Artifacts and checksums

Полный список — в `SHA256SUMS` рядом с этой страницей; каждый артефакт имеет свой
`.sha256`. Проверка: `sha256sum -c SHA256SUMS`.

The full list is in `SHA256SUMS` next to this page; every artifact also has its own
`.sha256`. Verify with `sha256sum -c SHA256SUMS`.

Ассеты с пометкой `-linux-x86_64` — это Linux-релизы без пакета (сам ELF и помощник
интерфейса), они не требуют установки через менеджер пакетов. На Windows помощник
`forest-ui.exe` приложен отдельным ассетом только для ручных установок — обычному
пользователю он не нужен: он уже внутри `ForestProxyUpdater.exe`.
