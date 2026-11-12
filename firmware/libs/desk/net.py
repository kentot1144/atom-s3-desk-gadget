import json
import socket
import time

import network

try:
    import ntptime
except ImportError:
    ntptime = None

# Resolve NTP with a numeric address: a failing DNS lookup (captive portal,
# hotel / hotspot Wi-Fi) can block for a long time and trip the watchdog.
NTP_HOST = "216.239.35.0"  # time.google.com
NTP_RETRY_MS = 10000       # retry quickly until the first successful sync
NTP_RESYNC_MS = 600000     # then only correct drift every 10 minutes
HUB_EVERY_MS = 10000       # background hub poll also keeps the clock in sync
LIVE_EVERY_MS = 1000       # status pages fetch fresh data every second
HUB_TIMEOUT_S = 1.2        # keep a dead hub from freezing the UI
HUB_RETRY_MS = 15000       # wait before retrying a failed hub address
HUB_BAD_MS = 15000         # ignore a failed address for this long
STATE_LIVE_MS = 3000       # state younger than this counts as live
STREAM_STALE_MS = 6000     # no bytes for this long -> reconnect
STREAM_RETRY_MS = 5000     # first reconnect delay
STREAM_RETRY_MAX_MS = 20000
STATE_KEYS = ("pc", "svc", "sp", "ly", "cal", "ev")


WX_SHORT = {
    0: "CLEAR", 1: "MOSTLY CLEAR", 2: "PARTLY", 3: "CLOUDY",
    45: "FOG", 48: "FOG",
    51: "DRIZZLE", 53: "DRIZZLE", 55: "DRIZZLE", 56: "DRIZZLE", 57: "DRIZZLE",
    61: "RAIN", 63: "RAIN", 65: "RAIN", 66: "RAIN", 67: "RAIN",
    71: "SNOW", 73: "SNOW", 75: "SNOW", 77: "SNOW",
    80: "SHOWERS", 81: "SHOWERS", 82: "SHOWERS",
    85: "SNOW", 86: "SNOW",
    95: "STORM", 96: "STORM", 99: "STORM",
}


def wx_short(code):
    return WX_SHORT.get(code, "?")


_DNS_TTL_MS = 600000
_dns_cache = {}


def _is_ipv4(host):
    parts = host.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or int(part) > 255:
            return False
    return True


