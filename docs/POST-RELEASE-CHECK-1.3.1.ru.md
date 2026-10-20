# Проверка выпуска v1.3.1 — после публикации

Дата: 2026-10-21. Репозиторий: **[FORESTmg/proxy-auto-updater](https://github.com/FORESTmg/proxy-auto-updater)**
(публичный). Релиз: **[v1.3.1](https://github.com/FORESTmg/proxy-auto-updater/releases/tag/v1.3.1)**.
Тег `v1.3.1` → коммит `5b37e93eb8955a1b0556d890e8089e0b96e056ad` (= `main` HEAD на момент публикации).

**English summary.** Post-publication check of release `v1.3.1`: 19 assets re-read from the GitHub API
(names, sizes, server-computed SHA-256 digests) and every one matched the local build tree; the
repository is still **public** with the same neutral description; the five-format acceptance matrix is
green (13/13, 13/13, 14/14, 13/13, 13/13 steps, real `204` through every package); the payload hashes
inside all five packages are byte-identical to the build tree and the WSL / not-verified-on-a-live-desktop
note is present in all of them; the live Windows installation was **brought to the released build**
(hashes match the release, `config.json` byte-identical, one supervisor + one helper + one core, `204`
locally / over SOCKS / over LAN, 3 real right-clicks opened the menu with no crash); the `pathological`
`github.com` host was unreachable and everything was done through the local proxy. What is *not*
verified is at the bottom — the list is not empty.

## 1. Версия сведена к одной — 1.3.1

| Место | Значение |
| --- | --- |
| `packaging/version_info.txt` | `filevers=(1, 3, 1, 0)`, `prodvers=(1, 3, 1, 0)`, `FileVersion 1.3.1.0`, `ProductVersion 1.3.1` |
| `packaging-linux/lib/common.sh` | `VERSION="${FOREST_PKG_VERSION:-1.3.1}"` |
| `CHANGELOG.md` | секция `## [1.3.1] — 2026-10-21` |
| `docs/RELEASE-NOTES-1.3.1.md` | заметки к выпуску (RU + EN, с пометкой про WSL) |
| `docs/LINUX-INSTALL.md` / `.ru.md`, `packaging-linux/README.md` | имена пакетов `1.3.1`, дефолт `FOREST_PKG_VERSION` |
| в коде `src/*.py` | константы версии нет (править не нужно) |

Ресурс версии **в самом выложенном exe** (прочитан `pefile`, не по исходнику):

```
packaging\dist\ForestProxyUpdater.exe
  filevers  (1, 3, 1, 0)      prodvers  (1, 3, 1, 0)
  FileVersion 1.3.1.0         ProductVersion 1.3.1
```

## 2. Что чем собрано

```
Windows  packaging/build.ps1     -Python Python312\python.exe (3.12.10) + PyInstaller 6.22.3
         ForestProxyUpdater.exe  30 779 032 байт  sha256 7229150d95b412b4973191332fbb6e5efba9df5996067389498babfe86d10cd0
         build-ui.ps1            forest-ui.exe 37 693 453 байт  sha256 3c7d19008af69849862efa2836b53279ae8b88585cf4ea88501b920d9825db57
Linux    packaging/build-linux.sh / build-ui.sh (в WSL Ubuntu 26.04)
         PYI_PYTHON = packaging/.build-venv-linux (python 3.12.15) + PyInstaller 6.22.3
         forest-proxy-updater    34 333 536 байт  sha256 437f6bcb97a0da0dd884a311c6002e96ebd1ae4650721e474cf6361a70da443e
         forest-ui               64 395 248 байт  sha256 12256758faf78faef27d9caa4d02c6af80d2c6d08c7117729636c284c821dd9c
```

Входные данные сборки не менялись во время сборок (хеши `src/*.py`, `config.example.json` и трёх
спеков совпали до и после; окно тишины перед стартом — правок моложе 20 минут нет).

**Доказательство, что в выложенных бинарниках именно исправленный код**, а не «версия поднята»:
из CArchive/PYZ собранных файлов распакованы модули и в них найдены строки-маркеры выпуска
(не «есть номер версии», а «есть код фикса»):

| Маркер | `ForestProxyUpdater.exe` | `forest-ui.exe` | Linux ELF | Linux `forest-ui` |
| --- | --- | --- | --- | --- |
| `tls_measure` (мульти-доменный гейт) | 3 | 3 | 3 | 3 |
| `youtube_filter` | 5 | 1 | 5 | 1 |
| `burst_guard` / `attribute_bad_nodes` / `ban_bad_nodes` | 4 / 5 / 3 | 2 / 4 / 2 | 4 / 5 / 3 | 2 / 4 / 2 |
| `post_validate_pool` | 3 | 2 | 3 | 2 |
| `burst_backoff_s` / `burst_cooldown_after_rebuild_s` | 3 / 2 | 3 / 2 | 3 / 2 | 3 / 2 |
| `pool_floor` / `tls_min_ok` / `tls_slow_factor` | 4 / 4 / 3 | 4 / 4 / 3 | 4 / 4 / 3 | 4 / 4 / 3 |
| `ru.wikipedia.org` / `youtube.com/generate_204` | 3 / 5 | 3 / 5 | 3 / 5 | 3 / 5 |
| `CARD_COALESCE_MS` (склейка карточек) | — (это помощник) | **1** | — | **1** |

`packaging/dist/ForestProxyUpdater.exe --selftest` → `SELFTEST: PASSED` (код 0); Linux
`forest-proxy-updater --selftest` → **300 OK / 0 FAIL** (в нём закреплены наличие
`parallel_probe`, `post_validate_pool`, `attribute_bad_nodes`, `ban_bad_nodes`, `burst_guard`,
`burst_trigger`, `cycle_running`, дефолт `tls_urls` и YouTube-зонд балансировщика).

## 3. Файлы релиза — как их отдаёт API (имена, размеры, digest)

Прочитано обратным чтением через API (`releases/tags/v1.3.1`), сверено с локальным деревом сборки
по размеру **и** по SHA-256, который посчитал сам GitHub (`assets[].digest`): **19 из 19 совпали**,
локальных файлов, которых нет в релизе, нет.

| Файл | Размер, байт | SHA-256 |
| --- | --- | --- |
| `ForestProxyUpdater.exe` | 30 779 032 | `7229150d95b412b4973191332fbb6e5efba9df5996067389498babfe86d10cd0` |
| `forest-ui.exe` | 37 693 453 | `3c7d19008af69849862efa2836b53279ae8b88585cf4ea88501b920d9825db57` |
| `forest-proxy-updater-linux-x86_64` | 34 333 536 | `437f6bcb97a0da0dd884a311c6002e96ebd1ae4650721e474cf6361a70da443e` |
| `forest-ui-linux-x86_64` | 64 395 248 | `12256758faf78faef27d9caa4d02c6af80d2c6d08c7117729636c284c821dd9c` |
| `forest-proxy-updater_1.3.1_amd64.deb` | 97 152 880 | `e3bd16ea6328476877403587810f94f6abd747fedb2918483099d7964805df8b` |
| `forest-proxy-updater-1.3.1-1.x86_64.rpm` | 97 295 143 | `a967ac83bfd81900263939d1b1adab4988965d37e3a97eca633cb49b4449be97` |
| `forest-proxy-updater-1.3.1-1-x86_64.pkg.tar.zst` | 98 028 585 | `2cc817268c8a618274082ce2e4da2f9acb4bb1847055a7aae1943a05e32b4c74` |
| `forest-proxy-updater-1.3.1-linux-x86_64.tar.gz` | 33 781 842 | `3bb8ef15920b9d07b60f0a504d3cac5af9e6f3b8348e50bfa6c8d98714cc7606` |
| `ForestProxyUpdater-1.3.1-x86_64.AppImage` | 34 757 112 | `0bc22c968e3c6e58baa6aa8e03e7bea153d8eb17491c8543689f172e97d67593` |
| `SHA256SUMS` | 901 | `318c246eae16aea2…` (9 записей внутри) |

Плюс девять одноимённых `.sha256` (по одному на каждый артефакт) — всего 19 ассетов.

Репозиторий прочитан обратно тем же API: `visibility = public`, `private = false`,
описание не менялось («Fully automatic proxy upkeep: finds fresh server lists, tests, drops dead
ones, switches with no human input. Windows (.exe) + Linux (.deb/.rpm/Arch/AppImage/tarball).
Vibe-coded.») — без страны, провайдера и фильтров.

## 4. Матрица: 5 форматов × (установка → служба активна → РЕАЛЬНЫЙ 204 → чистое удаление)

Стенд `linux/tests/wsl-matrix.sh --isolate --port 11808`, по одному дистрибутиву за раз, живой
прокси (порт 10808) не задействован. «РЕАЛЬНЫЙ 204» — это ответ, прошедший **через туннель
пакета**: `curl -x http://127.0.0.1:11808 https://www.gstatic.com/generate_204` → `204`.

| Формат | Дистрибутив | Шагов | PASS | 204 получен через | Файлы/юниты/порт после удаления |
| --- | --- | --- | --- | --- | --- |
| `.deb` | Debian GNU/Linux 13 (trixie) | 13 | **13** | ~162 с | чисто (0 файлов, 0 юнитов, порт закрыт) |
| `.tar.gz` | Ubuntu 26.04 LTS | 13 | **13** | ~246 с | чисто |
| `.AppImage` | Ubuntu 26.04 LTS (установка от пользователя `tester`) | 14 | **14** | ~240 с | чисто |
| `.rpm` | openSUSE Tumbleweed | 13 | **13** | ~237 с | чисто |
| Arch `.pkg.tar.zst` | Arch Linux | 13 | **13** | ~165 с | чисто |

Проверяемые шаги (одинаковые для всех форматов): окружение; systemd запущен; тип и версия пакета
(`1.3.1`); копирование в дистрибутив; установка штатным менеджером; юнит виден системе; служба
**активна**; **трафик через прокси → 204**; удаление; отсутствие регистрации в БД менеджера;
отсутствие файлов; отсутствие юнитов/автозапуска; порт `11808` закрыт. У AppImage добавляется
шаг установки от пользователя. Итоги по всем пяти прогонам: **66 PASS / 0 FAIL / 0 SKIP**.

## 5. Содержимое пакетов — совпадает с деревом сборки

Из каждого пакета полезная нагрузка распакована и её хеши сверены с деревом сборки:

| Формат | Чем распаковано | `forest-proxy-updater` | `forest-ui` |
| --- | --- | --- | --- |
| `.deb` | `dpkg-deb -x` | `437f6bcb…da443e` ✔ | `12256758…21dd9c` ✔ |
| `.rpm` | `rpm2cpio \| cpio -idm` | `437f6bcb…da443e` ✔ | `12256758…21dd9c` ✔ |
| Arch | `tar --zstd -xf` | `437f6bcb…da443e` ✔ | `12256758…21dd9c` ✔ |
| `.tar.gz` | `tar xzf` | `437f6bcb…da443e` ✔ | помощник прилагается отдельно |
| `.AppImage` | `--appimage-extract` | `437f6bcb…da443e` ✔ | помощник прилагается отдельно |

`config.example.json` внутри AppImage побайтово равен репозиторному
(`98804ca3c8ca900541202cfa1640986f15464de3452f356afe28be027fa33713`).

**Пометки про WSL и непроверенный живой рабочий стол** — на месте во всех форматах:

* `.deb` — в `Description:` пакета `dpkg-deb -I` (2 строки: RU-блок «ОГРАНИЧЕНИЯ ПРОВЕРКИ» и
  EN-блок «TESTING SCOPE»), плюс те же тексты в `docs/LINUX-INSTALL*.md` внутри пакета;
* `.rpm` — `rpm -qip` → в описании обе строки (RU + EN);
* Arch — `pkgdesc` в `PKGBUILD` («GUI menu/notifications tested only in WSL, not on a live
  desktop») и тот же блок комментарием;
* `.tar.gz` и `.AppImage` — в `doc/INSTALL*.md` внутри архива.

## 6. Живая установка приведена к релизной сборке

Раньше на машине лежала сборка `930fbc14…` / `793e789a…` — та же логика, другие байты
(пересборка PyInstaller побайтово не воспроизводима). Теперь в живую установку
(`%LOCALAPPDATA%\ForestProxyUpdater`) положены **ровно релизные** файлы:

```
до:    ForestProxyUpdater.exe 930fbc14…  forest-ui.exe 793e789a…   →  .backup-pre-1.3.1-rel-win\
после: ForestProxyUpdater.exe 7229150d…  forest-ui.exe 3c7d1900…   ←  совпадают с релизом
```

Обновление штатным путём: `state/stop.flag` + `state/ui_stop.flag` (все процессы завершились сами,
реестр системного прокси не трогался) → подмена файлов → снятие флагов →
`schtasks /Run /TN ForestProxyUpdater-Watchdog`.

| Проверка | Результат |
| --- | --- |
| `config.json` | `2da919886794f5c76f6bff39e43ae309f261bb282819be0688e3def292dc30a2` — **до и после совпадает** |
| Экземпляров программы | **1** (пара bootloader + рабочий: pid 8436 → 5372) |
| Помощников | **1** (pid 40460 → 34524) |
| Ядер | **1** (pid 24192) — временные пробы ядра завершились |
| `state/tray_status.json` | `state=ok`, `tip="FOREST связь OK \| 10 серверов \| РФ напрямую \| watch OK"`, `rtt=236.7` |
| `curl -x http://127.0.0.1:10808 …/generate_204` | **204** (0.24 с) |
| `curl -x socks5h://127.0.0.1:10808 …/generate_204` | **204** (0.25 с) |
| `curl -x http://192.168.0.92:10808 …/generate_204` | **204** (0.23 с) |
| YouTube ×3 (`youtube.com/generate_204`) | **204 / 204 / 204** (0.24 / 0.24 / 0.25 с) |

### Три настоящих правых клика по значку

Клики не «программные»: `SendInput` в очередь ввода Windows по центру значка, найденного через
UI Automation в переполнении области уведомлений (`state/menu-proof-harness/click_once.py right`).

| Клик | Меню открылось | Окно меню | Помощники до/после | `crash.log` |
| --- | --- | --- | --- | --- |
| 1 | **да** | `Qt691QWindowPopupSaveBits` 297×515 в `[1997, 768, 2294, 1283]` | `[34524, 40460]` → `[34524, 40460]` | не вырос (304 байта, mtime 19.10) |
| 2 | **да** | то же окно | те же pid | не вырос |
| 3 | **да** | то же окно | те же pid | не вырос |

`Esc` закрывает меню (`menu_before` = 1 окно → `menu_after` = []), падений нет. Итог: **3/3 клика
открыли меню, 0 падений, оба процесса помощника живы**.

## 7. Уборка `.backup-*`

Удалены **6 файлов `.exe`** (195.7 МиБ): `.backup-pre-rel130-win`, `.backup-pre-youtube-fix-win`,
`.backup-pre-1.3.1-rel-win`. Осталось ровно то, что нужно: `config.json`, `config.json.bak`,
`app.ico` и `.sha256`-файлы старых сборок (по ним видно, что именно там лежало). Каталоги
`state/`, `logs/`, `bin/` и боевые `.exe` не тронуты, `config.json` живой установки не менялся.

## 8. `github.com` был недоступен

Провайдер отдавал `Connection was reset` на `github.com`, `api.github.com` и `codeload.github.com`
при прямом обращении (в окружении эти хосты исключены из прокси через `NO_PROXY`, поэтому запросы
уходили мимо рабочего прокси). Всё, что требовало GitHub (`gh api`, `git push`, `gh release create`),
сделано **через локальный прокси** (`http://127.0.0.1:10808` при снятом `NO_PROXY`) — те же
команды, только с явным маршрутом. Опубликовано с первой попытки, повторов не потребовалось.

## 9. Что НЕ проверено (честно)

- **GUI на живом Linux-рабочем столе (X11/Wayland, настоящий трей) не проверялся** — в WSL нет
  графической сессии. Один FAIL Linux-самотеста помощника — геометрия меню против чисел макета
  (в WSL нет Segoe UI, шрифт стенда шире на 12–14 %: панель 301 px вместо 269); на живом Windows
  та же проверка зелёная. Это среда стенда, а не дефект.
- **AppImage** проверен через `--appimage-extract-and-run`: штатное монтирование (FUSE) в WSL
  недоступно.
- **По одному дистрибутиву на формат**: `.deb` — Debian (не Ubuntu; на Ubuntu проверены `.tar.gz` и
  AppImage), `.rpm` — openSUSE (не Fedora/Rocky), Arch — Arch Linux. Другие дистрибутивы тех же
  семейств не прогонялись.
- **Функциональные проверки 1.3.1 на живом трафике** (0 переотборов / 0 пересборок пула / 0
  перезапусков ядра за час, 7 из 7 сайтов на 840 запросах, YouTube 3/3) сняты на сборке из **того же
  состояния исходников** (`930fbc14…`), а не на релизных байтах; релизные байты проверены
  самотестом, распаковкой маркеров и живой установкой (204 локально/SOCKS/LAN, 3 правых клика).
  Час живого наблюдения на релизных байтах не переигрывался.
- **Подписей gpg нет**, `lintian` / `rpmlint` не запускались; контрольные суммы есть только sha256.
- **Тег аннотированный, но не подписанный** (`git tag -a`, без `-s`).
