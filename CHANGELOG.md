# Changelog

All notable changes to **FOREST's Proxy Auto-updater** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.10] — 2026-10-22

Packaging-only release. **No program code changed**: both ELFs are byte-identical
to 1.3.9 (`2685166…` service, `754888d…` helper — full hashes in the release
notes). The release fixes the
package defect that broke the one-line install, and adds a guard so it cannot
come back.

### Fixed

- **Installer scripts were stored with CRLF, so `curl … | bash` died.**
  `linux/install.sh`, `packaging-linux/tar/install.sh`,
  `packaging-linux/tar/uninstall.sh`, every `build-*.sh`, `lib/common.sh`,
  `deb/postinst|prerm|postrm`, `arch/*.install` and all `.desktop` files were
  committed through a Windows checkout (`core.autocrlf=true`), which left `\r`
  at the end of every line. Piping the file into bash produced
  `bash: line 30: $'\r': command not found` and a syntax error, so the
  documented install path did not work at all in a clean guest; `postinst`
  would have failed the same way.
  Every script/desktop/`.install`/spec file in the repository is now LF.

### Added

- **`.gitattributes`** with `* text=auto eol=lf` plus explicit `eol=lf` rules
  for `*.sh`, `*.install`, `*.desktop`, `postinst`/`prerm`/`postrm`, `PKGBUILD`,
  `*.spec(.in)`, `*.service`, `*.timer`, `*.py`, `*.qml`, `*.md`, `*.json`
  — line endings no longer depend on the machine that checks the tree out.
  `core.autocrlf` is set to `false` in this working copy.
- **`packaging-linux/check-eol.sh`** — scans every file that ends up in a
  package and fails (or `--fix`es) on CR. `check_inputs()` in `lib/common.sh`
  calls the same check, so a build with CRLF sources stops instead of shipping
  a broken installer.
- **Windows artifacts are rebuilt for this tag** (`ForestProxyUpdater.exe`,
  `forest-ui.exe`) from the current tree, and `packaging/version_info.txt` now
  carries `1.3.10` (`filevers`/`prodvers` = `1, 3, 10, 0`, `FileVersion` =
  `1.3.10.0`, `ProductVersion` = `1.3.10`) so the Properties dialog of the
  shipped `.exe` matches the tag instead of the old `1.3.2.0`.

## [1.3.9] — 2026-10-22

Patch release closing every defect found by the 1.3.8 headless DE matrix
(`docs/de-matrix/138/`) and by the package audit. No new features; the only
behaviour changes are the ones listed here. All five formats rebuilt from the
same freshly built ELFs.

### Fixed

- **D1 (P0) — the fallback tray icon disappeared after two seconds.** With no
  notification area in the session (LXQt without a tray plugin, GNOME without
  an extension, a bare window manager) the helper raises its own icon window
  *instead of* the configured mode. `Ui.refresh()` (2 s tick) re-read
  `ui.tray_mode`, got `sni` back and tore the icon down — so the user ended up
  with no icon at all, worse than before 1.3.8. A fallback flag (`ui._tray_fallback`)
  is now set when the fallback is taken, `refresh()` does not re-ask the setting
  while it is set, and an explicit choice in the tray menu clears it.
- **D2 (P0) — the bundle could not see the desktop theme (white card on a dark
  desktop).** PyInstaller exports `LD_LIBRARY_PATH=/tmp/_MEI…`; the *system*
  `gsettings` loaded glib from there and answered `'default'`, which the old
  code mapped to `light`. Child processes that interrogate the session now get
  an environment without `LD_LIBRARY_PATH`/`PYTHONHOME`/`PYTHONPATH`, `'default'`
  means "unknown" (never "light"), and `~/.config/gtk-3.0/settings.ini`
  (`gtk-application-prefer-dark-theme`) is read as a last resort — it works even
  with no session bus. `desktop_env.run_in_session()` (proxy settings written
  through `gsettings`) was cleaned the same way.
- **D3 (P1) — XFCE and LXQt never write GSettings.** The theme is now read from
  `xfconf` (`xsettings /Net/ThemeName`) for XFCE and from `~/.config/lxqt/lxqt.conf`
  for LXQt before GSettings is consulted.
- **D4 (P2) — the self-test contradicted the 1.3.8 code.** It demanded
  `Context`/`setContextMenu is None` — XEmbed assertions — while in SNI mode the
  menu *must* be handed to the panel. The test now branches on `ui.icon_mode`:
  SNI checks that `contextMenu()` is set and that a second (own) menu is not
  opened; the own-window mode keeps the `Context` checks. The "menu failure is
  logged" check reads the logger's real path (it used to fail because of the
  directory, not the code) and is exercised through the guarded path in SNI.
- **D5 (P3) — card margins were wrong at 150 %.** All offsets are logical pixels
  (Qt scales them), but the work area itself was wrong: at 150 % on XFCE the
  panel's strut is reported in device pixels, Qt divides it and adds it to `y`,
  so `availableGeometry()` reached *past* the bottom of the screen and the card
  sat 8 device pixels from the edge instead of 14 logical (21 device). The card
  position is now clamped to the real screen rectangle in logical pixels.
- **D6 (P3) — `PANEL_PX = 46` was hard-coded.** With no panel the own icon stood
  52 px off the bottom edge; on a 32 px LXQt panel the gap was wrong too. The
  panel height is now *measured* (`geometry() - availableGeometry()`, or
  `_NET_WORKAREA`), `tray.panel_px` is only a manual last resort (default 0), and
  `--tray-probe` prints the measured value with its source.

### Packaging

- **Arch: `/usr/share/doc/forest-proxy-updater/COPYING`** is shipped now (it used
  to exist only under `/usr/share/licenses`), as `.deb`/`.rpm`/tar already did.
- **Arch: declared `bash` and `hicolor-icon-theme`** (`namcap` findings: the
  `.install` script and `forest-clean-desktop` are bash scripts, and the icon is
  installed into the hicolor theme).
