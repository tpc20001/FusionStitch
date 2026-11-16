"""Dual-fisheye -> equirectangular geometry for the GoPro Fusion.

This is the single source of truth for the projection math. The WebGL
preview shader (static/app.js, FRAG_SHADER) implements exactly the same
model, so what you see in the previewer is what the exporter renders.

Conventions
-----------
World: +z forward (front lens), +x right, +y up.
Equirect pixel (u, v): lon = (u+0.5)/W*360-180, lat = 90-(v+0.5)/H*180.
Lens model: fisheye r = radius * u * (1 + k3 * (1 - u^2)), u = theta / (fov/2).
k3 = 0 is the plain equidistant model (before V2.8.0). The Fusion lens squeezes the
edge: fitted to GoPro Fusion Studio's own renders of two clips (dev/fit_geometry.py)
fov 199.0 / k3 0.244 match it far better (gradient NCC 0.30-0.36 -> 0.80-0.82) and
let the two lenses line up better in the overlap band.
Lens image: +x right, +y up (image row index grows downward).
Calibration is stored normalised: cx/W, cy/H, radius/H, so the same
parameters work for full-res files and low-res LRV proxies.
"""
from __future__ import annotations

import copy
import math

import numpy as np

# Measured lens circles (pixels) for each Fusion recording mode, from the
# open-source fusion2sphere project. Normalised on load.
_KNOWN_LENSES = {
    (2704, 2624): ((1345, 1303, 1335), (1337, 1303, 1338)),  # 5.2K30 video
    (1568, 1504): ((779, 742, 765), (779, 742, 765)),        # 3K60 video
    (3104, 3000): ((1543, 1484, 1520), (1536, 1500, 1534)),  # 18MP photo
}


def _default_view_yaw() -> float:
    """設定 → 預設正面: the view starts facing the back lens (camera mounted the other way round)."""
    try:
        import core
        return 180.0 if (core.cfg("default_view") or "back") == "back" else 0.0
    except Exception:
        return 0.0


def default_params(width: int, height: int) -> dict:
    lens = _KNOWN_LENSES.get((width, height))
    if lens is None:
        r = 0.5 * min(width, height) * 0.99
        lens = ((width / 2, height / 2, r), (width / 2, height / 2, r))

    def L(cx, cy, r):
        return {"cx": cx / width, "cy": cy / height, "r": r / height,
                "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
                "ev": 0.0, "vignette": 0.0,
                "color": [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]}     # R,G,B gain then R,G,B offset (levels)

    return {
        "fov": LENS_FOV,     # lens field of view in degrees (r corresponds to fov/2)
        "k3": LENS_K3,       # radial term of the lens model (0 = equidistant)
        "blend": 4.0,        # seam blend width in degrees (narrow: near objects such as mirrors are
                             # not drawn twice; 接縫亮度補償 removes the brightness step a narrow seam had)
        "front": L(*lens[0]),
        "back": L(*lens[1]),
        "orient": {"yaw": _default_view_yaw(), "pitch": 0.0, "roll": 0.0},  # horizon / heading
        "back_offset": 0.0,  # seconds the back file is ahead of the front
        "seamgain": SEAMGAIN_DEFAULT,  # 接縫色彩補償: spread in degrees (0 = off), see SeamBlend
        "seamwide": SEAMWIDE_DEFAULT,  # 接縫寬混合: the smooth difference between the lenses is blended over
                                       # this many degrees (0 = off); picture detail still over `blend`
    }


SEAMGAIN_DEFAULT = 50.0
SEAMWIDE_DEFAULT = 16.0
LENS_FOV, LENS_K3 = 199.0, 0.244
OLD_FOV = 190.0          # equidistant model before V2.8.0


def merge_params(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    if override and "k3" not in override and "fov" in override:
        # lens values saved with the old equidistant model: the old default moves to the new
        # lens model, a hand-tuned fov keeps the old model so the picture does not change
        if float(override["fov"]) == OLD_FOV:
            override = {k: v for k, v in override.items() if k != "fov"}
        else:
            override = {**override, "k3": 0.0}
    def num(x):
        return [float(y) for y in x] if isinstance(x, (list, tuple)) else float(x)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update({kk: num(vv) for kk, vv in v.items()})
        else:
            out[k] = num(v)
    return out


IDENTITY_COLOR = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]


def lens_color(params: dict, which: str) -> list:
    c = params[which].get("color") or IDENTITY_COLOR
    return [float(x) for x in c]


def lens_color_active(params: dict) -> bool:
    return any(abs(a - b) > 1e-6 for w in ("front", "back") for a, b in zip(lens_color(params, w), IDENTITY_COLOR))


