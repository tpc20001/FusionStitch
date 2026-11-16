"""Phone web page: conversion progress, pause/resume/cancel, computer load, update upload.

Off by default (設定 → 手機網頁). Listens on the LAN; every request needs the
6-digit PIN shown in the app (kept in a cookie after the first login).
The HTTP server runs in its own thread; actions that touch the UI (installing
an update) are handed to the main window through a Qt signal.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core

PAGE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FusionStitch</title>
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icon-192.png">
<meta name="theme-color" content="#0e1116"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="FusionStitch"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>
:root{color-scheme:dark}body{margin:0;background:#0e1116;color:#e8ecf2;font:15px/1.45 system-ui,"Microsoft JhengHei",sans-serif}
header{padding:12px 16px;background:#090c10;border-bottom:1px solid #252c35;display:flex;justify-content:space-between;align-items:center}
h1{font-size:17px;margin:0}main{padding:12px 16px;max-width:720px;margin:auto}
.card{background:#151a21;border:1px solid #252c35;border-radius:12px;padding:12px 14px;margin:10px 0}
.k{color:#8994a4;font-size:13px}.v{font-weight:800;font-size:18px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.bar{height:8px;background:#222933;border-radius:4px;overflow:hidden;margin:6px 0}.bar i{display:block;height:100%;background:#2b8ad8}
.job{border-top:1px solid #252c35;padding:10px 0}.job:first-child{border-top:0}
.st{font-size:12px;padding:1px 8px;border-radius:9px;background:#222933}.run{background:#1f5f99}.done{background:#1d5c3a}.err{background:#6b2430}.pau{background:#5c4a1d}
button{background:#171d26;color:#e8ecf2;border:1px solid #2a3340;border-radius:8px;padding:7px 12px;font:inherit;margin:4px 6px 0 0}
button.p{background:#e51e63;border:0;font-weight:800}input{font:inherit}
.muted{color:#8994a4;font-size:13px}#notes li{margin:4px 0}
</style></head><body>
<header><h1>FusionStitch</h1><span class="muted" id="ver"></span></header>
<main>
<div class="card"><div class="grid">
<div><div class="k">CPU</div><div class="v" id="cpu">—</div></div>
<div><div class="k">GPU 3D</div><div class="v" id="gpu">—</div></div>
<div><div class="k">NVENC</div><div class="v" id="enc">—</div></div>
<div><div class="k">GPU 溫度</div><div class="v" id="temp">—</div></div>
<div><div class="k">功耗</div><div class="v" id="pow">—</div></div>
<div><div class="k">記憶體</div><div class="v" id="ram">—</div></div>
</div></div>
<div class="card"><div class="k">轉檔佇列 <span id="sum"></span></div>
<button onclick="all_('pause')">⏸ 全部暫停</button><button onclick="all_('resume')">▶ 全部繼續</button>
<div id="relay" class="muted"></div><div id="jobs" class="muted">載入中…</div></div>
<div class="card"><div class="k">線上更新</div>
<p class="muted">選手機裡的 v版本_Update.zip，上傳後會先顯示更新內容，按「確定更新」才會開始。轉檔不會中斷。</p>
<input type="file" id="zip" accept=".zip"><button onclick="up()">上傳</button>
<div id="upd"></div></div>
</main>
<script>
const ST={queued:["排隊中",""],running:["轉檔中","run"],paused:["已暫停","pau"],done:["完成","done"],error:["失敗","err"],cancelled:["已取消",""]};
const fmt=s=>s==null?"—":(s>=3600?Math.floor(s/3600)+":"+String(Math.floor(s%3600/60)).padStart(2,"0"):Math.floor(s/60))+":"+String(Math.floor(s%60)).padStart(2,"0");
async function act(id,a){await fetch(`/api/job/${id}/${a}`,{method:"POST"});load()}
if("serviceWorker" in navigator)navigator.serviceWorker.register("/sw.js").catch(()=>{});
async function all_(a){await fetch(`/api/queue/${a}`,{method:"POST"});load()}
async function load(){
 const r=await fetch("/api/status");if(r.status==401){location.reload();return}
 const d=await r.json();ver.textContent="V"+d.version;
 const g=d.perf.gpu||{};cpu.textContent=Math.round(d.perf.cpu||0)+"%";gpu.textContent=g.gpu!=null?g.gpu+"%":"—";
 enc.textContent=g.enc!=null?g.enc+"%":"—";temp.textContent=g.temp!=null?g.temp+"°C":"—";pow.textContent=g.power!=null?Math.round(g.power)+" W":"—";
 ram.textContent=Math.round(d.perf.ram_pct||0)+"%";
 relay.textContent=d.relay||"";sum.textContent=d.summary||"";
 jobs.innerHTML=d.jobs.length?d.jobs.map(j=>{const s=ST[j.status]||[j.status,""];const p=(j.status=="done"?1:j.progress)*100;
  return `<div class="job"><b>${j.title}</b> <span class="st ${s[1]}">${s[0]}</span>
  <div class="bar"><i style="width:${p.toFixed(1)}%"></i></div>
  <div class="muted">${p.toFixed(1)}%${j.status=="running"?` · ${j.fps.toFixed(1)} fps · 剩 ${fmt(j.eta)}`:""} · ${j.name}</div>
  <div class="muted">${j.message||""}</div>
  ${j.status=="running"||j.status=="queued"?`<button onclick="act(${j.id},'pause')">暫停</button>`:""}
  ${j.status=="paused"||j.status=="error"?`<button onclick="act(${j.id},'resume')">繼續</button>`:""}
  ${["running","queued","paused"].includes(j.status)?`<button onclick="if(confirm('取消？'))act(${j.id},'cancel')">取消</button>`:""}</div>`}).join(""):"沒有工作";
}
async function up(){const f=zip.files[0];if(!f)return;upd.innerHTML="上傳中…";
 const r=await fetch("/api/update/upload",{method:"POST",body:f});const d=await r.json();
 if(!d.ok){upd.innerHTML=`<p style="color:#ff6577">${d.message}</p>`;return}
 upd.innerHTML=`<p><b>V${d.from} → V${d.to}</b></p><ul id="notes">${d.notes.map(n=>"<li>"+n+"</li>").join("")}</ul><button class="p" onclick="go()">確定更新</button>`}
async function go(){const r=await fetch("/api/update/apply",{method:"POST"});const d=await r.json();upd.innerHTML=`<p>${d.message}</p>`;setTimeout(load,4000)}
load();setInterval(load,2000);
</script></body></html>"""

