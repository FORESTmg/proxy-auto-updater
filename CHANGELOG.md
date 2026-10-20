# Changelog

All notable changes to **FOREST's Proxy Auto-updater** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] — 2026-10-21

### Fixed

- **YouTube refused to load while "the connection was fine".** The gate that
  accepted a node was a single `curl` to one domain and the balancer probe was
  `gstatic`, which a node that tears down TLS to YouTube passes happily. Nodes
  were therefore admitted, `leastPing` picked them, and YouTube answered `000`
  in ~40 ms. A node is now verified by a **real handshake to several different
  domains** (`test.tls_urls`: youtube, gstatic, wikipedia, ytimg, example.com;
  `tls_min_ok` = 3 of 5, `tls_max_s` = 2.5 s) and the verdict separates the
  **reason** — `reset` / `unreachable` / `mitm` / `few` (hard reject) versus
  `slow` (a pool-relative question, not an absolute one). The balancer probe is
  YouTube (`watch.balancer_probe_url`), and the reserve path runs the same
  YouTube gate, so a "mute" node cannot slip into the pool through the reserve.
- **The pool shrank on every re-selection (10 → 5 → 3 → 2).** A slow handshake
  was judged against a fixed 2.5 s threshold, so nodes at 2.8–3.1 s were dropped
  one round after another. A slow node is now compared with the **median of the
  pool** (`tls_slow_factor` 1.8, `tls_slow_margin_s` 1.0) and only clear
  outliers are dropped; `tls_hard_s` 10 s stays an absolute reject. In addition
  `watch.pool_floor` keeps the composition from dropping below the floor.
- **The single probe could not see a burst.** A browser opens dozens of
  connections at once; one probe sees none of that, so a bad node stayed in the
  pool until the user noticed. There is now a **parallel probe** (`burst_probe_n`
  = 12 requests every `burst_probe_every_s` = 30 s, plus 16 requests × 2 rounds
  right after the pool is applied), **attribution** of the failures to specific
  nodes, and a **ban** for the culprits (`ban_bad_minutes` = 30).
- **~90 re-selections per hour with no real fault.** A `github.com` request that
  takes 5–15 s is a property of the site/route, not of the node, but it used to
  trigger a re-selection; and when attribution found no culprit in the pool the
  cycle simply repeated 30–40 s later. `github.com` is now a **slow URL**
  (`burst_slow_urls`, stall 15 s) that never starts a re-selection, an
  unattributed burst **backs off** 5/10/20/30 min (`burst_backoff_s`), and a
  re-selection is not started for 120 s after a pool rebuild
  (`burst_cooldown_after_rebuild_s`). Measured over an hour of live traffic:
  **0 re-selections, 0 pool rebuilds, 0 core restarts** (was ~90).
- **`tests`/state files and probe output were read with the machine's default
  encoding.** Every state, config and probe read/write that this release touches
  specifies **UTF-8** explicitly, so the Cyrillic status is not mangled by the
  code page of the host.
- **A burst guard that cannot act is now visible.** `burst_guard`,
  `attribute_bad_nodes`, `ban_bad_nodes` and `cycle_running` are exported by both
  the Windows and the POSIX back end and are pinned by `--selftest` (the POSIX
  side used to lack them entirely, so the Linux build silently had no parallel
  probe).

### Changed

- **One action = one notification card.** A single "refresh now" click produced
  a queue of cards (ok → error → busy → ok → off) because each stage of the
  action raised its own notification. Cards are now **coalesced in a 2.5 s
  window** (`CARD_COALESCE_MS`), the process card marks its own time so the
  command-reply card cannot follow it, and the same command reply is shown once
  (previously every tick under 6 s re-showed it). The full series is still
  available through the explicit **`--show-all`** demo mode; the selftest pins
  both behaviours.
- **"Обновить серверы сейчас" really is a full cycle.** The tray command used to
  run the quick path with a frozen pool, so the button changed nothing visible.
  It now runs with `force_discovery` (fresh lists) **and** `pool_force` (the new
  pool is applied even when the composition looks unchanged), the same way as
  the scheduled cycle.
- **Balancer: `leastLoad` + `burstObservatory`** (2 s timeout, 15 s interval,
  sample 2) instead of `leastPing`, so a stalling or dead node leaves the
  rotation within seconds. `watch.balancer_strategy` switches back to
  `leastPing` without a rebuild.

### Verified

- Live connection on the released build: **7 of 7 sites** reachable with **0
  failures in 840 requests**, YouTube **3/3**, and `204` through the local HTTP
  proxy, the SOCKS proxy and the LAN address.
- One hour of the same traffic: **0 re-selections, 0 pool rebuilds, 0 core
  restarts**; `github.com` no longer causes a single trigger; the pool stays
  stable.

## [1.3.0] — 2026-10-18

### Added

