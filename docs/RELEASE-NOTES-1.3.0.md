# v1.3.0 — уведомление 1:1 с макетом, иконка пользователя, ровные 60 кадров/с / the notification exactly as in the mock-up, the user's icon, a steady 60 fps

**Дата / Date:** 2026-10-18 · **Тег / Tag:** `v1.3.0`
**Артефакты / Artifacts:** `ForestProxyUpdater.exe` (Windows), `forest-ui.exe`
(Windows helper), `forest-proxy-updater` (Linux ELF), `forest-ui` (Linux helper),
пакеты `.deb` / `.rpm` / Arch `.pkg.tar.zst` / AppImage / `.tar.gz`.
Версия сведена к одной — **1.3.0** (`FileVersion 1.3.0.0`, `ProductVersion 1.3.0`):
до выпуска встречались пометки «1.3.1» (сборки меню) и не поднятый `filevers=(1,2,2,0)`.
Отдельного выпуска 1.3.1 не существует.

---

## 🇷🇺 Русский

### Что было не так

1. **Вид уведомления не совпадал с утверждённым макетом.** Карточка была 360 px,
   слева рисовалась своя полоска, этапы выводились строками вида
   `✓ Обрыв обнаружен (пульс порта)   50 мс (0.05 с)`, вместо полосы времени
   стоял `QProgressBar` с процентами, а спиннер рисовался дугой поверх значка.
2. **Значка пользователя в уведомлении не было как такового** — рисовался значок
   состояния, а не тот же рисунок `app.ico` с меткой состояния, что в трее.
3. **Плавность была неровной.** Таймер кадров проверял сетку 60 к/с лишь вдвое
   реже периода, поэтому часть кадров выходила 25 мс вместо 16.7 мс, а в момент
   перехода к финалу кадр выпадал целиком: замер в WSL показывал **58 мс**.
4. **На Linux своего уведомления не было** — этапы и время уходили текстом в
   системное уведомление D-Bus, вид был совсем другой.
5. **Запасной путь без Qt показывал только текст события** — ни этапов, ни
   времени восстановления.
6. **Пакеты не объявляли системные библиотеки помощника.** Без них плагин
   платформы Qt не загружается, и помощник не стартует вовсе (проверено: в
   минимальной Ubuntu `forest-ui` падал с `libxkbcommon-x11.so.0: cannot open
   shared object file`).

### Что сделано

**1. Карточка — 1:1 с макетом `docs/notify-mockup.html`.** Числа взяты из макета
и **сверяются с ним самотестом** (файл макета едет в комплекте, проверка работает
и у собранной программы): ширина 392 px, скругление 12, полоса состояния 3 px
слева, отступы 13/15/12/14, квадрат значка 34×34 (скругление 9), зазор 11,
спиннер 18 px (отступ 4), отступ этапов 45 px, маркер этапа 13 px, зазор 6,
полоса времени 3 px, вход 340 мс (`cubic-bezier(.16,.84,.28,1)`), выход 260 мс
(`ease-in`), сдвиг 420 px, спиннер 0.85 с на оборот, этап считается пройденным
через 6 % полосы. Цвета состояний — из макета: ok `#41c26d`, error `#dc4f55`,
busy `#e5bf37`, off `#9497a0`.

Состав — как в оригинале (`tray.ps1`): заголовок по состоянию
(`Восстановление связи` → `Связь восстановлена`, `Переключение сервера` →
`Переключение завершено`, при провале `Восстановить не удалось`), подпись
(`подбор живого сервера`, `обрыв обнаружен · пульс порта`, `связь подтверждена ·
N.NN с`), **блок этапов с временем каждого в миллисекундах**, полоса времени,
спиннер и футер (`восстановлено за N.NN с · окно закроется через 4 с`).

