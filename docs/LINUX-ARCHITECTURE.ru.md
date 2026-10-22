# FOREST's Proxy Auto-updater — архитектура Linux-версии

Документ объясняет, как один и тот же продукт работает на Windows и Linux, что
именно различается по платформам и почему выбраны такие решения.

## 1. Главный принцип: общая сердцевина + тонкий платформенный слой

`src/clientwrite.py` (1951 строка) устроен так, что **всё ценное уже
переносимо**:

| Переносимая часть | Где |
| --- | --- |
| Проверка связи и скорости через `curl` (204 + реальные данные + TLS) | `_curl_metrics`, `_curl_speed`, `port_works`, `probe_client`, `measure_speed_kbps`, `youtube_probe` |
| Чёрный список узлов с TTL | `bl_add`, `bl_add_pool`, `bl_active`, `bl_filter` |
| Стор серверов (JSON) и `[AUTO]`-группа | `_store_load/_save`, `server_to_row`, `row_to_server`, `_sa_*` |
| Генерация конфигов ядра (single + balancer, торрент-вход) | `build_single_config`, `build_balancer_config`, `_build_routing`, `_build_dns` |
| **Проверка нового конфига ядром ДО остановки рабочего ядра** | `_test_config_by_core`, `_validate_new_config`, `_validate_config`, `_conf_sha`, `_cfg_sha` |
| **Замер разрыва связи в миллисекундах** при переключении | `_restart_core_timed` |
| Пул балансировщика, заморозка пула, «бесшовность» | `balancer_pool`, `_pool_sig`, `_link_ok`, `ensure_balancer` |
| Активация, гвард, автостраховка | `activate`, `guard_cycle`, `_emergency_restore`, `save_lastgood`, `load_lastgood` |
| Стор состояния + hotcache | `StateStore`, `update_hotcache`, `hotcache_switch` |
| Файловые блокировки «один управляющий ядром» | `_lock`, `_unlock` |

ОС-зависимых мест ровно два: **управление процессами** и **системный прокси**.

## 2. Как вживляется POSIX-реализация

`src/clientwrite_posix.py` не переписывает логику. Он:

1. импортирует `clientwrite` (на Linux модуль импортируется: все вызовы WinAPI
   лежат внутри функций, а не на уровне модуля);
2. подменяет ОС-зависимые примитивы **в самом модуле** —
   `clientwrite._kill_pid = _posix_kill_pid` и т. п.;
3. копирует к себе оставшиеся имена (включая «подчёркнутые», которые
   `import *` не переносит) — уже **после** подмены.

Почему подмена в модуле, а не просто переопределение функций в своём файле:
функции `clientwrite` ищут зависимости в **своём** модульном словаре. Поэтому

```
stop_core -> _pid_alive / _kill_pid
ensure_balancer -> _restart_core_timed -> stop_core / start_core
restore_system_proxy -> _sys_proxy_disable
ensure_system_proxy -> _sys_proxy_state / _sys_proxy_apply
guard_cycle / activate -> wait_and_probe -> probe_client
```

идут в POSIX-реализацию без единой правки в вызывающем коде. Windows-файл при
этом не меняется ни на строку.

### Что подменяется

| Функция | Windows | POSIX |
| --- | --- | --- |
| `_pid_alive` | `OpenProcess` + `WaitForSingleObject` | `os.kill(pid, 0)` + состояние из `/proc/<pid>/stat` (zombie = мёртв) |
| `_port_pid` | `GetExtendedTcpTable`, затем `netstat` | `/proc/net/tcp` → inode → `/proc/*/fd`, затем `ss -p`, затем `lsof` |
| `_our_core_pids` | PowerShell CIM по `xray.exe` | обход `/proc`: имя процесса `xray` + наш каталог в `cmdline` |
| `_kill_pid` | `TerminateProcess`, затем `taskkill` | `SIGTERM` (до 1.5 с) → `SIGKILL` → `waitpid(pid, WNOHANG)` (забрать zombie) |
| `ensure_port_free` | владелец порта из таблицы TCP | владелец порта из `/proc`, «наше ядро» — по pid-файлу |
| `_sys_proxy_state/_apply/_disable` | реестр `HKCU\...\Internet Settings` + `InternetSetOptionW` | `gsettings org.gnome.system.proxy`, при отсутствии — переменные окружения и мягкий отказ |
| `geo_asset_dir` | рядом с `xray.exe` | рядом с `xray`, затем каталог программы, затем `/usr/share/xray` и др. |
| `_mode_of` | standalone / v2rayn | только standalone (v2rayN — приложение Windows) |

