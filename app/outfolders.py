"""成品資料夾清單: the 成品 page shows the current output folder plus any number of folders
the user added (old 360 videos). Removing a folder only takes it off this list; nothing on
the disk is touched. Saved in the settings as "outs_folders"."""
from __future__ import annotations

import os
from pathlib import Path

import core


def current() -> Path | None:
    try:
        from PySide6.QtWidgets import QApplication
        w = getattr(QApplication.instance(), "_fs_window", None)
        f = getattr(w, "folder", None) if w else None
    except Exception:
        f = None
    f = f or core.cfg("last_folder")
    if not f and not (core.cfg("out_folder_mode") == "custom" and core.cfg("out_folder")):
        return None
    import naming
    return naming.output_folder(f or "")


def saved() -> list[str]:
    return [str(x) for x in (core.cfg("outs_folders") or []) if x]


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def all_() -> list[Path]:
    """Current output folder first, then the added ones (no duplicates)."""
    out, seen = [], set()
    c = current()
    for p in ([str(c)] if c else []) + saved():
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append(Path(p))
    return out


def add(path: str) -> dict:
    if not path or not os.path.isdir(path):
        return {"ok": False, "message": "找不到這個資料夾"}
    s = saved()
    if any(_key(x) == _key(path) for x in s) or (current() and _key(current()) == _key(path)):
        return {"ok": True, "message": "清單裡已經有這個資料夾"}
    s.append(path)
    core.set_cfg("outs_folders", s)
    return {"ok": True, "message": f"已加入：{path}"}


def remove(path: str) -> dict:
    s = saved()
    n = [x for x in s if _key(x) != _key(path)]
    if len(n) == len(s):
        return {"ok": False, "message": "這個資料夾不在清單裡（目前的成品資料夾不能移除）"}
    core.set_cfg("outs_folders", n)
    return {"ok": True, "message": "已從清單移除（電腦上的資料夾和影片都沒有刪除）"}


def token(p: Path, folders: list[Path] | None = None) -> str:
    """'<folder index>|<file name>' (index in all_())."""
    fs = folders if folders is not None else all_()
    k = _key(p.parent)
    for i, f in enumerate(fs):
        if _key(f) == k:
            return f"{i}|{p.name}"
    return p.name


def resolve(tok: str) -> Path | None:
    """File for a token ('i|name', or a bare name = current output folder). Only files directly
    inside a listed folder."""
    if not tok:
        return None
    idx, _, name = tok.partition("|") if "|" in tok else ("0", "", tok)
    if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        return None
    fs = all_()
    try:
        f = fs[int(idx)]
    except (ValueError, IndexError):
        return None
    p = f / name
    return p if p.is_file() else None
