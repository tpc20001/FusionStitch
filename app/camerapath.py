"""Keyframed virtual camera for reframing (flat 16:9 / 9:16 ...) and animated little planets.

A path is a list of keyframes {"t": seconds, "yaw", "pitch", "roll", "fov"}.
Between keys the view moves along a monotone cubic (PCHIP: smooth, never
overshoots past a key); yaw is unwrapped so it always takes the short way
round. One key = a fixed view.
"""
from __future__ import annotations

import math

import numpy as np

FIELDS = ("yaw", "pitch", "roll", "fov")


def clean(keys) -> list[dict]:
    out = []
    for k in keys or []:
        try:
            out.append({"t": float(k["t"]), **{f: float(k.get(f, 0.0)) for f in FIELDS}})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda k: k["t"])
    # one key per time
    dedup = []
    for k in out:
        if dedup and abs(dedup[-1]["t"] - k["t"]) < 1e-3:
            dedup[-1] = k
        else:
            dedup.append(k)
    return dedup


def _unwrap(vals):
    v = np.array(vals, dtype=np.float64)
    return np.degrees(np.unwrap(np.radians(v)))


def _interp(T, V, t):
    if len(T) == 1:
        return np.full_like(np.asarray(t, dtype=np.float64), V[0])
    try:
        from scipy.interpolate import PchipInterpolator
        f = PchipInterpolator(T, V, extrapolate=False)
        out = f(np.clip(t, T[0], T[-1]))
        return out
    except Exception:
        return np.interp(t, T, V)


def sample(keys, times) -> dict | None:
    """Field arrays at the given times (seconds)."""
    keys = clean(keys)
    if not keys:
        return None
    T = np.array([k["t"] for k in keys])
    t = np.asarray(times, dtype=np.float64)
    out = {}
    for f in FIELDS:
        vals = [k[f] for k in keys]
        if f in ("yaw", "pitch", "roll"):
            vals = _unwrap(vals)
        out[f] = _interp(T, np.asarray(vals), t)
    return out


def at(keys, t: float) -> dict | None:
    s = sample(keys, [t])
    return None if s is None else {f: float(s[f][0]) for f in FIELDS}


def per_frame(keys, fps: float, first: int, count: int) -> dict | None:
    return sample(keys, (np.arange(first, first + count) + 0.5) / fps)


def view_matrix(kind: str, v: dict) -> np.ndarray:
    """View rotation used by geometry.flat_dirs / planet_dirs and the shaders."""
    from geometry import rot
    if kind == "flat":
        return rot(v["yaw"], -v["pitch"], v.get("roll", 0.0))
    return rot(v["yaw"], v["pitch"], v.get("roll", 0.0))


def flat_k(fov_deg: float) -> float:
    return math.tan(math.radians(max(5.0, min(170.0, fov_deg))) / 2)


def planet_k(fov_deg: float) -> float:
    return 2 * math.tan(math.radians(fov_deg) / 4)
