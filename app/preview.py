"""Native real-time stitched preview: QOpenGLWidget + NVDEC-decoded NV12 frames.

The fragment shader is the same lens model as geometry.py (and the exporter),
so the preview shows exactly what will be written.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import color
import enhance
import geometry as geo
import media

VERT = """
#version 330
in vec2 pos; out vec2 vUV;
void main() { vUV = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }
"""

_FRAG_SRC = """
#version 330
in vec2 vUV; out vec4 outColor;
uniform sampler2D yF, uvF, yB, uvB;
uniform int uMode; uniform int uSeam; uniform int uFull;
uniform mat3 uView, uGT, uRfT, uRbT;
uniform float uTanHalf, uAspect, uHalf, uBlend, uPlanetK;
uniform float uK3;              // lens model radial term (geometry.project)
float lensR(float th) { float u = th / uHalf; return u * (1.0 + uK3 * (1.0 - u * u)); }   // radius / R
uniform float uFrameAspect, uFlatK;    // reframe (mode 4): output aspect, tan(hfov/2)
uniform vec4 uLf, uLb;          // cx, cy, r (normalised), H/W
uniform vec2 uGf, uGb;          // 2^ev, vignette
uniform vec3 uCfA, uCfB, uCbA, uCbB;   // per-lens colour match
uniform vec3 uOmega;            // rolling shutter: angular velocity (camera frame, rad/s)
uniform float uRS;              // rolling shutter: readout time (s), 0 = off
uniform vec2 uSize;             // source size in pixels
uniform float uCompare;         // split position 0..1 (left = before grading), <0 = off
uniform int uPatchMode;         // nadir patch: 0 off, 1 colour, 2 image
uniform vec3 uPatchColor;
uniform vec3 uPatch;            // half size (rad), rotation (rad), feather (rad)
uniform sampler2D uLogo;
const float PI = 3.14159265359;

//COLOR_GLSL////SEAM_GLSL//
vec3 rgbAt(sampler2D ty, sampler2D tuv, vec2 tc) {
    float y = texture(ty, tc).r;
    vec2 c = texture(tuv, tc).rg - 0.5;
    if (uFull == 0) { y = (y - 16.0 / 255.0) * 255.0 / 219.0; c *= 255.0 / 224.0; }
    return vec3(y + 1.5748 * c.y, y - 0.1873 * c.x - 0.4681 * c.y, y + 1.8556 * c.x);
}

vec3 dirFor(vec2 uv) {
    vec2 n = uv * 2.0 - 1.0;
    if (uMode == 1) {
        float lon = n.x * PI, lat = n.y * PI * 0.5;
        return vec3(cos(lat) * sin(lon), sin(lat), cos(lat) * cos(lon));
    }
    if (uMode == 2) {               // little planet, same as geometry.planet_dirs
        vec2 p = vec2(n.x * uAspect, n.y) * uPlanetK;
        float rho = max(length(p), 1e-6), c = 2.0 * atan(rho * 0.5);
        return uView * vec3(sin(c) * p.x / rho, -cos(c), sin(c) * p.y / rho);
    }
    if (uMode == 4) {                 // reframe: the output frame fitted inside the canvas
        vec2 f = uAspect >= uFrameAspect ? vec2(n.x * uAspect / uFrameAspect, n.y) / 0.92
                                         : vec2(n.x, n.y * uFrameAspect / uAspect) / 0.92;
        return uView * normalize(vec3(f.x * uFlatK, f.y * uFlatK / uFrameAspect, 1.0));
    }
    return uView * normalize(vec3(n.x * uTanHalf * uAspect, n.y * uTanHalf, 1.0));
}
float frameMask(vec2 uv) {          // 1 inside the reframe output, 0 outside
    vec2 n = uv * 2.0 - 1.0;
    vec2 f = uAspect >= uFrameAspect ? vec2(n.x * uAspect / uFrameAspect, n.y) / 0.92
                                     : vec2(n.x, n.y * uFrameAspect / uAspect) / 0.92;
    return max(abs(f.x), abs(f.y)) <= 1.0 ? 1.0 : 0.0;
}

