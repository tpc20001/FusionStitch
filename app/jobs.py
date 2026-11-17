"""Export engine: resumable, GPU-first conversion queue.

Video jobs are encoded in parts of N seconds (Package setting segment_seconds)
inside "<output>.parts\\". A finished part is never redone, so a crash, a
reboot, a pause or a hot update resumes where it stopped. When every part is
done they are joined without re-encoding, the audio is added and the 360
metadata is written.

Every process in the pipeline is watched: a decoder or encoder that dies, or
a pipeline that stops moving for STALL_SECONDS, ends the job with an error
message instead of hanging (the web version could hang at 5%).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

import numpy as np

import color
import core
import enhance
import ffm as ff
import geometry as geo
import media
import spherical
from imageio_util import imread, imwrite_jpg

STALL_SECONDS = 120

# ---------------------------------------------------------------- presets
PRESETS = {
    "archive": {"label": "典藏：H.265 10-bit 視覺無損", "codec": "hevc", "cq": 15, "x265_crf": 14,
                "nvenc": ["-preset", "p6", "-multipass", "qres", "-rc-lookahead", "20"],
                "desc": "畫質優先，肉眼看不出差異"},
    "youtube": {"label": "YouTube：H.265 10-bit 高畫質", "codec": "hevc", "cq": 18, "x265_crf": 17,
                "nvenc": ["-preset", "p5", "-rc-lookahead", "20"],
                "desc": "上傳用母帶，YouTube 會再壓縮"},
    "small": {"label": "精簡：H.265 10-bit 小檔", "codec": "hevc", "cq": 24, "x265_crf": 22,
              "nvenc": ["-preset", "p5", "-rc-lookahead", "20"], "desc": "分享、手機觀看"},
    "gopro4k": {"label": "GoPro 對標：H.265 4K 立體聲", "codec": "hevc", "cq": 22, "x265_crf": 21, "width": 3840,
                "nvenc": ["-preset", "p6", "-multipass", "qres", "-rc-lookahead", "20"],
                "no_stab": True, "stereo": True,
                "desc": "對應原廠 Fusion Studio：YouTube 4K（3840×1920）、Stereo、不防震；一次轉成 H.265＋360 資訊，可直接上傳 YouTube"},
    "compat": {"label": "相容：H.264 4K", "codec": "h264", "cq": 18, "x264_crf": 16, "max_width": 3840,
               "nvenc": ["-preset", "p6", "-rc-lookahead", "20"], "desc": "舊電腦、舊手機也能播放"},
}
EQUI_SIZES = [7680, 5760, 5376, 4096, 3840, 3072, 2048]
FLAT_SIZES = ["3840x2160", "2560x1440", "1920x1080", "2160x3840", "1080x1920", "2160x2160", "1080x1080",
              "2160x2700", "1080x1350"]
PLANET_SIZES = ["3840x3840", "2880x2880", "2160x2160", "1440x1440", "3840x2160", "1920x1080", "2160x3840", "1080x1920"]


def all_presets() -> dict:
    out = dict(PRESETS)
    for k, p in (core.cfg("custom_presets") or {}).items():
        base = dict(PRESETS.get(p.get("base", "youtube"), PRESETS["youtube"]))
        base.update(p)
        base["custom"] = True
        out[k] = base
    return out


def auto_width(src_h: int, params: dict) -> int:
    ppd = params["front"]["r"] * src_h / (params["fov"] / 2)
    want = ppd * 360
    for w in sorted(EQUI_SIZES):
        if w >= want * 0.97:
            return w
    return EQUI_SIZES[0]


def out_size(opts: dict, tl: media.Timeline, params: dict, preset: dict) -> tuple[int, int]:
    spec = opts.get("projection") or {"type": "equirect"}
    if spec.get("type") in ("planet", "flat"):
        default = core.cfg("planet_size") if spec["type"] == "planet" else core.cfg("flat_size")
        w, h = (int(x) for x in str(opts.get("size") or default).split("x"))
    else:
        w = int(opts.get("width") or 0) or auto_width(tl.height, params)
        if preset.get("max_width"):
            w = min(w, preset["max_width"])
        if preset.get("width"):
            w = int(preset["width"])
        h = w // 2
        return w - w % 16, h - h % 16
    return w - w % 2, h - h % 2          # flat / planet: exact size (1080 etc.), NVENC only needs even


_enc_probe: dict = {}


def encoders() -> dict:
    if not _enc_probe:
        _enc_probe.update(ff.available_encoders())
    return _enc_probe


def gpu_ok() -> bool:
    if "_gpu" not in _enc_probe:
        import gpu
        _enc_probe["_gpu"] = gpu.available()
    return _enc_probe["_gpu"][0]


def encoder_args(preset: dict, engine: str, fps: float) -> tuple[list[str], str, str]:
    """-> (ffmpeg args, encoder name, raw input pixel format for GPU stitching)."""
    enc = encoders()
    gop = ["-g", str(max(1, round(fps * 2)))]
    cq = str(preset.get("cq", 18))
    maxrate = ["-maxrate", f"{preset['maxrate']}M", "-bufsize", f"{preset['maxrate'] * 2}M"] if preset.get("maxrate") else []
    if preset["codec"] == "hevc":
        if engine != "cpu" and enc.get("hevc_nvenc", {}).get("ok"):
            return (["-c:v", "hevc_nvenc", *preset["nvenc"], "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
                     *maxrate, "-profile:v", "main10", "-pix_fmt", "p010le", "-spatial-aq", "1",
                     "-temporal-aq", "1", "-aq-strength", "8", "-bf", "3", "-b_ref_mode", "middle", *gop],
                    "hevc_nvenc", "p010le")
        if engine == "gpu":
            raise RuntimeError("GPU 編碼不可用：" + enc.get("hevc_nvenc", {}).get("error", ""))
        return (["-c:v", "libx265", "-preset", "slow" if preset.get("cq", 18) <= 15 else "medium",
                 "-crf", str(preset.get("x265_crf", 17)), "-pix_fmt", "yuv420p10le", "-profile:v", "main10",
                 "-x265-params", "aq-mode=3:no-sao=1:deblock=-1,-1:log-level=error", *gop], "libx265", "p010le")
    if engine != "cpu" and enc.get("h264_nvenc", {}).get("ok"):
        return (["-c:v", "h264_nvenc", *preset["nvenc"], "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
                 *maxrate, "-profile:v", "high", "-pix_fmt", "nv12", "-spatial-aq", "1", "-bf", "3", *gop],
                "h264_nvenc", "nv12")
    return (["-c:v", "libx264", "-preset", "slow", "-crf", str(preset.get("x264_crf", 16)), "-pix_fmt", "yuv420p",
             "-profile:v", "high", *gop], "libx264", "nv12")


# ---------------------------------------------------------------- CPU stitching
def _cv():
    import cv2
    return cv2


def _apply(src, m):
    cv2 = _cv()
    return cv2.remap(src, m[0], m[1], cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _blend(f, b, plane, offset, add=None, scale=255.0):
    cv2 = _cv()
    if plane["unity"] and add is None:
        return cv2.blendLinear(f, b, plane["wf"], plane["wb"])
    af = plane["af"] if f.ndim == 2 else plane["af"][..., None]
    ab = plane["ab"] if b.ndim == 2 else plane["ab"][..., None]
    out = (f.astype(np.float32) - offset) * af + (b.astype(np.float32) - offset) * ab + offset
    if add is not None:                      # 接縫寬混合 (0..1 units -> code values)
        out += add * scale
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def lens_color_yuv(planes, coeffs, full=True):
    """Per-lens colour match on a source frame (RGB gain + offset). Affine colour maps
    commute with the (linear) resampling, so doing it before the remap equals the GPU order."""
    if all(abs(a - b) < 1e-6 for a, b in zip(coeffs, geo.IDENTITY_COLOR)):
        return planes
    rgb = yuv420_to_rgb(*planes, full=full)
    rgb = rgb * np.array(coeffs[:3], np.float32) + np.array(coeffs[3:], np.float32)
    return rgb_to_yuv420(np.clip(rgb, 0, 1), full)


def lens_color_bgr(img, coeffs):
    if all(abs(a - b) < 1e-6 for a, b in zip(coeffs, geo.IDENTITY_COLOR)):
        return img
    rgb = img[..., ::-1].astype(np.float32) / 255 * np.array(coeffs[:3], np.float32) + np.array(coeffs[3:], np.float32)
    return np.clip(rgb[..., ::-1] * 255 + 0.5, 0, 255).astype(np.uint8)


def stitch_rgb(fr, bk, maps, params, full=True):
    """CPU twin of the GPU graded path: remap each lens, to RGB, per-lens colour match,
    接縫融合 (per-channel gain + wide blend of the smooth difference), then blend with the
    weight*gain maps (no extra chroma subsampling)."""
    L, C = maps.luma, maps.chroma
    colors = (geo.lens_color(params, "front"), geo.lens_color(params, "back"))
    tone = params.get("_lens_tone")                    # 原廠鏡頭曝光: (2, N) gain by brightness per lens
    st = maps.seam_for((fr[0], (fr[1], fr[2])), (bk[0], (bk[1], bk[2])), full, colors=colors, rgb=True, tone=tone)

    def lens(p, key, c, row):
        rgb = yuv420_to_rgb(_apply(p[0], L["m" + key]), _apply(p[1], C["m" + key]), _apply(p[2], C["m" + key]),
                            full=full, clip=False)         # keep super-whites, like the GPU path
        rgb = rgb * np.array(c[:3], np.float32) + np.array(c[3:], np.float32)
        if tone is not None:
            import fusionlook
            rgb = fusionlook.apply_tone(rgb, tone[row])
        return rgb
    F, B = lens(fr, "f", colors[0], 0), lens(bk, "b", colors[1], 1)
    af, ab = L["af"][..., None], L["ab"][..., None]
    t = maps.seam_plane(st, L) if st is not None else None
    if t is not None and t["gB"] is not None:
        af, ab = af * t["gF"], ab * t["gB"]
    out = F * af + B * ab
    if t is not None and t["dterm"] is not None:
        out += t["dterm"]
    return np.clip(out, 0, 1)


def to_limited(planes, full=True):
    """Full-range planes -> limited range (what the encoder is told); no-op for limited input."""
    if not full:
        return planes
    y, u, v = planes
    q = lambda a, s_, o: np.clip(a.astype(np.float32) * s_ + o + 0.5, 0, 255).astype(np.uint8)
    return q(y, 219 / 255, 16), q(u, 224 / 255, 128 - 128 * 224 / 255), q(v, 224 / 255, 128 - 128 * 224 / 255)


def stitch_yuv420(fr, bk, maps, full=True):
    L, C = maps.planes_for(fr, bk, full)
    ys, cs = (255.0, 255.0) if full else (219.0, 224.0)
    y = _blend(_apply(fr[0], L["mf"]), _apply(bk[0], L["mb"]), L, 0 if full else 16, L.get("add"), ys)
    u = _blend(_apply(fr[1], C["mf"]), _apply(bk[1], C["mb"]), C, 128, C.get("add_u"), cs)
    v = _blend(_apply(fr[2], C["mf"]), _apply(bk[2], C["mb"]), C, 128, C.get("add_v"), cs)
    return y, u, v


def stitch_bgr(fr, bk, maps):
    """Photos (BGR pictures of both lenses), with 接縫融合 like the videos."""
    L = maps.luma
    st = maps.seam_for(np.ascontiguousarray(fr[..., ::-1]), np.ascontiguousarray(bk[..., ::-1]), pictures=True)
    if st is None:
        return _blend(_apply(fr, L["mf"]), _apply(bk, L["mb"]), L, 0)
    t = maps.seam_plane(st, L)
    af, ab = L["af"][..., None], L["ab"][..., None]
    if t["gB"] is not None:
        af, ab = af * t["gF"][..., ::-1], ab * t["gB"][..., ::-1]
    out = _apply(fr, L["mf"]).astype(np.float32) * af + _apply(bk, L["mb"]).astype(np.float32) * ab
    if t["dterm"] is not None:
        out += t["dterm"][..., ::-1] * 255.0
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def yuv_to_bgr(planes, w, h):
    cv2 = _cv()
    i420 = np.concatenate([p.ravel() for p in planes]).reshape(h * 3 // 2, w)
    return cv2.cvtColor(i420, cv2.COLOR_YUV2BGR_I420)


# ---------------------------------------------------------------- colour + nadir patch (CPU side)
def yuv420_to_rgb(y, u, v, full=True, clip=True):
    """BT.709 planes -> float RGB 0..1 (chroma upsampled)."""
    cv2 = _cv()
    h, w = y.shape
    Y = y.astype(np.float32) / 255
    C = cv2.resize(np.dstack([u, v]).astype(np.float32) / 255, (w, h), interpolation=cv2.INTER_LINEAR) - 0.5
    if not full:
        Y = (Y - 16 / 255) * 255 / 219
        C *= 255 / 224
    r = Y + 1.5748 * C[..., 1]
    g = Y - 0.1873 * C[..., 0] - 0.4681 * C[..., 1]
    b = Y + 1.8556 * C[..., 0]
    out = np.dstack([r, g, b])
    return np.clip(out, 0, 1) if clip else out


def rgb_to_yuv420(rgb, full=True):
    cv2 = _cv()
    Y = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    cb = (rgb[..., 2] - Y) / 1.8556
    cr = (rgb[..., 0] - Y) / 1.5748
    if not full:
        Y = Y * 219 / 255 + 16 / 255
        cb, cr = cb * 224 / 255, cr * 224 / 255
    h, w = Y.shape
    cb = cv2.resize(cb, (w // 2, h // 2), interpolation=cv2.INTER_AREA) + 0.5
    cr = cv2.resize(cr, (w // 2, h // 2), interpolation=cv2.INTER_AREA) + 0.5
    q = lambda a: np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    return q(Y), q(cb), q(cr)


class Finisher:
    """Applies grading and the nadir patch to RGB frames on the CPU (photos, stills, CPU export)."""

    def __init__(self, grade, patch, spec, w, h):
        self.grade = None if color.is_identity(grade) else color.merge(grade)
        self.patch = patch if patch and patch.get("mode", "off") != "off" else None
        self.pm = self.logo = None
        if self.patch:
            self.pm = geo.patch_map(geo.output_dirs(spec, w, h), self.patch)
            if self.patch["mode"] == "image":
                import gpu
                lw, lh, data = gpu.load_logo_rgba(self.patch.get("image", ""), 1024)
                self.logo = np.frombuffer(data, np.uint8).reshape(lh, lw, 4).astype(np.float32) / 255

    @property
    def active(self):
        return bool(self.grade or self.patch)

    def rgb(self, c, ev=0.0):
        """ev: extra exposure of this frame in EV on top of the grade (自動亮度 / 原廠亮度), like
        GPUStitcher.set_exposure."""
        if ev:
            g = dict(self.grade or color.merge(None))
            g["exposure"] = float(g.get("exposure", 0.0)) + float(ev)
            c = color.grade_rgb(c, g)
        elif self.grade:
            c = color.grade_rgb(c, self.grade)
        if self.patch:
            cv2 = _cv()
            a = self.pm[..., 2:3]
            if self.logo is not None:
                n = self.logo.shape[0]
                lc = cv2.remap(self.logo, self.pm[..., 0] * n - 0.5, self.pm[..., 1] * n - 0.5, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
                a = a * lc[..., 3:4]
                col = lc[..., :3]
            else:
                col = np.array(self.patch.get("color", [0, 0, 0]), np.float32) / 255
            c = c * (1 - a) + col * a
        return c

    def yuv(self, planes, full, ev=0.0):
        if not self.active and not ev:
            return planes
        return rgb_to_yuv420(self.rgb(yuv420_to_rgb(*planes, full=full), ev), full)

    def bgr8(self, img):
        if not self.active:
            return img
        out = self.rgb(img[..., ::-1].astype(np.float32) / 255)
        return np.clip(out[..., ::-1] * 255 + 0.5, 0, 255).astype(np.uint8)


# The GPU writes BT.709 limited-range YUV: say so in the file, so YouTube, browsers and players do not
# have to guess (an untagged file can be read as BT.601 → reds turn orange, greens yellowish).
COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def nv12_to_planes(buf, w, h):
    a = np.frombuffer(buf, np.uint8)
    uv = a[w * h:w * h * 3 // 2].reshape(h // 2, w // 2, 2)
    return a[:w * h].reshape(h, w), np.ascontiguousarray(uv[..., 0]), np.ascontiguousarray(uv[..., 1])


# ---------------------------------------------------------------- job objects
class Cancelled(Exception):
    pass


class DiskFull(Exception):
    pass


def _est_part_bytes(parts: Path, done_parts: int, ow: int, oh: int, preset: dict, fps: float, seg: int) -> float:
    """Expected size of one part: the average of the finished parts, or a conservative guess
    (HEVC 10-bit at CQ18 measured ~0.11 bit per pixel on Fusion footage; x1.5 margin, x2 per -6 CQ)."""
    done = [q.stat().st_size for q in sorted(parts.glob("part_*.mkv")) if ".tmp" not in q.name][:done_parts]
    if len(done) >= 2:
        return max(done) * 1.15
    bpp = 0.16 * 2 ** ((18 - float(preset.get("cq", 18))) / 6) * (1.4 if preset.get("codec") == "h264" else 1.0)
    return bpp * ow * oh * seg / 8


def estimate_bytes(preset: dict, w: int, h: int, fps: float, seconds: float) -> float:
    """Expected output size. Base: HEVC 10-bit at CQ18 ~0.105 bit/pixel on Fusion footage
    (6544: 5376x2688, 27:10 -> 9.21 GB), x2 per -6 CQ, H.264 x1.4; then corrected by the
    measured actual/estimate ratio of past jobs (size_calib, per codec)."""
    bpp = 0.105 * 2 ** ((18 - float(preset.get("cq", 18))) / 6) * (1.4 if preset.get("codec") == "h264" else 1.0)
    k = float((core.cfg("size_calib") or {}).get(preset.get("codec", "hevc"), 1.0))
    return bpp * w * h * fps * seconds / 8 * k


def _update_calib(preset: dict, est: float, actual: float):
    if est <= 0 or actual <= 0:
        return
    cal = dict(core.cfg("size_calib") or {})
    key = preset.get("codec", "hevc")
    old = float(cal.get(key, 1.0))
    ratio = actual / (est / old)                      # ratio against the uncalibrated formula
    cal[key] = round(max(0.2, min(5.0, old * 0.7 + ratio * 0.3)), 4)
    core.set_cfg("size_calib", cal)


def check_space(folder: Path, need: float, what: str):
    """Raise DiskFull (the job pauses, nothing is lost) when the output drive has too little room."""
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < need:
        raise DiskFull(f"⚠ 磁碟空間不足：{what}約需 {core.fmt_bytes(need)}，{folder.anchor} 只剩 {core.fmt_bytes(free)}。"
                       f"清出空間後按「繼續」，會從中斷處接著轉")


class Paused(Exception):
    pass


class Job:
    def __init__(self, jid: int, clip: dict, opts: dict, status="queued"):
        self.id, self.clip, self.opts = jid, clip, opts
        self.status = status          # queued running paused done error cancelled
        self.progress = 0.0
        self.fps = 0.0
        self.eta = None
        self.message = ""
        self.detail = ""
        self.output = opts.get("output", "")
        self.cancel = False
        self.pause = False
        self.started = self.finished = None
        self.created = time.time()
        self.engine_desc = ""
        self.retries = 0              # automatic retries after an error (unattended runs)
        self.not_before = 0.0         # a retry waits until this time

    @property
    def title(self):
        if self.opts.get("auto"):
            return f"全自動分析 {self.clip.get('num', '')}"
        kind = "照片" if self.clip.get("kind") == "photo" else ("截圖" if self.opts.get("still") is not None else "影片")
        return f"{kind} {self.clip.get('num', '')}"

    def to_dict(self):
        return {"id": self.id, "clip": self.clip, "opts": self.opts, "status": self.status,
                "progress": self.progress, "message": self.message, "output": self.output,
                "created": self.created}


_SAVE_LOCK = threading.Lock()


class _Superseded(Exception):
    """This attempt was replaced by a newer one (worker restarted): leave the job alone."""


_QUEUE = None           # the program's queue (run_video saves it once the analysis before converting is done)


WORKER_NAME = "jobqueue2"


class _RetireWorker(BaseException):
    """Ends an older worker loop after a hot update (BaseException: its except clauses let it pass)."""


def _gpu_gate() -> dict:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None
    g = getattr(app, "_fs_gpu_gate", None) if app is not None else globals().get("_fs_gpu_gate")
    if g is None:
        g = {"cv": threading.Condition(), "holders": {}}
        if app is not None:
            setattr(app, "_fs_gpu_gate", g)
        else:
            globals()["_fs_gpu_gate"] = g
    return g


def _gpu_acquire(job):
    """The GPU conversion stage takes turns (「同時用 GPU 轉檔」, default 1): a job that finished its
    preparation waits here while another one converts."""
    g = _gpu_gate()
    with g["cv"]:
        waited = False
        while True:
            limit = max(1, int(core.cfg("gpu_jobs") or 1))
            for jid, j in list(g["holders"].items()):          # holders that are gone
                if j is job or j.status != "running":
                    g["holders"].pop(jid, None)
            if len(g["holders"]) < limit:
                break
            if job.cancel:
                raise Cancelled()
            if job.pause:
                raise Paused()
            if not waited:
                core.log(f"job {job.id}: ready, waiting for the GPU ({', '.join('#' + str(k) for k in g['holders'])})", "job_log.txt")
            waited = True
            job.gpu_wait = True
            others = "、".join(f"#{k}" for k in g["holders"])
            job.message = f"等 GPU：{others} 轉完就開始轉檔（這支已準備好）"
            g["cv"].wait(1.0)
        g["holders"][job.id] = job
        job.gpu_wait = False


def _gpu_release(job):
    g = _gpu_gate()
    with g["cv"]:
        if g["holders"].get(job.id) is job:
            g["holders"].pop(job.id, None)
        g["cv"].notify_all()


class JobQueue:
    """One worker, one job at a time (the GPU encoder is the shared resource).
    Lives on the QApplication so a hot update keeps the running job."""
    API = 1

    def __init__(self):
        global _QUEUE
        _QUEUE = self
        self.jobs: list[Job] = []
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.listeners: list = []
        self._next = 1
        self.after_queue_cb = None
        self._load()
        self._thread = None
        self.ensure_worker()
        try:
            import staging                    # SSD relay: prefetch footage, finish pending moves
            staging.start()
        except Exception:
            pass

    # ---- persistence
    def _load(self):
        data = None
        for f_ in (core.QUEUE_FILE, core.QUEUE_FILE.with_name("queue.bak.json")):   # 斷電保護: the copy if the file broke
            try:
                data = json.loads(f_.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                continue
        if not data:
            return
        auto = core.cfg("auto_resume", True)
        for d in data.get("jobs", []):
            st = d.get("status")
            if st in ("running", "queued"):
                st = "queued" if auto else "paused"
            j = Job(d["id"], d["clip"], d["opts"], st)
            j.progress, j.message, j.created = d.get("progress", 0), d.get("message", ""), d.get("created", time.time())
            if st in ("queued", "paused") and Path(j.output + ".parts").exists():
                j.message = "從中斷處繼續（已完成的部分不會重做）"
            self.jobs.append(j)
            self._next = max(self._next, j.id + 1)

    def save(self):
        """Never raises: the worker thread, the window and the phone page all save; two saves at
        the same moment (or the file briefly locked on Windows) used to kill the worker thread,
        leaving a job stuck at "轉檔中" that could not be cancelled."""
        try:
            with self.lock:
                data = {"jobs": [j.to_dict() for j in self.jobs if j.status != "cancelled"]}
            txt = json.dumps(data, ensure_ascii=False)
            with _SAVE_LOCK:
                tmp = core.QUEUE_FILE.with_name(f"queue.{threading.get_ident()}.tmp")
                with open(tmp, "w", encoding="utf-8") as f_:
                    f_.write(txt)
                    f_.flush()
                    os.fsync(f_.fileno())               # 斷電保護: the queue survives a power cut
                for i in range(20):
                    try:
                        os.replace(tmp, core.QUEUE_FILE)
                        try:
                            core.write_text_safe(core.QUEUE_FILE.with_name("queue.bak.json"), txt)
                        except OSError:
                            pass
                        return
                    except PermissionError:        # Windows: someone is reading it right now
                        time.sleep(0.1 * (i + 1))
                core.log("queue.json could not be replaced (locked)", "job_log.txt")
        except Exception as e:
            try:
                core.log(f"queue save failed: {e}", "job_log.txt")
            except Exception:
                pass

    def ensure_worker(self):
        """Restart the worker thread if it ever died (it should not, but a stuck queue is the worst case)."""
        t = getattr(self, "_thread", None)
        if t is None or not t.is_alive():
            if t is not None:
                core.log("job worker thread was not running: restarted", "job_log.txt")
                alive = {th.name for th in threading.enumerate() if th.is_alive()}
                for j in self.jobs:                    # a running job without its own thread is orphaned
                    if j.status == "running" and f"jobrun{j.id}" not in alive and f"job{j.id}" not in alive:
                        j.status, j.message = "queued", "工作程序重新啟動：從中斷處繼續"
            self._start_worker()

    def _start_worker(self):
        self._thread = threading.Thread(target=self._worker_safe, daemon=True, name=WORKER_NAME)
        self._thread.start()

    def _worker_safe(self):
        while True:
            try:
                self._worker()
            except Exception:
                # (the jobs run on their own threads: a scheduling error does not touch them)
                core.log("job scheduler error (continuing)\n" + traceback.format_exc(), "job_log.txt")
                time.sleep(2)

    # ---- public API (used by the UI; keep stable across hot updates)
    def add(self, clip: dict, opts: dict) -> Job:
        self.ensure_worker()
        with self.cv:
            j = Job(self._next, clip, opts)
            self._next += 1
            self.jobs.append(j)
            self.cv.notify_all()
        self.save()
        return j

    def add_after(self, ref: Job, clip: dict, opts: dict) -> Job:
        """Queue right after `ref` (and after jobs already inserted behind it by the same ref)."""
        with self.cv:
            j = Job(self._next, clip, opts)
            j.parent = ref.id
            self._next += 1
            i = next((k for k, x in enumerate(self.jobs) if x is ref), len(self.jobs) - 1)
            while i + 1 < len(self.jobs) and getattr(self.jobs[i + 1], "parent", None) == ref.id:
                i += 1
            self.jobs.insert(i + 1, j)
            self.cv.notify_all()
        self.save()
        return j

    def get(self, jid):
        return next((j for j in self.jobs if j.id == jid), None)

    def cancel(self, jid):
        self.ensure_worker()
        j = self.get(jid)
        if not j:
            return
        j.cancel = True
        if j.status in ("queued", "paused", "error"):
            j.status = "cancelled"
            _remove_parts(j.output)
        self.save()

    def pause(self, jid):
        self.ensure_worker()
        j = self.get(jid)
        if j and j.status == "running":
            j.pause = True
        elif j and j.status == "queued":
            j.status = "paused"
        self.save()

    def resume(self, jid):
        self.ensure_worker()
        j = self.get(jid)
        if j and j.status in ("paused", "error"):
            with self.cv:
                j.status, j.pause, j.cancel = "queued", False, False
                j.not_before, j.retries = 0.0, 0
                j.message = "等待中（會從中斷處繼續）"
                self.cv.notify_all()
            self.save()

    def move(self, jid, delta):
        with self.lock:
            i = next((k for k, j in enumerate(self.jobs) if j.id == jid), None)
            if i is None:
                return
            k = max(0, min(len(self.jobs) - 1, i + delta))
            self.jobs.insert(k, self.jobs.pop(i))
        self.save()

    def clear_finished(self):
        with self.lock:
            self.jobs[:] = [j for j in self.jobs if j.status not in ("done", "cancelled")]
        self.save()

    def running(self):
        return next((j for j in self.jobs if j.status == "running"), None)

    def busy(self):
        return any(j.status in ("running", "queued") for j in self.jobs)

    # ---- worker
    def _run_guarded(self, job):
        """Runs the job on its own thread so the queue stays in control:
        * 取消／暫停 always work: if the job does not react within 20 s it is let go (marked
          cancelled / paused) and the queue moves on;
        * a job that shows no sign of life for core.cfg("stuck_minutes", 15) minutes is let go
          as an error (the normal automatic retry continues from the finished parts);
        * the job runs with the newest jobs module, so a hot update applies to the next job
          without restarting the program."""
        if threading.current_thread().name == "jobqueue":
            # the one-job-at-a-time worker of an older version (still running after a hot update)
            # reached its next job: hand over to the new scheduler and let the old loop end
            with self.cv:
                job.status, job.message = "queued", "換成新的排程（可同時處理多支）：馬上開始"
                self.cv.notify_all()
            core.log("job worker: old one-at-a-time worker retired, new scheduler started", "job_log.txt")
            self._start_worker()
            raise _RetireWorker()
        import sys as _sys
        M = _sys.modules.get("jobs") or _sys.modules[__name__]
        fn = (M.run_auto if job.opts.get("auto") else M.run_photo if job.clip.get("kind") == "photo"
              else M.run_still if job.opts.get("still") is not None else M.run_video)
        box = {}
        job._attempt = tok = object()                  # a let-go attempt sees a different token and stops

        def target():
            try:
                fn(job, self) if fn is M.run_auto else fn(job)
            except BaseException as e:
                box["e"] = e
        t = threading.Thread(target=target, daemon=True, name=f"job{job.id}")
        t.start()
        sig, last, asked = None, time.time(), None
        stuck = float(core.cfg("stuck_minutes") or 15) * 60
        while True:
            t.join(1.0)
            if getattr(job, "_attempt", None) is not tok:
                raise _Superseded()                    # another worker owns this job now
            if not t.is_alive():
                if "e" in box:
                    raise box["e"]
                return
            now = time.time()
            s_ = (round(job.progress, 4), job.message, getattr(job, "detail", ""), round(job.fps, 1))
            if s_ != sig:
                sig, last = s_, now
            if job.cancel or job.pause:
                asked = asked or now
                if now - asked > 20:
                    core.log(f"JOB {job.id} did not stop within 20 s: let go ({job.message})", "job_log.txt")
                    job._attempt = None
                    if job.cancel:
                        raise Cancelled()
                    raise Paused()
            elif now - last > stuck and not str(job.message).startswith(("合併", "等 GPU")):   # joining a big file shows no progress
                job._attempt = None
                core.log(f"JOB {job.id} stuck {int(now - last)} s at: {job.message}", "job_log.txt")
                raise RuntimeError(f"工作超過 {int(stuck // 60)} 分鐘沒有任何進度（卡在：{job.message or '準備中'}），"
                                   f"已強制停止；會自動重試並從中斷處繼續")

    def _worker(self):
        """Scheduling loop. Each round calls _schedule_once (looked up again every round, so a hot
        update changes the scheduling at once); every job runs on its own thread (_run_one)."""
        while True:
            self._schedule_once()

    def _past_prep(self, j) -> bool:
        """A running job that no longer needs the preparation slot (CPU / disk heavy)."""
        est = getattr(j, "est", None)
        return bool((est is not None and getattr(est, "t0", None)) or getattr(j, "t_fin0", None)
                    or getattr(j, "gpu_wait", False) or j.progress > 0)

    def _schedule_once(self):
        """Start the next queued job when there is room: up to 「同時處理幾支」 jobs, and a new one only
        when every running job is past its preparation. So the jobs interleave: one prepares (CPU,
        disk) or finishes (join, move) while another converts on the GPU; the GPU stage takes turns."""
        with self.cv:
            if not any(j.status == "queued" for j in self.jobs):
                if not any(j.status == "running" for j in self.jobs):
                    _keep_awake(False)
                self.cv.wait(5)
                return
            if not any(j.status == "queued" and getattr(j, "not_before", 0) <= time.time() for j in self.jobs):
                self.cv.wait(5)               # only retries waiting for their time
                return
        if not in_schedule():
            self.waiting_schedule = True
            time.sleep(20)                    # outside the allowed hours: wait (the UI shows it)
            return
        self.waiting_schedule = False
        with self.cv:
            act = [j for j in self.jobs if j.status == "running"]
            limit = max(1, min(4, int(core.cfg("parallel_jobs") or 1)))
            if len(act) >= limit or (act and not all(self._past_prep(j) for j in act)):
                self.cv.wait(2)
                return
            job = next((j for j in self.jobs if j.status == "queued" and getattr(j, "not_before", 0) <= time.time()), None)
            if job is None:
                return
            job.status = "running"
            job.cancel = job.pause = False
        _keep_awake(True)
        self.save()
        threading.Thread(target=self._run_one, args=(job,), daemon=True, name=f"jobrun{job.id}").start()
        time.sleep(1.0)

    def _run_one(self, job):
        try:
            self._run_one_body(job)
        except Exception:
            core.log(f"job runner {job.id} crashed\n" + traceback.format_exc(), "job_log.txt")
            if job.status == "running":
                job.status, job.message = "queued", "工作程序出錯後重新開始：從中斷處繼續"
        finally:
            try:
                _gpu_release(job)
            except Exception:
                pass
            with self.cv:
                self.cv.notify_all()

    def _run_one_body(self, job):
        job.started, job.finished = time.time(), None
        try:
            try:
                self._run_guarded(job)
            except _Superseded:
                return
            except Exception as e0:                 # classes from a newer (hot-updated) jobs module
                k = type(e0).__name__
                if k == "DiskFull" and not isinstance(e0, DiskFull):
                    raise DiskFull(str(e0))
                if k == "Paused" and not isinstance(e0, Paused):
                    raise Paused()
                if k == "Cancelled" and not isinstance(e0, Cancelled):
                    raise Cancelled()
                raise
            job.status, job.progress = "done", 1.0
        except DiskFull as e:
            job.status, job.message = "paused", str(e)
        except Paused:
            if getattr(job, "schedule_paused", False):
                job.schedule_paused = False
                job.status, job.message = "queued", "超出轉檔時段，到時間會自動從中斷處繼續"
            else:
                job.status, job.message = "paused", "已暫停（按繼續會從中斷處開始）"
        except Cancelled:
            job.status, job.message = "cancelled", "已取消"
            _remove_parts(job.output)
        except Exception as e:
            job.status, job.message = "error", str(e)
            core.log(f"JOB {job.id} {job.output} FAILED\n{traceback.format_exc()}", "job_log.txt")
            n_max = int(core.cfg("auto_retry", 2) or 0)
            if getattr(job, "retries", 0) < n_max and not job.cancel:
                # unattended runs: a passing problem (driver hiccup, file briefly locked, disk woke up)
                # should not stop the night - try again later, finished parts are kept
                job.retries = getattr(job, "retries", 0) + 1
                delay = 30 if job.retries == 1 else 120
                job.not_before = time.time() + delay
                job.status = "queued"
                job.message = f"⚠ 失敗：{str(e)[:160]}（{delay} 秒後自動重試，第 {job.retries}/{n_max} 次）"
                core.log(f"JOB {job.id} retry {job.retries}/{n_max} in {delay}s", "job_log.txt")
        job.finished = time.time()
        core.log(f"JOB {job.id} {job.status}: {job.output} {job.message}", "job_log.txt")
        self.save()
        for cb in list(self.listeners):
            try:
                cb(job)
            except Exception:
                pass
        if not self.busy() and self.after_queue_cb:
            try:
                self.after_queue_cb()
            except Exception:
                pass


def _keep_awake(on: bool):
    """Stop Windows from sleeping while a conversion runs (display may still turn off)."""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def in_schedule(now=None) -> bool:
    """Settings → 轉檔時段: only convert between start and end (crossing midnight allowed)."""
    sch = core.cfg("schedule") or {}
    if not sch.get("on"):
        return True
    import datetime as _dt
    now = now or _dt.datetime.now()
    def mins(s):
        h, m = (int(x) for x in str(s).split(":"))
        return h * 60 + m
    a, b, n = mins(sch.get("start", "01:00")), mins(sch.get("end", "07:00")), now.hour * 60 + now.minute
    return a <= n < b if a <= b else (n >= a or n < b)


def _remove_parts(output):
    shutil.rmtree(output + ".parts", ignore_errors=True)
    try:
        import staging
        wd = staging.work_dir()
        if wd:
            shutil.rmtree(wd / (Path(output).name + ".parts"), ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 全自動處理
def run_auto(job: Job, queue: "JobQueue"):
    import autoproc
    clip = job.clip
    steps = {"1/4": 0.1, "2/4": 0.35, "3/4": 0.45, "4/4": 0.6}

    def status(m):
        job.message = m
        for k, v in steps.items():
            if k in m:
                job.progress = v
    if clip.get("kind") != "video":
        raise RuntimeError("全自動處理目前只支援影片")
    proj = autoproc.analyse(clip, status=status, cancel=lambda: job.cancel)
    if job.cancel:
        raise Cancelled()
    meta = {"created": media.probe(clip["front"][0]).get("creation_time")}
    added = []
    for key in job.opts["auto"].get("outputs") or ["equirect"]:
        if key not in autoproc.OUTPUTS:
            continue
        opts = autoproc.export_opts(clip, proj, key, meta)
        if os.path.exists(opts["output"]) and not job.opts["auto"].get("overwrite"):
            added.append(f"略過（已存在）{Path(opts['output']).name}")
            continue
        Path(opts["output"]).parent.mkdir(parents=True, exist_ok=True)
        queue.add_after(job, clip, opts)
        added.append(Path(opts["output"]).name)
    rep = proj.get("auto_report") or {}
    job.detail = " ／ ".join(str(rep.get(k, "")) for k in ("align", "stab", "heading", "color"))
    job.message = "自動分析完成 → " + "、".join(added)
    job.output = added and str(Path(autoproc.export_opts(clip, proj, (job.opts["auto"].get("outputs") or ["equirect"])[0], meta)["output"]).parent) or ""


# ---------------------------------------------------------------- photo / still
def _params(clip_w, clip_h, opts):
    p = geo.merge_params(geo.default_params(clip_w, clip_h), opts.get("params"))
    if opts.get("seamflow_file"):
        import seamflow
        sf = seamflow.load(opts["seamflow_file"])
        if sf is not None:
            p["_seamflow"] = sf
    return p


def gopro_grade(grade: dict | None) -> dict | None:
    """原廠色彩 for a clip without its own grade (the rule the preview shows): the learned LUT
    plus Fusion Studio's smoothing. None when it does not apply."""
    lut_ = core.gopro_lut_path()
    if core.cfg("color_mode") != "gopro" or not lut_ or not os.path.exists(lut_) or not color.is_identity(grade):
        return None
    g_ = color.merge(None)
    g_["lut"], g_["lut_strength"] = lut_, max(0.0, min(1.0, float(core.cfg("gopro_lut_strength") or 1.0)))
    g_["denoise"] = max(0.0, min(1.0, float(core.cfg("gopro_denoise") or 0.0)))   # Fusion Studio is this smooth
    g_["sharpen"] = max(0.0, min(1.0, float(core.cfg("gopro_sharpen", 0.7) or 0.0)))  # ... and this sharp
    return g_


