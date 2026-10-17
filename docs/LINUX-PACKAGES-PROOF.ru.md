# Linux: пакеты 1.3.0 — что собрано и что проверено

Отчёт о фактически выполненной работе. Каждый хеш, размер и вывод ниже получены запуском на этой
машине; ничего не додумано. Живой Windows-прокси пользователя (порты 10808/10820, реестр, системный
прокси, каталог `%LOCALAPPDATA%\ForestProxyUpdater`) не затрагивался: тесты идут на своём порту
11808 и в своих каталогах внутри WSL.

Дата сборки и проверки: 2026-10-18 (финальная сборка **v1.3.0**).

> **Ограничения проверки.** Всё проверено **в WSL**, а не на установленной системе на реальном
> железе: systemd в WSL работает, ядро — WSL2. Меню и уведомления графического интерфейса проверены
> логически и самотестами, но **не** на живом рабочем столе. AppImage проверен только распаковкой
> (`--appimage-extract-and-run`): в WSL нет FUSE, штатное монтирование не проверялось. Подпись
> пакетов (gpg) и линтеры `lintian`/`rpmlint` не запускались. Та же оговорка лежит **внутри всех
> пяти пакетов** — см. §6.

## 1. Сборка приложения (вход для всех пакетов)

Обе программы собраны PyInstaller 6.22.3 **интерпретатором 3.12.15**
(`packaging/.build-venv-linux`, создан через `uv venv --python 3.12`). Это существенно: сборка
системным `python3` (на Ubuntu это 3.14) давала рабочий на вид артефакт, у которого краснели проверки.

```sh
PYI_PYTHON=<repo>/packaging/.build-venv-linux/bin/python \
    bash packaging/build-linux.sh --skip-core      # -> linux/dist/forest-proxy-updater
PYI_PYTHON=<repo>/packaging/.build-venv-linux/bin/python \
    bash packaging/build-ui.sh --skip-venv         # -> linux/dist/forest-ui  (Qt)
```

Голосом сборщика (это и есть доказательство, каким интерпретатором собрано):

```
PyInstaller 6.22.3 | python 3.12.15
```

| Артефакт | Размер, Б | sha256 |
| --- | --- | --- |
| `linux/dist/forest-proxy-updater` | 34 296 040 | `0a2ca380903388875b7e6b9b1575b4f230188067af7ab7c158d5280082b4285c` |
| `linux/dist/forest-ui` | 64 361 712 | `28d4108641231368b0dd65aed4bd21ee075cf572b3600f6c2f8085e80a4df786` |

Сборка велась с проверкой «исходники не менялись во время сборки»: хеш всех входных модулей снимается
до и после обоих прогонов, и сборка принимается только при совпадении.

```
pre  = 3e82693f681e71d9aab914566c3c71d50660e3a163c15f929f51df313bfe4dc2
post = 3e82693f681e71d9aab914566c3c71d50660e3a163c15f929f51df313bfe4dc2
STABLE: входы сборки не изменились за время сборки
```

Хеши исходников этого самого состояния:

```
098b61f407fe1322ca17cfd23f5aa68c37491afb234950df1575f066b024c500  src/forest_ui.py
600d86725aa4adca7afcd54aeb15c58162df4c1ec3d198561d955b91c6a8f5e6  src/ui_menu.py
7d80d255986865ce6e745237185379b708124205cd87f99187cad08a9bd85549  src/ui_style.py
a8c1344b5a45008b9974fec4d682b6aa97d8140ab46415cfb676c25f2f9ddf89  src/ui_helper.py
d8b6c4a2831a556842e7cb889936f7b376c1c6e16f88a22b1443365cf5e8321f  src/selftest.py
95531f129a942510800f1a2f87a1ae17189e65070838407b8f2370ec51aadf66  src/watch.py
d182ea9e3076d6b56df56edb62c87fbcf75bc8517482f7896c85803fe5a0acca  src/status_info.py
ca012c6bb71b89c6bd1ace72c179184d8660852fc9bcb5163193257956ee9be5  src/notify.py
```

Манифест всех 65 входных файлов сборки (sha256 каждого) снят после сборки;
агрегат манифеста — `be3a749848ab4192445e7e4ed917dc0ef25f604a0802bc63ef041229537411aa`.

### 1.1 Фикс живости помощника — внутри обоих бинарников (побайтово)

Номер версии ни о чём не говорит: пакет, собранный до последней правки, уносит старый код молча.
Поэтому фикс ищется **в полезной нагрузке**: архив PyInstaller распаковывается прямо из выложенного
ELF (не из дерева сборки), из него достаётся модуль `ui_helper`, и в байтах кода ищутся строки самого
фикса.

