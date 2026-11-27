r"""FusionStitch main window (PySide6).

Started by FusionStitch.exe (launcher) or `python launcher\\launcher.py`.
Long-lived objects (conversion queue, performance sampler) live on the
QApplication so a hot update can swap this window without stopping a job.
"""
from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import threading
import time
import traceback

import numpy as np
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QDoubleSpinBox, QStackedWidget, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

import core
from core import APP_VERSION, cfg, set_cfg, fmt_dur, fmt_bytes
import clips as clipscan
import color
import geometry as geo
import gyro
import projects
import jobs
import media
import updater
from ui_common import Histogram, NavDelegate, ParamRow, Sparkline, TC, app_icon, card, stylesheet

PAGE_LIBRARY, PAGE_EDITOR, PAGE_QUEUE, PAGE_PERF, PAGE_SETTINGS, PAGE_UPDATE, PAGE_CHANGELOG, PAGE_LOG, PAGE_HELP, PAGE_OUTPUTS, PAGE_QUICK = range(11)


# ======================================================================== shared state
def shared(name, factory):
    """Objects that survive hot updates (kept on the QApplication)."""
    app = QApplication.instance()
    obj = getattr(app, name, None)
    if obj is None:
        obj = factory()
        setattr(app, name, obj)
    return obj


def job_queue() -> jobs.JobQueue:
    q = shared("_fs_queue", jobs.JobQueue)
    if type(q) is not jobs.JobQueue:
        # created by an older version before a hot update: switch it to the current code. The old
        # worker thread (if still alive) keeps the job it is running; a dead one is replaced.
        try:
            q.__class__ = jobs.JobQueue
            q._thread = next((t for t in threading.enumerate() if t.name == "jobqueue" and t.is_alive()), None)
            if q._thread is None:
                jobs.core.log("hot update: job worker was not running, starting a new one", "job_log.txt")
                q._thread = threading.Thread()          # dead placeholder -> ensure_worker restarts + requeues
            q.ensure_worker()
        except Exception:
            traceback.print_exc()
    return q


def web_remote():
    import webremote

    def make():
        app = QApplication.instance()
        return webremote.Remote(lambda: getattr(app, "_fs_window", None),
                                lambda path: getattr(app, "_fs_window").bus.webUpdate.emit(path))
    return shared("_fs_web", make)


def perf_sampler():
    import perf
    s = shared("_fs_perf", perf.PerfSampler)
    return s


class Bus(QObject):
    """Cross-thread signals."""
    jobFinished = Signal(object)
    clipInfo = Signal(str, dict)
    scanDone = Signal(list)
    analysisDone = Signal(str, object)
    analysisProgress = Signal(str, float)
    webUpdate = Signal(str)
    uiCall = Signal(object)              # phone page: run a function on the Qt thread


# ======================================================================== clip helpers
def clip_meta(c: dict) -> dict:
    """Probe a clip (cached by media.probe). Runs in a worker thread."""
    out = {}
    try:
        if c["kind"] == "video" and c["front"]:
            tl = media.timeline(c["front"])
            info = media.probe(c["front"][0])
            out.update(duration=tl.duration, chapters=len(c["front"]), width=tl.width, height=tl.height,
                       fps=tl.fps, size=sum(media.probe(p).get("size", 0) for p in c["front"] + c["back"]),
                       created=info.get("creation_time"))
        elif c["front"]:
            st = os.stat(c["front"][0])
            out.update(duration=0, chapters=1, size=st.st_size,
                       created=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as e:
        out["error"] = str(e)
    return out


from naming import created_parts, output_folder, output_name  # noqa: E402,F401  (共用一份：快速轉檔、自動流程、已轉過判斷都用同一套檔名)


# ======================================================================== performance float
class PerfFloat(QWidget):
    """Always-on-top, draggable, translucent mini monitor."""

    def __init__(self, main):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.main = main
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("perf_float")
        self.setStyleSheet(f"QWidget#perf_float{{background:rgba(12,16,22,235);border:1px solid #2a3340;border-radius:10px}}"
                           f"QLabel{{background:transparent;color:#e8ecf2;font-size:9pt}} QLabel#k{{color:#8994a4}}"
                           f"QLabel#v{{font-weight:800}} QLabel#job{{color:#4dc9ff;font-weight:800}}")
        self.setWindowOpacity(float(cfg("perf_float_opacity") or 0.92))
        g = QGridLayout(self)
        g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(1)
        self.rows = {}
        spec = [("cpu", "CPU", "#4dc9ff", 100), ("ram", "記憶體", "#b48cff", 100), ("gpu", "GPU 3D", "#45d483", 100),
                ("enc", "NVENC", "#f0ad4e", 100), ("dec", "NVDEC", "#ff7eb6", 100), ("temp", "GPU 溫度", "#ff6577", 100)]
        r = 0
        for key, label, color, vmax in spec:
            k = QLabel(label); k.setObjectName("k")
            v = QLabel("—"); v.setObjectName("v"); v.setMinimumWidth(66); v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sp = Sparkline(color, vmax); sp.setFixedSize(84, 18)
            g.addWidget(k, r, 0); g.addWidget(sp, r, 1); g.addWidget(v, r, 2)
            self.rows[key] = (v, sp)
            r += 1
        self.disk_box = QLabel("")
        self.disk_box.setObjectName("k")
        g.addWidget(self.disk_box, r, 0, 1, 3); r += 1
        self.job = QLabel("")
        self.job.setObjectName("job")
        self.job.setWordWrap(True)
        g.addWidget(self.job, r, 0, 1, 3)
        self._drag = None
        pos = cfg("perf_float_pos")
        if pos:
            self.move(QPoint(*pos))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def _menu(self, pt):
        m = QMenu(self)
        m.addAction("開啟主視窗", self.main.show_main)
        m.addAction("電腦效能頁", lambda: (self.main.show_main(), self.main.go(PAGE_PERF)))
        sub = m.addMenu("透明度")
        for o in (1.0, 0.92, 0.8, 0.65, 0.5):
            sub.addAction(f"{int(o * 100)}%", lambda o=o: (self.setWindowOpacity(o), set_cfg("perf_float_opacity", o)))
        m.addSeparator()
        m.addAction("關閉浮窗", lambda: self.main.set_float(False))
        m.exec(self.mapToGlobal(pt))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            set_cfg("perf_float_pos", [self.x(), self.y()])

    def mouseDoubleClickEvent(self, e):
        self.main.show_main()
        self.main.go(PAGE_PERF)

    def refresh(self, data, hist, job):
        g = data.get("gpu") or {}
        vals = {"cpu": f"{data.get('cpu', 0):.0f}%", "ram": f"{data.get('ram_pct', 0):.0f}%",
                "gpu": f"{g.get('gpu', 0) or 0:.0f}%" if g else "—", "enc": f"{g.get('enc', 0) or 0:.0f}%" if g else "—",
                "dec": f"{g.get('dec', 0) or 0:.0f}%" if g else "—",
                "temp": f"{g.get('temp', 0) or 0:.0f}°C {g.get('power', 0) or 0:.0f}W" if g else "—"}
        for k, (v, sp) in self.rows.items():
            v.setText(vals[k])
            sp.set_values(hist.get(k, []))
        disks = data.get("disks") or {}
        self.disk_box.setText("  ".join(f"{d}: ↓{x['read'] / 1048576:.0f} ↑{x['write'] / 1048576:.0f}MB/s"
                                        for d, x in disks.items() if x["read"] + x["write"] > 2e5) or "硬碟：閒置")
        if job:
            self.job.setText(f"▶ {job.title}  {job.progress * 100:.1f}%  {job.fps:.1f} fps  剩 {fmt_dur(job.eta)}")
            self.job.show()
        else:
            self.job.hide()
        self.adjustSize()


# ---------------------------------------------------------------- table kit (same feel as TikLive)
from PySide6.QtGui import QColor as _QColor, QPainter as _QPainter, QPainterPath as _QPainterPath
from PySide6.QtWidgets import QStyledItemDelegate as _QStyledItemDelegate

ALIGN_MODES = {"left": (Qt.AlignLeft | Qt.AlignVCenter, ""), "center": (Qt.AlignCenter, ""),
               "right": (Qt.AlignRight | Qt.AlignVCenter, ""),
               "left_pad": (Qt.AlignLeft | Qt.AlignVCenter, "l"), "right_pad": (Qt.AlignRight | Qt.AlignVCenter, "r")}
SORT_ROLE = Qt.UserRole + 1


class SortItem(QTableWidgetItem):
    """Sorts by the numeric key in SORT_ROLE when set (sizes, lengths), otherwise by text."""
    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            try:
                return float(a) < float(b)
            except (TypeError, ValueError):
                pass
        return self.text() < other.text()


class SortHeader(QHeaderView):
    """Click a title to sort: the active column shows a red arrow (▲ small -> large, ▼ large -> small),
    the others a faint ▲▼."""
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.sort_col, self.sort_desc, self.sortable = -1, False, True
        self.setSectionsClickable(True)
        self.setHighlightSections(False)

    def paintSection(self, painter, rect, logical):
        painter.save(); super().paintSection(painter, rect, logical); painter.restore()
        if not self.sortable or not str(self.model().headerData(logical, Qt.Horizontal) or "").strip():
            return
        painter.save(); painter.setRenderHint(_QPainter.Antialiasing); painter.setPen(Qt.NoPen)
        w, h = 7.0, 5.0
        x = rect.right() - w - 5
        cy = rect.center().y() + 0.5

        def tri(top, up, color):
            path = _QPainterPath()
            if up:
                path.moveTo(x, top + h); path.lineTo(x + w, top + h); path.lineTo(x + w / 2, top)
            else:
                path.moveTo(x, top); path.lineTo(x + w, top); path.lineTo(x + w / 2, top + h)
            path.closeSubpath(); painter.setBrush(_QColor(color)); painter.drawPath(path)
        if logical == self.sort_col:
            tri(cy - h / 2, not self.sort_desc, "#ff3b4f")
        else:
            c = _QColor(255, 255, 255, 120)
            tri(cy - h - 1, True, c); tri(cy + 1, False, c)
        painter.restore()


class _AlignDelegate(_QStyledItemDelegate):
    def __init__(self, kit):
        super().__init__(kit.table)
        self.kit = kit

    def initStyleOption(self, opt, index):
        super().initStyleOption(opt, index)
        mode = self.kit.align_mode(index.column())
        if mode:
            al, pad = ALIGN_MODES[mode]
            opt.displayAlignment = al
            if pad == "l" and opt.text:
                opt.text = " " + opt.text
            elif pad == "r" and opt.text:
                opt.text = opt.text + " "


class TableKit:
    """Every table: drag widths, drag column order, click a title to sort, right-click a title for
    alignment (靠左 / 置中 / 靠右 / 留空格); all remembered in settings.json (tables)."""

    def __init__(self, table, key: str, cols: list, widths: dict, aligns: dict | None = None,
                 sortable=True, on_sorted=None):
        self.table, self.key, self.cols = table, key, list(cols)
        self.defaults = dict(aligns or {})
        self.on_sorted = on_sorted
        h = SortHeader(table)
        h.sortable = sortable
        table.setHorizontalHeader(h)
        table.setHorizontalHeaderLabels(self.cols)
        self.h = h
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        h.setMinimumSectionSize(28)
        h.setSectionsMovable(True)
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._menu)
        h.setToolTip("拖拉分隔線調整寬度（雙擊分隔線＝依內容寬度）；拖拉標題調整順序；"
                     + ("點標題排序；" if sortable else "") + "右鍵標題選對齊方式")
        st = self._state()
        ws = st.get("widths") or {}
        for i in range(len(self.cols)):
            w = ws.get(str(i)) or widths.get(i)
            if w:
                table.setColumnWidth(i, int(w))
        order = st.get("order")
        if isinstance(order, list) and sorted(order) == list(range(len(self.cols))):
            for vis, logical in enumerate(order):
                h.moveSection(h.visualIndex(logical), vis)
        srt = st.get("sort")
        if sortable and isinstance(srt, list) and len(srt) == 2 and 0 <= srt[0] < len(self.cols):
            h.sort_col, h.sort_desc = int(srt[0]), bool(srt[1])
        table.setItemDelegate(_AlignDelegate(self))
        self._timer = QTimer(table); self._timer.setSingleShot(True); self._timer.setInterval(600)
        self._timer.timeout.connect(self._save_layout)
        h.sectionResized.connect(lambda *_: self._timer.start())
        h.sectionMoved.connect(lambda *_: self._timer.start())
        if sortable:
            h.sectionClicked.connect(self._click)

    # settings
    def _state(self) -> dict:
        return dict((cfg("tables") or {}).get(self.key) or {})

    def _put(self, **kw):
        allt = dict(cfg("tables") or {})
        st = dict(allt.get(self.key) or {})
        st.update(kw)
        allt[self.key] = st
        set_cfg("tables", allt)

    def _save_layout(self):
        self._put(widths={str(i): self.table.columnWidth(i) for i in range(len(self.cols))},
                  order=[self.h.logicalIndex(v) for v in range(len(self.cols))])

    # alignment
    def align_mode(self, col):
        return (self._state().get("align") or {}).get(str(col)) or self.defaults.get(col)

    def _menu(self, pos):
        col = self.h.logicalIndexAt(pos)
        if col < 0:
            return
        al = dict(self._state().get("align") or {})
        cur = self.align_mode(col)
        m = QMenu(self.table)
        t = m.addAction(f"「{self.cols[col] or '縮圖'}」對齊方式"); t.setEnabled(False)
        m.addSeparator()
        acts = {}
        for mode, label in (("left", "靠左"), ("center", "置中"), ("right", "靠右"),
                            ("left_pad", "靠左（前面空一格半形）"), ("right_pad", "靠右（與邊線空一格半形）")):
            a = m.addAction(label); a.setCheckable(True); a.setChecked(cur == mode); acts[a] = mode
        m.addSeparator()
        a_def = m.addAction("回復預設")
        a_all = m.addAction("這張表全部欄位都用目前選擇") if cur else None
        a_w = m.addAction("欄寬全部回復預設")
        ch = m.exec(self.h.mapToGlobal(pos))
        if ch is None:
            return
        if ch is a_w:
            self._put(widths={}, order=None)
            QMessageBox.information(self.table, "欄寬", "下次開啟程式時回復預設欄寬與順序。")
            return
        if ch is a_all:
            al = {str(c): cur for c in range(len(self.cols))}
        elif ch is a_def:
            al.pop(str(col), None)
        else:
            al[str(col)] = acts[ch]
        self._put(align=al)
        self.table.viewport().update()

    # sorting
    def _click(self, col):
        if not str(self.cols[col]).strip():
            return
        if self.h.sort_col == col:
            if self.h.sort_desc:                      # third click: back to the original order
                self.h.sort_col, self.h.sort_desc = -1, False
            else:
                self.h.sort_desc = True
        else:
            self.h.sort_col, self.h.sort_desc = col, False
        self._put(sort=[self.h.sort_col, self.h.sort_desc])
        self.apply_sort()

    def apply_sort(self, default_col: int | None = None):
        col = self.h.sort_col
        if col < 0:
            if default_col is None:
                self.h.viewport().update()
                if self.on_sorted:
                    self.on_sorted(False)
                return
            col, desc = default_col, False
        else:
            desc = self.h.sort_desc
        self.table.sortItems(col, Qt.DescendingOrder if desc else Qt.AscendingOrder)
        self.h.viewport().update()
        if self.on_sorted:
            self.on_sorted(True)


