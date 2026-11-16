"""原廠亮度: how GoPro Fusion Studio sets the brightness of the picture and of each lens.

The camera exposes each lens on its own and records ISO gain (ISOG) and shutter time (SHUT)
for every frame in the GPMF telemetry: EV = log2(1 / (ISO gain x shutter)) is the light level
that lens was set for. Comparing our render (原廠色彩 table) with Fusion Studio's, moment by
moment, on two clips (night street, day street + garage):

1. Whole picture. Bright daylight scenes are rendered brighter than a fixed colour table makes
   them, dark scenes are not (correlation with the mean EV 0.83):
       extra exposure (EV) = K * (mean EV - E0), limited to LO .. HI

2. Each lens (V2.11). Away from the seam, the brightness of the two lens halves differs from a
   plain render in proportion to D = EV(this lens) - EV(other lens), in the same way in both clips
   and whichever lens it is (matched percentiles of each half, Fusion Studio / ours, per EV):
     - the lens set for the BRIGHTER side (D > 0) has its shadows and mid tones lifted, less and
       less towards the highlights, which stay (a sky does not wash out):
           x -> x * 2^(D * TONE_UP_A * (1 - x)^TONE_UP_P)
     - the lens set for the DARKER side (D < 0) is darkened a little everywhere except at white,
       and its black level is pulled down:
           x -> x * 2^(D * TONE_DN_A * hl(x)) + D * TONE_DN_OFF * (1 - x)^2
   Fusion Studio gives back part of the brightness relation that the two separate auto
   exposures took away. It does not match the lenses by picture content (色彩追蹤 does); what is
   left at the seam is the job of 接縫融合. Here x is the value of each colour channel of the lens
   pixel before the 原廠色彩 table (each channel on its own, like a camera tone curve).

Both are applied per frame, before the 原廠色彩 table, only in 原廠色彩 mode.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

K = 0.061
E0 = 1.5
LO, HI = -0.15, 0.5
SMOOTH_S = 2.0
TONE_UP_A, TONE_UP_P = 0.22, 1.5   # lens set for the brighter side: lift of the dark / mid tones (log2 per EV)
TONE_DN_A = 0.052                  # lens set for the darker side: darkening (log2 per EV) ...
TONE_DN_OFF = 0.013                # ... and black level (display value per EV)
TONE_LIMIT = 3.5                   # largest exposure difference used (EV)
TONE_N = 256
LENS_SMOOTH_S = 0.15               # the camera changes exposure from frame to frame: follow it closely
LUT_NAMES = ("gopro_fusion.cube", "gopro_match.cube")

_TEL: dict = {}


def active(grade: dict | None) -> bool:
    """True when the grade uses the 原廠色彩 table."""
    return bool(grade and grade.get("lut") and Path(str(grade["lut"])).name.lower() in LUT_NAMES)


def _scene_ev(files: list[str]):
    """(t, EV) per telemetry sample (one per video frame) over the clip timeline, or None
    without ISO / shutter data. Cached per file list."""
    key = tuple(files)
    if key in _TEL:
        return _TEL[key]
    import gyro
    import media
    tl = media.timeline(files)
    T, E = [], []
    t0 = 0.0
    for ci, path in enumerate(files):
        pk, data = gyro._packets(path)
        pos = 0
        for pts, dur, size in pk:
            buf = data[pos:pos + size]
            pos += size
            iso = sh = None
            for key_, _t, _s, _r, pl in gyro._klv(buf):
                if key_ != b"DEVC":
                    continue
                for k2, _t2, _s2, _r2, p2 in gyro._klv(pl):
                    if k2 != b"STRM":
                        continue
                    for k3, t3, _s3, _r3, p3 in gyro._klv(p2):
                        if k3 in (b"ISOG", b"SHUT") and t3 == ord("f") and len(p3) >= 4:
                            v = np.frombuffer(p3[:len(p3) // 4 * 4], ">f4").astype(np.float64)
                            if k3 == b"ISOG":
                                iso = v
                            else:
                                sh = v
            if iso is None or sh is None or not len(iso) or not len(sh):
                continue
            n = max(len(iso), len(sh))
            x = (np.arange(n) + 0.5) / n
            iso = np.interp(x, (np.arange(len(iso)) + 0.5) / len(iso), iso)
            sh = np.interp(x, (np.arange(len(sh)) + 0.5) / len(sh), sh)
            ok = (iso > 0) & (sh > 0)
            T.extend((t0 + pts + x[ok] * dur).tolist())
            E.extend(np.log2(1.0 / (iso[ok] * sh[ok])).tolist())
        t0 += tl.durations[ci]
    res = (np.array(T), np.array(E)) if len(T) >= 2 else None
    if len(_TEL) > 32:
        _TEL.clear()
    _TEL[key] = res
    return res


def _smooth(x, seconds, fps):
    n = max(1, int(round(seconds * fps)))
    if seconds <= 0 or len(x) <= 4 * n:
        return x
    k = np.exp(-0.5 * (np.arange(-2 * n, 2 * n + 1) / n) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(x, 2 * n, mode="edge"), k, mode="valid")


def exposure_curve(clip: dict, fps: float, first: int, count: int) -> np.ndarray | None:
    """Whole picture: per-frame extra exposure (EV), None when the clip has no exposure telemetry."""
    try:
        f = _scene_ev(clip["front"])
        b = _scene_ev(clip["back"])
    except Exception:
        return None
    if f is None and b is None:
        return None
    ft = (np.arange(first, first + count) + 0.5) / fps
    parts = [np.interp(ft, *x) for x in (f, b) if x is not None]
    ev = _smooth(np.mean(parts, axis=0), SMOOTH_S, fps)       # the camera changes exposure in small steps
    return np.clip(K * (ev - E0), LO, HI).astype(np.float32)


def lens_diff(clip: dict, fps: float, first: int, count: int) -> np.ndarray | None:
    """Per frame: EV of the front lens minus EV of the back lens (count,), None without telemetry
    of both lenses."""
    try:
        f = _scene_ev(clip["front"])
        b = _scene_ev(clip["back"])
    except Exception:
        return None
    if f is None or b is None:
        return None
    ft = (np.arange(first, first + count) + 0.5) / fps
    d = _smooth(np.interp(ft, *f) - np.interp(ft, *b), LENS_SMOOTH_S, fps)
    return np.clip(d, -TONE_LIMIT, TONE_LIMIT).astype(np.float32)


_TX = np.arange(TONE_N, dtype=np.float64) / (TONE_N - 1)


def tone_table(d: float) -> np.ndarray:
    """Gain by brightness (TONE_N,) for a lens whose exposure is d EV above (below) the other lens'."""
    x = _TX
    if d >= 0:
        return (2.0 ** (d * TONE_UP_A * (1 - x) ** TONE_UP_P)).astype(np.float32)
    t = np.clip((x - 0.86) / 0.14, 0, 1)
    hl = 1 - t * t * (3 - 2 * t)
    y = x * 2.0 ** (d * TONE_DN_A * hl) + d * TONE_DN_OFF * (1 - x) ** 2
    return (np.maximum(y, 0) / np.maximum(x, 1e-4)).astype(np.float32)


