"""PC 轉檔佇列: one bar per batch (footage folder) above the job table.

Each bar: select box, ▾ collapse (hides that batch's rows in the table), title, done/total,
progress, time left, expected size, room left on the output drive, warning, spare badges and
⚙ settings (output folder + 2 spare folders, for one or several batches at once).
"""
from __future__ import annotations

import time

from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget)

import core
from core import fmt_bytes, fmt_dur

CSS = """
QFrame#bt{background:#141b24;border:1px solid #263342;border-radius:10px}
QFrame#bt[run="1"]{border-color:#2b6a9c}
QLabel#btt{font-weight:800;font-size:14px;color:#e8eef5}
QLabel#bsp{background:#1d5c3a;color:#bff5d6;border-radius:5px;padding:1px 6px;font-weight:800;font-size:11px}
QLabel#bwarn{background:#3a2a10;border:1px solid #6b4a14;color:#ffd18a;border-radius:7px;padding:4px 7px}
"""


class BatchDialog(QDialog):
    def __init__(self, parent, bs: list):
        super().__init__(parent)
        self.setWindowTitle("⚙ 批次設定")
        self.setMinimumWidth(620)
        self.bs = bs
        same = lambda k: all(b.get(k) == bs[0].get(k) for b in bs)
        self.vals = {"dest": bs[0].get("dest", "") if same("dest") else None,
                     "s0": (bs[0].get("spares") or ["", ""])[0] if same("spares") else "",
                     "s1": ((bs[0].get("spares") or ["", ""]) + [""])[1] if same("spares") else ""}
        self.changed = set()
        v = QVBoxLayout(self)
        t = QLabel("套用到：" + "、".join("📁 " + b["title"] for b in bs)); t.setWordWrap(True); v.addWidget(t)
        g = QGridLayout(); v.addLayout(g)
        self.labs = {}
        for r, (k, name) in enumerate((("dest", "成品資料夾"), ("s0", "備用 1"), ("s1", "備用 2"))):
            g.addWidget(QLabel(name), r, 0)
            lab = QLabel(); lab.setWordWrap(True); lab.setMinimumWidth(320); self.labs[k] = lab; g.addWidget(lab, r, 1)
            b = QPushButton("選擇…"); b.clicked.connect(lambda _, k=k: self.pick(k)); g.addWidget(b, r, 2)
            x = QPushButton("✕ 清除"); x.clicked.connect(lambda _, k=k: self.set(k, "")); g.addWidget(x, r, 3)
        g.setColumnStretch(1, 1)
        hint = QLabel("• 改成品資料夾：正在轉的那支不變，之後排隊的會存到新位置。\n"
                      "• 成品資料夾放不下時，自動改存到備用 1，再放不下存備用 2；都沒有空間才暫停。\n"
                      "• 新加入的批次會沿用上一批的備用資料夾。")
        hint.setObjectName("sub"); v.addWidget(hint)
        row = QHBoxLayout(); row.addStretch(1)
        ok = QPushButton("套用"); ok.setObjectName("blue"); ok.clicked.connect(self.accept); row.addWidget(ok)
        c = QPushButton("取消"); c.clicked.connect(self.reject); row.addWidget(c)
        v.addLayout(row)
        self._show()

    def _show(self):
        for k, lab in self.labs.items():
            val = self.vals[k]
            if k == "dest" and val is None:
                lab.setText("<span style='color:#8994a4'>（各批次不同，選了就全部改成同一個）</span>")
            elif val:
                lab.setText(val)
            else:
                lab.setText("<span style='color:#8994a4'>" + (f"預設：{self.bs[0].get('dest_dir', '')}" if k == "dest" else "未設定") + "</span>")

    def set(self, k, val):
        self.vals[k] = val
        self.changed.add("dest" if k == "dest" else "spares")
        self._show()

    def pick(self, k):
        start = self.vals.get(k) or (self.bs[0].get("dest_dir", "") if k == "dest" else "")
        d = QFileDialog.getExistingDirectory(self, "選擇資料夾", start)
        if d:
            import os
            self.set(k, os.path.normpath(d))


