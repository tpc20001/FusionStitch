"""Static seam parallax correction for a camera fixed to a vehicle (固定支架視差校正).

Things bolted to the camera (motorbike, handlebars, mirrors, tripod) are close,
so the two lenses see them shifted at the seam - and because they never move
relative to the camera, the shift is the same in every frame. Scenery is far
and needs no shift. So: measure the front/back disagreement in the overlap
band on many frames, keep the per-direction median (moving scenery averages
out to ~0, the bike stays), smooth it, and use it as a fixed correction.

Band parametrisation (camera frame, lens axes along ±z):
  phi  = angle around the seam circle, atan2(y, x)          (0..2pi, NB columns)
  delt = elevation from the seam plane, asin(z)              (-band..+band, NR rows)
Flow is in band pixels: (d_phi, d_delta); applied half to each lens with
opposite signs so both meet in the middle.
"""
from __future__ import annotations

import math

import numpy as np

import geometry as geo
import media

NB, NR = 1440, 64               # band size: 0.25° per column
BAND_DEG = 6.0                  # half height of the band (overlap is ~±5°)


def band_dirs(nb=NB, nr=NR, band=BAND_DEG):
    phi = (np.arange(nb) + 0.5) / nb * 2 * math.pi
    de = np.radians(((np.arange(nr) + 0.5) / nr * 2 - 1) * band)
    P, D = np.meshgrid(phi, de)
    c = np.cos(D)
    return np.stack([c * np.cos(P), c * np.sin(P), np.sin(D)], -1)       # (nr, nb, 3) camera frame


def _band_images(fr_gray, bk_gray, params, w, h, dirs):
    import cv2
    base = geo.merge_params(params, {"orient": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}})
    base.pop("_stab", None)
    d = dirs.reshape(-1, 3).astype(np.float32)
    fx, fy, _ = geo.project(d, base, "front", w, h)
    bx, by, _ = geo.project(d, base, "back", w, h)
    sh = dirs.shape[:2]
    f = cv2.remap(fr_gray, fx.reshape(sh), fy.reshape(sh), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    b = cv2.remap(bk_gray, bx.reshape(sh), by.reshape(sh), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return f, b


def measure(clip: dict, params: dict, n_frames: int = 40, progress=None) -> dict:
    """Median front->back band flow over the clip. Returns {"flow": (NR, NB, 2) float32, "stats"}."""
    import cv2
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    dirs = band_dirs()
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    flows = []
    for k in range(n_frames):
        fi = int(tf.total_frames * (0.04 + 0.92 * k / max(1, n_frames - 1)))
        try:
            fr = media.read_frame(tf, fi)[0]
            bk = media.read_frame(tb, min(tb.total_frames - 1, fi))[0]
        except Exception:
            continue
        f, b = _band_images(fr, bk, params, tf.width, tf.height, dirs)
        # pad around the seam circle (it wraps) so the flow is continuous at phi = 0
        pad = 64
        fp = np.concatenate([f[:, -pad:], f, f[:, :pad]], 1)
        bp = np.concatenate([b[:, -pad:], b, b[:, :pad]], 1)
        fl = dis.calc(fp, bp, None)[:, pad:-pad]
        # texture-less parts (sky, blur) give unreliable flow: weight by gradient
        g = cv2.Sobel(f, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(f, cv2.CV_32F, 0, 1) ** 2
        fl[g < 40] = np.nan
        flows.append(fl)
        if progress:
            progress((k + 1) / n_frames)
    if len(flows) < 8:
        raise RuntimeError("可用的畫面太少")
    F = np.stack(flows)                                    # (n, NR, NB, 2)
    med = np.nanmedian(F, axis=0)
    cnt = np.sum(~np.isnan(F[..., 0]), axis=0)
    # consistency: how much of the shift is the same in every frame (fixed objects) vs random (scenery)
    mad = np.nanmedian(np.abs(F - med[None]), axis=0)
    med = np.nan_to_num(med)
    reliable = (cnt >= len(flows) * 0.4) & (np.hypot(mad[..., 0], mad[..., 1]) < 1.5)
    med[~reliable] = 0
    # smooth (fixed objects are large) and fade towards the band edges (outside the overlap)
    med = cv2.GaussianBlur(med, (0, 0), 3)
    fade = np.cos(np.linspace(-math.pi / 2, math.pi / 2, NR))[:, None, None] ** 0.5
    med = (med * fade).astype(np.float32)
    mag = np.hypot(med[..., 0], med[..., 1])
    return {"flow": med, "frames": len(flows),
            "stats": {"max_px": float(mag.max()), "p95_px": float(np.percentile(mag, 95)),
                      "fixed_share": float(reliable.mean()),
                      "deg_per_px": 360.0 / NB}}


# ---------------------------------------------------------------- storage (Package\\seamflow\\*.npz)
def save(result: dict, folder: str, clip_id: str) -> str:
    import hashlib
    import core
    d = core.PACKAGE_DIR / "seamflow"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{folder}|{clip_id}".encode()).hexdigest()[:16]
    p = d / f"{key}.npz"
    np.savez_compressed(p, flow=result["flow"], band=np.float32(BAND_DEG))
    return str(p)


_cache: dict = {}


def load(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        import os
        k = (path, os.path.getmtime(path))
        if k not in _cache:
            z = np.load(path)
            _cache[k] = {"flow": z["flow"].astype(np.float32), "band": float(z["band"])}
        return _cache[k]
    except Exception:
        return None
