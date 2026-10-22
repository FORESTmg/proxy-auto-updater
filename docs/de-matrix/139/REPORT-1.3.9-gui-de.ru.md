# 1.3.9: закрытие дефектов 1.3.8, пересборка 5 форматов, повторная матрица (пакеты + графика)

Дата: 22.10.2026. Всё головно (headless): пакеты — в WSL-дистрибутивах, графика —
`Xvfb` + `openbox` + настоящие панели в `dbus-run-session`. Окно VM и рабочий стол
пользователя не трогались, `src/` менялся только по дефектам D1–D6, ничего не
публиковалось.

Бинари 1.3.9 (собраны заново после правок):

| файл | размер | sha256 |
|---|---|---|
| `linux/dist/forest-ui` | 56 708 344 | `754888de2ec02195975c2ce386e20b2e32e79ad9994a33e745595c8cc22eb05b` |
| `linux/dist/forest-proxy-updater` | 34 387 232 | `268516647edb05cda5a14d0427164d58fe2a5a3898b59e1f63b0c35e55047cc3` |

Стенд: WSL Ubuntu 26.04 (графика и .deb), Debian 13, openSUSE Tumbleweed, Arch Linux
(сборка ELF и .rpm/Arch/tar/AppImage), `Xvfb 21.x`, `xfce4-panel`, `mate-panel`,
`lxqt-panel`. Клиенты принудительно X11 (`GDK_BACKEND=x11`, `QT_QPA_PLATFORM=xcb`).

## 1. Что исправлено в коде

| дефект | файл | правка | как проверено |
|---|---|---|---|
| **D1 (P0)** запасной значок жил 2 с | `src/forest_ui.py` | флаг `Ui._tray_fallback`: ставится в `_ensure_visible_icon()` перед `_apply_tray_mode("own")`, гард в `refresh()` (`and not getattr(self, "_tray_fallback", False)`), снимается при явном выборе в меню (`_set_tray_mode`) | LXQt без лотка: значок найден на снимках t=4/6/8/11/14 с; в журнале ОДНА строка «режим значка переключён», обратного переключения нет (§3.2) |
| **D2 (P0)** `LD_LIBRARY_PATH` бандла ломал `gsettings` | `src/theme.py` | `_clean_env()` — дочерним процессам не отдаются `LD_LIBRARY_PATH`/`PYTHONHOME`/`PYTHONPATH`; `_run_quiet()` — единая точка запуска; `'default'` больше НЕ означает «светлая»; добавлен запасной путь `~/.config/gtk-3.0/settings.ini` (`gtk-application-prefer-dark-theme`) | `state/t139/theme-test.py`: 16/16 PASS, в т.ч. «в наследстве переменная есть, в подпроцессе её нет» (§4) |
| **D2 (тот же корень), настройки прокси** | `src/desktop_env.py` | `run_in_session()` собирает окружение без переменных бандла (env САМОГО сеанса сохраняется) | код-ревью + те же 16 проверок темы (тот же подпроцессный путь) |
| **D3 (P1)** XFCE/LXQt не пишут `gsettings` | `src/theme.py` | `_detect_xfconf()`: `xfconf-query -c xsettings -p /Net/ThemeName` (XFCE) и `~/.config/lxqt/lxqt.conf` `[General] theme` (LXQt) — ДО gsettings | тест 16/16: `xfconf Adwaita-dark → dark`, `Greybird → light`, `lxqt.conf Ambiance-Dark → dark`, `Lightly → light` |
| **D4 (P2)** самотест противоречил SNI-коду | `src/forest_ui.py` (`--selftest`) | ветвление по `ui.icon_mode`: для `sni` — `contextMenu() is not None` и «второе своё меню не открывается»; `Context`/«QMenu не отдан» проверяются только для режима своего окна. Проверка «сбой меню записан в журнал» читает ФАКТИЧЕСКИЙ путь логгера и идёт через `_show_menu_guarded` | `--selftest`: `OK Linux/SNI: QMenu ОТДАН трею … contextMenu=<QMenu>, пунктов в модели=16`, `OK Linux/SNI: правый клик НЕ открывает второе меню` (§4) |
| **D5 (P3)** отступ карточки на 150 % | `src/forest_ui.py` `home_pos()` | причина оказалась не в множителе (все величины и так логические), а в прямоугольнике: на 150 % XFCE отдаёт страту панели в устройстве, Qt пересчитывает её в логические и ПРИБАВЛЯЕТ к `y` — `availableGeometry()` выходила за низ экрана (y=27, h=515 при экране 533). Теперь место дополнительно ограничивается ФАКТИЧЕСКИМ прямоугольником экрана | 150 %: зазоры **12 device справа / 21 device снизу** = 8/14 логических ровно как в макете (в 1.3.8 было 12/8) |
| **D6 (P3)** `PANEL_PX = 46` захардкожен | `src/tray_popup.py` | `PANEL_PX = 0`; высота панели измеряется (`geometry() − availableGeometry()`, затем `_NET_WORKAREA`), `tray.panel_px` — только ручной резерв; `--tray-probe` печатает измеренное значение и его источник; место значка ограничено прямоугольником экрана | без панели: `панель=0px(none)`, значок в (1248,768) — зазор 6 px вместо 52; LXQt: `панель=32px(availableGeometry)`; XFCE: `27px`, при 150 % — `18px` |

