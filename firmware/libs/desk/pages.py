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


def kind_color(kind):
    if kind == "mc_join":
        return u.GREEN
    if kind == "mc_leave":
        return u.RED
    if kind == "mc_move":
        return u.AMBER
    return u.ACCENT


def age_text(sec):
    if sec < 60:
        return "NOW"
    if sec < 3600:
        return "%dm" % (sec // 60)
    if sec < 86400:
        return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)
    return "%dd%02dh" % (sec // 86400, (sec % 86400) // 3600)


def countdown_text(sec):
    if sec <= 0:
        return "NOW"
    if sec < 60:
        return "IN <1m"
    if sec < 3600:
        return "IN %dm" % (sec // 60)
    if sec < 86400:
        return "IN %dh%02dm" % (sec // 3600, (sec % 3600) // 60)
    return "IN %dd %dh" % (sec // 86400, (sec % 86400) // 3600)


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
        app.net.maybe_calendar()

    def action(self, app):
        app.net.maybe_weather(force=True)
        app.net.maybe_calendar(force=True)

    def tick(self, app, now):
        s = app.local()[5]
        if s != self.sec:
            self.sec = s
            if s % 60 == 0:
                app.net.maybe_weather()
            if s % 300 == 0:
                app.net.maybe_calendar()
            return True
        return False

    def _cal_line(self, app):
        c = app.net.calendar
        if not c or c.get("status") != "ok" or c.get("none"):
            return ""
        title = (c.get("title") or "EVENT")[:30]
        start = int(c.get("start") or 0)
        end = int(c.get("end") or 0)
        now = app.now()
        if start and start > now:
            return countdown_text(start - now) + " " + title
        if start and end > now:
            return "NOW " + title
        return ""

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
        cal = self._cal_line(app)
        if cal:
            u.text1(cal[:25], u.F_TINY, 4, 109, u.ACCENT2)
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
            app.net.maybe_hub_live(now)
            return True
        return False

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages))
        h = app.net.state.get("pc") or app.net.hub
        if not h:
            if not app.net.connected:
                msg = "NO WIFI"
            elif not app.net.hub_host:
                msg = "HUB NOT SET"
            else:
                msg = (app.net.state_err or app.net.hub_err
                       or "HUB OFFLINE")
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
        if app.net.state_err or app.net.hub_err:
            # Keep the last values on screen but make it obvious they are old.
            age = app.net.hub_age()
            u.ctext(("OFFLINE %s" % age_text(age))[:21], u.F_TINY, 104, u.RED)
        else:
            u.ctext("UPTIME %dd %dh %dm" % (d, hh, mm), u.F_TINY, 104, u.MUTED)
        u.dots(len(app.pages), app.idx)


class ServersPage(BasePage):
    title = "SERVERS"
    PER = 4

    def __init__(self):
        self.at = 0
        self.slot = 0

    def enter(self, app):
        self.slot = 0
        self.at = 0
        app.net.maybe_hub(force=True)

    def action(self, app):
        # double click: jump to the next services page
        self._next(app)
        app.net.maybe_hub(force=True)

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) > 1000:
            self.at = now
            app.net.maybe_hub_live(now)
            return True
        return False

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
        stale = bool(net.state_err or net.hub_err)
        hc = u.RED if net.alert else (u.AMBER if stale else u.ACCENT)
        u.header(head, app.idx, len(app.pages), hc)

        if not svcs:
            if not net.connected:
                msg = "NO WIFI"
            elif not net.hub_host:
                msg = "HUB NOT SET"
            elif not net.state.get("svc"):
                msg = (net.state_err or net.hub_err or "HUB OFFLINE")[:20]
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
        if stale:
            u.ctext(("STALE %s" % age_text(net.hub_age()))[:20],
                    u.F_TINY, 108, u.AMBER)
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


class NoticePage(BasePage):
    """Recent hub notifications. Double click clears the local list."""

    title = "NOTICES"

    def __init__(self):
        self.at = 0

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) > 1000:
            self.at = now
            app.net.maybe_events()
            app._tick_notif(now)
            return True
        return False

    def action(self, app):
        app.notif = None
        app.notif_queue = []
        app.notif_log = []
        self.draw(app)

    def draw(self, app):
        u.clear()
        u.header(self.title, app.idx, len(app.pages), u.ACCENT2)
        log = app.notif_log or []
        if not log:
            u.ctext("NO NOTICES", u.F_SM, 52, u.MUTED)
            u.ctext("double click clears", u.F_TINY, 72, u.LINE)
            u.dots(len(app.pages), app.idx)
            return
        y = 25
        for ev in log[:3]:
            kind = ev.get("kind") or ""
            u.LCD.fillRect(4, y + 3, 4, 4, kind_color(kind))
            title = (ev.get("title") or "?").replace("\n", " ")
            u.text1(title[:15], u.F_SM, 12, y, u.WHITE)
            body = (ev.get("body") or kind.upper()).replace("\n", " ")
            u.text1(body[:25], u.F_TINY, 12, y + 12, u.MUTED)
            y += 24
        if len(log) > 3:
            u.text("+%d MORE" % (len(log) - 3), u.F_TINY, 6, 99, u.MUTED)
        u.ctext("2x CLICK CLEAR", u.F_TINY, 110, u.LINE)
        u.dots(len(app.pages), app.idx)