LOGIN = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0e1116"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="FusionStitch">
<title>FusionStitch</title><style>body{background:#0e1116;color:#e8ecf2;font:16px system-ui,"Microsoft JhengHei";display:grid;place-items:center;height:100vh;margin:0}
form{background:#151a21;border:1px solid #252c35;border-radius:12px;padding:20px}input,button{font:inherit;padding:8px;border-radius:8px;border:1px solid #2a3340;background:#0e1116;color:#fff}
button{background:#e51e63;border:0;font-weight:800}</style></head><body><form method="post" action="/login">
<div>FusionStitch 手機網頁</div><p style="color:#8994a4">輸入電腦程式「設定 → 手機網頁」的 PIN</p>
<input name="pin" inputmode="numeric" autocomplete="current-password" autofocus> <button>進入</button>%s</form>
<script>if("serviceWorker" in navigator&&window.isSecureContext)navigator.serviceWorker.register("/sw.js").catch(()=>{});</script></body></html>"""


ICON = """<svg viewBox="0 0 200 200" width="512" height="512" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g43" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0e2a47"/><stop offset="1" stop-color="#14506e"/></linearGradient><filter id="f43" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="6" stdDeviation="5" flood-opacity=".55"/></filter></defs><rect x="4" y="4" width="192" height="192" rx="40" fill="url(#g43)"/><path d="M4,40 a36,36 0 0 1 36,-36 h120 a36,36 0 0 1 36,36 v30 q-96,40 -192,0z" fill="#fff" opacity=".10"/><g><g filter="url(#f43)"><text x="100" y="128" text-anchor="middle" font-family="Arial Black,Arial" font-weight="900" font-size="84" letter-spacing="-6" fill="#bfe9ff">F<tspan fill="#6fd3ff">S</tspan></text></g></g></svg>"""

MANIFEST = json.dumps({"id": "/fusionstitch", "name": "FusionStitch", "short_name": "FusionStitch",
                       "start_url": "/", "scope": "/", "display": "standalone",
                       "background_color": "#0e1116", "theme_color": "#0e1116",
                       "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                                 {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                                 {"src": "/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]},
                      ensure_ascii=False)
# a service worker with a fetch handler is part of Chrome's "installable" check; it only passes requests through
SW = """// FusionStitch phone app: no caching of data, only a friendly page when the computer cannot be reached.
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{if(e.request.mode!=='navigate')return;
  e.respondWith(fetch(e.request).catch(()=>new Response('<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><body style="margin:0;background:#0e1116;color:#e8ecf2;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center"><div><h2>連不到電腦</h2><p style="color:#8994a4">請確認電腦與 FusionStitch 開著、<br>手機的 Tailscale 已連線</p><button onclick="location.reload()" style="padding:12px 24px;border:0;border-radius:10px;background:#e51e63;color:#fff;font-size:16px">重試</button></div>',{headers:{'Content-Type':'text/html; charset=utf-8'}})));});
"""
CODE = core.APP_VERSION          # the running server is replaced when the program code changes (hot update)


# ---------------------------------------------------------------- Tailscale (same approach as TikLive)
# Phone and computer on one private Tailscale network: the page works away from home (4G/5G) with
# a direct connection. `tailscale serve` adds HTTPS on the computer's Tailscale name so the phone
# can install the page as an app.
_ts = {"t": 0.0, "ip": "", "dns": "", "https": False, "busy": False}


def https_port(port: int) -> int:
    """Each of the user's programs gets its own HTTPS port on the Tailscale name, so several
    programs on one computer never overwrite each other's `tailscale serve` (TikLive keeps 443).
    Rule: local port + 1000 (FusionStitch 8361 -> 9361), or web_https_port in settings.json."""
    return int(core.cfg("web_https_port") or (int(port) + 1000))


def tailscale_exe() -> str:
    import shutil
    exe = shutil.which("tailscale") or ""
    for c in (os.path.expandvars(r"%ProgramFiles%\Tailscale\tailscale.exe"), r"C:\Program Files\Tailscale\tailscale.exe",
              r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if not exe and os.path.exists(c):
            exe = c
    return exe


def tailscale_run(args, timeout=8):
    """(returncode, text); returncode None = still running when the time ran out."""
    import subprocess
    exe = tailscale_exe()
    if not exe:
        return -1, ""
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode, (r.stdout or b"").decode("utf-8", "ignore") + (r.stderr or b"").decode("utf-8", "ignore")
    except subprocess.TimeoutExpired as e:
        return None, (e.stdout or b"").decode("utf-8", "ignore") + (e.stderr or b"").decode("utf-8", "ignore")
    except Exception as e:
        return -1, str(e)


def tailscale_refresh(port: int, force=False):
    """Updates the cached Tailscale address / name / HTTPS state in the background (at most every 60 s)."""
    if _ts["busy"] or (not force and time.time() - _ts["t"] < 60):
        return
    _ts["busy"], _ts["t"] = True, time.time()

    def work():
        ip = dns = ""
        https = False
        try:
            rc, out = tailscale_run(["status", "--json"], 6)
            try:
                j = json.loads(out[out.find("{"):] if "{" in out else "{}")
                me = j.get("Self") or {}
                if str(j.get("BackendState", "")) == "Running":
                    ips = [x for x in (me.get("TailscaleIPs") or []) if "." in str(x)]
                    ip = ips[0] if ips else ""
                    dns = str(me.get("DNSName") or "").rstrip(".")
            except ValueError:
                pass
            if dns:
                rc, out = tailscale_run(["serve", "status", "--json"], 6)
                hp = https_port(port)
                https = bool(rc == 0 and f'"{dns.lower()}:{hp}"' in out.lower() and f":{int(port)}" in out)
            if not ip:                                       # no CLI: look for a 100.64.0.0/10 address
                for a in lan_addresses():
                    p_ = [int(v) for v in a.split(".")]
                    if p_[0] == 100 and 64 <= p_[1] <= 127:
                        ip = a
                        break
            _ts.update(ip=ip, dns=dns, https=https)
        finally:
            _ts["busy"] = False
    threading.Thread(target=work, daemon=True, name="tailscale").start()


def tailscale_url(port: int) -> str:
    if _ts["dns"] and _ts["https"]:
        return f"https://{_ts['dns']}:{https_port(port)}/"
    if _ts["ip"]:
        return f"http://{_ts['ip']}:{int(port)}/"
    return ""


def tailscale_serve(port: int) -> tuple[bool, str]:
    """Turns on Tailscale HTTPS for the page (blocking, call from a worker thread). -> (ok, message)"""
    if not tailscale_exe():
        return False, "這台電腦還沒裝 Tailscale：請先到 tailscale.com 下載安裝並登入，手機也裝 Tailscale 並登入同一個帳號。"
    # only this program's own HTTPS port is touched; never `serve reset` (that would cut off TikLive etc.)
    rc, out = tailscale_run(["serve", "--bg", f"--https={https_port(port)}", f"http://127.0.0.1:{int(port)}"], 40)
    import re
    m = re.search(r"https://login\.tailscale\.com/\S+", out or "")
    if m:
        return False, "LOGIN " + m.group(0)
    if rc not in (0, None):
        return False, "Tailscale 沒有成功開啟 HTTPS：\n" + (out or "").strip()[-600:]
    _ts["t"] = 0
    tailscale_refresh(port, True)
    return True, ""


def lan_addresses() -> list[str]:
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


class Remote:
    def __init__(self, main_window_getter, request_update):
        self.get_main = main_window_getter
        self.request_update = request_update        # callable(path) -> runs on the UI thread
        self.httpd = None
        self.thread = None
        self.pending = None                         # uploaded package waiting for confirmation
        self.code = CODE
        self.result = ""

    @property
    def running(self):
        return self.httpd is not None

    def pin(self) -> str:
        p = core.cfg("web_pin")
        if not p:
            p = f"{secrets.randbelow(10 ** 6):06d}"
            core.set_cfg("web_pin", p)
        return p

    def start(self, port: int):
        self.stop()
        remote = self
        token = core.cfg("web_token") or ""
        if not token:                       # kept across restarts / updates: the phone stays logged in
            token = secrets.token_hex(16)
            core.set_cfg("web_token", token)

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if not getattr(self, "_head", False):
                    self.wfile.write(data)

            def _json(self, obj, code=200):
                self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

            def _authed(self):
                c = self.headers.get("Cookie", "")
                return f"fs={token}" in c

            def do_GET(self):
                if self.path == "/manifest.webmanifest":
                    return self._send(200, MANIFEST, "application/manifest+json; charset=utf-8")
                if self.path == "/icon.svg":
                    return self._send(200, ICON, "image/svg+xml")
                if self.path in ("/icon-192.png", "/icon-512.png", "/apple-touch-icon.png", "/icon-512-maskable.png"):
                    import base64
                    import webicons
                    b64 = {"/icon-192.png": webicons.ICON_192, "/icon-512.png": webicons.ICON_512,
                           "/icon-512-maskable.png": webicons.ICON_512_MASK}.get(self.path, webicons.ICON_180)
                    return self._send(200, base64.b64decode(b64), "image/png")
                if self.path == "/sw.js":
                    return self._send(200, SW, "text/javascript; charset=utf-8", {"Service-Worker-Allowed": "/"})
                if not self._authed():
                    return self._send(200 if self.path == "/" else 401, LOGIN % "")
                u = urllib.parse.urlsplit(self.path)
                qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                if u.path in ("/", "/index.html"):
                    import webpc_page
                    return self._send(200, webpc_page.PAGE)
                if u.path == "/simple":                      # the old one-page view
                    return self._send(200, PAGE)
                if u.path == "/api/status":
                    return self._json(remote.status())
                if u.path == "/api/file":
                    return self._file(qs.get("name", ""))
                import webpc
                r = webpc.handle("GET", u.path, qs, {}, remote)
                if r:
                    return self._send(r[0], r[2], r[1])
                self._send(404, "not found")

            def _file(self, name):
                """Finished output for playing / downloading on the phone (Range = seeking in the video)."""
                import webpc
                p = webpc.output_path(name)
                if not p:
                    return self._send(404, "not found")
                size = p.stat().st_size
                start, end = 0, size - 1
                rng = self.headers.get("Range", "")
                code = 200
                if rng.startswith("bytes="):
                    a, _, b = rng[6:].split(",")[0].partition("-")
                    try:
                        if a:
                            start = int(a)
                            end = min(size - 1, int(b)) if b else size - 1
                        elif b:
                            start = max(0, size - int(b))
                        code = 206
                    except ValueError:
                        pass
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(code)
                ctype = "video/mp4" if p.suffix.lower() == ".mp4" else "image/jpeg"
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(p.name))
                self.end_headers()
                try:
                    with open(p, "rb") as f:
                        f.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = f.read(min(left, 1 << 20))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_HEAD(self):
                self._head = True
                try:
                    self.do_GET()
                finally:
                    self._head = False

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if self.path == "/login":
                    body = urllib.parse.parse_qs(self.rfile.read(n).decode(errors="ignore"))
                    if (body.get("pin") or [""])[0].strip() == remote.pin():
                        return self._send(303, "", headers={"Location": "/", "Set-Cookie": f"fs={token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"})
                    time.sleep(1.0)                                     # slow down guessing
                    return self._send(200, LOGIN % '<p style="color:#ff6577">PIN 不正確</p>')
                if not self._authed():
                    return self._json({"ok": False, "message": "請先登入"}, 401)
                if self.path.startswith("/api/update/upload"):
                    if n > 50 * 1024 * 1024:
                        return self._json({"ok": False, "message": "檔案太大"})
                    data = self.rfile.read(n)
                    return self._json(remote.upload(data))
                if self.path == "/api/update/apply":
                    return self._json(remote.apply())
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    body = {}
                import webpc
                r = webpc.handle("POST", urllib.parse.urlsplit(self.path).path, {}, body if isinstance(body, dict) else {}, remote)
                if r:
                    return self._send(r[0], r[2], r[1])
                self._json({"ok": False}, 404)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="webremote")
        self.thread.start()

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None

    # ---- data / actions (called from HTTP threads)
    def status(self) -> dict:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        q = getattr(app, "_fs_queue", None)
        perf = getattr(app, "_fs_perf", None)
        data = perf.snapshot()[0] if perf else {}
        jobs = []
        for j in (list(q.jobs) if q else [])[-30:]:
            jobs.append({"id": j.id, "title": j.title, "status": j.status, "progress": j.progress, "fps": j.fps,
                         "eta": j.eta, "message": j.message, "name": os.path.basename(j.output)})
        jobs.reverse()
        relay = ""
        try:
            import staging
            relay = staging.status_text() if core.cfg("cache_dir") else ""
        except Exception:
            pass
        run = q.running() if q else None
        n = sum(1 for j in (q.jobs if q else []) if j.status == "queued")
        summary = (f"· 轉檔中 {run.title}" if run else "· 閒置") + (f" · 排隊 {n}" if n else "")
        return {"version": core.APP_VERSION, "perf": data, "jobs": jobs, "relay": relay, "summary": summary}

    def queue_action(self, action) -> dict:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        if not q:
            return {"ok": False}
        for j in list(q.jobs):
            if action == "pause" and j.status in ("running", "queued"):
                q.pause(j.id)
            elif action == "resume" and j.status == "paused":
                q.resume(j.id)
        return {"ok": True}

    def job_action(self, jid, action) -> dict:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        if not q:
            return {"ok": False}
        {"pause": q.pause, "resume": q.resume, "cancel": q.cancel}.get(action, lambda _: None)(jid)
        return {"ok": True}

    def upload(self, data: bytes) -> dict:
        import updater
        fd, path = tempfile.mkstemp(suffix="_Update.zip")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            pkg = updater.read_package(path)
        except Exception as e:
            return {"ok": False, "message": f"無法使用這個更新包：{e}"}
        self.pending = path
        self.pending_to = pkg["version"]
        try:
            cmp = (core.version_tuple(pkg["version"]) > core.version_tuple(core.APP_VERSION)) - \
                  (core.version_tuple(pkg["version"]) < core.version_tuple(core.APP_VERSION))
        except Exception:
            cmp = 1
        return {"ok": True, "tok": "1", "from": core.APP_VERSION, "to": pkg["version"], "cmp": cmp,
                "name": "更新包.zip", "size": len(data), "pw": False,
                "notes": [html.escape(n) for n in pkg["notes"]]}

    def apply(self) -> dict:
        if not self.pending:
            return {"ok": False, "message": "請先上傳更新包"}
        path, self.pending = self.pending, None
        self.request_update(path)
        return {"ok": True, "to": getattr(self, "pending_to", ""),
                "message": "已開始更新，電腦上的程式會自動重整為新版本（轉檔不中斷）。幾秒後重新整理這個頁面。"}
