"""Picture enhancement on the source planes, before stitching: denoise + sharpen.

Denoise: 5x5 edge-preserving bilateral filter (spatial sigma 1.5 px, range
sigma grows with the strength), applied to luma and to chroma.
Sharpen: unsharp mask on luma: Y + amount * 1.5 * (Y - gaussian3x3(Y)).
Working on the fisheye source (not the stitched output) keeps it cheap and
identical in the preview, the GPU export and the CPU stills.
"""
from __future__ import annotations

import numpy as np

GLSL = """
#version 330
uniform sampler2D src;
uniform float uDenoise, uSharp;
uniform int uLuma;             // 1 = luma plane (denoise + sharpen), 0 = chroma (denoise only)
out vec4 color;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    ivec2 mx = textureSize(src, 0) - 1;
    vec2 c = texelFetch(src, p, 0).rg;
    vec2 d = c;
    if (uDenoise > 0.0) {
        float sr = 0.01 + 0.07 * uDenoise;
        vec2 acc = vec2(0.0); vec2 ws = vec2(0.0);
        for (int j = -2; j <= 2; j++)
            for (int i = -2; i <= 2; i++) {
                vec2 q = texelFetch(src, clamp(p + ivec2(i, j), ivec2(0), mx), 0).rg;
                vec2 w = exp(-float(i * i + j * j) / 4.5 - (q - c) * (q - c) / (2.0 * sr * sr));
                acc += q * w; ws += w;
            }
        d = acc / ws;
    }
    if (uLuma == 1 && uSharp > 0.0) {
        float b = 0.0;
        for (int j = -1; j <= 1; j++)
            for (int i = -1; i <= 1; i++) {
                float w = (i == 0 ? 2.0 : 1.0) * (j == 0 ? 2.0 : 1.0);
                b += texelFetch(src, clamp(p + ivec2(i, j), ivec2(0), mx), 0).r * w;
            }
        b /= 16.0;
        d.r = d.r + uSharp * 1.5 * (c.r - b);
    }
    color = vec4(clamp(d, 0.0, 1.0), 0.0, 1.0);
}
"""


def active(g: dict | None) -> bool:
    g = g or {}
    return float(g.get("sharpen", 0) or 0) > 1e-4 or float(g.get("denoise", 0) or 0) > 1e-4


def plane(a: np.ndarray, denoise: float, sharpen: float, luma: bool) -> np.ndarray:
    """Same filter as GLSL on one uint8 plane (CPU path: stills and CPU export)."""
    c = a.astype(np.float32) * np.float32(1 / 255)
    h, w = c.shape
    pad = np.pad(c, 2, mode="edge")                      # edge replication = GLSL clamp
    sh = lambda i, j: pad[2 + j:2 + j + h, 2 + i:2 + i + w]
    d = c
    if denoise > 0:
        sr = 0.01 + 0.07 * denoise
        inv = np.float32(-1 / (2 * sr * sr))
        acc = np.zeros_like(c)
        ws = np.zeros_like(c)
        for j in range(-2, 3):
            for i in range(-2, 3):
                q = sh(i, j)
                diff = q - c
                wgt = np.exp(diff * diff * inv + np.float32(-(i * i + j * j) / 4.5))
                acc += q * wgt
                ws += wgt
        d = acc / ws
    if luma and sharpen > 0:
        b_ = (sh(-1, -1) + sh(1, -1) + sh(-1, 1) + sh(1, 1) + 2 * (sh(0, -1) + sh(0, 1) + sh(-1, 0) + sh(1, 0))
              + 4 * c) * np.float32(1 / 16)
        d = d + np.float32(sharpen * 1.5) * (c - b_)
    return np.clip(d * 255 + 0.5, 0, 255).astype(np.uint8)


def planes(yuv: tuple, g: dict | None) -> tuple:
    if not active(g):
        return yuv
    dn, sh = float(g.get("denoise", 0) or 0), float(g.get("sharpen", 0) or 0)
    y, u, v = yuv
    return plane(y, dn, sh, True), plane(u, dn, 0, False), plane(v, dn, 0, False)


def bgr(img: np.ndarray, g: dict | None) -> np.ndarray:
    """Photos: run the filter in YUV so it matches the video path."""
    if not active(g):
        return img
    import cv2
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV)
    dn, sh = float(g.get("denoise", 0) or 0), float(g.get("sharpen", 0) or 0)
    out = np.dstack([plane(yuv[..., 0], dn, sh, True), plane(yuv[..., 1], dn, 0, False), plane(yuv[..., 2], dn, 0, False)])
    return cv2.cvtColor(out, cv2.COLOR_YUV2BGR)


class GLEnhancer:
    """Runs the pre-pass on moderngl textures (export context or the preview's Qt context)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader="""
#version 330
in vec2 pos;
void main() { gl_Position = vec4(pos, 0.0, 1.0); }
""", fragment_shader=GLSL)
        vbo = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], "f4").tobytes())
        self.vao = ctx.vertex_array(self.prog, [(vbo, "2f", "pos")])
        self.targets = {}

    def run(self, tex, key, comps, g, luma):
        """Filter `tex` into a cached texture of the same size; returns that texture."""
        import moderngl
        size = tex.size
        t = self.targets.get(key)
        if t is None or t[0].size != size:
            out = self.ctx.texture(size, comps, dtype="f1", alignment=1)
            out.filter = tex.filter
            fbo = self.ctx.framebuffer(color_attachments=[out])
            t = self.targets[key] = (out, fbo)
        out, fbo = t
        tex.use(0)
        self.prog["src"].value = 0
        self.prog["uDenoise"].value = float(g.get("denoise", 0) or 0)
        self.prog["uSharp"].value = float(g.get("sharpen", 0) or 0) if luma else 0.0
        self.prog["uLuma"].value = 1 if luma else 0
        prev = self.ctx.fbo
        fbo.use()
        self.ctx.viewport = (0, 0, size[0], size[1])
        self.vao.render()
        if prev is not None:
            prev.use()
        return out