```
=== main ELF: linux/dist/forest-proxy-updater ===
PyInstaller 6.22.3
modules in pyz: 201 | ui_helper included: True
ui_helper code blob bytes: 24892
MARKER b'WaitForSingleObject' -> 2
MARKER b'_is_our_helper' -> 1
MARKER b'_pid_image' -> 1
MARKER b'\xd0\x9e\xd0\xb4\xd0\xbd\xd0\xbe\xd0\xb3\xd0\xbe \xd0\xbf\xd1\x80\xd0\xb8\xd0\xb7\xd0\xbd\xd0\xb0\xd0\xba\xd0\xb0' -> 1      # «Одного признака»
MARKER b'\xd0\xb8\xd1\x81\xd0\xba\xd0\xbb\xd1\x8e\xd1\x87\xd0\xb8\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c\xd0\xbd\xd0\xbe\xd1\x81\xd1\x82\xd1\x8c \xd0\xb4\xd0\xb5\xd1\x80\xd0\xb6\xd0\xb8\xd1\x82' -> 1   # «исключительность держит»

=== helper: linux/dist/forest-ui ===
PyInstaller 6.22.3
modules in pyz: 200 | ui_helper included: True
ui_helper code blob bytes: 24892
MARKER b'WaitForSingleObject' -> 2
MARKER b'_is_our_helper' -> 1
MARKER b'_pid_image' -> 1
MARKER b'\xd0\x9e\xd0\xb4\xd0\xbd\xd0\xbe\xd0\xb3\xd0\xbe \xd0\xbf\xd1\x80\xd0\xb8\xd0\xb7\xd0\xbd\xd0\xb0\xd0\xba\xd0\xb0' -> 1
MARKER b'\xd0\xb8\xd1\x81\xd0\xba\xd0\xbb\xd1\x8e\xd1\x87\xd0\xb8\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c\xd0\xbd\xd0\xbe\xd1\x81\xd1\x82\xd1\x8c \xd0\xb4\xd0\xb5\xd1\x80\xd0\xb6\xd0\xb8\xd1\x82' -> 1
```

То есть и супервизор, и помощник несут `_pid_alive` на `WaitForSingleObject`, `_pid_image` и
`_is_our_helper` — тот самый фикс, без которого пульс с мёртвым pid считался работающим помощником и
значка в трее не появлялось.

## 2. Пакеты

Все форматы, кроме портативных, собираются из **одной раскладки** — `stage_payload` в
`packaging-linux/lib/common.sh`. Поэтому содержимое `.deb`, `.rpm` и Arch-пакета не может разъехаться:
в каждом лежат оба ELF (программа и помощник интерфейса), юниты systemd, `.desktop`-файлы и значок.

| Файл | Размер, Б | sha256 |
| --- | --- | --- |
| `forest-proxy-updater_1.3.0_amd64.deb` | 97 084 272 | `21a5a9b7e00e5e3b8337b3caf1c076101a9f734c0e3df4bb85c3abbd0fd44a47` |
| `forest-proxy-updater-1.3.0-1.x86_64.rpm` | 97 225 482 | `bc403664b8efeafd0324c72c782a3389ca14c405651db64ef0ecbe8792a6b6f4` |
| `forest-proxy-updater-1.3.0-1-x86_64.pkg.tar.zst` | 97 985 131 | `98ba3e89ab0056ea0d51581474e5333aa793359858336222f9582a5f0bb8bb23` |
| `forest-proxy-updater-1.3.0-linux-x86_64.tar.gz` | 33 744 942 | `3830c1ed867f3ef84acae4a9b9a826d4a8489209c045eee403f320433a1f5789` |
| `ForestProxyUpdater-1.3.0-x86_64.AppImage` | 34 716 152 | `d279626804a93d50607ca833e8fe66840475294fbdfba657b4d43e6fe010b273` |

Рядом с каждым файлом — `.sha256`. Артефакты в git не добавляются (`packaging-linux/out/` в
`.gitignore`), поэтому «устареть в репозитории» они не могут — но и выкладывать надо ровно те файлы,
чей хеш выше, либо пересобрать и **заново прогнать матрицу**: раскладка от версии к версии одна, а
содержимое ELF — нет.

Раскладка внутри `.deb`/`.rpm`/Arch (проверено распаковкой `.deb`):

```
/usr/lib/forest-proxy-updater/forest-proxy-updater   самодостаточный ELF (Python, ядро Xray, гео-базы)
/usr/lib/forest-proxy-updater/forest-ui              помощник интерфейса (Qt), программа ищет его рядом с собой
/usr/bin/forest-proxy-updater, /usr/bin/forest-ui     симлинки в PATH
/usr/lib/systemd/system/forest-proxy-updater*.{service,timer}
/etc/forest-proxy-updater/config.json
/usr/share/applications/forest-proxy-updater{,-ui}.desktop
/usr/share/icons/hicolor/256x256/apps/forest-proxy-updater.png
/usr/share/doc/forest-proxy-updater/*, /usr/share/licenses/forest-proxy-updater/COPYING
```

