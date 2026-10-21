# Architecture

This document describes how FOREST's Proxy Auto-updater is built inside:
modules, threads, on-disk state, the config format, where the logs are, and how
the balancer and the checks work. It is written for someone reading or
maintaining the code. For end-user usage see [`../README.md`](../README.md).

The code is plain Python and uses **only the standard library**. External
programs it relies on at runtime are `curl.exe` (all HTTP probing and
subscription fetching), plus `PowerShell`, `tasklist`, `taskkill` and `netstat`
for process/port queries on Windows.

## Module map (`src/`)

| Module | Responsibility |
|---|---|
| `v2ray_updater.py` | Entry point and CLI. Owns the cycle (`run_cycle`), the guard (`run_guard`), the supervisor (`run_supervisor`), install/uninstall, status, self-test dispatch, and a cross-process cycle lock. |
| `config.py` | Configuration loading and defaults, state-directory resolution, core (`xray.exe`) discovery, client-mode detection (standalone/v2rayN), the proxy chain used to fetch subscriptions. |
| `clientwrite.py` | The core of the client logic: connectivity/speed checks, blacklist, `Server`↔row conversion, the JSON server store, core-config and balancer-config generation, config validation, file locks, the **standalone back-end** (own `xray.exe` process, own server store, own system proxy via the WinINET registry), failover/guard, the state store and hot cache, and the **mode dispatcher** that routes calls to the v2rayN back-end when needed. |
| `clientwrite_v2rayn.py` | The `v2rayn` back-end: read/write of v2rayN's SQLite `guiNDB.db` and `guiNConfig.json`, DB backups, activation via stop→write→start→verify→rollback, guard, GUI helpers. Used **only** when `client.mode == "v2rayn"`. |
| `clientwrite_posix.py` | A POSIX port of the standalone back-end (xray as a `systemd --user` service). Not used on Windows; kept for a future Linux port. |
| `watch.py` | The watchdog: the fast local loop that keeps the core alive, notices a dead port, monitors throughput and the YouTube canary, and calls for a full cycle on total failure. |
| `parser.py` | Parsing of share links (`vless://`, `vmess://`, `trojan://`, `ss://`) into a unified `Server` dataclass, plus the reverse (`to_share_link`, `to_row_dict`). |
| `sources.py` | Subscription sources: GitHub search for fresh repos, freshness by content sha1, fixed-URL refresh, source bodies cached to disk. |
| `xraytest.py` | Candidate testing through throwaway Xray instances: RTT, TCP pre-filter, speed test, YouTube probe, final verify, plus cleanup of orphaned test cores. |
| `netfilters.py` | Subnet-diversity selection. |
| `tray.py` | The tray. **Primary path**: the built-in icon — a layered window (`UpdateLayeredWindow`), a menu with submenus (`tray_menu.py`), the user's `app.ico` as-is plus a state badge in the corner (`tray_icon.py`). While the Qt helper (`forest-ui`) is alive, this thread waits **in reserve** and takes the interface over if the helper stops. |
| `tray_icon.py` | The icon itself: reads `app.ico` (own `ICO`/PNG decoder — no Pillow), copies the user's artwork **with not a single altered pixel** and draws a state badge (`mark_state`) in the bottom-right corner, produces `.ico` data for four states and `HICON` handles for WinAPI. |
| `safestdio.py` | Safe stdout/stderr (`utf-8` + `errors="replace"`), ASCII-only `say()`, UTF-8 report files, `crash_log()` and `guarded()` — any unhandled exception goes to `logs/crash.log` with exit code 70 instead of a PyInstaller modal window. |
| `tray_menu.py` | Menu semantics with no Win32: commands, state, filters, supervision, notifications, theme; writes `config.json` atomically. Also the **only** place that performs a command — the Qt helper's clicks land here too. |
| `tray_status.py` | The status channel (`state/tray_status.json`): one atomic state + current process description; the source of the icon colour for both the built-in tray and the helper. |
| `notify.py` | Windows notifications (the fallback path): a layered window drawn with one `UpdateLayeredWindow` per frame, a **high-resolution waitable timer** with an absolute frame timeline (60 fps, no drift), never takes focus. On Linux it sends `org.freedesktop.Notifications` via `gdbus`, then `notify-send`. |
| `theme.py` | One source of truth for the light/dark palettes (notification card, tray icon, menu QSS), automatic detection (Windows registry / `gsettings`) and `contrast()`/`state_contrasts()` WCAG checks. |
| `instancelock.py` | **Single-instance lock**: an exclusive file lock held for the whole life of a process (`msvcrt.locking`/`LockFile` on Windows, `fcntl.flock` on POSIX). The kernel releases the lock together with the process, so stale locks cannot happen and the start-up race is decided atomically. Used by the supervisor (`watchdog.py`) and the helper (`forest_ui.py`). |
| `watchdog.py` | Autonomous supervision: the **supervisor lock** (`state/supervisor.lock`, see `instancelock.py`) as the only basis for the “a second copy is not needed” decision, detached relaunch, Task Scheduler XML (logon trigger + repetition), `apply_watchdog`/`delete_task`, `--watchdog on\|off\|status`. |
| `ui_cmd.py` | The command bridge between the helper and the program: `state/ui_cmd.json` in, `state/ui_cmd_result.json` out. The supervisor performs each command through `tray_menu` — the helper never touches the core. |
| `ui_helper.py` | Lifecycle of the helper process: locate `forest-ui(.exe)`, detached launch, stop, `ensure()`. Whether the helper runs is decided by its **lock** (`state/forest-ui.lock`); the heartbeat (`state/ui_helper.json`) is only used to show the pid. Launching is serialised by a short lock, so two supervisors never bring up two helpers. |
| `ui_bundle.py` | The embedded helper for the Windows “one file” build: the helper travels inside `ForestProxyUpdater.exe` as bundle data (`ui_bundle/forest-ui.exe` plus a manifest with its SHA-256); on `--run`/`--ensure`/`--install` it is compared with the copy in the permanent folder (`<root>\forest-ui.exe`) and, only when the hash differs, unpacked there atomically (copy → verify → `os.replace` → verify). It is never run from `_MEI`. On failure: one log line and the built-in icon. |
| `forest_ui.py` | The helper itself (Qt/PySide6): `QSystemTrayIcon`, `QMenu` with submenus, QSS from our palette, **notification card** (`QPropertyAnimation` slide + fade, 60 fps, never takes focus), the same visuals as the built-in icon, `--selftest`/`--menu-dump`/`--show` for evidence. On Linux notifications go through the system service. |
| `i18n.py` | Minimal Russian/English string table and language selection. |
| `log.py` | Logger: UTF-8 file plus a console-safe writer; picks a writable log directory. |
| `selftest.py` | The self-test suite (`--selftest`): API surface, store round-trip, config generation, mode dispatcher, i18n, first-run config, core discovery. Network-free, touches no system proxy, occupies no real ports. |

