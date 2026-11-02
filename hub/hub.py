#!/usr/bin/env python3
"""AtomS3 desk hub.

Serves one JSON endpoint (/stats) on the LAN with:
  - PC CPU / MEM / GPU / uptime
  - a list of service checks (HTTP, TCP, Minecraft)
  - an "alert" flag when a critical service is down

Run:    python hub.py        (or install_hub.bat for autostart)
Config: hub_config.json next to this file.
"""
import json
import os
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "hub_config.json")
import spotify_bridge

with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

PORT = int(os.environ.get("ATOM_HUB_PORT") or CFG.get("port", 8765))
DISCOVERY_PORT = int(os.environ.get("ATOM_DISCOVERY_PORT") or CFG.get("discovery_port", 8766))
TOKEN = (os.environ.get("ATOM_HUB_TOKEN") or CFG.get("token") or "").strip()
TARGET = CFG.get("target", "")
SERVICES = CFG.get("services") or []
ALERT_RULES = CFG.get("alert_rules") or []

CRAFTY = CFG.get("crafty") or {}
CRAFTY_URL = (CRAFTY.get("url") or "").rstrip("/")
CRAFTY_KEY = CRAFTY.get("api_key") or ""
CRAFTY_TIMEOUT = float(CRAFTY.get("timeout", 4))
CRAFTY_ON = bool(CRAFTY.get("enabled", True) and CRAFTY_URL and CRAFTY_KEY)

try:
    import psutil
except ImportError:
    psutil = None

STATE = {
    "cpu": 0.0,
    "mem": 0.0,
    "uptime": 0,
    "gpu": None,
    "ts": 0,
}

_svc_cache = {"at": 0.0, "data": None}
_svc_lock = threading.Lock()
_gpu_cache = {"at": 0.0, "data": None}
_crafty_state = {"at": 0.0, "fail_at": 0.0, "map": {}}
_crafty_lock = threading.Lock()


def sample_loop():
    if psutil:
        psutil.cpu_percent(interval=None)
    while True:
        try:
            if psutil:
                STATE["cpu"] = psutil.cpu_percent(interval=None)
                STATE["mem"] = psutil.virtual_memory().percent
                STATE["uptime"] = int(time.time() - psutil.boot_time())
            else:
                STATE["cpu"] = 0.0
                STATE["mem"] = 0.0
                STATE["uptime"] = int(time.time() - _win_boot_time())
            STATE["ts"] = int(time.time())
        except Exception:
            pass
        time.sleep(1.0)


# When the hub runs under pythonw.exe there is no console. A plain
# subprocess.run() then gives the child a brand new *visible* console, so
# every nvidia-smi / powershell call flashes a black cmd window. These
# creation flags keep helper processes completely silent.
NO_WINDOW = 0
if os.name == "nt":
    NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _win_boot_time():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToFileTimeUtc()"],
            capture_output=True, text=True, timeout=6,
            creationflags=NO_WINDOW).stdout.strip()
        return time.time() - (int(out) / 10_000_000 - 11644473600)
    except Exception:
        return time.time()


def gpu_info():
    now = time.time()
    if _gpu_cache["data"] is not None and now - _gpu_cache["at"] < 5:
        return _gpu_cache["data"]
    data = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=NO_WINDOW).stdout.strip()
        if out:
            parts = [p.strip() for p in out.splitlines()[0].split(",")]
            data = {
                "name": parts[0],
                "util": float(parts[1]),
                "temp": float(parts[2]),
                "mem_used": float(parts[3]),
                "mem_total": float(parts[4]),
            }
    except Exception:
        data = None
    _gpu_cache["data"] = data
    _gpu_cache["at"] = now
    return data


# ---------------------------------------------------------------- checks

def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes((b | 0x80,))
        else:
            return out + bytes((b,))


def _read_varint(sock):
    n = 0
    shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise EOFError("closed")
        b = b[0]
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n
        shift += 7


