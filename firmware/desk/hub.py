"""Hub polling over plain HTTP with DNS caching and target failover."""
import json
import socket
import time
from desk.const import HUB_BAD_MS, HUB_TIMEOUT_S

# ---------------------------------------------------------------------- hub
_dns = {}


def _is_ipv4(host):
    parts = host.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or int(p) > 255:
            return False
    return True


def _resolve(host, port):
    if _is_ipv4(host):
        return [(socket.AF_INET, socket.SOCK_STREAM, (host, port))]
    key = (host, port)
    now = time.ticks_ms()
    e = _dns.get(key)
    if e and time.ticks_diff(now, e[0]) < 600000:
        return e[1]
    infos = []
    for fam, typ, _proto, _canon, addr in socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM):
        if fam == socket.AF_INET:
            infos.insert(0, (fam, typ, addr))
        else:
            infos.append((fam, typ, addr))
    if len(_dns) > 6:
        _dns.clear()
    _dns[key] = (now, infos)
    return infos


def http_get(host, path, port, timeout, max_size):
    last = None
    for fam, typ, addr in _resolve(host, port):
        s = None
        try:
            s = socket.socket(fam, typ)
            s.settimeout(timeout)
            s.connect(addr)
            req = ("GET " + path + " HTTP/1.0\r\nHost: " + host +
                   "\r\nConnection: close\r\nUser-Agent: AtomS3-Desk\r\n\r\n")
            s.send(req.encode())
            parts = []
            total = 0
            while total < max_size:
                d = s.recv(512)
                if not d:
                    break
                parts.append(d)
                total += len(d)
            return b"".join(parts)
        except Exception as e:
            last = e
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    if last is not None:
        raise last
    return b""


def _body(raw):
    i = raw.find(b"\r\n\r\n")
    return raw[i + 4:] if i >= 0 else b""


def _status(raw):
    try:
        return int(raw.split(b"\r\n", 1)[0].split(b" ")[1])
    except Exception:
        return 0


def _parse_url(u):
    if not u:
        return None
    u = u.strip()
    port = 8765
    if u.startswith("http://"):
        u = u[7:]
    elif u.startswith("https://"):
        u = u[8:]
    if "/" in u:
        u = u.split("/", 1)[0]
    if ":" in u:
        u, ps = u.split(":", 1)
        try:
            port = int(ps)
        except Exception:
            pass
    return (u, port) if u else None


