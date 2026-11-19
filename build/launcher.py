"""FusionStitch 啟動器（FusionStitch.exe）.

EXE 裡只有 Python 和用到的套件（PySide6、numpy、moderngl、OpenCV、scipy、psutil；雲端用 PyInstaller 打包）。
真正的程式在 <ROOT>\\app\\*，更新只換那裡：
- 熱更新：程式裡「線上更新」／☁ 雲端更新／手機上傳 → 不關程式、轉檔不中斷。
- 放進 <ROOT>\\UPDATE\\*.zip → 下次開程式時由這裡套用（先備份 app_backup，新版開不起來自動退回）。
沒人在電腦前也不能卡住：提示視窗會自己關掉，全部寫進 LOG\\update_log.txt。
"""
import json
import os
import re
import runpy
import shutil
import sys
import time
import traceback
import zipfile
from pathlib import Path

# 程式會用到的模組列在 build/_deps.py，打包時用 --hidden-import _deps 包進 EXE（執行時不先全部載入）

LAUNCHER_VERSION = 3          # ⛔ 不要再改（2026-11-19 使用者決定：只熱更新、不換 EXE；雲端打包會擋）。 2：V2.19.0 雲端自動更新（設 FS_LAUNCHER）；3：V2.19.2 新 EXE 自己換上去（--fs-swap，不用 BAT）
EXE_NAME = "FusionStitch.exe"
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
UPDATE_DIR = ROOT / "UPDATE"
BACKUP_DIR = ROOT / "app_backup"
LOG_DIR = ROOT / "LOG"


def log(msg):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "update_log.txt").open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [啟動器 L{LAUNCHER_VERSION}] {msg}\n")
    except Exception:
        pass