def resolve(host, port):
    """Cached, IPv4-first name resolution.

    A failing DNS server (captive portal, hotel Wi-Fi) can block for tens of
    seconds, which used to freeze the whole display.  Numeric addresses skip
    DNS entirely, and a name is only looked up once every ten minutes.
    """
    if _is_ipv4(host):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (host, port))]
    key = (host, port)
    now = time.ticks_ms()
    ent = _dns_cache.get(key)
    if ent and time.ticks_diff(now, ent[0]) < _DNS_TTL_MS:
        return ent[1]
    infos = list(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
    infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    if len(_dns_cache) > 6:
        _dns_cache.clear()
    _dns_cache[key] = (now, infos)
    return infos


def http_get(host, path, port=80, timeout=5.0, max_size=12288):
    # A name can resolve to both IPv4 and IPv6 (or to several addresses).
    # Try each one so a broken IPv4 route does not hide a working IPv6 one.
    infos = resolve(host, port)
    last = None
    for fam, typ, proto, _canon, addr in infos:
        s = socket.socket(fam, typ)
        s.settimeout(timeout)
        try:
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
            s.close()
    if last is not None:
        raise last
    return b""


def http_body(raw):
    i = raw.find(b"\r\n\r\n")
    if i < 0:
        return b""
    return raw[i + 4:]


def http_status(raw):
    try:
        return int(raw.split(b"\r\n", 1)[0].split(b" ")[1])
    except Exception:
        return 0


def with_token(path, token):
    """Append the hub access key to a request path (no-op when empty)."""
    if not token:
        return path
    return path + ("&" if "?" in path else "?") + "k=" + token


class Net:
    def __init__(self, cfg):
        self.cfg = cfg
        self.nets = self._parse_nets(cfg.get("wifi"))
        self.ssid = self.nets[0][0] if self.nets else ""
        self.password = self.nets[0][1] if self.nets else ""
        self.net_idx = 0
        self.try_ssid = ""
        self.wlan = network.WLAN(network.STA_IF)
        self.connected = False
        self.time_synced = False
        self.clock_diff = 0
        self.last_try = time.ticks_ms() - 20000
        self.next_try = 0
        self.ntp_next = time.ticks_ms()
        self.weather = None
        self.weather_at = -1000000
        self.weather_err = ""
        self.hub = None
        self.hub_at = -1000000
        self.hub_next = 0
        self.hub_err = ""
        self.hub_bad = {}       # (host, port) -> ticks until we retry it
        self.last_err = ""
        self.live_at = time.ticks_ms() - LIVE_EVERY_MS
        self.services = []
        self.alert = False
        self.alert_ids = []
        self.players = []
        self.uptime = {}
        self.events = []
        self.event_last = 0
        self.events_at = time.ticks_ms() - 60000
        self.events_err = ""
        self.calendar = None
        self.calendar_at = time.ticks_ms() - 600000
        self.calendar_err = ""
        # One authoritative snapshot pushed by the hub.  Every page reads
        # from here; the atom never combines endpoints on its own.
        now0 = time.ticks_ms()
        self.state = {}
        self.state_rev = {}
        self.state_at = now0 - 1000000
        self.state_ts = 0
        self.state_err = ""
        self.state_next = 0
        self.sp_at = now0
        self.sp_pos = 0
        self.stream = None
        self.stream_buf = b""
        self.stream_last = now0
        self.stream_next = 0
        self.stream_retry = STREAM_RETRY_MS
        self.stream_err = ""
        self.hub_host = ""
        self.hub_port = 8765
        self.hub_path = "/stats"
        self.hub_targets = []
        self.hub_idx = 0
        hub_cfg = cfg.get("hub") or {}
        self.hub_mac = ((hub_cfg.get("mac") or "").strip().lower()
                        .replace("-", ":"))
        self.disc_port = int(hub_cfg.get("discovery_port", 8766))
        self.disc_at = time.ticks_ms() - 60000
        self.hub_token = (hub_cfg.get("token") or "").strip()
        urls = hub_cfg.get("urls")
        if not urls:
            urls = [hub_cfg.get("url", "")]
        for u in urls:
            t = self._parse_hub(u)
            if t:
                self.hub_targets.append(t)
        if self.hub_targets:
            self.hub_host, self.hub_port, self.hub_path = self.hub_targets[0]

    def _parse_nets(self, wifi):
        """Accept a single network dict, a list, or {"networks": [...]}."""
        if not wifi:
            return []
        if isinstance(wifi, dict) and isinstance(wifi.get("networks"), list):
            src = wifi["networks"]
        elif isinstance(wifi, list):
            src = wifi
        elif isinstance(wifi, dict):
            src = [wifi]
        else:
            return []
        out = []
        for w in src:
            if isinstance(w, dict):
                ssid = w.get("ssid") or ""
                if ssid:
                    out.append((ssid, w.get("password") or ""))
        return out

    def _parse_hub(self, url):
        if not url:
            return None
        u = url.strip()
        path = "/stats"
        port = 8765
        if u.startswith("http://"):
            u = u[7:]
        elif u.startswith("https://"):
            u = u[8:]
        if "/" in u:
            u, p = u.split("/", 1)
            path = "/" + p
        if ":" in u:
            u, ps = u.split(":", 1)
            try:
                port = int(ps)
            except Exception:
                pass
        if not u:
            return None
        return (u, port, path)

    def discover(self, now):
        """Find the hub by UDP broadcast on the LAN.

        The hub answers with its port and MAC addresses. If hub.mac is set in
        config.json only that PC is accepted, so a DHCP address change or a
        different machine on the same LAN cannot hijack the link.
        """
        self.disc_at = now
        try:
            cfg = self.wlan.ifconfig()
            ip, mask = cfg[0], cfg[1]
        except Exception:
            return False
        bcasts = ["255.255.255.255"]
        try:
            a = [int(x) for x in ip.split(".")]
            m = [int(x) for x in mask.split(".")]
            bc = ".".join(str(a[i] | (255 - m[i])) for i in range(4))
            if bc not in bcasts:
                bcasts.append(bc)
        except Exception:
            pass
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
            s.settimeout(0.3)
            for bc in bcasts:
                try:
                    s.sendto(b"ATOMDISCOVER", (bc, self.disc_port))
                except Exception:
                    pass
            end = time.ticks_add(time.ticks_ms(), 1200)
            while time.ticks_diff(end, time.ticks_ms()) > 0:
                try:
                    data, addr = s.recvfrom(256)
                except OSError:
                    continue
                try:
                    d = json.loads(data.decode())
                except Exception:
                    continue
                if d.get("service") != "atom-hub":
                    continue
                if self.hub_mac:
                    macs = [str(x).lower().replace("-", ":")
                            for x in (d.get("macs") or [])]
                    if self.hub_mac not in macs:
                        continue
                host = addr[0]
                tgt = (host, int(d.get("port") or 8765), "/stats")
                new = [tgt]
                for t in self.hub_targets:
                    if t[0] != host:
                        new.append(t)
                self.hub_targets = new
                self.hub_host, self.hub_port, self.hub_path = tgt
                self.hub_idx = 0
                self.hub_next = 0
                self.hub_err = ""
                return True
        finally:
            s.close()
        return False

    def poll(self, now):
        try:
            up = self.wlan.isconnected()
        except Exception:
            up = False
        self.connected = up
        if up:
            if self.try_ssid:
                # remember which network worked so reconnects retry it first
                for i, n in enumerate(self.nets):
                    if n[0] == self.try_ssid:
                        self.net_idx = i
                        self.ssid, self.password = n
                        break
                self.try_ssid = ""
            if time.ticks_diff(now, self.ntp_next) >= 0:
                self._sync_ntp(now)
            self.stream_poll(now)
        elif time.ticks_diff(now, self.next_try) >= 0:
            self._connect(now)

    def _connect(self, now):
        self.next_try = time.ticks_add(now, 3000)
        if not self.nets:
            return
        try:
            if not self.wlan.active():
                self.wlan.active(True)
                return
        except Exception as e:
            self.weather_err = "wifi act " + str(e)
            self.next_try = time.ticks_add(now, 30000)
            return
        try:
            st = self.wlan.status()
        except Exception as e:
            self.weather_err = "wifi st " + str(e)
            self.next_try = time.ticks_add(now, 30000)
            return
        if st == 1001 or st == 1010:
            self.next_try = time.ticks_add(now, 1500)
            return
        if st == 1002 or st == 1003 or st == 1004:
            # wrong password / no AP / connect failed: move on quickly
            self.next_try = time.ticks_add(now, 500)
            return
        ssid, password = self.nets[self.net_idx]
        try:
            if password:
                self.wlan.connect(ssid, password)
            else:
                self.wlan.connect(ssid)          # open network
            self.try_ssid = ssid
            self.ssid = ssid
            self.password = password
            self.weather_err = ""
            # try the next known network if this one does not come up
            self.net_idx = (self.net_idx + 1) % len(self.nets)
            self.next_try = time.ticks_add(now, 3000)
        except Exception as e:
            self.weather_err = "wifi " + str(e)
            self.net_idx = (self.net_idx + 1) % len(self.nets)
            self.next_try = time.ticks_add(now, 3000)

    def now(self):
        """Current epoch, corrected when the RTC could not be set by NTP."""
        return int(time.time()) + self.clock_diff

    def _sync_ntp(self, now):
        self.ntp_next = time.ticks_add(now, NTP_RETRY_MS)
        if ntptime is None:
            return
        try:
            try:
                # Numeric host: no DNS, the UDP socket itself times out fast.
                ntptime.settime(NTP_HOST)
            except TypeError:
                # Older builds do not accept a host argument.
                ntptime.settime()
            self.time_synced = True
            self.clock_diff = 0      # RTC is correct now, drop the hub offset
            self.ntp_next = time.ticks_add(now, NTP_RESYNC_MS)
        except Exception as e:
            self.weather_err = "ntp " + str(e)

    def maybe_weather(self, force=False):
        if not self.connected:
            return False
        now = time.ticks_ms()
        if (not force and self.weather is not None and
                time.ticks_diff(now, self.weather_at) < 600000):
            return False
        loc = self.cfg.get("location") or {}
        try:
            lat = float(loc.get("lat", 35.68))
            lon = float(loc.get("lon", 139.69))
            path = ("/v1/forecast?latitude=%.4f&longitude=%.4f"
                    "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
                    "&daily=temperature_2m_max,temperature_2m_min"
                    "&timezone=Asia%%2FTokyo&forecast_days=1") % (lat, lon)
            raw = http_get("api.open-meteo.com", path, timeout=5.0, max_size=4096)
            data = json.loads(http_body(raw))
            cur = data["current"]
            day = data["daily"]
            self.weather = {
                "temp": cur["temperature_2m"],
                "hum": cur["relative_humidity_2m"],
                "code": cur["weather_code"],
                "wind": cur["wind_speed_10m"],
                "tmax": day["temperature_2m_max"][0],
                "tmin": day["temperature_2m_min"][0],
            }
            self.weather_at = time.ticks_ms()
            self.weather_err = ""
            return True
        except Exception as e:
            self.weather_err = "wx " + str(e)
            self.weather_at = time.ticks_ms() - 480000
            return False

    def _target_ok(self, host):
        """A LAN address is only useful on the LAN we are actually on.

        The gadget moves between home Wi-Fi and phones/hotspots. Trying a
        private 192.168.x.x hub address from another subnet can never work,
        and doing it on every UI frame is what made the pages lag.
        """
        try:
            parts = [int(x) for x in host.split(".")]
        except Exception:
            return True          # hostname: keep it
        if len(parts) != 4:
            return True
        a, b = parts[0], parts[1]
        if a == 127:
            return False
        private = (a == 10 or (a == 192 and b == 168)
                   or (a == 172 and 16 <= b <= 31))
        if not private:
            return True
        try:
            ip, mask = self.wlan.ifconfig()[:2]
            ip = [int(x) for x in ip.split(".")]
            mask = [int(x) for x in mask.split(".")]
            if len(ip) != 4 or len(mask) != 4:
                return True
            for i in range(4):
                if (parts[i] & mask[i]) != (ip[i] & mask[i]):
                    return False
            return True
        except Exception:
            return True

    def _hub_candidates(self, now):
        """Hub addresses worth trying right now, best first."""
        out = []
        seen = []
        if self.hub_host:
            first = (self.hub_host, int(self.hub_port or 8765),
                     self.hub_path or "/stats")
            out.append(first)
            seen.append((first[0], first[1]))
        for t in self.hub_targets:
            tp = (t[0], int(t[1] or 8765), t[2] or "/stats")
            if (tp[0], tp[1]) in seen:
                continue
            seen.append((tp[0], tp[1]))
            out.append(tp)
        good = []
        for host, port, path in out:
            if not self._target_ok(host):
                continue
            if time.ticks_diff(self.hub_bad.get((host, port), 0), now) > 0:
                continue
            good.append((host, port, path))
        # On the home LAN the numeric hub address is fastest and does not
        # depend on DNS or NAT hairpin; try it before the public name.
        good.sort(key=lambda t: 0 if _is_ipv4(t[0]) else 1)
        return good

    def hub_age(self):
        """Seconds since the last successful hub answer (large if never)."""
        return time.ticks_diff(time.ticks_ms(), self.hub_at) // 1000

    # -------------------------------------------------- authoritative state
    def state_age(self):
        return time.ticks_diff(time.ticks_ms(), self.state_at) // 1000

    def state_fresh(self, ms=STATE_LIVE_MS):
        return time.ticks_diff(time.ticks_ms(), self.state_at) <= ms

    def sp_progress(self, now=None):
        """Progress between hub anchors; the hub owns the real position."""
        if now is None:
            now = time.ticks_ms()
        sp = self.state.get("sp") or {}
        pos = self.sp_pos
        if sp.get("is_playing"):
            pos += time.ticks_diff(now, self.sp_at)
        dur = int(sp.get("duration_ms") or 0)
        if pos < 0:
            pos = 0
        if dur and pos > dur:
            pos = dur
        return pos

    def _apply_events(self, ev):
        """Merge the last few hub events without showing one twice."""
        try:
            last = int(ev.get("last") or 0)
        except Exception:
            last = 0
        if last < self.event_last:
            # hub restarted and its event ids restarted too
            self.event_last = 0
        for e in ev.get("events") or []:
            eid = int(e.get("id") or 0)
            if eid > self.event_last:
                self.event_last = eid
                self.events.append(e)
        if len(self.events) > 10:
            del self.events[:-10]

    def _apply_state(self, j):
        """Merge one hub snapshot/delta into the local display state."""
        if not isinstance(j, dict):
            return False
        ts = int(j.get("ts") or 0)
        if ts:
            self.state_ts = ts
            diff = ts - int(time.time())
            if abs(diff) > 1:
                self.clock_diff = diff
        revs = j.get("rev") or {}
        for k in STATE_KEYS:
            if k in revs:
                self.state_rev[k] = revs[k]
            if k not in j:
                continue
            val = j[k]
            self.state[k] = val
            if k == "pc":
                self.hub = val or {}
            elif k == "svc":
                svc = val or {}
                self.services = svc.get("services") or []
                self.alert = bool(svc.get("alert"))
                self.alert_ids = svc.get("alert_ids") or []
                self.players = svc.get("players") or []
                self.uptime = svc.get("uptime") or {}
            elif k == "sp":
                self.sp_pos = int((val or {}).get("progress_ms") or 0)
                self.sp_at = time.ticks_ms()
            elif k == "cal":
                self.calendar = val
            elif k == "ev":
                self._apply_events(val or {})
        self.state_at = time.ticks_ms()
        self.hub_at = self.state_at
        self.state_err = ""
        self.hub_err = ""
        self.hub_next = time.ticks_add(self.state_at, HUB_EVERY_MS)
        return True

    def _state_path(self):
        path = "/state"
        sep = "?"
        for k in STATE_KEYS:
            rev = self.state_rev.get(k)
            if rev is not None:
                path += sep + "r_" + k + "=" + str(rev)
                sep = "&"
        return path

    def _state_query(self):
        """One-shot fallback when the long-lived stream is not connected."""
        if not self.connected:
            self.last_err = "NO WIFI"
            return False
        now = time.ticks_ms()
        d = self.hub_get(self._state_path(), timeout=HUB_TIMEOUT_S,
                         max_size=12288, limit=1)
        if not d:
            self.state_err = self.last_err or "HUB OFFLINE"
            # Do not let every page redraw try the same dead address again.
            self.state_next = time.ticks_add(now, HUB_RETRY_MS)
            return False
        return self._apply_state(d)

    def _stream_close(self):
        s = self.stream
        self.stream = None
        self.stream_buf = b""
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        self.stream_next = time.ticks_add(time.ticks_ms(), self.stream_retry)

    def _stream_connect(self, now):
        self.stream_next = time.ticks_add(now, self.stream_retry)
        cand = self._hub_candidates(now)
        for host, port, _ in cand:
            s = None
            last = None
            try:
                infos = resolve(host, port)
                for fam, typ, _proto, _canon, addr in infos:
                    try:
                        s = socket.socket(fam, typ)
                        s.settimeout(HUB_TIMEOUT_S)
                        s.connect(addr)
                        break
                    except Exception as e:
                        last = e
                        if s is not None:
                            try:
                                s.close()
                            except Exception:
                                pass
                            s = None
                if s is None:
                    if last is not None:
                        raise last
                    continue
                path = "/stream"
                sep = "?"
                if self.hub_token:
                    path += sep + "k=" + self.hub_token
                    sep = "&"
                for k in STATE_KEYS:
                    rev = self.state_rev.get(k)
                    if rev is not None:
                        path += sep + "r_" + k + "=" + str(rev)
                        sep = "&"
                req = ("GET " + path + " HTTP/1.0\r\nHost: " + host +
                       "\r\nConnection: keep-alive"
                       "\r\nUser-Agent: AtomS3-Desk\r\n\r\n")
                s.send(req.encode())
                s.settimeout(0)
                self.stream = s
                self.stream_buf = b""
                self.stream_last = now
                self.stream_host = host
                self.stream_port = int(port)
                self.hub_host, self.hub_port = host, int(port)
                self.hub_bad.pop((host, port), None)
                self.stream_retry = STREAM_RETRY_MS
                self.stream_err = ""
                return True
            except Exception as e:
                self.stream_err = "stream " + str(e)[:20]
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
                self.hub_bad[(host, port)] = time.ticks_add(now, HUB_BAD_MS)
        if self.stream_retry < STREAM_RETRY_MAX_MS:
            self.stream_retry *= 2
            if self.stream_retry > STREAM_RETRY_MAX_MS:
                self.stream_retry = STREAM_RETRY_MAX_MS
        return False

    def stream_poll(self, now):
        """Read pushed state without ever blocking the UI loop."""
        if not self.connected:
            if self.stream is not None:
                self._stream_close()
            return
        if self.stream is None:
            if time.ticks_diff(now, self.stream_next) >= 0:
                self._stream_connect(now)
            return
        for _ in range(4):
            try:
                data = self.stream.recv(1024)
            except OSError as e:
                code = e.args[0] if e.args else 0
                if code in (11, 110, 10035):
                    break              # EAGAIN: no more bytes right now
                self._stream_close()
                return
            except Exception:
                self._stream_close()
                return
            if data is None:
                break              # some ports report would-block as None
            if not data:
                self._stream_close()
                return
            self.stream_last = now
            self.stream_buf += data
            if len(self.stream_buf) > 12288:
                self._stream_close()
                return
            while True:
                i = self.stream_buf.find(b"\n")
                if i < 0:
                    break
                line = self.stream_buf[:i].strip()
                self.stream_buf = self.stream_buf[i + 1:]
                if not line:
                    continue
                try:
                    self._apply_state(json.loads(line))
                except Exception as e:
                    self.stream_err = "state " + str(e)[:16]
        if time.ticks_diff(now, self.stream_last) > STREAM_STALE_MS:
            self._stream_close()

    def hub_get(self, path, timeout=HUB_TIMEOUT_S, max_size=4096, limit=0):
        """One short GET to the hub, skipping dead addresses.

        Used by the action pages (Spotify, restart) so a failed hub cannot
        block the button/UI loop for several seconds on every poll.
        """
        if not self.connected:
            self.last_err = "NO WIFI"
            return None
        self.last_err = ""
        now = time.ticks_ms()
        cands = self._hub_candidates(now)
        if limit:
            cands = cands[:limit]
        for host, port, _ in cands:
            try:
                raw = http_get(host, with_token(path, self.hub_token),
                               port=port, timeout=timeout, max_size=max_size)
                if http_status(raw) == 401:
                    self.last_err = "BAD TOKEN"
                    continue
                body = http_body(raw)
                if not body:
                    self.last_err = "EMPTY"
                    continue
                data = json.loads(body)
                self.hub_host, self.hub_port = host, port
                self.hub_bad.pop((host, port), None)
                self.last_err = ""
                return data
            except Exception as e:
                msg = str(e)
                if "116" in msg or "110" in msg:
                    self.last_err = "HUB TIMEOUT"
                elif "111" in msg or "104" in msg or "113" in msg:
                    self.last_err = "HUB OFFLINE"
                else:
                    self.last_err = msg[:22]
                self.hub_bad[(host, port)] = time.ticks_add(now, HUB_BAD_MS)
        if not self.last_err:
            self.last_err = "HUB OFFLINE"
        return None

    def maybe_hub(self, force=False, live=False):
        """Refresh the authoritative snapshot (stream first, /state fallback)."""
        if not self.connected:
            return False
        if self.state_fresh():
            return True
        now = time.ticks_ms()
        if not self.hub_targets:
            if time.ticks_diff(now, self.disc_at) > 15000:
                self.discover(now)
            if not self.hub_targets:
                self.state_err = "HUB NOT SET"
                return False
        if not force and not live and time.ticks_diff(now, self.hub_next) < 0:
            return False
        return self._state_query()

    def maybe_hub_live(self, now):
        """Called by live pages; the stream usually keeps this a no-op."""
        if self.state_fresh():
            return True
        if self.state_err and time.ticks_diff(now, self.state_next) < 0:
            return False
        if time.ticks_diff(now, self.live_at) < LIVE_EVERY_MS:
            return False
        self.live_at = now
        return self.maybe_hub(live=True)

    def maybe_events(self, force=False):
        """Notifications now arrive inside the hub state snapshot."""
        return bool(self.events)

    def maybe_calendar(self, force=False):
        """The calendar is part of the hub state; no separate fetch any more."""
        return self.calendar is not None

    def _legacy_services(self, hub):
        out = []
        mc = hub.get("minecraft")
        if mc:
            out.append({
                "id": "mc", "name": "MINECRAFT", "kind": "mc", "port": 25565,
                "up": bool(mc.get("online")), "ms": 0,
                "players": mc.get("players", 0), "max": mc.get("max", 0),
                "critical": False,
            })
        for s in hub.get("servers") or []:
            out.append({
                "id": "web", "name": s.get("name", "WEB"), "kind": "http",
                "port": 0, "up": bool(s.get("up")), "ms": s.get("ms", 0),
                "players": 0, "max": 0, "critical": False,
            })
        return out