def _mc_slp(host, port, timeout=3, handshake=None):
    """Minecraft Server List Ping (Java 1.7+). No external API."""
    s = socket.create_connection((host, port), timeout)
    s.settimeout(timeout)
    try:
        hb = (handshake or host).encode()
        body = (b"\x00" + _varint(47) + _varint(len(hb)) + hb
                + struct.pack(">H", port) + _varint(1))
        s.sendall(_varint(len(body)) + body + b"\x01\x00")
        ln = _read_varint(s)
        data = b""
        while len(data) < ln:
            chunk = s.recv(ln - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    i = 0
    while data[i] & 0x80:
        i += 1
    i += 1
    slen = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        slen |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
    return json.loads(data[i:i + slen].decode("utf-8", "replace"))


def _tcp_ok(host, port, timeout=1.5):
    try:
        s = socket.create_connection((host, port), timeout)
        s.close()
        return True
    except Exception:
        return False


_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(
    _NoRedirect, urllib.request.HTTPSHandler(context=_SSL_CTX))


def _http_ok(url, timeout=2.5):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AtomS3-Desk/1.0"})
        with _OPENER.open(req, timeout=timeout) as r:
            r.read(64)
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def _svc_hosts(s):
    hosts = s.get("hosts")
    if hosts:
        return [h for h in hosts if h]
    return [s.get("host") or TARGET or "127.0.0.1"]


# ---------------------------------------------------------------- crafty

def _crafty_api(path, timeout=None):
    req = urllib.request.Request(
        CRAFTY_URL + path,
        headers={
            "Authorization": "Bearer " + CRAFTY_KEY,
            "Accept": "application/json",
            "User-Agent": "AtomS3-Desk/1.0",
        },
    )
    with _OPENER.open(req, timeout=timeout or CRAFTY_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _crafty_servers_map():
    """id/name (lowercase) -> {id, name, port}. Cached for 5 minutes."""
    if not CRAFTY_ON:
        return {}
    now = time.time()
    if _crafty_state["map"] and now - _crafty_state["at"] < 300:
        return _crafty_state["map"]
    if not _crafty_state["map"] and now - _crafty_state["fail_at"] < 30:
        return {}
    with _crafty_lock:
        now = time.time()
        if _crafty_state["map"] and now - _crafty_state["at"] < 300:
            return _crafty_state["map"]
        try:
            rows = _crafty_api("/api/v2/servers").get("data") or []
            m = {}
            for s in rows:
                item = {
                    "id": s.get("server_id"),
                    "name": s.get("server_name"),
                    "port": int(s.get("server_port") or 0),
                }
                for key in (item["id"], item["name"]):
                    if key:
                        m[str(key).lower()] = item
            if m:
                _crafty_state["map"] = m
                _crafty_state["at"] = now
                _crafty_state["fail_at"] = 0.0
        except Exception:
            _crafty_state["fail_at"] = now
        return _crafty_state["map"]


def _crafty_check(s, res):
    """Fill res from Crafty's stats API. True if Crafty answered."""
    ref = s.get("crafty")
    if not (CRAFTY_ON and ref):
        return False
    item = _crafty_servers_map().get(str(ref).lower())
    if not item:
        return False
    try:
        d = _crafty_api("/api/v2/servers/%s/stats" % item["id"]).get("data") or {}
    except Exception:
        return False
    running = bool(d.get("running"))
    res.update({
        "up": running,
        "players": int(d.get("online") or 0),
        "max": int(d.get("max") or 0),
        "version": d.get("version") or "",
        "port": int(d.get("server_port") or item.get("port") or res.get("port") or 0),
        "via": "crafty",
    })
    if not running:
        res["error"] = "stopped"
    return True


def _check_one(s):
    kind = (s.get("type") or "tcp").lower()
    name = s.get("name") or s.get("id") or "?"
    sid = s.get("id") or name
    port = int(s.get("port", 0) or 0)
    hosts = _svc_hosts(s)
    res = {
        "id": sid,
        "name": name,
        "kind": kind,
        "port": port,
        "note": s.get("note", ""),
        "critical": bool(s.get("critical")),
        "up": False,
        "ms": 0,
        "players": 0,
        "max": 0,
        "error": "",
    }
    t0 = time.time()
    if s.get("crafty") and _crafty_check(s, res):
        res["ms"] = int((time.time() - t0) * 1000)
        return res
    res["error"] = ""
    if kind == "http":
        urls = [u for u in (s.get("url"), s.get("alt")) if u]
        res["up"] = any(_http_ok(u) for u in urls) if urls else False
        if not res["up"]:
            res["error"] = "no response"
    elif kind == "mc":
        last = ""
        for host in hosts:
            try:
                d = _mc_slp(host, port or 25565, handshake=s.get("slp_host") or None)
                players = d.get("players") or {}
                res.update({
                    "up": True,
                    "players": players.get("online", 0),
                    "max": players.get("max", 0),
                    "version": (d.get("version") or {}).get("name", ""),
                    "via": host,
                })
                break
            except Exception as e:
                last = str(e)[:40]
        if not res["up"]:
            res["error"] = last or "no response"
    else:
        for host in hosts:
            if _tcp_ok(host, port):
                res["up"] = True
                res["via"] = host
                break
    res["ms"] = int((time.time() - t0) * 1000)
    return res


def _blank(s, err=""):
    return {
        "id": s.get("id", "?"), "name": s.get("name", "?"),
        "kind": s.get("type", "tcp"), "port": int(s.get("port", 0) or 0),
        "note": s.get("note", ""), "critical": bool(s.get("critical")),
        "up": False, "ms": 0, "players": 0, "max": 0, "error": err,
    }


def check_services(force=False):
    """Refresh all service checks in parallel.

    Never blocks a caller on an in-flight refresh: /stats always gets the
    last known snapshot immediately. A slow check keeps the previous result.
    """
    now = time.time()
    if (not force and _svc_cache["data"] is not None
            and now - _svc_cache["at"] < 10):
        return _svc_cache["data"]
    if not _svc_lock.acquire(False):
        return _svc_cache["data"] or []

    try:
        if CRAFTY_ON and any(s.get("crafty") for s in SERVICES):
            _crafty_servers_map()
        results = [None] * len(SERVICES)

        def run(i, s):
            try:
                results[i] = _check_one(s)
            except Exception as e:
                results[i] = _blank(s, str(e)[:40])

        threads = []
        for i, s in enumerate(SERVICES):
            t = threading.Thread(target=run, args=(i, s), daemon=True)
            t.start()
            threads.append(t)
        deadline = time.time() + 8.0
        for t in threads:
            t.join(max(0.05, deadline - time.time()))

        prev = {s["id"]: s for s in (_svc_cache["data"] or [])}
        out = []
        for i, s in enumerate(SERVICES):
            r = results[i]
            if r is None:
                # check still hanging: keep last known state for this id
                r = prev.get(s.get("id")) or _blank(s, "timeout")
            out.append(r)

        _svc_cache["data"] = out
        _svc_cache["at"] = time.time()
        return out
    finally:
        _svc_lock.release()


def refresh_loop():
    while True:
        try:
            check_services(force=True)
        except Exception:
            pass
        time.sleep(4)


def eval_alerts(svcs):
    """Return the ids that should raise an alert.

    Default: every service with "critical": true that is down.
    Optional hub_config "alert_rules" override, e.g.
        [{"any_down": ["velocity"]}, {"all_down": ["main", "crafty"]}]
    (OR between rules) -> flash if Velocity is down OR both web+Crafty are.
    """
    by_id = {s["id"]: s for s in svcs}
    if not ALERT_RULES:
        return [s["id"] for s in svcs if s.get("critical") and not s.get("up")]

    down = []
    for rule in ALERT_RULES:
        any_ids = rule.get("any_down") or []
        if any_ids and any(not by_id.get(i, {}).get("up") for i in any_ids):
            down.extend(i for i in any_ids if not by_id.get(i, {}).get("up"))
        all_ids = rule.get("all_down") or []
        if all_ids and all(not by_id.get(i, {}).get("up", True) for i in all_ids):
            down.extend(all_ids)
    return sorted(set(down))


def stats():
    svcs = check_services()
    down = eval_alerts(svcs)
    mc = next((s for s in svcs if s.get("kind") == "mc"), None)
    return {
        "ts": int(time.time()),
        "host": socket.gethostname(),
        "host_mac": hub_macs()[0],
        "cpu": {"percent": round(STATE["cpu"], 1)},
        "mem": {"percent": round(STATE["mem"], 1)},
        "gpu": gpu_info(),
        "uptime": STATE["uptime"],
        "services": svcs,
        "alert": bool(down),
        "alert_ids": down,
        # legacy keys (older device builds)
        "minecraft": ({
            "host": "%s:%d" % (TARGET or "mc", mc.get("port", 0)),
            "online": mc["up"],
            "players": mc.get("players", 0),
            "max": mc.get("max", 0),
        } if mc else None),
        "servers": [
            {"name": s["name"], "url": "", "up": s["up"], "ms": s["ms"]}
            for s in svcs if s.get("kind") == "http"
        ],
    }


HTML = """<!doctype html><meta charset=utf-8>
<title>AtomS3 desk hub</title>
<style>body{font-family:system-ui;background:#111;color:#eee;padding:24px}
pre{background:#1c1c1c;padding:12px;border-radius:8px;overflow:auto}
.ok{color:#3fb950}.bad{color:#f85149}</style>
<h2>AtomS3 desk hub</h2>
<p>JSON endpoint: <a href="/stats">/stats</a></p>
<div id=s>loading...</div>
<pre id=o></pre>
<script>
fetch('/stats').then(r=>r.json()).then(j=>{
  s.innerHTML = j.services.map(x=>`<div class="${x.up?'ok':'bad'}">${x.up?'UP  ':'DOWN'} ${x.name} :${x.port} ${x.ms}ms</div>`).join('')
    + (j.alert ? '<h3 class="bad">ALERT: '+j.alert_ids.join(', ')+'</h3>' : '<h3 class="ok">all critical services up</h3>');
  o.textContent = JSON.stringify(j,null,2);
});
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, path):
        # /ping is open so the local watchdog keeps working without the key
        if not TOKEN or path == "/ping":
            return True
        if parse_qs(urlparse(self.path).query).get("k", [""])[0] == TOKEN:
            return True
        return self.headers.get("X-Atom-Key", "") == TOKEN

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._authorized(path):
            body = b'{"status":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/stats" or path == "/stats/":
            body = json.dumps(stats()).encode()
            self._send(body, "application/json")
        elif path == "/ping":
            self._send(b"ok", "text/plain")
        elif path in ("/spotify_toggle", "/spotify_next",
                      "/spotify_previous"):
            name = path.rsplit("_", 1)[-1]
            spotify_bridge.command(name)
            body = json.dumps({
                "status": "queued",
                "message": "queued " + name,
            }).encode()
            self._send(body, "application/json")
        elif path == "/spotify_status":
            body = json.dumps(spotify_bridge.status()).encode()
            self._send(body, "application/json")
        else:
            self._send(HTML.encode(), "text/html; charset=utf-8")


def lan_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


class HubServer(ThreadingHTTPServer):
    """Single-instance HTTP server.

    Python's HTTPServer sets SO_REUSEADDR, which on Windows allows several
    processes to bind the same port at once. Requests then get spread over
    every copy, each with its own alert state, so the device LED alert
    flickers. SO_EXCLUSIVEADDRUSE makes the second copy fail loudly instead.
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        ThreadingHTTPServer.server_bind(self)


_mac_cache = {"at": 0.0, "data": []}


def hub_macs():
    """Link-layer addresses of this PC (lowercase, colon separated)."""
    now = time.time()
    if _mac_cache["data"] and now - _mac_cache["at"] < 60:
        return _mac_cache["data"]
    out = []
    try:
        if psutil:
            link = getattr(psutil, "AF_LINK", -1)
            for alist in psutil.net_if_addrs().values():
                for a in alist:
                    if a.family == link and a.address:
                        m = a.address.lower().replace("-", ":")
                        if m not in out:
                            out.append(m)
    except Exception:
        pass
    if not out:
        n = uuid.getnode()
        out.append(":".join("%02x" % ((n >> i) & 0xFF)
                            for i in (40, 32, 24, 16, 8, 0)))
    _mac_cache["at"] = now
    _mac_cache["data"] = out
    return out


def discovery_loop():
    """Answer ATOMDISCOVER broadcasts so the device can find this PC.

    The device sends a UDP broadcast to port 8766. The reply carries the hub
    port and every MAC address of this machine, so the device can tell the
    right PC apart from other hosts even when its IP address changes.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError:
        s.close()
        return
    host = socket.gethostname()
    while True:
        try:
            data, addr = s.recvfrom(512)
        except OSError:
            time.sleep(0.5)
            continue
        if not data.startswith(b"ATOMDISCOVER"):
            continue
        reply = json.dumps({
            "service": "atom-hub",
            "port": PORT,
            "macs": hub_macs(),
            "host": host,
        }).encode()
        try:
            s.sendto(reply, addr)
        except OSError:
            pass


def port_in_use():
    """True when something already listens on PORT (another hub copy)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0
    except Exception:
        return False
    finally:
        probe.close()


def main():
    if port_in_use():
        print("hub already running on port %d - this copy exits" % PORT)
        return
    threading.Thread(target=sample_loop, daemon=True).start()
    threading.Thread(target=refresh_loop, daemon=True).start()
    threading.Thread(target=discovery_loop, daemon=True).start()
    spotify_bridge.start()
    try:
        srv = HubServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        print("cannot bind port %d (%s) - another hub is running" % (PORT, e))
        return
    print("AtomS3 desk hub listening on port", PORT)
    for ip in lan_ips():
        print("  http://%s:%d/stats" % (ip, PORT))
    if not psutil:
        print("psutil not installed -> CPU/MEM disabled (python -m pip install --user psutil)")
    if not SERVICES:
        print("no services configured in hub_config.json")
    srv.serve_forever()


if __name__ == "__main__":
    main()
