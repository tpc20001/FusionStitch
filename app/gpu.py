"""GPU stitching with OpenGL (moderngl): NV12 in -> P010 (10-bit) or NV12 out.

Frames arrive straight from NVDEC as NV12 and leave in the exact layout
NVENC eats (P010 / NV12), so the CPU only moves bytes. The remap tables come
from geometry.float_maps, the same tables the CPU path uses.

Two shaders:
  fast   - blends Y and UV planes directly (no colour grading, no nadir patch)
  graded - per plane: sample both lenses as RGB, blend, colour.grade(),
           nadir patch, back to YUV. Used when any grading/patch is active.
"""
from __future__ import annotations

import numpy as np

import color
import enhance
import geometry as geo

_VERT = """
#version 430
in vec2 pos;
void main() { gl_Position = vec4(pos, 0.0, 1.0); }
"""

_CUBIC = """
// OpenCV-compatible bicubic (A = -0.75) with replicated borders, texelFetch only
vec4 cubicW(float x) {
    const float A = -0.75;
    vec4 w;
    w.x = ((A * (x + 1.0) - 5.0 * A) * (x + 1.0) + 8.0 * A) * (x + 1.0) - 4.0 * A;
    w.y = ((A + 2.0) * x - (A + 3.0)) * x * x + 1.0;
    w.z = ((A + 2.0) * (1.0 - x) - (A + 3.0)) * (1.0 - x) * (1.0 - x) + 1.0;
    w.w = 1.0 - w.x - w.y - w.z;
    return w;
}
vec2 cubic(sampler2D t, vec2 p) {
    ivec2 sz = textureSize(t, 0) - 1;
    vec2 fl = floor(p), f = p - fl;
    ivec2 i0 = ivec2(fl);
    vec4 wx = cubicW(f.x), wy = cubicW(f.y);
    vec2 acc = vec2(0.0);
    for (int j = 0; j < 4; j++) {
        vec2 row = vec2(0.0);
        int yy = clamp(i0.y + j - 1, 0, sz.y);
        for (int i = 0; i < 4; i++)
            row += texelFetch(t, ivec2(clamp(i0.x + i - 1, 0, sz.x), yy), 0).rg * wx[i];
        acc += row * wy[j];
    }
    return acc;
}
"""

SEAM_GLSL = """
uniform sampler2D uSeamTex;         // fixed-mount seam parallax (RG32F, NB x NR band pixels)
uniform int uSeamOn;
uniform float uSeamBand;            // half band height (radians)
uniform vec2 uSeamSize;             // NB, NR
vec3 seamAdjust(vec3 dc, float sgn) {
    if (uSeamOn == 0) return dc;
    float de = asin(clamp(dc.z, -1.0, 1.0));
    if (abs(de) >= uSeamBand) return dc;
    float ph = atan(dc.y, dc.x);
    if (ph < 0.0) ph += 6.28318530718;
    vec2 f = texture(uSeamTex, vec2(ph / 6.28318530718, (de / uSeamBand + 1.0) * 0.5)).rg;
    ph += sgn * f.x * 6.28318530718 / uSeamSize.x;
    de += sgn * f.y * 2.0 * uSeamBand / uSeamSize.y;
    float c = cos(de);
    return vec3(c * cos(ph), c * sin(ph), sin(de));
}
"""

import os as _os
import threading as _threading
SEAM_EVERY = max(1, int(_os.environ.get("FS_SEAM_EVERY", "1") or 1))   # 接縫融合 is measured on every N-th frame at full rate (see _seam_tables)

# Several stitchers run in one process (同時 2～3 段, each in its own thread), next to the threads that
# read the decoders and feed the encoders, and all of them share Python's GIL. In V2.11.0 that made
# the 接縫融合 measurement (9 ms of work on an idle machine) take 16 .. 22 ms in a job and 70 .. 95 ms
# with two segments:
#   - it is ~130 small numpy / OpenCV calls, each of which lets go of the GIL and has to get it back.
#     A thread waiting for the GIL is woken by every one of them and usually loses the race again
#     (the 5 ms forced switch never fires, because each of those wake-ups restarts its timer), so
#     two threads that both want the GIL slow each other down badly.
#   - moderngl keeps the GIL while a GL call runs: waiting for the GPU and copying the finished
#     43 MB frame out froze every other thread, the other stitcher's measurement included.
# What is done about it (2026-11-15, numbers in _t\speed_report.md):
#   - the finished frame is collected with ONE call made through ctypes (_RawGL.read), which needs
#     no GIL while it waits for the GPU and copies (FS_GL_RAW=0: the moderngl way);
#   - only one stitcher measures at a time (_SEAM_LOCK; FS_SEAM_LOCK=0: off). The one waiting for
#     its turn sleeps on the lock, it does not ask for the GIL.
# Tried and NOT the default, because it was no faster or slower in real jobs: the measurement in a
# helper thread while the frame before is collected (FS_SEAM_THREAD=1: as soon as its GL call
# returns, the stitcher's own thread fights its helper for the GIL), and the plane uploads without
# the GIL (FS_GL_RAW=1). Same pictures in every combination.
_raw = _os.environ.get("FS_GL_RAW", "collect") or "collect"      # collect / upload / 1 (both) / 0
RAW_GL = _raw != "0"
RAW_UPLOAD, RAW_COLLECT = _raw in ("1", "upload"), _raw in ("1", "collect")
SEAM_THREAD = (_os.environ.get("FS_SEAM_THREAD", "0") or "0") == "1"
SEAM_LOCK = (_os.environ.get("FS_SEAM_LOCK", "1") or "1") != "0"
_SEAM_LOCK = _threading.Lock()
import contextlib as _contextlib
_NO_LOCK = _contextlib.nullcontext()


