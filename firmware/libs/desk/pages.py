import gc
import math
import random
import time

import M5

from desk import ui as u
from desk.net import wx_short

WD = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
       "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def pct_color(p):
    if p < 60:
        return u.GREEN
    if p < 85:
        return u.AMBER
    return u.RED


class BasePage:
    title = ""

    def enter(self, app):
        pass

    def action(self, app):
        pass

    def tick(self, app, now):
        return False

    def draw(self, app):
        pass


class ClockPage(BasePage):
    title = "CLOCK"

    def __init__(self):
        self.sec = -1

    def enter(self, app):
        app.net.maybe_weather()

    def action(self, app):
        app.net.maybe_weather(force=True)

    def tick(self, app, now):
        s = app.local()[5]
        if s != self.sec:
            self.sec = s
            if s % 60 == 0:
                app.net.maybe_weather()
            return True
        return False

    def draw(self, app):
        u.clear()
        t = app.local()
        u.ctext("%s %02d %s" % (WD[t[6]], t[2], MON[t[1] - 1]), u.F_SM, 6, u.MUTED)
        u.seg_time("%02d%02d" % (t[3], t[4]), 24, u.WHITE)

        # seconds progress
        u.LCD.fillRect(14, 76, 100, 3, u.LINE)
        sec = t[5]
        if sec > 0:
            u.LCD.fillRect(14, 76, sec * 100 // 60, 3, u.ACCENT)

        w = app.net.weather
        if w:
            u.temp("%d" % int(round(w["temp"])), u.F_MD, 14, 88, u.AMBER)
        else:
            u.temp("--", u.F_MD, 14, 88, u.LINE)
        if app.net.connected:
            if app.net.alert:
                u.rtext("ALERT", u.F_SM, 120, 90, u.RED)
            else:
                u.rtext("ONLINE", u.F_SM, 120, 90, u.GREEN)
        else:
            u.rtext("NO WIFI", u.F_SM, 120, 90, u.RED)
        u.dots(len(app.pages), app.idx)


class WeatherPage(BasePage):
    title = "WEATHER"

    def __init__(self):
        self.at = 0

    def enter(self, app):
        app.net.maybe_weather()

    def action(self, app):
        app.net.maybe_weather(force=True)

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) > 10000:
            self.at = now
            return True
        return False

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages))
        w = app.net.weather
        if not w:
            if not app.net.connected:
                msg = "NO WIFI"
            elif app.net.weather_err:
                msg = app.net.weather_err
            else:
                msg = "LOADING..."
            u.ctext(msg[:20], u.F_SM, 58, u.MUTED)
            u.dots(len(app.pages), app.idx)
            return
        u.ctemp("%d" % int(round(w["temp"])), u.F_BIG, 34, u.WHITE, r=3)
        u.ctext(wx_short(w["code"]), u.F_MD, 84, u.ACCENT)
        u.ctext("LOW %d   HIGH %d   %d%%" % (
            int(round(w["tmin"])), int(round(w["tmax"])), int(w["hum"])),
            u.F_SM, 106, u.MUTED)
        u.dots(len(app.pages), app.idx)


class PomodoroPage(BasePage):
    title = "FOCUS"

    def __init__(self):
        self.at = 0

    def action(self, app):
        app.pom.toggle()

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) >= 250:
            self.at = now
            return True
        return False

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages))
        p = app.pom
        if p.mode == "focus":
            color = u.RED
            label = "FOCUS"
        else:
            color = u.GREEN
            label = "BREAK"
        u.ctext(label, u.F_SM, 28, color)
        rem = p.rem
        if rem < 0:
            rem = 0
        m = rem // 60000
        s = (rem % 60000) // 1000
        u.seg_time("%02d%02d" % (m, s), 42, u.WHITE)
        total = p.focus if p.mode == "focus" else p.brk
        pct = rem * 100 // total
        u.bar(14, 94, 100, 5, pct, color)
        if p.running:
            hint = "2x CLICK PAUSE"
        else:
            hint = "2x CLICK START"
        u.ctext(hint, u.F_TINY, 104, u.MUTED)
        u.rtext("S%d" % p.sessions, u.F_TINY, 122, 28, u.MUTED)
        u.dots(len(app.pages), app.idx)


