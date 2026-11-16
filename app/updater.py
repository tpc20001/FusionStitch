r"""Update packages: vX.Y.Z_Update.zip = manifest.json + app/<files>.

Same scheme as the user's other tools: validate → back up app\ → write files →
hot reload (running conversions keep going) → automatic rollback if the new
code cannot load. The launcher applies UPDATE\*.zip on start with the same code.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import core


def read_package(path: str) -> dict:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json").decode("utf-8-sig")) if "manifest.json" in names else {}
        files = {m[4:]: zf.read(m) for m in names if m.startswith("app/") and not m.endswith("/")}
    if not files:
        raise RuntimeError("更新包裡沒有 app/ 程式檔")
    if str(manifest.get("app") or "FusionStitch") != "FusionStitch":
        raise RuntimeError(f"這是 {manifest.get('app')} 的更新包，不是 FusionStitch 的")
    if "app.py" not in files and "core.py" not in files:
        raise RuntimeError("這不是 FusionStitch 的更新包")
    for name in files:
        p = Path(name)
        if ".." in p.parts or p.is_absolute() or "/" in name or "\\" in name:
            raise RuntimeError(f"不安全的路徑：{name}")
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data.decode("utf-8"), name, "exec")       # syntax check before anything is written
    need = int(manifest.get("requires_launcher", 1))
    if need > core.LAUNCHER_API:
        raise RuntimeError("此更新需要新版啟動器（FusionStitch.exe），請用完整安裝包更新")
    ver = str(manifest.get("version") or "?")
    notes = []
    try:
        log = json.loads(files["CHANGELOG.json"].decode("utf-8-sig")) if "CHANGELOG.json" in files else []
        entry = next((e for e in log if str(e.get("version")) == ver), None)
        notes = list((entry or {}).get("items", []))
    except Exception:
        pass
    return {"path": path, "files": files, "version": ver, "notes": notes, "manifest": manifest}


def backup_current(tag: str) -> Path:
    dst = core.BACKUP_DIR / (time.strftime("%Y%m%d_%H%M%S") + "_" + tag)
    shutil.copytree(core.APP_DIR, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def write_files(files: dict, backup: Path):
    cur = core.APP_DIR
    try:
        for name, data in files.items():
            dst = cur / name
            tmp = dst.with_name(dst.name + ".new")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        shutil.rmtree(cur / "__pycache__", ignore_errors=True)
    except Exception:
        restore(backup)
        raise


def restore(backup: Path):
    for f in Path(backup).iterdir():
        if f.is_file():
            shutil.copy2(f, core.APP_DIR / f.name)
    shutil.rmtree(core.APP_DIR / "__pycache__", ignore_errors=True)


def backups() -> list[Path]:
    if not core.BACKUP_DIR.exists():
        return []
    return sorted((p for p in core.BACKUP_DIR.iterdir() if p.is_dir()), reverse=True)


def backup_version(p: Path) -> str:
    try:
        import re
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', (p / "core.py").read_text(encoding="utf-8"))
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def log_update(msg):
    core.log(msg, "update_log.txt")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
