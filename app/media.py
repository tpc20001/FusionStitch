"""Chapter-aware decoding.

A Fusion recording is split into chapters per lens (GPFR0012, GF010012, ...).
Every read seeks *inside the chapter file that holds the wanted frame*
(fast keyframe seek) instead of seeking through an FFmpeg concat list, which
decodes from the very first chapter and was the cause of the CPU spikes and
"無法解碼影格" errors in the web version.
"""
from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

import ffm as ff


@lru_cache(maxsize=512)
def _probe_cached(path: str, mtime: float) -> dict:
    return ff.probe(path)


def probe(path: str) -> dict:
    return _probe_cached(path, os.path.getmtime(path))


@dataclass
class Timeline:
    files: list[str]
    fps: float
    fps_str: str
    width: int
    height: int
    frames: list[int] = field(default_factory=list)       # frames per chapter
    durations: list[float] = field(default_factory=list)
    color_range: str | None = None
    audio: dict | None = None

    @property
    def total_frames(self) -> int:
        return sum(self.frames)

    @property
    def duration(self) -> float:
        return sum(self.durations)

    @property
    def starts(self) -> list[int]:
        out, acc = [], 0
        for n in self.frames:
            out.append(acc)
            acc += n
        return out

    def locate(self, frame: int) -> tuple[int, int]:
        """Global frame -> (chapter index, frame inside chapter)."""
        frame = max(0, min(frame, self.total_frames - 1))
        for i, s in enumerate(self.starts):
            if frame < s + self.frames[i]:
                return i, frame - s
        return len(self.files) - 1, self.frames[-1] - 1


def timeline(files: list[str]) -> Timeline:
    infos = [probe(p) for p in files]
    i0 = infos[0]
    tl = Timeline(files=list(files), fps=i0["fps"], fps_str=i0["fps_str"], width=i0["width"],
                  height=i0["height"], color_range=i0.get("color_range"), audio=i0.get("audio"))
    for inf in infos:
        n = inf.get("nb_frames") or int(round(inf["duration"] * inf["fps"]))
        tl.frames.append(n)
        tl.durations.append(n / inf["fps"])
    return tl


