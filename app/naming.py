"""Output folder and file names (shared by the UI and the automatic pipeline)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core import cfg


def created_parts(meta) -> tuple[str, str]:
    s = str((meta or {}).get("created") or "")
    try:
        d = datetime.fromisoformat(s.replace("Z", "").split(".")[0])
        return d.strftime("%Y%m%d"), d.strftime("%H%M%S")
    except ValueError:
        return "00000000", "000000"


def output_folder(scan_folder: str) -> Path:
    if cfg("out_folder_mode") == "custom" and cfg("out_folder"):
        return Path(cfg("out_folder"))
    return Path(scan_folder) / "stitched"


def output_name(c: dict, meta: dict, preset: str, projection: str, ext: str, speed: float = 1.0) -> str:
    d, t = created_parts(meta)
    if d == "00000000":                        # clip info not read yet: use the footage file's time
        try:
            import os
            src = (c.get("front") or [None])[0]
            if src:
                dt = datetime.fromtimestamp(os.path.getmtime(src))
                d, t = dt.strftime("%Y%m%d"), dt.strftime("%H%M%S")
        except (OSError, ValueError, TypeError):
            pass
    name = str(cfg("name_pattern") or "{date}_{time}_GP{num}_{preset}")
    name = name.replace("{date}", d).replace("{time}", t).replace("{num}", c["num"]).replace("{preset}", preset)
    if projection == "planet":
        name += "_planet"
    elif projection == "flat":
        name += "_flat"
    if speed != 1.0:
        name += f"_{speed:g}x"
    return name + "." + ext
