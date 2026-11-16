"""Front + back lens frame of a clip, side by side, so it can be recognised at a glance.

One JPEG per clip (left = front lens, right = back lens), cached in Package/thumbs/pair and
made one at a time, in the order asked, by a background thread (easy on an HDD).
The frame is taken at 5 s; if that cannot be decoded, 10 s, 15 s, 1 s, then the first frame.
A clip where none of these works is remembered as a problem (failed.json) with the reason.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

import core
import ffm as ff

TIMES = (5.0, 10.0, 15.0, 1.0, 0.0)
EACH = 480         # size of each lens in the image (px)


def _shared() -> dict:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None
    d = getattr(app, "_fs_pairs2", None) if app is not None else globals().get("_fs_pairs2")
    if d is None:
        d = {"todo": [], "busy": set(), "cv": threading.Condition(), "started": False, "failed": None}
        if app is not None:
            setattr(app, "_fs_pairs2", d)
        else:
            globals()["_fs_pairs2"] = d
    return d


_S = _shared()


def _dir() -> Path:
    d = core.PACKAGE_DIR / "thumbs" / "pair"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _failed() -> dict:
    """{image name: reason} — survives restarts."""
    if _S["failed"] is None:
        try:
            _S["failed"] = json.loads((_dir() / "failed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _S["failed"] = {}
    return _S["failed"]


def _save_failed():
    try:
        f = _dir() / "failed.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(_failed(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def path_for(clip: dict) -> Path | None:
    files = clip.get("front") or clip.get("back") or []
    if not files:
        return None
    try:
        st = os.stat(files[0])
    except OSError:
        return None
    tag = hashlib.sha1(f"{os.path.abspath(files[0]).lower()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    return _dir() / f"{tag}.jpg"


def ready(clip: dict) -> Path | None:
    p = path_for(clip)
    return p if p and p.exists() else None


def problem(clip: dict) -> str:
    """Why no picture could be made ('' = fine or not tried yet)."""
    files = clip.get("front") or clip.get("back") or []
    if files and not os.path.exists(files[0]):
        return "讀不到檔案"
    p = path_for(clip)
    return _failed().get(p.name, "") if p else ""


def failed(clip: dict) -> bool:
    return bool(problem(clip))


def state(clip: dict) -> str:
    """'ok' / 'fail' / 'wait'."""
    if ready(clip):
        return "ok"
    return "fail" if failed(clip) else "wait"


def retry(clip: dict):
    p = path_for(clip)
    if p and _failed().pop(p.name, None) is not None:
        _save_failed()
    request(clip)


def request(clip: dict, first: bool = False):
    p = path_for(clip)
    if p is None or p.exists() or p.name in _failed():
        return
    with _S["cv"]:
        if str(p) not in _S["busy"] and not any(str(q) == str(p) for q, _c in _S["todo"]):
            item = (p, {"front": list(clip.get("front") or []), "back": list(clip.get("back") or []),
                        "duration": clip.get("duration")})
            _S["todo"].insert(0, item) if first else _S["todo"].append(item)
        _S["cv"].notify_all()
        if not _S["started"]:
            _S["started"] = True
            threading.Thread(target=_loop, daemon=True, name="framepair").start()


def wait(clip: dict, timeout: float = 6.0) -> Path | None:
    """Ask for it and wait a little (the phone retries; nothing is lost on a timeout)."""
    p = ready(clip)
    if p:
        return p
    request(clip)
    target = path_for(clip)
    with _S["cv"]:
        _S["cv"].wait_for(lambda: (target and target.exists()) or (target and target.name in _failed()), timeout)
    return ready(clip)


def _make(out: Path, c: dict):
    dur = float(c.get("duration") or 0)
    srcs = [x for x in ((c["front"] or [None])[0], (c["back"] or [None])[0]) if x and os.path.exists(x)]
    if not srcs:
        raise OSError("讀不到檔案")
    last = ""
    for t in TIMES:
        if dur and t > 0 and t > dur - 0.5:
            continue
        try:
            _make_at(out, srcs, t)
            return
        except subprocess.TimeoutExpired:
            last = "讀取逾時（檔案可能損壞）"
        except OSError as e:
            last = str(e) or "沒有畫面"
    raise OSError(f"讀不到畫面：{last[-160:]}")


def _make_at(out: Path, srcs: list, t: float):
    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for s in srcs:
        cmd += ["-ss", f"{t:.2f}", "-i", s]
    if len(srcs) == 2:
        fc = f"[0:v]scale={EACH}:{EACH}[a];[1:v]scale={EACH}:{EACH}[b];[a][b]hstack=inputs=2"
    else:
        fc = f"[0:v]scale={EACH}:{EACH}"
    tmp = out.with_suffix(".tmp.jpg")
    cmd += ["-filter_complex", fc, "-frames:v", "1", "-q:v", "5", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, timeout=90, creationflags=ff.NO_WINDOW)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 500:
        tmp.unlink(missing_ok=True)
        raise OSError(r.stderr.decode(errors="replace").strip()[-200:] or "沒有畫面")
    os.replace(tmp, out)


def _loop():
    cv = _S["cv"]
    while True:
        with cv:
            cv.wait_for(lambda: bool(_S["todo"]))
            out, c = _S["todo"].pop(0)
            _S["busy"].add(str(out))
        try:
            if not out.exists():
                _make(out, c)
        except Exception as e:
            _failed()[out.name] = str(e) or "讀不到畫面"
            _save_failed()
            core.log(f"framepair {out.name}: {e}")
        finally:
            with cv:
                _S["busy"].discard(str(out))
                cv.notify_all()
