# packaging-linux — сборка Linux-пакетов

**English summary.** Ready-made builders for the Linux release: `build-deb.sh`
(`.deb`), `build-rpm.sh` (`.rpm`), `build-arch.sh` (Arch `.pkg.tar.zst` via
`makepkg` + a real `PKGBUILD`), `build-appimage.sh` (universal AppImage) and
`build-tar.sh` (portable `.tar.gz` with `install.sh`/`uninstall.sh`). All of them
stage **one and the same file tree** (`lib/common.sh`), so the contents of the
five formats cannot drift apart. Artifacts land in `out/` (not in git, see
`.gitignore`), work directories in `work/` (or a local `$TMPDIR` path when the
repo sits on a Windows drive, because Windows filesystems do not keep file
modes). The user-facing instructions are [`docs/LINUX-INSTALL.ru.md`](../docs/LINUX-INSTALL.ru.md)
and [`docs/LINUX-INSTALL.md`](../docs/LINUX-INSTALL.md); the package matrix bench
is [`linux/tests/`](../linux/tests/README.md).

---

## Что здесь лежит

```
packaging-linux/
├── lib/common.sh              одна раскладка файлов и одни пути для ВСЕХ форматов
├── build-deb.sh               .deb  (dpkg-deb) — Debian, Ubuntu, Mint
├── build-rpm.sh               .rpm  (rpmbuild) — Fedora, RHEL, Rocky, openSUSE
├── build-arch.sh              Arch  (makepkg)  — Arch, Manjaro, Endeavourous
├── build-appimage.sh          AppImage (appimagetool + mksquashfs)
├── build-tar.sh               портативный .tar.gz (install.sh / uninstall.sh)
├── build-all.sh               всё, что возможно на этой машине, + сводка
├── system/                    юниты systemd для СИСТЕМНОЙ службы (шаблоны)
├── deb/                       control + postinst/prerm/postrm для .deb
├── rpm/                       spec для rpmbuild
├── arch/                      PKGBUILD + install-скрипт pacman
├── appimage/                  AppRun + .desktop
├── tar/                       install.sh / uninstall.sh для архива
├── scripts/make_icon_linux.py иконка PNG без внешних библиотек
├── doc/                       copyright, PACKAGE-README.md (кладётся в пакеты)
├── out/                       ГОТОВЫЕ ФАЙЛЫ  (в git не попадает)
└── work/                      рабочие каталоги сборки (в git не попадает)
```

## Сборка

```sh
sh packaging-linux/build-all.sh            # все форматы, возможные здесь
sh packaging-linux/build-deb.sh            # только .deb
sh packaging-linux/build-rpm.sh            # только .rpm
sh packaging-linux/build-arch.sh           # только Arch .pkg.tar.zst
sh packaging-linux/build-appimage.sh       # только AppImage
sh packaging-linux/build-tar.sh            # только .tar.gz
```

Версия задаётся переменной окружения: `FOREST_PKG_VERSION=1.0.2 sh build-deb.sh`
(по умолчанию — та, что в `lib/common.sh`, сейчас `1.3.1`; номер выпуска пакета —
`FOREST_PKG_RELEASE`). Каталоги вывода/сборки переопределяются `FOREST_OUT_DIR` и
`FOREST_WORK_DIR`.

Что нужно для каждого формата:

| Формат | Инструменты | Замечания |
| --- | --- | --- |
| `.deb` | `dpkg-deb` | есть в Debian/Ubuntu из коробки |
| `.rpm` | `rpmbuild` (+ `rpm`) | openSUSE: `zypper install rpm-build`; Fedora: `dnf install rpm-build`. В CIQ-образе Rocky `rpmbuild` не ставится — собирайте в openSUSE |
| Arch | `makepkg` + `fakeroot` | `pacman -S --needed fakeroot`; makepkg **отказывается** работать от root — скрипт сам понижает права (см. `FOREST_MAKEPKG_USER`) |
| AppImage | `mksquashfs`, `python3`, `curl` | `appimagetool` скачается сам (~15 МБ) либо `FOREST_APPIMAGETOOL=/путь`; FUSE не нужен — всё через `--appimage-extract-and-run` |
| `.tar.gz` | `tar`, `gzip` | ничего больше |

Входы: `linux/dist/forest-proxy-updater` (собирается `packaging/build-linux.sh`),
шаблоны `system/`, `docs/LINUX-INSTALL*.md`, `config.example.json`, `LICENSE`,
значок `icons/forest-proxy-updater.png` и — если собран — помощник интерфейса
`linux/dist/forest-ui` (сборка: `sh packaging/build-ui.sh`).

В каждый пакет кладутся **значок** (`/usr/share/icons/hicolor/256x256/apps/`) и
**.desktop**-файлы: `forest-proxy-updater.desktop` (сама программа) и, если
помощник собран, `forest-proxy-updater-ui.desktop` (значок в трее, меню,
уведомления). Без помощника пакет собирается так же — с предупреждением.

## Что попадает в каждый пакет

Раскладка задана один раз — в `lib/common.sh::stage_payload`:

| Что | Куда |
| --- | --- |
| Программа (ELF; Python, ядро `xray` и гео-базы внутри) | `/usr/lib/forest-proxy-updater/forest-proxy-updater` |
| Помощник интерфейса (Qt; если собран) | `/usr/lib/forest-proxy-updater/forest-ui` (+ симлинк `/usr/bin/forest-ui`) |
| Команда в PATH | `/usr/bin/forest-proxy-updater` (симлинк) |
| Юниты systemd (служба + плановый прогон + таймер) | `/usr/lib/systemd/system/forest-proxy-updater*.{service,timer}` |
| Конфиг по умолчанию | `/etc/forest-proxy-updater/config.json` (`conffile` в .deb, `%config(noreplace)` в .rpm) |
| Документация (README, INSTALL.ru/en, TERMS.ru/en, copyright, COPYING) | `/usr/share/doc/forest-proxy-updater/` |
| Лицензия GPL-3.0 | `/usr/share/licenses/forest-proxy-updater/COPYING` |