- **The notification card is now the approved design, 1:1** (`docs/notify-mockup.html`
  is the specification). The card is 392 px wide, radius 12, a 3 px state stripe on
  the left, a 34×34 icon tile, a header with a spinner on the right, a **block of
  stages with each stage's time in milliseconds**, a **time bar**, and a footer.
  State colours are the mockup's own table (ok `#41c26d`, error `#dc4f55`,
  busy `#e5bf37`, off `#9497a0`). The stages come from the original script
  (`D:\v2ray-auto-updater\tray.ps1`): *Обрыв обнаружен (пульс порта)*,
  *Переключение на новый узел*, *Ядро приняло соединения*, *Данные подтверждены*,
  and the final line is *восстановлено за 1.85 с · окно закроется через 4 с*.
- **The user's own icon inside the notification.** The card draws the same
  `packaging/app.ico` artwork through the very same code path as the tray icon
  (`tray_icon.state_ico` → pixel-identical drawing + the state badge in the
  corner). `forest-ui --selftest` compares the pixmap the card actually paints
  against the tray icon raster: **1024/1024 pixels identical (100 %)**.
- **A measurement of the smoothness, not a promise.** The card drives its frames
  on an absolute 60 fps grid and records the real intervals between paints.
  Measured on the live PC: **average 60.1 fps, 388 frames, worst frame 25.1 ms
  (1.5 frame periods), 0 dropped frames after the entrance, 0 stalls** (frames
  longer than two periods). Only transform (window position) and opacity change
  between frames; the static layer (background, border, stripe, icon tile,
  artwork, title, subtitle, bar track, footer) is rendered **once** into a
  `QPixmap`, so there is nothing for Qt to repaint in parts — no flicker.
- **Focus proof.** The card never becomes the foreground window: the selftest
  samples `GetForegroundWindow` during the whole animation and asserts the card's
  own HWND never appears in the samples (measured: 0 times), and that the user's
  foreground window does not change.
- **The same card on Linux**, with a fallback to a system D-Bus notification when
  there is no graphical session or the card cannot be shown. The fallback carries
  the stages with their times in the body of the system notification.
  `notify.card_on_linux` (default `true`) switches it off.
- **The Qt-free fallback (`src/notify.py`) now shows stages and the recovery
  time too.** Its layered Win32 card grew a stage block (`✓ Обрыв обнаружен
  (пульс порта)   50 мс`) and a footer (`восстановлено за 1.85 с (1850 мс) ·
  окно закроется через 4 с`); `tray._notify_details` builds them from the
  watchdog state, so the built-in tray shows the same content as the Qt helper.
- **Package dependencies for the Qt helper.** The Qt platform plugins (`xcb`,
  `wayland`, `offscreen`) and the Qt libraries travel **inside** the bundle; the
  system libraries are now declared in all three package formats. `.deb`:
  `libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0,
  libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xkb1, libgl1,
  libegl1`. `.rpm`: the same libraries **by SONAME** (`libxkbcommon-x11.so.0()(64bit)`
  …) so the names do not depend on the distribution. Arch: `libxkbcommon-x11,
  xcb-util-cursor, xcb-util-wm, xcb-util-image, xcb-util-keysyms,
  xcb-util-renderutil, libxcb` (+ `libglvnd`/`mesa` as optional). Verified by
  measurement: without these libraries `QT_QPA_PLATFORM=xcb forest-ui` does not
  start at all (*libxkbcommon-x11.so.0: cannot open shared object file*); after
  installing exactly this set the helper starts and draws the card.
- **Honest testing-scope notices in the Linux packages and docs.** The `.deb`
  description, the `.rpm` `%description`, the Arch `PKGBUILD` (`pkgdesc` and the
  install script message), the README shipped inside the AppImage and the portable
  `.tar.gz`, `README.md`/`README.ru.md` and `docs/LINUX-INSTALL.md`/`.ru.md` all
  state that the package was tested in WSL and the GUI menu/notifications were
  **not** verified on a live desktop. The Windows build carries no such caveat —
  it is tested on a live PC.
- **The tray menu is one menu again — its own single window with submenus that
  open on hover.** The menu is drawn by `src/ui_menu.py` / `src/ui_style.py` (a
  frameless `Qt.Popup` window, *not* a `QMenu`), so the whole menu — panel,
  rows, the state stripe, the highlight and the submenus — uses one style table
  and one text layer. A submenu opens **on hover** after the style delay
  (`ui_style.SUB_HOVER_OPEN_MS`, 150 ms; measured on the live PC: **159-163 ms**),
  closes through a **280 ms** grace window when the cursor leaves (**283 ms**
  measured), a plain pass over an item with a submenu does **not** open it,
  moving to a different item with a submenu **reuses the same panel**
  (**4.7-6.0 ms**), and returns to the previous item cancel the close. Keyboard
  behaviour is unchanged (Right opens, Left/Esc close, arrows do not open).