- **The Arch `.install` inside the released package is the current one** — 1.3.8
  shipped a pre-fix copy; the packaged `.INSTALL` is now byte-identical to
  `packaging-linux/arch/forest-proxy-updater.install` (verified by sha256).
- All five formats rebuilt as **1.3.9** (`.deb`, `.rpm`, Arch, `.tar.gz`,
  AppImage) from the same ELFs; payload hashes inside `.deb` and Arch match
  `linux/dist/` byte for byte.

### Verified (facts, 2026-10-22)

- Package matrix **4 distros × 16 checks = 64/64 PASS** (Ubuntu 26.04 and Debian
  13 with `.deb`, openSUSE Tumbleweed with `.rpm`, Arch with `pacman -U`):
  install → files/rights → version → service → three live helper copies killed on
  upgrade → service restarted → remove → no leftovers.
- Dark card on dark panels **by screenshot**: XFCE `srgb(25,26,30)`, MATE
  `srgb(25,26,30)` (1.3.8: `srgb(240,240,240)` and `srgb(238,238,238)`).
- Fallback icon **stays**: found on LXQt at t=4/6/8/11/14 s (1.3.8: `NOTFOUND`
  from 6 s), one single "mode switched" line in the log.
- 150 %: card gaps 12 device right / 21 device bottom = 8/14 logical; the icon is
  no longer clipped by the screen edge.
- `--selftest` (offscreen, XFCE session): **153 OK / 6 FAIL**, all six stand
  artefacts (Xvfb frame pacing, DejaVu menu metrics, demo data timing); 1.3.8 had
  153 OK / 7 FAIL with two real test/code contradictions (D4).

## [1.3.8] — 2026-10-22

Live check on the CachyOS KDE VM (live KWin 6.8, Wayland) with the 1.3.7 helper
running under the service. The card was reported **in the centre** and **white**
while the desktop is dark. Numbers, not guesses: the helper process had a
COMPLETE session environment (`DISPLAY`, `XAUTHORITY=/run/user/1000/xauth_*`,
`XDG_CURRENT_DESKTOP=KDE`, `KDE_FULL_SESSION=true`) — so the environment was not
the cause. The cause was the **platform**: `--toast-helper` with that exact
environment reported `"platform": "wayland"`, `"can_place_in_process": false`,
`frames: 0` — no X window at all. The same environment plus
`QT_QPA_PLATFORM=xcb` gave `abs_x=624 abs_y=525 w=392 h=199`,
`override_redirect=1`, `_NET_WM_STATE=ABOVE,STAYS_ON_TOP,SKIP_TASKBAR`,
`_NET_WM_USER_TIME=0`, focus unchanged (`0x200000`) — i.e. exactly the corner
(1024−392−8, 738−199−14) and no focus steal.

### Fixed

- **The helper is an X11 client on Wayland.** `_force_x11_for_icon()` was called
  only in service modes (`--tray-icon`, `--tray-demo`, `--toast-live`, …); the
  MAIN path — the one the service launches — created `QApplication` without it,
  so the helper ran as a native Wayland client. KWin then chose the card's
  position (centre), `override_redirect` was never applied, and without xcb the
  KDE theme plugin was not loaded. Now the main path normalises the platform
  before `QApplication`, like the icon always did.
- **`--toast-helper` no longer forces the Wayland path** (`keep_x11 = False`):
  the diagnostic measured a path that does not exist in production any more.
- **KDE theme is read from KDE, not from GSettings.** `gsettings get
  org.gnome.desktop.interface color-scheme` answers `'default'` on KDE, and the
  old code mapped that to `light` — hence the white card on a dark desktop.
  `theme._detect_kde()` reads `kdeglobals` (`[General] ColorScheme`, then
  `[Colors:Window] BackgroundNormal` by luminance) and falls back to "dark" for
  a KDE session with no readable scheme.
- **Card placement is verified in X, post-map.** `card_run_mode()` now calls
  `card_place_hard()`: it sets `override_redirect` **in the X server**
  (`XChangeWindowAttributes`, CWOverrideRedirect) if Qt did not, then
  `XMoveWindow` after mapping, then reads back `XGetWindowAttributes`. On a
  mismatch it logs the FACTUAL coordinates to the journal.
- **`card_child_env()` completes the session passport** for the child card:
  `XDG_SESSION_TYPE`, `DISPLAY`, `XDG_CURRENT_DESKTOP=KDE`,
  `KDE_FULL_SESSION=true`, `KDE_SESSION_VERSION=6`, and an `XAUTHORITY` found
  under `/run/user/<uid>/xauth_*` when the variable is missing.

### Fixed (checks)

- `check-card-137.sh` launched `forest-ui --card-run` **without** the card spec
  path, so the card died instantly (`не прочитан : [Errno 2] … ''`) and no window
  properties were collected at all. `check-card-138.sh` shows the card through
  the real helper path, finds its window by walking the X tree with libX11
  (there is no `xwininfo`/`xdotool` in the system, and override-redirect windows
  are absent from `_NET_CLIENT_LIST`), and prints numbers: `abs_x/abs_y/w/h`,
  `override_redirect`, `map_state`, `_NET_WM_WINDOW_TYPE`, `_NET_WM_STATE`,
  `_NET_WM_USER_TIME`, plus the env of the service-launched helper vs the session.

## [1.3.7] — 2026-10-22

Live check of 1.3.6 (CachyOS KDE VM, live KWin 6.8): the card was still reported
in the **centre** of the screen, and the 1.3.6 report carried
`core_reason: ` — empty — next to "ядро не слушает 10808". Both had the same
root cause: a value was **assumed**, never read back.

### Fixed

- **`core_reason` is filled on the paths that actually close the port.** In 1.3.6
  the reason was written only inside `start_core()` failures. When the pool was
  empty (`ensure_balancer: пул пуст`, `аварийный конфиг: сохранённого рабочего
  состава нет`) the core was never started at all, and when `Guard` gave up
  (`Guard: восстановить связь не удалось`) nothing was recorded — so the report
  said `core_reason:` empty while the port stayed shut. Both paths now call
  `core_diag()` and store the fact (`_set_core_reason`) before returning, and log
  it as `ПРИЧИНА: …`.