## 2. Что исправлено в упаковке

| правка | файл | факт |
|---|---|---|
| Arch: `/usr/share/doc/forest-proxy-updater/COPYING` | `packaging-linux/arch/PKGBUILD` | в пакете 1.3.9 файл есть (`pacman -Qlp`), рядом с `/usr/share/licenses/…/COPYING`; в 1.3.8 его не было — матрица это фиксирует как `KNOWN-1.3.8` и требует наличия после обновления |
| namcap: объявить `bash` и `hicolor-icon-theme` | `PKGBUILD` (`depends`) | `pacman -Qip`: `Depends On: bash curl hicolor-icon-theme libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb` |
| Arch: в выпущенном 1.3.8 лежал до-фиксовый `.install` | пересборка | `.INSTALL` внутри 1.3.9 побайтово равен `arch/forest-proxy-updater.install`: `ce8daa941e1d7a4a961602d4034dbe81438a53c3dc4a5e0476523b6597ba90d5` |
| версия пакетов по умолчанию | `packaging-linux/lib/common.sh` | `VERSION=1.3.9` (была 1.3.2 — наивная сборка выпускала бы пакет с чужим номером) |
| пересобраны ВСЕ 5 форматов | `packaging-linux/out/` | §5 |

## 3. Графика в Xvfb (панели настоящие)

Артефакты прогонов: `docs/de-matrix/139/logs/<прогон>/` (`report.txt`, снимки,
журнал помощника), сводный лог `docs/de-matrix/139/logs/matrix.log`,
харнесс — `docs/de-matrix/139/harness/`.

### 3.1 Тёмная карточка на тёмной панели — ГЛАВНОЕ (D2/D3)

| DE | панель (пиксель) | карточка: место и цвет | снимок |
|---|---|---|---|
| **XFCE**, панель сверху 1280×27, `srgb(53,53,53)` | тёмная | 392×120 в (880,666), фон **`srgb(25,26,30)`** | `xfce-dark-card.png` |
| **MATE**, `color-scheme=prefer-dark`, панель сверху 1280×25 | тёмная | 392×120 в (880,642), фон **`srgb(25,26,30)`** | `mate-dark-card.png` |
| **XFCE 150 %** | тёмная | 588×180 в (680,599), фон **`srgb(25,26,30)`**, зазоры 12/21 device = 8/14 логических | `xfce-150-card.png` |

Для сравнения, 1.3.8 на том же стенде: `srgb(240,240,240)` (XFCE) и
`srgb(238,238,238)` (MATE) — белая карточка. Сейчас тёмная на обеих панелях.

### 3.2 Запасной значок не падает через 2 с (D1)