- **A menu highlight that keeps up with the cursor.** Moving the mouse moves the
  highlight **in the same input event** and fades it in with a short 50 ms tempo
  (`HL_MOUSE_MS`), while keyboard navigation keeps the smooth 180 ms glide
  (`STEP_FADE_MS`). Measured on the live PC against the previous build: the
  highlight reaches the hovered row in a median of **12-15 ms** instead of
  700-990 ms, frames showing an intermediate highlight: **0** (was 467-522),
  frames with two highlight bands at once: **0**, and the highlight band is on
  the right row in **6 of 6** steps on both monitors (100 % and 150 % scaling).

### Changed

- The notification card's entrance is **340 ms** with the mockup's
  `cubic-bezier(.16,.84,.28,1)` and the exit is **260 ms** with `ease-in`; the
  slide is 420 px, the spinner turns once per 0.85 s, and a stage counts as passed
  when the bar is 6 % past it — every one of these numbers is read **from the
  mockup file** by the selftest and compared with the code.
- The final state is applied **in place**: when the supervisor reports the end of
  the same process, the card that is already on screen changes to the final
  appearance instead of sliding in a second time (as in the mockup).
- `notify.width` default is 392 px (matches the card).
- The Arch `PKGBUILD` URL now points at the real repository.
- **The whole tray menu (and its submenus) is drawn from one style table**
  (`src/ui_style.py`): panel padding, row height, stripe, radii, colours, the
  hover timings and the shadow are single constants instead of inline numbers, so
  the main menu and a submenu cannot drift apart. Numbers taken from
  `docs/menu-mockup.html` are pinned by the selftest (`--menu-shots`,
  `--selftest`, `src/ui_style.py --selftest`). The menu item for the direct-RU
  filter reads **«РФ — напрямую»** (`tray.menu.filter_ru`) and the tick follows
  the live setting.

### Fixed

- **A frame could be up to 25 ms instead of 16.7 ms (uneven pacing)** because the
  frame timer only checked the grid every half period; the check is now four times
  per period and the frame is drawn on the nearest tick after its `n/60` deadline.
- `forest-ui --selftest` no longer measures a "focus stolen" that was really the
  terminal taking focus: the check is now "the card's HWND never becomes
  foreground" plus "the user's foreground window does not change".
- **Right-clicking the tray icon could crash the helper and the menu never
  appeared.** The activation handler built the menu inside a try-block that
  re-raised the error from the tray callback, so a failing menu took the whole
  helper down (`RuntimeError: не удалось открыть своё меню (повтор)`); the
  handler now recovers, writes the traceback to `logs/crash.log` and the menu is
  shown on the next activation. Proved on the live installation with **5 real
  right-clicks in a row** (`SendInput`, the icon found by UIA in the notification
  area): **5/5 opened the menu, 0 crashes**, both helper pids unchanged, Esc and
  a click outside close it.
- **The tray icon could disappear and the helper never came back.** `ui_helper.
  _pid_alive()` treated a successful `OpenProcess` as a live process, but that
  call also succeeds for a finished process whose handle is not released yet: a
  stale pid 31800 in `state/ui_helper.json` made the supervisor believe the helper
  was running, so it never started the real one (no icon in the tray). Liveness is
  now `OpenProcess` + `WaitForSingleObject(h, 0)` (`WAIT_TIMEOUT` = alive,
  `WAIT_OBJECT_0` = finished), `_pid_image()` / `_is_our_helper()` only accept a
  pulse if **our** helper file runs under that pid, and in a frozen build
  `we_are_helper()` additionally requires the executable's own name
  (`forest-ui.exe`), so an inherited `FOREST_UI=1` can no longer make the
  supervisor consider itself the helper.
- **`state/status.txt` was written with `?` instead of Cyrillic.** The
  human-readable status was taken from the ASCII-safe stdout buffer
  (`safestdio.say` deliberately replaces non-ASCII outside a console); the report
  is now collected from the original strings and the file is written in UTF-8,
  while stdout stays ASCII.

## [1.2.2] — 2026-10-17

### Fixed

- **The tray icon was not the user's artwork.** `tray_icon.user_state_ico` painted
  the connection colour **over** the user's `app.ico` at 90 % blend, so the icon
  collapsed into a single flat coloured tile — the drawing (cyan ring, green
  swoosh, red chain, white plane) was gone. The artwork is now copied **without a
  single altered pixel** and the state is shown by a small **badge in the
  bottom-right corner** (`tray_icon.mark_state`). Proved pixel by pixel in
  `forest-ui --selftest`: 77.8 % of the 32×32 pixels are byte-identical to
  `app.ico`, and the badge reads 100 % at the badge centre.
- **The build could silently replace the icon.** `build.ps1` drew a *generated*
  `app.ico` (`make_icon.py`) whenever the file was missing, and `make_icon.py`
  overwrote an existing file without asking. The build now verifies `app.ico`
  against the hash recorded in `packaging/app.ico.sha256` and **stops** if the
  file is missing (`-GenerateIcon` is the explicit opt-in); `make_icon.py` refuses
  to overwrite without `--force`; the Linux builds run `packaging/check_icon.sh`.
