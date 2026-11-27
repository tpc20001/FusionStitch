"""成品 page: every finished video / photo in the output folder, newest first, with a thumbnail,
length, size, resolution and a check (complete / short / damaged). "New" = finished since the
page was last looked at (shown as a badge in the side bar) - the morning view after a night run."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

import core
import ffm
import naming
from core import cfg, fmt_bytes, fmt_dur, set_cfg
from ui_common import THEME

EXTS = (".mp4", ".mov", ".mkv", ".jpg")
TW, TH = 320, 180
CACHE = core.PACKAGE_DIR / "thumb_cache"


def scan(folder: Path) -> list[Path]:
    if not folder or not folder.is_dir():
        return []
    out = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS
           and ".joining" not in p.name and ".tmp" not in p.name]
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _thumb_path(p: Path) -> Path:
    st = p.stat()
    return CACHE / ("out_" + hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()[:16] + ".jpg")


def make_thumb(p: Path, info: dict) -> Path | None:
    """One frame at 10% (360 videos: the 2:1 picture letterboxed into 16:9)."""
    tp = _thumb_path(p)
    if tp.exists():
        return tp
    CACHE.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".jpg":
        args = ["-i", str(p)]
    else:
        args = ["-ss", f"{max(0.0, float(info.get('duration') or 0) * 0.1):.2f}", "-i", str(p)]
    vf = f"scale={TW}:{TH}:force_original_aspect_ratio=decrease,pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2:color=0x0d1218"
    r = ffm.run([ffm.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, "-frames:v", "1", "-vf", vf,
                 "-q:v", "4", str(tp)])
    return tp if r.returncode == 0 and tp.exists() else None


def check(p: Path, info: dict):
    """True = complete, "short" = shorter than it should be, False = unreadable."""
    if p.suffix.lower() == ".jpg":
        return p.stat().st_size > 1000
    dur = float(info.get("duration") or 0)
    if dur <= 0.1 or not info.get("width"):
        return False
    m = re.search(r"expected=([\d.]+)s", info.get("comment") or "")
    if m and dur < float(m.group(1)) * 0.98 - 0.5:
        return "short"
    return True


class _Bus(QObject):
    ready = Signal(str, object)


class OutputsPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.bus = _Bus()
        self.bus.ready.connect(self._ready)
        self.info: dict[str, dict] = {}
        self.pool = ThreadPoolExecutor(2)
        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        t = QLabel("成品"); t.setObjectName("title"); top.addWidget(t)
        self.summary = QLabel(""); self.summary.setObjectName("sub"); top.addWidget(self.summary, 1)
        self.recent = QCheckBox("只看最近 24 小時"); self.recent.toggled.connect(self.refresh); top.addWidget(self.recent)
        self.fsel = QComboBox(); self.fsel.setMinimumWidth(220); self.fsel.activated.connect(lambda *_: self.refresh())
        self.fsel.setToolTip("看哪個成品資料夾（全部＝清單裡所有資料夾一起列出）"); top.addWidget(self.fsel)
        b = QPushButton("＋ 加入資料夾"); b.setToolTip("把放舊 360 影片的資料夾加入清單來瀏覽（不會改任何檔案）")
        b.clicked.connect(self.add_folder); top.addWidget(b)
        self.b_rm = QPushButton("從清單移除"); self.b_rm.setToolTip("只是不在這裡顯示，電腦上的資料夾和影片都不會刪除")
        self.b_rm.clicked.connect(self.remove_folder); top.addWidget(self.b_rm)
        b = QPushButton("🧪 成品健檢"); b.clicked.connect(self.out_check); top.addWidget(b)
        b.setToolTip("找出 0 位元組、打不開、長度不對、沒有 360 資訊、沒有色彩標記的成品；能補的補（不重新轉檔），壞的搬到「_問題檔」（不刪）")
        b = QPushButton("開啟成品資料夾"); b.clicked.connect(self.open_folder); top.addWidget(b)
        b = QPushButton("重新整理"); b.clicked.connect(self.refresh); top.addWidget(b)
        l.addLayout(top)
        self.where = QLabel(""); self.where.setObjectName("sub"); l.addWidget(self.where)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(TW, TH))
        self.list.setGridSize(QSize(TW + 24, TH + 104))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda it: self.play(it.data(Qt.UserRole)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        l.addWidget(self.list, 1)
        self.empty = QLabel("還沒有成品。轉檔完成的影片會出現在這裡（雙擊播放）。")
        self.empty.setObjectName("sub"); self.empty.setAlignment(Qt.AlignCenter); l.addWidget(self.empty)
        self._blank = self._placeholder()
        self._timer = QTimer(self); self._timer.timeout.connect(self._update_badge); self._timer.start(15000)
        QTimer.singleShot(3000, self._update_badge)

    # ---------------------------------------------------------------- data
    def folder(self) -> Path | None:
        f = getattr(self.main, "folder", None) or cfg("last_folder")
        return naming.output_folder(f) if f else None

    def _fill_fsel(self):
        import outfolders
        cur = self.fsel.currentData()
        self.fsel.blockSignals(True)
        self.fsel.clear()
        fl = outfolders.all_()
        self.fsel.addItem(f"全部（{len(fl)} 個資料夾）", "")
        c = outfolders.current()
        for f in fl:
            self.fsel.addItem(("📦 " if c and str(f) == str(c) else "🗂 ") + str(f), str(f))
        i = self.fsel.findData(cur)
        self.fsel.setCurrentIndex(i if i >= 0 else 0)
        self.fsel.blockSignals(False)
        sel = self.fsel.currentData()
        self.b_rm.setEnabled(bool(sel) and not (c and str(sel) == str(c)))

    def add_folder(self):
        import outfolders
        d = QFileDialog.getExistingDirectory(self, "加入成品資料夾（只是加入清單來瀏覽）", "")
        if d:
            r = outfolders.add(os.path.normpath(d))
            self._fill_fsel()
            i = self.fsel.findData(os.path.normpath(d))
            if i >= 0:
                self.fsel.setCurrentIndex(i)
            self.refresh()
            if hasattr(self.main, "toast"):
                self.main.toast(r.get("message", ""))

    def remove_folder(self):
        import outfolders
        sel = self.fsel.currentData()
        if not sel:
            return
        if QMessageBox.question(self, "從清單移除", f"把這個資料夾從成品清單移除？\n\n只是不在這裡顯示，電腦上的資料夾和影片都不會刪除。\n\n{sel}") != QMessageBox.Yes:
            return
        outfolders.remove(sel)
        self.fsel.setCurrentIndex(0)
        self.refresh()

    def out_check(self):
        """🧪 成品健檢 (same folders as 🎨)."""
        import outcheck
        import outfolders
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        sel = self.fsel.currentData()
        outcheck.scan_async([Path(sel)] if sel else outfolders.all_())
        dlg = QDialog(self); dlg.setWindowTitle("🧪 成品健檢"); dlg.resize(720, 500)
        v = QVBoxLayout(dlg)
        head = QLabel("檢查中…"); head.setWordWrap(True); v.addWidget(head)
        lst = QListWidget(); v.addWidget(lst, 1)
        note = QLabel("🔧 補＝不重新轉檔，補上 360 資訊和色彩標記；📦 移走＝搬到同資料夾的「_問題檔」，不會刪。")
        note.setObjectName("sub"); note.setWordWrap(True); v.addWidget(note)
        bb = QDialogButtonBox(); go = bb.addButton("處理勾選的", QDialogButtonBox.AcceptRole)
        bb.addButton("關閉", QDialogButtonBox.RejectRole).clicked.connect(dlg.reject); v.addWidget(bb)
        go.setEnabled(False)
        box = {}

        def poll():
            st = outcheck.status()
            if st["scanning"]:
                head.setText(f"檢查中 {st['done']}/{st['total']}…"); return
            if not box.get("shown") and st["found"] is not None:
                box["shown"] = True
                for x in st["found"]:
                    it = QListWidgetItem(f"{x['name']}　⚠ {x['txt']}　→ {'🔧 補' if x['fix'] else '📦 移走'}")
                    it.setData(Qt.UserRole, {"path": x["path"], "action": "fix" if x["fix"] else "move"})
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked); it.setToolTip(x["path"])
                    lst.addItem(it)
                head.setText(f"{st['total']} 支裡有 {len(st['found'])} 支有問題（預設全勾）" if st["found"] else f"{st['total']} 支都沒問題 👍")
                go.setEnabled(bool(st["found"]))
            f = st.get("fixing") or {}
            if box.get("run") and f:
                if f.get("busy"):
                    head.setText(f"補資訊中 {f['done']}/{f['total']}：{f['cur']}")
                else:
                    box["run"] = False
                    head.setText(f"完成：補好 {len(f.get('ok') or [])} 支" + (f"，失敗 {len(f['fail'])} 支：" + "；".join(f["fail"]) if f.get("fail") else ""))
                    self.refresh()
        tm = QTimer(dlg); tm.timeout.connect(poll); tm.start(500)

        def run():
            items = [lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
            r = outcheck.run_fix(items)
            head.setText(r.get("message", "")); go.setEnabled(False); lst.clear()
            box["run"] = any(i["action"] == "fix" for i in items)
            self.refresh()
        go.clicked.connect(run)
        dlg.exec()

    def files(self) -> list[Path]:
        import outfolders
        try:
            sel = self.fsel.currentData() if hasattr(self, "fsel") else ""
            folders = [Path(sel)] if sel else outfolders.all_()
            fs = []
            for f in folders:
                try:
                    fs += scan(f)
                except OSError:
                    pass
            mt = {}
            for p in fs:                                  # 每個檔案只讀一次時間（硬碟上幾百個檔案時差很多）
                try:
                    mt[p] = p.stat().st_mtime
                except OSError:
                    mt[p] = 0
            fs.sort(key=lambda p: mt[p], reverse=True)
        except OSError:
            return []
        if self.recent.isChecked():
            cut = time.time() - 86400
            fs = [p for p in fs if mt[p] >= cut]
        return fs

    def new_count(self) -> int:
        seen = float(cfg("outputs_seen", 0) or 0)
        try:
            return sum(1 for p in scan(self.folder()) if p.stat().st_mtime > seen)
        except OSError:
            return 0

    def _update_badge(self):
        it = getattr(self.main, "nav_items", {}).get(getattr(self.main, "PAGE_OUTPUTS", -1))
        if it is None:
            return
        n = 0 if self.isVisible() else self.new_count()
        it.setData(Qt.UserRole + 4, n if n else None)
        if self.isVisible():
            for i in range(self.list.count()):
                self._relabel(self.list.item(i))

    # ---------------------------------------------------------------- view
    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        set_cfg("outputs_seen", time.time())
        self._update_badge()

    def _placeholder(self) -> QIcon:
        pm = QPixmap(TW, TH); pm.fill(QColor(THEME["chart_bg"]))
        p = QPainter(pm); p.setPen(QColor(THEME["muted"])); p.drawText(pm.rect(), Qt.AlignCenter, "載入中…"); p.end()
        return QIcon(pm)

    def refresh(self, *_):
        seen = float(cfg("outputs_seen", 0) or 0)
        self._fill_fsel()
        fs = self.files()
        folder = self.fsel.currentData() or self.folder()
        self.where.setText((f"資料夾：{folder}" if self.fsel.currentData() else f"清單裡全部資料夾（目前的成品資料夾：{folder or '—'}）")
                           if folder or self.fsel.count() > 1 else "請先在素材庫選擇素材資料夾")
        self.list.clear()
        total, n_new, n_bad = 0, 0, 0
        for p in fs:
            st = p.stat()
            total += st.st_size
            new = st.st_mtime > seen
            n_new += new
            it = QListWidgetItem(self._blank, self._label(p, self.info.get(str(p)), new))
            it.setData(Qt.UserRole, str(p))
            it.setData(Qt.UserRole + 1, new)
            it.setToolTip(str(p))
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(it)
            key = str(p)
            cached = self.info.get(key)
            if cached and cached.get("sig") == (st.st_size, st.st_mtime) and cached.get("thumb"):
                it.setIcon(QIcon(cached["thumb"]))
                n_bad += cached.get("ok") is not True
            else:
                self.pool.submit(self._load, key, (st.st_size, st.st_mtime))
        self.empty.setVisible(not fs)
        self.list.setVisible(bool(fs))
        src_t = out_t = 0
        for p in fs:
            b = self._src_bytes(p)
            if b:
                src_t += b
                out_t += p.stat().st_size
        saved = (f"　｜原檔 {fmt_bytes(src_t)} → 成品 {fmt_bytes(out_t)}（{out_t / src_t * 100:.0f}%，共省下 {fmt_bytes(src_t - out_t)}）"
                 if src_t and out_t else "")
        self.summary.setText(f"　{len(fs)} 個，共 {fmt_bytes(total)}" + (f"，{n_new} 個是新的" if n_new else "") + saved)

    def _verdict(self, p: Path, info: dict):
        ok = info.get("ok")
        if ok is not True or p.suffix.lower() == ".jpg" or re.search(r"expected=", info.get("comment") or ""):
            return ok
        # no length tag (older versions / other tools): compare with the clip's own length, for
        # full-length 1x 360 outputs only (time-lapse, flat and planet outputs are allowed to differ)
        name = p.name
        if re.search(r"_\d+(\.\d+)?x\.", name) or "_flat" in name or "_planet" in name:
            return ok
        m = re.search(r"GP(\d{4})_", name)
        clips = getattr(self.main, "clips", {}) or {}
        meta = getattr(self.main, "meta", {}) or {}
        cid = next((k for k, c in clips.items() if m and c.get("num") == m.group(1)), None)
        clip_d = (meta.get(cid) or {}).get("duration") if cid else None
        if clip_d and float(info.get("duration") or 0) < float(clip_d) * 0.9:
            return "short"
        return ok

    def _src_bytes(self, p: Path) -> int:
        """Size of the original footage of an output: the history record, else the clip in the library."""
        import history
        r = history.for_output(str(p))
        if r and r.get("src"):
            return int(r["src"])
        if re.search(r"_\d+(\.\d+)?x\.", p.name) or p.suffix.lower() == ".jpg":
            return 0                                  # time-lapse / photo: not comparable
        m = re.search(r"GP(\d{4})_", p.name)
        if not m:
            return 0
        for c in (getattr(self.main, "clips", {}) or {}).values():
            if c.get("num") == m.group(1) and c.get("kind") == "video":
                return history.source_bytes(c)
        return 0

    def _label(self, p: Path, info: dict | None, new: bool) -> str:
        when = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m/%d %H:%M")
        head = ("● 新　" if new else "") + p.name
        if not info:
            return f"{head}\n{when}　{fmt_bytes(p.stat().st_size)}"
        ok = self._verdict(p, info)
        tag = "" if ok is True else ("⚠ 不完整　" if ok == "short" else "⚠ 無法讀取　")
        dims = f"{info.get('width', '?')}×{info.get('height', '?')}"
        length = "" if p.suffix.lower() == ".jpg" else fmt_dur(float(info.get("duration") or 0)) + "　"
        import history
        sv = history.saving_text(self._src_bytes(p), p.stat().st_size)
        return f"{head}\n{tag}{length}{dims}　{fmt_bytes(p.stat().st_size)}\n" + (sv + "\n" if sv else "") + f"完成 {when}"

    def _load(self, key: str, sig):
        p = Path(key)
        try:
            info = ffm.probe(key)
        except Exception:
            info = {}
        try:
            ok = check(p, info)
        except OSError:
            ok = False
        thumb = None
        if ok is not False:
            try:
                tp = make_thumb(p, info)
                thumb = str(tp) if tp else None
            except Exception:
                thumb = None
        self.bus.ready.emit(key, {**info, "ok": ok, "thumb": thumb, "sig": sig})

    def _ready(self, key: str, info: dict):
        self.info[key] = info
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == key:
                if info.get("thumb"):
                    it.setIcon(QIcon(info["thumb"]))
                self._relabel(it)
                break

    def _relabel(self, it):
        key = it.data(Qt.UserRole)
        info = self.info.get(key)
        if not info:
            return
        it.setText(self._label(Path(key), info, bool(it.data(Qt.UserRole + 1))))
        if self._verdict(Path(key), info) is not True:
            it.setForeground(QColor(THEME["warn"]))

    # ---------------------------------------------------------------- actions
    def play(self, path: str):
        if path and os.path.exists(path):
            os.startfile(path)

    def play360(self, path: str):
        from PySide6.QtWidgets import QMessageBox
        if not (path and os.path.exists(path)):
            return
        try:
            import eqplayer
            self._players = [d for d in getattr(self, "_players", []) if d.isVisible()]
            self._players.append(eqplayer.open_player(path, self.window()))
        except Exception as e:
            QMessageBox.warning(self, "360 播放", f"無法開啟：{e}")

    def open_folder(self):
        f = self.folder()
        if f and f.is_dir():
            os.startfile(str(f))

    def _menu(self, pt):
        it = self.list.itemAt(pt)
        if it is None:
            return
        p = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("🌐 360 播放（環景／小星球）", lambda: self.play360(p))
        m.addAction("▶ 用系統播放器播放", lambda: self.play(p))
        if hasattr(self.main, "requeue_output"):
            m.addAction("↻ 重新轉檔（同一段素材、同一個預設）",
                        lambda: self.main.toast("已加入重新轉檔" if self.main.requeue_output(p)
                                                else "找不到這個成品對應的素材（素材庫要先開啟同一個資料夾）"))
        m.addAction("在檔案總管中顯示", lambda: subprocess.Popen(["explorer", "/select,", p]))
        m.addAction("複製檔案路徑", lambda: __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.clipboard().setText(p))
        m.addSeparator()
        m.addAction("🗑 刪除這個成品…", lambda: self._delete(p))
        m.exec(self.list.mapToGlobal(pt))

    def _delete(self, p):
        from PySide6.QtWidgets import QMessageBox
        import outfolders
        cur = outfolders.current()
        if not cur or os.path.normcase(str(Path(p).parent)) != os.path.normcase(str(cur)):
            QMessageBox.information(self, "刪除成品", "加入清單的資料夾只能看，不會刪除裡面的檔案。")
            return
        if QMessageBox.question(self, "刪除成品", f"刪除這個成品？（原始素材不受影響）\n\n{os.path.basename(p)}") != QMessageBox.Yes:
            return
        try:
            os.remove(p)
        except OSError as e:
            QMessageBox.warning(self, "刪除成品", f"無法刪除（可能正在播放）：{e}")
            return
        self.refresh() if hasattr(self, "refresh") else None