Состояние (`/var/lib/forest-proxy-updater`) создаётся при установке и удаляется
при удалении пакета. Ядро и гео-базы НЕ кладутся отдельными файлами: они уже
внутри ELF-бинарника, поэтому пакет не требует ни скачивания при установке, ни
интернета.

## Системная служба, а не пользовательские юниты

Выбран **один** вариант, и он применяется во всех форматах: пакет ставит
**системную** службу `forest-proxy-updater.service` (`multi-user.target`).

* autostart работает без входа в систему и **без** `loginctl enable-linger`;
* не нужен живой `systemd --user` (в контейнерах и WSL он регулярно недоступен);
* одинаковая настройка на сервере, в контейнере и на рабочей станции;
* `KillMode=control-group` снимает и ядро `xray` при остановке службы — сирот с
  занятым портом не остаётся;
* пути передаются юниту явно (`FOREST_ROOT`, `FOREST_CONFIG`, `XDG_STATE_HOME`),
  поэтому служба не зависит от `$HOME`.

Пользовательские юниты (`systemctl --user`) остались вторым, «домашним»
вариантом — им пользуются AppImage (`--install`) и `install.sh --user`; там
`enable-linger` включается автоматически. Для ровно тех же путей и юнитов
используются шаблоны `system/` — они общие с пакетами.

`forest-proxy-updater.timer` ставится, но **не** включается: супервизор
(`--run`) обновляет пул сам (`cycle.full_refresh_hours`). Таймер — для тех, кто
хочет отдельный расписанный прогон `--once`:
`sudo systemctl enable --now forest-proxy-updater.timer`.

## Удаление

| Формат | Как удаляются служба и файлы |
| --- | --- |
| `.deb` | `prerm`: `disable --now`; `postrm`: `daemon-reload`, `rm -rf /var/lib/forest-proxy-updater`; `purge` снимает и конфиг |
| `.rpm` | `%preun`: `disable --now`; `%postun`: `daemon-reload` + удаление каталога состояния |
| Arch | install-скрипт: `pre_remove` / `post_remove` |
| AppImage | `--uninstall` (юниты, ядро, каталоги данных/состояния/конфига) |
| `.tar.gz` | `uninstall.sh` (идемпотентный, те же пути) |

Конфиг `/etc/forest-proxy-updater/config.json` при обычном удалении .deb
сохраняется (решение dpkg), при `purge` — удаляется; `uninstall.sh` удаляет его
вместе со всем, но понимает `--keep-config`.

## Проверка

Собранные пакеты прогоняются стендом `linux/tests/wsl-matrix.sh` в реальных
дистрибутивах: установка → служба активна → **реальный трафик** (`curl -x
http://127.0.0.1:11808 … generate_204` → `204`) → удаление и проверка, что не
осталось ни файлов, ни юнитов, ни занятого порта. Подробности и матрица —
`linux/tests/README.md`, результаты прогонов — `docs/LINUX-PACKAGES-PROOF.ru.md`.

```sh
sh linux/tests/wsl-matrix.sh --distro Debian   --package packaging-linux/out/forest-proxy-updater_1.3.1_amd64.deb
sh linux/tests/wsl-matrix.sh --distro openSUSE-Tumbleweed --package packaging-linux/out/forest-proxy-updater-1.3.1-1.x86_64.rpm
sh linux/tests/wsl-matrix.sh --distro archlinux --package packaging-linux/out/forest-proxy-updater-1.3.1-1-x86_64.pkg.tar.zst
sh linux/tests/wsl-matrix.sh --distro Ubuntu --package packaging-linux/out/ForestProxyUpdater-1.3.1-x86_64.AppImage --as-user tester
sh linux/tests/wsl-matrix.sh --distro Ubuntu --package packaging-linux/out/forest-proxy-updater-1.3.1-linux-x86_64.tar.gz
```

## Грабли, уже учтённые в сборщиках

* **Windows-диск в WSL не хранит права** (`/mnt/d` — всё `0777`). Пакет,
  собранный прямо там, положил бы конфиг доступным на запись всем.
  `lib/common.sh::ensure_dirs` проверяет это и уводит сборку в локальный `$TMPDIR`.
* **`makepkg` от root не работает** — `build-arch.sh` сам понижает права до
  непривилегированного пользователя (`FOREST_MAKEPKG_USER`, иначе первый uid ≥ 1000).
* **`rpmbuild` полосует бинарники** и собирает debuginfo — для PyInstaller-onefile
  это лишнее и рискованное, поэтому в spec отключена пост-обработка
  (`__os_install_post`).
* **`%` в комментариях spec** раскрывается как макрос rpm (однажды превратило
  комментарий в мусорные строки) — в комментариях макросы не пишутся.
* **`appimagetool`** — сам AppImage: в WSL FUSE нет, поэтому и сборка, и проверка
  идут с `--appimage-extract-and-run`; после сборки автотесты продукта запускаются
  прямо из AppImage (`--selftest`).
* **`pacman 7`** убрал `-Qp --format` и требует `-p/--file` для `-Ql` — в
  `build-arch.sh` метаданные читаются через `pacman -Qip/-Qlp`.
