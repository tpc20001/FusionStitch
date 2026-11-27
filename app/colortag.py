"""🧪 成品健檢的「補」：沒有色彩標記（V2.19.5 以前）或沒有 360 資訊的成品，原地補上（不重新轉檔）.
Outputs made before V2.19.5 carry no colour tags (color_space=unknown),
so YouTube and players have to guess and may pick BT.601. This writes the BT.709 / limited-range
tags into the file in place, WITHOUT re-encoding: the picture and the sound stay bit-identical.

Per file: copy all streams into <name>.colortag.tmp.mp4 with the tags set (bitstream + container),
put the 360 / spatial-audio metadata back, check duration, streams and size against the original,
then replace the original. If anything fails the original is left untouched.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import core
import ffm as ff

TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
_S = {"busy": False, "done": 0, "total": 0, "cur": "", "ok": [], "fail": [], "cancel": False}


def _probe(p) -> dict:
    r = subprocess.run([ff.FFPROBE, "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name,channels,color_space,color_primaries,color_transfer,color_range:format=duration",
                        "-of", "json", str(p)], capture_output=True, timeout=60, creationflags=ff.NO_WINDOW)
    return json.loads(r.stdout.decode("utf-8", "ignore") or "{}")


def fix(path: str) -> str:
    """Tag one file in place. Returns a short result; raises on failure (original untouched)."""
    import spherical
    p = Path(path)
    info = _probe(p)
    st = info.get("streams", [])
    v = next((s for s in st if s.get("codec_type") == "video"), None)
    if not v:
        raise OSError("沒有影像軌")
    bsf = {"hevc": "hevc_metadata", "h264": "h264_metadata"}.get(v.get("codec_name"))
    if not bsf:
        raise OSError(f"不支援的編碼：{v.get('codec_name')}")
    a = next((s for s in st if s.get("codec_type") == "audio"), None)
    spatial = 4 if a and int(a.get("channels") or 0) == 4 else 0
    dur0 = float((info.get("format") or {}).get("duration") or 0)
    tmp = p.with_name(p.stem + ".colortag.tmp.mp4")
    tmp.unlink(missing_ok=True)
    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(p),
           "-map", "0", "-c", "copy",
           "-bsf:v", f"{bsf}=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
           *TAGS, "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=ff.NO_WINDOW)
    try:
        if r.returncode != 0 or not tmp.exists():
            raise OSError("ffmpeg：" + r.stderr.decode("utf-8", "ignore")[-200:])
        spherical.inject_mp4(str(tmp), spatial_audio=spatial)          # 360 (and spatial audio) metadata back
        chk = _probe(tmp)
        v2 = next((s for s in chk.get("streams", []) if s.get("codec_type") == "video"), {})
        dur1 = float((chk.get("format") or {}).get("duration") or 0)
        if len(chk.get("streams", [])) != len(st):
            raise OSError("補完的檔案軌道數不一樣")
        if dur0 and abs(dur1 - dur0) > 0.5:
            raise OSError(f"補完的檔案長度不一樣（{dur0:.1f} → {dur1:.1f} 秒）")
        if abs(tmp.stat().st_size - p.stat().st_size) > max(5e6, p.stat().st_size * 0.01):
            raise OSError("補完的檔案大小差太多")
        if v2.get("color_space") != "bt709":
            raise OSError("標記沒有寫進去")
        mt = p.stat().st_mtime
        os.replace(tmp, p)                                               # (fails if the file is open in a player)
        os.utime(p, (time.time(), mt))                                   # keep the original date
    finally:
        tmp.unlink(missing_ok=True)
    core.log(f"colortag: {p.name} -> bt709", "job_log.txt")
    return "已補上"


def start(paths: list[str]) -> dict:
    if _S["busy"]:
        return {"ok": False, "message": "已經在補了，請稍候"}
    paths = [p for p in paths if p]
    if not paths:
        return {"ok": False, "message": "沒有需要補的影片"}
    _S.update(busy=True, done=0, total=len(paths), cur="", ok=[], fail=[], cancel=False)

    def run():
        try:
            for p in paths:
                if _S["cancel"]:
                    break
                _S["cur"] = Path(p).name
                try:
                    fix(p)
                    _S["ok"].append(Path(p).name)
                except Exception as e:
                    msg = str(e)
                    if isinstance(e, PermissionError) or "WinError 32" in msg or "being used" in msg:
                        msg = "檔案正在被播放器或其他程式開著，關掉後再補一次"
                    _S["fail"].append(f"{Path(p).name}：{msg}")
                    core.log(f"colortag failed {p}: {e}", "job_log.txt")
                _S["done"] += 1
        finally:
            _S["busy"] = False
            _S["cur"] = ""
    threading.Thread(target=run, daemon=True, name="colortag").start()
    return {"ok": True, "message": f"開始修補 {len(paths)} 支：補上 360 資訊和色彩標記（不重新轉檔，一支幾秒）"}




def status() -> dict:
    d = {k: (list(v) if isinstance(v, list) else v) for k, v in _S.items() if k != "cancel"}
    return d