class PcPage(BasePage):
    title = "PC"

    def __init__(self):
        self.at = 0

    def enter(self, app):
        app.net.maybe_hub(force=True)

    def action(self, app):
        app.net.maybe_hub(force=True)

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) > 1000:
            self.at = now
            app.net.maybe_hub()
            return True
        return False

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages))
        h = app.net.hub
        if not h:
            if not app.net.connected:
                msg = "NO WIFI"
            elif not app.net.hub_host:
                msg = "HUB NOT SET"
            else:
                msg = app.net.hub_err or "HUB OFFLINE"
            u.ctext(msg[:20], u.F_SM, 58, u.MUTED)
            u.dots(len(app.pages), app.idx)
            return

        cpu = h.get("cpu") or {}
        mem = h.get("mem") or {}
        gpu = h.get("gpu") or {}
        rows = [
            ("CPU", cpu.get("percent", 0), "%d%%" % int(cpu.get("percent", 0))),
            ("MEM", mem.get("percent", 0), "%d%%" % int(mem.get("percent", 0))),
        ]
        y = 28
        for name, val, txt in rows:
            u.text(name, u.F_SM, 4, y, u.MUTED)
            u.bar(34, y + 3, 56, 7, val, pct_color(val))
            u.rtext(txt, u.F_SM, 124, y, u.WHITE)
            y += 22

        if gpu:
            gu = gpu.get("util", 0)
            u.text("GPU", u.F_SM, 4, y, u.MUTED)
            u.bar(34, y + 3, 46, 7, gu, pct_color(gu))
            u.rtemp("%d" % int(gpu.get("temp", 0)), u.F_SM, 124, y, u.ORANGE)
        else:
            u.text("GPU", u.F_SM, 4, y, u.MUTED)
            u.rtext("--", u.F_SM, 124, y, u.LINE)

        up = int(h.get("uptime", 0))
        d = up // 86400
        hh = (up % 86400) // 3600
        mm = (up % 3600) // 60
        u.ctext("UPTIME %dd %dh %dm" % (d, hh, mm), u.F_TINY, 104, u.MUTED)
        u.dots(len(app.pages), app.idx)


class ServersPage(BasePage):
    title = "SERVERS"
    PER = 4
    CYCLE_MS = 5000

    def __init__(self):
        self.at = 0
        self.cyc = 0
        self.slot = 0

    def enter(self, app):
        self.slot = 0
        self.at = 0
        self.cyc = time.ticks_ms()
        app.net.maybe_hub(force=True)

    def action(self, app):
        # double click: jump to the next services page
        self._next(app)
        self.cyc = time.ticks_ms()
        app.net.maybe_hub(force=True)

    def tick(self, app, now):
        redraw = False
        if time.ticks_diff(now, self.at) > 1000:
            self.at = now
            app.net.maybe_hub()
            redraw = True
        if time.ticks_diff(now, self.cyc) >= self.CYCLE_MS:
            self.cyc = now
            self._next(app)
            redraw = True
        return redraw

    def _npages(self, app):
        n = len(app.net.services or [])
        if n == 0:
            return 1
        return (n + self.PER - 1) // self.PER

    def _next(self, app):
        self.slot = (self.slot + 1) % self._npages(app)

    def draw(self, app):
        u.clear()
        net = app.net
        svcs = net.services or []
        npages = self._npages(app)
        self.slot %= npages
        head = "%s %d/%d" % (self.title, self.slot + 1, npages)
        u.header(head, app.idx, len(app.pages), u.RED if net.alert else u.ACCENT)

        if not svcs:
            if not net.connected:
                msg = "NO WIFI"
            elif not net.hub_host:
                msg = "HUB NOT SET"
            elif not net.hub:
                msg = (net.hub_err or "HUB OFFLINE")[:20]
            else:
                msg = "NO SERVICES"
            u.ctext(msg, u.F_SM, 58, u.MUTED)
            u.dots(len(app.pages), app.idx)
            return

        chunk = svcs[self.slot * self.PER:(self.slot + 1) * self.PER]
        y = 28
        for s in chunk:
            up = bool(s.get("up"))
            crit = bool(s.get("critical"))
            kind = s.get("kind") or ""
            u.LCD.fillRect(5, y + 3, 5, 5, u.GREEN if up else u.RED)
            u.text((s.get("name") or "?")[:11], u.F_SM, 14, y,
                   u.WHITE if up else u.MUTED)

            if kind == "mc" and up and s.get("max"):
                u.rtext("%d/%d" % (int(s.get("players", 0)), int(s.get("max", 0))),
                        u.F_SM, 123, y, u.GREEN)
            elif up:
                ms = int(s.get("ms", 0))
                if ms < 1000:
                    txt = "UP %dms" % ms
                else:
                    txt = "UP %d.%ds" % (ms // 1000, (ms % 1000) // 100)
                u.rtext(txt, u.F_TINY, 123, y + 2,
                        u.GREEN if ms < 300 else u.AMBER)
            else:
                u.rtext("DOWN", u.F_TINY, 123, y + 2, u.RED)

            sub = s.get("note") or ""
            if not sub:
                port = int(s.get("port", 0) or 0)
                sub = str(port) if port else (kind.upper() or "")
            if crit:
                sub = (sub + " CRIT").strip()
            u.text(sub, u.F_TINY, 14, y + 13,
                   (u.ORANGE if up else u.RED) if crit else
                   (u.MUTED if up else u.RED))
            y += 22

        if len(chunk) < self.PER:
            total = len(svcs)
            ups = 0
            for s in svcs:
                if s.get("up"):
                    ups += 1
            u.ctext("%d/%d UP" % (ups, total), u.F_SM, 74,
                    u.GREEN if ups == total else u.AMBER)
            if net.alert:
                ids = ",".join((net.alert_ids or [])[:3])
                u.ctext(("ALERT " + ids)[:22], u.F_TINY, 92, u.RED)
        u.dots(len(app.pages), app.idx)


class MascotPage(BasePage):
    """HelloATOMS3 character: breathes, blinks and reacts to the IMU.

    The animation is advanced from tick() instead of a Timer callback:
    a 100 ms timer was what crashed UIFlow on interrupt in the original.
    """

    title = "FACE"
    BREATH_CYCLE = 30

    def __init__(self):
        self.at = 0
        self.breath_T = 0
        self.dev = 0
        self.tm_blink = 0
        self.st_blink = 0
        self.open_ms = 3000

    def enter(self, app):
        self.at = 0

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) < 100:
            return False
        self.at = now
        self.breath_T = (self.breath_T + 1) % self.BREATH_CYCLE
        self.dev = int(4 * math.sin(
            self.breath_T * 2 * math.pi / self.BREATH_CYCLE))
        self.tm_blink += 100
        if self.st_blink == 0:
            if self.tm_blink >= 300:
                self.st_blink = 1
                self.tm_blink = 0
                self.open_ms = random.randint(2, 6) * 1000
        elif self.tm_blink >= self.open_ms:
            self.st_blink = 0
            self.tm_blink = 0
        return True

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages),
                 u.RED if app.net.alert else u.ACCENT)
        try:
            ax, ay, az = M5.Imu.getAccel()
        except Exception:
            ax, ay, az = 0.0, 0.0, 1.0
        if ax > 0.35:
            mood, label, color = "happy", "HAPPY", u.AMBER
        elif ax < -0.35:
            mood, label, color = "sleep", "SLEEPY", u.PURPLE
        else:
            mood, label, color = "normal", "CHILL", u.ACCENT
        if mood != "sleep" and self.st_blink == 0:
            mood = "blink"
        if app.net.alert:
            label, color = "ALERT", u.RED
        u.atom_face(mood, self.dev, app.net.alert)
        u.ctext(label, u.F_TINY, 107, color)
        u.dots(len(app.pages), app.idx)