- **The card is placed by the X server, and judged by the X server.** Qt's
  `pos()` is a *request*, not a fact: under XWayland KWin can put the window in
  the centre while `pos()` reports the corner — so 1.3.6 logged "placed" while
  the screen showed the centre. 1.3.7 reads `x11_geometry()` through
  `XGetWindowAttributes` (position, size, `override_redirect`, map state), calls
  `x11_move()` (`XMoveWindow`, which the X server executes itself for an
  override-redirect window, bypassing the WM) when X disagrees, and decides
  `placed` on the X answer. A mismatch now logs
  `ПРИЧИНА не-в-углу: X держит окно в X,Y вместо W,H (override_redirect=…)`.
- **Window facts no longer depend on `xwininfo`.** It is not installed on the
  live system, so every geometry field in the 1.3.6 report was empty and the
  "centre" claim rested on a screenshot. `x11_window_info()` now merges
  `x11_geometry()` data first, so `--card-run` prints real coordinates, size and
  the override flag on a machine with no X utilities at all.

## [1.3.6] — 2026-10-22

Live-install report of 1.3.5 (CachyOS KDE VM, `report-135725.txt`): after the
upgrade **three copies** of the interface helper were alive at once (three tray
icons, three cards), the installer printed "старый значок остановлен" while all
of them kept running, the failure card got a **new `proc.id` every ~10 s**, and
the **core never listened on the client port 10808** while the report said only
"серверы пула не отвечают".

### Fixed

- **ONE helper, finally.** The single-instance lock now lives where *this* uid
  can actually write — `$XDG_RUNTIME_DIR` → `/run/user/<uid>` → `state_dir` →
  `/tmp/forest-ui-<uid>.lock` — instead of only `paths.state_dir`. The service
  (root, `/var/lib/…`) and the session helper (user, `~/.local/state/…`) used to
  lock **different files**, and when the file was unwritable
  `InstanceLock.acquire()` reported "degraded" and the guard fell back to the
  heartbeat — so every `ensure()` could raise one more icon. Degraded locks are
  now skipped, not trusted, and a `/proc` scan by executable name + uid is the
  last line: a second start silently exits with code 0 and draws no icon.
- **Honest stop.** `.install` (`post_install`, so also on upgrade) kills every
  `/usr/lib/forest-proxy-updater/forest-ui` process of every user before the
  service starts: `pkill -f` by the **full path** (arguments differ between
  copies, `-x` by name does not match), then a `/proc` pass by real executable,
  a wait loop and `SIGKILL` for survivors, and it prints how many pids were
  killed and what is left. `forest-ui --stop` no longer writes only a flag file:
  it signals the pids from the locks plus `/proc` and waits. `--stop` is also
  handled **before** the graphics-session check (over ssh/headless it used to
  exit 0 without stopping anything — which is why the installer "succeeded").
- **Upgrade restarts the service.** `enable --now` does not restart an already
  running unit, so after an upgrade the **old supervisor code** kept running
  until reboot; `post_install` now does an explicit `systemctl restart`.
- **60-second gate on every failure path.** `_outage_done` respected
  `FAIL_CARD_MIN_S`, but `_tray_settle` wrote its own failure with a fresh
  `proc.id` — the documented "new id every ~10 s" stream of cards. Both paths
  now share one gate (`_fail_card_due`).

### Added

- **A reason line instead of "серверы пула не отвечают".** `clientwrite.core_diag()`
  names the actual cause — binary missing, config not written, port held by a
  foreign pid, our core alive but not listening, or the last lines of
  `forest_xray.log` — and it is written to the log (`start_core: …`,
  `ПРИЧИНА: …`), to the state file (`core_reason`) and into the card phase
  (`Не удалось: <причина>`). The installer report prints the reason, who listens
  on the port, the xray binary and the core-log tail.

## [1.3.5] — 2026-10-22

Live KWin regression (found on a real CachyOS KDE Wayland session; the WSL checks
could not see it): the card **circled continuously**, stood **in the middle of the
screen**, **stole keyboard focus**, and every failed server search drew another
card. All four have one root cause — the card was an ordinary, WM-managed
top-level window.

### Fixed

- **The card is now override-redirect, exactly like the tray icon.** Default
  preset is `override`: `Qt.Window | FramelessWindowHint | WindowStaysOnTopHint |
  WindowDoesNotAcceptFocus | BypassWindowManagerHint` plus
  `WA_TranslucentBackground`, `WA_ShowWithoutActivating`, `WA_NativeWindow`,
  `WA_TransparentForMouseEvents`, `WA_X11DoNotAcceptFocus` and
  `_NET_WM_STATE_SKIP_TASKBAR`. `xwininfo -id <карточка>` теперь показывает
  `Override Redirect State: yes` — оконный менеджер окно не видит, поэтому KWin
  и не центрирует его, и не активирует. X-свойства окна:
  `_KDE_NET_WM_WINDOW_TYPE_OVERRIDE` + `_NET_WM_WINDOW_TYPE_NORMAL`,
  `_NET_WM_STATE=ABOVE,STAYS_ON_TOP,SKIP_TASKBAR` — тот же набор, что у
  работающего значка (замер: окно в 2160,1309 на мониторе 2560x1440, то есть
  8 px от правого края и 14 px от нижнего).
- **`_NET_WM_USER_TIME=0`** is set on the card window after `show()` (via
  `libX11` through `ctypes`, with a local X error handler so a bad window id can
  never kill the process). This removes the "user just interacted" hint KWin uses
  when deciding whether to focus a new window.
- **Anti-spam: one card per event type, not more than once per 60 s.**
  `Ui._card_throttled` keys on (kind, title, subtitle) instead of a process id, and
  `Ui._proc_card_gate` keys on (kind, cause, failed). Previously every new process
  id — the supervisor wrote a new one on each retry — produced a fresh window.