- **The notification window had no visible animation and no step times.** The
  entrance was 260 ms and there was no hold indicator. The card now has three
  clearly visible phases — entrance (48 px slide + fade, **350 ms**), hold (a
  spinner turns by the icon and the progress bar is eased), exit (slide back +
  fade, 260 ms) — at 60 fps without flicker and without ever taking focus. Steps
  are rendered **with their duration in milliseconds** and the final line carries
  the **link-recovery time in ms** as well (`восстановлено за 1.85 с (1850 мс)`).
- **The "Filters" section was missing from the Qt menu.** `forest_ui` now shows a
  **Filters** submenu with two checkable items (**RU direct**, **Cloudflare
  bypass**); the tick follows the live settings and the click goes through
  `state/ui_cmd.json` to the supervisor (`filter_ru_on/off`, `filter_cf_on/off`),
  which applies it through the very same code as the built-in menu.
- **`Unhandled exception in script` modal window on the desktop.** The helper's
  `--selftest` printed `✓` (U+2713) to stdout; a windowed PyInstaller build has no
  console and its "console encoding" on Windows is cp1251, so `UnicodeEncodeError`
  escaped and PyInstaller showed a **modal window**. Fixed at three levels
  (`src/safestdio.py`): stdout/stderr are switched to `utf-8` + `errors="replace"`
  (a missing stream gets a sink), stdout is **ASCII-only** (non-ASCII becomes `?`)
  while the human-readable report goes to UTF-8 files, and any unhandled exception
  is written to `logs/crash.log` with exit code 70 — `disable_windowed_traceback`
  is on in every spec, so a modal window is impossible.
- **`forest_ui` crashed on import when Qt was absent** (`class Toast(QtWidgets.
  QWidget)` → `NameError`) instead of exiting quietly with "Qt is unavailable";
  stub classes now let the module import.
- `src/tray_check.py` expected `state=switch/restoring` to give the `ok` icon; the
  code has returned `busy` (amber, "switching") for a long time — the stale check
  was corrected (it was red before this release).
- `log.py` printed every line to stdout with `print()`; it now goes through the
  safe printer (stdout ASCII, full text always in the log file).

### Added

- **`src/safestdio.py`** — safe stdout/stderr, ASCII-only `say()`, UTF-8 report
  files, `crash_log()`, `install_excepthook()` and `guarded()` (the entry-point
  wrapper). Used by both the main program and the helper.
- **`packaging/app.ico.sha256`** and **`packaging/check_icon.sh`** — the icon pin
  and the Linux-side check.
- **`state/ui_selftest.txt`** — the full UTF-8 self-test report (stdout stays
  ASCII, as a windowed build requires).
- New self-test sections: the icon pixel proof, the Filters submenu and its
  commands, the animation phase/position/opacity time series, the notification
  text in milliseconds, and the crash-safety checks (7 and 15).

### Changed

- **Linux**: the notification has no window of its own, so the **recovery steps
  and their millisecond durations are passed in the BODY** of the system
  notification; the app icon (`forest-proxy-updater.png`, the same artwork) is
  installed into `share/icons/hicolor/256x256/apps` and named in the `.desktop`
  file. `--status` prints an ASCII summary and the full report goes to
  `state/status.txt` (UTF-8).

## [1.2.1] — 2026-10-17

### Fixed

- **Two copies of the program and two helpers (two tray icons) could run at
  once.** The single-instance guard was a *marker file with a process id* plus a
  process-list scan, and both sides of that heuristic were wrong on a live
  machine:
  * starting two copies **at the same instant** ended with **zero** survivors —
    each copy saw the other `--run` process in the list and both refused to
    start;
  * when the recorded pid was judged dead (a killed/restarted supervisor, a
    dead-recorded marker) a new launch **took the marker over** while the old
    supervisor kept running, and its `watch`/`tray` threads launched a **second
    helper** as well — the log shows two supervisors and two helper launches at
    the same second, hence two tray icons and two supervisors competing for
    `state/state.json` (`WinError 32`, `state.json.tmp`).

### Added

- **`src/instancelock.py` — a real single-instance lock.** An exclusive file lock
  held for the whole life of the process: `msvcrt.locking` (`LockFile`) on
  Windows, `fcntl.flock` on POSIX. The kernel releases the lock together with the
  process, so there are no stale locks and no start-up race. Locking is
  non-blocking: a second attempt gets an immediate refusal. The file also carries
  JSON with pid/exe/root/argv, but that is **diagnostics only** — every decision
  is made by the lock itself. `--selftest` proves exclusivity with an independent
  attempt (a forked child on POSIX, a second handle on Windows) and checks that a
  dead owner leaves no held lock.

### Changed

- **`watchdog.py`** — `state/supervisor.lock` is now that lock (the name and the
  diagnostics JSON stay, so `--status` and the docs keep working). `claim_supervisor`
  makes exactly one atomic decision — the lock — and no longer scans the process
  list to decide whether a second copy is needed. `find_live_supervisor`,
  `_marker_live_pid` and `watchdog_report` report the pid from the lock (with the
  old marker/scan path kept only as a transition/compat path), and the process
  scan additionally requires the process to belong to *this* installation (its
  root or config path must appear in the command line), so installations, the
  sandbox and the self-test can no longer see each other.
