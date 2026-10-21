# Проверка гейта уведомлений НА ФАЙЛАХ КОНФИГА (не на словаре в памяти):
#   notify.enabled=false  -> ни одной карточки (событие, провал процесса, ошибка)
#   notify.enabled=true   -> карточка есть
#   ручной показ («Проверить уведомление») работает при выключенных уведомлениях
# Запуск: python packaging/check_notify_gate.py
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

ROOT = tempfile.mkdtemp(prefix="forest_notify_gate_")
STATE = os.path.join(ROOT, "state")
os.makedirs(STATE, exist_ok=True)

base = {}
src_cfg = os.path.join(REPO, "config.example.json")
try:
    with open(src_cfg, "r", encoding="utf-8") as f:
        base = json.load(f)
except Exception:
    base = {}
base.setdefault("paths", {})
base["paths"]["root"] = ROOT
base["paths"]["state_dir"] = STATE
base["paths"]["log_dir"] = os.path.join(ROOT, "logs")

CFG = os.path.join(ROOT, "config.json")


def write_cfg(enabled: bool) -> None:
    c = json.loads(json.dumps(base))
    c["notify"] = dict(c.get("notify") or {})
    c["notify"]["enabled"] = bool(enabled)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def write_failing_proc() -> None:
    """Состояние «провал без серверов» — ровно то, что было в песочнице."""
    st = {"ts": 1, "state": "error",
          "tip": "FOREST нет связи | серверов 0",
          "proc": {"id": "sandbox-proc-1", "kind": "restore", "cause": "outage",
                   "started": 1, "failed": True, "final": True, "pct": 100,
                   "elapsed_ms": 1500,
                   "steps": [{"nm": "Обрыв обнаружен (пульс порта)", "t": 50,
                              "done": True},
                             {"nm": "Переключение на новый узел", "t": 600,
                              "done": False}]}}
    with open(os.path.join(STATE, "tray_status.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


os.environ["FOREST_ROOT"] = ROOT
os.environ["FOREST_CONFIG"] = CFG
os.environ["XDG_STATE_HOME"] = os.path.join(ROOT, "xdg")
write_failing_proc()

import forest_ui as FU            # noqa: E402
from PySide6 import QtWidgets     # noqa: E402

fails = []


def chk(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def drop(u):
    t = getattr(u, "toast", None)
    try:
        if t is not None:
            t._ticker.stop()
            t.hide()
    except Exception:
        pass
    u.toast = None


print("=" * 70)
print("Гейт уведомлений на файлах конфига: notify.enabled = false / true")
print("=" * 70)

# ── выключено ──────────────────────────────────────────────────────────────
write_cfg(False)
ui = FU.Ui(FU.load_cfg())
try:
    chk("конфиг прочитан как «уведомления выключены»", ui.notifications_on() is False,
        f"notifications_on={ui.notifications_on()}")
    # 1. провал процесса из файла состояния (тот самый случай из песочницы)
    ui.refresh()
    chk("notify.enabled=false: провал из tray_status.json НЕ показан",
        ui.toast is None, f"toast={ui.toast!r}")
    # 2. событие перехода
    ui._on_transition("ok", "error")
    chk("notify.enabled=false: событие обрыва НЕ показано", ui.toast is None,
        f"toast={ui.toast!r}")
    # 3. служебная ошибка (запись команды не удалась)
    st = ui.show_toast("error", "Ошибка команды", "ui_cmd.json")
    chk("notify.enabled=false: служебная ошибка НЕ показана",
        ui.toast is None and "suppressed" in st, f"stats={st}")
    # 4. карточка процесса напрямую
    ui.show_process_card({"id": "p2", "kind": "restore", "failed": True,
                          "steps": [{"nm": "шаг", "t": 100}]})
    chk("notify.enabled=false: карточка процесса НЕ показана", ui.toast is None,
        f"toast={ui.toast!r}")
    # 5. ручной показ — работает
    ui.show_demo_card()
    chk("notify.enabled=false: ручной показ («Проверить уведомление») РАБОТАЕТ",
        ui.toast is not None, f"toast={ui.toast!r}")
    drop(ui)
finally:
    drop(ui)

# ── включено ───────────────────────────────────────────────────────────────
write_cfg(True)
ui2 = FU.Ui(FU.load_cfg())
try:
    chk("конфиг прочитан как «уведомления включены»", ui2.notifications_on() is True)
    ui2.refresh()          # тот же путь, что у живого помощника: провал из файла
    chk("notify.enabled=true: провал из tray_status.json ПОКАЗАН (refresh)",
        ui2.toast is not None, f"toast={ui2.toast!r}")
    drop(ui2)
    st = ui2.show_toast("error", "Провал", "", steps=[("шаг", 100)], total_ms=100)
    chk("notify.enabled=true: карточка показывается",
        ui2.toast is not None and not st.get("suppressed"),
        f"toast={ui2.toast!r}")
finally:
    drop(ui2)

print()
print("ИТОГ:", "PASSED" if not fails else f"FAILED ({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
