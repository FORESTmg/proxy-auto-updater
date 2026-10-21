# Полный самотест помощника В LINUX-ВЕТКЕ (os.name="posix") на Qt этой машины.
# Нужен, чтобы проверить именно то, что увидит KDE: QMenu панели, клики
# (левый — ничего, правый — панель), отсутствие регрессий в остальных 150+ проверках.
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

# ВАЖНО: Qt импортируется ДО подмены (PySide6 не грузится при os.name="posix").
import forest_ui as FU          # noqa: E402

os.name = "posix"
FU.os.name = "posix"
sys.exit(FU.main(["--selftest"]))
