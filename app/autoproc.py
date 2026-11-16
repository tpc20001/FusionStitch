"""全自動處理: import a clip, let the program optimise everything, convert.

Per clip, with no manual settings:
  1. seam alignment       align.auto_align over 10/30/50/70/90 %
  2. stabilisation        gyro "smooth" (FlowState-like) + level horizon, when the clip has telemetry
  3. direction of travel  heading.estimate -> the 360 view starts facing the road
  4. lens colour          colortrack.analyse (time-varying colour match over the whole clip)
  5. outputs              from Settings → 全自動處理 (default: 360 YouTube); queued right after
Every automatic decision is saved in the clip project (so it can be reviewed
or tweaked later) and written to LOG\\auto_log.txt with the measured numbers.
"""
from __future__ import annotations

import json
import time

import core
import geometry as geo
import media
import naming
import projects

OUTPUTS = {   # key: (label, projection, extra opts)
    "equirect": ("360 全景（YouTube）", "equirect", {}),
    "flat": ("平面 16:9 1080p（朝行進方向）", "flat", {"size": "1920x1080"}),
    "vertical": ("直式 9:16 1080×1920（朝行進方向，短影音）", "flat", {"size": "1080x1920"}),
    "hyperlapse": ("10x 超級縮時 16:9（朝行進方向）", "flat", {"size": "1920x1080", "speed": 10.0}),
    "planet": ("小星球 2160×2160", "planet", {"size": "2160x2160"}),
}


def log(msg):
    core.log(msg, "auto_log.txt")


def analyse(clip: dict, status=None, cancel=None) -> dict:
    """Run every automatic step; returns the project dict (also saved) with a report."""
    def say(m):
        if status:
            status(m)
    if cancel and cancel():
        raise RuntimeError("已取消")
    folder = clip.get("folder", "")
    tf = media.timeline(clip["front"])
    w, h = tf.width, tf.height
    cal = (core.cfg("calibration") or {}).get(f"{w}x{h}")
    P = geo.merge_params(geo.default_params(w, h), cal) if cal else geo.default_params(w, h)
    d = tf.duration
    rep = {"clip": clip["name"], "duration": round(d, 1)}

    say("自動分析 1/4：對齊前後鏡頭接縫…")
    import align
    t0 = time.time()
    try:
        r = align.auto_align(clip, P, [d * p for p in (0.1, 0.3, 0.5, 0.7, 0.9)])
        if r["improved"]:
            P = r["params"]
        rep["align"] = f"接縫吻合度 {round((1 - r['before']) * 100)}% → {round((1 - r['after']) * 100)}%（{time.time() - t0:.0f} 秒）"
    except Exception as e:
        rep["align"] = f"略過：{e}"
    if cancel and cancel():
        raise RuntimeError("已取消")

    say("自動分析 1/4：固定支架視差校正…")
    seam_file = None
    try:
        import seamflow
        t0 = time.time()
        sf = seamflow.measure(clip, P, n_frames=40)
        seam_file = seamflow.save(sf, folder, clip["id"])
        st = sf["stats"]
        rep["seam"] = (f"機車／支架等固定物的接縫錯位：最大 {st['max_px'] * st['deg_per_px']:.1f}°，"
                       f"已校正（{time.time() - t0:.0f} 秒）")
    except Exception as e:
        rep["seam"] = f"略過：{e}"

    say("自動分析 2/4：讀取陀螺儀、判斷是否防震…")
    import gyro
    stab = gyro.default_stab()
    try:
        tel = gyro.load(clip["front"])
        if tel is not None and len(tel["gt"]) > 1000:
            stab["mode"] = "smooth"
            rep["stab"] = f"有陀螺儀資料（{len(tel['gt'])} 筆）→ 防震＋水平"
        else:
            rep["stab"] = "沒有陀螺儀資料 → 不防震"
    except Exception as e:
        rep["stab"] = f"沒有陀螺儀資料 → 不防震（{e}）"

    say("自動分析 3/4：判斷行進方向…")
    import heading
    t0 = time.time()
    try:
        hd = heading.estimate(clip, P)
        if hd["confidence"] >= 0.3:
            P["orient"]["yaw"] = round(hd["yaw"], 1)
            rep["heading"] = (f"行進方向在前鏡頭 {(-hd['yaw']) % 360:.0f}°（一致度 {hd['confidence']:.2f}，{hd['pairs']} 組畫面）"
                              f" → 畫面正前方朝 {hd['yaw']:.1f}°（{time.time() - t0:.0f} 秒）")
            # remember it: the camera usually stays on the same mount between clips
            hist = list(core.cfg("auto_headings") or [])[-9:] + [round(hd["yaw"], 1)]
            core.set_cfg("auto_headings", hist)
        else:
            known = _known_heading()
            if known is not None:
                P["orient"]["yaw"] = known
                rep["heading"] = (f"這段畫面移動不夠（一致度 {hd['confidence']:.2f}），沿用之前素材判斷出的方向 {known:.1f}°")
            else:
                rep["heading"] = f"方向不明確（一致度 {hd['confidence']:.2f}），保持預設正面（{'後鏡頭' if (core.cfg('default_view') or 'back') == 'back' else '前鏡頭'}）"
        rep["heading_raw"] = hd
    except Exception as e:
        rep["heading"] = f"略過：{e}"
    if cancel and cancel():
        raise RuntimeError("已取消")

    say("自動分析 4/4：前後鏡頭色彩隨時間匹配…")
    import colortrack
    t0 = time.time()
    track = None
    try:
        interval = 3.0 if d < 600 else 5.0
        track = colortrack.analyse(clip, P, interval=interval, smooth=6.0, cancel=cancel)
        rep["color"] = (f"每 {interval:g} 秒取樣 {track['samples']} 格：接縫色階差 {track['before']:.1f} → "
                        f"{track['after']:.1f}（{time.time() - t0:.0f} 秒）")
    except Exception as e:
        rep["color"] = f"略過：{e}"

    proj = projects.get(folder, clip["id"])
    import color
    gopro_mode = (core.cfg("color_mode") or "auto") == "gopro" and bool(core.gopro_lut_path())
    if gopro_mode:
        rep["tone"] = "原廠色彩模式：顏色在轉檔時套用原廠色彩（不另外自動調色）"
    if (core.cfg("auto_tone", True) and not gopro_mode and color.is_identity(proj.get("grade"))
            and not (cancel and cancel())):
        say("自動調色：分析整段的明暗與色彩…")
        try:
            import autotone
            t0 = time.time()
            tr = autotone.analyse(clip, P)
            g = color.merge(proj.get("grade"))
            g.update(tr["values"])
            proj["grade"] = g
            rep["tone"] = autotone.describe(tr) + f"（{time.time() - t0:.0f} 秒）"
        except Exception as e:
            rep["tone"] = f"略過：{e}"
    if track is not None and track.get("lum_t") and "auto_exposure" not in proj:
        proj["auto_exposure"] = float(core.cfg("auto_exposure_strength", 0.5))
        E = [colortrack.exposure_at(track, t, proj["auto_exposure"]) for t in track["lum_t"]]
        rep["exposure"] = f"自動亮度 強度 {proj['auto_exposure']:.2f}：補償 {min(E):+.2f} ～ {max(E):+.2f} EV"
    proj.update({"params": P, "stab": stab, "color_track": track, "color_track_on": track is not None,
                 "seamflow_file": seam_file, "seamflow_on": seam_file is not None,
                 "auto_report": rep, "auto_time": time.time()})
    projects.put(folder, clip["id"], proj)
    log(f"{clip['name']} ({folder})\n" + "\n".join(f"  {k}: {v}" for k, v in rep.items() if k != "heading_raw"))
    return proj


