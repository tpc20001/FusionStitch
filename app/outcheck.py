"""🧪 成品健檢.

健檢：掃成品資料夾的 mp4，找出
  zero   0 位元組               → 移到「_問題檔」
  bad    打不開（ffprobe 讀不到） → 移到「_問題檔」
  len    長度不對（影像比檔案短 2 秒以上，通常是沒轉完） → 移到「_問題檔」
  no360  沒有 360 資訊          → 一鍵補（不重新轉檔：跟補色彩標記同一套）
  color  沒有色彩標記           → 一鍵補
移動不是刪除：檔案搬到同一個資料夾裡的「_問題檔」子資料夾，自己確認後再處理。
"""
from __future__ import annotations

import json
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