### Что НЕ подменяется (и почему)

`start_core`, `restart_core`, `_restart_core_timed`, `wait_and_probe`,
`_validate_config`, `_test_config_by_core`, `_curl_*` уже работают на POSIX:
ядро запускается как дочерний процесс через `subprocess`, а
`creationflags=_no_window()` возвращает `getattr(subprocess, "CREATE_NO_WINDOW", 0)`
= `0`, что на POSIX допустимо (ошибка возникает только при ненулевом значении).
Благодаря этому Linux-версия **бесплатно** получает всю «бесшовность»:

* новый конфиг проверяется ядром (`xray run -test -config`) **до** снятия
  работающего ядра — при ошибке старое ядро не трогается;
* `_restart_core_timed` измеряет **разрыв связи в миллисекундах** (от снятия
  старого ядра до приёма порта новым) и пишет его в лог и в состояние;
* при неизменном пуле (`_pool_sig` в состоянии, `pool_freeze_s`) перезапуска нет;
* при пустом/мёртвом пуле поднимается последний рабочий состав
  (`save_lastgood`/`load_lastgood`) и есть резерв по RTT.

## 3. Жизненный цикл ядра на Linux

Ядро — **дочерний процесс** супервизора (`subprocess.Popen([core, "run", "-c",
cfg])`), а не отдельный systemd-юнит. Это сохранено специально:

* не нужен второй юнит и «драка» за порт между юнитом и программой;
* разрыв связи измеряется по факту приёма порта, а не по факту перезапуска юнита;
* `KillMode=control-group` в `forest-proxy-updater.service` гарантирует, что при
  остановке службы ядро снимается и сирот с занятым портом не остаётся.

systemd управляет **программой**: `forest-proxy-updater.service` (`--run`) и
таймер планового цикла (`--once`).

## 4. Пути (XDG)

| Роль | Путь |
| --- | --- |
| данные (бинарь, `bin/xray/xray`, гео-базы) | `$XDG_DATA_HOME/forest-proxy-updater` |
| конфиг | `$XDG_CONFIG_HOME/forest-proxy-updater/config.json` |
| состояние (стор, кэши, `tray_status.json`, блокировки) | `$XDG_STATE_HOME/forest-proxy-updater` |
| логи | `$XDG_STATE_HOME/forest-proxy-updater/logs/main.log` |
| юниты | `$XDG_CONFIG_HOME/systemd/user/forest-proxy-updater*.{service,timer}` |

Порядок поиска ядра: явный `client.core` → `bin/xray/xray` рядом с программой →
каталог программы → каталог данных → `/usr/local/bin`, `/usr/bin`, `/opt/xray` →
`PATH`. Если файл найден, но бит исполняемости потерян (бывает при распаковке
onefile), он возвращается — иначе ядро просто не запустилось бы.

## 5. Индикация вместо трея

`tray.py` — WinAPI (`ctypes.windll`), на Linux не работает. Замена:

* **файл состояния** `state/tray_status.json` — его пишет общий модуль
  `tray_status.py` (он переносим), поэтому формат тот же, что на Windows;
  читают `--status` и внешние скрипты;
* **`notify-send`** — уведомления о потере/восстановлении связи; вызывается
  только если утилита есть в `PATH`. Нет её — событие остаётся в логе
  (мягкая деградация), программа работает штатно;
* поток `indicators_loop` в супервизоре держит «пульс» файла состояния и следит
  за сменой состояния связи;
* **значок в области уведомлений** рисует отдельный помощник `forest-ui` (Qt).
  Значок, подсказка и уведомления — общие с Windows; отличается поведение
  кликов, и это ограничение ПРОТОКОЛА, а не оформления:

| Клик | Windows | Linux SNI (KDE Plasma, XFCE, MATE, Cinnamon, LXQt с лотком) | Свой значок-окно (`ui.tray_mode = "own"`, запасной режим) |
| --- | --- | --- | --- |
| правый | наше анимированное меню-окно | **наше** `QMenu` (его рисует панель: 11 пунктов + оба подменю) | **наше** анимированное меню-окно у значка |
| левый | ничего | ничего | ничего |
| клик мимо меню | закрывает | закрывает (панель) | закрывает прозрачный слой на весь экран |
| наведение на пункт с подменю | наше подменю с задержкой | системное подменю | наше подменю с задержкой |

