"""快速轉檔: the clean main page. Pick (or drag in) the footage folder, check where the videos go,
press 開始轉檔. Shows only what is converting / waiting now, with a steady 預計完成 time; the
history lives on the 成品 page.

The folder browser starts in the folder used last time and has 上一層 / 最近用過, because footage
is usually filed in the same place every time.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import core
import folders
from core import cfg, fmt_bytes, fmt_dur, set_cfg

CSS = """
QFrame#qcard{background:#141a22;border:1px solid #232b36;border-radius:14px}
QLabel#qstep{color:#4dc9ff;font-weight:900;font-size:11pt}
QLabel#qbig{font-size:16pt;font-weight:900}
QLabel#qpath{color:#e8ecf2;font-size:10.5pt;font-weight:700}
QFrame#qdrop{border:2px dashed #2f3b4a;border-radius:12px;background:#10151c}
QFrame#qdrop[hot="true"]{border:2px dashed #4dc9ff;background:#13202c}
QListWidget#qdirs{background:#10151c;border:1px solid #232b36;border-radius:10px;padding:4px}
QListWidget#qdirs::item{padding:5px 6px;border-radius:6px}
QListWidget#qdirs::item:selected{background:#263342}
QTableWidget{background:#10151c;border:1px solid #232b36;border-radius:10px;gridline-color:transparent}
QPushButton#qgo{background:#e51e63;border:0;border-radius:10px;font-weight:900;font-size:12pt;min-height:40px;padding:6px 22px;color:#fff}
QPushButton#qgo:disabled{background:#4a2433;color:#b98a99}
QProgressBar{background:#1c232d;border:0;border-radius:5px;height:10px;text-align:center}
QProgressBar::chunk{background:#2b8ad8;border-radius:5px}
"""


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName("qcard")
    l = QVBoxLayout(f); l.setContentsMargins(16, 12, 16, 14); l.setSpacing(8)
    t = QLabel(title); t.setObjectName("qstep"); l.addWidget(t)
    return f, l


class Drop(QFrame):
    def __init__(self, page):
        super().__init__(); self.page = page
        self.setObjectName("qdrop"); self.setAcceptDrops(True)
        l = QVBoxLayout(self); l.setContentsMargins(12, 10, 12, 10)
        t = QLabel("把 GoPro Fusion 素材資料夾（或其中的檔案）拖到這裡")
        t.setAlignment(Qt.AlignCenter); t.setObjectName("sub"); l.addWidget(t)

    def _hot(self, on):
        self.setProperty("hot", "true" if on else "false"); self.style().unpolish(self); self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); self._hot(True)

    def dragLeaveEvent(self, e):
        self._hot(False)

    def dropEvent(self, e):
        self._hot(False)
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                self.page.open_folder(p if os.path.isdir(p) else os.path.dirname(p))
                break


def advice(src: str) -> dict:
    """Where the output goes, as advice: {"tip", "level": ok / bad / info, "relay_on", "relay"}.
    Reading the footage from one HDD while writing the output to the same HDD is slow."""
    import disks
    import naming
    relay = cfg("cache_dir") or ""
    r = {"tip": "", "level": "info", "relay_on": bool(relay), "relay": "", "out": ""}
    if not src:
        return r
    out = str(naming.output_folder(src))
    r["out"] = out
    same, src_ssd = disks.same_disk(src, out), disks.kind(src) == "SSD"
    if same and not src_ssd:
        r["level"] = "bad"
        r["tip"] = (f"⚠ 成品和素材在同一顆硬碟（{disks.label(src)}），轉檔時同時讀寫會變慢。"
                    "建議改「固定的資料夾」，選另一顆硬碟。")
    elif same:
        r["tip"] = f"素材和成品都在 {disks.label(src)}：SSD 同時讀寫沒問題。"
    else:
        r["level"] = "ok"
        r["tip"] = f"✓ 素材 {disks.label(src)} → 成品 {disks.label(out)}：讀寫在不同硬碟，已錯開。"
    if relay:
        r["relay"] = f"中繼：{relay}" + ("" if disks.kind(relay) == "SSD" else "（⚠ 這個資料夾不在 SSD 上）")
    elif src_ssd:
        r["relay"] = "素材已經在 SSD 上，不需要開"
    else:
        ss = disks.ssd_drives()
        r["relay"] = "建議開啟（素材在傳統硬碟）：" + (f"可用 SSD {', '.join(ss)}" if ss else "需要一顆 SSD")
    return r


def set_relay(on: bool, path: str = "") -> str:
    """SSD 加速 on / off. Without a path: the last one, else a folder on the system SSD."""
    import disks
    if on:
        d = path or cfg("cache_dir_last") or ""
        if not d:
            ss = [x for x in disks.ssd_drives() if x.upper().startswith("C")] or disks.ssd_drives()
            d = os.path.join(ss[0], "FusionStitch中繼") if ss else ""
        if not d:
            return ""
        set_cfg("cache_dir", d); set_cfg("cache_dir_last", d)
    else:
        set_cfg("cache_dir", "")
    try:
        import staging
        staging.start()
    except Exception:
        pass
    return cfg("cache_dir") or ""


class QuickPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setStyleSheet(CSS)
        self.cur = ""
        self._eta_cache: dict = {}
        outer = QVBoxLayout(self); outer.setContentsMargins(18, 14, 18, 14); outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("快速轉檔"); t.setObjectName("qbig"); head.addWidget(t)
        s = QLabel("　選素材 → 確認輸出 → 開始轉檔"); s.setObjectName("sub"); head.addWidget(s, 1)
        b = QPushButton("歷史紀錄 / 成品 →"); b.clicked.connect(lambda: self.main.go(self.main.PAGE_OUTPUTS)); head.addWidget(b)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal); split.setChildrenCollapsible(False)
        # ---- ① footage folder
        c1, l1 = card("① 素材資料夾")
        self.path = QLabel("—"); self.path.setObjectName("qpath"); self.path.setWordWrap(True); l1.addWidget(self.path)
        row = QHBoxLayout()
        self.b_up = QPushButton("⬆ 上一層"); self.b_up.clicked.connect(self.up); row.addWidget(self.b_up)
        b = QPushButton("選擇資料夾…"); b.clicked.connect(self.pick); row.addWidget(b)
        self.recent = QComboBox(); self.recent.setMinimumWidth(120); self.recent.activated.connect(self._recent_chosen)
        row.addWidget(self.recent, 1)
        l1.addLayout(row)
        self.dirs = QListWidget(); self.dirs.setObjectName("qdirs")
        self.dirs.itemDoubleClicked.connect(lambda it: self.open_folder(it.data(Qt.UserRole)))
        self.dirs.itemChanged.connect(lambda *_: self._multi_count())
        l1.addWidget(self.dirs, 1)
        mr = QHBoxLayout()
        self.b_mall = QPushButton("全選"); self.b_mall.clicked.connect(lambda: self._multi_all(True)); mr.addWidget(self.b_mall)
        self.b_mnone = QPushButton("全不選"); self.b_mnone.clicked.connect(lambda: self._multi_all(False)); mr.addWidget(self.b_mnone)
        self.b_multi = QPushButton("📂 勾選的資料夾全部加入佇列"); self.b_multi.setEnabled(False)
        self.b_multi.setToolTip("每個資料夾裡（含子資料夾）還沒轉過的影片，用下面 ③ 的成品位置和輸出格式加入佇列；每個資料夾自己一批")
        self.b_multi.clicked.connect(self.queue_multi); mr.addWidget(self.b_multi, 1)
        l1.addLayout(mr)
        hint = QLabel("雙擊資料夾進入；勾選多個資料夾可以一次全部加入佇列"); hint.setObjectName("sub"); l1.addWidget(hint)
        l1.addWidget(Drop(self))
        split.addWidget(c1)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(12)
        # ---- ② clips
        c2, l2 = card("② 這個資料夾裡的影片")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "影片", "拍攝時間", "長度 / 大小", "狀態"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(lambda *_: self._count())
        from PySide6.QtCore import QSize
        self.table.setIconSize(QSize(150, 75))           # front | back frame at 5 s
        self.table.verticalHeader().setDefaultSectionSize(82)
        self.table.cellDoubleClicked.connect(self._big)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        self._probs: dict = {}
        self._extra: dict = {"clips": {}, "basis": ""}
        self._pair_timer = QTimer(self); self._pair_timer.setInterval(1000); self._pair_timer.timeout.connect(self._pairs)
        l2.addWidget(self.table, 1)
        self.empty = QLabel("這裡沒有 GoPro Fusion 影片。請在左邊選擇放素材的資料夾。")
        self.empty.setObjectName("sub"); self.empty.setAlignment(Qt.AlignCenter); l2.addWidget(self.empty)
        row = QHBoxLayout()
        b = QPushButton("全選未轉的"); b.clicked.connect(lambda: self._check_all(True)); row.addWidget(b)
        b = QPushButton("全不選"); b.clicked.connect(lambda: self._check_all(False)); row.addWidget(b)
        row.addStretch(1)
        l2.addLayout(row)
        rl.addWidget(c2, 3)
        # ---- ③ output
        c3, l3 = card("③ 轉好的影片放在")
        row = QHBoxLayout()
        self.out_mode = QComboBox()
        self.out_mode.addItem("素材資料夾裡的 stitched 子資料夾", "beside")
        self.out_mode.addItem("固定的資料夾", "custom")
        self.out_mode.activated.connect(self._out_changed); row.addWidget(self.out_mode)
        self.out_path = QLabel(""); self.out_path.setObjectName("qpath"); self.out_path.setWordWrap(True); row.addWidget(self.out_path, 1)
        self.b_out = QPushButton("選擇…"); self.b_out.clicked.connect(self.pick_out); row.addWidget(self.b_out)
        b = QPushButton("開啟"); b.clicked.connect(self.open_out); row.addWidget(b)
        l3.addLayout(row)
        self.out_tip = QLabel(""); self.out_tip.setWordWrap(True); l3.addWidget(self.out_tip)
        row = QHBoxLayout()
        self.ssd = QPushButton("SSD 加速：關"); self.ssd.setCheckable(True); self.ssd.clicked.connect(self._ssd_toggled)
        self.ssd.setToolTip("素材先一個一個複製到 SSD，在 SSD 上轉檔（同時複製下一個），轉好再背景搬到成品資料夾。"
                            "HDD 只做連續讀取，不會同時讀寫，轉檔更順。")
        row.addWidget(self.ssd)
        self.ssd_path = QLabel(""); self.ssd_path.setObjectName("sub"); self.ssd_path.setWordWrap(True); row.addWidget(self.ssd_path, 1)
        self.b_ssd = QPushButton("中繼資料夾…"); self.b_ssd.clicked.connect(self.pick_ssd); row.addWidget(self.b_ssd)
        l3.addLayout(row)
        self.est_lab = QLabel(""); self.est_lab.setWordWrap(True); self.est_lab.setTextFormat(Qt.RichText)
        self.est_lab.setStyleSheet("background:#0f1a24;border:1px solid #1e3346;border-radius:10px;padding:8px")
        self.est_lab.setVisible(False); l3.addWidget(self.est_lab)
        row = QHBoxLayout()
        lab = QLabel("輸出格式"); lab.setObjectName("sub"); row.addWidget(lab)
        self.preset = QComboBox(); self.preset.activated.connect(lambda *_: set_cfg("preset", self.preset.currentData()))
        row.addWidget(self.preset, 1)
        self.go_btn = QPushButton("開始轉檔"); self.go_btn.setObjectName("qgo"); self.go_btn.clicked.connect(self.start)
        row.addWidget(self.go_btn)
        l3.addLayout(row)
        rl.addWidget(c3)
        # ---- ④ now converting
        c4, l4 = card("④ 轉檔中")
        self.jobs = QVBoxLayout(); self.jobs.setSpacing(6); l4.addLayout(self.jobs)
        self.idle = QLabel("目前沒有轉檔工作"); self.idle.setObjectName("sub"); l4.addWidget(self.idle)
        l4.addStretch(1)
        rl.addWidget(c4, 2)
        split.addWidget(right)
        split.setSizes([380, 900])
        outer.addWidget(split, 1)

        self._rows: dict = {}
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.main.bus.scanDone.connect(lambda *_: QTimer.singleShot(0, self.fill))
        self.main.bus.clipInfo.connect(lambda *_: self._meta_later())
        self._meta_timer = QTimer(self); self._meta_timer.setSingleShot(True); self._meta_timer.timeout.connect(self.fill)

    # ---------------------------------------------------------------- folder
    def showEvent(self, e):
        super().showEvent(e)
        self._fill_presets()
        self._fill_out()
        start = self.main.folder or folders.last("footage")
        if start and start != self.cur:
            self._browse(start)
        self.fill()

    def _fill_presets(self):
        import jobs
        self.preset.clear()
        cur = cfg("preset") or "youtube"
        for k, p in jobs.all_presets().items():
            self.preset.addItem(p.get("label", k), k)
            if k == cur:
                self.preset.setCurrentIndex(self.preset.count() - 1)

    def _fill_recent(self):
        self.recent.blockSignals(True)
        self.recent.clear()
        self.recent.addItem("最近用過的資料夾…", "")
        for r in folders.recent():
            self.recent.addItem(r, r)
        self.recent.blockSignals(False)

    def _recent_chosen(self, i):
        p = self.recent.itemData(i)
        if p:
            self.open_folder(p)

    def _browse(self, path):
        """Show path and its sub-folders (no scan)."""
        self.cur = path
        self.path.setText(path)
        self.b_up.setEnabled(bool(os.path.dirname(path.rstrip("\\/"))) and os.path.dirname(path.rstrip("\\/")) != path)
        self.dirs.clear()
        try:
            subs = sorted((e.path for e in os.scandir(path) if e.is_dir() and not e.name.startswith(("$", "."))
                           and e.name.lower() != "stitched"), key=str.lower)
        except OSError:
            subs = []
        self.dirs.blockSignals(True)
        for s in subs:
            it = QListWidgetItem("📁  " + os.path.basename(s)); it.setData(Qt.UserRole, s)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Unchecked); self.dirs.addItem(it)
        self.dirs.blockSignals(False)
        self._multi_count()
        self._fill_recent()

    # ---------------------------------------------------------------- 📂 複選資料夾
    def _multi_sel(self) -> list[str]:
        return [self.dirs.item(i).data(Qt.UserRole) for i in range(self.dirs.count())
                if self.dirs.item(i).checkState() == Qt.Checked]

    def _multi_count(self):
        n = len(self._multi_sel())
        self.b_multi.setEnabled(n > 0)
        self.b_multi.setText(f"📂 勾選的 {n} 個資料夾全部加入佇列" if n else "📂 勾選資料夾 → 一次加入佇列")

    def _multi_all(self, on):
        self.dirs.blockSignals(True)
        for i in range(self.dirs.count()):
            self.dirs.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.dirs.blockSignals(False)
        self._multi_count()

    def queue_multi(self):
        sel = self._multi_sel()
        if not sel:
            return
        from PySide6.QtWidgets import QApplication, QMessageBox
        preset = self.preset.currentData()
        self.b_multi.setEnabled(False)

        def prog(t):
            self.b_multi.setText("加入中… " + t)
            QApplication.processEvents()
        try:
            res = self.main.queue_folders(sel, preset, prog)
        finally:
            self._multi_all(False)
        add = sum(a for a, _ in res.values())
        lines = [f"{os.path.basename(f)}：加入 {a} 支" + (f"（略過 {k} 支：已轉過／在佇列／不完整）" if k else "") for f, (a, k) in res.items()]
        try:
            self.main.library.refresh_status()
        except Exception:
            pass
        self.main.statusBar().showMessage(f"📂 {len(sel)} 個資料夾，共加入 {add} 個轉檔工作", 8000)
        QMessageBox.information(self, "📂 加入佇列", f"共加入 {add} 個轉檔工作\n\n" + "\n".join(lines))
        QTimer.singleShot(300, self.fill)

    def open_folder(self, path):
        path = os.path.normpath(path)
        if not os.path.isdir(path):
            return
        self._browse(path)
        import clips as clips_mod
        if not clips_mod.has_clips(path):        # 上層資料夾（例 DASHCAM）：只列出子資料夾，不讀全部影片（會卡）
            self._bo = path
            self.table.setRowCount(0); self._rows = {}
            self.empty.setText("這一層沒有直接放影片：雙擊進入有影片的資料夾，\n或在左邊勾選資料夾，按「📂 勾選的資料夾全部加入佇列」（勾了才會讀）")
            self.empty.setVisible(True); self.table.setVisible(False)
            try:
                self._count()
            except Exception:
                pass
            return
        self._bo = None
        self.empty.setText("這裡沒有 GoPro Fusion 影片。請在左邊選擇放素材的資料夾。")
        folders.remember("footage", path)
        self.main.library.scan(path)            # sets the program's folder (成品, 素材庫 follow)
        self._fill_out()

    def up(self):
        p = os.path.dirname(self.cur.rstrip("\\/"))
        if p and p != self.cur:
            self.open_folder(p if not p.endswith(":") else p + "\\")

    def pick(self):
        d = folders.pick_dir(self, "選擇素材資料夾", "footage", self.cur)
        if d:
            self.open_folder(d)

    # ---------------------------------------------------------------- clips
    def _meta_later(self):
        self._meta_timer.start(400)

    def fill(self):
        if getattr(self, "_bo", None) and os.path.normcase(self._bo) == os.path.normcase(self.cur or ""):
            return                                # 只在瀏覽上層資料夾：不顯示舊資料夾的影片
        if self.main.folder and os.path.normcase(self.main.folder) != os.path.normcase(self.cur or ""):
            self._browse(self.main.folder)
        clips = [c for c in (self.main.clips or {}).values() if c.get("kind") == "video"]
        clips.sort(key=lambda c: c.get("num", ""))
        checked = {cid for cid, r in self._rows.items() if self.table.item(r, 0) and
                   self.table.item(r, 0).checkState() == Qt.Checked}
        first = not self._rows or set(self._rows) != {c["id"] for c in clips}
        import problems
        try:
            import webpc
            self._extra = webpc.clip_extras([c["id"] for c in clips], cfg("preset"))
        except Exception as e:
            core.log(f"quick extras: {e}")
            self._extra = {"clips": {}, "basis": ""}
        self._probs = {}
        for c in clips:
            pr = problems.check(c, (self.main.meta or {}).get(c["id"]) or {})
            if pr:
                self._probs[c["id"]] = pr
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._rows = {}
        for c in clips:
            r = self.table.rowCount(); self.table.insertRow(r)
            self._rows[c["id"]] = r
            meta = (self.main.meta or {}).get(c["id"]) or {}
            try:
                st = self.main.library.clip_status(c)
            except Exception:
                st = ""
            dn = (self._extra["clips"].get(c["id"]) or {}).get("done")
            if not st and dn:
                st = "✓ 已轉過"
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            pr = self._probs.get(c["id"])
            on = (st == "" and c["complete"] and not pr) if first else (c["id"] in checked and not pr)
            chk.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.table.setItem(r, 0, chk)
            ni = QTableWidgetItem(c.get("name", ""))
            ni.setToolTip("左：前鏡頭　右：後鏡頭（第 5 秒）・雙擊放大")
            self.table.setItem(r, 1, ni)
            from naming import created_parts
            d, t = created_parts(meta)
            self.table.setItem(r, 2, QTableWidgetItem(f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}" if d != "00000000" else "…"))
            dur = meta.get("duration")
            est = (self._extra["clips"].get(c["id"]) or {}).get("est") or 0
            self.table.setItem(r, 3, QTableWidgetItem((f"{fmt_dur(dur)}　{fmt_bytes(meta.get('size') or 0)}"
                                                       + (f" → 約 {fmt_bytes(est)}" if est else "")) if dur else "讀取中…"))
            if pr:
                si = QTableWidgetItem(f"⚠ {pr['label']}")
                si.setToolTip(f"{pr['label']}：{pr.get('detail', '')}\n右鍵 →「移回清單」或「重新讀取」")
                si.setForeground(QColor("#ffb347"))
                self.table.item(r, 1).setForeground(QColor("#ffb347"))
            else:
                si = QTableWidgetItem(st or ("未轉檔" if c["complete"] else "檔案不完整"))
                if dn and st == "✓ 已轉過":
                    si.setToolTip(f"以前轉過（{dn['how']}）：{dn['path']}\n預設不勾；要再轉一次就自己勾")
                si.setForeground(Qt.green if st in ("已轉檔", "✓ 已轉過") else (Qt.yellow if st else Qt.white))
            self.table.setItem(r, 4, si)
        self.table.blockSignals(False)
        self._pairs(request=True)
        self.empty.setVisible(not clips)
        self.table.setVisible(bool(clips))
        self._count()
        self._fill_out()

    # ---------------------------------------------------------------- front/back frame per clip
    def _pair_clip(self, cid):
        c = (self.main.clips or {}).get(cid)
        if not c:
            return None
        meta = (self.main.meta or {}).get(cid) or {}
        return dict(c, duration=meta.get("duration"))

    def _pairs(self, request=False):
        import framepair
        from PySide6.QtGui import QIcon, QPixmap
        missing = 0
        for cid, r in self._rows.items():
            it = self.table.item(r, 1)
            if it is None or it.data(Qt.UserRole + 5):
                continue
            c = self._pair_clip(cid)
            if not c:
                continue
            p = framepair.ready(c)
            if p:
                pm = QPixmap(str(p))
                if not pm.isNull():
                    it.setIcon(QIcon(pm))
                    it.setData(Qt.UserRole + 5, str(p))
                    continue
            if framepair.failed(c):
                continue
            missing += 1
            if request:
                framepair.request(c)
        if missing and not self._pair_timer.isActive():
            self._pair_timer.start()
        elif not missing:
            self._pair_timer.stop()

    def _big(self, r, col):
        it = self.table.item(r, 1)
        p = it.data(Qt.UserRole + 5) if it else None
        if not p:
            return
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog, QVBoxLayout as VB
        d = QDialog(self); d.setWindowTitle(f"{it.text()}　左：前鏡頭　右：後鏡頭（第 5 秒）")
        d.setAttribute(Qt.WA_DeleteOnClose)
        lab = QLabel(); lab.setPixmap(QPixmap(p))
        lab.mousePressEvent = lambda e: d.close()
        v = VB(d); v.setContentsMargins(6, 6, 6, 6); v.addWidget(lab)
        d.show()

    def _check_all(self, on):
        self.table.blockSignals(True)
        for cid, r in self._rows.items():
            c = self.main.clips.get(cid) or {}
            st = self.table.item(r, 4).text() if self.table.item(r, 4) else ""
            self.table.item(r, 0).setCheckState(Qt.Checked if (on and c.get("complete") and st == "未轉檔"
                                                               and cid not in self._probs) else Qt.Unchecked)
        self.table.blockSignals(False)
        self._count()

    def _selected(self):
        return [cid for cid, r in self._rows.items() if self.table.item(r, 0).checkState() == Qt.Checked]

    def _count(self):
        n = len(self._selected())
        self.go_btn.setText(f"開始轉檔（{n} 個）" if n else "開始轉檔")
        self.go_btn.setEnabled(n > 0)
        self._estimate()

    def _estimate(self):
        ids = self._selected()
        ex = self._extra.get("clips", {})
        rows = [ex.get(i) or {} for i in ids]
        if not rows:
            self.est_lab.setVisible(False)
            return
        src = sum(r.get("src") or 0 for r in rows)
        mid = sum(r.get("est") or 0 for r in rows)
        lo = sum(r.get("est_lo") or 0 for r in rows)
        hi = sum(r.get("est_hi") or 0 for r in rows)
        need = hi * 1.1 + 2e9
        html = (f"<b>這批 {len(rows)} 支</b>：原檔 {fmt_bytes(src)} → 預估約 <b>{fmt_bytes(mid)}</b>"
                + (f"（約 {mid / src * 100:.0f}%）" if src else "")
                + f"<br>安全範圍 {fmt_bytes(lo)} – {fmt_bytes(hi)}，建議成品資料夾至少準備 <b>{fmt_bytes(need)}</b>"
                + f"<br><span style='color:#8994a4'>{self._extra.get('basis', '')}</span>")
        try:
            import disks
            import naming
            out = str(naming.output_folder(self.main.folder or "")) if (self.main.folder or cfg("out_folder_mode") == "custom") else ""
            d = disks.info(out) if out else None
        except Exception:
            d = None
        if d and d.get("total"):
            ok, tight = d["free"] >= need, d["free"] >= hi
            col = "#45d483" if ok else ("#ffb347" if tight else "#ff6577")
            kind = f"（{d['kind']}）" if d.get("kind") else ""
            html += (f"<br><span style='color:{col}'>{'✓' if ok else '⚠'} 成品位置 {d['drive']} {d.get('label') or ''}{kind}："
                     f"剩 {fmt_bytes(d['free'])}／共 {fmt_bytes(d['total'])}，"
                     + ("夠用" if ok else ("剛好夠，建議多留一點" if tight else "不夠，請換一顆硬碟或先清出空間")) + "</span>")
        self.est_lab.setText(html)
        self.est_lab.setVisible(True)

    def _row_menu(self, pt):
        from PySide6.QtWidgets import QMenu
        it = self.table.itemAt(pt)
        if it is None:
            return
        cid = next((k for k, r in self._rows.items() if r == it.row()), None)
        c = (self.main.clips or {}).get(cid)
        if not c:
            return
        import problems
        meta = (self.main.meta or {}).get(cid) or {}
        m = QMenu(self)
        if cid in self._probs:
            m.addAction("↩ 移回清單（當作沒問題）", lambda: (problems.release(c), self.fill()))
            m.addAction("↻ 重新讀取", lambda: (problems.recheck(dict(c, duration=meta.get("duration"))),
                                              QTimer.singleShot(2500, self.fill)))
        else:
            m.addAction("⚠ 移到問題檔案（先不轉）", lambda: (problems.mark(c), self.fill()))
        if self.table.item(it.row(), 1) and self.table.item(it.row(), 1).data(Qt.UserRole + 5):
            m.addAction("🔍 放大畫面", lambda: self._big(it.row(), 1))
        m.exec(self.table.viewport().mapToGlobal(pt))

    def start(self):
        ids = self._selected()
        n = sum(1 for cid in ids if self.main.queue_clip(cid, self.preset.currentData()))
        try:
            self.main.library.refresh_status()
        except Exception:
            pass
        self.main.statusBar().showMessage(f"已加入 {n} 個轉檔工作", 6000)
        QTimer.singleShot(300, self.fill)

    # ---------------------------------------------------------------- output
    def _fill_out(self):
        mode = cfg("out_folder_mode") or "beside"
        self.out_mode.setCurrentIndex(1 if mode == "custom" else 0)
        self.b_out.setEnabled(mode == "custom")
        if mode == "custom":
            self.out_path.setText(cfg("out_folder") or "（請選擇資料夾）")
        else:
            self.out_path.setText(str(Path(self.main.folder) / "stitched") if self.main.folder else "（先選素材資料夾）")
        self._advice()

    def _advice(self):
        ad = advice(self.main.folder or self.cur)
        self.out_tip.setText(ad["tip"])
        self.out_tip.setStyleSheet({"bad": "color:#ff6577;font-weight:800", "ok": "color:#45d483"}.get(ad["level"], "color:#8994a4"))
        on = ad["relay_on"]
        self.ssd.setChecked(on)
        self.ssd.setText("SSD 加速：開" if on else "SSD 加速：關")
        self.ssd.setStyleSheet("background:#1d5c3a;border:0;font-weight:800;color:#fff" if on else "")
        self.ssd_path.setText(ad["relay"])

    def _ssd_toggled(self, on):
        if on and not set_relay(True):
            d = folders.pick_dir(self, "選擇 SSD 中繼資料夾", "cache")
            if d:
                set_relay(True, d)
        elif not on:
            set_relay(False)
        self._advice()

    def pick_ssd(self):
        d = folders.pick_dir(self, "選擇 SSD 中繼資料夾", "cache", cfg("cache_dir") or cfg("cache_dir_last") or "")
        if d:
            set_relay(True, d)
            self._advice()

    def _out_changed(self, *_):
        mode = self.out_mode.currentData()
        if mode == "custom" and not cfg("out_folder"):
            d = folders.pick_dir(self, "選擇成品資料夾", "output")
            if not d:
                self.out_mode.setCurrentIndex(0)
                return
            set_cfg("out_folder", d)
        set_cfg("out_folder_mode", mode)
        self.main._out_cache = (0, None, [])
        self._fill_out()

    def pick_out(self):
        d = folders.pick_dir(self, "選擇成品資料夾", "output", cfg("out_folder") or "")
        if d:
            set_cfg("out_folder", d)
            set_cfg("out_folder_mode", "custom")
            self.main._out_cache = (0, None, [])
            self._fill_out()

    def open_out(self):
        import naming
        if not self.main.folder and cfg("out_folder_mode") != "custom":
            return
        p = naming.output_folder(self.main.folder or "")
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    # ---------------------------------------------------------------- jobs
    def _tick(self):
        if not self.isVisible():
            return
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        act = [j for j in (q.jobs if q else []) if j.status in ("running", "queued", "paused")]
        while self.jobs.count():
            w = self.jobs.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.idle.setVisible(not act)
        import history
        sched = history.schedule(act)
        for j in act:
            box = QFrame(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 2, 0, 2); bl.setSpacing(3)
            top = QHBoxLayout()
            name = QLabel(f"<b>{j.title}</b>　<span style='color:#8994a4'>{os.path.basename(j.output or '')}</span>")
            top.addWidget(name, 1)
            fin = sched.get(j.id)
            if j.status == "running":
                if getattr(j, "eta_at", None) and fin:
                    txt = f"預計 {datetime.fromtimestamp(fin).strftime('%H:%M')} 完成（剩 {fmt_dur(max(0, fin - time.time()))}）"
                else:
                    txt = "準備中（分析、對齊）…"
                info = QLabel(txt); info.setStyleSheet("color:#45d483;font-weight:800"); top.addWidget(info)
            else:
                txt = ("排隊中" + (f" · 預計 {datetime.fromtimestamp(fin).strftime('%H:%M')} 完成" if fin else "")) \
                    if j.status == "queued" else "已暫停"
                info = QLabel(txt); info.setObjectName("sub"); top.addWidget(info)
            bl.addLayout(top)
            if j.status == "running":
                bar = QProgressBar(); bar.setRange(0, 1000); bar.setValue(int(j.progress * 1000)); bar.setTextVisible(False)
                bar.setFixedHeight(10); bl.addWidget(bar)
                sub = QLabel(f"{j.progress * 100:.1f}%　{j.fps:.1f} 格/秒　{getattr(j, 'detail', '') or j.message}")
                sub.setObjectName("sub"); bl.addWidget(sub)
            self.jobs.addWidget(box)
