# Проверка LINUX-ветки меню трея на Qt, доступном на этой машине:
# os.name принудительно "posix" (живой XEmbed-стек и SNI-панель здесь не поднять,
# но КОД ветки проверяется целиком: отсутствие QMenu у трея, правый клик ->
# наше окно, левый -> ничего, режим --menu-popup без второго значка).
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import forest_ui as FU          # noqa: E402
from PySide6 import QtWidgets, QtCore   # noqa: E402

real_name = os.name
os.name = "posix"               # ТОЛЬКО на этот процесс: включаем Linux-ветки
FU.os.name = "posix"            # модуль держит ссылку на тот же объект os

fails = []


def chk(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


cfg = FU.CFG.load_config()
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
ui = FU.Ui(cfg, quiet=True)

print("=" * 64)
print("Linux-ветка: правый клик = НАШЕ меню, QMenu трею не отдан")
print("=" * 64)

# 1. QMenu трею НЕ отдан: пока он был отдан, XEmbed-панели открывали штатное
#    меню Qt вместо нашего окна.
pm = ui.tray.contextMenu()
chk("QMenu трею не отдан (setContextMenu убран)", pm is None, repr(pm))

# 2. Состав меню живёт в модели — её и рисует наше окно.
rows = [r for r in (ui.menu_model.rows if ui.menu_model else []) if r.kind != "sep"]
chk("в модели 12 пунктов, как в дампе", len(rows) == 12, str(len(rows)))
subs = [r for r in (ui.menu_model.rows if ui.menu_model else []) if r.has_sub()]
chk("подменю «Фильтры» и «Тема оформления» на месте", len(subs) == 2,
    str([r.text() for r in subs]))

# 3. Клики: правый (Context) открывает наше окно, левый (Trigger) — нет.
ARC = QtWidgets.QSystemTrayIcon.ActivationReason
shows = []
real_show = ui.show_menu
ui.show_menu = lambda pos=None: (shows.append(pos), {})[1]
try:
    ui._on_tray_activated(ARC.Context)
    n_ctx = len(shows)
    ui._on_tray_activated(ARC.Trigger)
    n_left = len(shows)
    ui._on_tray_activated(ARC.DoubleClick)
    n_dbl = len(shows)
    ui._on_tray_activated(FU.enum_int(ARC.Context))     # причина числом
    n_int = len(shows)
finally:
    ui.show_menu = real_show
chk("правый клик (Context) открывает НАШЕ окно меню", n_ctx == 1, str(n_ctx))
chk("левый клик и двойной меню НЕ открывают", n_left == 1 and n_dbl == 1,
    f"после левого {n_left}, после двойного {n_dbl}")
chk("причина клика числом тоже ведёт в наше меню", n_int == 2, str(n_int))

# 4. Те же команды, что раньше шли из меню панели: путь из нашего окна работает.
cmds = []
real_write = FU.UC.write_cmd
FU.UC.write_cmd = lambda cfg_, cmd: (cmds.append(cmd), True)[1]
try:
    act = ui.acts["filter_ru"]
    act.setChecked(not act.isChecked())
    ui._menu_toggle("filter_ru", act.isChecked())
    ui._menu_activate("refresh")
finally:
    FU.UC.write_cmd = real_write
chk("галочка «РФ напрямую» из нашего меню доходит до приложения",
    any(c.startswith("filter_ru_") for c in cmds), str(cmds))
chk("команда «Обновить серверы сейчас» из нашего меню доходит до приложения",
    "refresh" in cmds, str(cmds))

# 5. Режим плазмоида: наше меню БЕЗ второго значка в трее.
ui2 = FU.Ui(cfg, quiet=True, tray=False)
chk("--menu-popup не создаёт значок в трее", ui2.has_tray is False
    and not isinstance(ui2.tray, QtWidgets.QSystemTrayIcon),
    type(ui2.tray).__name__)
p = QtCore.QPoint(300, 200)
st = ui2.show_menu(p)
pnl = ui2._menu_panel
chk("--menu-popup показывает наше окно в заданной точке",
    pnl is not None and pnl.isVisible() and isinstance(st, dict),
    f"панель {type(pnl).__name__ if pnl else None}")
try:
    ui2._close_menu(instant=True)
except Exception:
    pass

os.name = real_name
print()
print("ИТОГ:", "PASSED" if not fails else f"FAILED ({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
