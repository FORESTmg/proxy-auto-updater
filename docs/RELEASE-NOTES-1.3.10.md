# v1.3.10 — установка одной строкой снова работает, значок/меню/карточка на живых панелях, Windows-сборка / one-line install works again, tray/menu/card on real panels, Windows build

**Дата / Date:** 2026-10-23 · **Тег / Tag:** `v1.3.10`
**Предыдущий опубликованный выпуск / Previous published release:** `v1.3.1`
(всё, что закрывали 1.3.2–1.3.9, выходит здесь — это первый тег после 1.3.1).

**Артефакты / Artifacts:** `ForestProxyUpdater.exe` (Windows, **один** файл —
помощник интерфейса внутри), `forest-ui.exe` (помощник Windows отдельным файлом),
`forest-proxy-updater-linux-x86_64` и `forest-ui-linux-x86_64` (Linux ELF),
`.deb` / `.rpm` / Arch `.pkg.tar.zst` / `.tar.gz` / AppImage, сводная `SHA256SUMS`.

Версия сведена к одной — **1.3.10**: `packaging-linux/lib/common.sh`
(`VERSION="1.3.10"`) и `packaging/version_info.txt`
(`filevers`/`prodvers` = `1, 3, 10, 0`, `FileVersion` = `1.3.10.0`,
`ProductVersion` = `1.3.10`).

---

## Главное / Highlights

1. **Установка одной строкой работает.** Все скрипты внутри пакетов лежали с
   виндовыми переводами строк, поэтому `curl … | bash` падал на
   `bash: line 30: $'\r': command not found`, а `postinst` у `.deb` упал бы так
   же. Дерево переведено в LF, это закреплено `.gitattributes`, а сборка теперь
   останавливается, если в пакет пытается попасть CR.
   *(Every script shipped inside a package was committed with CRLF, so the
   documented `curl … | bash` path died on `$'\r'`. The tree is LF, pinned by
   `.gitattributes`, and the build refuses CR sources.)*
2. **Значок, меню и карточка проверены на живых панелях XFCE и MATE**, а не
   только в offscreen-режиме: тёмная тема, масштаб 150 %, меню встаёт у значка.
   *(Tray, menu and notification card verified on live XFCE and MATE panels —
   dark theme, 150 % scaling, menu next to the icon.)*
3. **Windows-артефакты собраны из того же дерева**, что и Linux-пакеты, и несут
   версию 1.3.10 в свойствах файла.
   *(The Windows artifacts are built from the same tree as the Linux packages
   and carry 1.3.10 in their file properties.)*

---

## Что исправлено / Fixed

### 1.3.10 — упаковка / packaging

- **CRLF в установочных скриптах**: `linux/install.sh`,
  `packaging-linux/tar/install.sh`, `uninstall.sh`, все `build-*.sh`,
  `lib/common.sh`, `deb/postinst|prerm|postrm`, `arch/*.install`, все
  `.desktop`. Из-за них не работал ни `curl … | bash`, ни `postinst`.
- **`.gitattributes`**: `* text=auto eol=lf` плюс явные правила `eol=lf` для
  `*.sh`, `*.install`, `*.desktop`, `PKGBUILD`, `*.spec(.in)`, `*.service`,
  `*.timer`, `*.py`, `*.qml`, `*.md`, `*.json`.
- **`packaging-linux/check-eol.sh`** — проверяет каждый файл, который попадает в
  пакет; `check_inputs()` в `lib/common.sh` вызывает ту же проверку, поэтому
  сборка с CR-исходниками останавливается, а не отдаёт сломанный установщик.

### 1.3.9 — дефекты D1–D6 (матрица DE)

- **D1 (P0)** запасной значок исчезал через 2 секунды: `refresh()` перечитывал
  `ui.tray_mode`, получал `sni` и снимал значок. Теперь флаг `ui._tray_fallback`
  удерживает режим, а явный выбор в меню его сбрасывает.
- **D2 (P0)** бандл не видел тёмную тему: PyInstaller экспортирует
  `LD_LIBRARY_PATH=/tmp/_MEI…`, системный `gsettings` подхватывал оттуда glib и
  отвечал `'default'`, что старый код считал за `light` → белая карточка на
  тёмном столе. Дочерние процессы получают окружение без
  `LD_LIBRARY_PATH`/`PYTHONHOME`/`PYTHONPATH`, `'default'` означает «неизвестно»,
  а `~/.config/gtk-3.0/settings.ini` читается как последний источник.
