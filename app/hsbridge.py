"""🤝 FusionStitch ⇄ 別的程式（SortCut）握手：app_handshake.py 是兩邊共用的同一份檔，這裡是 FusionStitch 這一半.

* 告訴對方：want（2＝有轉檔在跑或排隊、1＝只有中繼複製／補色彩標記這種小工作、0＝閒著、3＝按了「⏩ 這邊先跑」）、
  prio（設定 hs_prio，0～10）、在做什麼、CPU 用多少、幾條 NVENC。
* 照 decide() 做：yield／split 時把轉檔放慢到 cpu_cap %（pace.py 的休息比例，GPU、CPU、硬碟一起慢），
  no_new_heavy 時新的工作先不開（跑到一半的照跑）；放回來時慢慢放（每 10 秒 +10%）。畫面和手機網頁永遠不受限制。
* 握手物件掛在 QApplication._fs_hs：熱更新不會斷；app_handshake.py 換新版時舊的停掉（不刪檔）、新的接著寫。
"""
from __future__ import annotations

import sys
import threading
import time

import core

PORT = 8361
_lock = threading.Lock()
_g = {"lv": 1.0, "t": 0.0, "d": {}, "cpu": None}


def _app():
    try:
        from PySide6.QtWidgets import QApplication
        return QApplication.instance()
    except Exception:
        return None


def _holder():
    a = _app()
    return a if a is not None else sys.modules[__name__]


def _cpu() -> float:
    """FusionStitch + its ffmpeg children, % of the whole computer."""
    try:
        import psutil
        me = _g.get("proc")
        if me is None:
            me = _g["proc"] = psutil.Process()
        tot = me.cpu_percent(None)
        kids = _g.setdefault("kids", {})
        alive = set()
        for ch in me.children(recursive=True):
            alive.add(ch.pid)
            p = kids.get(ch.pid)
            if p is None:
                kids[ch.pid] = ch
                ch.cpu_percent(None)
                continue
            try:
                tot += p.cpu_percent(None)
            except Exception:
                pass
        for pid in list(kids):
            if pid not in alive:
                kids.pop(pid, None)
        return round(tot / max(1, psutil.cpu_count() or 1), 1)
    except Exception:
        return 0.0


def state() -> dict:
    now = time.time()
    try:                                    # ✅ 原檔資料夾轉完 → 改名「-FS DONE」（每 60 秒，背景）
        import donefolder
        donefolder.tick()
    except Exception:
        pass
    a = _app()
    q = getattr(a, "_fs_queue", None)
    js = list(getattr(q, "jobs", []) or [])
    run = [j for j in js if j.status == "running"]
    wait = [j for j in js if j.status == "queued"]
    enc = [j for j in run if getattr(getattr(j, "est", None), "t0", None) and not getattr(j, "t_fin0", None)]
    small = False
    try:
        import staging
        small = bool(staging._state.get("copy") or staging._state.get("move"))
    except Exception:
        pass
    try:
        import colortag
        small = small or bool(colortag._S.get("busy"))
    except Exception:
        pass
    boost = float(core.cfg("hs_boost_until") or 0) > now
    want = 3 if boost else 2 if (run or wait) else 1 if small else 0
    what = "使用者按了「這邊先跑」" if boost else ""
    if run or wait:
        what = (what + "，" if what else "") + f"360 轉檔 {len(run)} 支在跑" + (f"、{len(wait)} 支排隊" if wait else "")
    elif small:
        what = "中繼複製／補標記"
    prog = int(sum(float(getattr(j, "progress", 0) or 0) for j in run) * 1000) + len([j for j in js if j.status == "done"]) * 1000
    if prog != _g.get("prog"):
        _g["prog"] = prog
        _g["prog_t"] = now
    return {"on": core.cfg("hs_on") is not False, "want": want, "prog": prog, "prog_t": _g.get("prog_t", now) if (run or wait) else now,
            "prio": int(core.cfg("hs_prio") if core.cfg("hs_prio") is not None else 5), "what": what,
            "use": {"cpu": _cpu(), "gpu": 0, "nvenc": len(enc), "disks": [],
                    "busy": 100 if enc else (30 if run else 0)}, "max": {"nvenc": 8}}


