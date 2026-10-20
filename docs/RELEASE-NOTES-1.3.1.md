# v1.3.1 — мульти-доменный TLS-гейт, параллельная проба и одно уведомление на действие / multi-domain TLS gate, parallel probe, one notification per action

**Дата / Date:** 2026-10-21 · **Тег / Tag:** `v1.3.1`
**Артефакты / Artifacts:** `ForestProxyUpdater.exe` (Windows), `forest-ui.exe`
(помощник Windows), `forest-proxy-updater-linux-x86_64` (Linux ELF),
`forest-ui-linux-x86_64` (помощник Linux), пакеты `.deb` / `.rpm` / Arch
`.pkg.tar.zst` / AppImage / `.tar.gz`, `SHA256SUMS`.
Версия сведена к одной — **1.3.1**: `packaging/version_info.txt`
(`filevers`/`prodvers` = `1, 3, 1, 0`, `FileVersion` = `1.3.1.0`,
`ProductVersion` = `1.3.1`) и `packaging-linux/lib/common.sh` (`VERSION="1.3.1"`).

> **ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** проверка Linux-пакетов выполнялась в среде **WSL**
> (Linux внутри Windows), а не на установленной системе на реальном железе. Меню и
> уведомления графического интерфейса на Linux проверены логически и в offscreen-режиме,
> но **НЕ проверены на живом рабочем столе** (X11/Wayland, настоящий трей) — возможны
> ошибки. AppImage проверен распаковкой (`--appimage-extract-and-run`): штатное
> монтирование (FUSE) в WSL недоступно и не проверялось. Windows-часть проверена на
> живом рабочем столе.
>
> **TESTING SCOPE (EN):** the Linux packages were verified inside **WSL** (Linux on
> Windows), not on a real installation on bare metal. The GUI menu and notifications were
> verified logically and in offscreen mode but were **NOT verified on a live desktop**
> (X11/Wayland, a real tray) — issues are possible. The AppImage was verified by
> extraction (`--appimage-extract-and-run`); normal FUSE mounting is unavailable in WSL and
> was not tested. The Windows part was verified on a live desktop.

---

## 🇷🇺 Русский

### Что было не так

1. **YouTube не открывался при «рабочей связи».** Узел принимался по одному `curl` к
   одному домену, а зонд балансировщика ходил на `gstatic` — узел, который рвёт TLS к
   YouTube, обе проверки проходил. Такой узел попадал в пул, `leastPing` выбирал его,
   и YouTube отвечал `000` за ~40 мс при полностью живом канале.
2. **Пул съёживался на каждом переотборе (10 → 5 → 3 → 2).** Медленное рукопожатие
   сравнивалось с жёстким порогом 2.5 с, поэтому узлы с 2.8–3.1 с вылетали один за
   другим, пока пул не вырождался.
3. **Одиночная проба не видела пачку.** Браузер открывает десятки соединений сразу;
   один запрос этого не замечает, и плохой узел оставался в пуле, пока пользователь
   сам не наткнётся на него.
4. **~90 переотборов за час без настоящей аварии.** Ответ `github.com` в 5–15 с —
   свойство сайта и маршрута, а не узла, но именно он запускал переотбор; а когда
   атрибуция не находила виновника в пуле, цикл просто повторялся через 30–40 с.
5. **Одно действие — пять уведомлений.** Одно нажатие «Обновить серверы сейчас»
   выдавало очередь карточек (`ok` → `error` → `busy` → `ok` → `off`): каждая стадия
   действия поднимала свою карточку.
6. **Кнопка «Обновить серверы сейчас» ничего не меняла.** Запрос из меню шёл быстрым
   путём с замороженным пулом, а не полным циклом.
7. **На Linux параллельной пробы не было вовсе**: переносимый бэкенд не экспортировал
   `parallel_probe`, `burst_guard`, `attribute_bad_nodes` и `ban_bad_nodes`.

### Что сделано

**1. Мульти-доменный TLS-гейт — узлы проверяются РЕАЛЬНЫМ рукопожатием к разным
доменам.** `test.tls_urls` (по умолчанию: `youtube.com/generate_204`,
`gstatic.com/generate_204`, `ru.wikipedia.org`, `i.ytimg.com/generate_204`,
`example.com`), `tls_min_ok = 3` из 5, `tls_max_s = 2.5` с. Вердикт разделяет
**причину**: `reset` / `unreachable` / `mitm` / `few` — жёсткий брак (узел рвёт или не
устанавливает рукопожатие), `slow` — вопрос сравнения с пулом, а не абсолютный приговор.
Зонд балансировщика — YouTube (`watch.balancer_probe_url`); резервный путь прогоняет тот
же YouTube-гейт, поэтому «глушитель» не просачивается в пул через резерв.

**2. Медленный узел сравнивается с МЕДИАНОЙ по пулу, а не с константой.** Отсекаются
только явные лидеры отставания (`tls_slow_factor` 1.8, `tls_slow_margin_s` 1.0 с);
`tls_hard_s` 10 с остаётся абсолютным браком. Дополнительно `watch.pool_floor` не даёт
составу провалиться ниже пола.