`.tar.gz` и `AppImage` — облегчённые портативные сборки: в них только сама программа (33–34 МБ против
93–94 МБ у пакетов), помощник интерфейса к ним прилагается отдельным файлом.

### 2.1 Сборка Arch-пакета: почему он не собирался и что сделано

`makepkg` отказывался собирать пакет, пока не удовлетворены объявленные в `PKGBUILD` зависимости (они
нужны **пользователю** — без них `QT_QPA_PLATFORM=xcb forest-ui` не стартует). Прежний лог:

```
  -> xcb-util-image
  -> xcb-util-keysyms
  -> xcb-util-renderutil
  -> libxcb
==> ERROR: Could not resolve all dependencies.
```

Выбран первый из двух путей: зависимости **установлены в контейнере**, а объявленные в `PKGBUILD`
`depends` остались как есть (флаг `--nodeps` не использовался — иначе пакет собрался бы, ничего не
доказав):

```sh
pacman -Sy --noconfirm
pacman -S --noconfirm --needed fakeroot base-devel curl \
    libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image \
    xcb-util-keysyms xcb-util-renderutil libxcb
useradd -m -s /bin/bash builder          # makepkg от root запрещён
FOREST_PKG_VERSION=1.3.0 FOREST_MAKEPKG_USER=builder bash packaging-linux/build-arch.sh
```

После этого пакет собрался штатно (`.pkg.tar.zst`, 94 МБ). Те же зависимости потом понадобились
матрице: `pacman -U` не подтягивает зависимости из сети, в отличие от `apt-get install` и
`zypper install`.

### 2.2 Внутри пакетов — ИМЕННО эти сборки (сверка побайтово)

Номер версии ничего не доказывает: пакет, собранный до последней правки, уносит старый код молча.
Поэтому каждый формат распакован и хеши файлов внутри сверены с деревом сборки:

| Формат | Чем распаковано | `forest-proxy-updater` | `forest-ui` |
| --- | --- | --- | --- |
| `.deb` | `dpkg-deb -x` | `0a2ca380…` = §1 | `28d41086…` = §1 |
| `.rpm` | `rpm2cpio \| cpio -idm` | `0a2ca380…` = §1 | `28d41086…` = §1 |
| Arch | `bsdtar -xf` | `0a2ca380…` = §1 | `28d41086…` = §1 |
| `.tar.gz` | `tar -xzf` | `0a2ca380…` = §1 | помощника внутри нет — прилагается отдельным файлом |
| AppImage | `--appimage-extract` | `0a2ca380…` = §1 | помощника внутри нет — прилагается отдельным файлом |

Полные хеши — в §1: `0a2ca380903388875b7e6b9b1575b4f230188067af7ab7c158d5280082b4285c`
(программа) и `28d4108641231368b0dd65aed4bd21ee075cf572b3600f6c2f8085e80a4df786` (помощник).
Распаковка шла в работе (`packaging-linux/work/verify*`, в git не попадает); сценарии сверки —
`state/wsl-logs/verify-payload-*.sh`.

Оговорка про WSL из §6 лежит **внутри** каждого формата: `dpkg-deb -I` (строки 46 и 52),
`rpm -qi` (строки 34 и 36), `pacman -Qip` (в `Description`), `doc/*.md` в `.tar.gz` и
`/usr/share/doc/forest-proxy-updater/INSTALL*.md` в AppImage — проверено грепом по распакованному
содержимому, а не по репозиторию.

## 3. Самопроверки на Linux

Обе программы прогнаны из `/tmp`, то есть **вне дерева сборки и вне venv** — это проверяет
автономность артефакта, а не наличие окружения рядом. Хеши запускавшихся файлов совпадают с §1.

| Прогон | OK | FAIL | Код возврата |
| --- | --- | --- | --- |
| `forest-proxy-updater --selftest` (основная программа) | 285 | 0 | 0 (`POSIX-тесты пройдены`) |
| `forest-ui --selftest` (помощник интерфейса, `QT_QPA_PLATFORM=offscreen`) | 160 | **1** | 1 (см. §3.3 — расхождение геометрии меню из-за шрифта) |
| `forest-ui` без графической сессии | — | — | 0 (тихий выход с записью в журнал) |

Основная программа — общий набор плюс POSIX-часть (`linux/posix_selftest.py`): подмена примитивов,
XDG-пути, процессы через `/proc`, рендер юнитов systemd, мягкая деградация уведомлений.

### 3.1 Что пришлось починить в самотесте, чтобы Linux был зелёным