- **Server-search failures and an empty pool never draw a card at all**
  (`card_is_search_noise`): those are supervisor work, not a user event; they go
  to the journal only.
- **Pause/backoff instead of a hot cycle.** `watch` now waits 10/20/40/60 s after
  each failed recovery (both on the port pulse and on the strike path) and logs
  the pause (journal showed ×191 attempts per 60 s). A failed recovery is written
  to the state file (and thus shown as a card) at most once per 60 s.
- Card interaction with X now degrades safely: the `hide_from_taskbar` call and
  the user-time write run only on real X11 (`xcb`/`x11`), never on
  `offscreen`/`minimal`, where the "window id" is not an X window and an X request
  to it is fatal to the process.

### Verification

- WSL (Arch, `QT_QPA_PLATFORM=offscreen`): 39 logic checks pass — preset
  flags, window attributes, corner geometry from `available_area()` numbers,
  throttling, search-noise classification, and "191 attempts in 60 s → 1 card".
- WSL (WSLg, real X server, `QT_QPA_PLATFORM=xcb`): window properties read back
  from X — `_KDE_NET_WM_WINDOW_TYPE_OVERRIDE`, `ABOVE/STAYS_ON_TOP/SKIP_TASKBAR`,
  `_NET_WM_USER_TIME=0`, geometry equal to the requested corner.
- **Not verified here:** behaviour under a live KWin (KWin is the actor that
  centred/activated the window). `state/serve/repro-card.sh` performs that check
  on the real session: it shows one card, measures `_NET_ACTIVE_WINDOW` before and
  during, takes two screenshots, prints the card window's coordinates, type and
  state, and sends report plus PNGs back to the host.

## [1.3.4] — 2026-10-22

Notification card on Linux: the card appeared in the **middle of the screen**
instead of the bottom-right corner above the panel.

### Fixed

- **The card was centred by the compositor, not placed by us.** A Wayland client
  cannot position a top-level window at all; `Qt.Tool` (window type `UTILITY`)
  made it worse — KWin ignores `move()` both before and after `show()`. The card
  now uses the **same window recipe as the tray icon** (`Qt.Window |
  FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus` +
  `WA_TranslucentBackground`/`WA_ShowWithoutActivating`/`WA_TransparentForMouse
  Events`/`WA_NativeWindow` + `_NET_WM_STATE_SKIP_TASKBAR`) **and the same
  process type**: `move()` is applied before `show()`, repeated from
  `QTimer.singleShot(0)`/`(60)` after it, and re-asserted every ~250 ms while the
  card is up, which wins the race with the compositor.
- **Under a Wayland-native helper the card is now drawn by an X11 child
  process** (`forest-ui --card-run` with `QT_QPA_PLATFORM=xcb`, XWayland) — the
  exact path the icon already uses (`_force_x11_for_icon`). Wayland offers no way
  to position a window at all, so no flag combination can fix it in-process; the
  child receives late updates (final state) through a small JSON file, and the
  helper keeps its usual `Toast` API. Windows and X11 sessions are unchanged: the
  card is still shown in-process there.
- **Margins follow the approved mockup** (`docs/notify-mockup.html`:
  `.toast{right:0;bottom:14px}`) — 8 px from the right edge and 14 px from the
  bottom of `availableGeometry()` (i.e. above the panel), instead of a flat
  12 px from the *screen* edge.
- **`WA_X11NetWmWindowType*` attributes were silently never applied** (both in
  the card and in the icon presets): in PySide6 they live on
  `Qt.WidgetAttribute`, not on `Qt`, so `getattr(Qt, "WA_X11NetWmWindowTypeDock")`
  returned `None` and `setAttribute` was skipped. There is also no
  `WA_X11NetWmWindowTypeOverride` in Qt — override-redirect is
  `Qt.BypassWindowManagerHint`, which the `override` preset now uses.

### Added

- Diagnostics that answer "where is the card" with a **screen shot** and X11
  data, not with Qt's opinion: `forest-ui --toast-live` (corner + shot, `--n`
  cards in a row), `--toast-sweep` (every window-type preset, one shot each),
  `--toast-helper` (the real `Ui.show_toast` path, Wayland → X11 child). The flag
  preset can be re-picked at runtime with `FOREST_TOAST_FLAGS=<preset>`
  (`window` (default), `window_plain`, `notification`, `utility`, `dock`,
  `override`/`bypass`, `tool`, `tooltip_type`, `tooltip`, `splash`, `dialog`).
- `state/check_toast_place.py` — 35 off-screen checks for placement, margins,
  stacking, flags and the preset sweep (all pass).

## [1.3.3] — 2026-10-22

Tray icon on Linux: three defects from a live CachyOS test, plus an icon-mode
switch (the standard notification-area icon is now the default).

### Fixed