## The interface: two processes, one owner of the core

```
ForestProxyUpdater.exe  (supervisor)                forest-ui.exe  (Qt helper)
  watch/guard/update threads                          tray icon (colour = state)
  tray thread ── in reserve while the helper lives     menu with submenus
  reads  state/ui_cmd.json  ──────────────────────►    writes state/ui_cmd.json
  applies it via tray_menu.py                          reads  state/tray_status.json
  writes state/ui_cmd_result.json ◄────────────────     writes state/ui_helper.json (heartbeat)
  writes state/tray_status.json                        notifications (card / D-Bus)
```

* The helper is a **separate program** (Qt/PySide6, LGPL-3.0) so the main program
  stays small and dependency-free. It **never touches the core**.
* A click in the Qt menu becomes a command record in `state/ui_cmd.json`; the
  supervisor consumes it (`ui_cmd.consume`) and performs it with exactly the same
  functions the built-in menu uses (`tray_menu.TrayActions`). The result goes to
  `state/ui_cmd_result.json` and is shown to the user as a notification.
* State flows the other way through `state/tray_status.json` (written by the
  watchdog) — that is where the icon colour and the tooltip come from, for both
  interfaces.
* Who owns the interface is decided at runtime: if the helper is alive, the
  built-in tray thread waits in reserve (no second icon, no duplicate
  notifications); if the helper stops, the built-in icon takes over within
  seconds. No helper at all — everything falls back to `tray.py` + `notify.py`.

## Threads

A single process (the supervisor, `run_supervisor`) starts four daemon threads:

| Thread | Loop | Function |
|---|---|---|
| `watch` | continuous, `watch.check_interval_s` | `watch.run_watch` — keep the core alive, rebuild the pool in 1–2 s when the port dies, monitor speed and YouTube; call `full_cycle` on total failure. |
| `guard` | every 300 s | `v2ray_updater._guard_loop` → `run_guard` — one recovery pass. |
| `update` | every 30 min (unless `FOREST_NO_CYCLE`) | `v2ray_updater._update_loop` → `run_cycle` — a full selection cycle. |
| `tray` | every ~2 s | `tray.run_tray` — consume the status channel. |

The supervisor stops on `Ctrl-C`, on a `state/stop.flag` file, or after
`FOREST_RUN_SECONDS` (used by tests), and then calls `stop_core`.

`--watch` runs the watchdog alone as a separate process (guarded against
duplicates by `state/watch.pid`).

## The selection cycle (`run_cycle`)

1. Resolve the client (`config.find_client`) and require a core.
2. Probe the current active server; remember a healthy baseline.
3. **Sources:** run GitHub discovery if due, refresh all known sources, refresh
   the fixed URLs. Only sources whose content hash changed provide new servers.
