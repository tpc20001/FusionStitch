"""Phone version of the desktop program (same pages as the sidebar): 素材庫, 轉檔佇列, 成品,
電腦效能, LOG, 設定, 線上更新, 升級資訊. Layout follows TikLive's phone page.

Everything that touches the window (scan a folder, queue clips, full-auto) runs on the Qt
thread through bus.uiCall; reads are plain data. Served by webremote (same PIN / login).
"""
from __future__ import annotations

import json
import os
import string
import threading
import time
from pathlib import Path

import core


# ---------------------------------------------------------------- helpers
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance()


def _win():
    return getattr(_app(), "_fs_window", None)


def _queue():
    return getattr(_app(), "_fs_queue", None)


def ui(fn, timeout=30.0):
    """Run fn on the Qt thread and return its result."""
    w = _win()
    if w is None:
        raise RuntimeError("程式視窗還沒準備好")
    box, ev = {}, threading.Event()

    def run():
        try:
            box["r"] = fn()
        except Exception as e:                        # report, never crash the UI
            box["e"] = e
        finally:
            ev.set()
    w.bus.uiCall.emit(run)
    if not ev.wait(timeout):
        raise RuntimeError("電腦程式忙碌中，請稍後再試")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def _fmt_time(meta):
    from naming import created_parts
    d, t = created_parts(meta)
    return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}" if d != "00000000" else ""


# ---------------------------------------------------------------- 素材庫
def library() -> dict:
    w = _win()
    if w is None:
        return {"folder": "", "clips": []}
    import jobs
    import projects
    lib = w.library
    pkey = core.cfg("preset") or "youtube"
    presets = jobs.all_presets()
    preset = presets.get(pkey, jobs.PRESETS["youtube"])
    out = []
    for cid, c in list((w.clips or {}).items()):
        meta = (w.meta or {}).get(cid) or {}
        try:
            st = lib.clip_status(c)
        except Exception:
            st = ""
        outs, sizes = [], {}
        try:
            outs = [p for p in lib.outputs(c)[0] if p.lower().endswith(".mp4")]
            for p in outs:
                try:
                    sizes[os.path.basename(p)] = os.path.getsize(p)
                except OSError:
                    pass
        except Exception:
            pass
        est = 0.0
        if c["kind"] == "video" and meta.get("duration"):
            try:
                wd = int(preset.get("width") or min(5376, preset.get("max_width") or 5376))
                est = jobs.estimate_bytes(preset, wd, wd // 2, float(meta.get("fps") or 29.97), float(meta["duration"]))
            except Exception:
                est = 0.0
        out.append({"id": cid, "name": c["name"], "kind": c["kind"], "num": c["num"], "complete": c["complete"],
                    "time": _fmt_time(meta), "duration": meta.get("duration") or 0, "chapters": len(c["front"]),
                    "size": meta.get("size") or 0, "outputs": sizes, "estimate": est, "wav": bool(c.get("wav")),
                    "status": st, "edited": bool(w.folder and projects.has_edits(w.folder, cid)),
                    "thumb": bool(c.get("thm"))})
    return {"folder": w.folder or "", "clips": out, "preset": pkey,
            "presets": [{"key": k, "label": p.get("label", k)} for k, p in presets.items()]}


def clip_thumb(cid: str) -> bytes | None:
    w = _win()
    c = (w.clips or {}).get(cid) if w else None
    p = (c or {}).get("thm")
    if p and os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    return None


_SRC = {}


def _shot_of(j) -> str:
    """拍攝時間 '2026-07-25 21:20'（從成品檔名 20260725_212041_… 來）."""
    import re
    m = re.match(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", os.path.basename(j.output or ""))
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else ""


def _src_of(j):
    v = _SRC.get(j.id)
    if v is None:
        try:
            v = sum(os.path.getsize(f) for f in (list(j.clip.get("front") or []) + list(j.clip.get("back") or [])) if f.lower().endswith(".mp4"))
        except OSError:
            v = 0
        _SRC[j.id] = v
    return v


def job_pair(jid: str) -> bytes | None:
    """排隊方格的小畫面：跟快速轉檔一樣的前／後鏡頭第 5 秒."""
    import framepair
    q = _queue()
    j = next((x for x in (q.jobs if q else []) if str(x.id) == str(jid)), None)
    if not j:
        return None
    p = framepair.wait(dict(j.clip), 6.0)
    if p:
        with open(p, "rb") as f:
            return f.read()
    return None


def clip_pair(cid: str) -> bytes | None:
    """Front + back frame at 5 s (made on demand, waits up to 25 s)."""
    import framepair
    w = _win()
    c = (w.clips or {}).get(cid) if w else None
    if not c:
        return None
    meta = (w.meta or {}).get(cid) or {}
    p = framepair.wait(dict(c, duration=meta.get("duration")), 6.0)
    if p:
        with open(p, "rb") as f:
            return f.read()
    return None


def scan(folder: str) -> dict:
    if not folder or not os.path.isdir(folder):
        return {"ok": False, "message": "找不到這個資料夾"}
    ui(lambda: _win().library.scan(folder))
    return {"ok": True, "message": "掃描中…"}


def queue_clips(ids: list, preset: str | None) -> dict:
    def go():
        w = _win()
        n = sum(1 for cid in ids if w.queue_clip(cid, preset or None))
        w.library.refresh_status()
        return n
    n = ui(go)
    return {"ok": True, "message": f"已加入 {n} 個轉檔工作"}


def queue_folders(dirs: list, preset: str | None) -> dict:
    """📂 several footage folders at once (each its own batch)."""
    dirs = [d for d in dirs if d and os.path.isdir(d)]
    if not dirs:
        return {"ok": False, "message": "沒有勾選資料夾"}
    res = ui(lambda: _win().queue_folders(dirs, preset or None))
    add = sum(a for a, _ in res.values())
    return {"ok": True, "message": f"📂 {len(dirs)} 個資料夾，共加入 {add} 個轉檔工作",
            "detail": [{"folder": f, "add": a, "skip": k} for f, (a, k) in res.items()]}


def queue_new(preset: str | None) -> dict:
    lib = library()
    ids = [c["id"] for c in lib["clips"] if c["status"] == "" and c["complete"]]
    return queue_clips(ids, preset)


def auto(ids: list) -> dict:
    def go():
        lib = _win().library
        return lib.auto_process(ids or None, confirm=False)
    n = ui(go) or 0
    return {"ok": bool(n), "message": f"已加入 {n} 段全自動處理" if n else "沒有可以處理的影片素材"}


def dirs(path: str) -> dict:
    """Folder browser for the phone (drives, then sub-folders)."""
    if not path:
        if os.name == "nt":
            ds = [f"{L}:\\" for L in string.ascii_uppercase if os.path.exists(f"{L}:\\")]
        else:
            ds = ["/"]
        try:
            import disks
            dinfo = [disks.info(x) for x in ds]
        except Exception:
            dinfo = []
        return {"path": "", "parent": None, "dirs": ds, "drives": dinfo}
    p = Path(path)
    if not p.is_dir():
        return {"path": path, "parent": None, "dirs": [], "error": "找不到資料夾"}
    try:
        subs = sorted((str(x) for x in p.iterdir() if x.is_dir() and not x.name.startswith(("$", "."))),
                      key=str.lower)
    except OSError as e:
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "dirs": [], "error": str(e)}
    try:
        import disks
        di = disks.info(str(p))
    except Exception:
        di = None
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "dirs": subs[:500], "drive": di}


