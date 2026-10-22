# FOREST's Proxy Auto-updater — как устроен этот пакет (Linux)

Короткая шпаргалка для того, кто ставит пакет. Подробно — в `INSTALL.ru.md`
(русский) и `INSTALL.md` (English) рядом с этим файлом.

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

## Что уже внутри

Ядро `xray` и гео-базы (`geoip.dat`, `geosite.dat`) **вшиты внутрь бинарника**,
скачивать и докладывать больше нечего. Python не нужен: программа несёт свой
интерпретатор. Зависимостей у пакета нет, кроме `curl` для проверок связи, и
системных библиотек для помощника интерфейса (Qt6): `libxkbcommon-x11`,
`xcb-util-cursor`, `xcb-util-wm`, `xcb-util-keysyms`, `xcb-util-image`,
`xcb-util-renderutil`, `libxcb` (в .deb — `libxkbcommon-x11-0`,
`libxcb-cursor0`, `libxcb-icccm4`, `libxcb-image0`, `libxcb-keysyms1`,
`libxcb-render-util0`, `libxcb-shape0`, `libxcb-xkb1`, `libgl1`, `libegl1`).
Сами плагины платформ Qt (`xcb`, `wayland`) и библиотеки Qt **внутри комплекта**.

## Раскладка

| Что | Где |
| --- | --- |
| Программа (ELF, ядро и гео-базы внутри) | `/usr/lib/forest-proxy-updater/forest-proxy-updater` |
| Команда в PATH | `/usr/bin/forest-proxy-updater` (симлинк) |
| Конфиг | `/etc/forest-proxy-updater/config.json` |
| Состояние, кэши, сторожевые файлы | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/` |
| Логи | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/logs/main.log` и journal |
| Юниты systemd | `/usr/lib/systemd/system/forest-proxy-updater*.{service,timer}` |
| Значок в стиле трея (рисует сама программа) | окно процесса `forest-ui`, отдельных файлов в системе не требует |
| Уборка панели от старого виджета | `/usr/bin/forest-clean-desktop` → `/usr/lib/forest-proxy-updater/forest-clean-desktop` |
| Лицензия GPL-3.0 | `/usr/share/licenses/forest-proxy-updater/COPYING` |
| Документация | `/usr/share/doc/forest-proxy-updater/` |

## Значок: одно окно в стиле трея

Плазмоида в пакете **нет** и не будет: правый клик по ЛЮБОМУ виджету панели
Plasma забирает сама панель (в меню приходят «Настроить виджет / Режим
редактирования / Удалить виджет»), поэтому меню программы до виджета не
доходило. Значок — **своё окно программы** (`src/tray_popup.py`):

* 26 px, без рамки, всегда поверх, у правого края над панелью, фокус не
  забирает (`WA_ShowWithoutActivating` + `WindowDoesNotAcceptFocus`);
* в панели задач его нет (`_NET_WM_STATE_SKIP_TASKBAR`), в списке окон — тоже;
* **правый клик** — наше меню ровно у значка; **левый** — ничего;
  **наведение** — подсказка с цветом состояния;
* в полноэкранных приложениях значок прячется.

Добавлять ничего не нужно: значок поднимает сама программа при запуске службы.
Если раньше в панели стоял виджет `org.forest.proxyupdater`, уберите остатки
**от пользователя** (root не нужен):

```sh
forest-clean-desktop            # убрать виджет и закреплённый ярлык
forest-clean-desktop --dry-run  # только показать, что будет сделано
```

Скрипт правит конфиг панели теми же инструментами KDE (`kreadconfig6` /
`kwriteconfig6`), делает копию конфига рядом и обновляет кеши (`kbuildsycoca6`,
`qdbus6 org.kde.KWin /KWin reconfigure`).

## Служба

Пакет ставит **системную** службу (не `systemctl --user`): она поднимается при
загрузке независимо от того, вошёл ли кто-то в систему, и не требует
`loginctl enable-linger`. `postinst` сам делает `systemctl enable --now`.

```sh
systemctl status  forest-proxy-updater.service
systemctl restart forest-proxy-updater.service
journalctl -u forest-proxy-updater -f
```

Таймер планового обновления пула (`forest-proxy-updater.timer`) ставится, но по
умолчанию **не** включается: супервизор (`--run`) обновляет пул сам каждые
`cycle.full_refresh_hours`. Таймер — для тех, кто хочет отдельный расписанный
прогон `--once`:

```sh
systemctl enable --now forest-proxy-updater.timer
```

## Порт

По умолчанию `socks_port = 10808` на `127.0.0.1` (правится в
`/etc/forest-proxy-updater/config.json`, применяется после
`systemctl restart forest-proxy-updater`).

## Проверка связи

```sh
curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n' \
     https://www.gstatic.com/generate_204      # ожидается 204
```

Первый запуск занимает несколько минут: программа скачивает публичные списки
серверов, проверяет их и только потом поднимает прокси. До этого момента порт может
быть закрыт — это нормально.

## Удаление

Пакетным менеджером (`apt purge` / `dnf remove` / `pacman -Rns`) — скрипты
пакета сами останавливают и снимают службу, делают `daemon-reload` и удаляют
`/var/lib/forest-proxy-updater`. Либо полностью, вручную, если что-то осталось:

```sh
sudo systemctl disable --now forest-proxy-updater.service forest-proxy-updater.timer
sudo rm -rf /var/lib/forest-proxy-updater
```

## Условия использования

Программа предоставляет доступ ко всем сайтам, включая те, что заблокированы
средствами сетевой фильтрации; ответственность за соблюдение законов своей
юрисдикции и правил своего интернет-провайдера лежит на пользователе. Скрипты
установки печатают это уведомление, а при первом запуске программа пишет его
один раз в журнал (дальше — молча). Полный текст:
`/usr/share/doc/forest-proxy-updater/TERMS.ru.md` (English: `TERMS.md`).

## Если systemd недоступен

В контейнере или дистрибутиве без systemd служба не поднимется. Программа
работает и просто из командной строки:

```sh
FOREST_ROOT=/var/lib/forest-proxy-updater \
FOREST_CONFIG=/etc/forest-proxy-updater/config.json \
/usr/lib/forest-proxy-updater/forest-proxy-updater --run
```

Проверка установки без запуска прокси: `forest-proxy-updater --selftest`
(автотесты, ничего не меняет).
