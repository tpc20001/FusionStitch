"""Automatic gyro <-> picture time offset for one clip.

Frame pairs 5 frames apart are matched on the stitched panorama (ORB); the
visual camera rotation (Kabsch + RANSAC on the unit sphere) is compared with
the gyro rotation integrated over the same interval, for offsets from -200 to
+200 ms. The offset with the smallest median disagreement wins (a parabola
through the best three gives sub-step precision).
"""
from __future__ import annotations

import math

import numpy as np

import geometry as geo
import gyro
import media

W, H = 1024, 512


def _kabsch(a, b):
    U, S, Vt = np.linalg.svd(a.T @ b)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    return Vt.T @ np.diag([1, 1, d]) @ U.T


def estimate(clip: dict, params: dict, n_pairs: int = 60, gap: int = 5, progress=None) -> dict:
    import cv2
    import jobs
    tel = gyro.load(clip["front"])
    if tel is None:
        raise RuntimeError("這段影片沒有陀螺儀資料")
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    P = geo.merge_params(params, {"orient": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}})
    P.pop("_stab", None)
    maps = geo.StitchMaps(P, tf.width, tf.height, W, H)
    dirs = geo.equirect_dirs(W, H).astype(np.float64)
    orb = cv2.ORB_create(2500)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    pairs = []
    d = tf.duration
    for k in range(n_pairs):
        fi = int(d * (0.05 + 0.9 * k / max(1, n_pairs - 1)) * tf.fps)
        if fi + gap >= tf.total_frames:
            continue
        if progress:
            progress((k + 1) / n_pairs)
        try:
            ys = []
            for f_ in (fi, fi + gap):
                fr = media.read_frame(tf, f_)
                bk = media.read_frame(tb, min(tb.total_frames - 1, f_))
                ys.append(jobs.stitch_yuv420(fr, bk, maps)[0])
        except Exception:
            continue
        k1, d1 = orb.detectAndCompute(ys[0][40:-40], None)
        k2, d2 = orb.detectAndCompute(ys[1][40:-40], None)
        if d1 is None or d2 is None:
            continue
        m = bf.match(d1, d2)
        if len(m) < 50:
            continue
        p1 = np.array([k1[x.queryIdx].pt for x in m]); p2 = np.array([k2[x.trainIdx].pt for x in m])
        v1 = dirs[(p1[:, 1] + 40).astype(int), p1[:, 0].astype(int)]
        v2 = dirs[(p2[:, 1] + 40).astype(int), p2[:, 0].astype(int)]
        # drop points that move with the camera (rider, handlebars): they say "no rotation"
        moving = np.linalg.norm(v1 - v2, axis=1) > 0.003
        if moving.sum() < 40:
            continue
        v1, v2 = v1[moving], v2[moving]
        rng = np.random.default_rng(k)
        best, bi = None, 0
        for _ in range(250):
            s = rng.choice(len(v1), 3, replace=False)
            R = _kabsch(v1[s], v2[s])
            inl = np.linalg.norm(v1 @ R.T - v2, axis=1) < 0.006
            if inl.sum() > bi:
                best, bi = inl, inl.sum()
        if bi < 25:
            continue
        R = _kabsch(v1[best], v2[best])
        rv, _ = cv2.Rodrigues(R.T)                     # camera rotation, camera frame
        rv = rv.ravel()
        if np.linalg.norm(rv) < math.radians(0.6):     # too little rotation to time anything
            continue
        pairs.append((fi / tf.fps, (fi + gap) / tf.fps, rv))
    if len(pairs) < 6:
        raise RuntimeError("可用的畫面太少（轉動太小或畫面太暗），維持目前的時間偏移")
    gt, gw = tel["gt"], tel["gyro"]

    def err(off):
        e = []
        for a, b, rv in pairs:
            s = (gt >= a + off) & (gt < b + off)
            if s.sum() < 10:
                continue
            g = gw[s].sum(0) * (b - a) / s.sum()
            e.append(np.linalg.norm(g - rv) / max(np.linalg.norm(rv), 1e-6))
        return float(np.median(e)) if e else 9.0
    offs = np.arange(-0.2, 0.2001, 0.01)
    errs = np.array([err(o) for o in offs])
    i = int(np.argmin(errs))
    best = offs[i]
    if 0 < i < len(offs) - 1:                          # parabolic refinement
        a, b, c = errs[i - 1], errs[i], errs[i + 1]
        den = a - 2 * b + c
        if den > 1e-9:
            best = offs[i] + 0.01 * 0.5 * (a - c) / den
    return {"offset_ms": round(float(best) * 1000), "error": float(errs[i]), "pairs": len(pairs),
            "curve": [(round(float(o) * 1000), round(float(e_), 3)) for o, e_ in zip(offs, errs)]}
