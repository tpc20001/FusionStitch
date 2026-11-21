"""🧪 成品健檢 + 🧪 全部轉完後（睡眠／關機）.

健檢：掃成品資料夾的 mp4，找出
  zero   0 位元組               → 移到「_問題檔」
  bad    打不開（ffprobe 讀不到） → 移到「_問題檔」
  len    長度不對（影像比檔案短 2 秒以上，通常是沒轉完） → 移到「_問題檔」
  no360  沒有 360 資訊          → 一鍵補（不重新轉檔：跟補色彩標記同一套）
  color  沒有色彩標記           → 一鍵補
移動不是刪除：檔案搬到同一個資料夾裡的「_問題檔」子資料夾，自己確認後再處理。

全部轉完後：cfg after_all = none／sleep／shutdown。佇列從「有工作」變成「全部做完（沒有在跑、排隊、暫停的）」→ 倒數 60 秒，
可以取消；做一次就自動回到「不動作」（不會下次又關機）。tick() 由握手的背景迴圈每 2 秒叫一次（熱更新也不斷）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import core
import ffm as ff

BAD_DIR = "_問題檔"
KIND = {"zero": "0 位元組", "bad": "打不開", "len": "長度不對（可能沒轉完）", "no360": "沒有 360 資訊", "color": "沒有色彩標記"}
FIXABLE = ("no360", "color")
_C = {"scanning": False, "done": 0, "total": 0, "found": None, "busy": False, "msg": ""}


def _probe(p) -> dict | None:
    try:
        r = subprocess.run([ff.FFPROBE, "-v", "error", "-show_entries",
                            "stream=codec_type,duration,color_space:stream_side_data=side_data_type:format=duration",
                            "-of", "json", str(p)], capture_output=True, timeout=60, creationflags=ff.NO_WINDOW)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    except Exception:
        return None


def check(p: Path) -> list[str]:
    try:
        if p.stat().st_size == 0:
            return ["zero"]
    except OSError:
        return ["bad"]
    d = _probe(p)
    if not d:
        return ["bad"]
    st = d.get("streams") or []
    v = next((s for s in st if s.get("codec_type") == "video"), None)
    if not v:
        return ["bad"]
    out = []
    try:
        fd, vd = float((d.get("format") or {}).get("duration") or 0), float(v.get("duration") or 0)
        if fd and vd and fd - vd > 2:
            out.append("len")
    except (TypeError, ValueError):
        pass
    sides = [x.get("side_data_type", "") for x in (v.get("side_data_list") or [])]
    if not any("Spherical" in x for x in sides):
        out.append("no360")
    if v.get("color_space") != "bt709":
        out.append("color")
    return out


def scan_async(folders) -> dict:
    if _C["scanning"]:
        return {"ok": True}
    files = []
    for f in folders:
        try:
            files += [p for p in sorted(Path(f).glob("*.mp4")) if ".tmp" not in p.name and ".joining" not in p.name]
        except OSError:
            pass
    _C.update(scanning=True, done=0, total=len(files), found=None)

    def run():
        res = []
        try:
            for p in files:
                k = check(p)
                if k:
                    res.append({"path": str(p), "name": p.name, "kinds": k, "txt": "、".join(KIND[x] for x in k),
                                "fix": all(x in FIXABLE for x in k)})
                _C["done"] += 1
        finally:
            _C.update(found=res, scanning=False)
    threading.Thread(target=run, daemon=True, name="outcheck").start()
    return {"ok": True, "message": f"開始健檢 {len(files)} 支"}


def run_fix(items: list[dict]) -> dict:
    """items: [{path, action: 'fix'|'move'}]."""
    if _C["busy"]:
        return {"ok": False, "message": "上一批還在處理"}
    fixes = [i["path"] for i in items if i.get("action") == "fix"]
    moves = [i["path"] for i in items if i.get("action") == "move"]
    n = 0
    for p in moves:
        try:
            src = Path(p)
            dst = src.parent / BAD_DIR
            dst.mkdir(exist_ok=True)
            tgt = dst / src.name
            if tgt.exists():
                tgt = dst / f"{src.stem}_{int(time.time())}{src.suffix}"
            shutil.move(str(src), str(tgt))
            n += 1
            core.log(f"outcheck: moved {src} -> {tgt}", "job_log.txt")
        except Exception as e:
            core.log(f"outcheck: move failed {p}: {e}", "job_log.txt")
    msg = f"移到「{BAD_DIR}」{n} 支" if moves else ""
    if fixes:
        import colortag
        r = colortag.start(fixes)          # remux + 360 資訊 + 色彩標記（不重新轉檔）
        msg = (msg + "，" if msg else "") + r.get("message", "")
    if _C.get("found"):
        done = set(moves) | set(fixes)
        _C["found"] = [x for x in _C["found"] if x["path"] not in done]
    return {"ok": True, "message": msg or "沒有要處理的"}


def status() -> dict:
    d = dict(_C)
    try:
        import colortag
        d["fixing"] = colortag.status()
    except Exception:
        d["fixing"] = None
    return d


# ---------------------------------------------------------------- 🧪 全部轉完後
_A = {"had": False, "armed": 0.0, "fired": False}
AFTER_TXT = {"none": "不動作", "sleep": "睡眠", "shutdown": "關機"}
COUNT = 60


def after_mode() -> str:
    m = str(core.cfg("after_all") or "none")
    return m if m in AFTER_TXT else "none"


def _queue_left() -> tuple[int, int]:
    try:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        js = list(getattr(q, "jobs", []) or [])
    except Exception:
        js = []
    left = [j for j in js if j.status in ("running", "queued", "paused", "waiting")]
    _A["failed"] = len([j for j in js if j.status in ("failed", "error")])
    return len(left), len(js)


def tick():
    m = after_mode()
    left, _ = _queue_left()
    if left:
        _A["had"] = True
        if _A["armed"]:
            _A["armed"] = 0.0                          # 又有新的工作 → 取消倒數
        return
    if m == "none" or not _A["had"]:
        _A["armed"] = 0.0
        return
    now = time.time()
    if not _A["armed"]:
        _A["armed"] = now
        core.log(f"after_all: queue done → {m} in {COUNT}s")
        return
    if now - _A["armed"] >= COUNT:
        _A.update(armed=0.0, had=False)
        core.set_cfg("after_all", "none")              # 只做一次
        core.log(f"after_all: {m} now")
        _do(m)


def _do(m: str):
    if os.name != "nt":
        return
    try:
        if m == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "10", "/c", "FusionStitch：全部轉完了，10 秒後關機（shutdown /a 可以取消）"],
                             creationflags=ff.NO_WINDOW)
        elif m == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=ff.NO_WINDOW)
    except Exception as e:
        core.log(f"after_all failed: {e}")


def after_set(mode: str | None = None, cancel: bool = False) -> dict:
    if cancel:
        _A.update(armed=0.0, had=False)
        core.set_cfg("after_all", "none")
        return {"ok": True, "message": "已取消：全部轉完後不動作", "after": after_status()}
    if mode in AFTER_TXT:
        core.set_cfg("after_all", mode)
        if mode != "none" and _queue_left()[0]:
            _A["had"] = True
        return {"ok": True, "message": f"全部轉完後：{AFTER_TXT[mode]}", "after": after_status()}
    return {"ok": False, "message": "不認得的選項"}


def after_status() -> dict:
    left, tot = _queue_left()
    cd = max(0, int(COUNT - (time.time() - _A["armed"]))) if _A["armed"] else None
    return {"mode": after_mode(), "txt": AFTER_TXT[after_mode()], "left": left, "countdown": cd, "failed": _A.get("failed", 0)}