Тесты живости помощника (раздел 14.5a) были написаны под Windows и на Linux мерили **не то, что в
продукте**. Правилось измерение, а не продукт; обе правки платформенно-раздельные и на Windows не
меняют поведения:

- **Поддельный помощник.** На Windows тест копировал `ping.exe` в `forest-ui.exe`. На Linux имя
  образа процесса берётся из `/proc/<pid>/exe`, а у shell-скрипта там **интерпретатор**, а не скрипт —
  проверка «наш ли это файл» на таком «помощнике» не могла сработать в принципе. Теперь тест копирует
  настоящий ELF под именем `forest-ui` и запускает его с файлом-скриптом. Отдельно исключены
  симлинки: в этом Ubuntu `/usr/bin/sleep` — симлинк на мультиколл-бинарник uutils coreutils, который
  выбирает апплет по `argv[0]` и с чужим именем отказывается работать
  (`coreutils: unknown program 'forest-ui'`, код 1). Кандидат проверяется пробным запуском.
- **Признак `FOREST_UI` в сборке супервизора.** Тест выставлял `FOREST_UI=1` и требовал, чтобы
  `ensure()` не поднимал помощник. В **собранном** супервизоре это как раз неверно: признак приходит
  по наследству вместе с окружением, и фикс намеренно не даёт супервизору считать себя помощником —
  иначе он никогда не поднял бы настоящий помощник и значка в трее не было бы. Теперь тест разводит
  два случая: из исходников — «вторую копию не поднимаем», в сборке — «унаследованный признак не
  глушит запуск».

Правки лежат в `src/selftest.py` (тот же файл используется и Windows-прогоном).

### 3.2 Помощник интерфейса: чем Linux отличается

Своего анимированного окна на Linux **нет** — уведомления отдаёт системная служба
`org.freedesktop.Notifications` (D-Bus). Поэтому самотест на Linux проверяет не кадры/FPS, а: выбран
ли путь через службу, определяется ли её доступность, не роняет ли помощник её отсутствие, определена
ли тема. Хвост отчёта, который помощник пишет сам (файл UTF-8):

```
  OK   карточка: в текстах НЕТ «0.00 с» — 'обрыв обнаружен · пульс порта' / 'наблюдение включено'
  OK   подсказка: четыре поля в порядке эталона (имя, состояние, серверов, маршрут, watch OK) — ['FOREST связь OK', '10 серверов', 'РФ через прокси', 'watch OK']
  OK   тексты окна совпадают с эталоном дословно — Восстановление связи / Связь восстановлена
  OK   время восстановления связи есть (в подзаголовке и футере) — связь подтверждена · 1.85 с | восстановлено за 1.85 с · окно закроется через 4 с
  OK   этапы идут с временами, проценты есть — ['v Обрыв обнаружен (пульс порта)   0.05 с', '· Трафик подтверждён (204 + TLS)   0.95 с']
  OK   шесть состояний эталона дают свои цвета — [(57, 194, 109), (229, 72, 77), (79, 140, 255), (240, 180, 41)]
  OK   в покое процессор не расходуется (< 60 мс за 3 с) — 9.8 мс
  OK   печать символа вне cp1251 не бросает исключения (нет модального окна) — None
  OK   служебная печать идёт через безопасный слой
  OK   guarded(): исключение внутри main -> ненулевой код (70) — 70
  OK   отчёт самотеста пишется в UTF-8-ФАЙЛ (stdout для этого не нужен)

ИТОГ: ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ
```

Без графической сессии помощник проверяет её **до** создания `QApplication` (иначе Qt падает с
`Aborted`) и тихо выходит с кодом 0. Строка в UTF-8-журнале помощника (`…/logs/main.log`):

```
[2026-10-17 23:10:59] INFO  forest-ui: нет графической сессии (DISPLAY/WAYLAND_DISPLAY не заданы) — помощник не запущен
```

В stdout при этом печатается ASCII-безопасный вариант той же строки (вместо кириллицы — `?`):
помощник держит свой stdout ASCII-совместимым, а весь не-ASCII текст пишет в журнал и в отчёт — это
ровно то, что проверяет тест «печать символа вне cp1251 не бросает исключения».

### 3.3 Единственная красная проверка на Linux — геометрия меню и шрифт WSL

`forest-ui --selftest` в WSL даёт **одну** FAIL из 161 проверки:

```
FAIL геометрия меню — числа макета (панель 269 px, строка 34, полоса 3) — 301x487 строка 34
```

Это **не** дефект продукта, а разница метрик шрифта, и вот почему. Ширина панели меню не является
константой: она считается из метрик шрифта —

```
panel_w = STRIPE(3) + PAD_L(14) + row_w + PAD_R(15) + HAIRLINE(1),  row_w = max(MARK+GAP+advance)
```