vec4 lens(vec3 d, mat3 R, float sgn, vec4 L, vec2 G, vec3 CA, vec3 CB, sampler2D ty, sampler2D tuv, out float theta) {
    vec3 dc = seamAdjust(uGT * d, sgn);
    vec3 l = R * dc;
    if (uRS != 0.0) {
        for (int i = 0; i < 2; i++) {
            float th = acos(clamp(l.z, -1.0, 1.0));
            float row = L.y - L.z * lensR(th) * sin(atan(l.y, l.x));
            l = R * normalize(dc - cross(uOmega, dc) * ((row - 0.5) * uRS));
        }
    }
    theta = acos(clamp(l.z, -1.0, 1.0));
    float phi = atan(l.y, l.x);
    float rn = L.z * lensR(theta);
    vec2 tc = vec2(L.x + rn * L.w * cos(phi), L.y - rn * sin(phi));
    vec2 m = 0.5 / uSize;
    bool ok = theta <= uHalf && tc.x >= m.x && tc.y >= m.y && tc.x <= 1.0 - m.x && tc.y <= 1.0 - m.y;
    float g = G.x * (1.0 + G.y * pow(theta / uHalf, 2.0));
    return vec4(lensTone(rgbAt(ty, tuv, clamp(tc, m, 1.0 - m)) * CA + CB, sgn < 0.0 ? 0.25 : 0.75) * g, ok ? 1.0 : 0.0);
}

vec3 rawView(vec2 uv) {
    bool back = uv.x >= 0.5;
    vec2 q = vec2(back ? uv.x * 2.0 - 1.0 : uv.x * 2.0, 1.0 - uv.y);
    vec4 L = back ? uLb : uLf;
    float regionAspect = uAspect * 0.5, srcAspect = uSize.x / uSize.y;
    vec2 tc;
    if (regionAspect > srcAspect) tc = vec2((q.x - 0.5) * regionAspect / srcAspect + 0.5, q.y);
    else tc = vec2(q.x, (q.y - 0.5) * srcAspect / regionAspect + 0.5);
    if (any(lessThan(tc, vec2(0.0))) || any(greaterThan(tc, vec2(1.0)))) return vec3(0.06);
    vec3 c = back ? rgbAt(yB, uvB, tc) : rgbAt(yF, uvF, tc);
    vec2 px = tc * uSize, ctr = L.xy * uSize;
    float dist = length(px - ctr), rpx = L.z * uSize.y;
    float lw = 1.5 * max(fwidth(px.x), fwidth(px.y));
    if (abs(dist - rpx) < lw * 1.5) c = vec3(1.0, 0.2, 0.3);
    if (abs(dist - rpx * lensR(PI * 0.5)) < lw) c = mix(c, vec3(1.0, 0.85, 0.2), 0.8);
    if ((abs(px.x - ctr.x) < lw && abs(px.y - ctr.y) < rpx * 0.05) ||
        (abs(px.y - ctr.y) < lw && abs(px.x - ctr.x) < rpx * 0.05)) c = vec3(0.2, 1.0, 0.5);
    return c;
}

