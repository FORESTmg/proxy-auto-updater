# Проверка выпуска v1.3.0 — после публикации

Дата: 2026-10-18. Репозиторий: **[FORESTmg/proxy-auto-updater](https://github.com/FORESTmg/proxy-auto-updater)**
(публичный). Релиз: **[v1.3.0](https://github.com/FORESTmg/proxy-auto-updater/releases/tag/v1.3.0)**.

**English summary.** Post-publication check of release `v1.3.0`: 19 assets re-read from the GitHub API
(names, sizes, server-computed SHA-256 digests) and matched against the local build tree; the repository
is still **public** with a neutral description; the five-format acceptance matrix is green
(13-14 steps each, real `204` through every package); the live Windows installation is intact
(one supervisor + one helper + one core, `204` locally and over LAN, `config.json` byte-identical);
the released Windows `.exe` passes its own `--selftest` (`SELFTEST: PASSED`). The list of what is *not*
verified is at the bottom — it is not empty.

## 1. Что опубликовано

Версия сведена к одной — **1.3.0**: `packaging/version_info.txt` (`filevers`/`prodvers` = `1, 3, 0, 0`,
`FileVersion` = `1.3.0.0`, `ProductVersion` = `1.3.0`), `packaging-linux/lib/common.sh`
(`VERSION="1.3.0"`), `CHANGELOG.md` (`## [1.3.0] — 2026-10-18`) и `docs/RELEASE-NOTES-1.3.0.md`.
Пометки «1.3.1» относились к промежуточным сборкам меню; отдельного выпуска 1.3.1 нет
(доказательства переименованы в `docs/WINDOWS-PROOF-1.3.0-menu-hover.ru.md`).

Ресурс версии **в самом выложенном exe** (прочитан `pefile`):

```
packaging\dist\ForestProxyUpdater.exe
  filevers = (1, 3, 0, 0)   prodvers = (1, 3, 0, 0)
  FileVersion = 1.3.0.0     ProductVersion = 1.3.0
```

Коммит `ad2102b` в ветке `main`, аннотированный тег `v1.3.0` →
https://github.com/FORESTmg/proxy-auto-updater/releases/tag/v1.3.0

## 2. Файлы релиза — как их отдаёт API (имена, размеры, хеши)

Список получен **заново** из API после публикации (`releases/tags/v1.3.0`), а не из вывода команды
загрузки. Хеши — digest, посчитанный GitHub; они совпадают с локальными побайтово.

| Файл | Размер, Б | sha256 |
| --- | --- | --- |
| `ForestProxyUpdater.exe` | 30 744 691 | `c4cf85f41a7002392f2a3c7bcee81f420438a73f89cecfd315dad9d684cc2587` |
| `forest-ui.exe` | 37 657 964 | `eb30988ba8ab6401ab5dd00f952f27d8e5895e5eafe1eae80b22b8619bcf5c9c` |
| `forest-proxy-updater-linux-x86_64` | 34 296 040 | `0a2ca380903388875b7e6b9b1575b4f230188067af7ab7c158d5280082b4285c` |
| `forest-ui-linux-x86_64` | 64 361 712 | `28d4108641231368b0dd65aed4bd21ee075cf572b3600f6c2f8085e80a4df786` |
| `forest-proxy-updater_1.3.0_amd64.deb` | 97 084 272 | `21a5a9b7e00e5e3b8337b3caf1c076101a9f734c0e3df4bb85c3abbd0fd44a47` |
| `forest-proxy-updater-1.3.0-1.x86_64.rpm` | 97 225 482 | `bc403664b8efeafd0324c72c782a3389ca14c405651db64ef0ecbe8792a6b6f4` |
| `forest-proxy-updater-1.3.0-1-x86_64.pkg.tar.zst` | 97 985 131 | `98ba3e89ab0056ea0d51581474e5333aa793359858336222f9582a5f0bb8bb23` |
| `forest-proxy-updater-1.3.0-linux-x86_64.tar.gz` | 33 744 942 | `3830c1ed867f3ef84acae4a9b9a826d4a8489209c045eee403f320433a1f5789` |
| `ForestProxyUpdater-1.3.0-x86_64.AppImage` | 34 716 152 | `d279626804a93d50607ca833e8fe66840475294fbdfba657b4d43e6fe010b273` |

Плюс к каждому файлу его `.sha256` (9 шт.) и сводная `SHA256SUMS` (901 Б,
`c045397bc34209fef5d07399e9bb9ee58dcffdd5a0bdde5929de96055c81468e`) — всего **19 ассетов**.
`SHA256SUMS`, скачанная из релиза через API, после скачивания совпала с локальной **побайтово**
(`diff` пуст), а `sha256sum -c SHA256SUMS` по локальной папке дал `OK` на всех девяти файлах.

## 3. Матрица приёмки: 5 форматов × (установка → служба активна → РЕАЛЬНЫЙ 204 → чистое удаление)

Артефакты §2 (те же хеши), по одному дистрибутиву за раз, порт **11808**, живой прокси не тронут.
Логи: `state/wsl-logs/matrix-20261018-015722/`, дословные выводы — §5 файла
[`docs/LINUX-PACKAGES-PROOF.ru.md`](LINUX-PACKAGES-PROOF.ru.md).

| Формат | Дистрибутив | Шагов | Итог | 204 через |
| --- | --- | --- | --- | --- |
| `.deb` | Ubuntu 26.04 | 13 | **13 PASS / 0 FAIL / 0 SKIP** | ~132 с |
| `.rpm` | openSUSE Tumbleweed | 13 | **13 PASS / 0 FAIL / 0 SKIP** | ~135 с |
| Arch `.pkg.tar.zst` | Arch Linux rolling | 13 | **13 PASS / 0 FAIL / 0 SKIP** | ~144 с |
| `.tar.gz` | Ubuntu 26.04 (user-mode) | 14 | **14 PASS / 0 FAIL / 0 SKIP** | ~135 с |
| `AppImage` | Ubuntu 26.04 (user-mode) | 14 | **14 PASS / 0 FAIL / 0 SKIP** | ~132 с |

В каждом прогоне проверено и удаление: пакет не в базе менеджера, файлов полезной нагрузки нет,
юнитов и автозапуска нет, порт 11808 закрыт.

Побайтовая сверка полезной нагрузки внутри пакетов с деревом сборки (см. §2.2 того же файла):

```
внутри .deb / .rpm / Arch:  программa 0a2ca380… = linux/dist/forest-proxy-updater
                            помощник  28d41086… = linux/dist/forest-ui
внутри .tar.gz и AppImage:  программa 0a2ca380… (помощник прилагается отдельным файлом)
```

## 4. Самотесты выложенных артефактов

| Артефакт | Прогон | Итог |
| --- | --- | --- |
| `ForestProxyUpdater.exe` (ровно выложенный файл, sha256 `c4cf85f4…`) | `--selftest` на живом Windows | **`SELFTEST: PASSED`**, код 0, без окон |
| `forest-proxy-updater-linux-x86_64` (выложенный файл, `0a2ca380…`) | `--selftest` в Ubuntu, из `/home/tester` (вне дерева сборки и venv) | 285 OK / 0 FAIL, `POSIX-тесты пройдены` |
| `forest-ui-linux-x86_64` (выложенный файл, `28d41086…`) | `--selftest`, `QT_QPA_PLATFORM=offscreen` | 160 OK / **1 FAIL** — геометрия меню против чисел макета (в WSL нет Segoe UI, метрики другого шрифта; разбор — §3.3 того же файла). На живом Windows то же меню 269x487 и 303 OK / 0 FAIL |
| `AppImage` | `--appimage-extract-and-run --selftest` (FUSE в WSL нет) | `POSIX-тесты пройдены` |

## 5. Проверка публикации (обратное чтение из API)

```
repo   : FORESTmg/proxy-auto-updater
private: false      visibility: public      default_branch: main
description: "Fully automatic proxy upkeep: finds fresh server lists, tests, drops dead ones,
              switches with no human input. Windows (.exe) + Linux
              (.deb/.rpm/Arch/AppImage/tarball). Vibe-coded."   ← нейтральное (страна/провайдер/фильтры не упомянуты)
release: tag v1.3.0 -> commit ad2102b (target main), draft=false, prerelease=false
assets : 19 (9 артефактов + 9 .sha256 + SHA256SUMS), заметки RU+EN с пометкой про WSL
```

## 6. Живая установка после публикации

Не переустанавливалась и не менялась (только чтение состояния и трафик через неё):

```
процессы: ForestProxyUpdater.exe 26272 (bootloader) + 28892 (рабочий) + xray.exe 32136
          forest-ui.exe 21052 + 40688            ← ровно один экземпляр и один помощник
state/tray_status.json: возрастом 9.5 с, state=ok,
                        tip="FOREST связь OK | 10 серверов | РФ напрямую | watch OK", rtt=179.5
curl -x http://127.0.0.1:10808   …/generate_204 -> 204  (0.18 с)
curl -x http://<LAN-IP>:10808 …/generate_204 -> 204  (0.18 с)
config.json: sha256 a551878359a1c9f40ac602a5502d1a639ae5d74ac30f6ea67229c5d947aefb8e (не менялся)
```

Значок и меню — на месте (процессы помощника живы, пульс свежий, файлы помощника — те, что лежат в
каталоге установки). Правые клики и карточка в этот раз **не перезапускались**: клик двигает настоящий
курсор пользователя, а карточка — видимое окно; доказательства правого клика (5/5, 0 падений, те же
pid 21052/40688) сняты ранее на этой же установке — `docs/WINDOWS-PROOF-1.3.0-menu-hover.ru.md`.

## 7. Уборка

Из каталогов `.backup-*` рядом с живой установкой удалены **13 файлов `.exe`** (433.1 МиБ):
`.backup-1.2.1`, `.backup-1.2.2`, `.backup-pre-1.3.0-win`, `.backup-pre-1.3.1-win`,
`.backup-pre-menu-hover-win`, `.backup-pre-menu-rclick-win`, `.backup-pre-menu-win`.
`config.json`, `state/`, `logs/`, `src/`, `bin/` и оба боевых `.exe` не тронуты; в `.backup-1.2.1`
остался `app.ico` (не `.exe`).

## 8. Что НЕ проверено (честно)

- **Выложенный Windows `.exe` не прогонялся по живому сценарию** (правые клики по значку, показ
  карточки): он прошёл только `--selftest` (`SELFTEST: PASSED`). Интерактивные доказательства
  сняты на сборке из **того же состояния исходников**, но с другими байтами (пересборка PyInstaller
  не воспроизводима побайтово; неизменность входов доказана агрегатным хешем до/после — §7.1
  `docs/LINUX-PACKAGES-PROOF.ru.md`).
- **Живая установка обновлялась не мной**: там лежит сборка `c23ec070…` / `9917345a…` (01:29/01:27),
  а в релиз выложены `c4cf85f4…` / `eb30988b…` (пересобраны из тех же исходников). Расхождение
  байтовое, не функциональное; при желании установку можно обновить файлами из релиза.
- **Один FAIL Linux-самотеста помощника** — геометрия меню против чисел макета: в WSL нет Segoe UI,
  метрики другого шрифта (§3.3 `docs/LINUX-PACKAGES-PROOF.ru.md`). На живом Windows проверка зелёная.
- **GUI на Linux-рабочем столе (X11/Wayland, реальный трей) не проверялся**: в WSL нет сессии.
- **AppImage** проверен через `--appimage-extract-and-run`; штатное монтирование (FUSE) не проверялось.
- **Матрица по одному дистрибутиву на формат**: `.deb` — Ubuntu (не Debian), `.rpm` — openSUSE
  (не Fedora/Rocky).
- **Подписи gpg нет**, `lintian`/`rpmlint` не запускались.
- **В `docs/WINDOWS-PROOF-1.3.0.ru.md` в публичном репозитории остаются** имя машины `<PC-NAME>`
  и адрес локальной сети `<LAN-IP>` (часть доказательства «204 по LAN» и «config.json не менялся»).
  Это осознанно оставлено как есть — решение владельца репозитория; при желании вычищается
  отдельным коммитом.
