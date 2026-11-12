#!/usr/bin/env python3
"""AtomS3 desk hub.

Serves one JSON endpoint (/stats) on the LAN with:
  - PC CPU / MEM / GPU / uptime
  - a list of service checks (HTTP, TCP, Minecraft)
  - an "alert" flag when a critical service is down

Run:    python hub.py        (or install_hub.bat for autostart)
Config: hub_config.json next to this file.
"""
import calendar as _calmod
import datetime as _dt
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
        "player_names": _crafty_names(d),
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
        "player_names": [],
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
        "up": False, "ms": 0, "players": 0, "max": 0,
        "player_names": [], "error": err,
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
            svcs = check_services(force=True)
            update_players(svcs)
            update_history(svcs)
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


# ---------------------------------------------------------------- events

EVENT_KEEP = int((CFG.get("events") or {}).get("keep", 200))
_events = []
_events_lock = threading.Lock()
_event_next = [1]


def add_event(kind, title, body=""):
    """Append one notification. /events hands new ids to the device."""
    with _events_lock:
        ev = {
            "id": _event_next[0],
            "ts": int(time.time()),
            "kind": kind,
            "title": str(title)[:40],
            "body": str(body)[:160],
        }
        _event_next[0] += 1
        _events.append(ev)
        if len(_events) > EVENT_KEEP:
            del _events[:len(_events) - EVENT_KEEP]
        return ev


def events_since(since):
    with _events_lock:
        last = _event_next[0] - 1
        try:
            since = int(since)
        except Exception:
            since = 0
        # device remembers a bigger id than this (hub restarted) -> replay all
        if since > last or since < 0:
            since = 0
        out = [e for e in _events if e["id"] > since]
        return {"now": int(time.time()), "last": last, "events": out}


# --------------------------------------------------------------- players

_players_lock = threading.Lock()
_players = {"by_srv": {}, "seeded": False, "list": []}


def _crafty_names(d):
    names = []
    pl = d.get("players")
    if isinstance(pl, list):
        for p in pl:
            if isinstance(p, dict):
                n = p.get("name") or p.get("username") or ""
            elif isinstance(p, str):
                n = p
            else:
                n = ""
            n = str(n).strip()
            if n:
                names.append(n[:20])
    return names


def update_players(svcs):
    """Diff Crafty player lists so joins/leaves become notifications."""
    # Only trust the diff when Crafty itself answered this round; a timed out
    # check has no names and would otherwise look like everyone left.
    if not any(s.get("via") == "crafty" for s in svcs):
        return
    by_srv = {}
    for s in svcs:
        if s.get("via") != "crafty":
            continue
        sid = str(s.get("id") or s.get("name") or "MC")
        by_srv[sid] = (s.get("name") or sid, list(s.get("player_names") or []))
    with _players_lock:
        prev = _players["by_srv"]
        # keep the last names for servers whose check was slow this round
        for sid, last in prev.items():
            if sid not in by_srv:
                by_srv[sid] = last
        cur = {}
        for sid, (srv, names) in by_srv.items():
            for n in names:
                cur[n] = (srv, sid)
        if _players["seeded"]:
            prev_names = {}
            for sid, (srv, names) in prev.items():
                for n in names:
                    prev_names[n] = srv
            for n, (srv, sid) in cur.items():
                if n not in prev_names:
                    add_event("mc_join", n, "joined " + srv)
                elif prev_names[n] != srv:
                    add_event("mc_move", n, prev_names[n] + " -> " + srv)
            for n, srv in prev_names.items():
                if n not in cur:
                    add_event("mc_leave", n, "left " + srv)
        _players["by_srv"] = by_srv
        _players["seeded"] = True
        _players["list"] = [{"name": n, "server": v[0]}
                            for n, v in cur.items()]


# ---------------------------------------------------------------- uptime

HIST_POINTS = 40
HIST_STEP = 15.0
_hist = {"samples": [], "at": 0.0, "down_set": set(), "last_down": 0}


