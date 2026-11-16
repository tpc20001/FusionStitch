"""Replace the icon embedded in FusionStitch.exe (what Explorer / the taskbar shortcut show).

The running exe cannot be written, but Windows lets it be *renamed*, so:
  1. copy FusionStitch.exe -> FusionStitch.exe.new
  2. swap the icon resources inside the copy (Windows UpdateResource API)
  3. put back the PyInstaller archive that is appended after the PE image
     (UpdateResource rewrites the file and would drop it)
  4. check the copy, then rename FusionStitch.exe -> FusionStitch.exe.old and the copy into place
  5. tell Explorer to refresh its icon cache
The next start deletes the .old file. If anything looks wrong, nothing is renamed.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import core

ICON_TAG = "043"                  # bump when the icon changes again
MEI_MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
RT_ICON, RT_GROUP_ICON = 3, 14


def exe_path() -> Path | None:
    for p in (Path(sys.executable) if getattr(sys, "frozen", False) else None, core.ROOT / "FusionStitch.exe"):
        if p and p.name.lower() == "fusionstitch.exe" and p.is_file():
            return p
    return None


def ico_path() -> Path:
    return core.APP_DIR / "FusionStitch.ico"


# ---------------------------------------------------------------- PE helpers (pure Python)
def pe_image_end(data: bytes) -> tuple[int, int]:
    """(end of the last section's raw data, size of the certificate table)."""
    if data[:2] != b"MZ":
        raise ValueError("不是 Windows 程式檔")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("PE 標頭錯誤")
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    cert_size = struct.unpack_from("<I", data, dd + 4 * 8 + 4)[0]      # data directory 4 = security
    end = 0
    sec = opt + opt_size
    for i in range(nsec):
        raw_size, raw_ptr = struct.unpack_from("<II", data, sec + 40 * i + 16)
        end = max(end, raw_ptr + raw_size)
    return end, cert_size


def overlay_of(data: bytes) -> bytes:
    end, cert = pe_image_end(data)
    if cert:
        raise ValueError("程式檔有數位簽章，不自動修改")
    ov = data[end:]
    if MEI_MAGIC not in ov[-4096:]:
        raise ValueError("找不到程式附帶的資料（不是預期的格式），不自動修改")
    return ov


def read_ico(path: Path) -> list[tuple[bytes, bytes]]:
    """[(14-byte group entry without id, image bytes)]"""
    d = path.read_bytes()
    _res, typ, n = struct.unpack_from("<HHH", d, 0)
    if typ != 1 or not n:
        raise ValueError("ico 檔格式錯誤")
    out = []
    for i in range(n):
        w, h, cc, r, planes, bpp, size, off = struct.unpack_from("<BBBBHHII", d, 6 + 16 * i)
        out.append((struct.pack("<BBBBHHI", w, h, cc, r, planes, bpp, size), d[off:off + size]))
    return out


def group_data(entries, first_id: int) -> bytes:
    b = struct.pack("<HHH", 0, 1, len(entries))
    for i, (head, _img) in enumerate(entries):
        b += head + struct.pack("<H", first_id + i)
    return b


# ---------------------------------------------------------------- Windows resource update
def _update_resources(path: Path, ico: Path):
    import ctypes
    from ctypes import wintypes as W
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.restype = W.HMODULE
    k32.LoadLibraryExW.argtypes = [W.LPCWSTR, W.HANDLE, W.DWORD]
    k32.FreeLibrary.argtypes = [W.HMODULE]
    k32.BeginUpdateResourceW.restype = W.HANDLE
    k32.BeginUpdateResourceW.argtypes = [W.LPCWSTR, W.BOOL]
    k32.UpdateResourceW.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, W.WORD, ctypes.c_void_p, W.DWORD]
    k32.EndUpdateResourceW.argtypes = [W.HANDLE, W.BOOL]
    NAMEPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    LANGPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMODULE, ctypes.c_void_p, ctypes.c_void_p, W.WORD, ctypes.c_void_p)
    k32.EnumResourceNamesW.argtypes = [W.HMODULE, ctypes.c_void_p, NAMEPROC, ctypes.c_void_p]
    k32.EnumResourceLanguagesW.argtypes = [W.HMODULE, ctypes.c_void_p, ctypes.c_void_p, LANGPROC, ctypes.c_void_p]

    # 1. list existing icon resources (name, language)
    existing = []                                    # (type, name int|str, lang)
    h = k32.LoadLibraryExW(str(path), None, 0x2)     # LOAD_LIBRARY_AS_DATAFILE
    if not h:
        raise OSError(f"讀不到程式檔資源（{ctypes.get_last_error()}）")
    try:
        for rtype in (RT_GROUP_ICON, RT_ICON):
            names = []

            def on_name(_m, _t, name, _p):
                names.append(name if name < 0x10000 else ctypes.wstring_at(name))
                return True
            cb = NAMEPROC(on_name)
            k32.EnumResourceNamesW(h, rtype, cb, None)
            for nm in names:
                langs = []

                def on_lang(_m, _t, _n, lang, _p):
                    langs.append(lang)
                    return True
                lcb = LANGPROC(on_lang)
                key = ctypes.c_wchar_p(nm) if isinstance(nm, str) else nm
                k32.EnumResourceLanguagesW(h, rtype, key, lcb, None)
                existing += [(rtype, nm, lg) for lg in langs]
    finally:
        k32.FreeLibrary(h)
    groups = [e for e in existing if e[0] == RT_GROUP_ICON]
    group_name = groups[0][1] if groups else 1
    lang = groups[0][2] if groups else 0

    # 2. delete old, add new
    entries = read_ico(ico)
    hu = k32.BeginUpdateResourceW(str(path), False)
    if not hu:
        raise OSError(f"無法開始修改程式檔（{ctypes.get_last_error()}）")
    keep = []                                        # keep buffers alive until EndUpdateResource
    ok = False
    try:
        for rtype, nm, lg in existing:
            key = ctypes.c_wchar_p(nm) if isinstance(nm, str) else nm
            keep.append(key)
            k32.UpdateResourceW(hu, rtype, ctypes.cast(key, ctypes.c_void_p) if isinstance(nm, str) else nm, lg, None, 0)
        first = 1
        for i, (_head, img) in enumerate(entries):
            buf = ctypes.create_string_buffer(img, len(img))
            keep.append(buf)
            if not k32.UpdateResourceW(hu, RT_ICON, first + i, lang, buf, len(img)):
                raise OSError(f"寫入圖示失敗（{ctypes.get_last_error()}）")
        gd = group_data(entries, first)
        gbuf = ctypes.create_string_buffer(gd, len(gd))
        keep.append(gbuf)
        gkey = ctypes.c_wchar_p(group_name) if isinstance(group_name, str) else group_name
        keep.append(gkey)
        if not k32.UpdateResourceW(hu, RT_GROUP_ICON, ctypes.cast(gkey, ctypes.c_void_p) if isinstance(group_name, str) else group_name,
                                   lang, gbuf, len(gd)):
            raise OSError(f"寫入圖示群組失敗（{ctypes.get_last_error()}）")
        ok = True
    finally:
        if not k32.EndUpdateResourceW(hu, not ok) and ok:
            raise OSError(f"儲存程式檔失敗（{ctypes.get_last_error()}）")