def run_photo(job: Job):
    clip, opts = job.clip, job.opts
    g_ = gopro_grade(opts.get("grade"))
    if g_:                                   # same colour as the preview and the videos
        opts = {**opts, "grade": g_}
    f, b = imread(clip["front"][0]), imread(clip["back"][0])
    h, w = f.shape[:2]
    params = _params(w, h, opts)
    spec = opts.get("projection") or {"type": "equirect"}
    if spec.get("type") in ("planet", "flat"):
        ow, oh = (int(x) for x in str(opts.get("size") or core.cfg(spec["type"] + "_size")).split("x"))
    else:
        ow = int(opts.get("width") or 0) or auto_width(h, params)
        oh = ow // 2
    job.message = "拼接照片…"
    maps = geo.StitchMaps(params, w, h, ow, oh, chroma=False, spec=spec)
    Path(job.output).parent.mkdir(parents=True, exist_ok=True)
    fin = Finisher(opts.get("grade"), opts.get("patch"), spec, ow, oh)
    f, b = enhance.bgr(f, opts.get("grade")), enhance.bgr(b, opts.get("grade"))
    f, b = lens_color_bgr(f, geo.lens_color(params, "front")), lens_color_bgr(b, geo.lens_color(params, "back"))
    imwrite_jpg(job.output, fin.bgr8(stitch_bgr(f, b, maps)), 95)
    if spec.get("type", "equirect") == "equirect":
        spherical.inject_jpeg(job.output, ow, oh)
    job.message = f"完成：{ow}×{oh}"


