"""問題檔案: clips that should not go into a batch until looked at.

Found automatically (lens file missing, no picture can be decoded, length unreadable) or
moved there by hand. Nothing is moved on disk: the clip is only listed apart and left out of
"select all". Hand marks and "it is fine after all" overrides are kept in the settings.
"""
from __future__ import annotations

import os

import core

CATS = {
    "missing": "缺少鏡頭檔",
    "picture": "讀不到畫面",
    "length": "讀不到長度",
    "manual": "手動移入",
}


def key(clip: dict) -> str:
    files = clip.get("front") or clip.get("back") or []
    return os.path.abspath(files[0]).lower() if files else str(clip.get("id", ""))


def _marks() -> dict:
    return dict(core.cfg("problem_marks") or {})


def _ignored() -> set:
    return set(core.cfg("problem_ignore") or [])


def check(clip: dict, meta: dict | None = None) -> dict | None:
    """{"cat", "label", "detail"} or None."""
    k = key(clip)
    m = _marks().get(k)
    if m is not None:
        return {"cat": "manual", "label": CATS["manual"], "detail": str(m or "")}
    if k in _ignored():
        return None
    if not clip.get("complete", True):
        nf, nb = len(clip.get("front") or []), len(clip.get("back") or [])
        why = "沒有前鏡頭檔" if not nf else ("沒有後鏡頭檔" if not nb else f"前鏡頭 {nf} 段、後鏡頭 {nb} 段，數量不同")
        return {"cat": "missing", "label": CATS["missing"], "detail": why}
    try:
        import framepair
        p = framepair.problem(dict(clip, duration=(meta or {}).get("duration")))
    except Exception:
        p = ""
    if p:
        return {"cat": "picture", "label": CATS["picture"], "detail": p}
    if meta and meta.get("error"):
        return {"cat": "length", "label": CATS["length"], "detail": "讀不到影片資訊：" + str(meta["error"])[-160:]}
    if meta and "duration" in meta and not meta.get("duration"):
        return {"cat": "length", "label": CATS["length"], "detail": "影片長度是 0（檔案可能沒寫完）"}
    return None


def mark(clip: dict, note: str = ""):
    m = _marks()
    m[key(clip)] = note or "手動移入"
    core.set_cfg("problem_marks", m)


def release(clip: dict):
    """Back to the normal list (for an automatic problem: 'treat as fine')."""
    k = key(clip)
    m = _marks()
    if k in m:
        m.pop(k)
        core.set_cfg("problem_marks", m)
        return
    ig = _ignored()
    ig.add(k)
    core.set_cfg("problem_ignore", sorted(ig)[-2000:])


def recheck(clip: dict):
    """Forget 'treat as fine' and try decoding the picture again."""
    k = key(clip)
    ig = _ignored()
    if k in ig:
        ig.discard(k)
        core.set_cfg("problem_ignore", sorted(ig))
    try:
        import framepair
        framepair.retry(clip)
    except Exception:
        pass
