"""Find GoPro Fusion front/back recordings in a folder tree and pair them.

Fusion writes each lens to its own SD card:
  front card: GPFR0012.MP4 (+ .LRV .THM), later chapters GF010012.MP4 ...
  back card:  GPBK0012.MP4 (+ .LRV .THM .WAV), later chapters GB010012.MP4 ...
  photos:     GF######.JPG / GB###### .JPG (or GPFR/GPBK + 4 digits)
Copy both cards anywhere under one folder; pairing is by clip number.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

VIDEO_RE = re.compile(r"^(GPFR|GPBK|GF|GB)(\d{2})?(\d{4})\.(MP4|LRV|THM|WAV)$", re.I)
PHOTO_RE = re.compile(r"^(GPFR|GPBK|GF|GB)(\d{4,6})\.JPG$", re.I)
MAX_DEPTH = 4


def _walk(root: Path):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - root_depth >= MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() != "stitched"]
        for f in filenames:
            yield Path(dirpath) / f


def has_clips(folder: str, limit: int = 3000) -> bool:
    """快速判斷：這個資料夾本身或下一層（例 100GFRNT／100GBACK）有沒有 GoPro Fusion 檔（不往更深找，不讀內容）."""
    n = 0
    try:
        with os.scandir(folder) as it:
            subs = []
            for e in it:
                n += 1
                if n > limit:
                    return False
                if e.is_file() and (VIDEO_RE.match(e.name) or PHOTO_RE.match(e.name)):
                    return True
                if e.is_dir() and not e.name.startswith((".", "$")) and e.name.lower() != "stitched":
                    subs.append(e.path)
        for sp in subs[:50]:
            try:
                with os.scandir(sp) as it2:
                    for k, e in enumerate(it2):
                        if k > limit:
                            break
                        if VIDEO_RE.match(e.name) or PHOTO_RE.match(e.name):
                            return True
            except OSError:
                pass
    except OSError:
        return False
    return False


def scan(folder: str) -> list[dict]:
    root = Path(folder)
    videos: dict[str, dict] = {}
    photos: dict[str, dict] = {}
    for p in _walk(root):
        name = p.name.upper()
        m = VIDEO_RE.match(name)
        if m:
            prefix, chap, num, ext = m.groups()
            side = "front" if prefix in ("GPFR", "GF") else "back"
            if prefix in ("GF", "GB") and chap is None:
                continue  # GF0012.MP4 is not a Fusion name
            chapter = 0 if prefix in ("GPFR", "GPBK") else int(chap)
            c = videos.setdefault(num, {"id": "V" + num, "kind": "video", "num": num,
                                        "front": {}, "back": {}, "lrv_front": None,
                                        "lrv_back": None, "thm": None, "wav": None,
                                        "wavs": {}})
            if ext == "MP4":
                c[side][chapter] = str(p)
            elif ext == "LRV" and chapter == 0:
                c["lrv_" + side] = str(p)
            elif ext == "THM" and (side == "front" or not c["thm"]):
                c["thm"] = str(p)
            elif ext == "WAV":
                c["wavs"][chapter] = str(p)
            continue
        m = PHOTO_RE.match(name)
        if m:
            prefix, num = m.groups()
            side = "front" if prefix in ("GPFR", "GF") else "back"
            c = photos.setdefault(num, {"id": "P" + num, "kind": "photo", "num": num,
                                        "front": {}, "back": {}})
            c[side][0] = str(p)

    out = []
    for c in list(videos.values()) + list(photos.values()):
        c["front"] = [c["front"][k] for k in sorted(c["front"])]
        chapters = sorted(c["back"])
        c["back"] = [c["back"][k] for k in chapters]
        if c["kind"] == "video":
            # one 4-channel WAV per chapter (GPBK0012.WAV, GB010012.WAV, ...); only
            # usable when every chapter has its own, so the audio lines up
            wavs = c.pop("wavs")
            c["wav"] = wavs.get(0) or next(iter(wavs.values()), None)
            c["wavs"] = [wavs[k] for k in chapters] if chapters and all(k in wavs for k in chapters) else []
        c["complete"] = bool(c["front"]) and bool(c["back"]) and len(c["front"]) == len(c["back"])
        c["name"] = ("影片 " if c["kind"] == "video" else "照片 ") + c["num"]
        out.append(c)
    out.sort(key=lambda c: (c["kind"], c["num"]))
    return out