def make_dir(parent: str, name: str) -> dict:
    """New folder from the phone's folder picker."""
    name = (name or "").strip().rstrip(". ")
    if not parent or not os.path.isdir(parent):
        return {"ok": False, "message": "上層資料夾不存在"}
    if not name or name in (".", "..") or any(ch in name for ch in '<>:"/\\|?*') or len(name) > 120:
        return {"ok": False, "message": '資料夾名稱不能空白，也不能有 \\ / : * ? " < > |'}
    p = Path(parent) / name
    try:
        existed = p.is_dir()
        p.mkdir(exist_ok=True)
    except OSError as e:
        return {"ok": False, "message": f"無法新增：{e}"}
    core.log(f"phone: new folder {p}")
    return {"ok": True, "path": str(p), "message": ("已有這個資料夾" if existed else "已新增資料夾") + f"：{name}"}


# ---------------------------------------------------------------- 轉檔佇列
def job_action(jid: int, action: str) -> dict:
    q = _queue()
    if not q:
        return {"ok": False}
    if action in ("pause", "resume", "cancel"):
        getattr(q, action)(jid)
    elif action in ("up", "down"):
        q.move(jid, -1 if action == "up" else 1)
    elif action == "top":
        q.move(jid, -10000)
    return {"ok": True}


def queue_action(action: str, value=None) -> dict:
    q = _queue()
    if not q:
        return {"ok": False}
    if action == "clear":
        q.clear_finished()
    elif action == "retry":
        n = 0
        for j in list(q.jobs):
            if j.status == "error":
                q.resume(j.id)
                n += 1
        return {"ok": True, "message": f"重新開始 {n} 個失敗的工作" if n else "沒有失敗的工作"}
    elif action == "after":
        core.set_cfg("after_queue", str(value or "none"))
    elif action in ("pause", "resume"):
        for j in list(q.jobs):
            if action == "pause" and j.status in ("running", "queued"):
                q.pause(j.id)
            elif action == "resume" and j.status == "paused":
                q.resume(j.id)
    return {"ok": True}


def set_route(cache_dir: str | None, dest: str | None, cache_dir2: str | None = None) -> dict:
    if cache_dir2 is not None:
        core.set_cfg("cache_dir2", cache_dir2.strip())
        core.set_cfg("relay_plan_done", True)
    if cache_dir is not None:
        core.set_cfg("relay_plan_done", True)            # chosen by hand: no automatic layout change
        core.set_cfg("cache_dir", cache_dir.strip())
        try:
            import staging
            staging.start()
        except Exception:
            pass
    if dest is not None:
        d = dest.strip()
        if d:
            core.set_cfg("out_folder", d)
            core.set_cfg("out_folder_mode", "custom")
        else:
            core.set_cfg("out_folder_mode", "beside")
        w = _win()
        if w is not None:
            w._out_cache = (0, None, [])
    return {"ok": True}


def _ov_of(j):
    try:
        import history
        return history.overall(j)
    except Exception:
        return None


def _copy_of(j):
    try:
        import staging
        if core.cfg("cache_dir") and j.status in ("running", "queued"):
            staging.start()                    # restarts the copier if it is gone
        return staging.job_copy(j)
    except Exception:
        return None


def _bid(j) -> str:
    try:
        import batches
        return batches.bid_of(j)
    except Exception:
        return ""


def batch_set(body: dict) -> dict:
    import batches
    ids = [str(x) for x in (body.get("ids") or []) if x]
    if not ids:
        return {"ok": False, "message": "沒有選批次"}
    dest = body.get("dest") if "dest" in body else None
    spares = body.get("spares") if "spares" in body else None
    for f in ([dest] if dest else []) + [x for x in (spares or []) if x]:
        if not os.path.isdir(f):
            return {"ok": False, "message": f"找不到資料夾：{f}"}
    r = batches.update(ids, dest=dest, spares=spares)
    msg = f"已套用到 {len(ids)} 個批次"
    if dest is not None:
        msg += f"，{r['moved']} 支排隊中的影片改存到新位置（正在轉的那支不變）"
    return {"ok": True, "message": msg}


def queue_state() -> dict:
    q = _queue()
    jobs_ = []
    for j in (list(q.jobs) if q else []):
        nb = getattr(j, "not_before", 0) or 0
        jobs_.append({"id": j.id, "title": j.title, "status": j.status, "progress": j.progress, "fps": j.fps,
                      "eta": j.eta, "message": j.message, "detail": getattr(j, "detail", ""),
                      "engine": getattr(j, "engine_desc", ""), "name": os.path.basename(j.output or ""),
                      "retry_in": max(0, int(nb - time.time())) if j.status == "queued" and nb > time.time() else 0,
                      "batch": _bid(j), "out_dir": os.path.dirname(j.output or ""), "copy": _copy_of(j), "ov": _ov_of(j),
                      "shot": _shot_of(j), "src": _src_of(j), "num": str(j.clip.get("num") or "")})
    relay = ""
    try:
        import staging
        relay = staging.status_text() if core.cfg("cache_dir") else ""
    except Exception:
        pass
    try:
        import batches
        bs = batches.summary()
    except Exception as e:
        core.log(f"batch summary: {e}")
        bs = {"batches": [], "drives": []}
    try:
        import staging
        ru = staging.relay_usage() if core.cfg("cache_dir") else None
    except Exception:
        ru = None
    try:
        import pace
        pst = pace.state()
        pst["label"] = pace.label(pst)
        pst.pop("idle", None)
    except Exception:
        pst = None
    try:
        import hsbridge
        hst = hsbridge.status()
    except Exception:
        hst = None
    try:
        afin = all_finish(list(q.jobs) if q else [])
    except Exception:
        afin = None
    try:
        import donefolder
        dfs = donefolder.status()
    except Exception:
        dfs = None
    try:
        import donefolder
        for b in bs.get("batches") or []:
            b["src"] = donefolder.batch_src(b.get("folder") or "")
    except Exception:
        pass
    return {"pace": pst, "hs": hst, "all_finish": afin, "donefolder": dfs, "relay_use": ru, "batches": bs["batches"], "drives": bs["drives"], "jobs": jobs_, "after": core.cfg("after_queue") or "none", "relay": relay,
            "cache_dir": core.cfg("cache_dir") or "", "cache_dir2": core.cfg("cache_dir2") or "",
            "dest": core.cfg("out_folder") if core.cfg("out_folder_mode") == "custom" else ""}


