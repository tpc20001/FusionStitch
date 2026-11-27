"""轉檔紀錄: every finished video conversion (original size, output size, speed), and the
time estimates made from them.

Records live in Package\\history.json (newest last, at most 3000). They answer two questions:
  * history page: how big the output is next to the original ("轉出後 17%，省下 20 GB");
  * how fast the next conversion will be (預計完成時間). The speed of past jobs with the same
    output size / time-lapse step is the starting guess; during a job it is blended with the
    job's own AVERAGE speed (not the momentary one), and the finish time it gives is smoothed,
    so the estimate settles instead of jumping with every speed change.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time

import core

FILE = core.PACKAGE_DIR / "history.json"
MAX = 3000
_lock = threading.Lock()
_cache: list | None = None


_ver = [0]                    # 每加一筆 +1：下面算好的統計就失效
_memo: dict = {}


def _memoize(fn):
    """紀錄沒變就重用上次的結果（手機、電腦每秒都會問，原本每次都把幾千筆重算一次）."""
    def w(*a):
        k = (fn.__name__, a, _ver[0])
        if k not in _memo:
            if len(_memo) > 500:
                _memo.clear()
            _memo[k] = fn(*a)
        return _memo[k]
    w.__name__ = fn.__name__
    w.__doc__ = fn.__doc__
    return w


# ---------------------------------------------------------------- storage
def records() -> list:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = json.loads(FILE.read_text(encoding="utf-8"))
                if not isinstance(_cache, list):
                    _cache = []
            except (OSError, ValueError):
                _cache = []
        return list(_cache)


def add(rec: dict):
    records()
    with _lock:
        _ver[0] += 1
        _cache.append(rec)
        del _cache[:-MAX]
        try:
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, FILE)
        except OSError:
            pass


_SB: dict = {}


def source_bytes(clip: dict) -> int:
    """Size of the original footage of a clip (both lenses, all chapters, sound files).
    手機每 3 秒問一次整個資料夾：同一組檔案 60 秒內不再去硬碟讀."""
    k = tuple(f for key in ("front", "back", "wavs") for f in (clip.get(key) or []))
    hit = _SB.get(k)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    n = 0
    for key in ("front", "back", "wavs"):
        for f in clip.get(key) or []:
            try:
                n += os.path.getsize(f)
            except OSError:
                pass
    if len(_SB) > 5000:
        _SB.clear()
    _SB[k] = (time.time(), n)
    return n


@_memoize
def _by_name() -> dict:
    d = {}
    for r in records():                               # (後面的蓋前面的 → 最新的)
        n = os.path.basename(r.get("output", "")).lower()
        if n:
            d[n] = r
    return d


def for_output(path: str) -> dict | None:
    """Newest record of this output file (by name, so a moved file is still found). 用名字索引，不再每次掃全部紀錄."""
    return _by_name().get(os.path.basename(path).lower())


def saving_text(src: int, out: int) -> str:
    """'原檔 32.1 GB → 轉出後 17%，省下 26.6 GB'."""
    if not src or not out:
        return ""
    pct = out / src * 100
    diff = src - out
    tail = f"省下 {core.fmt_bytes(diff)}" if diff > 0 else f"多了 {core.fmt_bytes(-diff)}"
    return f"原檔 {core.fmt_bytes(src)} → 轉出後 {pct:.0f}%，{tail}"


# ---------------------------------------------------------------- speed from past jobs
@_memoize
def _similar(ow: int, step: int, preset: str | None):
    rs = [r for r in records() if r.get("fps") and r.get("frames", 0) > 200 and not r.get("throttled")]
    for f in (lambda r: r.get("ow") == ow and r.get("step") == step and r.get("preset") == preset,
              lambda r: r.get("ow") == ow and r.get("step") == step,
              lambda r: r.get("ow") == ow,
              lambda r: True):
        m = [r for r in rs if f(r)]
        if len(m) >= 1:
            return m[-30:]
    return []


@_memoize
def prior(ow: int, step: int, preset: str | None) -> dict:
    """Expected frame-loop speed (frames / s), preparation time (s) and finishing time per output
    second, from up to 30 similar past jobs (weighted towards the recent ones), plus how many."""
    m = _similar(ow, step, preset)
    if not m:
        return {"fps": None, "prep": 60.0, "n": 0}
    w = [0.9 ** (len(m) - 1 - i) for i in range(len(m))]
    tot = sum(w)
    # speed: weighted harmonic mean (time per frame is what adds up)
    fps = tot / sum(wi / r["fps"] for wi, r in zip(w, m))
    prep = sorted(r.get("prep", 60.0) for r in m)[len(m) // 2]
    return {"fps": fps, "prep": prep, "n": len(m)}


class Estimator:
    """Finish time of the running job. update() once per finished frame (cheap)."""
    K = 1200.0          # frames of own measurement that count as much as the past jobs
    TAU = 30.0          # seconds: smoothing of the finish time

    def __init__(self, total_frames: int, done_frames: int, ow: int, step: int, preset: str | None):
        self.total = max(1, int(total_frames))
        self.p = prior(ow, step, preset)
        self.t0 = None
        self.n0 = done_frames
        self.n = 0
        self.finish = None
        self.last = 0.0

    def start_guess(self) -> float | None:
        """Seconds the frame loop will take, before it starts (past jobs only)."""
        if not self.p["fps"]:
            return None
        return (self.total - self.n0) / self.p["fps"]

    def update(self, done_total: int):
        now = time.time()
        if self.t0 is None:
            self.t0 = now
            return
        self.n += 1
        if now - self.last < 0.5:           # twice a second is plenty
            return
        self.last = now
        el = now - self.t0
        if el < 3 or self.n < 10:
            if self.p["fps"] and self.finish is None:
                self.finish = now + (self.total - done_total) / self.p["fps"]
            return
        own = self.n / el                    # average speed of this job so far
        if self.p["fps"]:
            rate = (self.p["fps"] * self.K + own * self.n) / (self.K + self.n)
        else:
            rate = own
        target = now + (self.total - done_total) / max(rate, 1e-3)
        if self.finish is None:
            self.finish = target
        else:
            a = 1.0 - math.exp(-0.5 / self.TAU)
            self.finish += a * (target - self.finish)
            # never show a finish time in the past while frames are still left
            self.finish = max(self.finish, now + 1.0)

    def eta(self) -> float | None:
        return None if self.finish is None else max(0.0, self.finish - time.time())


_guess_cache: dict = {}


def guess_job(j) -> float | None:
    """Seconds a queued job will take, from past jobs' speed (None = no history yet). Cached per job."""
    if j.id in _guess_cache:
        return _guess_cache[j.id]
    g = None
    try:
        import media
        tl = media.timeline(j.clip["front"])
        sp = float(j.opts.get("speed") or 1.0)
        step = max(1, int(round(sp))) if sp >= 1 else 1
        start, end = float(j.opts.get("start") or 0), float(j.opts.get("end") or 0) or tl.duration
        frames = max(1.0, (end - start) * tl.fps / step)
        w = int(j.opts.get("width") or 0) or 5376
        p = prior(w, step, j.opts.get("preset"))
        if p["fps"]:
            E = expected(j)
            try:
                import pace
                lv_ = max(0.1, pace.level())
            except Exception:
                lv_ = 1.0
            g = E["prep"] + frames / p["fps"] / lv_ + E["fin"]
    except Exception:
        g = None
    _guess_cache[j.id] = g
    return g


