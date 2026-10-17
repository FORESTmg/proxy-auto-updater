# FOREST's Proxy Auto-updater — Linux

Кратко: как поставить, чем управлять и где что лежит.

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

## Что это

Тот же продукт, что и на Windows, но с ядром `xray` (не `xray.exe`), юнитами
`systemd --user` (вместо реестра и ярлыка) и путями XDG. Клиентская логика
общая: `src/clientwrite.py` — сердцевина (проверки связи и скорости, чёрный
список, стор, генерация и валидация конфигов ядра, пул балансировщика,
активация, гвард), на Linux к ней добавляется тонкий POSIX-слой
`src/clientwrite_posix.py`, который подменяет только ОС-зависимые примитивы
(процессы и системный прокси). Окон и диалогов нет: только лог и уведомления
`notify-send`, если он установлен.

## Быстрый старт (из исходников)

```sh
cd <repo>
python3 src/v2ray_updater.py --selftest     # автотесты (общие + POSIX)
python3 src/v2ray_updater.py --install      # раскладка, юниты, первый цикл
python3 src/v2ray_updater.py --status
python3 src/v2ray_updater.py --uninstall    # снять всё
```

## Быстрый старт (готовый ELF)

```sh
sh packaging/build-linux.sh                 # соберёт linux/dist/forest-proxy-updater
./linux/dist/forest-proxy-updater --install
./linux/dist/forest-proxy-updater --status
./linux/dist/forest-proxy-updater --uninstall
```

Бинарь самодостаточный: Python и ядро внутри, ядро распаковывается в
`bin/xray/xray` рядом с программой (установщик копирует его в каталог данных).

## Пути (XDG)

| Что | Куда |
| --- | --- |
| Программа, ядро, гео-базы | `~/.local/share/forest-proxy-updater/` (`bin/xray/{xray,geoip.dat,geosite.dat}`) |
| Конфиг | `~/.config/forest-proxy-updater/config.json` |
| Состояние, кэши | `~/.local/state/forest-proxy-updater/` |
| Логи | `~/.local/state/forest-proxy-updater/logs/main.log` |
| Юниты | `~/.config/systemd/user/forest-proxy-updater*.{service,timer}` |

Перекрытие: `FOREST_ROOT` (весь корень), `FOREST_CONFIG` (конфиг),
`XDG_DATA_HOME` / `XDG_CONFIG_HOME` / `XDG_STATE_HOME`.

Порядок поиска ядра: явный путь из `client.core` → `bin/xray/xray` рядом с
программой → каталог программы → `/usr/local/bin`, `/usr/bin`, `/opt/xray` →
`PATH`.

## Службы systemd

```sh
systemctl --user status  forest-proxy-updater.service
systemctl --user restart forest-proxy-updater.service
systemctl --user list-timers forest-proxy-updater.timer
journalctl --user -u forest-proxy-updater -f
```

* `forest-proxy-updater.service` — супервизор (`--run`): сторож + гвард +
  обновление + индикация. `Restart=always`, `RestartSec=5`,
  `StartLimitIntervalSec=0` — перезапускается без ограничения попыток.
  `KillMode=control-group` снимает и ядро: сирот с занятым портом не остаётся.
* `forest-proxy-updater-refresh.service` + `forest-proxy-updater.timer` —
  плановый полный цикл (`--once`) раз в 6 часов, `Persistent=true` (пропущенный
  прогон выполнится после включения).

Автозапуск без входа в систему — `loginctl enable-linger $USER`
(`--install` делает это сам; если прав не хватило, подскажет команду с `sudo`).

## Индикация вместо трея

WinAPI-трей на Linux не работает. Вместо него:

* файл состояния `~/.local/state/forest-proxy-updater/tray_status.json`
  (его читает `--status` и внешние скрипты);
* `notify-send`, если он есть в `PATH` — уведомления о потере и
  восстановлении связи (без окон). Нет `notify-send` — событие остаётся в логе
  и в файле состояния, программа работает как обычно.

## Ручное отключение прокси

Флаг `~/.local/state/forest-proxy-updater/proxy_off.flag`: пока он есть,
автоподъём запрещён во всех циклах.

```sh
touch ~/.local/state/forest-proxy-updater/proxy_off.flag     # стоп
rm    ~/.local/state/forest-proxy-updater/proxy_off.flag     # продолжить
```

Системный прокси (GNOME) правится только при наличии `gsettings`; в
минимальной среде (сервер, WSL, контейнер) программа этого не делает и просто
работает на своём SOCKS-порту.

## Сборка

См. `packaging/build-linux.sh` и `packaging/forest-proxy-updater-linux.spec`.
Ядро качается официальным загрузчиком `packaging/fetch_core_linux.py`
(пин — `packaging/core.lock.linux.json`, только `github.com/XTLS/Xray-core`).