void main() {
    if (uMode == 3) { outColor = vec4(rawView(vUV), 1.0); return; }
    vec3 d = dirFor(vUV);
    float tf, tb;
    vec4 F = lens(d, uRfT, -0.5, uLf, uGf, uCfA, uCfB, yF, uvF, tf);
    vec4 B = lens(d, uRbT, 0.5, uLb, uGb, uCbA, uCbB, yB, uvB, tb);
    float psi = 0.0, se = 0.0;              // 接縫融合, same as the exporter (gpu.py)
    if (uSgOn != 0 || uTbOn != 0) {
        vec3 lf = uRfT * (uGT * d);
        psi = atan(lf.y, lf.x) / (2.0 * PI);
        if (psi < 0.0) psi += 1.0;
        se = asin(clamp(lf.z, -1.0, 1.0));
    }
    if (uSgOn != 0) {
        vec3 ev = seamGainEV(psi, se).rgb;
        F.rgb *= exp2(-ev);
        B.rgb *= exp2(ev);
    }
    float t = clamp(0.5 + (tb - tf) / (2.0 * uBlend), 0.0, 1.0);
    float w = t * t * (3.0 - 2.0 * t);
    if (F.a > 0.5 && B.a < 0.5) w = 1.0;
    if (F.a < 0.5 && B.a > 0.5) w = 0.0;
    vec3 c = mix(B.rgb, F.rgb, w);
    if (uTbOn != 0 && abs(se) < 0.5 * uTbWide && F.a > 0.5 && B.a > 0.5) c += seamD(psi, se) * (seamWideW(se) - w);
    c = clamp(c, 0.0, 1.0);
    if (F.a < 0.5 && B.a < 0.5) c = vec3(0.0);
    bool before = uCompare >= 0.0 && vUV.x < uCompare;
    if (!before) c = grade(c);
    if (uPatchMode > 0) {             // same as geometry.patch_map
        float a = acos(clamp(-d.y, -1.0, 1.0));
        float r = a / uPatch.x, ph = atan(d.z, d.x) + uPatch.y;
        float al = clamp((uPatch.x - a) / uPatch.z, 0.0, 1.0);
        if (al > 0.0) {
            vec4 lc = uPatchMode == 2 ? texture(uLogo, vec2(0.5 + 0.5 * r * cos(ph), 0.5 - 0.5 * r * sin(ph)))
                                      : vec4(uPatchColor, 1.0);
            c = mix(c, lc.rgb, al * lc.a);
        }
    }
    if (uCompare >= 0.0 && abs(vUV.x - uCompare) < 1.5 / (uAspect * 600.0)) c = vec3(1.0);
    if (uMode == 4 && frameMask(vUV) < 0.5) c *= 0.35;   // context outside the output frame, dimmed
    if (uSeam == 1) {
        if (w > 0.002 && w < 0.998) c = mix(c, vec3(1.0, 0.0, 0.8), 0.25);
        if (abs(w - 0.5) < 0.03) c = vec3(1.0, 0.0, 0.8);
    }
    outColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""


from gpu import SEAM_GLSL, SEAMGAIN_GLSL
FRAG = _FRAG_SRC.replace("//COLOR_GLSL//", color.GLSL).replace("//SEAM_GLSL//", SEAM_GLSL + SEAMGAIN_GLSL)



class StitchView(QOpenGLWidget):
    """mode: 0 = 360 環景, 1 = 平面全景, 2 = 小星球, 3 = 原始雙魚眼"""
    viewChanged = Signal()
    histogramReady = Signal(object)
    compareMoved = Signal(float)

    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        super().__init__(parent)
        self.setFormat(fmt)
        self.ctx = None
        self.params = None
        self.mode = 0
        self.seam = False
        self.full_range = True
        self.view = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 100.0}
        self.planet = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 270.0}
        self.flat = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 90.0}
        self.frame_aspect = 16 / 9
        self._pending = None
        self._size = None
        self._tex = None
        self._drag = None
        self.message = "選擇素材開始預覽"
        self.grade = color.default_grade()
        self.stab = None                 # per-frame gyro rotation (3x3) or None
        self.rs = None                   # rolling shutter (omega xyz rad/s, readout s) or None
        self.lens_color = None           # per-frame (front6, back6) from the colour track, or None
        self.lens_tone = None            # per-frame (2, 256) gain by brightness per lens (原廠鏡頭曝光), or None
        self.expo_add = 0.0              # per-frame extra exposure (EV) from 自動亮度
        self.patch = {"mode": "off"}
        self.compare = -1.0
        self._lut_path = None
        self._logo_path = None
        self._lut = self._logo = None
        self._hist_t = 0.0
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 200)

    # ---- data in
    def set_frames(self, fbuf, bbuf, w, h):
        self._pending = (bytes(fbuf), bytes(bbuf), w, h)
        self.message = ""
        self.update()

    def set_params(self, params):
        self.params = params
        self.update()

    def set_grade(self, g):
        g = color.merge(g)
        if color.is_identity(g):          # 原廠色彩 mode: show what the conversion will apply
            import core
            lut = core.gopro_lut_path() if core.cfg("color_mode") == "gopro" else ""
            if lut:
                g["lut"], g["lut_strength"] = lut, max(0.0, min(1.0, float(core.cfg("gopro_lut_strength") or 1.0)))
                g["denoise"] = max(0.0, min(1.0, float(core.cfg("gopro_denoise") or 0.0)))
                g["sharpen"] = max(0.0, min(1.0, float(core.cfg("gopro_sharpen", 0.7) or 0.0)))
        self.grade = g
        self.update()

    def set_patch(self, p):
        self.patch = dict(p or {"mode": "off"})
        self.update()

    # ---- GL
    def initializeGL(self):
        import moderngl
        self.ctx = moderngl.create_context()
        self.prog = self.ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        vbo = self.ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], "f4").tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(vbo, "2f", "pos")])
        import moderngl
        self._hist_fbo = self.ctx.simple_framebuffer((320, 160), components=4)

    def _ensure_tex(self, w, h):
        import moderngl
        if self._size == (w, h):
            return
        if self._tex:
            for t in self._tex:
                t.release()
        mk = lambda size, comps: self.ctx.texture(size, comps, dtype="f1", alignment=1)
        self._tex = [mk((w, h), 1), mk((w // 2, h // 2), 2), mk((w, h), 1), mk((w // 2, h // 2), 2)]
        for t in self._tex:
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = False
        self._size = (w, h)

    def paintGL(self):
        if self.ctx is None:
            return
        if self._pending:
            fb, bb, w, h = self._pending
            self._pending = None
            self._ensure_tex(w, h)
            ys = w * h
            self._tex[0].write(fb[:ys]); self._tex[1].write(fb[ys:ys * 3 // 2])
            self._tex[2].write(bb[:ys]); self._tex[3].write(bb[ys:ys * 3 // 2])
            self._seamgain_frame(fb, bb, w, h)
        texs = self._tex
        if texs and enhance.active(self.grade):          # denoise / sharpen pre-pass (same as export)
            import moderngl
            if getattr(self, "_enh", None) is None:
                self._enh = enhance.GLEnhancer(self.ctx)
            g = self.grade
            texs = [self._enh.run(self._tex[0], "fy", 1, g, True), self._enh.run(self._tex[1], "fuv", 2, g, False),
                    self._enh.run(self._tex[2], "by", 1, g, True), self._enh.run(self._tex[3], "buv", 2, g, False)]
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()
        dpr = self.devicePixelRatioF()
        W, H = int(self.width() * dpr), int(self.height() * dpr)
        self.ctx.viewport = (0, 0, W, H)
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        if not self._tex or not self.params:
            return
        P, p = self.params, self.prog
        for i, name in enumerate(("yF", "uvF", "yB", "uvB")):
            texs[i].use(i)
            if name in p:
                p[name].value = i
        def setu(name, v):
            if name in p:
                p[name].value = v
        setu("uMode", self.mode)
        setu("uSeam", 1 if self.seam else 0)
        setu("uFull", 1 if self.full_range else 0)
        if self.mode == 2:
            V = geo.rot(self.planet["yaw"], self.planet["pitch"], self.planet["roll"])
            setu("uPlanetK", 2 * math.tan(math.radians(self.planet["fov"]) / 4))
        elif self.mode == 4:
            V = geo.rot(self.flat["yaw"], -self.flat["pitch"], self.flat["roll"])
            setu("uFlatK", math.tan(math.radians(max(5.0, min(170.0, self.flat["fov"]))) / 2))
            setu("uFrameAspect", float(self.frame_aspect))
        else:
            V = geo.rot(self.view["yaw"], -self.view["pitch"], 0)
        setu("uView", tuple(V.T.flatten()))                # GLSL is column-major
        Ps = P if self.stab is None else {**P, "_stab": self.stab}
        # GLSL mat3 is column-major: uploading M.flatten() gives M^T in the shader
        setu("uGT", tuple(geo.global_rotation(Ps).flatten()))
        setu("uRfT", tuple(geo.lens_rotation(Ps, "front").flatten()))
        setu("uRbT", tuple(geo.lens_rotation(Ps, "back").flatten()))
        self._bind_seam(setu)
        self._bind_seamgain(setu)
        self._bind_lens_tone(setu)
        setu("uOmega", tuple(float(x) for x in (self.rs[0] if self.rs else (0.0, 0.0, 0.0))))
        setu("uRS", float(self.rs[1]) if self.rs else 0.0)
        setu("uTanHalf", math.tan(math.radians(self.view["fov"]) / 2))
        setu("uAspect", W / max(1, H))
        setu("uHalf", math.radians(P["fov"]) / 2)
        setu("uK3", float(P.get("k3", 0.0) or 0.0))
        setu("uBlend", max(math.radians(P["blend"]), 1e-4))
        w, h = self._size
        setu("uLf", (P["front"]["cx"], P["front"]["cy"], P["front"]["r"], h / w))
        setu("uLb", (P["back"]["cx"], P["back"]["cy"], P["back"]["r"], h / w))
        setu("uGf", (2 ** P["front"]["ev"], P["front"]["vignette"]))
        setu("uGb", (2 ** P["back"]["ev"], P["back"]["vignette"]))
        for i, (which, (na, nb)) in enumerate((("front", ("uCfA", "uCfB")), ("back", ("uCbA", "uCbB")))):
            c = self.lens_color[i] if self.lens_color else geo.lens_color(P, which)
            setu(na, tuple(c[:3]))
            setu(nb, tuple(c[3:]))
        setu("uSize", (float(w), float(h)))
        self._bind_extras(setu)
        self.vao.render()
        if time.time() - self._hist_t > 0.35 and self.mode != 3:
            self._hist_t = time.time()
            cmp = self.compare
            setu("uCompare", -1.0)
            setu("uAspect", 2.0)
            self._hist_fbo.use()
            self.ctx.viewport = (0, 0, 320, 160)
            self.vao.render()
            img = np.frombuffer(self._hist_fbo.read(components=4), np.uint8).reshape(-1, 4)[:, :3]
            self.histogramReady.emit(img.copy())
            fbo.use()
            setu("uCompare", cmp)

    def _seamgain_frame(self, fb, bb, w, h):
        """接縫融合 for the preview: the same measurement as the exporter (geometry.SeamBlend),
        from the frame that was just uploaded."""
        import json as _json
        import moderngl
        P = self.params
        if not P:
            self._sg_on = False
            return
        key = _json.dumps([w, h, P["fov"], P.get("k3"), P["front"], P["back"], P["orient"], P.get("seamgain"),
                           P.get("seamwide")], sort_keys=True, default=str)
        if getattr(self, "_sg_key", None) != key:
            self._sg_key = key
            self._sg = geo.SeamBlend(P, w, h, rgb=True)
            self._sg_t = 0.0
        sb = self._sg
        self._sg_on = sb.on
        if not sb.on:
            return
        ys = w * h
        planes = []
        for buf in (fb, bb):
            a = np.frombuffer(buf, np.uint8, count=ys * 3 // 2)
            planes.append((a[:ys].reshape(h, w), a[ys:].reshape(h // 2, w // 2, 2)))
        cols = self.lens_color if self.lens_color else (geo.lens_color(P, "front"), geo.lens_color(P, "back"))
        F, B = sb.sample(planes[0], planes[1], self.full_range, cols, self.lens_tone)
        now = time.time()                      # playing: smooth over time like the export; a seek starts fresh
        gain, D = sb.update(F, B, 1 if now - self._sg_t < 0.25 else 10 ** 6)
        self._sg_t = now
        if getattr(self, "_sg_tex", None) is None or self._sg_tex.size != (sb.K, 1):
            self._sg_tex = self.ctx.texture((sb.K, 1), 4, dtype="f4")
            self._sgd_tex = self.ctx.texture((sb.K, sb.M), 3, dtype="f4", alignment=1)
            for t in (self._sg_tex, self._sgd_tex):
                t.filter = (moderngl.LINEAR, moderngl.LINEAR)
                t.repeat_x, t.repeat_y = True, False
        self._sg_tex.write(gain.tobytes())
        self._sgd_tex.write(D.tobytes())

    def _bind_lens_tone(self, setu):
        import moderngl
        t = self.lens_tone
        setu("uLtOn", 0 if t is None else 1)
        if t is None:
            return
        if getattr(self, "_lt_tex", None) is None:
            self._lt_tex = self.ctx.texture((256, 2), 1, dtype="f4")
            self._lt_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._lt_tex.repeat_x = self._lt_tex.repeat_y = False
        self._lt_tex.write(np.ascontiguousarray(t, np.float32).tobytes())
        self._lt_tex.use(11)
        setu("uLensTone", 11)

    def _bind_seamgain(self, setu):
        on = bool(getattr(self, "_sg_on", False)) and getattr(self, "_sg_tex", None) is not None
        sb = getattr(self, "_sg", None)
        setu("uSgOn", 1 if on and sb.spread > 0 else 0)
        setu("uTbOn", 1 if on and sb.wide > 0 else 0)
        if on:
            self._sg_tex.use(9)
            setu("uGainTex", 9)
            self._sgd_tex.use(10)
            setu("uSeamD", 10)
            setu("uSgSpread", max(math.radians(sb.spread), 1e-4))
            setu("uTbWide", max(math.radians(sb.wide), 1e-4))
            setu("uTbRow", tuple(float(x) for x in sb.rows()))

    def set_seam(self, sf):
        self.seamflow = sf
        self._seam_dirty = True
        self.update()

    def _bind_seam(self, setu):
        import moderngl
        sf = getattr(self, "seamflow", None)
        if getattr(self, "_seam_dirty", True) or getattr(self, "_seam_tex", None) is None:
            if getattr(self, "_seam_tex", None) is not None:
                self._seam_tex.release()
            if sf is None:
                data, nb, nr = np.zeros(8, "f4"), 2, 2
            else:
                data = np.ascontiguousarray(np.asarray(sf["flow"], np.float32))
                nr, nb = data.shape[:2]
            self._seam_tex = self.ctx.texture((nb, nr), 2, data.tobytes(), dtype="f4")
            self._seam_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._seam_tex.repeat_x, self._seam_tex.repeat_y = True, False
            self._seam_dims = (float(nb), float(nr))
            self._seam_dirty = False
        self._seam_tex.use(7)
        setu("uSeamTex", 7)
        setu("uSeamOn", 1 if sf is not None else 0)
        setu("uSeamBand", math.radians(float((sf or {}).get("band", 6.0))))
        setu("uSeamSize", self._seam_dims)

    def _bind_extras(self, setu):
        import moderngl
        g = self.grade
        for k, v in color.uniforms(g).items():
            if k == "gA" and self.expo_add:
                v = (v[0] + self.expo_add, *v[1:])
            setu(k, v)
        lut = g["lut"] if g["lut"] and g["lut_strength"] > 0 else None
        if lut != self._lut_path or self._lut is None:
            if self._lut:
                self._lut.release()
            try:
                n, data = color.lut_texture_bytes(lut) if lut else (2, np.zeros(24, "f4").tobytes())
            except Exception:
                n, data = 2, np.zeros(24, "f4").tobytes()
            self._lut = self.ctx.texture3d((n, n, n), 3, data, dtype="f4")
            self._lut.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._lut_path = lut
        self._lut.use(5)
        setu("gLut", 5)
        pt = self.patch or {}
        mode = {"off": 0, "color": 1, "image": 2}.get(pt.get("mode", "off"), 0)
        img = pt.get("image") if mode == 2 else None
        if img != self._logo_path or self._logo is None:
            if self._logo:
                self._logo.release()
            try:
                import gpu
                lw, lh, data = gpu.load_logo_rgba(img, 512) if img else (1, 1, bytes([0, 0, 0, 255]))
            except Exception:
                lw, lh, data, mode = 1, 1, bytes([0, 0, 0, 255]), 0
            self._logo = self.ctx.texture((lw, lh), 4, data)
            self._logo.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._logo_path = img
        self._logo.use(6)
        setu("uLogo", 6)
        setu("uPatchMode", mode)
        setu("uPatchColor", tuple(float(c) / 255 for c in pt.get("color", [0, 0, 0])))
        setu("uPatch", (math.radians(float(pt.get("size", 30))) / 2, math.radians(float(pt.get("rotate", 0))),
                        max(1e-4, math.radians(float(pt.get("feather", 1.0))))))
        setu("uCompare", float(self.compare))

    # ---- interaction
    def mousePressEvent(self, e):
        if self.compare >= 0 and abs(e.position().x() / max(1, self.width()) - self.compare) < 0.015:
            self._cmp_drag = True
            return
        self._cmp_drag = False
        self._drag = (e.position(), dict(self.view), dict(self.planet), dict(self.flat))
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if getattr(self, "_cmp_drag", False):
            self.compare = min(0.98, max(0.02, e.position().x() / max(1, self.width())))
            self.update()
            return
        if not self._drag:
            return
        p0, v0, pl0, fl0 = self._drag
        dx, dy = e.position().x() - p0.x(), e.position().y() - p0.y()
        if self.mode == 4:
            k = self.flat["fov"] / max(1, self.width() * 0.92)
            self.flat["yaw"] = fl0["yaw"] - dx * k
            self.flat["pitch"] = max(-90, min(90, fl0["pitch"] + dy * k))
        elif self.mode == 2:
            k = 180.0 / max(1, self.height())
            self.planet["yaw"] = (pl0["yaw"] - dx * k + 180) % 360 - 180
            self.planet["pitch"] = (pl0["pitch"] - dy * k + 180) % 360 - 180   # free: past 90° looks up
        else:
            k = self.view["fov"] / max(1, self.height())
            self.view["yaw"] = v0["yaw"] - dx * k
            self.view["pitch"] = max(-90, min(90, v0["pitch"] + dy * k))
        self.update()
        self.viewChanged.emit()

    def mouseReleaseEvent(self, e):
        self._cmp_drag = False
        self._drag = None
        self.unsetCursor()

    def wheelEvent(self, e):
        f = math.exp(-e.angleDelta().y() / 1200)
        if self.mode == 2:
            self.planet["fov"] = max(90, min(340, self.planet["fov"] * f))
        elif self.mode == 4:
            self.flat["fov"] = max(15, min(150, self.flat["fov"] * f))
        else:
            self.view["fov"] = max(20, min(150, self.view["fov"] * f))
        self.update()
        self.viewChanged.emit()

    def reset_view(self):
        if self.mode == 4:
            self.flat.update({"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 90.0})
        elif self.mode == 2:
            self.planet.update({"yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 270.0})
        else:
            self.view.update({"yaw": 0.0, "pitch": 0.0, "fov": 100.0})
        self.update()
        self.viewChanged.emit()


class Player(QObject):
    """Plays/scrubs a clip pair. Frames are decoded by NVDEC at preview size."""
    frameReady = Signal(object, object, int, int, int)     # front, back, w, h, global frame
    stateChanged = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tf = self.tb = None
        self.frame = 0
        self.playing = False
        self.back_offset = 0
        self.scale = None
        self._readers = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._seek_req = None
        self._seek_busy = False
        self._audio = None
        self._audio_on = True
        self.photo = None

    # ---- setup
    def set_clip(self, clip: dict, quality="half"):
        self.stop()
        self.photo = None
        self.tf, self.tb = media.timeline(clip["front"]), media.timeline(clip["back"])
        w, h = self.tf.width, self.tf.height
        if quality == "half":
            w, h = (w // 2) // 2 * 2, (h // 2) // 2 * 2
        self.scale = (w, h)
        self.frame = 0
        self.seek(0)

    def set_photo(self, front_bgr, back_bgr):
        """Photos: convert once to NV12 and show."""
        import cv2
        self.stop()
        self.tf = self.tb = None
        h, w = front_bgr.shape[:2]
        w2, h2 = w // 2 * 2, h // 2 * 2
        def nv12(img):
            i420 = cv2.cvtColor(img[:h2, :w2], cv2.COLOR_BGR2YUV_I420)
            y = i420[:h2].ravel()
            u = i420[h2:h2 + h2 // 4].reshape(-1)
            v = i420[h2 + h2 // 4:].reshape(-1)
            uv = np.empty(u.size * 2, np.uint8)
            uv[0::2], uv[1::2] = u, v
            return np.concatenate([y, uv]).tobytes()
        self.photo = (nv12(front_bgr), nv12(back_bgr), w2, h2)
        self.frameReady.emit(*self.photo, 0)

    @property
    def total(self):
        return self.tf.total_frames if self.tf else 0

    @property
    def fps(self):
        return self.tf.fps if self.tf else 30.0

    def set_back_offset(self, frames: int):
        self.back_offset = int(frames)
        if not self.playing:
            self.seek(self.frame)

    # ---- scrubbing (coalesced: only the newest request is decoded)
    def seek(self, frame: int):
        if not self.tf:
            return
        was = self.playing
        if was:
            self.pause()
        self.frame = max(0, min(self.total - 1, int(frame)))
        self._seek_req = self.frame
        if not self._seek_busy:
            threading.Thread(target=self._seek_worker, daemon=True).start()
        self.stateChanged.emit()

    def _seek_worker(self):
        self._seek_busy = True
        try:
            while self._seek_req is not None:
                fi = self._seek_req
                self._seek_req = None
                try:
                    fb = media.read_frame(self.tf, fi, raw=True, scale=self.scale)
                    bb = media.read_frame(self.tb, max(0, min(self.tb.total_frames - 1, fi + self.back_offset)),
                                          raw=True, scale=self.scale)
                    if self._seek_req is None:
                        self.frameReady.emit(fb, bb, self.scale[0], self.scale[1], fi)
                except Exception as e:
                    self.error.emit(str(e))
        finally:
            self._seek_busy = False

    # ---- playback
    def play(self):
        if not self.tf or self.playing:
            return
        if self.frame >= self.total - 1:
            self.frame = 0
        self._readers = (media.FrameReader(self.tf, self.frame, raw=True, scale=self.scale, qsize=8),
                         media.FrameReader(self.tb, self.frame + self.back_offset, raw=True, scale=self.scale, qsize=8))
        for r in self._readers:
            r.start()
        self._t0 = time.perf_counter()
        self._f0 = self.frame
        self._shown = self.frame - 1
        self.playing = True
        self._timer.start(max(5, int(500 / self.fps)))
        self._audio_play()
        self.stateChanged.emit()

    def pause(self):
        if not self.playing:
            return
        self.playing = False
        self._timer.stop()
        for r in self._readers or ():
            r.close()
        self._readers = None
        self._audio_pause()
        self.stateChanged.emit()

    def stop(self):
        self.pause()

    def toggle(self):
        self.pause() if self.playing else self.play()

    def _tick(self):
        if not self._readers:
            return
        target = self._f0 + int((time.perf_counter() - self._t0) * self.fps)
        rf, rb = self._readers
        latest = None
        while self._shown < target:
            try:
                fb = rf.q.get_nowait()
            except Exception:
                break
            bb = rb.q.get()
            if fb is None or bb is None:
                self.frame = self.total - 1
                self.pause()
                return
            self._shown += 1
            latest = (fb, bb)
        if latest:
            self.frame = self._shown
            ci = self.tf.locate(self.frame)[0]
            if ci != getattr(self, "_audio_ci", ci):
                self._audio_play()          # next chapter file
            self._audio_ci = ci
            self.frameReady.emit(latest[0], latest[1], self.scale[0], self.scale[1], self.frame)
            self.stateChanged.emit()

    # ---- audio (front lens stereo track)
    def set_audio(self, on: bool):
        self._audio_on = on
        if not on:
            self._audio_pause()
        elif self.playing:
            self._audio_play()

    def _audio_play(self):
        if not self._audio_on or not self.tf or not self.tf.audio:
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if self._audio is None:
                self._audio = QMediaPlayer(self)
                self._audio_out = QAudioOutput(self)
                self._audio.setAudioOutput(self._audio_out)
            ci, local = self.tf.locate(self.frame)
            self._audio_ci = ci
            src = QUrl.fromLocalFile(self.tf.files[ci])
            if self._audio.source() != src:
                self._audio.setSource(src)
            self._audio.setPosition(int(local / self.tf.fps * 1000))
            self._audio.play()
        except Exception:
            pass

    def _audio_pause(self):
        try:
            if self._audio:
                self._audio.pause()
        except Exception:
            pass