# ---------------------------------------------------------------- 成品
def _out_folder() -> Path | None:
    w = _win()
    if w is None or not w.folder:
        return None
    from naming import output_folder
    return output_folder(w.folder)


def outputs(sel: str = "") -> dict:
    """Videos of every folder on the 成品 list (or only folder `sel`, its index), newest first."""
    import outfolders
    fs = outfolders.all_()
    cur = outfolders.current()
    items, folders = [], []
    for i, f in enumerate(fs):
        n = 0
        if f.is_dir():
            try:
                for p in f.iterdir():
                    if not p.is_file() or p.suffix.lower() not in (".mp4", ".jpg") or ".joining" in p.name \
                            or ".tmp" in p.name or p.name.endswith(".moving"):
                        continue
                    n += 1
                    if sel not in ("", str(i)):
                        continue
                    st = p.stat()
                    items.append({"name": f"{i}|{p.name}", "file": p.name, "dir": i, "size": st.st_size,
                                  "mtime": st.st_mtime, "video": p.suffix.lower() == ".mp4"})
            except OSError:
                pass
        folders.append({"i": i, "path": str(f), "name": f.name or str(f), "count": n, "exists": f.is_dir(),
                        "current": bool(cur) and i == 0, "removable": not (bool(cur) and i == 0)})
    items.sort(key=lambda x: -x["mtime"])
    return {"folder": str(fs[int(sel)]) if sel.isdigit() and int(sel) < len(fs) else (str(cur or "")),
            "folders": folders, "sel": sel, "items": items[:800], "total": len(items)}


def output_path(name: str) -> Path | None:
    import outfolders
    return outfolders.resolve(name)


def output_thumb(name: str) -> bytes | None:
    p = output_path(name)
    if not p:
        return None
    try:
        import ffm
        import outputs_page
        info = ffm.probe(str(p)) if p.suffix.lower() == ".mp4" else {}
        tp = outputs_page.make_thumb(p, info)
        return tp.read_bytes() if tp else None
    except Exception:
        return None


def requeue_output(name: str) -> dict:
    p = output_path(name)
    if not p:
        return {"ok": False, "message": "找不到這個成品"}
    ok = ui(lambda: _win().requeue_output(str(p)))
    return {"ok": bool(ok), "message": "已加入重新轉檔" if ok else "找不到對應的素材（素材庫要開同一個資料夾）"}


def delete_output(name: str) -> dict:
    p = output_path(name)
    if not p:
        return {"ok": False, "message": "找不到這個成品"}
    import outfolders
    cur = outfolders.current()
    if not cur or os.path.normcase(str(p.parent)) != os.path.normcase(str(cur)):
        return {"ok": False, "message": "加入清單的資料夾只能看，不會刪除裡面的檔案"}
    try:
        os.remove(p)
    except OSError as e:
        return {"ok": False, "message": f"無法刪除（可能正在播放）：{e}"}
    return {"ok": True, "message": f"已刪除 {name}"}


# ---------------------------------------------------------------- LOG / 升級資訊
LOGS = ["job_log.txt", "auto_log.txt", "update_log.txt", "crash_log.txt", "app_log.txt"]


def log_tail(name: str, n: int = 400) -> dict:
    if name not in LOGS:
        name = "job_log.txt"
    p = core.LOG_DIR / name
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            txt = f.read().decode("utf-8", "replace")
    except OSError:
        txt = "（沒有這個紀錄檔）"
    return {"name": name, "names": LOGS, "text": "\n".join(txt.splitlines()[-n:])}