class BatchBar(QFrame):
    def __init__(self, panel, bid):
        super().__init__()
        self.panel, self.bid = panel, bid
        self.setObjectName("bt")
        v = QVBoxLayout(self); v.setContentsMargins(10, 6, 10, 6); v.setSpacing(3)
        top = QHBoxLayout()
        self.chk = QCheckBox(); self.chk.toggled.connect(lambda on: panel.select(bid, on)); top.addWidget(self.chk)
        self.tog = QPushButton("▾"); self.tog.setFixedWidth(30); self.tog.setToolTip("收起／展開這批的影片")
        self.tog.clicked.connect(lambda: panel.toggle(bid)); top.addWidget(self.tog)
        self.title = QLabel(""); self.title.setObjectName("btt"); top.addWidget(self.title)
        self.count = QLabel(""); self.count.setObjectName("sub"); top.addWidget(self.count)
        self.badges = QHBoxLayout(); top.addLayout(self.badges)
        top.addStretch(1)
        self.info = QLabel(""); self.info.setObjectName("sub"); top.addWidget(self.info)
        b = QPushButton("⚙ 設定"); b.clicked.connect(lambda: panel.settings([bid])); top.addWidget(b)
        self.b_pause = QPushButton("⏸ 整批暫停"); self.b_pause.clicked.connect(lambda: panel.act(bid, "pause")); top.addWidget(self.b_pause)
        self.b_resume = QPushButton("▶ 整批繼續"); self.b_resume.clicked.connect(lambda: panel.act(bid, "resume")); top.addWidget(self.b_resume)
        self.b_cancel = QPushButton("✕ 取消整批"); self.b_cancel.setStyleSheet("color:#ff6577")
        self.b_cancel.clicked.connect(lambda: panel.act(bid, "cancel")); top.addWidget(self.b_cancel)
        v.addLayout(top)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setFixedHeight(12); self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        self.line2 = QLabel(""); self.line2.setObjectName("sub"); self.line2.setWordWrap(True); v.addWidget(self.line2)
        self.warn = QLabel(""); self.warn.setObjectName("bwarn"); self.warn.setWordWrap(True); v.addWidget(self.warn)
        self._badge_key = None

    def update_from(self, b: dict, collapsed: bool, selected: bool):
        self.setProperty("run", "1" if b.get("running") else "0")
        self.style().unpolish(self); self.style().polish(self)
        self.tog.setText("▸" if collapsed else "▾")
        self.chk.blockSignals(True); self.chk.setChecked(selected); self.chk.blockSignals(False)
        self.title.setText("📁 " + b["title"])
        self.count.setText(f"{b['done']}/{b['n']} 完成" + (f" · {b['errors']} 失敗" if b.get("errors") else ""))
        self.bar.setValue(int(b["progress"] * 1000))
        if b.get("active"):
            if b.get("left") is not None:
                t = f"剩 {fmt_dur(b['left'])}{' 以上' if b.get('eta_partial') else ''} · 預計 {time.strftime('%m/%d %H:%M', time.localtime(b['finish']))} 完成"
            else:
                t = "完成時間估算中"
        else:
            t = "全部完成" if not b.get("errors") else "有失敗的影片"
        self.info.setText(f"{b['progress'] * 100:.1f}%　{t}")
        d = b.get("drive")
        s = f"→ {b.get('dest_dir', '')}　成品約 {fmt_bytes(b['size_total'])}"
        if b.get("active") and b.get("size_left", 0) > 1e8:
            s += f"（還要寫入 {fmt_bytes(b['size_left'])}）"
        if d:
            s += f"　{d['drive']} {d.get('kind') or ''} 剩 {fmt_bytes(max(0, d['free']))}"
            if b.get("active"):
                s += f"，全部轉完約剩 {fmt_bytes(d['after'])}" if d["after"] >= 0 else f"，全部轉完 <b style='color:#ff6577'>不足 {fmt_bytes(-d['after'])}</b>"
        self.line2.setText(s)
        self.b_pause.setVisible(bool(b.get("active")))
        self.b_resume.setVisible(bool(b.get("active") or b.get("errors")))
        self.b_cancel.setVisible(bool(b.get("active") or b.get("errors")))
        self.warn.setText(b.get("warn") or "")
        self.warn.setVisible(bool(b.get("warn")))
        key = tuple((x["drive"], x["kind"]) for x in b.get("spare_info") or [])
        if key != self._badge_key:
            self._badge_key = key
            while self.badges.count():
                w = self.badges.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for i, x in enumerate(b.get("spare_info") or []):
                lab = QLabel(f"備用{i + 1} {x['drive'].rstrip(chr(92))}{(' ' + x['kind']) if x.get('kind') else ''} ✓")
                lab.setObjectName("bsp"); lab.setToolTip(f"{x['path']}　剩 {fmt_bytes(x['free'])}")
                self.badges.addWidget(lab)