def run_still(job: Job):
    clip, opts = job.clip, job.opts
    g_ = gopro_grade(opts.get("grade"))
    if g_:
        opts = {**opts, "grade": g_}
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    params = _params(tf.width, tf.height, opts)
    fi = int(round(float(opts["still"]) * tf.fps))
    bi = fi + int(round(params["back_offset"] * tf.fps))
    fr = enhance.planes(media.read_frame(tf, fi), opts.get("grade"))
    bk = enhance.planes(media.read_frame(tb, max(0, bi)), opts.get("grade"))
    spec = opts.get("projection") or {"type": "equirect"}
    import fusionlook
    lens_ev = (fusionlook.lens_at(clip, fi / tf.fps, tf.fps)
               if fusionlook.active(opts.get("grade")) and core.cfg("gopro_lens_exposure", True) else None)
    if opts.get("color_track"):
        import colortrack
        cc = colortrack.at(opts["color_track"], fi / tf.fps)
        if cc:
            lens_match = opts.get("lens_match")
            if lens_ev is not None and lens_match is not True:      # same rule as the video export
                cc = (geo.lens_color(params, "front"), geo.lens_color(params, "back"))
            g_wb = colortrack.wb_at(opts["color_track"], fi / tf.fps, float(opts.get("auto_wb") or 0))
            if g_wb is not None:
                cc = (colortrack.apply_gain(cc[0], g_wb), colortrack.apply_gain(cc[1], g_wb))
            params["front"]["color"], params["back"]["color"] = cc[0], cc[1]
        ev = colortrack.exposure_at(opts["color_track"], fi / tf.fps, float(opts.get("auto_exposure") or 0))
        if ev:
            import color as _color
            g = _color.merge(opts.get("grade"))
            g["exposure"] = float(g["exposure"]) + ev
            opts = {**opts, "grade": g}
    if lens_ev is not None:                                   # 原廠鏡頭曝光
        params["_lens_tone"] = fusionlook.tone_tables(lens_ev)
    if fusionlook.active(opts.get("grade")) and core.cfg("gopro_exposure", True):
        ev_m = fusionlook.exposure_at(clip, fi / tf.fps, tf.fps)
        if ev_m:
            import color as _color
            g = _color.merge(opts.get("grade"))
            g["exposure"] = float(g["exposure"]) + ev_m
            opts = {**opts, "grade": g}
    if (opts.get("stab") or {}).get("mode", "off") != "off":
        import gyro
        S = gyro.frame_rotations(clip["front"], tf.fps, fi, 1, opts["stab"])
        if S is not None and len(S):
            params["_stab"] = S[0].tolist()
    if spec.get("type") in ("planet", "flat") and opts.get("path"):
        import camerapath
        v = camerapath.at(opts["path"], fi / tf.fps)
        if v:
            spec = {**spec, **v}
    if spec.get("type") in ("planet", "flat"):
        ow, oh = (int(x) for x in str(opts.get("size") or core.cfg(spec["type"] + "_size")).split("x"))
    else:
        ow = int(opts.get("width") or 0) or auto_width(tf.height, params)
        oh = ow // 2
    ow -= ow % 2
    oh -= oh % 2
    maps = geo.StitchMaps(params, tf.width, tf.height, ow, oh, spec=spec)
    full = tf.color_range == "pc"
    fin = Finisher(opts.get("grade"), opts.get("patch"), spec, ow, oh)
    if geo.lens_color_active(params) or maps.seam_on or params.get("_lens_tone") is not None:   # like the video export
        rgb = fin.rgb(stitch_rgb(fr, bk, maps, params, full))
    else:
        rgb = fin.rgb(yuv420_to_rgb(*stitch_yuv420(fr, bk, maps, full), full=full))
    Path(job.output).parent.mkdir(parents=True, exist_ok=True)
    imwrite_jpg(job.output, np.clip(rgb[..., ::-1] * 255 + 0.5, 0, 255).astype(np.uint8), 95)
    if spec.get("type", "equirect") == "equirect":
        spherical.inject_jpeg(job.output, ow, oh)
    job.message = f"完成：{ow}×{oh}"