- **One rule for self-control.** `run_supervisor` marks its process
  (`FOREST_SUPERVISOR=1`); `watchdog.ensure()` refuses to start anything from
  such a process, and `_spawn_detached()` refuses as a second line of defence.
  `--ensure`, the Task Scheduler action and every internal caller therefore share
  one rule: **a live lock means do nothing**; the start-up itself is serialised by
  a short `state/ensure.lock` (also a real lock, no stale-age heuristics).
- **`ui_helper.py` / `forest_ui.py` / `tray.py` — one helper, one tray icon.** The
  helper now holds an exclusive lock (`state/forest-ui.lock`) for its whole life,
  which it takes before building the interface; `ui_helper.running()` decides by
  that lock (the heartbeat JSON is only used to show the pid), `ui_helper.ensure()`
  is serialised by a short lock and never launches a copy from inside a helper. A
  second helper now exits quietly instead of adding a second icon. The built-in
  (reserve) tray icon no longer competes with the helper: the tray thread waits up
  to 20 s for the helper — which comes up a second or two after the supervisor —
  and then does not create its own icon at all, and if the helper appears later it
  removes its own icon (`tray-handover`).
- **Linux**: `posix_platform.ensure()` respects the same supervisor lock, and the
  POSIX self-test gained lock checks (`--selftest` covers the lock on both
  platforms).
- Documentation (README, README.ru, ARCHITECTURE, ARCHITECTURE.ru) explains the
  lock and the three properties it guarantees.

## [1.2.0] — 2026-10-17

### Added