- **D3 (P1)** XFCE и LXQt не пишут GSettings: тема читается из `xfconf`
  (`xsettings /Net/ThemeName`) и `~/.config/lxqt/lxqt.conf` до GSettings.
- **D4 (P2)** самотест противоречил коду SNI (требовал XEmbed-признаки); теперь
  проверки ветвятся по `ui.icon_mode`.
- **D5 (P3)** поля карточки на 150 %: смешивались логические и устройственные
  пиксели в strut панели.
- **D6 (P3)** жёсткий `PANEL_PX = 46` заменён измерением панели.

### 1.3.5–1.3.8 — живой KDE/Wayland

- Карточка крутилась по кругу, вставала в центре экрана и уезжала по полям.
- После обновления жили **три** копии помощника и три значка — введён
  единственный экземпляр на пользователя.

### 1.3.2 — меню и один файл Windows

- Правый клик по значку на XEmbed-панелях открывает **наше** меню (раньше Qt
  показывал свой `QMenu`, потому что меню было отдано трею).
- `ForestProxyUpdater.exe` — **один** файл: помощник распаковывается в
  `%LOCALAPPDATA%\ForestProxyUpdater\forest-ui.exe` и сверяется по SHA-256;
  если распаковать нельзя, работает встроенный упрощённый значок.
- `notify.enabled = false` действительно глушит все карточки.

---

## Пакеты / Packages

| Формат / Format | Файл / File | Размер / Size | SHA-256 |
|---|---|---|---|
| `.deb` | `forest-proxy-updater_1.3.10_amd64.deb` | 89 596 932 B (85.4 MiB) | `ba0212c66df9261da4d074f80cdbac6e6fb8d6d67ce6e0ca8782850d7856f3a4` |
| `.rpm` | `forest-proxy-updater-1.3.10-1.x86_64.rpm` | 89 732 963 B (85.6 MiB) | `c8fcb369471a5072f7e05108f292e82623e8b63db9e7fb119423775b76c5f66d` |
| Arch | `forest-proxy-updater-1.3.10-1-x86_64.pkg.tar.zst` | 90 467 552 B (86.3 MiB) | `bba75d2e44611642593ea71b1b9eb35241f417ec3d806cc8d7510322a4f6a96b` |
| `.tar.gz` | `forest-proxy-updater-1.3.10-linux-x86_64.tar.gz` | 33 840 929 B (32.3 MiB) | `7af70654787915a11efec389334024b17c2c9282d46e1fed0897c51130503a59` |
| AppImage | `ForestProxyUpdater-1.3.10-x86_64.AppImage` | 34 810 360 B (33.2 MiB) | `54d12828adee819a23d430dddcce03ae1b7b292485f2ae7d45a607d6dbe7a204` |
| Windows | `ForestProxyUpdater.exe` | 68 395 405 B (65.2 MiB) | `4bbd62e158d3f1dfcd96c9672d9edf92d9e79695b5ac7dadbaa3bec50e752dec` |
| Windows helper | `forest-ui.exe` | 37 861 794 B (36.1 MiB) | `ae25380a60a1c4c35802645ae12d6a6693fdf4d007cb69b315f62224a18950d0` |
| Linux ELF | `forest-proxy-updater-linux-x86_64` | 34 387 232 B (32.8 MiB) | `268516647edb05cda5a14d0427164d58fe2a5a3898b59e1f63b0c35e55047cc3` |
| Linux helper | `forest-ui-linux-x86_64` | 56 708 344 B (54.1 MiB) | `754888de2ec02195975c2ce386e20b2e32e79ad9994a33e745595c8cc22eb05b` |

Рядом с каждым файлом лежит `<файл>.sha256`, общая сводка — `SHA256SUMS`
(формат `sha256sum -c`). Пакеты собраны из ELF с хешами
`268516647edb…` (служба) и `754888de2e…` (помощник) — теми же, что выложены
отдельными файлами.

---

## Установка / Install

`<TAG>` в ссылках ниже — `v1.3.10`, `<FILE>` — имя файла из таблицы.

**Debian / Ubuntu / Mint**

```sh
curl -LO https://github.com/FORESTmg/proxy-auto-updater/releases/download/v1.3.10/forest-proxy-updater_1.3.10_amd64.deb
sudo apt install ./forest-proxy-updater_1.3.10_amd64.deb
```

