"""Colour grading: one model, two implementations (GLSL for preview/export, NumPy for CPU/photos).

All adjustments work on display-referred BT.709 R'G'B' in 0..1 after the
lenses are stitched. Order: white balance -> exposure -> shadows/highlights ->
whites/blacks -> brightness -> contrast -> HSL bands -> vibrance/saturation
-> LUT -> clamp. GLSL `grade()` and NumPy `grade_rgb()` must stay identical
(dev/tests/check_color.py compares them).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- parameters
BANDS = [("red", "紅", 0.0), ("orange", "橙", 30.0), ("yellow", "黃", 60.0), ("green", "綠", 120.0),
         ("aqua", "青", 180.0), ("blue", "藍", 240.0), ("purple", "紫", 275.0), ("magenta", "洋紅", 315.0)]

SLIDERS = [  # key, label, min, max, step, default, tip
    ("exposure", "曝光", -2.0, 2.0, 0.01, 0.0, "整體亮度（以光圈級數計）"),
    ("brightness", "亮度", -1.0, 1.0, 0.01, 0.0, "提亮或壓暗中間調，亮部暗部不爆"),
    ("contrast", "對比", -1.0, 1.0, 0.01, 0.0, ""),
    ("highlights", "亮部", -1.0, 1.0, 0.01, 0.0, "負值把天空等亮處拉回來"),
    ("shadows", "陰影", -1.0, 1.0, 0.01, 0.0, "正值把暗處拉亮"),
    ("whites", "白色", -1.0, 1.0, 0.01, 0.0, "最亮的地方"),
    ("blacks", "黑色", -1.0, 1.0, 0.01, 0.0, "最暗的地方（正值提亮暗部）"),
    ("temperature", "色溫", -1.0, 1.0, 0.01, 0.0, "負＝冷（藍），正＝暖（黃）"),
    ("tint", "色調", -1.0, 1.0, 0.01, 0.0, "負＝綠，正＝洋紅"),
    ("saturation", "飽和度", -1.0, 1.0, 0.01, 0.0, ""),
    ("vibrance", "自然飽和度", -1.0, 1.0, 0.01, 0.0, "只加強不夠鮮豔的顏色，膚色比較自然"),
    ("lut_strength", "LUT 強度", 0.0, 1.0, 0.01, 1.0, ""),
]


def default_grade() -> dict:
    g = {k: d for k, _, _, _, _, d, _ in SLIDERS}
    g["hsl"] = {b: {"h": 0.0, "s": 0.0, "l": 0.0} for b, _, _ in BANDS}
    g["lut"] = ""
    g["sharpen"] = 0.0          # picture enhancement (enhance.py), not part of the colour math
    g["denoise"] = 0.0
    return g


def merge(g: dict | None) -> dict:
    out = default_grade()
    for k, v in (g or {}).items():
        if k == "hsl":
            for b, hv in (v or {}).items():
                if b in out["hsl"]:
                    out["hsl"][b].update({kk: float(vv) for kk, vv in hv.items()})
        elif k == "lut":
            out["lut"] = str(v or "")
        elif k in ("sharpen", "denoise"):
            out[k] = float(v or 0)
        elif k in out:
            out[k] = float(v)
    return out


def is_identity(g: dict | None) -> bool:
    g = merge(g)
    if g["lut"] and g["lut_strength"] > 0:
        return False
    if any(abs(g[k]) > 1e-6 for k, *_ in SLIDERS if k != "lut_strength"):
        return False
    return all(abs(x) < 1e-6 for b in g["hsl"].values() for x in b.values())


BUILTIN_PRESETS = {
    "原始": {},
    "鮮豔": {"vibrance": 0.35, "saturation": 0.1, "contrast": 0.12, "shadows": 0.15},
    "電影感": {"contrast": 0.18, "highlights": -0.35, "shadows": 0.2, "saturation": -0.15, "temperature": 0.08,
               "hsl": {"aqua": {"h": 8, "s": 0.1}, "blue": {"h": -6}, "orange": {"s": 0.1}}},
    "暖陽": {"temperature": 0.35, "tint": 0.05, "vibrance": 0.2, "highlights": -0.15},
    "冷調": {"temperature": -0.3, "contrast": 0.1, "saturation": -0.05},
    "高動態": {"highlights": -0.6, "shadows": 0.55, "whites": 0.1, "blacks": -0.1, "vibrance": 0.25},
    "黑白": {"saturation": -1.0, "contrast": 0.2},
    "藍天綠地": {"vibrance": 0.15, "hsl": {"blue": {"s": 0.35, "l": -0.08}, "aqua": {"s": 0.25}, "green": {"s": 0.25, "h": -5}}},
}


# ---------------------------------------------------------------- uniform packing
def uniforms(g: dict) -> dict:
    """Values for the GLSL grade() uniforms."""
    g = merge(g)
    hsl = np.array([[g["hsl"][b]["h"], g["hsl"][b]["s"], g["hsl"][b]["l"]] for b, _, _ in BANDS], "f4")
    return {
        "gA": (g["exposure"], g["brightness"], g["contrast"], g["highlights"]),
        "gB": (g["shadows"], g["whites"], g["blacks"], g["temperature"]),
        "gC": (g["tint"], g["saturation"], g["vibrance"], g["lut_strength"] if g["lut"] else 0.0),
        "gHsl": tuple(hsl.flatten()),
        "gOn": 0 if is_identity(g) else 1,
    }


# ---------------------------------------------------------------- GLSL
GLSL = """
uniform vec4 gA;            // exposure, brightness, contrast, highlights
uniform vec4 gB;            // shadows, whites, blacks, temperature
uniform vec4 gC;            // tint, saturation, vibrance, lut strength (0 = no LUT)
uniform float gHsl[24];     // 8 bands x (hue deg, sat, lum)
uniform int gOn;
uniform sampler3D gLut;
const float BAND_H[8] = float[8](0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 275.0, 315.0);
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

