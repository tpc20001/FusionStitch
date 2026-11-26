"""✅ 原檔資料夾轉完自動改名：20261121 → 20261121-FS DONE（使用者 2026-11-27 定的規則）.

規則：
  1. 只改最外層的日期資料夾（批次的素材資料夾；如果批次是 100GFRNT／100GBACK 這一層，就往上一層）。
  2. 裡面的 100GBACK、100GFRNT 不改；影片檔一個都不改名、不搬、不刪；成品資料夾不動。
  3. 這個資料夾裡每一支完整的影片都轉好了（成品還在、轉檔時的檢查有過），而且佇列裡沒有它還在排隊／在轉／暫停／失敗的
     → 才改名。有任何一支失敗就不改。
  4. 資料夾正在被用（Windows 改不了名）→ 先跳過，之後再試。
  5. 設定 src_done_rename（預設開）。
背景每 60 秒檢查一次（跟著握手的背景迴圈；熱更新不斷）。
"""
from __future__ import annotations

import os
import re
import threading
import time

import core

SUFFIX = "-FS DONE"
SUB_RE = re.compile(r"^\d{3}G[A-Z]{4}$", re.I)        # 100GFRNT、100GBACK
_S = {"t": 0.0, "busy": False, "last": [], "wait": {}}


def on() -> bool:
    return core.cfg("src_done_rename") is not False


def top_of(folder: str) -> str:
    f = os.path.normpath(folder or "")
    if SUB_RE.match(os.path.basename(f)):
        f = os.path.dirname(f)
    return f


def _jobs():
    try:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        return list(getattr(q, "jobs", []) or [])
    except Exception:
        return []


def _under(path: str, top: str) -> bool:
    a, b = os.path.normcase(os.path.normpath(path or "")), os.path.normcase(top)
    return a == b or a.startswith(b + os.sep)


_DRV = {"t": 0.0, "list": []}