4. **Candidates:** parse changed sources, add the user's own stored servers
   (they are preserved and never dropped in favour of auto ones), de-duplicate;
   if the pool is small, top it up from the pool cache and cached source bodies;
   cap at `test.max_candidates` (prioritising a few known-good sources).
5. **Tests (`xraytest.run_tests`):** TCP pre-filter → RTT per candidate (one
   throwaway Xray instance each) → jitter filter → `max_rtt_ms` filter →
   diversity-capped top-`top_for_speed` → speed test → `min_speed_kbps` filter →
   `final_verify` (RTT again + YouTube reachability).
6. **Rank:** by speed (desc) then RTT (asc); apply the blacklist; if the pool is
   below `reserve_min`, add back servers that passed RTT.
7. **Diversity cap:** at most `diversity.per_subnet_cap` servers per /24; if that
   shrinks the pool below `diversity.min_pool_after_cap`, the cap is relaxed up to
   `diversity.max_cap_relax`.
8. Update the hot cache; write `subscription_base64.txt`.
9. **Choose the active server:** keep the current one if it is healthy and not
    clearly beaten (`activation.prefer_current_if_healthy`), otherwise the top
    ranked.
10. If the proxy was switched off by hand (`proxy_off.flag`), write the store
    but do not activate.
11. **Activate** (`clientwrite.activate`): in balancer mode raise the balancer;
    otherwise write a single-server core config and restart the core; then fall
    back to the emergency "last good" pool if needed.

A file lock (`state/lock.json`) prevents two cycles from running at once.

## Balancer and checks

**Balancer.** In balancer mode the servers that passed are written into one Xray
config by `build_balancer_config`: one inbound (mixed SOCKS/HTTP on the client
port) plus a second SOCKS inbound (`torrent-in`, `client.torrent_port`), a pool
of `proxy-out-*` outbounds, a `direct` (freedom) and a `block` (blackhole)
outbound, an `observatory` that health-probes the pool every 5 s, and a
`leastPing` balancer with selector `proxy-out`. Xray then routes new connections
to the least-ping live node **without a core restart**, so a single node dying
does not break the connection. Routing rules: local names → direct, `torrent-in`
→ balancer, (optionally) RU domains/IPs → direct, everything else → balancer.
Only servers with a non-empty password/ID enter the pool (an empty one produces
an invalid outbound that would break the whole config).

**Guarding the config and seamlessness.** Before ANY core restart the new config
is validated **before** the running core is stopped:

1. a fast check by the core itself — `xray run -test -config <file>` (no ports
   are bound, the live core is untouched); if a core build does not understand
   `-test`, the program falls back to `_validate_config` (start on free
   ephemeral ports while the current config still runs);
2. fingerprint comparison: `_conf_sha` of the freshly built config against
   `_cfg_sha` of the file the running core actually uses (also kept in
   `state.json` as `balancer_cfg_sha`), plus the pool fingerprint
   (`balancer_sig`). Equal means **the core is not touched at all** (even on
   `force=True`). Otherwise, if the core is ours, the link works and the pool is
   frozen (`watch.pool_freeze_s`), no restart happens either;
3. only then the config is replaced atomically and the core restarted — with the
   **link gap measured in milliseconds** (`_restart_core_timed`): from stopping
   the core to the new one accepting the port and to confirmed data transfer.
   There is no fixed sleep after stopping any more; the port is polled for
   release. Hashes of successful checks are cached so identical configs are not
   re-validated within an hour.

**Connectivity checks.** `probe_client` fetches
`https://www.gstatic.com/generate_204` (or the Cloudflare endpoint on retries)
through the local SOCKS port and, in strict mode, also downloads data from
`speed.cloudflare.com` and checks the TLS verify result — so a "stub" server
that answers 204 but carries no traffic, or one that substitutes the TLS
certificate, is not accepted. `port_listening` is a fast TCP check used by the
watchdog.

**Watchdog (`watch.py`).** After each probe interval it re-probes the port with
retries; every few ticks it re-checks the system proxy, refreshes the pool,
probes YouTube (3 failures → full cycle) and measures throughput (2 slow hits →
rebuild the pool, else full cycle). While it sleeps it watches the port every
50 ms, so a dead core is noticed in 1–2 s. Consecutive failures (`max_strikes`)
or a dead port trigger a balancer rebuild; if that fails, a full cycle.

**Guard (`run_guard`).** If the proxy was switched off by hand, it does nothing.
Otherwise it tries the balancer, then `guard_cycle` failover over the stored
`[AUTO]` servers (TCP pre-filter, RTT, try the best few in turn, blacklist the
ones that fail), then the emergency pool, then a full cycle.

