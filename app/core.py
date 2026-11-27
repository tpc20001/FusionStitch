r"""Paths, version, settings and logging shared by every module.

Layout (fixed folders, so updates never move data):
  <ROOT>\FusionStitch.exe   launcher (Python + PySide6 + libraries, rarely rebuilt)
  <ROOT>\app\*.py           program code (replaced by update packages, hot-reloadable)
  <ROOT>\UPDATE\*.zip       drop update packages here; applied on next launch
  <ROOT>\Package\           settings, calibration, queue (persistent)
  <ROOT>\LOG\               human-readable logs
  <ROOT>\app_backup\        automatic backup of app\ before every update
  <ROOT>\tools\ffmpeg\      ffmpeg.exe / ffprobe.exe
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

APP_NAME = "FusionStitch"
APP_VERSION = "2.34.3"
LAUNCHER_API = 1          # bump when an update needs a newer launcher (FusionStitch.exe)

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
# FUSIONSTITCH_PACKAGE=<folder> runs with separate settings/queue (used by the developer tests so a
# test window never picks up the real conversion queue)
PACKAGE_DIR = Path(os.environ["FUSIONSTITCH_PACKAGE"]) if os.environ.get("FUSIONSTITCH_PACKAGE") else ROOT / "Package"
LOG_DIR = ROOT / "LOG"
UPDATE_DIR = ROOT / "UPDATE"
BACKUP_DIR = ROOT / "app_backup"
for _d in (PACKAGE_DIR, LOG_DIR, UPDATE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = PACKAGE_DIR / "settings.json"
QUEUE_FILE = PACKAGE_DIR / "queue.json"

DEFAULTS = {
    "last_folder": "",
    "out_folder_mode": "beside",          # beside = <素材資料夾>\stitched ; custom = out_folder
    "out_folder": "",
    "name_pattern": "{date}_{time}_GP{num}_{preset}",
    "preset": "youtube",
    "projection": "equirect",
    "planet": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 270.0},
    "planet_size": "2160x2160",
    "flat": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 90.0},
    "flat_size": "1920x1080",
    "engine": "auto",
    "segment_seconds": 30,
    "spatial_audio": False,               # 360 全景＋WAV：輸出 4 聲道 ambiX＋SA3D
    "parallel_segments": 2,               # 一支影片同時轉幾段（GPU 轉檔）
    "parallel_jobs": 2,                   # 同時處理幾支影片（準備／收尾和另一支的 GPU 轉檔錯開）
    "gpu_jobs": 1,                        # 同時用 GPU 轉檔的影片數
    "cache2_max_gb": 50,                  # 中繼 SSD 2（備援）最多先存多少素材
    "cloud_on": True,                     # ☁ 自動檢查雲端更新（只提示）
    "cloud_kbps": 0,                      # ☁ 下載更新的速度上限（KB/s）；0＝不限速
    "pace_mode": "full",                  # 轉檔速度：full 全速 / manual 固定降速 / smart 智慧降速
    "pace_pct": 60,                       # 降速時的比例（%）
    "pace_idle_min": 3,                   # 智慧降速：幾分鐘沒碰鍵盤滑鼠就回到全速
    "default_view": "back",               # 預設正面：back＝後鏡頭朝前（相機反裝）、front＝前鏡頭
    "auto_align": True,                   # 轉檔前自動對齊（鏡頭還是出廠值的素材）
    "color_mode": "gopro",                # gopro＝原廠色彩（LUT，內建一張 Fusion 的）、auto＝自動調色＋白平衡
    "gopro_lut_strength": 1.0,
    "gopro_denoise": 0.3,                 # 原廠色彩 also smooths like Fusion Studio (0 = off)
    "gopro_sharpen": 0.7,                 # 原廠色彩 also sharpens like Fusion Studio (0 = off)
    "gopro_wb": 0.8,                      # 原廠白平衡: strength of the white balance drift correction (0 = off)
    "gopro_lens_exposure": True,          # 原廠鏡頭曝光: brightness of each lens from its recorded exposure
    "gopro_exposure": True,               # 原廠亮度: brighter daylight scenes like Fusion Studio (ISO / shutter)
    "preview_quality": "half",             # half | full
    "preview_audio": True,
    "perf_float_start": False,      # 效能浮窗 shown at start (off unless the user turns it on)
    "perf_float_opacity": 0.92,
    "perf_float_pos": None,
    "minimize_to_tray": True,
    "after_queue": "none",                 # none | sleep | shutdown
    "calibration": {},
    "custom_presets": {},
    "window_geometry": None,
    "nav_row": 0,
}

_lock = threading.RLock()
_cache: dict | None = None


def settings() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _cache = {**json.loads(json.dumps(DEFAULTS)), **data}
            if data and int(data.get("settings_rev", 0) or 0) < SETTINGS_REV:
                # V2.8.0: 原廠色彩 is the default now (a Fusion LUT ships with the program); switch once,
                # a later choice of 自動調色 stays
                if data.get("color_mode", "auto") == "auto":
                    _cache["color_mode"] = "gopro"
                _cache["settings_rev"] = SETTINGS_REV
                try:
                    save_settings()
                except OSError:
                    pass
        return _cache


SETTINGS_REV = 280
BUILTIN_LUT = APP_DIR / "gopro_fusion.cube"     # learned from GoPro Fusion Studio's renders (V2.8.0)


def gopro_lut_path() -> str:
    """原廠色彩 LUT: the one computed on this computer (設定 → 計算原廠色彩), else the built-in one."""
    own = str(cfg("gopro_lut") or "")
    if own and os.path.exists(own):
        return own
    return str(BUILTIN_LUT) if BUILTIN_LUT.exists() else ""


def cfg(key, default=None):
    return settings().get(key, DEFAULTS.get(key, default))


def set_cfg(key, value, save=True):
    with _lock:
        settings()[key] = value
        if save:
            save_settings()


def save_settings():
    with _lock:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)


def log(msg: str, file: str = "app_log.txt"):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(LOG_DIR / file, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if sys.stdout and not getattr(sys, "frozen", False):
        try:
            sys.stdout.write(line)
        except Exception:
            pass


def version_tuple(v) -> tuple:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))) or (0,)


def fmt_dur(s) -> str:
    if s is None:
        return "—"
    s = max(0, int(s))
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def fmt_bytes(n) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024


def fsync_path(path) -> None:
    """Make sure a file's data is on the disk (a power cut right after must not leave it empty)."""
    try:
        fd = os.open(str(path), os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_text_safe(path, text: str) -> None:
    """Write a small state file so that after a power cut it is either the old or the new one."""
    from pathlib import Path as _P
    p = _P(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def place_file(src: str, dest: str, log_file: str = "job_log.txt") -> str:
    """Move src to dest (overwriting like before). When dest is locked (open in a player,
    being backed up...) the file is saved under a free name instead: name_2.mp4, name_3.mp4...
    Returns the path actually used. Works across drives."""
    import shutil
    from pathlib import Path as _P
    d = _P(dest)
    d.parent.mkdir(parents=True, exist_ok=True)
    cands = [d] + [d.with_name(f"{d.stem}_{k}{d.suffix}") for k in range(2, 100)]
    last_err = None
    for c in cands:
        if c != d and c.exists():
            continue
        try:
            os.replace(src, c)
        except PermissionError as e:              # target open somewhere: try the next name
            last_err = e
            continue
        except OSError:                           # another drive: copy then swap in
            tmp = c.with_name(c.name + ".moving")
            shutil.copyfile(src, tmp)
            fsync_path(tmp)
            if os.path.getsize(tmp) != os.path.getsize(src):      # never delete the original of a bad copy
                os.remove(tmp)
                raise OSError(f"複製不完整：{src}")
            try:
                os.replace(tmp, c)
            except PermissionError as e:
                os.remove(tmp)
                last_err = e
                continue
            os.remove(src)
        if c != d:
            log(f"{d.name} 正在被使用（例如播放器開著），改存成 {c.name}", log_file)
        return str(c)
    raise last_err or OSError(f"無法寫入 {dest}")