vec3 rgb2hsv(vec3 c) {
    float mx = max(c.r, max(c.g, c.b)), mn = min(c.r, min(c.g, c.b)), d = mx - mn;
    float h = 0.0;
    if (d > 1e-6) {
        if (mx == c.r) h = mod((c.g - c.b) / d, 6.0);
        else if (mx == c.g) h = (c.b - c.r) / d + 2.0;
        else h = (c.r - c.g) / d + 4.0;
    }
    return vec3(h * 60.0, mx > 1e-6 ? d / mx : 0.0, mx);
}
vec3 hsv2rgb(vec3 h) {
    float hh = mod(h.x, 360.0) / 60.0, c = h.z * h.y, x = c * (1.0 - abs(mod(hh, 2.0) - 1.0)), m = h.z - c;
    vec3 r;
    if (hh < 1.0) r = vec3(c, x, 0.0); else if (hh < 2.0) r = vec3(x, c, 0.0); else if (hh < 3.0) r = vec3(0.0, c, x);
    else if (hh < 4.0) r = vec3(0.0, x, c); else if (hh < 5.0) r = vec3(x, 0.0, c); else r = vec3(c, 0.0, x);
    return r + m;
}
vec3 bandAdjust(float h) {           // piecewise-linear between neighbouring band centres
    vec3 acc = vec3(0.0);
    for (int i = 0; i < 8; i++) {
        float a = BAND_H[i], b = i < 7 ? BAND_H[i + 1] : 360.0;
        int j = i < 7 ? i + 1 : 0;
        if (h >= a && h < b) {
            float t = (h - a) / (b - a);
            acc = mix(vec3(gHsl[i * 3], gHsl[i * 3 + 1], gHsl[i * 3 + 2]),
                      vec3(gHsl[j * 3], gHsl[j * 3 + 1], gHsl[j * 3 + 2]), t);
        }
    }
    return acc;
}
float toneCurve(float L) {
    float sh = gB.x, hl = gA.w, wh = gB.y, bl = gB.z;
    L += 0.35 * sh * L * (1.0 - L) * (1.0 - L) * 2.0;
    L += 0.35 * hl * L * L * (1.0 - L) * 2.0;
    // x^4 by multiplication: GLSL pow() of a negative base is NaN on NVIDIA (L can pass 1.0 after
    // "whites"), which showed as black blobs in the sun; NumPy's ** gives the same result as this
    float a = L * L, b = (1.0 - L) * (1.0 - L);
    L += 0.25 * wh * a * a;
    b = 1.0 - L; b = b * b;
    L += 0.25 * bl * b * b;
    return L;
}
vec3 grade(vec3 c) {
    if (gOn == 0) return c;
    float t = gB.w, ti = gC.x;
    c *= vec3(1.0 + 0.18 * t + 0.06 * ti, 1.0 - 0.12 * ti, 1.0 - 0.18 * t + 0.06 * ti);
    c *= exp2(gA.x / 2.2);
    float L = max(dot(c, LUMA), 0.0);
    float L2 = toneCurve(min(L, 1.0)) + max(L - 1.0, 0.0);
    c *= L > 1e-5 ? L2 / L : 1.0;
    c = clamp(c, 0.0, 65504.0);
    c = pow(max(c, vec3(1e-8)), vec3(exp2(-gA.y)));
    c = (c - 0.5) * (1.0 + gA.z) + 0.5;
    c = clamp(c, 0.0, 1.0);
    vec3 hsv = rgb2hsv(c);
    vec3 adj = bandAdjust(hsv.x);
    float lw = hsv.y;                                 // luminance shift only on coloured pixels
    hsv.x += adj.x;
    hsv.y = clamp(hsv.y * (1.0 + adj.y), 0.0, 1.0);
    hsv.z = clamp(hsv.z * (1.0 + adj.z * lw), 0.0, 1.0);
    c = hsv2rgb(hsv);
    float g = dot(c, LUMA);
    float sat = max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b));
    float boost = (1.0 + gC.y) * (1.0 + gC.z * (1.0 - sat));
    // no extra colour in near-black: there the "colour" is sensor noise and turns into blue / red speckles
    if (boost > 1.0) boost = 1.0 + (boost - 1.0) * smoothstep(0.03, 0.22, g);
    c = mix(vec3(g), c, boost);
    c = clamp(c, 0.0, 1.0);
    if (gC.w > 0.0) {
        float n = float(textureSize(gLut, 0).x);
        vec3 lc = texture(gLut, c * (n - 1.0) / n + 0.5 / n).rgb;
        c = mix(c, lc, gC.w);
    }
    c = clamp(c, 0.0, 1.0);
    return mix(c, vec3(1.0), vec3(isnan(c)));      // last line of defence: never output NaN (= black)
}
"""


# ---------------------------------------------------------------- NumPy (same math)
def _rgb2hsv(c):
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mx, mn = c.max(-1), c.min(-1)
    d = mx - mn
    safe = np.where(d > 1e-6, d, 1.0)
    h = np.where(mx == r, np.mod((g - b) / safe, 6.0), np.where(mx == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0))
    h = np.where(d > 1e-6, h, 0.0)
    s = np.where(mx > 1e-6, d / np.where(mx > 1e-6, mx, 1.0), 0.0)
    return np.stack([h * 60.0, s, mx], -1)


def _hsv2rgb(hsv):
    hh = np.mod(hsv[..., 0], 360.0) / 60.0
    c = hsv[..., 2] * hsv[..., 1]
    x = c * (1 - np.abs(np.mod(hh, 2.0) - 1))
    m = hsv[..., 2] - c
    z = np.zeros_like(c)
    i = np.floor(hh).astype(int)
    r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [c, x, z, z, x], c)
    g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [x, c, c, x, z], z)
    b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [z, z, x, c, c], x)
    return np.stack([r + m, g + m, b + m], -1)


def _tone(L, g):
    sh, hl, wh, bl = g["shadows"], g["highlights"], g["whites"], g["blacks"]
    L = L + 0.35 * sh * L * (1 - L) * (1 - L) * 2
    L = L + 0.35 * hl * L * L * (1 - L) * 2
    L = L + 0.25 * wh * L ** 4
    L = L + 0.25 * bl * (1 - L) ** 4
    return L


def grade_rgb(c: np.ndarray, g: dict) -> np.ndarray:
    """c: float32 (..., 3) RGB 0..1."""
    g = merge(g)
    if is_identity(g):
        return c
    c = c.astype(np.float32).copy()
    t, ti = g["temperature"], g["tint"]
    c *= np.array([1 + 0.18 * t + 0.06 * ti, 1 - 0.12 * ti, 1 - 0.18 * t + 0.06 * ti], np.float32)
    c *= 2 ** (g["exposure"] / 2.2)
    luma = np.array([0.2126, 0.7152, 0.0722], np.float32)
    L = np.maximum(c @ luma, 0)
    L2 = _tone(np.minimum(L, 1), g) + np.maximum(L - 1, 0)
    c *= np.where(L > 1e-5, L2 / np.where(L > 1e-5, L, 1), 1.0)[..., None]
    c = np.maximum(c, 0) ** (2 ** -g["brightness"])
    c = np.clip((c - 0.5) * (1 + g["contrast"]) + 0.5, 0, 1)
    hsv = _rgb2hsv(c)
    centers = [h for _, _, h in BANDS] + [360.0]
    tab = np.array([[g["hsl"][b]["h"], g["hsl"][b]["s"], g["hsl"][b]["l"]] for b, _, _ in BANDS] +
                   [[g["hsl"][BANDS[0][0]][k] for k in ("h", "s", "l")]], np.float32)
    adj = [np.interp(hsv[..., 0], centers, tab[:, k]) for k in range(3)]
    lw = hsv[..., 1].copy()
    hsv[..., 0] += adj[0]
    hsv[..., 1] = np.clip(hsv[..., 1] * (1 + adj[1]), 0, 1)
    hsv[..., 2] = np.clip(hsv[..., 2] * (1 + adj[2] * lw), 0, 1)
    c = _hsv2rgb(hsv)
    gray = (c @ luma)[..., None]
    sat = (c.max(-1) - c.min(-1))[..., None]
    boost = (1 + g["saturation"]) * (1 + g["vibrance"] * (1 - sat))
    t_ = np.clip((gray - 0.03) / (0.22 - 0.03), 0.0, 1.0)
    k_ = t_ * t_ * (3 - 2 * t_)                       # smoothstep, same as the GLSL
    boost = np.where(boost > 1, 1 + (boost - 1) * k_, boost)
    c = gray + (c - gray) * boost
    c = np.clip(c, 0, 1)
    if g["lut"] and g["lut_strength"] > 0:
        lut = load_cube(g["lut"])
        c = c + (apply_lut(c, lut) - c) * g["lut_strength"]
    return np.clip(c, 0, 1).astype(np.float32)



# ---------------------------------------------------------------- .cube LUTs
_lut_cache: dict = {}


def load_cube(path: str) -> np.ndarray:
    """Adobe/Resolve .cube 3D LUT -> float32 array [b][g][r][3] (r changes fastest in the file)."""
    key = (path, Path(path).stat().st_mtime)
    if key in _lut_cache:
        return _lut_cache[key]
    size, rows, dmin, dmax = None, [], [0.0] * 3, [1.0] * 3
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s[0] == "#":
            continue
        if s[0].isalpha():
            up = s.upper()
            if up.startswith("LUT_3D_SIZE"):
                size = int(s.split()[1])
            elif up.startswith("DOMAIN_MIN"):
                dmin = [float(x) for x in s.split()[1:4]]
            elif up.startswith("DOMAIN_MAX"):
                dmax = [float(x) for x in s.split()[1:4]]
            continue
        rows.append(s)
    # the numbers in one go (a 65-point table has 824 000 of them); line by line only for odd files
    flat = np.fromstring(" ".join(rows), dtype=np.float64, sep=" ") if rows else np.zeros(0)
    if flat.size != len(rows) * 3:
        flat = np.array([[float(v) for v in r.split()[:3]] for r in rows if len(r.split()) >= 3], np.float64).ravel()
    if not size or flat.size < size ** 3 * 3:
        raise ValueError("不是有效的 3D .cube LUT")
    a = flat[:size ** 3 * 3].astype(np.float32).reshape(size, size, size, 3)
    a = (a - np.array(dmin, np.float32)) / (np.array(dmax, np.float32) - np.array(dmin, np.float32))
    _lut_cache[key] = np.clip(a, 0, 1)
    return _lut_cache[key]


def apply_lut(c: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Trilinear lookup, same as the GPU sampler3D (LINEAR)."""
    n = lut.shape[0]
    p = np.clip(c, 0, 1) * (n - 1)
    i0 = np.floor(p).astype(int)
    i0 = np.minimum(i0, n - 2) if n > 1 else i0
    f = p - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    def L(db, dg, dr):
        return lut[b0 + db, g0 + dg, r0 + dr]
    c00 = L(0, 0, 0) * (1 - fr) + L(0, 0, 1) * fr
    c01 = L(0, 1, 0) * (1 - fr) + L(0, 1, 1) * fr
    c10 = L(1, 0, 0) * (1 - fr) + L(1, 0, 1) * fr
    c11 = L(1, 1, 0) * (1 - fr) + L(1, 1, 1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def lut_texture_bytes(path: str) -> tuple[int, bytes]:
    lut = load_cube(path)
    return lut.shape[0], lut.astype("f4").tobytes()      # moderngl 3D texture: x=r fastest, then g, then b