**Emergency "last good" pool.** `ensure_balancer` saves the last working pool to
`state/lastgood_servers.json`; when no live servers can be found,
`_emergency_restore` raises that pool (rate-limited to once per 120 s).

## On-disk state

By default everything lives under `%LOCALAPPDATA%\ForestProxyUpdater`
(override with `FOREST_ROOT` or `paths.root`). The source tree stays clean.

| File | Written by | Content |
|---|---|---|
| `config.json` | `config.load_config` | The working config (created from `config.example.json` on first run). |
| `bin/xray/{xray(.exe),geoip.dat,geosite.dat,LICENSE}` | `config.ensure_core_installed` | **Permanent copy of the core and both geo databases** (Windows: `%LOCALAPPDATA%\ForestProxyUpdater\bin\xray`, Linux: `~/.local/share/forest-proxy-updater/bin/xray`). On first run (or when the bundle holds a newer version — size and SHA-256 are compared) the files are copied here out of the unpacked onefile directory (`sys._MEIPASS/bin/xray`), and the core is started **only from here**. The unpack directory `_MEIxxxx` lives until the program exits, while the core deliberately outlives it: started from `_MEI*` it kept the file busy, so the PyInstaller cleanup could not delete the directory (modal “Failed to remove temporary directory” window) and left `_MEI*` junk in temp. Our own stale `_MEI*` folders from temp are removed in the background at startup (`config.cleanup_stale_bundles`) — only those containing our `bin/xray`, never the current one and never one a process is still running from. |
| `logs/main.log` | `log` | Main log, UTF-8. |
| `state/servers.json` | `clientwrite` (standalone) | Own server store: `{next_index, active_id, servers:[...]}`. Auto servers are tagged `[AUTO] `. |
| `state/forest_xray.json` | `clientwrite` | Config of our own core. |
| `state/forest_xray.log` | the core | Output of our own core. |
| `state/core.pid` | `clientwrite` | PID of our own core. |
| `state/state.json` | `StateStore` | Internal state: hot cache, blacklist, balancer signature/count/timestamp, last probe marks, geo-asset path, validation hash, cycle timestamps. |
| `state/tray_status.json` | `tray_status` | Status channel for the (future) tray icon. |
| `state/sources.json` | `source` manager | Known sources with their last sha1, last-ok time and fail counts. |
| `state/bodies/` | `sources` | Cached bodies of each source (re-parseable without network). |
| `state/pool_cache.json` | `v2ray_updater` | Recent candidate share links. |
| `state/subscription_base64.txt` | `v2ray_updater` | Current pool as a base64 subscription. |
| `state/lastgood_servers.json` | `clientwrite` | Last working pool (emergency restore). |
| `state/proxy_orig.json` | `clientwrite` | System-proxy state saved before the first change. |
| `state/proxy_off.flag` | `clientwrite` | Present when the proxy was switched off by hand. |
| `state/lock.json`, `state/watch.pid`, `*.lock` | lock helpers | Cross-process locks and the watchdog singleton. |

Config writes and most state writes are atomic (`tmp` file + `os.replace`).

## Config format

The config is a single JSON object. Missing keys are filled in from `DEFAULTS`
in `config.py`, so a partial `config.json` is valid. On first run with no
config, `config.example.json` is copied to `config.json`. Top-level sections:
`ui`, `client`, `cycle`, `test`, `sources`, `limits`, `activation`, `watch`,
`diversity`, `blacklist`. A resolved `paths` block
(`root`/`state_dir`/`backup_dir`/`log_dir`) is added at load time and is not
meant to be edited by hand. See the tables in
[`../README.md`](../README.md#key-settings-from-configexamplejson) for every key.

## The two modes and the dispatcher

`clientwrite._mode_of` decides the mode. `read_db_rows`, `write_auto_group`,
`read_active_id`, `write_active_id`, `activate`, `ensure_balancer`,
`guard_cycle`, `kill_kernels`, `start_client`, `stop_client`, `write_bin_config`
and `ensure_port_free` route to the standalone implementation or to
`clientwrite_v2rayn` depending on mode and on whether the database path is a
SQLite `.db` (v2rayN) or our JSON store. The rest of the program talks only to
this dispatcher, so the two modes share `v2ray_updater.py` and `watch.py`
unchanged.

## Logs and diagnostics

- Main log: `%LOCALAPPDATA%\ForestProxyUpdater\logs\main.log`. If that
  directory is not writable, the logger falls back to a `logs` directory next to
  the code, then to `%LOCALAPPDATA%\ForestProxyUpdater\logs`.
- Core output: `state/forest_xray.log`; validation output:
  `state/forest_validate.log`.
- `--status` prints mode, core path, whether the core is running, pool size,
  system-proxy state and connectivity.
- `--dry-run` runs a full cycle without writing anything or starting the core.

---

Русская версия: [`ARCHITECTURE.ru.md`](ARCHITECTURE.ru.md).