**2. Иконка пользователя внутри уведомления.** Карточка рисует **тот же
`packaging/app.ico` и тем же кодом**, что значок трея
(`tray_icon.state_ico`: рисунок без изменений + метка состояния в углу).
Доказательство — сравнение пикселей того, что карточка реально нарисовала, с
растром значка трея:

```
значок карточки против значка трея: 1024/1024 пикселей совпадают (100.0%)
```

**3. Ровные 60 кадров/с и замер вместо обещания.** Кадры идут по абсолютной сетке
времени (`n/60`), таймер проверяет её вчетверо чаще периода; за кадр меняются
только положение окна и прозрачность, а фон, полоса состояния, квадрат значка,
рисунок, заголовок, подпись, дорожка полосы и футер рисуются **один раз** в
отдельный слой (`QPixmap`) — мерцать нечему. Финальный слой **готовится заранее**:
раньше в момент финала кадр выпадал (в WSL — **58 мс**), теперь это подмена
готового слоя (**20.3 мс** там же).

Замер на живом ПК (собранный `forest-ui.exe --selftest`):

```
кадров=388 (отрисовок=388) средний FPS=60.1 (отрисовок 60.2); цель 60 к/с
худший интервал кадра=21.4 мс (без прогрева 21.4 мс), таймера=21.5 мс,
пропущено кадров=0 (после входа 0), залипаний=0
фаза входа=340 мс (макет 340); разных положений=32, прозрачностей=31,
значений полосы=112
размер карточки: 392x194 (ширина из макета 392)
финал достигнут на 2190.1 мс (макет: 1850 мс + вход 340 мс)
```

**4. Фокус не забирается — тоже с доказательством.** Самотест следит за активным
окном всю анимацию и проверяет, что окно карточки ни разу не стало активным, а
окно пользователя не сменилось:

```
активное окно: до=199442, после=199442; за время показа карточки окно менялось
на: [199442] (карточка НИ РАЗУ не становилась активной: 0 раз)
```

**5. Финал — в той же карточке.** Когда супервизор сообщает об окончании
процесса, карточка на экране меняет состояние (заголовок `Связь восстановлена`,
подпись `связь подтверждена · 1.85 с`, футер `восстановлено за 1.85 с · окно
закроется через 4 с`, спиннер становится зелёным) и держится 4 с — второй раз
она не выезжает.

**6. На Linux — та же карточка.** Помощник рисует её и там (проверено в WSL на
`offscreen`, `xcb` и `wayland`), а если графической сессии нет или окно не
создалось — откат на системное уведомление D-Bus, куда этапы с временами уходят
**в теле**. Настройка `notify.card_on_linux: false` принудительно включает откат.

**7. Запасной путь без Qt (`src/notify.py`) показывает то же содержимое.** В его
слоистой карточке появился блок этапов (`✓ Обрыв обнаружен (пульс порта)   50 мс`)
и футер (`восстановлено за 1.85 с (1850 мс) · окно закроется через 4 с`);
`tray._notify_details` берёт их из состояния сторожа, поэтому встроенный значок
показывает тот же состав, что помощник.

**8. Зависимости пакетов.** Плагины платформ Qt (`xcb`, `wayland`, `offscreen`) и
библиотеки Qt **в комплекте**; системные библиотеки объявлены в трёх форматах:

| Формат | Объявлено |
|---|---|
| `.deb` | `libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xkb1, libgl1, libegl1` |
| `.rpm` | те же библиотеки **по SONAME** (`libxkbcommon-x11.so.0()(64bit)` …) — имена не зависят от дистрибутива |
| Arch | `libxkbcommon-x11, xcb-util-cursor, xcb-util-wm, xcb-util-image, xcb-util-keysyms, xcb-util-renderutil, libxcb` (+ `libglvnd`, `mesa` как необязательные) |

Имена и sonames проверены по **живым** пакетам Arch (`extra`, 17.10.2026) и по
факту установки в Ubuntu. Проверка «до/после»:

