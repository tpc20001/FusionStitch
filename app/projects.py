"""Per-clip edits, saved automatically (like Insta360 Studio keeps every clip's edits).

Package\\projects.json:  "<folder>|<clip id>" -> {
    "params":  stitching overrides (calibration, horizon, back offset),
    "grade":   colour grading,
    "patch":   nadir patch,
    "in", "out": frames, "projection": "equirect"/"planet", "planet": view,
    "updated": time }
Batch conversion uses a clip's saved edits when it has them.
"""
from __future__ import annotations

import json
import os
import threading
import time

import core

FILE = core.PACKAGE_DIR / "projects.json"
PRESET_FILE = core.PACKAGE_DIR / "color_presets.json"
_lock = threading.RLock()
_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        try:
            _data = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _data = {}
    return _data


def _save():
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_load(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FILE)


def key(folder: str, clip_id: str) -> str:
    return f"{os.path.normcase(os.path.abspath(folder or ''))}|{clip_id}"


def get(folder: str, clip_id: str) -> dict:
    with _lock:
        return json.loads(json.dumps(_load().get(key(folder, clip_id), {})))


def put(folder: str, clip_id: str, data: dict):
    with _lock:
        d = dict(data)
        d["updated"] = time.time()
        _load()[key(folder, clip_id)] = d
        _save()


def has_edits(folder: str, clip_id: str) -> bool:
    return key(folder, clip_id) in _load()


def clear(folder: str, clip_id: str):
    with _lock:
        _load().pop(key(folder, clip_id), None)
        _save()


# ---- user colour presets ("我的濾鏡")
def user_presets() -> dict:
    try:
        return json.loads(PRESET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_user_preset(name: str, grade: dict):
    p = user_presets()
    p[name] = grade
    PRESET_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def delete_user_preset(name: str):
    p = user_presets()
    p.pop(name, None)
    PRESET_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
