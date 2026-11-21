"""✓ 已轉過：這個片段以前轉過了嗎（快速轉檔頁用；只看、不改任何檔案）.

1. 轉檔紀錄（history）：同一個編號 GP<num>、原檔大小一樣，而且那個成品還在 → 轉過。
2. 成品資料夾（這次的輸出資料夾＋成品頁清單裡的資料夾）：有 <日期>_<時間>_GP<num>… 開頭的 mp4 → 轉過。
資料夾內容 30 秒更新一次，所以每次重新整理不會一直去讀硬碟。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_H = {"t": 0.0, "idx": {}}
_F = {}


def _hist() -> dict:
    now = time.time()
    if now - _H["t"] < 30:
        return _H["idx"]
    idx = {}
    try:
        import history
        for r in history.records():
            out, num, src = r.get("output"), str(r.get("num") or ""), r.get("src")
            if out and num and src:
                idx.setdefault((num, int(src)), []).append(out)
    except Exception:
        pass
    _H.update(t=now, idx=idx)
    return idx


def _names(folder: str) -> list[str]:
    now = time.time()
    c = _F.get(folder)
    if c and now - c[0] < 30:
        return c[1]
    try:
        ns = [p.name for p in Path(folder).glob("*.mp4") if ".tmp" not in p.name]
    except OSError:
        ns = []
    _F[folder] = (now, ns)
    return ns


def _prefix(c: dict, meta: dict) -> str | None:
    try:
        import naming
        n = naming.output_name(c, meta or {}, "\x00", "equirect", "mp4")
        p = n.split("\x00")[0]
        return p if "00000000" not in p and len(p) >= 6 else None
    except Exception:
        return None


def find(c: dict, meta: dict, src_bytes, folders: list[str]) -> dict | None:
    """{'path', 'how'} if this clip was converted before, else None."""
    num = str(c.get("num") or "")
    if num and src_bytes:
        for out in _hist().get((num, int(src_bytes)), []):
            if os.path.exists(out):
                return {"path": out, "how": "轉檔紀錄"}
    pre = _prefix(c, meta)
    if pre:
        for f in folders:
            if not f:
                continue
            for n in _names(f):
                if n.startswith(pre):
                    return {"path": os.path.join(f, n), "how": "成品資料夾"}
    return None


def folders_for(out_dir: str) -> list[str]:
    fs = [out_dir] if out_dir else []
    try:
        import outfolders
        fs += [str(x) for x in outfolders.all_()]
    except Exception:
        pass
    seen, res = set(), []
    for f in fs:
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen:
            seen.add(k)
            res.append(f)
    return res
