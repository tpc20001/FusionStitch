"""效能（降速）: let conversions run slower so the computer stays smooth for other things.

Modes (settings "pace_mode"):
  full    100%: as fast as possible (default)
  manual  always at pace_pct %
  smart   智慧降速: at pace_pct % while someone is using this computer (keyboard / mouse in the
          last pace_idle_min minutes), back to 100% when nobody is

How: every converting thread works, then rests in proportion (at 60% it rests 0.67 s for every
second of work), so the GPU, CPU and disks all get about that share; the relay copy is limited to a
matching speed; and the program runs at "below normal" priority so the apps you use come first.
Conversion never stops. Jobs that ran slower are not used to learn the normal speed.
"""
from __future__ import annotations

import os
import threading
import time

import core

_tl = threading.local()
_cache = {"t": 0.0, "level": 1.0, "active": False, "prio": None}


def idle_seconds() -> float | None:
    """Seconds since the last keyboard / mouse input on this computer (Windows), else None."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LII()
        li.cbSize = ctypes.sizeof(LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return None
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)
    except Exception:
        return None


def state() -> dict:
    """{"mode", "pct", "level" 0.1..1, "active" (someone using the PC), "idle_min"}"""
    now = time.time()
    if now - _cache["t"] < 1.0:
        return dict(_cache["state"])
    mode = str(core.cfg("pace_mode") or "full")
    pct = max(10, min(100, int(core.cfg("pace_pct") or 60)))
    idle_min = float(core.cfg("pace_idle_min") or 3)
    idle = idle_seconds()
    active = idle is not None and idle < idle_min * 60
    if mode == "manual":
        level = pct / 100
    elif mode == "smart":
        level = pct / 100 if active else 1.0
    else:
        level = 1.0
    st = {"mode": mode, "pct": pct, "level": level, "active": active, "idle_min": idle_min,
          "idle": idle}
    _cache.update(t=now, state=st)
    _priority(level < 0.999)
    return dict(st)


def level() -> float:
    return state()["level"]


def _priority(low: bool):
    """Below-normal process priority while slowed down (new ffmpeg processes inherit it)."""
    if _cache["prio"] == low or os.name != "nt":
        _cache["prio"] = low
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000 if low else 0x20)   # BELOW_NORMAL / NORMAL
        _cache["prio"] = low
        core.log(f"pace: priority {'below normal' if low else 'normal'}")
    except Exception:
        _cache["prio"] = low


def pace(job=None):
    """Call once per unit of work (a frame) from a working thread: rests in proportion to the work
    done since the last call when slowed down."""
    now = time.time()
    last = getattr(_tl, "last", None)
    _tl.last = now
    lv = level()
    if lv >= 0.999 or last is None:
        return
    if job is not None:
        job.throttled = True
    work = min(5.0, now - last)
    rest = min(3.0, work * (1.0 / lv - 1.0))
    if rest > 0.002:
        time.sleep(rest)
        _tl.last = time.time()


def copy_rest(nbytes: int, seconds: float):
    """The relay copy: at most ~250 MB/s × level while slowed down."""
    lv = level()
    if lv >= 0.999:
        return
    want = nbytes / max(15e6, 250e6 * lv)
    if want > seconds:
        time.sleep(min(2.0, want - seconds))


def label(st: dict | None = None) -> str:
    st = st or state()
    if st["mode"] == "full":
        return "全速"
    if st["mode"] == "manual":
        return f"降速 {st['pct']}%"
    return f"智慧降速：{'有人在用電腦 → ' + str(st['pct']) + '%' if st['active'] else '沒人在用 → 全速'}"
