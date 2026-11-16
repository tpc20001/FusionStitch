"""GoPro Fusion gyro / accelerometer (GPMF) -> per-frame camera orientation -> stabilisation.

Telemetry lives in the front file's 'gpmd' track (one ~1 s packet per second):
  ACCL  float m/s²  ~200 Hz, axes labelled (up/down, right/left, forward/back)
  GYRO  int16/SCAL rad/s  3200 Hz, axes labelled (z, x, y)
Both are converted to the stitcher's camera frame (x right, y up, z forward =
front lens) with AXES below, which was measured on real footage
(dev/tests/check_gyro_axes.py).

Orientation: gyro integration with a slow accelerometer correction for tilt
(complementary filter, gated so a motorcycle's lean in a turn is not taken
as "level"). Stabilisation then chooses a view orientation per frame:
  horizon - keep the horizon level, follow the heading exactly
  smooth  - level horizon + smoothed heading (FlowState-like)
  lock    - level horizon + heading fixed (the 360 view never turns)
and returns, per frame, the rotation S(t) that maps stabilised view
directions into the camera frame (it multiplies geometry.global_rotation).
"""
from __future__ import annotations

import hashlib
import math
import os
import struct
import subprocess
from pathlib import Path

import numpy as np

import core
import ffm as ff
import media

CACHE = core.PACKAGE_DIR / "gyro_cache"

# camera = AXES @ sensor  (rows: camera x, y, z; columns: sensor axes in file order).
# Measured on the user's Fusion (dev/tests/check_gyro_axes.py): visual rotation of
# frame pairs vs integrated gyro (Procrustes, residual 0.9° on 3.8° motion) and
# gravity rotation vs gyro for the accelerometer. The accelerometer reports
# specific force with the opposite sign of the gyro axes; at rest it reads +9.8 on y.
GYRO_AXES = np.array([[0, 1, 0], [0, 0, -1], [-1, 0, 0]], np.float64)
ACCL_AXES = -GYRO_AXES
DEFAULT_OFFSET_MS = -60.0      # telemetry runs ~60 ms ahead of the picture

MODES = [("off", "關閉"), ("horizon", "水平鎖定"), ("smooth", "防震＋水平（推薦）"), ("lock", "方向鎖定")]


def default_stab() -> dict:
    return {"mode": "off", "smooth": 1.5, "offset_ms": DEFAULT_OFFSET_MS, "tilt_tau": 6.0, "rs_ms": 0.0}


# ---------------------------------------------------------------- GPMF parsing
def _klv(buf: bytes):
    i, n = 0, len(buf)
    while i + 8 <= n:
        key = buf[i:i + 4]
        typ = buf[i + 4]
        ssize = buf[i + 5]
        rep = struct.unpack(">H", buf[i + 6:i + 8])[0]
        size = ssize * rep
        yield key, typ, ssize, rep, buf[i + 8:i + 8 + size]
        i += 8 + (size + 3) // 4 * 4


