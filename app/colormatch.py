"""原廠色彩對標: learn GoPro Fusion Studio's colour rendering from one clip it rendered.

Input: the official render (any resolution, 360 equirect) + the same clip's footage.
1. At ~30 moments, stitch our *ungraded* picture straight from the footage (CPU, 1024x512) and
   grab the official frame at the same time.
2. The two are not pointed the same way (official = front lens in the middle): find the
   horizontal turn (yaw) that lines them up best, the same for the whole clip.
3. Pair blurred pixels (clipped, edge and pole pixels left out) and fit
     a monotone brightness curve  +  a 3x3 colour matrix (+ offset)   (robust, re-weighted)
   = contrast/brightness like GoPro, then their saturation / white balance / hue.
4. Bake that into a 33³ .cube LUT. Conversions in 原廠色彩 mode use it instead of 自動調色.
Every 5th moment is kept aside to measure how close the result gets.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

import core
import ffm as ff

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
W, H = 1024, 512


def lut_dir() -> Path:
    d = core.PACKAGE_DIR / "luts"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- frames
def _ref_info(path: str) -> dict:
    r = ff.run([ff.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=color_space,color_range,width,height:format=duration", "-of", "json", path], timeout=90)
    j = json.loads(r.stdout or b"{}")
    s = (j.get("streams") or [{}])[0]
    return {"matrix": s.get("color_space") or "bt709", "range": s.get("color_range") or "tv",
            "duration": float((j.get("format") or {}).get("duration") or 0)}


def ref_frame(path: str, t: float, info: dict) -> np.ndarray:
    mtx = "bt709" if info["matrix"] in ("bt709", "unknown", "reserved") else (
        "bt601" if info["matrix"] in ("smpte170m", "bt470bg") else "bt709")
    rng = "full" if info["range"] in ("pc", "jpeg") else "tv"
    r = ff.run([ff.FFMPEG, "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                "-vf", f"scale={W}:{H}:flags=area:in_color_matrix={mtx}:in_range={rng}:out_range=full",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=120)
    if r.returncode or len(r.stdout) != W * H * 3:
        raise RuntimeError(f"讀不到原廠影片 {t:.1f} 秒的畫面：{r.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(r.stdout, np.uint8).reshape(H, W, 3).astype(np.float32) / 255


class OurStitcher:
    """Ungraded CPU stitch of the footage at W x H (same geometry as the conversion)."""

    def __init__(self, clip: dict, params: dict):
        import geometry as geo
        import media
        self.media, self.geo = media, geo
        self.tf, self.tb = media.timeline(clip["front"]), media.timeline(clip["back"])
        p = dict(params)
        p.pop("_stab", None)
        self.params = p
        self.maps = geo.StitchMaps(p, self.tf.width, self.tf.height, W, H, spec={"type": "equirect"})
        self.full = self.tf.color_range == "pc"
        self.off = int(round(float(p.get("back_offset", 0)) * self.tf.fps))

    @property
    def duration(self):
        return self.tf.duration

    def frame(self, t: float) -> np.ndarray:
        import jobs
        fi = int(round(t * self.tf.fps))
        fr = self.media.read_frame(self.tf, fi)
        bk = self.media.read_frame(self.tb, max(0, fi + self.off))
        if self.geo.lens_color_active(self.params):
            rgb = jobs.stitch_rgb(fr, bk, self.maps, self.params, self.full)
        else:
            rgb = jobs.yuv420_to_rgb(*jobs.stitch_yuv420(fr, bk, self.maps, self.full), full=self.full)
        return np.clip(rgb.astype(np.float32), 0, 1)


# ---------------------------------------------------------------- alignment
def _small_gray(img, w=256):
    h = w // 2
    g = img @ LUMA
    g = g.reshape(h, g.shape[0] // h, w, g.shape[1] // w).mean((1, 3))
    return g


def yaw_shift(ours: np.ndarray, ref: np.ndarray) -> tuple[int, float]:
    """Horizontal shift (pixels at W) that best lines ours up with ref, and its correlation."""
    a, b = _small_gray(ours), _small_gray(ref)
    rows = slice(int(a.shape[0] * 0.12), int(a.shape[0] * 0.80))     # leave out the poles
    a, b = a[rows], b[rows]
    a = (a - a.mean(1, keepdims=True)) / (a.std(1, keepdims=True) + 1e-4)
    b = (b - b.mean(1, keepdims=True)) / (b.std(1, keepdims=True) + 1e-4)
    c = np.fft.irfft(np.fft.rfft(b, axis=1) * np.conj(np.fft.rfft(a, axis=1)), n=a.shape[1], axis=1).sum(0)
    c /= a.size
    k = int(np.argmax(c))
    return k * (W // a.shape[1]), float(c[k])


def _blur(img, r=6):
    try:
        import cv2
        return cv2.blur(img, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_WRAP if hasattr(cv2, "BORDER_WRAP") else 4)
    except Exception:
        k = np.ones(2 * r + 1, np.float32) / (2 * r + 1)
        out = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, img)
        return np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, out)


def pairs(ours, ref, shift, step=3):
    o = np.roll(ours, shift, axis=1)
    ob, rb = _blur(o), _blur(ref)

    def grad(x):                                   # edges of the blurred picture (sensor noise / grain is gone)
        g = x @ LUMA
        return np.abs(np.roll(g, -4, 1) - np.roll(g, 4, 1)) + np.abs(np.roll(g, -4, 0) - np.roll(g, 4, 0))
    edge = grad(ob) + grad(rb)
    y0, y1 = int(H * 0.08), int(H * 0.92)
    m = np.zeros((H, W), bool)
    m[y0:y1:step, ::step] = True
    m &= edge < 0.08
    for x in (ob, rb):
        m &= (x.max(-1) < 0.97) & (x.min(-1) > 0.015)
    return ob[m], rb[m]


# ---------------------------------------------------------------- model
def _curve_fit(x_l, y_l, n=32):
    """Monotone brightness curve: median target brightness per source-brightness bin."""
    edges = np.linspace(0, 1, n + 1)
    xs, ys = [0.0], [0.0]
    for i in range(n):
        sel = (x_l >= edges[i]) & (x_l < edges[i + 1])
        if sel.sum() >= 30:
            xs.append(float(np.median(x_l[sel])))
            ys.append(float(np.median(y_l[sel])))
    xs.append(1.0)
    ys.append(max(ys[-1], 1.0) if len(ys) > 1 else 1.0)
    ys = np.maximum.accumulate(np.array(ys))                     # never darker for brighter input
    ys = np.convolve(np.pad(ys, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    return np.array(xs), np.clip(np.maximum.accumulate(ys), 0, 1.2)


def _apply_curve(rgb, cx, cy):
    L = np.maximum(rgb @ LUMA, 1e-4)
    L2 = np.interp(L, cx, cy)
    return rgb * (L2 / L)[..., None]


def _chan_curves(x, dst, n=24):
    """Per-channel residual curve after the affine step: median target per bin of each output
    channel (bright parts can have another tint than the mid tones), smoothed, monotone."""
    out = []
    for k in range(3):
        edges = np.quantile(x[:, k], np.linspace(0, 1, n + 1))
        xs, ys = [], []
        for i in range(n):
            sel = (x[:, k] >= edges[i]) & (x[:, k] <= edges[i + 1])
            if sel.sum() >= 50:
                xs.append(float(np.median(x[sel, k])))
                ys.append(float(np.median(dst[sel, k])))
        xs, ys = np.array(xs), np.array(ys)
        d = np.convolve(np.pad(ys - xs, 2, mode="edge"), np.ones(5) / 5, mode="valid")
        xs = np.concatenate([[0.0], xs, [1.0]])
        d = np.concatenate([[0.0], d, [d[-1] if len(d) else 0.0]])
        y = np.maximum.accumulate(np.clip(xs + d, 0, 1.2))
        out.append([xs.tolist(), y.tolist()])
    return out


def fit(src, dst, chan_curves=False):
    """src, dst: (N,3) -> model dict."""
    cx, cy = _curve_fit(src @ LUMA, dst @ LUMA)
    x = _apply_curve(src, cx, cy)
    X = np.hstack([x, np.ones((len(x), 1), np.float32)])
    w = np.ones(len(x), np.float32)
    M = None
    for _ in range(4):                                            # robust: down-weight badly matched pairs
        Xw = X * w[:, None]
        M, *_ = np.linalg.lstsq(Xw, dst * w[:, None], rcond=None)
        res = np.abs(X @ M - dst).sum(1)
        s = np.median(res) * 1.5 + 1e-4
        w = 1.0 / np.maximum(1.0, res / s)
    model = {"cx": cx.tolist(), "cy": cy.tolist(), "M": M.tolist()}
    if chan_curves:
        model["C"] = _chan_curves(np.clip(X @ M, 0, 1), dst)
    return model


def apply_model(rgb, model):
    x = _apply_curve(rgb, np.array(model["cx"]), np.array(model["cy"]))
    M = np.array(model["M"], np.float32)
    y = np.clip(np.concatenate([x, np.ones(x.shape[:-1] + (1,), np.float32)], -1) @ M, 0, 1)
    if model.get("C"):
        y = np.stack([np.interp(y[..., k], *model["C"][k]) for k in range(3)], -1).astype(np.float32)
    return np.clip(y, 0, 1)


# ---------------------------------------------------------------- vivid step
# The model above is least squares on paired pixels. Pairs never match exactly (registration,
# noise, different sharpening) and least squares answers that by pulling every colour towards
# the average: the table comes out too grey. Measured without pairing pixels (chroma percentiles
# of the same picture region, two clips, dev\\vivid_lut.py): clearly coloured pixels had ~0.76 of
# Fusion Studio's chroma while the median chroma was equal, and the brightest nearly white parts
# were green-cyan (a* -7.7) where Fusion Studio is neutral (a* -1.5, b* +2 in both clips). So, on
# the model's output in CIELAB: (1) the brightest low-chroma colours go to that white, (2) chroma gets
# a gain rising from 1 (greys keep their colour: there it is cast / noise) to "sat" for coloured
# pixels, a little less for reds and more for greens / purples (same sign in both clips).
VIVID = {"sat": 1.36, "c0": 8, "c1": 16, "sat_hi": 0.08, "h0": 30, "h1": 50, "red": -0.8, "green": 0.4, "purple": 0.5, "wta": -1.5, "wtb": 2.0, "wl0": 95.5, "wl1": 98.5, "wc0": 14, "wc1": 30, "yb": 1.5, "yl0": 82, "yl1": 90}


def _ss(lo, hi, v):
    t = np.clip((v - lo) / max(hi - lo, 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def vivid(rgb: np.ndarray, par: dict | None = None) -> np.ndarray:
    """rgb float (..., 3) 0..1 (display values) -> same shape with the vivid step applied."""
    import cv2
    p = dict(VIVID, **(par or {}))
    x = np.clip(rgb, 0, 1).reshape(-1, 1, 3).astype(np.float32)
    lab = cv2.cvtColor(x, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float64)
    L, a, b = lab[:, 0], lab[:, 1].copy(), lab[:, 2].copy()
    C = np.hypot(a, b)
    grey = 1 - _ss(p["wc0"], p["wc1"], C)
    w = _ss(p["wl0"], p["wl1"], L) * grey * _ss(-11.0, -5.0, b)     # pale blue sky keeps its blue: only the green-cyan cast goes
    b += _ss(p["yl0"], p["yl1"], L) * (1 - _ss(p["wl0"], p["wl1"], L)) * grey * p["yb"]
    a += w * (p["wta"] - a)                          # whites go to Fusion Studio's white, wherever the model put them
    b += w * (p["wtb"] - b)
    C = np.hypot(a, b)
    h = np.degrees(np.arctan2(b, a)) % 360

    def bump(center, width):
        return np.clip(1 - np.abs(((h - center + 180) % 360) - 180) / width, 0, 1)
    g = (p["sat"] - 1.0) * (1 + p["red"] * bump(25, 50) + p["green"] * bump(150, 60) + p["purple"] * bump(310, 60))
    k = 1 + g * _ss(p["c0"], p["c1"], C) + p["sat_hi"] * _ss(p["h0"], p["h1"], C)
    out = cv2.cvtColor(np.stack([L, a * k, b * k], -1).reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_LAB2RGB)
    return np.clip(out.reshape(np.shape(rgb)), 0, 1).astype(np.float32)


def apply_look(rgb, model):
    """What the written table does: the fitted model, then the vivid step."""
    return vivid(apply_model(rgb, model))


def write_cube(model, path, n=65):
    # 65 points, not 33: a table interpolates linearly in the colour space of its input, and with
    # 33 points the chroma gain reaches down to the grey axis (measured 1.2 where it should be 1.0)
    g = np.linspace(0, 1, n, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")                # r changes fastest in the file
    rgb = np.stack([r, gg, b], -1).reshape(-1, 3)
    out = apply_look(rgb, model)
    lines = ['TITLE "FusionStitch GoPro match"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stats(x):
    sat = x.max(-1) - x.min(-1)
    return {"mean": [round(float(v) * 255) for v in x.reshape(-1, 3).mean(0)], "sat": round(float(np.median(sat)), 3),
            "luma": round(float(np.median(x @ LUMA)), 3)}


def params_for(clip: dict, folder: str) -> dict:
    """Same lens geometry the conversion uses (calibration, then the clip's own adjustments)."""
    import geometry as geo
    import media
    import projects
    tl = media.timeline(clip["front"])
    w, h = tl.width, tl.height
    cal = (core.cfg("calibration") or {}).get(f"{w}x{h}")
    params = geo.merge_params(geo.default_params(w, h), cal) if cal else geo.default_params(w, h)
    proj = projects.get(folder, clip["id"]) if folder else {}
    if proj.get("params"):
        params = geo.merge_params(geo.default_params(w, h), proj["params"])
    return params


def guess_clip(ref_name: str, clips: dict) -> str | None:
    """VIDEO_6545(1).mp4 / ..._GP6545_... -> the clip id with that number."""
    import re
    for m in re.finditer(r"(\d{4})", Path(ref_name).stem):
        cid = next((k for k, c in clips.items() if c.get("num") == m.group(1) and c.get("kind") == "video"), None)
        if cid:
            return cid
    return None


_job = {"state": "idle", "msg": "", "frac": 0.0, "result": None, "error": "", "cancel": False}


def start(clip: dict, folder: str, ref_path: str) -> dict:
    """Run calibrate() on a background thread (used by the window and the phone page)."""
    import threading
    if _job["state"] == "running":
        return {"ok": False, "message": "已經在計算中"}
    _job.update(state="running", msg="開始…", frac=0.0, result=None, error="", cancel=False)

    def work():
        try:
            params = params_for(clip, folder)
            res = calibrate(clip, params, ref_path, progress=lambda m, f: _job.update(msg=m, frac=f),
                            cancel=lambda: _job["cancel"])
            _job.update(state="done", result=res, msg="完成", frac=1.0)
        except Exception as e:
            _job.update(state="error", error=str(e), msg="")
            core.log(f"原廠色彩對標失敗：{e}", "job_log.txt")
    threading.Thread(target=work, daemon=True, name="colormatch").start()
    return {"ok": True, "message": "開始計算（約 1～3 分鐘）"}


def status() -> dict:
    return {**{k: v for k, v in _job.items() if k != "cancel"}, "last": core.cfg("gopro_match"),
            "mode": core.cfg("color_mode") or "auto", "strength": float(core.cfg("gopro_lut_strength") or 1.0)}


# ---------------------------------------------------------------- the whole tool
def calibrate(clip: dict, params: dict, ref_path: str, n=30, progress=None, cancel=None) -> dict:
    t0 = time.time()
    say = progress or (lambda m, f: None)
    info = _ref_info(ref_path)
    st = OurStitcher(clip, params)
    dur = min(st.duration, info["duration"] or st.duration)
    if dur < 5:
        raise RuntimeError("影片太短")
    times = list(np.linspace(dur * 0.05, dur * 0.95, n))
    frames = []
    for i, t in enumerate(times):
        if cancel and cancel():
            raise RuntimeError("已取消")
        say(f"讀取畫面 {i + 1}/{n}（{t:.0f} 秒）", 0.05 + 0.75 * i / n)
        frames.append((t, st.frame(t), ref_frame(ref_path, t, info)))
    say("對齊方向…", 0.82)
    sh = [yaw_shift(o, r) for _, o, r in frames]
    score = float(np.median([c for _, c in sh]))
    # one turn for the whole clip: circular median of the per-frame shifts weighted by correlation
    ang = np.array([s for s, _ in sh]) / W * 2 * np.pi
    wts = np.array([max(0.0, c) for _, c in sh]) + 1e-6
    shift = int(round((np.arctan2((np.sin(ang) * wts).sum(), (np.cos(ang) * wts).sum()) / (2 * np.pi)) % 1 * W)) % W
    if score < 0.25:
        raise RuntimeError(f"兩支影片對不起來（相似度 {score:.2f}）：請確認原廠檔和選的素材是同一段，而且原廠轉檔時沒有防震")
    say("計算顏色對照…", 0.88)
    train = [f for i, f in enumerate(frames) if i % 5 != 2]
    test = [f for i, f in enumerate(frames) if i % 5 == 2] or frames[:1]
    S_, D_ = zip(*[pairs(o, r, shift) for _, o, r in train])
    src, dst = np.concatenate(S_), np.concatenate(D_)
    if len(src) < 5000:
        raise RuntimeError("可以比對的畫面太少（太暗、太亮或兩支差太多）")
    model = fit(src, dst)
    S2, D2 = zip(*[pairs(o, r, shift) for _, o, r in test])
    s2, d2 = np.concatenate(S2), np.concatenate(D2)
    err0 = float(np.abs(s2 - d2).mean() * 255)
    err1 = float(np.abs(apply_model(s2, model) - d2).mean() * 255)
    d = lut_dir()
    cube = d / "gopro_match.cube"
    write_cube(model, cube)
    # preview: ours (no grade) | ours + LUT | official, one test moment
    _, o, r = test[len(test) // 2]
    o = np.roll(o, shift, axis=1)
    prev = np.concatenate([o, apply_look(o, model), r], 0)
    try:
        import cv2
        cv2.imwrite(str(d / "gopro_match_preview.jpg"),
                    cv2.resize((prev[..., ::-1] * 255).astype(np.uint8), (W // 2, H * 3 // 2)),
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
    except Exception:
        pass
    res = {"cube": str(cube), "ref": ref_path, "clip": clip.get("name", ""), "shift_deg": round(shift / W * 360, 1),
           "align": round(score, 2), "err_before": round(err0, 1), "err_after": round(err1, 1),
           "pairs": int(len(src)), "frames": n, "time": time.strftime("%Y-%m-%d %H:%M"),
           "seconds": round(time.time() - t0), "ours": _stats(np.concatenate([apply_look(f[1], model) for f in test])),
           "ours_raw": _stats(np.concatenate([f[1] for f in test])), "official": _stats(np.concatenate([f[2] for f in test])),
           "preview": str(d / "gopro_match_preview.jpg")}
    (d / "gopro_match.json").write_text(json.dumps({**res, "model": model}, ensure_ascii=False, indent=1), encoding="utf-8")
    core.set_cfg("gopro_lut", str(cube))
    core.set_cfg("gopro_match", res)
    say("完成", 1.0)
    core.log(f"原廠色彩對標：{json.dumps(res, ensure_ascii=False)}", "job_log.txt")
    return res
