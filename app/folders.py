"""Remembered folders: every folder picker opens where it was last time, and the folders used
most recently are kept in a short list (設定 file, key "recent_folders")."""
from __future__ import annotations

import os

from core import cfg, set_cfg

MAX_RECENT = 12


def last(purpose: str, fallback: str = "") -> str:
    """The folder this picker used last time (or the nearest one that still exists)."""
    p = cfg(f"last_dir_{purpose}") or fallback or cfg("last_folder") or ""
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p.rstrip("\\/"))
        if not parent or parent == p:
            return ""
        p = parent
    return p


def remember(purpose: str, path: str):
    if not path:
        return
    set_cfg(f"last_dir_{purpose}", path)
    rec = [r for r in (cfg("recent_folders") or []) if os.path.normcase(r) != os.path.normcase(path)]
    set_cfg("recent_folders", ([path] + rec)[:MAX_RECENT])


def recent() -> list:
    return [r for r in (cfg("recent_folders") or []) if os.path.isdir(r)]


def pick_dir(parent, title: str, purpose: str, start: str = "") -> str:
    from PySide6.QtWidgets import QFileDialog
    d = QFileDialog.getExistingDirectory(parent, title, start or last(purpose))
    if d:
        d = os.path.normpath(d)
        remember(purpose, d)
    return d


def pick_file(parent, title: str, purpose: str, filt: str, start: str = "") -> str:
    from PySide6.QtWidgets import QFileDialog
    p, _ = QFileDialog.getOpenFileName(parent, title, start or last(purpose), filt)
    if p:
        p = os.path.normpath(p)
        set_cfg(f"last_dir_{purpose}", os.path.dirname(p))
    return p
