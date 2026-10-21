# Installing FOREST's Proxy Auto-updater on Linux

Step-by-step instructions: from a clean system to a working proxy. No windows, no
dialogs — only a log.

## Testing scope

**TESTING SCOPE (EN):** Package testing was performed in WSL (Linux on Windows),
not on a real installation on bare metal. The GUI menu and notifications are
tested logically but have NOT been verified on a live desktop — issues are
possible. If something does not work, please report it in the repository.

**ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** Проверка пакета выполнялась в среде WSL (Linux
внутри Windows), а не на установленной системе на реальном железе. Меню и
уведомления в графическом интерфейсе протестированы логически, но НЕ проверены
на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в
репозитории.

**MENU LOOK ON LINUX (honest).** The tray menu is our own window
(`src/ui_menu.py`, Qt.Popup, the notification card's style: colors, geometry and
curves come from the shared `src/ui_style.py`, plus the entrance animation, row
highlight, checkmarks and submenu slide-out). Whether you see it depends on the
tray backend:

* **XEmbed panels** (XFCE, MATE, Cinnamon, LXQt): the right click reaches the
  application, so you get **our menu window**, 1:1 with Windows. No `QMenu` is
  handed to the tray any more — that was why the panel used to show Qt's stock
  menu;
* **KDE Plasma (StatusNotifier)**: the panel owns the right click and would draw
  its own menu, so the package ships a **plasmoid** for the panel
  (`/usr/share/plasma/plasmoids/org.forest.proxyupdater`) that draws our icon
  with the state colour and opens **our** menu (`forest-ui --menu-popup`).
  Add it: right-click the panel → *Add Widgets…* → *FOREST Proxy Updater*.

The left click opens nothing anywhere. If Plasma keeps the right click for the
widget's own context menu, use **Ctrl+left click**. Not verified on a live Linux
desktop — the menu logic and QML syntax are checked, the panels are not.

**ВИД МЕНЮ ТРЕЯ НА LINUX (честно).** Меню трея — своё окно (`src/ui_menu.py`,
Qt.Popup, стиль карточки: цвета/геометрия/кривые из общего `src/ui_style.py`,
анимация появления, подсветка строки, галочки, выезд подменю). Видно ли его,
зависит от механизма трея:

* **XEmbed-панели** (XFCE, MATE, Cinnamon, LXQt): правый клик доходит до
  программы, поэтому открывается **наше окно меню**, 1:1 как в Windows. `QMenu`
  трею больше не отдаётся — именно из-за него панель показывала штатное меню Qt;
* **KDE Plasma (StatusNotifier)**: правый клик забирает панель, поэтому в пакете
  едет **плазмоид** (`/usr/share/plasma/plasmoids/org.forest.proxyupdater`): он
  рисует нашу иконку с цветом состояния и открывает **наше** меню
  (`forest-ui --menu-popup`). Добавить: правый клик по панели → «Добавить
  виджеты…» → «FOREST Proxy Updater».

Левый клик не открывает меню нигде. Если Plasma оставляет правый клик за меню
виджета — используйте **Ctrl+левый клик**. На живом рабочем столе Linux не
проверялось: логика меню и синтаксис QML проверены, панели — нет.

Contents