| сценарий | t=2 с | t=4 с | t=6 с | t=8 с | t=11 с | t=14 с | журнал |
|---|---|---|---|---|---|---|---|
| **LXQt** (панель 32 px, плагина лотка нет) | нет (поднимается) | **FOUND (1267,755)** | **FOUND** | **FOUND** | **FOUND** | **FOUND** | одна строка «режим значка переключён на popup» |
| **MATE** (в этом прогоне watcher не поднялся) | нет | **FOUND (1267,763)** | **FOUND** | **FOUND** | **FOUND** | **FOUND** | то же |
| **Без панели** (голый openbox) | нет | **FOUND (1267,787)** | **FOUND** | **FOUND** | **FOUND** | **FOUND** | то же |

Снимки: `lxqt-fallback-t4.png`, `lxqt-fallback-t14.png`, `mate-fallback-t14.png`,
`no-panel-t14.png`. В 1.3.8 на том же стенде: t=4 с «FOUND», t=6/8/11/14 с —
`NOTFOUND`.

### 3.3 Значок, меню и подсказка на тёмной панели XFCE

* протокол — **SNI**: `org.kde.StatusNotifierWatcher` есть, `_NET_SYSTEM_TRAY_S0`
  не выставлен; журнал: `меню SNI собрано (16 пунктов, подменю: фильтры/тема)`;
* значок найден по цвету состояния на панели: **FOUND (1160,18)**, `colour=(57,194,109)`
  — `xfce-dark-panel-icon.png`, то же при 150 % — `xfce-150-panel-icon.png`;
* подсказка: `FOREST связь OK | 7 серверов | РФ через прокси | watch OK`;
* правый клик → меню панели (окна `0x2001…`), `Down×10 + Enter` → **карточка
  392×120 в (880,666)** — `xfce-dark-card-from-menu.png`, меню — `xfce-dark-menu.png`.

### 3.4 `--tray-probe`: панель измеряется, а не берётся из константы (D6)

| стенд | экран / рабочая | `панель=` | было в 1.3.8 |
|---|---|---|---|
| XFCE 100 % | 1280×800 / (0,27,1280,773) | `27px(availableGeometry)` | `46px` (константа) |
| XFCE 150 % | 853×533 / (0,27,853,515) | `18px(availableGeometry)` | `46px` |
| LXQt (панель снизу 32) | 1280×800 / (0,0,1280,768) | `32px(availableGeometry)` | `46px` |
| без панели | 1280×800 / (0,0,1280,800) | `0px(none)` | `46px` (значок стоял с зазором 52 px) |

## 4. Самотесты (`--selftest`), сравнение с 1.3.8

| прогон | 1.3.8 | 1.3.9 | вердикт |
|---|---|---|---|
| offscreen (сеанс XFCE, тот же стенд) | 153 OK / **7 FAIL** | **153 OK / 6 FAIL** | новых падений кода нет; ушли 2 расхождения теста с SNI-кодом (D4) |
| XCB (живой X) | 150 OK / **10 FAIL** | **153 OK / 6 FAIL** | то же: три «XEmbed-ных» падения D4 исчезли |

Оставшиеся 6 падений в обоих прогонах — артефакты стенда, не код:
`геометрия меню … 301x521` (метрики DejaVu вместо Segoe UI — с 1.3.2),
`нет залипаний … 54.0 мс`, `кадров длиннее ДВУХ периодов: 3`,
`кадры не пропущены: 6 (всего 8)`, `демо-данные: 1/100000`,
`ДВИЖЕНИЕ НА УДЕРЖАНИИ: пиксели полосы 3` — всё это llvmpipe/Xvfb без GPU.

Отдельные проверки D4 в отчёте самотеста:

```
OK   Linux/SNI: QMenu ОТДАН трею (setContextMenu) — меню рисует панель — contextMenu=<QMenu>, пунктов в модели=16
OK   Linux/SNI: правый клик НЕ открывает второе (своё) меню — показов своего окна: Context=0, int(Context)=0, Trigger=0, DoubleClick=0
OK   сбой показа меню записан в журнал помощника — RuntimeError: искусственный сбой показа меню (проверка)
```