def changelog() -> list:
    try:
        return json.loads((core.APP_DIR / "CHANGELOG.json").read_text(encoding="utf-8"))[:40]
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- 設定
def _schema():
    import jobs
    import autoproc
    presets = [[k, p.get("label", k)] for k, p in jobs.all_presets().items()]
    sch = core.cfg("schedule") or {}
    return [
        {"group": "輸出", "items": [
            {"key": "preset", "label": "轉檔預設（加入佇列用）", "type": "select", "options": presets},
            {"key": "out_folder_mode", "label": "輸出位置", "type": "select",
             "options": [["beside", "素材資料夾裡的 stitched 子資料夾"], ["custom", "指定資料夾"]]},
            {"key": "out_folder", "label": "指定資料夾", "type": "dir"},
            {"key": "name_pattern", "label": "檔名規則", "type": "text",
             "hint": "{date} {time} {num} {preset}"},
            {"key": "segment_seconds", "label": "斷點續轉分段（秒）", "type": "int", "min": 5, "max": 600},
        ]},
        {"group": "轉檔", "items": [
            {"key": "parallel_jobs", "label": "同時處理幾支影片（錯開：一支準備／收尾時另一支用 GPU 轉檔）", "type": "select",
             "options": [[1, "1 支（一支接一支）"], [2, "2 支（預設）"], [3, "3 支"]]},
            {"key": "gpu_jobs", "label": "同時用 GPU 轉檔的影片數", "type": "select",
             "options": [[1, "1 支（預設，GPU 輪流用）"], [2, "2 支"]]},
            {"key": "parallel_segments", "label": "同時轉檔段數", "type": "select",
             "options": [[1, "1 段"], [2, "2 段（預設）"], [3, "3 段"]]},
            {"key": "cache_dir", "label": "中繼 SSD 資料夾（主要；空白＝不使用）", "type": "dir"},
            {"key": "cache_dir2", "label": "中繼 SSD 2（備援：主要那顆到上限時才用；建議系統碟放這裡）", "type": "dir"},
            {"key": "cache2_max_gb", "label": "中繼 SSD 2 最多先存多少素材（GB）", "type": "float", "min": 0, "max": 20000},
            {"key": "cache_reserve_gb", "label": "中繼 SSD 保留空間（GB）", "type": "float", "min": 1, "max": 2000},
            {"key": "cache_max_gb", "label": "中繼 SSD 最多先存多少素材（GB，0＝不限）", "type": "float", "min": 0, "max": 20000},
            {"key": "auto_resume", "label": "開啟程式時自動繼續沒轉完的工作", "type": "bool"},
            {"key": "auto_retry", "label": "失敗自動重試次數", "type": "int", "min": 0, "max": 5},
            {"key": "schedule.on", "label": "只在指定時段轉檔", "type": "bool", "value": bool(sch.get("on"))},
            {"key": "schedule.start", "label": "時段開始", "type": "time", "value": sch.get("start", "01:00")},
            {"key": "schedule.end", "label": "時段結束", "type": "time", "value": sch.get("end", "07:00")},
            {"key": "after_queue", "label": "全部轉完後", "type": "select",
             "options": [["none", "不做任何事"], ["sleep", "睡眠"], ["shutdown", "關機（60 秒後）"]]},
        ]},
        {"group": "全自動處理", "items": [
            {"key": "auto_preset", "label": "全自動處理用的預設", "type": "select", "options": presets},
            {"key": "auto_outputs", "label": "轉出哪些格式", "type": "multi",
             "options": [[k, v[0]] for k, v in autoproc.OUTPUTS.items()]},
            {"key": "auto_tone", "label": "自動調色", "type": "bool"},
            {"key": "auto_tone_all", "label": "一般轉檔也自動調色（沒手動調過的素材）", "type": "bool"},
            {"key": "auto_wb", "label": "自動白平衡", "type": "bool"},
            {"key": "auto_exposure_strength", "label": "自動亮度強度（0～1）", "type": "float", "min": 0, "max": 1},
            {"key": "auto_on_scan", "label": "掃描到新素材自動全自動處理", "type": "bool"},
        ]},
        {"group": "顏色與方向", "items": [
            {"key": "default_view", "label": "預設正面", "type": "select",
             "options": [["back", "後鏡頭（相機反裝）"], ["front", "前鏡頭"]],
             "hint": "360 畫面一開始朝哪個鏡頭；全自動分析判斷出行進方向時以行進方向為準"},
            {"key": "color_mode", "label": "轉檔顏色", "type": "select",
             "options": [["auto", "自動調色＋白平衡"], ["gopro", "原廠色彩"]],
             "hint": "原廠色彩要先在 工具 → 🎨 原廠色彩對標 算一次"},
            {"key": "gopro_lut_strength", "label": "原廠色彩強度（0～1）", "type": "float", "min": 0, "max": 1},
        ]},
        {"group": "聲音與視窗", "items": [
            {"key": "spatial_audio", "label": "360 空間音訊（實驗）", "type": "bool"},
            {"key": "minimize_to_tray", "label": "關閉視窗時縮到系統匣", "type": "bool"},
            {"key": "perf_float_start", "label": "啟動時顯示效能浮窗", "type": "bool"},
        ]},
        {"group": "手機網頁", "items": [
            {"key": "web_pin", "label": "PIN（4～20 字）", "type": "text"},
        ]},
    ]


DEFAULTS = {"default_view": "back", "color_mode": "gopro", "gopro_lut_strength": 1.0, "auto_resume": True, "auto_tone": True, "auto_tone_all": True, "auto_wb": True, "auto_retry": 2,
            "parallel_segments": 2, "parallel_jobs": 2, "gpu_jobs": 1, "segment_seconds": 30, "cache_reserve_gb": 20, "cache_max_gb": 50, "cache2_max_gb": 50,
            "auto_exposure_strength": 0.5, "after_queue": "none", "out_folder_mode": "beside",
            "auto_preset": "youtube", "preset": "youtube", "auto_outputs": ["equirect"]}


def settings() -> dict:
    groups = _schema()
    for g in groups:
        for it in g["items"]:
            if "value" not in it:
                v = core.cfg(it["key"])
                it["value"] = DEFAULTS.get(it["key"]) if v is None else v
    return {"groups": groups}


def set_setting(key: str, value) -> dict:
    allowed = {it["key"]: it for g in _schema() for it in g["items"]}
    it = allowed.get(key)
    if not it:
        return {"ok": False, "message": "這個設定不能從手機改"}
    t = it["type"]
    try:
        if t == "bool":
            value = bool(value)
        elif t == "int":
            value = max(it.get("min", -1e9), min(it.get("max", 1e9), int(value)))
        elif t == "float":
            value = max(it.get("min", -1e9), min(it.get("max", 1e9), float(value)))
        elif t == "multi":
            ok = {o[0] for o in it["options"]}
            value = [v for v in (value or []) if v in ok] or ["equirect"]
        elif t == "select":
            ok = {o[0] for o in it["options"]}
            if value not in ok:
                value = int(value) if str(value).isdigit() and int(value) in ok else None
            if value is None:
                return {"ok": False, "message": "選項不正確"}
        elif t == "time":
            h, m = (int(x) for x in str(value).split(":"))
            value = f"{h:02d}:{m:02d}"
        else:
            value = str(value).strip()
    except (TypeError, ValueError):
        return {"ok": False, "message": "格式不正確"}
    if key == "web_pin" and (not 4 <= len(value) <= 20 or any(ch.isspace() for ch in value)):
        return {"ok": False, "message": "PIN 要 4～20 個字、不能有空白"}
    if key.startswith("schedule."):
        sch = dict(core.cfg("schedule") or {})
        sch[key.split(".", 1)[1]] = value
        core.set_cfg("schedule", sch)
    else:
        core.set_cfg(key, value)
    if key in ("out_folder", "out_folder_mode"):
        w = _win()
        if w is not None:
            w._out_cache = (0, None, [])
    return {"ok": True, "message": "已儲存"}