class _RawGL:
    """The GL calls of the frame loop that take milliseconds, through ctypes (Windows, GL 4.5 direct
    state access). Made for the context that is current when it is created; raises when a function
    is missing, the caller then stays with moderngl.

    Letting go of the GIL is not free either: with other threads after it, getting it back takes
    milliseconds (measured in a job: 2.5 ms on average, 8 ms at the 90th percentile). So only the
    long calls release it, one call each: waiting for and copying the finished frame (read), and,
    with FS_GL_RAW=1 only, the luma planes (upload: slower in a job, kept for experiments)."""
    UNPACK_ALIGNMENT, RED, RG, UNSIGNED_BYTE = 0x0CF5, 0x1903, 0x8227, 0x1401

    def __init__(self):
        import ctypes
        from ctypes import c_void_p, c_int, c_uint, c_ssize_t
        gl = ctypes.WinDLL("opengl32")
        gl.wglGetProcAddress.restype = c_void_p
        gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]
        held = ctypes.PyDLL("opengl32")          # calls through this one keep the GIL

        def ext(name, kind, res, *args):
            addr = gl.wglGetProcAddress(name)
            if not addr:
                raise RuntimeError("no " + name.decode())
            return kind(res, *args)(addr)
        sub = (None, c_uint, c_int, c_int, c_int, c_int, c_int, c_uint, c_uint, c_void_p)
        self._tex_sub = ext(b"glTextureSubImage2D", ctypes.WINFUNCTYPE, *sub)
        self._tex_sub_held = ext(b"glTextureSubImage2D", ctypes.PYFUNCTYPE, *sub)
        self._get_sub = ext(b"glGetNamedBufferSubData", ctypes.WINFUNCTYPE, None, c_uint, c_ssize_t, c_ssize_t, c_void_p)
        self._pixel_store = held.glPixelStorei
        self._pixel_store.restype, self._pixel_store.argtypes = None, [c_uint, c_int]
        self._flush = held.glFlush
        self._flush.restype, self._flush.argtypes = None, []
        self._error = held.glGetError
        self._error.restype, self._error.argtypes = c_uint, []

    def errors(self) -> bool:
        """True when GL has an error pending (and clears it)."""
        bad = False
        for _ in range(16):
            if self._error() == 0:
                break
            bad = True
        return bad

    def upload(self, tex: int, w: int, h: int, comps: int, addr: int):
        self._pixel_store(self.UNPACK_ALIGNMENT, 1)
        (self._tex_sub if comps == 1 else self._tex_sub_held)(
            tex, 0, 0, 0, w, h, self.RED if comps == 1 else self.RG, self.UNSIGNED_BYTE, addr)

    def read(self, buf: int, n: int, addr: int):
        """Copy n bytes of a pixel buffer to addr; waits (without the GIL) until the GPU has filled it."""
        self._get_sub(buf, 0, n, addr)

    def flush(self):
        self._flush()

SEAMGAIN_GLSL = """
uniform sampler2D uGainTex;          // 接縫色彩補償: EV along the seam, per channel + luma (RGBA32F, K x 1, wraps)
uniform sampler2D uSeamD;            // 接縫寬混合: smooth front - back difference in the seam band (RGB32F, K x M)
uniform int uSgOn, uTbOn;
uniform float uSgSpread;             // falloff of the gain (radians from the seam)
uniform float uTbWide;               // width of the wide blend (radians)
uniform vec2 uTbRow;                 // row of uSeamD: v = e * uTbRow.x + uTbRow.y
vec4 seamGainEV(float psi, float e) {     // EV for the BACK lens (rgb per channel, a = luma); the front gets the negative
    if (uSgOn == 0) return vec4(0.0);
    float t = clamp(1.0 - abs(e) / uSgSpread, 0.0, 1.0);
    if (t <= 0.0) return vec4(0.0);
    return 0.5 * (t * t * (3.0 - 2.0 * t)) * texture(uGainTex, vec2(psi, 0.5));
}
float seamWideW(float e) {                // front weight of the wide blend
    float t = clamp(0.5 + e / uTbWide, 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}
vec3 seamD(float psi, float e) {
    return texture(uSeamD, vec2(psi, e * uTbRow.x + uTbRow.y)).rgb;
}
uniform sampler2D uLensTone;         // 原廠鏡頭曝光: gain by brightness (R32F, N x 2: row 0 front lens, row 1 back lens)
uniform int uLtOn;
vec3 lensTone(vec3 c, float row) {        // every channel by its own value, like a camera tone curve
    if (uLtOn == 0) return c;
    vec3 x = (clamp(c, 0.0, 1.0) * 255.0 + 0.5) / 256.0;
    return c * vec3(texture(uLensTone, vec2(x.r, row)).r, texture(uLensTone, vec2(x.g, row)).r,
                    texture(uLensTone, vec2(x.b, row)).r);
}
"""

_FRAG_PROBE = """
#version 430
uniform sampler2D srcF, srcB;   // luma planes
uniform sampler2D probeMap;     // RGBA32F: front x, front y, back x, back y per probe point
out vec4 color;
""" + _CUBIC + """
void main() {
    vec4 m = texelFetch(probeMap, ivec2(gl_FragCoord.xy), 0);
    color = vec4(cubic(srcF, m.xy).r, cubic(srcB, m.zw).r, 0.0, 1.0);
}
"""

_FRAG_FAST = """
#version 430
uniform sampler2D srcF, srcB;   // R8 (luma) or RG8 (interleaved chroma)
uniform sampler2D lensMap;      // RGBA32F: front x, front y, back x, back y (plane pixels)
uniform sampler2D weightMap;    // RG32F: front weight*gain, back weight*gain
uniform float offset;           // 16/255 luma, 128/255 chroma
uniform int bits;               // 10 -> P010 values, 8 -> NV12 values
uniform sampler2D seamMap;      // RG32F: psi / 2pi, e = radians from the seam plane (接縫融合)
uniform float sgBlack;          // value the gain scales around: black level (luma) or 0.5 (chroma)
uniform vec2 uVgF;              // front lens 2^ev, vignette: gives the narrow weight back from weightMap
uniform float uHalfFov;
uniform float uDScale;          // 寬混合 term (0..1 full range) -> this plane's code values
uniform int toLimited;          // 1: full-range source -> limited-range output
uniform int chromaPlane;        // 0 luma, 1 interleaved chroma
out vec4 color;
""" + _CUBIC + SEAMGAIN_GLSL + """
void main() {
    ivec2 pix = ivec2(gl_FragCoord.xy);
    vec4 m = texelFetch(lensMap, pix, 0);
    vec2 a = texelFetch(weightMap, pix, 0).rg;
    vec2 F = a.x > 0.0 ? cubic(srcF, m.xy) : vec2(offset);
    vec2 B = a.y > 0.0 ? cubic(srcB, m.zw) : vec2(offset);
    vec2 sm = (uSgOn != 0 || uTbOn != 0) ? texelFetch(seamMap, pix, 0).rg : vec2(0.0);
    if (uSgOn != 0) {
        float ev = seamGainEV(sm.x, sm.y).a;
        F = sgBlack + (F - sgBlack) * exp2(-ev);
        B = sgBlack + (B - sgBlack) * exp2(ev);
    }
    vec2 v = offset + (F - offset) * a.x + (B - offset) * a.y;
    if (uTbOn != 0 && abs(sm.y) < 0.5 * uTbWide) {
        vec3 D = seamD(sm.x, sm.y);
        float dy = dot(D, vec3(0.2126, 0.7152, 0.0722));
        vec2 dv = chromaPlane == 1 ? vec2((D.b - dy) / 1.8556, (D.r - dy) / 1.5748) : vec2(dy);
        float gf = uVgF.x * (1.0 + uVgF.y * pow((1.5707963 - sm.y) / uHalfFov, 2.0));
        v += dv * uDScale * (seamWideW(sm.y) - clamp(a.x / gf, 0.0, 1.0));
    }
    v = clamp(v, 0.0, 1.0);
    if (toLimited == 1)
        v = chromaPlane == 1 ? (v - 0.5) * (224.0 / 255.0) + 0.5 : v * (219.0 / 255.0) + 16.0 / 255.0;
    if (bits == 10)  // 8-bit code value * 4 = 10-bit code, stored in the top bits of 16
        v = clamp(floor(v * 1020.0 + 0.5), 0.0, 1023.0) * 64.0 / 65535.0;
    color = vec4(v, 0.0, 1.0);
}
"""

