# FOREST's Proxy Auto-updater

Automatic selection and switching of proxy servers on **Windows and Linux**. The
program downloads public server lists, tests the servers, picks the working ones
and keeps your connection going by switching between them — so that a single
server dying out does not break your internet access.

**Standalone by default.** The program does *not* require v2rayN. It keeps its
own server list, writes the Xray-core config itself, starts and stops
`xray.exe` itself and sets/clears the Windows system proxy itself. An optional
v2rayN mode is available for people who already use v2rayN.

## Testing scope (Linux)

**Package testing was performed in WSL (Linux on Windows), not on a real installation on bare metal. The GUI menu and notifications are tested logically but have NOT been verified on a live desktop — issues are possible. If something does not work, please report it in the repository.**

**ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** Проверка пакета выполнялась в среде WSL (Linux внутри Windows), а не на установленной системе на реальном железе. Меню и уведомления в графическом интерфейсе протестированы логически, но НЕ проверены на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в репозитории.

The Windows build is tested on a live PC and carries no such caveat.

- License: **GPL-3.0-only** (see [`LICENSE`](LICENSE))
- Languages: Russian + English (see [`README.ru.md`](README.ru.md))
- Vibe-coded — written iteratively in close dialogue with an AI assistant, so
  please verify behaviour yourself before you rely on it
- No third-party Python dependencies — the standard library only
- One process (supervisor) instead of three separate scheduled tasks