class StatusPage(BasePage):
    title = "STATUS"

    def __init__(self):
        self.at = 0

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) > 2000:
            self.at = now
            return True
        return False

    def draw(self, app):
        u.clear()
        net = app.net
        u.header(self.title, app.idx, len(app.pages),
                 u.RED if net.alert else u.ACCENT)
        ip = "-"
        rssi = 0
        if net.connected:
            try:
                ip = net.wlan.ifconfig()[0]
            except Exception:
                ip = "?"
            try:
                rssi = net.wlan.status("rssi")
            except Exception:
                rssi = 0
        gc.collect()
        free = gc.mem_free() // 1024
        up = time.ticks_diff(time.ticks_ms(), app.started) // 1000

        light = getattr(app, "light", 0)
        try:
            bright = app.levels[app.lvl]
        except Exception:
            bright = 0
        auto = getattr(app, "auto", None)
        if auto is not None and not auto.on:
            light_txt = "OFF"
        else:
            light_txt = "%d%%  B%d" % (light, bright)
        rows = [
            ("IP", ip),
            ("RSSI", "%d dBm" % rssi),
            ("HEAP", "%d KB" % free),
            ("UPTIME", "%dd %02dh %02dm" % (up // 86400, (up % 86400) // 3600, (up % 3600) // 60)),
            ("LIGHT", light_txt),
        ]
        y = 26
        for name, val in rows:
            u.text(name, u.F_TINY, 6, y, u.MUTED)
            u.rtext(val, u.F_SM, 122, y - 1, u.WHITE)
            y += 14

        hub = "OK" if net.hub else ("SET" if net.hub_host else "NONE")
        hc = u.GREEN if net.hub else u.MUTED
        u.text("HUB", u.F_TINY, 6, 96, u.MUTED)
        u.rtext(hub, u.F_SM, 122, 95, hc)
        if net.alert:
            ids = ",".join((net.alert_ids or [])[:3])
            u.text(("ALERT " + ids)[:22], u.F_TINY, 6, 110, u.RED)
        else:
            err = net.hub_err or net.weather_err
            if err:
                u.text(err[:22], u.F_TINY, 6, 110, u.LINE)
        u.dots(len(app.pages), app.idx)
def mmss(ms):
    s = int(ms or 0) // 1000
    return "%d:%02d" % (s // 60, s % 60)


class SpotifyPage(BasePage):
    """Now-playing page fed by the hub's /spotify_status endpoint."""

    title = "SPOTIFY"
    REFRESH_MS = 200
    SCROLL_MS = 55
    GAP = 22

    def __init__(self):
        self.at = -1000000
        self.data = None
        self.err = ""
        self.scroll_at = 0
        self.scroll_pos = 0

    def enter(self, app):
        self.at = time.ticks_ms() - self.REFRESH_MS
        self.scroll_at = time.ticks_ms()
        self.scroll_pos = 0
        self.refresh(app)

    def action(self, app):
        self.refresh(app, force=True)

    def refresh(self, app, force=False):
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.at) < self.REFRESH_MS:
            return False
        self.at = now
        d = app._http_get("/spotify_status")
        if not d:
            self.err = (app.http_err or "HUB OFFLINE")[:22]
            self.data = None
            return True
        st = d.get("status")
        if st in ("success", "not_playing", "paused"):
            prev = self.data or {}
            self.data = d
            self.err = ""
            if (d.get("song"), d.get("artist"), d.get("device")) != (
                    prev.get("song"), prev.get("artist"), prev.get("device")):
                self.scroll_pos = 0
                self.scroll_at = now
        else:
            self.err = (d.get("message") or st or "ERROR")[:22]
            self.data = None
        return True

    def tick(self, app, now):
        redraw = self.refresh(app)
        if self._animate(now):
            redraw = True
        return redraw

    def _long(self, s, font):
        return u.tw(s, font) > u.W - 12

    def _animating(self):
        d = self.data
        if not d:
            return False
        return (self._long(d.get("song") or "-", u.F_SM) or
                self._long(d.get("artist") or "-", u.F_TINY) or
                self._long("ON " + (d.get("device") or "-"), u.F_TINY))

    def _animate(self, now):
        if time.ticks_diff(now, self.scroll_at) < self.SCROLL_MS:
            return False
        self.scroll_at = now
        if not self._animating():
            self.scroll_pos = 0
            return False
        self.scroll_pos += 1
        return True

    def _scroll(self, s, font, y, color):
        """Draw left-aligned, sliding left like a VFD when it does not fit."""
        w = u.tw(s, font)
        if w <= u.W - 12:
            u.text(s, font, (u.W - w) // 2, y, color)
            return
        x = 6 - (self.scroll_pos % (w + self.GAP))
        u.text(s, font, x, y, color)

    def draw(self, app):
        u.clear()
        d = self.data
        playing = bool(d and d.get("is_playing"))
        u.header(self.title, app.idx, len(app.pages),
                 u.GREEN if playing else u.ACCENT)
        if self.err:
            u.ctext("SPOTIFY", u.F_SM, 34, u.MUTED)
            u.ctext(self.err, u.F_TINY, 54, u.RED)
            u.ctext("hub on port 8765?", u.F_TINY, 72, u.LINE)
            u.ctext("hold 3s: back", u.F_TINY, 104, u.MUTED)
            u.dots(len(app.pages), app.idx)
            return
        if not d:
            u.ctext("LOADING...", u.F_SM, 58, u.MUTED)
            u.dots(len(app.pages), app.idx)
            return

        self._scroll(d.get("song") or "-", u.F_SM, 30, u.WHITE)
        self._scroll(d.get("artist") or "-", u.F_TINY, 48, u.MUTED)
        dev = d.get("device") or "-"
        if self._long("ON " + dev, u.F_TINY):
            self._scroll("ON " + dev, u.F_TINY, 64, u.ACCENT)
        else:
            u.text("ON", u.F_TINY, 6, 64, u.LINE)
            u.text(dev[:16], u.F_TINY, 24, 64, u.ACCENT)

        dur = int(d.get("duration_ms") or 0)
        pos = int(d.get("progress_ms") or 0)
        pct = (pos * 100 // dur) if dur else 0
        u.bar(6, 82, 116, 5, pct, u.GREEN if playing else u.LINE)
        u.text(mmss(pos), u.F_TINY, 6, 90, u.MUTED)
        u.rtext(mmss(dur), u.F_TINY, 122, 90, u.MUTED)
        u.ctext("PLAYING" if playing else "PAUSED", u.F_TINY, 104,
                u.GREEN if playing else u.AMBER)
        u.dots(len(app.pages), app.idx)