- **Indication is now exactly the composition of the previous add-on.** The old
  setup (`D:\v2ray-auto-updater`, switched off but kept for rollback) is the
  reference: its contents, texts and colour codes were ported verbatim into one
  place (`src/status_info.py`) and are used by **both** interfaces (the built-in
  icon and the Qt helper), so nothing is lost and the two can never drift apart:
  * the icon tooltip keeps the four reference fields in order —
    `FOREST <link state> | <N> servers | <RU route> | watch OK` (the previous
    version always printed “10 servers”, a hard-coded default — the field is
    kept, the number is now the real pool size);
  * the indication card keeps the reference title, subtitle, percentage, the
    step list **with per-step times**, and the footer, including
    **the link recovery time** (`восстановено за N.NN с · окно закроется через
    4 с`, `связь подтверждена · N.NN с`) — recovery time is now also persisted
    (`last_restore_s`/`last_restore_ts`) so it stays readable in the menu after
    the card is gone;
  * six icon states with the reference colours: `ok` (57,194,109), `off`
    (132,132,140), `switch` (240,180,41), `noanswer` (240,180,41), `down`
    (229,72,77), `restoring` (79,140,255);
  * the menu keeps the reference items (open log, open status, enable/disable the
    proxy by hand, exit) and adds what cannot be left out (supervision,
    notifications, theme, refresh, about).
  What changed is only the smoothness (60 fps) and the look (our light/dark
  palette, the user's own icon with the state colour inside).
  Verified side by side by running the reference read-only (its modules imported
  in a throwaway state directory — the live proxy, ports 10808/10820 and the
  registry are untouched): every title, subtitle and colour matches, the field
  lists are identical.
- **Interface helper `forest-ui` (Qt/PySide6, LGPL-3.0).** The interface is now a
  **separate program** shipped as its own artefact, so the main program stays
  small and dependency-free. It draws the tray icon (your `app.ico` with the
  connection colour painted **inside** the picture — four distinct states,
  readable on light and dark taskbars), a **menu with submenus** (status,
  supervision on/off, notifications on/off, **theme: follow system / light /
  dark**, refresh now, test notification, open log, about, quit) and
  **notifications** as a frameless translucent card (rounded corners, shadow,
  your icon) sliding in and fading out at **60 fps** that **never takes focus**.
  The helper **does not touch the core**: a menu item is written to
  `state/ui_cmd.json` and performed by the supervisor through exactly the same
  code as the built-in menu; state comes back through `state/tray_status.json`
  and a heartbeat in `state/ui_helper.json`. Settings: `ui.helper` =
  `auto`|`on`|`off`, `ui.helper_exe`. Build: `packaging/build-ui.ps1`,
  `packaging/build-ui.sh`, spec `packaging/forest-ui.spec`.
- **Interface fallback, honestly.** No helper, a crashed helper, no system tray —
  the built-in ctypes icon and the built-in notifications (`src/notify.py`) work
  as before; the built-in tray waits in reserve while the helper is alive and
  takes over within seconds after it stops, and notifications are only shown by
  whichever side owns the interface (never twice).
- **Themes (light/dark) as one source of truth** — `src/theme.py`: palettes for
  the notification card, the tray icon and the menu styling (QSS comes from the
  same palette), automatic detection (Windows registry / `gsettings`, with
  `FOREST_THEME`, `GTK_THEME`, `COLORFGBG` as fallbacks) and a `ui.theme`
  override. State colours are checked against WCAG (minimum contrast 3.36:1).
- **Notifications (`src/notify.py`, Windows)** — layered window drawn with a
  single `UpdateLayeredWindow` per frame (no flicker at all, no `WM_PAINT`, no
  child controls), shown with `SW_SHOWNOACTIVATE`/`WS_EX_NOACTIVATE` so the
  active window keeps focus, frames scheduled on a **high-resolution waitable
  timer** on an absolute timeline (no drift) — measured: **60.0 fps average,
  worst frame 17.4 ms, 0.0 ms CPU in idle, focus unchanged**.
- **Helper keep-alive.** `--install`, the scheduled task (`--ensure`, every
  minute) and the supervisor (every 30 s) all call the same `ui_helper.ensure()`,
  so a crashed helper comes back by itself and a second copy is never started
  (`state/ui_helper.json` plus a live-process check).
- **Linux**: the same helper provides a real tray icon and menu (StatusNotifier/
  D-Bus); notifications in the helper go through `org.freedesktop.Notifications`
  (the fallback path in `posix_platform.py` now uses the same `gdbus` →
  `notify-send` chain with the app icon and urgency); packages install the icon
  and `.desktop` files (`forest-proxy-updater.desktop`, `...-ui.desktop`).
- **Autonomous supervision — the program brings itself back.** `--install` now
  registers a **Task Scheduler task** `ForestProxyUpdater-Watchdog` (hidden, runs
  as the current user, `MultipleInstancesPolicy=IgnoreNew`) with a logon trigger
  and a repeat-every-5-minutes trigger, whose action is
  `ForestProxyUpdater.exe --ensure`. It **replaces** the old `HKCU\...\Run`
  autostart entry (removed by `--install`), so exactly one mechanism brings the
  program up.
- **`--ensure`** — a quick check (own marker file + live-process scan) that
  launches `--run` **detached from the calling process** (`DETACHED_PROCESS` +
  `CREATE_BREAKAWAY_FROM_JOB`, own process group) when no supervisor of *this*
  installation is alive. It refuses to create a second supervisor, writes only to
  the log and never shows a window.
- **`--watchdog on|off|status`** and a `watchdog` config section
  (`{"enabled": true, "interval_min": 5, "task_name": "..."}`, on by default).
  `--watchdog off` deletes the task and the program honestly stops restarting
  itself; `--status` and `--watchdog status` print the supervision state (setting,
  task present?, last run, is the program running now). The tray menu gained
  **Supervision → “Bring the program up automatically”**.
- **Honest first-run notice.** `--install` tells the user in plain words that a
  task was created and enabled, that it raises the program at logon and every
  5 minutes, and that switching supervision off means the program will **not**
  restart itself and must be started by hand. The same warning is printed by
  `--watchdog status` / `--status`.
- **Self-test section 14** (watchdog): config defaults, task XML (logon trigger,
  5-minute repetition, hidden, IgnoreNew, current user), marker/claim logic
  (a second `--ensure`/`--run` must not create a second supervisor) and a check
  that `watchdog.enabled=false` does not create a task.
- **Linux**: `--ensure` implemented for systemd — it starts the user service if it
  is not active. Supervision on Linux remains systemd's job
  (`Restart=always` plus the update timer).
- **Console debug build** for safe testing: `FOREST_CONSOLE_BUILD=1` builds
  `ForestProxyUpdater-console.exe`, whose bootloader errors go to stderr instead
  of a desktop dialog.

### Fixed

- **Notification animation did not run (0 frames) after the switch to the
  high-resolution waitable timer.** The timer was armed once in `start()` and
  never re-armed, so exactly one frame was drawn and then the window sat still
  until it timed out. Frames are now scheduled on an **absolute timeline**
  (`next_due += period`), the timer is re-armed after every frame, `_disarm()`
  uses `CancelWaitableTimer()`, and frames that are more than one period late are
  counted as dropped instead of accumulating debt (which would make the animation
  stutter). Verified: 117 frames for a 1.96 s animation, average **60.0 fps**,
  worst frame **17.4 ms**.
- **Modal window “Failed to remove temporary directory”** (Windows, PyInstaller
  onefile). A copy of the program started **by the program itself** (the new
  `--ensure` → `--run` launch) inherited PyInstaller's `_PYI_APPLICATION_HOME_DIR`
  and `_PYI_PARENT_PROCESS_LEVEL`, so the child **reused the parent's `_MEI`
  unpack directory** instead of extracting its own; the parent then could not
  delete `_MEI` (its files were still in use by the surviving child) and showed
  the modal error. The child environment is now cleared of the PyInstaller
  variables (plus `PYINSTALLER_RESET_ENVIRONMENT=1`), so every process owns and
  removes its own `_MEI`. In addition the program leaves the unpack directory on
  start (`config.ensure_safe_cwd`, before any child process is launched) and
  starts its background processes with a working directory outside the unpack
  directory.

### Changed

- `--install` no longer writes an autostart entry to the registry; the single
  autostart mechanism is the scheduler task. `--uninstall` removes the task, any
  legacy autostart entry, the shortcut and the files.
- The identity used for “is a supervisor of *this* installation alive” is derived
  from the running executable (`sys.executable`) and the state directory, so two
  different installations (a working one and a test sandbox) never see each other.

## [1.1.0] — 2026-10-17

### Added

- **Linux packages.** The Linux build now ships as real packages, all built from
  one shared file layout (`packaging-linux/lib/common.sh`, so the formats cannot
  drift apart): `.deb` (Debian/Ubuntu/Mint), `.rpm` (Fedora/RHEL/Rocky/Alma/
  openSUSE), Arch `PKGBUILD` + `.pkg.tar.zst`, a universal `AppImage` and a
  portable `.tar.gz` with `install.sh` / `uninstall.sh`. Every format installs
  **one systemd system service** (no logged-in user and no
  `loginctl enable-linger` required) and needs nothing dropped in by hand: the
  Xray core and both geo databases are inside the binary. Installation is a
  single command, removal is the distribution's own (`apt purge` / `dnf remove` /
  `pacman -Rns`) or `uninstall.sh`. Instructions:
  `docs/LINUX-INSTALL.md` and `docs/LINUX-INSTALL.ru.md`.