> **Read the [Security and limitations](#security-and-limitations) section
> before you run it.** The program changes the Windows system proxy and manages
> a proxy-core process. That is by design, and it is described plainly there.
>
> **By using the program you accept the
> [Terms of use](docs/TERMS.md)** — it provides access to all websites, including
> blocked ones, and complying with the laws of your jurisdiction is your
> responsibility.

---

## Who it is for

- People who want a **working proxy on Windows without babysitting it** —
  start it once, and it re-selects servers as the free ones die.
- People who already have subscription links or a v2rayN setup and want the
  server list kept fresh and the active server switched automatically.
- People who want to **see exactly what it does** — it runs a plain Xray core
  and points the Windows system proxy at a local port; there is no hidden
  magic, and no third-party Python packages.

It is **not** for you if you need a guaranteed, stable, high-speed service.
Public free servers are unstable by nature — see the limitations section.

## How it differs from v2rayN

v2rayN is a full GUI client: you import servers, choose one and connect.
FOREST's Proxy Auto-updater is the opposite kind of tool — it is a **background
worker** with no server-management GUI. It:

- **finds and tests servers for you** rather than waiting for you to pick one;
- **keeps a pool of servers inside one running core** and lets Xray switch
  between them, instead of restarting on every change;
- **watches the connection continuously** and re-selects when servers die;
- **manages the Windows system proxy** so that system-wide traffic follows the
  working core.

In other words, v2rayN is the client you configure; this program is the thing
that keeps a client's server list alive. They are not competitors, and the
program can also *drive an installed v2rayN* if you prefer (see the two modes
below).

## Two modes

You choose the mode in `config.json` (in `client.mode`). No code changes.

| `client.mode` | What it does |
|---|---|
| `standalone` (default) | Own Xray core, own server store, own system-proxy management. v2rayN is not needed. |
| `v2rayn` | Works through an installed v2rayN: its database (`guiNDB.db`), its config (`guiNConfig.json`) and its core. The database is backed up before writing. |

In standalone mode the core is looked up in this order:

1. an explicit path from the config (`client.core`);
2. `bin\xray\xray.exe` next to the program;
3. `xray.exe` in `PATH`.

If the core is not found, the program tells you and points you to the official
Xray-core release page. **The program does not download the core for you** — you
put `xray.exe` in place yourself.

## Quick start

The distributable is a single `.exe`. The usual flow:

1. **Download** the `.exe` from the project's GitHub Releases page.
   2. **Nothing to download — the core is already inside.** The `.exe` ships with
      Xray v26.3.27 and both geo databases (`geoip.dat`, `geosite.dat`) embedded, so
      standalone mode works on a clean machine with no extra files. Put your own
      `bin\xray\` folder beside the `.exe` only if you want to override or update
      the core — that copy takes priority over the built-in one.
      v2rayN mode needs no files at all: v2rayN provides its own core.
3. **Run the `.exe`.** Windows will warn about an unsigned program
   (SmartScreen) — see the security section. On first run the program creates
   its working config from `config.example.json`, lays itself out under
   `%LOCALAPPDATA%\ForestProxyUpdater`, adds a Start Menu shortcut and a
   **Windows Task Scheduler watchdog task** (autonomous supervision — see
   below), and runs the first selection cycle.
4. After the first cycle, **the Windows system proxy is pointed at the local
   port** `127.0.0.1:10808` and traffic goes through the selected servers.

State, logs and the working config live under
`%LOCALAPPDATA%\ForestProxyUpdater`. The folder you unzipped the program into
stays clean.

At runtime the program uses tools that ship with Windows — `curl.exe`
(Windows 10 1803+), and `PowerShell`/`tasklist`/`taskkill`/`netstat` for
process and port checks. No administrator rights are required.

### Removing it

Run the `.exe` with `--uninstall` (or the "uninstall" entry the packaged build
provides). This stops the core, **restores the system proxy to the state it had
before the first run**, removes the supervision task (and any old autostart
entry) and the shortcut, and deletes `%LOCALAPPDATA%\ForestProxyUpdater`.

## Autonomous supervision: the program brings itself back

By default the installer creates a **Windows Task Scheduler task**
`ForestProxyUpdater-Watchdog` and **enables it**. This is what keeps the program
alive without you watching it:

- it starts the program **when you log in**, and
- it **checks every 5 minutes** whether the program is running, and starts it
  again if it is not — after a crash, after you closed it by accident, or after
  the parent process that started it died (for example an editor, a package
  manager or an updater that kills its own process tree).

The check itself is the `--ensure` mode: it is quick, writes only to the log,
**never shows a window**, and refuses to start a second copy while one is
already running — two running programs would fight over the same port and break
your connection.

Single-instance operation is held by an **exclusive file lock**
(`state/supervisor.lock`, module `src/instancelock.py`), not by a marker file
with a process id. The program takes the lock and holds it for its whole life;
the operating-system kernel releases it together with the process. Three
consequences, all verified on a live machine:

* when two copies are started **at the same instant**, exactly one survives — the
  other sees a live lock and exits quietly (previously **zero** survived: the
  copies saw each other in the process list and both refused to start);
* process ids are no longer *guessed* at: while the lock is held no second copy
  appears, and a dead owner leaves no stale lock behind;
* a Task Scheduler tick and a repeated `--ensure` do **nothing** while the
  program runs — an extra call is harmless.

The interface helper has the same lock (`state/forest-ui.lock`): that is why the
tray always shows **one** icon, even after a helper restart or a double start.
No thread of the program ever brings up a second copy of it. The built-in
(reserve) icon does not compete with the helper either: while the helper lives the
program draws no icon of its own, and if the helper appears later it removes its
own.

> **Read this.** If the task is not created, or you switch it off, the program
> **will not restart itself**. After a crash or an accidental close you will have
> to start it by hand. That is the only mechanical difference between supervised
> and unsupervised operation — everything else works exactly the same.

Turn it off, on, or inspect it:

```bat
ForestProxyUpdater.exe --watchdog off      :: remove the task — no auto-restart any more
ForestProxyUpdater.exe --watchdog on       :: create it again and enable it
ForestProxyUpdater.exe --watchdog status   :: is it on? is the task there? when did it last run?
```

From the tray icon: **Supervision → "Bring the program up automatically"**
(a tick means on). `--status` also prints the supervision line.

The setting lives in `config.json`:

```json
"watchdog": { "enabled": true, "interval_min": 5, "task_name": "ForestProxyUpdater-Watchdog" }
```

`enabled: false` makes `--install` skip creating the task (and delete a previous
one), and it says so honestly instead of pretending the program will come back.
`interval_min` changes the check period. On Linux this section is informational:
supervision there is systemd's job (`Restart=always` plus the update timer), and
`--ensure` simply starts the service if it is not running.

## What the interface shows (same as the previous add-on)

The tray icon and the notification card show exactly the data the earlier setup
showed — the composition is kept in one place (`src/status_info.py`) and used by
both interfaces, so nothing is lost:

| Where | What is shown |
|---|---|
| icon tooltip | `FOREST <link state> \| <N> servers \| <RU route> \| watch OK` (four fields, same order) |
| icon colour | six states: green `связь OK`, grey `выключено вручную`, amber `переключение` / `сторож молчит (нет данных)`, red `НЕТ СВЯЗИ`, blue `восстановление связи` |
| notification card | title (`Восстановление связи` / `Переключение сервера` / `Связь восстановлена` / `Переключение завершено` / `Восстановить не удалось`), subtitle, a **step block with each step's time in milliseconds** (`Обрыв обнаружен (пульс порта)   50 мс`), a time bar, a spinner and the footer |
| link recovery time | `связь подтверждена · N.NN с` in the subtitle and `восстановлено за N.NN с (NNNN мс) · окно закроется через 4 с` in the footer; also kept in the menu after the card closes |
| menu | open log, open status, enable/disable the proxy by hand, quit — plus supervision, notifications, **“Filters” (RU direct; Cloudflare bypass)**, theme, “refresh now” and about |

Only the smoothness (60 fps) and the look (light/dark themes, your own icon with
a state badge in the corner) are new; the data, the wording and the order are
the reference ones. The one deliberate correction: the server count used to be a
fixed `10`, now it is the real pool size.

## Interface: the Qt helper (tray icon, menu, notifications)

The interface is a **separate program, `forest-ui`** (Qt/PySide6, LGPL-3.0 —
compatible with this project's GPL-3.0; Qt is linked dynamically and unmodified).
The main program stays small and needs no third-party packages; the helper draws:

* the **tray icon** — your own `app.ico` with **not a single altered pixel**
  (proved pixel by pixel in the self-test: 77.8 % of the 32×32 points match the
  artwork), and the connection state is shown by a **badge in the bottom-right
  corner** (green = working, grey = off by hand, red = no connection, amber =
  switching server, blue = restoring). The badge has a dark outline so it reads
  on both light and dark taskbars;
* the **menu with submenus**: the status line, supervision on/off, notifications
  on/off, **“Filters” → “RU direct” / “Cloudflare bypass” (two checkable
  items)**, **theme (follow system / light / dark)**, "Refresh servers now",
  "Test notification", "Open log", "About", "Quit";
* **notifications** — a card **exactly as in the approved mock-up**
  (`docs/notify-mockup.html`; it ships inside the bundle and the selftest compares
  its numbers with the code): 392 px wide, radius 12, a 3 px state stripe on the
  left, a 34×34 icon tile, a spinner on the right, a **step block with each
  step's time in milliseconds**, a time bar and a footer. Your own `app.ico` sits
  inside — the same file and the same code path as the tray icon (verified pixel
  by pixel: **1024 of 1024 pixels match the tray icon**). The entrance slides in
  420 px with the mock-up's curve over **340 ms**, the exit takes **260 ms**, the
  spinner turns once per 0.85 s. Frames run on an absolute **60 fps** grid
  (measured on the live PC: **60.1 fps** average, 388 frames, worst frame
  **22.7 ms**, no dropped frames and no stalls); only the window position and its
  opacity change, while the background, icons and texts are rendered **once** into
  a separate layer — so there is nothing to flicker. It **never** takes focus: the
  selftest watches the foreground window for the whole animation and never sees
  the card as the active window.
* **the final state happens in the same card**: when the supervisor reports the end
  of the process, the card on screen changes state (title `Связь восстановлена`,
  subtitle `связь подтверждена · N.NN с`, footer `восстановлено за N.NN с · окно
  закроется через 4 с`, the spinner turns green) instead of sliding in again.

No windows on the desktop: both the helper and the main program print **ASCII
only** to stdout, the human-readable report goes to UTF-8 files
(`state/ui_selftest.txt`, `state/status.txt`, `logs/main.log`), and **any**
unhandled exception is written with a traceback to `logs/crash.log` and exits with
code 70 (`src/safestdio.py`, `disable_windowed_traceback=True` in every spec). The
"Unhandled exception in script" modal window is impossible.

**The helper does not control the core.** A menu item is written as a command
into `state/ui_cmd.json`; the supervisor reads that file and performs it through
exactly the same code as the built-in menu (system proxy, supervision task,
`config.json`, the core itself). So there is still **one owner** of the core. In
the other direction the helper reads `state/tray_status.json` (colour and tooltip)
and leaves a heartbeat in `state/ui_helper.json`, which `--status` prints.

Everything degrades quietly:

| Situation | What happens |
|---|---|
| helper not installed, not running or crashed | the built-in icon (ctypes) and the built-in notifications work — the program behaves exactly as before |
| helper running | the helper owns the icon, the menu and the notifications; the built-in tray waits **in reserve** and takes over within seconds if the helper stops |
| no tray on the desktop at all | notifications still appear; the program keeps working (only the menu is missing) |
| `"ui": {"helper": "off"}` | the helper is never started |

The helper is raised by the program itself — at `--install`, by the **scheduled
task** (its action is `--ensure`) and by the supervisor's keep-alive loop every
30 s — so a helper that crashes comes back on its own, and a second copy is never
started.

Settings: `ui.helper` = `auto` (default; start it if the file is present) |
`on` | `off`, and `ui.helper_exe` — an explicit path to the file.
Build it with `packaging\build-ui.ps1` (Windows) or
`sh packaging/build-ui.sh` (Linux); it ships as a separate artefact
(`forest-ui.exe` / `forest-ui`, ~80 MB — Qt is big, that is expected).

On **Linux** the same helper gives a real tray icon and menu (Qt talks to
StatusNotifier/**D-Bus**) and **draws the SAME notification card as on Windows**:
it is visible even without a notification service.

**Honest note about the menu on Linux.** The tray menu is our own window
(`src/ui_menu.py`, Qt.Popup, the card's style, animation), so it applies where
**we draw the menu**. When the tray goes through the system protocol
(StatusNotifier/AppIndicator, which `QSystemTrayIcon` sits on), the context menu
is drawn by the **desktop panel** (KDE Plasma, GNOME, XFCE): its fonts, paddings,
corner radii and animation — our style cannot apply there, exactly as with the
notification card, where Linux keeps the system notification. Our menu look
(colors, radii, row highlight, the 3 px lift, submenu slide-out) works on Windows
and wherever we draw the menu ourselves. Our own (non-system-protocol) path is
the same code on Linux (`src/ui_style.py` + `src/ui_menu.py`), but it could not be
checked on a live Linux desktop in this build (see "Verification limits").

If there is no graphical
session or the card cannot be shown (window failure), the fallback kicks in — a
system notification through `org.freedesktop.Notifications`, where the
**recovery steps and their millisecond durations are passed in the BODY**; the
Qt-free fallback uses `gdbus`/`notify-send`. The fallback can be forced with
`notify.card_on_linux: false`. The theme is read from the system settings
(`gsettings`), and the app icon (`forest-proxy-updater.png` — the same artwork) is
installed into `share/icons/hicolor/256x256/apps` and named in the `.desktop` file
(`Icon=forest-proxy-updater`).

The Linux packages declare the system libraries Qt needs (`libxkbcommon-x11-0`,
`libxcb-cursor0`, `libxcb-icccm4`, `libxcb-image0`, `libxcb-keysyms1`,
`libxcb-render-util0`, `libxcb-shape0`, `libxcb-xkb1`, `libgl1`, `libegl1` in the
`.deb`; the same libraries by SONAME in the `.rpm`; `libxkbcommon-x11`,
`xcb-util-cursor`, `xcb-util-wm`, `xcb-util-image`, `xcb-util-keysyms`,
`xcb-util-renderutil`, `libxcb` on Arch) — without them the Qt platform plugin
does not load and the helper does not start. The platform plugins themselves
(`xcb`, `wayland`) and the Qt libraries are **inside the bundle**. Where exactly
the packages were tested is stated in the package descriptions and in the docs
(see “Testing scope”).

### “Filters” in the menu

The menu has a **“Filters”** submenu with two checkable items:

| Item | Setting key | What it does |
|---|---|---|
| **RU direct** | `watch.ru_direct` | Russian domains and addresses go **around the proxy** (`geosite:category-ru` / `tld-ru` / `category-gov-ru` / `category-bank-ru` / `private` and `geoip:ru` / `geoip:private`). Toggling it regenerates the core config and restarts the core **immediately** |
| **Cloudflare bypass** | `blacklist.cloudflare_nodes` | Nodes whose addresses fall inside Cloudflare ranges are kept out of the pool (they are stubs that answer 204 but carry no data). Applied from the next pool pick |

The check marks reflect the **current** settings: the menu re-reads `config.json`
every tick, so if the program itself changed a setting, the tick follows. Both the
built-in menu and the helper behave identically: the helper writes
`filter_ru_on` / `filter_ru_off` / `filter_cf_on` / `filter_cf_off` into
`state/ui_cmd.json`, and the supervisor performs it through **the same code** as
the built-in menu (`TrayActions.set_ru_direct` / `set_cloudflare` →
`ensure_balancer`).

## Linux

The same program, packaged for Linux (x86_64): the proxy core is `xray`, service
management is systemd. **Nothing has to be dropped in by hand** — the Xray core
and both geo databases (`geoip.dat`, `geosite.dat`) are embedded in the single
executable, and Python is not required. The only dependency is `curl`.

Every package installs with **one command**:

| Format | Distributions | Install | Root |
| --- | --- | --- | --- |
| `.deb` | Debian, Ubuntu, Linux Mint | `sudo apt install ./forest-proxy-updater_1.2.0_amd64.deb` | yes |
| `.rpm` | Fedora, RHEL, Rocky, Alma, openSUSE | `sudo dnf install ./forest-proxy-updater-1.2.0-1.x86_64.rpm` | yes |
| `.pkg.tar.zst` | Arch, Manjaro, EndeavourOS | `sudo pacman -U ./forest-proxy-updater-1.2.0-1-x86_64.pkg.tar.zst` | yes |
| `.AppImage` | any glibc x86_64 | `./ForestProxyUpdater-1.2.0-x86_64.AppImage --install` | **no** |
| `.tar.gz` | any | `tar xzf forest-proxy-updater-1.2.0-linux-x86_64.tar.gz && cd forest-proxy-updater-1.2.0-linux-x86_64 && sudo ./install.sh` | yes |

Check it once the first selection cycle has finished (a few minutes — the
program is downloading and testing hundreds of public servers):

```sh
curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n' \
     https://www.gstatic.com/generate_204          # expect 204
systemctl status forest-proxy-updater.service
journalctl -u forest-proxy-updater -f
```

The packages install a **system** service, so autostart needs no login and no
`loginctl enable-linger`. The AppImage and `install.sh --user` install
`systemd --user` units instead and enable linger automatically. Removal is
handled by the package manager (`apt purge` / `dnf remove` / `pacman -Rns`) or by
`uninstall.sh` / `--uninstall`.

Full instructions: [`docs/LINUX-INSTALL.md`](docs/LINUX-INSTALL.md) (English) and
[`docs/LINUX-INSTALL.ru.md`](docs/LINUX-INSTALL.ru.md) (Russian) — install,
configuration, logs, verification, removal, and what to do when `systemctl --user`
is unavailable. Internals: [`docs/LINUX-ARCHITECTURE.ru.md`](docs/LINUX-ARCHITECTURE.ru.md)
(Russian). Packaging from source: [`packaging-linux/README.md`](packaging-linux/README.md).

## How it works

A single program runs several worker threads (a "supervisor"):

- **Source manager** — fetches subscription files from the fixed URLs in the
  config and, periodically, searches GitHub for fresh public server repos. A
  source is dropped if it has not changed for `sources.stale_days`.
- **Test pipeline** — candidates are de-duplicated, optionally pre-filtered by
  TCP reachability, then each candidate is raised as a **throwaway Xray
  instance** on a temporary port to measure RTT, then the best ones are speed
  tested, then the top ones are re-checked (RTT again + a YouTube reachability
  probe). Servers have to pass speed and jitter thresholds to be kept.
- **Balancer** — the servers that passed are written into **one** Xray config
  as a pool of outbounds, together with Xray's own `observatory` (health
  checks) and a `leastPing` balancer. Xray then sends new connections to a
  live, fast node **without restarting**, so the connection does not drop when
  a single node dies.
- **Watchdog ("watch")** — every few seconds it probes the local port through
  the core. If the port is dead or the core process is gone it rebuilds the
  pool in 1–2 seconds; if the probes keep failing it falls back to a full
  selection cycle. It also watches throughput and uses YouTube as a canary.
- **Guard** — a lighter periodic self-check that restores a connection from the
  stored servers (or does a full cycle as a last resort).
- **Update loop** — runs a full selection cycle on a schedule
  (`cycle.full_refresh_hours`).

When the balancer cannot be brought up, the program falls back to a single
server, and then to an **emergency "last good" pool** it saved earlier.

### Seamlessness: when the core is NOT restarted

Restarting the core means a short link gap, so it only happens when it really is
needed:

- **The new config is validated by the core before the running one is stopped** —
  `xray run -test -config <file>`. A broken config is never applied, so it cannot
  take the link down (if a core build does not support `-test`, the program falls
  back automatically to starting it on free ports).
- **Unchanged pool means no restart at all.** The program remembers the hash of
  the config the running core actually uses (`balancer_cfg_sha`) and the pool
  fingerprint (`balancer_sig`); if the freshly built config matches the running
  one, the core is not touched, even on `force=True`.
- **Pool freeze** (`watch.pool_freeze_s`, 3 h by default): while the core is alive
  and the link works, a re-computed top-10 does not reshuffle the config.
- **Restart only for a reason**: the active node is dead / the core is not
  answering, the pool really changed after the freeze expired, or the user
  pressed "Rebuild" in the tray.
- **The gap is measured and logged in milliseconds** — from stopping the old core
  to the new one accepting the client port, plus the time until data transfer is
  confirmed. The fixed 300 ms sleep is gone; the port is polled for release.

Log line template: `перезапуск ядра: РАЗРЫВ связи <ms> мс (порт принял
соединения), подтверждение данных <ms> мс, rtt=<ms> мс`.

The program saves the original system-proxy settings on its first intervention
and can restore them exactly.

## Key settings (from `config.example.json`)

Everything below can be tuned in `config.json`. Defaults are chosen to be safe
for a new user: the provider-specific switches are **off**.

> ### ⚖️ Terms of use
>
> The program provides access to **all** websites, including those blocked by
> network filtering in a given country or network. **You are responsible for
> complying with the laws of your jurisdiction** and with your provider's rules.
> By using the program you confirm that you accept these terms.
>
> Full text: [`docs/TERMS.md`](docs/TERMS.md) (Russian:
> [`docs/TERMS.ru.md`](docs/TERMS.ru.md)).

### General

| Key | Default | Meaning |
|---|---|---|
| `ui.lang` | `""` | Interface/CLI language: `""` = auto (system locale), `"ru"`, `"en"`. |
| `client.mode` | `"standalone"` | `standalone` (own core) or `v2rayn` (through installed v2rayN). |
| `client.socks_port` | `10808` | Local mixed SOCKS/HTTP port of the core. |
| `client.torrent_port` | `10820` | A second local SOCKS port for a torrent client (routed like normal traffic). |
| `client.listen` | `"127.0.0.1"` | Interface the core listens on. Local only by default. |
| `client.manage_system_proxy` | `true` | Whether the program sets/clears the Windows system proxy. |
| `client.core` | `""` | Explicit path to `xray.exe` (standalone). Empty = auto-discovery. |
| `client.dir`, `client.exe`, `client.db_rel`, `client.cfg_rel`, `client.core_rel` | v2rayN defaults | Where v2rayN's executable, database and core are, in `v2rayn` mode. |
| `cycle.full_refresh_hours` | `0.5` | How often a full refresh (fetch sources) runs; between them only the existing servers are re-tested. |
| `limits.max_servers` | `100` | Cap on how many servers are kept/ranked. |

### Testing

| Key | Default | Meaning |
|---|---|---|
| `test.port_start` | `10810` | First port used for throwaway test cores. |
| `test.workers` | `8` | Parallel test threads. |
| `test.rtt_timeout` | `6` | Per-RTT timeout (seconds). |
| `test.rtt_repeats` | `2` | RTT measurements averaged per server. |
| `test.speed_timeout` | `15` | Speed-test timeout (seconds). |
| `test.speed_bytes` | `5242880` | Bytes (5 MB) to download in the speed test. |
| `test.speed_rounds` | `1` | Speed-test rounds. |
| `test.min_speed_kbps` | `1000` | Minimum speed (KB/s) to keep a server. |
| `test.max_rtt_ms` | `1500` | Maximum acceptable RTT. |
| `test.max_jitter_ms` | `800` | Reject servers whose RTT jitter is higher. |
| `test.top_for_speed` | `60` | How many best-by-RTT servers get a speed test. |
| `test.max_candidates` | `400` | Cap on candidates to test per cycle. |
| `test.tcp_prefilter` | `true` | Drop `host:port` with no TCP reachability before testing. |
| `test.min_pool` | `60` | Pool target size. |
| `test.reserve_min` | `30` | Minimum final pool size; below it, servers are added back by RTT. |
| `test.reserve_verify` | `20` | How many reserve servers get a speed verification. |
| `test.final_verify_top` | `60` | How many servers get the final RTT + YouTube re-check. |
| `test.gate_urls` | `[]` | Extra domains that a server must be able to reach. **Empty = off.** See security section. |

### Watchdog and balancer

| Key | Default | Meaning |
|---|---|---|
| `watch.balancer_mode` | `true` | Use Xray's native balancer pool (recommended). |
| `watch.balancer_pool_n` | `10` | How many servers sit inside the running core's pool. |
| `watch.check_interval_s` | `10` | Watchdog probe interval. |
| `watch.probe_timeout_s` | `4` | Watchdog probe timeout. |
| `watch.max_strikes` | `3` | Consecutive failures before the pool is rebuilt. |
| `watch.probe_retries` | `2` | Extra probe attempts before counting a failure. |
| `watch.strict_check_every_s` | `300` | How often a strict ("real data + TLS") probe runs. |
| `watch.pool_refresh_s` | `600` | How often the pool inside the core is refreshed. |
| `watch.pool_freeze_s` | `10800` | Do not rebuild a pool that is working for this long. |
| `watch.speed_check_every_s` | `300` | Throughput check interval. |
| `watch.speed_min_kbps` | `1500` | Below this throughput the pool is rebuilt. |
| `watch.min_speed_kbps` | `200` | Minimum server speed to be allowed into the pool. |
| `watch.youtube_check_every_s` | `300` | YouTube-canary interval; 3 failures in a row trigger a full cycle. |
| `watch.hotcache_n` | `20` | How many verified servers are kept in the fast-failover cache. |
| `watch.hotcache_ttl_s` | `3600` | Lifetime of a hot-cache entry. |

### Sources and selection

| Key | Default | Meaning |
|---|---|---|
| `sources.fixed_urls` | list | Fixed subscription URLs, each with an `every_minutes` refresh interval. |
| `sources.search_queries` | list | GitHub search queries used to discover new sources. |
| `sources.seed_repos` | list | Fallback repositories if discovery finds nothing. |
| `sources.known_files` | list | File names sniffed inside a repository. |
| `sources.stale_days` | `30` | Drop a source that has not changed for this many days. |
| `sources.rediscover_hours` | `12` | How often the GitHub search runs. |
| `diversity.per_subnet_cap` | `1` | Max servers from one /24 subnet. |
| `diversity.min_pool_after_cap` | `12` | If the cap shrinks the pool below this, the cap is relaxed. |
| `diversity.max_cap_relax` | `4` | How far the cap may be relaxed. |
| `activation.prefer_current_if_healthy` | `true` | Keep the current server if it is healthy and competitive. |
| `activation.max_restart_wait_s` | `40` | Max wait for a core restart during activation. |

## Command-line interface

Run the program (`.exe`, or `python src/v2ray_updater.py` from source) with one
of these:

```
--install     lay out under %LOCALAPPDATA%\ForestProxyUpdater (the .exe itself is
              copied there), create the Start Menu shortcut and the watchdog
              task (autonomous supervision, on by default), run the first cycle
--uninstall   stop the core, restore the system proxy, remove the supervision task,
              the autostart entry (if any), the shortcut, the installed .exe and
              the program directory
--run         normal run: the supervisor with its worker threads (until stopped)
--ensure      start the supervisor if it is not running (this is what the watchdog
              task calls); quiet, log only, never shows a window, never starts a
              second copy
--watchdog on|off|status
              autonomous supervision: create+enable the task, remove it, or report
              its honest state (setting, task, last run, is the program running)
--once        one full server-selection cycle            (alias: --auto)
--guard       one quick self-check / recovery pass
--status      show the current state (including the supervision line)
--selftest    run the built-in test suite
--watch       run only the watchdog (one process)
--dry-run     run a cycle without writing anything or starting the core
--config PATH use a specific config.json
--help        show the built-in help
```

Background work runs without console windows (`CREATE_NO_WINDOW` / `pythonw`
semantics). The watchdog task is registered as hidden, runs as the current user
and is configured **not to start a new instance while it is already running**.

## Files and logs

Everything the program writes lives under `%LOCALAPPDATA%\ForestProxyUpdater`
(override with `FOREST_ROOT` or `paths.root` in the config):

| Path | What it is |
|---|---|
| `config.json` | The working configuration (created from `config.example.json` on first run). |
| `logs/main.log` | Main log (UTF-8). |
| `state/servers.json` | Own server store (standalone mode). |
| `state/forest_xray.json` | Config of our own core. |
| `state/forest_xray.log` | Output of our own core. |
| `state/state.json` | Internal state: hot cache, blacklist, balance signature, etc. |
| `state/subscription_base64.txt` | Current pool as a base64 subscription (for manual import into any client). |
| `state/proxy_orig.json` | The system-proxy state saved before the first change. |

Where to find problems is described in [`docs/FAQ.md`](docs/FAQ.md).

## Security and limitations

Read this before running the program. Nothing here is a bug — it is how the
program works, and you should know it.

**The program changes the Windows system proxy.** It writes
`ProxyEnable`/`ProxyServer` under
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Internet Settings`
and points them at its local port (`127.0.0.1:10808` by default) so that
system-wide traffic goes through the selected server. This is a user-level
setting (no administrator rights required), and it affects every application
that follows the Windows system proxy. It saves the original value on the first
change and restores it on `--uninstall`.

**The program manages a process.** In standalone mode it starts and stops
`xray.exe`. It only ever kills *its own* core (identified by its own config
name); it does not touch other `xray.exe` processes.

**The `.exe` is not code-signed.** Windows SmartScreen and some antivirus
products will warn about an unsigned program that changes proxy settings and
launches another executable — this is expected for a program of this kind. If
you do not trust the build, do not run it: build it yourself from source (see
[`docs/BUILDING.md`](docs/BUILDING.md)) and read the code first. The source is
GPL-3.0 and has no third-party dependencies.

**You are routing your traffic through third-party servers you did not
choose.** The program pulls public server lists from external sources on the
internet. It tests the servers, but **it does not and cannot verify the
operator of a server**. Whoever runs a proxy node can see and modify the
traffic that goes through it. Do not send sensitive traffic through servers you
do not trust. The program does not encrypt anything itself — it relies on the
transport security (TLS/REALITY) configured by whoever published each server.

**Free servers are unstable.** Public free nodes go down, get blocked, throttle,
or disappear constantly. Expect the connection to be uneven, to re-select
often, and sometimes to have no working server at all. This is a property of
the servers, not of the program. For a stable, fast connection, use a paid
provider.

**Extra options exist for particular network conditions, and enabling them is
your responsibility.** `test.gate_urls` (empty by default) is a "gate": extra
domains a server must reach, used to reject servers that cannot reach a specific
site. Turning it on changes how your traffic is routed on *your* network — do it
on your own responsibility.

**Intended use.** The program is meant for lawful use — for example, keeping a
working connection to servers you are entitled to use. You are responsible for
how you use it and for complying with the laws and terms of service that apply
to you. The program is provided under GPL-3.0, **without any warranty**.

## FAQ

Short answers; the full list is in [`docs/FAQ.md`](docs/FAQ.md).

- **"It can't find any servers."** The first run can need a few minutes and a
  working internet connection; free lists are hit-and-miss. Check
  `logs/main.log`. See [`docs/FAQ.md`](docs/FAQ.md).
- **"There's no internet after I started it."** The program points the system
  proxy at its local port; if no server is up, the system proxy has nothing to
  talk to. Run `--uninstall` to put the proxy back, or see the FAQ.
- **"My antivirus complains."** The build is unsigned. See the security
  section and [`docs/FAQ.md`](docs/FAQ.md).
- **"How do I remove it?"** Run `--uninstall`.
- **"Where are the logs?"** `%LOCALAPPDATA%\ForestProxyUpdater\logs\main.log`.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how it is built inside.
- [`docs/BUILDING.md`](docs/BUILDING.md) — building the `.exe` and releasing.
- [`docs/FAQ.md`](docs/FAQ.md) — common problems and answers.
- [`docs/LINUX-INSTALL.md`](docs/LINUX-INSTALL.md) — installing on Linux:
  `.deb` / `.rpm` / Arch / AppImage / `.tar.gz`, configuration, removal
  (Russian version: [`docs/LINUX-INSTALL.ru.md`](docs/LINUX-INSTALL.ru.md)).
- [`docs/TERMS.md`](docs/TERMS.md) — terms of use and disclaimer (Russian:
  [`docs/TERMS.ru.md`](docs/TERMS.ru.md)).
- [`packaging-linux/README.md`](packaging-linux/README.md) — building the Linux
  packages from source.

## License

GPL-3.0-only. See [`LICENSE`](LICENSE).

Copyright (C) 2026 FOREST.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. This program is distributed in the hope that it will be useful, but
**WITHOUT ANY WARRANTY**; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE.