(`packaging-linux/../src/ui_menu.py`, `_layout_rows`). На живом Windows-рабочем столе шрифт —
**Segoe UI**, в WSL его нет (`fc-match sans-serif` → **DejaVu Sans**), и тот же текст шире.
Прямой замер одного и того же набора подписей (12 px, ширины в пикселях):

| Среда | Семейство (QFontInfo) | ширины подписей меню |
| --- | --- | --- |
| Windows, платформа `windows` | **Segoe UI** | 91 / 106 / 166 / **180** |
| WSL Ubuntu, платформа `offscreen` | **DejaVu Sans** | 101 / 118 / 188 / **206** |

Отсюда и 301 px вместо 269 px (в английской локали — 352 px). На живом ПК меню ровно 269x487 —
это записано в `docs/menu-shots.json` (пиксельные снимки меню на 100 % и 150 %) и подтверждено
самотестом на собранном `forest-ui.exe` (303 OK / 0 FAIL, `docs/WINDOWS-PROOF-1.3.0-menu-hover.ru.md`).
Проверка в самотесте сравнивает с числом макета и потому краснеет в среде без Segoe UI; продукт при
этом рисует меню по метрикам доступного шрифта, то есть шире, а не «неправильно». В WSL нет
графической сессии, поэтому и проверить иначе (на «живом» рабочем столе Linux) нечем.

Замеры шрифта воспроизводятся: `state\wsl-logs\metrics_probe.py`.

## 4. Матрица: пакет × дистрибутив

Каждый прогон: установка одной командой → служба активна → **реальный трафик** через прокси на порту
11808 → чистое удаление (пакет, файлы, юниты, порт). Ни одного FAIL, ни одного SKIP.

```sh
bash linux/tests/wsl-matrix.sh --distro <дистрибутив> \
     --package packaging-linux/out/<файл> --port 11808 --isolate
```

| Прогон | Дистрибутив | Пакет | Итог | 204 через |
| --- | --- | --- | --- | --- |
| deb-ubuntu | Ubuntu 26.04 | `.deb` | 13/13 PASS, 0 FAIL, 0 SKIP | ~132 с |
| rpm-opensuse | openSUSE Tumbleweed 20260723 | `.rpm` | 13/13 PASS, 0 FAIL, 0 SKIP | ~135 с |
| pac-arch | Arch Linux rolling | `.pkg.tar.zst` | 13/13 PASS, 0 FAIL, 0 SKIP | ~144 с |
| tar-ubuntu | Ubuntu 26.04 (user-mode, `--as-user tester`) | `.tar.gz` | 14/14 PASS, 0 FAIL, 0 SKIP | ~135 с |
| appimage-ubuntu | Ubuntu 26.04 (user-mode, `--as-user tester`) | `AppImage` | 14/14 PASS, 0 FAIL, 0 SKIP | ~132 с |

Прогон этого выпуска — 2026-10-18, логи: `state/wsl-logs/matrix-20261018-015722/`. Все пять форматов
собраны из **одного** дерева ELF (§1), и у каждого проверена не только установка, но и **чистое
удаление**: пакет не в базе менеджера, файлов полезной нагрузки нет, юнитов/автозапуска нет,
порт 11808 закрыт.

Прогоны идут **по одному дистрибутиву за раз** (`--isolate` попутно гасит остальные): два запущенных
дистрибутива делят root-cgroup и один UID 1000, и `systemctl --user` начинает падать с
`Device or resource busy` — это выглядит как «сломанный systemd», хотя лечится освобождением соседа.

## 5. Дословные выводы матрицы

Прогон 2026-10-18 на артефактах §2 (`.deb` 97 084 272 Б, `.rpm`, Arch, `.tar.gz`, AppImage — хеши в §2).
Каждый блок — вывод стенда `linux/tests/wsl-matrix.sh` целиком, без правок.

### deb-ubuntu — Ubuntu 26.04 (системная установка, `apt`)

```
#    STEP               STATUS DETAIL
--   ------------------ ------ ---------------------------------------------
1    env                PASS   Ubuntu 26.04 LTS 26.04
2    systemd            PASS   systemd is-system-running=running
3    pkg-type           PASS   deb 'forest-proxy-updater' 1.3.0
4    pkg-copy           PASS   /tmp/forest-proxy-updater_1.3.0_amd64.deb (93M)
5    install            PASS   forest-proxy-updater installed (/usr/lib/forest-proxy-updater/forest-proxy-updater)
6    unit-present       PASS   systemd unit 'forest-proxy-updater.service' installed and visible
7    service-active     PASS   forest-proxy-updater.service is active
8    proxy-traffic      PASS   curl -x http://127.0.0.1:11808 -> 204 after ~132s
9    uninstall          PASS   forest-proxy-updater removed
10   residue-pkg        PASS   not registered in the package DB and payload gone
11   residue-files      PASS   no payload files left behind
12   residue-units      PASS   no unit file / no active service / no autostart
13   residue-port       PASS   127.0.0.1:11808 is closed

TOTAL: 13 steps  |  13 PASS  |  0 FAIL  |  0 SKIP
RESULT: PASS
```

