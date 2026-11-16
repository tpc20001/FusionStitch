"""Timeline film strip: small stitched panoramas spread over a clip (cached on disk)."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import core
import geometry as geo
import media

CACHE = core.PACKAGE_DIR / "thumb_cache"
TW, TH = 160, 80


def _key(files, n, look=""):
    # look: how the strip was rendered (lens model / colour since V2.8) -> old strips are made again
    return hashlib.sha1(("|".join(f"{p}:{os.path.getsize(p)}" for p in files) + f"|{n}" + look).encode()).hexdigest()[:20]


def strip(clip: dict, n: int = 48, progress=None) -> tuple[np.ndarray, list[float]]:
    """(n, TH, TW, 3) RGB uint8 thumbnails and their times (seconds)."""
    import jobs
    import color
    g = jobs.gopro_grade(None)                  # 原廠色彩: the strip shows the same colour as the preview
    if g:
        g = {**g, "denoise": 0.0}
    CACHE.mkdir(parents=True, exist_ok=True)
    cf = CACHE / f"{_key(clip['front'] + clip['back'], n, '|v2.13|' + ('gopro' if g else 'plain'))}.npz"
    if cf.exists():
        z = np.load(cf)
        return z["img"], z["t"].tolist()
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    sw, sh = (tf.width // 8) // 2 * 2, (tf.height // 8) // 2 * 2
    P = geo.default_params(tf.width, tf.height)
    maps = geo.StitchMaps(P, sw, sh, TW, TH)
    full = tf.color_range == "pc"
    times = [(i + 0.5) / n * tf.duration for i in range(n)]
    done = [0]

    def one(t):
        fi = min(tf.total_frames - 1, int(t * tf.fps))
        try:
            f = media.read_frame(tf, fi, scale=(sw, sh))
            b = media.read_frame(tb, min(tb.total_frames - 1, fi), scale=(sw, sh))
            rgb = jobs.yuv420_to_rgb(*jobs.stitch_yuv420(f, b, maps, full), full=full)
            if g:
                rgb = color.grade_rgb(rgb, g)
            img = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
        except Exception:
            img = np.zeros((TH, TW, 3), np.uint8)
        done[0] += 1
        if progress:
            progress(done[0] / n)
        return img

    with ThreadPoolExecutor(4) as ex:
        imgs = list(ex.map(one, times))
    arr = np.stack(imgs)
    np.savez_compressed(cf, img=arr, t=np.array(times))
    return arr, times