- **The tooltip said «FOREST: сторож молчит (нет данных)» while the service kept
  writing a fresh `tray_status.json`.** The icon process and the service can get
  different environments (the unit sets `XDG_STATE_HOME`/`FOREST_ROOT`, the
  helper is launched inside the user session), so the helper read a path where no
  file existed and reported "watchdog silent". `tray_status.read_status()` now
  scans every known state directory (config, XDG, `FOREST_ROOT/state`, the
  systemd unit's `/var/lib/...` variants) and returns the **freshest** readable
  record; `status_info` derives the record's age from `ts` and falls back to the
  file's mtime, so a record without `ts` is no longer "eternally stale". The file
  actually used is logged (`источник: …`).
- **The icon colour never followed the state.** `Ui.refresh()` only changed the
  icon when the state changed and never passed the state to the own icon window,
  which also whitelisted states to `ok|error|busy|off` — `switch`, `noanswer`,
  `down` and `restoring` all collapsed to grey "off".
- **The tooltip only refreshed after opening and closing the menu.** The refresh
  timer now runs every **2 s** and always updates the tooltip (and the live tip
  window, if it is open); the icon bitmap is regenerated only on a real state
  change.
- **The menu did not close on a click anywhere outside it.** `Qt.Popup` relies on
  an X grab, which does not exist under Wayland/XWayland, so clicks outside the
  menu went to other windows. A 1 %-opaque, frameless, always-on-top `Qt.Window`
  layer (`ui_menu._ClickCatcher`) is now placed on every screen *under* the menu
  (the menu is override-redirect and stays above it), so a click on a menu item
  still reaches the menu and any other click closes it. The layer is removed the
  moment closing starts.

### Added — icon mode switch

- New menu item **«Значок в области уведомлений»** (`ui.tray_mode`: `"sni"`
  default, `"own"` = the floating icon window with our animated menu). The switch
  applies immediately: the old icon is hidden and destroyed, the new one is
  created, `QMenu` is attached/detached — no second icon, no second process.
  The value is persisted through the service (`ui_cmd` → `tray_menu.set_tray_mode`).
- In SNI mode the panel draws a full `QMenu` built from the *same* action model:
  11 items plus «Фильтры» (two checkables) and «Тема оформления» (three), same
  handlers, tick-refreshed check marks. Notifications stay our own Toast card.
- **No tray area at all** (GNOME without AppIndicator, some LXQt builds, bare
  WMs): the helper now automatically falls back to the own icon window instead of
  silently running without an icon; the reason goes to the log and to one
  `notify-send` line, and `ui.tray_mode` is left untouched.
- The own icon's position no longer assumes a fixed 46 px panel: the working area
  comes from `availableGeometry`, then from `_NET_WORKAREA` (panel struts), and
  only then from the configured `tray.panel_px`.

## [1.3.2] — 2026-10-21

Linux-only fixes: four defects found on a live CachyOS (KDE Plasma Wayland,
package 1.3.1). On the Windows side the behaviour of the program itself is
unchanged; the **packaging** changed — the interface helper is now embedded and
Windows ships as **one** file (see the last sections of this release).

### Fixed — the tray icon was not drawn on screen, and never came up on restart

Both defects were found and fixed on a live CachyOS VM (KDE Plasma 6, Wayland,
KWin 6, XWayland), package 1.3.2. Same version, repack.

- **The icon existed but nothing was painted at the right edge.** Qt reported
  `isVisible() == True` while the X server kept the window **Unmapped**
  (`map_state=0`) and `isExposed()` was `False`. Two separate causes:

  1. *Window flags.* `Qt.Tool | FramelessWindowHint | WindowStaysOnTopHint |
     WindowDoesNotAcceptFocus` makes KWin see a `UTILITY` window carrying
     `_KDE_NET_WM_WINDOW_TYPE_OVERRIDE`; such a window is not composited. The
     icon is now a normal managed window —
     `Qt.Window | FramelessWindowHint | WindowStaysOnTopHint |
     WindowDoesNotAcceptFocus` plus `WA_ShowWithoutActivating` (no frame, always
     on top, never steals focus). Selecting a different type is not a fix by
     itself: `WA_X11NetWmWindowTypeDock` reports `map_state=2`/`isExposed=True`
     and is still **not drawn** — only a screen capture settles it.
  2. *Task-bar button.* A normal window gets a task-bar entry, so the program
     showed up **twice** (icon at the edge + a button in the panel). Qt cannot
     set `_NET_WM_STATE_SKIP_TASKBAR` at all, so `src/tray_popup.py` sets it
     itself through Xlib: property change **and** an EWMH `ClientMessage` to the
     root window (KWin may miss a bare property change).

  Verified with real screen captures of the VM console, by colour: the icon's
  26 px window lands as an exact 39×39 px block at 1.5× scale, and the panel gets
  no extra button. Diagnostics grew accordingly: `--tray-demo` now prints
  `x11_map_state` and the X window type, and `FOREST_TRAY_FLAGS` /
  `FOREST_TRAY_DEBUG_FILL` re-run the whole flag matrix without a rebuild.

- **The icon did not come up after `systemctl restart`.** The supervisor looks for
  the user's graphical session by scanning `/proc/<pid>/environ`. The candidate
  filter accepted a process with a D-Bus address but **no display**, and
  `kwin_wayland` (the compositor) is exactly that: it owns the session yet has
  neither `DISPLAY` nor `WAYLAND_DISPLAY`. The helper was therefore launched with
  the compositor's environment, printed «нет графической сессии» and exited —
  no icon. `desktop_env.scan_sessions()` now **requires** a real display.
  Related: `kwin_wayland_wrapper` was listed by its full name, but
  `/proc/<pid>/comm` is truncated to 15 characters (`kwin_wayland_wr`), so that
  entry could never match.

- **`forest-clean-desktop`** (new, runs as the user, no root): removes the old
  `org.forest.proxyupdater` panel widget and the pinned helper launcher from the
  Plasma panel config with KDE's own tools (`kreadconfig6`/`kwriteconfig6`),
  backs the config up and refreshes the caches. The plasmoid is no longer shipped
  at all; `post_upgrade` also deletes its leftovers from `/usr/share`.

### Fixed — emergency repack of the same version (KDE plasmoid froze the shell)

- **The panel widget (plasmoid) could hang the whole Plasma 6 session.** The
  first 1.3.2 build imported the **Plasma 5** module and started an external
  process from QML on a timer:

  ```qml
  import org.kde.plasma.plasma5support as P5Support
  ...
  function refreshState() {
      var path = String(Qt.resolvedUrl("../code/read-state.sh")).replace("file://", "");
      stateSrc.connectSource("sh '" + path + "' 2>/dev/null");
  }
  P5Support.DataSource {
      id: stateSrc
      engine: "executable"
      connectedSources: []
      onNewData: function (sourceName, data) { ... stateSrc.disconnectSource(sourceName); ... }
  }
  Timer { interval: 5000; running: true; repeat: true; triggeredOnStart: true
          onTriggered: root.refreshState() }
  ```

  plus a second `P5Support.DataSource` with `engine: "executable"` for the
  menu (`exec.connectSource("forest-ui --menu-popup")`). On a live CachyOS
  (Plasma 6, Wayland) adding the widget froze the shell: the panel stopped
  painting and ignored clicks while guest CPU stayed at 0% — i.e. the QML thread
  was blocked, not the daemon and not the kernel. The plasmoid has been
  rewritten:
  - **no process launching at all** — no `Plasma5Support`/`P5Support`, no
    `DataSource`, no `engine: "executable"`, no `connectSource`, no scripts;
  - the state is read with one **asynchronous** `XMLHttpRequest` against
    `file://` (`contents/ui/StateReader.qml`), same path list as the old script,
    first successfully opened file wins, nothing is written to disk;
  - the poll interval is **20 s** and a tick is skipped while the previous
    request is still in flight; a request that does not answer within 8 s is
    dropped by a watchdog;
  - any failure (no file, broken JSON, array, `null`, no permission, blocked
    file reads) yields an **empty state → neutral grey**, everything is wrapped
    in `try`/`catch`;
  - the menu is opened by asking the desktop to launch the shipped
    `forest-proxy-updater-menu.desktop` (`Qt.openUrlExternally`, `NoDisplay=true`,
    `Exec=… --menu-popup`) — non-blocking, no shell involved; `Ctrl+left click`
    stays as the fallback, left click still does nothing;
  - `contents/code/read-state.sh` is **removed from the package**; the plasmoid
    now contains only three files (two `.qml` and `metadata.json`) and no
    executable bits at all.
- **Qt blocks `file://` XHR unless the session opts in.** `QML_XHR_ALLOW_FILE_READ=1`
  is required for QML to read local files (plasmashell does not set it), so the
  widget stays neutral grey out of the box; the README of the plasmoid documents
  the one-line session hook. With the opt-in disabled the widget neither crashes
  nor hangs — it just shows the neutral colour.
- Verification without a live Plasma session (`sh plasma/plasmoid/check-plasmoid.sh`):
  `qmllint` (Qt 6.12, libplasma 6.8) clean on both files, a static audit that
  fails on any forbidden construct (and ignores comments), and an offscreen
  `qml6` run of `StateReader.qml` over 11 scenarios / 39 assertions (valid JSON,
  broken JSON, array, number, `null`, empty file, missing file, fallback to the
  second path, unknown state, 10 clicks in one tick → exactly one emission) plus
  a blocked-file-reads run. Arch package rebuilt from the same tree; the
  plasmoid files inside it are byte-identical to the repository
  (`main.qml` `9b9f6f5a…`, `StateReader.qml` `71d13d8a…`,
  `metadata.json` `8150000e…`).

### Fixed

- **Every Cyrillic message turned into `?????` in `journalctl`.** The journal
  (`StandardOutput=journal`) received the program's stdout, and stdout went
  through `safestdio.say()`, which encoded the line with
  `ascii + errors="replace"` — a Windows-era guard against a modal PyInstaller
  window on a cp1251 console. On Linux there is no such console: journald wants
  UTF-8, so the guard mangled every Russian line while the UTF-8 file log stayed
  correct (hence "the file is fine, the journal is unreadable"). `say()` now
  prints UTF-8 on POSIX (and on Windows only when the stream is already UTF-8),
  keeps the ASCII fallback for the cp1251/windowed build, and can be forced back
  with `FOREST_STDOUT_ASCII=1`. Measured in WSL: `?????` in journal = **0**,
  Russian lines readable under `LANG=C.UTF-8`, `LC_ALL=C`,
  `PYTHONCOERCECLOCALE=0`, `PYTHONUTF8=0` and a koi8-r `LANG`; the log file is
  valid UTF-8 in every case.
- **KDE Plasma was detected as GNOME, so the system proxy was set by the wrong
  mechanism every 30–60 s.** The old test for "this is GNOME" was "gsettings
  exists" — it exists on KDE too (glib2), so a Plasma session took the GNOME
  branch. Three things were wrong and are fixed by `desktop_env.py`:
  (1) the desktop is now detected explicitly (`XDG_CURRENT_DESKTOP`,
  `KDE_FULL_SESSION`, `DESKTOP_SESSION`, and — when the environment is empty,
  which is the case for a system service — by looking at `kwin_wayland` /
  `plasmashell` / `gnome-shell` processes);
  (2) **the write is verified by reading it back**: a system service runs as
  root without a session bus, so GSettings falls back to the `memory` backend,
  `set` returns success and nothing is stored — that is exactly why the same
  "proxy enabled" warning repeated forever; KDE is now configured through
  `kioslaverc` (`kwriteconfig6/5` + the KIO reparse signal) in the *user's*
  session (root → `runuser -u`), and GNOME-like desktops through GSettings, both
  read back and compared with the wanted `host:port`;
  (3) when there is no session or no common mechanism (i3, sway, container,
  WSL) the program says so **once** and stops retrying every cycle (the desktop
  /session is re-checked every 10 minutes). `gsettings` is still written on KDE
  as well, because Chromium/Electron/GTK read GSettings regardless of the
  desktop.
- **The journal repeated the same warning dozens of times.** Identical messages
  are now de-duplicated in `log.py` (default window 60 s,
  `FOREST_LOG_DEDUPE_S`), and the suppressed count is appended to the next
  identical line (`… [повторилось 124 раз(а) за 60 с]`), so no information is
  lost. A live 150-second run of the real supervisor in WSL: 59 journal lines,
  **0** `?????`, and each of the recurring watch messages appears **once**
  instead of 124 times.
- **`WARN Кандидатов > 400; взято 400` looked like an alarm.** It is the
  normal `test.max_candidates` load limit, and `WARN Добавлен резерв по RTT:
  +18 (итого 30)` is the normal top-up when the strict selection yields fewer
  than `reserve_min` nodes. Both are now `INFO` and say so in words
  (`Кандидатов больше лимита test.max_candidates (400) — на тест беру 400
  (лимит задаётся в config.json, не ошибка)`).
- **Right-clicking the tray icon showed the panel's stock menu instead of ours
  (and on KDE Wayland — nothing at all).** In the StatusNotifier protocol the
  panel owns the right click: it asks the application for a menu description
  (`com.canonical.dbusmenu`) and draws it itself, so the helper used to hand a
  real `QMenu` to the tray (`QSystemTrayIcon.setContextMenu`) — and on
  **XEmbed** panels (XFCE, MATE, Cinnamon, LXQt) that same `QMenu` was what Qt
  popped up instead of our animated window. The `QMenu` is gone: on XEmbed the
  right click (`ActivationReason.Context`) now opens **our** `ui_menu.PopupMenu`
  at the icon geometry, exactly as on Windows, and the left click still opens
  nothing. For StatusNotifier panels (KDE Plasma), where the panel keeps the
  right click for itself, the same package now ships a **panel widget
  (plasmoid)** — `plasma/plasmoid/org.forest.proxyupdater` installed into
  `/usr/share/plasma/plasmoids/`: it draws our icon with the state colour read
  from `tray_status.json` and runs the new `forest-ui --menu-popup [x y]`
  (a process with no second tray icon) that shows **our** menu. On Plasma the
  widget may still get the right click taken by the panel's own widget menu;
  `Ctrl+left click` opens our menu as the documented fallback.
- **`notify.enabled = false` did not silence the notification cards.** The setting
  was checked in one place only (`_on_transition`), so the card for a failed
  restore process, for a command error or for a missing log still popped up —
  in a sandbox without any servers the helper showed the 392x172 "failure" card
  with notifications switched off. All cards now pass one gate in
  `Ui.show_toast()`: with `notify.enabled=false` nothing appears (events,
  process/failure cards, service errors), and only an explicit user action —
  `--show`/`--show-all`, the "Проверить уведомление" and "Сведения" menu items —
  still draws a card. The quiet mode of the checks silences everything.
- **A missing Qt (e.g. no `libglvnd`) crashed the helper with a PyInstaller
  traceback instead of the clear "Qt unavailable" exit.** `import ui_style as US`
  sat inside `if QT_OK:`, while the module-level `NT_* = US.*` block below ran
  unconditionally — without Qt the import of `forest_ui` died with
  `NameError: name 'US' is not defined`. `ui_style` is pure stdlib, so it is now
  imported outside the guard: the helper prints
  `forest-ui: Qt недоступен (…) — помощник не запущен` and exits with code 3,
  which is what `src/ui_helper.py` already expects. `libglvnd` stays in
  `optdepends` (now with the consequence spelled out) — the program itself works
  without it, only the tray helper degrades.

### Verified

- WSL Ubuntu: `linux/posix_selftest.py` — 89 checks, all green (desktop
  detection for KDE/GNOME/empty/Cinnamon/mixed `KDE:GNOME`, `/proc` session
  scan, `kioslaverc` write→readback→disable round-trip, one-line reporting for
  an unavailable mechanism, log de-duplication, UTF-8 `say()` and log file).
- WSL Ubuntu: a real 150-second `--run` with the real Xray core, stdout piped to
  `systemd-cat`: 59 journal lines, **0** `?????`, 57 lines with Cyrillic, no
  repeating warnings.
- Windows: `v2ray_updater.py --selftest` — PASSED; `forest_ui.py --selftest` —
  304 checks green, 1 known artifact (menu width 453 px instead of the 269 px of
  the mock-up — the stand's font metrics, the same artifact as before; the
  right-click/left-click checks and the `notify.enabled` gate are green).
- Qt branch of the tray menu (Linux code path) — on Qt 6.9 with
  `os.name="posix"`: no `QMenu` is handed to the tray, right click (`Context`)
  opens our menu, left click opens nothing, and both the toggle and the command
  items reach the application (`packaging/check_linux_tray.py` — 11 checks).
- **Live XEmbed panel** (Xvfb + `stalonetray` + `xdotool`, WSL Ubuntu, the built
  `linux/dist/forest-ui`): the icon is embedded (24x24), a **left** click leaves
  0 menu windows, a **right** click opens our window (380x515 at the icon,
  `Map State=IsViewable`), a second right click does not duplicate it, `Esc` and
  a click outside both return it to `IsUnMapped`, and the helper stays alive.
- `notify.enabled` gate on real config files
  (`packaging/check_notify_gate.py`, 9 checks): with `enabled=false` the failure
  card from `tray_status.json`, the `ok→error` event, a service error and a
  direct process card are all suppressed; the manual "Проверить уведомление"
  still works; with `enabled=true` the same failure card is shown.
- Arch package in the `archlinux` container: contents equal to the built ELFs
  (`forest-ui` sha256 `6f644111…`, main `5a47d9fa…`), plasmoid files equal to the
  repo sources, `pacman -U` → run → `pacman -R` leaves no traces.
- Plasmoid: `qmllint` against the real Plasma 6 QML modules — 0 warnings;
  `qmlformat` parses the file; `kpackagetool6 --type Plasma/Applet --install`
  accepts the package (both from the repo and after installation).

### Not verified

- The real DBusMenu round-trip to a KDE panel and the plasmoid on a live Plasma
  session (no Plasma on the stand): right-click behaviour in the user's theme and
  the panel icon must be checked on the VM.

### Packaging (Windows): one file instead of two

- **The interface helper is now embedded in `ForestProxyUpdater.exe`.** A Windows
  user downloads **one** file: `forest-ui.exe` (tray icon, menu, notifications,
  ~36 MB) travels inside the main `.exe` as bundle data
  (`ui_bundle/forest-ui.exe` + `ui_bundle/ui-bundle.json`) instead of being a
  second asset. The helper is still built as its own artifact
  (`packaging/build-ui.ps1`, `forest-ui.spec`) — Linux and manual installs are
  unchanged, and `build.ps1` embeds whatever `dist/forest-ui.exe` currently is
  (staleness against `src/forest_ui.py` is reported as a warning, not silently
  ignored).
- **Unpacking with a SHA-256 check** (`src/ui_bundle.py`): on `--run`/`--ensure`/
  `--install` the program compares `<root>/forest-ui.exe` with the expected hash
  from the embedded manifest. Match — nothing is copied (a log line, ~0.05 s).
  Mismatch or missing — copy to `.new-<pid>`, verify the **copy's** hash,
  `os.replace`, verify the hash of what is now on disk, then launch. A helper that
  is running (file locked) or an unwritable folder never turns into an error
  dialog: the program logs one line and works with the built-in tray icon.
- Measured on a live PC, isolated sandbox (`FOREST_ROOT`, ports 118xx, system
  proxy off, cycles off): cold start **3.9 s** in total, of which unpacking
  **0.20 s**; helper SHA-256 `3c7d1900…` identical to the embedded one; the
  process runs from the permanent folder, not from `_MEI`; second start **2.35 s**
  with no re-copy (same mtime), `копия на месте и совпадает по SHA-256` in the
  log; deleting `forest-ui.exe` restores it on the next start. With the target
  deliberately blocked: no crash, no window, built-in icon, one log line.
- Final release rebuild (helper built from the current `src/forest_ui.py`):
  `forest-ui.exe` `7848453b…` (37 731 130 B). Sandbox re-run: cold start **≈3 s**
  with unpacking **0.20 s** and a matching SHA-256; second start **≈4 s**, copy
  untouched (same mtime); with the target blocked — one `WARN`, built-in icon,
  exit **0**. A `--run` leaves one helper (one PyInstaller bootloader process plus
  its child), never a second window.
- Windows `--selftest` on the built exe: 238 checks, PASSED. (Needs a clean
  environment: a leftover `FOREST_TASK_NAME` in the shell makes the watchdog
  check fail on purpose — the environment overrides the config by design.)
- Size: **68 246 890 bytes (65.09 MiB)**, sha256 `2d14562e…` — was 30.8 MB +
  37.7 MB as two files.

### Not verified (Windows one-file packaging)

- SmartScreen/antivirus behaviour of the 65 MB one-file build (no signed binary).
- Upgrade over a **running** two-file 1.3.1 install: the replacement is designed
  to be skipped while `forest-ui.exe` is locked (the old copy keeps running and
  the new one lands on the next start), checked only by simulating a blocked
  target, not by upgrading a live installation.

### Packaging (Linux): all five formats at 1.3.2, re-verified end to end

- Rebuilt in this pass from the current ELFs (`forest-proxy-updater` `5a47d9fa…`,
  `forest-ui` `6f644111…`): **`.rpm`** (`rpmbuild` in openSUSE Tumbleweed,
  `cadf2f33…`) and **AppImage** (`appimagetool` via
  `--appimage-extract-and-run`, `fb1b2255…`; the self-test inside the mounted
  image passed). The `.deb` (`388d7062…`), Arch package (`f29b4cfe…`) and
  `.tar.gz` (`2ec6f9f7…`) were rebuilt in the same pass, after the shipped
  `INSTALL*.md` was brought to 1.3.2, and then re-extracted to confirm their
  payload hashes; the artifact hashes differ from the first build of this release
  only by those doc files (the ELFs inside are the same).
- Payload extracted from every package and compared with the build tree: `.deb`,
  `.rpm` and Arch carry the main ELF `5a47d9fa…` and the helper `6f644111…`;
  `.tar.gz` and AppImage carry the main ELF (the helper is attached as a separate
  artifact in both portable formats, by design). The plasmoid is present in all
  three system packages, in
  `/usr/share/plasma/plasmoids/org.forest.proxyupdater/`, byte-identical to the
  repository sources (`metadata.json` `8150000e…`, `contents/ui/main.qml`
  `5e3ffcc9…`, `contents/code/read-state.sh` `1145afa4…`).
- Built Linux artifacts, offscreen (`QT_QPA_PLATFORM=offscreen`):
  `forest-proxy-updater --selftest` — **328 checks, 0 failures** (including the 89
  POSIX ones); `forest-ui --selftest` — **158 OK / 2 known artifacts of the
  stand**: menu width `352 px` instead of `269 px` (no Segoe UI in WSL; on a live
  Windows the menu is 269×487 and the run was already green in 1.3.2) and the
  "menu-failure line is in the log" check, which reads `main.log` under
  `FOREST_ROOT` while the line lands in the state directory's log (both files
  exist, the marker is in the log). With a shared `$HOME` the demo-card check
  (`демо-данные: этапы…`) also failed on the "last known process" left over in
  `ui_last_proc.json` (1 step, `1/100000`); with an isolated `HOME`/`XDG_*` it is
  green. `packaging/check_notify_gate.py` — 9 checks PASSED;
  `packaging/check_linux_tray.py` — 10 checks PASSED.
- 5-format matrix, one distribution at a time, port 11808
  (`linux/tests/wsl-matrix.sh --isolate`) on the FINAL artifacts: **66 PASS /
  0 FAIL / 0 SKIP** — install → unit visible → service **active** → a real `204`
  **through the package's own tunnel** → uninstall → no package, no files, no
  units, port closed. `.deb` (Debian 13) 13/13, `204` after ~252 s · `.tar.gz`
  (Ubuntu 26.04) 13/13, ~252 s · AppImage (Ubuntu 26.04, user install) 14/14,
  ~249 s · `.rpm` (openSUSE Tumbleweed) 13/13, ~267 s · Arch `.pkg.tar.zst`
  (Arch rolling) 13/13, ~258 s. The host's live proxy (10808) was not involved.
- `INSTALL.md` / `INSTALL.ru.md` shipped inside every package now name **1.3.2**
  in their examples (the packages were rebuilt after those two files changed; the
  extracted copies show `1.3.1` **0** times and `1.3.2` 18 times).
- `SHA256SUMS` (901 B, 9 entries) covers all nine assets; each artifact also has
  its own `.sha256`.

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