def _known_heading():
    """Circular median of the last confident headings (same camera mount)."""
    import math
    h = core.cfg("auto_headings") or []
    if not h:
        return None
    x = sum(math.cos(math.radians(v)) for v in h)
    y = sum(math.sin(math.radians(v)) for v in h)
    return round(math.degrees(math.atan2(y, x)), 1)


def export_opts(clip: dict, proj: dict, key: str, meta: dict) -> dict:
    label, kind, extra = OUTPUTS[key]
    preset = core.cfg("auto_preset") or "youtube"
    speed = float(extra.get("speed", 1.0))
    out = str(naming.output_folder(clip.get("folder", "")) /
              naming.output_name(clip, meta, preset, kind, "mp4", speed))
    opts = {"preset": preset, "engine": "auto", "params": proj["params"], "audio": speed == 1.0,
            "output": out, "stab": proj.get("stab") if clip["kind"] == "video" else None,
            "color_track": proj.get("color_track") if proj.get("color_track_on") else None,
            "auto_exposure": float(proj.get("auto_exposure") or 0),
            "grade": proj.get("grade"), "patch": proj.get("patch"),
            "seamflow_file": proj.get("seamflow_file") if proj.get("seamflow_on") else None}
    if kind == "equirect":
        opts["projection"] = {"type": "equirect"}
    elif kind == "flat":
        opts["projection"] = {"type": "flat", "yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 100.0}
    else:
        opts["projection"] = {"type": "planet", "yaw": 0.0, "pitch": 0.0, "roll": 0.0, "fov": 260.0}
    opts.update({k: v for k, v in extra.items()})
    if speed != 1.0 and opts.get("stab"):
        opts["stab"] = {**opts["stab"], "smooth": 3.0}       # smoother heading for time-lapse
    return opts