def hs():
    """The running handshake (started on first use; restarted when app_handshake.py was hot-updated)."""
    with _lock:
        import app_handshake as HS
        h = _holder()
        cur = getattr(h, "_fs_hs", None)
        if cur is not None and type(cur) is HS.Handshake and cur.th is not None and cur.th.is_alive():
            cur.state = sys.modules[__name__].state          # (this module may be newer too)
            cur.ver = core.APP_VERSION
            return cur
        if cur is not None:
            try:
                cur.stop(remove=False)
            except Exception:
                pass
        new = HS.Handshake("FusionStitch", "FusionStitch", core.APP_VERSION, PORT, state).start()
        setattr(h, "_fs_hs", new)
        core.log(f"handshake: started (rules {HS.RULES})")
        return new


def _compute():
    """(背景執行緒) 真的去算：可能用到別的程式提供的新版規則、nvidia-smi，所以不能在畫面的執行緒跑."""
    now = time.time()
    try:
        d = hs().decide()
    except Exception as e:
        d = {"mode": "alone", "why": f"握手出錯：{e}", "cpu_cap": None, "no_new_heavy": False}
    if core.cfg("hs_on") is False:
        d = {**d, "mode": "alone", "cpu_cap": None, "nvenc_cap": None, "no_new_heavy": False, "why": "握手關著"}
    old = _g["d"].get("mode")
    _g.update(d=d, t=now)
    try:
        import app_handshake as HS
        if hasattr(HS, "gpu_util"):
            _g["gpu"] = HS.gpu_util()
    except Exception:
        pass
    if old is not None and old != d.get("mode"):
        core.log(f"handshake: {old} → {d.get('mode')}：{d.get('why', '')}")


def _worker_loop(gen):
    while True:
        m = sys.modules.get(__name__)
        h = _holder()
        if getattr(h, "_fs_hsw_gen", None) != gen:
            return                                     # 熱更新換了新的
        t0 = time.time()
        try:
            m._compute()
        except Exception:
            pass
        if time.time() - t0 > 3:
            core.log(f"handshake: 算一次花了 {time.time() - t0:.1f} 秒（在背景，不影響畫面）")
        time.sleep(1.0)


def _ensure_worker():
    h = _holder()
    th = getattr(h, "_fs_hsw", None)
    if th is not None and th.is_alive() and getattr(h, "_fs_hsw_gen", None) == WORKER_GEN:
        return
    setattr(h, "_fs_hsw_gen", WORKER_GEN)
    th = threading.Thread(target=_worker_loop, args=(WORKER_GEN,), name=f"hsdecide{WORKER_GEN}", daemon=True)
    setattr(h, "_fs_hsw", th)
    th.start()


WORKER_GEN = 2


def decide() -> dict:
    """畫面、轉檔都只讀背景算好的結果（永遠不會卡）."""
    try:
        _ensure_worker()
    except Exception:
        pass
    return _g["d"] or {"mode": "alone", "why": "握手啟動中", "cpu_cap": None, "no_new_heavy": False}


def level() -> float:
    """The conversion speed the handshake allows (0.05..1): down at once, back up slowly."""
    d = decide()
    cap = d.get("cpu_cap")
    target = 1.0 if cap is None else max(0.03, min(1.0, float(cap) / 100.0))
    now = time.time()
    lv = _g["lv"]
    last = _g.get("lt", now)
    _g["lt"] = now
    if target < lv:
        lv = target
    else:
        lv = min(target, lv + 0.01 * max(0.0, now - last))       # +10% every 10 s
    _g["lv"] = lv
    return lv


def no_new() -> bool:
    return bool(decide().get("no_new_heavy"))


def label() -> str:
    d = decide()
    m = d.get("mode")
    if d.get("cpu_cap") is not None:
        return f"🤝 Lv{d.get('level', '')} {d.get('why', '')} → {_g['lv'] * 100:.0f}%"
    return ""