class BatchPanel(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setStyleSheet(CSS)
        self.collapsed: set = set(core.cfg("batch_collapsed") or [])
        self.expanded: set = set()
        self.sel: set = set()
        self.data: dict = {"batches": []}
        self._t = 0.0
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(4)
        head = QHBoxLayout()
        lab = QLabel("批次（依素材資料夾）"); lab.setObjectName("sub"); head.addWidget(lab)
        head.addStretch(1)
        self.bulk = QPushButton("⚙ 一起設定"); self.bulk.clicked.connect(lambda: self.settings(sorted(self.sel)))
        head.addWidget(self.bulk)
        b = QPushButton("全部收起"); b.clicked.connect(lambda: self.all_(True)); head.addWidget(b)
        b = QPushButton("全部展開"); b.clicked.connect(lambda: self.all_(False)); head.addWidget(b)
        outer.addLayout(head)
        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.area.setFrameShape(QFrame.NoFrame)
        self.area.setMaximumHeight(330)
        self.inner = QWidget(); self.lay = QVBoxLayout(self.inner); self.lay.setContentsMargins(0, 0, 0, 0); self.lay.setSpacing(6)
        self.lay.addStretch(1)
        self.area.setWidget(self.inner)
        outer.addWidget(self.area)
        self.bars: dict = {}

    # ---- state
    def is_collapsed(self, b: dict) -> bool:
        if b["id"] in self.collapsed:
            return True
        if b["id"] in self.expanded:
            return False
        return not b.get("active")            # finished batches start collapsed

    def toggle(self, bid):
        b = next((x for x in self.data["batches"] if x["id"] == bid), {"id": bid})
        if self.is_collapsed(b):
            self.collapsed.discard(bid); self.expanded.add(bid)
        else:
            self.expanded.discard(bid); self.collapsed.add(bid)
        core.set_cfg("batch_collapsed", sorted(self.collapsed)[-300:])
        self.apply(force=True)

    def all_(self, on):
        for b in self.data["batches"]:
            (self.collapsed.add if on else self.collapsed.discard)(b["id"])
            (self.expanded.discard if on else self.expanded.add)(b["id"])
        core.set_cfg("batch_collapsed", sorted(self.collapsed)[-300:])
        self.apply(force=True)

    def select(self, bid, on):
        (self.sel.add if on else self.sel.discard)(bid)
        self._bulk_text()

    def _bulk_text(self):
        self.bulk.setText(f"⚙ 一起設定（{len(self.sel)}）" if self.sel else "⚙ 一起設定")
        self.bulk.setEnabled(bool(self.sel))

    def act(self, bid, what):
        b = next((x for x in self.data["batches"] if x["id"] == bid), None)
        if not b:
            return
        n = b.get("active", 0) + b.get("errors", 0)
        q = {"cancel": f"取消「{b['title']}」整批？\n\n{n} 支還沒轉完的影片全部取消（正在轉的那支會停下，已轉好的部分刪除）。已完成的成品不受影響。",
             "pause": f"暫停「{b['title']}」整批？之後按「整批繼續」會從中斷處接著轉。",
             "resume": f"繼續「{b['title']}」整批？"}[what]
        if QMessageBox.question(self, "批次", q) != QMessageBox.Yes:
            return
        import batches
        r = batches.action([bid], what)
        try:
            self.page.main.toast(r.get("message", ""))
        except Exception:
            pass
        self.apply(force=True)

    def settings(self, ids):
        bs = [b for b in self.data["batches"] if b["id"] in ids]
        if not bs:
            return
        d = BatchDialog(self, bs)
        if d.exec() != QDialog.Accepted or not d.changed:
            return
        import webpc
        body = {"ids": ids}
        if "dest" in d.changed:
            body["dest"] = d.vals["dest"] or ""
        if "spares" in d.changed:
            body["spares"] = [d.vals["s0"] or "", d.vals["s1"] or ""]
        r = webpc.batch_set(body)
        if r.get("ok") is False:
            QMessageBox.warning(self, "批次設定", r.get("message", ""))
        else:
            try:
                self.page.main.toast(r.get("message", ""))
            except Exception:
                pass
        self.apply(force=True)

    # ---- refresh (called by the queue page; the numbers are recomputed every 3 s)
    def apply(self, force=False):
        if force or time.time() - self._t > 3:
            self._t = time.time()
            try:
                import batches
                self.data = batches.summary()
            except Exception as e:
                core.log(f"batch panel: {e}")
                self.data = {"batches": []}
        bs = [b for b in self.data["batches"] if b.get("n")]
        ids = [b["id"] for b in bs]
        if ids != list(self.bars):
            for w in self.bars.values():
                w.setParent(None); w.deleteLater()
            self.bars = {}
            for i, b in enumerate(bs):
                w = BatchBar(self, b["id"])
                self.lay.insertWidget(i, w)
                self.bars[b["id"]] = w
        self.sel &= set(ids)
        self._bulk_text()
        hide = set()
        for b in bs:
            col = self.is_collapsed(b)
            self.bars[b["id"]].update_from(b, col, b["id"] in self.sel)
            if col:
                hide.update(b["jobs"])
        self.setVisible(bool(bs))
        return hide
