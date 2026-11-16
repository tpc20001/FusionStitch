"""Which physical disk a folder is on, and whether it is an SSD (Windows, asked once, cached).
Used to advise where to put the 中繼 (SSD relay) and the output: reading the footage from one
HDD while writing the output to the same HDD makes its head jump back and forth."""
from __future__ import annotations

import json
import os
import subprocess
import threading

_info: dict | None = None
_lock = threading.Lock()


def _load() -> dict:
    global _info
    with _lock:
        if _info is not None:
            return _info
        _info = {}
        try:
            cmd = ("$p = Get-Partition | Where-Object DriveLetter | Select-Object DriveLetter,DiskNumber;"
                   "$d = Get-PhysicalDisk | Select-Object DeviceId,MediaType,FriendlyName;"
                   "@{p=@($p);d=@($d)} | ConvertTo-Json -Compress -Depth 4")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                               timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            j = json.loads(r.stdout or "{}")
            kinds = {str(d.get("DeviceId")): (str(d.get("MediaType") or ""), str(d.get("FriendlyName") or ""))
                     for d in j.get("d") or []}
            for p in j.get("p") or []:
                L = str(p.get("DriveLetter") or "").upper()
                n = str(p.get("DiskNumber"))
                if L:
                    k = kinds.get(n, ("", ""))
                    _info[L] = {"disk": n, "kind": k[0], "name": k[1]}
        except Exception:
            pass
        return _info


def drive(path: str) -> str:
    d = os.path.splitdrive(os.path.abspath(path or ""))[0]
    return d[:1].upper() if d and d[0].isalpha() else ""


def disk_of(path: str) -> str:
    """Physical disk id of a folder ('' = unknown; a network path counts by itself)."""
    L = drive(path)
    if not L:
        return path[:2] if path else ""
    return (_load().get(L) or {}).get("disk", L)


def kind(path: str) -> str:
    """'SSD', 'HDD' or ''."""
    return ((_load().get(drive(path)) or {}).get("kind") or "").upper().replace("UNSPECIFIED", "")


def same_disk(a: str, b: str) -> bool:
    return bool(a and b) and disk_of(a) == disk_of(b)


def ssd_drives() -> list[str]:
    return [L + ":\\" for L, v in sorted(_load().items()) if (v.get("kind") or "").upper() == "SSD"]


def label(path: str) -> str:
    L = drive(path)
    v = _load().get(L) or {}
    k = (v.get("kind") or "").upper()
    return f"{L}: {'SSD' if k == 'SSD' else ('硬碟 HDD' if k == 'HDD' else '磁碟')}" if L else ""


def volume_label(root: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0):
            return buf.value
    except Exception:
        pass
    return ""


def info(path: str) -> dict:
    """Drive facts for the folder pickers: {"drive", "kind", "model", "label", "total", "free"}."""
    import shutil
    L = drive(path)
    root = f"{L}:\\" if L else (path or "/")
    v = _load().get(L) or {}
    k = (v.get("kind") or "").upper()
    out = {"drive": root, "kind": k if k in ("SSD", "HDD") else "", "model": v.get("name") or "",
           "label": volume_label(root), "total": 0, "free": 0}
    try:
        du = shutil.disk_usage(root)
        out.update(total=du.total, free=du.free)
    except OSError:
        pass
    return out