# ---------------------------------------------------------------- video
def _job_key(opts, extra):
    keep = {k: v for k, v in opts.items() if k not in ("output", "overwrite")}
    return hashlib.sha1(json.dumps([keep, extra], sort_keys=True, default=str).encode()).hexdigest()[:16]


class _Writer(threading.Thread):
    """Feeds one encoder process; any failure is kept for the main loop."""

    def __init__(self, proc):
        super().__init__(daemon=True)
        self.proc, self.q, self.error = proc, queue.Queue(maxsize=3), None
        self.written = 0

    def run(self):
        try:
            while True:
                item = self.q.get()
                if item is None:
                    break
                for p in (item if isinstance(item, tuple) else (item,)):
                    self.proc.stdin.write(memoryview(np.ascontiguousarray(p)) if isinstance(p, np.ndarray) else p)
                self.written += 1
        except Exception as e:
            self.error = e
        finally:
            try:
                self.proc.stdin.close()
            except Exception:
                pass

    def put(self, item, job: Job):
        while True:
            if self.error is not None or self.proc.poll() is not None:
                raise RuntimeError("編碼器意外結束")
            if job.cancel:
                raise Cancelled()
            try:
                self.q.put(item, timeout=1)
                return
            except queue.Full:
                continue


def _get_frame(reader: media.FrameReader, job: Job, last_progress: list):
    while True:
        if job.cancel:
            raise Cancelled()
        try:
            item = reader.q.get(timeout=1)
            last_progress[0] = time.time()
            return item
        except queue.Empty:
            if time.time() - last_progress[0] > STALL_SECONDS:
                raise RuntimeError(f"解碼超過 {STALL_SECONDS} 秒沒有進度（硬碟太忙或檔案損壞？）")