**3. Параллельная проба + атрибуция + бан.** 12 запросов каждые `burst_probe_every_s`
= 30 с и 16 запросов × 2 раунда сразу после применения пула; отказы
**атрибутируются конкретным узлам**, виновники уходят в бан на `ban_bad_minutes` = 30.

**4. Переотбор только по делу.** `github.com` объявлен «медленным URL»
(`burst_slow_urls`, стопор 15 с) и переотбор не запускает; неатрибутированная пачка
уходит в **backoff** 5/10/20/30 мин (`burst_backoff_s`); после пересборки пула
переотбор не начинается `burst_cooldown_after_rebuild_s` = 120 с.
Замер на живом трафике: **0 переотборов, 0 пересборок пула, 0 перезапусков ядра за час**
(было ~90).

**5. ОДНО ДЕЙСТВИЕ = ОДНА КАРТОЧКА.** Карточки склеиваются в окне 2.5 с
(`CARD_COALESCE_MS`), карточка процесса помечает своё время, ответ команды показывается
один раз (раньше повторялся на каждом тике младше 6 с). Полная серия доступна явным
демо-режимом `--show-all`; оба поведения закреплены самотестом.

**6. «Обновить серверы сейчас» — действительно полный цикл.** Запрос из трея идёт с
`force_discovery` (свежие списки) и `pool_force` (свежий пул применяется даже если состав
выглядит неизменным) — тем же путём, что и плановый цикл.

**7. Балансировщик: `leastLoad` + `burstObservatory`** (таймаут 2 с, интервал 15 с,
выборка 2) вместо `leastPing`: тормозящий или мёртвый узел уходит из ротации за секунды.
Возврат к `leastPing` — сменой `watch.balancer_strategy`, без пересборки.

**8. UTF-8 явно.** Все чтения и записи состояния, конфига и вывода проб, которых касается
этот выпуск, указывают **UTF-8** явно — кириллический статус больше не зависит от кодовой
страницы хоста.

**9. Переносимость.** `parallel_probe`, `post_validate_pool`, `attribute_bad_nodes`,
`ban_bad_nodes`, `burst_guard`, `burst_trigger`, `cycle_running` экспортируются и
Windows-, и POSIX-бэкендом; наличие каждой закреплено `--selftest`.

### Замеры этого выпуска

| Что | Значение |
| --- | --- |
| Переотборы за час | **0** (было ~90) |
| Пересборки пула за час | **0** |
| Перезапуски ядра за час | **0** |
| Сайты (живая связь) | **7 из 7**, отказов **0 на 840 запросах** |
| YouTube | **3 из 3** |
| `204` локально / SOCKS / LAN | **да** (HTTP/SOCKS/LAN) |
| `github.com` как причина срабатываний | больше **не вызывает** |
| Пул | стабилен |

### Установка

```bash
# Debian / Ubuntu / Mint
sudo apt install ./forest-proxy-updater_1.3.1_amd64.deb
# Fedora / Rocky / Alma / openSUSE
sudo dnf install ./forest-proxy-updater-1.3.1-1.x86_64.rpm       # или: sudo zypper install ./*.rpm
# Arch / Manjaro / EndeavourOS
sudo pacman -U forest-proxy-updater-1.3.1-1-x86_64.pkg.tar.zst
# Любой дистрибутив (glibc x86_64) — без root
chmod +x ForestProxyUpdater-1.3.1-x86_64.AppImage && ./ForestProxyUpdater-1.3.1-x86_64.AppImage --install
# Универсальный tar.gz
tar xzf forest-proxy-updater-1.3.1-linux-x86_64.tar.gz && cd forest-proxy-updater-1.3.1-linux-x86_64 && sudo ./install.sh
```

Проверка после установки: `curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n'
https://www.gstatic.com/generate_204` → `204` (первые минуты уходят на подбор рабочих
серверов).

---

## 🇬🇧 English

### What was wrong

1. **YouTube refused to load while "the connection worked".** A node was admitted by a
   single `curl` to a single domain and the balancer probe used `gstatic` — a node that
   tears down TLS to YouTube passed both. It entered the pool, `leastPing` picked it, and
   YouTube answered `000` in ~40 ms on a perfectly live link.
2. **The pool shrank on every re-selection (10 → 5 → 3 → 2).** A slow handshake was judged
   against a fixed 2.5 s threshold, so nodes at 2.8–3.1 s were dropped one round after
   another until the pool degenerated.
3. **The single probe could not see a burst.** A browser opens dozens of connections at
   once; one request notices nothing, so a bad node stayed in the pool until the user ran
   into it.
4. **~90 re-selections per hour with no real fault.** A `github.com` answer in 5–15 s is a
   property of the site and the route, not of the node — yet it started a re-selection;
   and when attribution found no culprit in the pool the cycle simply repeated 30–40 s later.
5. **One action, five notifications.** One "refresh now" click produced a queue of cards
   (`ok` → `error` → `busy` → `ok` → `off`), because every stage of the action raised its own.
6. **The "refresh now" button changed nothing.** The tray command took the quick path with a
   frozen pool instead of a full cycle.
