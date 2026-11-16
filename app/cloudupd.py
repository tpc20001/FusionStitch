"""☁ 雲端更新（GitHub Releases）.

1 minute after start and then every 6 hours (setting "cloud_on"), update.json is read in the
background. A newer version is only announced: nothing is downloaded until you press 「更新」.

* Only the program changed → FS_V<ver>_Update.zip is downloaded (background, rate-limited,
  SHA-256 checked) and installed with the normal hot update: conversions keep running.
* The EXE (launcher) has to change (update.json "launcher" > FS_LAUNCHER of the running EXE):
  the running / queued conversions are listed first and waiting for them is recommended; when you
  insist, FS_Shell_L<n>.zip is downloaded, a small BAT waits until FusionStitch has closed, swaps
  FusionStitch.exe + _internal, starts the new EXE and waits for it to report a good start
  (UPDATE\\shell_ok.flag). If the swap or the start fails, the old EXE comes back and starts.
  Conversions continue where they stopped (the queue resumes after every start).
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
def busy_jobs() -> list[dict]:
    """Conversions that are running or waiting (listed before the EXE is swapped)."""
    out = []
    try:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        for j in (q.jobs if q else []):
            if j.status in ("running", "queued"):
                out.append({"id": j.id, "title": j.title, "status": j.status, "progress": round(j.progress, 3)})
    except Exception:
        pass
    return out


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
            need_shell = bool(info.get("shell")) and int(info.get("launcher") or 1) > launcher()
            newer_code = vkey(info["version"]) > vkey(core.APP_VERSION)
            R = {"ok": True, "info": info, "url": u, "t": time.time(), "need_shell": need_shell,
                 "newer_code": newer_code, "newer": newer_code or need_shell, "cur": core.APP_VERSION, "launcher": launcher()}
            break
        except Exception as ex:
            err = f"{type(ex).__name__}: {ex}"[:200]
    if R is None:
        R = {"ok": False, "newer": False, "t": time.time(), "cur": core.APP_VERSION, "message": "連不到 GitHub 更新：" + err}
    D = _store(last_t=time.time(), last_ok=R["ok"])
    R["skipped"] = bool(R.get("newer") and D.get("skip") == (R.get("info") or {}).get("version") and not R.get("need_shell"))
    st["chk"] = R
    if R.get("newer") and not R["skipped"]:
        v = R["info"]["version"]
        key = f"{v}/{R['info'].get('launcher')}"
        if manual or D.get("told") != key:
            _store(told=key)
            msg = f"雲端有新版 V{v}（現在 V{core.APP_VERSION}）" + \
                  ("，這次要換 EXE（程式會關掉再自動打開）" if R["need_shell"] else "，可以熱更新、轉檔不中斷") + \
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
    """Background, rate-limited (cloud_kbps, default 2 MB/s), .part first, SHA-256 checked, cancellable."""
    st = _st()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    lim = max(64.0, float(core.cfg("cloud_kbps") or 2048)) * 1024
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
    if R["need_shell"]:
        if not getattr(sys, "frozen", False):
            return {"ok": False, "message": "這不是 EXE 執行的（原始碼模式），不能換 EXE"}
        busy = busy_jobs()
        if busy and not force:
            run = [b for b in busy if b["status"] == "running"]
            return {"ok": False, "need_confirm": True, "busy": busy,
                    "message": f"這次要換 EXE，程式會關掉再自動打開（約 1～2 分鐘）。現在 {len(run)} 支正在轉、"
                               f"{len(busy) - len(run)} 支在排隊："
                               + "、".join(f"#{b['id']} {b['title']}" + (f"（{b['progress'] * 100:.0f}%）" if b['status'] == 'running' else '')
                                          for b in busy[:10]) + ("…" if len(busy) > 10 else "")
                               + "。建議等轉完再更新；堅持現在更新的話，正在轉的會暫停，開起來從中斷處接著轉（已轉好的部分不重做）。"}
    st.update(busy=True, cancel=False, msg="下載中…")

    def run():
        _low_priority_thread()
        try:
            if R["need_shell"] and R["newer_code"]:
                # the program first (hot update, conversions keep going): the new program then swaps the EXE with its
                # own, newest swap code when you press 「更新」 again
                zp = _download(info["zip"], CLOUD_DIR() / f"FS_V{info['version']}_Update.zip",
                               info.get("zip_sha256"), info.get("zip_size"), f"程式 V{info['version']}")
                st["msg"] = f"先熱更新程式到 V{info['version']}（轉檔不中斷）；好了之後再按一次「更新」換 EXE"
                log(f"V{core.APP_VERSION} → V{info['version']}：先熱更新程式，之後再換 EXE")
                _ui(lambda p=str(zp): _hot(p))
                st["busy"] = False
            elif R["need_shell"]:
                # NOT into UPDATE: an older launcher would treat every zip there as a program update
                sz = _download(info["shell"], CLOUD_DIR() / f"FS_Shell_L{info.get('launcher')}.zip", info.get("shell_sha256"),
                               info.get("shell_size"), f"EXE 第 {info.get('launcher')} 版")
                log(f"EXE 第 {info.get('launcher')} 版下載好了（SHA-256 對過），準備換 EXE")
                nd = None
                if int(info.get("launcher") or 1) >= 3:          # the new EXE can swap itself in (no BAT)
                    st["msg"] = "解壓縮新的 EXE…"
                    nd = _extract(sz)
                    log(f"新的 EXE 解壓縮好了：{nd}")
                st["msg"] = "準備換 EXE（程式會關掉再自動打開）"
                _ui(lambda p=str(sz), d=(str(nd) if nd else ""): swap_exe(p, d))
            else:
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
    return {"ok": True, "message": "開始下載（背景、限速），好了會自動更新" +
            ("；要換 EXE，程式會關掉再自動打開" if R["need_shell"] else "，轉檔不中斷")}


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
    if R.get("need_shell"):
        return {"ok": False, "message": "要換 EXE 的版本不能略過（之後的更新都需要新 EXE）"}
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
    I = c.get("info") or {}
    dl = st.get("dl")
    return {"cur": core.APP_VERSION, "launcher": launcher(), "on": bool(core.cfg("cloud_on", True)),
            "busy": bool(st.get("busy")), "msg": st.get("msg") or "",
            "dl": {k: dl.get(k) for k in ("label", "got", "size", "spd")} if dl else None,
            "chk": {k: c.get(k) for k in ("ok", "newer", "need_shell", "newer_code", "skipped", "t", "message")} if c else None,
            "ver": I.get("version"), "date": I.get("date"), "notes": I.get("notes") or [],
            "busy_jobs": busy_jobs() if c.get("need_shell") else []}


# ---------------------------------------------------------------- EXE swap
def _bat(shell_zip: str, pid: int) -> Path:
    root = core.ROOT
    okf = core.UPDATE_DIR / "shell_ok.flag"
    bat = CLOUD_DIR() / "shell_update.bat"
    tmp = CLOUD_DIR() / "shell_new"
    logf = core.LOG_DIR / "update_log.txt"
    q = lambda p: str(p).replace("%", "%%")
    R_, T_, Z_, O_ = q(root), q(tmp), q(shell_zip), q(okf)
    s = f"""@echo off
