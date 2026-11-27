"""SSD relay: source footage is copied ahead to a fast folder, converted there, and the
finished file is moved to the output folder in the background.

  HDD (素材) --prefetch--> SSD 中繼資料夾 --GPU 轉檔、合併--> SSD --背景搬運--> 成品資料夾

* Prefetch runs one file at a time (sequential reads are what an HDD is fast at), in queue
  order, for the running job first and then the next queued ones, as long as the SSD keeps
  `cache_reserve_gb` free and the relay stays under `cache_max_gb`.
* A job waits for its own files (status shows the copy progress); every other job's copy
  happens while the GPU is busy.
* Finished outputs are moved by a background thread, so the GPU starts the next job at once.
  The pending move is written next to the file (<name>.dest) and resumes after a restart.
* Copies of footage no queued / running / paused job needs any more are deleted.
Nothing changes when no relay folder is set.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from pathlib import Path

import core

CHUNK = 16 << 20


def _shared() -> dict:
    """State kept on the QApplication so a hot update does not start a second copier."""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None
    holder = app if app is not None else globals()
    d = getattr(holder, "_fs_staging", None) if app is not None else holder.get("_fs_staging")
    if d is None:
        d = {"state": {"copy": None, "move": None, "note": ""}, "started": False,
             "wake": threading.Event(), "lock": threading.RLock()}
        if app is not None:
            setattr(app, "_fs_staging", d)
        else:
            holder["_fs_staging"] = d
    return d


_S = _shared()
if core.cfg("cache_max_gb") is None:          # 中繼最多存 50 GB 的素材（之後再複製下一支）
    core.set_cfg("cache_max_gb", 50)
_lock = _S["lock"]
_state = _S["state"]
_wake = _S["wake"]


def root() -> Path | None:
    d = str(core.cfg("cache_dir") or "").strip()
    if not d:
        return None
    p = Path(d)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return p if p.is_dir() else None


def root2() -> Path | None:
    """Second relay SSD (備援): takes footage copies when the main one is at its limit."""
    d = str(core.cfg("cache_dir2") or "").strip()
    if not d or root() is None:
        return None
    p = Path(d)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if not p.is_dir() or os.path.normcase(str(p)) == os.path.normcase(str(root())):
        return None
    return p


def roots() -> list[Path]:
    return [r for r in (root(), root2()) if r is not None]


def _cap_of(r: Path) -> float:
    k = "cache_max_gb" if r == root() else "cache2_max_gb"
    return float(core.cfg(k) or 0) * 1e9


def _src_of(r: Path) -> Path:
    d = r / "src"
    d.mkdir(exist_ok=True)
    return d


def _work_of(r: Path) -> Path:
    d = r / "work"
    d.mkdir(exist_ok=True)
    return d


def _root_of(path) -> Path | None:
    pk = os.path.normcase(os.path.abspath(str(path)))
    for r in roots():
        if pk.startswith(os.path.normcase(os.path.abspath(str(r))) + os.sep):
            return r
    return None


def work_dir(out: str | None = None) -> Path | None:
    """Parts and the join go on the main relay (or where this output's parts already are)."""
    r = root()
    if r is None:
        return None
    if out:
        for rr in roots():
            if (rr / "work" / (Path(out).name + ".parts")).exists():
                return _work_of(rr)
    return _work_of(r)


def _src_dir() -> Path:
    d = root() / "src"
    d.mkdir(exist_ok=True)
    return d



def _staged_name(src: str) -> str:
    st = os.stat(src)
    tag = hashlib.sha1(f"{os.path.abspath(src).lower()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:10]
    return f"{tag}_{Path(src).name}"


def staged_find(src: str) -> Path | None:
    n = _staged_name(src)
    for r in roots():
        p = r / "src" / n
        if p.exists():
            return p
    return None


def staged_path(src: str) -> Path:
    """The copy of a footage file on a relay SSD (where it is, or where it would go on the main one)."""
    return staged_find(src) or (_src_dir() / _staged_name(src))


def _clip_files(clip: dict) -> list[str]:
    return list(clip.get("front") or []) + list(clip.get("back") or []) + list(clip.get("wavs") or [])


def _uses_footage(job) -> bool:
    o = job.opts
    return not o.get("auto") and o.get("still") is None and job.clip.get("kind") == "video"


def _copy(src: str, dst: Path, progress=None, cancel=None, tmp: Path | None = None):
    tmp = tmp or dst.with_name(dst.name + ".copying")
    total = os.path.getsize(src)
    done = 0
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while True:
            if cancel and cancel():
                fo.close()
                tmp.unlink(missing_ok=True)
                return False
            t_c = time.time()
            b = fi.read(CHUNK)
            if not b:
                break
            fo.write(b)
            try:
                import pace                              # 效能（降速）: slower copy while slowed down
                pace.copy_rest(len(b), time.time() - t_c)
            except Exception:
                pass
            done += len(b)
            if progress:
                progress(done, total)
        fo.flush()
        os.fsync(fo.fileno())                         # 斷電保護: on the disk before it counts
    if os.path.getsize(tmp) != total:
        tmp.unlink(missing_ok=True)
        raise OSError(f"複製不完整：{src}")
    os.replace(tmp, dst)
    return True


def _dir_size(p: Path) -> int:
    n = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                n += f.stat().st_size
        except OSError:
            pass
    return n


MIN_AHEAD = 2          # the running clip and the next one are always copied, even past cache_max_gb


def _room_in(r: Path, size: float, ignore_cap: bool = False) -> bool:
    try:
        free = shutil.disk_usage(r).free
    except OSError:
        return False
    if free - size < float(core.cfg("cache_reserve_gb") or 20) * 1e9:
        return False
    cap = _cap_of(r)
    if cap and not ignore_cap and _dir_size(_src_of(r)) + size > cap:
        return False
    return True


def _pick_root(size: float, must: bool) -> Path | None:
    """Main relay first, the second one when the main is at its limit; for the running and the next
    clip (must) any relay that physically has room."""
    for r in roots():
        if _room_in(r, size):
            return r
    if must:
        for r in roots():
            if _room_in(r, size, ignore_cap=True):
                return r
    return None


def _room_for(size: int, ignore_cap: bool = False) -> bool:
    r = root()
    try:
        free = shutil.disk_usage(r).free
    except OSError:
        return False
    reserve = float(core.cfg("cache_reserve_gb") or 20) * 1e9
    cap = float(core.cfg("cache_max_gb") or 0) * 1e9
    if free - size < reserve:
        return False
    if cap and not ignore_cap and _dir_size(_src_dir()) + size > cap:
        return False
    return True


# ---------------------------------------------------------------- used by run_video
def use(job, clip: dict) -> dict:
    """Clip with its footage paths pointing at the SSD copies (waits for them).
    Falls back to the original files when there is no relay or no room."""
    if root() is None or not _uses_footage(job):
        return clip
    start()
    files = _clip_files(clip)
    total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    stall = (time.time(), -1)
    t_start = time.time()
    while True:
        if job.cancel:
            from jobs import Cancelled
            raise Cancelled()
        if job.pause:
            from jobs import Paused
            raise Paused()
        if root() is None:                              # relay turned off while waiting: read the originals now
            job.message = "已關閉中繼 SSD：直接讀原始素材"
            return clip
        if time.time() - t_start > 90 and total and stall[1] <= 0 and time.time() - stall[0] > 60:
            core.log(f"relay: {clip.get('name', '')} still nothing copied after {int(time.time() - t_start)} s -> reading the originals", "job_log.txt")
            _dump_threads("relay stalled")
            job.message = "中繼 SSD 一直沒有複製進度：改成直接讀原始素材"
            return clip
        missing = [f for f in files if not staged_path(f).exists()]
        if not missing:
            break
        need = sum(os.path.getsize(f) for f in missing)
        if not _room_for(need) and _state["copy"] is None and not _disk_room(need):
            core.log(f"中繼資料夾空間不足，{clip.get('name', '')} 直接讀原始位置", "job_log.txt")
            job.message = "中繼 SSD 空間不足：直接讀原始素材"
            return clip
        done = total - sum(os.path.getsize(f) for f in missing)
        c = _state["copy"]
        if c and c.get("src") in missing:
            done += c.get("done", 0)
        spd = _speed(c["src"], c.get("done", 0)) if c and c.get("src") in missing else 0.0
        job.message = (f"複製素材到中繼 SSD… {done / max(1, total) * 100:.0f}%（{core.fmt_bytes(done)} / {core.fmt_bytes(total)}"
                       + (f"，{core.fmt_bytes(spd)}/s" if spd > 0 else "") + "）")
        job.progress = 0.0
        # watchdog: nothing copied for 20 s (the background copier is busy elsewhere, full or stuck)
        # -> the job copies its own footage now, so it never waits for nothing
        if done != stall[1]:
            stall = (time.time(), done)
        elif time.time() - stall[0] > 20:
            th = [t.name for t in threading.enumerate() if t.name.startswith("staging")]
            core.log(f"relay: no copy progress for {clip.get('name', '')} 20 s (copy={_state.get('copy') and Path(_state['copy']['src']).name}, "
                     f"move={_state.get('move') and Path(_state['move']['src']).name}, threads={th}, note={_state.get('note')}) "
                     "-> copying in the job", "job_log.txt")
            if not _disk_room(need):
                job.message = "中繼 SSD 空間不足：直接讀原始素材"
                return clip
            _dump_threads("relay no progress")
            _copy_inline(job, missing, total, done)
            stall = (time.time(), 0)
            continue
        _wake.set()
        time.sleep(0.5)
    out = dict(clip)
    for k in ("front", "back", "wavs"):
        if clip.get(k):
            out[k] = [str(staged_path(f)) for f in clip[k]]
    out["_staged"] = True
    return out


def _dump_threads(why: str):
    """Write what every thread is doing to job_log (for 卡住診斷)."""
    try:
        import diag
        core.log(f"threads ({why}):\n" + diag.threads_text(), "job_log.txt")
    except Exception:
        pass


def _disk_room(size: float) -> bool:
    """Physical room only (the 50 GB prefetch limit does not apply to the job being converted)."""
    try:
        return shutil.disk_usage(root()).free - size >= float(core.cfg("cache_reserve_gb") or 20) * 1e9
    except OSError:
        return False


def _copy_inline(job, missing: list, total: int, done0: int):
    """Copy the job's own missing footage in the job thread (shown like the background copy)."""
    from jobs import Cancelled, Paused
    base = done0
    for f in missing:
        dst = staged_path(f)
        if dst.exists():
            continue
        c = _state.get("copy")
        if c and c.get("src") == f:
            continue                                   # the background copier is on it after all
        size = os.path.getsize(f)
        tmp = work_dir() / (dst.name + ".jobcopy")
        _state["jcopy"] = {"src": f, "done": 0, "total": size}

        def prog(d, t, f=f):
            _state["jcopy"]["done"] = d
            spd = _speed("job:" + f, d)
            job.message = (f"複製素材到中繼 SSD… {(base + d) / max(1, total) * 100:.0f}%（{core.fmt_bytes(base + d)} / "
                           f"{core.fmt_bytes(total)}" + (f"，{core.fmt_bytes(spd)}/s" if spd > 0 else "") + "）")
        try:
            ok = _copy(f, tmp, prog, cancel=lambda: job.cancel or job.pause)
        finally:
            _state["jcopy"] = None
        if not ok:
            tmp.unlink(missing_ok=True)
            raise Cancelled() if job.cancel else Paused()
        if dst.exists():
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, dst)
        base += size


def deliver(job, tmp_file: str, dest: str) -> str:
    """Hand a finished output (on the SSD) to the background mover. Returns a status text."""
    rr = _root_of(tmp_file) or root()
    dd = rr / "done"
    dd.mkdir(exist_ok=True)
    d = dd / Path(dest).name
    core.write_text_safe(str(d) + ".dest", dest)     # 斷電保護: where it goes is written before it moves
    core.fsync_path(tmp_file)
    try:
        os.replace(tmp_file, d)
    except OSError:
        shutil.move(tmp_file, d)                     # (relay changed while converting: other drive)
    start()
    _wake.set()
    return f"搬運到 {Path(dest).parent} 中"


# ---------------------------------------------------------------- background thread
CODE_GEN = 3        # bump when the copier / mover loops change: a hot update then starts fresh ones


def _M():
    """The newest staging module (a hot update replaces it; running loops follow)."""
    import sys
    return sys.modules.get("staging") or sys.modules[__name__]


def start():
    with _lock:
        gen = _S.get("gen", 0)
        alive = {t.name for t in threading.enumerate() if t.is_alive()}
        cname, mname = f"staging-c{gen}", f"staging-m{gen}"
        if _S.get("gen_code") == CODE_GEN and cname in alive and mname in alive:
            return
        if _S.get("gen_code") == CODE_GEN and gen:
            # same code, a loop died: restart only that one
            if cname not in alive:
                core.log("relay: copier was gone, restarted", "job_log.txt")
                threading.Thread(target=_loop, args=(gen,), daemon=True, name=cname).start()
            if mname not in alive:
                core.log("relay: mover was gone, restarted", "job_log.txt")
                threading.Thread(target=_mover_loop, args=(gen,), daemon=True, name=mname).start()
            return
        # first start, or new loop code from a hot update: new generation (older loops of this
        # kind stop at their next round; loops from before 2.15.1 cannot be stopped and simply run on)
        gen += 1
        _S["gen"], _S["gen_code"], _S["started"], _S["mover"] = gen, CODE_GEN, True, True
        if gen > 1 or any(n == "staging" or n.startswith("staging-") for n in alive):
            core.log(f"relay: copier/mover started (generation {gen})", "job_log.txt")
    threading.Thread(target=_loop, args=(gen,), daemon=True, name=f"staging-c{gen}").start()
    threading.Thread(target=_mover_loop, args=(gen,), daemon=True, name=f"staging-m{gen}").start()


def _mover_loop(gen=0):
    """Moving finished outputs runs on its own, so copying the next footage never waits for it."""
    while _S.get("gen") == gen:
        time.sleep(3)
        if _S.get("gen") != gen:
            return
        try:
            _M()._move_round()
        except Exception as e:
            _state["note"] = f"⚠ 中繼搬運：{e}"
            core.log(f"staging move: {e}", "job_log.txt")
            time.sleep(10)


def _move_round():
    if root() is not None:
        _move_pending()


# ---------------------------------------------------------------- copy progress for the queue page
def _speed(key: str, done: float) -> float:
    """Bytes per second of the copy `key`, smoothed (sampled whenever the page asks)."""
    sp = _S.setdefault("speed", {})
    now = time.time()
    s = sp.get(key)
    if s is None or done < s["done"]:
        sp[key] = {"t": now, "done": done, "rate": 0.0}
        return 0.0
    dt = now - s["t"]
    if dt >= 0.8:
        inst = (done - s["done"]) / dt
        s["rate"] = inst if not s["rate"] else s["rate"] * 0.6 + inst * 0.4
        s["t"], s["done"] = now, done
    return s["rate"]


def job_copy(job) -> dict | None:
    """{"state": done / copying / wait, "done", "total", "speed"} for a job that uses the relay."""
    if root() is None or not _uses_footage(job) or job.status not in ("running", "queued", "paused"):
        return None
    files = [f for f in _clip_files(job.clip) if os.path.exists(f)]
    if not files:
        return None
    total, done, copying, speed = 0, 0, False, 0.0
    c = _state.get("copy")
    for f in files:
        try:
            sz = os.path.getsize(f)
        except OSError:
            continue
        total += sz
        try:
            if staged_path(f).exists():
                done += sz
                continue
        except OSError:
            pass
        jc = _state.get("jcopy")
        if c and c.get("src") == f:
            copying = True
            done += c.get("done", 0)
            speed = _speed(f, c.get("done", 0))
        elif jc and jc.get("src") == f:
            copying = True
            done += jc.get("done", 0)
            speed = _speed("job:" + f, jc.get("done", 0))
    st = "done" if done >= total else ("copying" if copying else "wait")
    return {"state": st, "done": done, "total": total, "speed": speed}


def relay_usage() -> dict:
    per = []
    for r in roots():
        try:
            used = _dir_size(r / "src") if (r / "src").is_dir() else 0
        except Exception:
            used = 0
        per.append({"root": str(r), "drive": os.path.splitdrive(str(r))[0], "used": used, "cap": _cap_of(r),
                    "main": r == root()})
    return {"used": sum(x["used"] for x in per), "cap": sum(x["cap"] for x in per), "roots": per}


def status_text() -> str:
    parts = []
    c, m = _state["copy"], _state["move"]
    if c:
        parts.append(f"預先複製 {Path(c['src']).name} {c['done'] / max(1, c['total']) * 100:.0f}%")
    if m:
        parts.append(f"搬運成品 {Path(m['src']).name} {m['done'] / max(1, m['total']) * 100:.0f}%")
    if _state["note"]:
        parts.append(_state["note"])
    return "　".join(parts)


def _jobs():
    q = None
    try:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
    except Exception:
        pass
    if q is None:
        q = _S.get("queue")
    return list(q.jobs) if q else []


def _loop(gen=0):
    while _S.get("gen") == gen:
        _wake.wait(3)
        _wake.clear()
        if _S.get("gen") != gen:
            return
        try:
            _M()._copy_round()
        except Exception as e:
            _state["note"] = f"⚠ 中繼：{e}"
            core.log(f"staging: {e}", "job_log.txt")
            time.sleep(10)


_LOADED = time.time()


def _relay_plan():
    """One-time move to the recommended layout: main relay on a non-system SSD (T:), the system disk
    (C:) only as backup, 50 GB each. Copies already on C: stay in use. Waits until no job that was
    started by an older version is running (its end would still expect the old folders)."""
    if core.cfg("relay_plan_done"):
        return
    cur = str(core.cfg("cache_dir") or "")
    sysd = os.environ.get("SystemDrive", "C:").upper()
    if not cur or os.path.splitdrive(cur)[0].upper() != sysd or core.cfg("cache_dir2"):
        core.set_cfg("relay_plan_done", True)
        return
    try:
        import disks
        cand = None
        for L in "TSRQPEFGHIJKLMNO":
            if f"{L}:" == sysd or not os.path.isdir(f"{L}:\\"):
                continue
            inf = disks.info(f"{L}:\\")
            if inf.get("kind") == "SSD" and inf.get("free", 0) > 80e9:
                cand = f"{L}:\\FusionStitch中繼"
                break
    except Exception:
        cand = None
    if not cand:
        core.set_cfg("relay_plan_done", True)
        return
    old = [j for j in _jobs() if j.status == "running" and (getattr(j, "started", 0) or 0) < _LOADED]
    if old:
        _state["note"] = f"做完 #{old[0].id} 後，中繼改成 {cand[:2]} 為主、{sysd} 備援"
        return
    core.set_cfg("cache_dir2", cur)
    core.set_cfg("cache_dir", cand)
    if float(core.cfg("cache_max_gb") or 0) <= 0 or float(core.cfg("cache_max_gb")) > 50:
        core.set_cfg("cache_max_gb", 50)
    core.set_cfg("cache2_max_gb", 50)
    core.set_cfg("relay_plan_done", True)
    _state["note"] = ""
    core.log(f"relay: main {cand}, backup {cur} (50 GB each)", "job_log.txt")


def _copy_round():
    try:
        _relay_plan()
    except Exception as e:
        core.log(f"relay plan: {e}", "job_log.txt")
    if root() is None:
        return
    _prefetch_one()
    _cleanup()


def _move_pending():
    markers = []
    for r in roots():
        dd = r / "done"
        if dd.is_dir():
            markers += sorted(dd.glob("*.dest"))
    for marker in markers:
        src = Path(str(marker)[:-5])
        if not src.exists():
            marker.unlink(missing_ok=True)
            continue
        dest = Path(marker.read_text(encoding="utf-8").strip())
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = src.stat().st_size
        try:
            free = shutil.disk_usage(dest.parent).free
        except OSError:
            free = 0
        if free < size + 1e9:
            _state["note"] = f"⚠ {dest.anchor} 空間不足，{src.name} 先留在中繼資料夾（空出空間後會自動搬）"
            return
        _state["move"] = {"src": str(src), "done": 0, "total": size}

        def prog(d, t):
            _state["move"]["done"] = d
        tmp = dest.with_name(dest.name + ".fsmove")         # (not ".moving": an older mover may still run after a hot update)
        try:
            _copy(str(src), tmp, prog)
            used = Path(core.place_file(str(tmp), str(dest)))   # locked target -> name_2.mp4
        finally:
            _state["move"] = None
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        # 斷電保護: the copy on the SSD is deleted only once the output folder has the whole file
        try:
            ok_ = used.exists() and used.stat().st_size == size
        except OSError:
            ok_ = False
        if not ok_:
            core.log(f"move check failed {src.name} -> {used}: kept on the relay, will try again", "job_log.txt")
            return
        core.fsync_path(used)
        src.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        _state["note"] = ""
        core.log(f"moved {src.name} -> {used}", "job_log.txt")
        for j in _jobs():
            if Path(j.output).name == dest.name and j.status == "done":
                j.message = j.message.replace(f"搬運到 {dest.parent} 中", f"已送到 {used}")
                j.output = str(used)
        return                                   # one per round: prefetch gets its turn



def _prefetch_one():
    js = [j for j in _jobs() if _uses_footage(j) and j.status in ("running", "queued")]
    js.sort(key=lambda j: 0 if j.status == "running" else 1)
    seen = set()
    for rank, j in enumerate(js):
        files = [f for f in _clip_files(j.clip) if os.path.exists(f) and f not in seen]
        seen.update(files)
        have = {f: staged_find(f) for f in files}
        missing = [f for f in files if not have[f]]
        if not missing:
            continue
        size = sum(os.path.getsize(f) for f in missing)
        must = rank < MIN_AHEAD                       # 正在轉的和下一支：超過上限也照樣複製
        jr = next((_root_of(p) for p in have.values() if p), None)   # a clip stays on one SSD
        if jr is None or not _room_in(jr, size, ignore_cap=must):
            jr = _pick_root(size, must)
        if jr is None:
            caps = "、".join(f"{os.path.splitdrive(str(r))[0]} {_cap_of(r) / 1e9:g} GB" for r in roots())
            _state["note"] = (f"已先存到上限（{caps}，至少先存 {MIN_AHEAD} 支），等用完再複製下一支" if not must else
                              f"中繼 SSD 實際空間不足（每顆保留 {float(core.cfg('cache_reserve_gb') or 20):g} GB），等目前的工作用完再複製")
            return
        f = missing[0]
        dst = _src_of(jr) / _staged_name(f)
        tmp = _work_of(jr) / (dst.name + ".pcopy")
        _state["note"] = ""
        _state["copy"] = {"src": f, "done": 0, "total": os.path.getsize(f), "root": str(jr)}

        def prog(d, t):
            _state["copy"]["done"] = d
        try:
            ok = _copy(f, dst, prog, cancel=lambda: root() is None, tmp=tmp)
            if ok is False:
                tmp.unlink(missing_ok=True)
        finally:
            _state["copy"] = None
        return


def _cleanup():
    for r in roots():
        try:
            for p in _work_of(r).iterdir():               # leftovers of interrupted copies
                if (p.name.endswith(".pcopy") and _state.get("copy") is None) or \
                        (p.name.endswith(".jobcopy") and _state.get("jcopy") is None):
                    if time.time() - p.stat().st_mtime > 120:
                        p.unlink(missing_ok=True)
        except OSError:
            pass
    keep = set()
    for j in _jobs():
        if _uses_footage(j) and j.status in ("running", "queued", "paused", "error"):
            for f in _clip_files(j.clip):
                try:
                    keep.add(_staged_name(f))
                except OSError:
                    pass
    for r in roots():
        sd = r / "src"
        if not sd.is_dir():
            continue
        for p in sd.iterdir():
            if p.name.endswith(".copying") and _state["copy"] is None:
                p.unlink(missing_ok=True)
            elif p.is_file() and not p.name.endswith(".copying") and p.name not in keep:
                try:
                    p.unlink()
                except OSError:
                    pass                          # still open by a reader: next round