# ---------------------------------------------------------------- TikLive-style widgets
def perf_data() -> dict:
    """📊 效能 (floating panel and page): current values + 3 minutes of 1-second history."""
    app = _app()
    pf = getattr(app, "_fs_perf", None)
    if pf is None:
        return {"error": "效能資料還沒準備好"}
    d, h = pf.snapshot()
    g = d.get("gpu") or {}
    n = max([len(v) for v in h.values()] or [0])

    def col(k):
        v = list(h.get(k) or [])
        return [None] * (n - len(v)) + v
    cols = {k: col(k) for k in h}
    hist = []
    for i in range(n):
        hist.append({"cpu": cols.get("cpu", [None] * n)[i], "ram": cols.get("ram", [None] * n)[i],
                     "gpu": cols.get("gpu", [None] * n)[i], "enc": cols.get("enc", [None] * n)[i],
                     "dec": cols.get("dec", [None] * n)[i], "fps": cols.get("fps", [None] * n)[i],
                     "disk": {k[5:]: cols[k][i] for k in cols if k.startswith("disk_")}})
    drives = [{"d": L, "busy": x.get("busy", 0), "rd": x.get("read", 0), "wr": x.get("write", 0)}
              for L, x in (d.get("disks") or {}).items()]
    import shutil as _sh
    for x in drives:                                   # each drive on its own: one bad drive must not hide the rest
        try:
            L = str(x["d"]).rstrip(":\\/")
            u = _sh.disk_usage(f"{L}:\\") if os.name == "nt" and len(L) == 1 else _sh.disk_usage(str(x["d"]) if os.name == "nt" else "/")
            x.update(total=u.total, free=u.free)
        except Exception:
            pass
    q = _queue()
    run = q.running() if q else None
    return {"time": time.strftime("%H:%M:%S"), "cpu": d.get("cpu"), "ram": d.get("ram_pct"),
            "ram_used": d.get("ram_used"), "ram_total": d.get("ram_total"), "cores": d.get("cores"),
            "gpus": [{"name": getattr(pf, "gpu_name", "") or "GPU", "use": g.get("gpu"), "enc": g.get("enc"),
                      "dec": g.get("dec"), "temp": g.get("temp"), "power": g.get("power"),
                      "power_limit": g.get("power_limit"), "mem_used": g.get("mem_used"),
                      "mem_total": g.get("mem_total")}] if g else [],
            "drives": drives, "hist": hist,
            "job": {"title": run.title, "fps": run.fps, "progress": run.progress, "eta": run.eta} if run else None}


def bg_status() -> dict:
    """🖥 背景: what the computer is doing (TikLive 後台狀態 format)."""
    q = _queue()
    conv, auto = [], []
    for j in (list(q.jobs) if q else []):
        if j.status not in ("running", "queued", "paused"):
            continue
        st = "run" if j.status == "running" else "wait"
        sub = (f"{j.fps:.1f} fps · 剩 {core.fmt_dur(j.eta)} · {getattr(j, 'detail', '')}" if st == "run" and j.eta
               else ("已暫停" if j.status == "paused" else (j.message or "排隊中")))
        item = {"id": str(j.id), "state": st, "text": f"#{j.id} {j.title} · {os.path.basename(j.output or '')}",
                "sub": sub, "pct": round(j.progress * 100, 1) if st == "run" else None, "cancel": True}
        (auto if j.opts.get("auto") else conv).append(item)
    relay = []
    try:
        import staging
        st_ = staging._state
        c, m = st_.get("copy"), st_.get("move")
        if c:
            relay.append({"id": "copy", "state": "run", "text": "預先複製素材 " + os.path.basename(c["src"]),
                          "sub": f"{core.fmt_bytes(c['done'])} / {core.fmt_bytes(c['total'])}",
                          "pct": round(c["done"] / max(1, c["total"]) * 100, 1)})
        if m:
            relay.append({"id": "move", "state": "run", "text": "搬運成品 " + os.path.basename(m["src"]),
                          "sub": f"{core.fmt_bytes(m['done'])} / {core.fmt_bytes(m['total'])}",
                          "pct": round(m["done"] / max(1, m["total"]) * 100, 1)})
        root = staging.root()
        if root:
            for mk in sorted((root / "done").glob("*.dest")):
                if not m or os.path.basename(m["src"]) != mk.name[:-5]:
                    relay.append({"id": mk.name, "state": "wait", "text": "等待搬運 " + mk.name[:-5], "sub": ""})
        note = st_.get("note") or ""
    except Exception:
        note = ""
    secs = [{"key": "conv", "title": "🎞 轉檔", "note": "一次轉一支；同一支會同時轉幾段", "items": conv},
            {"key": "auto", "title": "✦ 全自動分析", "note": "對齊、防震、方向、調色，分析完自動排轉檔", "items": auto},
            {"key": "relay", "title": "💽 中繼 SSD", "note": note or "素材先複製到 SSD、成品背景搬到成品資料夾",
             "items": relay}]
    from jobs import in_schedule
    guard = "" if in_schedule() else "不在轉檔時段，等時間到自動開始"
    return {"sections": secs, "guard": guard}


def bg_cancel(key: str, id_: str) -> dict:
    if key in ("conv", "auto") and str(id_).isdigit():
        q = _queue()
        if q:
            q.cancel(int(id_))
            return {"ok": True, "message": "已取消"}
    return {"ok": False, "message": "這一項不能取消"}


def badge() -> dict:
    q = _queue()
    js = list(q.jobs) if q else []
    return {"ok": True, "files": sum(1 for j in js if j.status == "done" and not j.opts.get("auto")),
            "pend": 0, "run": sum(1 for j in js if j.status == "running")}


def issues() -> dict:
    """📋 LOG 與問題: self check, recent problems, what the program did by itself."""
    import re as _re
    import shutil as _sh
    checks = []
    try:
        import ffm
        checks.append(["ok", "FFmpeg", ffm.version()[:60]])
        import jobs as _jobs
        enc = _jobs.encoders() if hasattr(_jobs, "encoders") else (ffm.encoders() if hasattr(ffm, "encoders") else {})
        nv = (enc.get("hevc_nvenc") or {}).get("ok")
        checks.append(["ok" if nv else "warn", "NVENC 顯示卡編碼", "可以用" if nv else "不能用（會改用 CPU，比較慢）"])
    except Exception as e:
        checks.append(["bad", "FFmpeg", str(e)])
    for label, path in (("成品資料夾", str(_out_folder() or "")), ("中繼 SSD", core.cfg("cache_dir") or "")):
        if not path:
            continue
        try:
            u = _sh.disk_usage(path)
            pct = u.used / u.total * 100
            checks.append(["bad" if u.free < 20e9 else "warn" if pct > 90 else "ok", f"{label}空間",
                           f"剩 {core.fmt_bytes(u.free)}（已用 {pct:.0f}%）· {path}"])
        except OSError as e:
            checks.append(["bad", label, f"讀不到：{e}"])
    from jobs import in_schedule
    sch = core.cfg("schedule") or {}
    if sch.get("on"):
        checks.append(["ok" if in_schedule() else "warn", "轉檔時段",
                       f"{sch.get('start')}～{sch.get('end')}（{'現在可以轉' if in_schedule() else '現在不在時段內'}）"])
    errs, acts = [], []
    now = time.time()
    try:
        txt = (core.LOG_DIR / "job_log.txt").read_text(encoding="utf-8", errors="replace")[-600_000:]
    except OSError:
        txt = ""
    cur = None
    for line in txt.splitlines():
        m = _re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)", line)
        if m:
            try:
                t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            msg = m.group(2)
            cur = None
            if now - t > 7 * 86400:
                continue
            if "FAILED" in msg:
                cur = {"t": t, "title": msg[:160], "detail": ""}
                errs.append(cur)
            elif _re.search(r"retry|改存成|moved|空間不足|spatial audio skipped|auto tone|無聲音", msg):
                acts.append({"t": t, "auto": True, "msg": msg[:200]})
        elif cur is not None and line.strip() and len(cur["detail"]) < 600:
            cur["detail"] += line.strip()[-200:] + "\n"
    return {"checks": checks, "errors": errs[-30:][::-1], "actions": acts[-60:][::-1]}