class Hub:
    def __init__(self, wlan, cfg):
        self.wlan = wlan
        h = cfg.get("hub") or {}
        self.token = (h.get("token") or "").strip()
        urls = h.get("urls") or []
        if not urls:
            urls = [h.get("url") or ""]
        self.targets = []
        for u in urls:
            t = _parse_url(u)
            if t:
                self.targets.append(t)
        self.host, self.port = (self.targets[0] if self.targets
                                else ("", 8765))
        self.bad = {}
        self.rev = {}
        self.ui = None
        self.sp = {}
        self.ly = {}
        self.ev = {}
        self.ts = 0
        self.clock_diff = 0
        self.ok_at = time.ticks_ms() - 1000000
        self.at = time.ticks_ms() - 1000000
        self.err = ""
        self.new_events = []
        self.last_event = 0
        self.sp_pos = 0
        self.sp_at = time.ticks_ms()
        self.sp_hist = []
        self.sp_stalled = False
        self.sp_key = None
        self.sp_playing = False

    def age(self):
        return time.ticks_diff(time.ticks_ms(), self.ok_at) // 1000

    def fresh(self, ms=6000):
        return time.ticks_diff(time.ticks_ms(), self.ok_at) <= ms

    def now(self):
        return int(time.time()) + self.clock_diff

    def progress(self):
        pos = self.sp_pos
        if self.playing():
            pos += time.ticks_diff(time.ticks_ms(), self.sp_at)
        dur = int(self.sp.get("duration_ms") or 0)
        if pos < 0:
            pos = 0
        if dur and pos > dur:
            pos = dur
        return pos

    def playing(self):
        """Spotify is really playing, not just a stale is_playing flag."""
        return bool(self.sp.get("is_playing")) and not self.sp_stalled

    def _watch_sp(self):
        """Catch a frozen position even if the hub still says playing.

        The hub normally filters this, but an older hub build (or a stalled
        state refresh) can leave is_playing=True.  Anchors arrive every few
        seconds; a real track advances by roughly the elapsed time, while a
        paused one only jitters by a second or so.
        """
        now = time.ticks_ms()
        sp = self.sp
        playing = bool(sp.get("is_playing"))
        key = (sp.get("song"), sp.get("artist"),
               int(sp.get("duration_ms") or 0))
        if playing and not self.sp_playing:
            # Playback just started: trust the hub and start a fresh window
            # instead of staying stuck on an old stall.
            self.sp_hist = []
            self.sp_stalled = False
        self.sp_playing = playing
        if not playing or self.sp_key != key:
            self.sp_key = key
            self.sp_hist = []
            self.sp_stalled = False
            if not playing:
                return
        self.sp_hist.append((now, self.sp_pos))
        while self.sp_hist and (
                len(self.sp_hist) > 6 or
                time.ticks_diff(now, self.sp_hist[0][0]) > 12000):
            self.sp_hist.pop(0)
        if len(self.sp_hist) < 2:
            return
        if self.sp_stalled:
            # Fast resume: a position that clearly advances over the last
            # couple of samples means playback restarted, even while old
            # frozen samples are still inside the window.
            t1, p1 = self.sp_hist[-1]
            for t, p in reversed(self.sp_hist[:-1]):
                d2 = time.ticks_diff(t1, t)
                if d2 >= 1500:
                    if p1 - p >= d2 * 0.6:
                        self.sp_stalled = False
                    break
            if not self.sp_stalled:
                return
        t0, p0 = self.sp_hist[0]
        dt = time.ticks_diff(now, t0)
        if dt < 6000:
            return
        advance = self.sp_pos - p0
        if advance < -2000:
            # A seek or track jump is not a stall.
            self.sp_hist = [(now, self.sp_pos)]
            self.sp_stalled = False
            return
        self.sp_stalled = advance < dt * 0.4

    def _ok_target(self, host):
        """A private hub address is only useful on its own subnet."""
        try:
            parts = [int(x) for x in host.split(".")]
        except Exception:
            return True
        if len(parts) != 4:
            return True
        a, b = parts[0], parts[1]
        if a == 127:
            return False
        if not (a == 10 or (a == 192 and b == 168) or
                (a == 172 and 16 <= b <= 31)):
            return True
        try:
            ip, mask = self.wlan.ifconfig()[:2]
            ip = [int(x) for x in ip.split(".")]
            mask = [int(x) for x in mask.split(".")]
            for i in range(4):
                if (parts[i] & mask[i]) != (ip[i] & mask[i]):
                    return False
            return True
        except Exception:
            return True

    def _cands(self, now):
        out = []
        if self.host:
            out.append((self.host, self.port))
        for t in self.targets:
            if t not in out:
                out.append(t)
        good = []
        for host, port in out:
            if not self._ok_target(host):
                continue
            if time.ticks_diff(self.bad.get((host, port), 0), now) > 0:
                continue
            good.append((host, port))
        good.sort(key=lambda t: 0 if _is_ipv4(t[0]) else 1)
        return good

    def get(self, path, timeout=HUB_TIMEOUT_S, max_size=8192):
        now = time.ticks_ms()
        for host, port in self._cands(now):
            try:
                if self.token:
                    path2 = path + ("&" if "?" in path else "?") + \
                        "k=" + self.token
                else:
                    path2 = path
                raw = http_get(host, path2, port, timeout, max_size)
                if _status(raw) == 401:
                    self.err = "BAD TOKEN"
                    continue
                body = _body(raw)
                if not body:
                    self.err = "EMPTY"
                    continue
                d = json.loads(body)
                self.host, self.port = host, port
                self.bad.pop((host, port), None)
                self.err = ""
                return d
            except Exception as e:
                msg = str(e)
                if "116" in msg or "110" in msg:
                    self.err = "HUB TIMEOUT"
                elif "111" in msg or "104" in msg or "113" in msg:
                    self.err = "HUB OFFLINE"
                else:
                    self.err = msg[:22]
                self.bad[(host, port)] = time.ticks_add(now, HUB_BAD_MS)
        if not self.err:
            self.err = "HUB OFFLINE"
        return None

    def poll(self, now, interval_ms):
        if time.ticks_diff(now, self.at) < 0:
            return False
        self.at = time.ticks_add(now, interval_ms)
        path = "/state?only=ui,sp,ly,ev"
        for k, v in self.rev.items():
            path += "&r_" + k + "=" + str(v)
        d = self.get(path, timeout=HUB_TIMEOUT_S, max_size=12288)
        if not d:
            return False
        self.apply(d)
        return True

    def apply(self, j):
        ts = int(j.get("ts") or 0)
        if ts:
            self.ts = ts
            diff = ts - int(time.time())
            if abs(diff) > 1:
                self.clock_diff = diff
        for k, v in (j.get("rev") or {}).items():
            self.rev[k] = v
        if "ui" in j:
            self.ui = j["ui"]
        if "sp" in j:
            self.sp = j["sp"] or {}
            self.sp_pos = int(self.sp.get("progress_ms") or 0)
            self.sp_at = time.ticks_ms()
            self._watch_sp()
        if "ly" in j:
            self.ly = j["ly"] or {}
        if "ev" in j:
            self._events(j["ev"] or {})
        self.ok_at = time.ticks_ms()

    def _events(self, ev):
        try:
            last = int(ev.get("last") or 0)
        except Exception:
            last = 0
        if last < self.last_event:
            self.last_event = 0
        for e in ev.get("events") or []:
            try:
                eid = int(e.get("id") or 0)
            except Exception:
                eid = 0
            if eid > self.last_event:
                self.last_event = eid
                self.new_events.append(e)
        if len(self.new_events) > 6:
            del self.new_events[:-6]