```
без библиотек: libqxcb.so cannot load: libxkbcommon-x11.so.0: cannot open shared object file
после установки: плагин xcb загружается, forest-ui --menu-dump работает
```

**9. Трей: одно меню в едином стиле, подменю открывается НАВЕДЕНИЕМ.** Меню
рисует своё окно (`src/ui_menu.py` + `src/ui_style.py`, безрамочный `Qt.Popup`,
а не `QMenu`), поэтому панель, строки, полоса состояния, подсветка и подменю
берут **одну таблицу стиля** и один слой текста. Подменю открывается наведением
через задержку стиля (150 мс) — на живом ПК замерено **159-163 мс**; закрывается
через льготное окно 280 мс (**283 мс** в замере); простой **пронос** курсора
подменю НЕ раскрывает; переход на другой пункт с подменю **переиспользует ту же
панель** (4.7-6.0 мс); возврат курсора на свой пункт отменяет закрытие.
Клавиатура как прежде: «вправо» открывает, «влево»/Esc закрывают, стрелки сами
подменю не раскрывают. Пункт фильтра читается **«РФ — напрямую»**
(`tray.menu.filter_ru`), галочка идёт за живой настройкой.

**10. Подсветка успевает за курсором.** Движение мыши переносит подсветку
**в том же событии ввода** и проявляет её коротким темпом 50 мс; клавиатурная
навигация сохранила плавные 180 мс. Замер на живом ПК (100 % / 200 Гц и
150 % / 60 Гц): медиана курсор → подсветка на строке **12 мс** (11-14) и
**15 мс** (2-17) вместо 700-990 мс; кадров с промежуточной подсветкой **0**
(было 467-522); кадров с двумя полосами подсветки **0**; полоса «на своей
строке» **6 из 6** шагов на обоих мониторах.

**11. Правый клик по значку больше не роняет помощник.** Обработчик активации
строил меню внутри блока, который перебрасывал ошибку наружу, и падение меню
уносило весь помощник (`RuntimeError: не удалось открыть своё меню (повтор)`).
Теперь ошибка перехватывается, след уходит в `logs/crash.log`, а меню работает
со следующего открытия. Доказательство — **5 настоящих правых кликов подряд** на
живой установке (`SendInput`, значок найден по UIA в переполнении области
уведомлений):

```
clicks_total 5 | clicks_menu_opened 5 | clicks_pid_alive 5 | crashes 0
esc_closed True | outside_closed True | left_click_alive True
pids_start [21052, 40688] -> pids_end [21052, 40688]
```

**12. Значок трея больше не пропадает.** `ui_helper._pid_alive()` считала
процесс живым по успешному `OpenProcess`, а он отвечает успехом и для
завершённого, но ещё не освобождённого процесса: в `state/ui_helper.json`
остался pid 31800, и супервизор «видел» работающий помощник, поэтому настоящий
не поднимал — значка в трее не было. Стало: `OpenProcess` +
`WaitForSingleObject(h, 0)` (`WAIT_TIMEOUT` = жив, `WAIT_OBJECT_0` = завершён),
`_pid_image()` / `_is_our_helper()` принимают пульс только если по pid работает
**наш** файл помощника, а в сборке `we_are_helper()` дополнительно требует имя
своего файла (`forest-ui.exe`) — унаследованный `FOREST_UI=1` больше не делает
помощником сам супервизор.

**13. `state/status.txt` — в UTF-8.** Кириллица в отчёте статуса раньше
превращалась в `?` (текст брался из ASCII-безопасного stdout). Теперь
человекочитаемый отчёт собирается из исходных строк и пишется в файл UTF-8,
а stdout в сборке остаётся ASCII.

### Ограничения проверки (Linux)

**ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** Проверка пакета выполнялась в среде WSL (Linux
внутри Windows), а не на установленной системе на реальном железе. Меню и
уведомления в графическом интерфейсе протестированы логически, но НЕ проверены
на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в
репозитории.