# ======================================================================== library page
class LibraryPage(QWidget):
    COLS = ["", "素材", "拍攝時間", "長度", "章節", "素材大小", "成品大小", "360 音訊", "狀態"]
    OUT, WAV, ST = 6, 7, 8

    def __init__(self, main):
        super().__init__()
        self.main = main
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        t = QLabel("素材庫"); t.setObjectName("title"); top.addWidget(t)
        self.folder_lab = QLabel("尚未選擇資料夾（把前、後兩張 SD 卡的內容放在同一個資料夾底下）")
        self.folder_lab.setObjectName("sub")
        top.addWidget(self.folder_lab, 1)
        b = QPushButton("選擇素材資料夾…"); b.setObjectName("blue"); b.clicked.connect(self.pick_folder); top.addWidget(b)
        b2 = QPushButton("重新掃描"); b2.clicked.connect(lambda: self.scan(self.main.folder)); top.addWidget(b2)
        l.addLayout(top)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("篩選"))
        self.filter = QComboBox()
        self.filter.addItems(["全部", "未轉檔", "已轉檔", "佇列中", "失敗", "成品損壞／未完成", "已調整", "前後不完整"])
        self.filter.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.filter)
        self.count_lab = QLabel(""); self.count_lab.setObjectName("sub"); bar.addWidget(self.count_lab, 1)
        bp = QPushButton("▶ 預覽校正"); bp.clicked.connect(self.open_selected); bar.addWidget(bp)
        bq = QPushButton("加入佇列（選取的）"); bq.clicked.connect(self.queue_selected); bar.addWidget(bq)
        bg = QPushButton("🎯 GoPro 4K"); bg.setToolTip("選取的素材用「GoPro 對標：H.265 4K 立體聲」加入佇列（不改預設）")
        bg.clicked.connect(self.quick_gopro); bar.addWidget(bg)
        ba = QPushButton("全部未轉加入佇列"); ba.clicked.connect(self.queue_all_new); bar.addWidget(ba)
        bauto = QPushButton("✦ 全自動處理"); bauto.setObjectName("start")
        bauto.setToolTip("選取的素材（沒選就是全部未轉檔的）：軟體自動對齊、防震、判斷行進方向、色彩匹配，然後轉出\n"
                         "要轉出哪些格式在「設定 → 全自動處理」選")
        bauto.clicked.connect(self.auto_process); bar.addWidget(bauto)
        l.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(96, 54))
        self.table.verticalHeader().setDefaultSectionSize(60)
        self.kit = TableKit(self.table, "library", self.COLS,
                            {0: 108, 1: 330, 2: 140, 3: 70, 4: 50, 5: 95, 6: 150, 7: 70, 8: 130},
                            {2: "center", 3: "center", 4: "center", 5: "right_pad", 6: "right_pad", 7: "center", 8: "center"},
                            on_sorted=self._after_sort)
        self._resort = QTimer(self); self._resort.setSingleShot(True); self._resort.setInterval(700)
        self._resort.timeout.connect(lambda: self.kit.apply_sort() if self.kit.h.sort_col >= 0 else None)
        self.table.cellDoubleClicked.connect(lambda r, c: self.open_selected())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self._valid = {}            # output path -> (size, mtime, ok)
        self._checking = False
        l.addWidget(self.table, 1)
        # 空的時候：一句話告訴使用者下一步（不是一張空表格）
        el = QVBoxLayout(self.table.viewport()); el.setAlignment(Qt.AlignCenter)
        self.empty_lab = QLabel("還沒有素材\n\n按右上「選擇素材資料夾…」，或把 GoPro Fusion 資料夾拖進來\n（大多數時候用「★ 快速轉檔」更快）")
        self.empty_lab.setAlignment(Qt.AlignCenter); self.empty_lab.setStyleSheet("color:#8994a4;font-size:15px;line-height:150%")
        el.addWidget(self.empty_lab)
        m = self.table.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.modelReset):
            sig.connect(lambda *_: self.empty_lab.setVisible(self.table.rowCount() == 0))
        tip = QLabel("提示：雙擊素材開啟預覽校正；狀態「已轉檔」是依輸出資料夾裡有沒有同編號的成品判斷。")
        tip.setObjectName("sub")
        l.addWidget(tip)
        self.rows: dict[str, int] = {}

    def pick_folder(self):
        import folders
        d = folders.pick_dir(self, "選擇素材資料夾", "footage", self.main.folder or "")
        if d:
            self.scan(d)

    def scan(self, folder):
        if not folder or not os.path.isdir(folder):
            return
        self.main.folder = folder
        set_cfg("last_folder", folder)
        self.folder_lab.setText(folder)
        self.count_lab.setText("掃描中…")
        bus = self.main.bus

        def work():
            from concurrent.futures import ThreadPoolExecutor
            found = clipscan.scan(folder)
            bus.scanDone.emit(found)
            with ThreadPoolExecutor(4) as ex:      # ffprobe is I/O bound: probe 4 clips at a time
                for c, meta in zip(found, ex.map(clip_meta, found)):
                    bus.clipInfo.emit(c["id"], meta)
        threading.Thread(target=work, daemon=True).start()

    def on_scan(self, found):
        self.main.clips = {c["id"]: c for c in found}
        self.main.meta = {}
        self.table.setRowCount(0)
        self.rows = {}
        for c in found:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.rows[c["id"]] = r
            it = SortItem()
            it.setData(SORT_ROLE, r)
            thumb = c.get("thm") or (c["front"][0] if c["kind"] == "photo" and c["front"] else None)
            if thumb:
                pm = QPixmap(thumb)
                if not pm.isNull():
                    it.setIcon(QIcon(pm.scaled(96, 54, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)))
            it.setData(Qt.UserRole, c["id"])
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, SortItem(c["name"] + ("" if c["complete"] else "  ⚠ 前後不完整")
                                                      + ("  ✎ 已調整" if projects.has_edits(self.main.folder, c["id"]) else "")))
            for col in range(2, len(self.COLS)):
                self.table.setItem(r, col, SortItem("…" if col in (2, 3, 5) else ""))
            self.table.item(r, 4).setText(str(len(c["front"])) if c["kind"] == "video" else "照片")
            self.table.item(r, self.WAV).setText("✔" if c.get("wav") else "")
        self.count_lab.setText(f"{len(found)} 組素材")
        self.kit.apply_sort()
        self.refresh_status()
        auto = getattr(self.main, "_cli_auto", None)
        if auto is not None and found:
            self.main._cli_auto = None
            only = auto.get("only")
            ids = [c["id"] for c in found if c["kind"] == "video" and (not only or c["num"] in only)]
            QTimer.singleShot(500, lambda: self.auto_process(ids, confirm=False))
        elif cfg("auto_on_scan") and found:
            def new_ones():
                ids = [cid for cid, r in self.rows.items() if self.table.item(r, self.ST).text() == ""
                       and not projects.has_edits(self.main.folder, cid)]
                if ids:
                    self.auto_process(ids, confirm=False)
            QTimer.singleShot(4000, new_ones)
        self.main.editor.refresh_clip_combo()

    def on_meta(self, cid, meta):
        self.main.meta[cid] = meta
        r = self.rows.get(cid)
        if r is None:
            return
        d, t = created_parts(meta)
        self.table.item(r, 2).setText(f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}" if d != "00000000" else "—")
        self.table.item(r, 3).setText(fmt_dur(meta.get("duration")) if meta.get("duration") else "—")
        self.table.item(r, 5).setText(fmt_bytes(meta.get("size", 0)))
        self.table.item(r, 3).setData(SORT_ROLE, float(meta.get("duration") or 0))
        self.table.item(r, 5).setData(SORT_ROLE, float(meta.get("size") or 0))
        if self.kit.h.sort_col >= 0:
            self._resort.start()                 # lengths / sizes arrive after the scan: sort again
        self.refresh_status()

    def _after_sort(self, sorted_):
        if not sorted_:                              # back to the scan order
            self.table.sortItems(0, Qt.AscendingOrder)
        self.rows = {self.table.item(r, 0).data(Qt.UserRole): r for r in range(self.table.rowCount())}
        self.apply_filter()

    def mark_edited(self, cid):
        r = self.rows.get(cid)
        if r is not None and "✎" not in self.table.item(r, 1).text():
            self.table.item(r, 1).setText(self.table.item(r, 1).text() + "  ✎ 已調整")

    def clip_status(self, c) -> str:
        q = job_queue()
        for j in reversed(q.jobs):
            if j.clip.get("id") == c["id"] and j.clip.get("folder") == self.main.folder:
                if j.status == "running":
                    return f"轉檔中 {j.progress * 100:.0f}%"
                if j.status in ("queued", "paused"):
                    return "佇列中" if j.status == "queued" else "已暫停"
                if j.status == "error":
                    return "失敗"
                break
        outs, parts = self.outputs(c)
        if parts:
            return "未完成（可續轉）"
        if outs:
            states = [self._check(p) for p in outs]
            if any(s_ is False for s_ in states):
                return "⚠ 成品損壞"
            if all(s_ == "short" for s_ in states):
                return "⚠ 成品不完整"
            return "已轉檔"
        return "" if c["complete"] else "不完整"

    def outputs(self, c):
        """(finished outputs, leftover .parts folders) of a clip in the output folder."""
        out = output_folder(self.main.folder)
        if not out.is_dir():
            return [], []
        key = f"GP{c['num']}_"
        files = [f for f in self.main.out_files(out) if key in f]
        outs = [str(out / f) for f in files if f.lower().endswith((".mp4", ".jpg")) and ".joining." not in f]
        parts = [str(out / f) for f in files if f.endswith(".parts")]
        return outs, parts

    def _check(self, path):
        """True/False once verified (ffprobe in the background); None = not checked yet (counts as OK)."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        sig = (st.st_size, st.st_mtime)
        v = self._valid.get(path)
        if v and v[0] == sig:
            return v[1]
        if not self._checking:
            self._checking = True
            threading.Thread(target=self._verify_all, daemon=True).start()
        return None

    def _verify_all(self):
        try:
            import ffm
            todo = []
            for c in list(self.main.clips.values()):
                todo += self.outputs(c)[0]
            for p in todo:
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                sig = (st.st_size, st.st_mtime)
                if self._valid.get(p, (None,))[0] == sig:
                    continue
                ok = False
                try:
                    if p.lower().endswith(".jpg"):
                        ok = st.st_size > 1000
                    else:
                        info = ffm.probe(p)
                        dur = float(info.get("duration", 0))
                        ok = dur > 0.1 and bool(info.get("width"))
                        m = re.search(r"expected=([\d.]+)s", info.get("comment") or "")
                        if ok and m:                                   # written by FusionStitch: exact length known
                            ok = True if dur >= float(m.group(1)) * 0.98 - 0.5 else "short"
                        elif ok and "_x." not in p and not re.search(r"_\d+(\.\d+)?x\.", p):
                            # no length info (older/other tools): compare with the clip's own length
                            cid = next((k for k, c in self.main.clips.items() if f"GP{c['num']}_" in p), None)
                            if cid and cid not in self.main.meta:
                                continue                      # clip length not probed yet: decide later
                            clip_d = (self.main.meta.get(cid) or {}).get("duration") if cid else None
                            if clip_d and dur < clip_d * 0.9 and "_flat" not in p and "_planet" not in p:
                                ok = "short"
                except Exception:
                    ok = False
                self._valid[p] = (sig, ok)
        finally:
            self._checking = False          # the 1 s UI timer picks the results up

    def _menu(self, pt):
        ids = self.selected_ids()
        if not ids:
            return
        c = self.main.clips.get(ids[0])
        m = QMenu(self)
        n_ = len(ids)
        outs0, _p0 = self.outputs(c)
        m.addAction(("↻ 重新轉檔" if outs0 else "▶ 立即轉檔") + f"（目前預設{'，' + str(n_) + ' 段' if n_ > 1 else ''}）",
                    lambda: self._quick(ids, None))
        m.addAction("🎯 轉檔：GoPro 對標 4K", lambda: self._quick(ids, "gopro4k"))
        m.addAction("✦ 全自動處理" + (f"（{n_} 段）" if n_ > 1 else "這段"), lambda: self.auto_process(ids, confirm=False))
        m.addSeparator()
        m.addAction("▶ 預覽校正", self.open_selected)
        m.addAction("加入轉檔佇列", self.queue_selected)
        outs, parts = self.outputs(c)
        if outs:
            m.addSeparator()
            for p in outs[:6]:
                ok = self._valid.get(p, (None, None))[1]
                tag = "⚠ 損壞 " if ok is False else ("⚠ 不完整 " if ok == "short" else "")
                m.addAction(tag + "開啟成品：" + Path(p).name, lambda p=p: os.startfile(p))
            m.addAction("開啟成品資料夾", lambda: subprocess.Popen(["explorer", "/select,", outs[0]]))
        bad = [p for p in outs if self._valid.get(p, (None, None))[1] in (False, "short")]
        if bad or parts:
            m.addSeparator()
            m.addAction(f"刪除損壞／不完整成品與未完成暫存（{len(bad) + len(parts)} 個）…", lambda: self._delete_bad(bad, parts))
        m.addSeparator()
        if projects.has_edits(self.main.folder, c["id"]):
            m.addAction("清除這段素材的調整（校正、調色、運鏡…）", lambda: self._clear_edits(c))
        m.exec(self.table.viewport().mapToGlobal(pt))

    def _delete_bad(self, bad, parts):
        names = "\n".join(Path(p).name for p in bad + parts)
        if QMessageBox.question(self, "刪除", f"刪除下列損壞的成品與未完成的暫存？（只刪這些，原始素材不受影響）\n\n{names}") != QMessageBox.Yes:
            return
        import shutil
        for p in bad:
            try:
                os.remove(p)
            except OSError as e:
                QMessageBox.warning(self, "刪除", f"{Path(p).name}：{e}")
        busy = {j.output + ".parts" for j in job_queue().jobs if j.status in ("queued", "running", "paused")}
        for p in parts:
            if p in busy:
                continue
            shutil.rmtree(p, ignore_errors=True)
        self.main._out_cache = (0, None, [])
        self.refresh_status()

    def _clear_edits(self, c):
        if QMessageBox.question(self, "清除調整", f"清除 {c['name']} 的所有調整（校正、調色、防震、運鏡、入出點）？") == QMessageBox.Yes:
            projects.clear(self.main.folder, c["id"])
            r = self.rows.get(c["id"])
            if r is not None:
                self.table.item(r, 1).setText(self.table.item(r, 1).text().replace("  ✎ 已調整", ""))

    def refresh_status(self):
        for cid, r in self.rows.items():
            c = self.main.clips.get(cid)
            if not c:
                continue
            self._show_size(c, r)
            st = self.clip_status(c)
            it = self.table.item(r, self.ST)
            if it.text() != st:
                it.setText(st)
                it.setForeground(TC("ok") if st == "已轉檔" else TC("err") if st in ("失敗", "不完整")
                                 else TC("accent") if st else TC("text"))
        self.apply_filter()

    def _show_size(self, c, r):
        """成品大小: the real size once converted (with the estimate for comparison), else an estimate."""
        it = self.table.item(r, self.OUT)
        meta = self.main.meta.get(c["id"]) or {}
        est_txt, est = "", 0.0
        if c["kind"] == "video" and meta.get("duration"):
            try:
                pkey = cfg("auto_preset") or "youtube"
                preset = jobs.all_presets().get(pkey, jobs.PRESETS["youtube"])
                w = int(preset.get("width") or min(5376, preset.get("max_width") or 5376))
                est = jobs.estimate_bytes(preset, w, w // 2, float(meta.get("fps") or 29.97), float(meta["duration"]))
                est_txt = f"≈ {fmt_bytes(est)}"
            except Exception:
                est = 0.0
        outs = [p for p in self.outputs(c)[0] if p.lower().endswith(".mp4")]
        main = [p for p in outs if "_flat" not in p and "_planet" not in p and not re.search(r"_\d+(\.\d+)?x\.mp4$", p)]
        txt, tip, pick, sizes = (est_txt + "（預估）" if est_txt else ""), "", None, {}
        if outs:
            sizes = {}
            for p in outs:
                try:
                    sizes[p] = os.path.getsize(p)
                except OSError:
                    pass
            pick = next((p for p in main if p in sizes), next(iter(sizes), None))
            if pick:
                src = meta.get("size") or 0
                pct = f"，{sizes[pick] / src * 100:.0f}%" if src else ""
                txt = fmt_bytes(sizes[pick]) + pct
                tip = "\n".join(f"{Path(p).name}：{fmt_bytes(v)}" for p, v in sizes.items())
                if est_txt and pick in main:
                    tip += f"\n轉檔前預估：{est_txt}"
        it.setData(SORT_ROLE, float(sizes[pick]) if outs and pick else float(est or 0))
        if it.text() != txt:
            it.setText(txt)
            it.setForeground(TC("text") if outs else TC("subtext"))
        it.setToolTip(tip or "還沒轉檔：依目前全自動處理的預設、解析度和長度推算，轉完後會自動修正估算")

    def apply_filter(self):
        f = self.filter.currentText()
        for cid, r in self.rows.items():
            st = self.table.item(r, self.ST).text()
            name = self.table.item(r, 1).text()
            show = (f == "全部" or (f == "未轉檔" and st == "") or (f == "已轉檔" and st == "已轉檔")
                    or (f == "佇列中" and (st.startswith("轉檔中") or st in ("佇列中", "已暫停")))
                    or (f == "失敗" and st == "失敗") or (f == "前後不完整" and st == "不完整")
                    or (f == "成品損壞／未完成" and (st.startswith("⚠") or st.startswith("未完成")))
                    or (f == "已調整" and "✎" in name))
            self.table.setRowHidden(r, not show)

    def selected_ids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def open_selected(self):
        ids = self.selected_ids()
        if ids:
            self.main.open_clip(ids[0])

    def _quick(self, ids, preset):
        n = sum(1 for cid in ids if self.main.queue_clip(cid, preset))
        self.main.toast(f"已加入 {n} 個轉檔工作" + ("（GoPro 對標 4K）" if preset == "gopro4k" else ""))
        self.refresh_status()

    def quick_gopro(self):
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "GoPro 對標 4K", "請先選取素材（可以多選）。")
            return
        self._quick(ids, "gopro4k")

    def queue_selected(self):
        n = 0
        for cid in self.selected_ids():
            if self.main.queue_clip(cid):
                n += 1
        self.main.toast(f"已加入 {n} 個轉檔工作")

    def auto_process(self, ids=None, confirm=True):
        ids = ids or self.selected_ids() or [cid for cid, r in self.rows.items() if self.table.item(r, self.ST).text() == ""]
        ids = [cid for cid in ids if self.main.clips.get(cid, {}).get("kind") == "video" and self.main.clips[cid]["complete"]]
        if not ids:
            QMessageBox.information(self, "全自動處理", "沒有可以處理的影片素材。")
            return 0
        import autoproc
        outs = [autoproc.OUTPUTS[k][0] for k in (cfg("auto_outputs") or ["equirect"]) if k in autoproc.OUTPUTS]
        if confirm and QMessageBox.question(self, "全自動處理",
                                            f"全自動處理 {len(ids)} 段影片？\n\n每段會自動：對齊接縫 → 防震＋水平 → 判斷行進方向 → 前後鏡頭色彩匹配，然後轉出：\n• "
                                            + "\n• ".join(outs)) != QMessageBox.Yes:
            return 0
        q = job_queue()
        for cid in ids:
            c = dict(self.main.clips[cid])
            c["folder"] = self.main.folder
            q.add(c, {"auto": {"outputs": list(cfg("auto_outputs") or ["equirect"])}, "output": ""})
        self.main.toast(f"已加入 {len(ids)} 段全自動處理")
        self.main.go(PAGE_QUEUE)
        return len(ids)

    def queue_all_new(self):
        todo = [cid for cid, r in self.rows.items() if self.table.item(r, self.ST).text() == ""]
        if not todo:
            QMessageBox.information(self, "全部未轉加入佇列", "沒有未轉檔的素材。")
            return
        preset = cfg("preset")
        if QMessageBox.question(self, "全部未轉加入佇列",
                                f"用目前的校正與輸出設定（{jobs.all_presets().get(preset, {}).get('label', preset)}）"
                                f"轉檔 {len(todo)} 組未轉檔的素材？") != QMessageBox.Yes:
            return
        n = sum(1 for cid in todo if self.main.queue_clip(cid))
        self.main.toast(f"已加入 {n} 個轉檔工作")
        self.main.go(PAGE_QUEUE)


# ======================================================================== editor page
class FilmStrip(QWidget):
    """Stitched thumbnails over the whole clip; click or drag to seek."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.pix = []
        self.pos = 0.0
        self.msg = ""
        self.setFixedHeight(46)
        self.setCursor(Qt.PointingHandCursor)

    def set_images(self, arr):
        from PySide6.QtGui import QImage
        self.pix = []
        for a in arr:
            a = np.ascontiguousarray(a)
            img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_RGB888).copy()
            self.pix.append(QPixmap.fromImage(img))
        self.msg = ""
        self.update()

    def set_pos(self, p):
        if abs(p - self.pos) > 1e-4:
            self.pos = p
            self.update()

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter, QColor, QPen
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, TC("chart_bg"))
        m = 8
        w = r.width() - 2 * m
        if self.pix:
            n = len(self.pix)
            cw = w / n
            for i, pm in enumerate(self.pix):
                tgt = QRectF(m + i * cw, 2, cw + 0.5, r.height() - 4)
                # crop the middle of the 2:1 panorama to the cell's aspect
                sw = pm.height() * tgt.width() / tgt.height()
                src = QRectF((pm.width() - sw) / 2, 0, sw, pm.height())
                p.drawPixmap(tgt, pm, src)
        elif self.msg:
            p.setPen(TC("subtext"))
            p.drawText(r, Qt.AlignCenter, self.msg)
        x = m + w * self.pos
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(int(x), 0, int(x), r.height())

    def _seek(self, e):
        m = 8
        f = (e.position().x() - m) / max(1, self.width() - 2 * m)
        ed = self.editor
        if ed.player.total:
            ed.player.seek(int(max(0.0, min(1.0, f)) * (ed.player.total - 1)))

    def mousePressEvent(self, e):
        self.editor.player.pause()
        self._seek(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._seek(e)


class KeyStrip(QWidget):
    """Keyframe markers under the timeline; click a marker to jump there."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.keys, self.dur = [], 0.0
        self.setFixedHeight(12)
        self.setCursor(Qt.PointingHandCursor)

    def set_keys(self, keys, dur):
        self.keys, self.dur = list(keys), float(dur or 0)
        self.update()

    def _x(self, t):
        m = 8
        return m + (self.width() - 2 * m) * (t / self.dur if self.dur else 0)

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter, QPolygonF
        from PySide6.QtCore import QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(TC("warn"))
        for t in self.keys:
            x = self._x(t)
            p.drawPolygon(QPolygonF([QPointF(x, 1), QPointF(x + 5, 6), QPointF(x, 11), QPointF(x - 5, 6)]))

    def mousePressEvent(self, e):
        if not self.keys or not self.dur:
            return
        x = e.position().x()
        t = min(self.keys, key=lambda k: abs(self._x(k) - x))
        if abs(self._x(t) - x) < 10:
            self.editor.player.seek(int(round(t * self.editor.fps)))


class EditorPage(QWidget):
    def __init__(self, main):
        super().__init__()
        import preview
        self.main = main
        self.clip = None
        self.params = None
        self.defaults = None
        self.in_f = self.out_f = None
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        center = QVBoxLayout()
        center.setSpacing(6)
        # --- toolbar
        tb = QHBoxLayout()
        self.clip_combo = QComboBox(); self.clip_combo.setMinimumWidth(170)
        self.clip_combo.activated.connect(lambda i: self.main.open_clip(self.clip_combo.itemData(i)))
        tb.addWidget(self.clip_combo)
        bprev = QPushButton("◀"); bprev.setToolTip("上一個素材"); bprev.clicked.connect(lambda: self.step_clip(-1)); tb.addWidget(bprev)
        bnext = QPushButton("▶"); bnext.setToolTip("下一個素材"); bnext.clicked.connect(lambda: self.step_clip(1)); tb.addWidget(bnext)
        tb.addSpacing(10)
        self.mode_group = QButtonGroup(self)
        for i, name in enumerate(["360 環景", "平面全景", "小星球", "原始雙魚眼", "取景"]):
            b = QPushButton(name); b.setCheckable(True); b.setChecked(i == 0)
            self.mode_group.addButton(b, i); tb.addWidget(b)
        self.mode_group.idClicked.connect(self.set_mode)
        self.seam_chk = QCheckBox("顯示接縫 (S)"); self.seam_chk.toggled.connect(self._seam); tb.addWidget(self.seam_chk)
        tb.addStretch()
        self.quality = QComboBox(); self.quality.addItems(["預覽：半解析（流暢）", "預覽：全解析（清晰）"])
        self.quality.setCurrentIndex(1 if cfg("preview_quality") == "full" else 0)
        self.quality.currentIndexChanged.connect(self._quality)
        tb.addWidget(self.quality)
        br = QPushButton("⟲ 視角"); br.clicked.connect(lambda: self.view.reset_view()); tb.addWidget(br)
        center.addLayout(tb)
        # --- view
        self.view = preview.StitchView()
        self.view.viewChanged.connect(self._view_changed)
        center.addWidget(self.view, 1)
        self.status = QLabel(""); self.status.setObjectName("sub"); center.addWidget(self.status)
        self.film = FilmStrip(self)
        center.addWidget(self.film)
        # --- transport
        tr = QHBoxLayout()
        self.play_btn = QPushButton("▶"); self.play_btn.setFixedWidth(44); self.play_btn.clicked.connect(self.toggle_play)
        tr.addWidget(self.play_btn)
        for txt, d, tip in (("⏮", -1, "上一格 (←)"), ("⏭", 1, "下一格 (→)")):
            b = QPushButton(txt); b.setFixedWidth(36); b.setToolTip(tip); b.clicked.connect(lambda _, d=d: self.step(d)); tr.addWidget(b)
        self.tc = QLabel("00:00.00 / 00:00.00"); self.tc.setFont(QFont("Consolas", 10)); tr.addWidget(self.tc)
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(lambda v: self.player.seek(v))
        self.slider.sliderPressed.connect(lambda: self.player.pause())
        sbox = QVBoxLayout(); sbox.setSpacing(0); sbox.setContentsMargins(0, 0, 0, 0)
        sbox.addWidget(self.slider)
        self.keystrip = KeyStrip(self)
        sbox.addWidget(self.keystrip)
        tr.addLayout(sbox, 1)
        bi = QPushButton("[ 入點"); bi.setToolTip("I"); bi.clicked.connect(self.set_in); tr.addWidget(bi)
        bo = QPushButton("出點 ]"); bo.setToolTip("O"); bo.clicked.connect(self.set_out); tr.addWidget(bo)
        bc = QPushButton("✕"); bc.setToolTip("清除入出點"); bc.clicked.connect(self.clear_io); tr.addWidget(bc)
        self.audio_chk = QCheckBox("聲音"); self.audio_chk.setChecked(bool(cfg("preview_audio")))
        self.audio_chk.toggled.connect(lambda on: (set_cfg("preview_audio", on), self.player.set_audio(on)))
        tr.addWidget(self.audio_chk)
        center.addLayout(tr)
        root.addLayout(center, 1)
        # --- side tabs
        self.tabs = QTabWidget(); self.tabs.setFixedWidth(370)
        self.stab = gyro.default_stab()
        self.stab_S = None
        self.stab_key = None
        self.grade = color.default_grade()
        self.patch = {"mode": "off", "size": 30.0, "rotate": 0.0, "color": [24, 24, 24], "image": "", "feather": 1.0}
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.timeout.connect(self.save_project)
        self._loading = False
        self.tabs.addTab(self._build_calib(), "拼接校正")
        self.tabs.addTab(self._build_color(), "調色")
        self.tabs.addTab(self._build_output(), "輸出")
        root.addWidget(self.tabs)

        self.player = preview.Player(self)
        self.player.frameReady.connect(self._frame)
        self.player.stateChanged.connect(self._state)
        self.player.error.connect(lambda m: self.status.setText("⚠ " + m))
        self.view.histogramReady.connect(self.hist.set_pixels)
        for key, fn in (("Space", self.toggle_play), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                        ("Shift+Left", lambda: self.step(-10)), ("Shift+Right", lambda: self.step(10)),
                        ("I", self.set_in), ("O", self.set_out), ("S", lambda: self.seam_chk.toggle()),
                        ("Home", lambda: self.player.seek(0)), ("K", lambda: self.key_add())):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ---------------- calibration tab
    def _build_calib(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        self.btn_align = QPushButton("自動對齊"); self.btn_align.setObjectName("blue")
        self.btn_align.setToolTip("分析接縫重疊區，自動微調後鏡頭的圓心、半徑與角度")
        self.btn_align.clicked.connect(lambda: self.analyse("align"))
        self.btn_exp = QPushButton("自動曝光"); self.btn_exp.clicked.connect(lambda: self.analyse("exposure"))
        self.btn_color = QPushButton("自動色彩匹配")
        self.btn_color.setToolTip("比對前後鏡頭在接縫的紅綠藍色階，修正兩顆鏡頭的色差、色調與黑白位\n"
                                  "會在影片 10%、20%…90% 各取一格比對，選出整段最一致的設定")
        self.btn_color.clicked.connect(lambda: self.analyse("color"))
        row.addWidget(self.btn_align); row.addWidget(self.btn_exp); row.addWidget(self.btn_color)
        outer.addLayout(row)
        row2 = QHBoxLayout()
        b = QPushButton("存為相機預設"); b.setToolTip("之後同解析度的素材都會套用這組校正"); b.clicked.connect(self.save_calib); row2.addWidget(b)
        b = QPushButton("還原預設"); b.clicked.connect(self.reset_calib); row2.addWidget(b)
        outer.addLayout(row2)
        self.calib_msg = QLabel(""); self.calib_msg.setObjectName("sub"); self.calib_msg.setWordWrap(True)
        outer.addWidget(self.calib_msg)
        sg2 = QGroupBox("固定支架視差校正")
        sl2 = QVBoxLayout(sg2); sl2.setSpacing(3)
        r2 = QHBoxLayout()
        self.seam_chk = QCheckBox("開啟"); self.seam_chk.toggled.connect(self._seam_toggled); r2.addWidget(self.seam_chk)
        self.btn_seam = QPushButton("分析（約 1–2 分鐘）")
        self.btn_seam.setToolTip("相機鎖在機車／腳架上時，車身、把手這些很近的東西在接縫會錯開（視差），而且每一格都一樣；\n"
                                 "分析整段找出這個固定錯位，用固定的校正場對齊。遠處景物不受影響。")
        self.btn_seam.clicked.connect(self.analyse_seam); r2.addWidget(self.btn_seam, 1)
        sl2.addLayout(r2)
        self.seam_msg = QLabel("尚未分析"); self.seam_msg.setObjectName("sub"); self.seam_msg.setWordWrap(True)
        sl2.addWidget(self.seam_msg)
        outer.addWidget(sg2)
        self.seam_file = None
        tg = QGroupBox("色彩追蹤（隨時間自動變化）")
        tl_ = QVBoxLayout(tg); tl_.setSpacing(3)
        self.track_chk = QCheckBox("前後鏡頭色彩隨時間自動匹配")
        self.track_chk.setToolTip("光線變化大（停車場→大太陽、隧道、雲）時，整段用同一組修正不夠：\n"
                                  "每隔幾秒比對一次接縫兩邊的色彩，平滑後逐格套用")
        self.track_chk.toggled.connect(self._track_toggled)
        tl_.addWidget(self.track_chk)
        tr_ = QHBoxLayout()
        tr_.addWidget(QLabel("取樣間隔"))
        self.track_interval = QComboBox()
        for s_ in (1, 2, 3, 5, 10):
            self.track_interval.addItem(f"{s_} 秒", float(s_))
        self.track_interval.setCurrentIndex(2)
        tr_.addWidget(self.track_interval)
        self.btn_track = QPushButton("分析整段")
        self.btn_track.clicked.connect(self.analyse_track)
        tr_.addWidget(self.btn_track, 1)
        tl_.addLayout(tr_)
        self.track_smooth = ParamRow("平滑（秒）", 1, 30, 0.5, 1, 1, "越大顏色變化越緩；越小越快跟上光線變化")
        self.track_smooth.default = 6.0
        self.track_smooth.set_value(6.0)
        tl_.addWidget(self.track_smooth)
        self.auto_expo = ParamRow("自動亮度", 0, 1, 0.05, 2, 1,
                                  "隨時間自動拉平整體亮度：進隧道、樹蔭變暗時調亮，大太陽過亮時壓暗\n"
                                  "0＝關閉，0.5＝溫和（建議），1＝完全拉到標準亮度；範圍 -1 ～ +1.5 EV\n"
                                  "（用色彩追蹤的分析結果，需要先「分析整段」）")
        self.auto_expo.default = 0.0
        self.auto_expo.set_value(0.0)
        self.auto_expo.changed.connect(self._expo_changed)
        tl_.addWidget(self.auto_expo)
        self.expo_spark = Sparkline("#ffd166", None, fill=False); self.expo_spark.setFixedHeight(30); self.expo_spark.hide()
        self.expo_spark.setToolTip("自動亮度：每個時間點的曝光補償（往上＝調亮）")
        tl_.addWidget(self.expo_spark)
        self.track_bar = QProgressBar(); self.track_bar.setRange(0, 1000); self.track_bar.setFixedHeight(8)
        self.track_bar.setTextVisible(False); self.track_bar.hide()
        tl_.addWidget(self.track_bar)
        self.track_spark = Sparkline("#ff6577", None, fill=False); self.track_spark.setFixedHeight(34); self.track_spark.hide()
        self.track_spark.setToolTip("後鏡頭紅色增益隨時間的變化")
        tl_.addWidget(self.track_spark)
        self.track_msg = QLabel(""); self.track_msg.setObjectName("sub"); self.track_msg.setWordWrap(True)
        tl_.addWidget(self.track_msg)
        outer.addWidget(tg)
        self.color_track = None
        sg = QGroupBox("防震／水平（陀螺儀）")
        sl = QVBoxLayout(sg); sl.setSpacing(2)
        mr = QHBoxLayout()
        self.stab_mode = QComboBox()
        for k, t in gyro.MODES:
            self.stab_mode.addItem(t, k)
        self.stab_mode.setToolTip("水平鎖定：地平線永遠水平，畫面方向跟著相機\n"
                                  "防震＋水平：再把左右晃動抹平，轉彎時畫面平順跟上\n"
                                  "方向鎖定：畫面永遠朝同一個方位（適合 360 觀看）")
        self.stab_mode.currentIndexChanged.connect(lambda: self._stab_changed("mode", self.stab_mode.currentData()))
        mr.addWidget(self.stab_mode, 1)
        sl.addLayout(mr)
        self.stab_rows = {}
        for k, lab, lo, hi, st, dec, tip in (("smooth", "平滑（秒）", 0.2, 6, 0.1, 1, "越大越平穩，但轉彎時畫面跟得越慢"),
                                             ("offset_ms", "時間偏移 ms", -300, 300, 5, 0, "陀螺儀和畫面的時間差；畫面在晃動瞬間抖一下時微調"),
                                             ("tilt_tau", "水平修正（秒）", 1, 30, 0.5, 1, "越小越快修正到水平；機車過彎傾斜時建議 6 秒以上"),
                                             ("rs_ms", "滾動快門 ms", 0, 30, 1, 0,
                                              "［實驗功能］滾動快門（果凍）校正：感光元件由上往下逐行讀取，震動時畫面會扭曲；\n"
                                              "用陀螺儀逐行反向修正。0＝關閉；Fusion 實測約 10 ms。\n"
                                              "不需要開防震也能用；只對影片、GPU 轉檔有效")):
            r = ParamRow(lab, lo, hi, st, dec, 1, tip); r.default = gyro.default_stab()[k]
            r.changed.connect(lambda v, k=k: self._stab_changed(k, v))
            sl.addWidget(r)
            self.stab_rows[k] = r
        self.btn_sync = QPushButton("自動校正時間偏移")
        self.btn_sync.setToolTip("比對畫面的轉動和陀螺儀，找出這段影片最準的時間偏移（約 1–3 分鐘）")
        self.btn_sync.clicked.connect(self.analyse_sync)
        sl.addWidget(self.btn_sync)
        self.stab_msg = QLabel(""); self.stab_msg.setObjectName("sub"); self.stab_msg.setWordWrap(True)
        sl.addWidget(self.stab_msg)
        outer.addWidget(sg)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        inner = QWidget(); self.calib_layout = QVBoxLayout(inner); self.calib_layout.setContentsMargins(0, 0, 4, 0)
        sc.setWidget(inner)
        outer.addWidget(sc, 1)
        self.rows = {}
        return w

    def _build_rows(self):
        while self.calib_layout.count():
            it = self.calib_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.rows = {}
        W, H, fps = self.src_w, self.src_h, self.fps
        lens = lambda k: [
            (f"{k}.cx", "圓心 X", W * 0.4, W * 0.6, 0.5, 1, W, "鏡頭影像圓心的水平位置（像素）"),
            (f"{k}.cy", "圓心 Y", H * 0.4, H * 0.6, 0.5, 1, H, "鏡頭影像圓心的垂直位置（像素）"),
            (f"{k}.r", "半徑", H * 0.4, H * 0.6, 0.5, 1, H, "鏡頭視角邊緣的圓半徑（像素）"),
            (f"{k}.yaw", "偏航", -6, 6, 0.02, 2, 1, ""), (f"{k}.pitch", "俯仰", -6, 6, 0.02, 2, 1, ""),
            (f"{k}.roll", "滾轉", -6, 6, 0.02, 2, 1, ""), (f"{k}.ev", "曝光 EV", -1.5, 1.5, 0.01, 2, 1, ""),
            (f"{k}.vignette", "暗角補償", 0, 1, 0.01, 2, 1, "畫面邊緣變暗時調高"),
            (f"{k}.color.0", "紅 增益", 0.6, 1.6, 0.002, 3, 1, "這顆鏡頭的紅色強度（色彩匹配）"),
            (f"{k}.color.1", "綠 增益", 0.6, 1.6, 0.002, 3, 1, ""),
            (f"{k}.color.2", "藍 增益", 0.6, 1.6, 0.002, 3, 1, ""),
            (f"{k}.color.3", "紅 黑位", -0.15, 0.15, 0.001, 3, 1, "這顆鏡頭暗部的紅色（色階）"),
            (f"{k}.color.4", "綠 黑位", -0.15, 0.15, 0.001, 3, 1, ""),
            (f"{k}.color.5", "藍 黑位", -0.15, 0.15, 0.001, 3, 1, "")]
        groups = [("整體方向（水平校正）", [("orient.yaw", "朝向", -180, 180, 0.5, 1, 1, "畫面正前方的方向"),
                                    ("orient.pitch", "前後傾", -90, 90, 0.1, 1, 1, "相機前後傾斜時調整，讓地平線變直"),
                                    ("orient.roll", "左右傾", -90, 90, 0.1, 1, 1, "相機左右傾斜時調整")]),
                  ("接縫", [("fov", "鏡頭視角", 180, 210, 0.05, 2, 1, ""),
                           ("k3", "鏡頭畸變", -0.2, 0.5, 0.002, 3, 1, "魚眼鏡頭邊緣壓縮的程度（0＝等距魚眼；Fusion 約 0.244）"),
                           ("blend", "融合寬度", 0.5, 10, 0.1, 1, 1, "接縫漸變的寬度（度）"),
                           ("seamgain", "接縫色彩補償", 0, 90, 1, 0, 1,
                            "前後鏡頭的亮度、顏色不同（各自曝光、白平衡，路燈眩光只照到一顆）時，每一格量出接縫兩邊"
                            "紅綠藍各自的差，平滑地攤到接縫兩側這麼多度（0＝關）"),
                           ("seamwide", "接縫寬混合", 0, 18, 1, 0, 1,
                            "補償後還剩下的平滑差異（例如夜空的光暈）在這麼多度內漸變，畫面細節仍用上面的"
                            "融合寬度，所以近物不會出現疊影（0＝關）")]
                   + ([("back_offset", "後鏡時差(格)", -15, 15, 1, 0, fps, "前後鏡頭不同步時調整")] if self.clip["kind"] == "video" else [])),
                  ("前鏡頭", lens("front")), ("後鏡頭", lens("back"))]
        for title, rows in groups:
            gb = QGroupBox(title)
            gl = QVBoxLayout(gb); gl.setSpacing(0)
            for path, label, lo, hi, step, dec, scale, tip in rows:
                pr = ParamRow(label, lo, hi, step, dec, scale, tip)
                pr.default = self._get(self.defaults, path)
                pr.set_value(self._get(self.params, path))
                pr.changed.connect(lambda v, p=path: self._param_changed(p, v))
                gl.addWidget(pr)
                self.rows[path] = pr
            self.calib_layout.addWidget(gb)
        self.calib_layout.addStretch()

    @staticmethod
    def _get(P, path):
        o = P
        for k in path.split("."):
            o = o[int(k)] if isinstance(o, list) else o[k]
        return o

    @staticmethod
    def _set(P, path, v):
        ks = path.split(".")
        o = P
        for k in ks[:-1]:
            o = o[int(k)] if isinstance(o, list) else o[k]
        if isinstance(o, list):
            o[int(ks[-1])] = v
        else:
            o[ks[-1]] = v

    def _param_changed(self, path, v):
        self._set(self.params, path, float(v))
        self.view.set_params(self.params)
        self.dirty()
        if path == "back_offset":
            self.player.set_back_offset(int(round(v * self.fps)))

    def refresh_rows(self):
        for p, r in self.rows.items():
            r.set_value(self._get(self.params, p))
        self.view.set_params(self.params)
        self.dirty()

    def save_calib(self):
        if not self.clip:
            return
        p = json.loads(json.dumps(self.params))
        p.pop("orient", None); p.pop("back_offset", None)
        cal = dict(cfg("calibration") or {})
        cal[f"{self.src_w}x{self.src_h}"] = p
        set_cfg("calibration", cal)
        self.calib_msg.setText(f"✓ 已儲存：之後 {self.src_w}×{self.src_h} 的素材都會套用這組校正")

    def reset_calib(self):
        if not self.clip:
            return
        self.params = json.loads(json.dumps(self.defaults))
        self.refresh_rows()
        self.calib_msg.setText("已還原為內建預設值")

    def analyse(self, kind):
        if not self.clip or getattr(self, "_analysing", False):
            return
        import align
        self._analysing = True
        for b in (self.btn_align, self.btn_exp, self.btn_color):
            b.setEnabled(False)
        self.calib_msg.setText({"align": "比對接縫中：取影片 10%、30%、50%、70%、90% 五格（約 20–60 秒）…",
                                "exposure": "比對亮度中：取影片 10%～90% 共 9 格…",
                                "color": "比對色彩中：取影片 10%、20%…90% 共 9 格，找出最佳設定（約 15 秒）…"}[kind])
        clip, params = self.clip, json.loads(json.dumps(self.params))
        if clip["kind"] == "video":
            d = self.player.total / self.fps
            pts = [0.1, 0.3, 0.5, 0.7, 0.9] if kind == "align" else align.SWEEP
            times = [d * p for p in pts]
        else:
            times = [0.0]
        bus = self.main.bus

        def work():
            try:
                fn = {"align": align.auto_align, "exposure": align.auto_exposure, "color": align.auto_color}[kind]
                r = fn(clip, params, times)
                bus.analysisDone.emit(kind, (clip["id"], r))
            except Exception as e:
                bus.analysisDone.emit(kind, (clip["id"], e))
        threading.Thread(target=work, daemon=True).start()

    def on_analysis(self, kind, payload):
        if kind == "track":
            return self._track_done(payload)
        if kind == "seam":
            self._seaming = False
            self.btn_seam.setEnabled(True)
            cid, r = payload
            if isinstance(r, Exception):
                self.seam_msg.setText(f"⚠ {r}")
                return
            if self.clip and cid == self.clip["id"]:
                path, st = r
                self.seam_file = path
                self.seam_chk.setChecked(True)
                self.apply_seam()
                self.dirty()
                self.seam_msg.setText(f"✓ 固定物的接縫錯位最大 {st['max_px'] * st['deg_per_px']:.1f}°，已校正"
                                      f"（約 {st['fixed_share'] * 100:.0f}% 的接縫是固定物）")
            return
        if kind == "tone":
            self._tone_done(payload)
            return
        if kind == "sync":
            self._syncing = False
            self.btn_sync.setEnabled(True)
            cid, r = payload
            if isinstance(r, Exception):
                self.stab_msg.setText(f"⚠ {r}")
                return
            if self.clip and cid == self.clip["id"]:
                self.stab["offset_ms"] = float(r["offset_ms"])
                self.stab_rows["offset_ms"].set_value(float(r["offset_ms"]))
                self.stab_key = None
                self.dirty()
                self.refresh_stab()
                self.stab_msg.setText(f"✓ 時間偏移 {r['offset_ms']} ms（比對 {r['pairs']} 組畫面，誤差 {r['error']:.2f}）")
            return
        if kind == "thumbs":
            cid, r = payload
            if self.clip and cid == self.clip["id"] and not isinstance(r, Exception):
                self.film.set_images(r)
            return
        if kind == "stab":
            cid, r = payload
            if not self.clip or cid != self.clip["id"]:
                return
            if isinstance(r, Exception):
                self.stab_msg.setText(f"⚠ {r}")
                return
            key, S = r
            if key != self.stab_key:
                return
            self.stab_S = S
            self.stab_msg.setText(f"✓ 陀螺儀資料 {len(S)} 格已套用" + ("（畫面正前方＝前鏡頭；要看行進方向請把「整體方向 → 朝向」設為 180）"
                                                                  if self.stab["mode"] != "lock" else ""))
            if not self.player.playing:
                self.player.seek(self.player.frame)
            return
        self._analysing = False
        for b in (self.btn_align, self.btn_exp, self.btn_color):
            b.setEnabled(True)
        cid, r = payload
        if isinstance(r, Exception):
            self.calib_msg.setText(f"⚠ 分析失敗：{r}")
            return
        if not self.clip or cid != self.clip["id"]:
            return
        if kind == "align":
            for lens in ("front", "back"):
                for k in ("cx", "cy", "r", "yaw", "pitch", "roll"):
                    self.params[lens][k] = r["params"][lens][k]
            q = lambda c: round((1 - c) * 100)
            self.calib_msg.setText(f"✓ 已對齊：接縫吻合度 {q(r['before'])}% → {q(r['after'])}%" if r["improved"]
                                   else f"目前設定已是最佳（吻合度 {q(r['before'])}%）")
        elif kind == "color":
            for lens in ("front", "back"):
                self.params[lens]["color"] = list(r["params"][lens]["color"])
            self.calib_msg.setText(
                f"✓ 已匹配前後鏡頭色彩（比對 {r['frames']} 格）：色階差 {r['before']:.1f} → {r['after']:.1f}，"
                f"採用「{r['chosen']}」的設定。可在「前鏡頭／後鏡頭」的紅綠藍增益與黑位微調。")
        else:
            self.params["front"]["ev"] = r["params"]["front"]["ev"]
            self.params["back"]["ev"] = r["params"]["back"]["ev"]
            self.calib_msg.setText(f"✓ 已校正：前後鏡頭亮度差 {abs(r['ev_diff']):.2f} EV")
        self.refresh_rows()

    def analyse_sync(self):
        if not self.clip or self.clip["kind"] != "video" or getattr(self, "_syncing", False):
            return
        self._syncing = True
        self.btn_sync.setEnabled(False)
        self.stab_msg.setText("比對畫面轉動與陀螺儀中（約 1–3 分鐘）…")
        clip, params = self.clip, json.loads(json.dumps(self.params))
        bus = self.main.bus

        def work():
            import gyrosync
            try:
                bus.analysisDone.emit("sync", (clip["id"], gyrosync.estimate(clip, params)))
            except Exception as e:
                bus.analysisDone.emit("sync", (clip["id"], e))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- fixed-mount seam parallax
    def analyse_seam(self):
        if not self.clip or self.clip["kind"] != "video" or getattr(self, "_seaming", False):
            return
        self._seaming = True
        self.btn_seam.setEnabled(False)
        self.seam_msg.setText("分析接縫中：取整段 40 格比對前後鏡頭…")
        clip, params, folder = self.clip, json.loads(json.dumps(self.params)), self.main.folder
        bus = self.main.bus

        def work():
            import seamflow
            try:
                r = seamflow.measure(clip, params, n_frames=40)
                path = seamflow.save(r, folder, clip["id"])
                bus.analysisDone.emit("seam", (clip["id"], (path, r["stats"])))
            except Exception as e:
                bus.analysisDone.emit("seam", (clip["id"], e))
        threading.Thread(target=work, daemon=True).start()

    def _seam_toggled(self, on):
        self.apply_seam()
        self.dirty()

    def apply_seam(self):
        import seamflow
        sf = seamflow.load(self.seam_file) if (self.seam_chk.isChecked() and self.seam_file) else None
        self.view.set_seam(sf)

    # ---------------- colour track
    def analyse_track(self):
        if not self.clip or self.clip["kind"] != "video" or getattr(self, "_tracking", False):
            return
        import colortrack
        self._tracking = True
        self._track_cancel = False
        self.btn_track.setText("取消")
        self.btn_track.clicked.disconnect()
        self.btn_track.clicked.connect(lambda: setattr(self, "_track_cancel", True))
        self.track_bar.setValue(0); self.track_bar.show()
        interval = float(self.track_interval.currentData())
        smooth = float(self.track_smooth.sp.value())
        n = int(self.player.total / self.fps / interval)
        self.track_msg.setText(f"分析中：每 {interval:g} 秒取一格，共約 {n} 格（約 {max(1, n // 120)}–{max(2, n // 60)} 分鐘）…")
        clip, params = self.clip, json.loads(json.dumps(self.params))
        bus = self.main.bus

        def work():
            try:
                r = colortrack.analyse(clip, params, interval, smooth,
                                       progress=lambda p: bus.analysisProgress.emit("track", p),
                                       cancel=lambda: self._track_cancel)
                bus.analysisDone.emit("track", (clip["id"], r))
            except Exception as e:
                bus.analysisDone.emit("track", (clip["id"], e))
        threading.Thread(target=work, daemon=True).start()

    def on_progress(self, kind, p):
        if kind == "track":
            self.track_bar.setValue(int(p * 1000))

    def _track_done(self, payload):
        self._tracking = False
        self.btn_track.setText("分析整段")
        self.btn_track.clicked.disconnect()
        self.btn_track.clicked.connect(self.analyse_track)
        self.track_bar.hide()
        cid, r = payload
        if isinstance(r, Exception):
            self.track_msg.setText(f"⚠ {r}")
            return
        if not self.clip or cid != self.clip["id"]:
            return
        self.color_track = r
        self.track_chk.setChecked(True)
        self._show_track()
        self.dirty()

    def _show_track(self):
        r = self.color_track
        if not r:
            self.track_spark.hide()
            self.track_msg.setText("尚未分析。按「分析整段」開始（分析結果會跟著這段素材儲存）。")
            return
        import numpy as np
        C = np.asarray(r["c"])
        self.track_spark.set_values(list(C[:, 6]))
        self.track_spark.show()
        self.track_msg.setText(f"✓ {r['samples']} 格取樣（每 {r['interval']:g} 秒，平滑 {r['smooth']:g} 秒）："
                               f"接縫色階差 原始 {r['before']:.1f} → 整段固定修正 {r['static']:.1f} → 隨時間修正 {r['after']:.1f}"
                               + ("" if r.get("lum_t") else "\n（這份分析是舊版做的，沒有亮度資料；要用自動亮度請重新分析）"))
        self._show_expo()

    def _show_expo(self):
        r, s_ = self.color_track, float(self.auto_expo.sp.value())
        if not r or not r.get("lum_t") or s_ <= 0:
            self.expo_spark.hide()
            return
        import colortrack
        import numpy as np
        E = [colortrack.exposure_at(r, t, s_) for t in r["lum_t"]]
        self.expo_spark.set_values(E)
        self.expo_spark.setToolTip(f"自動亮度：曝光補償 {min(E):+.2f} ～ {max(E):+.2f} EV（往上＝調亮）")
        self.expo_spark.show()

    def _expo_changed(self, v):
        self._show_expo()
        self.dirty()
        if self.clip and not self.player.playing:
            self.player.seek(self.player.frame)

    def expo_now(self, idx):
        ev = 0.0
        if self.track_chk.isChecked() and self.color_track:
            import colortrack
            ev = colortrack.exposure_at(self.color_track, (idx + 0.5) / self.fps, float(self.auto_expo.sp.value()))
        try:
            import fusionlook
            if fusionlook.preview_active() and color.is_identity(self.grade):
                ev += fusionlook.exposure_at_nowait(self.clip, (idx + 0.5) / self.fps, self.fps)   # 原廠亮度
        except Exception:
            pass
        return ev

    def _track_toggled(self, on):
        self.dirty()
        if not self.player.playing:
            self.player.seek(self.player.frame)

    def _lens_d(self, idx):
        """原廠鏡頭曝光: EV front - EV back of this frame, None when it does not apply (yet)."""
        if not (self.clip and self.clip.get("kind") == "video"):
            return None
        if not ((cfg("color_mode") or "auto") == "gopro" and color.is_identity(self.grade)):
            return None
        try:
            import fusionlook
            return fusionlook.lens_at_nowait(self.clip, (idx + 0.5) / self.fps, self.fps) if fusionlook.lens_active() else None
        except Exception:
            return None

    def tone_now(self, idx):
        d = self._lens_d(idx)
        if d is None:
            return None
        import fusionlook
        return fusionlook.tone_tables(d)

    def track_now(self, idx):
        t = (idx + 0.5) / self.fps
        gopro = (cfg("color_mode") or "auto") == "gopro" and color.is_identity(self.grade)
        cc = None
        if self.track_chk.isChecked() and self.color_track:
            import colortrack
            cc = colortrack.at(self.color_track, t)
            if cc and self._lens_d(idx) is not None:      # 原廠色彩: per-lens exposure from the telemetry instead (same as the export)
                cc = (geo.lens_color(self.params, "front"), geo.lens_color(self.params, "back"))
            wb = float(cfg("gopro_wb", 0.8) or 0)
            if cc and wb > 0 and gopro:
                g = colortrack.wb_at(self.color_track, t, wb)      # 原廠白平衡
                if g is not None:
                    cc = (colortrack.apply_gain(cc[0], g), colortrack.apply_gain(cc[1], g))
        return cc

    # ---------------- gyro stabilisation
    def _stab_changed(self, k, v):
        self.stab[k] = v
        self.dirty()
        self.refresh_stab()

    def refresh_stab(self, ui=False):
        if ui:
            self.stab_mode.blockSignals(True)
            self.stab_mode.setCurrentIndex(max(0, self.stab_mode.findData(self.stab["mode"])))
            self.stab_mode.blockSignals(False)
            for k, r in self.stab_rows.items():
                r.set_value(float(self.stab[k]))
        video = bool(self.clip and self.clip["kind"] == "video")
        self.stab_mode.setEnabled(video)
        if not video or self.stab["mode"] == "off":
            self.view.stab = None
            self.view.update()
            self.stab_msg.setText("" if video else "照片沒有陀螺儀資料")
            return
        key = (self.clip["id"], self.stab["mode"], round(self.stab["smooth"], 2), round(self.stab["offset_ms"]),
               round(self.stab["tilt_tau"], 1))
        if key == self.stab_key and self.stab_S is not None:
            self.player.seek(self.player.frame) if not self.player.playing else None
            return
        self.stab_key = key
        self.stab_msg.setText("讀取陀螺儀資料、計算中…（長片第一次約 10–30 秒，之後會快取）")
        clip, stab, fps = self.clip, dict(self.stab), self.fps
        bus = self.main.bus

        def work():
            try:
                S = gyro.frame_rotations(clip["front"], fps, 0, 10 ** 9, stab)
                bus.analysisDone.emit("stab", (clip["id"], (key, S)))
            except Exception as e:
                bus.analysisDone.emit("stab", (clip["id"], e))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- colour tab
    def _build_color(self):
        from PySide6.QtWidgets import QColorDialog
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        self.hist = Histogram()
        outer.addWidget(self.hist)
        row = QHBoxLayout()
        self.cmp_chk = QCheckBox("前後對比（拖曳畫面上的白線）")
        self.cmp_chk.toggled.connect(lambda on: (setattr(self.view, "compare", 0.5 if on else -1.0), self.view.update()))
        row.addWidget(self.cmp_chk)
        row.addStretch()
        outer.addLayout(row)
        pr = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(120)
        pr.addWidget(self.preset_combo, 1)
        b = QPushButton("套用"); b.clicked.connect(self._apply_color_preset); pr.addWidget(b)
        b = QPushButton("存成濾鏡"); b.clicked.connect(self._save_color_preset); pr.addWidget(b)
        self.btn_tone = QPushButton("✦ 自動調色")
        self.btn_tone.setToolTip("分析整段影片的明暗與色彩，自動調整對比、黑色、白色、亮度、自然飽和度\n"
                                 "（Fusion 原始畫面偏灰；夜景會用較輕的調整）約 20 秒")
        self.btn_tone.clicked.connect(self.auto_tone)
        pr.addWidget(self.btn_tone)
        b = QPushButton("刪除"); b.clicked.connect(self._del_color_preset); pr.addWidget(b)
        outer.addLayout(pr)
        self._fill_color_presets()
        sc = QScrollArea(); sc.setWidgetResizable(True)
        inner = QWidget(); lay = QVBoxLayout(inner); lay.setContentsMargins(0, 0, 4, 0)
        sc.setWidget(inner)
        outer.addWidget(sc, 1)
        self.color_rows = {}
        groups = [("光線", ["exposure", "brightness", "contrast", "highlights", "shadows", "whites", "blacks"]),
                  ("顏色", ["temperature", "tint", "saturation", "vibrance"])]
        spec = {k: (lab, lo, hi, st, d, tip) for k, lab, lo, hi, st, d, tip in color.SLIDERS}
        for title, keys in groups:
            gb = QGroupBox(title); gl = QVBoxLayout(gb); gl.setSpacing(0)
            for k in keys:
                lab, lo, hi, st, d, tip = spec[k]
                r = ParamRow(lab, lo, hi, st, 2, 1, tip); r.default = d
                r.changed.connect(lambda v, k=k: self._grade_changed(k, v))
                gl.addWidget(r); self.color_rows[k] = r
            lay.addWidget(gb)
        gb = QGroupBox("畫質（銳利化／降噪）"); gl = QVBoxLayout(gb); gl.setSpacing(0)
        for k, lab, tip in (("sharpen", "銳利化", "讓細節更清楚；太高會出現白邊"),
                            ("denoise", "降噪", "減少暗處、停車場、夜間的雜訊顆粒（保留邊緣）")):
            r = ParamRow(lab, 0, 1, 0.01, 2, 1, tip); r.default = 0.0
            r.changed.connect(lambda v, k=k: self._grade_changed(k, v))
            gl.addWidget(r); self.color_rows[k] = r
        lay.addWidget(gb)
        gb = QGroupBox("HSL（個別顏色）"); gl = QVBoxLayout(gb); gl.setSpacing(2)
        brow = QHBoxLayout(); brow.setSpacing(3)
        self.band_group = QButtonGroup(self)
        swatch = {"red": "#e5484d", "orange": "#f08c2e", "yellow": "#e8c547", "green": "#4cb35a", "aqua": "#3fc1c9",
                  "blue": "#3b74e0", "purple": "#8b5cf6", "magenta": "#d946a8"}
        for i, (b_, lab, _) in enumerate(color.BANDS):
            bt = QPushButton(lab); bt.setCheckable(True); bt.setFixedHeight(26)
            bt.setStyleSheet(f"QPushButton{{border-bottom:3px solid {swatch[b_]};padding:2px 4px}} QPushButton:checked{{background:{swatch[b_]}55}}")
            self.band_group.addButton(bt, i); brow.addWidget(bt)
            if i == 0:
                bt.setChecked(True)
        gl.addLayout(brow)
        self.band_group.idClicked.connect(lambda i: self.refresh_color())
        self.hsl_rows = {}
        for k, lab, lo, hi, st in (("h", "色相", -30, 30, 0.5), ("s", "飽和度", -1, 1, 0.01), ("l", "明度", -1, 1, 0.01)):
            r = ParamRow(lab, lo, hi, st, 2 if st < 0.5 else 1, 1); r.default = 0.0
            r.changed.connect(lambda v, k=k: self._hsl_changed(k, v))
            gl.addWidget(r); self.hsl_rows[k] = r
        lay.addWidget(gb)
        gb = QGroupBox("LUT（.cube 調色檔）"); gl = QVBoxLayout(gb)
        lr = QHBoxLayout()
        self.lut_lab = QLabel("未使用"); self.lut_lab.setObjectName("sub"); lr.addWidget(self.lut_lab, 1)
        b = QPushButton("選擇…"); b.clicked.connect(self._pick_lut); lr.addWidget(b)
        b = QPushButton("清除"); b.clicked.connect(lambda: self._grade_changed("lut", "")); lr.addWidget(b)
        gl.addLayout(lr)
        r = ParamRow("強度", 0, 1, 0.01, 2, 1); r.default = 1.0
        r.changed.connect(lambda v: self._grade_changed("lut_strength", v)); gl.addWidget(r)
        self.color_rows["lut_strength"] = r
        lay.addWidget(gb)
        gb = QGroupBox("底部遮擋（蓋住車架／腳架）"); gl = QVBoxLayout(gb)
        mr = QHBoxLayout()
        self.patch_mode = QComboBox()
        for k, t in (("off", "關閉"), ("color", "純色圓片"), ("image", "圖片／Logo")):
            self.patch_mode.addItem(t, k)
        self.patch_mode.currentIndexChanged.connect(lambda: self._patch_changed("mode", self.patch_mode.currentData()))
        mr.addWidget(self.patch_mode, 1)
        self.patch_color_btn = QPushButton("顏色")

        def pick_color():
            from PySide6.QtGui import QColor
            c = QColorDialog.getColor(QColor(*self.patch.get("color", [24, 24, 24])), self, "遮擋顏色")
            if c.isValid():
                self._patch_changed("color", [c.red(), c.green(), c.blue()])
        self.patch_color_btn.clicked.connect(pick_color)
        mr.addWidget(self.patch_color_btn)
        bi = QPushButton("圖片…")

        def pick_img():
            p, _ = QFileDialog.getOpenFileName(self, "選擇 Logo 圖片", "", "圖片 (*.png *.jpg *.jpeg *.webp *.bmp)")
            if p:
                self._patch_changed("image", p.replace("/", "\\"))
                self.patch_mode.setCurrentIndex(2)
        bi.clicked.connect(pick_img)
        mr.addWidget(bi)
        gl.addLayout(mr)
        self.patch_rows = {}
        for k, lab, lo, hi, st, d in (("size", "大小（度）", 5, 100, 0.5, 30.0), ("rotate", "旋轉", -180, 180, 1, 0.0),
                                      ("feather", "邊緣柔化", 0, 10, 0.1, 1.0)):
            r = ParamRow(lab, lo, hi, st, 1, 1); r.default = d
            r.changed.connect(lambda v, k=k: self._patch_changed(k, v)); gl.addWidget(r); self.patch_rows[k] = r
        hint = QLabel("切到「小星球」或在 360 環景往下看最容易調整。PNG 透明背景會保留。")
        hint.setObjectName("sub"); hint.setWordWrap(True)
        gl.addWidget(hint)
        lay.addWidget(gb)
        lay.addStretch()
        br = QHBoxLayout()
        b = QPushButton("複製調色"); b.clicked.connect(self._copy_grade); br.addWidget(b)
        b = QPushButton("貼上調色"); b.clicked.connect(self._paste_grade); br.addWidget(b)
        b = QPushButton("套用到全部"); b.clicked.connect(self._grade_to_all); br.addWidget(b)
        b = QPushButton("重設"); b.clicked.connect(lambda: self._set_grade(color.default_grade())); br.addWidget(b)
        outer.addLayout(br)
        return w

    def _fill_color_presets(self):
        self.preset_combo.clear()
        for name in color.BUILTIN_PRESETS:
            self.preset_combo.addItem(name, ("builtin", name))
        for name in projects.user_presets():
            self.preset_combo.addItem("★ " + name, ("user", name))

    def _apply_color_preset(self):
        kind, name = self.preset_combo.currentData() or ("builtin", "原始")
        g = color.BUILTIN_PRESETS.get(name) if kind == "builtin" else projects.user_presets().get(name)
        g2 = color.merge(g)
        if kind == "builtin" and not g2["lut"]:
            g2["lut"] = self.grade.get("lut", "")
        self._set_grade(g2)

    def auto_tone(self):
        if not self.clip or getattr(self, "_toning", False):
            return
        self._toning = True
        self.btn_tone.setEnabled(False); self.btn_tone.setText("分析中…")
        clip, params, base = self.clip, json.loads(json.dumps(self.params)), json.loads(json.dumps(self.grade))
        bus = self.main.bus

        def work():
            import autotone
            try:
                bus.analysisDone.emit("tone", (clip["id"], autotone.analyse(clip, params, base)))
            except Exception as e:
                bus.analysisDone.emit("tone", (clip["id"], e))
        threading.Thread(target=work, daemon=True).start()

    def _tone_done(self, payload):
        self._toning = False
        self.btn_tone.setEnabled(True); self.btn_tone.setText("✦ 自動調色")
        cid, r = payload
        if isinstance(r, Exception):
            self.main.statusBar().showMessage(f"自動調色失敗：{r}", 8000)
            return
        if not self.clip or cid != self.clip["id"]:
            return
        import autotone
        g = color.merge(self.grade)
        g.update(r["values"])
        self._set_grade(g)
        self.main.statusBar().showMessage("自動調色：" + autotone.describe(r), 15000)

    def _save_color_preset(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "存成濾鏡", "濾鏡名稱：")
        if ok and name.strip():
            projects.save_user_preset(name.strip(), json.loads(json.dumps(self.grade)))
            self._fill_color_presets()
            self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findText("★ " + name.strip())))

    def _del_color_preset(self):
        kind, name = self.preset_combo.currentData() or (None, None)
        if kind == "user" and QMessageBox.question(self, "刪除濾鏡", f"刪除「{name}」？") == QMessageBox.Yes:
            projects.delete_user_preset(name)
            self._fill_color_presets()

    def _pick_lut(self):
        p, _ = QFileDialog.getOpenFileName(self, "選擇 LUT", "", "3D LUT (*.cube)")
        if not p:
            return
        try:
            color.load_cube(p)
        except Exception as e:
            QMessageBox.warning(self, "LUT", f"無法使用這個 LUT：{e}")
            return
        self._grade_changed("lut", p.replace("/", "\\"))

    def _grade_changed(self, key, v):
        self.grade[key] = v
        self.view.set_grade(self.grade)
        if key == "lut":
            self.lut_lab.setText(Path(v).name if v else self._auto_color_text())
        self.dirty()

    def _hsl_changed(self, k, v):
        band = color.BANDS[max(0, self.band_group.checkedId())][0]
        self.grade["hsl"][band][k] = float(v)
        self.view.set_grade(self.grade)
        self.dirty()

    def _patch_changed(self, k, v):
        self.patch[k] = v
        self.view.set_patch(self.patch)
        self.dirty()

    def _set_grade(self, g):
        self.grade = color.merge(g)
        self.refresh_color()
        self.dirty()

    def refresh_color(self):
        for k, r in self.color_rows.items():
            r.set_value(self.grade[k])
        band = color.BANDS[max(0, self.band_group.checkedId())][0]
        for k, r in self.hsl_rows.items():
            r.set_value(self.grade["hsl"][band][k])
        self.lut_lab.setText(Path(self.grade["lut"]).name if self.grade.get("lut") else self._auto_color_text())
        self.patch_mode.blockSignals(True)
        self.patch_mode.setCurrentIndex(max(0, self.patch_mode.findData(self.patch.get("mode", "off"))))
        self.patch_mode.blockSignals(False)
        for k, r in self.patch_rows.items():
            r.set_value(float(self.patch.get(k, r.default)))
        self.view.set_grade(self.grade)
        self.view.set_patch(self.patch)

    def _auto_color_text(self) -> str:
        """What an ungraded clip gets when it is converted (and what the preview shows)."""
        if not color.is_identity(self.grade):
            return "未使用"
        if (cfg("color_mode") or "auto") == "gopro" and core.gopro_lut_path():
            return "原廠色彩（自動；調色後就不套用）"
        return "未使用（轉檔時自動調色）" if cfg("auto_tone", True) else "未使用"

    def _copy_grade(self):
        QApplication.clipboard().setText(json.dumps({"fusionstitch_grade": self.grade, "patch": self.patch}, ensure_ascii=False))
        self.main.toast("已複製調色（含底部遮擋）")

    def _paste_grade(self):
        try:
            d = json.loads(QApplication.clipboard().text())
            g = d["fusionstitch_grade"]
        except Exception:
            self.main.toast("剪貼簿裡沒有調色資料")
            return
        self.patch.update(d.get("patch") or {})
        self._set_grade(g)

    def _grade_to_all(self):
        n = len(self.main.clips)
        if QMessageBox.question(self, "套用到全部素材", f"把目前的調色和底部遮擋套用到這個資料夾的全部 {n} 組素材？\n（各素材的校正、入出點不變）") != QMessageBox.Yes:
            return
        self.save_project()
        for cid in self.main.clips:
            d = projects.get(self.main.folder, cid)
            d["grade"] = json.loads(json.dumps(self.grade))
            d["patch"] = dict(self.patch)
            projects.put(self.main.folder, cid, d)
            self.main.library.mark_edited(cid)
        self.main.toast(f"已套用到 {n} 組素材")

    # ---------------- per-clip project (auto-saved)
    def dirty(self):
        if self.clip and not self._loading:
            self._save_timer.start(800)

    def save_project(self):
        if not self.clip:
            return
        d = {"params": json.loads(json.dumps(self.params)), "grade": json.loads(json.dumps(self.grade)),
             "stab": dict(self.stab), "seamflow_file": self.seam_file, "seamflow_on": self.seam_chk.isChecked(),
             "color_track": self.color_track, "color_track_on": self.track_chk.isChecked(),
             "track_smooth": float(self.track_smooth.sp.value()), "track_interval": float(self.track_interval.currentData()),
             "auto_exposure": float(self.auto_expo.sp.value()),
             "patch": dict(self.patch), "in": self.in_f, "out": self.out_f,
             "projection": self.proj_kind(), "speed": float(self.speed.currentData() or 1.0),
             "planet": dict(self.view.planet), "flat": dict(self.view.flat), "paths": self.paths}
        projects.put(self.main.folder, self.clip["id"], d)
        self.main.library.mark_edited(self.clip["id"])

    # ---------------- output tab
    def _build_output(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(8, 8, 8, 8)
        gp = QGroupBox("輸出格式")
        f = QFormLayout(gp)
        self.proj = QComboBox(); self.proj.addItems(["360 全景（YouTube 360）", "小星球（一般影片）", "平面取景（一般影片，可運鏡）"])
        self.proj.setCurrentIndex({"planet": 1, "flat": 2}.get(cfg("projection"), 0))
        self.proj.currentIndexChanged.connect(self._proj_changed)
        f.addRow("投影", self.proj)
        self.preset = QComboBox()
        f.addRow("預設", self.preset)
        self.preset_desc = QLabel(""); self.preset_desc.setObjectName("sub"); self.preset_desc.setWordWrap(True)
        f.addRow("", self.preset_desc)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.size = QComboBox()
        self.size.currentIndexChanged.connect(self._size_changed)
        f.addRow("解析度", self.size)
        self.engine = QComboBox()
        self.engine.addItem("自動（GPU 全程加速）", "auto"); self.engine.addItem("GPU：NVDEC＋GPU 拼接＋NVENC", "gpu")
        self.engine.addItem("CPU：x265（很慢）", "cpu")
        f.addRow("編碼器", self.engine)
        self.speed = QComboBox()
        for v_, t_ in ((0.25, "0.25x 慢動作（60 fps 素材）"), (0.5, "0.5x 慢動作（60 fps 素材）"), (1.0, "1x 原速"),
                       (2.0, "2x 縮時"), (4.0, "4x 縮時"), (8.0, "8x 縮時"), (10.0, "10x 縮時"), (15.0, "15x 縮時"),
                       (30.0, "30x 縮時"), (60.0, "60x 縮時")):
            self.speed.addItem(t_, v_)
        self.speed.setCurrentIndex(2)
        self.speed.setToolTip("縮時：每 N 格取 1 格（搭配「防震＋水平」就是超級縮時 Hyperlapse）\n"
                              "慢動作：每一格都保留、用較低的播放速率輸出（3K 60fps 素材最適合）\n"
                              "非原速時不含聲音")
        self.speed.currentIndexChanged.connect(lambda: (self.update_out_path(), self.dirty()))
        f.addRow("速度", self.speed)
        self.with_audio = QCheckBox("包含聲音"); self.with_audio.setChecked(True)
        self.with_audio.toggled.connect(self._update_spatial)
        f.addRow("", self.with_audio)
        self.spatial = QCheckBox("360 空間音訊（YouTube）［實驗功能］")
        self.spatial.setChecked(bool(cfg("spatial_audio")))
        self.spatial.setToolTip("用背面記憶卡的 4 聲道 WAV 輸出一階 ambisonics（ambiX）並寫入 YouTube 空間音訊資訊。\n"
                                "Fusion 的 WAV 是 4 支麥克風的原始訊號，GoPro 沒有公開麥克風位置，\n"
                                "方向是推算的，可能與畫面不完全一致。只適用 360 全景，且每個章節都要有 WAV。")
        self.spatial.toggled.connect(lambda v: set_cfg("spatial_audio", bool(v)) if self.spatial.isEnabled() else None)
        f.addRow("", self.spatial)
        l.addWidget(gp)
        self.planet_box = QGroupBox("視角與運鏡（在預覽畫面拖曳／滾輪調整）")
        pf = QFormLayout(self.planet_box)
        self.planet_spin = {}
        for key, label, lo, hi in (("yaw", "左右", -720, 720), ("pitch", "上下", -180, 180), ("roll", "傾斜", -180, 180), ("fov", "視角／縮放", 15, 340)):
            sp = QDoubleSpinBox(); sp.setRange(lo, hi); sp.setDecimals(1); sp.setKeyboardTracking(False)
            sp.valueChanged.connect(lambda v, k=key: self._planet_spin(k, v))
            pf.addRow(label, sp)
            self.planet_spin[key] = sp
        kr = QHBoxLayout(); kr.setSpacing(4)
        self.btn_key_add = QPushButton("＋ 關鍵影格"); self.btn_key_add.setToolTip("在目前時間記下目前的視角（K）")
        self.btn_key_add.clicked.connect(self.key_add); kr.addWidget(self.btn_key_add, 1)
        b = QPushButton("⏮"); b.setToolTip("上一個關鍵影格"); b.setFixedWidth(34); b.clicked.connect(lambda: self.key_jump(-1)); kr.addWidget(b)
        b = QPushButton("⏭"); b.setToolTip("下一個關鍵影格"); b.setFixedWidth(34); b.clicked.connect(lambda: self.key_jump(1)); kr.addWidget(b)
        b = QPushButton("刪除"); b.setToolTip("刪除目前時間的關鍵影格"); b.clicked.connect(self.key_del); kr.addWidget(b)
        b = QPushButton("清除"); b.setToolTip("清除全部關鍵影格"); b.clicked.connect(self.key_clear); kr.addWidget(b)
        pf.addRow(kr)
        self.key_lab = QLabel(""); self.key_lab.setObjectName("sub"); self.key_lab.setWordWrap(True)
        pf.addRow(self.key_lab)
        self.paths = {"planet": [], "flat": []}
        hint = QLabel("沒有關鍵影格＝固定視角。設兩個以上，鏡頭會在它們之間平順移動（播放預覽即可看到）。"
                      "小星球往上拖超過 90° 會變成「隧道」效果。")
        hint.setObjectName("sub"); hint.setWordWrap(True)
        pf.addRow(hint)
        l.addWidget(self.planet_box)
        gr = QGroupBox("範圍")
        rl = QVBoxLayout(gr)
        self.range_lab = QLabel("整段"); self.range_lab.setWordWrap(True); rl.addWidget(self.range_lab)
        l.addWidget(gr)
        go = QGroupBox("輸出檔案")
        ol = QVBoxLayout(go)
        h = QHBoxLayout()
        self.out_edit = QLineEdit(); h.addWidget(self.out_edit, 1)
        b = QPushButton("…"); b.setFixedWidth(32); b.clicked.connect(self._browse_out); h.addWidget(b)
        ol.addLayout(h)
        l.addWidget(go)
        self.btn_queue = QPushButton("加入轉檔佇列"); self.btn_queue.setObjectName("start"); self.btn_queue.clicked.connect(self.add_job)
        l.addWidget(self.btn_queue)
        self.btn_still = QPushButton("目前畫面存成照片"); self.btn_still.clicked.connect(self.add_still)
        l.addWidget(self.btn_still)
        self.out_msg = QLabel(""); self.out_msg.setObjectName("sub"); self.out_msg.setWordWrap(True); l.addWidget(self.out_msg)
        l.addStretch()
        self._fill_presets()
        self._proj_changed(self.proj.currentIndex(), init=True)
        return w

    def _fill_presets(self):
        cur = cfg("preset")
        self.preset.blockSignals(True)
        self.preset.clear()
        for k, p in jobs.all_presets().items():
            self.preset.addItem(p["label"] + ("（自訂）" if p.get("custom") else ""), k)
        i = self.preset.findData(cur)
        self.preset.setCurrentIndex(max(0, i))
        self.preset.blockSignals(False)
        self._preset_changed()

    def _preset_changed(self, *_):
        k = self.preset.currentData()
        if k:
            set_cfg("preset", k)
            self.preset_desc.setText(jobs.all_presets()[k].get("desc", ""))
        self.update_out_path()

    def proj_kind(self) -> str:
        return ("equirect", "planet", "flat")[max(0, self.proj.currentIndex())]

    def active_view(self) -> dict:
        return self.view.flat if self.proj_kind() == "flat" else self.view.planet

    def _proj_changed(self, i, init=False):
        kind = ("equirect", "planet", "flat")[i]
        planet = kind in ("planet", "flat")
        set_cfg("projection", kind)
        self.planet_box.setVisible(planet)
        self.size.blockSignals(True)
        self.size.clear()
        if planet:
            sizes = jobs.PLANET_SIZES if kind == "planet" else jobs.FLAT_SIZES
            for s in sizes:
                w_, h_ = (int(x) for x in s.split("x"))
                tag = "直式" if h_ > w_ else ("方形" if h_ == w_ else "橫式")
                self.size.addItem(f"{w_}×{h_}（{tag}）", s)
            j = self.size.findData(cfg(kind + "_size"))
            self.size.setCurrentIndex(max(0, j))
            self._sync_frame_aspect()
            if kind == "flat":
                self.planet_spin["fov"].setRange(15, 150)
            else:
                self.planet_spin["fov"].setRange(90, 340)
            if not init:
                self.set_mode(2 if kind == "planet" else 4)
            self._view_changed()
            self.refresh_keys()
        else:
            self.size.addItem("自動（保留原生解析度）", 0)
            for wv in jobs.EQUI_SIZES:
                self.size.addItem(f"{wv}×{wv // 2}" + ("（放大）" if wv > 5760 else ""), wv)
        self.size.blockSignals(False)
        self.update_out_path()
        if hasattr(self, "spatial"):
            self._update_spatial()

    def _spatial_ok(self) -> bool:
        c = self.clip
        return bool(c and c.get("kind") == "video" and self.proj_kind() == "equirect"
                    and c.get("wavs") and len(c["wavs"]) == len(c.get("back") or []))

    def _update_spatial(self, *_):
        ok = self._spatial_ok() and self.with_audio.isChecked()
        self.spatial.blockSignals(True)
        self.spatial.setEnabled(ok)
        self.spatial.setChecked(ok and bool(cfg("spatial_audio")))
        self.spatial.blockSignals(False)

    def _size_changed(self, *_):
        kind = self.proj_kind()
        if kind in ("planet", "flat") and self.size.currentData():
            set_cfg(kind + "_size", self.size.currentData())
            self._sync_frame_aspect()

    def _sync_frame_aspect(self):
        s = self.size.currentData()
        if isinstance(s, str) and "x" in s:
            w_, h_ = (int(x) for x in s.split("x"))
            self.view.frame_aspect = w_ / h_
            self.view.update()

    def _planet_spin(self, key, v):
        if getattr(self, "_planet_lock", False):
            return
        self.active_view()[key] = v
        self.view.update()

    def _view_changed(self):
        if self.view.mode in (2, 4):
            self.dirty()
        self._planet_lock = True
        av = self.active_view()
        for k, sp in self.planet_spin.items():
            sp.setValue(av[k])
        self._planet_lock = False

    # ---------------- keyframes (reframe / animated planet)
    def cur_keys(self):
        kind = self.proj_kind()
        return self.paths.setdefault(kind, []) if kind in ("planet", "flat") else []

    def key_add(self):
        if not self.clip or self.proj_kind() == "equirect":
            return
        import camerapath
        t = self.player.frame / self.fps if self.clip["kind"] == "video" else 0.0
        keys = [k for k in self.cur_keys() if abs(k["t"] - t) > 1e-3]
        keys.append({"t": t, **{f: float(self.active_view()[f]) for f in camerapath.FIELDS}})
        self.paths[self.proj_kind()] = camerapath.clean(keys)
        self.refresh_keys(); self.dirty()

    def key_del(self):
        t = self.player.frame / self.fps
        tol = 0.6 / self.fps
        before = len(self.cur_keys())
        self.paths[self.proj_kind()] = [k for k in self.cur_keys() if abs(k["t"] - t) > tol]
        if len(self.cur_keys()) == before and self.cur_keys():
            # nothing exactly here: remove the nearest one
            near = min(self.cur_keys(), key=lambda k: abs(k["t"] - t))
            self.paths[self.proj_kind()] = [k for k in self.cur_keys() if k is not near]
        self.refresh_keys(); self.dirty()

    def key_clear(self):
        if self.cur_keys() and QMessageBox.question(self, "清除關鍵影格", f"清除全部 {len(self.cur_keys())} 個關鍵影格？") == QMessageBox.Yes:
            self.paths[self.proj_kind()] = []
            self.refresh_keys(); self.dirty()

    def key_jump(self, d):
        keys = self.cur_keys()
        if not keys:
            return
        t = self.player.frame / self.fps
        ts = [k["t"] for k in keys]
        nxt = [x for x in ts if x > t + 1e-3] if d > 0 else [x for x in ts if x < t - 1e-3]
        if nxt:
            self.player.seek(int(round((min(nxt) if d > 0 else max(nxt)) * self.fps)))

    def refresh_keys(self):
        keys = self.cur_keys()
        n = len(keys)
        self.key_lab.setText("固定視角（沒有關鍵影格）" if n == 0 else
                             f"{n} 個關鍵影格：" + "、".join(self._ts(k['t'] * self.fps) for k in keys[:8]) + ("…" if n > 8 else ""))
        pl = getattr(self, "player", None)
        dur = pl.total / self.fps if (pl and pl.total) else 0
        self.keystrip.set_keys([k["t"] for k in keys], dur)

    def apply_path(self, idx):
        """During playback / seeking the view follows the keyframed path."""
        keys = self.cur_keys()
        if not keys or self.view._drag:
            return
        import camerapath
        v = camerapath.at(keys, (idx + 0.5) / self.fps)
        if v:
            self.active_view().update(v)
            self._view_changed()

    def _browse_out(self):
        p, _ = QFileDialog.getSaveFileName(self, "輸出檔案", self.out_edit.text(), "MP4 (*.mp4);;JPEG (*.jpg)")
        if p:
            self.out_edit.setText(p.replace("/", "\\"))

    def update_out_path(self):
        self._update_spatial()
        if not self.clip:
            return
        ext = "jpg" if self.clip["kind"] == "photo" else "mp4"
        proj = self.proj_kind()
        name = output_name(self.clip, self.main.meta.get(self.clip["id"], {}), self.preset.currentData() or "youtube", proj, ext)
        sp = float(self.speed.currentData() or 1.0) if self.clip["kind"] == "video" else 1.0
        if sp != 1.0:
            stem, dot, e_ = name.rpartition(".")
            name = f"{stem}_{sp:g}x.{e_}"
        self.out_edit.setText(str(output_folder(self.main.folder) / name))

    def current_opts(self, **extra):
        kind = self.proj_kind()
        opts = {"preset": self.preset.currentData(), "engine": self.engine.currentData(),
                "params": json.loads(json.dumps(self.params)), "audio": self.with_audio.isChecked(),
                "spatial_audio": self.spatial.isEnabled() and self.spatial.isChecked(),
                "speed": float(self.speed.currentData() or 1.0) if self.clip["kind"] == "video" else 1.0,
                "start": (self.in_f or 0) / self.fps if self.clip["kind"] == "video" else 0,
                "end": (self.out_f / self.fps) if (self.out_f and self.clip["kind"] == "video") else 0,
                "output": self.out_edit.text().strip(),
                "grade": json.loads(json.dumps(self.grade)), "patch": dict(self.patch),
                "stab": dict(self.stab) if self.clip["kind"] == "video" else None,
                "seamflow_file": self.seam_file if self.seam_chk.isChecked() else None,
                "color_track": self.color_track if (self.track_chk.isChecked() and self.clip["kind"] == "video") else None,
                "auto_exposure": float(self.auto_expo.sp.value())}
        if kind in ("planet", "flat"):
            v = self.active_view()
            opts["projection"] = {"type": kind, **{k: float(x) for k, x in v.items()}}
            opts["size"] = self.size.currentData()
            opts["path"] = list(self.cur_keys()) if self.clip["kind"] == "video" else None
            set_cfg(kind, dict(v))
        else:
            opts["projection"] = {"type": "equirect"}
            opts["width"] = int(self.size.currentData() or 0)
        opts.update(extra)
        return opts

    def add_job(self):
        if not self.clip:
            return
        opts = self.current_opts()
        if self.main.submit(self.clip, opts):
            self.out_msg.setText(f"✓ 已加入佇列：{Path(opts['output']).name}")

    def add_still(self):
        if not self.clip:
            return
        if self.clip["kind"] == "photo":
            return self.add_job()
        t = self.player.frame / self.fps
        base = Path(self.out_edit.text()).with_suffix("")
        out = f"{base}_{t:.2f}s.jpg"
        if self.main.submit(self.clip, self.current_opts(still=t, output=out)):
            self.out_msg.setText(f"✓ 已加入：{Path(out).name}")

    # ---------------- clip loading / playback
    def refresh_clip_combo(self):
        self.clip_combo.blockSignals(True)
        self.clip_combo.clear()
        for cid, c in self.main.clips.items():
            self.clip_combo.addItem(c["name"], cid)
        if self.clip:
            self.clip_combo.setCurrentIndex(max(0, self.clip_combo.findData(self.clip["id"])))
        self.clip_combo.blockSignals(False)

    def step_clip(self, d):
        ids = list(self.main.clips)
        if not ids:
            return
        i = ids.index(self.clip["id"]) if self.clip and self.clip["id"] in ids else -1
        self.main.open_clip(ids[(i + d) % len(ids)])

    def load(self, clip):
        from imageio_util import imread
        if self.clip and self._save_timer.isActive():
            self._save_timer.stop()
            self.save_project()
        proj = {}
        self.player.stop()
        self.clip = clip
        self.in_f = self.out_f = None
        self.clip_combo.setCurrentIndex(max(0, self.clip_combo.findData(clip["id"])))
        try:
            if clip["kind"] == "photo":
                fimg, bimg = imread(clip["front"][0]), imread(clip["back"][0])
                self.src_h, self.src_w = fimg.shape[:2]
                self.fps = 30.0
                self.view.full_range = True
            else:
                tl = media.timeline(clip["front"])
                self.src_w, self.src_h, self.fps = tl.width, tl.height, tl.fps
                self.view.full_range = tl.color_range == "pc"
            self.defaults = geo.default_params(self.src_w, self.src_h)
            cal = (cfg("calibration") or {}).get(f"{self.src_w}x{self.src_h}")
            self.params = geo.merge_params(self.defaults, cal) if cal else json.loads(json.dumps(self.defaults))
            self._loading = True
            proj = projects.get(self.main.folder, clip["id"])
            if proj.get("params"):
                self.params = geo.merge_params(self.defaults, proj["params"])
            self.grade = color.merge(proj.get("grade"))
            self.stab = {**gyro.default_stab(), **(proj.get("stab") or {})}
            self.seam_file = proj.get("seamflow_file")
            self.seam_chk.blockSignals(True)
            self.seam_chk.setChecked(bool(proj.get("seamflow_on") and self.seam_file))
            self.seam_chk.blockSignals(False)
            self.seam_msg.setText("已有分析結果" if self.seam_file else "尚未分析")
            self.btn_seam.setEnabled(clip["kind"] == "video")
            self.apply_seam()
            self.color_track = proj.get("color_track")
            self.track_chk.blockSignals(True)
            self.track_chk.setChecked(bool(proj.get("color_track_on") and self.color_track))
            self.track_chk.blockSignals(False)
            self.track_smooth.set_value(float(proj.get("track_smooth", 6.0)))
            self.auto_expo.blockSignals(True)
            self.auto_expo.set_value(float(proj.get("auto_exposure", 0.0)))
            self.auto_expo.blockSignals(False)
            ti = self.track_interval.findData(float(proj.get("track_interval", 3.0)))
            self.track_interval.setCurrentIndex(ti if ti >= 0 else 2)
            self._show_track()
            self.track_chk.setEnabled(clip["kind"] == "video"); self.btn_track.setEnabled(clip["kind"] == "video")
            self.stab_S, self.stab_key = None, None
            self.patch.update(proj.get("patch") or {"mode": "off"})
            self.in_f, self.out_f = proj.get("in"), proj.get("out")
            self._build_rows()
            self.refresh_color()
            self.refresh_stab(ui=True)
            self.view.set_params(self.params)
            if clip["kind"] == "photo":
                self.player.set_photo(fimg, bimg)
                self.slider.setRange(0, 0)
            else:
                self.player.set_clip(clip, "full" if self.quality.currentIndex() == 1 else "half")
                self.slider.setRange(0, max(0, self.player.total - 1))
            self.film.setVisible(clip["kind"] == "video")
            if clip["kind"] == "video":
                self.film.pix = []
                self.film.msg = "產生縮圖中…"
                self.film.update()
                bus = self.main.bus

                def thumbs_work(c=clip):
                    import thumbs
                    try:
                        arr, _ = thumbs.strip(c)
                        bus.analysisDone.emit("thumbs", (c["id"], arr))
                    except Exception as e:
                        bus.analysisDone.emit("thumbs", (c["id"], e))
                threading.Thread(target=thumbs_work, daemon=True).start()
            self.status.setText(f"{clip['name']} · {self.src_w}×{self.src_h}" +
                                (f" · {self.fps:.2f} fps · {len(clip['front'])} 個章節" if clip["kind"] == "video" else " · 照片"))
        except Exception as e:
            self.status.setText(f"⚠ 無法開啟：{e}")
            core.log(traceback.format_exc())
        self._loading = True
        self.view.planet.update(proj.get("planet") or cfg("planet") or {})
        self.view.flat.update(proj.get("flat") or cfg("flat") or {})
        self.paths = {"planet": list((proj.get("paths") or {}).get("planet") or []),
                      "flat": list((proj.get("paths") or {}).get("flat") or [])}
        if proj.get("projection") in ("planet", "equirect", "flat"):
            self.proj.setCurrentIndex({"planet": 1, "flat": 2}.get(proj["projection"], 0))
        self.refresh_keys()
        si = self.speed.findData(float(proj.get("speed", 1.0)))
        self.speed.blockSignals(True); self.speed.setCurrentIndex(si if si >= 0 else 2); self.speed.blockSignals(False)
        self._view_changed()
        self._loading = False
        self.btn_still.setText("目前畫面存成照片" if clip["kind"] == "video" else "輸出這張照片")
        self.update_range()
        self.update_out_path()

    def set_mode(self, m):
        self.view.mode = m
        b = self.mode_group.button(m)
        if b:
            b.setChecked(True)
        self.view.update()

    def _seam(self, on):
        self.view.seam = on
        self.view.update()

    def _quality(self, i):
        set_cfg("preview_quality", "full" if i == 1 else "half")
        if self.clip and self.clip["kind"] == "video":
            f = self.player.frame
            self.player.set_clip(self.clip, "full" if i == 1 else "half")
            self.player.seek(f)

    def omega_now(self, idx):
        """(omega, readout s) for the rolling-shutter correction of frame idx, or None."""
        rs = float(self.stab.get("rs_ms") or 0)
        if rs <= 0 or not self.clip or self.clip["kind"] != "video":
            return None
        key = (self.clip["id"], round(float(self.stab.get("offset_ms", 0))))
        om = getattr(self, "_omega", None)
        if om is None or om[0] != key:
            if getattr(self, "_omega_busy", None) != key:
                self._omega_busy = key
                clip, fps, off = self.clip, self.fps, float(self.stab.get("offset_ms", 0))

                def work():
                    try:
                        n = int(media.timeline(clip["front"]).total_frames)
                        self._omega = (key, gyro.frame_omega(clip["front"], fps, 0, n, off))
                    except Exception as e:
                        core.log(f"rolling shutter omega failed: {e}")
                        self._omega = (key, None)
                threading.Thread(target=work, daemon=True).start()
            return None
        if om[1] is None or not len(om[1]):
            return None
        return om[1][min(idx, len(om[1]) - 1)], rs / 1000.0

    def _frame(self, fb, bb, w, h, idx):
        S = self.stab_S
        self.view.rs = self.omega_now(idx)
        self.view.stab = S[min(idx, len(S) - 1)] if S is not None and len(S) and self.stab["mode"] != "off" else None
        self.view.lens_color = self.track_now(idx) if self.clip and self.clip["kind"] == "video" else None
        self.view.lens_tone = self.tone_now(idx)
        self.view.expo_add = self.expo_now(idx) if self.clip and self.clip["kind"] == "video" else 0.0
        if self.clip and self.clip["kind"] == "video" and self.proj_kind() != "equirect":
            self.apply_path(idx)
        self.view.set_frames(fb, bb, w, h)

    def _state(self):
        p = self.player
        self.play_btn.setText("⏸" if p.playing else "▶")
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True); self.slider.setValue(p.frame); self.slider.blockSignals(False)
        self.film.set_pos(p.frame / max(1, p.total - 1))
        self.tc.setText(f"{self._ts(p.frame)} / {self._ts(p.total)}")
        if p.playing and self.out_f is not None and p.frame >= self.out_f:
            p.pause()

    def _ts(self, f):
        t = f / (self.fps or 30)
        return f"{int(t // 60):02d}:{t % 60:05.2f}"

    def toggle_play(self):
        if self.clip and self.clip["kind"] == "video":
            if not self.player.playing and self.out_f is not None and self.player.frame >= self.out_f:
                self.player.seek(self.in_f or 0)
            self.player.toggle()

    def step(self, n):
        if self.clip and self.clip["kind"] == "video":
            self.player.pause()
            self.player.seek(self.player.frame + n)

    def set_in(self):
        self.in_f = self.player.frame
        if self.out_f is not None and self.out_f <= self.in_f:
            self.out_f = None
        self.update_range()

    def set_out(self):
        self.out_f = self.player.frame
        if self.in_f is not None and self.in_f >= self.out_f:
            self.in_f = None
        self.update_range()

    def clear_io(self):
        self.in_f = self.out_f = None
        self.update_range()

    def update_range(self):
        self.dirty()
        if not self.clip or self.clip["kind"] != "video":
            self.range_lab.setText("照片")
            return
        a, b = self.in_f or 0, self.out_f if self.out_f is not None else self.player.total
        txt = f"{self._ts(a)} → {self._ts(b)}（{fmt_dur((b - a) / self.fps)}）"
        if self.in_f is None and self.out_f is None:
            txt += "　整段；用 [ 入點 / 出點 ]（I / O 鍵）可只輸出一部分"
        self.range_lab.setText(txt)


# ======================================================================== queue page
class QueuePage(QWidget):
    COLS = ["#", "素材", "狀態", "進度", "速度", "剩餘", "輸出"]
    STATUS = {"queued": "排隊中", "running": "轉檔中", "paused": "已暫停", "done": "完成", "error": "失敗", "cancelled": "已取消"}

    def __init__(self, main):
        super().__init__()
        self.main = main
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        t = QLabel("轉檔佇列"); t.setObjectName("title"); top.addWidget(t)
        self.summary = QLabel(""); self.summary.setObjectName("sub"); top.addWidget(self.summary, 1)
        top.addWidget(QLabel("全部轉完後"))
        self.after = QComboBox()
        for k, txt in (("none", "不做任何事"), ("sleep", "睡眠"), ("shutdown", "關機（60 秒後，可取消）")):
            self.after.addItem(txt, k)
        self.after.setCurrentIndex(max(0, self.after.findData(cfg("after_queue"))))
        self.after.currentIndexChanged.connect(lambda: set_cfg("after_queue", self.after.currentData()))
        top.addWidget(self.after)
        l.addLayout(top)
        # ⚙ 設定（中繼、成品、速度、握手、改名）收成一區，預設收合
        self.set_btn = QPushButton(); self.set_btn.setCheckable(True); self.set_btn.setObjectName("setfold")
        self.set_btn.setStyleSheet("QPushButton#setfold{text-align:left;padding:6px 10px;border:1px solid #252c35;border-radius:8px;background:#121821}"
                                   "QPushButton#setfold:checked{background:#16222e}")
        l.addWidget(self.set_btn)
        self.qset = QWidget(); outer = l; l = QVBoxLayout(self.qset); l.setContentsMargins(4, 2, 0, 4)
        # where the footage is converted and where the result goes (can be changed any time)
        route = QHBoxLayout()
        route.addWidget(QLabel("中繼 SSD"))
        self.cache_edit = QLineEdit(cfg("cache_dir") or "")
        self.cache_edit.setPlaceholderText("不使用（直接讀素材、在成品資料夾合併）")
        self.cache_edit.setToolTip("素材先整支複製到這個資料夾（建議 SSD），在這裡轉檔、合併，完成後背景搬到成品資料夾。\n"
                                   "素材在傳統硬碟時可以避免硬碟同時讀寫而拖慢顯示卡。用完的暫存會自動刪除。")
        self.cache_edit.editingFinished.connect(lambda: set_cfg("cache_dir", self.cache_edit.text().strip()))
        route.addWidget(self.cache_edit, 1)
        b = QPushButton("選擇…"); b.clicked.connect(lambda: self._pick_dir(self.cache_edit, "cache")); route.addWidget(b)
        route.addSpacing(14)
        route.addWidget(QLabel("成品送到"))
        self.dest_edit = QLineEdit(cfg("out_folder") if cfg("out_folder_mode") == "custom" else "")
        self.dest_edit.setPlaceholderText("素材資料夾裡的 stitched 子資料夾")
        self.dest_edit.setToolTip("轉完的影片放在哪裡；改了之後，還沒轉完的工作也會送到新位置（同 設定 → 輸出位置）")
        self.dest_edit.editingFinished.connect(lambda: self._set_dest(self.dest_edit.text().strip()))
        route.addWidget(self.dest_edit, 1)
        b = QPushButton("選擇…"); b.clicked.connect(lambda: self._pick_dir(self.dest_edit, "dest")); route.addWidget(b)
        l.addLayout(route)
        self.route_lab = QLabel(""); self.route_lab.setObjectName("sub")
        l.addWidget(self.route_lab)
        # ⚡ 轉檔速度（降速）
        pr = QHBoxLayout()
        lab = QLabel("⚡ 轉檔速度"); pr.addWidget(lab)
        self.pace_mode = QComboBox()
        for k_, t_ in (("full", "🚀 全速"), ("smart", "🧠 智慧降速（有人在用電腦時才降）"), ("manual", "🐢 固定降速")):
            self.pace_mode.addItem(t_, k_)
        i_ = self.pace_mode.findData(str(cfg("pace_mode") or "full"))
        self.pace_mode.setCurrentIndex(max(0, i_))
        self.pace_mode.currentIndexChanged.connect(lambda _i: self._pace_set(mode=self.pace_mode.currentData()))
        pr.addWidget(self.pace_mode)
        self.pace_slider = QSlider(Qt.Horizontal); self.pace_slider.setRange(2, 20)   # ×5 %
        self.pace_slider.setValue(max(2, min(20, int(cfg("pace_pct") or 60) // 5)))
        self.pace_slider.setFixedWidth(220)
        self.pace_slider.valueChanged.connect(lambda v: self.pace_val.setText(f"{v * 5}%"))
        self.pace_slider.sliderReleased.connect(lambda: self._pace_set(pct=self.pace_slider.value() * 5))
        pr.addWidget(self.pace_slider)
        self.pace_val = QLabel(f"{self.pace_slider.value() * 5}%"); self.pace_val.setFixedWidth(44); pr.addWidget(self.pace_val)
        self.pace_now = QLabel(""); self.pace_now.setObjectName("sub"); pr.addWidget(self.pace_now)
        pr.addStretch(1)
        l.addLayout(pr)
        # 🤝 跟 SortCut 握手（分配拉桿兩邊共用）
        hr = QHBoxLayout()
        self.hs_on = QCheckBox("🤝 握手（SortCut、ComfyRemote）"); self.hs_on.setChecked(cfg("hs_on") is not False)
        self.hs_on.setToolTip("同一台電腦上的 SortCut 也在轉檔時，兩邊照下面的比例分電腦的效能；對方閒著時這邊全速")
        self.hs_on.toggled.connect(lambda v: self._hs_act(on=bool(v))); hr.addWidget(self.hs_on)
        hr.addWidget(QLabel("分配"))
        self.hs_share = QSlider(Qt.Horizontal); self.hs_share.setRange(1, 9); self.hs_share.setFixedWidth(160)
        self.hs_share.setToolTip("兩邊都在轉檔時：FusionStitch 拿幾成（拉到 4 ＝ 40 : 60）。SortCut 那邊拉也一樣，最後拉的為準")
        self.hs_share.valueChanged.connect(lambda v: self.hs_share_lab.setText(f"FusionStitch {v * 10} : {100 - v * 10} {self._hs_other}"))
        self.hs_share.sliderReleased.connect(lambda: self._hs_act(share=self.hs_share.value() * 10))
        hr.addWidget(self.hs_share)
        self._hs_other = "SortCut"
        self.hs_share_lab = QLabel(""); self.hs_share_lab.setFixedWidth(180); hr.addWidget(self.hs_share_lab)
        self.hs_res_on = QCheckBox("留給你"); self.hs_res_on.setToolTip("你在用電腦（2 分鐘內動過鍵盤滑鼠）時，大家合起來只用剩下的（預設留 10%：FusionStitch、SortCut 各 45%）；沒人用 → 全力跑")
        self.hs_res_on.toggled.connect(lambda v: self._hs_act(reserve_on=bool(v))); hr.addWidget(self.hs_res_on)
        self.hs_res = QSpinBox(); self.hs_res.setRange(0, 80); self.hs_res.setSingleStep(10); self.hs_res.setSuffix("%")
        self.hs_res.editingFinished.connect(lambda: self._hs_act(reserve=self.hs_res.value())); hr.addWidget(self.hs_res)
        hr.addWidget(QLabel("優先"))
        self.hs_prio = QSpinBox(); self.hs_prio.setRange(0, 10); self.hs_prio.setToolTip("比對方大 → 這邊先跑、對方讓路（預設 5 ＝ 一樣，照分配）")
        self.hs_prio.editingFinished.connect(lambda: self._hs_act(prio=self.hs_prio.value())); hr.addWidget(self.hs_prio)
        self.hs_boost = QPushButton("⏩ 這邊先跑 1 小時"); self.hs_boost.clicked.connect(lambda: self._hs_act(boost=0 if self._hs_boosting else 1))
        hr.addWidget(self.hs_boost)
        self._hs_boosting = False
        from PySide6.QtWidgets import QToolButton, QMenu
        self.hs_mem = QToolButton(); self.hs_mem.setText("✅ 參與"); self.hs_mem.setPopupMode(QToolButton.InstantPopup)
        self.hs_mem.setToolTip("這台電腦有哪些程式參與分配（沒在這台跑的不要勾）")
        self.hs_mem_menu = QMenu(self.hs_mem); self.hs_mem.setMenu(self.hs_mem_menu)
        self.hs_mem_menu.aboutToShow.connect(self._hs_mem_fill); hr.addWidget(self.hs_mem)
        hr.addStretch(1)
        l.addLayout(hr)
        self.hs_now = QLabel(""); self.hs_now.setObjectName("sub"); self.hs_now.setWordWrap(True); l.addWidget(self.hs_now)
        self.df_on = QCheckBox("✅ 原檔資料夾轉完自動改名（20261121 → 20261121-FS DONE）")
        self.df_on.setToolTip("日期資料夾裡的影片全部轉好、沒有失敗才改；只改最外層的日期資料夾，100GBACK／100GFRNT 和影片檔名都不動，不刪任何檔案")
        self.df_on.setChecked(cfg("src_done_rename") is not False)
        self.df_on.toggled.connect(lambda v: set_cfg("src_done_rename", bool(v)))
        l.addWidget(self.df_on)
        outer.addWidget(self.qset); l = outer
        self.set_btn.setChecked(bool(cfg("q_set_open")))
        self.qset.setVisible(self.set_btn.isChecked())

        def _fold(v):
            self.qset.setVisible(v); set_cfg("q_set_open", bool(v)); self._set_txt()
        self.set_btn.toggled.connect(_fold)
        self._hs_busy = False
        self._pace_ui()
        try:
            import batch_panel
            self.batch_panel = batch_panel.BatchPanel(self)
            l.addWidget(self.batch_panel)
        except Exception as e:
            core.log(f"batch panel: {e}")
            self.batch_panel = None
        bar = QHBoxLayout()
        for txt, fn in (("⏸ 暫停", "pause"), ("▶ 繼續", "resume"), ("✕ 取消", "cancel"), ("↑ 上移", "up"),
                        ("↓ 下移", "down"), ("⤒ 置頂", "top"), ("↻ 失敗全部重試", "retry"),
                        ("開啟資料夾", "open"), ("清除已完成", "clear")):
            b = QPushButton(txt); b.clicked.connect(lambda _, f=fn: self.action(f)); bar.addWidget(b)
        bar.addStretch()
        l.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # queue order = conversion order, so no sorting here (use 上移／下移)
        self.kit = TableKit(self.table, "queue", self.COLS, {0: 40, 1: 150, 2: 80, 3: 220, 4: 90, 5: 90},
                            {0: "center", 2: "center", 4: "right_pad", 5: "right_pad"}, sortable=False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.cellDoubleClicked.connect(lambda r, c: self.action("open"))
        l.addWidget(self.table, 1)
        self.detail = QLabel(""); self.detail.setObjectName("sub"); self.detail.setWordWrap(True)
        l.addWidget(self.detail)
        self.table.itemSelectionChanged.connect(self.refresh)
        self._ids = []

    def _pace_set(self, mode=None, pct=None):
        if mode:
            set_cfg("pace_mode", mode)
        if pct:
            set_cfg("pace_pct", int(pct))
        try:
            import pace
            pace._cache["t"] = 0
            core.log(f"pace: {pace.label()}")
        except Exception:
            pass
        self._pace_ui()

    def _set_txt(self):
        """⚙ 設定 收合時的一行摘要."""
        try:
            relay = (self.cache_edit.text().strip() or "不使用")
            hs = f"握手 Lv{self.hs_now.text().split('Lv')[1].split(' ')[0]}" if "Lv" in self.hs_now.text() else ("握手 開" if self.hs_on.isChecked() else "握手 關")
            txt = (f"{'▾' if self.set_btn.isChecked() else '▸'} ⚙ 設定　中繼 {relay} · 速度 {self.pace_mode.currentText()} · {hs}"
                   f" · 改名 {'開' if self.df_on.isChecked() else '關'}")
            if self.set_btn.text() != txt:
                self.set_btn.setText(txt)
        except Exception:
            pass

    def _hs_act(self, **kw):
        if getattr(self, "_hs_busy", False):
            return
        try:
            import hsbridge
            r = hsbridge.act(kw)
            if hasattr(self.main, "toast"):
                self.main.toast(r.get("message", ""))
        except Exception as e:
            core.log(f"handshake ui: {e}")
        self._pace_ui()

    def _hs_mem_fill(self):
        try:
            import hsbridge
            st = hsbridge.status()
        except Exception:
            return
        m = self.hs_mem_menu; m.clear()
        mem = st.get("members")
        for a in st.get("apps") or []:
            act = m.addAction(a); act.setCheckable(True); act.setChecked(mem is None or a in mem)

            def tog(_c, a=a):
                cur = [x.text() for x in m.actions() if x.isCheckable() and x.isChecked()]
                self._hs_act(members=cur)
            act.toggled.connect(tog)

    def _hs_ui(self):
        try:
            import hsbridge
            st = hsbridge.status()
        except Exception as e:
            self.hs_now.setText(f"握手：{e}")
            return
        self._hs_busy = True
        try:
            self._hs_other = st.get("other") or "SortCut"
            self.hs_on.setChecked(bool(st["on"]))
            if not self.hs_share.isSliderDown():
                self.hs_share.setValue(max(1, min(9, int(round((st.get("share") or 50) / 10)))))
            self.hs_share_lab.setText(f"FusionStitch {self.hs_share.value() * 10} : {100 - self.hs_share.value() * 10} {self._hs_other}")
            self.hs_res_on.setChecked(bool(st.get("reserve_on")))
            if not self.hs_res.hasFocus():
                self.hs_res.setValue(int(st.get("reserve") or 0))
            if not self.hs_prio.hasFocus():
                self.hs_prio.setValue(int(st.get("prio") or 5))
            left = st.get("boost_until", 0) - time.time()
            self._hs_boosting = left > 0
            self.hs_boost.setText(f"取消先跑（剩 {int(left // 60) + 1} 分）" if left > 0 else "⏩ 這邊先跑 1 小時")
            P = st.get("peers") or []
            who = "、".join(f"{p.get('name')} V{p.get('ver')}（{p.get('what') or '閒著'}）" for p in P) or "沒看到 SortCut"
            M = {"alone": "照常跑", "lead": "這邊先跑", "yield": "讓對方先", "split": "照分配"}
            txt = f"　Lv{st.get('lv')} · {who} · {M.get(st.get('mode'), st.get('mode'))}：{st.get('why', '')} · 規則第 {st.get('rules')} 版"
            self.hs_now.setText(txt if st["on"] else "　握手關著（兩邊各跑各的）")
            self.hs_now.setToolTip((st.get("err") or "") + ("\n" if st.get("err") else "") +
                                   f"這邊的規則第 {st.get('rules_own')} 版；用的是{'對方提供的較新版' if st.get('rules_from') == 'lib' else '自己的'}。"
                                   + (f"\n分配最後由 {st.get('policy_by')} 改" if st.get("policy_by") else ""))
            self.hs_now.setStyleSheet("color:#ffb347" if st.get("mode") in ("yield", "split") else "")
        finally:
            self._hs_busy = False

    def _pace_ui(self):
        try:
            import pace
            st = pace.state()
            on = st["mode"] != "full"
            self.pace_slider.setEnabled(on)
            self.pace_val.setEnabled(on)
            self.pace_now.setText("　現在：" + pace.label(st))
            self.pace_now.setStyleSheet("color:#ffb347" if st["level"] < 0.999 else "color:#45d483")
        except Exception:
            pass
        if hasattr(self, "hs_now"):
            self._hs_ui()
        if hasattr(self, "set_btn"):
            self._set_txt()

    def _left(self, j):
        try:
            import history as _h
            o = _h.overall(j)
            return o["left"] if o.get("left") is not None else j.eta
        except Exception:
            return j.eta

    def selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._ids[r] for r in rows if r < len(self._ids)]

    def action(self, what):
        q = job_queue()
        ids = self.selected()
        if what == "clear":
            q.clear_finished()
        if what == "retry":
            n = 0
            for j in list(q.jobs):
                if j.status == "error":
                    q.resume(j.id)
                    n += 1
            self.main.toast(f"重新開始 {n} 個失敗的工作（從中斷處繼續）" if n else "沒有失敗的工作")
            ids = []
        if what == "top":
            for jid in reversed(ids):
                q.move(jid, -10000)
            ids = []
        for jid in ids:
            j = q.get(jid)
            if not j:
                continue
            if what == "pause":
                q.pause(jid)
            elif what == "resume":
                q.resume(jid)
            elif what == "cancel":
                if j.status in ("running", "queued", "paused") and QMessageBox.question(
                        self, "取消", f"取消「{j.title}」？已轉好的部分會刪除。") != QMessageBox.Yes:
                    continue
                q.cancel(jid)
            elif what in ("up", "down"):
                q.move(jid, -1 if what == "up" else 1)
            elif what == "open":
                p = Path(j.output)
                target = p if p.exists() else p.parent
                if target.exists():
                    subprocess.Popen(["explorer", "/select,", str(target)] if p.exists() else ["explorer", str(target)])
        self.refresh()

    def _pick_dir(self, edit, what):
        import folders
        d = folders.pick_dir(self, "選擇中繼資料夾（SSD）" if what == "cache" else "選擇成品資料夾",
                             "cache" if what == "cache" else "output", edit.text() or "")
        if not d:
            return
        edit.setText(d)
        if what == "cache":
            set_cfg("cache_dir", d)
            try:
                import staging
                staging.start()
            except Exception:
                pass
        else:
            self._set_dest(d)

    def _set_dest(self, d):
        if d:
            set_cfg("out_folder", d)
            set_cfg("out_folder_mode", "custom")
        else:
            set_cfg("out_folder_mode", "beside")
        self.main._out_cache = (0, None, [])

    def refresh(self):
        q = job_queue()
        js = list(q.jobs)
        ids = [j.id for j in js]
        if ids != self._ids:
            sel = set(self.selected())
            self._ids = ids
            self.table.setRowCount(len(js))
            for r in range(len(js)):
                for c in range(len(self.COLS)):
                    if c == 3:
                        pb = QProgressBar(); pb.setRange(0, 1000); self.table.setCellWidget(r, c, pb)
                    else:
                        self.table.setItem(r, c, QTableWidgetItem(""))
            self.table.clearSelection()
            for r, jid in enumerate(ids):
                if jid in sel:
                    self.table.selectRow(r)
        for r, j in enumerate(js):
            stxt = self.STATUS.get(j.status, j.status)
            nb = getattr(j, "not_before", 0) or 0
            if j.status == "queued" and nb > time.time():
                stxt = f"⚠ {int(nb - time.time())} 秒後重試"
            elif j.status == "queued" and getattr(j, "retries", 0):
                stxt = "重試中"
            vals = [str(j.id), j.title, stxt, None,
                    f"{j.fps:.1f} fps" if j.status == "running" else "",
                    fmt_dur(self._left(j)) if j.status == "running" else "", j.output]
            for c, v in enumerate(vals):
                if v is None:
                    pb = self.table.cellWidget(r, 3)
                    try:                                   # 整體進度: preparation + conversion + finishing
                        import history as _h
                        ov = _h.overall(j)
                    except Exception:
                        ov = {"pct": 1.0 if j.status == "done" else j.progress, "phase": ""}
                    pv = 1.0 if j.status == "done" else ov["pct"]
                    pb.setValue(int(pv * 1000))
                    ph = {"copy": "　等複製", "prep": "　準備", "encode": "　轉檔", "finish": "　收尾"}.get(ov.get("phase"), "")
                    pb.setFormat(f"{pv * 100:.1f}%{ph}")
                    continue
                it = self.table.item(r, c)
                if it.text() != v:
                    it.setText(v)
                if c == 2:
                    it.setToolTip(j.message or "")
                    if v.startswith("⚠"):
                        it.setForeground(TC("warn"))
                        continue
                    it.setForeground(TC("ok") if j.status == "done" else TC("err") if j.status == "error"
                                     else TC("accent") if j.status == "running" else TC("warn") if j.status == "paused" else TC("text"))
        if hasattr(self, "pace_now"):
            self._pace_ui()
        if getattr(self, "batch_panel", None) is not None:
            try:
                hide = self.batch_panel.apply()
                for r, j in enumerate(js):
                    self.table.setRowHidden(r, j.id in hide)
            except Exception as e:
                core.log(f"batch panel refresh: {e}")
        n_act = sum(1 for j in js if j.status in ("queued", "running"))
        run = q.running()
        waiting = getattr(q, "waiting_schedule", False) and not run
        retry = next((x for x in js if x.status == "queued" and (getattr(x, "not_before", 0) or 0) > time.time()), None)
        self.summary.setText((f"轉檔中：{run.title} · {run.engine_desc} · {run.detail}" if run else
                              ("等待轉檔時段（設定 → 轉檔時段）" if waiting else
                               f"#{retry.id} 等待自動重試：{retry.message[:80]}" if retry else "閒置"))
                             + (f" · 還有 {n_act - (1 if run else 0)} 個排隊" if n_act > 1 else ""))
        sel = self.selected()
        j = q.get(sel[0]) if sel else run
        self.detail.setText(f"#{j.id} {j.title}：{j.message}" if j else "")
        try:
            import staging
            st = staging.status_text() if cfg("cache_dir") else ""
        except Exception:
            st = ""
        self.route_lab.setText(st)
        self.route_lab.setVisible(bool(st))


# ======================================================================== perf page
class PerfPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        t = QLabel("電腦效能"); t.setObjectName("title"); top.addWidget(t)
        self.gpu_name = QLabel(""); self.gpu_name.setObjectName("sub"); top.addWidget(self.gpu_name, 1)
        self.float_btn = QPushButton("效能浮窗"); self.float_btn.setCheckable(True)
        self.float_btn.toggled.connect(self.main.set_float)
        top.addWidget(self.float_btn)
        l.addLayout(top)
        grid = QGridLayout(); grid.setSpacing(8)
        self.cards = {}
        spec = [("cpu", "CPU", "#4dc9ff", 100), ("ram", "記憶體", "#b48cff", 100), ("gpu", "GPU 3D（拼接）", "#45d483", 100),
                ("enc", "NVENC（編碼）", "#f0ad4e", 100), ("dec", "NVDEC（解碼）", "#ff7eb6", 100),
                ("temp", "GPU 溫度", "#ff6577", 100), ("power", "GPU 功耗", "#ffd166", None), ("vram", "顯示記憶體", "#7ee081", 100),
                ("fps", "轉檔速度", "#4dc9ff", None)]
        for i, (k, title, color, vmax) in enumerate(spec):
            c = card(title)
            sp = Sparkline(color, vmax); sp.setMinimumHeight(46)
            c.layout().addWidget(sp)
            c.spark = sp
            grid.addWidget(c, i // 3, i % 3)
            self.cards[k] = c
        l.addLayout(grid)
        dl = QLabel("硬碟"); dl.setObjectName("section_title"); l.addWidget(dl)
        self.disk_grid = QGridLayout(); self.disk_grid.setSpacing(8)
        l.addLayout(self.disk_grid)
        self.disk_cards = {}
        self.note = QLabel("提示：轉檔時 NVENC 接近 100% 代表 GPU 編碼已滿載；素材硬碟讀取若持續很高（HDD 約 150–250 MB/s），瓶頸可能在硬碟。")
        self.note.setObjectName("sub"); self.note.setWordWrap(True)
        l.addWidget(self.note)
        l.addStretch()

    _dinfo: dict = {}
    _dinfo_t = 0.0

    def refresh(self, data, hist):
        g = data.get("gpu") or {}
        s = perf_sampler()
        self.gpu_name.setText(s.gpu_name)
        self.float_btn.blockSignals(True); self.float_btn.setChecked(self.main.float_visible()); self.float_btn.blockSignals(False)
        def put(k, text, values):
            c = self.cards[k]
            c.value_label.setText(text)
            c.spark.set_values(values)
        put("cpu", f"{data.get('cpu', 0):.0f}%  ({data.get('cores', 0)} 執行緒)", hist.get("cpu", []))
        put("ram", f"{fmt_bytes(data.get('ram_used', 0))} / {fmt_bytes(data.get('ram_total', 0))}", hist.get("ram", []))
        put("gpu", f"{g.get('gpu', 0) or 0:.0f}%" if g else "—", hist.get("gpu", []))
        put("enc", f"{g.get('enc', 0) or 0:.0f}%" if g else "—", hist.get("enc", []))
        put("dec", f"{g.get('dec', 0) or 0:.0f}%" if g else "—", hist.get("dec", []))
        put("temp", f"{g.get('temp', 0) or 0:.0f} °C  風扇 {g.get('fan', 0) or 0:.0f}%" if g else "—", hist.get("temp", []))
        put("power", f"{g.get('power', 0) or 0:.0f} / {g.get('power_limit', 0) or 0:.0f} W" if g else "—", [])
        put("vram", f"{(g.get('mem_used') or 0) / 1024:.1f} / {(g.get('mem_total') or 0) / 1024:.1f} GB" if g else "—", [])
        run = job_queue().running()
        put("fps", f"{run.fps:.1f} fps" if run else "閒置", hist.get("fps", []))
        for letter, d in (data.get("disks") or {}).items():
            c = self.disk_cards.get(letter)
            if c is None:
                c = card(f"磁碟 {letter}:")
                sp = Sparkline("#4dc9ff", None); sp.setMinimumHeight(36); c.layout().addWidget(sp); c.spark = sp
                n = len(self.disk_cards)
                self.disk_grid.addWidget(c, n // 4, n % 4)
                self.disk_cards[letter] = c
            c.value_label.setText(f"讀 {d['read'] / 1048576:.0f}  寫 {d['write'] / 1048576:.0f} MB/s")
            c.spark.set_values(hist.get("disk_" + letter, []))
            di = self._dinfo.get(letter)
            if di:
                head = " · ".join(x for x in (di.get("label") or "", di.get("kind") or "") if x)
                cap = f"剩 {fmt_bytes(di['free'])} / {fmt_bytes(di['total'])}" if di.get("total") else ""
                c.title_label.setText(f"磁碟 {letter}:　{head}　{cap}".rstrip())
        if time.time() - self._dinfo_t > 30 and data.get("disks"):     # 容量 30 秒查一次，在背景（第一次要問 Windows，會花幾秒）
            self._dinfo_t = time.time()
            letters = list(data["disks"].keys())

            def look():
                try:
                    import disks
                    self._dinfo = {L: disks.info(f"{L}:\\") for L in letters}
                except Exception:
                    pass
            threading.Thread(target=look, daemon=True, name="perf-disks").start()


# ======================================================================== settings page
class SettingsPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        t = QLabel("設定"); t.setObjectName("title"); outer.addWidget(t)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        body = QWidget(); l = QVBoxLayout(body); l.setContentsMargins(0, 0, 8, 0)
        sc.setWidget(body)
        outer.addWidget(sc, 1)

        g = QGroupBox("輸出")
        f = QFormLayout(g)
        self.out_mode = QComboBox(); self.out_mode.addItem("素材資料夾裡的 stitched 子資料夾", "beside"); self.out_mode.addItem("指定資料夾", "custom")
        self.out_mode.setCurrentIndex(max(0, self.out_mode.findData(cfg("out_folder_mode"))))
        self.out_mode.currentIndexChanged.connect(lambda: set_cfg("out_folder_mode", self.out_mode.currentData()))
        f.addRow("輸出位置", self.out_mode)
        h = QHBoxLayout(); self.out_dir = QLineEdit(cfg("out_folder") or ""); h.addWidget(self.out_dir, 1)
        b = QPushButton("…"); b.setFixedWidth(32); b.clicked.connect(self._pick_out); h.addWidget(b)
        self.out_dir.editingFinished.connect(lambda: set_cfg("out_folder", self.out_dir.text().strip()))
        f.addRow("指定資料夾", h)
        self.pattern = QLineEdit(cfg("name_pattern")); self.pattern.editingFinished.connect(lambda: set_cfg("name_pattern", self.pattern.text().strip() or core.DEFAULTS["name_pattern"]))
        f.addRow("檔名規則", self.pattern)
        hint = QLabel("可用：{date} 拍攝日期、{time} 拍攝時間、{num} 素材編號、{preset} 輸出預設。小星球會自動加 _planet。")
        hint.setObjectName("sub"); hint.setWordWrap(True); f.addRow("", hint)
        self.seg = QSpinBox(); self.seg.setRange(5, 600); self.seg.setSuffix(" 秒"); self.seg.setValue(int(cfg("segment_seconds")))
        self.seg.valueChanged.connect(lambda v: set_cfg("segment_seconds", v))
        f.addRow("斷點續轉分段", self.seg)
        sch = dict(cfg("schedule") or {"on": False, "start": "01:00", "end": "07:00"})
        sr = QHBoxLayout()
        self.sch_on = QCheckBox("只在這段時間轉檔"); self.sch_on.setChecked(bool(sch.get("on")))
        self.sch_a = QLineEdit(sch.get("start", "01:00")); self.sch_a.setFixedWidth(60)
        self.sch_b = QLineEdit(sch.get("end", "07:00")); self.sch_b.setFixedWidth(60)
        sr.addWidget(self.sch_on); sr.addWidget(self.sch_a); sr.addWidget(QLabel("～")); sr.addWidget(self.sch_b); sr.addStretch()

        def save_sch(*_):
            import re as _re
            a, b = self.sch_a.text().strip(), self.sch_b.text().strip()
            ok = all(_re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", x) for x in (a, b))
            if ok:
                set_cfg("schedule", {"on": self.sch_on.isChecked(), "start": a, "end": b})
        self.sch_on.toggled.connect(save_sch); self.sch_a.editingFinished.connect(save_sch); self.sch_b.editingFinished.connect(save_sch)
        f.addRow("轉檔時段", sr)
        hint2 = QLabel("例如 01:00～07:00：白天用電腦時不轉，晚上自動開始；超過時間會在下一段自動暫停、隔天從中斷處繼續。轉檔時電腦不會進入睡眠。")
        hint2.setObjectName("sub"); hint2.setWordWrap(True); f.addRow("", hint2)
        self.auto_resume = QCheckBox("開啟程式時自動繼續上次沒轉完的工作"); self.auto_resume.setChecked(bool(cfg("auto_resume", True)))
        self.auto_resume.toggled.connect(lambda v: set_cfg("auto_resume", v))
        f.addRow("", self.auto_resume)
        self.par_seg = QComboBox()
        for n_, t_ in ((1, "1 段（舊方式）"), (2, "2 段（預設）"), (3, "3 段（顯示卡、硬碟都夠快時）")):
            self.par_seg.addItem(t_, n_)
        self.par_seg.setCurrentIndex(max(0, min(2, int(cfg("parallel_segments") or 2) - 1)))
        self.par_seg.currentIndexChanged.connect(lambda _i: set_cfg("parallel_segments", int(self.par_seg.currentData())))
        self.par_seg.setToolTip("一支影片同時轉幾段：每段有自己的解碼、拼接、編碼，讓顯示卡和 CPU 跑得更滿。\n"
                                "素材在傳統硬碟時，3 段可能讓硬碟來回讀取反而變慢；從下一段開始生效。")
        f.addRow("同時轉檔段數", self.par_seg)
        self.par_jobs = QComboBox()
        for n_, t_ in ((1, "1 支（一支接一支）"), (2, "2 支（預設）"), (3, "3 支")):
            self.par_jobs.addItem(t_, n_)
        self.par_jobs.setCurrentIndex(max(0, min(2, int(cfg("parallel_jobs") or 2) - 1)))
        self.par_jobs.currentIndexChanged.connect(lambda _i: set_cfg("parallel_jobs", int(self.par_jobs.currentData())))
        self.par_jobs.setToolTip("錯開同時處理：一支在準備（對齊、分析）或收尾（合併、搬檔）時，下一支先開始，\n"
                                 "GPU 轉檔階段輪流用（見下一項），讓顯示卡一直有事做。")
        f.addRow("同時處理幾支影片", self.par_jobs)
        self.gpu_jobs = QComboBox()
        for n_, t_ in ((1, "1 支（預設，GPU 輪流用）"), (2, "2 支同時用 GPU")):
            self.gpu_jobs.addItem(t_, n_)
        self.gpu_jobs.setCurrentIndex(max(0, min(1, int(cfg("gpu_jobs") or 1) - 1)))
        self.gpu_jobs.currentIndexChanged.connect(lambda _i: set_cfg("gpu_jobs", int(self.gpu_jobs.currentData())))
        f.addRow("同時用 GPU 轉檔", self.gpu_jobs)
        l.addWidget(g)

        g = QGroupBox("視窗與預覽")
        f = QFormLayout(g)
        self.tray = QCheckBox("關閉視窗時縮到系統匣（轉檔繼續）"); self.tray.setChecked(bool(cfg("minimize_to_tray")))
        self.tray.toggled.connect(lambda v: set_cfg("minimize_to_tray", v)); f.addRow("", self.tray)
        self.pf = QCheckBox("啟動時顯示效能浮窗"); self.pf.setChecked(bool(cfg("perf_float_start")))
        self.pf.toggled.connect(lambda v: set_cfg("perf_float_start", v)); f.addRow("", self.pf)
        l.addWidget(g)

        g = QGroupBox("自訂輸出預設")
        v = QVBoxLayout(g)
        self.custom_list = QListWidget(); self.custom_list.setMaximumHeight(110); v.addWidget(self.custom_list)
        form = QFormLayout()
        self.c_name = QLineEdit(); self.c_name.setPlaceholderText("例如：YouTube 8K")
        self.c_base = QComboBox()
        for k, p in jobs.PRESETS.items():
            self.c_base.addItem(p["label"], k)
        self.c_cq = QSpinBox(); self.c_cq.setRange(8, 40); self.c_cq.setValue(18)
        self.c_width = QComboBox(); self.c_width.addItem("自動", 0)
        for wv in jobs.EQUI_SIZES:
            self.c_width.addItem(f"{wv}×{wv // 2}", wv)
        self.c_max = QSpinBox(); self.c_max.setRange(0, 800); self.c_max.setSuffix(" Mbps"); self.c_max.setSpecialValueText("不限制")
        form.addRow("名稱", self.c_name); form.addRow("以此為基礎", self.c_base)
        form.addRow("畫質 CQ（越小越好）", self.c_cq); form.addRow("解析度", self.c_width); form.addRow("位元率上限", self.c_max)
        v.addLayout(form)
        h = QHBoxLayout()
        b = QPushButton("新增／更新"); b.clicked.connect(self._save_custom); h.addWidget(b)
        b = QPushButton("刪除選取"); b.clicked.connect(self._del_custom); h.addWidget(b)
        h.addStretch(); v.addLayout(h)
        l.addWidget(g)

        g = QGroupBox("全自動處理（素材庫 → ✦ 全自動處理）")
        v = QVBoxLayout(g)
        n = QLabel("每段影片會自動：對齊接縫 → 防震＋水平（有陀螺儀時）→ 判斷行進方向 → 前後鏡頭色彩隨時間匹配，然後轉出下面勾選的格式。")
        n.setObjectName("sub"); n.setWordWrap(True); v.addWidget(n)
        import autoproc
        self.auto_checks = {}
        sel = set(cfg("auto_outputs") or ["equirect"])
        for k, (label, _, _) in autoproc.OUTPUTS.items():
            cb = QCheckBox(label); cb.setChecked(k in sel)
            cb.toggled.connect(self._auto_outputs)
            v.addWidget(cb); self.auto_checks[k] = cb
        self.auto_scan = QCheckBox("選擇素材資料夾時，自動處理還沒轉過也沒調整過的新素材")
        self.auto_scan.setChecked(bool(cfg("auto_on_scan", False)))
        self.auto_scan.toggled.connect(lambda on: set_cfg("auto_on_scan", bool(on)))
        v.addWidget(self.auto_scan)
        ae = QHBoxLayout()
        ae.addWidget(QLabel("自動亮度（隨時間）"))
        self.auto_expo_cfg = QComboBox()
        for lab_, val_ in (("關閉", 0.0), ("輕微 0.3", 0.3), ("溫和 0.5（建議）", 0.5), ("強 0.8", 0.8)):
            self.auto_expo_cfg.addItem(lab_, val_)
        i_ = self.auto_expo_cfg.findData(float(cfg("auto_exposure_strength", 0.5)))
        self.auto_expo_cfg.setCurrentIndex(i_ if i_ >= 0 else 2)
        self.auto_expo_cfg.currentIndexChanged.connect(
            lambda _i: set_cfg("auto_exposure_strength", float(self.auto_expo_cfg.currentData())))
        self.auto_expo_cfg.setToolTip("全自動處理時，隧道、樹蔭變暗會自動調亮，大太陽過亮會壓暗（之後可在預覽校正微調）")
        ae.addWidget(self.auto_expo_cfg); ae.addStretch()
        v.addLayout(ae)
        self.auto_tone_chk = QCheckBox("自動調色（對比、黑白位、自然飽和度；只套用在還沒手動調色的素材）")
        self.auto_tone_chk.setChecked(bool(cfg("auto_tone", True)))
        self.auto_tone_chk.toggled.connect(lambda on: set_cfg("auto_tone", bool(on)))
        v.addWidget(self.auto_tone_chk)
        self.auto_align_chk = QCheckBox("轉檔前自動對齊前後鏡頭（只套用在還沒對齊過、也沒存相機預設的素材；約 10–20 秒）")
        self.auto_align_chk.setChecked(bool(cfg("auto_align", True)))
        self.auto_align_chk.toggled.connect(lambda on: set_cfg("auto_align", bool(on)))
        v.addWidget(self.auto_align_chk)
        l.addWidget(g)

        # ---- 原廠色彩對標 + 預設正面
        g = QGroupBox("顏色與方向")
        f = QFormLayout(g)
        self.dview = QComboBox()
        self.dview.addItem("後鏡頭（相機反裝，後鏡頭朝前）", "back"); self.dview.addItem("前鏡頭", "front")
        self.dview.setCurrentIndex(max(0, self.dview.findData(cfg("default_view") or "back")))
        self.dview.currentIndexChanged.connect(lambda _i: set_cfg("default_view", self.dview.currentData()))
        self.dview.setToolTip("360 畫面一開始朝哪個鏡頭。全自動分析判斷出行進方向時以行進方向為準。")
        f.addRow("預設正面", self.dview)
        self.cmode = QComboBox()
        self.cmode.addItem("自動調色＋自動白平衡（每支素材各自分析）", "auto")
        self.cmode.addItem("原廠色彩（和 GoPro Fusion Studio 一樣的顏色）", "gopro")
        self.cmode.setCurrentIndex(max(0, self.cmode.findData(cfg("color_mode") or "auto")))
        self.cmode.currentIndexChanged.connect(lambda _i: set_cfg("color_mode", self.cmode.currentData()))
        f.addRow("轉檔顏色", self.cmode)
        self.cstr = QSpinBox(); self.cstr.setRange(0, 100); self.cstr.setSuffix(" %")
        self.cstr.setValue(int(round(float(cfg("gopro_lut_strength") or 1.0) * 100)))
        self.cstr.valueChanged.connect(lambda v: set_cfg("gopro_lut_strength", v / 100))
        f.addRow("原廠色彩強度", self.cstr)
        self.cdn = QSpinBox(); self.cdn.setRange(0, 100); self.cdn.setSuffix(" %")
        self.cdn.setValue(int(round(float(cfg("gopro_denoise") or 0.0) * 100)))
        self.cdn.valueChanged.connect(lambda v: set_cfg("gopro_denoise", v / 100))
        self.cdn.setToolTip("原廠色彩模式順便做的降噪（Fusion Studio 的畫面比較平滑；0＝不降噪）")
        f.addRow("原廠色彩降噪", self.cdn)
        self.csh = QSpinBox(); self.csh.setRange(0, 100); self.csh.setSuffix(" %")
        self.csh.setValue(int(round(float(cfg("gopro_sharpen", 0.7) or 0.0) * 100)))
        self.csh.valueChanged.connect(lambda v: set_cfg("gopro_sharpen", v / 100))
        self.csh.setToolTip("Fusion Studio 的畫面比較銳利；原廠色彩模式順便銳化（0＝不銳化）")
        f.addRow("原廠色彩銳化", self.csh)
        self.cwb = QSpinBox(); self.cwb.setRange(0, 100); self.cwb.setSuffix(" %")
        self.cwb.setValue(int(round(float(cfg("gopro_wb", 0.8) or 0.0) * 100)))
        self.cwb.valueChanged.connect(lambda v: set_cfg("gopro_wb", v / 100))
        self.cwb.setToolTip("相機的白平衡會隨光線飄（一下偏黃一下偏藍），Fusion Studio 會把它拉平；"
                            "轉檔前會先分析整段（約 10 秒）。0＝不修正")
        f.addRow("原廠白平衡（拉平色溫飄移）", self.cwb)
        self.cex = QCheckBox("白天亮的場景自動加亮，跟 Fusion Studio 一樣（依相機記錄的 ISO／快門）")
        self.cex.setChecked(bool(cfg("gopro_exposure", True)))
        self.cex.toggled.connect(lambda on: set_cfg("gopro_exposure", bool(on)))
        f.addRow("原廠亮度", self.cex)
        self.clx = QCheckBox("兩顆鏡頭各自的亮度，依相機記錄的曝光差調整（跟 Fusion Studio 一樣；前後鏡頭看起來才會一致）")
        self.clx.setChecked(bool(cfg("gopro_lens_exposure", True)))
        self.clx.toggled.connect(lambda on: set_cfg("gopro_lens_exposure", bool(on)))
        f.addRow("原廠鏡頭曝光", self.clx)
        rr = QHBoxLayout()
        self.cm_ref = QLineEdit(); self.cm_ref.setPlaceholderText("原廠轉出的 360 影片（例如 VIDEO_6545(1).mp4）")
        rr.addWidget(self.cm_ref, 1)
        b = QPushButton("選擇…"); b.clicked.connect(self._cm_pick); rr.addWidget(b)
        f.addRow("原廠成品", rr)
        self.cm_clip = QComboBox(); self.cm_clip.setMinimumWidth(160)
        f.addRow("同一段素材", self.cm_clip)
        rr2 = QHBoxLayout()
        self.cm_go = QPushButton("🎨 計算原廠色彩"); self.cm_go.setObjectName("blue"); self.cm_go.clicked.connect(self._cm_start)
        rr2.addWidget(self.cm_go)
        self.cm_msg = QLabel(""); self.cm_msg.setObjectName("sub"); self.cm_msg.setWordWrap(True); rr2.addWidget(self.cm_msg, 1)
        f.addRow("", rr2)
        self.cm_prev = QLabel(""); self.cm_prev.setVisible(False)
        f.addRow("", self.cm_prev)
        hint = QLabel("選一支用原廠 Fusion Studio 轉好的 360 影片（轉檔時不要開防震），和它的原始素材。程式從兩支各取 30 個畫面，"
                      "自動對齊方向後算出顏色對照表；之後選「原廠色彩」轉出來的顏色、對比、飽和度都會接近原廠。約 1～3 分鐘，只在這台電腦算。")
        hint.setObjectName("sub"); hint.setWordWrap(True); f.addRow("", hint)
        ib = QPushButton("🖼 更新 FusionStitch.exe 圖示"); ib.clicked.connect(self._exe_icon)
        ib.setToolTip("把檔案總管、捷徑、工作列看到的程式圖示換成新圖示（開程式時也會自動做一次）")
        f.addRow("程式圖示", ib)
        l.addWidget(g)
        self._cm_timer = QTimer(self); self._cm_timer.setInterval(700); self._cm_timer.timeout.connect(self._cm_poll)
        QTimer.singleShot(0, self._cm_fill)

        g = QGroupBox("手機網頁（同一個 Wi-Fi，或透過 Tailscale 在外面也能用手機看進度、暫停、上傳更新包）")
        f = QFormLayout(g)
        self.web_on = QCheckBox("開啟手機網頁"); self.web_on.setChecked(bool(cfg("web_enabled", False)))
        self.web_on.toggled.connect(self._web_toggle)
        f.addRow("", self.web_on)
        self.web_port = QSpinBox(); self.web_port.setRange(1024, 65535); self.web_port.setValue(int(cfg("web_port", 8361)))
        self.web_port.valueChanged.connect(lambda v: (set_cfg("web_port", v), self._web_toggle(self.web_on.isChecked())))
        f.addRow("連接埠", self.web_port)
        pr = QHBoxLayout()
        self.web_pin = QLineEdit(""); self.web_pin.setMaximumWidth(180)
        self.web_pin.setToolTip("自己設定 PIN：4～20 個字（數字、英文都可以），按 Enter 或點別的地方就儲存")
        self.web_pin.editingFinished.connect(self._web_set_pin); pr.addWidget(self.web_pin)
        b = QPushButton("隨機產生"); b.clicked.connect(self._web_new_pin); pr.addWidget(b)
        self.web_pin_msg = QLabel(""); self.web_pin_msg.setObjectName("sub"); pr.addWidget(self.web_pin_msg); pr.addStretch()
        f.addRow("PIN", pr)
        self.web_url = QLabel(""); self.web_url.setWordWrap(True); self.web_url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow("網址", self.web_url)
        tr_ = QHBoxLayout()
        self.ts_url = QLabel("偵測中…"); self.ts_url.setWordWrap(True)
        self.ts_url.setTextInteractionFlags(Qt.TextSelectableByMouse); tr_.addWidget(self.ts_url, 1)
        self.ts_btn = QPushButton("📱 手機 App（Tailscale HTTPS）"); self.ts_btn.clicked.connect(self._ts_setup); tr_.addWidget(self.ts_btn)
        b = QPushButton("複製網址"); b.clicked.connect(self._ts_copy); tr_.addWidget(b)
        f.addRow("Tailscale", tr_)
        ts_hint = QLabel("電腦和手機都裝 Tailscale、登入同一個帳號，出門用 4G/5G 也能連。FusionStitch 使用自己的 HTTPS 埠"
                         "（本機埠＋1000，預設 9361），和 TikLive 等其他程式同時開著不會互相覆蓋。")
        ts_hint.setObjectName("sub"); ts_hint.setWordWrap(True); f.addRow("", ts_hint)
        hint = QLabel("第一次開啟時 Windows 可能會詢問防火牆，請允許「私人網路」。PIN 只有知道的人能操作。")
        hint.setObjectName("sub"); hint.setWordWrap(True); f.addRow("", hint)
        l.addWidget(g)
        QTimer.singleShot(0, self._web_refresh)

        g = QGroupBox("資料位置")
        f = QFormLayout(g)
        for label, path in (("程式資料夾", core.ROOT), ("設定與佇列", core.PACKAGE_DIR), ("紀錄檔", core.LOG_DIR)):
            b = QPushButton(str(path)); b.clicked.connect(lambda _, p=path: os.startfile(p)); f.addRow(label, b)
        l.addWidget(g)
        l.addStretch()
        self._fill_custom()

    def _auto_outputs(self, *_):
        keys = [k for k, cb in self.auto_checks.items() if cb.isChecked()]
        set_cfg("auto_outputs", keys or ["equirect"])

    def _cm_fill(self):
        self.cm_clip.clear()
        for cid, c in sorted((self.main.clips or {}).items(), key=lambda kv: kv[1].get("num", "")):
            if c.get("kind") == "video" and c.get("complete"):
                self.cm_clip.addItem(c["name"], cid)
        last = cfg("gopro_match") or {}
        if last:
            self.cm_msg.setText(f"上次：{last.get('time', '')} · {last.get('clip', '')} · 色差 {last.get('err_before')} → {last.get('err_after')}")
            self._cm_show_preview(last.get("preview"))

    def _cm_pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "原廠轉出的 360 影片", self.cm_ref.text() or (self.main.folder or ""),
                                           "影片 (*.mp4 *.mov)")
        if not p:
            return
        self.cm_ref.setText(p)
        import colormatch
        if not self.cm_clip.count():
            self._cm_fill()
        cid = colormatch.guess_clip(p, self.main.clips or {})
        if cid:
            self.cm_clip.setCurrentIndex(max(0, self.cm_clip.findData(cid)))

    def _exe_icon(self):
        try:
            import exeicon
            msg = exeicon.apply()
        except Exception as e:
            msg = f"沒有更新：{e}"
        QMessageBox.information(self, "程式圖示", msg)

    def _cm_start(self):
        import colormatch
        ref = self.cm_ref.text().strip()
        cid = self.cm_clip.currentData()
        if not ref or not os.path.exists(ref) or not cid:
            QMessageBox.information(self, "原廠色彩", "請選原廠轉出的影片，以及同一段素材（素材庫要先開啟那個資料夾）。")
            return
        c = dict(self.main.clips[cid]); c["folder"] = self.main.folder
        r = colormatch.start(c, self.main.folder, ref)
        self.cm_msg.setText(r["message"])
        if r["ok"]:
            self.cm_go.setEnabled(False)
            self._cm_timer.start()

    def _cm_poll(self):
        import colormatch
        st = colormatch.status()
        if st["state"] == "running":
            self.cm_msg.setText(f"{st['msg']}　{st['frac'] * 100:.0f}%")
            return
        self._cm_timer.stop()
        self.cm_go.setEnabled(True)
        if st["state"] == "error":
            self.cm_msg.setText("✖ " + st["error"])
            return
        r = st["result"] or {}
        self.cm_msg.setText(f"✔ 完成：色差 {r.get('err_before')} → {r.get('err_after')}（越小越接近原廠），方向差 {r.get('shift_deg')}°。"
                            f"「轉檔顏色」選「原廠色彩」之後轉的影片就會套用。")
        self._cm_show_preview(r.get("preview"))
        if self.cmode.currentData() != "gopro" and QMessageBox.question(
                self, "原廠色彩", "計算好了。之後轉檔要改用原廠色彩嗎？\n（上：我們原本的顏色、中：套用後、下：原廠）") == QMessageBox.Yes:
            self.cmode.setCurrentIndex(self.cmode.findData("gopro"))

    def _cm_show_preview(self, path):
        if path and os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self.cm_prev.setPixmap(pm.scaledToWidth(420, Qt.SmoothTransformation))
                self.cm_prev.setToolTip("上：我們原本的顏色（不調色）　中：套用原廠色彩後　下：原廠")
                self.cm_prev.setVisible(True)

    def _web_toggle(self, on):
        set_cfg("web_enabled", bool(on))
        self.main.apply_web()
        self._web_refresh()

    def _web_set_pin(self):
        p = self.web_pin.text().strip()
        if p == (cfg("web_pin") or ""):
            return
        if not (4 <= len(p) <= 20) or any(ch.isspace() for ch in p):
            self.web_pin_msg.setText("⚠ PIN 要 4～20 個字、不能有空白，沒有儲存")
            self.web_pin.setText(cfg("web_pin") or "")
            return
        set_cfg("web_pin", p)
        self.web_pin_msg.setText("✔ 已儲存（已登入的手機不受影響）")

    def _web_new_pin(self):
        set_cfg("web_pin", "")
        web_remote().pin()
        self._web_refresh()

    def _web_refresh(self):
        import webremote
        r = web_remote()
        self.web_pin.setText(r.pin())
        if r.running:
            ips = [ip for ip in webremote.lan_addresses() if not ip.startswith("100.")] or ["電腦的IP"]
            self.web_url.setText("　".join(f"http://{ip}:{self.web_port.value()}" for ip in ips))
            webremote.tailscale_refresh(self.web_port.value())
            QTimer.singleShot(4000, self._ts_show)
        else:
            self.web_url.setText("（未開啟）")
            self.ts_url.setText("（先開啟手機網頁）")

    def _ts_show(self):
        import webremote
        u = webremote.tailscale_url(self.web_port.value())
        if u:
            self.ts_url.setText(u + ("" if u.startswith("https") else "　（按右邊按鈕開啟 HTTPS，手機才能安裝成 App）"))
        else:
            self.ts_url.setText("沒有偵測到 Tailscale（電腦要安裝並登入 tailscale.com）")

    def _ts_copy(self):
        import webremote
        u = webremote.tailscale_url(self.web_port.value())
        if u:
            QApplication.clipboard().setText(u)
            self.main.statusBar().showMessage("已複製：" + u, 6000)

    def _ts_setup(self):
        import webremote
        if not web_remote().running:
            self.web_on.setChecked(True)
        self.ts_btn.setEnabled(False)
        self.ts_btn.setText("開啟中…（第一次約 10–30 秒）")
        res = {}
        port = self.web_port.value()
        threading.Thread(target=lambda: res.update(r=webremote.tailscale_serve(port)), daemon=True).start()

        def poll():
            if "r" not in res:
                return QTimer.singleShot(500, poll)
            self.ts_btn.setEnabled(True)
            self.ts_btn.setText("📱 手機 App（Tailscale HTTPS）")
            ok, msg = res["r"]
            if msg.startswith("LOGIN "):
                QDesktopServices.openUrl(QUrl(msg[6:]))
                QMessageBox.information(self, "手機 App", "需要在 Tailscale 網站開啟 HTTPS（只要做一次）：\n\n"
                                        "已幫您打開網頁，請按頁面上的「Enable」，完成後回來再按一次這個按鈕。")
                return
            if not ok:
                QMessageBox.warning(self, "手機 App", msg)
                return

            def done():
                u = webremote.tailscale_url(port)
                self._ts_show()
                if u:
                    QApplication.clipboard().setText(u)
                QMessageBox.information(self, "手機 App",
                                        f"✔ 完成！網址已複製：\n\n{u}\n\n手機（Tailscale 要開著）用 Chrome 打開 → 右上角「⋮」→「安裝應用程式」"
                                        "（或「加到主畫面」）。iPhone 用 Safari 打開 → 分享 →「加入主畫面」。\n"
                                        "第一次打開可能要等幾秒（正在申請安全憑證）。")
            QTimer.singleShot(3500, done)
        QTimer.singleShot(500, poll)

    def _pick_out(self):
        import folders
        d = folders.pick_dir(self, "輸出資料夾", "output", self.out_dir.text())
        if d:
            self.out_dir.setText(d.replace("/", "\\"))
            set_cfg("out_folder", self.out_dir.text())
            self.out_mode.setCurrentIndex(1)

    def _fill_custom(self):
        self.custom_list.clear()
        for k, p in (cfg("custom_presets") or {}).items():
            it = QListWidgetItem(f"{p['label']}　（CQ {p.get('cq')}，{p.get('width') or '自動'}，上限 {p.get('maxrate') or '不限'}）")
            it.setData(Qt.UserRole, k)
            self.custom_list.addItem(it)

    def _save_custom(self):
        name = self.c_name.text().strip()
        if not name:
            return
        cp = dict(cfg("custom_presets") or {})
        key = "c_" + str(abs(hash(name)) % 10 ** 8)
        base = self.c_base.currentData()
        cp[key] = {"label": name, "base": base, "cq": self.c_cq.value(), "x265_crf": max(8, self.c_cq.value() - 1),
                   "width": self.c_width.currentData() or None, "maxrate": self.c_max.value() or None,
                   "desc": f"自訂：{jobs.PRESETS[base]['label']} 為基礎"}
        set_cfg("custom_presets", cp)
        self._fill_custom()
        self.main.editor._fill_presets()

    def _del_custom(self):
        it = self.custom_list.currentItem()
        if not it:
            return
        cp = dict(cfg("custom_presets") or {})
        cp.pop(it.data(Qt.UserRole), None)
        set_cfg("custom_presets", cp)
        self._fill_custom()
        self.main.editor._fill_presets()


# ======================================================================== update / changelog / log pages
class DropPage(QWidget):
    zipDropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".zip") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".zip"):
                self.zipDropped.emit(u.toLocalFile())
                break


class UpdatePage(DropPage):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.pkg = None
        self.zipDropped.connect(self.load)
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        t = QLabel("線上更新 Update"); t.setObjectName("title"); l.addWidget(t)
        n = QLabel("轉檔不會中斷。選擇或拖曳更新包（v版本_Update.zip）→ 開始更新；完成後畫面會自動重整為新版本。"
                   "也可以把更新包放進程式資料夾的 UPDATE，下次開啟時自動套用。")
        n.setObjectName("sub"); n.setWordWrap(True); l.addWidget(n)
        cb = QGroupBox("☁ 雲端更新（GitHub）")
        cl = QVBoxLayout(cb)
        self.cu_info = QLabel(""); self.cu_info.setWordWrap(True); self.cu_info.setTextFormat(Qt.RichText); cl.addWidget(self.cu_info)
        crow = QHBoxLayout()
        self.cu_on = QCheckBox("自動檢查更新（開程式 1 分鐘後、之後每 6 小時；只提示，按「更新」才下載）")
        self.cu_on.setChecked(bool(cfg("cloud_on", True)))
        self.cu_on.toggled.connect(lambda v: set_cfg("cloud_on", bool(v)))
        crow.addWidget(self.cu_on, 1)
        self.cu_chk = QPushButton("☁ 檢查更新"); self.cu_chk.clicked.connect(self._cu_check); crow.addWidget(self.cu_chk)
        self.cu_skip = QPushButton("略過這版"); self.cu_skip.clicked.connect(self._cu_skip); crow.addWidget(self.cu_skip)
        self.cu_cancel = QPushButton("取消下載"); self.cu_cancel.clicked.connect(self._cu_cancel); crow.addWidget(self.cu_cancel)
        self.cu_go = QPushButton("更新"); self.cu_go.setObjectName("start"); self.cu_go.clicked.connect(self._cu_update); crow.addWidget(self.cu_go)
        cl.addLayout(crow)
        l.addWidget(cb)
        self.cu_timer = QTimer(self); self.cu_timer.setInterval(1000); self.cu_timer.timeout.connect(self._cu_paint); self.cu_timer.start()
        box = QFrame(); box.setObjectName("hero"); bl = QVBoxLayout(box); bl.setContentsMargins(14, 12, 14, 12)
        self.cur = QLabel(f"目前版本：V{APP_VERSION}"); self.cur.setObjectName("section_title"); bl.addWidget(self.cur)
        row = QHBoxLayout()
        b = QPushButton("選擇更新包…"); b.clicked.connect(self.pick); row.addWidget(b)
        self.file = QLabel("尚未選擇（也可以直接把 zip 拖進這個頁面）"); self.file.setObjectName("sub"); row.addWidget(self.file, 1)
        bl.addLayout(row)
        self.info = QLabel(""); self.info.setWordWrap(True); self.info.setTextFormat(Qt.RichText); bl.addWidget(self.info)
        h = QHBoxLayout(); h.addStretch()
        self.apply_btn = QPushButton("開始更新"); self.apply_btn.setObjectName("start"); self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply); h.addWidget(self.apply_btn)
        bl.addLayout(h)
        l.addWidget(box)
        rb = QGroupBox("退回前面的版本（每次更新前自動備份）")
        rl = QVBoxLayout(rb)
        self.backups = QListWidget(); self.backups.setMaximumHeight(160); rl.addWidget(self.backups)
        b = QPushButton("退回選取的版本"); b.clicked.connect(self.rollback); rl.addWidget(b, 0, Qt.AlignRight)
        l.addWidget(rb)
        l.addStretch()
        self.fill_backups()

    # ---- ☁ 雲端更新
    def _cu_paint(self):
        if not self.isVisible():
            return
        try:
            import cloudupd
            S = cloudupd.status()
        except Exception as e:
            self.cu_info.setText(f"雲端更新無法使用：{e}")
            return
        c = S.get("chk") or {}
        h = f"目前 V{S['cur']}（啟動器第 {S['launcher']} 版）"
        if S.get("dl"):
            d = S["dl"]
            pct = d["got"] / d["size"] * 100 if d.get("size") else 0
            h += f"<br>⬇ 下載 {d['label']}：{pct:.0f}%（{fmt_bytes(d['got'])} / {fmt_bytes(d.get('size') or 0)}，{fmt_bytes(d.get('spd') or 0)}/s）"
        elif S.get("msg"):
            h += "<br>" + S["msg"]
        if c.get("newer"):
            h += (f"<br>🆕 <b>雲端有新版 V{S['ver']}</b>" + (f"（{S['date']}）" if S.get("date") else "")
                  + "　熱更新，轉檔不中斷"
                  + ("　· 已略過這版" if c.get("skipped") else ""))
            if S.get("notes"):
                h += "<ul style='margin:2px 0'>" + "".join(f"<li>{n}</li>" for n in S["notes"][:8]) + "</ul>"
        elif c:
            h += "<br>" + (c.get("message") or ("✓ 已經是最新版" if c.get("ok") else ""))
            if c.get("t"):
                h += time.strftime("　（%m/%d %H:%M 檢查）", time.localtime(c["t"]))
        self.cu_info.setText(h)
        self.cu_go.setVisible(bool(c.get("newer")) and not S["busy"])
        self.cu_go.setText(f"更新到 V{S.get('ver') or ''}")
        self.cu_skip.setVisible(bool(c.get("newer")) and not S["busy"])
        self.cu_cancel.setVisible(bool(S.get("dl")))

    def _cu_check(self):
        import cloudupd
        self.cu_info.setText("檢查中…")
        threading.Thread(target=lambda: cloudupd.check(manual=True), daemon=True, name="FSCloudChk").start()

    def _cu_skip(self):
        import cloudupd
        self.main.toast(cloudupd.skip()["message"])

    def _cu_cancel(self):
        import cloudupd
        self.main.toast(cloudupd.cancel()["message"])

    def _cu_update(self):
        import cloudupd
        r = cloudupd.update()
        self.main.toast(r.get("message", ""), 8000)

    def fill_backups(self):
        self.backups.clear()
        for p in updater.backups()[:30]:
            it = QListWidgetItem(f"V{updater.backup_version(p)}　{p.name}")
            it.setData(Qt.UserRole, str(p))
            self.backups.addItem(it)

    def pick(self):
        import folders
        p = folders.pick_file(self, "選擇更新包", "update", "更新包 (*_Update.zip *.zip)")
        if p:
            self.load(p)

    def load(self, path):
        self.pkg = None
        self.apply_btn.setEnabled(False)
        self.file.setText(Path(path).name)
        try:
            pkg = updater.read_package(path)
        except Exception as e:
            self.info.setText(f'<span style="color:#ff6577">無法使用這個更新包：{e}</span>')
            return
        vt, ct = core.version_tuple(pkg["version"]), core.version_tuple(APP_VERSION)
        direction = "升級" if vt > ct else ("相同版本" if vt == ct else "⚠ 降版")
        html = f"<b>V{APP_VERSION} → V{pkg['version']}</b>　（{direction}）"
        if pkg["notes"]:
            html += "<br>" + "<br>".join("• " + str(n) for n in pkg["notes"])
        html += ('<br><span style="color:#f0ad4e">目前有轉檔進行中：轉檔不會中斷，正在轉的這個工作沿用舊版程式，之後的工作用新版。</span>'
                 if job_queue().running() else '<br><span style="color:#45d483">可以立即熱更新。</span>')
        self.info.setText(html)
        self.pkg = pkg
        self.apply_btn.setEnabled(True)

    def apply(self):
        pkg = self.pkg
        if not pkg:
            return
        if QMessageBox.warning(self, "確認更新", f"您將從 V{APP_VERSION} 更新到 V{pkg['version']}。\n\n"
                               "更新期間轉檔不會中斷，完成後畫面會自動重整為新版本。\n\n確定開始更新？",
                               QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Ok:
            return
        try:
            backup = updater.backup_current(f"before_V{pkg['version']}")
            updater.write_files(pkg["files"], backup)
        except Exception as e:
            QMessageBox.warning(self, "更新失敗", f"寫入檔案失敗，已還原：{e}")
            return
        notes = "\n".join("  • " + n for n in pkg["notes"])
        try:
            log = json.loads(pkg["files"]["CHANGELOG.json"].decode("utf-8-sig"))
            entry = next((e for e in log if str(e.get("version")) == pkg["version"]), {})
            notes += "\n" + "\n".join("    - " + d for d in entry.get("details", []))
        except Exception:
            pass
        updater.log_update(f"更新 V{APP_VERSION} -> V{pkg['version']}（{pkg['path']}）\n{notes}")
        self.main.hot_reload(backup=backup, announce={"from": APP_VERSION, "to": pkg["version"], "items": pkg["notes"]},
                             pkg_path=pkg["path"])

    def rollback(self):
        it = self.backups.currentItem()
        if not it:
            return
        p = Path(it.data(Qt.UserRole))
        ver = updater.backup_version(p)
        if QMessageBox.question(self, "退回版本", f"退回到 V{ver}（{p.name}）？目前的程式會先備份。") != QMessageBox.Yes:
            return
        cur_backup = updater.backup_current(f"before_rollback_to_V{ver}")
        updater.restore(p)
        updater.log_update(f"rollback V{APP_VERSION} -> V{ver} ({p.name})")
        self.main.hot_reload(backup=cur_backup, announce={"from": APP_VERSION, "to": ver, "items": ["退回舊版"]})


def changelog_html(log, only=None) -> str:
    """CHANGELOG.json -> HTML. Each entry: items (what changed) + details (how / measured)."""
    import html as _h
    out = []
    for n, e in enumerate(log):
        if only and str(e.get("version")) != only:
            continue
        cur = str(e.get("version")) == APP_VERSION
        out.append(f'<div style="margin:6px 0 14px 0">'
                   f'<span style="font-size:15pt;font-weight:900;color:{"#4dc9ff" if cur else "#e8ecf2"}">V{_h.escape(str(e.get("version")))}</span>'
                   f'<span style="color:#8994a4">　{_h.escape(str(e.get("date", "")))}{"　（目前版本）" if cur else ""}</span>')
        if e.get("title"):
            out.append(f'<div style="font-weight:800;margin-top:2px">{_h.escape(e["title"])}</div>')
        items = e.get("items", [])
        if items:
            out.append('<div style="color:#45d483;font-weight:800;margin-top:6px">更新內容</div><ul style="margin-top:2px">')
            out += [f"<li style='margin:3px 0'>{_h.escape(str(i))}</li>" for i in items]
            out.append("</ul>")
        det = e.get("details", [])
        if det:
            out.append('<div style="color:#f0ad4e;font-weight:800;margin-top:4px">技術細節與實測</div><ul style="margin-top:2px;color:#aeb7c4">')
            out += [f"<li style='margin:3px 0'>{_h.escape(str(i))}</li>" for i in det]
            out.append("</ul>")
        out.append("</div><hr style='border:0;border-top:1px solid #252c35'>")
    return "".join(out) or "（沒有更新紀錄）"


class ChangelogPage(QWidget):
    def __init__(self, main):
        super().__init__()
        from PySide6.QtWidgets import QTextBrowser
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        t = QLabel("升級資訊"); t.setObjectName("title"); top.addWidget(t)
        sub = QLabel("每一版做了什麼、怎麼做、實測結果。更新的過程也記錄在 LOG 與問題 → update_log.txt")
        sub.setObjectName("sub"); top.addWidget(sub, 1)
        l.addLayout(top)
        self.txt = QTextBrowser()
        self.txt.setOpenExternalLinks(True)
        self.txt.setStyleSheet("QTextBrowser{background:#11161c;border:1px solid #252c35;border-radius:9px;padding:10px;font-size:10.5pt}")
        try:
            log = json.loads((core.APP_DIR / "CHANGELOG.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log = []
        self.txt.setHtml(changelog_html(log))
        l.addWidget(self.txt, 1)


class HelpPage(QWidget):
    def __init__(self, main):
        super().__init__()
        from PySide6.QtWidgets import QTextBrowser
        import helpdoc
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        t = QLabel("使用說明"); t.setObjectName("title"); l.addWidget(t)
        tb = QTextBrowser()
        tb.setStyleSheet("QTextBrowser{background:#11161c;border:1px solid #252c35;border-radius:9px;padding:12px;font-size:10.5pt}")
        tb.setHtml(helpdoc.H)
        l.addWidget(tb, 1)


class LogPage(QWidget):
    def __init__(self, main):
        super().__init__()
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        t = QLabel("LOG 與問題"); t.setObjectName("title"); top.addWidget(t, 1)
        b = QPushButton("開啟 LOG 資料夾"); b.clicked.connect(lambda: os.startfile(core.LOG_DIR)); top.addWidget(b)
        b = QPushButton("重新整理"); b.clicked.connect(self.refresh); top.addWidget(b)
        l.addLayout(top)
        self.files = QComboBox(); self.files.currentIndexChanged.connect(self.show_file); l.addWidget(self.files)
        self.txt = QPlainTextEdit(); self.txt.setReadOnly(True); self.txt.setFont(QFont("Consolas", 9)); l.addWidget(self.txt, 1)

    def refresh(self):
        cur = self.files.currentText()
        self.files.blockSignals(True)
        self.files.clear()
        for p in sorted(core.LOG_DIR.glob("*.txt")):
            self.files.addItem(p.name)
        if cur:
            self.files.setCurrentIndex(max(0, self.files.findText(cur)))
        self.files.blockSignals(False)
        self.show_file()

    def show_file(self, *_):
        p = core.LOG_DIR / self.files.currentText() if self.files.currentText() else None
        if p and p.exists():
            data = p.read_bytes()[-200_000:].decode("utf-8", "replace")
            self.txt.setPlainText(data)
            self.txt.verticalScrollBar().setValue(self.txt.verticalScrollBar().maximum())


# ======================================================================== main window
class MainWindow(QMainWindow):
    def __init__(self, adopt: dict | None = None):
        super().__init__()
        self.setWindowTitle(f"FusionStitch V{APP_VERSION} — GoPro Fusion 360 預覽與轉檔")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(stylesheet())
        self.bus = Bus()
        self.folder = ""
        self.clips: dict = {}
        self.meta: dict = {}
        self._out_cache = (0, None, [])
        self._handoff = False
        self._quitting = False

        side = QFrame(); side.setObjectName("side"); side.setFixedWidth(196)
        sl = QVBoxLayout(side); sl.setContentsMargins(8, 12, 8, 8); sl.setSpacing(6)
        brand = QLabel("FusionStitch"); brand.setObjectName("brand"); sl.addWidget(brand)
        sub = QLabel("GoPro Fusion 360"); sub.setObjectName("sub"); sl.addWidget(sub)
        self.nav = QListWidget(); self.nav.setObjectName("workspace_nav")
        self.nav.setItemDelegate(NavDelegate(self.nav)); self.nav.setMouseTracking(True)
        sl.addWidget(self.nav, 1)
        self.side_job = QLabel(""); self.side_job.setObjectName("sub"); self.side_job.setWordWrap(True); sl.addWidget(self.side_job)
        self.side_bar = QProgressBar(); self.side_bar.setRange(0, 1000); self.side_bar.setTextVisible(False); self.side_bar.setFixedHeight(6)
        sl.addWidget(self.side_bar)
        fb = QPushButton("◫ 效能浮窗"); fb.clicked.connect(lambda: self.set_float(not self.float_visible())); sl.addWidget(fb)
        ver = QLabel(f"V{APP_VERSION}"); ver.setObjectName("sub"); sl.addWidget(ver)

        self.stack = QStackedWidget()
        self.library = LibraryPage(self)
        self.editor = EditorPage(self)
        self.queue_page = QueuePage(self)
        self.perf_page = PerfPage(self)
        self.settings_page = SettingsPage(self)
        self.update_page = UpdatePage(self)
        self.changelog = ChangelogPage(self)
        self.log_page = LogPage(self)
        self.help_page = HelpPage(self)
        import outputs_page
        self.outputs_page = outputs_page.OutputsPage(self)
        self.PAGE_OUTPUTS = PAGE_OUTPUTS
        import quick_page
        self.quick_page = quick_page.QuickPage(self)
        self.PAGE_QUICK = PAGE_QUICK
        for w in (self.library, self.editor, self.queue_page, self.perf_page, self.settings_page,
                  self.update_page, self.changelog, self.log_page, self.help_page, self.outputs_page, self.quick_page):
            self.stack.addWidget(w)

        section = {"color": "#4dc9ff"}

        def group(text, color):
            it = QListWidgetItem(text); it.setFlags(Qt.ItemIsEnabled)
            it.setData(Qt.UserRole, -1); it.setData(Qt.UserRole + 1, color); it.setData(Qt.UserRole + 2, "header")
            section["color"] = color; self.nav.addItem(it)

        self.nav_items = {}

        def item(page, text, icon):
            it = QListWidgetItem(text); it.setData(Qt.UserRole, page); it.setData(Qt.UserRole + 1, section["color"])
            it.setData(Qt.UserRole + 2, "item"); it.setData(Qt.UserRole + 3, icon); self.nav.addItem(it)
            self.nav_items[page] = it

        group("開始", "#e51e63"); item(PAGE_QUICK, "快速轉檔", "★")
        group("素材", "#4dc9ff"); item(PAGE_LIBRARY, "素材庫", "▦"); item(PAGE_EDITOR, "預覽校正", "◉")
        group("轉檔", "#f0ad4e"); item(PAGE_QUEUE, "轉檔佇列", "⇶"); item(PAGE_OUTPUTS, "成品／歷史紀錄", "▶")
        group("電腦", "#45d483"); item(PAGE_PERF, "電腦效能", "▤"); item(PAGE_LOG, "LOG 與問題", "⇪")
        group("設定", "#9aa5b3"); item(PAGE_SETTINGS, "設定", "⚙"); item(PAGE_UPDATE, "線上更新", "⟳"); item(PAGE_CHANGELOG, "升級資訊", "✦"); item(PAGE_HELP, "使用說明", "?")
        self.nav.currentItemChanged.connect(self._nav_changed)

        central = QWidget(); cl = QHBoxLayout(central); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        cl.addWidget(side); cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.statusBar()

        self.bus.scanDone.connect(self.library.on_scan)
        self.bus.clipInfo.connect(self.library.on_meta)
        self.bus.analysisDone.connect(self.editor.on_analysis)
        self.bus.analysisProgress.connect(self.editor.on_progress)
        self.bus.webUpdate.connect(self._web_update)
        self.bus.uiCall.connect(lambda fn: fn())
        self.bus.jobFinished.connect(self._job_finished)
        q = job_queue()
        q.listeners[:] = [lambda j: self.bus.jobFinished.emit(j)]
        q.after_queue_cb = self._after_queue

        self.float = PerfFloat(self)
        QTimer.singleShot(500, self.apply_web)
        self._make_tray()
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        perf_sampler()

        geom = cfg("window_geometry")
        if geom:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))
        else:
            self.resize(1500, 900)
        self.go(PAGE_QUICK if not adopt else adopt.get("page", 0))      # always opens on the clean 快速轉檔 page
        if cfg("perf_float_start"):
            QTimer.singleShot(300, lambda: self.set_float(True))
        if adopt:
            QTimer.singleShot(0, lambda: self._adopt(adopt))
        elif cfg("last_folder") and os.path.isdir(cfg("last_folder")) and __import__("clips").has_clips(cfg("last_folder")):
            QTimer.singleShot(100, lambda: self.library.scan(cfg("last_folder")))

    # ---- navigation
    def go(self, page):
        it = self.nav_items.get(page)
        if it:
            self.nav.setCurrentItem(it)

    def _nav_changed(self, cur, prev):
        if cur is None:
            return
        page = cur.data(Qt.UserRole)
        if page is None or page < 0:
            if prev is not None:
                self.nav.setCurrentItem(prev)
            return
        self.stack.setCurrentIndex(page)
        set_cfg("nav_row", page)
        if page == PAGE_LOG:
            self.log_page.refresh()
        if page == PAGE_UPDATE:
            self.update_page.fill_backups()

    def toast(self, msg, ms=5000):
        self.statusBar().showMessage(msg, ms)

    # ---- phone web page
    def apply_web(self):
        import webremote
        r = web_remote()
        if getattr(r, "code", None) != webremote.CODE:      # server from the previous version: replace it
            try:
                r.stop()
            except Exception:
                pass
            try:
                delattr(QApplication.instance(), "_fs_web")
            except AttributeError:
                pass
            r = web_remote()
        want = bool(cfg("web_enabled", False))
        port = int(cfg("web_port", 8361))
        try:
            if want and not r.running:
                r.start(port)
                core.log(f"phone web page on port {port}")
            elif not want and r.running:
                r.stop()
        except OSError as e:
            self.toast(f"手機網頁無法開啟（連接埠 {port}）：{e}", 10000)

    def _web_update(self, path):
        """Update package confirmed on the phone: install without dialogs (the phone showed the notes)."""
        try:
            pkg = updater.read_package(path)
            backup = updater.backup_current(f"before_V{pkg['version']}_web")
            updater.write_files(pkg["files"], backup)
        except Exception as e:
            updater.log_update(f"phone update failed: {e}")
            self.tray_msg("手機更新失敗", str(e), QSystemTrayIcon.Warning)
            return
        updater.log_update(f"更新 V{APP_VERSION} -> V{pkg['version']}（手機上傳）\n" + "\n".join("  • " + n for n in pkg["notes"]))
        self.hot_reload(backup=backup, announce={"from": APP_VERSION, "to": pkg["version"], "items": pkg["notes"]}, pkg_path=path)

    # ---- clips & jobs
    def out_files(self, folder: Path):
        now = time.time()
        if self._out_cache[1] == str(folder) and now - self._out_cache[0] < 3:
            return self._out_cache[2]
        try:
            files = os.listdir(folder)
        except OSError:
            files = []
        self._out_cache = (now, str(folder), files)
        return files

    def open_clip(self, cid):
        c = self.clips.get(cid)
        if not c:
            return
        if not c["complete"]:
            QMessageBox.warning(self, "素材不完整", f"{c['name']} 的前／後鏡頭檔案不完整，無法拼接。")
            return
        self.go(PAGE_EDITOR)
        self.editor.load(c)

    def requeue_output(self, path: str) -> bool:
        """↻ 重新轉檔 from a finished file: same clip, same preset (taken from the file name)."""
        import re as _re
        m = _re.search(r"_GP(\d{4})_([A-Za-z0-9]+)", os.path.basename(path))
        if not m:
            return False
        cid = next((k for k, c in self.clips.items() if c["num"] == m.group(1)), None)
        if not cid:
            return False
        pk = m.group(2) if m.group(2) in jobs.all_presets() else None
        return self.queue_clip(cid, pk)

    def queue_clip(self, cid, preset=None, folder=None, clip=None) -> bool:
        """Queue with the saved calibration and the current output settings (or the given preset).
        folder/clip: queue a clip of another footage folder (📂 複選資料夾) without switching to it."""
        folder = folder or self.folder
        c = clip if clip is not None else self.clips.get(cid)
        if not c or not c["complete"]:
            return False
        meta = (self.meta.get(cid) if clip is None else None) or clip_meta(c)
        if c["kind"] == "video":
            tl = media.timeline(c["front"])
            w, h = tl.width, tl.height
        else:
            from imageio_util import imread
            h, w = imread(c["front"][0]).shape[:2]
        cal = (cfg("calibration") or {}).get(f"{w}x{h}")
        params = geo.merge_params(geo.default_params(w, h), cal) if cal else geo.default_params(w, h)
        proj = projects.get(folder, cid)
        if proj.get("params"):
            params = geo.merge_params(geo.default_params(w, h), proj["params"])
        kind = proj.get("projection") or cfg("projection") or "equirect"
        planet = kind in ("planet", "flat")
        preset = preset or cfg("preset")
        ext = "jpg" if c["kind"] == "photo" else "mp4"
        name = output_name(c, meta, preset, kind, ext)
        sp = float(proj.get("speed", 1.0)) if c["kind"] == "video" else 1.0
        if sp != 1.0:
            stem, dot, e_ = name.rpartition(".")
            name = f"{stem}_{sp:g}x.{e_}"
        try:
            import batches
            bid = batches.assign(folder)
            bdest = batches.get(bid).get("dest")
        except Exception as e:
            core.log(f"batch assign: {e}")
            bid, bdest = None, ""
        out = str((Path(bdest) if bdest else output_folder(folder)) / name)
        spatial = bool(not planet and cfg("spatial_audio") and c.get("wavs") and len(c["wavs"]) == len(c["back"]))
        opts = {"preset": preset, "engine": "auto", "params": params, "audio": True, "output": out,
                "spatial_audio": spatial,
                "projection": {"type": kind, **(proj.get(kind) or cfg(kind) or {})} if planet else {"type": "equirect"},
                "size": cfg(kind + "_size") if planet else None, "grade": proj.get("grade"), "patch": proj.get("patch"),
                "path": ((proj.get("paths") or {}).get(kind) or None) if (planet and c["kind"] == "video") else None,
                "speed": sp,
                "stab": proj.get("stab") if c["kind"] == "video" else None,
                "seamflow_file": proj.get("seamflow_file") if proj.get("seamflow_on") else None,
                "color_track": proj.get("color_track") if (c["kind"] == "video" and proj.get("color_track_on")) else None,
                "auto_exposure": float(proj.get("auto_exposure") or 0)}
        if bid:
            opts["batch"] = bid
        fps = tl.fps if c["kind"] == "video" else 30.0
        if proj.get("in") is not None:
            opts["start"] = proj["in"] / fps
        if proj.get("out") is not None:
            opts["end"] = proj["out"] / fps
        return self.submit(c, opts, quiet=True, folder=folder)

    def submit(self, clip, opts, quiet=False, folder=None) -> bool:
        out = opts.get("output")
        if not out:
            QMessageBox.warning(self, "輸出", "請指定輸出檔案。")
            return False
        q = job_queue()
        if any(j.output == out and j.status in ("queued", "running", "paused") for j in q.jobs):
            if not quiet:
                QMessageBox.information(self, "已在佇列", "這個輸出檔已經在佇列裡了。")
            return False
        if os.path.exists(out):
            if quiet:
                return False
            if QMessageBox.question(self, "檔案已存在", f"{out}\n已存在，要覆蓋嗎？") != QMessageBox.Yes:
                return False
        clip = dict(clip)
        clip["folder"] = folder or self.folder
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        q.add(clip, opts)
        self.toast(f"已加入佇列：{Path(out).name}")
        self.library.refresh_status()
        return True

    @staticmethod
    def warm_folders(folders_, progress=None, stop=None):
        """(背景執行緒) 先把每支影片的檔案資訊讀好（ffprobe，有快取），之後在畫面執行緒加入佇列就不會卡."""
        import clips as clips_mod
        for i, f in enumerate(folders_):
            try:
                cs = [c for c in clips_mod.scan(f) if c.get("complete") and c.get("kind") == "video"]
            except Exception:
                continue
            for j, c in enumerate(cs):
                if stop and stop():
                    return
                if progress:
                    progress(f"讀取 {os.path.basename(f)}（{i + 1}/{len(folders_)}）：{j + 1}/{len(cs)}")
                try:
                    media.timeline(c["front"])
                    for p in c["front"] + c["back"]:
                        media.probe(p)
                except Exception:
                    pass

    def queue_folders(self, folders_, preset=None, progress=None) -> dict:
        """📂 一次把好幾個素材資料夾的影片加入佇列（每個資料夾自己一批）。
        跳過：不完整、已經在佇列、成品已經存在、以前轉過（✓ 已轉過）。回傳 {folder: (加入, 略過)}."""
        import clips as clips_mod
        import donemark
        import folders
        res = {}
        for i, f in enumerate(folders_):
            add = skip = 0
            try:
                cs = clips_mod.scan(f)
            except Exception as e:
                core.log(f"queue_folders scan {f}: {e}")
                cs = []
            dfs = donemark.folders_for(str(output_folder(f)))
            for j, c in enumerate(cs):
                if progress:
                    progress(f"{os.path.basename(f)}（{i + 1}/{len(folders_)}）：{j + 1}/{len(cs)}")
                if not c.get("complete") or c.get("kind") != "video":
                    skip += 1
                    continue
                try:
                    if donemark.find(c, clip_meta(c), None, dfs):
                        skip += 1
                        continue
                    if self.queue_clip(c["id"], preset, folder=f, clip=c):
                        add += 1
                    else:
                        skip += 1
                except Exception as e:
                    core.log(f"queue_folders {f} {c.get('id')}: {e}")
                    skip += 1
            res[f] = (add, skip)
            folders.remember("footage", f)
        return res

    def _job_finished(self, j):
        self.library.refresh_status()
        if self.outputs_page.isVisible():
            self.outputs_page.refresh()
        self.outputs_page._update_badge()
        if j.status == "done":
            self.tray_msg("轉檔完成", f"{j.title}\n{Path(j.output).name}")
        elif j.status == "error":
            self.tray_msg("轉檔失敗", f"{j.title}：{j.message[:120]}", QSystemTrayIcon.Warning)

    def _after_queue(self):
        what = cfg("after_queue")
        if what == "none" or self._quitting:
            return
        set_cfg("after_queue", "none")
        core.log(f"queue finished -> {what}")
        if what == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "60", "/c", "FusionStitch：轉檔全部完成，60 秒後關機（執行 shutdown /a 可取消）"])
        elif what == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])

    # ---- periodic UI refresh
    def _tick(self):
        if self._handoff:
            return
        s = perf_sampler()
        data, hist = s.snapshot()
        run = job_queue().running()
        s.job_info = {"fps": run.fps if run else 0}
        if self.float.isVisible():
            self.float.refresh(data, hist, run)
        if self.stack.currentIndex() == PAGE_PERF:
            self.perf_page.refresh(data, hist)
        if self.stack.currentIndex() == PAGE_QUEUE:
            self.queue_page.refresh()
        q = job_queue()
        n = sum(1 for j in q.jobs if j.status in ("queued", "running"))
        it = self.nav_items.get(PAGE_QUEUE)
        if it and it.data(Qt.UserRole + 4) != (n or None):
            it.setData(Qt.UserRole + 4, n or None)
        if run:
            self.side_job.setText(f"▶ {run.title}  {run.progress * 100:.1f}%\n{run.fps:.1f} fps · 剩 {fmt_dur(run.eta)}")
            self.side_bar.setValue(int(run.progress * 1000))
            self.side_bar.show()
            self.tray.setToolTip(f"FusionStitch：{run.title} {run.progress * 100:.0f}%")
        else:
            self.side_job.setText("")
            self.side_bar.hide()
            self.tray.setToolTip("FusionStitch")
        if self.stack.currentIndex() == PAGE_LIBRARY and int(time.time()) % 3 == 0:
            self.library.refresh_status()

    # ---- performance float
    def float_visible(self):
        return self.float.isVisible()

    def set_float(self, on):
        if on:
            if not cfg("perf_float_pos"):
                g = self.screen().availableGeometry()
                self.float.adjustSize()
                self.float.move(g.right() - 260, g.top() + 60)
            self.float.show()
            self.float.raise_()
        else:
            self.float.hide()

    # ---- tray
    def _make_tray(self):
        app = QApplication.instance()
        old = getattr(app, "_fs_tray", None)
        if old is not None:
            old.hide()
            old.deleteLater()
        self.tray = QSystemTrayIcon(app_icon(), self)
        m = QMenu()
        m.addAction("顯示 FusionStitch", self.show_main)
        m.addAction("效能浮窗", lambda: self.set_float(not self.float_visible()))
        m.addAction("轉檔佇列", lambda: (self.show_main(), self.go(PAGE_QUEUE)))
        m.addSeparator()
        m.addAction("結束", self.quit_app)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_main() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
        app._fs_tray = self.tray

    def tray_msg(self, title, text, icon=QSystemTrayIcon.Information):
        try:
            self.tray.showMessage(title, text, icon, 8000)
        except Exception:
            pass

    def show_main(self):
        self.showNormal() if self.isMinimized() or not self.isVisible() else None
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if self._handoff:
            e.accept()
            return
        q = job_queue()
        if q.busy() and cfg("minimize_to_tray") and not self._quitting:
            e.ignore()
            self.hide()
            self.tray_msg("FusionStitch 在背景繼續轉檔", "從系統匣圖示可以打開視窗或結束程式。")
            return
        e.ignore()
        self.quit_app()

    def quit_app(self, force=False):
        q = job_queue()
        if q.busy() and not force:
            r = QMessageBox.question(self, "結束 FusionStitch",
                                     "還有轉檔工作。\n結束後，下次開啟程式會從中斷處繼續（已完成的部分不會重做）。\n\n確定結束？")
            if r != QMessageBox.Yes:
                return
        self._quitting = True
        set_cfg("window_geometry", bytes(self.saveGeometry().toHex()).decode(), save=False)
        core.save_settings()
        runs = [j for j in q.jobs if j.status == "running"]      # (several run at once since 2.17)
        for run in runs:
            run.pause = True
        t0 = time.time()
        while any(r.status == "running" for r in runs) and time.time() - t0 < 10:
            QApplication.processEvents()
            time.sleep(0.05)
        for run in runs:
            run.status = "queued"   # resume next start
        if runs:
            (q._save_now() if hasattr(q, "_save_now") else q.save())     # 關程式：馬上寫，不等合併
        try:
            import psutil
            for ch in psutil.Process().children(recursive=True):
                ch.kill()
        except Exception:
            pass
        try:
            import hsbridge
            hsbridge.stop()                       # 🤝 對方馬上知道這邊不在了
        except Exception:
            pass
        try:
            perf_sampler().shutdown()
        except Exception:
            pass
        self.editor.player.stop()
        self.tray.hide()
        self.float.close()
        QApplication.instance().quit()

    # ---- hot update
    def export_state(self) -> dict:
        return {"page": self.stack.currentIndex(), "folder": self.folder,
                "clip": self.editor.clip["id"] if self.editor.clip else None,
                "float": self.float_visible()}

    def _adopt(self, st):
        if st.get("folder"):
            self.library.scan(st["folder"])
            if st.get("clip"):
                def reopen():
                    if st["clip"] in self.clips:
                        self.open_clip(st["clip"])
                QTimer.singleShot(1500, reopen)
        self.set_float(bool(st.get("float")))
        ann = st.get("announce")
        if ann:
            set_cfg("last_version", ann["from"])        # show the full notes of every newer version once
            _announce_new_version(self)

    def hot_reload(self, backup=None, announce=None, pkg_path=None):
        """Load the new code as fresh modules and hand everything live to a new window."""
        import importlib
        state = self.export_state()
        state["announce"] = announce
        set_cfg("window_geometry", bytes(self.saveGeometry().toHex()).decode())
        app_dir = str(core.APP_DIR).lower()
        saved = {}
        for name, mod in list(sys.modules.items()):
            f = str(getattr(mod, "__file__", "") or "").lower()
            if f.startswith(app_dir):
                saved[name] = sys.modules.pop(name)
        try:
            new_app = importlib.import_module("app")
            new_win = new_app.MainWindow(adopt=state)
        except Exception as e:
            err = traceback.format_exc()
            updater.log_update("HOT RELOAD FAILED\n" + err)
            for name in [n for n, m in sys.modules.items() if str(getattr(m, "__file__", "") or "").lower().startswith(app_dir)]:
                sys.modules.pop(name, None)
            sys.modules.update(saved)
            if backup:
                updater.restore(backup)
            if pkg_path:
                try:
                    import shutil
                    shutil.copy2(pkg_path, core.UPDATE_DIR / Path(pkg_path).name)
                except Exception:
                    pass
            QMessageBox.warning(self, "無法熱更新", "新版無法在執行中載入，已還原目前版本，轉檔沒有受到影響。\n"
                                "更新包已放進 UPDATE 資料夾，下次開啟程式時會自動套用（失敗會自動退回）。\n\n" + str(e))
            return
        self._handoff = True
        self.timer.stop()
        self.editor.player.stop()
        self.float.hide()
        self.float.deleteLater()
        geo_ = self.saveGeometry()
        was_max = self.isMaximized()
        new_win.restoreGeometry(geo_)
        new_win.showMaximized() if was_max else new_win.show()
        QApplication.instance()._fs_window = new_win
        self.hide()
        self.deleteLater()


# ======================================================================== entry point
def _announce_new_version(win):
    """First start of a new version (updated by the launcher or by hand): write the full
    changelog entry to the update log (LOG folder) and show it once."""
    last = cfg("last_version")
    set_cfg("last_version", APP_VERSION)
    if not last or last == APP_VERSION:
        return
    try:
        log = json.loads((core.APP_DIR / "CHANGELOG.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log = []
    newer = [e for e in log if core.version_tuple(last) < core.version_tuple(e.get("version")) <= core.version_tuple(APP_VERSION)]
    lines = [f"啟動新版本 V{last} -> V{APP_VERSION}"]
    for e in newer:
        lines.append(f"  V{e.get('version')} {e.get('date', '')}")
        lines += ["    • " + str(i) for i in e.get("items", [])]
        lines += ["      - " + str(d) for d in e.get("details", [])]
    updater.log_update(chr(10).join(lines))
    from PySide6.QtWidgets import QDialog, QTextBrowser, QDialogButtonBox
    dlg = QDialog(win)
    dlg.setWindowTitle(f"已更新到 V{APP_VERSION}")
    dlg.resize(760, 560)
    lay = QVBoxLayout(dlg)
    tb = QTextBrowser()
    tb.setHtml("".join(changelog_html([e]) for e in newer))
    lay.addWidget(tb)
    bb = QDialogButtonBox(QDialogButtonBox.Ok)
    bb.accepted.connect(dlg.accept)
    lay.addWidget(bb)
    QTimer.singleShot(600, dlg.show)


# ======================================================================== entry point
def selftest() -> str:
    """FusionStitch.exe --selftest : checks every engine part, writes LOG\\selftest.txt."""
    lines = [f"FusionStitch V{APP_VERSION} selftest {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    def check(name, fn):
        try:
            lines.append(f"OK   {name}: {fn()}")
        except Exception as e:
            lines.append(f"FAIL {name}: {e}")
    import ffm
    check("ffmpeg", ffm.version)
    check("encoders", lambda: {k: v["ok"] for k, v in ffm.available_encoders().items()})
    import gpu
    check("gpu stitch", lambda: gpu.available())
    def gpu_run():
        import numpy as np
        P = geo.default_params(256, 256)
        g = gpu.GPUStitcher(P, 256, 256, 512, 256, 10)
        out = g.stitch(np.full(256 * 384, 128, np.uint8).tobytes(), np.full(256 * 384, 128, np.uint8).tobytes())
        g.release()
        return f"{len(out)} bytes"
    check("gpu render", gpu_run)
    check("scipy", lambda: __import__("scipy.optimize").__version__)
    def interp():
        import camerapath
        v = camerapath.at([{"t": 0, "yaw": 0, "pitch": 0, "roll": 0, "fov": 90}, {"t": 2, "yaw": 90, "pitch": 0, "roll": 0, "fov": 90}], 1.0)
        import scipy.interpolate  # noqa: F401
        return f"camera path yaw@1s = {v['yaw']:.1f}"
    check("camera path", interp)
    def enh():
        import moderngl, numpy as np, enhance
        ctx = moderngl.create_standalone_context(require=430)
        t = ctx.texture((64, 64), 1, np.full(64 * 64, 100, np.uint8).tobytes())
        e = enhance.GLEnhancer(ctx)
        out = e.run(t, "y", 1, {"denoise": 0.5, "sharpen": 0.5}, True)
        v = np.frombuffer(out.read(), np.uint8).mean()
        ctx.release()
        return f"enhance pass ok (mean {v:.0f})"
    check("enhance", enh)
    def shaders():
        import moderngl, preview, gpu as g_
        ctx = moderngl.create_standalone_context(require=430)
        for name, v, f in (("preview", preview.VERT, preview.FRAG), ("dynamic", g_._VERT, g_._FRAG_DYN),
                           ("graded", g_._VERT, g_._FRAG_GRADED), ("fast", g_._VERT, g_._FRAG_FAST)):
            ctx.program(vertex_shader=v, fragment_shader=f).release()
        ctx.release()
        return "preview / dynamic / graded / fast compile"
    check("shaders", shaders)
    check("opencv", lambda: __import__("cv2").__version__)
    import perf
    check("perf", lambda: perf.fixed_drives())
    text = chr(10).join(lines)
    (core.LOG_DIR / "selftest.txt").write_text(text, encoding="utf-8")
    return text


def main(launcher_info: dict | None = None):
    if "--selftest" in sys.argv:
        print(selftest())
        return 0
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FusionStitch.App")
        except Exception:
            pass
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FusionStitch")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    # single instance: a second start just brings the first window forward
    sock = QLocalSocket()
    sock.connectToServer("FusionStitch_single")
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer("FusionStitch_single")
    server.listen("FusionStitch_single")
    app._fs_server = server

    def on_conn():
        c = server.nextPendingConnection()
        w = getattr(app, "_fs_window", None)
        if w:
            w.show_main()
        if c:
            c.disconnectFromServer()
    server.newConnection.connect(on_conn)

    def excepthook(t, v, tb):
        core.log("UNCAUGHT\n" + "".join(traceback.format_exception(t, v, tb)), "crash_log.txt")
    sys.excepthook = excepthook
    core.log(f"start V{APP_VERSION} launcher={launcher_info}")
    cli_auto = None
    if "--auto" in sys.argv:
        # FusionStitch.exe --auto "<素材資料夾>" [--only 6544,6545] : import and process automatically
        i = sys.argv.index("--auto")
        folder = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
        if os.path.isdir(folder):
            set_cfg("last_folder", folder)          # the window scans it on start
            cli_auto = {"only": only}
            core.log(f"command line auto processing: {folder} only={only}")
    win = MainWindow()
    app._fs_window = win
    win._cli_auto = cli_auto
    win.show()
    _announce_new_version(win)
    try:
        import cloudupd                     # ☁ 雲端更新: background checks, report a good start after an EXE swap
        cloudupd.after_start(win)
    except Exception as e:
        core.log(f"cloudupd: {e}")
    try:
        import exeicon
        exeicon.auto_apply_later()
    except Exception as e:
        core.log(f"exeicon: {e}")
    for msg in (launcher_info or {}).get("messages", []):
        QTimer.singleShot(800, lambda m=msg: QMessageBox.information(win, "更新", m))
    return app.exec()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
