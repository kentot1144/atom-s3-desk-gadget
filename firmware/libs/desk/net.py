import json
import socket
import time

import network

try:
    import ntptime
except ImportError:
    ntptime = None


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


class Net:
    def __init__(self, cfg):
        self.cfg = cfg
        wifi = cfg.get("wifi") or {}
        self.ssid = wifi.get("ssid", "")
        self.password = wifi.get("password", "")
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
        self.hub_host = ""
        self.hub_port = 8765
        self.hub_path = "/stats"
        self.hub_targets = []
        self.hub_idx = 0
        hub_cfg = cfg.get("hub") or {}
        urls = hub_cfg.get("urls")
        if not urls:
            urls = [hub_cfg.get("url", "")]
        for u in urls:
            t = self._parse_hub(u)
            if t:
                self.hub_targets.append(t)
        if self.hub_targets:
            self.hub_host, self.hub_port, self.hub_path = self.hub_targets[0]

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

    def poll(self, now):
        try:
            up = self.wlan.isconnected()
        except Exception:
            up = False
        self.connected = up
        if up:
            if not self.time_synced and time.ticks_diff(now, self.ntp_next) >= 0:
                self._sync_ntp(now)
        elif time.ticks_diff(now, self.next_try) >= 0:
            self._connect(now)

    def _connect(self, now):
        self.next_try = time.ticks_add(now, 3000)
        if not self.ssid:
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
            self.next_try = time.ticks_add(now, 2000)
            return
        try:
            self.wlan.connect(self.ssid, self.password)
            self.weather_err = ""
            self.next_try = time.ticks_add(now, 5000)
        except Exception as e:
            self.weather_err = "wifi " + str(e)
            self.next_try = time.ticks_add(now, 30000)

    def _sync_ntp(self, now):
        self.ntp_next = time.ticks_add(now, 30000)
        if ntptime is None:
            return
        try:
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
        if not self.hub_targets:
            return False
        if not self.connected:
            return False
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.hub_next) < 0:
            return False
        host, port, path = self.hub_targets[self.hub_idx]
        try:
            raw = http_get(host, path, port=port,
                           timeout=1.5, max_size=8192)
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
            return True
        except Exception as e:
            self.hub_err = "hub " + str(e)
            if len(self.hub_targets) > 1:
                self.hub_idx = (self.hub_idx + 1) % len(self.hub_targets)
                self.hub_next = time.ticks_add(now, 4000)
            else:
                self.hub_next = time.ticks_add(now, 20000)
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