TOTAL: 13 steps  |  13 PASS  |  0 FAIL  |  0 SKIP

Трафик (шаг `proxy-traffic`): `curl -x http://127.0.0.1:11808 -> 204 after ~132s`

### rpm-opensuse — openSUSE Tumbleweed (системная установка, `zypper`/`rpm`)

```
#    STEP               STATUS DETAIL
--   ------------------ ------ ---------------------------------------------
1    env                PASS   openSUSE Tumbleweed 20260723
2    systemd            PASS   systemd is-system-running=running
3    pkg-type           PASS   rpm 'forest-proxy-updater' 1.3.0-1
4    pkg-copy           PASS   /tmp/forest-proxy-updater-1.3.0-1.x86_64.rpm (93M)
5    install            PASS   forest-proxy-updater installed (/usr/lib/forest-proxy-updater/forest-proxy-updater)
6    unit-present       PASS   systemd unit 'forest-proxy-updater.service' installed and visible
7    service-active     PASS   forest-proxy-updater.service is active
8    proxy-traffic      PASS   curl -x http://127.0.0.1:11808 -> 204 after ~135s
9    uninstall          PASS   forest-proxy-updater removed
10   residue-pkg        PASS   not registered in the package DB and payload gone
11   residue-files      PASS   no payload files left behind
12   residue-units      PASS   no unit file / no active service / no autostart
13   residue-port       PASS   127.0.0.1:11808 is closed

TOTAL: 13 steps  |  13 PASS  |  0 FAIL  |  0 SKIP
RESULT: PASS
```

TOTAL: 13 steps  |  13 PASS  |  0 FAIL  |  0 SKIP

Трафик (шаг `proxy-traffic`): `curl -x http://127.0.0.1:11808 -> 204 after ~135s`

### pac-arch — Arch Linux (системная установка, `pacman -U`)

```
#    STEP               STATUS DETAIL
--   ------------------ ------ ---------------------------------------------
1    env                PASS   Arch Linux rolling
2    systemd            PASS   systemd is-system-running=running
3    pkg-type           PASS   pac 'forest-proxy-updater' 1.3.0-1
4    pkg-copy           PASS   /tmp/forest-proxy-updater-1.3.0-1-x86_64.pkg.tar.zst (94M)
5    install            PASS   forest-proxy-updater installed (/usr/lib/forest-proxy-updater/forest-proxy-updater)
6    unit-present       PASS   systemd unit 'forest-proxy-updater.service' installed and visible
7    service-active     PASS   forest-proxy-updater.service is active
8    proxy-traffic      PASS   curl -x http://127.0.0.1:11808 -> 204 after ~144s
9    uninstall          PASS   forest-proxy-updater removed
10   residue-pkg        PASS   not registered in the package DB and payload gone
11   residue-files      PASS   no payload files left behind
12   residue-units      PASS   no unit file / no active service / no autostart
13   residue-port       PASS   127.0.0.1:11808 is closed

TOTAL: 13 steps  |  13 PASS  |  0 FAIL  |  0 SKIP
RESULT: PASS
```

TOTAL: 13 steps  |  13 PASS  |  0 FAIL  |  0 SKIP

Трафик (шаг `proxy-traffic`): `curl -x http://127.0.0.1:11808 -> 204 after ~144s`

### tar-ubuntu — Ubuntu 26.04 (портативный `.tar.gz`, `install.sh`, user-mode `--as-user tester`)

```
#    STEP               STATUS DETAIL
--   ------------------ ------ ---------------------------------------------
1    env                PASS   Ubuntu 26.04 LTS 26.04
2    systemd            PASS   systemd is-system-running=running
3    user-mode          PASS   tester uid=1000 home=/home/tester
4    pkg-type           PASS   tar 'forest-proxy-updater' 1.3
5    pkg-copy           PASS   /tmp/forest-proxy-updater-1.3.0-linux-x86_64.tar.gz (33M)
6    install            PASS   forest-proxy-updater installed (/home/tester/.local/share/forest-proxy-updater/forest-proxy-updater)
7    unit-present       PASS   systemd unit 'forest-proxy-updater.service' installed and visible
8    service-active     PASS   forest-proxy-updater.service is active
9    proxy-traffic      PASS   curl -x http://127.0.0.1:11808 -> 204 after ~135s
10   uninstall          PASS   forest-proxy-updater removed
11   residue-pkg        PASS   not registered in the package DB and payload gone
12   residue-files      PASS   no payload files left behind
13   residue-units      PASS   no unit file / no active service / no autostart
14   residue-port       PASS   127.0.0.1:11808 is closed

TOTAL: 14 steps  |  14 PASS  |  0 FAIL  |  0 SKIP
RESULT: PASS
```

