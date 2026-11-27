"""Locating FFmpeg, probing files and detecting usable encoders."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # <ROOT>ppfm.py
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _find(name: str) -> str:
    exe = name + (".exe" if sys.platform == "win32" else "")
    for p in (ROOT / "tools" / "ffmpeg" / exe, ROOT / exe):
        if p.exists():
            return str(p)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"找不到 {name}，請把 ffmpeg 放到 tools/ffmpeg/ 或加入 PATH")
    return found


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


def run(args, **kw):
    # stdin=DEVNULL: in the windowed EXE there is no console, and ffmpeg would wait for keyboard input
    return subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, **kw)


def probe(path: str) -> dict:
    r = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", path], timeout=90)    # a sleeping / failing disk must not hang a job forever
    if r.returncode:
        raise RuntimeError(f"ffprobe 失敗: {path}\n{r.stderr.decode(errors='replace')}")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fmt = info["format"]
    tags = fmt.get("tags") or {}
    out = {"duration": float(fmt.get("duration") or 0), "audio": None, "size": int(fmt.get("size") or 0),
           "creation_time": tags.get("creation_time"), "comment": tags.get("comment", "")}
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        fps_num, fps_den = int(num), int(den) or 1
        if fps_num == 0:
            num, den = v.get("r_frame_rate", "30/1").split("/")
            fps_num, fps_den = int(num), int(den) or 1
        out.update(width=int(v["width"]), height=int(v["height"]), codec=v.get("codec_name"),
                   fps=fps_num / fps_den, fps_str=f"{fps_num}/{fps_den}",
                   pix_fmt=v.get("pix_fmt"),
                   # GoPro writes yuvj420p (full-range samples: Y goes 0..255) but tags the stream
                   # "tv": the pixel format is what the data really is
                   color_range="pc" if str(v.get("pix_fmt") or "").startswith("yuvj") else v.get("color_range"),
                   nb_frames=int(v.get("nb_frames") or 0))
    if a:
        out["audio"] = {"codec": a.get("codec_name"), "channels": int(a.get("channels") or 0),
                        "rate": int(a.get("sample_rate") or 0)}
    return out


def version() -> str:
    return run([FFMPEG, "-hide_banner", "-version"]).stdout.decode(errors="replace").splitlines()[0]


@lru_cache(maxsize=1)
def available_encoders() -> dict:
    """Actually try each HEVC/H.264 encoder on a tiny clip (drivers can be too old)."""
    cands = {
        "libx265": ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=none"],
        "hevc_nvenc": ["-c:v", "hevc_nvenc", "-pix_fmt", "p010le", "-profile:v", "main10"],
        "hevc_qsv": ["-c:v", "hevc_qsv", "-pix_fmt", "p010le"],
        "hevc_amf": ["-c:v", "hevc_amf"],
        "libx264": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        "h264_nvenc": ["-c:v", "h264_nvenc"],
    }
    res = {}
    for name, args in cands.items():
        r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "testsrc2=s=1024x512:d=0.2", *args, "-f", "null", "-"], timeout=60)
        err = r.stderr.decode(errors="replace").strip()
        res[name] = {"ok": r.returncode == 0, "error": err.splitlines()[0] if (r.returncode and err) else ""}
        if name == "hevc_nvenc" and r.returncode and "driver" in err.lower():
            res[name]["error"] = "NVIDIA 驅動版本太舊，請更新顯示卡驅動後即可使用 GPU 編碼"
    return res