**TESTING SCOPE (EN):** Package testing was performed in WSL (Linux on Windows),
not on a real installation on bare metal. The GUI menu and notifications are
tested logically but have NOT been verified on a live desktop — issues are
possible. If something does not work, please report it in the repository.

Эта оговорка вписана **в сами пакеты**: в длинное описание `.deb`, в
`%description` `.rpm`, в `pkgdesc` и в сообщение установочного скрипта Arch, в
README внутри AppImage и портативного `.tar.gz`, а также в `README.md`,
`README.ru.md`, `docs/LINUX-INSTALL.md` и `docs/LINUX-INSTALL.ru.md`.

### Как обновиться

* **Windows:** запустите `ForestProxyUpdater.exe --install` из новой версии
  (конфиг `config.json` и состояние не затрагиваются), либо распакуйте архив в
  каталог установки.
* **Linux (пакет):** `sudo apt install ./forest-proxy-updater_1.3.0_amd64.deb`
  (или `dnf install`, `pacman -U` — по формату); конфиг в
  `/etc/forest-proxy-updater/` сохраняется.

---

## 🇬🇧 English

### What was wrong

1. The notification did not match the approved mock-up: 360 px wide, its own stripe
   on the left, steps as text lines (`50 мс (0.05 с)`), a `QProgressBar` with a
   percentage instead of a time bar, and a spinner drawn as an arc over the icon.
2. The user's own icon was not really used inside the notification.
3. Pacing was uneven: the frame timer checked the 60 fps grid only every half
   period, so some frames took 25 ms instead of 16.7 ms, and the switch to the
   final state dropped a frame entirely (**58 ms** measured in WSL).
4. On Linux there was no card of our own — steps went as text into a D-Bus system
   notification.
5. The Qt-free fallback showed only the event text — no steps, no recovery time.
6. The packages did not declare the helper's system libraries, so the Qt platform
   plugin could not load and the helper did not start at all.

### What is done

1. **The card is 1:1 with `docs/notify-mockup.html`**, and the selftest **reads the
   mock-up file** (it ships inside the bundle) and compares every number: 392 px,
   radius 12, 3 px state stripe, padding 13/15/12/14, 34×34 icon tile, gap 11,
   18 px spinner, 45 px stage indent, 13 px markers, 3 px bar, 340 ms entrance
   with the mock-up's `cubic-bezier(.16,.84,.28,1)`, 260 ms `ease-in` exit, 420 px
   slide, 0.85 s per spinner turn, stage passed at 6 % of the bar. State colours
   come from the mock-up (`#41c26d`, `#dc4f55`, `#e5bf37`, `#9497a0`).
2. **The user's icon inside the card**: the same `packaging/app.ico` through the
   same code path as the tray icon. Proved pixel by pixel — **1024/1024 pixels
   identical to the tray icon**.
3. **A steady 60 fps, measured**: frames run on an absolute `n/60` grid; only the
   window position and opacity change per frame while everything static is
   rendered **once** into a separate layer (nothing to flicker). The final layer
   is **pre-rendered**, so the final switch no longer costs a frame
   (58 ms → 20.3 ms in WSL). Live PC measurement: **60.1 fps average, 388 frames,
   worst frame 21.4 ms, 0 dropped frames, 0 stalls**.
4. **Focus is never taken**: the selftest watches the foreground window during the
   whole animation and asserts the card never becomes the active window (measured:
   0 times) and the user's window does not change.
5. **The final state happens in the same card** (title/subtitle/footer/spinner
   change in place, held for 4 s) instead of sliding in a second time.
6. **The same card on Linux** (verified in WSL on `offscreen`, `xcb` and
   `wayland`), with a fallback to a D-Bus system notification (steps passed in the
   body) when there is no graphical session or the window fails.
   `notify.card_on_linux: false` forces the fallback.
7. **The Qt-free fallback shows the same content** (stages with millisecond times
   plus the recovery-time footer), fed from the watchdog state.
