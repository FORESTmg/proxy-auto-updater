# Установка FOREST's Proxy Auto-updater на Linux

Пошаговая инструкция: от чистой системы до работающего прокси. Без окон и
графических диалогов — только журнал.

## Ограничения проверки

**ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** Проверка пакета выполнялась в среде WSL (Linux
внутри Windows), а не на установленной системе на реальном железе. Меню и
уведомления в графическом интерфейсе протестированы логически, но НЕ проверены
на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в
репозитории.

**TESTING SCOPE (EN):** Package testing was performed in WSL (Linux on Windows),
not on a real installation on bare metal. The GUI menu and notifications are
tested logically but have NOT been verified on a live desktop — issues are
possible. If something does not work, please report it in the repository.

**ВИД МЕНЮ ТРЕЯ НА LINUX (честно).** Меню трея — своё окно (`src/ui_menu.py`,
Qt.Popup, стиль карточки: цвета/геометрия/кривые из общего `src/ui_style.py`,
анимация появления, подсветка строки, галочки, выезд подменю). Видно ли его,
зависит от механизма трея:

* **XEmbed-панели** (XFCE, MATE, Cinnamon, LXQt): правый клик доходит до
  программы, поэтому открывается **наше окно меню**, 1:1 как в Windows. `QMenu`
  трею больше не отдаётся — именно из-за него панель показывала штатное меню Qt;
* **KDE Plasma**: значок — **своё окно программы** (`src/tray_popup.py`), а не
  виджет панели и не значок лотка. Оно встаёт у правого края над панелью
  (26 px, без рамки, всегда поверх, фокус не забирает, в панели задач его нет),
  правый клик открывает **наше** меню ровно у значка. Добавлять в панель ничего
  не нужно: значок поднимает сама программа.

> **Если раньше в панели стоял виджет** (`org.forest.proxyupdater`) — уберите
> остатки одной командой **от пользователя** (root не нужен):
> `forest-clean-desktop`. Плазмоида в пакете больше НЕТ: правый клик по любому
> виджету панели Plasma забирает сама панель, и меню программы до виджета не
> доходило.

Левый клик не открывает меню нигде — так и задумано. Проверено снимком экрана
на KWin 6 / XWayland (Plasma 6, CachyOS): значок виден, меню открывается у
значка. На других панелях (XFCE, MATE, Cinnamon, LXQt) значок работает так же,
если в сеансе есть X11/XWayland.

**MENU LOOK ON LINUX (honest).** The tray menu is our own window
(`src/ui_menu.py`, Qt.Popup, the notification card's style: colors/geometry/curves
come from the shared `src/ui_style.py`, plus the entrance animation, row
highlight, checkmarks and submenu slide-out). Whether you see it depends on the
tray backend:

* **XEmbed panels** (XFCE, MATE, Cinnamon, LXQt): the right click reaches the
  application, so you get **our menu window**, 1:1 with Windows. No `QMenu` is
  handed to the tray any more — that was why the panel used to show Qt's stock
  menu;
* **KDE Plasma**: the icon is the program's **own window** (`src/tray_popup.py`),
  not a panel widget and not a StatusNotifier item. It sits at the right edge
  above the panel (26 px, no frame, always on top, never takes focus, not in the
  task bar) and the right click opens **our** menu right at the icon. There is
  nothing to add to the panel: the program raises the icon itself.

> **If the panel widget** (`org.forest.proxyupdater`) was added earlier, remove
> the leftovers with one command **as the user** (no root):
> `forest-clean-desktop`. The package no longer ships a plasmoid: the Plasma
> panel takes the right click on any widget for itself, so the program's menu
> never reached it.

The left click opens nothing anywhere — by design. Verified with a real screen
capture on KWin 6 / XWayland (Plasma 6, CachyOS): the icon is visible and the
menu opens at the icon. Other panels (XFCE, MATE, Cinnamon, LXQt) work the same
way as long as the session has X11/XWayland.

Оглавление