_FRAG_GRADED = """
#version 430
uniform sampler2D fY, fUV, bY, bUV;   // source planes of both lenses
uniform sampler2D lensMap, weightMap; // luma-resolution tables (also used by the chroma pass)
uniform sampler2D patchMap;           // RGB32F: u, v, alpha (nadir patch), luma resolution
uniform sampler2D logo;               // RGBA8 patch image
uniform int patchMode;                // 0 off, 1 colour, 2 image
uniform vec3 patchColor;
uniform int full;                     // source range: 1 = full (pc), 0 = limited (tv)
uniform int outFull;                  // output range (0 = limited, what players and YouTube expect)
uniform int bits;
uniform vec3 uCfA, uCfB, uCbA, uCbB;  // per-lens colour match: rgb * A + B
uniform sampler2D seamMap;            // RG32F: psi / 2pi, e = radians from the seam plane, luma resolution
uniform vec2 uVgF;                    // front lens 2^ev, vignette: gives the narrow weight back from weightMap
uniform float uHalfFov;
layout(location = 0) out vec4 color;      // Y plane, final code values
layout(location = 1) out vec4 colorUV;    // U, V of this pixel (float), averaged 2x2 by _FRAG_CHROMA
""" + _CUBIC + color.GLSL + SEAMGAIN_GLSL + """
vec3 yuv2rgb(float y, vec2 c) {
    c -= 0.5;
    if (full == 0) { y = (y - 16.0 / 255.0) * 255.0 / 219.0; c *= 255.0 / 224.0; }
    return vec3(y + 1.5748 * c.y, y - 0.1873 * c.x - 0.4681 * c.y, y + 1.8556 * c.x);
}
vec3 rgb2yuv(vec3 r) {                      // output is always limited range unless outFull
    float y = dot(r, vec3(0.2126, 0.7152, 0.0722));
    vec2 c = vec2((r.b - y) / 1.8556, (r.r - y) / 1.5748);
    if (outFull == 0) { y = y * 219.0 / 255.0 + 16.0 / 255.0; c *= 224.0 / 255.0; }
    return vec3(y, c + 0.5);
}
vec3 lensRGB(sampler2D ty, sampler2D tuv, vec2 p) {     // p = luma pixel coords
    return yuv2rgb(cubic(ty, p).r, cubic(tuv, (p + 0.5) * 0.5 - 0.5));
}
vec3 shadeAt(ivec2 pix) {                                // final RGB of one luma-resolution output pixel
    vec4 m = texelFetch(lensMap, pix, 0);
    vec2 a = texelFetch(weightMap, pix, 0).rg;
    vec3 gF = vec3(1.0), gB = vec3(1.0);
    vec2 sm = (uSgOn != 0 || uTbOn != 0) ? texelFetch(seamMap, pix, 0).rg : vec2(0.0);
    if (uSgOn != 0) { vec3 ev = seamGainEV(sm.x, sm.y).rgb; gF = exp2(-ev); gB = exp2(ev); }
    vec3 rgb = vec3(0.0);
    if (a.x > 0.0) rgb += lensTone(lensRGB(fY, fUV, m.xy) * uCfA + uCfB, 0.25) * gF * a.x;
    if (a.y > 0.0) rgb += lensTone(lensRGB(bY, bUV, m.zw) * uCbA + uCbB, 0.75) * gB * a.y;
    if (uTbOn != 0 && abs(sm.y) < 0.5 * uTbWide) {       // wide blend of the smooth difference
        float gf = uVgF.x * (1.0 + uVgF.y * pow((1.5707963 - sm.y) / uHalfFov, 2.0));
        rgb += seamD(sm.x, sm.y) * (seamWideW(sm.y) - clamp(a.x / gf, 0.0, 1.0));
    }
    rgb = grade(clamp(rgb, 0.0, 1.0));
    if (patchMode > 0) {
        vec3 pm = texelFetch(patchMap, pix, 0).rgb;
        if (pm.z > 0.0) {
            vec4 lc = patchMode == 2 ? texture(logo, pm.xy) : vec4(patchColor, 1.0);
            rgb = mix(rgb, lc.rgb, pm.z * lc.a);
        }
    }
    return clamp(rgb, 0.0, 1.0);
}
void main() {                // every output pixel is shaded once: Y goes out as it is, U and V at full
    vec3 yuv = rgb2yuv(shadeAt(ivec2(gl_FragCoord.xy)));     // resolution for the small chroma pass
    vec2 v = vec2(yuv.x, 0.0);
    if (bits == 10)
        v = clamp(floor(v * 1020.0 + 0.5), 0.0, 1023.0) * 64.0 / 65535.0;
    color = vec4(v, 0.0, 1.0);
    colorUV = vec4(yuv.yz, 0.0, 1.0);
}
"""

# Second pass of the graded / dynamic shaders: 4:2:0 chroma = average of the 2x2 full-resolution
# results the first pass left in a float texture (the same sum, in the same order, the chroma pass
# used to get by shading every pixel a second time).
_FRAG_CHROMA = """
#version 430
uniform sampler2D uvFull;       // RG32F: U, V of every luma-resolution pixel
uniform int bits;
out vec4 color;
void main() {
    ivec2 pix = ivec2(gl_FragCoord.xy);
    ivec2 mx = textureSize(uvFull, 0) - 1;
    vec2 acc = vec2(0.0);
    for (int j = 0; j < 2; j++)
        for (int i = 0; i < 2; i++)
            acc += texelFetch(uvFull, min(pix * 2 + ivec2(i, j), mx), 0).rg;
    vec2 v = acc * 0.25;
    if (bits == 10)
        v = clamp(floor(v * 1020.0 + 0.5), 0.0, 1023.0) * 64.0 / 65535.0;
    color = vec4(v, 0.0, 1.0);
}
"""