* [0. Which package to choose](#0-which-package-to-choose)
* [1. Requirements](#1-requirements)
* [2. Installing from a package](#2-installing-from-a-package)
  * [2.1. .deb — Debian, Ubuntu, Linux Mint](#21-deb--debian-ubuntu-linux-mint)
  * [2.2. .rpm — Fedora, RHEL/Rocky/Alma, openSUSE](#22-rpm--fedora-rhelrockyalma-opensuse)
  * [2.3. Arch, Manjaro — .pkg.tar.zst and PKGBUILD](#23-arch-manjaro--pkgtarzst-and-pkgbuild)
  * [2.4. AppImage — nothing installed system-wide](#24-appimage--nothing-installed-system-wide)
  * [2.5. Portable .tar.gz — any distribution](#25-portable-targz--any-distribution)
* [3. What exactly gets installed](#3-what-exactly-gets-installed)
* [4. Verifying it works](#4-verifying-it-works)
* [5. Configuration](#5-configuration)
* [6. Uninstalling](#6-uninstalling)
* [7. If systemd is missing or `systemctl --user` is unavailable](#7-if-systemd-is-missing-or-systemctl---user-is-unavailable)
* [8. WSL](#8-wsl)
* [9. FAQ](#9-faq)
* [10. Building the packages from source](#10-building-the-packages-from-source)
* [11. Installing from the source repository](#11-installing-from-the-source-repository)

---

## 0. Which package to choose

| Format | Distributions | One-command install | Privileges |
| --- | --- | --- | --- |
| `.deb` | Debian, Ubuntu, Mint | `sudo apt install ./forest-proxy-updater_1.3.2_amd64.deb` | root |
| `.rpm` | Fedora, RHEL, Rocky, Alma, openSUSE | `sudo dnf install ./forest-proxy-updater-1.3.2-1.x86_64.rpm` | root |
| `.pkg.tar.zst` | Arch, Manjaro, EndeavourOS | `sudo pacman -U ./forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst` | root |
| `.AppImage` | any glibc x86_64 | `chmod +x ForestProxyUpdater-1.3.2-x86_64.AppImage && ./ForestProxyUpdater-1.3.2-x86_64.AppImage --install` | **no root** |
| `.tar.gz` | any | `tar xzf forest-proxy-updater-1.3.2-linux-x86_64.tar.gz && cd forest-proxy-updater-1.3.2-linux-x86_64 && sudo ./install.sh` | root |

In every case the `xray` core and the geo databases (`geoip.dat`, `geosite.dat`)
are **already inside the binary** — there is nothing to download or drop in
place. Python is not required either: the program carries its own interpreter.
The only dependency is `curl` (used for connectivity checks and for fetching
server lists).

Right after installation the proxy is not answering yet: the program downloads
public server lists, tests them (RTT, throughput) and only then starts the core.
That is normal — see [Verifying it works](#4-verifying-it-works).

---

## 1. Requirements

* Linux **x86_64**;
* **systemd** (for autostart; the program works without it too — section 7);
* **curl** — pulled in as a package dependency;
* ~100 MB of disk (the program is ~34 MB; state and logs grow as it runs);
* internet access: the program fetches public server lists itself.

Checks:

```sh
uname -m                                   # x86_64
command -v systemctl || echo "no systemd — see section 7"
curl --version | head -1
```

---

## 2. Installing from a package

### 2.1. .deb — Debian, Ubuntu, Linux Mint

```sh
sudo apt install ./forest-proxy-updater_1.3.2_amd64.deb
```

`apt` resolves the `curl` dependency from its configured repositories. If you
prefer plain `dpkg`:

```sh
sudo dpkg -i forest-proxy-updater_1.3.2_amd64.deb
sudo apt-get -f install        # pulls in curl if it was missing
```

### 2.2. .rpm — Fedora, RHEL/Rocky/Alma, openSUSE

Fedora / RHEL / Rocky / Alma:

```sh
sudo dnf install ./forest-proxy-updater-1.3.2-1.x86_64.rpm
```

openSUSE (the package is not signed, so the check must be waived explicitly):

```sh
sudo zypper --no-gpg-checks install ./forest-proxy-updater-1.3.2-1.x86_64.rpm
```

If repositories are unreachable (a machine behind a local proxy, for example) and
`curl` is already installed, a local file installs offline:

```sh
sudo rpm -ivh --nodeps forest-proxy-updater-1.3.2-1.x86_64.rpm
```

### 2.3. Arch, Manjaro — .pkg.tar.zst and PKGBUILD

Ready-made package:

```sh
sudo pacman -U forest-proxy-updater-1.3.2-1-x86_64.pkg.tar.zst
```

Building from the shipped `PKGBUILD` (in `packaging-linux/arch/PKGBUILD`) — the
same file set as the prebuilt package:

```sh
cd packaging-linux/arch
makepkg -si                     # build and install (never as root:
                                # makepkg refuses to run as root)
```

The `PKGBUILD` ships an install scriptlet
(`post_install`/`pre_remove`/`post_remove`) that enables the service on install
and takes it down on removal.

### 2.4. AppImage — nothing installed system-wide

An AppImage is a single file, needs nothing from the system and **does not need
root**. Per-user install (systemd user units + autostart without a login):

```sh
chmod +x ForestProxyUpdater-1.3.2-x86_64.AppImage
./ForestProxyUpdater-1.3.2-x86_64.AppImage --install
```

What happens: the program copies itself into `~/.local/share/forest-proxy-updater/`,
writes the config to `~/.config/forest-proxy-updater/config.json`, installs
`systemctl --user` units and runs `loginctl enable-linger $USER` (so the proxy
keeps running without an interactive login). The AppImage file is not needed
afterwards — the installed copy takes over.

If FUSE is unavailable (common in WSL and containers), run it with the extract
flag:

```sh
./ForestProxyUpdater-1.3.2-x86_64.AppImage --appimage-extract-and-run --install
```

To run it **without installing** (foreground, no autostart):

```sh
./ForestProxyUpdater-1.3.2-x86_64.AppImage --run
```

### 2.5. Portable .tar.gz — any distribution

```sh
tar xzf forest-proxy-updater-1.3.2-linux-x86_64.tar.gz
cd forest-proxy-updater-1.3.2-linux-x86_64
sudo ./install.sh
```

The script installs the same system-wide layout as the packages (section 3) and
starts the service. Useful flags:

```sh
sudo ./install.sh --dry-run      # show what would be done, change nothing
sudo ./install.sh --user         # install for the CURRENT USER only:
                                 # ~/.local + systemd --user, no sudo
sudo ./install.sh --prefix /opt  # a different prefix (default /usr)
sudo ./uninstall.sh              # complete removal
sudo ./uninstall.sh --prefix /opt
```

---

## 3. What exactly gets installed

System-wide layout (`.deb`/`.rpm`/Arch packages and `install.sh`):

| What | Where |
| --- | --- |
| Program (ELF; core and geo bases inside) | `/usr/lib/forest-proxy-updater/forest-proxy-updater` |
| Command in PATH | `/usr/bin/forest-proxy-updater` (symlink) |
| Config | `/etc/forest-proxy-updater/config.json` |
| State, caches, watchdog files | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/` |
| Logs | `/var/lib/forest-proxy-updater/state/forest-proxy-updater/logs/main.log` and the journal |
| systemd units | `/usr/lib/systemd/system/forest-proxy-updater*.{service,timer}` |
| Documentation (install guide, terms of use, license) | `/usr/share/doc/forest-proxy-updater/` |
| GPL-3.0 license | `/usr/share/licenses/forest-proxy-updater/COPYING` |

Per-user layout (AppImage, `install.sh --user`):

| What | Where |
| --- | --- |
| Program | `~/.local/share/forest-proxy-updater/forest-proxy-updater` |
| Config | `~/.config/forest-proxy-updater/config.json` |
| State and logs | `~/.local/state/forest-proxy-updater/` |
| Units | `~/.config/systemd/user/forest-proxy-updater*.{service,timer}` |

### System service or user units?

The packages install a **system** service (`multi-user.target`), not user units.
The reason is simple: a system service needs **no** `loginctl enable-linger`, no
logged-in user and no live `systemd --user`. It always comes up at boot and
behaves the same on a server, in a container and on a workstation. User units
(`AppImage --install`, `install.sh --user`) remain as the second, per-user
option; there `enable-linger` is turned on automatically.

```sh
systemctl status    forest-proxy-updater.service
systemctl restart   forest-proxy-updater.service
systemctl stop      forest-proxy-updater.service
journalctl -u forest-proxy-updater -f
```

Units:

* `forest-proxy-updater.service` — the supervisor (`--run`): watchdog + guard +
  pool refresh + core startup. `Restart=always`, `KillMode=control-group` (the
  core is killed together with the service, so no orphan keeps the client port).
* `forest-proxy-updater-refresh.service` + `forest-proxy-updater.timer` — a
  separate scheduled `--once` run (every 6 hours, `Persistent=true`). It is
  installed but **not enabled by default**: the supervisor refreshes the pool on
  its own (`cycle.full_refresh_hours`). To enable it:

  ```sh
  sudo systemctl enable --now forest-proxy-updater.timer
  ```

---

## 4. Verifying it works

```sh
systemctl status forest-proxy-updater.service          # active (running)
forest-proxy-updater --status                          # state and core path
journalctl -u forest-proxy-updater -f                  # live log
```

The real check is **traffic through the proxy**:

```sh
curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n' \
     https://www.gstatic.com/generate_204
```

Expect `204`. The first time this can take several minutes (longer on a slow
link): the program downloads public server lists, runs a TCP prefilter, RTT and
throughput tests and only then starts the core. While the search is running the
port is closed and the log shows progress.

To validate the installation without starting the proxy (self-tests, changes
nothing):

```sh
forest-proxy-updater --selftest
```

---

## 5. Configuration

`/etc/forest-proxy-updater/config.json` (system install) or
`~/.config/forest-proxy-updater/config.json` (AppImage/`--user`). The keys people
change most often:

| Key | Meaning |
| --- | --- |
| `client.socks_port` | local proxy port (default 10808) |
| `client.listen` | listen address (default `127.0.0.1`) |
| `client.manage_system_proxy` | whether to touch the GNOME system proxy (`false` — leave it alone) |
| `client.core` | explicit path to the core (overrides the embedded one) |
| `test.port_start` | first port used for temporary cores during tests |
| `test.max_candidates` | how many nodes to test per cycle |
| `watch.balancer_mode` | raise the pool with a balancer (default yes) |

> ### ⚖️ Terms of use
>
> The program provides access to **all** websites, including those blocked by
> network filtering in a given country or network. **You are responsible for
> complying with the laws of your jurisdiction** and with your provider's rules.
> By using the program you confirm that you accept these terms.
>
> Full text: [`TERMS.md`](TERMS.md) (Russian: [`TERMS.ru.md`](TERMS.ru.md)); in an
> installed package it is
> `/usr/share/doc/forest-proxy-updater/TERMS.md`.

After an edit:

```sh
sudo systemctl restart forest-proxy-updater.service
```

Manually suspend/resume the proxy without stopping the service (the "do not
raise" flag):

```sh
sudo touch /var/lib/forest-proxy-updater/state/forest-proxy-updater/proxy_off.flag
sudo rm    /var/lib/forest-proxy-updater/state/forest-proxy-updater/proxy_off.flag
```

Other useful modes (they work with the system install as well):

```sh
forest-proxy-updater --status      # mode, core, pool, service state
forest-proxy-updater --once        # one full discovery cycle
forest-proxy-updater --guard       # quick connectivity self-check
forest-proxy-updater --dry-run     # cycle without writing or starting the core
forest-proxy-updater --selftest    # self-tests
```

For manual commands, point the program at the same paths the unit uses:

```sh
FOREST_ROOT=/var/lib/forest-proxy-updater \
FOREST_CONFIG=/etc/forest-proxy-updater/config.json \
XDG_STATE_HOME=/var/lib/forest-proxy-updater/state \
forest-proxy-updater --status
```

---

## 6. Uninstalling

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

The package scriptlets do everything needed: `disable --now` for the service and
the timer, `daemon-reload`, removal of the payload and of the state directory
`/var/lib/forest-proxy-updater`. The config `/etc/forest-proxy-updater/config.json`
is a conffile: a plain `remove` keeps it (handy for reinstalls), `purge` deletes
it.

Checking that nothing is left behind:

```sh
systemctl is-active  forest-proxy-updater.service     # inactive/unknown
systemctl is-enabled forest-proxy-updater.service     # not-found
ls /usr/lib/forest-proxy-updater                      # no such directory
ls /var/lib/forest-proxy-updater                      # no such directory
command -v forest-proxy-updater || echo "no command — good"
ss -ltnp | grep 10808 || echo "port closed — good"
```

If something remains after a manual tarball install (not a package):

```sh
sudo systemctl disable --now forest-proxy-updater.service forest-proxy-updater.timer
sudo rm -rf /usr/lib/forest-proxy-updater /etc/forest-proxy-updater \
            /var/lib/forest-proxy-updater /usr/bin/forest-proxy-updater \
            /usr/lib/systemd/system/forest-proxy-updater*
sudo systemctl daemon-reload
```

---

## 7. If systemd is missing or `systemctl --user` is unavailable

The program is an ordinary process; the service only provides autostart.

Running it by hand (foreground, log goes to the terminal):

```sh
FOREST_ROOT=/var/lib/forest-proxy-updater \
FOREST_CONFIG=/etc/forest-proxy-updater/config.json \
XDG_STATE_HOME=/var/lib/forest-proxy-updater/state \
/usr/lib/forest-proxy-updater/forest-proxy-updater --run
```

Your own autostart without systemd (a crontab example):

```cron
@reboot /usr/bin/forest-proxy-updater --once
```

`systemctl --user` not answering (`Failed to connect to user scope bus`,
`Failed to spawn executor: Device or resource busy`)? That is about the systemd
user manager, not about this program:

```sh
systemd-run --user --scope true          # does the manager answer?
loginctl enable-linger "$USER"           # user services without an interactive login
# if that did not help — once, as root:
sudo systemctl restart user@$(id -u).service
```

The system-wide install (packages) does not depend on any of this: it ships no
user units at all.

---

## 8. WSL

Enable systemd in `/etc/wsl.conf`:

```ini
[user]
default=<your_user>

[boot]
systemd=true
```

Then `wsl --shutdown` and start it again. Check:

```sh
systemctl is-system-running        # running; "degraded" is fine too
```

`degraded` is normal in WSL: WSL-specific units (`getty@tty1.service` and
friends) fail there. It does not affect the package.

Both install flavours work in WSL: system-wide (`sudo apt install ./...deb`) and
per-user (AppImage/`--user` plus `loginctl enable-linger $USER`). FUSE is usually
absent in WSL — run the AppImage with `--appimage-extract-and-run`.

`notify-send` is absent in WSL: notifications go to the log and the program keeps
working. The system proxy is not touched (no `gsettings`) — point applications at
`127.0.0.1:<client.socks_port>` manually.

---

## 9. FAQ

**Port 10808 is closed right after installation.** That is expected for the first
few minutes: the server search is running. Watch it with
`journalctl -u forest-proxy-updater -f`.

**The service keeps restarting.** Look at
`journalctl -u forest-proxy-updater -n 100`. The usual cause is no network or no
working servers yet: the program repeats the full cycle and raises the connection
as soon as nodes appear (that is normal behaviour, not a failure).

**The core was not found.** In the packages the core lives inside the binary —
there is nothing to look for. If you want your own core, set `client.core`.

**I need a different port.** Set `client.socks_port` in the config, then
`sudo systemctl restart forest-proxy-updater.service`.

**The system proxy was not enabled.** On Linux that is only possible under GNOME
(`gsettings`). Elsewhere, and in WSL, the program deliberately leaves foreign
settings alone.

**How do I check the install without network access?** `forest-proxy-updater
--selftest` — self-tests, no network needed, changes nothing.

---

## 10. Building the packages from source

```sh
sh packaging-linux/build-all.sh          # every format possible on this machine
sh packaging-linux/build-deb.sh          # .deb only
sh packaging-linux/build-rpm.sh          # .rpm only    (needs rpmbuild)
sh packaging-linux/build-arch.sh         # Arch only    (needs makepkg, not root)
sh packaging-linux/build-appimage.sh     # AppImage only (needs mksquashfs)
sh packaging-linux/build-tar.sh          # portable .tar.gz only
```

Artifacts land in `packaging-linux/out/`. Tool requirements and the layout of the
build system are described in `packaging-linux/README.md`. The version embedded in
the packages comes from `FOREST_PKG_VERSION` (default `1.3.2`).

---

## 11. Installing from the source repository

Use this if there is no package for your system, or if you want to build
everything yourself.

**The ready-made single ELF:**

```sh
cd <repository>
sh packaging/build-linux.sh              # -> linux/dist/forest-proxy-updater
./linux/dist/forest-proxy-updater --install      # systemd user units
```

**From source (needs the system python3):**

```sh
python3 src/v2ray_updater.py --selftest  # self-tests (shared + POSIX)
python3 src/v2ray_updater.py --install   # layout, units, first cycle
python3 src/v2ray_updater.py --uninstall # remove everything
```

What `--install` does:

1. `~/.local/share/forest-proxy-updater/` — the program and
   `bin/xray/{xray,geoip.dat,geosite.dat,LICENSE}` (in the built ELF the core
   comes from inside the binary);
2. `~/.config/forest-proxy-updater/config.json` — config (from
   `config.example.json` if no file exists yet);
3. `~/.config/systemd/user/forest-proxy-updater{,-refresh}.service` and
   `forest-proxy-updater.timer`;
4. `systemctl --user enable --now forest-proxy-updater.service
   forest-proxy-updater.timer` and `loginctl enable-linger $USER`;
5. the first full server discovery cycle (the system proxy is **not** touched at
   this step).

Return codes: `0` — success, `2` — could not lay down the binary, `3` — no systemd,
`4` — could not write the units.

Uninstall: `./linux/dist/forest-proxy-updater --uninstall` — takes the units down
(`disable --now` + file removal + `daemon-reload`), stops **only its own** core,
restores the system proxy and removes the data/state/config directories.
