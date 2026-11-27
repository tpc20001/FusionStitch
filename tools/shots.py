"""開發用：在雲端 Windows（GitHub Actions）把電腦版每一頁截圖，給 Claude 檢查介面（不在使用者電腦上跑）。
python tools/shots.py → shots/<頁>.png"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
out = ROOT / "shots"
out.mkdir(exist_ok=True)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
import app as A  # noqa: E402


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


win = A.MainWindow()
app._fs_window = win
win.resize(1600, 1000)
win.show()
pump(3)
for page in list(getattr(win, "nav_items", {}).keys()):
    try:
        win.go(page)
        pump(1.5)
        win.grab().save(str(out / f"{page}.png"))
        print("shot", page)
    except Exception as e:
        print("fail", page, e)
os._exit(0)