def schedule(jobs_list) -> dict:
    """{job id: (finish timestamp or None, is_estimate_from_history)} for running + waiting jobs, in order."""
    out, t = {}, time.time()
    for j in jobs_list:
        if j.status == "running":
            ea = getattr(j, "eta_at", None)
            ov = None
            try:
                ov = overall(j)
            except Exception:
                pass
            if ov and ov.get("left") is not None and ov.get("phase") != "copy":
                t = time.time() + ov["left"]
                out[j.id] = t
            elif ea:
                t = ea + 15
                out[j.id] = t
            else:
                g = guess_job(j)
                t = (t + g) if g else t
                out[j.id] = t if g else None
        elif j.status == "queued":
            g = guess_job(j)
            if g:
                t = max(t, time.time()) + g
            out[j.id] = t if g else None
    return out


# ---------------------------------------------------------------- output size from past jobs
@_memoize
def ratio_stats(preset: str | None) -> dict:
    """Output size / original size of past normal-speed jobs with this preset (newest 200):
    {"n", "mid", "lo", "hi"} (median, 10th and 90th percentile), or n = 0."""
    rs = [r["out"] / r["src"] for r in records()
          if r.get("src") and r.get("out") and (r.get("step") or 1) == 1 and (r.get("speed") or 1.0) == 1.0
          and r.get("preset") == preset and (r.get("duration") or 0) > 20 and 0.01 < r["out"] / r["src"] < 3]
    rs = sorted(rs[-200:])
    if not rs:
        return {"n": 0}

    def q(p):
        i = (len(rs) - 1) * p
        a, b = int(math.floor(i)), int(math.ceil(i))
        return rs[a] + (rs[b] - rs[a]) * (i - a)
    return {"n": len(rs), "mid": q(0.5), "lo": q(0.1), "hi": q(0.9)}


