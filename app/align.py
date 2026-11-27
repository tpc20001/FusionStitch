"""Automatic seam alignment and exposure matching from the overlap band.

Both lenses see a ~10° wide ring around the seam. We sample that ring from
both lenses and optimise lens centres / radii / back-lens rotation to make
the two views agree (high-pass NCC, so exposure differences don't matter).
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.optimize import minimize

import geometry as geo
import media
from imageio_util import imread

GRID_W = 1024


def _frames(clip: dict, times: list[float], params: dict):
    """Gray frames (front, back) at the given seconds, decoded on the GPU."""
    if clip["kind"] == "photo":
        f, b = imread(clip["front"][0]), imread(clip["back"][0])
        return [(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY))]
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    off = int(round(params.get("back_offset", 0) * tf.fps))
    out = []
    for t in times:
        fi = max(0, min(tf.total_frames - 1, int(round(t * tf.fps))))
        bi = max(0, min(tb.total_frames - 1, fi + off))
        fy = media.read_frame(tf, fi)[0].copy()
        by = media.read_frame(tb, bi)[0].copy()
        out.append((fy, by))
    return out


def _sample(img, px, py):
    n = px.size  # remap is limited to 32767 columns -> sample in a 2D block
    cols = 1024
    pad = (-n) % cols
    mx = np.concatenate([px, np.zeros(pad, np.float32)]).reshape(-1, cols)
    my = np.concatenate([py, np.zeros(pad, np.float32)]).reshape(-1, cols)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()[:n]


def _ring_dirs(params, src_w, src_h):
    d = geo.equirect_dirs(GRID_W, GRID_W // 2).reshape(-1, 3)
    _, _, tf = geo.project(d, params, "front", src_w, src_h)
    keep = np.abs(np.degrees(tf) - 90) < (params["fov"] / 2 - 90) + 2
    return d[keep]


def _vec_to_params(x, base):
    """Only the back lens moves: the front lens defines the frame and the scale
    (a common scale error of both lenses is invisible at the seam).
    x = [cx, cy, r, yaw, pitch, roll] in 1e-3 / 0.1° units."""
    p = geo.merge_params(base, {})
    b0, b = base["back"], p["back"]
    b["cx"], b["cy"], b["r"] = b0["cx"] + x[0] * 1e-3, b0["cy"] + x[1] * 1e-3, b0["r"] + x[2] * 1e-3
    b["yaw"], b["pitch"], b["roll"] = b0["yaw"] + x[3] * 0.1, b0["pitch"] + x[4] * 0.1, b0["roll"] + x[5] * 0.1
    return p


def auto_align(clip: dict, params: dict, times: list[float]) -> dict:
    frames = _frames(clip, times, params)
    src_h, src_w = frames[0][0].shape
    dirs = _ring_dirs(params, src_w, src_h)
    half = math.radians(params["fov"]) / 2

    def level(scale, blur, band):
        """Band-passed frames at one pyramid level (coarse levels see big offsets)."""
        w, h = max(64, int(src_w * scale)), max(64, int(src_h * scale))
        def hpass(g):
            g = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
            g = cv2.GaussianBlur(g, (0, 0), blur)
            return g - cv2.GaussianBlur(g, (0, 0), band)
        return w, h, [(hpass(f), hpass(b)) for f, b in frames]

    def make_cost(w, h, hp):
        def cost(x):
            p = _vec_to_params(x, params)
            fx, fy, tf = geo.project(dirs, p, "front", w, h)
            bx, by, tb = geo.project(dirs, p, "back", w, h)
            ok = (tf < half) & (tb < half)
            if ok.sum() < 500:
                return 2.0
            total = 0.0
            for f, b in hp:
                a, c = _sample(f, fx[ok], fy[ok]), _sample(b, bx[ok], by[ok])
                a, c = a - a.mean(), c - c.mean()
                total += 1 - float((a * c).sum() / (math.sqrt((a * a).sum() * (c * c).sum()) + 1e-6))
            return total / len(hp)
        return cost

    bounds = [(-25, 25)] * 3 + [(-40, 40)] * 3   # ±2.5% centres/radius, ±4° rotation
    lo, hi = np.array(bounds).T
    x = np.zeros(6)
    final_cost = make_cost(*level(0.5, 1.0, 5))
    before = final_cost(x)
    # coarse-to-fine; centre/radius/roll first, the near-degenerate yaw/pitch
    # (they mostly duplicate cx/cy) only join for the last, finest pass
    stages = ((0.125, 2.5, 10, 6, [0, 1, 2, 5]),
              (0.25, 1.5, 6, 2, [0, 1, 2, 5]),
              (0.5, 1.0, 5, 0.7, [0, 1, 2, 3, 4, 5]))
    for scale, blur, band, step, act in stages:
        c = make_cost(*level(scale, blur, band))

        def cost(v, c=c, act=act):
            full = x.copy()
            full[act] = v
            return c(np.clip(full, lo, hi))

        for _ in range(3):  # Nelder-Mead restarts escape stalled simplices
            v0 = x[act]
            simplex = np.vstack([v0] + [v0 + step * e for e in np.eye(len(act))])
            res = minimize(cost, v0, method="Nelder-Mead",
                           options={"initial_simplex": simplex, "xatol": step / 20,
                                    "fatol": 1e-4, "maxfev": 500})
            moved = np.abs(res.x - v0).max()
            x[act] = res.x
            x = np.clip(x, lo, hi)
            if moved < step / 4:
                break
    after = final_cost(x)
    if after >= before:
        return {"params": params, "before": before, "after": before, "improved": False}
    return {"params": _vec_to_params(x, params), "before": before, "after": after, "improved": True}


def auto_exposure(clip: dict, params: dict, times: list[float]) -> dict:
    frames = _frames(clip, times, params)
    src_h, src_w = frames[0][0].shape
    dirs = _ring_dirs(params, src_w, src_h)
    half = math.radians(params["fov"]) / 2
    fx, fy, tf = geo.project(dirs, params, "front", src_w, src_h)
    bx, by, tb = geo.project(dirs, params, "back", src_w, src_h)
    ok = (tf < half) & (tb < half)
    # black level: 0 for photos and full-range (yuvj, GoPro) video, 16 only for limited-range video
    off = 0 if clip["kind"] == "photo" or media.timeline(clip["front"]).color_range == "pc" else 16
    rf, rb = [], []
    for f, b in frames:
        a = _sample(f.astype(np.float32), fx[ok], fy[ok]) - off
        c = _sample(b.astype(np.float32), bx[ok], by[ok]) - off
        good = (a > 8) & (c > 8) & (a < 225) & (c < 225)  # skip clipped pixels
        rf.append(a[good]), rb.append(c[good])
    a, c = np.concatenate(rf), np.concatenate(rb)
    # account for vignette compensation already applied at the seam
    gf = geo.vignette_gain(tf[ok], params, "front").mean() / 2 ** params["front"]["ev"]
    gb = geo.vignette_gain(tb[ok], params, "back").mean() / 2 ** params["back"]["ev"]
    d = math.log2((np.median(a) * gf + 1e-3) / (np.median(c) * gb + 1e-3))
    p = geo.merge_params(params, {"front": {"ev": -d / 2}, "back": {"ev": d / 2}})
    return {"params": p, "ev_diff": d}


SWEEP = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]      # 10 % ... 90 % of the clip


def sweep_times(clip: dict) -> list[float]:
    if clip["kind"] == "photo":
        return [0.0]
    d = media.timeline(clip["front"]).duration
    return [d * p for p in SWEEP]


def _color_samples(clip, params, times):
    """Overlap-ring RGB of both lenses per sampled frame (as the blend sees them)."""
    import jobs
    from imageio_util import imread
    if clip["kind"] == "photo":
        fb = [(imread(clip["front"][0])[..., ::-1].astype(np.float32) / 255,
               imread(clip["back"][0])[..., ::-1].astype(np.float32) / 255)]
    else:
        tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
        off = int(round(params.get("back_offset", 0) * tf.fps))
        full = tf.color_range == "pc"
        fb = []
        for t in times:
            fi = max(0, min(tf.total_frames - 1, int(round(t * tf.fps))))
            bi = max(0, min(tb.total_frames - 1, fi + off))
            fb.append((jobs.yuv420_to_rgb(*media.read_frame(tf, fi), full=full),
                       jobs.yuv420_to_rgb(*media.read_frame(tb, bi), full=full)))
    src_h, src_w = fb[0][0].shape[:2]
    base = geo.merge_params(params, {"front": {"color": geo.IDENTITY_COLOR}, "back": {"color": geo.IDENTITY_COLOR}})
    dirs = _ring_dirs(base, src_w, src_h)
    half = math.radians(base["fov"]) / 2
    fx, fy, tf_ = geo.project(dirs, base, "front", src_w, src_h)
    bx, by, tb_ = geo.project(dirs, base, "back", src_w, src_h)
    ok = (tf_ < half) & (tb_ < half)
    gf = geo.vignette_gain(tf_[ok], base, "front")[:, None]
    gb = geo.vignette_gain(tb_[ok], base, "back")[:, None]
    out = []
    for f, b in fb:
        a = np.stack([_sample(np.ascontiguousarray(f[..., k]), fx[ok], fy[ok]) for k in range(3)], -1) * gf
        c = np.stack([_sample(np.ascontiguousarray(b[..., k]), bx[ok], by[ok]) for k in range(3)], -1) * gb
        good = (a.max(1) < 0.97) & (c.max(1) < 0.97) & (a.min(1) > 0.02) & (c.min(1) > 0.02)
        if good.sum() > 1500:
            out.append((a[good], c[good]))
    return out, float(gf.mean()), float(gb.mean())


_Q = np.linspace(3, 97, 25)


def _fit_color(F, B, gf, gb):
    coef = {"front": [1.0] * 3 + [0.0] * 3, "back": [1.0] * 3 + [0.0] * 3}
    for k in range(3):
        qf, qb = np.percentile(F[:, k], _Q), np.percentile(B[:, k], _Q)
        t = (qf + qb) / 2
        for name, qs, g in (("front", qf, gf), ("back", qb, gb)):
            a_, b_ = np.polyfit(qs, t, 1)
            coef[name][k] = float(np.clip(a_, 0.6, 1.6))
            coef[name][3 + k] = float(np.clip(b_, -0.15, 0.15)) / max(g, 1e-3)
    return coef


def _color_error(coef, F, B, gf, gb):
    """Mean level difference (0..255) between the corrected lenses in this frame."""
    e = 0.0
    for k in range(3):
        qf, qb = np.percentile(F[:, k], _Q), np.percentile(B[:, k], _Q)
        cf = qf * coef["front"][k] + coef["front"][3 + k] * gf
        cb = qb * coef["back"][k] + coef["back"][3 + k] * gb
        e += float(np.abs(cf - cb).mean())
    return e / 3 * 255


def auto_color(clip: dict, params: dict, times: list[float] | None = None) -> dict:
    """Match the two lenses' colour and levels (gain + offset per channel, per lens).

    Samples the clip at 10 %, 20 % ... 90 %, builds candidate corrections (one
    per sampled frame, the pooled fit and the per-coefficient median), scores
    every candidate on *all* sampled frames and keeps the one with the lowest
    median colour difference -- the setting that works best for the whole clip,
    not just one moment. Quantiles instead of pixel pairs, so seam parallax
    does not matter.
    """
    times = times or sweep_times(clip)
    samples, gf, gb = _color_samples(clip, params, times)
    if not samples:
        raise RuntimeError("接縫區可用的畫面太少（太暗或過曝）")
    ident = {"front": list(geo.IDENTITY_COLOR), "back": list(geo.IDENTITY_COLOR)}
    cands = {"單格 %d%%" % round(SWEEP[i] * 100) if clip["kind"] == "video" and i < len(SWEEP) else f"單格 {i + 1}":
             _fit_color(F, B, gf, gb) for i, (F, B) in enumerate(samples)}
    cands["全部合併"] = _fit_color(np.concatenate([F for F, _ in samples]), np.concatenate([B for _, B in samples]), gf, gb)
    per = list(cands.values())
    cands["各格中位數"] = {n: [float(np.median([c[n][j] for c in per])) for j in range(6)] for n in ("front", "back")}

    def score(c):
        return float(np.median([_color_error(c, F, B, gf, gb) for F, B in samples]))
    before = score(ident)
    scored = sorted(((score(c), name, c) for name, c in cands.items()), key=lambda x: x[0])
    best_err, best_name, best = scored[0]
    if best_err >= before:
        best, best_name, best_err = ident, "不需修正", before
    out = geo.merge_params(params, {"front": {"color": best["front"]}, "back": {"color": best["back"]}})
    allF = np.concatenate([F for F, _ in samples]); allB = np.concatenate([B for _, B in samples])
    return {"params": out, "before": before, "after": best_err, "chosen": best_name, "frames": len(samples),
            "ranking": [(n, round(e, 2)) for e, n, _ in scored[:5]],
            "front_mean": (allF.mean(0) * 255).round(1).tolist(), "back_mean": (allB.mean(0) * 255).round(1).tolist()}