def status() -> dict:
    import app_handshake as HS
    d = dict(decide())
    try:
        h = hs()
        err = h.err
    except Exception as e:
        err = str(e)
    P = HS.policy()
    peers = d.get("peers") or []
    other = "SortCut"                                        # (分配拉桿是 FusionStitch : SortCut)
    sh = P.get("share") or {}
    mine = float(sh.get("FusionStitch", 50))
    oth = float(sh.get(other, 50))
    return {"on": core.cfg("hs_on") is not False, "prio": int(core.cfg("hs_prio") if core.cfg("hs_prio") is not None else 5),
            "boost_until": float(core.cfg("hs_boost_until") or 0), "mode": d.get("mode"), "why": d.get("why", ""),
            "peers": peers, "other": other, "share": round(100 * mine / (mine + oth)) if mine + oth else 50,
            "reserve": P.get("reserve"), "reserve_on": P.get("reserve_on"), "idle_min": P.get("idle_min"),
            "user_active": d.get("user_active"), "budget": d.get("budget"), "level": round(_g["lv"], 2),
            "rules": d.get("rules"), "rules_own": HS.RULES, "rules_from": d.get("rules_from"), "err": err,
            "policy_by": P.get("by", ""), "no_new": bool(d.get("no_new_heavy")),
            "lv": d.get("level", 10), "cap": d.get("cpu_cap"), "apps": list(getattr(HS, "APPS", ["FusionStitch", "SortCut"])),
            "members": P.get("members"), "boss": P.get("boss"), "boss_cap": P.get("boss_cap"), "others_cap": P.get("others_cap"),
            "reserve_when": P.get("reserve_when", "active"), "stalled": d.get("stalled") or [],
            "stall_min": P.get("stall_min", 4), "gpu": _gpu()}


def _gpu():
    return _g.get("gpu")                    # (背景算好的；畫面不叫 nvidia-smi)


def act(body: dict) -> dict:
    """Settings from the PC page or the phone: on, prio, boost (hours, 0 = cancel), share (FusionStitch %),
    reserve (%), reserve_on."""
    import app_handshake as HS
    msg = []
    if "on" in body:
        core.set_cfg("hs_on", bool(body["on"]))
        msg.append("握手：" + ("開" if body["on"] else "關"))
    if body.get("prio") is not None:
        core.set_cfg("hs_prio", max(0, min(10, int(body["prio"]))))
        msg.append(f"這邊的優先：{int(body['prio'])}")
    if body.get("boost") is not None:
        h = float(body["boost"])
        core.set_cfg("hs_boost_until", time.time() + h * 3600 if h > 0 else 0)
        msg.append(f"⏩ 這邊先跑 {h:g} 小時" if h > 0 else "取消「這邊先跑」")
    pol = {}
    if body.get("share") is not None:
        s = max(10, min(90, float(body["share"])))
        other = str(body.get("other") or "SortCut")
        pol["share"] = {"FusionStitch": s, other: 100 - s}
        msg.append(f"分配：FusionStitch {s:.0f} : {100 - s:.0f} {other}")
    if body.get("reserve") is not None:
        pol["reserve"] = max(0, min(80, float(body["reserve"])))
        msg.append(f"有人在用電腦時留 {pol['reserve']:.0f}% 給你")
    if body.get("reserve_on") is not None:
        pol["reserve_on"] = bool(body["reserve_on"])
        msg.append("留給你：" + ("開" if body["reserve_on"] else "關"))
    if "members" in body:
        m = body["members"]
        pol["members"] = None if m is None else [str(x) for x in m]
        msg.append("參與：" + ("全部" if m is None else "、".join(pol["members"]) or "沒有"))
    for k, t in (("boss_cap", "最優先的用"), ("others_cap", "其他降到"), ("stall_min", "幾分鐘沒進度算卡住：")):
        if body.get(k) is not None:
            pol[k] = float(body[k])
            msg.append(f"{t} {float(body[k]):.0f}%")
    if body.get("reserve_when") in ("always", "active"):
        pol["reserve_when"] = body["reserve_when"]
        msg.append("留給你：" + ("一直留" if body["reserve_when"] == "always" else "有在用電腦時才留"))
    if pol:
        HS.set_policy("FusionStitch", **pol)
    _g["t"] = 0
    try:
        import pace
        pace._cache["t"] = 0
    except Exception:
        pass
    return {"ok": True, "message": "、".join(msg) or "沒有改", "hs": status()}


def stop():
    h = getattr(_holder(), "_fs_hs", None)
    if h is not None:
        try:
            h.stop(remove=True)
        except Exception:
            pass