def rot(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """R = Ry(yaw) @ Rx(pitch) @ Rz(roll), angles in degrees."""
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    Rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


def lens_rotation(params: dict, which: str) -> np.ndarray:
    L = params[which]
    R = rot(L["yaw"], L["pitch"], L["roll"])
    if which == "back":
        R = rot(180, 0, 0) @ R
    return R


def global_rotation(params: dict) -> np.ndarray:
    """View -> camera rotation: the user's horizon/heading adjustment, preceded by the
    per-frame gyro stabilisation S (params["_stab"], set by the exporter/preview)."""
    o = params["orient"]
    G = rot(o["yaw"], o["pitch"], o["roll"])
    S = params.get("_stab")
    # project() applies the transpose of this matrix (d_cam = G^T d_view), so the
    # stabilisation S (view -> camera) enters transposed: G^T = S @ G_user^T
    return G if S is None else G @ np.asarray(S, dtype=np.float64).T


def equirect_dirs(W: int, H: int, dtype=np.float32) -> np.ndarray:
    """Unit view directions for every equirect pixel centre, shape (H, W, 3)."""
    lon = ((np.arange(W, dtype=np.float64) + 0.5) / W * 2 - 1) * math.pi
    lat = (0.5 - (np.arange(H, dtype=np.float64) + 0.5) / H) * math.pi
    clat = np.cos(lat)[:, None]
    d = np.empty((H, W, 3), dtype=dtype)
    d[..., 0] = clat * np.sin(lon)[None, :]
    d[..., 1] = np.sin(lat)[:, None]
    d[..., 2] = clat * np.cos(lon)[None, :]
    return d


def planet_dirs(W: int, H: int, view: dict, dtype=np.float32) -> np.ndarray:
    """Little planet (stereographic) view directions, shape (H, W, 3).

    The image centre looks at the nadir of the *view* frame; view = yaw/pitch/
    roll rotate that frame freely (pitch 180 turns the planet into a tunnel),
    fov = angle from the centre to the top/bottom edge, times two."""
    xs = ((np.arange(W, dtype=np.float64) + 0.5) / W * 2 - 1) * (W / H)
    ys = 1 - (np.arange(H, dtype=np.float64) + 0.5) / H * 2
    k = 2 * math.tan(math.radians(view.get("fov", 270.0)) / 4)
    px, py = np.meshgrid(xs * k, ys * k)
    rho = np.hypot(px, py)
    c = 2 * np.arctan(rho / 2)
    safe = np.where(rho > 1e-9, rho, 1.0)
    loc = np.stack([np.sin(c) * px / safe, -np.cos(c), np.sin(c) * py / safe], -1)
    V = rot(view.get("yaw", 0.0), view.get("pitch", 0.0), view.get("roll", 0.0))
    return (loc @ V.T).astype(dtype)


def flat_dirs(W: int, H: int, view: dict, dtype=np.float32) -> np.ndarray:
    """Rectilinear (normal camera) view: fov = horizontal field of view in degrees."""
    k = math.tan(math.radians(max(5.0, min(170.0, view.get("fov", 90.0)))) / 2)
    xs = ((np.arange(W, dtype=np.float64) + 0.5) / W * 2 - 1) * k
    ys = (1 - (np.arange(H, dtype=np.float64) + 0.5) / H * 2) * k * H / W
    px, py = np.meshgrid(xs, ys)
    loc = np.stack([px, py, np.ones_like(px)], -1)
    loc /= np.linalg.norm(loc, axis=-1, keepdims=True)
    V = rot(view.get("yaw", 0.0), -view.get("pitch", 0.0), view.get("roll", 0.0))
    return (loc @ V.T).astype(dtype)


def output_dirs(spec: dict | None, W: int, H: int) -> np.ndarray:
    spec = spec or {"type": "equirect"}
    if spec.get("type") == "planet":
        return planet_dirs(W, H, spec)
    if spec.get("type") == "flat":
        return flat_dirs(W, H, spec)
    return equirect_dirs(W, H)


def seam_adjust(dcam: np.ndarray, sf: dict, sign: float) -> np.ndarray:
    """Shift camera-frame directions inside the seam band by sign * flow (fixed-mount parallax).
    sf = {"flow": (NR, NB, 2) band pixels, "band": half band height in degrees}."""
    flow = np.asarray(sf["flow"], np.float32)
    nr, nb = flow.shape[:2]
    band = math.radians(float(sf.get("band", 6.0)))
    z = np.clip(dcam[..., 2], -1, 1)
    delt = np.arcsin(z)
    inb = np.abs(delt) < band
    if not inb.any():
        return dcam
    d = dcam[inb]
    de = delt[inb]
    ph = np.mod(np.arctan2(d[:, 1], d[:, 0]), 2 * math.pi)
    px = ph / (2 * math.pi) * nb - 0.5
    py = (de / band + 1) / 2 * nr - 0.5
    x0 = np.floor(px).astype(int); y0 = np.floor(py).astype(int)
    fx, fy = px - x0, py - y0
    xa, xb = x0 % nb, (x0 + 1) % nb
    ya, yb = np.clip(y0, 0, nr - 1), np.clip(y0 + 1, 0, nr - 1)
    f = (flow[ya, xa] * ((1 - fx) * (1 - fy))[:, None] + flow[ya, xb] * (fx * (1 - fy))[:, None]
         + flow[yb, xa] * ((1 - fx) * fy)[:, None] + flow[yb, xb] * (fx * fy)[:, None])
    ph2 = ph + sign * f[:, 0] * (2 * math.pi / nb)
    de2 = de + sign * f[:, 1] * (2 * band / nr)
    c = np.cos(de2)
    out = dcam.copy()
    out[inb] = np.stack([c * np.cos(ph2), c * np.sin(ph2), np.sin(de2)], -1).astype(dcam.dtype)
    return out


def project(dirs: np.ndarray, params: dict, which: str, src_w: int, src_h: int):
    """Map scene directions to source pixel coords of one lens.

    Returns (px, py, theta) where px/py follow OpenCV remap convention
    (pixel centre at integer index) and theta is the angle from the lens axis.
    """
    sf = params.get("_seamflow")
    if sf is None:
        M = (global_rotation(params) @ lens_rotation(params, which)).T  # world-view -> lens local
        loc = dirs @ M.T.astype(dirs.dtype)
    else:
        dcam = dirs @ global_rotation(params).astype(dirs.dtype)        # camera frame (= G^T d)
        dcam = seam_adjust(dcam, sf, -0.5 if which == "front" else 0.5)
        loc = dcam @ lens_rotation(params, which).astype(dirs.dtype)
    x, y, z = loc[..., 0], loc[..., 1], loc[..., 2]
    theta = np.arccos(np.clip(z, -1, 1))
    phi = np.arctan2(y, x)
    L = params[which]
    half = math.radians(params["fov"]) / 2
    u = theta / half
    r = (L["r"] * src_h) * u * (1.0 + float(params.get("k3", 0.0) or 0.0) * (1.0 - u * u))
    px = L["cx"] * src_w + r * np.cos(phi) - 0.5
    py = L["cy"] * src_h - r * np.sin(phi) - 0.5
    return px.astype(np.float32), py.astype(np.float32), theta.astype(np.float32)


def front_weight(theta_f, theta_b, px_f, py_f, px_b, py_b, params, src_w, src_h):
    """Blend weight of the front lens (1 = front only, 0 = back only)."""
    half = math.radians(params["fov"]) / 2
    bw = max(math.radians(params["blend"]), 1e-4)
    t = np.clip(0.5 + (theta_b - theta_f) / (2 * bw), 0, 1)
    w = t * t * (3 - 2 * t)  # smoothstep

    def valid(th, px, py):
        return (th <= half) & (px >= 0) & (py >= 0) & (px <= src_w - 1) & (py <= src_h - 1)

    vf = valid(theta_f, px_f, py_f)
    vb = valid(theta_b, px_b, py_b)
    w = np.where(vf & ~vb, 1.0, w)
    w = np.where(~vf & vb, 0.0, w)
    return w.astype(np.float32)


# ---------------------------------------------------------------- 接縫亮度補償 (seam gain)
# Each lens exposes on its own, and flare / street lights reach only one of them, so
# the overlap band shows a brightness difference that changes along the seam and
# over time. Every frame the exporter samples both lenses on a ring of points in
# the overlap band (seam_probe), takes d(psi) = log2(front / back) per azimuth psi
# around the seam, and multiplies the front lens by 2^(-d/2 * f) and the back lens
# by 2^(+d/2 * f), f = 1 on the seam falling smoothly to 0 at `seamgain` degrees
# from it. Both lenses meet on the seam, and the difference is spread over tens of
# degrees instead of a few pixels. psi / elevation come from the front lens frame:
# psi = atan2(y, x), e = asin(z) of the front-lens-local direction (e = 0 on the seam).
SEAM_K, SEAM_M, SEAM_BAND = 180, 7, 3.0     # probe azimuths, rows, half band (degrees)
LUMA3 = np.array([0.2126, 0.7152, 0.0722])


def seam_coords(params: dict, dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(psi / 2pi in [0, 1), elevation from the seam plane in radians) per view direction."""
    p = {k: v for k, v in params.items() if k != "_seamflow"}
    M = (global_rotation(p) @ lens_rotation(p, "front")).T
    loc = dirs @ M.T.astype(dirs.dtype)
    psi = np.arctan2(loc[..., 1], loc[..., 0]) / (2 * math.pi)
    psi = np.where(psi < 0, psi + 1.0, psi)
    e = np.arcsin(np.clip(loc[..., 2], -1, 1))
    return psi.astype(np.float32), e.astype(np.float32)


def seam_probe(params: dict, src_w: int, src_h: int, K: int = SEAM_K, M: int = SEAM_M,
               band: float = SEAM_BAND) -> dict:
    """Source pixel coords of both lenses on an M x K grid in the overlap band
    (rows: elevation -band..+band from the seam, columns: psi 0..2pi), in the camera
    frame (independent of the view rotation and stabilisation)."""
    p = {k: v for k, v in params.items() if k not in ("_seamflow", "_stab")}
    psi = (np.arange(K) + 0.5) / K * 2 * math.pi
    e = np.radians(np.linspace(-band, band, M))
    E, P = np.meshgrid(e, psi, indexing="ij")
    loc = np.stack([np.cos(E) * np.cos(P), np.cos(E) * np.sin(P), np.sin(E)], -1)   # front-local
    cam = loc @ lens_rotation(p, "front").T
    view = cam @ global_rotation(p).T                                              # project() undoes G
    fx, fy, tf = project(view, p, "front", src_w, src_h)
    bx, by, tb = project(view, p, "back", src_w, src_h)
    half = math.radians(p["fov"]) / 2
    ok = ((tf <= half) & (tb <= half) & (fx >= 1) & (fy >= 1) & (fx <= src_w - 2) & (fy <= src_h - 2)
          & (bx >= 1) & (by >= 1) & (bx <= src_w - 2) & (by <= src_h - 2))
    gf = vignette_gain(tf, p, "front"); gb = vignette_gain(tb, p, "back")
    return {"fx": fx, "fy": fy, "bx": bx, "by": by, "ok": ok, "gf": gf.astype(np.float32),
            "gb": gb.astype(np.float32), "K": K, "M": M}


class SeamGain:
    """Turns per-frame probe samples into the d(psi) table (EV), smoothed along the
    seam and over time."""

    def __init__(self, probe: dict, sigma_deg: float = 8.0, tau_frames: float = 4.0, limit_ev: float = 1.5):
        self.probe, self.limit = probe, limit_ev
        K = probe["K"]
        k = np.arange(K) - K // 2
        s = sigma_deg / (360.0 / K)
        g = np.exp(-0.5 * (k / s) ** 2)
        self.kernel_f = np.fft.rfft(np.roll(g / g.sum(), -(K // 2)))
        self.tau = tau_frames
        self.d = None

    def _smooth(self, x):
        return np.fft.irfft(np.fft.rfft(x) * self.kernel_f, n=len(x))

    def update(self, Lf: np.ndarray, Lb: np.ndarray, step: int = 1, colors=None) -> np.ndarray:
        """Lf, Lb: M x K luma (0..1, gamma encoded) of the front / back lens.
        colors: (front6, back6) lens colour match in effect (static or 色彩追蹤 of this frame):
        measured after it, so only what it leaves over is corrected (never twice)."""
        P = self.probe
        if colors is not None:
            Lf, Lb = (L * float(np.dot(LUMA3, c[:3])) + float(np.dot(LUMA3, c[3:6]))
                      for L, c in zip((Lf, Lb), colors))
        f = Lf * P["gf"]; b = Lb * P["gb"]
        good = P["ok"] & (f > 0.03) & (b > 0.03) & (f < 0.94) & (b < 0.94)
        lr = np.log2(np.maximum(f, 1e-3) / np.maximum(b, 1e-3))
        w = good.sum(0).astype(np.float64)
        d = np.where(w > 0, (lr * good).sum(0) / np.maximum(w, 1), 0.0)
        ws = self._smooth(w)
        d = np.where(ws > 1e-3, self._smooth(d * w) / np.maximum(ws, 1e-3), 0.0)
        conf = np.clip(ws / 2.0, 0, 1)                   # few valid samples -> fade towards 0
        d = np.clip(d * conf, -self.limit, self.limit)
        prev = self.d
        if prev is not None:
            a = 1.0 - math.exp(-max(1, step) / self.tau)
            d = prev + a * (d - prev)
        self.d = d
        return d.astype(np.float32)


def seam_falloff(e: np.ndarray, spread_deg: float) -> np.ndarray:
    s = max(math.radians(spread_deg), 1e-4)
    t = np.clip(1.0 - np.abs(e) / s, 0, 1)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- 接縫融合 (V2.10)
# The two lenses differ in more than brightness: white balance, veiling glare from a lamp
# that only one of them sees, the tint of the black level at night. A brightness-only gain
# (SeamGain above) left a colour step on the seam and, where it skipped the dark samples,
# a hard line through the night sky. SeamBlend replaces it, every frame, in three steps:
#
#   1. Both lenses are sampled in the overlap band on a (psi, e) grid (seam_band): psi along
#      the seam, e across it, half a degree apart.
#   2. 色彩補償: per colour channel d(psi) = log2((front + lo) / (back + lo)) from the rows
#      next to the seam, smoothed along the seam and over time. "lo" damps the dark end (an
#      offset such as glare must not become a huge gain), the total is limited, and where the
#      lenses do not see the same thing (bike, rider: parallax) the value falls back to the
#      average of the trustworthy part of the seam. The front lens is multiplied by
#      2^(-d/2 f), the back lens by 2^(+d/2 f), f falling to 0 at "seamgain" degrees.
#   3. 寬混合: what is still different after 2. and varies smoothly, D = blur(front' - back'),
#      is blended over "seamwide" degrees while the picture detail keeps the narrow "blend"
#      (so near objects are still not drawn twice):
#          out = front' wn + back' (1 - wn) + D (ww - wn)
#      wn / ww = narrow / wide front weight. Where the lenses disagree in structure, D fades out.
BAND_K, BAND_M, BAND_HALF = 360, 29, 14.0   # band pictures: azimuths x rows (1 degree steps), half height (deg)
FIT_ROWS = 3.0            # |e| (degrees) of the rows the gain is measured on
FIT_SIGMA = 8.0           # smoothing of the gain along the seam (degrees)
FIT_LO = 0.05             # damping of the dark end (display value 0..1)
FIT_LIMIT = 1.5           # largest |d| (EV)
D_SIGMA = 4.0             # blur of the difference field (degrees)
MM_ROWS = 8.0             # rows used to see whether both lenses show the same thing
LUMA3F = np.array([0.2126, 0.7152, 0.0722], np.float32)


def seam_band(params: dict, src_w: int, src_h: int, K: int = BAND_K, M: int = BAND_M,
              half: float = BAND_HALF) -> dict:
    """Source pixel coords of both lenses on the (psi, e) grid of the seam band, camera frame
    (independent of the view rotation / stabilisation): rows e = -half..+half degrees from the
    seam (+ = front lens side), columns psi = 0..2pi."""
    p = {k: v for k, v in params.items() if k not in ("_seamflow", "_stab")}
    psi = (np.arange(K) + 0.5) / K * 2 * math.pi
    e = np.radians(np.linspace(-half, half, M))
    E, P = np.meshgrid(e, psi, indexing="ij")
    loc = np.stack([np.cos(E) * np.cos(P), np.cos(E) * np.sin(P), np.sin(E)], -1)   # front-local
    cam = loc @ lens_rotation(p, "front").T
    view = cam @ global_rotation(p).T                                              # project() undoes G
    fx, fy, tf = project(view, p, "front", src_w, src_h)
    bx, by, tb = project(view, p, "back", src_w, src_h)
    hf = math.radians(p["fov"]) / 2

    def ok(t, x, y):
        return (t <= hf) & (x >= 1) & (y >= 1) & (x <= src_w - 2) & (y <= src_h - 2)
    return {"fx": fx, "fy": fy, "bx": bx, "by": by, "okf": ok(tf, fx, fy), "okb": ok(tb, bx, by),
            "gf": vignette_gain(tf, p, "front").astype(np.float32),
            "gb": vignette_gain(tb, p, "back").astype(np.float32),
            "K": K, "M": M, "half": half, "e": e.astype(np.float32)}


def seam_wide(params: dict) -> float:
    """寬混合 width in degrees actually used: never wider than the overlap of the two lenses."""
    w = float(params.get("seamwide", SEAMWIDE_DEFAULT) or 0.0)
    return max(0.0, min(w, float(params["fov"]) - 181.0)) if w > 0 else 0.0


def wide_weight(e, wide_deg: float):
    """Front weight of the wide blend at elevation e (radians) from the seam."""
    t = np.clip(0.5 + e / max(math.radians(wide_deg), 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


class SeamBlend:
    """Per-frame seam treatment (see above), shared by the GPU exporter, the preview and the
    CPU path. rgb=False: one gain for all channels (the plane-wise fast paths)."""

    def __init__(self, params: dict, src_w: int, src_h: int, rgb: bool = True, tau_frames: float = 4.0):
        self.rgb = rgb
        self.spread = float(params.get("seamgain", 0.0) or 0.0)
        self.wide = seam_wide(params)
        self.on = self.spread > 0 or self.wide > 0
        self.tau = tau_frames
        self.gain = self.confd = None
        if not self.on:
            return
        b = self.band = seam_band(params, src_w, src_h)
        self.K, self.M, self.half = b["K"], b["M"], b["half"]
        self.e_deg = np.degrees(b["e"])
        self.both = b["okf"] & b["okb"]
        self.fit_rows = np.abs(self.e_deg) <= FIT_ROWS + 1e-6
        self.mm_rows = np.abs(self.e_deg) <= MM_ROWS + 1e-6
        self.fall = (seam_falloff(b["e"], self.spread).astype(np.float32)[:, None, None]
                     if self.spread > 0 else None)
        import cv2
        f32 = lambda a: np.ascontiguousarray(a, np.float32)
        fix = lambda x, y: cv2.convertMaps(f32(x), f32(y), cv2.CV_16SC2)      # fixed point: faster remap
        self.m = {"f": fix(b["fx"], b["fy"]), "b": fix(b["bx"], b["by"])}
        self.mc = {"f": fix((b["fx"] + 0.5) / 2 - 0.5, (b["fy"] + 0.5) / 2 - 0.5),
                   "b": fix((b["bx"] + 0.5) / 2 - 0.5, (b["by"] + 0.5) / 2 - 0.5)}
        self.g = {"f": b["gf"][..., None], "b": b["gb"][..., None]}
        self._kf = {}
        self.tex = (self.M - 1) / (2 * self.half)          # band texels per degree
        # the blurred validity mask of the difference field never changes
        sig = D_SIGMA * self.tex
        self._dpad = int(sig * 3) + 1
        v = self.both.astype(np.float32)
        self._dv = v[..., None]
        self._dden = np.maximum(cv2.GaussianBlur(self._wrap(v, self._dpad), (0, 0), sig, borderType=cv2.BORDER_CONSTANT)
                                [:, self._dpad:-self._dpad], 0.05)[..., None]
        self._mmv = self.both[self.mm_rows].astype(np.float32)
        self._mmn = None

    @staticmethod
    def _wrap(x, pad):
        return np.concatenate([x[:, -pad:], x, x[:, :pad]], 1)

    # ---- helpers
    def _S(self, x, sigma_deg):
        """Circular Gaussian smoothing along the seam of a (K, ...) array."""
        K = self.K
        kf = self._kf.get(sigma_deg)
        if kf is None:
            k = np.arange(K) - K // 2
            g = np.exp(-0.5 * (k / (sigma_deg * K / 360.0)) ** 2)
            kf = self._kf[sigma_deg] = np.fft.rfft(np.roll(g / g.sum(), -(K // 2)))
        X = np.fft.rfft(x, axis=0)
        return np.fft.irfft(X * (kf if x.ndim == 1 else kf[:, None]), n=K, axis=0)

    def rows(self) -> tuple:
        """(scale, offset): texture row coordinate v = e (radians) * scale + offset of the D table."""
        M, half = self.M, math.radians(self.half)
        return (M - 1) / (2 * half * M), (0.5 * (M - 1) + 0.5) / M

    # ---- sampling
    def sample(self, front, back, full: bool = True, colors=None, tone=None):
        """front / back: (y, uv) planes of one lens, uv = interleaved (h/2, w/2, 2) plane (NV12)
        or a (u, v) pair. -> F, B: (M, K, 3) float32 display RGB of the band, with the lens colour
        match and the vignette gain, as the stitcher draws them."""
        import cv2
        out = []
        cols = colors or (None, None)
        self._tone = tone                      # (2, N) gain by brightness per lens (原廠鏡頭曝光) or None
        for key, planes, col in (("f", front, cols[0]), ("b", back, cols[1])):
            x, y = self.m[key]
            cx, cy = self.mc[key]
            Y = cv2.remap(planes[0], x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
            uv = planes[1]
            if isinstance(uv, (tuple, list)):
                U = cv2.remap(uv[0], cx, cy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
                V = cv2.remap(uv[1], cx, cy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
            else:
                UV = cv2.remap(uv, cx, cy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
                U, V = UV[..., 0], UV[..., 1]
            Y *= 1 / 255.0
            U = U * (1 / 255.0) - 0.5
            V = V * (1 / 255.0) - 0.5
            if not full:
                Y = (Y - 16 / 255) * (255 / 219)
                U *= 255 / 224
                V *= 255 / 224
            rgb = np.stack([Y + 1.5748 * V, Y - 0.1873 * U - 0.4681 * V, Y + 1.8556 * U], -1)
            out.append(self._finish(rgb, key, col))
        return out[0], out[1]

    def sample_rgb(self, front_rgb, back_rgb, colors=None):
        """Same for RGB pictures (photos): front_rgb / back_rgb float32 0..1 or uint8, (h, w, 3) RGB."""
        import cv2
        out = []
        cols = colors or (None, None)
        self._tone = None
        for key, img, col in (("f", front_rgb, cols[0]), ("b", back_rgb, cols[1])):
            x, y = self.m[key]
            rgb = cv2.remap(img, x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
            if img.dtype == np.uint8:
                rgb *= 1 / 255.0
            out.append(self._finish(rgb, key, col))
        return out[0], out[1]

    def _finish(self, rgb, key, col):
        if col is not None:
            rgb = rgb * np.asarray(col[:3], np.float32) + np.asarray(col[3:6], np.float32)
        tone = getattr(self, "_tone", None)
        if tone is not None:
            tab = tone[0 if key == "f" else 1]
            rgb = rgb * np.interp(np.clip(rgb, 0, 1), np.linspace(0, 1, len(tab)), tab).astype(np.float32)
        return (rgb * self.g[key]).astype(np.float32)

    # ---- per frame
    def _same_scene(self, F, B):
        """(for the gain, for the wide blend) 0..1 per azimuth: 1 where both lenses show the same
        thing in the band, 0 where they do not (near objects: parallax). A smooth difference
        (exposure, white balance, veiling glare) is "the same thing": only the structure
        (about 3 .. 8 degrees) is compared, in log brightness."""
        import cv2
        r = self.mm_rows
        v = self._mmv
        pad = int(round(12 * self.tex))
        k = int(round(2.5 * self.tex)) | 1

        def bp(x):                                       # band pass, wraps along the seam
            x = self._wrap(x, pad)
            x = cv2.blur(x, (k, k), borderType=cv2.BORDER_REPLICATE)        # small misregistration does not count
            return (x - cv2.GaussianBlur(x, (0, 0), 4.0 * self.tex, borderType=cv2.BORDER_REPLICATE))[:, pad:-pad]
        bf = bp(np.log2(np.maximum(F[r] @ LUMA3F, 0) + FIT_LO))
        bb = bp(np.log2(np.maximum(B[r] @ LUMA3F, 0) + FIT_LO))
        S = lambda x: self._S((x * v).sum(0).astype(np.float64), 6.0)
        if self._mmn is None:
            self._mmn = np.maximum(S(np.ones_like(bf)), 1e-6)
        n = self._mmn
        d = bf - bb
        ed = S(d * d) / n
        es = S(bf * bf + bb * bb) / n
        self.mismatch = mm = ed / (es + 0.08 ** 2)
        t = np.clip((mm - 0.45) / 0.45, 0, 1)             # the gain is spread far from the seam: be strict
        u = np.clip((mm - 0.65) / 0.35, 0, 1)             # the wide blend stays inside the overlap
        return 1 - t * t * (3 - 2 * t), 1 - u * u * (3 - 2 * u)

    def update(self, F, B, step: int = 1):
        """F, B from sample(). -> (gain (K, 4) float32: EV per channel R, G, B and for luma, to
        apply to the BACK lens (front: the negative), times falloff / 2;
        D (M, K, 3) float32: the smooth front - back difference left after the gain)."""
        import cv2
        K, M = self.K, self.M
        a = 1.0 - math.exp(-max(1, step) / self.tau)
        same, same_d = self._same_scene(F, B)
        if self.spread > 0:
            r = self.fit_rows
            f, b = F[r], B[r]
            ok = self.both[r] & (f.max(-1) < 0.94) & (b.max(-1) < 0.94)
            w = ok[..., None].astype(np.float32)
            n = self._S(ok.sum(0).astype(np.float64), FIT_SIGMA)
            nn = np.maximum(n, 1e-6)[:, None]
            mf = np.maximum(self._S((f * w).sum(0).astype(np.float64), FIT_SIGMA) / nn, 0)
            mb = np.maximum(self._S((b * w).sum(0).astype(np.float64), FIT_SIGMA) / nn, 0)
            d = np.concatenate([np.log2((mf + FIT_LO) / (mb + FIT_LO)),
                                np.log2((mf @ LUMA3 + FIT_LO) / (mb @ LUMA3 + FIT_LO))[:, None]], 1)
            conf = np.clip(n / (0.25 * r.sum()), 0, 1) * same
            wsum = float((conf * n).sum())
            ref = (d * (conf * n)[:, None]).sum(0) / wsum if wsum > 1e-3 else np.zeros(4)
            d = conf[:, None] * d + (1 - conf[:, None]) * ref
            d = np.clip(d, -FIT_LIMIT, FIT_LIMIT)
            if not self.rgb:
                d[:, :3] = d[:, 3:4]
            self.gain = d if self.gain is None else self.gain + a * (d - self.gain)
        else:
            self.gain = np.zeros((K, 4))
        gain = self.gain.astype(np.float32)
        if self.wide > 0:
            self.confd = same_d if self.confd is None else self.confd + a * (same_d - self.confd)
            if self.spread > 0:
                ev = 0.5 * gain[None, :, :3] * self.fall
                Fc, Bc = F * np.exp2(-ev), B * np.exp2(ev)
            else:
                Fc, Bc = F, B
            pad = self._dpad
            num = cv2.GaussianBlur(self._wrap((Fc - Bc) * self._dv, pad), (0, 0), D_SIGMA * self.tex,
                                   borderType=cv2.BORDER_CONSTANT)[:, pad:-pad]
            D = num / self._dden
            D *= self.confd.astype(np.float32)[None, :, None]
            D = np.clip(D, -0.6, 0.6).astype(np.float32)
        else:
            D = np.zeros((M, K, 3), np.float32)
        return gain, D


def vignette_gain(theta, params, which):
    """Exposure gain for one lens at angle theta (EV + radial falloff fix)."""
    L = params[which]
    half = math.radians(params["fov"]) / 2
    return (2.0 ** L["ev"]) * (1.0 + L["vignette"] * (theta / half) ** 2)


def patch_map(dirs: np.ndarray, patch: dict | None) -> np.ndarray | None:
    """Nadir patch (logo / colour disc over the mount or tripod), per output pixel:
    (u, v, alpha). The disc is an azimuthal projection centred on straight down."""
    if not patch or patch.get("mode", "off") == "off":
        return None
    half = math.radians(float(patch.get("size", 30.0))) / 2
    a = np.arccos(np.clip(-dirs[..., 1], -1, 1))
    r = a / half
    phi = np.arctan2(dirs[..., 2], dirs[..., 0]) + math.radians(float(patch.get("rotate", 0.0)))
    u = 0.5 + 0.5 * r * np.cos(phi)
    v = 0.5 - 0.5 * r * np.sin(phi)
    edge = max(1e-4, math.radians(float(patch.get("feather", 1.0))))
    alpha = np.clip((half - a) / edge, 0, 1)
    return np.stack([u, v, alpha], -1).astype(np.float32)


def float_maps(params: dict, src_w: int, src_h: int, out_w: int, out_h: int, scale: int = 1,
               spec: dict | None = None) -> dict:
    """Float remap tables for one plane. scale=2 gives a 4:2:0 chroma plane:
    output grid out_w x out_h is already the plane size, coordinates are
    converted to the subsampled source plane. spec selects the output
    projection (equirect / planet)."""
    d = output_dirs(spec, out_w, out_h)
    fx, fy, tf = project(d, params, "front", src_w, src_h)
    bx, by, tb = project(d, params, "back", src_w, src_h)
    w = front_weight(tf, tb, fx, fy, bx, by, params, src_w, src_h)
    gf = vignette_gain(tf, params, "front").astype(np.float32)
    gb = vignette_gain(tb, params, "back").astype(np.float32)
    if scale != 1:
        fx, fy, bx, by = ((a + 0.5) / scale - 0.5 for a in (fx, fy, bx, by))
    psi, e = seam_coords(params, d)
    return {"fx": fx, "fy": fy, "bx": bx, "by": by, "w": w, "dirs": d,
            "af": (w * gf).astype(np.float32), "ab": ((1 - w) * gb).astype(np.float32),
            "unity": bool(np.allclose(gf, 1) and np.allclose(gb, 1)),
            "seam_psi": psi, "seam_e": e,
            "seam_f": seam_falloff(e, float(params.get("seamgain", 0.0) or 0.0)).astype(np.float32)}


class StitchMaps:
    """CPU (OpenCV) remap tables + blend weights for one output size."""

    def __init__(self, params: dict, src_w: int, src_h: int, out_w: int, out_h: int,
                 chroma: bool = True, spec: dict | None = None):
        import cv2
        self.out_w, self.out_h = out_w, out_h
        self.params = params
        self.src = (src_w, src_h)

        spread = float(params.get("seamgain", 0.0) or 0.0)
        wide = seam_wide(params)
        self.seam_on = spread > 0 or wide > 0

        def build(W, H, scale):
            m = float_maps(params, src_w, src_h, W, H, scale, spec)
            pl = {"mf": cv2.convertMaps(m["fx"], m["fy"], cv2.CV_16SC2),
                  "mb": cv2.convertMaps(m["bx"], m["by"], cv2.CV_16SC2),
                  "wf": m["w"], "wb": (1 - m["w"]).astype(np.float32),
                  "af": m["af"], "ab": m["ab"], "unity": m["unity"],
                  "psi": m["seam_psi"] if spread > 0 else None, "sf": m["seam_f"] if spread > 0 else None}
            if self.seam_on:
                # where this pixel reads the per-frame tables of SeamBlend (K wide, padded by one column)
                e = m["seam_e"]
                pl["tx"] = (m["seam_psi"] * BAND_K + 0.5).astype(np.float32)
                pl["ty"] = ((np.degrees(e) + BAND_HALF) / (2 * BAND_HALF) * (BAND_M - 1)).astype(np.float32)
                pl["wd"] = (np.where(np.abs(e) < math.radians(wide) / 2, wide_weight(e, wide) - m["w"], 0)
                            .astype(np.float32) if wide > 0 else None)
            return pl

        self.luma = build(out_w, out_h, 1)
        self.chroma = build(out_w // 2, out_h // 2, 2) if chroma else None
        self._sb = {}              # SeamBlend per mode (per-channel / one gain), each with its own state
        self.step = 10 ** 6        # frames are unrelated (stills, analysis); the video exporter sets its step

    def seam_for(self, front, back, full=True, step=None, colors=None, rgb=True, pictures=False, tone=None):
        """This frame's seam measurement (接縫融合), None when it is off.
        front / back: (y, uv) source planes of each lens, or RGB pictures with pictures=True."""
        if not self.seam_on:
            return None
        sb = self._sb.get(rgb)
        if sb is None:
            sb = self._sb[rgb] = SeamBlend(self.params, *self.src, rgb=rgb)
        F, B = sb.sample_rgb(front, back, colors) if pictures else sb.sample(front, back, full, colors, tone)
        gain, D = sb.update(F, B, self.step if step is None else step)
        return {"gain": gain, "D": D, "sb": sb}

    def seam_plane(self, st, pl):
        """Per-pixel terms for one plane table: gF, gB (H, W, 3) gains of the lenses (or None) and
        dterm (H, W, 3), what the wide blend adds (or None). Same numbers as the GPU shaders."""
        import cv2
        gain, D, sb = st["gain"], st["D"], st["sb"]
        out = {"gF": None, "gB": None, "dterm": None}
        if sb.spread > 0:
            tab = np.ascontiguousarray(np.concatenate([gain[-1:, :3], gain[:, :3], gain[:1, :3]])[None])
            g = cv2.remap(tab, pl["tx"], np.zeros_like(pl["tx"]), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            ev = 0.5 * g * pl["sf"][..., None]
            out["gB"] = np.exp2(ev)
            out["gF"] = np.exp2(-ev)
        if sb.wide > 0 and pl.get("wd") is not None:
            Dp = np.ascontiguousarray(np.concatenate([D[:, -1:], D, D[:, :1]], 1))
            d = cv2.remap(Dp, pl["tx"], pl["ty"], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            out["dterm"] = d * pl["wd"][..., None]
        return out

    def planes_for(self, front, back, full=True, step=None):
        """(luma, chroma) plane tables for one frame of the plane-wise (YUV) CPU path: with 接縫融合
        the weight*gain maps carry this frame's gain (one for all channels) and "add" / "add_u" /
        "add_v" what the wide blend adds (0..1 units). front / back: (y, u, v) source planes."""
        st = self.seam_for((front[0], (front[1], front[2])), (back[0], (back[1], back[2])), full, step, None, rgb=False)
        if st is None:
            return self.luma, self.chroma

        def adj(pl, chroma):
            if pl is None:
                return None
            t = self.seam_plane(st, pl)
            out = dict(pl)
            out["unity"] = False
            if t["gB"] is not None:
                g = t["gB"][..., 0]
                out["af"], out["ab"] = pl["af"] / g, pl["ab"] * g
            if t["dterm"] is not None:
                d = t["dterm"]
                y = d @ LUMA3F
                if chroma:
                    out["add_u"], out["add_v"] = (d[..., 2] - y) / 1.8556, (d[..., 0] - y) / 1.5748
                else:
                    out["add"] = y
            return out
        return adj(self.luma, False), adj(self.chroma, True)