TOTAL: 14 steps  |  14 PASS  |  0 FAIL  |  0 SKIP

Трафик (шаг `proxy-traffic`): `curl -x http://127.0.0.1:11808 -> 204 after ~135s`

### appimage-ubuntu — Ubuntu 26.04 (AppImage, установка в домашний каталог пользователя)

```
#    STEP               STATUS DETAIL
--   ------------------ ------ ---------------------------------------------
1    env                PASS   Ubuntu 26.04 LTS 26.04
2    systemd            PASS   systemd is-system-running=running
3    user-mode          PASS   tester uid=1000 home=/home/tester
4    pkg-type           PASS   appimage 'ForestProxyUpdater' 1.3.0
5    pkg-copy           PASS   /tmp/ForestProxyUpdater-1.3.0-x86_64.AppImage (34M)
6    install            PASS   ForestProxyUpdater installed (/home/tester/.local/share/forest-proxy-updater/forest-proxy-updater)
7    unit-present       PASS   systemd unit 'forest-proxy-updater.service' installed and visible
8    service-active     PASS   forest-proxy-updater.service is active
9    proxy-traffic      PASS   curl -x http://127.0.0.1:11808 -> 204 after ~132s
10   uninstall          PASS   forest-proxy-updater removed
11   residue-pkg        PASS   not registered in the package DB and payload gone
12   residue-files      PASS   no payload files left behind
13   residue-units      PASS   no unit file / no active service / no autostart
14   residue-port       PASS   127.0.0.1:11808 is closed

TOTAL: 14 steps  |  14 PASS  |  0 FAIL  |  0 SKIP
RESULT: PASS
```

TOTAL: 14 steps  |  14 PASS  |  0 FAIL  |  0 SKIP

Трафик (шаг `proxy-traffic`): `curl -x http://127.0.0.1:11808 -> 204 after ~132s`

## 6. Пометки об ограничениях проверки — внутри самих пакетов

Оговорка «проверено в WSL, GUI не проверен на живом рабочем столе» лежит не только здесь, но и в
метаданных каждого пакета: пользователь увидит её при установке, не открывая README.

### `.deb` — `dpkg-deb -I`

```
Package: forest-proxy-updater
Version: 1.3.0
Section: net
Priority: optional
Architecture: amd64
Maintainer: FOREST <forest@example.invalid>
Installed-Size: 96600
Depends: curl,
 libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0,
 libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xkb1,
 libgl1, libegl1
Homepage: https://github.com/FORESTmg/proxy-auto-updater
Description: FOREST's Proxy Auto-updater — автообновляемый прокси на ядре Xray
  ...
  ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU): Проверка пакета выполнялась в среде WSL (Linux
  внутри Windows), а не на установленной системе на реальном железе. Меню и
  уведомления в графическом интерфейсе протестированы логически, но НЕ проверены
  на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в
  репозитории.
  .
  TESTING SCOPE (EN): Package testing was performed in WSL (Linux on Windows),
  not on a real installation on bare metal. The GUI menu and notifications are
  tested logically but have NOT been verified on a live desktop — issues are
  possible. If something does not work, please report it in the repository.
```

Проверка грепом по выводу `dpkg-deb -I` (номера строк — из самого вывода):

```
46:  ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU): Проверка пакета выполнялась в среде WSL (Linux
52:  TESTING SCOPE (EN): Package testing was performed in WSL (Linux on Windows),
```

### `.rpm` — `rpm -qi`

```
Name        : forest-proxy-updater
Version     : 1.3.0
Release     : 1
Architecture: x86_64
License     : GPL-3.0-only
Summary     : Auto-updating proxy supervisor built on top of the Xray core
Description :
...
ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU): Проверка пакета выполнялась в среде WSL (Linux внутри Windows), а не на установленной системе на реальном железе. Меню и уведомления в графическом интерфейсе протестированы логически, но НЕ проверены на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в репозитории.

TESTING SCOPE (EN): Package testing was performed in WSL (Linux on Windows), not on a real installation on bare metal. The GUI menu and notifications are tested logically but have NOT been verified on a live desktop — issues are possible. If something does not work, please report it in the repository.
```

Зависимости объявлены по SONAME (имя пакета не зависит от дистрибутива):

```
curl
libxcb-cursor.so.0()(64bit)
libxcb-icccm.so.4()(64bit)
libxcb-image.so.0()(64bit)
libxcb-keysyms.so.1()(64bit)
libxcb-render-util.so.0()(64bit)
libxcb-shape.so.0()(64bit)
libxcb-xkb.so.1()(64bit)
libxkbcommon-x11.so.0()(64bit)
```

