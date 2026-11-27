"""轉檔批次: every footage folder added to the queue is one batch (title = folder name).

A batch has its own output folder and up to two spare folders (備用). Settings live in
Package/batches.json; a job knows its batch by opts["batch"].

Disk space:
  * the queue page shows, per batch and per output drive, how much the remaining jobs will
    write and how much room is left afterwards (warning only, converting goes on);
  * when a finished file does not fit in the batch's folder, it goes to spare 1, then spare 2;
    with no spare that fits, the job pauses (nothing is lost) until there is room.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

import core

FILE = core.PACKAGE_DIR / "batches.json"
_lock = threading.RLock()
_cache: dict | None = None
MARGIN = 1.5e9          # keep this much free on an output drive


def _all() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = json.loads(FILE.read_text(encoding="utf-8"))
                if not isinstance(_cache, dict):
                    _cache = {}
            except (OSError, ValueError):
                _cache = {}
        return _cache


def _save():
    with _lock:
        try:
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_all(), ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, FILE)
        except OSError:
            pass


def _queue():
    try:
        from PySide6.QtWidgets import QApplication
        return getattr(QApplication.instance(), "_fs_queue", None)
    except Exception:
        return None


def _jobs():
    q = _queue()
    return list(q.jobs) if q else []


ACTIVE = ("running", "queued", "paused", "error")


def get(bid: str) -> dict:
    return dict(_all().get(bid) or {})


def bid_of(job) -> str:
    b = job.opts.get("batch")
    if b:
        return b
    f = job.clip.get("folder") or str(Path(job.output or "").parent)
    return "f:" + os.path.normcase(f)


def assign(folder: str) -> str:
    """Batch for jobs added from this footage folder: the folder's open batch (one with jobs
    still to do), or a new one."""
    key = os.path.normcase(os.path.abspath(folder or ""))
    with _lock:
        open_ids = {bid_of(j) for j in _jobs() if j.status in ACTIVE}
        for bid, b in sorted(_all().items(), key=lambda kv: -kv[1].get("created", 0)):
            if b.get("key") == key and bid in open_ids:
                return bid
        n = int(time.time() * 1000)
        while f"b{n}" in _all():
            n += 1
        bid = f"b{n}"
        prev = next((b for b in sorted(_all().values(), key=lambda b: -b.get("created", 0))), None)
        _all()[bid] = {"title": Path(folder).name or folder, "folder": folder, "key": key, "created": time.time(),
                       "dest": "", "spares": list((prev or {}).get("spares") or ["", ""]) if core.cfg("batch_inherit_spares", True) else ["", ""]}
        # keep the file small: forget batches with no jobs left in the queue
        live = {bid_of(j) for j in _jobs()} | {bid}
        for k in [k for k in _all() if k not in live and time.time() - _all()[k].get("created", 0) > 30 * 86400]:
            _all().pop(k, None)
        _save()
        return bid


def dest_dir(job) -> Path:
    """Folder the batch's outputs go to (its own setting, else the program's output folder)."""
    import naming
    b = get(bid_of(job))
    if b.get("dest"):
        return Path(b["dest"])
    folder = job.clip.get("folder") or b.get("folder") or str(Path(job.output).parent.parent)
    return naming.output_folder(folder)



_DONE_SZ: dict = {}
_PEND: dict = {}


def _pending_to(drive: str) -> float:
    """（3 秒內重用）"""
    hit = _PEND.get(drive)
    if hit and time.time() - hit[0] < 3:
        return hit[1]
    v = _pending_to_now(drive)
    _PEND[drive] = (time.time(), v)
    return v


def _pending_to_now(drive: str) -> float:
    """Bytes waiting on the SSD relay to be moved to this drive."""
    n = 0.0
    try:
        import staging
        r = staging.root()
        if r is None:
            return 0.0
        ms = []
        for rr in (staging.roots() if hasattr(staging, "roots") else [r]):
            if (rr / "done").is_dir():
                ms += list((rr / "done").glob("*.dest"))
        for m in ms:
            try:
                d = m.read_text(encoding="utf-8").strip()
                if os.path.splitdrive(d)[0].upper() == drive.upper():
                    n += Path(str(m)[:-5]).stat().st_size
            except OSError:
                pass
    except Exception:
        pass
    return n


def free_of(folder: str) -> float | None:
    try:
        p = Path(folder)
        while not p.exists() and p.parent != p:
            p = p.parent
        return shutil.disk_usage(p).free - _pending_to(os.path.splitdrive(str(folder))[0])
    except OSError:
        return None


def choose(job, size: float) -> tuple[Path, str]:
    """Where a finished file of this size goes: (folder, note). Raises jobs.DiskFull when no
    folder of the batch has room."""
    main = dest_dir(job)
    cands = [("", str(main))] + [(f"備用 {i + 1}", s) for i, s in enumerate(get(bid_of(job)).get("spares") or []) if s]
    tried = []
    for label, folder in cands:
        fr = free_of(folder)
        if fr is None or fr >= size + MARGIN:
            note = "" if not label else f"（{main.anchor} 空間不足，改存到{label}：{folder}）"
            if label:
                core.log(f"job {job.id}: {main} full, using {label} {folder}", "job_log.txt")
            return Path(folder), note
        tried.append(f"{label or '成品資料夾'} {Path(folder).anchor} 剩 {core.fmt_bytes(max(0, fr))}")
    import jobs
    raise jobs.DiskFull(f"⚠ 成品資料夾空間不足：這支約 {core.fmt_bytes(size)}，" + "、".join(tried)
                        + "。已暫停（轉好的部分保留）；清出空間或在佇列的「批次設定」加備用資料夾後按「繼續」")


def update(bids: list, dest: str | None = None, spares: list | None = None) -> dict:
    """Change output folder / spares of batches. A new output folder applies to jobs not
    started yet (and paused ones whose partial work is not kept beside the old output)."""
    moved = 0
    with _lock:
        for bid in bids:
            b = _all().get(bid)
            if b is None:
                if not bid.startswith("f:"):
                    continue
                b = _all()[bid] = {"title": Path(bid[2:]).name, "folder": bid[2:], "key": bid[2:],
                                   "created": time.time(), "dest": "", "spares": ["", ""]}
            if spares is not None:
                sp = (list(spares) + ["", ""])[:2]
                b["spares"] = [str(s or "") for s in sp]
            if dest is not None:
                b["dest"] = str(dest or "")
        _save()
    if dest is not None:
        q = _queue()
        for j in _jobs():
            if bid_of(j) not in bids or j.status not in ("queued", "paused", "error"):
                continue
            if not j.output or j.opts.get("still") is not None or j.opts.get("auto"):
                continue
            if Path(j.output + ".parts").exists():
                continue                      # partial work is next to the old output: keep it there
            new = str(dest_dir(j) / Path(j.output).name)
            if new != j.output:
                j.output = j.opts["output"] = new
                moved += 1
        if q and moved:
            q.save()
    return {"ok": True, "moved": moved}


# ---------------------------------------------------------------- estimates for the queue page
def _job_bytes(j, stats: dict) -> float:
    """Expected output size of a job (actual size once done)."""
    if j.status == "done":                            # 完成的檔案大小不會變：記住，不用每次問硬碟
        v = _DONE_SZ.get(j.id)
        if v is None:
            try:
                v = _DONE_SZ[j.id] = float(os.path.getsize(j.output))
            except OSError:
                return 0.0
        return v
    import history
    src = 0
    try:
        src = history.source_bytes(j.clip)
    except Exception:
        pass
    st = stats.get(j.opts.get("preset"))
    if st is None:
        st = stats[j.opts.get("preset")] = history.ratio_stats(j.opts.get("preset"))
    if st.get("n", 0) >= 3 and src:
        span = 1.0
        if j.opts.get("start") is not None or j.opts.get("end") is not None:
            span = 0.5                        # trimmed: rough
        return src * st["mid"] * span / float(j.opts.get("speed") or 1.0)
    try:
        import jobs
        pre = jobs.all_presets().get(j.opts.get("preset") or "youtube") or jobs.PRESETS["youtube"]
        import media
        tl = media.timeline(j.clip["front"])
        wd = int(pre.get("width") or min(5376, pre.get("max_width") or 5376))
        return jobs.estimate_bytes(pre, wd, wd // 2, tl.fps, tl.duration) / float(j.opts.get("speed") or 1.0)
    except Exception:
        return src * 0.2


def summary() -> dict:
    """Batches in queue order with progress, time left, expected size and the room left on
    their output drives."""
    import disks
    import history
    js = _jobs()
    act = [j for j in js if j.status in ("running", "queued", "paused")]
    try:
        sched = history.schedule(act)
    except Exception:
        sched = {}
    stats: dict = {}
    groups: dict = {}
    order = []
    for j in js:
        b = bid_of(j)
        if b not in groups:
            groups[b] = []
            order.append(b)
        groups[b].append(j)
    now = time.time()
    per_drive_need: dict = {}               # drive -> bytes still to be written there
    out = []
    for b in order:
        L = groups[b]
        meta = get(b)
        title = meta.get("title") or Path(b[2:] if b.startswith("f:") else "").name or "其他"
        n = len(L)
        done = sum(1 for j in L if j.status == "done")
        def _ov(j):
            try:
                return history.overall(j)["pct"]
            except Exception:
                return j.progress
        prog = sum(1.0 if j.status == "done" else (_ov(j) if j.status in ("running", "paused") else 0.0)
                   for j in L if j.status != "cancelled")
        total_n = sum(1 for j in L if j.status != "cancelled")
        fin = [sched.get(j.id) for j in L if j.status in ("running", "queued")]
        unknown = any(f is None for f in fin)
        last = max([f for f in fin if f] or [0])
        sizes = {j.id: _job_bytes(j, stats) for j in L if j.status != "cancelled"}
        remain = sum(s * (1 - (j.progress if j.status in ("running", "paused") else 0))
                     for j in L for s in [sizes.get(j.id, 0)] if j.status in ("running", "queued", "paused"))
        try:
            dd = str(dest_dir(L[0]))
        except Exception:
            dd = ""
        drv = os.path.splitdrive(dd)[0].upper()
        per_drive_need[drv] = per_drive_need.get(drv, 0) + remain
        out.append({"id": b, "title": title, "folder": meta.get("folder") or (b[2:] if b.startswith("f:") else ""),
                    "dest": meta.get("dest") or "", "dest_dir": dd, "spares": (meta.get("spares") or ["", ""]) + [""] * 0,
                    "n": n, "done": done, "active": sum(1 for j in L if j.status in ("running", "queued", "paused")),
                    "errors": sum(1 for j in L if j.status == "error"),
                    "running": any(j.status == "running" for j in L),
                    "progress": prog / max(1, total_n), "finish": last or None, "left": max(0, last - now) if last else None,
                    "eta_partial": unknown, "size_total": sum(sizes.values()), "size_left": remain,
                    "jobs": [j.id for j in L],
                    "job_finish": {j.id: sched.get(j.id) for j in L if sched.get(j.id)},
                    "job_size": {j.id: sizes.get(j.id, 0) for j in L}})
    # room after everything still queued is written (per drive, all batches together)
    drives = {}
    for drv, need in per_drive_need.items():
        if not drv:
            continue
        inf = disks.info(drv + "\\")
        fr = (inf.get("free") or 0) - _pending_to(drv)
        drives[drv] = {"drive": drv + "\\", "kind": inf.get("kind", ""), "label": inf.get("label", ""),
                       "total": inf.get("total", 0), "free": fr, "need": need, "after": fr - need}
    for b in out:
        drv = os.path.splitdrive(b["dest_dir"])[0].upper()
        d = drives.get(drv)
        sp_free = 0.0
        for s in b["spares"]:
            if s:
                f = free_of(s)
                sp_free += max(0.0, f or 0)
        b["drive"] = d
        b["spare_info"] = []
        for s in b["spares"]:
            if s:
                inf = disks.info(s)
                b["spare_info"].append({"path": s, "drive": inf.get("drive", ""), "kind": inf.get("kind", ""),
                                        "free": max(0.0, free_of(s) or 0)})
        b["spare_free"] = sp_free
        b["spare_n"] = sum(1 for s in b["spares"] if s)
        if d and b["size_left"] > 0:
            short = d["after"] < MARGIN
            b["warn"] = ""
            if short:
                cover = d["after"] + sp_free >= MARGIN
                b["warn"] = (f"⚠ 容量可能不足：{d['drive']} 剩 {core.fmt_bytes(max(0, d['free']))}，"
                             f"佇列還要寫入約 {core.fmt_bytes(d['need'])}。轉檔會繼續；"
                             + ("真的放不下時會自動改存到備用資料夾" if cover else
                                ("備用資料夾可能也不夠，" if b["spare_n"] else "") + "真的放不下時會暫停，請先設定備用資料夾或清出空間"))
        else:
            b["warn"] = ""
    return {"batches": out, "drives": list(drives.values())}


def action(bids: list, what: str) -> dict:
    """取消／暫停／繼續 every job of these batches."""
    q = _queue()
    if q is None:
        return {"ok": False, "message": "佇列還沒準備好"}
    n = 0
    for j in list(q.jobs):
        if bid_of(j) not in bids:
            continue
        if what == "cancel" and j.status in ("running", "queued", "paused", "error"):
            q.cancel(j.id); n += 1
        elif what == "pause" and j.status in ("running", "queued"):
            q.pause(j.id); n += 1
        elif what == "resume" and j.status in ("paused", "error"):
            q.resume(j.id); n += 1
    word = {"cancel": "取消", "pause": "暫停", "resume": "繼續"}.get(what, what)
    core.log(f"batch {what}: {bids} ({n} jobs)", "job_log.txt")
    return {"ok": True, "message": f"已{word} {n} 支影片" + ("（正在轉的那支最多 20 秒內停下）" if what in ("cancel", "pause") else "")}