def batch_estimate(items: list, preset: str | None) -> dict:
    """items: [{"src": bytes, "fallback": formula estimate}] -> per-item mid estimate and the
    batch total with a safe range. Uses past ratios when there are at least 3 such jobs."""
    st = ratio_stats(preset)
    use = st.get("n", 0) >= 3
    per, mid, lo, hi = [], 0.0, 0.0, 0.0
    for it in items:
        src = float(it.get("src") or 0)
        if use and src:
            m, a, b = src * st["mid"], src * st["lo"], src * st["hi"]
        else:
            m = float(it.get("fallback") or 0)
            a, b = m * 0.75, m * 1.35
        per.append(m)
        mid, lo, hi = mid + m, lo + a, hi + b
    basis = (f"依過去 {st['n']} 支同設定的轉檔（平均約原檔 {st['mid'] * 100:.0f}%，"
             f"範圍 {st['lo'] * 100:.0f}–{st['hi'] * 100:.0f}%）") if use else \
        f"依畫質公式估算（同設定轉檔紀錄 {st.get('n', 0)} 支，滿 3 支後改用實際比例）"
    return {"per": per, "mid": mid, "lo": lo, "hi": hi, "basis": basis, "stats": st}


# ---------------------------------------------------------------- 整體進度: preparation + conversion + finishing
# Every finished job records how long its three stages took (prep2 / loopt / fin, seconds, plus
# the output duration). The bar of a running job spreads over all three stages with the expected
# length of each, learned from up to 30 similar past jobs (per output second), so it moves evenly
# from 0 to 100 instead of sitting at 0 during the analysis and at 100 during the join.
PREP_DEF = (20.0, 0.06)      # seconds + seconds per output second, until there is history
FIN_DEF = (8.0, 0.02)
_dur_cache: dict = {}


def _out_seconds(j) -> float:
    if j.id in _dur_cache:
        return _dur_cache[j.id]
    d = 0.0
    try:
        import media
        tl = media.timeline(j.clip["front"])
        start, end = float(j.opts.get("start") or 0), float(j.opts.get("end") or 0) or tl.duration
        sp = float(j.opts.get("speed") or 1.0)
        d = max(1.0, (end - start) / (sp if sp >= 1 else 1.0))
    except Exception:
        d = 0.0
    _dur_cache[j.id] = d
    return d