# Per-frame rotation (gyro stabilisation): the lens projection is evaluated in the
# shader instead of read from tables (same math as geometry.project / front_weight).
_FRAG_DYN = """
#version 430
uniform sampler2D fY, fUV, bY, bUV;
uniform sampler2D logo;
uniform int patchMode; uniform vec3 patchColor; uniform vec3 uPatch;
uniform int full, bits, outFull;
uniform mat3 uGT;                 // view -> camera (per frame: stabilisation + heading)
uniform mat3 uRfT, uRbT;          // camera -> lens local
uniform vec4 uLf, uLb;            // cx, cy (normalised), r (/H), unused
uniform vec2 uGf, uGb;            // 2^ev, vignette
uniform float uHalf, uBlend;
uniform float uK3;                // lens model radial term: r = R u (1 + k3 (1 - u^2)), u = theta / half fov
uniform vec2 uSrc;                // luma source size
uniform vec2 uOut;                // luma-plane output size
uniform int uProj;                // 0 equirect, 1 planet, 2 flat (rectilinear)
uniform mat3 uPlanetV; uniform float uPlanetK;
uniform vec3 uCfA, uCfB, uCbA, uCbB;
uniform vec3 uOmega;              // angular velocity during this frame (camera frame, rad/s)
uniform float uRS;                // rolling shutter: sensor readout time (s), 0 = off
layout(location = 0) out vec4 color;      // Y plane, final code values
layout(location = 1) out vec4 colorUV;    // U, V of this pixel (float), averaged 2x2 by _FRAG_CHROMA
const float PI = 3.14159265358979;
""" + _CUBIC + color.GLSL + SEAM_GLSL + SEAMGAIN_GLSL + """
vec3 yuv2rgb(float y, vec2 c) {
    c -= 0.5;
    if (full == 0) { y = (y - 16.0 / 255.0) * 255.0 / 219.0; c *= 255.0 / 224.0; }
    return vec3(y + 1.5748 * c.y, y - 0.1873 * c.x - 0.4681 * c.y, y + 1.8556 * c.x);
}
vec3 rgb2yuv(vec3 r) {                      // output is always limited range unless outFull
    float y = dot(r, vec3(0.2126, 0.7152, 0.0722));
    vec2 c = vec2((r.b - y) / 1.8556, (r.r - y) / 1.5748);
    if (outFull == 0) { y = y * 219.0 / 255.0 + 16.0 / 255.0; c *= 224.0 / 255.0; }
    return vec3(y, c + 0.5);
}
vec3 outDir(vec2 fc) {                       // pixel centre -> view direction (row 0 = top)
    if (uProj == 2) {                        // uPlanetK = tan(horizontal fov / 2)
        vec2 p = vec2((fc.x / uOut.x * 2.0 - 1.0) * uPlanetK, (1.0 - fc.y / uOut.y * 2.0) * uPlanetK * uOut.y / uOut.x);
        return uPlanetV * normalize(vec3(p, 1.0));
    }
    if (uProj == 1) {
        vec2 p = vec2((fc.x / uOut.x * 2.0 - 1.0) * (uOut.x / uOut.y), 1.0 - fc.y / uOut.y * 2.0) * uPlanetK;
        float rho = max(length(p), 1e-9), c = 2.0 * atan(rho * 0.5);
        return uPlanetV * vec3(sin(c) * p.x / rho, -cos(c), sin(c) * p.y / rho);
    }
    float lon = (fc.x / uOut.x * 2.0 - 1.0) * PI, lat = (0.5 - fc.y / uOut.y) * PI;
    return vec3(cos(lat) * sin(lon), sin(lat), cos(lat) * cos(lon));
}
vec3 project(vec3 l, vec4 L) {                // lens-local direction -> luma pixel coords + theta
    float th = acos(clamp(l.z, -1.0, 1.0)), ph = atan(l.y, l.x);
    float u = th / uHalf;
    float r = L.z * uSrc.y * u * (1.0 + uK3 * (1.0 - u * u));
    return vec3(L.x * uSrc.x + r * cos(ph) - 0.5, L.y * uSrc.y - r * sin(ph) - 0.5, th);
}
vec3 projectRS(vec3 c, mat3 RT, vec4 L) {   // camera direction -> lens pixel, with rolling shutter
    vec3 p = project(RT * c, L);
    if (uRS != 0.0) {
        for (int i = 0; i < 2; i++) {         // the row decides when it was read: undo the rotation since mid-frame
            float dt = (p.y / uSrc.y - 0.5) * uRS;
            p = project(RT * normalize(c - cross(uOmega, c) * dt), L);
        }
    }
    return p;
}
bool valid(vec3 p) {
    return p.z <= uHalf && p.x >= 0.0 && p.y >= 0.0 && p.x <= uSrc.x - 1.0 && p.y <= uSrc.y - 1.0;
}
vec3 lensRGB(sampler2D ty, sampler2D tuv, vec2 pl) {
    vec2 pC = (pl + 0.5) * 0.5 - 0.5;
    return yuv2rgb(cubic(ty, pl).r, cubic(tuv, pC));
}
vec3 shadeAt(vec2 fc) {                     // fc = pixel centre in the luma output grid
    vec3 d = outDir(fc);
    vec3 dc = uGT * d;
    vec3 F = projectRS(seamAdjust(dc, -0.5), uRfT, uLf), B = projectRS(seamAdjust(dc, 0.5), uRbT, uLb);
    float t = clamp(0.5 + (B.z - F.z) / (2.0 * uBlend), 0.0, 1.0);
    float w = t * t * (3.0 - 2.0 * t);
    bool vf = valid(F), vb = valid(B);
    if (vf && !vb) w = 1.0;
    if (!vf && vb) w = 0.0;
    float gf = uGf.x * (1.0 + uGf.y * pow(F.z / uHalf, 2.0));
    float gb = uGb.x * (1.0 + uGb.y * pow(B.z / uHalf, 2.0));
    vec3 gF = vec3(gf), gB = vec3(gb);
    float psi = 0.0, se = 0.0;
    if (uSgOn != 0 || uTbOn != 0) {
        vec3 lf = uRfT * dc;
        psi = atan(lf.y, lf.x) / (2.0 * PI);
        if (psi < 0.0) psi += 1.0;
        se = asin(clamp(lf.z, -1.0, 1.0));
    }
    if (uSgOn != 0) { vec3 ev = seamGainEV(psi, se).rgb; gF *= exp2(-ev); gB *= exp2(ev); }
    vec3 rgb = vec3(0.0);
    if (w > 0.0) rgb += lensTone(lensRGB(fY, fUV, F.xy) * uCfA + uCfB, 0.25) * (w * gF);
    if (w < 1.0) rgb += lensTone(lensRGB(bY, bUV, B.xy) * uCbA + uCbB, 0.75) * ((1.0 - w) * gB);
    if (uTbOn != 0 && abs(se) < 0.5 * uTbWide) rgb += seamD(psi, se) * (seamWideW(se) - w);
    rgb = grade(clamp(rgb, 0.0, 1.0));
    if (patchMode > 0) {
        float a = acos(clamp(-d.y, -1.0, 1.0));
        float r = a / uPatch.x, ph = atan(d.z, d.x) + uPatch.y;
        float al = clamp((uPatch.x - a) / uPatch.z, 0.0, 1.0);
        if (al > 0.0) {
            vec4 lc = patchMode == 2 ? texture(logo, vec2(0.5 + 0.5 * r * cos(ph), 0.5 - 0.5 * r * sin(ph))) : vec4(patchColor, 1.0);
            rgb = mix(rgb, lc.rgb, al * lc.a);
        }
    }
    return clamp(rgb, 0.0, 1.0);
}
void main() {                // shaded once per output pixel, see _FRAG_GRADED
    vec3 yuv = rgb2yuv(shadeAt(gl_FragCoord.xy));
    vec2 v = vec2(yuv.x, 0.0);
    if (bits == 10)
        v = clamp(floor(v * 1020.0 + 0.5), 0.0, 1023.0) * 64.0 / 65535.0;
    color = vec4(v, 0.0, 1.0);
    colorUV = vec4(yuv.yz, 0.0, 1.0);
}
"""