def _export_dir() -> Path:
    d = core.LOG_DIR / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logx_make(minutes=None, t_from=None, t_to=None) -> dict:
    """📤 匯出 LOG: lines of every log in the time range + settings (no PIN) + queue, as one zip."""
    import re as _re
    import zipfile
    to = float(t_to) if t_to else time.time()
    fr = float(t_from) if t_from else to - float(minutes or 30) * 60
    name = time.strftime("FS_LOG_%Y%m%d_%H%M", time.localtime(fr)) + time.strftime("-%H%M.zip", time.localtime(to))
    out = _export_dir() / name
    lines = 0
    summary = [f"FusionStitch V{core.APP_VERSION} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(fr))} ～ "
               f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(to))}"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(core.LOG_DIR.glob("*.txt")):
            keep, on = [], False
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = _re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]", line)
                if m:
                    try:
                        t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                        on = fr <= t <= to
                    except ValueError:
                        pass
                if on:
                    keep.append(line)
            if keep:
                z.writestr(f.name, "\n".join(keep))
                lines += len(keep)
                summary.append(f"{f.name}：{len(keep)} 行" + (f"，失敗 {sum('FAILED' in x for x in keep)} 次"
                                                             if f.name == "job_log.txt" else ""))
        st = {k: v for k, v in dict(core.settings()).items() if k not in ("web_pin", "web_token")}
        z.writestr("settings.json", json.dumps(st, ensure_ascii=False, indent=1))
        try:
            z.writestr("queue.json", core.QUEUE_FILE.read_text(encoding="utf-8"))
        except OSError:
            pass
        z.writestr("00_摘要.txt", "\n".join(summary))
    return {"name": name, "bytes": out.stat().st_size, "lines": lines, "summary": "\n".join(summary),
            "from": time.strftime("%m/%d %H:%M", time.localtime(fr)), "to": time.strftime("%m/%d %H:%M", time.localtime(to))}