def _drives() -> list[str]:
    if os.name != "nt":
        return []
    if time.time() - _DRV["t"] > 60:
        _DRV.update(t=time.time(), list=[f"{c}:" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")])
    return _DRV["list"]


def resolve(out: str, extra: list) -> str | None:
    """成品現在在哪：原位置 → 成品頁清單裡的資料夾有同名檔 → 換磁碟代號（例 D:\\A\\B → G:\\A\\B，資料夾層數一樣）."""
    if not out:
        return None
    if os.path.exists(out):
        return out
    bn = os.path.basename(out)
    for f in extra:
        p = os.path.join(f, bn)
        if os.path.exists(p):
            return p
    if len(out) > 2 and out[1] == ":":
        for d in _drives():
            p = d + out[2:]
            if os.path.exists(p):
                return p
    return None


def _key(c: dict) -> str:
    fs = c.get("front") or []
    return os.path.basename(fs[0]).lower() if fs else ""


def check(top: str, js: list) -> tuple[bool, str]:
    """(可以改, 原因)；每一支沒過的原因放在 _S['detail'][top]."""
    det = []
    _S.setdefault("detail", {})[top] = det
    if not os.path.isdir(top):
        return False, "資料夾不見了"
    if os.path.basename(top).endswith(SUFFIX):
        return False, "已經改過"
    mine = [j for j in js if _under(j.clip.get("folder") or "", top)]
    left = [j for j in mine if j.status in ("queued", "running", "paused", "waiting")]
    if left:
        return False, f"還有 {len(left)} 支沒轉完"
    import clips as clips_mod
    import donemark
    import history
    extra = []
    try:
        import outfolders
        extra = [str(x) for x in outfolders.all_()]
    except Exception:
        pass
    byk = {}
    for j in mine:                                    # 同一支轉過好幾次：完成的優先
        k = _key(j.clip)
        if k and (k not in byk or j.status == "done"):
            byk[k] = j
    try:
        allc = [c for c in clips_mod.scan(top) if c.get("kind") == "video"]
    except Exception as e:
        return False, f"讀不到資料夾：{e}"
    cs = [c for c in allc if c.get("complete")]
    if not cs:
        return False, "裡面沒有完整的影片"
    hist = donemark._hist()
    bad = 0
    for c in cs:
        nm = f"GP{c.get('num', '')}（{os.path.basename((c.get('front') or [''])[0])}）"
        j = byk.get(_key(c))
        if j is not None and j.status == "done":
            if resolve(j.output, extra):
                continue
            det.append({"clip": nm, "why": "轉好了，但成品找不到（搬走了？）", "path": j.output}); bad += 1; continue
        if j is not None and j.status in ("error", "failed"):
            det.append({"clip": nm, "why": "轉檔失敗" + (f"：{j.message}" if getattr(j, "message", "") else ""), "path": ""}); bad += 1; continue
        hs = hist.get((str(c.get("num") or ""), int(history.source_bytes(c) or 0)), [])
        if any(resolve(o, extra) for o in hs):
            continue
        if hs:
            det.append({"clip": nm, "why": "轉檔紀錄有，但成品找不到（搬走了？）", "path": hs[0]}); bad += 1; continue
        if j is not None and j.status == "cancelled":
            det.append({"clip": nm, "why": "被取消，沒有轉", "path": ""}); bad += 1; continue
        det.append({"clip": nm, "why": "沒有加入過佇列（當初沒勾到，或那時檔案還不完整）", "path": ""}); bad += 1
    inc = [c for c in allc if not c.get("complete")]
    for c in inc:
        det.append({"clip": f"GP{c.get('num', '')}", "why": "檔案不完整（缺前或後鏡頭），不算", "path": "", "info": True})
    if bad:
        return False, f"{len(cs)} 支裡有 {bad} 支沒有確認到成品"
    return True, f"{len(cs)} 支都轉好了" + (f"（另有 {len(inc)} 支不完整的不算）" if inc else "")


def _rename(top: str) -> str | None:
    new = top + SUFFIX
    if os.path.exists(new):
        return None
    try:
        os.rename(top, new)
    except OSError as e:
        _S["wait"][top] = f"改不了名（可能有程式開著裡面的檔案）：{e.strerror or e}"
        return None
    core.log(f"donefolder: {top} -> {new}", "job_log.txt")
    _after(top, new)
    return new


def _after(old: str, new: str):
    """改名後把程式記得的路徑跟著改（素材資料夾、最近用過、佇列裡已完成工作的原檔路徑）."""
    try:
        for j in _jobs():
            f = j.clip.get("folder") or ""
            if _under(f, old):
                j.clip["folder"] = new + f[len(old):]
                for k in ("front", "back", "wavs"):
                    j.clip[k] = [new + p[len(old):] if _under(p, old) else p for p in (j.clip.get(k) or [])]
    except Exception:
        pass
    try:                                              # 批次記的素材資料夾也跟著改（佇列標題、按鈕才對得上）
        import batches
        for b in batches._all().values():
            f = b.get("folder") or ""
            if _under(f, old):
                b["folder"] = new + f[len(old):]
                b["title"] = os.path.basename(b["folder"]) or b.get("title")
        batches._save()
    except Exception as e:
        core.log(f"donefolder: batches update {e}")
    try:
        rec = [new + r[len(old):] if _under(r, old) else r for r in (core.cfg("recent_folders") or [])]
        core.set_cfg("recent_folders", rec)
        for key in ("last_folder", "last_dir_footage"):
            v = core.cfg(key) or ""
            if _under(v, old):
                core.set_cfg(key, new + v[len(old):])
    except Exception:
        pass
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        w = getattr(QApplication.instance(), "_fs_window", None)
        if w is not None and w.folder and _under(w.folder, old):
            nf = new + w.folder[len(old):]
            QTimer.singleShot(0, w, lambda: w.library.scan(nf))        # 畫面執行緒
    except Exception:
        pass


def batch_src(folder: str) -> dict:
    """批次標題列用（便宜：只看資料夾在不在）."""
    top = top_of(folder or "")
    name = os.path.basename(top)
    done = name.endswith(SUFFIX)
    return {"top": top, "name": name, "exists": bool(top) and os.path.isdir(top), "done": done,
            "new": name if done else name + SUFFIX, "wait": _S["wait"].get(top, "")}


def rename_batch(folder: str, dry: bool = True, force: bool = False) -> dict:
    """手動（佇列批次上的按鈕）：dry=先看能不能改；force=有原因也照改（使用者自己確認過）."""
    top = top_of(folder or "")
    if not top or not os.path.isdir(top):
        return {"ok": False, "exists": False, "message": "原檔資料夾不存在（可能已經刪掉了）", "top": top}
    if os.path.basename(top).endswith(SUFFIX):
        return {"ok": False, "exists": True, "done": True, "message": "已經加過 -FS DONE", "top": top}
    ok, why = check(top, _jobs())
    new = top + SUFFIX
    if dry:
        return {"ok": ok, "exists": True, "why": why, "top": top, "new": new, "detail": list(_S.get("detail", {}).get(top, [])),
                "name": os.path.basename(top), "new_name": os.path.basename(new)}
    if not ok and not force:
        return {"ok": False, "exists": True, "message": why, "top": top}
    if os.path.exists(new):
        return {"ok": False, "exists": True, "message": f"已經有「{os.path.basename(new)}」這個資料夾了", "top": top}
    r = _rename(top)
    if not r:
        return {"ok": False, "exists": True, "message": _S["wait"].get(top, "改不了名"), "top": top}
    _S["last"] = ([{"old": top, "new": r, "t": time.time()}] + _S["last"])[:20]
    core.log(f"donefolder: manual{' (force: ' + why + ')' if not ok else ''} {top} -> {r}", "job_log.txt")
    return {"ok": True, "message": f"已改名：{os.path.basename(r)}", "new": r}


def run_once() -> list:
    js = _jobs()
    tops = {}
    for j in js:
        f = j.clip.get("folder")
        if f and j.status == "done":
            t = top_of(f)
            tops[os.path.normcase(t)] = t
    done = []
    for t in tops.values():
        if os.path.basename(t).endswith(SUFFIX) or not os.path.isdir(t):
            continue
        ok, why = check(t, js)
        if not ok:
            if "沒有確認到成品" in why:
                _S["wait"][t] = why
            else:
                _S["wait"].pop(t, None)
            continue
        new = _rename(t)
        if new:
            _S["wait"].pop(t, None)
            done.append({"old": t, "new": new, "t": time.time()})
    if done:
        _S["last"] = (done + _S["last"])[:20]
    return done


def tick():
    if not on() or _S["busy"] or time.time() - _S["t"] < 60:
        return
    _S["t"] = time.time()
    _S["busy"] = True

    def go():
        try:
            run_once()
        except Exception as e:
            core.log(f"donefolder: {e}")
        finally:
            _S["busy"] = False
    threading.Thread(target=go, daemon=True, name="donefolder").start()


def status() -> dict:
    return {"on": on(), "last": list(_S["last"]), "wait": dict(_S["wait"])}