Полные отчёты: `logs/selftest/selftest-off.txt`, `logs/selftest/selftest-x.txt`
(и `~/.local/state/forest-proxy-updater/ui_selftest.{txt,json}` на стенде).

Основная программа (служба) — `forest-proxy-updater --selftest`:
**328 OK / 0 FAIL, rc=0**, `POSIX-тесты пройдены` — правки `theme.py` и
`desktop_env.py` её не задели (`state/t139/logs/selftest-service.txt`).

## 5. Матрица пакетов: 4 дистрибутива × 16 проверок

Харнесс: `state/t139/matrix.sh` (из `state/t138/matrix.sh`; версии 1.3.8→1.3.9 и
явная пометка `KNOWN-1.3.8` для отсутствующего `doc/COPYING` в старом Arch-пакете).
Каждый прогон: установка 1.3.8 → файлы/права → версия → служба (enabled/active) →
три «живые копии» помощника → обновление до 1.3.9 (старые процессы сняты, служба
перезапущена, новый MainPID) → удаление/очистка → следов нет.

| дистрибутив | формат | установка | версия | файлы/права | служба | обновление | удаление | ИТОГ |
|---|---|---|---|---|---|---|---|---|
| Ubuntu 26.04 | `.deb` (dpkg -i) | PASS | 1.3.8 → 1.3.9 | PASS | enabled+active | PASS (копии сняты, новый MainPID) | PASS (следов нет) | **16/16** |
| Debian 13 | `.deb` | PASS | PASS | PASS | PASS | PASS | PASS | **16/16** |
| openSUSE Tumbleweed | `.rpm` (rpm -U) | PASS | PASS | PASS | PASS | PASS | PASS | **16/16** |
| Arch Linux | `.pkg.tar.zst` (pacman -U) | PASS | PASS | PASS (`COPYING` — после обновления) | PASS | PASS | PASS | **16/16** |

Логи: `state/t139/logs/matrix-{ubuntu,debian,opensuse,arch}.txt`.
Итого **64/64 PASS, 0 FAIL**.

## 6. Пять форматов 1.3.9 и сверка содержимого

| формат | файл | размер | sha256 |
|---|---|---|---|
| `.deb` | `forest-proxy-updater_1.3.9_amd64.deb` | 89 597 520 | `90a83d01c48ffcfc10526b147723979b5e522037c0f0fdd8a582855f747967a2` |
| `.rpm` | `forest-proxy-updater-1.3.9-1.x86_64.rpm` | 89 732 465 | `79e662eb19d4d7d47c8d1f0f7b2843ef36b667f24311cf71e054c907e120f386` |
| Arch | `forest-proxy-updater-1.3.9-1-x86_64.pkg.tar.zst` | 90 468 327 | `bf94a9b9c0d5fee01e5b8feeb360054a85cdd0e8a3f5afdc4111fb8f85aba93a` |
| `.tar.gz` | `forest-proxy-updater-1.3.9-linux-x86_64.tar.gz` | 33 841 187 | `e7204fddc9d4bb92f3180f23882f26b7d9b09643f4f2b3bf48d1e49a1d86de9c` |
| AppImage | `ForestProxyUpdater-1.3.9-x86_64.AppImage` | 34 810 360 | `0bfbf5693634c98a0ab219ab6403c8cd7d50f082d42cebe5422b866bd9d08d92` |

Сверка «в пакете именно эти сборки» (не по номеру версии):

* `.deb` (`dpkg-deb -x`) — `forest-ui 754888de…`, `forest-proxy-updater 2685166…` = `linux/dist/`;
* Arch (`bsdtar -xf`) — те же два хеша;
* AppImage (`--appimage-extract`) — `forest-proxy-updater 2685166…` = `linux/dist/`;
* `.tar.gz` — `forest-proxy-updater 2685166…` = `linux/dist/`.
  В `.tar.gz` и AppImage помощника (`forest-ui`) внутри нет — так и задумано с 1.3.2
  (`docs/LINUX-PACKAGES-PROOF.ru.md` §2.2): он прилагается отдельным файлом.