- **Terms of use and disclaimer** (`docs/TERMS.md`, Russian: `docs/TERMS.ru.md`),
  linked from both READMEs, printed for a few lines by the package installers
  (`.deb` / `.rpm` / Arch / `.tar.gz`) and shown once on the first run (plus a
  line in `--help`). The program gives access to
  all websites, including blocked ones; complying with the laws of your own
  jurisdiction is your responsibility. GPL-3.0 is not replaced by this notice —
  it is added to.
- **Package matrix bench.** `linux/tests/wsl-matrix.sh` now also handles
  `.tar.gz`, `.AppImage` and user-mode (`systemctl --user`) installs, waits for
  the product's real first selection cycle before probing traffic
  (`--traffic-wait`, minutes not seconds) and steers the payload to a test port
  through `FOREST_CONFIG` without modifying the shipped config;
  `linux/tests/run-matrix.sh` runs the whole package × distribution matrix and
  writes a `summary.csv`. Results of the release run:
  `docs/LINUX-PACKAGES-PROOF.ru.md`.

### Fixed

- **Modal window “Failed to remove temporary directory” and leftover `_MEI*`
  folders.** The app is a PyInstaller onefile bundle, and the Xray core used to
  be started straight **from the unpacked temp directory** (`_MEIxxxx/bin/xray`).
  The core outlives the program by design (the proxy must keep running), so the
  file stayed busy: the PyInstaller cleanup could not delete the directory,
  showed a modal `Warning` window and left `_MEI*` folders behind (on Windows
  every run with an empty `client.core`). The core and both geo databases are
  now **copied once from the bundle into a permanent directory** and the core is
  started only from there: `%LOCALAPPDATA%\ForestProxyUpdater\bin\xray`
  (Windows, or `<FOREST_ROOT>\bin\xray`) and
  `~/.local/share/forest-proxy-updater/bin/xray` (`$XDG_DATA_HOME`) on Linux.
  The copy is compared by size and SHA-256 and only happens when the file is
  missing or the bundle is newer, so an ordinary start neither copies nor reads
  anything extra. `client.core` may now stay empty (or relative —
  `bin/xray/xray(.exe)` is resolved against the data/program directory); no
  machine-specific absolute path is required. The program also removes **its
  own** stale `_MEI*` folders from temp on start (a background thread; only
  directories that contain our `bin/xray`, never the current unpack directory,
  never younger than 2 minutes, and never one a process is still running from —
  on Windows that is decided by a non-destructive write probe on the files).
- **Installer wrote a broken launch command.** `--install` used to register the
  Start Menu shortcut and the autostart entry as `pythonw.exe "<dev
  folder>\src\v2ray_updater.py" --run`, so clicking the shortcut or rebooting
  produced a modal error window. `--install` now **copies the product `.exe`
  into the install directory** (`%LOCALAPPDATA%\ForestProxyUpdater\
  ForestProxyUpdater.exe`) and registers exactly
  `"<install dir>\ForestProxyUpdater.exe" --run` — no extra script argument and
  no dependency on the folder the archive was unpacked into. Both the shortcut
  and the registry value are read back and compared right after being written
  (the result is logged). `--uninstall` now also removes the installed `.exe`
  (deferring to a cleanup script when `--uninstall` runs from that very `.exe`,
  which Windows will not let a process delete).
- **State file lost keys written by another writer.** `StateStore.save()`
  replaced the whole file from its own in-memory snapshot, so the pool
  fingerprint, its hash and the freeze timestamp written while the balancer was
  raised were wiped by the cycle's final save — the pool freeze and the
  "unchanged pool" fast path silently stopped working. Saves now merge with what
  is on disk (keys are never dropped).