8. **Package dependencies**: the Qt platform plugins and Qt libraries travel
   inside the bundle; the system libraries are declared in `.deb` (by package
   name), `.rpm` (by SONAME) and Arch (`depends`) — verified against live Arch
   packages and by installing them in Ubuntu.
9. **The tray menu is one menu again — a single window in one style, with
   submenus that open on HOVER.** The menu paints its own frameless `Qt.Popup`
   window (`src/ui_menu.py` + `src/ui_style.py`, not a `QMenu`), so the panel,
   the rows, the state stripe, the highlight and the submenus share **one style
   table** and one text layer. A submenu opens on hover after the style delay
   (150 ms) — **159-163 ms** measured on the live PC; it closes through a 280 ms
   grace window (**283 ms** measured); a plain **pass** over the item does NOT
   open it; moving to another item with a submenu **reuses the same panel**
   (4.7-6.0 ms); returning to the item cancels the close. The keyboard is
   unchanged (Right opens, Left/Esc close, the arrow keys do not open submenus).
   The direct-RU filter item reads **«РФ — напрямую»** and its tick follows the
   live setting.
10. **A highlight that keeps up with the cursor.** Moving the mouse moves the
    highlight **in the same input event** and fades it in with a short 50 ms
    tempo; keyboard navigation keeps the smooth 180 ms glide. Measured on the
    live PC (100 % / 200 Hz and 150 % / 60 Hz): cursor → highlight on the row,
    median **12 ms** (11-14) and **15 ms** (2-17) instead of 700-990 ms; frames
    with an intermediate highlight **0** (was 467-522); frames with two highlight
    bands **0**; the band is on the right row in **6 of 6** steps on both
    monitors.
11. **A right-click on the tray icon no longer takes the helper down.** The
    activation handler re-raised menu errors out of the tray callback, so a
    failing menu killed the whole helper (`RuntimeError: не удалось открыть своё
    меню (повтор)`); the error is now caught, the traceback goes to
    `logs/crash.log`, and the menu opens on the next activation. Proved with
    **5 real right-clicks in a row** on the live installation (`SendInput`, the
    icon located by UIA in the notification area): **5/5 menus opened, 0 crashes,
    0 pid changes**, Esc and an outside click close it.
12. **The tray icon no longer disappears.** `ui_helper._pid_alive()` treated a
    successful `OpenProcess` as proof of life, but that call also succeeds for a
    finished process whose handle is not released yet, so a stale pid in
    `state/ui_helper.json` made the supervisor believe the helper was running and
    it never started the real one (no tray icon). Liveness is now `OpenProcess` +
    `WaitForSingleObject(h, 0)`; a pulse is only accepted when **our** helper file
    runs under that pid; and in a frozen build `we_are_helper()` requires the
    executable's own name (`forest-ui.exe`), so an inherited `FOREST_UI=1` cannot
    make the supervisor consider itself the helper.
13. **`state/status.txt` is UTF-8.** The status report used to take its text from
    the ASCII-safe stdout buffer, so Cyrillic was written as `?`; the report is
    now collected from the original strings and written to the file in UTF-8 while
    stdout stays ASCII.

### Testing scope

**TESTING SCOPE (EN):** Package testing was performed in WSL (Linux on Windows),
not on a real installation on bare metal. The GUI menu and notifications are
tested logically but have NOT been verified on a live desktop — issues are
possible. If something does not work, please report it in the repository.

**ОГРАНИЧЕНИЯ ПРОВЕРКИ (RU):** Проверка пакета выполнялась в среде WSL (Linux
внутри Windows), а не на установленной системе на реальном железе. Меню и
уведомления в графическом интерфейсе протестированы логически, но НЕ проверены
на живом рабочем столе — возможны ошибки. Если что-то не работает, сообщите в
репозитории.

The Windows build is tested on a live PC and carries no such caveat.