def update_history(svcs):
    """Sample how many services were up, and remember the last transition."""
    now = time.time()
    down = set(s["id"] for s in svcs if not s.get("up"))
    if down - _hist["down_set"]:
        _hist["last_down"] = int(now)
    _hist["down_set"] = down
    if _hist["at"] == 0 or now - _hist["at"] >= HIST_STEP:
        _hist["at"] = now
        _hist["samples"].append(max(0, len(svcs) - len(down)))
        if len(_hist["samples"]) > HIST_POINTS:
            del _hist["samples"][:len(_hist["samples"]) - HIST_POINTS]


# -------------------------------------------------------------- calendar

_cal_cache = {"at": 0.0, "data": None, "err": ""}
_CAL_CACHE_S = 60
_WKDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_EPOCH = _dt.datetime(1970, 1, 1)


def _ics_unescape(v):
    v = (v or "").replace("\\N", "\n").replace("\\n", "\n")
    return (v.replace("\\,", ",").replace("\\;", ";")
             .replace("\\\\", "\\")).strip()


def _ics_duration(v):
    """ISO-8601 duration (P1D, PT1H30M) -> seconds."""
    if not v:
        return 0
    v = v.strip().upper()
    total = 0
    num = ""
    for ch in v[1:]:
        if ch.isdigit():
            num += ch
        elif ch == "W":
            total += int(num or 0) * 604800
            num = ""
        elif ch == "D":
            total += int(num or 0) * 86400
            num = ""
        elif ch == "H":
            total += int(num or 0) * 3600
            num = ""
        elif ch == "M":
            total += int(num or 0) * 60
            num = ""
        elif ch == "S":
            total += int(num or 0)
            num = ""
    return total


def _ics_dt(value, tz_off):
    """(epoch, all_day) for an iCalendar date / date-time value."""
    v = (value or "").strip()
    if not v:
        return None, False
    if "T" not in v:
        try:
            y, mo, d = int(v[0:4]), int(v[4:6]), int(v[6:8])
            return (int(_calmod.timegm((y, mo, d, 0, 0, 0)))
                    - int(tz_off * 3600), True)
        except Exception:
            return None, True
    utc = v.endswith("Z")
    core = v[:-1] if utc else v
    try:
        y, mo, d = int(core[0:4]), int(core[4:6]), int(core[6:8])
        hh = int(core[9:11])
        mi = int(core[11:13])
        ss = int(core[13:15]) if len(core) >= 15 else 0
        epoch = int(_calmod.timegm((y, mo, d, hh, mi, ss)))
        if not utc:
            epoch -= int(tz_off * 3600)
        return epoch, False
    except Exception:
        return None, False