class FrameReader(threading.Thread):
    """Decodes `count` frames starting at global frame `start` across chapters.

    raw=True: NV12 bytes straight from NVDEC (GPU paths); otherwise (Y, U, V)
    numpy planes. scale=(w, h) decodes resized on the GPU (preview).
    Frames before the timeline start (negative start, used for the back-lens
    offset) repeat the first frame so both lenses stay frame-locked.
    """

    def __init__(self, tl: Timeline, start: int, count: int | None = None, raw=True,
                 scale: tuple[int, int] | None = None, gpu=True, qsize=4, step: int = 1):
        super().__init__(daemon=True)
        self.tl, self.start_frame, self.raw, self.scale, self.gpu = tl, start, raw, scale, gpu
        self.step = max(1, int(step))
        # count = frames delivered (every `step`-th source frame)
        self.count = count if count is not None else -(-(tl.total_frames - max(0, start)) // self.step)
        self.w, self.h = scale or (tl.width, tl.height)
        self.size = self.w * self.h * 3 // 2
        self.q: queue.Queue = queue.Queue(maxsize=qsize)
        self.stop_flag = False
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None
        self.delivered = 0

    def _cmd(self, path, local_frame, frames):
        cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
        on_gpu = self.gpu and (self.scale or self.step > 1)       # keep frames on the GPU until selected
        if self.gpu:
            cmd += ["-hwaccel", "cuda"]
            if on_gpu:
                cmd += ["-hwaccel_output_format", "cuda"]
        if local_frame > 0:
            cmd += ["-ss", f"{local_frame / self.tl.fps:.6f}"]
        cmd += ["-i", path, "-map", "0:v:0", "-frames:v", str(frames)]
        vf = []
        if self.step > 1:
            vf.append(f"select=not(mod(n\\,{self.step}))")
        if self.scale:
            vf.append(f"scale_cuda={self.w}:{self.h}:format=nv12" if self.gpu else f"scale={self.w}:{self.h}")
        if on_gpu:
            vf.append("hwdownload,format=nv12")
        if vf:
            cmd += ["-vf", ",".join(vf)]
        if self.step > 1:
            cmd += ["-fps_mode", "passthrough"]
        cmd += ["-f", "rawvideo", "-pix_fmt", "nv12" if self.raw else "yuv420p", "-"]
        return cmd

    def _emit(self, buf):
        if self.raw:
            item = buf
        else:
            a = np.frombuffer(buf, np.uint8)
            w, h = self.w, self.h
            item = (a[:w * h].reshape(h, w), a[w * h:w * h * 5 // 4].reshape(h // 2, w // 2),
                    a[w * h * 5 // 4:].reshape(h // 2, w // 2))
        while not self.stop_flag:
            try:
                self.q.put(item, timeout=0.5)
                self.delivered += 1
                return True
            except queue.Full:
                continue
        return False

    def run(self):
        st = self.step
        try:
            remaining = self.count
            frame = self.start_frame
            pad = 0
            if frame < 0:                      # back-lens offset before the start: repeat the first frame
                pad = -(-(-frame) // st)
                frame += pad * st
            first = None
            while remaining > 0 and not self.stop_flag and frame < self.tl.total_frames:
                ci, local = self.tl.locate(frame)
                avail = self.tl.frames[ci] - local
                take = min(remaining, -(-avail // st))           # selected frames in this chapter
                self.proc = subprocess.Popen(self._cmd(self.tl.files[ci], local, take),
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             stdin=subprocess.DEVNULL, creationflags=ff.NO_WINDOW, bufsize=0)
                got_chapter = 0
                while got_chapter < take and not self.stop_flag:
                    buf = bytearray(self.size)
                    view, got = memoryview(buf), 0
                    while got < self.size:
                        n = self.proc.stdout.readinto(view[got:])
                        if not n:
                            break
                        got += n
                    if got < self.size:
                        break
                    if first is None:
                        first = buf
                        for _ in range(pad):
                            if not self._emit(bytearray(buf)):
                                break
                            remaining -= 1
                    if remaining <= 0 or not self._emit(buf):
                        break
                    got_chapter += 1
                    remaining -= 1
                err = b""
                try:
                    self.proc.stdout.close()
                    err = self.proc.stderr.read()
                except Exception:
                    pass
                rc = self.proc.wait()
                if self.stop_flag:
                    break
                if got_chapter == 0 and take > 0:
                    if rc != 0 and self.gpu:
                        self.gpu = False                 # e.g. a codec NVDEC cannot do: retry on the CPU
                        continue
                    self.error = err.decode(errors="replace").strip()[-600:] or "解碼器沒有輸出影格"
                    break
                if got_chapter < take:
                    # chapter shorter than its header says: continue at the next selected frame
                    # at or after the next chapter's start (keeps the step phase)
                    nxt = self.tl.starts[ci] + self.tl.frames[ci]
                    frame += -(-(nxt - frame) // st) * st
                else:
                    frame += take * st
        except Exception as e:
            self.error = str(e)
        finally:
            self.q.put(None)

    def close(self):
        self.stop_flag = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass


def read_frame(tl: Timeline, frame: int, raw=False, scale=None, gpu=True):
    """One frame at a global frame index (fast, seeks inside the right chapter)."""
    r = FrameReader(tl, frame, 1, raw=raw, scale=scale, gpu=gpu, qsize=2)
    r.start()
    item = r.q.get(timeout=60)
    r.close()
    if item is None:
        raise RuntimeError("無法解碼影格：" + (r.error or ""))
    return item


@lru_cache(maxsize=64)
def wav_layout(path: str) -> tuple[int, str, int, int, int] | None:
    """(data offset, raw format, rate, channels, data bytes) of a PCM WAV.

    Fusion WAVs carry a wrong byte rate in the header (128000 instead of
    768000 for 4 x 32-bit x 48 kHz), so FFmpeg's -ss lands at 1/6 of the
    wanted time. We seek by byte offset into the raw PCM instead."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return None
            fmt = None
            while True:
                h = f.read(8)
                if len(h) < 8:
                    return None
                cid, n = struct.unpack("<4sI", h)
                if cid == b"fmt ":
                    fmt = f.read(n)
                    if n & 1:
                        f.read(1)
                elif cid == b"data":
                    if not fmt:
                        return None
                    tag, ch, rate = struct.unpack("<HHI", fmt[:8])
                    bits = struct.unpack("<H", fmt[14:16])[0]
                    if tag == 0xFFFE and len(fmt) >= 26:   # WAVE_FORMAT_EXTENSIBLE
                        tag = struct.unpack("<H", fmt[24:26])[0]
                    raw = {(1, 16): "s16le", (1, 24): "s24le", (1, 32): "s32le",
                           (3, 32): "f32le", (3, 64): "f64le"}.get((tag, bits))
                    if not raw:
                        return None
                    size = min(n, os.path.getsize(path) - f.tell())
                    return f.tell(), raw, rate, ch, size
                else:
                    f.seek(n + (n & 1), 1)
    except (OSError, struct.error):
        return None


# Measured on real footage: the WAV runs 1270 samples behind the camera's own
# AAC track (same in every chapter and clip), so skip that much for A/V sync.
WAV_LAG_S = 1270 / 48000


def _audio_input(path: str, ss: float, t: float) -> list[str]:
    lay = wav_layout(path) if path.lower().endswith(".wav") else None
    if not lay:
        return ["-ss", f"{ss:.6f}", "-t", f"{t:.6f}", "-i", path]
    off, raw, rate, ch, _ = lay
    block = ch * int(raw[1:3]) // 8
    ss += WAV_LAG_S
    return ["-f", raw, "-ar", str(rate), "-ac", str(ch),
            "-skip_initial_bytes", str(off + int(round(ss * rate)) * block),
            "-t", f"{t:.6f}", "-i", path]


def build_audio(tl: Timeline, start_frame: int, count: int, out_path: str,
                wav_files: list[str] | None = None) -> str | None:
    """Cut the audio of [start, start+count) frames across chapters into one file."""
    if not tl.audio and not wav_files:
        return None
    a = start_frame / tl.fps
    b = (start_frame + count) / tl.fps
    inputs, lens, t0 = [], [], 0.0
    srcs = wav_files or tl.files
    for i, path in enumerate(srcs):
        d = tl.durations[i] if i < len(tl.durations) else probe(path)["duration"]
        s, e = max(a, t0), min(b, t0 + d)
        if e > s:
            inputs += _audio_input(path, s - t0, e - s)
            lens.append(e - s)
        t0 += d
    n = len(lens)
    if n == 0:
        return None
    if wav_files:
        # a chapter WAV can end a few ms before its video chapter: pad each piece
        # to its exact length so the next chapter stays in sync
        filt = "".join(f"[{i}:a:0]apad=whole_dur={L:.6f}[p{i}];" for i, L in enumerate(lens))
        filt += "".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    else:
        filt = "".join(f"[{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    codec = ["-c:a", "pcm_s24le"] if wav_files else ["-c:a", "aac", "-b:a", "320k"]
    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-nostdin", *inputs,
           "-filter_complex", filt, "-map", "[a]", *codec, out_path]
    r = ff.run(cmd)
    if r.returncode:
        raise RuntimeError("聲音處理失敗：" + r.stderr.decode(errors="replace")[-400:])
    return out_path


def peak_db(path: str) -> float:
    """Max volume of the first audio stream in dB (-inf / -200 when silent or unreadable)."""
    cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-i", path, "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    r = ff.run(cmd)
    for line in r.stderr.decode(errors="replace").splitlines():
        if "max_volume:" in line:
            try:
                return float(line.split("max_volume:")[1].split("dB")[0])
            except ValueError:
                return -200.0
    return -200.0


SILENT_DB = -60.0


def build_stereo_audio(tf: Timeline, tb: Timeline, start_frame: int, count: int, out_path: str,
                       wav_files: list[str] | None = None) -> tuple[str | None, str]:
    """Stereo AAC for the output. Tries the front MP4, the back MP4, then the 4-channel WAVs
    (mixed down), and keeps the first source that is not silent. Returns (path, description)."""
    tried = []
    for label, tl in (("前鏡頭", tf), ("後鏡頭", tb)):
        if not tl.audio:
            tried.append(f"{label}沒有聲軌")
            continue
        try:
            if build_audio(tl, start_frame, count, out_path):
                pk = peak_db(out_path)
                if pk > SILENT_DB:
                    return out_path, f"立體聲（{label}，最大 {pk:.0f} dB）"
                tried.append(f"{label}是靜音（{pk:.0f} dB）")
        except RuntimeError as e:
            tried.append(f"{label}失敗：{e}")
    if wav_files:
        pcm = out_path + ".pcm.wav"
        try:
            if build_audio(tf, start_frame, count, pcm, wav_files):
                mix = "+".join(f"0.25*c{i}" for i in range(4))
                cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-nostdin", "-i", pcm,
                       "-af", f"pan=stereo|c0={mix}|c1={mix},loudnorm=I=-18:TP=-1.5", "-ar", "48000",
                       "-c:a", "aac", "-b:a", "320k", "-f", "mp4", out_path]
                r = ff.run(cmd)
                if not r.returncode:
                    pk = peak_db(out_path)
                    if pk > SILENT_DB:
                        return out_path, f"立體聲（360 WAV 混音，最大 {pk:.0f} dB）"
                    tried.append(f"WAV 是靜音（{pk:.0f} dB）")
                else:
                    tried.append("WAV 混音失敗")
        except RuntimeError as e:
            tried.append(f"WAV 失敗：{e}")
        finally:
            try:
                os.remove(pcm)
            except OSError:
                pass
    try:
        os.remove(out_path)
    except OSError:
        pass
    return None, "⚠ 無聲音：" + "；".join(tried or ["找不到聲音來源"])


# ---------------------------------------------------------------- 360 spatial audio
# The Fusion back card writes one 4-channel WAV per chapter (pcm_s32le, 48 kHz).
# Measured on real footage (V1.1.0): it is NOT ambiX B-format. The four channels
# are in phase and highly coherent below 1 kHz with near-equal levels (closely
# spaced capsules), and the third channel often runs 6-20 dB above the first,
# which no B-format component can do relative to W (|Y|,|Z|,|X| <= W).
# So the WAV holds raw capsule signals; GoPro does not document their
# placement. We level-match the capsules and apply the standard tetrahedral
# A->B matrix assuming the order FLU, FRD, BLD, BRU. The output is physically
# valid first-order ambiX (ACN W,Y,Z,X, SN3D), but the directions are an
# assumption: the feature is marked experimental in the UI.
_SQ3 = 3 ** 0.5 / 4
TETRA_TO_AMBIX = (            # rows W, Y, Z, X; columns capsule FLU, FRD, BLD, BRU
    (0.25, 0.25, 0.25, 0.25),
    (_SQ3, -_SQ3, _SQ3, -_SQ3),
    (_SQ3, -_SQ3, -_SQ3, _SQ3),
    (_SQ3, _SQ3, -_SQ3, -_SQ3),
)


def channel_rms_db(path: str) -> list[float]:
    cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-i", path, "-map", "0:a:0",
           "-af", "astats=measure_perchannel=RMS_level:measure_overall=none", "-f", "null", "-"]
    r = ff.run(cmd)
    out = []
    for line in r.stderr.decode(errors="replace").splitlines():
        if "RMS level dB:" in line:
            v = line.rsplit(":", 1)[1].strip()
            try:
                out.append(float(v))
            except ValueError:   # "-inf" on a silent channel
                out.append(float("-inf"))
    return out


def build_spatial_audio(tl: Timeline, start_frame: int, count: int, out_path: str,
                        wav_files: list[str]) -> str | None:
    """Fusion WAVs of [start, start+count) -> 4-channel ambiX AAC (out_path, .m4a)."""
    pcm = out_path + ".pcm.wav"
    try:
        if not build_audio(tl, start_frame, count, pcm, wav_files):
            return None
        levels = channel_rms_db(pcm)
        if len(levels) != 4:
            raise RuntimeError("WAV 不是 4 聲道")
        live = sorted(v for v in levels if v > -120)
        ref = (live[len(live) // 2] if live else 0.0)
        gains = [10 ** (max(-24.0, min(24.0, ref - v)) / 20) if v > -120 else 1.0 for v in levels]
        rows = ["".join(f"{'-' if m < 0 else '+'}{abs(m) * g:.6f}*c{i}"
                        for i, (m, g) in enumerate(zip(row, gains))).lstrip("+")
                for row in TETRA_TO_AMBIX]
        pan = "pan=4.0|" + "|".join(f"c{k}={expr}" for k, expr in enumerate(rows))
        cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-nostdin", "-i", pcm,
               "-af", pan, "-c:a", "aac", "-b:a", "512k", "-f", "mp4", out_path]
        r = ff.run(cmd)
        if r.returncode:
            raise RuntimeError("空間音訊處理失敗：" + r.stderr.decode(errors="replace")[-400:])
        return out_path
    finally:
        try:
            os.remove(pcm)
        except OSError:
            pass