* [0. Что выбрать](#0-что-выбрать)
* [1. Требования](#1-требования)
* [2. Установка из пакета](#2-установка-из-пакета)
  * [2.1. .deb — Debian, Ubuntu, Linux Mint](#21-deb--debian-ubuntu-linux-mint)
  * [2.2. .rpm — Fedora, RHEL/Rocky/Alma, openSUSE](#22-rpm--fedora-rhelrockyalma-opensuse)
  * [2.3. Arch, Manjaro — .pkg.tar.zst и PKGBUILD](#23-arch-manjaro--pkgtarzst-и-pkgbuild)
  * [2.4. AppImage — без установки в систему](#24-appimage--без-установки-в-систему)
  * [2.5. Портативный .tar.gz — любой дистрибутив](#25-портативный-targz--любой-дистрибутив)
* [3. Что именно ставится](#3-что-именно-ставится)
* [4. Проверка](#4-проверка)
* [5. Настройка](#5-настройка)
* [6. Удаление](#6-удаление)
* [7. Если systemd нет или `systemctl --user` недоступен](#7-если-systemd-нет-или-systemctl---user-недоступен)
* [8. WSL](#8-wsl)
* [9. Частые вопросы](#9-частые-вопросы)
* [10. Сборка пакетов из исходников](#10-сборка-пакетов-из-исходников)
* [11. Установка из репозитория исходников](#11-установка-из-репозитория-исходников)

---

## 0. Что выбрать

| Формат | Дистрибутивы | Установка одной командой | Права |
| --- | --- | --- | --- |
| `.deb` | Debian, Ubuntu, Mint | `sudo apt install ./forest-proxy-updater_1.3.2_amd64.deb` | root |
| `.rpm` | Fedora, RHEL, Rocky, Alma, openSUSE | `sudo dnf install ./forest-proxy-updater-1.3.2-1.x86_64.rpm` | root |
| `.pkg.tar.zst` | Arch, Manjaro, EndeavourOS | `sudo pacman -U ./forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst` | root |
| `.AppImage` | любой (glibc, x86_64) | `chmod +x ForestProxyUpdater-1.3.2-x86_64.AppImage && ./ForestProxyUpdater-1.3.2-x86_64.AppImage --install` | **без root** |
| `.tar.gz` | любой | `tar xzf forest-proxy-updater-1.3.2-linux-x86_64.tar.gz && cd forest-proxy-updater-1.3.2-linux-x86_64 && sudo ./install.sh` | root |

Во всех случаях ядро `xray` и гео-базы (`geoip.dat`, `geosite.dat`) **уже внутри
бинарника** — докладывать ничего не нужно. Python не требуется: программа несёт
свой интерпретатор. Единственная зависимость — `curl` (им проверяется связь и
качаются списки серверов).

Первые минуты после установки прокси ещё не отвечает: программа скачивает
публичные списки серверов, проверяет их (RTT, скорость) и только потом поднимает
ядро. Это нормально — см. раздел [Проверка](#4-проверка).

---

## 1. Требования

* Linux **x86_64**;
* **systemd** (для автозапуска; без него программа тоже работает — раздел 7);
* **curl** — ставится как зависимость пакета;
* ~100 МБ на диске (программа ~34 МБ, состояние и логи — по мере работы);
* интернет: программа сама скачивает публичные списки серверов.

Проверка:

```sh
uname -m                                   # x86_64
command -v systemctl || echo "нет systemd — см. раздел 7"
curl --version | head -1
```

---

## 2. Установка из пакета

### 2.1. .deb — Debian, Ubuntu, Linux Mint

```sh
sudo apt install ./forest-proxy-updater_1.3.2_amd64.deb
```

`apt` сам подтянет зависимость `curl` из своих репозиториев. Если вы предпочитаете
чистый `dpkg`:

```sh
sudo dpkg -i forest-proxy-updater_1.3.2_amd64.deb
sudo apt-get -f install        # дотянет curl, если его не было
```

### 2.2. .rpm — Fedora, RHEL/Rocky/Alma, openSUSE

Fedora / RHEL / Rocky / Alma:

```sh
sudo dnf install ./forest-proxy-updater-1.3.2-1.x86_64.rpm
```

openSUSE (пакет не подписан, поэтому нужен явный флаг разрешения):

```sh
sudo zypper --no-gpg-checks install ./forest-proxy-updater-1.3.2-1.x86_64.rpm
```

Если репозитории недоступны (например, машина за локальным прокси), а `curl` уже
установлен, пакет ставится и без сети:

```sh
sudo rpm -ivh --nodeps forest-proxy-updater-1.3.2-1.x86_64.rpm
```

### 2.3. Arch, Manjaro — .pkg.tar.zst и PKGBUILD

Готовый пакет:

```sh
sudo pacman -U forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst
```

Сборка из `PKGBUILD` (лежит в `packaging-linux/arch/PKGBUILD`) — тот же набор
файлов, что и в готовом пакете:

```sh
cd packaging-linux/arch
makepkg -si                     # соберёт и поставит (только не от root:
                                # makepkg от root отказывается работать)
```

`PKGBUILD` содержит install-скрипт (`post_install`/`pre_remove`/`post_remove`),
который включает службу при установке и снимает её при удалении.

### 2.4. AppImage — без установки в систему

AppImage — один файл, ничего не требует от системы, **root не нужен**.
Установка «для себя» (юниты `systemd --user` + автозапуск без входа в систему):

```sh
chmod +x ForestProxyUpdater-1.3.2-x86_64.AppImage
./ForestProxyUpdater-1.3.2-x86_64.AppImage --install
```

Что происходит: программа копирует себя в `~/.local/share/forest-proxy-updater/`,
кладёт конфиг в `~/.config/forest-proxy-updater/config.json`, пишет юниты
`systemctl --user` и включает `loginctl enable-linger $USER` (чтобы прокси жил без
входа в систему). Дальше AppImage не нужен — работает установленная копия.

Если FUSE недоступен (частая история в WSL и контейнерах), запускайте через
распаковку во временный каталог:

```sh
./ForestProxyUpdater-1.3.2-x86_64.AppImage --appimage-extract-and-run --install
```

Запустить **без установки** (просто из файла, без автозапуска):

```sh
./ForestProxyUpdater-1.3.2-x86_64.AppImage --run
```

### 2.5. Портативный .tar.gz — любой дистрибутив

```sh
tar xzf forest-proxy-updater-1.3.2-linux-x86_64.tar.gz
cd forest-proxy-updater-1.3.2-linux-x86_64
sudo ./install.sh
```

Скрипт ставит ту же системную раскладку, что и пакеты (раздел 3), и поднимает
службу. Полезные ключи:

```sh
sudo ./install.sh --dry-run      # показать, что будет сделано, ничего не менять
sudo ./install.sh --user         # установка ТОЛЬКО для текущего пользователя:
                                 # ~/.local + systemd --user, без sudo
sudo ./install.sh --prefix /opt  # другой префикс (по умолчанию /usr)
sudo ./uninstall.sh              # полное удаление
sudo ./uninstall.sh --prefix /opt
```

---

## 3. Что именно ставится

Системная раскладка (пакеты `.deb`/`.rpm`/Arch и `install.sh`):

| Что | Где |
| --- | --- |
| Программа (ELF; ядро и гео-базы внутри) | `/usr/lib/forest-proxy-updater/forest-proxy-updater` |
| Команда в PATH | `/usr/bin/forest-proxy-updater` (симлинк) |
| Конфиг | `/etc/forest-proxy-updater/config.json` |
| Состояние, кэши, сторожевые файлы | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/` |
| Логи | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/logs/main.log` и journal |
| Юниты systemd | `/usr/lib/systemd/system/forest-proxy-updater*.{service,timer}` |
| Документация (инструкция, условия использования, лицензия) | `/usr/share/doc/forest-proxy-updater/` |
| Лицензия GPL-3.0 | `/usr/share/licenses/forest-proxy-updater/COPYING` |

Раскладка пользовательской установки (AppImage, `install.sh --user`):

| Что | Где |
| --- | --- |
| Программа | `~/.local/share/forest-proxy-updater/forest-proxy-updater` |
| Конфиг | `~/.config/forest-proxy-updater/config.json` |
| Состояние и логи | `~/.local/state/forest-proxy-updater/` |
| Юниты | `~/.config/systemd/user/forest-proxy-updater*.{service,timer}` |

### Системная служба или пользовательские юниты?

Пакет ставит **системную** службу (`multi-user.target`), а не пользовательские
юниты. Причина простая: системной службе **не нужны** `loginctl enable-linger`,
вход пользователя и живой `systemd --user` — она поднимается при загрузке всегда и
одинаково работает на сервере, в контейнере и на рабочей станции. Пользовательские
юниты (`AppImage --install`, `install.sh --user`) — второй, «домашний» вариант; там
`enable-linger` включается автоматически.

```sh
systemctl status    forest-proxy-updater.service
systemctl restart   forest-proxy-updater.service
systemctl stop      forest-proxy-updater.service
journalctl -u forest-proxy-updater -f
```

Юниты:

* `forest-proxy-updater.service` — супервизор (`--run`): сторож + гвард +
  обновление пула + подъём ядра. `Restart=always`, `KillMode=control-group`
  (ядро снимается вместе со службой — сирот с занятым портом не остаётся).
* `forest-proxy-updater-refresh.service` + `forest-proxy-updater.timer` — отдельный
  плановый прогон `--once` (раз в 6 часов, `Persistent=true`). Ставится, но **по
  умолчанию не включается**: супервизор обновляет пул сам
  (`cycle.full_refresh_hours`). Включить:

  ```sh
  sudo systemctl enable --now forest-proxy-updater.timer
  ```

---

## 4. Проверка

```sh
systemctl status forest-proxy-updater.service          # active (running)
forest-proxy-updater --status                          # состояние и путь к ядру
journalctl -u forest-proxy-updater -f                  # живой журнал
```

Главная проверка — **реальный трафик через прокси**:

```sh
curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n' \
     https://www.gstatic.com/generate_204
```

Ожидается `204`. Первый раз это может занять несколько минут (на медленном канале
дольше): программа скачивает публичные списки серверов, прогоняет TCP-предфильтр,
RTT, тест скорости и только затем поднимает ядро. Пока подбор идёт, порт закрыт, а в
журнале видно прогресс.

Проверка целостности установки без запуска прокси (автотесты, ничего не меняет):

```sh
forest-proxy-updater --selftest
```

---

## 5. Настройка

`/etc/forest-proxy-updater/config.json` (системная установка) или
`~/.config/forest-proxy-updater/config.json` (AppImage/`--user`). Что чаще всего
меняют:

| Ключ | Смысл |
| --- | --- |
| `client.socks_port` | локальный порт прокси (по умолчанию 10808) |
| `client.listen` | адрес прослушивания (по умолчанию `127.0.0.1`) |
| `client.manage_system_proxy` | править ли системный прокси GNOME (`false` — не трогать) |
| `client.core` | явный путь к ядру (перекрывает встроенное) |
| `test.port_start` | первый из портов для временных ядер при тестах |
| `test.max_candidates` | сколько узлов тестировать за цикл |
| `watch.balancer_mode` | поднимать пул с балансировщиком (по умолчанию да) |

> ### ⚖️ Условия использования
>
> Программа предоставляет доступ ко **всем** сайтам, включая те, что
> заблокированы средствами сетевой фильтрации в той или иной стране или сети.
> **Ответственность за соблюдение законов своей юрисдикции** и правил своего
> интернет-провайдера лежит на пользователе. Используя программу, вы
> подтверждаете, что принимаете эти условия.
>
> Полный текст: [`TERMS.ru.md`](TERMS.ru.md) (English:
> [`TERMS.md`](TERMS.md)); в установленном пакете он лежит в
> `/usr/share/doc/forest-proxy-updater/TERMS.ru.md`.

После правки:

```sh
sudo systemctl restart forest-proxy-updater.service
```

Ручной останов/возобновление прокси без остановки службы (флаг «не поднимать»):

```sh
sudo touch /var/lib/forest-proxy-updater/state/forest-proxy-updater/proxy_off.flag
sudo rm    /var/lib/forest-proxy-updater/state/forest-proxy-updater/proxy_off.flag
```

Другие полезные режимы (работают с системной установкой так же):

```sh
forest-proxy-updater --status      # состояние: режим, ядро, пул, служба
forest-proxy-updater --once        # один полный цикл подбора
forest-proxy-updater --guard       # быстрая самопроверка связи
forest-proxy-updater --dry-run     # цикл без записи и без запуска ядра
forest-proxy-updater --selftest    # автотесты
```

Для ручных команд пути задаются теми же переменными, что и в юните:

```sh
FOREST_ROOT=/var/lib/forest-proxy-updater \
FOREST_CONFIG=/etc/forest-proxy-updater/config.json \
XDG_STATE_HOME=/var/lib/forest-proxy-updater/state \
forest-proxy-updater --status
```

---

## 6. Удаление

```sh
# .deb
sudo apt purge forest-proxy-updater
# .rpm
sudo dnf remove forest-proxy-updater        # openSUSE: sudo zypper remove forest-proxy-updater
# Arch
sudo pacman -Rns forest-proxy-updater
# .tar.gz
sudo ./uninstall.sh
# AppImage / --user
~/.local/share/forest-proxy-updater/forest-proxy-updater --uninstall
```

Скрипты пакета делают всё сами: `disable --now` для службы и таймера,
`daemon-reload`, удаление файлов и каталога состояния
`/var/lib/forest-proxy-updater`. Конфиг `/etc/forest-proxy-updater/config.json` —
«conffile»: обычное удаление его сохраняет (на случай переустановки), `purge`
удаляет.

Проверка, что следов не осталось:

```sh
systemctl is-active  forest-proxy-updater.service     # inactive/unknown
systemctl is-enabled forest-proxy-updater.service     # not-found
ls /usr/lib/forest-proxy-updater                      # нет такого каталога
ls /var/lib/forest-proxy-updater                      # нет такого каталога
command -v forest-proxy-updater || echo "команды нет — ок"
ss -ltnp | grep 10808 || echo "порт закрыт — ок"
```

Если что-то осталось после ручной установки из архива (не пакета):

```sh
sudo systemctl disable --now forest-proxy-updater.service forest-proxy-updater.timer
sudo rm -rf /usr/lib/forest-proxy-updater /etc/forest-proxy-updater \
            /var/lib/forest-proxy-updater /usr/bin/forest-proxy-updater \
            /usr/lib/systemd/system/forest-proxy-updater*
sudo systemctl daemon-reload
```

---

## 7. Если systemd нет или `systemctl --user` недоступен

Программа — обычный процесс; служба нужна только для автозапуска.

Запуск вручную (в терминале, лог идёт на экран):

```sh
FOREST_ROOT=/var/lib/forest-proxy-updater \
FOREST_CONFIG=/etc/forest-proxy-updater/config.json \
XDG_STATE_HOME=/var/lib/forest-proxy-updater/state \
/usr/lib/forest-proxy-updater/forest-proxy-updater --run
```

Свой автозапуск, если systemd нет (пример для crontab):

```cron
@reboot /usr/bin/forest-proxy-updater --once
```

`systemctl --user` не отвечает (`Failed to connect to user scope bus`,
`Failed to spawn executor: Device or resource busy`)? Это про пользовательский
менеджер systemd, а не про программу:

```sh
systemd-run --user --scope true          # отвечает ли менеджер
loginctl enable-linger "$USER"           # службы пользователя без входа в систему
# если не помогло — один раз с правами root:
sudo systemctl restart user@$(id -u).service
```

Системная установка (пакеты) от этой проблемы не зависит вообще: пользовательских
юнитов у ней нет.

---

## 8. WSL

Для systemd включите его в `/etc/wsl.conf`:

```ini
[user]
default=<ваш_пользователь>

[boot]
systemd=true
```

Затем `wsl --shutdown` и запуск заново. Проверка:

```sh
systemctl is-system-running        # running; «degraded» тоже нормально
```

`degraded` в WSL — обычное дело: мешают WSL-специфичные юниты (`getty@tty1.service`
и другие). На работу пакета это не влияет.

В WSL работают оба варианта: системная установка (`sudo apt install ./...deb`) и
пользовательская (AppImage/`--user` + `loginctl enable-linger $USER`). FUSE в WSL
обычно отсутствует — AppImage запускайте с `--appimage-extract-and-run`.

`notify-send` в WSL нет: уведомления уходят в журнал, программа работает штатно.
Системный прокси не правится (нет `gsettings`) — адрес
`127.0.0.1:<client.socks_port>` указывайте в приложениях вручную.

---

## 9. Частые вопросы

**Порт 10808 закрыт сразу после установки.** Так и должно быть первые минуты: идёт
подбор серверов. Смотрите прогресс: `journalctl -u forest-proxy-updater -f`.

**Служба перезапускается по кругу.** Откройте
`journalctl -u forest-proxy-updater -n 100`. Частая причина — нет сети или нет
рабочих серверов: программа сама повторит полный цикл и поднимет связь, как только
узлы найдутся (это штатное поведение, а не отказ).

**Ядро не найдено.** В пакете ядро внутри бинарника, искать его вручную не нужно.
Если хотите своё — укажите путь в `client.core`.

**Нужно сменить порт.** `client.socks_port` в конфиге →
`sudo systemctl restart forest-proxy-updater.service`.

**Системный прокси не включился.** На Linux это возможно только в GNOME
(`gsettings`). В остальных окружениях и в WSL программа этого не делает
сознательно — она не меняет чужие настройки.

**Как проверить установку без сети.** `forest-proxy-updater --selftest` —
автотесты, сеть не нужны, ничего не меняется.

---

## 10. Сборка пакетов из исходников

```sh
sh packaging-linux/build-all.sh          # все форматы, возможные на этой машине
sh packaging-linux/build-deb.sh          # только .deb
sh packaging-linux/build-rpm.sh          # только .rpm    (нужен rpmbuild)
sh packaging-linux/build-arch.sh         # только Arch    (нужен makepkg, не root)
sh packaging-linux/build-appimage.sh     # только AppImage (нужен mksquashfs)
sh packaging-linux/build-tar.sh          # только портативный .tar.gz
```

Готовые файлы — в `packaging-linux/out/`. Требования к инструментам и устройство
сборки: `packaging-linux/README.md`. Версия продукта в пакете задаётся переменной
`FOREST_PKG_VERSION` (по умолчанию `1.3.2`).

---

## 11. Установка из репозитория исходников

Нужна, если пакетов под вашу систему нет или вы хотите собрать всё сами.

**Готовый одиночный ELF:**

```sh
cd <репозиторий>
sh packaging/build-linux.sh              # -> linux/dist/forest-proxy-updater
./linux/dist/forest-proxy-updater --install      # юниты systemd --user
```

**Из исходников (нужен python3 системы):**

```sh
python3 src/v2ray_updater.py --selftest  # автотесты (общие + POSIX)
python3 src/v2ray_updater.py --install   # раскладка, юниты, первый цикл
python3 src/v2ray_updater.py --uninstall # снять всё
```

Что делает `--install`:

1. `~/.local/share/forest-proxy-updater/` — программа и
   `bin/xray/{xray,geoip.dat,geosite.dat,LICENSE}` (в собранном ELF ядро берётся
   изнутри бинарника);
2. `~/.config/forest-proxy-updater/config.json` — конфиг (из `config.example.json`,
   если файла ещё нет);
3. `~/.config/systemd/user/forest-proxy-updater{,-refresh}.service` и
   `forest-proxy-updater.timer`;
4. `systemctl --user enable --now forest-proxy-updater.service
   forest-proxy-updater.timer` и `loginctl enable-linger $USER`;
5. первый полный цикл подбора серверов (системный прокси на этом шаге **не**
   трогается).

Коды возврата: `0` — успех, `2` — не удалось разложить бинарь, `3` — нет systemd,
`4` — не удалось записать юниты.

Удаление: `./linux/dist/forest-proxy-updater --uninstall` — снимает юниты
(`disable --now` + удаление файлов + `daemon-reload`), останавливает **только своё**
ядро, возвращает системный прокси в исходное состояние, удаляет каталоги
данных/состояния/конфига.
