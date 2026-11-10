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
NTP_RETRY_MS = 300000      # only retry every 5 minutes, not every 30 s


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


def http_get(host, path, port=80, timeout=5.0, max_size=12288):
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
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
    finally:
        s.close()


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
            end = time.ticks_add(time.ticks_ms(), 2000)
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
            if not self.time_synced and time.ticks_diff(now, self.ntp_next) >= 0:
                self._sync_ntp(now)
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

    def maybe_hub(self, force=False):
        if not self.connected:
            return False
        now = time.ticks_ms()
        if not self.hub_targets:
            if time.ticks_diff(now, self.disc_at) > 15000:
                self.discover(now)
            if not self.hub_targets:
                return False
        if not force and time.ticks_diff(now, self.hub_next) < 0:
            return False
        host, port, path = self.hub_targets[self.hub_idx]
        try:
            raw = http_get(host, with_token(path, self.hub_token), port=port,
                           timeout=1.5, max_size=8192)
            if http_status(raw) == 401:
                raise ValueError("bad token")
            hub = json.loads(http_body(raw))
            self.hub = hub
            self.hub_host, self.hub_port, self.hub_path = host, port, path
            self.hub_at = time.ticks_ms()
            self.hub_err = ""
            self.hub_next = time.ticks_add(now, 15000)
            svcs = hub.get("services")
            if svcs is None:
                svcs = self._legacy_services(hub)
            self.services = svcs or []
            self.alert = bool(hub.get("alert"))
            self.alert_ids = hub.get("alert_ids") or []
            self.players = hub.get("players") or []
            self.uptime = hub.get("svc_uptime") or {}
            return True
        except Exception as e:
            self.hub_err = "hub " + str(e)
            if time.ticks_diff(now, self.disc_at) > 20000:
                if self.discover(now):
                    return False
            if len(self.hub_targets) > 1:
                self.hub_idx = (self.hub_idx + 1) % len(self.hub_targets)
                self.hub_next = time.ticks_add(now, 4000)
            else:
                self.hub_next = time.ticks_add(now, 20000)
            return False

    def maybe_events(self, force=False):
        """Fetch notifications posted since the last id we saw."""
        if not self.connected or not self.hub_host:
            return False
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.events_at) < 15000:
            return False
        self.events_at = now
        try:
            path = "/events?since=%d" % self.event_last
            raw = http_get(self.hub_host, with_token(path, self.hub_token),
                           port=self.hub_port, timeout=2.0, max_size=6144)
            if http_status(raw) == 401:
                raise ValueError("bad token")
            d = json.loads(http_body(raw))
            last = int(d.get("last") or 0)
            if last < self.event_last:
                # hub restarted and its ids restarted: replay from zero
                self.event_last = 0
            for ev in d.get("events") or []:
                eid = int(ev.get("id") or 0)
                if eid > self.event_last:
                    self.event_last = eid
                    self.events.append(ev)
            if len(self.events) > 10:
                del self.events[:-10]
            self.events_err = ""
            return True
        except Exception as e:
            self.events_err = "ev " + str(e)[:16]
            return False

    def maybe_calendar(self, force=False):
        """Next calendar event from the hub's ICS feed, every 5 minutes."""
        if not self.connected or not self.hub_host:
            return False
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.calendar_at) < 300000:
            return False
        self.calendar_at = now
        try:
            raw = http_get(self.hub_host, with_token("/calendar", self.hub_token),
                           port=self.hub_port, timeout=3.0, max_size=2048)
            if http_status(raw) == 401:
                raise ValueError("bad token")
            self.calendar = json.loads(http_body(raw))
            self.calendar_err = ""
            return True
        except Exception as e:
            self.calendar_err = "cal " + str(e)[:16]
            return False

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