**Fedora / RHEL / Rocky / Alma / openSUSE**

```sh
curl -LO https://github.com/FORESTmg/proxy-auto-updater/releases/download/v1.3.10/forest-proxy-updater-1.3.10-1.x86_64.rpm
sudo dnf install ./forest-proxy-updater-1.3.10-1.x86_64.rpm      # openSUSE: sudo zypper install ./...
```

**Arch / Manjaro / EndeavourOS**

```sh
curl -LO https://github.com/FORESTmg/proxy-auto-updater/releases/download/v1.3.10/forest-proxy-updater-1.3.10-1-x86_64.pkg.tar.zst
sudo pacman -U forest-proxy-updater-1.3.10-1-x86_64.pkg.tar.zst
```

**AppImage — без root**

```sh
curl -LO https://github.com/FORESTmg/proxy-auto-updater/releases/download/v1.3.10/ForestProxyUpdater-1.3.10-x86_64.AppImage
chmod +x ForestProxyUpdater-1.3.10-x86_64.AppImage
./ForestProxyUpdater-1.3.10-x86_64.AppImage --install
```

**Портативный `.tar.gz`**

```sh
curl -LO https://github.com/FORESTmg/proxy-auto-updater/releases/download/v1.3.10/forest-proxy-updater-1.3.10-linux-x86_64.tar.gz
tar xzf forest-proxy-updater-1.3.10-linux-x86_64.tar.gz
cd forest-proxy-updater-1.3.10-linux-x86_64
sudo ./install.sh          # без root: ./install.sh --user
```

**Windows** — скачать `ForestProxyUpdater.exe` и запустить; больше ничего не нужно.

**Проверка целостности / Verify**