def tone_tables(d_front: float) -> np.ndarray:
    """(2, TONE_N) float32: row 0 front lens, row 1 back lens, for EV front - EV back = d_front."""
    return np.stack([tone_table(float(d_front)), tone_table(-float(d_front))])


def apply_tone(rgb: np.ndarray, table: np.ndarray) -> np.ndarray:
    """rgb (..., 3) display values of one lens (after its colour match) -> with its tone table,
    every channel by its own value (like a camera tone curve; this is what the comparison with
    Fusion Studio shows: the lifted lens gets warmer highlights and less coloured shadows)."""
    return rgb * np.interp(np.clip(rgb, 0, 1), _TX, table).astype(np.float32)


# ------------------------------------------------------------------ whole-clip curves (preview, stills)
_CACHE: dict = {}
_PENDING: set = set()


def _key(clip, fps, total):
    return tuple(clip["front"]), tuple(clip["back"]), round(fps, 3), int(total)


def _clip_curves(clip: dict, fps: float, total: int):
    """(whole-picture curve, per-lens curves) of the whole clip, computed once per clip."""
    key = _key(clip, fps, total)
    if key not in _CACHE:
        if len(_CACHE) > 16:
            _CACHE.clear()
        _CACHE[key] = (exposure_curve(clip, fps, 0, int(total)), lens_diff(clip, fps, 0, int(total)))
    return _CACHE[key]


def clip_curve(clip: dict, fps: float, total: int) -> np.ndarray | None:
    return _clip_curves(clip, fps, total)[0]


def _total(clip, t, fps):
    import media
    try:
        return max(media.timeline(clip["front"]).total_frames, int(t * fps) + 1)
    except Exception:
        return int(t * fps) + 1


def _pick(c, t, fps):
    if c is None or not len(c):
        return None
    return c[min(len(c) - 1, max(0, int(t * fps)))]


def exposure_at(clip: dict, t: float, fps: float = 30.0) -> float:
    v = _pick(_clip_curves(clip, fps, _total(clip, t, fps))[0], t, fps)
    return float(v) if v is not None else 0.0


def lens_at(clip: dict, t: float, fps: float = 30.0):
    """EV front - EV back at time t, or None."""
    v = _pick(_clip_curves(clip, fps, _total(clip, t, fps))[1], t, fps)
    return float(v) if v is not None else None


def _nowait(clip, t, fps):
    """The cached curves of the clip, or None while they are being read in the background
    (the first call for a clip reads the telemetry, about 1 s: opening a clip must not stall)."""
    import threading
    import media
    try:
        total = media.timeline(clip["front"]).total_frames
    except Exception:
        return None
    key = _key(clip, fps, total)
    if key in _CACHE:
        return _CACHE[key]
    if key not in _PENDING:
        _PENDING.add(key)

        def work():
            try:
                _clip_curves(clip, fps, total)
            finally:
                _PENDING.discard(key)
        threading.Thread(target=work, daemon=True).start()
    return None


def exposure_at_nowait(clip: dict, t: float, fps: float) -> float:
    c = _nowait(clip, t, fps)
    v = _pick(c[0], t, fps) if c else None
    return float(v) if v is not None else 0.0


def lens_at_nowait(clip: dict, t: float, fps: float):
    c = _nowait(clip, t, fps)
    v = _pick(c[1], t, fps) if c else None
    return float(v) if v is not None else None


def preview_active() -> bool:
    import core
    return (core.cfg("color_mode") or "auto") == "gopro" and bool(core.gopro_lut_path()) and bool(core.cfg("gopro_exposure", True))


def lens_active() -> bool:
    import core
    return (core.cfg("color_mode") or "auto") == "gopro" and bool(core.gopro_lut_path()) and bool(core.cfg("gopro_lens_exposure", True))