def _refresh_explorer(path: Path):
    try:
        import ctypes
        sh = ctypes.windll.shell32
        sh.SHChangeNotify(0x00002000, 0x0005, ctypes.c_wchar_p(str(path)), None)   # SHCNE_UPDATEITEM, SHCNF_PATHW
        sh.SHChangeNotify(0x08000000, 0x0000, None, None)                           # SHCNE_ASSOCCHANGED
    except Exception:
        pass
    try:
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ie4uinit.exe"
        if exe.exists():
            subprocess.Popen([str(exe), "-show"], creationflags=0x08000000)          # CREATE_NO_WINDOW
    except Exception:
        pass


# ---------------------------------------------------------------- public
def cleanup_old():
    exe = exe_path()
    if not exe:
        return
    for p in exe.parent.glob("FusionStitch.exe.old*"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in exe.parent.glob("FusionStitch.exe.new"):
        try:
            p.unlink()
        except OSError:
            pass


def needed() -> bool:
    return sys.platform == "win32" and exe_path() is not None and ico_path().exists() \
        and str(core.cfg("exe_icon_tag") or "") != ICON_TAG


def apply() -> str:
    """Swap the exe icon. Returns a message; raises on failure (nothing is changed then)."""
    if sys.platform != "win32":
        raise OSError("只能在 Windows 上執行")
    exe = exe_path()
    if exe is None:
        raise OSError("找不到 FusionStitch.exe")
    ico = ico_path()
    if not ico.exists():
        raise OSError("更新包裡沒有圖示檔")
    data = exe.read_bytes()
    overlay = overlay_of(data)
    new = exe.with_name(exe.name + ".new")
    shutil.copyfile(exe, new)
    try:
        _update_resources(new, ico)
        d2 = new.read_bytes()
        end2, _c = pe_image_end(d2)
        body = d2[:end2]
        with open(new, "wb") as f:                    # PE image + original archive, exactly once
            f.write(body)
            f.write(overlay)
        d3 = new.read_bytes()
        if not d3.endswith(overlay) or len(d3) != end2 + len(overlay) or d3[:2] != b"MZ":
            raise OSError("檢查新程式檔失敗")
        old = exe.with_name(exe.name + ".old")
        if old.exists():
            try:
                old.unlink()
            except OSError:
                old = exe.with_name(f"{exe.name}.old{os.getpid()}")
        os.replace(exe, old)                          # allowed while running
        try:
            os.replace(new, exe)
        except OSError:
            os.replace(old, exe)                      # put the original back
            raise
    finally:
        if new.exists():
            try:
                new.unlink()
            except OSError:
                pass
    core.set_cfg("exe_icon_tag", ICON_TAG)
    core.log(f"exe icon updated ({ICON_TAG}): {exe}")
    _refresh_explorer(exe)
    return "FusionStitch.exe 的圖示已更新。檔案總管若還顯示舊圖示，按 F5 重新整理或重新開機後就會變。"


def auto_apply_later(delay: float = 8.0):
    """Called at start: clean up and, once per icon version, swap the icon in the background."""
    def run():
        import time
        time.sleep(delay)
        try:
            cleanup_old()
            if needed():
                core.log(apply())
        except Exception as e:
            core.set_cfg("exe_icon_tag", ICON_TAG)    # do not retry on every start; the button still works
            core.log(f"exe icon update skipped: {e}")
    threading.Thread(target=run, daemon=True, name="exeicon").start()
