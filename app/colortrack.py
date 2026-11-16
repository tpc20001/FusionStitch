"""Time-varying lens colour matching ("色彩追蹤").

The static auto colour match uses one correction for the whole clip. When the
light changes (garage -> sunlight, tunnel, clouds) the two lenses drift apart
differently, so here the overlap ring is measured every `interval` seconds
over the whole clip, a correction is fitted per sample, outliers are removed
(median) and the curve is smoothed in time (Gaussian, `smooth` seconds). Every
frame then gets its own correction by linear interpolation.

Track format (stored in the clip project as "color_track"):
  {"t": [seconds...], "c": [[front 6, back 6]...], "interval", "smooth",
   "before", "static", "after"}
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import align
import geometry as geo
import media


def _ring(params, w, h):
    base = geo.merge_params(params, {"front": {"color": geo.IDENTITY_COLOR}, "back": {"color": geo.IDENTITY_COLOR}})
    dirs = align._ring_dirs(base, w, h)
    half = math.radians(base["fov"]) / 2
    fx, fy, tf_ = geo.project(dirs, base, "front", w, h)
    bx, by, tb_ = geo.project(dirs, base, "back", w, h)
    ok = (tf_ < half) & (tb_ < half)
    gf = geo.vignette_gain(tf_[ok], base, "front")[:, None]
    gb = geo.vignette_gain(tb_[ok], base, "back")[:, None]
    return fx[ok], fy[ok], bx[ok], by[ok], gf, gb


def _rgb(planes, full):
    import jobs
    return jobs.yuv420_to_rgb(*planes, full=full)


def analyse(clip: dict, params: dict, interval: float = 3.0, smooth: float = 6.0, progress=None,
            cancel=None) -> dict:
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    full = tf.color_range == "pc"
    sw, sh = (tf.width // 2) // 2 * 2, (tf.height // 2) // 2 * 2       # half-res decode is plenty for statistics
    fx, fy, bx, by, gf, gb = _ring(params, sw, sh)
    off = int(round(params.get("back_offset", 0) * tf.fps))
    dur = tf.duration
    times = np.arange(min(interval / 2, dur / 2), max(dur - 0.2, 0.01), interval)
    if len(times) < 2:
        times = np.array([dur * 0.25, dur * 0.75])
    done = [0]
    # overall brightness of the picture (inside both lens circles), for 自動亮度
    yy, xx = np.mgrid[0:sh, 0:sw]
    def circle(L):
        return (xx - L["cx"] * sw) ** 2 + (yy - L["cy"] * sh) ** 2 < (L["r"] * sh * 0.9) ** 2
    mf, mb = circle(params["front"]), circle(params["back"])
    lums = {}
    wbs = {}

    def sample(t):
        if cancel and cancel():
            return None
        fi = max(0, min(tf.total_frames - 1, int(round(t * tf.fps))))
        bi = max(0, min(tb.total_frames - 1, fi + off))
        try:
            f = _rgb(media.read_frame(tf, fi, scale=(sw, sh)), full)
            b = _rgb(media.read_frame(tb, bi, scale=(sw, sh)), full)
        except Exception:
            return None
        luma = lambda img: img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722
        lums[float(t)] = float(np.median(np.concatenate([luma(f)[mf][::7], luma(b)[mb][::7]])))
        # white balance of the whole picture (linear light, clipped pixels left out), for 自動白平衡
        px = np.concatenate([f[mf][::5], b[mb][::5]])
        px = px[(px.max(1) < 0.97) & (px.min(1) > 0.01)]
        if len(px) > 500:
            m = (px.astype(np.float64) ** 2.2).mean(0) + 1e-6
            wbs[float(t)] = (math.log2(m[0] / m[1]), math.log2(m[2] / m[1]))
        a = np.stack([align._sample(np.ascontiguousarray(f[..., k]), fx, fy) for k in range(3)], -1) * gf
        c = np.stack([align._sample(np.ascontiguousarray(b[..., k]), bx, by) for k in range(3)], -1) * gb
        good = (a.max(1) < 0.97) & (c.max(1) < 0.97) & (a.min(1) > 0.02) & (c.min(1) > 0.02)
        done[0] += 1
        if progress:
            progress(done[0] / len(times))
        if good.sum() < 800:
            return None
        return a[good], c[good]

    with ThreadPoolExecutor(4) as ex:
        samples = list(ex.map(sample, times))
    if cancel and cancel():
        raise RuntimeError("已取消")
    g_f, g_b = float(gf.mean()), float(gb.mean())
    keep = [i for i, s in enumerate(samples) if s is not None]
    if len(keep) < 2:
        raise RuntimeError("可用的取樣太少（畫面太暗或過曝）")
    T = times[keep]
    S = [samples[i] for i in keep]
    raw = np.array([sum((align._fit_color(F, B, g_f, g_b)[n] for n in ("front", "back")), []) for F, B in S])
    # outliers (a frame where a car fills one side of the seam): running median over 5 samples
    med = np.array([np.median(raw[max(0, i - 2):i + 3], axis=0) for i in range(len(raw))])
    # smooth in time (Gaussian in seconds, irregular spacing after dropped samples)
    sig = max(1e-3, smooth)
    W = np.exp(-0.5 * ((T[:, None] - T[None, :]) / sig) ** 2)
    W /= W.sum(1, keepdims=True)
    curve = W @ med
    # the whole-clip best static correction, for comparison
    static = align._fit_color(np.concatenate([F for F, _ in S]), np.concatenate([B for _, B in S]), g_f, g_b)

    def err(coef_row, F, B):
        c = {"front": list(coef_row[:6]), "back": list(coef_row[6:])}
        return align._color_error(c, F, B, g_f, g_b)
    ident = np.array(geo.IDENTITY_COLOR * 2)
    st_row = np.array(static["front"] + static["back"])
    before = float(np.median([err(ident, F, B) for F, B in S]))
    st_err = float(np.median([err(st_row, F, B) for F, B in S]))
    after = float(np.median([err(curve[i], F, B) for i, (F, B) in enumerate(S)]))
    # brightness curve: log2 of the median luma, smoothed over 8 s
    LT = np.array(sorted(lums))
    if len(LT) >= 2:
        LV = np.log2(np.maximum(np.array([lums[k] for k in LT]), 1e-3))
        Wl = np.exp(-0.5 * ((LT[:, None] - LT[None, :]) / 8.0) ** 2)
        LV = (Wl / Wl.sum(1, keepdims=True)) @ LV
    else:
        LV = np.zeros(len(LT))
    WT = np.array(sorted(wbs))
    if len(WT) >= 2:
        WV = np.array([wbs[k] for k in WT])
        WV = np.array([np.median(WV[max(0, i - 2):i + 3], axis=0) for i in range(len(WV))])
        Ww = np.exp(-0.5 * ((WT[:, None] - WT[None, :]) / WB_SMOOTH) ** 2)
        WV = (Ww / Ww.sum(1, keepdims=True)) @ WV
    else:
        WV = np.zeros((len(WT), 2))
    return {"t": T.round(3).tolist(), "c": curve.round(5).tolist(), "interval": interval, "smooth": smooth,
            "before": before, "static": st_err, "after": after, "samples": len(T), "of": len(times),
            "lum_t": LT.round(3).tolist(), "lum": LV.round(4).tolist(),
            "wb_t": WT.round(3).tolist(), "wb": WV.round(4).tolist()}


WB_SMOOTH = 6.0           # seconds
WB_REF = (0.0, 0.0)       # log2 R/G, B/G (linear) the clip's typical white balance is nudged towards
WB_SHARE = 0.0            # how much of the way
WB_LIMIT = 1.0            # largest correction (linear log2)


def wb_gains(track: dict | None, fps: float, first: int, count: int, strength: float) -> np.ndarray | None:
    """Per-frame RGB gain (count, 3) for 自動白平衡: the white balance of every moment is pulled
    towards the clip's typical white balance by `strength` (0..1) (the camera's own white balance
    drifts with the light; GoPro Fusion Studio evens it out). Gains act on the display values of
    both lenses (before the 原廠色彩 table) and keep the luma."""
    if not track or not track.get("wb_t") or strength <= 0:
        return None
    ft = (np.arange(first, first + count) + 0.5) / fps
    return _wb(track, np.asarray(track["wb_t"]), np.asarray(track["wb"]), ft, strength)


def _wb(track, WT, WV, ft, strength):
    med = np.median(WV, axis=0)
    ref = med + WB_SHARE * (np.asarray(WB_REF) - med)
    cur = np.stack([np.interp(ft, WT, WV[:, k]) for k in range(2)], -1)
    d = np.clip(strength * (ref - cur), -WB_LIMIT, WB_LIMIT) / 2.2        # linear log2 -> display log2
    g = np.stack([2 ** d[:, 0], np.ones(len(ft)), 2 ** d[:, 1]], -1)
    g /= (g @ np.array([0.2126, 0.7152, 0.0722]))[:, None]
    return g.astype(np.float32)


def wb_at(track, t, strength):
    if not track or not track.get("wb_t") or strength <= 0:
        return None
    return _wb(track, np.asarray(track["wb_t"]), np.asarray(track["wb"]), np.array([float(t)]), strength)[0]


def apply_gain(row, g):
    """Lens colour row (gains 3, offsets 3) followed by an RGB gain."""
    row = list(row)
    return [row[k] * float(g[k]) for k in range(3)] + [row[3 + k] * float(g[k]) for k in range(3)]


TARGET_LUM = 0.40          # median display brightness of a "normal" daylight picture
ABS_SHARE = 0.15           # how much of the way the clip's own level is pulled towards TARGET_LUM
NIGHT_LUM = 0.10           # below this the picture is a night scene: lifting it mostly lifts noise


def _ev(track: dict, L, strength: float):
    """Exposure offset (EV) for log2-luma L. The reference is the clip's own typical brightness
    (so a night ride stays a night ride), nudged a little towards a normal level; stretches darker
    or brighter than that (tunnel, shade, sun) are pulled back towards it by `strength`."""
    LV = np.asarray(track["lum"])
    med = float(np.median(LV))
    ref = med + ABS_SHARE * (math.log2(TARGET_LUM) - med)
    ev = strength * 2.2 * (ref - np.asarray(L))
    # very dark scenes: limit the lift (noise): +1.5 EV allowed at NIGHT_LUM, only +0.3 EV at 1/4 of it
    k = np.clip((np.asarray(L) - math.log2(NIGHT_LUM / 4)) / 2.0, 0, 1)
    hi = 0.3 + 1.2 * k * k
    return np.clip(ev, -1.0, hi)


def exposure_curve(track: dict | None, fps: float, first: int, count: int, strength: float) -> np.ndarray | None:
    """Per-frame exposure offset (EV) for 自動亮度, `strength` 0..1, limited to -1 .. +1.5 EV."""
    if not track or not track.get("lum_t") or strength <= 0:
        return None
    ft = (np.arange(first, first + count) + 0.5) / fps
    L = np.interp(ft, np.asarray(track["lum_t"]), np.asarray(track["lum"]))
    return _ev(track, L, strength).astype(np.float32)


def exposure_at(track, t, strength):
    if not track or not track.get("lum_t") or strength <= 0:
        return 0.0
    L = float(np.interp(t, np.asarray(track["lum_t"]), np.asarray(track["lum"])))
    return float(_ev(track, L, strength))


def at(track: dict | None, t: float):
    """(front6, back6) for time t (seconds), or None."""
    if not track or not track.get("t"):
        return None
    T = np.asarray(track["t"])
    C = np.asarray(track["c"])
    row = np.array([np.interp(t, T, C[:, k]) for k in range(C.shape[1])])
    return row[:6].tolist(), row[6:].tolist()


def per_frame(track: dict | None, fps: float, first: int, count: int) -> np.ndarray | None:
    """(count, 12) correction per frame."""
    if not track or not track.get("t"):
        return None
    T = np.asarray(track["t"])
    C = np.asarray(track["c"])
    ft = (np.arange(first, first + count) + 0.5) / fps
    return np.stack([np.interp(ft, T, C[:, k]) for k in range(C.shape[1])], -1)
