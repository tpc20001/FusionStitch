"""Computer performance sampler (CPU, RAM, NVIDIA GPU engines, disks per drive letter).

One sampler per program (kept on the QApplication so hot updates share it).
GPU numbers come from a long-running `nvidia-smi -lms` process (no polling
cost); disk throughput is mapped from physical disks to drive letters with
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, so a RAID volume sums its members.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import shutil
import string
import subprocess
import sys
import threading
import time
from collections import deque

import psutil

HISTORY = 180      # seconds kept for the charts
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

GPU_FIELDS = ["utilization.gpu", "utilization.encoder", "utilization.decoder", "temperature.gpu",
              "power.draw", "power.limit", "memory.used", "memory.total", "fan.speed", "clocks.gr", "name"]


def _volume_disks(letter: str) -> list[int]:
    """Physical disk numbers backing a drive letter (RAID volumes return several)."""
    if sys.platform != "win32":
        return []
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wt.HANDLE
    h = k32.CreateFileW(f"\\\\.\\{letter}:", 0, 3, None, 3, 0, None)
    if h in (None, wt.HANDLE(-1).value):
        return []
    try:
        buf = ctypes.create_string_buffer(8 + 24 * 32)
        ret = wt.DWORD()
        ok = k32.DeviceIoControl(wt.HANDLE(h), 0x00560000, None, 0, buf, len(buf), ctypes.byref(ret), None)
        if not ok:
            return []
        n = int.from_bytes(buf.raw[0:4], "little")
        return [int.from_bytes(buf.raw[8 + i * 24:12 + i * 24], "little") for i in range(n)]
    finally:
        k32.CloseHandle(wt.HANDLE(h))


def fixed_drives() -> list[str]:
    out = []
    for p in psutil.disk_partitions(all=False):
        letter = p.device[:1].upper()
        if letter in string.ascii_uppercase and "cdrom" not in p.opts:
            out.append(letter)
    return sorted(set(out))


class PerfSampler:
    VER = 1

    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {"t": 0}
        self.hist: dict[str, deque] = {}
        self.gpu: dict = {}
        self.gpu_name = ""
        self._gpu_proc = None
        self.drives = fixed_drives()
        self.drive_disks = {d: _volume_disks(d) for d in self.drives}
        self._last_io = None
        self.job_info: dict = {}
        self.stop = False
        psutil.cpu_percent(None)
        threading.Thread(target=self._gpu_loop, daemon=True, name="perf-gpu").start()
        threading.Thread(target=self._loop, daemon=True, name="perf").start()

    # ---- GPU via nvidia-smi (streams one line per second)
    def _gpu_loop(self):
        exe = shutil.which("nvidia-smi")
        if not exe:
            return
        while not self.stop:
            try:
                self._gpu_proc = subprocess.Popen(
                    [exe, "--query-gpu=" + ",".join(GPU_FIELDS), "--format=csv,noheader,nounits", "-lms", "1000"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    creationflags=NO_WINDOW, text=True)
                for line in self._gpu_proc.stdout:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < len(GPU_FIELDS):
                        continue
                    def num(s):
                        try:
                            return float(s)
                        except ValueError:
                            return None
                    g = dict(zip(GPU_FIELDS, parts))
                    self.gpu_name = g["name"]
                    with self.lock:
                        self.gpu = {"gpu": num(g["utilization.gpu"]), "enc": num(g["utilization.encoder"]),
                                    "dec": num(g["utilization.decoder"]), "temp": num(g["temperature.gpu"]),
                                    "power": num(g["power.draw"]), "power_limit": num(g["power.limit"]),
                                    "mem_used": num(g["memory.used"]), "mem_total": num(g["memory.total"]),
                                    "fan": num(g["fan.speed"]), "clock": num(g["clocks.gr"]), "t": time.time()}
                    if self.stop:
                        break
            except Exception:
                pass
            time.sleep(3)

    def _push(self, key, value):
        d = self.hist.get(key)
        if d is None:
            d = self.hist[key] = deque(maxlen=HISTORY)
        d.append(value)

    def _loop(self):
        while not self.stop:
            t = time.time()
            cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            try:
                io = psutil.disk_io_counters(perdisk=True)
            except Exception:
                io = {}
            disks = {}
            if self._last_io is not None:
                dt = max(1e-3, t - self._last_io[0])
                prev = self._last_io[1]
                for letter, nums in self.drive_disks.items():
                    rd = wr = busy = 0.0
                    for n in nums:
                        a, b = io.get(f"PhysicalDrive{n}"), prev.get(f"PhysicalDrive{n}")
                        if a and b:
                            rd += (a.read_bytes - b.read_bytes) / dt
                            wr += (a.write_bytes - b.write_bytes) / dt
                            bt = getattr(a, "busy_time", 0) - getattr(b, "busy_time", 0)
                            busy = max(busy, min(100.0, bt / (dt * 10)))
                    disks[letter] = {"read": rd, "write": wr, "busy": busy}
            self._last_io = (t, io)
            with self.lock:
                g = dict(self.gpu) if self.gpu and t - self.gpu.get("t", 0) < 5 else {}
                self.data = {"t": t, "cpu": cpu, "ram_used": vm.used, "ram_total": vm.total, "ram_pct": vm.percent,
                             "gpu": g, "disks": disks, "cores": psutil.cpu_count()}
                self._push("cpu", cpu)
                self._push("ram", vm.percent)
                for k in ("gpu", "enc", "dec", "temp"):
                    self._push(k, g.get(k) or 0)
                for letter, d in disks.items():
                    self._push("disk_" + letter, (d["read"] + d["write"]) / 1048576)
                self._push("fps", float(self.job_info.get("fps") or 0))
            time.sleep(max(0.2, 1.0 - (time.time() - t)))

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.data), {k: list(v) for k, v in self.hist.items()}

    def shutdown(self):
        self.stop = True
        try:
            if self._gpu_proc:
                self._gpu_proc.kill()
        except Exception:
            pass