7. **On Linux there was no parallel probe at all**: the portable back end did not export
   `parallel_probe`, `burst_guard`, `attribute_bad_nodes` or `ban_bad_nodes`.

### What was done

**1. Multi-domain TLS gate — nodes are verified by a REAL handshake to different domains.**
`test.tls_urls` (defaults: `youtube.com/generate_204`, `gstatic.com/generate_204`,
`ru.wikipedia.org`, `i.ytimg.com/generate_204`, `example.com`), `tls_min_ok = 3` of 5,
`tls_max_s = 2.5` s. The verdict separates the **reason**: `reset` / `unreachable` /
`mitm` / `few` are a hard reject (the node tears down or cannot establish a handshake),
while `slow` is a pool-relative question, not an absolute sentence. The balancer probes
YouTube (`watch.balancer_probe_url`) and the reserve path runs the same YouTube gate, so a
"mute" node cannot slip in through the reserve.

**2. A slow node is compared with the MEDIAN of the pool, not with a constant.** Only clear
outliers are dropped (`tls_slow_factor` 1.8, `tls_slow_margin_s` 1.0 s); `tls_hard_s` 10 s
stays an absolute reject. `watch.pool_floor` additionally keeps the composition from
dropping below the floor.

**3. Parallel probe + attribution + ban.** 12 requests every `burst_probe_every_s` = 30 s
and 16 requests × 2 rounds right after the pool is applied; failures are **attributed to
specific nodes** and the culprits are banned for `ban_bad_minutes` = 30.

**4. Re-selection only when deserved.** `github.com` is declared a slow URL
(`burst_slow_urls`, stall 15 s) and never starts a re-selection; an unattributed burst
**backs off** 5/10/20/30 min (`burst_backoff_s`); no re-selection is started for
`burst_cooldown_after_rebuild_s` = 120 s after a pool rebuild. Measured on live traffic:
**0 re-selections, 0 pool rebuilds, 0 core restarts in an hour** (was ~90).

**5. ONE ACTION = ONE CARD.** Cards coalesce in a 2.5 s window (`CARD_COALESCE_MS`), the
process card stamps its own time, and a command reply is shown once (it used to reappear on
every tick younger than 6 s). The full series remains available through the explicit
`--show-all` demo mode; both behaviours are pinned by the selftest.

**6. "Refresh now" really is a full cycle.** The tray command runs with `force_discovery`
(fresh lists) and `pool_force` (the new pool is applied even when the composition looks
unchanged) — the same path as the scheduled cycle.

**7. Balancer: `leastLoad` + `burstObservatory`** (2 s timeout, 15 s interval, sample 2)
instead of `leastPing`: a stalling or dead node leaves the rotation within seconds.
`watch.balancer_strategy` switches back to `leastPing` without a rebuild.

**8. UTF-8 made explicit.** Every state, config and probe read/write touched by this release
specifies **UTF-8** explicitly, so the Cyrillic status no longer depends on the host code page.

**9. Portability.** `parallel_probe`, `post_validate_pool`, `attribute_bad_nodes`,
`ban_bad_nodes`, `burst_guard`, `burst_trigger` and `cycle_running` are exported by both the
Windows and the POSIX back end, and the presence of each is pinned by `--selftest`.

### Measurements in this release

| Item | Value |
| --- | --- |
| Re-selections per hour | **0** (was ~90) |
| Pool rebuilds per hour | **0** |
| Core restarts per hour | **0** |
| Sites (live connection) | **7 of 7**, **0 failures in 840 requests** |
| YouTube | **3 of 3** |
| `204` locally / SOCKS / LAN | **yes** (HTTP/SOCKS/LAN) |
| `github.com` as a trigger cause | **no longer causes** any |
| Pool | stable |

### Install

```bash
sudo apt install ./forest-proxy-updater_1.3.1_amd64.deb                    # Debian/Ubuntu/Mint
sudo dnf install ./forest-proxy-updater-1.3.1-1.x86_64.rpm                  # Fedora/Rocky/Alma (openSUSE: zypper)
sudo pacman -U forest-proxy-updater-1.3.1-1-x86_64.pkg.tar.zst              # Arch/Manjaro/EndeavourOS
chmod +x ForestProxyUpdater-1.3.1-x86_64.AppImage && ./ForestProxyUpdater-1.3.1-x86_64.AppImage --install
tar xzf forest-proxy-updater-1.3.1-linux-x86_64.tar.gz && cd forest-proxy-updater-1.3.1-linux-x86_64 && sudo ./install.sh
```

After installing: `curl -x http://127.0.0.1:10808 -o /dev/null -w '%{http_code}\n'
https://www.gstatic.com/generate_204` → `204` (the first minutes go to finding working
servers).

---

## Артефакты и контрольные суммы / Artifacts and checksums

Полный список — в `SHA256SUMS` рядом с этой страницей; каждый артефакт имеет свой
`.sha256`. Проверка: `sha256sum -c SHA256SUMS`.

Ассеты с пометкой `-linux-x86_64` — это Linux-релизы без пакета (сам ELF и помощник
интерфейса), они не требуют установки через менеджер пакетов.