### Arch — `pacman -Qip` и скрипт установки

```
Name            : forest-proxy-updater
Version         : 1.3.0-1
Description     : Auto-updating proxy supervisor built on top of the Xray core (core and geo bases bundled); GUI menu/notifications tested only in WSL, not on a live desktop
Depends On      : curl  libxkbcommon-x11  xcb-util-cursor  xcb-util-wm  xcb-util-image  xcb-util-keysyms  xcb-util-renderutil  libxcb
Optional Deps   : libglvnd: GL/EGL для GL-интеграции xcb (иначе программная отрисовка)
                  mesa: программный GL-драйвер (llvmpipe) для машин без видеодрайвера
Install Script  : Yes
```

Скрипт установки (`.INSTALL` внутри `.pkg.tar.zst`) печатает пользователю то же самое:

```
  echo "ОГРАНИЧЕНИЯ ПРОВЕРКИ: Проверка пакета выполнялась в среде WSL (Linux внутри Windows), а не на установленной системе на реальном железе. Меню и уведомления в графическом интерфейсе протестированы логически, но НЕ проверены на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в репозитории."
  echo "TESTING SCOPE: Package testing was performed in WSL (Linux on Windows), not on a real installation on bare metal. The GUI menu and notifications are tested logically but have NOT been verified on a live desktop — issues are possible. If something does not work, please report it in the repository."
```

У `.tar.gz` и `AppImage` та же оговорка лежит в `PACKAGE-README.md` (в корне архива и в `.AppDir`) и в
`doc/README.md`.

## 7. Что НЕ проверено (честно)

- Пакеты ставились **в WSL**, а не на «живом» железе: systemd работает, ядро WSL2 (см. §5). На
  bare-metal установка не проверялась; `lintian`/`rpmlint` не запускались, подписи gpg нет.
- **Графический интерфейс на живом рабочем столе Linux не проверен**: в WSL нет графической сессии.
  Значок в трее, меню и рисование карточки уведомления проверены самотестами (161 проверка помощника
  + 285 проверок основной программы) и логически. **Одна проверка в WSL красная** — геометрия меню
  против чисел макета: причина разобрана в §3.3 (в WSL нет Segoe UI, метрики другого шрифта).
  На живом Windows-рабочем столе то же меню даёт 269x487 и проверка зелёная (303 OK / 0 FAIL).
- **AppImage**: проверен через `--appimage-extract-and-run` (в WSL нет FUSE). Штатное монтирование
  AppImage не проверялось; автотесты внутри AppImage пройдены (`POSIX-тесты пройдены`).
- Загрузка списков серверов из интернета проверялась только косвенно — через факт успешного выхода в
  сеть (код 204). Наличие конкретных узлов в пуле не измерялось.
- Матрица прогонялась по **одному** дистрибутиву на формат (сознательно: правило «один дистрибутив
  одновременно» и ограничение по времени). В этой редакции `.deb` проверен на Ubuntu, но не на
  Debian; `.rpm` — на openSUSE, но не на Fedora/Rocky; Arch — на `archlinux` в WSL; `.tar.gz` и
  AppImage — на Ubuntu в режиме пользователя.
- Матрица проверяет **установку, активную службу, реальный 204 и чистое удаление**. Отрисовка меню и
  карточки на Linux-рабочем столе (X11/Wayland, реальный трей) не проверялась: нет сессии.
- Хеши и размеры относятся **именно** к артефактам этого прогона (2026-10-18). `packaging-linux/out/`
  не версионируется, поэтому при повторной сборке хеши будут другими: выкладывать надо те файлы, что
  перечислены в §2, либо пересобрать и заново прогнать матрицу.

### 7.1 Неизменность входов во время сборки

Сборка 2026-10-18 велась по правилу «вход не менялся»: агрегатный sha256 всех входных модулей
(`src/*.py`, `linux/*.py`, спеки PyInstaller, `version_info.txt`, локи ядра, все сборщики и раскладки
`packaging-linux/`) совпал **до** и **после** сборок —

```
pre  = 3e82693f681e71d9aab914566c3c71d50660e3a163c15f929f51df313bfe4dc2
post = 3e82693f681e71d9aab914566c3c71d50660e3a163c15f929f51df313bfe4dc2
```

То есть пакеты §2 и прогоны §3–§5 относятся к одному и тому же состоянию исходников (хеши модулей —
§1). Окно тишины перед сборкой проверялось отдельно (`find … -newermt '-240 seconds'` — только правка
`version_info.txt`, сделанная до старта).

Если правки исходников продолжатся, Linux-часть нужно пересобрать и заново прогнать матрицу:
`packaging-linux/out/` в git нет, а хеш ELF внутри пакета не виден — «номер версии тот же» ничего не
доказывает.