def _parse_packet(buf: bytes) -> dict:
    out = {}
    for key, typ, ssize, rep, payload in _klv(buf):
        if key != b"DEVC":
            continue
        for k2, t2, s2, r2, p2 in _klv(payload):
            if k2 != b"STRM":
                continue
            scal, data, kind = 1.0, None, None
            for k3, t3, s3, r3, p3 in _klv(p2):
                if k3 == b"SCAL":
                    vals = struct.unpack(">" + {ord("s"): "h", ord("S"): "H", ord("l"): "i", ord("L"): "I", ord("f"): "f"}[t3] * r3 *
                                         (s3 // {ord("s"): 2, ord("S"): 2, ord("l"): 4, ord("L"): 4, ord("f"): 4}[t3]), p3)
                    scal = float(vals[0])
                elif k3 in (b"ACCL", b"GYRO"):
                    kind = k3.decode()
                    if t3 == ord("f"):
                        data = np.frombuffer(p3, ">f4").reshape(r3, s3 // 4).astype(np.float64)
                    elif t3 == ord("s"):
                        data = np.frombuffer(p3, ">i2").reshape(r3, s3 // 2).astype(np.float64)
            if kind and data is not None:
                out[kind] = data / scal
    return out


def _packets(path: str):
    """(pts, duration) per gpmd packet + their raw bytes."""
    info = ff.run([ff.FFPROBE, "-v", "error", "-select_streams", "d", "-show_entries",
                   "stream=index,codec_tag_string", "-of", "csv=p=0", path]).stdout.decode()
    idx = next((int(l.split(",")[0]) for l in info.splitlines() if "gpmd" in l), None)
    if idx is None:
        return [], b""
    r = ff.run([ff.FFPROBE, "-v", "error", "-select_streams", str(idx), "-show_entries",
                "packet=pts_time,duration_time,size", "-of", "csv=p=0", path])
    pk = []
    for line in r.stdout.decode().splitlines():
        p = line.split(",")
        if len(p) >= 3:
            pk.append((float(p[0]), float(p[1]), int(p[2])))
    data = ff.run([ff.FFMPEG, "-v", "error", "-i", path, "-map", f"0:{idx}", "-c", "copy", "-f", "data", "-"]).stdout
    return pk, data


def load(files: list[str]) -> dict | None:
    """Telemetry of all chapters on the clip timeline (cached). Camera-frame arrays:
    gt, gyro (N,3) rad/s ; at, accl (M,3) m/s²."""
    key = hashlib.sha1("|".join(f"{p}:{os.path.getsize(p)}:{os.path.getmtime(p)}" for p in files).encode()).hexdigest()[:20]
    CACHE.mkdir(parents=True, exist_ok=True)
    cf = CACHE / f"{key}.npz"
    if cf.exists():
        z = np.load(cf)
        return {k: z[k] for k in z.files}
    tl = media.timeline(files)
    gts, gs, ats, acs = [], [], [], []
    t0 = 0.0
    for ci, path in enumerate(files):
        pk, data = _packets(path)
        pos = 0
        for pts, dur, size in pk:
            d = _parse_packet(data[pos:pos + size])
            pos += size
            for kind, tl_, vl_ in (("GYRO", gts, gs), ("ACCL", ats, acs)):
                v = d.get(kind)
                if v is None or len(v) == 0:
                    continue
                n = len(v)
                tl_.append(t0 + pts + (np.arange(n) + 0.5) / n * dur)
                vl_.append(v[:, :3])
        t0 += tl.durations[ci]
    if not gs:
        return None
    out = {"gt": np.concatenate(gts), "gyro": np.concatenate(gs) @ GYRO_AXES.T,
           "at": np.concatenate(ats), "accl": np.concatenate(acs) @ ACCL_AXES.T}
    np.savez(cf, **out)
    return out


# ---------------------------------------------------------------- orientation
def _qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _qrot(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _axis_angle_q(v):
    a = np.linalg.norm(v)
    if a < 1e-12:
        return np.array([1.0, *(v / 2)])
    s = math.sin(a / 2) / a
    return np.array([math.cos(a / 2), v[0] * s, v[1] * s, v[2] * s])


def orientation(tel: dict, frame_times: np.ndarray, tilt_tau: float = 6.0, rate: float = 400.0) -> np.ndarray:
    """Camera->world rotation per frame time, world y = up (from gravity)."""
    gt, gyro, at, accl = tel["gt"], tel["gyro"], tel["at"], tel["accl"]
    t_end = max(gt[-1], frame_times[-1] + 0.1)
    grid = np.arange(gt[0], t_end, 1.0 / rate)
    # average the 3200 Hz gyro into the integration steps (box filter, no aliasing of vibration)
    edges = np.concatenate([grid - 0.5 / rate, [grid[-1] + 0.5 / rate]])
    cnt = np.histogram(gt, edges)[0]
    w = np.stack([np.histogram(gt, edges, weights=gyro[:, k])[0] for k in range(3)], -1) / np.maximum(cnt, 1)[:, None]
    empty = cnt == 0
    if empty.any():
        for k in range(3):
            w[empty, k] = np.interp(grid[empty], gt, gyro[:, k])
    # accelerometer: 0.25 s average (motorcycle vibration is several m/s²)
    acc_g = np.stack([np.interp(grid, at, np.convolve(accl[:, k], np.ones(50) / 50, "same")) for k in range(3)], -1)
    still = (np.linalg.norm(w, axis=1) < 0.05) & (np.abs(np.linalg.norm(acc_g, axis=1) - 9.81) < 0.4)
    bias = np.median(w[still], axis=0) if still.sum() > rate * 2 else np.zeros(3)
    w = w - bias
    q = _tilt_quat(acc_g[: int(rate)].mean(0))
    qw, qx, qy, qz = (float(v) for v in q)
    dt = 1.0 / rate
    k = dt / max(0.1, tilt_tau)
    W = w.tolist()
    A = acc_g.tolist()
    out = np.empty((len(grid), 4))
    sin, cos, sqrt = math.sin, math.cos, math.sqrt
    for i in range(len(grid)):
        wx, wy, wz = W[i]
        # q = q * exp(w dt)
        ang = sqrt(wx * wx + wy * wy + wz * wz) * dt
        if ang > 1e-12:
            sa = sin(ang / 2) / (ang / dt)
            bw, bx, by, bz = cos(ang / 2), wx * sa, wy * sa, wz * sa
            qw, qx, qy, qz = (qw * bw - qx * bx - qy * by - qz * bz, qw * bx + qx * bw + qy * bz - qz * by,
                              qw * by - qx * bz + qy * bw + qz * bx, qw * bz + qx * by - qy * bx + qz * bw)
        ax, ay, az = A[i]
        an = sqrt(ax * ax + ay * ay + az * az)
        # gravity is trusted only when the camera is neither turning nor accelerating: in a turn a
        # motorcycle leans and the accelerometer points along the bike (|a| = g / cos(lean), only
        # ~1.15 g at 30°), which would drag the horizon with the bike
        if abs(an - 9.81) < 0.6 and ang / dt < 0.25:
            ax, ay, az = ax / an, ay / an, az / an
            # measured up in world = R(q) a
            ux = (1 - 2 * (qy * qy + qz * qz)) * ax + 2 * (qx * qy - qz * qw) * ay + 2 * (qx * qz + qy * qw) * az
            uy = 2 * (qx * qy + qz * qw) * ax + (1 - 2 * (qx * qx + qz * qz)) * ay + 2 * (qy * qz - qx * qw) * az
            uz = 2 * (qx * qz - qy * qw) * ax + 2 * (qy * qz + qx * qw) * ay + (1 - 2 * (qx * qx + qy * qy)) * az
            cx, cz = -uz * k, ux * k                        # (u x [0,1,0]) * k : small world-frame rotation
            # q = exp(c) * q   (c has no y part)
            qw, qx, qy, qz = (qw - 0.5 * (cx * qx + cz * qz), qx + 0.5 * (cx * qw - cz * qy),
                              qy + 0.5 * (cz * qx - cx * qz), qz + 0.5 * (cx * qy + cz * qw))
        nq = sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
        qw, qx, qy, qz = qw / nq, qx / nq, qy / nq, qz / nq
        out[i] = (qw, qx, qy, qz)
    idx = np.clip(np.searchsorted(grid, frame_times), 0, len(grid) - 1)
    return np.array([_qrot(out[j]) for j in idx])


def _tilt_quat(up_cam):
    """Rotation that takes the measured up direction (camera frame) to world +y."""
    u = up_cam / (np.linalg.norm(up_cam) + 1e-9)
    axis = np.cross(u, [0.0, 1.0, 0.0])
    s = np.linalg.norm(axis)
    c = float(np.dot(u, [0.0, 1.0, 0.0]))
    if s < 1e-9:
        return np.array([1.0, 0, 0, 0]) if c > 0 else np.array([0.0, 1.0, 0, 0])
    ang = math.atan2(s, c)
    return _axis_angle_q(axis / s * ang)


# ---------------------------------------------------------------- stabilisation
def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def stab_rotations(R: np.ndarray, fps: float, mode: str, smooth_s: float) -> np.ndarray:
    """Per-frame S = R^T V : stabilised view direction -> camera direction."""
    fwd = R[:, :, 2]                                   # camera forward in world
    yaw = np.unwrap(np.arctan2(fwd[:, 0], fwd[:, 2]))
    if mode == "lock":
        yaw_v = np.full_like(yaw, yaw[0])
    elif mode == "smooth":
        sig = max(1.0, smooth_s * fps)
        n = int(sig * 3)
        ker = np.exp(-0.5 * (np.arange(-n, n + 1) / sig) ** 2)
        ker /= ker.sum()
        pad = np.pad(yaw, n, mode="edge")
        yaw_v = np.convolve(pad, ker, mode="valid")
    else:
        yaw_v = yaw
    return np.array([R[i].T @ _ry(yaw_v[i]) for i in range(len(R))])


def frame_rotations(files: list[str], fps: float, first: int, count: int, stab: dict) -> np.ndarray | None:
    """S(t) for frames [first, first+count) of the clip, or None when off / no telemetry."""
    stab = {**default_stab(), **(stab or {})}
    if stab["mode"] == "off":
        return None
    tel = load(files)
    if tel is None:
        raise RuntimeError("這段影片沒有陀螺儀資料")
    off = float(stab.get("offset_ms", 0)) / 1000.0
    # orientation over the whole clip (smoothing needs context), then slice
    total = int(round(media.timeline(files).duration * fps))
    times = (np.arange(total) + 0.5) / fps + off
    R = orientation(tel, times, float(stab.get("tilt_tau", 6.0)))
    S = stab_rotations(R, fps, stab["mode"], float(stab.get("smooth", 1.5)))
    return S[first:first + count]


def frame_omega(files: list[str], fps: float, first: int, count: int, offset_ms: float = DEFAULT_OFFSET_MS) -> np.ndarray | None:
    """Mean angular velocity (camera frame, rad/s) during each frame [first, first+count), for
    rolling-shutter correction. None without telemetry."""
    tel = load(files)
    if tel is None:
        return None
    gt, g = tel["gt"], tel["gyro"]
    t = (np.arange(first, first + count) + 0.5) / fps + offset_ms / 1000.0
    edges = np.empty(2 * count)
    edges[0::2], edges[1::2] = t - 0.5 / fps, t + 0.5 / fps
    cs = np.concatenate([np.zeros((1, 3)), np.cumsum(g, 0)])
    ix = np.searchsorted(gt, edges)
    lo, hi = ix[0::2], ix[1::2]
    n = np.maximum(hi - lo, 1)[:, None]
    w = (cs[hi] - cs[lo]) / n
    empty = (hi - lo) == 0
    if empty.any():
        for k in range(3):
            w[empty, k] = np.interp(t[empty], gt, g[:, k])
    return w.astype(np.float32)