chcp 65001 >nul
title FusionStitch 換 EXE（這個視窗會自己關掉）
set "LOGF={q(logf)}"
call :L 開始：等 FusionStitch（PID {int(pid)}）關掉
set /a N=0
:wait
tasklist /FI "PID eq {int(pid)}" /NH 2>nul | find "{int(pid)}" >nul || goto closed
set /a N+=1
if %N% GTR 60 goto kill
timeout /t 1 /nobreak >nul
goto wait
:kill
call :L 等了 60 秒還沒關，強制結束 PID {int(pid)}
taskkill /F /T /PID {int(pid)} >nul 2>&1
timeout /t 5 /nobreak >nul
tasklist /FI "PID eq {int(pid)}" /NH 2>nul | find "{int(pid)}" >nul && (call :L 程式關不掉，這次不換 EXE & exit /b 1)
:closed
call :L 程式已經關了
timeout /t 3 /nobreak >nul
if exist "{T_}" rmdir /s /q "{T_}"
mkdir "{T_}"
call :L 解壓縮 {Z_}
tar -xf "{Z_}" -C "{T_}" >nul 2>&1
if not exist "{T_}\\{EXE}" (call :L tar 解不開，改用 PowerShell & powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '{Z_}' -DestinationPath '{T_}' -Force")
if not exist "{T_}\\{EXE}" (call :L 解壓縮失敗：找不到 {EXE} & goto fail)
if not exist "{T_}\\_internal" (call :L 解壓縮失敗：找不到 _internal & goto fail)
if exist "{R_}\\_internal_old" rmdir /s /q "{R_}\\_internal_old"
if exist "{R_}\\{EXE}.old" del /f /q "{R_}\\{EXE}.old"
if exist "{R_}\\_internal" move "{R_}\\_internal" "{R_}\\_internal_old" >nul
if exist "{R_}\\_internal" (call :L 舊的 _internal 搬不走（有檔案被占用） & goto restore)
move "{R_}\\{EXE}" "{R_}\\{EXE}.old" >nul
if exist "{R_}\\{EXE}" (call :L 舊的 {EXE} 搬不走 & goto restore)
call :L 舊的已改名備份，複製新的檔案
robocopy "{T_}" "{R_}" /E /R:3 /W:2 /NFL /NDL /NJH /NJS /NP >nul
set RC=%ERRORLEVEL%
if %RC% GEQ 8 (call :L 複製新檔案失敗（robocopy %RC%） & goto restore)
if not exist "{R_}\\{EXE}" (call :L 新的 {EXE} 不在 & goto restore)
if not exist "{R_}\\_internal" (call :L 新的 _internal 不在 & goto restore)
call :L 檔案換好了，打開新的 EXE、等它回報開成功（最多 3 分鐘）
rmdir /s /q "{T_}"
if exist "{O_}" del /f /q "{O_}"
start "" /D "{R_}" "{R_}\\{EXE}"
set /a K=0
:chk
timeout /t 3 /nobreak >nul
if exist "{O_}" goto good
set /a K+=1
if %K% GTR 60 goto slow
tasklist /FI "IMAGENAME eq {EXE}" /NH 2>nul | find /I "{EXE}" >nul && goto chk
call :L 新的 EXE 開不起來（自己關掉了），換回舊的
goto restore
:good
call :L 成功：新的 EXE 開起來了，刪掉舊的備份
del /f /q "{O_}"
if exist "{R_}\\_internal_old" rmdir /s /q "{R_}\\_internal_old"
if exist "{R_}\\{EXE}.old" del /f /q "{R_}\\{EXE}.old"
exit /b 0
:slow
call :L 新的 EXE 還在跑但 3 分鐘沒回報，先留著舊的備份（_internal_old、{EXE}.old）
exit /b 0
:restore
call :L 換回舊的 EXE
taskkill /F /IM {EXE} >nul 2>&1
timeout /t 2 /nobreak >nul
if exist "{R_}\\_internal_old" (if exist "{R_}\\_internal" rmdir /s /q "{R_}\\_internal")
if exist "{R_}\\_internal_old" move "{R_}\\_internal_old" "{R_}\\_internal" >nul
if exist "{R_}\\{EXE}.old" (if exist "{R_}\\{EXE}" del /f /q "{R_}\\{EXE}")
if exist "{R_}\\{EXE}.old" move "{R_}\\{EXE}.old" "{R_}\\{EXE}" >nul
:fail
call :L 沒有換成，用原本的 EXE 打開
if exist "{T_}" rmdir /s /q "{T_}"
tasklist /FI "IMAGENAME eq {EXE}" /NH 2>nul | find /I "{EXE}" >nul && exit /b 1
start "" /D "{R_}" "{R_}\\{EXE}"
exit /b 1
:L
echo [%date% %time%] [換 EXE] %*
echo [%date% %time%] [換 EXE] %* >> "%LOGF%"
exit /b 0
"""
    bat.parent.mkdir(parents=True, exist_ok=True)
    logf.parent.mkdir(parents=True, exist_ok=True)
    bat.write_text(s, encoding="utf-8")
    return bat


def _extract(shell_zip) -> Path:
    """Unpack the EXE package into Package\\cloud\\shell_new (background thread, before anything closes)."""
    import shutil
    import zipfile
    nd = CLOUD_DIR() / "shell_new"
    shutil.rmtree(nd, ignore_errors=True)
    nd.mkdir(parents=True)
    with zipfile.ZipFile(shell_zip) as zf:
        for m in zf.namelist():
            if ".." in Path(m).parts or Path(m).is_absolute():
                raise OSError(f"EXE 壓縮檔裡有不安全的路徑：{m}")
        zf.extractall(nd)
    if not (nd / EXE).exists() or not (nd / "_internal").is_dir():
        raise OSError("EXE 壓縮檔不完整（找不到 FusionStitch.exe 或 _internal）")
    return nd


def swap_exe(shell_zip: str, new_dir: str = ""):
    """Qt thread: start the swapper (the new EXE itself, or a BAT for older EXE packages), note what was
    running, close FusionStitch (no questions)."""
    st = _st()
    try:
        bat = None if new_dir else _bat(shell_zip, os.getpid())
        busy = busy_jobs()
        (core.PACKAGE_DIR / "resume_after_update.json").write_text(
            json.dumps({"t": time.time(), "busy": busy, "from": core.APP_VERSION, "launcher": launcher(), "why": "cloud_shell"},
                       ensure_ascii=False), encoding="utf-8")
        log(f"換 EXE：程式先關掉（{len(busy)} 支轉檔中／排隊，開起來從中斷處接著轉）")
        if new_dir and os.name == "nt":
            subprocess.Popen([str(Path(new_dir) / EXE), "--fs-swap", "--root", str(core.ROOT), "--pid", str(os.getpid())],
                             cwd=new_dir, close_fds=True, creationflags=0x00000008 | 0x00000200)   # DETACHED | NEW_GROUP
            log("換 EXE：新的 EXE 會自己換上去（不用 BAT），程式關掉中")
        elif os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 7                                # minimized, not focused: you can watch it in the taskbar
            subprocess.Popen(["cmd", "/c", str(bat)], startupinfo=si, close_fds=True,
                             creationflags=0x00000010 | 0x00000200)   # NEW_CONSOLE | NEW_PROCESS_GROUP (keeps running after we close)
        st["msg"] = "換 EXE 中：程式會關掉再自動打開（約 1～2 分鐘）"
        if not new_dir:
            log("換 EXE：BAT 已啟動，程式關掉中")

        def _hard_exit():                                     # leftover threads must not keep the process (and the old EXE) alive
            time.sleep(12)
            log("換 EXE：程式 12 秒內沒有自己結束，強制結束")
            os._exit(0)
        threading.Thread(target=_hard_exit, daemon=True, name="FSSwapExit").start()
        from PySide6.QtWidgets import QApplication
        w = getattr(QApplication.instance(), "_fs_window", None)
        if w is not None:
            w.quit_app(force=True)
        else:
            QApplication.instance().quit()
    except Exception as ex:
        st["busy"] = False
        st["msg"] = f"換 EXE 準備失敗（程式沒關）：{ex}"
        log(f"換 EXE 準備失敗（程式照常執行）：{ex}")


def after_start(win):
    """Called once the window is up: report a good start to the swap BAT, announce a finished EXE swap."""
    from PySide6.QtCore import QTimer

    def ok():
        try:
            (core.UPDATE_DIR / "shell_ok.flag").write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        p = core.PACKAGE_DIR / "resume_after_update.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                d = {}
            p.unlink(missing_ok=True)
            m = (f"換 EXE 完成：啟動器第 {launcher()} 版、V{core.APP_VERSION}"
                 + (f"；{len(d.get('busy') or [])} 支轉檔從中斷處接著轉" if d.get("busy") else ""))
            log(m)
            try:
                win.toast("☁ " + m, 15000)
            except Exception:
                pass
    QTimer.singleShot(15000, ok)
    def _clean_new():
        import shutil
        shutil.rmtree(CLOUD_DIR() / "shell_new", ignore_errors=True)   # (a swapper is long finished after 4 minutes)
    QTimer.singleShot(240000, _clean_new)
    # an EXE package left in UPDATE by V2.19.0 would make an older launcher complain at every start: move it away
    for d in (core.UPDATE_DIR, core.UPDATE_DIR / "failed"):
        try:
            for z in d.glob("FS_Shell_*.zip"):
                os.replace(z, CLOUD_DIR() / z.name)
                log(f"把 {z.name} 從 {d.name} 移到 Package\\cloud（EXE 壓縮檔不該放在 UPDATE）")
        except OSError:
            pass
    start_loop()
