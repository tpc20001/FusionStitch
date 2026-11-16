"""Find the direction of travel (where the vehicle goes) in camera coordinates.

Between two frames ~0.1 s apart, far scenery only rotates while near scenery
also slides away from the direction of travel (the "focus of expansion").
The camera rotation comes from the gyro (or from the matches when there is no
gyro), is removed, and every remaining point motion lies on a great circle
through the travel direction t: t is perpendicular to n = d1' x d2 for all
matches. A weighted least-squares (smallest singular vector) over many frame
pairs gives t; points fixed to the vehicle (rider, handlebars) do not move
and drop out because their n is ~0.
"""
from __future__ import annotations

import math

import numpy as np

import geometry as geo
import media

W, H = 1024, 512


def _pano_gray(clip, tf, tb, fi, maps):
    import jobs
    f = media.read_frame(tf, fi)
    b = media.read_frame(tb, min(tb.total_frames - 1, fi))
    return jobs.stitch_yuv420(f, b, maps)[0]


def _kabsch(a, b):
    U, S, Vt = np.linalg.svd(a.T @ b)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    return Vt.T @ np.diag([1, 1, d]) @ U.T


def estimate(clip: dict, params: dict, n_pairs: int = 16, gap: int = 3, progress=None) -> dict:
    """Returns {"t": unit vector (camera frame), "yaw": orient yaw that faces it, "pitch": deg,
    "pairs": used pairs, "confidence": 0..1}."""
    import cv2
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    P = geo.merge_params(params, {"orient": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}})
    P.pop("_stab", None)
    maps = geo.StitchMaps(P, tf.width, tf.height, W, H)
    dirs = geo.equirect_dirs(W, H).astype(np.float64)
    tel = None
    try:
        import gyro
        tel = gyro.load(clip["front"])
    except Exception:
        tel = None
    orb = cv2.ORB_create(3000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    rows, ts = [], []
    d = tf.duration
    off = gyro.DEFAULT_OFFSET_MS / 1000 if tel is not None else 0.0
    for k in range(n_pairs):
        t0 = d * (0.08 + 0.84 * k / max(1, n_pairs - 1))
        fi = int(t0 * tf.fps)
        if fi + gap >= tf.total_frames:
            continue
        try:
            y1, y2 = _pano_gray(clip, tf, tb, fi, maps), _pano_gray(clip, tf, tb, fi + gap, maps)
        except Exception:
            continue
        if progress:
            progress((k + 1) / n_pairs)
        k1, d1 = orb.detectAndCompute(y1[40:-40], None)
        k2, d2 = orb.detectAndCompute(y2[40:-40], None)
        if d1 is None or d2 is None:
            continue
        m = bf.match(d1, d2)
        if len(m) < 60:
            continue
        p1 = np.array([k1[x.queryIdx].pt for x in m])
        p2 = np.array([k2[x.trainIdx].pt for x in m])
        v1 = dirs[(p1[:, 1] + 40).astype(int), p1[:, 0].astype(int)]
        v2 = dirs[(p2[:, 1] + 40).astype(int), p2[:, 0].astype(int)]
        # camera rotation over the gap: gyro if we have it (camera frame), else from the matches
        if tel is not None:
            ta, tb_ = fi / tf.fps + off, (fi + gap) / tf.fps + off
            s = (tel["gt"] >= ta) & (tel["gt"] < tb_)
            if s.sum() < 10:
                continue
            rv = tel["gyro"][s].sum(0) * (tb_ - ta) / s.sum()
            Rc, _ = cv2.Rodrigues(rv)
            A = Rc.T                                  # d2 = A d1 for points at infinity
        else:
            best, bi = None, 0
            rng = np.random.default_rng(k)
            moving = np.linalg.norm(v1 - v2, axis=1) > 0.004       # ignore points fixed to the vehicle
            if moving.sum() < 30:
                continue
            a_, b_ = v1[moving], v2[moving]
            for _ in range(200):
                sel = rng.choice(len(a_), 3, replace=False)
                R = _kabsch(a_[sel], b_[sel])
                inl = np.linalg.norm(a_ @ R.T - b_, axis=1) < 0.004
                if inl.sum() > bi:
                    best, bi = inl, inl.sum()
            A = _kabsch(a_[best], b_[best]) if best is not None else np.eye(3)
        d1r = v1 @ A.T                                 # derotated first-frame directions
        nvec = np.cross(d1r, v2)
        mag = np.linalg.norm(nvec, axis=1)
        good = (mag > 0.002) & (mag < 0.08)            # moved, but not a mismatch
        if good.sum() < 25:
            continue
        nn = nvec[good] / mag[good, None]
        # robust: iteratively reweighted (drop the worst 30%)
        w = np.ones(len(nn))
        for _ in range(3):
            U, S, Vt = np.linalg.svd(nn * w[:, None], full_matrices=False)
            t = Vt[-1]
            r = np.abs(nn @ t)
            w = (r <= np.percentile(r, 70)).astype(float)
        # sign: scenery moves away from the direction of travel
        a1 = np.arccos(np.clip(d1r[good] @ t, -1, 1))
        a2 = np.arccos(np.clip(v2[good] @ t, -1, 1))
        if np.median(a2 - a1) < 0:
            t = -t
        rows.append(t)
    if len(rows) < 3:
        raise RuntimeError("畫面中移動的景物太少，無法判斷行進方向（可能是靜止拍攝）")
    T = np.array(rows)
    mean = T.mean(0)
    mean /= np.linalg.norm(mean)
    # agreement of the per-pair estimates (1 = all identical)
    conf = float(np.clip((T @ mean).mean(), 0, 1))
    yaw_cam = math.degrees(math.atan2(mean[0], mean[2]))
    pitch = math.degrees(math.asin(np.clip(mean[1], -1, 1)))
    return {"t": mean.tolist(), "yaw": -yaw_cam, "pitch": pitch, "pairs": len(rows), "confidence": conf}
