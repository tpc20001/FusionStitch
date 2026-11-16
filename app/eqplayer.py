"""360 player for finished videos: plays an equirectangular file directly
(no stitching) as an interactive 360 view, a little planet or the flat panorama.

Frames are decoded by NVDEC (media.FrameReader, NV12, GPU-scaled) and drawn by a
small GLSL shader. Sound comes from the file itself through QMediaPlayer and is
used as the clock, so picture and sound stay together.
"""
from __future__ import annotations

import math
import os
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

import geometry as geo
import media

MODES = [("🌐 360 環景", 0), ("🪐 小星球", 2), ("▭ 平面全景", 1)]

VERT = """
#version 330
in vec2 pos; out vec2 vUV;
void main() { vUV = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }
"""

FRAG = """
#version 330
in vec2 vUV; out vec4 outColor;
uniform sampler2D tY, tUV;
uniform int uMode, uFull;
uniform mat3 uView;
uniform float uTanHalf, uAspect, uPlanetK;
const float PI = 3.14159265359;

vec3 rgbAt(vec2 tc) {
    float y = texture(tY, tc).r;
    vec2 c = texture(tUV, tc).rg - 0.5;
    if (uFull == 0) { y = (y - 16.0 / 255.0) * 255.0 / 219.0; c *= 255.0 / 224.0; }
    return vec3(y + 1.5748 * c.y, y - 0.1873 * c.x - 0.4681 * c.y, y + 1.8556 * c.x);
}

void main() {
    vec2 n = vUV * 2.0 - 1.0;
    if (uMode == 1) {                       // flat panorama, letterboxed to 2:1
        vec2 f = uAspect >= 2.0 ? vec2(n.x * uAspect / 2.0, n.y) : vec2(n.x, n.y * 2.0 / uAspect);
        if (abs(f.x) > 1.0 || abs(f.y) > 1.0) { outColor = vec4(0.0, 0.0, 0.0, 1.0); return; }
        outColor = vec4(clamp(rgbAt(vec2(f.x * 0.5 + 0.5, 0.5 - f.y * 0.5)), 0.0, 1.0), 1.0);
        return;
    }
    vec3 d;
    if (uMode == 2) {                       // little planet (stereographic, looking down)
        vec2 p = vec2(n.x * uAspect, n.y) * uPlanetK;
        float rho = max(length(p), 1e-6), c = 2.0 * atan(rho * 0.5);
        d = uView * vec3(sin(c) * p.x / rho, -cos(c), sin(c) * p.y / rho);
    } else {
        d = uView * normalize(vec3(n.x * uTanHalf * uAspect, n.y * uTanHalf, 1.0));
    }
    float lon = atan(d.x, d.z), lat = asin(clamp(d.y, -1.0, 1.0));
    vec2 tc = vec2(0.5 + lon / (2.0 * PI), 0.5 - lat / PI);
    outColor = vec4(clamp(rgbAt(tc), 0.0, 1.0), 1.0);
}
"""