def _ics_events(text, tz_off):
    """Parse VEVENT blocks out of an iCalendar feed (line folding included)."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    out = []
    cur = None
    for line in lines:
        u = line.upper()
        if u == "BEGIN:VEVENT":
            cur = {"rrule": "", "duration": 0, "end": None,
                   "all_day": False, "title": "", "location": ""}
            continue
        if u == "END:VEVENT":
            if cur is not None and cur.get("start") is not None:
                if cur.get("end") is None and cur.get("duration"):
                    cur["end"] = cur["start"] + cur["duration"]
                out.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, val = line.split(":", 1)
        name = head.split(";")[0].upper()
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _ics_dt(val, tz_off)
        elif name == "DTEND":
            cur["end"], _ = _ics_dt(val, tz_off)
        elif name == "DURATION":
            cur["duration"] = _ics_duration(val)
        elif name == "SUMMARY":
            cur["title"] = _ics_unescape(val)
        elif name == "LOCATION":
            cur["location"] = _ics_unescape(val)
        elif name == "RRULE":
            cur["rrule"] = val.strip()
    return out


def _local_dt(epoch, tz_off):
    return _EPOCH + _dt.timedelta(seconds=epoch + tz_off * 3600)


def _local_epoch(dt, tz_off):
    return int((dt - _EPOCH).total_seconds()) - int(tz_off * 3600)


def _add_months(dt, n):
    y = dt.year + (dt.month - 1 + n) // 12
    m = (dt.month - 1 + n) % 12 + 1
    if m == 2:
        d = min(dt.day, 28)
    elif m in (4, 6, 9, 11):
        d = min(dt.day, 30)
    else:
        d = min(dt.day, 31)
    return dt.replace(year=y, month=m, day=d)


def _occurrences(ev, tz_off, hard_limit=8000):
    """Yield event start times, expanding the common RRULE cases."""
    base = ev.get("start")
    if base is None:
        return
    rr = (ev.get("rrule") or "").strip()
    if not rr:
        yield base
        return
    rules = {}
    for part in rr.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            rules[k.strip().upper()] = v.strip()
    freq = rules.get("FREQ", "").upper()
    try:
        interval = max(1, int(rules.get("INTERVAL", "1")))
    except Exception:
        interval = 1
    count = int(rules["COUNT"]) if rules.get("COUNT", "").isdigit() else None
    until = _ics_dt(rules["UNTIL"], tz_off)[0] if rules.get("UNTIL") else None
    byday = []
    for d in (rules.get("BYDAY") or "").split(","):
        d = d.strip().upper()
        if d[-2:] in _WKDAY:
            byday.append(_WKDAY[d[-2:]])

    b = _local_dt(base, tz_off)
    tod = _dt.timedelta(hours=b.hour, minutes=b.minute, seconds=b.second)
    day = _dt.datetime(b.year, b.month, b.day)
    n = 0
    for _ in range(hard_limit):
        if freq == "DAILY":
            cands = [day + tod]
            day += _dt.timedelta(days=interval)
        elif freq == "WEEKLY":
            week = day - _dt.timedelta(days=day.weekday())
            days = sorted(set(byday)) if byday else [b.weekday()]
            cands = [week + _dt.timedelta(days=wd) + tod for wd in days]
            day = week + _dt.timedelta(days=7 * interval)
        elif freq == "MONTHLY":
            cands = [day + tod]
            day = _add_months(day, interval)
        elif freq == "YEARLY":
            cands = [day + tod]
            day = _add_months(day, 12 * interval)
        else:
            yield base
            return
        for c in cands:
            ts = _local_epoch(c, tz_off)
            if ts < base:
                continue
            n += 1
            if count is not None and n > count:
                return
            if until is not None and ts > until:
                return
            yield ts


def calendar_next(force=False):
    """Next event from the configured ICS feed (cached for a minute)."""
    now = time.time()
    cfg = CFG.get("calendar") or {}
    url = (cfg.get("ics_url") or "").strip()
    if not url or url.upper().startswith(("CHANGE_ME", "YOUR_")):
        return {"status": "disabled"}
    if (not force and _cal_cache["data"] is not None
            and now - _cal_cache["at"] < _CAL_CACHE_S):
        return _cal_cache["data"]
    tz_off = float(cfg.get("tz_offset", 9))
    days = int(cfg.get("days", 14))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "AtomS3-Desk/1.0",
            "Accept": "text/calendar, text/plain, */*",
        })
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            text = r.read().decode("utf-8", "replace")
        horizon = now + days * 86400
        best = None
        for ev in _ics_events(text, tz_off):
            start = ev.get("start")
            if start is None:
                continue
            dur = (ev.get("end") or start) - start
            for ts in _occurrences(ev, tz_off):
                if ts + dur < now:
                    continue
                if ts > horizon:
                    break
                if best is None or ts < best[0]:
                    best = (ts, ev, dur)
                break
        if best is None:
            data = {"status": "ok", "none": True, "start": 0, "in": 0}
        else:
            ts, ev, dur = best
            data = {
                "status": "ok",
                "none": False,
                "title": (ev.get("title") or "EVENT")[:60],
                "start": int(ts),
                "end": int(ts + dur) if dur else 0,
                "all_day": bool(ev.get("all_day")),
                "location": (ev.get("location") or "")[:60],
                "in": int(ts - now),
            }
        _cal_cache["data"] = data
        _cal_cache["at"] = now
        _cal_cache["err"] = ""
        return data
    except Exception as e:
        data = {"status": "error", "message": str(e)[:60]}
        _cal_cache["data"] = data
        _cal_cache["at"] = now
        _cal_cache["err"] = str(e)[:60]
        return data


# ----------------------------------------------------------------- stats

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
        "players": list(_players["list"]),
        "svc_uptime": {
            "hist": list(_hist["samples"]),
            "total": len(svcs),
            "last_down": _hist["last_down"],
            "down": len([s for s in svcs if not s.get("up")]),
        },
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


# ------------------------------------------------------- authoritative state
#
# The device never combines endpoints by itself any more.  The hub publishes
# one state object, split into sections with their own revision numbers.  A
# client sends the revisions it already has and receives only what changed.
# /stream keeps the connection open and pushes the same deltas, so the atom
# is a display, not a second source of truth.

_STATE_KEYS = ("pc", "svc", "sp", "ly", "cal", "ev")
_STATE_LOCK = threading.Lock()
_STATE_REV = {}
_STATE_SIG = {}
# The snapshot is built in the background.  Building it inline used to let a
# slow service check stall a stream for several seconds, which the device saw
# as a dead link and answered with a reconnect.
_STATE_SNAP = {"data": None, "at": 0.0}
_STATE_SNAP_LOCK = threading.Lock()


def _state_pc(st):
    return {
        "cpu": st.get("cpu") or {},
        "mem": st.get("mem") or {},
        "gpu": st.get("gpu") or {},
        "uptime": int(st.get("uptime") or 0),
        "host": st.get("host") or "",
    }


def _state_svc(st):
    return {
        "services": st.get("services") or [],
        "alert": bool(st.get("alert")),
        "alert_ids": st.get("alert_ids") or [],
        "uptime": st.get("svc_uptime") or {},
        "players": st.get("players") or [],
    }


def _state_sections():
    st = stats()
    try:
        sp = spotify_bridge.status()
    except Exception as e:
        sp = {"status": "error", "message": str(e)[:40]}
    try:
        ly = spotify_bridge.lyrics()
    except Exception as e:
        ly = {"status": "error", "message": str(e)[:40]}
    # Never block the stream on a calendar fetch: calendar_loop() warms it.
    cal = _cal_cache["data"] if _cal_cache["data"] is not None else {
        "status": "loading", "none": False}
    ev = events_since(0)
    return {
        "pc": _state_pc(st),
        "svc": _state_svc(st),
        "sp": sp,
        "ly": ly,
        "cal": cal,
        "ev": {"last": ev.get("last"),
               "events": (ev.get("events") or [])[-10:]},
    }


def _state_sig(name, val, now):
    """Signature used to decide whether a section really changed."""
    if name == "sp":
        v = dict(val)
        # The bar is interpolated by the device; re-anchor it every 10 s.
        v["progress_ms"] = int(v.get("progress_ms") or 0) // 10000
        v["_bucket"] = int(now) // 10
    elif name == "cal":
        v = dict(val)
        v.pop("in", None)          # countdown is computed from start + ts
    elif name == "ly":
        v = dict(val)
        v.pop("at", None)          # progress is not part of the line
    elif name == "svc":
        v = dict(val)
        v["services"] = [
            {k: s.get(k) for k in ("id", "name", "kind", "port", "up",
                                    "players", "max", "critical", "note")}
            for s in (val.get("services") or [])
        ]
        v["_bucket"] = int(now) // 10   # refresh latency numbers every 10 s
    else:
        v = val
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def state_delta(known=None):
    """Sections whose revision differs from what the client already has."""
    now = time.time()
    sections = _state_snapshot()
    revs = {}
    with _STATE_LOCK:
        for name, val in sections.items():
            sig = _state_sig(name, val, now)
            if _STATE_SIG.get(name) != sig:
                _STATE_SIG[name] = sig
                _STATE_REV[name] = _STATE_REV.get(name, 0) + 1
            revs[name] = _STATE_REV[name]
    out = {"ts": int(now), "rev": revs}
    known = known or {}
    for name, val in sections.items():
        if str(known.get(name)) != str(revs[name]):
            out[name] = val
    return out


def _state_snapshot():
    """Return the last background snapshot, building one on first use."""
    with _STATE_SNAP_LOCK:
        data = _STATE_SNAP["data"]
    if data is not None:
        return data
    data = _state_sections()
    with _STATE_SNAP_LOCK:
        _STATE_SNAP["data"] = data
        _STATE_SNAP["at"] = time.time()
    return data


def state_refresh_loop():
    """Build the state snapshot once a second, off the request threads."""
    while True:
        try:
            data = _state_sections()
            with _STATE_SNAP_LOCK:
                _STATE_SNAP["data"] = data
                _STATE_SNAP["at"] = time.time()
        except Exception:
            pass
        time.sleep(1.0)


def calendar_loop():
    """Keep the calendar snapshot warm so state_delta() never blocks."""
    while True:
        try:
            calendar_next(force=True)
        except Exception:
            pass
        time.sleep(300)


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

    def _stream(self):
        """Push state deltas as newline-delimited JSON over one connection.

        The device treats this as a display feed: the hub decides what the
        next state is, the atom only applies it.  A delta goes out whenever a
        section revision changes, plus a 1 s heartbeat so a dead client is
        noticed and the device can use ts to detect a stalled link.
        """
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.connection.settimeout(5.0)
        except OSError:
            pass
        q = parse_qs(urlparse(self.path).query)
        known = {}
        for name in _STATE_KEYS:
            rev = (q.get("r_" + name) or [""])[0]
            if rev:
                known[name] = rev
        last_beat = 0.0
        try:
            self.wfile.flush()
            while True:
                delta = state_delta(known)
                revs = delta.get("rev") or {}
                now = time.time()
                changed = any(str(known.get(k)) != str(v)
                              for k, v in revs.items())
                if changed or now - last_beat >= 1.0:
                    self.wfile.write(
                        json.dumps(delta, separators=(",", ":"),
                                   default=str).encode() + b"\n")
                    self.wfile.flush()
                    known = dict(revs)
                    last_beat = now
                time.sleep(0.2)
        except Exception:
            # Client went away (or stalled); the thread is done.
            pass
        finally:
            self.close_connection = True

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
        elif path in ("/state", "/state/"):
            q = parse_qs(urlparse(self.path).query)
            known = {}
            for name in _STATE_KEYS:
                rev = (q.get("r_" + name) or [""])[0]
                if rev:
                    known[name] = rev
            body = json.dumps(state_delta(known), default=str).encode()
            self._send(body, "application/json")
        elif path in ("/stream", "/stream/"):
            self._stream()
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
        elif path == "/spotify_restart":
            spotify_bridge.command("restart")
            body = json.dumps({
                "status": "queued",
                "message": "restarting Spotify",
            }).encode()
            self._send(body, "application/json")
        elif path == "/lyrics":
            q = parse_qs(urlparse(self.path).query)
            body = json.dumps(
                spotify_bridge.lyrics((q.get("pos") or [None])[0])
            ).encode()
            self._send(body, "application/json")
        elif path in ("/events", "/events/"):
            q = parse_qs(urlparse(self.path).query)
            since = (q.get("since") or ["0"])[0]
            body = json.dumps(events_since(since)).encode()
            self._send(body, "application/json")
        elif path == "/calendar":
            body = json.dumps(calendar_next()).encode()
            self._send(body, "application/json")
        elif path == "/notify":
            # GET is handy from a browser / cron without a JSON body
            q = parse_qs(urlparse(self.path).query)
            ev = add_event(
                (q.get("kind") or ["manual"])[0][:20],
                (q.get("title") or ["NOTICE"])[0][:40],
                (q.get("body") or [""])[0][:160])
            body = json.dumps({"ok": True, "event": ev}).encode()
            self._send(body, "application/json")
        else:
            self._send(HTML.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authorized(path):
            body = b'{"status":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        if path in ("/notify", "/notify/"):
            data = {}
            try:
                data = json.loads(raw.decode("utf-8", "replace") or "{}")
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            ev = add_event(
                str(data.get("kind") or "manual")[:20],
                str(data.get("title") or data.get("message") or "NOTICE")[:40],
                str(data.get("body") or data.get("text") or "")[:160])
            body = json.dumps({"ok": True, "event": ev}).encode()
            self._send(body, "application/json")
        else:
            self._send(b'{"ok":false,"error":"unknown endpoint"}',
                       "application/json")



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
    threading.Thread(target=calendar_loop, daemon=True).start()
    threading.Thread(target=state_refresh_loop, daemon=True).start()
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