def _lens_untouched(params, w, h) -> bool:
    """Lens centres / radii / rotations still at the built-in values for this camera mode."""
    d = geo.default_params(w, h)
    return all(abs(float(params[lens][k]) - float(d[lens][k])) < 1e-9
               for lens in ("front", "back") for k in ("cx", "cy", "r", "yaw", "pitch", "roll"))


def _part_frames(path: str) -> int:
    """Video packets that can actually be read from a part (a file cut short by a power failure
    still carries the full length in its header). Demux only, no decoding: fast."""
    try:
        r = subprocess.run([ff.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                            "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=120, creationflags=ff.NO_WINDOW)
        return int((r.stdout or "0").strip().split(",")[0] or 0)
    except Exception:
        return 0


def run_video(job: Job):
    clip, opts = job.clip, job.opts
    my_attempt = getattr(job, "_attempt", None)
    job.message = "準備中：讀取素材資訊…"
    presets = all_presets()
    preset = presets.get(opts.get("preset", "youtube"), PRESETS["youtube"])
    if preset.get("no_stab") and opts.get("stab"):
        opts = {**opts, "stab": {**opts["stab"], "mode": "off"}}
    if preset.get("stereo"):
        opts = {**opts, "spatial_audio": False}
    import staging
    src_clip = clip
    clip = staging.use(job, clip)                 # footage copied to the SSD relay (when set)
    job.t_prep0, job.t_fin0, job.est = time.time(), None, None     # 整體進度: preparation starts now
    job.throttled = False
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    fps = tf.fps
    params = _params(tf.width, tf.height, opts)
    spec = opts.get("projection") or {"type": "equirect"}
    ow, oh = out_size(opts, tf, params, preset)
    total = tf.total_frames
    start = max(0, int(round(float(opts.get("start") or 0) * fps)))
    end = int(round(float(opts.get("end") or 0) * fps)) or total
    end = min(end, total)
    count_src = max(1, end - start)              # source frames in the range
    # speed: >1 time-lapse (every step-th frame, same frame rate), <1 slow motion (all frames, lower rate)
    from fractions import Fraction
    speed = float(opts.get("speed") or 1.0)
    step = max(1, int(round(speed))) if speed >= 1 else 1
    out_rate = Fraction(tf.fps_str) * (Fraction(speed).limit_denominator(100) if speed < 1 else 1)
    out_rate_str = f"{out_rate.numerator}/{out_rate.denominator}"
    out_fps = float(out_rate)
    count = -(-count_src // step)                 # output frames
    off = int(round(params.get("back_offset", 0) * fps))
    seg = max(1, int(round(float(core.cfg("segment_seconds") or 30) * out_fps)))
    n_parts = math.ceil(count / seg)

    engine = opts.get("engine", "auto")
    job.message = "準備中：檢查顯示卡與編碼器…"
    venc, enc_name, gpu_in = encoder_args(preset, engine, fps)
    use_gpu = engine != "cpu" and gpu_ok()
    job.message = "準備中…"
    if engine == "gpu" and not use_gpu:
        raise RuntimeError("GPU 拼接不可用")
    bits = 10 if preset["codec"] == "hevc" else 8
    full_range = tf.color_range == "pc"

    out = job.output
    wd = staging.work_dir(out)
    fresh = not Path(out + ".parts").exists() and not (wd is not None and (wd / (Path(out).name + ".parts")).exists())
    if (fresh and core.cfg("auto_align", True) and not opts.get("align_done")
            and _lens_untouched(params, tf.width, tf.height)):
        # 轉檔前自動對齊: lenses still at the factory values (never aligned, no 相機預設 saved) ->
        # the same 自動對齊 as the button, stored in the job so a resume / retry uses the same values
        try:
            import align
            job.message = "自動對齊前後鏡頭接縫…"
            r = align.auto_align(clip, params, [tf.duration * p for p in (0.1, 0.3, 0.5, 0.7, 0.9)])
            po = json.loads(json.dumps(opts.get("params") or {}))
            if r["improved"]:
                for lens in ("front", "back"):
                    po.setdefault(lens, {})
                    for k in ("cx", "cy", "r", "yaw", "pitch", "roll"):
                        params[lens][k] = po[lens][k] = float(r["params"][lens][k])
            opts = {**opts, "params": po or opts.get("params"), "align_done": True}
            job.opts = {**job.opts, "params": opts["params"], "align_done": True}
            core.log(f"auto align {out}: {r['before']:.3f} -> {r['after']:.3f}", "job_log.txt")
        except Exception as e:
            core.log(f"auto align skipped {out}: {e}", "job_log.txt")
            job.opts = {**job.opts, "align_done": True}
    g_ = gopro_grade(opts.get("grade")) if fresh and not opts.get("gopro_color_done") else None
    if g_:
        # 原廠色彩: the colour learned from GoPro Fusion Studio's render replaces 自動調色 / 白平衡
        # (only for clips without a grade of their own, like 自動調色; the preview shows the same)
        lut_ = g_["lut"]
        flags = {"gopro_color_done": True, "auto_tone_done": True, "wb_done": True}
        opts = {**opts, "grade": g_, **flags}
        job.opts = {**job.opts, "grade": g_, **flags}
        core.log(f"原廠色彩 {out}: {lut_} ×{g_['lut_strength']:.2f}", "job_log.txt")
    import fusionlook
    wb_s = float(core.cfg("gopro_wb", 0.8) or 0)
    if (fresh and wb_s > 0 and fusionlook.active(opts.get("grade")) and "auto_wb" not in opts
            and clip.get("kind", "video") == "video"):
        # 原廠白平衡: Fusion Studio evens out the camera's white balance drift over the clip; the colour
        # track (also matches the two lenses over time) measures it, stored in the job for resume / retry
        try:
            tr_ = opts.get("color_track")
            if not tr_ or not tr_.get("wb_t"):
                import colortrack
                job.message = "分析白平衡變化（原廠色彩）…"
                tr_ = colortrack.analyse(clip, params, interval=3.0, smooth=6.0)
            opts = {**opts, "color_track": tr_, "auto_wb": wb_s}
            job.opts = {**job.opts, "color_track": tr_, "auto_wb": wb_s}
        except Exception as e:
            core.log(f"原廠白平衡 skipped {out}: {e}", "job_log.txt")
            job.opts = {**job.opts, "auto_wb": 0.0}
    if (fresh and color.is_identity(opts.get("grade")) and core.cfg("auto_tone", True)
            and core.cfg("auto_tone_all", True) and not opts.get("auto_tone_done")):
        # 自動調色＋白平衡 for every conversion of an ungraded clip (manual grades are never touched);
        # stored in the job so a resume / retry uses exactly the same grade
        try:
            import autotone
            job.message = "自動調色分析（白平衡、對比、飽和度）…"
            tr = autotone.analyse(clip, params)
            g_ = color.merge(None)
            g_.update(tr["values"])
            opts = {**opts, "grade": g_}
            job.opts = {**job.opts, "grade": g_, "auto_tone_done": True}
            core.log(f"auto tone {out}: {autotone.describe(tr)}", "job_log.txt")
        except Exception as e:
            core.log(f"auto tone skipped {out}: {e}", "job_log.txt")
            job.opts = {**job.opts, "auto_tone_done": True}
    elif (fresh and not color.is_identity(opts.get("grade")) and core.cfg("auto_wb", True)
          and not opts.get("wb_done")):
        # graded before white balance existed (e.g. 全自動分析 on V2.7.0) and white balance never
        # touched by hand: add only the automatic white balance, keep the rest of the grade
        g0 = color.merge(opts.get("grade"))
        if abs(g0["temperature"]) < 1e-6 and abs(g0["tint"]) < 1e-6:
            try:
                import autotone
                job.message = "自動白平衡分析…"
                px = autotone.sample_pixels(clip, params)
                wb = autotone.white_balance(color.grade_rgb(px[:: max(1, len(px) // 20000)], g0))
                g0["temperature"], g0["tint"] = wb["temperature"], wb["tint"]
                opts = {**opts, "grade": g0}
                job.opts = {**job.opts, "grade": g0, "wb_done": True}
                core.log(f"auto white balance {out}: temperature {wb['temperature']:+.2f}, tint {wb['tint']:+.2f}"
                         f" (neutral px {wb.get('neutral')})", "job_log.txt")
            except Exception as e:
                core.log(f"auto white balance skipped {out}: {e}", "job_log.txt")
                job.opts = {**job.opts, "wb_done": True}
    # 斷電保護: the results of the analysis above (alignment, colour, white balance) are in job.opts
    # and decide the parts' key: save them now, or a power cut before the next save would make the
    # resumed job start over with different settings
    try:
        if _QUEUE is not None and job in _QUEUE.jobs:
            _QUEUE.save()
    except Exception:
        pass
    if wd is None and fresh and not Path(out + ".parts").exists():
        # no relay: the parts and the joined file are written next to the output -> if the batch
        # folder cannot hold them but a spare can, start there
        try:
            import batches
            need_ = estimate_bytes(preset, ow, oh, out_fps, count / out_fps) * 2.1
            folder_, note_ = batches.choose(job, need_)
            if Path(folder_) != Path(out).parent:
                out = job.output = job.opts["output"] = str(Path(folder_) / Path(out).name)
                job.message = "準備中…" + note_
        except Exception:
            pass                                        # no room anywhere: check_space pauses below
    parts = Path(out + ".parts")
    if wd is not None and not parts.exists():          # convert and join on the SSD relay
        parts = wd / (Path(out).name + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    key = _job_key(opts, [ow, oh, seg, enc_name, use_gpu, list(src_clip["front"]), list(src_clip["back"])])
    state_file = parts / "job.json"
    try:
        st = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("key") != key:
        shutil.rmtree(parts, ignore_errors=True)
        parts.mkdir(parents=True, exist_ok=True)
        st = {"key": key, "n_parts": n_parts}
        state_file.write_text(json.dumps(st), encoding="utf-8")

    def part_path(k):
        return parts / f"part_{k:05d}.mkv"

    # 斷電保護: parts finished before a power cut may be empty or cut short on the disk. Check each
    # one (length against what it should be) and redo the broken ones.
    bad_ = []
    for k_ in range(n_parts):
        pp_ = part_path(k_)
        if not pp_.exists():
            continue
        want_ = min(seg, count - k_ * seg)
        ok_ = _part_frames(str(pp_)) >= want_          # the frames really in the file, not what the header says
        if not ok_:
            bad_.append(k_)
            pp_.unlink(missing_ok=True)
    for f_ in parts.glob("*.tmp.mkv"):
        f_.unlink(missing_ok=True)
    if bad_:
        core.log(f"斷電保護 {out}: 段落 {bad_} 損壞或不完整，重新轉", "job_log.txt")

    done_parts = 0
    while done_parts < n_parts and part_path(done_parts).exists():
        done_parts += 1
    where = ("GPU 解碼＋GPU 拼接" if use_gpu else "CPU 拼接") + f"＋{enc_name}"
    job.engine_desc = where

    # audio first (cheap) so a resumed job does not need it again
    audio_file = None
    audio_desc = "無聲音（縮時／慢動作或未勾選聲音）"
    wavs = clip.get("wavs") or []
    spatial = bool(opts.get("audio", True) and opts.get("spatial_audio") and spec.get("type") != "planet"
                   and len(wavs) == len(tf.files))
    if opts.get("spatial_audio") and not spatial and opts.get("audio", True):
        core.log(f"spatial audio skipped for {out}: {len(wavs)} WAV / {len(tf.files)} chapters", "job_log.txt")
    if opts.get("audio", True) and speed == 1.0:
        audio_file = parts / ("audio_ambix.m4a" if spatial else "audio.m4a")
        if audio_file.exists():
            try:
                audio_desc = (parts / "audio_desc.txt").read_text(encoding="utf-8")
            except OSError:
                audio_desc = "聲音"
        else:
            job.message = "處理 360 空間音訊…" if spatial else "處理聲音…"
            tmp = parts / "audio.tmp.m4a"
            if spatial:
                built = media.build_spatial_audio(tf, start, count, str(tmp), wavs)
                audio_desc = "360 空間音訊"
            else:
                built, audio_desc = media.build_stereo_audio(tf, tb, start, count, str(tmp), wavs)
            if built:
                os.replace(tmp, audio_file)
                (parts / "audio_desc.txt").write_text(audio_desc, encoding="utf-8")
            else:
                audio_file = None
                spatial = False
            core.log(f"audio for {out}: {audio_desc}", "job_log.txt")

    if done_parts < n_parts:
        _gpu_acquire(job)                         # the GPU stage takes turns with the other running jobs
        job.message = "建立投影表…"
        path_V = None
        if spec.get("type") in ("planet", "flat") and opts.get("path"):
            import camerapath
            path_V = camerapath.per_frame(opts["path"], fps, start, count_src)
        import fusionlook
        # 原廠鏡頭曝光: Fusion Studio sets the brightness of each lens from the exposure the camera
        # recorded for it; it does not match the lenses by picture content
        lens_E = (fusionlook.lens_diff(clip, fps, start, count_src)
                  if fusionlook.active(opts.get("grade")) and core.cfg("gopro_lens_exposure", True) else None)
        track_C = None
        if opts.get("color_track"):
            import colortrack
            track_C = colortrack.per_frame(opts["color_track"], fps, start, count_src)
            lens_match = opts.get("lens_match")
            if lens_match is None:
                lens_match = lens_E is None
            if track_C is not None and not lens_match:
                # white balance only: what is left between the lenses is matched at the seam, frame by
                # frame (接縫融合), not by one colour correction for the whole lens
                track_C = np.tile(np.array(geo.lens_color(params, "front") + geo.lens_color(params, "back"), np.float64),
                                  (len(track_C), 1))
            wb_G = colortrack.wb_gains(opts["color_track"], fps, start, count_src, float(opts.get("auto_wb") or 0))
            if track_C is not None and wb_G is not None:
                track_C = np.array([colortrack.apply_gain(r_[:6], g_) + colortrack.apply_gain(r_[6:], g_) for r_, g_ in zip(track_C, wb_G)])
                core.log(f"自動白平衡：強度 {float(opts['auto_wb']):.2f}，R {wb_G[:, 0].min():.3f}～{wb_G[:, 0].max():.3f}，"
                         f"B {wb_G[:, 2].min():.3f}～{wb_G[:, 2].max():.3f}", "job_log.txt")
        if lens_E is not None:
            core.log(f"原廠鏡頭曝光：前後鏡頭曝光差 {lens_E.min():+.2f} ～ {lens_E.max():+.2f} EV", "job_log.txt")
        expo_E = None
        if opts.get("color_track") and float(opts.get("auto_exposure") or 0) > 0:
            import colortrack
            expo_E = colortrack.exposure_curve(opts["color_track"], fps, start, count_src, float(opts["auto_exposure"]))
            if expo_E is not None:
                core.log(f"自動亮度：強度 {float(opts['auto_exposure']):.2f}，補償 {expo_E.min():+.2f} ～ {expo_E.max():+.2f} EV")
        if fusionlook.active(opts.get("grade")) and core.cfg("gopro_exposure", True):
            # 原廠亮度: Fusion Studio renders bright daylight scenes brighter (follows the camera's ISO / shutter)
            meta_E = fusionlook.exposure_curve(clip, fps, start, count_src)
            if meta_E is not None:
                expo_E = meta_E if expo_E is None else (expo_E + meta_E).astype(np.float32)
                core.log(f"原廠亮度：{meta_E.min():+.2f} ～ {meta_E.max():+.2f} EV", "job_log.txt")
        stab_S = None
        if (opts.get("stab") or {}).get("mode", "off") != "off":
            import gyro
            job.message = "讀取陀螺儀、計算防震…"
            stab_S = gyro.frame_rotations(clip["front"], fps, start, count_src, opts["stab"])
            if not use_gpu:
                raise RuntimeError("防震／水平鎖定需要 GPU 轉檔（目前選的是 CPU）")
        rs_ms = float((opts.get("stab") or {}).get("rs_ms") or 0)
        omega = None
        if rs_ms > 0:
            import gyro
            omega = gyro.frame_omega(clip["front"], fps, start, count_src,
                                     float((opts.get("stab") or {}).get("offset_ms", gyro.DEFAULT_OFFSET_MS)))
            if omega is None:
                core.log(f"滾動快門校正：{clip.get('name', '')} 沒有陀螺儀資料，略過", "job_log.txt")
            elif not use_gpu:
                core.log("滾動快門校正需要 GPU 轉檔，CPU 轉檔時略過", "job_log.txt")
                omega = None
            else:
                core.log(f"滾動快門校正：讀出時間 {rs_ms:g} ms", "job_log.txt")
        if path_V is not None and not use_gpu:
            raise RuntimeError("關鍵影格運鏡需要 GPU 轉檔（目前選的是 CPU）")
        missing = [k for k in range(n_parts) if not part_path(k).exists()]
        n_workers = 1
        if use_gpu:
            import gpu
            n_workers = max(1, min(3, int(core.cfg("parallel_segments") or 2), len(missing)))
        in_fmt = ("p010le" if bits == 10 else "nv12") if use_gpu else "yuv420p"
        bsf = ("hevc_metadata" if preset["codec"] == "hevc" else "h264_metadata") + \
              f"=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0"   # the stitcher always writes limited range
        lock = threading.Lock()
        stop = threading.Event()
        errors: list = []
        shared = {"frames": (n_parts - len(missing)) * seg, "parts_done": n_parts - len(missing), "eof_at": None,
                  "early": []}
        hist: list[tuple[float, int]] = []
        frames_run = [0]
        if n_workers > 1:
            where += f"，同時 {n_workers} 段"
            job.engine_desc = where
        job.message = f"{where}，{ow}×{oh}"

        class _Stop(Exception):
            pass

        import history
        est_ = history.Estimator(count, shared["frames"], ow, step, opts.get("preset"))
        job.est = est_
        g_ = est_.start_guess()
        if g_:
            job.eta = g_
            job.eta_at = time.time() + g_

        def frame_done(k):
            try:
                import pace as _pace               # 效能（降速）: rest in proportion while slowed down
                _pace.pace(job)
            except Exception:
                pass
            with lock:
                shared["frames"] += 1
                frames_run[0] += 1
                now = time.time()
                hist.append((now, frames_run[0]))
                while hist and now - hist[0][0] > 6:
                    hist.pop(0)
                if len(hist) > 1 and hist[-1][0] > hist[0][0]:
                    job.fps = (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0])
                job.progress = min(0.99, shared["frames"] / count)
                est_.update(shared["frames"])        # smoothed, from past jobs + this job's average speed
                job.eta = est_.eta()
                job.eta_at = est_.finish
                job.detail = (f"第 {k + 1}/{n_parts} 段" if n_workers == 1 else
                              f"已完成 {shared['parts_done']}/{n_parts} 段，同時轉 {n_workers} 段")

        def worker(ks: list):
            """Converts the parts in ks (ascending) with its own decoders, stitcher and encoder."""
            gs = rf = rb = None
            last = [time.time()]
            try:
                if use_gpu:
                    gs = gpu.GPUStitcher(params, tf.width, tf.height, ow, oh, bits, spec=spec, grade=opts.get("grade"),
                                         patch=opts.get("patch"), full_range=full_range,
                                         dynamic=stab_S is not None or path_V is not None or omega is not None
                                         or spec.get("type") == "flat",
                                         lens_track=track_C is not None or expo_E is not None or lens_E is not None)
                    gs.frame_step = step
                    ring = [bytearray(gs.frame_bytes) for _ in range(6)]
                else:
                    maps = geo.StitchMaps(params, tf.width, tf.height, ow, oh, spec=spec)
                    maps.step = step
                    fin = Finisher(opts.get("grade"), opts.get("patch"), spec, ow, oh)
                n_out = 0
                prev = None
                for i, k in enumerate(ks):
                    if stop.is_set():
                        return
                    if prev is None or k != prev + 1:              # start (or jump): open decoders for this run
                        for r_ in (rf, rb):
                            if r_:
                                r_.close()
                        run_end = k
                        while i + (run_end - k) + 1 < len(ks) and ks[i + (run_end - k) + 1] == run_end + 1:
                            run_end += 1
                        n_run = min(count, (run_end + 1) * seg) - k * seg
                        first = start + k * seg * step
                        rf = media.FrameReader(tf, first, n_run, raw=use_gpu, step=step)
                        rb = media.FrameReader(tb, first + off, n_run, raw=use_gpu, step=step)
                        rf.start(), rb.start()
                        last[0] = time.time()
                    prev = k
                    # room for the remaining parts + the joined file (a full copy) + 1 GB
                    with lock:
                        left = n_parts - shared["parts_done"]
                    pb = _est_part_bytes(parts, shared["parts_done"], ow, oh, preset, out_fps, seg)
                    check_space(parts, pb * left + pb * n_parts * 1.02 + 1e9, "剩下的段落＋合併")
                    n_k = min(seg, count - k * seg)
                    tmp = parts / f"part_{k:05d}.tmp.mkv"
                    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
                           "-f", "rawvideo", "-pix_fmt", in_fmt, "-s", f"{ow}x{oh}", "-r", out_rate_str, "-i", "-",
                           *venc, *COLOR_TAGS, "-bsf:v", bsf, "-an", str(tmp)]
                    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=ff.NO_WINDOW)
                    err_buf: list[bytes] = []
                    threading.Thread(target=lambda e=enc: err_buf.append(e.stderr.read()), daemon=True).start()
                    w = _Writer(enc)
                    w.start()
                    written = 0
                    try:
                        while written < n_k:
                            if stop.is_set():
                                raise _Stop()
                            if getattr(job, "_attempt", None) is not my_attempt:
                                raise Cancelled()      # this attempt was let go by the queue
                            if job.pause:
                                raise Paused()
                            if written % 300 == 0 and not in_schedule():
                                job.pause = True
                                job.schedule_paused = True
                                raise Paused()
                            fr = _get_frame(rf, job, last)
                            bk = _get_frame(rb, job, last)
                            if fr is None or bk is None:
                                err = rf.error or rb.error
                                if err:
                                    raise RuntimeError("解碼失敗：" + err)
                                # the decoder ran out of frames. Near the very end that is normal (frame counts
                                # in the files are a few frames off); earlier it means a broken read: drop this
                                # part so the retry redoes it, and never stop the other workers because of it
                                if k >= n_parts - 2:
                                    with lock:
                                        shared["eof_at"] = k if shared["eof_at"] is None else min(shared["eof_at"], k)
                                else:
                                    with lock:
                                        shared["early"].append((k, written, n_k))
                                    written = 0
                                break
                            fi_ = min(count_src - 1, (k * seg + written) * step)
                            if use_gpu:
                                if stab_S is not None:
                                    gs.set_rotation(stab_S[min(len(stab_S) - 1, fi_)])
                                if track_C is not None:
                                    row = track_C[min(len(track_C) - 1, fi_)]
                                    gs.set_lens_color(row[:6], row[6:])
                                if omega is not None:
                                    gs.set_omega(omega[min(len(omega) - 1, fi_)], rs_ms / 1000.0)
                                if expo_E is not None:
                                    gs.set_exposure(expo_E[min(len(expo_E) - 1, fi_)])
                                if lens_E is not None:
                                    gs.set_lens_tone(fusionlook.tone_tables(lens_E[min(len(lens_E) - 1, fi_)]))
                                if path_V is not None:
                                    j_ = min(len(path_V["yaw"]) - 1, fi_)
                                    gs.set_view({f_: float(path_V[f_][j_]) for f_ in ("yaw", "pitch", "roll", "fov")})
                                buf = ring[n_out % len(ring)]
                                n_out += 1
                                if gs.push(memoryview(fr), memoryview(bk), buf):
                                    w.put(buf, job)
                            else:
                                if enhance.active(opts.get("grade")):
                                    fr, bk = enhance.planes(fr, opts.get("grade")), enhance.planes(bk, opts.get("grade"))
                                ev_ = float(expo_E[min(len(expo_E) - 1, fi_)]) if expo_E is not None else 0.0
                                if track_C is not None or lens_E is not None:
                                    pf = dict(params)
                                    if track_C is not None:
                                        row = track_C[min(len(track_C) - 1, fi_)]
                                        pf["front"] = {**params["front"], "color": list(row[:6])}
                                        pf["back"] = {**params["back"], "color": list(row[6:])}
                                    if lens_E is not None:
                                        pf["_lens_tone"] = fusionlook.tone_tables(lens_E[min(len(lens_E) - 1, fi_)])
                                    w.put(rgb_to_yuv420(fin.rgb(stitch_rgb(fr, bk, maps, pf, full_range), ev_), False), job)
                                elif geo.lens_color_active(params):
                                    w.put(rgb_to_yuv420(fin.rgb(stitch_rgb(fr, bk, maps, params, full_range), ev_), False), job)
                                else:
                                    w.put(to_limited(fin.yuv(stitch_yuv420(fr, bk, maps, full_range), full_range, ev_), full_range), job)
                            written += 1
                            frame_done(k)
                        if use_gpu:
                            buf = ring[n_out % len(ring)]
                            n_out += 1
                            if gs.flush(buf):
                                w.put(buf, job)
                    finally:
                        try:
                            w.q.put(None, timeout=5)
                        except queue.Full:
                            enc.kill()
                        w.join(timeout=30)
                        if job.cancel or job.pause or stop.is_set():
                            enc.kill()
                        rc = enc.wait()
                    if rc != 0 or w.error:
                        raise RuntimeError("編碼失敗：" + b"".join(err_buf).decode(errors="replace")[-600:])
                    if written == 0:
                        tmp.unlink(missing_ok=True)
                        return
                    core.fsync_path(tmp)                # 斷電保護: a finished part is on the disk before it counts
                    os.replace(tmp, part_path(k))
                    with lock:
                        shared["parts_done"] += 1
            except _Stop:
                pass
            except BaseException as e:            # Paused / Cancelled / DiskFull / errors: stop the others too
                with lock:
                    errors.append(e)
                stop.set()
            finally:
                for r_ in (rf, rb):
                    if r_:
                        r_.close()
                if gs is not None:
                    gs.release()

        size_ = -(-len(missing) // n_workers)
        chunks = [missing[i * size_:(i + 1) * size_] for i in range(n_workers)]
        chunks = [c_ for c_ in chunks if c_]
        if len(chunks) == 1:
            worker(chunks[0])
        else:
            ths = [threading.Thread(target=worker, args=(c_,), daemon=True) for c_ in chunks]
            for t_ in ths:
                t_.start()
                time.sleep(1.0)                     # stagger start-up (GL context, decoder seek)
            for t_ in ths:
                t_.join()
        if errors:
            prio = [e for e in errors if not isinstance(e, (Paused, Cancelled))]
            raise (prio or errors)[0]
        left_ = [k for k in range(n_parts) if not part_path(k).exists()]
        if shared["early"]:
            k0, got, want = sorted(shared["early"])[0]
            core.log(f"early end of decode in part {k0 + 1}/{n_parts}: {got}/{want} frames {shared['early']}", "job_log.txt")
        eof_at = shared["eof_at"]
        if left_ and not (eof_at is not None and all(k > eof_at for k in left_)):
            raise RuntimeError(f"有 {len(left_)} 段沒有轉完（第 {left_[0] + 1} 段起，解碼提早結束）：會自動重試這幾段")

    # ---- join parts, add audio, metadata
    check_space(parts, sum(q.stat().st_size for q in parts.glob("part_*.mkv")) * 1.02 + 5e8, "合併成品")
    _gpu_release(job)                             # joining needs no GPU: the next job may convert now
    job.message = "合併各段並寫入 360 資訊…"
    job.t_fin0 = time.time()                      # 整體進度: finishing
    job.detail = ""
    plist = sorted(parts.glob("part_*.mkv"))
    plist = [p for p in plist if ".tmp" not in p.name]
    if not plist:
        raise RuntimeError("沒有產生任何影格")
    lst = parts / "parts.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in plist), encoding="utf-8")
    tmp_out = str(parts.parent / (Path(out).name + ".joining.mp4"))
    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
           "-f", "concat", "-safe", "0", "-i", str(lst)]
    if audio_file and audio_file.exists():
        cmd += ["-i", str(audio_file), "-map", "0:v", "-map", "1:a", "-shortest"]
    cmd += ["-c", "copy"]
    if preset["codec"] == "hevc":
        cmd += ["-tag:v", "hvc1"]
    cmd += ["-metadata", f"comment=Stitched with FusionStitch V{core.APP_VERSION} | expected={count / out_fps:.3f}s", tmp_out]
    r = ff.run(cmd)
    if r.returncode:
        raise RuntimeError("合併失敗：" + r.stderr.decode(errors="replace")[-600:])
    if spec.get("type", "equirect") == "equirect":
        spherical.inject_mp4(tmp_out, spatial_audio=4 if spatial and audio_file else 0)
    # verify before replacing anything
    info = ff.probe(tmp_out)
    expect = count / out_fps
    if info["duration"] < expect * 0.98 - 1:
        core.log(f"short output {tmp_out}: {info['duration']:.2f}s < {expect:.2f}s", "job_log.txt")
        os.remove(tmp_out)
        raise RuntimeError(f"合併後的影片比應有的短（{info['duration']:.1f} 秒 / 應有 {expect:.1f} 秒），沒有存成成品；會自動重試")
    core.fsync_path(tmp_out)
    out_size_ = os.path.getsize(tmp_out)
    import naming
    dest = str(naming.output_folder(job.clip.get("folder") or str(Path(out).parent.parent)) / Path(out).name) \
        if job.clip.get("folder") else out                 # 成品資料夾 can be changed while queued
    spare_note = ""
    try:                                                   # batch folder, or a spare when it is full
        import batches
        folder_, spare_note = batches.choose(job, out_size_)
        dest = str(folder_ / Path(out).name)
    except DiskFull:
        raise
    except Exception as e_:
        if type(e_).__name__ == "DiskFull":
            raise
        core.log(f"batch choose: {e_}", "job_log.txt")
    moving = ""
    if wd is not None and Path(tmp_out).parent != Path(dest).parent:
        moving = "，" + staging.deliver(job, tmp_out, dest)    # background move, the GPU goes on
    else:
        dest = core.place_file(tmp_out, dest)              # locked target -> name_2.mp4
    job.output = out = dest
    shutil.rmtree(parts, ignore_errors=True)
    el = time.time() - (job.started or time.time())
    try:
        est = estimate_bytes(preset, ow, oh, out_fps, count / out_fps)
        _update_calib(preset, est, out_size_)
        core.log(f"size {out}: actual {core.fmt_bytes(out_size_)}, estimate {core.fmt_bytes(est)}, "
                 f"{ow}x{oh} {enc_name}, {core.fmt_dur(el)}", "job_log.txt")
    except Exception:
        pass
    try:                                                   # 轉檔紀錄: sizes for the history, speed for the next estimate
        import history
        e_ = getattr(job, "est", None)
        loop_s = (time.time() - e_.t0) if e_ and e_.t0 else 0.0
        n_ = e_.n if e_ else 0
        history.add({"time": time.time(), "output": out, "clip": job.clip.get("name", ""), "num": job.clip.get("num", ""),
                     "src": history.source_bytes(src_clip), "out": out_size_, "duration": info["duration"],
                     "frames": n_, "fps": round(n_ / loop_s, 3) if loop_s > 5 and n_ > 50 else None,
                     "prep": round((e_.t0 - job.started), 1) if e_ and e_.t0 and job.started else None,
                     "wall": round(el, 1), "ow": ow, "oh": oh, "step": step, "preset": opts.get("preset"),
                     "speed": speed, "version": core.APP_VERSION,
                     "throttled": bool(getattr(job, "throttled", False)),   # slowed down: not used to learn speed
                     # stage lengths for the 整體進度 bar of future jobs
                     "prep2": round(e_.t0 - job.t_prep0, 1) if e_ and e_.t0 and getattr(job, "t_prep0", None) else None,
                     "loopt": round(job.t_fin0 - e_.t0, 1) if e_ and e_.t0 and getattr(job, "t_fin0", None) else None,
                     "fin": round(time.time() - job.t_fin0, 1) if getattr(job, "t_fin0", None) else None})
    except Exception as e:
        core.log(f"history record failed: {e}", "job_log.txt")
    sa = "，" + (audio_desc if audio_file else ("⚠ 無聲音" if speed == 1.0 and opts.get("audio", True) else "無聲音"))
    sp = "" if speed == 1.0 else (f"，{speed:g}x 縮時" if speed > 1 else f"，{speed:g}x 慢動作")
    job.message = f"完成：{info['duration']:.1f} 秒{sp}，{ow}×{oh}{sa}，{where}，{core.fmt_bytes(out_size_)}，用時 {core.fmt_dur(el)}{moving}{spare_note}"