class StatusPage(BasePage):
    title = "STATUS"

    def __init__(self):
        self.at = 0

    def enter(self, app):
        self.at = 0
        app.net.maybe_hub(force=True)

    def tick(self, app, now):
        if time.ticks_diff(now, self.at) > 1000:
            self.at = now
            app.net.maybe_hub_live(now)
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
        y = 23
        for name, val in rows:
            u.text(name, u.F_TINY, 6, y, u.MUTED)
            u.rtext(val, u.F_SM, 122, y - 1, u.WHITE)
            y += 12

        uc = net.uptime or {}
        hist = uc.get("hist") or []
        total = int(uc.get("total") or len(net.services) or 1)
        down = int(uc.get("down") or 0)
        last = int(uc.get("last_down") or 0)
        upto = total - down
        u.text("SVC", u.F_TINY, 6, 86, u.MUTED)
        sc = u.GREEN if down == 0 else (u.AMBER if upto > total // 2 else u.RED)
        u.sparkline(30, 85, 92, 13, hist or [upto], total, sc)
        if last:
            ago = max(0, app.now() - last)
            u.text(("DOWN %s AGO" % age_text(ago))[:21], u.F_TINY, 6, 100,
                   u.RED if down else u.AMBER)
        else:
            u.text("NO DOWNTIME", u.F_TINY, 6, 100, u.GREEN)

        stale = bool(net.state_err or net.hub_err)
        if net.state and (stale or not net.state_fresh(10000)):
            hub, hc = "STALE", u.AMBER
        elif net.state:
            hub, hc = "OK", u.GREEN
        elif net.hub_host:
            hub, hc = "OFFLINE", u.RED
        else:
            hub, hc = "NONE", u.MUTED
        u.text("HUB", u.F_TINY, 6, 110, u.MUTED)
        u.rtext(hub, u.F_TINY, 122, 110, hc)
        u.dots(len(app.pages), app.idx)
def mmss(ms):
    s = int(ms or 0) // 1000
    return "%d:%02d" % (s // 60, s % 60)


class SpotifyPage(BasePage):
    """Now-playing page fed by the hub's /spotify_status endpoint."""

    title = "SPOTIFY"
    REFRESH_MS = 200
    FRAME_MS = 33
    LYRIC_FRAME_MS = 15    # fast lyric scrolling frame rate
    LYRIC_STEP = 4         # base: 4 px per frame = ~266 px/s
    LYRIC_STEP_MAX = 8     # only for long lines with a short time slot
    LYRIC_HOLD_MS = 300    # short pause at each end of a lyric line
    HOLD_MS = 1000
    LYRICS_MS = 200        # match the line quickly; no lyric lag

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
        self.lyr_key = None
        self.rows = []
        self.row_y = 22
        self.row_dy = 25
        self.next_y = 73
        self.lyr_font = u.F_JA
        self.lyr_scroll = False
        self.net = None

    def enter(self, app):
        now = time.ticks_ms()
        self.net = app.net
        self.at = now - self.REFRESH_MS
        self.frame_at = now
        self.pos = 0
        self.dir = 1
        self.hold_until = time.ticks_add(now, self.HOLD_MS)
        self.sig = None
        self.dirty_full = True
        app.net.maybe_hub(force=True)
        self.refresh(app)

    def soft_hold(self, app):
        """~1 s hold toggles the lyrics overlay."""
        self.lyrics = not self.lyrics
        self.lyr = None
        self.lyr_line = None
        self.lyr_key = None
        self.rows = []
        self.lyr_at = time.ticks_ms() - 1000000
        self.pos = 0
        self.dir = 1
        self.long = False
        self.dirty_full = True
        return True

    def action(self, app):
        app.net.maybe_hub(force=True)
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
        """Read the hub-pushed snapshot.  No per-page HTTP requests."""
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.at) < self.gap:
            return self._dirty()
        self.at = now
        net = app.net
        fresh = net.state_fresh(8000)
        self.gap = self.REFRESH_MS if fresh else 3000
        sp = net.state.get("sp")
        if not sp or not fresh:
            # The push stream may still be connecting; ask for one snapshot.
            net.maybe_hub(force=True)
            sp = net.state.get("sp")
            if not net.state_fresh(10000):
                self._fail(net.state_err or net.hub_err or "HUB OFFLINE")
                return self._dirty()
            self.gap = self.REFRESH_MS
        if not sp:
            self._fail(net.state_err or net.hub_err or "HUB OFFLINE")
            return self._dirty()
        st = sp.get("status")
        if st not in ("success", "not_playing", "paused"):
            self._fail(sp.get("message") or st or "ERROR")
            return self._dirty()
        sig = (sp.get("song"), sp.get("artist"), sp.get("device"),
               bool(sp.get("is_playing")))
        prev = self.sig
        self.data = sp
        self.err = ""
        self.sig = sig
        if not self.lyrics:
            self._plan(sp)
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
        if self.lyrics:
            self._sync_lyrics(net, now)
        return self._dirty()

    def _pos(self):
        """Progress owned by the hub, interpolated locally between updates."""
        if self.net is not None:
            return self.net.sp_progress()
        return 0

    def _sync_lyrics(self, net, now):
        """Current + next line come straight from the hub state."""
        ly = net.state.get("ly")
        if not ly:
            return
        self.lyr = ly
        line = ""
        if ly.get("status") == "success":
            line = ly.get("line") or ""
        key = (ly.get("status"), line)
        if key != self.lyr_key:
            self.lyr_key = key
            self.lyr_line = line
            self._plan_lyrics(now)
            self.dirty_lines = True
        elif self.long:
            # scrolling keeps repainting; static text stays put (no flicker)
            self.dirty_lines = True

    def _plan_lyrics(self, now):
        """Lay out a lyric line so the whole line can be read in one slot.

        Most lyrics are Latin text, so the first choice is a small font that
        word-wraps the full line into at most four static rows.  Only CJK or
        extremely long lines have to be split in half and scrolled.
        """
        s = (self.lyr_line or "").strip()
        limit = u.W - 12
        rows = None
        font = u.F_JA
        dy = 25
        y = 22
        scroll = False

        if not s:
            rows = [""]
            y = 33
        elif u.tw(s, u.F_JA) <= limit:
            # short line: keep the big font, one centered row
            rows = [s]
            y = 33
        elif self._is_latin(s):
            # Latin: wrap in a smaller font, whole line visible, no scrolling
            rows = self._wrap(s, u.F_SM, 4)
            if rows:
                font = u.F_SM
                dy = 13
                y = 21
            else:
                rows = self._wrap(s, u.F_TINY, 4)
                if rows:
                    font = u.F_TINY
                    dy = 12
                    y = 20

        if not rows:
            # CJK or a line too long even for the tiny font: split in half
            # and scroll the two halves together
            rows = self._split_two(s, u.F_JA)
            font = u.F_JA
            dy = 25
            y = 22
            scroll = True
        elif len(rows) == 1:
            # single row: center it under the LYRICS label
            y = 33

        self.rows = rows
        self.lyr_font = font
        self.lyr_scroll = scroll
        self.row_y = y
        self.row_dy = dy
        # leave room for the next line under whatever rows we used
        if not scroll and len(rows) == 1:
            self.next_y = 64
        else:
            self.next_y = y + dy * len(rows)
            if self.next_y < 64:
                self.next_y = 64
            if self.next_y > 74:
                self.next_y = 74

        w = 0
        if scroll:
            for row in rows:
                rw = u.tw(row, font)
                if rw > w:
                    w = rw
        self.max_off = w - limit
        if self.max_off < 0:
            self.max_off = 0
        self.long = self.max_off > 0
        self.pos = 0
        self.dir = 1
        self.frame_at = now
        self.hold_until = time.ticks_add(now, self.LYRIC_HOLD_MS)
        self.step = self._lyric_step()

    def _is_latin(self, s):
        """True when every character is Latin/Cyrillic/Greek-ish."""
        for ch in s:
            if ord(ch) > 0x2E7F:
                return False
        return True

    def _wrap(self, s, font, max_rows):
        """Greedy word-wrap into at most max_rows rows, or None if too long."""
        limit = u.W - 12
        words = s.split()
        if not words:
            return None
        rows = []
        cur = ""
        for word in words:
            # a single word wider than one row: hard-split it by width
            while u.tw(word, font) > limit:
                lo, hi = 1, len(word)
                while lo < hi:
                    m = (lo + hi + 1) // 2
                    if u.tw(word[:m], font) <= limit:
                        lo = m
                    else:
                        hi = m - 1
                if cur:
                    rows.append(cur)
                    cur = ""
                rows.append(word[:lo])
                word = word[lo:]
                if len(rows) > max_rows:
                    return None
            if not word:
                continue
            cand = (cur + " " + word) if cur else word
            if u.tw(cand, font) <= limit:
                cur = cand
            else:
                if cur:
                    rows.append(cur)
                cur = word
                if len(rows) >= max_rows and u.tw(cur, font) > limit:
                    return None
            if len(rows) > max_rows:
                return None
        if cur:
            rows.append(cur)
        if len(rows) > max_rows:
            return None
        return rows

    def _split_two(self, s, font):
        """Split a line at its width midpoint into two balanced halves."""
        total = u.tw(s, font)
        if total <= u.W - 12:
            return [s]
        target = total // 2
        lo, hi = 1, len(s) - 1
        best = max(1, len(s) // 2)
        while lo <= hi:
            m = (lo + hi) // 2
            if u.tw(s[:m], font) < target:
                lo = m + 1
            else:
                best = m
                hi = m - 1
        p1 = s[:best].strip()
        p2 = s[best:].strip()
        if not p1:
            return [s]
        if not p2:
            return [p1]
        return [p1, p2]

    def _lyric_step(self):
        """Scroll just fast enough to reach the end before the next line."""
        if not self.long:
            return 1
        step = self.LYRIC_STEP
        na = (self.lyr or {}).get("next_at")
        if na:
            remain = int(na) - self._pos() - 2 * self.LYRIC_HOLD_MS
            if remain < 200:
                remain = 200
            frames = remain // self.LYRIC_FRAME_MS
            if frames < 1:
                frames = 1
            need = self.max_off // frames + 1
            if need > step:
                step = need
        if step > self.LYRIC_STEP_MAX:
            step = self.LYRIC_STEP_MAX
        return step

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
        hold = self.LYRIC_HOLD_MS if self.lyrics else self.HOLD_MS
        moved = False
        if time.ticks_diff(now, self.hold_until) >= 0:
            self.pos += self.dir * self.step
            if self.pos >= self.max_off:
                self.pos = self.max_off
                self.dir = -1
                self.hold_until = time.ticks_add(now, hold)
            elif self.pos <= 0:
                self.pos = 0
                self.dir = 1
                self.hold_until = time.ticks_add(now, hold)
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

    def _scroll(self, s, font, y, color, center=True):
        """One clipped line: slide sideways, then back from the other side."""
        w = u.tw(s, font)
        if w <= u.W - 12:
            x = (u.W - w) // 2 if center else 6
            u.text1(s, font, x, y, color)
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
        """Current lyric line (wrapped or scrolled) plus the next line."""
        u.fill(0, self.BAND_Y, u.W, self.BAND_H, u.BLACK)
        d = self.lyr or {}
        st = d.get("status")
        if st == "success":
            rows = self.rows or [""]
            nxt = (d.get("next") or "").strip()
            if not self.lyr_scroll and len(rows) <= 1:
                u.text("LYRICS", u.F_TINY, 6, 22, u.LINE)
            y = self.row_y
            center = len(rows) == 1
            if not rows[0]:
                u.ctext("...", u.F_JA, y, u.LINE)
            else:
                for row in rows:
                    if self.lyr_scroll:
                        self._scroll(row, self.lyr_font, y, u.WHITE, False)
                    elif center:
                        self._scroll(row, self.lyr_font, y, u.WHITE, True)
                    else:
                        u.text1(row, self.lyr_font, 6, y, u.WHITE)
                    y += self.row_dy
            if nxt:
                if u.tw(nxt, u.F_TINY) <= u.W - 12:
                    u.ctext(nxt, u.F_TINY, self.next_y, u.ACCENT)
                else:
                    u.text1(nxt, u.F_TINY, 6, self.next_y, u.ACCENT)
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
        pos = self._pos()
        pct = (pos * 100 // dur) if dur else 0
        u.bar(6, 85, 116, 5, pct, u.GREEN if playing else u.LINE)
        u.text(mmss(pos), u.F_TINY, 6, 92, u.MUTED)
        u.rtext(mmss(dur), u.F_TINY, 122, 92, u.MUTED)
        u.ctext("PLAYING" if playing else "PAUSED", u.F_TINY, 104,
                u.GREEN if playing else u.AMBER)
