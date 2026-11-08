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
    animating = False

    def enter(self, app):
        pass

    def action(self, app):
        pass

    def soft_hold(self, app):
        """~1 s hold on the button.  Return True to swallow the click."""
        return False

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


class RestartPage(BasePage):
    """Restart the Spotify desktop app on the hub PC (double click).

    Spotify occasionally hangs with a WinError and has to be closed with
    Alt+F4 and started again by hand.  This page does that remotely.
    """

    title = "RESTART"

    def __init__(self):
        self.state = "idle"
        self.msg = ""
        self.at = 0
        self.sent = 0
        self.dots = 0

    def enter(self, app):
        self.state = "idle"
        self.msg = ""
        self.at = 0

    def action(self, app):
        now = time.ticks_ms()
        d = app._http_get("/spotify_restart")
        if d and d.get("status") in ("queued", "success", "triggered"):
            self.state = "busy"
            self.msg = (d.get("message") or "restarting")[:20]
            self.sent = now
        else:
            self.state = "error"
            self.msg = (app.http_err or "HUB OFFLINE")[:18]
            self.sent = now
        self.at = time.ticks_add(now, -5000)
        self.draw(app)

    def tick(self, app, now):
        if self.state == "busy":
            if time.ticks_diff(now, self.sent) > 10000:
                self.state = "done"
                return True
            if time.ticks_diff(now, self.at) > 400:
                self.at = now
                self.dots = (self.dots + 1) % 4
                return True
        elif self.state == "error" and time.ticks_diff(now, self.sent) > 5000:
            self.state = "idle"
            return True
        return False

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages), u.PURPLE)
        c = u.GREEN if self.state == "busy" else (
            u.RED if self.state == "error" else u.ACCENT2)
        u.LCD.fillCircle(64, 46, 19, c)
        u.LCD.fillTriangle(58, 35, 58, 57, 76, 46, u.BLACK)
        u.ctext("SPOTIFY", u.F_SM, 70, u.WHITE)
        if self.state == "busy":
            u.ctext("RESTARTING" + "." * self.dots, u.F_TINY, 90, u.AMBER)
            u.ctext("on the hub PC", u.F_TINY, 104, u.LINE)
        elif self.state == "done":
            u.ctext("DONE", u.F_SM, 90, u.GREEN)
            u.ctext("check spotify", u.F_TINY, 106, u.MUTED)
        elif self.state == "error":
            u.ctext(self.msg, u.F_TINY, 90, u.RED)
            u.ctext("try again", u.F_TINY, 104, u.LINE)
        else:
            u.ctext("DOUBLE CLICK", u.F_TINY, 88, u.MUTED)
            u.ctext("to restart", u.F_TINY, 102, u.LINE)
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
    FRAME_MS = 33
    LYRIC_FRAME_MS = 20    # lyrics scroll a bit faster than song/artist
    LYRIC_STEP = 2         # px per frame while a long lyric line scrolls
    HOLD_MS = 1000
    LYRICS_MS = 900

    # Regions that are repainted independently (no full-screen flash).
    BAND_Y, BAND_H = 21, 63          # song + artist + device / lyrics
    PROG_Y, PROG_H = 84, 34          # progress bar, times, play state

    def __init__(self):
        self.at = -1000000
        self.gap = self.REFRESH_MS
        self.data = None
        self.err = ""
        self.sig = None
        self.frame_at = 0
        self.pos = 0
        self.dir = 1
        self.hold_until = 0
        self.max_off = 0
        self.long = False
        self.step = 1
        self.dirty_full = True
        self.dirty_lines = False
        self.dirty_prog = False
        # lyrics overlay: off by default, ~1 s hold toggles it
        self.lyrics = False
        self.lyr = None
        self.lyr_at = -1000000
        self.lyr_line = None

    def enter(self, app):
        now = time.ticks_ms()
        self.at = now - self.REFRESH_MS
        self.frame_at = now
        self.pos = 0
        self.dir = 1
        self.hold_until = time.ticks_add(now, self.HOLD_MS)
        self.sig = None
        self.dirty_full = True
        self.refresh(app)

    def soft_hold(self, app):
        """~1 s hold toggles the lyrics overlay."""
        self.lyrics = not self.lyrics
        self.lyr = None
        self.lyr_line = None
        self.lyr_at = time.ticks_ms() - 1000000
        self.pos = 0
        self.dir = 1
        self.long = False
        self.dirty_full = True
        return True

    def action(self, app):
        self.refresh(app, force=True)

    def _fail(self, msg):
        """Switch to the error screen; only touch the screen if it changed."""
        msg = msg[:22]
        if msg != self.err or self.data is not None:
            self.err = msg
            self.data = None
            self.long = False
            self.animating = False
            self.sig = None
            self.dirty_full = True

    def _dirty(self):
        return self.dirty_full or self.dirty_lines or self.dirty_prog

    def refresh(self, app, force=False):
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.at) < self.gap:
            return self._dirty()
        self.at = now
        t0 = now
        d = app._http_get("/spotify_status")
        # A hub on the internet answers in tens of milliseconds; poll less
        # often then so the marquee keeps its frame rate.
        took = time.ticks_diff(time.ticks_ms(), t0)
        self.gap = self.REFRESH_MS if took < 60 else 500
        if not d:
            self._fail(app.http_err or "HUB OFFLINE")
            return self._dirty()
        st = d.get("status")
        if st not in ("success", "not_playing", "paused"):
            self._fail(d.get("message") or st or "ERROR")
            return self._dirty()
        sig = (d.get("song"), d.get("artist"), d.get("device"),
               bool(d.get("is_playing")))
        prev = self.sig
        self.data = d
        self.err = ""
        self.sig = sig
        if not self.lyrics:
            self._plan(d)
        if sig != prev:
            # new track / device / play state -> one clean full repaint
            self.pos = 0
            self.dir = 1
            self.frame_at = now
            self.hold_until = time.ticks_add(now, self.HOLD_MS)
            self.dirty_full = True
        else:
            # same track: only the small progress region changes
            self.dirty_prog = True
        if (self.lyrics
                and time.ticks_diff(now, self.lyr_at) >= self.LYRICS_MS):
            self._fetch_lyrics(app, now)
        return self._dirty()

    def _fetch_lyrics(self, app, now):
        self.lyr_at = now
        pos = int((self.data or {}).get("progress_ms") or 0)
        d = app._http_get("/lyrics?pos=%d" % pos)
        if d:
            self.lyr = d
        else:
            self.lyr = {"status": "error",
                        "message": app.http_err or "NO HUB"}
        line = ""
        if self.lyr.get("status") == "success":
            line = self.lyr.get("line") or ""
        if line != self.lyr_line:
            self.lyr_line = line
            self.pos = 0
            self.dir = 1
            self.frame_at = now
            self.hold_until = time.ticks_add(now, self.HOLD_MS)
            w = u.tw(line, u.F_JA)
            self.max_off = (w - (u.W - 12)) if w > u.W - 12 else 0
            self.long = self.max_off > 0
            self.step = self.LYRIC_STEP if self.long else 1
        self.dirty_lines = True

    def tick(self, app, now):
        redraw = self.refresh(app)
        if not self.long:
            self.animating = False
            return redraw
        self.animating = True
        frame = self.LYRIC_FRAME_MS if self.lyrics else self.FRAME_MS
        if time.ticks_diff(now, self.frame_at) < frame:
            return redraw
        self.frame_at = now
        moved = False
        if time.ticks_diff(now, self.hold_until) >= 0:
            self.pos += self.dir * self.step
            if self.pos >= self.max_off:
                self.pos = self.max_off
                self.dir = -1
                self.hold_until = time.ticks_add(now, self.HOLD_MS)
            elif self.pos <= 0:
                self.pos = 0
                self.dir = 1
                self.hold_until = time.ticks_add(now, self.HOLD_MS)
            moved = True
        if moved:
            self.dirty_lines = True
        return redraw or moved

    def _long(self, s, font):
        return u.tw(s, font) > u.W - 12

    def _plan(self, d):
        self.max_off = 0
        self.step = 1
        song, artist, dev = self._lines(d)
        for s, font in ((song, u.F_JA), (artist, u.F_JA), (dev, u.F_TINY)):
            w = u.tw(s, font)
            if w > u.W - 12 and w - (u.W - 12) > self.max_off:
                self.max_off = w - (u.W - 12)
        self.long = self.max_off > 0

    def _lines(self, d):
        return (d.get("song") or "-",
                d.get("artist") or "-",
                "ON " + (d.get("device") or "-"))

    def _scroll(self, s, font, y, color):
        """One clipped line: slide sideways, then back from the other side."""
        w = u.tw(s, font)
        if w <= u.W - 12:
            u.text1(s, font, (u.W - w) // 2, y, color)
            return
        off = self.pos
        lim = w - (u.W - 12)
        if off > lim:
            off = lim
        u.text1(s, font, 6 - off, y, color)

    def draw(self, app):
        if self.dirty_full:
            self.dirty_full = False
            self.dirty_lines = False
            self.dirty_prog = False
            self._draw_full(app)
            return
        if self.dirty_lines:
            self.dirty_lines = False
            self._draw_lines()
        if self.dirty_prog:
            self.dirty_prog = False
            self._draw_prog()

    def _draw_full(self, app):
        u.clear()
        d = self.data
        playing = bool(d and d.get("is_playing"))
        u.header(self.title, app.idx, len(app.pages),
                 u.GREEN if playing else u.ACCENT)
        u.dots(len(app.pages), app.idx)
        if self.err:
            u.ctext("SPOTIFY", u.F_SM, 34, u.MUTED)
            u.ctext(self.err, u.F_TINY, 54, u.RED)
            u.ctext("hub on port 8765?", u.F_TINY, 72, u.LINE)
            u.ctext("hold 3s: back", u.F_TINY, 104, u.MUTED)
            return
        if not d:
            u.ctext("LOADING...", u.F_SM, 58, u.MUTED)
            return
        self._draw_lines()
        self._draw_prog()

    def _draw_lines(self):
        d = self.data
        if not d:
            return
        if self.lyrics:
            self._draw_lyrics()
            return
        u.fill(0, self.BAND_Y, u.W, self.BAND_H, u.BLACK)
        song, artist, dev = self._lines(d)
        self._scroll(song, u.F_JA, 22, u.WHITE)
        self._scroll(artist, u.F_JA, 47, u.MUTED)
        if self._long(dev, u.F_TINY):
            self._scroll(dev, u.F_TINY, 73, u.ACCENT)
        else:
            u.text("ON", u.F_TINY, 6, 73, u.LINE)
            u.text(dev[3:][:16], u.F_TINY, 24, 73, u.ACCENT)

    def _draw_lyrics(self):
        """Two lyric rows: current line, then the upcoming one."""
        u.fill(0, self.BAND_Y, u.W, self.BAND_H, u.BLACK)
        u.text("LYRICS", u.F_TINY, 6, 22, u.LINE)
        d = self.lyr or {}
        st = d.get("status")
        if st == "success":
            cur = (d.get("line") or "").strip()
            nxt = (d.get("next") or "").strip()
            if cur:
                self._scroll(cur, u.F_JA, 33, u.WHITE)
            else:
                u.ctext("...", u.F_JA, 33, u.LINE)
            if nxt:
                if u.tw(nxt, u.F_TINY) > u.W - 12:
                    u.text1(nxt, u.F_TINY, 6, 64, u.ACCENT)
                else:
                    u.ctext(nxt, u.F_TINY, 64, u.ACCENT)
        elif st in (None, "loading"):
            u.ctext("FETCHING...", u.F_SM, 42, u.MUTED)
        elif st == "not_found":
            u.ctext("NO LYRICS", u.F_SM, 38, u.MUTED)
            u.ctext("hold 1s to hide", u.F_TINY, 58, u.LINE)
        else:
            u.ctext("LYRICS ERROR", u.F_SM, 38, u.RED)
            u.ctext((d.get("message") or "")[:18], u.F_TINY, 58, u.MUTED)

    def _draw_prog(self):
        d = self.data
        if not d:
            return
        u.fill(0, self.PROG_Y, u.W, self.PROG_H, u.BLACK)
        playing = bool(d.get("is_playing"))
        dur = int(d.get("duration_ms") or 0)
        pos = int(d.get("progress_ms") or 0)
        pct = (pos * 100 // dur) if dur else 0
        u.bar(6, 85, 116, 5, pct, u.GREEN if playing else u.LINE)
        u.text(mmss(pos), u.F_TINY, 6, 92, u.MUTED)
        u.rtext(mmss(dur), u.F_TINY, 122, 92, u.MUTED)
        u.ctext("PLAYING" if playing else "PAUSED", u.F_TINY, 104,
                u.GREEN if playing else u.AMBER)