def note(text, title="FusionStitch", error=False, secs=20):
    """會自己關掉的提示（Windows MessageBoxTimeout）：沒人在電腦前也不會卡住."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxTimeoutW(None, str(text), title, 0x10 if error else 0x40, 0, int(secs * 1000))
    except Exception:
        print(title, text)


def vkey(name):
    return tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)


def apply_updates():
    """UPDATE\\*.zip 照版本順序套用（跟程式裡的更新同一套規則）；回傳 (第一個備份, 套用了哪些版本)."""
    if not UPDATE_DIR.exists():
        return None, []
    zips = sorted([z for z in UPDATE_DIR.glob("*.zip") if not z.name.startswith("FS_Shell_")], key=lambda p: vkey(p.stem))
    first_backup, applied = None, []
    for z in zips:
        backup = None
        try:
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
                manifest = json.loads(zf.read("manifest.json").decode("utf-8-sig")) if "manifest.json" in names else {}
                if str(manifest.get("app") or "FusionStitch") != "FusionStitch":
                    raise RuntimeError(f"這是 {manifest.get('app')} 的更新包，不是 FusionStitch 的")
                need = int(manifest.get("requires_launcher", 1))
                if need > LAUNCHER_VERSION:
                    log(f"SKIP {z.name}: 要第 {need} 版啟動器（現在 {LAUNCHER_VERSION}），等換新 EXE 再套用")
                    continue
                members = [m for m in names if m.startswith("app/") and not m.endswith("/")]
                if not members:
                    raise RuntimeError("更新包裡沒有 app/ 的檔案")
                for m in members:
                    rel = m[4:]
                    if ".." in Path(rel).parts or Path(rel).is_absolute() or "/" in rel or "\\" in rel:
                        raise RuntimeError(f"不安全的路徑：{m}")
                for m in members:
                    if m.endswith(".py"):
                        compile(zf.read(m).decode("utf-8"), m, "exec")
                backup = BACKUP_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_before_{z.stem}"
                if APP_DIR.exists():
                    shutil.copytree(APP_DIR, backup, ignore=shutil.ignore_patterns("__pycache__"))
                first_backup = first_backup or backup
                APP_DIR.mkdir(parents=True, exist_ok=True)
                for m in members:
                    dest = APP_DIR / m[4:]
                    tmp = dest.with_name(dest.name + ".new")
                    tmp.write_bytes(zf.read(m))
                    os.replace(tmp, dest)
                shutil.rmtree(APP_DIR / "__pycache__", ignore_errors=True)
            done = UPDATE_DIR / "applied"
            done.mkdir(exist_ok=True)
            os.replace(z, done / z.name)
            applied.append(str(manifest.get("version") or z.stem))
            log(f"APPLIED {z.name}（備份 {backup}）")
        except Exception as e:
            log(f"FAILED {z.name}: {e}")
            if backup is not None and backup.exists():
                try:
                    restore(backup)
                    log(f"RESTORED {backup}（{z.name} 套用失敗）")
                except Exception as e2:
                    log(f"RESTORE FAILED: {e2}")
            try:
                bad = UPDATE_DIR / "failed"
                bad.mkdir(exist_ok=True)
                os.replace(z, bad / z.name)
            except Exception:
                pass
    try:
        olds = sorted([p for p in BACKUP_DIR.iterdir() if p.is_dir()], key=lambda p: p.name)[:-10]
        for d in olds:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
    return first_backup, applied


def restore(backup):
    shutil.rmtree(APP_DIR, ignore_errors=True)
    shutil.copytree(backup, APP_DIR)


def run_app(messages):
    os.environ["FS_LAUNCHER"] = str(LAUNCHER_VERSION)
    sys.path.insert(0, str(APP_DIR))
    for mod in [m for m, v in list(sys.modules.items()) if str(getattr(v, "__file__", "") or "").lower().startswith(str(APP_DIR).lower())]:
        sys.modules.pop(mod, None)
    import importlib
    app = importlib.import_module("app")
    return app.main({"launcher": LAUNCHER_VERSION, "messages": messages})


def cleanup_shell():
    """上次換 EXE 留下的解壓縮資料夾（_internal_old、.old 由換 EXE 的 BAT 在新 EXE 回報開成功後才刪）."""
    shutil.rmtree(UPDATE_DIR / "shell_new", ignore_errors=True)


DEPS = ("_deps", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtNetwork", "PySide6.QtMultimedia",
        "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "numpy", "cv2", "moderngl", "glcontext", "scipy.interpolate",
        "scipy.optimize", "psutil", "certifi", "ssl", "sqlite3", "ctypes.wintypes", "zlib")


def selftest():
    """雲端打包完試一下這個 EXE：每個套件都在、Qt 外掛有包進來；有 app 資料夾的話，每個程式檔都載入得了（不開主程式）."""
    import importlib
    bad, warn = [], []
    prog = ROOT / "selftest_step.txt"

    def step(s):
        try:
            with prog.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {s}\n")
        except Exception:
            pass
    step("開始")
    for m in DEPS:
        step("載入 " + m)
        try:
            importlib.import_module(m)
        except BaseException as e:
            bad.append(f"{m}: {type(e).__name__}: {e}")
    try:
        import glcontext
        glcontext.default_backend()                      # the Windows OpenGL backend moderngl uses
    except BaseException as e:
        bad.append(f"glcontext 後端: {type(e).__name__}: {e}")
    try:   # 不開視窗：只確認畫面外掛有包進來
        from PySide6.QtCore import QLibraryInfo
        pl = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
        if os.name == "nt" and not any((pl / "platforms").glob("qwindows*.dll")):
            bad.append(f"找不到 Qt 畫面外掛 qwindows.dll（{pl / 'platforms'}）")
        if not (pl / "multimedia").exists():
            warn.append(f"找不到 Qt 多媒體外掛（{pl / 'multimedia'}）")
    except Exception as e:
        bad.append(f"Qt 外掛: {e}")
    if (APP_DIR / "app.py").exists():                    # every program file must import (missing packages show up here)
        os.environ.setdefault("FUSIONSTITCH_PACKAGE", str(ROOT / "selftest_pkg"))
        sys.path.insert(0, str(APP_DIR))
        for f in sorted(APP_DIR.glob("*.py")):
            step("程式 " + f.name)
            try:
                importlib.import_module(f.stem)
            except ModuleNotFoundError as e:
                bad.append(f"app/{f.name}: 缺少套件 {e.name}")
            except BaseException as e:                   # (no ffmpeg / GPU on the build machine: fine)
                warn.append(f"app/{f.name}: {type(e).__name__}: {str(e)[:120]}")
    step("完成")
    out = {"ok": not bad, "launcher": LAUNCHER_VERSION, "bad": bad, "warn": warn}
    (ROOT / "selftest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        if sys.stdout:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    return 0 if not bad else 2


# ---------------------------------------------------------------- 換 EXE（新的 EXE 自己換上去）
def _arg(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv and sys.argv.index(name) + 1 < len(sys.argv) else default


def _alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, int(pid))      # QUERY_LIMITED | SYNCHRONIZE
    if not h:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102                # WAIT_TIMEOUT = still running
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def _retry(fn, what, secs=30):
    t0 = time.time()
    while True:
        try:
            return fn()
        except OSError as e:
            if time.time() - t0 > secs:
                raise OSError(f"{what}：{e}")
            time.sleep(1)


def swap(new_dir, root, pid):
    """FusionStitch.exe --fs-swap --root <安裝資料夾> --pid <舊程式>（從 Package\\cloud\\shell_new 執行）:
    等舊程式關掉 → 舊 EXE、_internal 改名 → 複製新的 → 打開新 EXE、等它回報開成功（UPDATE\\shell_ok.flag）
    → 成功刪舊的；失敗換回舊的照常打開。每一步寫進 <root>\\LOG\\update_log.txt."""
    root, new_dir = Path(root), Path(new_dir)
    logf = root / "LOG" / "update_log.txt"

    def L(m):
        try:
            logf.parent.mkdir(parents=True, exist_ok=True)
            with logf.open("a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [換 EXE L{LAUNCHER_VERSION}] {m}\n")
        except Exception:
            pass
    exe, internal = root / EXE_NAME, root / "_internal"
    old_exe, old_int = root / (EXE_NAME + ".old"), root / "_internal_old"
    okf = root / "UPDATE" / "shell_ok.flag"
    import subprocess

    def start(path):
        return subprocess.Popen([str(path)], cwd=str(root), close_fds=True,
                                creationflags=(0x00000008 | 0x00000200) if os.name == "nt" else 0)

    def restore(why, proc=None):
        L(f"換回舊的 EXE（{why}）")
        if proc is not None and proc.poll() is None:      # only the new program (not by name: this swapper is a FusionStitch.exe too)
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20)
            except Exception:
                proc.kill()
        time.sleep(2)
        try:
            if old_int.exists():
                if internal.exists():
                    _retry(lambda: shutil.rmtree(internal), "刪新的 _internal")
                _retry(lambda: os.replace(old_int, internal), "_internal_old 改回 _internal")
            if old_exe.exists():
                if exe.exists():
                    _retry(lambda: exe.unlink(), "刪新的 EXE")
                _retry(lambda: os.replace(old_exe, exe), f"{EXE_NAME}.old 改回")
            L("已經換回舊的，照常打開")
        except Exception as e:
            L(f"換回舊的時出錯：{e}（請看 {root}，_internal_old、{EXE_NAME}.old 是舊的）")
        try:
            start(exe)
        except Exception as e:
            L(f"打不開 EXE：{e}")
        note(f"這次沒有換成新的 EXE（{why}），已經用原本的打開。\n詳細記錄在 LOG\\update_log.txt。", secs=30)
        return 1

    L(f"開始：新 EXE 在 {new_dir}，安裝資料夾 {root}，等舊程式（PID {pid}）關掉")
    t0 = time.time()
    while pid and _alive(pid) and time.time() - t0 < 60:
        time.sleep(0.5)
    if pid and _alive(pid):
        L("等了 60 秒還沒關，強制結束")
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=20)
        except Exception:
            pass
        time.sleep(5)
        if _alive(pid):
            L("舊程式關不掉，這次不換 EXE")
            note("舊的 FusionStitch 關不掉，這次沒有換 EXE。", error=True, secs=30)
            return 1
    L(f"舊程式已經關了（{time.time() - t0:.0f} 秒）")
    time.sleep(2)
    if not (new_dir / EXE_NAME).exists() or not (new_dir / "_internal").is_dir():
        L("新的 EXE 不完整（找不到 EXE 或 _internal），不換")
        return restore("新的 EXE 不完整")
    try:
        if old_int.exists():
            _retry(lambda: shutil.rmtree(old_int), "刪上次留下的 _internal_old")
        if old_exe.exists():
            _retry(lambda: old_exe.unlink(), f"刪上次留下的 {EXE_NAME}.old")
        if internal.exists():
            _retry(lambda: os.replace(internal, old_int), "舊的 _internal 改名（可能有檔案被占用）")
        _retry(lambda: os.replace(exe, old_exe), f"舊的 {EXE_NAME} 改名")
        L("舊的已改名備份（_internal_old、.old），複製新的檔案")
        shutil.copytree(new_dir / "_internal", internal)
        shutil.copy2(new_dir / EXE_NAME, exe)
    except Exception as e:
        L(f"換檔失敗：{e}")
        return restore(str(e))
    L("檔案換好了，打開新的 EXE、等它回報開成功（最多 3 分鐘）")
    try:
        okf.unlink()
    except OSError:
        pass
    try:
        p = start(exe)
    except Exception as e:
        return restore(f"新的 EXE 打不開：{e}")
    t1 = time.time()
    while time.time() - t1 < 180:
        time.sleep(2)
        if okf.exists():
            L(f"成功：新的 EXE 開起來了（{time.time() - t1:.0f} 秒），刪掉舊的備份")
            try:
                okf.unlink()
            except OSError:
                pass
            shutil.rmtree(old_int, ignore_errors=True)
            try:
                old_exe.unlink()
            except OSError:
                pass
            return 0
        if p.poll() is not None:
            return restore(f"新的 EXE 自己關掉了（結束代碼 {p.returncode}）", p)
    L("新的 EXE 還在跑但 3 分鐘沒回報，先留著舊的備份（_internal_old、.old）")
    return 0


def main():
    if "--launcher-selftest" in sys.argv:
        return selftest()
    if "--fs-swap" in sys.argv:
        return swap(Path(sys.executable).resolve().parent, _arg("--root"), int(_arg("--pid", "0") or 0))
    os.chdir(ROOT)
    log(f"啟動 {sys.executable}")
    backup, applied = apply_updates()
    if not (APP_DIR / "app.py").exists():
        log("找不到 app\\app.py")
        note(f"找不到程式檔：\n{APP_DIR / 'app.py'}\n\n請重新安裝或放入更新檔。", error=True, secs=60)
        return 1
    msgs = []
    if applied:
        log("已套用：" + "、".join(applied))
        msgs.append("已更新到 V" + applied[-1])
    cleanup_shell()
    try:
        return run_app(msgs) or 0
    except SystemExit as e:
        return e.code or 0
    except Exception:
        err = traceback.format_exc()
        log("START FAILED\n" + err)
        if backup is not None:
            restore(backup)
            log(f"ROLLED BACK to {backup}（新版開不起來，退回更新前的版本再開一次）")
            if getattr(sys, "frozen", False):
                os.execv(sys.executable, [sys.executable] + sys.argv[1:])
        else:
            note("程式啟動失敗，詳細錯誤記錄在 LOG\\update_log.txt。\n\n" + err[-800:], error=True, secs=60)
        return 1


if __name__ == "__main__":
    try:
        rc = main()
    except BaseException:
        log("啟動器出錯\n" + traceback.format_exc())
        rc = 3
    sys.exit(rc)