class EquirectView(QOpenGLWidget):
    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        super().__init__(parent)
        self.setFormat(fmt)
        self.ctx = None
        self.mode = 0
        self.full_range = False
        self.view = {"yaw": 0.0, "pitch": 0.0, "fov": 100.0}
        self.planet = {"yaw": 0.0, "pitch": 0.0, "fov": 270.0}
        self._pending = None
        self._size = None
        self._tex = None
        self._drag = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 200)

    def set_frame(self, buf, w, h):
        self._pending = (buf, w, h)
        self.update()

    def initializeGL(self):
        import moderngl
        self.ctx = moderngl.create_context()
        self.prog = self.ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        vbo = self.ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], "f4").tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(vbo, "2f", "pos")])

    def _ensure_tex(self, w, h):
        import moderngl
        if self._size == (w, h):
            return
        for t in self._tex or ():
            t.release()
        self._tex = [self.ctx.texture((w, h), 1, dtype="f1", alignment=1),
                     self.ctx.texture((w // 2, h // 2), 2, dtype="f1", alignment=1)]
        for t in self._tex:
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = True                  # longitude wraps around: no seam at the back
            t.repeat_y = False
        self._size = (w, h)

    def paintGL(self):
        if self.ctx is None:
            return
        if self._pending:
            buf, w, h = self._pending
            self._pending = None
            self._ensure_tex(w, h)
            ys = w * h
            self._tex[0].write(bytes(buf[:ys]))
            self._tex[1].write(bytes(buf[ys:ys * 3 // 2]))
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()
        dpr = self.devicePixelRatioF()
        W, H = int(self.width() * dpr), int(self.height() * dpr)
        self.ctx.viewport = (0, 0, W, H)
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        if not self._tex:
            return
        p = self.prog
        self._tex[0].use(0)
        self._tex[1].use(1)

        def setu(name, v):
            if name in p:
                p[name].value = v
        setu("tY", 0)
        setu("tUV", 1)
        setu("uMode", self.mode)
        setu("uFull", 1 if self.full_range else 0)
        setu("uAspect", W / max(1, H))
        if self.mode == 2:
            V = geo.rot(self.planet["yaw"], self.planet["pitch"], 0)
            setu("uPlanetK", 2 * math.tan(math.radians(self.planet["fov"]) / 4))
        else:
            V = geo.rot(self.view["yaw"], -self.view["pitch"], 0)
        setu("uTanHalf", math.tan(math.radians(self.view["fov"]) / 2))
        setu("uView", tuple(np.asarray(V, dtype=float).T.flatten()))
        self.vao.render()

    # ---- mouse: drag to look around, wheel to zoom, double-click to reset
    def mousePressEvent(self, e):
        self._drag = (e.position(), dict(self.view), dict(self.planet))
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if not self._drag or self.mode == 1:
            return
        p0, v0, pl0 = self._drag
        dx, dy = e.position().x() - p0.x(), e.position().y() - p0.y()
        if self.mode == 2:
            k = 180.0 / max(1, self.height())
            self.planet["yaw"] = (pl0["yaw"] - dx * k + 180) % 360 - 180
            self.planet["pitch"] = (pl0["pitch"] - dy * k + 180) % 360 - 180
        else:
            k = self.view["fov"] / max(1, self.height())
            self.view["yaw"] = v0["yaw"] - dx * k
            self.view["pitch"] = max(-90, min(90, v0["pitch"] + dy * k))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e):
        self.reset_view()

    def wheelEvent(self, e):
        f = math.exp(-e.angleDelta().y() / 1200)
        if self.mode == 2:
            self.planet["fov"] = max(90, min(340, self.planet["fov"] * f))
        else:
            self.view["fov"] = max(20, min(150, self.view["fov"] * f))
        self.update()

    def reset_view(self):
        if self.mode == 2:
            self.planet.update({"yaw": 0.0, "pitch": 0.0, "fov": 270.0})
        else:
            self.view.update({"yaw": 0.0, "pitch": 0.0, "fov": 100.0})
        self.update()


class _Bus(QObject):
    frame = Signal(object, int)          # nv12 bytes, frame index
    error = Signal(str)


class PlayerDialog(QDialog):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"360 播放 — {os.path.basename(path)}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1280, 760)
        self.tl = media.timeline([path])
        w = min(self.tl.width, 3840) // 2 * 2
        h = max(2, int(round(w * self.tl.height / self.tl.width)) // 2 * 2)
        self.scale = (w, h)
        self.frame = 0
        self.playing = False
        self._reader = None
        self._seek_req = None
        self._seek_busy = False
        self._audio = None
        self._bus = _Bus(self)
        self._bus.frame.connect(self._show)
        self._bus.error.connect(lambda m: self.msg.setText("⚠ " + m[-200:]))

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.view = EquirectView(self)
        self.view.full_range = (self.tl.color_range or "") in ("pc", "full", "jpeg")
        v.addWidget(self.view, 1)
        self.bar = QWidget(self)
        b = QHBoxLayout(self.bar)
        b.setContentsMargins(10, 6, 10, 8)
        self.btn = QPushButton("▶")
        self.btn.setFixedWidth(44)
        self.btn.clicked.connect(self.toggle)
        b.addWidget(self.btn)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, self.tl.total_frames - 1))
        self.slider.sliderReleased.connect(lambda: self.seek(self.slider.value()))
        self.slider.valueChanged.connect(lambda v: None if self.slider.isSliderDown() else self.seek(v))
        self.slider.sliderMoved.connect(self._scrub)
        b.addWidget(self.slider, 1)
        self.time = QLabel("")
        self.time.setMinimumWidth(110)
        b.addWidget(self.time)
        self.mode = QComboBox()
        for name, m in MODES:
            self.mode.addItem(name, m)
        self.mode.currentIndexChanged.connect(self._set_mode)
        b.addWidget(self.mode)
        r = QPushButton("重設視角")
        r.clicked.connect(self.view.reset_view)
        b.addWidget(r)
        self.mute = QPushButton("🔊")
        self.mute.setFixedWidth(40)
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self._mute)
        b.addWidget(self.mute)
        fs = QPushButton("⛶")
        fs.setFixedWidth(40)
        fs.setToolTip("全螢幕（F）")
        fs.clicked.connect(self.toggle_full)
        b.addWidget(fs)
        v.addWidget(self.bar)
        self.msg = QLabel("拖曳改變視角・滾輪縮放・雙擊回正面・空白鍵播放／暫停・← → 跳 5 秒・F 全螢幕")
        self.msg.setObjectName("sub")
        self.msg.setContentsMargins(12, 0, 12, 6)
        v.addWidget(self.msg)

        for key, fn in (("Space", self.toggle), ("F", self.toggle_full), ("Esc", self._esc),
                        ("Left", lambda: self.seek(self.frame - int(5 * self.tl.fps))),
                        ("Right", lambda: self.seek(self.frame + int(5 * self.tl.fps))),
                        ("1", lambda: self.mode.setCurrentIndex(0)), ("2", lambda: self.mode.setCurrentIndex(1)),
                        ("3", lambda: self.mode.setCurrentIndex(2))):
            QShortcut(QKeySequence(key), self, activated=fn)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._update_time()
        self.seek(0)
        QTimer.singleShot(300, self.play)

    # ---- helpers
    def _fmt(self, f):
        s = int(f / max(1e-6, self.tl.fps))
        return f"{s // 60}:{s % 60:02d}"

    def _update_time(self):
        self.time.setText(f"{self._fmt(self.frame)} / {self._fmt(self.tl.total_frames)}")
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(self.frame)
            self.slider.blockSignals(False)

    def _show(self, buf, fi):
        if self.playing:
            return                                   # a late still frame from before play started
        self.view.set_frame(buf, *self.scale)
        self.frame = fi
        self._update_time()

    def _set_mode(self, _i):
        self.view.mode = self.mode.currentData()
        self.view.update()

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
            self.bar.show()
            self.msg.show()
        else:
            self.showFullScreen()
            self.msg.hide()

    def _esc(self):
        if self.isFullScreen():
            self.toggle_full()
        else:
            self.close()

    # ---- audio (the file's own track; also the playback clock)
    def _audio_player(self):
        if self._audio is None and self.tl.audio:
            try:
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
                self._audio = QMediaPlayer(self)
                self._audio_out = QAudioOutput(self)
                self._audio.setAudioOutput(self._audio_out)
                self._audio.setSource(QUrl.fromLocalFile(self.path))
            except Exception:
                self._audio = None
        return self._audio

    def _mute(self, on):
        self.mute.setText("🔇" if on else "🔊")
        a = self._audio_player()
        if a is not None:
            self._audio_out.setMuted(on)

    # ---- seeking
    def _scrub(self, f):
        self.time.setText(f"{self._fmt(f)} / {self._fmt(self.tl.total_frames)}")

    def seek(self, f):
        was = self.playing
        self.pause()
        self.frame = max(0, min(self.tl.total_frames - 1, int(f)))
        self._update_time()
        if was:
            self.play()
            return
        self._seek_req = self.frame
        if not self._seek_busy:
            threading.Thread(target=self._seek_worker, daemon=True).start()

    def _seek_worker(self):
        self._seek_busy = True
        try:
            while self._seek_req is not None:
                fi = self._seek_req
                self._seek_req = None
                try:
                    buf = media.read_frame(self.tl, fi, raw=True, scale=self.scale)
                    if self._seek_req is None:
                        self._bus.frame.emit(buf, fi)
                except Exception as e:
                    self._bus.error.emit(str(e))
        finally:
            self._seek_busy = False

    # ---- playback
    def play(self):
        if self.playing:
            return
        if self.frame >= self.tl.total_frames - 1:
            self.frame = 0
        self._reader = media.FrameReader(self.tl, self.frame, raw=True, scale=self.scale, qsize=8)
        self._reader.start()
        self._f0 = self.frame
        self._t0 = time.perf_counter()
        self._shown = self.frame - 1
        self.playing = True
        a = self._audio_player()
        if a is not None:
            a.setPosition(int(self.frame / self.tl.fps * 1000))
            a.play()
        self._timer.start(max(4, int(500 / self.tl.fps)))
        self.btn.setText("⏸")

    def pause(self):
        if not self.playing:
            return
        self.playing = False
        self._timer.stop()
        if self._reader:
            self._reader.close()
            self._reader = None
        if self._audio is not None:
            self._audio.pause()
        self.btn.setText("▶")

    def toggle(self):
        self.pause() if self.playing else self.play()

    def _clock(self) -> int:
        wall = self._f0 + int((time.perf_counter() - self._t0) * self.tl.fps)
        a = self._audio
        if a is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            if a.playbackState() == QMediaPlayer.PlayingState and a.position() > 0:
                af = int(a.position() / 1000 * self.tl.fps)
                if abs(af - wall) < self.tl.fps:     # sound is the clock once it is really running
                    self._f0, self._t0 = af, time.perf_counter()
                    return af
        return wall

    def _tick(self):
        r = self._reader
        if not r:
            return
        target = self._clock()
        latest = None
        while self._shown < target:
            try:
                buf = r.q.get_nowait()
            except Exception:
                break
            if buf is None:
                if r.error:
                    self.msg.setText("⚠ " + r.error[-200:])
                self.pause()
                self.frame = self.tl.total_frames - 1
                self._update_time()
                return
            self._shown += 1
            latest = buf
        if latest is not None:
            self.frame = self._shown
            self.view.set_frame(latest, *self.scale)
            self._update_time()

    def closeEvent(self, e):
        self.pause()
        try:
            if self._audio is not None:
                self._audio.stop()
                self._audio.setSource(QUrl())
        except Exception:
            pass
        super().closeEvent(e)


def open_player(path: str, parent=None):
    dlg = PlayerDialog(path, parent)
    dlg.show()
    return dlg
