"""自動調色 (auto tone): the Fusion records a flat, low-contrast picture. Sample a few stitched
frames over the clip and fit a gentle grade (contrast, blacks, whites, brightness, vibrance) so that
  - the darkest 2% sit near black and the brightest 2% near white (full range, no clipping),
  - the median brightness stays where it was (the auto brightness / exposure handle that),
  - colours reach a natural saturation,
with every slider kept small. Uses the same grade math as the export (color.grade_rgb)."""
from __future__ import annotations

import numpy as np

import color
import geometry as geo
import media

KEYS = ("contrast", "blacks", "whites", "brightness", "vibrance")
LIMITS = {"contrast": (0.0, 0.35), "blacks": (-0.6, 0.2), "whites": (-0.3, 0.5), "brightness": (-0.2, 0.2),
          "vibrance": (0.0, 0.55)}
P_LO, P_HI = 2, 98
T_LO, T_HI = 0.03, 0.93          # targets for the 2nd / 98th luma percentile
T_SAT = 0.22                     # target median saturation (max - min) of coloured pixels
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def sample_pixels(clip: dict, params: dict, n_frames: int = 9, n_px: int = 60000, progress=None) -> np.ndarray:
    """(N, 3) RGB 0..1 from stitched equirect frames (bottom 20% = bike / tripod left out)."""
    import jobs
    tf, tb = media.timeline(clip["front"]), media.timeline(clip["back"])
    full = tf.color_range == "pc"
    sw, sh = (tf.width // 2) // 2 * 2, (tf.height // 2) // 2 * 2
    P = geo.merge_params(params, {})
    P.pop("_stab", None)
    maps = geo.StitchMaps(P, sw, sh, 1024, 512)
    off = int(round(P.get("back_offset", 0) * tf.fps))
    rng = np.random.default_rng(1)
    out = []
    kind = clip.get("kind", "video")
    idx = [0] if kind == "photo" else [int(tf.total_frames * (0.06 + 0.88 * k / max(1, n_frames - 1))) for k in range(n_frames)]
    for k, fi in enumerate(idx):
        try:
            fr = media.read_frame(tf, fi, scale=(sw, sh))
            bk = media.read_frame(tb, max(0, min(tb.total_frames - 1, fi + off)), scale=(sw, sh))
        except Exception:
            continue
        rgb = np.clip(jobs.stitch_rgb(fr, bk, maps, P, full), 0, 1)[: int(512 * 0.8)]
        flat = rgb.reshape(-1, 3)
        out.append(flat[rng.choice(len(flat), min(len(flat), n_px // len(idx)), replace=False)])
        if progress:
            progress((k + 1) / len(idx))
    if not out:
        raise RuntimeError("讀不到畫面")
    return np.concatenate(out).astype(np.float32)


def stats(px: np.ndarray) -> dict:
    L = px @ LUMA
    sat = px.max(1) - px.min(1)
    coloured = sat[(L > 0.08) & (L < 0.92)]
    return {"lo": float(np.percentile(L, P_LO)), "hi": float(np.percentile(L, P_HI)), "med": float(np.median(L)),
            "sat": float(np.median(coloured)) if len(coloured) else 0.0}


WB_LIMIT = 0.35                  # max |temperature| / |tint| from the automatic white balance
WB_STRENGTH = 0.75               # correct 75% of the measured cast (warm light should stay a bit warm)


def white_balance(px: np.ndarray) -> dict:
    """Temperature / tint that make near-neutral pixels (grey walls, road, white signs, clouds) grey.
    Fusion footage often comes out pink / magenta in the whites. Uses the same channel gains as
    color.grade_rgb: R *= 1 + .18t + .06ti, G *= 1 - .12ti, B *= 1 - .18t + .06ti."""
    L = px @ LUMA
    mx, mn = px.max(1), px.min(1)
    sat = (mx - mn) / np.maximum(mx, 1e-4)
    neutral = px[(sat < 0.18) & (L > 0.18) & (L < 0.92) & (mx < 0.97)]
    if len(neutral) < 500:
        return {"temperature": 0.0, "tint": 0.0, "neutral": int(len(neutral))}
    r, g, b = (float(np.median(neutral[:, i])) for i in range(3))
    # want r*fr = g*fg = b*fb; small-signal solution in log space, then refine
    t = ti = 0.0
    for _ in range(30):
        fr, fg, fb = 1 + 0.18 * t + 0.06 * ti, 1 - 0.12 * ti, 1 - 0.18 * t + 0.06 * ti
        R, G, B = np.log(r * fr), np.log(g * fg), np.log(b * fb)
        # d/dt: R +.18, B -.18 ; d/dti: R +.06, G -.12, B +.06  (per unit, approx)
        e_rb = R - B                      # >0: too red/warm -> lower t
        e_g = (R + B) / 2 - G             # >0: magenta -> lower tint
        t -= e_rb / 0.36 * 0.5
        ti -= e_g / 0.18 * 0.5
    t, ti = t * WB_STRENGTH, ti * WB_STRENGTH
    t = float(np.clip(t, -WB_LIMIT, WB_LIMIT))
    ti = float(np.clip(ti, -WB_LIMIT, WB_LIMIT))
    return {"temperature": round(t, 3), "tint": round(ti, 3), "neutral": int(len(neutral)),
            "cast": [round(r, 3), round(g, 3), round(b, 3)]}


def fit(px: np.ndarray, base: dict | None = None) -> tuple[dict, dict, dict]:
    """Returns (slider values, stats before, stats after)."""
    from scipy.optimize import minimize
    base = color.merge(base)
    wb = {}
    if abs(base["temperature"]) < 1e-6 and abs(base["tint"]) < 1e-6 and core_cfg("auto_wb", True):
        wb = white_balance(color.grade_rgb(px, base))
        base["temperature"], base["tint"] = wb["temperature"], wb["tint"]
    s0 = stats(color.grade_rgb(px, base))

    lim = LIMITS

    def grade_of(x):
        g = dict(base)
        for k, v in zip(KEYS, x):
            lo, hi = lim[k]
            g[k] = float(np.clip(v, lo, hi))
        return g

    # targets: only stretch towards full range (a sky that is already white stays white, deep
    # shadows that are already black stay black); colour at most 1.6x of what it was
    t_lo = min(T_LO, s0["lo"])
    t_hi = min(max(T_HI, s0["hi"]), s0["hi"] + 0.10)           # a night scene must not become day
    night = s0["med"] < 0.12
    lim = {k: ((lo * 0.5, hi * 0.5) if night else (lo, hi)) for k, (lo, hi) in LIMITS.items()}
    if night:
        lim["vibrance"] = (0.0, 0.08)        # colour noise in a dark sky would turn into red/blue speckles
    t_sat = max(s0["sat"], min(T_SAT, s0["sat"] * 2.0))
    # a hazy, washed-out daytime picture (median well above mid-grey) may come down a little,
    # towards what GoPro's own render looks like; normal and dark scenes keep their brightness
    t_med = s0["med"] if s0["med"] <= 0.52 else max(0.50, s0["med"] - 0.08)

    def loss(x):
        s = stats(color.grade_rgb(px, grade_of(x)))
        e = ((s["lo"] - t_lo) / 0.02) ** 2 + ((s["hi"] - t_hi) / 0.03) ** 2 + ((s["med"] - t_med) / 0.03) ** 2
        e += ((s["sat"] - t_sat) / 0.02) ** 2
        e += 2.0 * float(np.sum(np.asarray(x) ** 2))                  # keep every change small
        # outside the limits costs extra, so the optimiser stays inside
        e += 100.0 * sum(max(0.0, lim[k][0] - v) + max(0.0, v - lim[k][1]) for k, v in zip(KEYS, x))
        return e

    n = len(KEYS)
    simplex = np.vstack([np.zeros(n), np.eye(n) * 0.1])
    r = minimize(loss, np.zeros(n), method="Nelder-Mead",
                 options={"xatol": 0.005, "fatol": 1e-3, "maxiter": 500, "initial_simplex": simplex})
    g = grade_of(r.x)
    vals = {k: round(g[k], 3) for k in KEYS}
    if wb:
        vals["temperature"], vals["tint"] = wb["temperature"], wb["tint"]
    return vals, s0, stats(color.grade_rgb(px, g))


def analyse(clip: dict, params: dict, base_grade: dict | None = None, progress=None) -> dict:
    px = sample_pixels(clip, params, progress=progress)
    # fit on a subsample for speed (grade_rgb is vectorised; 20k px x ~200 evaluations)
    sub = px[:: max(1, len(px) // 20000)]
    vals, before, after = fit(sub, base_grade)
    return {"values": vals, "before": before, "after": after}


def core_cfg(key, default=None):
    try:
        import core
        v = core.cfg(key)
        return default if v is None else v
    except Exception:
        return default


def describe(r: dict) -> str:
    b, a, v = r["before"], r["after"], r["values"]
    names = {"contrast": "對比", "blacks": "黑色", "whites": "白色", "brightness": "亮度", "vibrance": "自然飽和度",
             "temperature": "色溫", "tint": "色調"}
    ch = "、".join(f"{names[k]} {v[k]:+.2f}" for k in (*KEYS, "temperature", "tint")
                  if k in v and abs(v[k]) >= 0.01) or "不需要調整"
    return (f"{ch}（暗部 {b['lo']:.2f}→{a['lo']:.2f}，亮部 {b['hi']:.2f}→{a['hi']:.2f}，"
            f"飽和度 {b['sat']:.2f}→{a['sat']:.2f}）")
