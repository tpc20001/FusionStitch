"""☁ 雲端更新（GitHub Releases）.

1 minute after start and then every 6 hours (setting "cloud_on"), update.json is read in the
background. A newer version is only announced: nothing is downloaded until you press 「更新」.

* Only the program changed → FS_V<ver>_Update.zip is downloaded (background, rate-limited,
  SHA-256 checked) and installed with the normal hot update: conversions keep running.
* 換 EXE 已經停用（使用者 2026-11-19 決定只熱更新；雲端打包會先用使用者現在的 EXE 測過新程式）.
Everything is written to LOG\\update_log.txt.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import core

URLS = ("https://github.com/tpc20001/FusionStitch/releases/latest/download/update.json",
        "https://github.com/tpc20001/FusionStitch-Release/releases/latest/download/update.json")
EXE = "FusionStitch.exe"


def CLOUD_DIR() -> Path:
    """Where downloads go (never UPDATE\\: older launchers apply every zip found there)."""
    d = core.PACKAGE_DIR / "cloud"
    d.mkdir(parents=True, exist_ok=True)
    return d
FIRST_CHECK_S = 60
EVERY_S = 6 * 3600


# ---------------------------------------------------------------- state (kept on QApplication: survives hot updates)
def _st() -> dict:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None
    st = getattr(app, "_fs_cloud", None) if app is not None else globals().get("_fs_cloud")
    if st is None:
        st = {"chk": None, "dl": None, "busy": False, "loop": False, "cancel": False, "msg": ""}
        if app is not None:
            setattr(app, "_fs_cloud", st)
        else:
            globals()["_fs_cloud"] = st
    return st


def launcher() -> int:
    """Launcher (EXE) version: new EXEs set FS_LAUNCHER; older ones are 1."""
    try:
        return int(os.environ.get("FS_LAUNCHER") or 1)
    except ValueError:
        return 1


def vkey(v) -> tuple:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))) or (0,)


def _store(**kw) -> dict:
    p = core.PACKAGE_DIR / "cloud_upd.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if kw:
        d.update(kw)
        try:
            t = p.with_suffix(".tmp")
            t.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            os.replace(t, p)
        except OSError:
            pass
    return d


def log(msg: str):
    try:
        core.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (core.LOG_DIR / "update_log.txt").open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ☁ {msg}\n")
    except OSError:
        pass


def _ctx():
    import ssl
    c = ssl.create_default_context()
    try:                                     # Windows may lack the root certificate GitHub uses: add certifi's list too
        import certifi
        c.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return c


def _open(url: str, timeout: int = 20):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"FusionStitch-Updater/{core.APP_VERSION}",
                                               "Accept": "application/octet-stream, application/json"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())


def _ps_get(url: str, out: Path, timeout: int = 600):
    """Last resort on Windows: PowerShell download (uses the Windows certificate store)."""
    u = url.replace("'", "''")
    o = str(out).replace("'", "''")
    cmd = ("$ProgressPreference='SilentlyContinue';[Net.ServicePointManager]::SecurityProtocol='Tls12';"
           f"Invoke-WebRequest -UseBasicParsing -UserAgent 'FusionStitch-Updater' -Uri '{u}' -OutFile '{o}'")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                       capture_output=True, timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0 or not out.exists():
        raise OSError("PowerShell 也下載失敗：" + (r.stderr or b"").decode("utf-8", "ignore")[-200:])


def _is_cert(ex) -> bool:
    import ssl
    return isinstance(ex, ssl.SSLError) or "CERTIFICATE" in str(ex).upper()


# ---------------------------------------------------------------- check
def check(manual: bool = False) -> dict:
    """Read update.json (background thread). Only announces; never downloads."""
    st = _st()
    err, R = "", None
    for u in URLS:
        try:
            try:
                with _open(u) as r:
                    info = json.loads(r.read().decode("utf-8-sig"))
            except Exception as ex0:
                if not (_is_cert(ex0) and os.name == "nt"):
                    raise
                tmp = core.PACKAGE_DIR / "update_json.tmp"
                _ps_get(u, tmp, 60)
                info = json.loads(tmp.read_bytes().decode("utf-8-sig"))
                tmp.unlink(missing_ok=True)
            if str(info.get("app") or "FusionStitch") != "FusionStitch" or not info.get("version"):
                err = f"{u}：不是 FusionStitch 的 update.json"
                continue
            newer_code = vkey(info["version"]) > vkey(core.APP_VERSION)
            R = {"ok": True, "info": info, "url": u, "t": time.time(),
                 "newer_code": newer_code, "newer": newer_code, "cur": core.APP_VERSION, "launcher": launcher()}
            break
        except Exception as ex:
            err = f"{type(ex).__name__}: {ex}"[:200]
    if R is None:
        R = {"ok": False, "newer": False, "t": time.time(), "cur": core.APP_VERSION, "message": "連不到 GitHub 更新：" + err}
    D = _store(last_t=time.time(), last_ok=R["ok"])
    R["skipped"] = bool(R.get("newer") and D.get("skip") == (R.get("info") or {}).get("version"))
    st["chk"] = R
    if R.get("newer") and not R["skipped"]:
        v = R["info"]["version"]
        key = f"{v}/{R['info'].get('launcher')}"
        if manual or D.get("told") != key:
            _store(told=key)
            msg = f"雲端有新版 V{v}（現在 V{core.APP_VERSION}）" + \
                  "，可以熱更新、轉檔不中斷" + \
                  "：到「線上更新」或手機「版本與更新」按「更新」"
            log(msg)
            _announce(msg)
    elif manual:
        log(R.get("message") or f"檢查更新：已經是最新 V{core.APP_VERSION}")
    return R


def _announce(msg: str):
    try:
        from PySide6.QtWidgets import QApplication
        w = getattr(QApplication.instance(), "_fs_window", None)
        if w is not None:
            w.bus.uiCall.emit(lambda m=msg: (w.tray_msg("FusionStitch 有新版", m), w.toast("☁ " + m, 20000)))
    except Exception:
        pass


def start_loop():
    """Background checks: 1 minute after start, then every 6 hours (when 「自動檢查更新」 is on)."""
    st = _st()
    if st.get("loop"):
        return
    st["loop"] = True

    def run():
        try:
            time.sleep(FIRST_CHECK_S)
            while True:
                try:
                    if core.cfg("cloud_on", True):
                        import sys as _s
                        (_s.modules.get("cloudupd") or _s.modules[__name__]).check()
                except Exception as e:
                    log(f"檢查更新出錯：{e}")
                t0 = time.time()
                while time.time() - t0 < EVERY_S:
                    time.sleep(30)
        finally:
            st["loop"] = False
    threading.Thread(target=run, name="FSCloudUpd", daemon=True).start()


# ---------------------------------------------------------------- download
def _download(url: str, dest: Path, sha: str, size: int, label: str) -> Path:
    """Background, full speed (cloud_kbps > 0 sets a limit in KB/s), .part first, SHA-256 checked, cancellable."""
    st = _st()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    kb = float(core.cfg("cloud_kbps") or 0)
    lim = max(64.0, kb) * 1024 if kb > 0 else 0.0          # 0 = no limit (default)
    h = hashlib.sha256()
    got, t0 = 0, time.time()
    st["dl"] = {"label": label, "got": 0, "size": int(size or 0), "t0": t0, "spd": 0.0}
    try:
        try:
            with _open(url, 60) as r, open(tmp, "wb") as f:
                st["dl"]["size"] = int(r.headers.get("Content-Length") or size or 0)
                while True:
                    if st.get("cancel"):
                        raise InterruptedError("取消了")
                    b = r.read(256 * 1024)
                    if not b:
                        break
                    f.write(b)
                    h.update(b)
                    got += len(b)
                    if lim:
                        need = got / lim
                        el = time.time() - t0
                        if need > el:
                            time.sleep(need - el)
                    st["dl"].update(got=got, spd=got / max(0.001, time.time() - t0))
        except InterruptedError:
            raise
        except Exception as ex0:
            if not (_is_cert(ex0) and os.name == "nt"):
                raise
            _ps_get(url, tmp)                                   # certificate trouble: let Windows download it
            h = hashlib.sha256()
            with open(tmp, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
        if sha and h.hexdigest().lower() != str(sha).lower():
            raise OSError("下載的檔案不完整（SHA-256 對不上），請再按一次更新")
        os.replace(tmp, dest)
        return dest
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _low_priority_thread():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -2)   # BELOW_NORMAL... lowest
        except Exception:
            pass


# ---------------------------------------------------------------- update
def update(force: bool = False) -> dict:
    """「更新」: code only → download + hot update; EXE → list busy conversions first (force = 我堅持)."""
    st = _st()
    R = st.get("chk")
    if st.get("busy"):
        return {"ok": False, "message": "已經在更新了，請稍候"}
    if not R or not R.get("ok") or not R.get("newer"):
        return {"ok": False, "message": "沒有新版（先按「檢查更新」）"}
    info = R["info"]
    # the check may be older than a hot update that happened since: decide again with the running version
    R["newer_code"] = vkey(info["version"]) > vkey(core.APP_VERSION)
    R["newer"] = R["newer_code"]
    if not R["newer"]:
        return {"ok": False, "message": f"已經是最新 V{core.APP_VERSION}"}
    st.update(busy=True, cancel=False, msg="下載中…")

    def run():
        _low_priority_thread()
        try:
            zp = _download(info["zip"], CLOUD_DIR() / f"FS_V{info['version']}_Update.zip",
                           info.get("zip_sha256"), info.get("zip_size"), f"程式 V{info['version']}")
            st["msg"] = f"下載好了（SHA-256 對過），熱更新到 V{info['version']}…"
            log(f"V{core.APP_VERSION} → V{info['version']}（熱更新，轉檔不中斷）")
            _ui(lambda p=str(zp): _hot(p))
            st["busy"] = False
        except Exception as ex:
            st["busy"] = False
            st["msg"] = f"更新失敗：{ex}"
            log(f"雲端更新失敗：{ex}")
        finally:
            st["dl"] = None
    threading.Thread(target=run, name="FSCloudDl", daemon=True).start()
    return {"ok": True, "message": "開始下載（背景），好了會自動熱更新，轉檔不中斷"}


def _ui(fn):
    from PySide6.QtWidgets import QApplication
    w = getattr(QApplication.instance(), "_fs_window", None)
    if w is None:
        raise RuntimeError("找不到主視窗")
    w.bus.uiCall.emit(fn)


def _hot(path: str):
    from PySide6.QtWidgets import QApplication
    w = getattr(QApplication.instance(), "_fs_window", None)
    if w is not None:
        w._web_update(path)                     # same path as a package uploaded from the phone


def skip() -> dict:
    R = _st().get("chk") or {}
    v = (R.get("info") or {}).get("version")
    if not v:
        return {"ok": False, "message": "沒有可以略過的版本"}
    _store(skip=v)
    R["skipped"] = True
    log(f"略過 V{v}")
    return {"ok": True, "message": f"已略過 V{v}，有更新的版本才會再提示"}


def cancel() -> dict:
    st = _st()
    if not st.get("dl"):
        return {"ok": False, "message": "沒有在下載"}
    st["cancel"] = True
    return {"ok": True, "message": "取消下載"}


def status() -> dict:
    st = _st()
    c = st.get("chk") or {}
    if c.get("ok") and c.get("info"):                     # re-judge against the running version (after a hot update)
        I_ = c["info"]
        c["newer_code"] = vkey(I_["version"]) > vkey(core.APP_VERSION)
        c["newer"] = c["newer_code"]
    I = c.get("info") or {}
    dl = st.get("dl")
    return {"cur": core.APP_VERSION, "launcher": launcher(), "on": bool(core.cfg("cloud_on", True)),
            "busy": bool(st.get("busy")), "msg": st.get("msg") or "",
            "dl": {k: dl.get(k) for k in ("label", "got", "size", "spd")} if dl else None,
            "chk": {k: c.get(k) for k in ("ok", "newer", "newer_code", "skipped", "t", "message")} if c else None,
            "ver": I.get("version"), "date": I.get("date"), "notes": I.get("notes") or []}


def after_start(win):
    """Called once the window is up: start the background checks; tidy files left by the old EXE-swap code
    (換 EXE 已經停用：使用者 2026-11-19 決定只熱更新)."""
    def tidy():
        import shutil
        shutil.rmtree(CLOUD_DIR() / "shell_new", ignore_errors=True)
        for d in (core.UPDATE_DIR, core.UPDATE_DIR / "failed"):
            try:
                for z in d.glob("FS_Shell_*.zip"):
                    os.replace(z, CLOUD_DIR() / z.name)
            except OSError:
                pass
        try:
            (core.UPDATE_DIR / "shell_ok.flag").write_text(str(time.time()), encoding="utf-8")   # (L3 啟動器看這個)
        except OSError:
            pass
    threading.Thread(target=tidy, daemon=True, name="cloud-tidy").start()
    start_loop()