- **Orphan cores were never reaped.** The "our core" lookup matched the literal
  `forest_xray.json`, while the config actually used is
  `state/xray_config.json`; a leftover core could keep holding the client port.
  The lookup now matches our own state directory.

### Changed

- **Seamless core switching.** The new core config is validated **before** the
  running core is stopped (`xray run -test -config <file>`; automatic fallback
  to the old "start on free ports" check if a core build does not support
  `-test`), so a broken config can no longer take the link down. The live config
  file is only replaced after it passes.
- The core is **not restarted when nothing changed**: the pool fingerprint
  (`balancer_sig`) and the hash of the config the running core actually uses
  (`balancer_cfg_sha`) are compared first; an identical config or a frozen pool
  (`watch.pool_freeze_s`) means no restart at all, even for `force=True`.
- Restart is now measured: the log gets the actual **link gap in milliseconds**
  (from stopping the old core to the new one accepting the client port, plus the
  time until data is confirmed). The fixed 300 ms sleep after stopping the core
  is gone — the new process starts immediately and the port is polled for
  release.
- Faster restart path (the measured gap drops from ~0.9–1.1 s to ~0.2 s):
  stopping uses `TerminateProcess` (no `taskkill.exe` spawn), the orphan sweep
  (a ~0.4 s PowerShell query) only runs when there was no live core to stop, the
  port is polled in 10–50 ms steps, and the balancer no longer re-probes a link
  that the restart already confirmed.
- `start_core` verifies that the new core process is still alive after the port
  comes up, so a foreign/orphan process holding the port is not mistaken for a
  healthy core.
- `--install` now reports a first cycle that found no live servers (the install
  itself is complete; the watchdog keeps retrying and raises the link as soon as
  usable nodes appear) instead of staying silent about it.
- The single-server activation path and the guard failover path validate their
  configs before touching the live one as well.

## [1.0.0] — 2026

First public release.

### Added

- **Standalone mode (default).** The program does not need v2rayN. It keeps its
  own server list (`state/servers.json`), writes the Xray-core config itself,
  starts and stops `xray.exe` itself, and sets/clears the Windows system proxy
  itself.
- **Optional v2rayN mode** (`client.mode: "v2rayn"`). Works through an already
  installed v2rayN — its SQLite database (`guiNDB.db`), its config
  (`guiNConfig.json`) and its core — with backup/rollback of the database.
- **Automatic server selection.** Subscriptions are fetched from configured
  fixed URLs and from GitHub repositories found by search; share links
  (`vless://`, `vmess://`, `trojan://`, `ss://`) are parsed, de-duplicated and
  filtered.
- **Test pipeline:** TCP pre-filter, RTT measurement through one throwaway Xray
  instance per candidate, jitter filter, speed test on the best-by-RTT subset,
  final re-check (RTT + a YouTube reachability check).
- **Native Xray balancer.** One running core holds a pool of servers with
  `observatory` (health checks) and `leastPing` balancing, so new connections
  go to a live/fast node without restarting the core.
- **Watchdog ("watch")** that keeps the core alive, notices a dead port in
  1–2 s, monitors throughput and uses YouTube as a canary; on full failure it
  asks for a full refresh cycle.
- **Guard loop** for one quick self-check/recovery pass (failover over the
  stored `[AUTO]` servers, then a full cycle).
- **Automatic failover and an emergency "last good" pool** that is raised when
  no live servers can be found.
- **Subnet diversity cap** (default: at most one server per /24) with a floor
  that relaxes the cap if the pool would otherwise be too small.
- **Cloudflare-node filter** (on by default): nodes whose address is inside
  Cloudflare's ranges are dropped from the server list.
- **Manual proxy-off flag**: if the proxy is switched off by hand, the program
  does not raise it again on its own.
- **Single-process supervisor** running the watchdog, guard, update and tray
  threads in one program instead of three separate scheduled tasks.
- **Save/restore of the original system proxy** so `--uninstall` returns
  Windows to the state it was in before the first run.
- **Bilingual output (Russian + English)** for the CLI strings (status,
  install, self-test, banners), selected by `ui.lang` or the system locale.
- **Self-test suite** (`--selftest`) that checks the API surface, the server
  store, core/balancer config generation, the mode dispatcher, i18n, first-run
  config creation and core discovery — without network, without touching the
  system proxy and without occupying the real ports.
- **No third-party Python dependencies** — the standard library only.

### Known limitations in 1.0.0

- The **visual tray icon is not implemented yet** (planned via Win32
  `NotifyIcon`/ctypes). The status channel (`state/tray_status.json`) and its
  consumer thread already exist; only the on-screen icon is missing.
- **i18n is partial:** CLI strings are translated, long-cycle log messages are
  still Russian only.
- Long-cycle messages are written to the log in Russian regardless of
  `ui.lang`.

### Notes

- Licensed under **GPL-3.0-only** (see `LICENSE`).