```sh
curl -LO https://github.com/FORESTmg/proxy-auto-updater/releases/download/v1.3.10/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

---

## Обновление / Upgrade

Конфиг (`/etc/forest-proxy-updater/config.json` или
`~/.config/forest-proxy-updater/config.json`) и состояние не переписываются.
Старые версии ставили по два значка — после обновления значок **один**; лишний
ярлык/виджет, оставшийся от прежних выпусков, убирается без root командой
`forest-clean-desktop`. Плазмоид KDE из ранних 1.3.2 больше не нужен: в упаковке
его нет.

---

## Что проверено / Verified

- **Пакеты:** 64 из 64 проверок на 4 дистрибутивах (Debian/Ubuntu, Fedora,
  Arch, openSUSE) — установка, файлы, юниты systemd, запуск службы, удаление.
- **Пять форматов** собраны из одних и тех же ELF; хеши ELF в `.deb`, `.rpm`,
  Arch-пакете, `.tar.gz` и AppImage совпадают с выложенными отдельно.
- **Графика:** живая панель XFCE и MATE (значок, меню по правому клику,
  подсказка, карточка), тёмная и светлая тема, масштаб 150 %.
- **Живой CachyOS (KDE Plasma, Wayland):** один значок, карточка 624 × 525,
  фон карточки в тёмной теме 37/255, фокус окна не перехватывается. Прогон
  сделан на установленной `1.3.9-1`, у которой ELF **побайтово те же**, что в
  1.3.10 (`268516647edb…` служба, `754888de2e…` помощник), — см. пункт 3 ниже.

- **`ForestProxyUpdater.exe --selftest`**: `SELFTEST: PASSED`.
- **Обратное чтение:** каждый файл из этого релиза скачан с GitHub, SHA-256
  сверен с локальным и с `SHA256SUMS`; выложенные ELF и `.exe` прогнаны
  самотестом уже после скачивания.

## Что НЕ проверено / Not verified

Честно, чтобы не обещать лишнего:

1. **Вид меню попапом** на живой панели глазами: проверено, что окно меню
   создаётся, встаёт у значка с правильной геометрией и что клики приходят в
   него; «красиво/некрасиво» на каждом DE — нет.
2. **Cinnamon и GNOME вживую не запускались.** На GNOME без расширения
   AppIndicator значка не будет (в сеансе нет SNI-хоста); путь «своё окно-значок»
   включается вручную (`ui.tray_mode = "own"`).
3. **Установка без root** (AppImage `--install`, `install.sh --user`) проверена
   логически и на стенде, но не на «чистой» машине без прав администратора.
   Пакет 1.3.10 в **живом** госте CachyOS поверх старой версии не ставился:
   `install.sh` ставит только через `sudo -n`, а в неинтерактивном канале стенда
   sudo просит пароль (`sudo: требуется указать пароль`) — пароль человека не
   вводился. Живой DE-прогон сделан на `1.3.9-1` с теми же ELF; SHA пакета
   1.3.10, скачанного в госте, совпал с ожидаемым.
4. **`forest-ui.exe --selftest` на Windows** сообщает 2 невыполненные проверки,
   обе — расхождение теста с текущим оформлением, а не сбой программы:
   * «геометрия меню» ждёт ширину панели 269 px, а на этом экране (масштаб
     150 %, другие метрики шрифта) получается 309 px; **опубликованный 1.3.1
     падает на той же проверке с тем же числом** — проверка привязана к
     100 %-ному экрану разработчика;
   * проверка «правый клик открывает наше окно меню» написана под XEmbed, а
     режим по умолчанию — область уведомлений, где меню рисует система из
     нашего `QMenu` (тот же состав, наш стиль); на Linux эта же ветка уже
     переписана (D4), на Windows — ещё нет.
5. **AppImage** проверен распаковкой (`--appimage-extract-and-run`): штатное
   монтирование через FUSE на стенде недоступно.

---

## English

**v1.3.10 — the documented one-line install works again; tray, menu and card
verified on live XFCE/MATE panels and on a live CachyOS KDE session; Windows
artifacts rebuilt from the same tree.**

This is the first published tag after `v1.3.1`, so it also carries everything
`1.3.2`–`1.3.9` closed.

**Fixed**

- Every installer script inside the packages (`postinst`/`prerm`/`postrm`,
  `install.sh`, `build-*.sh`, `.desktop`, `PKGBUILD`, specs) had been stored
  with CRLF, so `curl … | bash` died with `$'\r': command not found` and
  `postinst` would have failed the same way. The tree is LF now, `.gitattributes`
  pins it, and `packaging-linux/check-eol.sh` (called from `check_inputs()`)
  stops a build that would ship CR.
- D1 (P0): the fallback tray icon was torn down two seconds after it appeared in
  sessions without a notification area.
- D2 (P0): the bundle could not see the dark theme — PyInstaller's
  `LD_LIBRARY_PATH` leaked into `gsettings`, which answered `'default'` and was
  read as "light" (white card on a dark desktop).
- D3 (P1): XFCE/LXQt theme is read from `xfconf` / `lxqt.conf` before GSettings.
- D4 (P2): the self-test contradicted the SNI code path; it now branches on
  `ui.icon_mode`.
- D5 (P3): card margins at 150 % (panel strut mixed logical and device pixels).
- D6 (P3): hard-coded `PANEL_PX = 46` replaced by measuring the panel.
- 1.3.5–1.3.7 live KWin: the card circled, sat centred, drifted at the edges; an
  upgrade left three helper copies and three icons alive — one instance per user
  now.
- 1.3.2: right-click opens OUR menu on XEmbed panels; `ForestProxyUpdater.exe`
  is a single file with the helper inside (SHA-256 verified on unpack, built-in
  simplified icon if unpacking is impossible); `notify.enabled = false` really
  silences every card.

**Verified**: 64/64 package checks on 4 distributions, five formats built from
the same ELFs (hashes match the standalone files), live XFCE and MATE panels
(light/dark, 150 %), live CachyOS KDE on Wayland (one icon, card 624 × 525, dark
background 37/255, focus not stolen — run on the installed `1.3.9-1`, whose ELFs
are byte-identical to 1.3.10), `ForestProxyUpdater.exe --selftest` →
`SELFTEST: PASSED`, and every asset downloaded back from the release and checked
against `SHA256SUMS`.

**Not verified**: the look of the pop-up menu on each desktop (its geometry,
creation and click routing are verified, the aesthetics are not), Cinnamon and
GNOME live, a genuinely root-less install path, installing the 1.3.10 package
over an existing one inside the live CachyOS guest (`install.sh` uses `sudo -n`
and sudo asked for a password in the non-interactive harness, so no password was
typed; the package digest downloaded in the guest did match), and
`forest-ui.exe --selftest` on Windows (two stale assertions, see item 4 in the
Russian section — the published 1.3.1 fails the same geometry assertion on this
display), and AppImage FUSE mounting (verified via
`--appimage-extract-and-run`).