Как это устроено:

* **SNI-панели** (`org.kde.StatusNotifierWatcher`) — это НЕ только KDE:
  проверено на живых XFCE 4.20 и MATE 1.27 — Qt 6.9 при наличии вотчера уходит
  в SNI и там же, поэтому «XEmbed всё сломает» к современным DE неприменимо.
  Правый клик по значку забирает ПАНЕЛЬ: она запрашивает у программы описание
  меню (`com.canonical.dbusmenu`) и рисует его сама. Поэтому в SNI помощник
  отдаёт трею полноценный `QMenu` (`setContextMenu`), собранный из ТОЙ ЖЕ модели,
  а своё окно меню в этом режиме не открывает (иначе было бы два меню);
* **свой значок-окно** (`ui.tray_mode = "own"`) — прежний путь: своё окно у края
  панели, правое наведение/анимация. Он же автоматически включается там, где
  лотка нет вовсе (GNOME без AppIndicator, часть сборок LXQt, голый WM): причина
  пишется в журнал и одной строкой `notify-send`, `ui.tray_mode` при этом НЕ
  меняется — это запасной режим, а не выбор пользователя.
* **XEmbed-панели** доставляют правый клик ПРОГРАММЕ (`ActivationReason.Context`),
  и раньше слот открывал своё окно `ui_menu.PopupMenu`. `QMenu` трею не
  отдавался, потому что тогда Qt показывал своё штатное меню;
* **StatusNotifier-панели** (KDE Plasma, XFCE 4.20, MATE 1.27, Cinnamon, LXQt с
  лотком) правый клик по значку забирают себе: они запрашивают у программы
  ОПИСАНИЕ меню (`com.canonical.dbusmenu`) и рисуют его сами. Поэтому в SNI
  помощник отдаёт трею полноценный `QMenu` — 11 пунктов, оба подменю
  («Фильтры»: две галочки, «Тема оформления»: три варианта), те же обработчики;
  галочки отражают реальное состояние и обновляются на каждом тике (2 с).

Меню открывается **только правой кнопкой** на всех платформах; левый клик не
открывает ничего — как в Windows.

**Выбор режима значка:** пункт «Значок в области уведомлений» (галочка) в меню.
Снятая галочка = свой значок-окно у края панели (`ui.tray_mode = "own"`), с
нашим меню, стилем и анимацией. Переключение применяется сразу: старый значок
снимается целиком, новый создаётся, второй значок и второй процесс не
появляются. Значение хранится в `config.json` → `ui.tray_mode` (`"sni"` по
умолчанию). Плазмоида в пакете больше нет — правый клик по виджету панели всегда
забирает панель, и никакой плазмоид этого не обойдёт.

## 6. Производительность

Что сделано для скорости (это же — горячие места, которые трогать при
дальнейшей оптимизации):

* `_pid_alive` — это `os.kill` + одно чтение `/proc/<pid>/stat`, без запуска
  процессов: вызывается в цикле сторожа каждые несколько секунд;
* `_port_pid` — сначала разбор `/proc/net/tcp` (без подпроцессов), `ss`/`lsof`
  только как резерв;
* `_our_core_pids` — обход `/proc` вместо PowerShell/CIM (~0.4 с на Windows);
* `_kill_pid` — `SIGTERM` первым: xray закрывает сокеты корректно и порт
  освобождается быстрее, чем при `SIGKILL`; иначе — `SIGKILL`;
* `_restart_core_timed` — ожидание по факту приёма порта (шаг 10–20 мс), без
  фиксированных пауз;
* `_validate_new_config` кэширует результат проверки по SHA конфига (час) и
  сначала пробует быстрый `xray run -test`, а запуск на свободных портах —
  только как фолбэк для старых сборок ядра;
* `ensure_balancer` не перезапускает ядро, если подпись пула не изменилась и
  связь жива (заморозка пула);
* в тестах узлов уже есть параллелизм (`ThreadPoolExecutor`), TCP-предфильтр
  отсеивает мёртвые адреса до запуска ядра;
* для сборки проверено: ELF собирается PyInstaller без UPX, запуск вне venv
  работает (`--selftest`).

## 7. Что осталось за рамками