def logx_list() -> dict:
    fs = sorted(_export_dir().glob("FS_LOG_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    return {"items": [{"name": p.name, "bytes": p.stat().st_size, "t": p.stat().st_mtime} for p in fs]}


def logx_file(name: str) -> bytes | None:
    if not name or "/" in name or "\\" in name or not name.startswith("FS_LOG_"):
        return None
    p = _export_dir() / name
    return p.read_bytes() if p.is_file() else None


def changelog_full() -> dict:
    items = changelog()
    seen = core.cfg("chv_seen")
    if not seen:                                  # first time: everything counts as read
        seen = core.APP_VERSION
        core.set_cfg("chv_seen", seen)
    return {"current": core.APP_VERSION, "seen": seen, "items": items}


def update_status() -> dict:
    return {"current": core.APP_VERSION}


def rollback_list() -> dict:
    import updater
    items = []
    for p in updater.backups()[:12]:
        v = updater.backup_version(p)
        notes = []
        try:
            log = json.loads((p / "CHANGELOG.json").read_text(encoding="utf-8"))
            notes = next((e.get("items", []) for e in log if str(e.get("version")) == v), [])
        except (OSError, ValueError):
            pass
        items.append({"id": p.name, "version": v, "older": core.version_tuple(v) < core.version_tuple(core.APP_VERSION),
                      "time": time.strftime("%m/%d %H:%M", time.localtime(p.stat().st_mtime)), "notes": notes[:8]})
    return {"current": core.APP_VERSION, "pw": False, "items": items}


def rollback_apply(id_: str) -> dict:
    import updater
    p = next((x for x in updater.backups() if x.name == id_), None)
    if not p:
        return {"ok": False, "message": "找不到這個備份"}
    v = updater.backup_version(p)

    def go():
        w = _win()
        cur = updater.backup_current(f"before_rollback_to_V{v}")
        updater.restore(p)
        updater.log_update(f"rollback (phone) V{core.APP_VERSION} -> V{v} ({p.name})")
        w.hot_reload(backup=cur, announce={"from": core.APP_VERSION, "to": v, "items": ["退回舊版"]})
    from PySide6.QtCore import QTimer
    ui(lambda: QTimer.singleShot(300, go))
    return {"ok": True, "to": v, "message": f"退回 V{v} 中"}


# ---------------------------------------------------------------- router
# ---------------------------------------------------------------- 快速轉檔
def quick_state() -> dict:
    import folders
    import history
    import quick_page
    w = _win()
    folder = (w.folder if w else "") or folders.last("footage")
    qp = getattr(w, "quick_page", None) if w else None
    bo = getattr(qp, "_bo", None) if qp is not None else None
    if bo:
        folder = bo                               # 瀏覽上層資料夾中：不顯示舊資料夾的影片
    d = dirs(folder) if folder else dirs("")
    lib = library()
    if bo:
        lib = {**lib, "clips": []}
    q = _queue()
    act = [j for j in (q.jobs if q else []) if j.status in ("running", "queued", "paused")]
    sched = history.schedule(act)
    jobs_ = [{"id": j.id, "title": j.title, "status": j.status, "progress": j.progress, "fps": j.fps,
              "name": os.path.basename(j.output or ""), "detail": getattr(j, "detail", "") or j.message,
              "finish": sched.get(j.id), "eta_ready": bool(getattr(j, "eta_at", None))} for j in act]
    ad = quick_page.advice(folder)
    clips_ = [c for c in lib["clips"] if c["kind"] == "video"]
    extra = clip_extras([c["id"] for c in clips_], lib.get("preset"), ad.get("out") or "")
    for c in clips_:
        c.update(extra["clips"].get(c["id"], {}))
    try:
        import disks
        odrv = disks.info(ad["out"]) if ad.get("out") else None
    except Exception:
        odrv = None
    return {"est_basis": extra["basis"], "out_drive": odrv, "folder": folder, "parent": d.get("parent"), "dirs": [x for x in d.get("dirs", [])
                                                                  if os.path.basename(x).lower() != "stitched"],
            "recent": folders.recent(), "clips": clips_,
            "preset": lib.get("preset"), "presets": lib.get("presets", []),
            "out_mode": core.cfg("out_folder_mode") or "beside", "out": ad["out"], "out_fixed": core.cfg("out_folder") or "",
            "tip": ad["tip"], "level": ad["level"], "relay_on": ad["relay_on"], "relay": ad["relay"], "jobs": jobs_,
            "all_finish": all_finish(act)}


def all_finish(act) -> dict | None:
    """⏱ 預計幾點全部完成（在跑＋排隊；暫停的不算）."""
    import history
    js = [j for j in act if j.status in ("running", "queued")]
    if not js:
        return None
    sch = history.schedule(js)
    ts = [t for t in sch.values() if t]
    if not ts:
        return None
    return {"at": max(ts), "n": len(js), "partial": len(ts) < len(js)}


def clip_extras(ids: list, preset: str | None, out_dir: str | None = None) -> dict:
    """Per clip: picture state, problem, original size, expected output size and ✓ converted before."""
    import donemark
    import framepair
    import history
    import problems
    w = _win()
    if out_dir is None:
        try:
            import quick_page
            out_dir = quick_page.advice(w.folder)["out"] if w and w.folder else ""
        except Exception:
            out_dir = ""
    dfs = donemark.folders_for(out_dir or "")
    allc = (w.clips or {}) if w else {}
    rows, items = {}, []
    for cid in ids:
        c = allc.get(cid)
        if not c:
            continue
        meta = (w.meta or {}).get(cid) or {}
        cc = dict(c, duration=meta.get("duration"))
        framepair.request(cc)                       # in list order, one at a time
        pr = problems.check(c, meta)
        src = history.source_bytes(c)
        rows[cid] = {"pair": framepair.state(cc), "problem": pr, "src": src}
        try:
            rows[cid]["done"] = donemark.find(c, meta, src, dfs)
        except Exception:
            rows[cid]["done"] = None
        fb = 0.0
        try:
            import jobs
            presets = jobs.all_presets()
            pre = presets.get(preset or "youtube", jobs.PRESETS["youtube"])
            if meta.get("duration"):
                wd = int(pre.get("width") or min(5376, pre.get("max_width") or 5376))
                fb = jobs.estimate_bytes(pre, wd, wd // 2, float(meta.get("fps") or 29.97), float(meta["duration"]))
        except Exception:
            pass
        items.append((cid, {"src": src, "fallback": fb}))
    be = history.batch_estimate([x for _c, x in items], preset)
    st = be["stats"]
    for (cid, it), m in zip(items, be["per"]):
        if st.get("n", 0) >= 3 and it["src"]:
            rows[cid].update(est=m, est_lo=it["src"] * st["lo"], est_hi=it["src"] * st["hi"])
        else:
            rows[cid].update(est=m, est_lo=m * 0.75, est_hi=m * 1.35)
    return {"clips": rows, "basis": be["basis"]}


def problem_action(cid: str, action: str) -> dict:
    import problems
    w = _win()
    c = (w.clips or {}).get(cid) if w else None
    if not c:
        return {"ok": False, "message": "找不到這支影片"}
    meta = (w.meta or {}).get(cid) or {}
    if action == "mark":
        problems.mark(c)
        return {"ok": True, "message": f"{c['name']} 已移到問題檔案"}
    if action == "release":
        problems.release(c)
        return {"ok": True, "message": f"{c['name']} 已移回影片清單"}
    if action == "recheck":
        problems.recheck(dict(c, duration=meta.get("duration")))
        return {"ok": True, "message": f"重新讀取 {c['name']}…"}
    return {"ok": False}


def quick_open(path: str) -> dict:
    import folders
    if not path or not os.path.isdir(path):
        return {"ok": False, "message": "找不到這個資料夾"}
    def go():
        w = _win()
        qp = getattr(w, "quick_page", None)
        if qp is not None:
            qp.open_folder(path)                  # 上層資料夾只列子資料夾，不讀全部影片
        else:
            folders.remember("footage", path)
            w.library.scan(path)
    ui(go)
    return {"ok": True}


def quick_out(mode: str, path: str) -> dict:
    if mode == "custom":
        if not path or not os.path.isdir(path):
            return {"ok": False, "message": "找不到這個資料夾"}
        core.set_cfg("out_folder", path)
        import folders
        folders.remember("output", path)
    core.set_cfg("out_folder_mode", "custom" if mode == "custom" else "beside")
    w = _win()
    if w is not None:
        w._out_cache = (0, None, [])
    return {"ok": True}


def quick_ssd(on: bool) -> dict:
    import quick_page
    d = quick_page.set_relay(bool(on))
    if on and not d:
        return {"ok": False, "message": "找不到 SSD，請在電腦上選中繼資料夾"}
    return {"ok": True, "message": ("SSD 加速已開啟：" + d) if on else "SSD 加速已關閉"}


def handle(method: str, path: str, query: dict, body: dict, remote) -> tuple:
    """-> (code, content-type, bytes) or None when the path is not ours."""
    def J(o, code=200):
        return code, "application/json; charset=utf-8", json.dumps(o, ensure_ascii=False).encode()
    try:
        if method == "GET":
            if path == "/api/quick":
                return J(quick_state())
            if path == "/api/library":
                return J(library())
            if path == "/api/queue":
                return J(queue_state())
            if path == "/api/cloud":
                import cloudupd
                return J(cloudupd.status())
            if path == "/api/outcheck":
                import outcheck
                return J(outcheck.status())
            if path == "/api/hs":
                import hsbridge
                return J(hsbridge.status())
            if path == "/api/colortag":
                import colortag
                return J(colortag.status())
            if path == "/api/outputs":
                return J(outputs(str(query.get("dir", "") or "")))
            if path == "/api/dirs":
                return J(dirs(query.get("path", "")))
            if path == "/api/log":
                return J(log_tail(query.get("name", "job_log.txt")))
            if path == "/api/changelog":
                return J(changelog())
            if path == "/api/settings":
                return J(settings())
            if path == "/api/clippair":
                b = clip_pair(query.get("id", ""))
                return (200, "image/jpeg", b) if b else (404, "text/plain", b"")
            if path == "/api/jobpair":
                b = job_pair(query.get("id", ""))
                return (200, "image/jpeg", b) if b else (404, "text/plain", b"")
            if path == "/api/clipthumb":
                b = clip_thumb(query.get("id", ""))
                return (200, "image/jpeg", b) if b else (404, "text/plain", b"")
            if path == "/api/cm/status":
                import colormatch
                st = colormatch.status()
                w = _win()
                outs = [o["name"] for o in outputs()["items"] if o["video"]]
                guess = None
                if w and outs:
                    guess = {o: colormatch.guess_clip(o, w.clips or {}) for o in outs}
                return J({**st, "outputs": outs, "guess": guess})
            if path == "/api/cm/preview":
                import colormatch
                p_ = colormatch.lut_dir() / "gopro_match_preview.jpg"
                return (200, "image/jpeg", p_.read_bytes()) if p_.exists() else (404, "text/plain", b"")
            if path == "/api/perf":
                return J(perf_data())
            if path == "/api/bg":
                return J(bg_status())
            if path == "/api/badge":
                return J(badge())
            if path == "/api/issues":
                return J(issues())
            if path == "/api/logx/make":
                return J(logx_make(query.get("min"), query.get("from"), query.get("to")))
            if path == "/api/logx/list":
                return J(logx_list())
            if path == "/api/logx/dl":
                b = logx_file(query.get("f", ""))
                return (200, "application/zip", b) if b else (404, "text/plain", b"")
            if path == "/api/changelog2":
                return J(changelog_full())
            if path == "/api/update/status":
                return J(update_status())
            if path == "/api/update/rollback":
                return J(rollback_list())
            if path == "/api/outthumb":
                b = output_thumb(query.get("name", ""))
                return (200, "image/jpeg", b) if b else (404, "text/plain", b"")
            return None
        if path == "/api/quick/open":
            return J(quick_open(str(body.get("path") or "")))
        if path == "/api/quick/out":
            return J(quick_out(str(body.get("mode") or ""), str(body.get("path") or "")))
        if path.startswith("/api/cloud/"):
            import cloudupd
            act_ = path.rsplit("/", 1)[1]
            if act_ == "check":
                threading.Thread(target=lambda: cloudupd.check(manual=True), daemon=True, name="FSCloudChk").start()
                return J({"ok": True, "message": "檢查中…"})
            if act_ == "update":
                return J(cloudupd.update(force=bool(body.get("force"))))
            if act_ == "skip":
                return J(cloudupd.skip())
            if act_ == "cancel":
                return J(cloudupd.cancel())
            if act_ == "on":
                core.set_cfg("cloud_on", bool(body.get("on")))
                return J({"ok": True, "message": "自動檢查更新：" + ("開" if body.get("on") else "關")})
            return J({"ok": False, "message": "不認得的動作"})
        if path == "/api/outcheck/scan":
            import outcheck
            import outfolders
            d = str(body.get("dir") or "")
            return J(outcheck.scan_async([Path(d)] if d else outfolders.all_()))
        if path == "/api/outcheck/run":
            import outcheck
            return J(outcheck.run_fix(list(body.get("items") or [])))
        if path == "/api/donefolder/rename":
            import donefolder
            return J(donefolder.rename_batch(str(body.get("folder") or ""), bool(body.get("dry", True)), bool(body.get("force"))))
        if path == "/api/donefolder":
            core.set_cfg("src_done_rename", bool(body.get("on")))
            return J({"ok": True, "message": "原檔資料夾轉完自動改名：" + ("開" if body.get("on") else "關")})
        if path == "/api/hs":
            import hsbridge
            return J(hsbridge.act(body))
        if path == "/api/pace":
            if body.get("mode") in ("full", "manual", "smart"):
                core.set_cfg("pace_mode", body["mode"])
            if body.get("pct") is not None:
                core.set_cfg("pace_pct", max(10, min(100, int(body["pct"]))))
            import pace
            pace._cache["t"] = 0
            st = pace.state()
            core.log(f"pace: {pace.label(st)}")
            return J({"ok": True, "message": "轉檔速度：" + pace.label(st)})
        if path == "/api/batch/action":
            import batches
            return J(batches.action([str(x) for x in (body.get("ids") or [])], str(body.get("action") or "")))
        if path == "/api/diag":
            import diag
            return J(diag.report())
        if path == "/api/batch/set":
            return J(batch_set(body))
        if path == "/api/problem":
            return J(problem_action(str(body.get("id") or ""), str(body.get("action") or "")))
        if path == "/api/mkdir":
            return J(make_dir(str(body.get("parent") or ""), str(body.get("name") or "")))
        if path == "/api/quick/ssd":
            return J(quick_ssd(bool(body.get("on"))))
        if path == "/api/library/scan":
            return J(scan(body.get("folder") or (_win().folder if _win() else "")))
        if path == "/api/library/queue":
            return J(queue_clips(list(body.get("ids") or []), body.get("preset")))
        if path == "/api/queue_folders":
            return J(queue_folders([str(x) for x in (body.get("dirs") or [])], body.get("preset")))
        if path == "/api/library/queue_new":
            return J(queue_new(body.get("preset")))
        if path == "/api/library/auto":
            return J(auto(list(body.get("ids") or [])))
        if path.startswith("/api/job/"):
            _, _, _, jid, action = path.split("/")
            return J(job_action(int(jid), action))
        if path.startswith("/api/queue/"):
            return J(queue_action(path.rsplit("/", 1)[1], body.get("value")))
        if path == "/api/colortag/scan":
            import colortag
            import outfolders
            d = str(body.get("dir") or "")
            return J(colortag.scan_async([Path(d)] if d else outfolders.all_()))
        if path == "/api/colortag/run":
            import colortag
            return J(colortag.start([str(x) for x in (body.get("paths") or [])]))
        if path == "/api/outfolders/add":
            import outfolders
            return J(outfolders.add(str(body.get("path") or "")))
        if path == "/api/outfolders/remove":
            import outfolders
            return J(outfolders.remove(str(body.get("path") or "")))
        if path == "/api/outputs/requeue":
            return J(requeue_output(str(body.get("name") or "")))
        if path == "/api/outputs/delete":
            return J(delete_output(str(body.get("name") or "")))
        if path == "/api/bg/cancel":
            return J(bg_cancel(str(body.get("key")), str(body.get("id"))))
        if path == "/api/changelog/seen":
            core.set_cfg("chv_seen", core.APP_VERSION)
            return J({"ok": True})
        if path == "/api/update/rollback":
            return J(rollback_apply(str(body.get("id") or "")))
        if path == "/api/cm/start":
            import colormatch
            w = _win()
            p_ = output_path(str(body.get("ref") or ""))
            c_ = (w.clips or {}).get(str(body.get("clip") or "")) if w else None
            if not p_ or not c_:
                return J({"ok": False, "message": "請選原廠成品和同一段素材"})
            c_ = dict(c_); c_["folder"] = w.folder
            return J(colormatch.start(c_, w.folder, str(p_)))
        if path == "/api/cm/cancel":
            import colormatch
            colormatch._job["cancel"] = True
            return J({"ok": True, "message": "取消中"})
        if path == "/api/route":
            return J(set_route(body.get("cache_dir"), body.get("dest"), body.get("cache_dir2")))
        if path == "/api/settings":
            return J(set_setting(str(body.get("key")), body.get("value")))
        return None
    except Exception as e:
        return J({"ok": False, "message": str(e)}, 200)
