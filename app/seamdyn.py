"""動態接縫光流: per-frame parallax correction at the seam (near objects such as mirrors).

The handlebar mirrors of a scooter sit exactly at the two seams (lenses face front
and back, seams left and right) a few tens of centimetres from the camera, so the
two lenses see them several degrees apart: blending the overlap draws them twice.
They move with the handlebars, so the static 固定支架視差校正 (seamflow.measure,
median over the clip) cannot hold them. Here the same band flow is measured on
every frame (DIS optical flow on the overlap band of both lenses), smoothed over
space and time, and the stitcher shifts each lens half way (same band texture and
shader code as seamflow: geometry.seam_adjust / gpu.SEAM_GLSL).
"""
from __future__ import annotations

import math

import numpy as np

import geometry as geo
import seamflow

MAX_DEG = 6.0          # largest shift we trust (a mirror at ~30 cm is ~4°)


class DynSeam:
    def __init__(self, params: dict, src_w: int, src_h: int, tau_frames: float = 2.0):
        import cv2
        self.cv2 = cv2
        self.nb, self.nr, self.band = seamflow.NB, seamflow.NR, seamflow.BAND_DEG
        dirs = seamflow.band_dirs()
        base = geo.merge_params(params, {"orient": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}})
        base.pop("_stab", None)
        base.pop("_seamflow", None)
        d = dirs.reshape(-1, 3).astype(np.float32)
        sh = dirs.shape[:2]
        fx, fy, _ = geo.project(d, base, "front", src_w, src_h)
        bx, by, _ = geo.project(d, base, "back", src_w, src_h)
        self.mf = cv2.convertMaps(fx.reshape(sh), fy.reshape(sh), cv2.CV_16SC2)
        self.mb = cv2.convertMaps(bx.reshape(sh), by.reshape(sh), cv2.CV_16SC2)
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.tau = tau_frames
        self.flow = None
        self.max_px = MAX_DEG / (360.0 / self.nb)
        self.fade = (np.cos(np.linspace(-math.pi / 2, math.pi / 2, self.nr)) ** 0.5)[:, None, None].astype(np.float32)

    def _norm(self, img):
        """Local contrast normalisation: brightness differences between the lenses must not look like motion."""
        cv2 = self.cv2
        x = img.astype(np.float32)
        m = cv2.GaussianBlur(x, (0, 0), 6)
        s = np.sqrt(cv2.GaussianBlur((x - m) ** 2, (0, 0), 6)) + 4.0
        return np.clip((x - m) / s * 40 + 128, 0, 255).astype(np.uint8)

    def update(self, yf: np.ndarray, yb: np.ndarray, step: int = 1) -> np.ndarray:
        """yf / yb: luma planes (uint8, full source size). Returns (NR, NB, 2) float32 band flow."""
        cv2 = self.cv2
        f = cv2.remap(yf, *self.mf, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        b = cv2.remap(yb, *self.mb, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        fn, bn = self._norm(f), self._norm(b)
        pad = 64
        fp = np.concatenate([fn[:, -pad:], fn, fn[:, :pad]], 1)
        bp = np.concatenate([bn[:, -pad:], bn, bn[:, :pad]], 1)
        fl = self.dis.calc(fp, bp, None)[:, pad:-pad]
        # confidence: texture (after contrast normalisation, so dark night scenes count) in both
        # lenses, and the two must look alike once shifted (occlusions such as the bike body don't)
        def tex(x):
            return cv2.GaussianBlur(cv2.Sobel(x, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(x, cv2.CV_32F, 0, 1) ** 2, (0, 0), 2)
        w = np.clip((np.minimum(tex(fn), tex(bn)) - 150.0) / 600.0, 0, 1).astype(np.float32)
        h, wd = fn.shape
        gx, gy = np.meshgrid(np.arange(wd, dtype=np.float32), np.arange(h, dtype=np.float32))
        warped = cv2.remap(bn, gx + fl[..., 0], gy + fl[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        err = cv2.GaussianBlur(np.abs(fn.astype(np.float32) - warped.astype(np.float32)), (0, 0), 3)
        w *= np.clip(1.5 - err / 30.0, 0, 1)
        mag = np.hypot(fl[..., 0], fl[..., 1])
        w[mag > self.max_px] = 0
        ws = cv2.GaussianBlur(w, (0, 0), 4)
        fs = cv2.GaussianBlur(fl * w[..., None], (0, 0), 4) / np.maximum(ws, 1e-3)[..., None]
        fs *= np.clip(ws / 0.3, 0, 1)[..., None]                     # little texture -> no shift
        fs = (fs * self.fade).astype(np.float32)
        if self.flow is not None:
            a = 1.0 - math.exp(-max(1, step) / self.tau)
            fs = self.flow + a * (fs - self.flow)
        self.flow = fs
        return fs