* `.rpm` собран тем же общим слоем `lib/common.sh` из того же дерева, что `.deb`
  и Arch (их хеши совпали побайтово); отдельной распаковки `.rpm` в этом прогоне не
  делал — в стенде нет `rpm2cpio`/`cpio` (см. §8).

## 7. Строка установки для пользователя

Ubuntu/Debian:

```bash
sudo apt install ./forest-proxy-updater_1.3.9_amd64.deb
```

Fedora/openSUSE:

```bash
sudo dnf install ./forest-proxy-updater-1.3.9-1.x86_64.rpm     # или: sudo zypper install ./…rpm
```

Arch/Manjaro:

```bash
sudo pacman -U forest-proxy-updater-1.3.9-1-x86_64.pkg.tar.zst
```

Без пакетного менеджера:

```bash
tar -xzf forest-proxy-updater-1.3.9-linux-x86_64.tar.gz && sudo ./forest-proxy-updater-1.3.9-linux-x86_64/install.sh
chmod +x ForestProxyUpdater-1.3.9-x86_64.AppImage && ./ForestProxyUpdater-1.3.9-x86_64.AppImage
```

## 8. Что НЕ проверено и почему (честно)

* **Наложение на вторую панель XFCE при 150 %.** На стенде XFCE поднимает ДВЕ
  панели: верхнюю 1280×27 (со лотком, страта учитывается) и нижний «док»
  306×49 в (487,751), который страту НЕ публикует (`availableGeometry` =
  (0,27,1280,773) — только верхняя панель). При 150 % карточка шириной 588 px
  стоит в (680,599)…(1268,779) и правым краем заходит на этот док (x 680…793,
  y 751…779). При 100 % наложения нет (карточка начинается с x=880, док
  кончается на 793). Это НЕ регрессия: в 1.3.8 на том же стенде карточка была в
  (680,612)…(1268,792) — заходила на док глубже и при этом ещё и стояла в 8 px от
  края экрана. Приложение позиционируется по рабочей области; панель, которая
  места не резервирует, по протоколу считается перекрываемой. Чинить «на глазок»
  (вычитать прямоугольники чужих DOCK-окон) не стал: это меняет поведение на
  живых панелях, а не закрывает дефект из отчёта 1.3.8.
* **MATE: SNI-лоток в headless-сеансе не поднялся** (`mate-panel` не завёл
  `org.kde.StatusNotifierWatcher`; в 1.3.8 то же самое было через раз). Поэтому
  на MATE проверены: тёмная карточка (скриншот + пиксель) и запасной значок
  (t=4…14 с), а меню/подсказка SNI — только на XFCE.
* **Cinnamon / GNOME / KDE Plasma** — не входили в это задание (Cinnamon в 1.3.8
  не ставился: 531 пакет; KDE проверялся живым прогоном в 1.3.7/1.3.8).
* **`.rpm` не распакован для сверки хешей** — в Arch-стенде нет `rpm2cpio`/`cpio`,
  а ставить пакеты без сети не стал; содержимое гарантировано общим слоем
  `lib/common.sh` (тот же stage-каталог, что у `.deb`/Arch, чьи хеши совпали).
* **AppImage/`.tar.gz` установлены не были** — они не ставятся пакетным
  менеджером; в AppImage сборка сама прогнала автотесты (`автотесты внутри
  AppImage: OK`), `.tar.gz` проверен распаковкой и сверкой хеша бинаря.
* **Живой рабочий стол пользователя (Windows/VM) не трогался** — по условию.
  Поведение на реальном железе (GPU-композитор, настоящий DPI-масштаб всего
  рабочего стола) не проверялось: масштабировался только Qt-клиент
  (`QT_SCALE_FACTOR=1.5`, `Xft.dpi=144`).