* Системный прокси правится **по механизму своего рабочего стола**:
  GNOME-подобные — `gsettings org.gnome.system.proxy`, KDE Plasma —
  `kioslaverc` (`kwriteconfig6/5`, затем сигнал KIO о перечитывании), плюс
  `gsettings` для Chromium/Electron/GTK (они читают его и на KDE). Стол
  определяется явно (`XDG_CURRENT_DESKTOP`, `KDE_FULL_SESSION`, `DESKTOP_SESSION`,
  а при пустом окружении — по процессам `kwin_wayland`/`plasmashell`/
  `gnome-shell`), а не «есть gsettings → GNOME». Если сеанса пользователя нет
  (служба от root, контейнер, WSL) или стол без общего механизма (i3, sway) —
  одна честная строка в журнал и мягкая деградация, без попыток каждые
  30–60 с. Запись ВСЕГДА проверяется чтением: `gsettings` без сеансовой шины
  уходит в backend `memory` и возвращает успех, ничего не сохранив.
* `client.mode = "v2rayn"` на Linux неприменим (на Windows поведение прежнее).
* Значок на Linux рисует Qt-помощник: в SNI — штатный значок области
  уведомлений (KDE Plasma, XFCE, MATE, Cinnamon, LXQt с лотком); если лотка нет
  вовсе (GNOME без AppIndicator, часть сборок LXQt, голый WM), автоматически
  включается свой значок-окно у края панели — причина пишется в журнал и одной
  строкой `notify-send`. Файл состояния и `notify-send` работают в любом случае.

## 8. Пакеты: одна раскладка на пять форматов

Пакеты собираются в `packaging-linux/`. Раскладка файлов задана **один раз** —
в `packaging-linux/lib/common.sh::stage_payload`, поэтому `.deb`, `.rpm`, Arch,
AppImage и `.tar.gz` не могут разъехаться по содержимому.

| Формат | Сборщик | Инструмент |
| --- | --- | --- |
| `.deb` | `build-deb.sh` | `dpkg-deb` |
| `.rpm` | `build-rpm.sh` | `rpmbuild` (spec в `rpm/`) |
| `.pkg.tar.zst` | `build-arch.sh` | `makepkg` + `arch/PKGBUILD` с install-скриптом |
| AppImage | `build-appimage.sh` | `appimagetool` + `mksquashfs` (+ иконка из `scripts/make_icon_linux.py`) |
| `.tar.gz` | `build-tar.sh` | `tar` (внутри `install.sh` / `uninstall.sh`) |

Системная раскладка (её ставят пакеты и `install.sh`):