@_memoize
def phase_model(ow: int, step: int, preset: str | None) -> dict:
    """{"prep": (base, per_s), "fin": (base, per_s), "n"} from past jobs that recorded their stages."""
    rs = [r for r in records() if r.get("fin") is not None and r.get("prep2") is not None and (r.get("duration") or 0) > 10
          and not r.get("throttled")]
    m = []
    for f in (lambda r: r.get("ow") == ow and r.get("step") == step and r.get("preset") == preset,
              lambda r: r.get("ow") == ow and r.get("step") == step, lambda r: True):
        m = [r for r in rs if f(r)]
        if len(m) >= 3:
            break
    if len(m) < 3:
        return {"prep": PREP_DEF, "fin": FIN_DEF, "n": len(m)}
    m = m[-30:]
    med = lambda xs: sorted(xs)[len(xs) // 2]
    return {"prep": (0.0, med([r["prep2"] / r["duration"] for r in m])),
            "fin": (0.0, med([r["fin"] / r["duration"] for r in m])), "n": len(m)}


def expected(j) -> dict:
    """Expected seconds of each stage for a job: {"prep", "loop" (None = unknown), "fin"}."""
    d = _out_seconds(j) or 600.0
    w = int(j.opts.get("width") or 0) or 5376
    sp = float(j.opts.get("speed") or 1.0)
    step = max(1, int(round(sp))) if sp >= 1 else 1
    pm = phase_model(w, step, j.opts.get("preset"))
    prep = pm["prep"][0] + pm["prep"][1] * d
    fin = pm["fin"][0] + pm["fin"][1] * d
    loop = None
    p = prior(w, step, j.opts.get("preset"))
    try:
        import media
        fps = media.timeline(j.clip["front"]).fps
    except Exception:
        fps = 29.97
    if p["fps"]:
        loop = d * fps / p["fps"]
        try:                                        # 效能（降速）: slower while slowed down
            import pace
            loop /= max(0.1, pace.level())
        except Exception:
            pass
    return {"prep": max(5.0, prep), "loop": loop, "fin": max(3.0, fin), "model_n": pm["n"]}


def overall(j) -> dict:
    """{"pct" 0..1, "left" seconds or None, "phase": copy / prep / encode / finish / done / wait}"""
    if j.status == "done":
        return {"pct": 1.0, "left": 0.0, "phase": "done"}
    if j.status not in ("running", "paused"):
        return {"pct": 0.0, "left": None, "phase": "wait"}
    if j.status == "paused" and not getattr(j, "t_prep0", None) and j.progress <= 0:
        return {"pct": 0.0, "left": None, "phase": "wait"}
    now = time.time()
    t_prep0 = getattr(j, "t_prep0", None)
    est = getattr(j, "est", None)
    if not t_prep0:
        # a job started by an older version (before a hot update) has no stage times: use what it has
        t_loop0_ = est.t0 if est is not None and getattr(est, "t0", None) else None
        if t_loop0_ or j.progress > 0:
            t_prep0 = t_loop0_ or getattr(j, "started", None) or now
        elif j.status == "running" and str(j.message).startswith("複製素材"):
            return {"pct": 0.0, "left": None, "phase": "copy"}
        elif j.status == "running":
            t_prep0 = getattr(j, "started", None) or now
        else:
            t_prep0 = None
    E = expected(j)
    t_loop0 = est.t0 if est is not None and getattr(est, "t0", None) else None
    t_fin0 = getattr(j, "t_fin0", None)
    prep_act = (t_loop0 - t_prep0) if (t_loop0 and t_prep0) else None
    loop_e = None
    if est is not None and est.finish and t_loop0:
        loop_e = (est.finish - t_loop0)
    elif E["loop"]:
        loop_e = E["loop"]
    elif j.progress > 0.02 and t_loop0:
        loop_e = (now - t_loop0) / j.progress
    if loop_e is None:
        loop_e = max(60.0, _out_seconds(j) * 2)      # no history yet: a rough guess (about 15 fps)
    prep = prep_act if prep_act is not None else E["prep"]
    fin = E["fin"]
    if t_fin0:                                       # finishing
        el = now - t_fin0
        done_s = prep + loop_e + min(el, fin * 0.97)
        left = max(3.0, fin - el)
        phase = "finish"
    elif t_loop0:                                    # converting
        done_s = prep + loop_e * min(1.0, max(0.0, j.progress))
        left = (est.eta() if est is not None and est.eta() is not None else loop_e * (1 - j.progress)) + fin
        phase = "encode"
    else:                                            # preparing
        el = now - (t_prep0 or now)
        if j.status == "paused" and j.progress > 0:
            el = E["prep"]
        done_s = min(el, E["prep"] * 0.97)
        left = max(E["prep"] * 0.03, E["prep"] - el) + loop_e + fin
        phase = "prep"
    total = prep + loop_e + fin
    pct = max(0.0, min(0.999, done_s / max(1.0, total)))
    key = (t_prep0, j.id)                            # never move backwards within one attempt
    mx = getattr(j, "_ov_max", None)
    if mx and mx[0] == key:
        pct = max(pct, mx[1])
    j._ov_max = (key, pct)
    return {"pct": pct, "left": left, "phase": phase}