def available() -> tuple[bool, str]:
    try:
        import moderngl
        ctx = moderngl.create_standalone_context(require=430)
        name = ctx.info["GL_RENDERER"]
        ctx.release()
        return True, name
    except Exception as e:  # no GPU / driver / package
        return False, str(e)


def load_logo_rgba(path: str, size: int = 1024) -> tuple[int, int, bytes]:
    import cv2
    from imageio_util import imread
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED) if path else None
    if img is None:
        raise RuntimeError(f"無法讀取 Logo 圖片：{path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    rgba = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)
    return size, size, rgba.tobytes()


class GPUStitcher:
    def __init__(self, params: dict, src_w: int, src_h: int, out_w: int, out_h: int, bits: int = 10,
                 spec: dict | None = None, grade: dict | None = None, patch: dict | None = None,
                 full_range: bool = True, dynamic: bool = False, lens_track: bool = False):
        import moderngl
        self.ctx = ctx = moderngl.create_standalone_context(require=430)
        self.src_w, self.src_h, self.out_w, self.out_h, self.bits = src_w, src_h, out_w, out_h, bits
        self.params, self.spec = params, spec or {"type": "equirect"}
        patch_on = bool(patch and patch.get("mode", "off") != "off")
        self.dyn_seam = bool(params.get("seamflow_dyn"))
        dynamic = dynamic or self.dyn_seam          # per-frame seam flow needs the per-pixel projection
        self.dynamic = dynamic
        self.graded = dynamic or lens_track or (not color.is_identity(grade)) or patch_on or geo.lens_color_active(params)
        frag = _FRAG_DYN if dynamic else (_FRAG_GRADED if self.graded else _FRAG_FAST)
        self.prog = ctx.program(vertex_shader=_VERT, fragment_shader=frag)
        vbo = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], "f4").tobytes())
        self.vao = ctx.vertex_array(self.prog, [(vbo, "2f", "pos")])
        self.uv_full = self.prog_c = self.vao_c = None
        if self.graded:                          # graded / dynamic: one shading pass + a small chroma pass
            self.prog_c = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG_CHROMA)
            self.vao_c = ctx.vertex_array(self.prog_c, [(vbo, "2f", "pos")])
            self.uv_full = ctx.texture((out_w, out_h), 2, dtype="f4")
            self.uv_full.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.full_range = full_range
        self.frame_step = 1                      # source frames between two pushes (time-lapse)
        self._lens_colors = (geo.lens_color(params, "front"), geo.lens_color(params, "back"))
        self.sg_spread = float(params.get("seamgain", 0.0) or 0.0)
        self.sg_wide = geo.seam_wide(params)
        seam_on = self.sg_spread > 0 or self.sg_wide > 0

        def tex(size, comps, dtype, data=None, linear=False):
            t = ctx.texture(size, comps, data, dtype=dtype, alignment=1)
            t.filter = (moderngl.LINEAR, moderngl.LINEAR) if linear else (moderngl.NEAREST, moderngl.NEAREST)
            return t

        cw, ch = src_w // 2, src_h // 2
        self.src = {k: (tex((src_w, src_h), 1, "f1"), tex((cw, ch), 2, "f1")) for k in ("f", "b")}
        self.planes = []
        for scale, comps in ((1, 1), (2, 2)):
            W, H = out_w // scale, out_h // scale
            extra = [self.uv_full] if self.graded and scale == 1 else []
            if dynamic or (self.graded and scale == 2):      # graded: the chroma pass needs no tables
                fmt = "nu2" if bits == 10 else "f1"
                target = ctx.texture((W, H), comps, dtype=fmt, alignment=1)
                self.planes.append({"fbo": ctx.framebuffer(color_attachments=[target] + extra), "comps": comps,
                                    "size": (W, H), "patch": None, "lens": None, "w": None, "seam": None})
                continue
            m = geo.float_maps(params, src_w, src_h, W, H, scale, spec)
            lens = tex((W, H), 4, "f4", np.dstack([m["fx"], m["fy"], m["bx"], m["by"]]).astype("f4").tobytes())
            wts = tex((W, H), 2, "f4", np.dstack([m["af"], m["ab"]]).astype("f4").tobytes())
            pm = geo.patch_map(m["dirs"], patch) if patch_on else None
            ptex = tex((W, H), 3, "f4", pm.tobytes()) if pm is not None else None
            stex = (tex((W, H), 2, "f4", np.dstack([m["seam_psi"], m["seam_e"]]).astype("f4").tobytes())
                    if seam_on else None)
            del m
            fmt = "nu2" if bits == 10 else "f1"
            target = ctx.texture((W, H), comps, dtype=fmt, alignment=1)
            fbo = ctx.framebuffer(color_attachments=[target] + extra)
            self.planes.append({"lens": lens, "w": wts, "patch": ptex, "seam": stex, "fbo": fbo, "comps": comps,
                                "offset": (16 if scale == 1 else 128) / 255, "size": (W, H),
                                "black": (0.0 if full_range else 16 / 255) if scale == 1 else 0.5})
        self._gA = None
        if self.graded:
            u = color.uniforms(grade)
            self._gA = tuple(float(x) for x in u["gA"])
            for k, v in u.items():
                if k in self.prog:
                    self.prog[k].value = v
            g = color.merge(grade)
            if g["lut"] and g["lut_strength"] > 0:
                n, data = color.lut_texture_bytes(g["lut"])
                self.lut = ctx.texture3d((n, n, n), 3, data, dtype="f4")
            else:
                self.lut = ctx.texture3d((2, 2, 2), 3, np.zeros(24, "f4").tobytes(), dtype="f4")
            self.lut.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.lut.repeat_x = self.lut.repeat_y = self.lut.repeat_z = False
            mode = {"off": 0, "color": 1, "image": 2}.get((patch or {}).get("mode", "off"), 0)
            if mode == 2:
                w, h, data = load_logo_rgba(patch.get("image", ""))
                self.logo = tex((w, h), 4, "f1", data, linear=True)
            else:
                self.logo = tex((1, 1), 4, "f1", bytes([0, 0, 0, 255]))
            self.logo.repeat_x = self.logo.repeat_y = False
            for which, (na, nb) in (("front", ("uCfA", "uCfB")), ("back", ("uCbA", "uCbB"))):
                c = geo.lens_color(params, which)
                if na in self.prog:
                    self.prog[na].value = tuple(c[:3])
                    self.prog[nb].value = tuple(c[3:])
            if dynamic:
                self._set_static_uniforms(patch)
                self._setup_seam(params.get("_seamflow"))
                if self.dyn_seam:
                    self._setup_dynseam()
                self.set_rotation(None)
            for name, v in (("patchMode", mode), ("full", 1 if full_range else 0), ("outFull", 0),
                            ("patchColor", tuple(float(c) / 255 for c in (patch or {}).get("color", [0, 0, 0])))):
                if name in self.prog:
                    self.prog[name].value = v
        self._lens_tone = None
        self.lt_tex = ctx.texture((256, 2), 1, np.ones(512, "f4").tobytes(), dtype="f4")
        self.lt_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.lt_tex.repeat_x = self.lt_tex.repeat_y = False
        if "uLtOn" in self.prog:
            self.prog["uLtOn"].value = 0
        self._setup_seamgain(vbo)
        self.enh_grade = color.merge(grade) if enhance.active(grade) else None
        self.enh = enhance.GLEnhancer(ctx) if self.enh_grade else None
        bpp = 2 if bits == 10 else 1
        self.y_bytes = out_w * out_h * bpp
        self.frame_bytes = self.y_bytes * 3 // 2
        self.out = bytearray(self.frame_bytes)
        # two pixel-pack buffers: GPU renders frame N while the CPU collects N-1
        self.pbos = [ctx.buffer(reserve=self.frame_bytes) for _ in range(2)]
        self.n = 0
        self.raw = None
        self._raw_new = True                     # first frame: check that the direct calls really work
        if RAW_GL:
            try:
                self.raw = _RawGL()
            except Exception:                    # not Windows / no GL 4.5: moderngl does it all
                self.raw = None

    def _set_static_uniforms(self, patch):
        import math
        P = self.params
        u = {"uLf": (P["front"]["cx"], P["front"]["cy"], P["front"]["r"], 0.0),
             "uLb": (P["back"]["cx"], P["back"]["cy"], P["back"]["r"], 0.0),
             "uGf": (2 ** P["front"]["ev"], P["front"]["vignette"]), "uGb": (2 ** P["back"]["ev"], P["back"]["vignette"]),
             "uHalf": math.radians(P["fov"]) / 2, "uBlend": max(math.radians(P["blend"]), 1e-4),
             "uK3": float(P.get("k3", 0.0) or 0.0),
             "uSrc": (float(self.src_w), float(self.src_h)),
             "uProj": {"planet": 1, "flat": 2}.get(self.spec.get("type"), 0)}
        pt = patch or {}
        u["uPatch"] = (math.radians(float(pt.get("size", 30))) / 2, math.radians(float(pt.get("rotate", 0))),
                       max(1e-4, math.radians(float(pt.get("feather", 1.0)))))
        for k, v in u.items():
            if k in self.prog:
                self.prog[k].value = v
        if self.spec.get("type") in ("planet", "flat"):
            self.set_view(self.spec)

    def _setup_seamgain(self, vbo=None):
        """接縫融合: per-frame gain table and difference table (see geometry.SeamBlend)."""
        import math
        import moderngl
        self.sg = None
        self._sg_pool = None
        self._sg_count = 0
        self._sg_prev = self._sg_next = None
        P = self.params
        on_g, on_w = (1 if self.sg_spread > 0 else 0), (1 if self.sg_wide > 0 else 0)
        for k, v in (("uSgOn", on_g), ("uTbOn", on_w), ("uSgSpread", max(math.radians(self.sg_spread), 1e-4)),
                     ("uTbWide", max(math.radians(self.sg_wide), 1e-4)),
                     ("uVgF", (2 ** P["front"]["ev"], P["front"]["vignette"])),
                     ("uHalfFov", math.radians(P["fov"]) / 2)):
            if k in self.prog:
                self.prog[k].value = v
        if not (on_g or on_w):
            return
        ctx = self.ctx
        self.sg = geo.SeamBlend(P, self.src_w, self.src_h, rgb=self.graded)
        K, M = self.sg.K, self.sg.M
        self.sg_gain = ctx.texture((K, 1), 4, np.zeros(K * 4, "f4").tobytes(), dtype="f4")
        self.sg_D = ctx.texture((K, M), 3, np.zeros(K * M * 3, "f4").tobytes(), dtype="f4", alignment=1)
        for t in (self.sg_gain, self.sg_D):
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x, t.repeat_y = True, False
        if "uTbRow" in self.prog:
            self.prog["uTbRow"].value = tuple(float(x) for x in self.sg.rows())
        self.sg_last = None
        if SEAM_THREAD:
            from concurrent.futures import ThreadPoolExecutor
            self._sg_pool = ThreadPoolExecutor(1, thread_name_prefix="fs-seam")

    def _seamgain_pass(self, front, back, mult=1, upload=True):
        """Measure both lenses in the overlap band of this frame (on the CPU, from the NV12
        buffers) and upload the gain / difference tables."""
        w, h = self.src_w, self.src_h
        ysz = w * h
        planes = []
        for buf in (front, back):
            a = np.frombuffer(buf, np.uint8, count=ysz * 3 // 2)
            planes.append((a[:ysz].reshape(h, w), a[ysz:].reshape(h // 2, w // 2, 2)))
        F, B = self.sg.sample(planes[0], planes[1], self.full_range, self._lens_colors,
                              self._lens_tone if self.graded else None)
        gain, D = self.sg.update(F, B, self.frame_step * mult)
        self.sg_last = gain
        if upload:
            self.sg_gain.write(gain.tobytes())
            self.sg_D.write(D.tobytes())
        return gain, D

    def _seam_tables(self, front, back):
        """The gain / difference tables of this frame as bytes. No GL in here (with FS_SEAM_THREAD=1
        it runs in the helper thread)."""
        with (_SEAM_LOCK if SEAM_LOCK else _NO_LOCK):
            # SEAM_EVERY = N measures only every N-th frame at full rate (the measurement costs ~4 ms of
            # ~28 per frame); the tables then move linearly from one measurement to the next (one
            # measurement behind), so nothing jumps. Holding a table for two frames (R114) made the seam
            # band flicker a little more (seam / control 1.36 instead of 1.19, Fusion Studio 1.15).
            every = SEAM_EVERY if self.frame_step == 1 else 1
            if every <= 1:
                gain, D = self._seamgain_pass(front, back, upload=False)
            else:
                k = self._sg_count % every
                if k == 0:
                    t = self._seamgain_pass(front, back, every, upload=False)
                    self._sg_prev = self._sg_next if self._sg_next is not None else t
                    self._sg_next = t
                w = k / every
                gain = (self._sg_prev[0] * (1 - w) + self._sg_next[0] * w).astype("f4")
                D = (self._sg_prev[1] * (1 - w) + self._sg_next[1] * w).astype("f4")
            self._sg_count += 1
            return gain.tobytes(), D.tobytes()

    def _upload(self, front, back):
        """Source planes of both lenses (NV12 buffers) into their textures."""
        ysz = self.src_w * self.src_h
        if self.raw is not None and RAW_UPLOAD:
            if self._raw_new:
                self.raw.errors()
            for key, buf in (("f", front), ("b", back)):
                a = np.frombuffer(buf, np.uint8)
                if a.size < ysz * 3 // 2:
                    raise ValueError("frame buffer smaller than one NV12 frame")
                ty, tuv = self.src[key]
                addr = a.ctypes.data
                self.raw.upload(tuv.glo, self.src_w // 2, self.src_h // 2, 2, addr + ysz)
                self.raw.upload(ty.glo, self.src_w, self.src_h, 1, addr)
            if not self._raw_new:
                return
            if not self.raw.errors():
                return
            self.raw = None                      # the driver refused: the moderngl way from now on
        for key, buf in (("f", front), ("b", back)):
            ty, tuv = self.src[key]
            ty.write(buf[:ysz])
            tuv.write(buf[ysz:ysz * 3 // 2])

    def _collect(self, pbo, dst):
        """Copy a finished frame out of its pixel buffer (waits for the GPU to get there)."""
        if self.raw is not None and RAW_COLLECT:
            a = np.frombuffer(dst, np.uint8)
            if a.size >= self.frame_bytes and a.flags.writeable:
                if self._raw_new:
                    self.raw.errors()
                self.raw.read(pbo.glo, self.frame_bytes, a.ctypes.data)
                if not self._raw_new:
                    return
                self._raw_new = False            # first frame: did the direct calls work?
                if not self.raw.errors():
                    return
            self.raw = None                      # no: the moderngl way from now on (and for this frame)
        pbo.read_into(dst)

    def _setup_dynseam(self):
        """動態接縫光流: band flow measured on every frame (seamdyn), replaces a static seamflow."""
        import moderngl
        import seamdyn
        self.ds = seamdyn.DynSeam(self.params, self.src_w, self.src_h)
        nb, nr = self.ds.nb, self.ds.nr
        self.seam_tex = self.ctx.texture((nb, nr), 2, np.zeros((nr, nb, 2), "f4").tobytes(), dtype="f4")
        self.seam_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.seam_tex.repeat_x, self.seam_tex.repeat_y = True, False
        for k, v in (("uSeamOn", 1), ("uSeamBand", np.radians(self.ds.band)), ("uSeamSize", (float(nb), float(nr)))):
            if k in self.prog:
                self.prog[k].value = v

    def _dynseam_pass(self, front, back):
        n = self.src_w * self.src_h
        yf = np.frombuffer(front, np.uint8, count=n).reshape(self.src_h, self.src_w)
        yb = np.frombuffer(back, np.uint8, count=n).reshape(self.src_h, self.src_w)
        self.seam_tex.write(self.ds.update(yf, yb, self.frame_step).tobytes())

    def _setup_seam(self, sf):
        import moderngl
        if sf is None:
            data, nb, nr, band, on = np.zeros(8, "f4"), 2, 2, 6.0, 0
        else:
            fl = np.ascontiguousarray(np.asarray(sf["flow"], np.float32))
            nr, nb = fl.shape[:2]
            data, band, on = fl, float(sf.get("band", 6.0)), 1
        self.seam_tex = self.ctx.texture((nb, nr), 2, data.tobytes(), dtype="f4")
        self.seam_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.seam_tex.repeat_x, self.seam_tex.repeat_y = True, False
        for k, v in (("uSeamOn", on), ("uSeamBand", np.radians(band)), ("uSeamSize", (float(nb), float(nr)))):
            if k in self.prog:
                self.prog[k].value = v

    def set_view(self, v: dict):
        """Per-frame virtual camera for planet / flat output (keyframed reframing)."""
        import camerapath
        kind = self.spec.get("type")
        V = camerapath.view_matrix(kind, v)
        K = camerapath.flat_k(v.get("fov", 90.0)) if kind == "flat" else camerapath.planet_k(v.get("fov", 270.0))
        with self.ctx:
            if "uPlanetV" in self.prog:
                self.prog["uPlanetV"].value = tuple(V.T.flatten())
            if "uPlanetK" in self.prog:
                self.prog["uPlanetK"].value = float(K)

    def set_lens_color(self, front6, back6):
        """Per-frame lens colour match (colour track)."""
        self._lens_colors = (list(front6), list(back6))
        with self.ctx:
            for c, (na, nb) in ((front6, ("uCfA", "uCfB")), (back6, ("uCbA", "uCbB"))):
                if na in self.prog:
                    self.prog[na].value = tuple(float(x) for x in c[:3])
                    self.prog[nb].value = tuple(float(x) for x in c[3:6])

    def set_lens_tone(self, tables):
        """Per-frame 原廠鏡頭曝光: (2, 256) gain by brightness for the front / back lens, or None."""
        self._lens_tone = None if tables is None else np.ascontiguousarray(tables, np.float32)
        with self.ctx:
            if self._lens_tone is not None:
                self.lt_tex.write(self._lens_tone.tobytes())
            if "uLtOn" in self.prog:
                self.prog["uLtOn"].value = 0 if self._lens_tone is None else 1

    def set_exposure(self, ev: float):
        """Per-frame extra exposure in EV on top of the grade (自動亮度). Needs the graded path."""
        if self._gA is None or "gA" not in self.prog:
            return
        a = self._gA
        with self.ctx:
            self.prog["gA"].value = (a[0] + float(ev), a[1], a[2], a[3])

    def set_omega(self, omega, readout_s: float):
        """Per-frame rolling-shutter correction (dynamic mode only): angular velocity (rad/s,
        camera frame) and sensor readout time in seconds (0 = off)."""
        with self.ctx:
            if "uOmega" in self.prog:
                self.prog["uOmega"].value = tuple(float(x) for x in omega)
                self.prog["uRS"].value = float(readout_s)

    def set_rotation(self, S):
        """Per-frame stabilisation S (view -> camera), dynamic mode only."""
        p = dict(self.params)
        p["_stab"] = None if S is None else S
        with self.ctx:
            mats = {"uGT": geo.global_rotation(p).T, "uRfT": geo.lens_rotation(p, "front").T,
                    "uRbT": geo.lens_rotation(p, "back").T}
            for name, M in mats.items():
                if name in self.prog:
                    self.prog[name].value = tuple(M.T.flatten())        # GLSL is column-major

    def _bind(self, name, unit):
        if name in self.prog:
            self.prog[name].value = unit

    def _render(self, front, back, pbo):
        with self.ctx:          # make this context current (several may exist in one thread)
            self._render_now(front, back, pbo)

    def _seam_start(self, front, back):
        """接縫融合 tables of this frame: measured here and now, or (FS_SEAM_THREAD=1) started in the
        helper thread, then this returns a future. _render_now picks them up before it draws, so the
        measurement never outlives push() / stitch()."""
        if self.sg is None:
            return None
        if self._sg_pool is not None:
            return self._sg_pool.submit(self._seam_tables, front, back)
        return self._seam_tables(front, back)

    def _render_now(self, front, back, pbo, tables=False):
        if tables is False:
            tables = self._seam_start(front, back)
        self._upload(front, back)
        if getattr(self, "ds", None) is not None:
            self._dynseam_pass(front, back)
        src = self.src
        if self.enh:                         # denoise / sharpen pre-pass on the source planes
            src = {k: (self.enh.run(self.src[k][0], k + "y", 1, self.enh_grade, True),
                       self.enh.run(self.src[k][1], k + "uv", 2, self.enh_grade, False)) for k in ("f", "b")}
        if tables is not None:
            gain, D = tables.result() if hasattr(tables, "result") else tables
            self.sg_gain.write(gain)
            self.sg_D.write(D)
        self.prog["bits"].value = self.bits
        dtype = "nu2" if self.bits == 10 else "f1"
        offset = 0
        if self.sg is not None:
            self.sg_gain.use(10)
            self._bind("uGainTex", 10)
            self.sg_D.use(12)
            self._bind("uSeamD", 12)
        self.lt_tex.use(13)
        self._bind("uLensTone", 13)
        for i, pl in enumerate(self.planes):
            if self.graded and i == 1:                   # chroma: 2x2 average of what the first pass shaded
                self.uv_full.use(0)
                self.prog_c["uvFull"].value = 0
                self.prog_c["bits"].value = self.bits
                pl["fbo"].use()
                self.ctx.viewport = (0, 0, *pl["size"])
                self.vao_c.render()
                pl["fbo"].read_into(pbo, components=pl["comps"], alignment=1, dtype=dtype, write_offset=offset)
                break
            if self.dynamic:
                for unit, t in enumerate((src["f"][0], src["f"][1], src["b"][0], src["b"][1])):
                    t.use(unit)
                for unit, name in enumerate(("fY", "fUV", "bY", "bUV")):
                    self._bind(name, unit)
                self.logo.use(7)
                self._bind("logo", 7)
                self.lut.use(8)
                self._bind("gLut", 8)
                self.seam_tex.use(9)
                self._bind("uSeamTex", 9)
                if "uOut" in self.prog:
                    self.prog["uOut"].value = (float(self.out_w), float(self.out_h))
            elif self.graded:
                p0 = self.planes[0]                      # chroma is computed from the luma-resolution tables
                for unit, t in enumerate((src["f"][0], src["f"][1], src["b"][0], src["b"][1],
                                          p0["lens"], p0["w"])):
                    t.use(unit)
                for unit, name in enumerate(("fY", "fUV", "bY", "bUV", "lensMap", "weightMap")):
                    self._bind(name, unit)
                if self.planes[0]["patch"] is not None:
                    self.planes[0]["patch"].use(6)
                    self._bind("patchMap", 6)
                self.logo.use(7)
                self._bind("logo", 7)
                self.lut.use(8)
                self._bind("gLut", 8)
                if self.sg is not None:
                    p0["seam"].use(11)
                    self._bind("seamMap", 11)
            else:
                src["f"][i].use(0)
                src["b"][i].use(1)
                pl["lens"].use(2)
                pl["w"].use(3)
                for name, unit in (("srcF", 0), ("srcB", 1), ("lensMap", 2), ("weightMap", 3)):
                    self._bind(name, unit)
                self.prog["offset"].value = pl["black"] if self.full_range and i == 0 else pl["offset"]
                if "toLimited" in self.prog:
                    self.prog["toLimited"].value = 1 if self.full_range else 0
                    self.prog["chromaPlane"].value = i
                if self.sg is not None:
                    pl["seam"].use(11)
                    self._bind("seamMap", 11)
                    self.prog["sgBlack"].value = pl["black"]
                    if "uDScale" in self.prog:
                        self.prog["uDScale"].value = 1.0 if self.full_range else (219 if i == 0 else 224) / 255
            pl["fbo"].use()
            self.ctx.viewport = (0, 0, *pl["size"])
            self.vao.render()
            pl["fbo"].read_into(pbo, components=pl["comps"], alignment=1, dtype=dtype, write_offset=offset)
            offset += self.y_bytes
        if self.raw is not None:
            self.raw.flush()                 # the GPU starts on this frame while the CPU collects the one before

    def push(self, front, back, dst: bytearray) -> bool:
        """Pipelined: start frame N on the GPU, then copy frame N-1 into dst.
        Returns False for the very first call (nothing finished yet)."""
        with self.ctx:
            got = self.n > 0
            if self._sg_pool is None:
                # Draw first (with the seam measurement, in this thread): the GPU works on frame N
                # while N-1 is copied out.
                self._render_now(front, back, self.pbos[self.n % 2])
                if got:
                    self._collect(self.pbos[(self.n - 1) % 2], dst)
            else:
                # FS_SEAM_THREAD=1 (experiment): the measurement of frame N starts first, in the helper
                # thread, and runs while this thread waits for the GPU and copies frame N-1 out; frame
                # N is uploaded and drawn after that, when its tables are there.
                tables = self._seam_start(front, back)
                if got:
                    self._collect(self.pbos[(self.n - 1) % 2], dst)
                self._render_now(front, back, self.pbos[self.n % 2], tables)
            self.n += 1
        return got

    def flush(self, dst: bytearray) -> bool:
        """Copy out the last frame still on the GPU."""
        if self.n == 0:
            return False
        with self.ctx:
            self._collect(self.pbos[(self.n - 1) % 2], dst)
        self.n = 0
        return True

    def stitch(self, front, back) -> bytearray:
        """Synchronous convenience wrapper: one frame in, one frame out."""
        with self.ctx:
            self._render_now(front, back, self.pbos[0])
            self._collect(self.pbos[0], self.out)
        return self.out

    def release(self):
        if self._sg_pool is not None:
            self._sg_pool.shutdown(wait=False)
            self._sg_pool = None
        self.ctx.release()