| Что | Куда |
| --- | --- |
| программа (ELF, ядро и гео-базы внутри) | `/usr/lib/forest-proxy-updater/forest-proxy-updater` |
| команда в PATH | `/usr/bin/forest-proxy-updater` (симлинк) |
| конфиг | `/etc/forest-proxy-updater/config.json` |
| состояние, кэши, сторожевые файлы, логи | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/` |
| юниты | `/usr/lib/systemd/system/forest-proxy-updater*.{service,timer}` |
| документация и лицензия | `/usr/share/doc/forest-proxy-updater/`, `/usr/share/licenses/.../COPYING` |

Важно: **ядро и гео-базы не кладутся отдельными файлами**. В onefile-ELF
`BASE_DIR` равен каталогу распаковки (`sys._MEIPASS`), поэтому `config.find_core`
находит `bin/xray/xray` внутри самого бинарника — пакету не нужен ни интернет, ни
`/usr/bin/xray`. Установка не требует ничего «докладывать вручную».

## 9. Системная служба, а не пользовательские юниты

Выбран один вариант для всех пакетов: **системная** служба
`/usr/lib/systemd/system/forest-proxy-updater.service` (`WantedBy=multi-user.target`),
включается самим пакетом (`systemctl enable --now` из `postinst` / `%post` /
`post_install`).

Почему так:

* не нужны ни вход пользователя, ни `loginctl enable-linger`, ни живой
  `systemd --user` (в контейнерах и WSL он регулярно недоступен — см.
  `posix_platform.user_manager_ok`);
* одинаково на сервере, в контейнере и на рабочей станции;
* `KillMode=control-group` снимает и дочернее ядро `xray`, поэтому сирот с
  занятым клиентским портом не остаётся;
* каталог состояния создаёт сам systemd (`StateDirectory=forest-proxy-updater`),
  а права не зависят от `$HOME`.

Ключевые строки юнита: `ExecStart=/usr/lib/forest-proxy-updater/forest-proxy-updater --run`,
`WorkingDirectory=/usr/lib/forest-proxy-updater`, явные
`FOREST_ROOT=/var/lib/forest-proxy-updater`, `FOREST_CONFIG=/etc/forest-proxy-updater/config.json`,
`XDG_STATE_HOME=/var/lib/forest-proxy-updater/state`, `Restart=always`,
`RestartSec=5`, `StartLimitIntervalSec=0`.

`forest-proxy-updater.timer` (+ `-refresh.service` с `--once`) ставится, но **не
включается**: супервизор обновляет пул сам (`cycle.full_refresh_hours`). Таймер —
дополнительная возможность для тех, кто хочет отдельный расписанный прогон.

Пользовательские юниты (`systemctl --user`) остались вторым, «домашним»
вариантом: их ставят AppImage (`--install`) и `install.sh --user`, там же
включается `loginctl enable-linger`.

## 10. Dev-режим: AppImage и .tar.gz без установки в систему

Оба формата — про запуск без root.

**AppImage.** Внутри AppDir лежит один и тот же самодостаточный ELF
(`usr/bin/forest-proxy-updater`) и `AppRun`, который просто передаёт аргументы
дальше — никакой отдельной логики в AppImage нет:

```sh
./ForestProxyUpdater-1.1.0-x86_64.AppImage --install    # установка для пользователя
./ForestProxyUpdater-1.1.0-x86_64.AppImage --run        # запуск без установки
./ForestProxyUpdater-1.1.0-x86_64.AppImage --selftest    # автотесты
```

`--install` — это штатный `posix_platform.install`: он копирует
`sys.executable` (то есть распакованный из AppImage ELF) в
`~/.local/share/forest-proxy-updater/`, пишет конфиг, юниты `systemd --user`,
делает `enable --now` и `enable-linger`. После этого AppImage не нужен.

FUSE в WSL и контейнерах обычно нет, поэтому и сборка, и запуск проверяются с
`--appimage-extract-and-run` (runtime распаковывается в `$TMPDIR` и запускается
оттуда) — это же рекомендуется пользователю в документации. Иконка и `.desktop`
существуют только потому, что этого требует спецификация AppImage: у продукта
нет графического интерфейса, `.desktop` объявляет `Terminal=true`.

**Портативный `.tar.gz`.** Внутри — тот же ELF, шаблоны юнитов `systemd/`,
`config.example.json`, `install.sh` и `uninstall.sh`. Скрипт установки
подставляет реальные пути в шаблоны (`sed` по `@EXEC@`, `@WORKDIR@`, `@ROOT@`,
`@CONFIG@`, `@STATE_HOME@`, `@DATA_HOME@`) — то есть использует ровно те же
юниты, что и пакеты, а не свою копию:

```sh
sudo ./install.sh            # системная установка (та же раскладка, что в пакетах)
./install.sh --user          # ~/.local + systemd --user, без sudo
sudo ./install.sh --dry-run  # только показать план
sudo ./install.sh --prefix /opt
sudo ./uninstall.sh          # идемпотентное удаление (--keep-config оставит конфиг)
```

## 11. Как это проверено

Пакеты прогоняются стендом `linux/tests/wsl-matrix.sh` внутри настоящих
дистрибутивов (Ubuntu, Debian, openSUSE Tumbleweed, Rocky 10, Arch Linux).
Стенд ставит пакет, включает службу, ждёт **реального трафика** через прокси
(`curl -x http://127.0.0.1:11808 … generate_204` → `204`), затем удаляет пакет и
проверяет, что не осталось ни файлов, ни юнитов, ни занятого порта.

Стенд научен трём вещам, важным именно для этого продукта:

* **свой порт.** Продукт по умолчанию слушает 10808 — этот порт принадлежит
  живому прокси владельца на хосте. Стенд копирует штатный конфиг продукта,
  правит в копии `client.socks_port` и `test.port_start` и подкладывает её юниту
  через drop-in `FOREST_CONFIG=/run/fp-matrix-<порт>/config.json` — сам пакет не
  меняется;
* **долгое ожидание.** Первый цикл продукта реально скачивает публичные списки и
  тестирует узлы (RTT + скорость), поэтому трафик ждётся минутами, а не
  секундами (`--traffic-wait`, по умолчанию 900 с);
* **пользовательский режим** (`--as-user`): AppImage ставится и снимается как
  обычный пользователь, проверяются юниты `systemctl --user` и linger.

Результаты прогонов — `docs/LINUX-PACKAGES-PROOF.ru.md`.

