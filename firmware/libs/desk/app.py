import json
import time

import M5

from desk import pages
from desk import ui as u
from desk import net as netmod
from desk.led import Alerter
from desk.light import AutoBrightness

HOLD_MS = 3000       # long press -> switch mode
SOFT_HOLD_MS = 1000  # short hold -> page action (Spotify lyrics toggle)
CLICK_GAP_MS = 400   # silence after last click before a gesture fires
NOTIF_POLL_MS = 15000  # how often to ask the hub for new notifications
NOTIF_SHOW_MS = 9000   # how long a banner stays before the next one shows
NOTIF_MAX_AGE_S = 300  # older events go to the log but do not pop up on boot


def _is_down(btn):
    """UIFlow2 renamed button methods a few times; try them all."""
    for name in ("isPressed", "is_pressed", "pressed"):
        fn = getattr(btn, name, None)
        if fn is None:
            continue
        try:
            return bool(fn())
        except Exception:
            continue
    try:
        return bool(btn.value())
    except Exception:
        return False


class Pomodoro:
    def __init__(self, cfg):
        p = cfg.get("pomodoro") or {}
        self.focus = int(p.get("focus", 25)) * 60000
        self.brk = int(p.get("break", 5)) * 60000
        self.mode = "focus"
        self.running = False
        self.rem = self.focus
        self.end = 0
        self.sessions = 1

    def toggle(self):
        if self.running:
            self.rem = time.ticks_diff(self.end, time.ticks_ms())
            if self.rem < 0:
                self.rem = 0
            self.running = False
        else:
            self.end = time.ticks_add(time.ticks_ms(), self.rem)
            self.running = True

    def reset(self):
        self.running = False
        self.mode = "focus"
        self.sessions = 1
        self.rem = self.focus

    def update(self, now):
        if not self.running:
            return False
        rem = time.ticks_diff(self.end, now)
        if rem <= 0:
            if self.mode == "focus":
                self.mode = "break"
                self.rem = self.brk
            else:
                self.mode = "focus"
                self.sessions += 1
                self.rem = self.focus
            self.end = time.ticks_add(now, self.rem)
            return True
        self.rem = rem
        return False


class App:
    def __init__(self, cfg, net):
        self.cfg = cfg
        self.net = net
        self.tz = int((cfg.get("location") or {}).get("tz", 9))

        # Two independent page sets.  mode 0 = Normal, mode 1 = Spotify.
        self.mode = 0
        self.norm_pages = [
            pages.ClockPage(),
            pages.WeatherPage(),
            pages.PomodoroPage(),
            pages.PcPage(),
            pages.ServersPage(),
            pages.NoticePage(),
            pages.RestartPage(),
            pages.MascotPage(),
            pages.StatusPage(),
        ]
        self.spot_page = pages.SpotifyPage()
        self.spot_pages = [self.spot_page]
        self.pages = self.norm_pages
        self.idx = 0

        self.pom = Pomodoro(cfg)
        self.auto = AutoBrightness(cfg)
        self.levels = self._levels(self.auto.value)
        self.lvl = 0
        self.light = 0
        a = cfg.get("alerts") or {}
        self.alerter = Alerter(
            self.levels[0],
            led_gpio=int(a.get("led_gpio", 5)),
            backlight=bool(a.get("backlight", False)),
            rgb_gpio=int(a.get("rgb_gpio", 0)),
        )
        self.blink = bool(a.get("blink", True))
        self._hold = False
        self.hub_poll = 0
        self.started = time.ticks_ms()

        # notification center (hub /events -> banner + local log)
        self.notif = None
        self.notif_at = 0
        self.notif_queue = []
        self.notif_log = []
        self.notif_dirty = False
        self.notif_poll = time.ticks_ms() - NOTIF_POLL_MS
        self._page_needs_draw = False

        # Button state (UIFlow2 API: isPressed())
        self._down = False
        self._press_at = 0
        self._hold_done = False
        self._soft_done = False
        self._clicks = []
        self.http_err = ""

    def _levels(self, base):
        base = int(base)
        if base < 4:
            base = 4
        if self.auto.on:
            return [base, max(6, base * 50 // 100), max(4, base * 15 // 100)]
        return [base, 45, 8]

    def local(self):
        return time.localtime(time.time() + self.tz * 3600)

    # ---- paging -------------------------------------------------------
    def next_page(self):
        self.idx = (self.idx + 1) % len(self.pages)
        pg = self.pages[self.idx]
        pg.enter(self)
        pg.draw(self)

    def _switch_mode(self, new_mode):
        self.mode = 1 if new_mode else 0
        self.pages = self.spot_pages if self.mode == 1 else self.norm_pages
        self.idx = 0
        self._clicks = []
        self._hold_done = True
        self.notif = None
        self.notif_dirty = False
        pg = self.pages[0]
        pg.enter(self)
        pg.draw(self)

    # ---- notifications -------------------------------------------------
    def _tick_notif(self, now):
        """Move /events into the local log and advance the banner queue."""
        if self.net.events:
            now_epoch = int(time.time())
            for ev in self.net.events:
                self.notif_log.insert(0, ev)
                ts = int(ev.get("ts") or 0)
                # a reboot replays the hub log; do not spam old banners
                if not ts or now_epoch - ts <= NOTIF_MAX_AGE_S:
                    self.notif_queue.append(ev)
            self.net.events = []
            if len(self.notif_log) > 20:
                del self.notif_log[20:]
        if (self.notif is not None and
                time.ticks_diff(now, self.notif_at) > NOTIF_SHOW_MS):
            self.notif = None
            self._page_needs_draw = True
        if self.notif is None and self.notif_queue:
            self.notif = self.notif_queue.pop(0)
            self.notif_at = now
            self.notif_dirty = True

    def _dismiss_notif(self):
        self.notif = None
        self.notif_dirty = False
        if self.notif_queue:
            self.notif = self.notif_queue.pop(0)
            self.notif_at = time.ticks_ms()
            self.notif_dirty = True
        else:
            self.pages[self.idx].draw(self)

    def _draw_notif(self):
        ev = self.notif
        if not ev:
            return
        kind = ev.get("kind") or ""
        col = pages.kind_color(kind)
        u.clear(u.PANEL)
        u.LCD.fillRect(0, 0, u.W, 3, col)
        label = "NEW " + kind.upper().replace("_", " ")
        u.text1(label[:20], u.F_TINY, 6, 7, col)
        ts = int(ev.get("ts") or 0)
        if ts:
            t = time.localtime(ts + self.tz * 3600)
            u.rtext("%02d:%02d" % (t[3], t[4]), u.F_TINY, 122, 7, u.MUTED)
        ty = 24
        for line in u.wrap(str(ev.get("title") or "NOTICE"),
                           u.F_MD, 116, 2):
            u.ctext(line, u.F_MD, ty, u.WHITE)
            ty += 23
        by = ty + 2
        for line in u.wrap(str(ev.get("body") or ""), u.F_SM, 116, 2):
            u.text1(line, u.F_SM, 6, by, u.MUTED)
            by += 14
        u.ctext("CLICK TO DISMISS", u.F_TINY, 113, u.LINE)

    # ---- buttons ------------------------------------------------------
    def _buttons(self):
        b = getattr(M5, "BtnA", None)
        if b is None:
            return
        now = time.ticks_ms()
        down = _is_down(b)

        if down:
            if not self._down:
                self._down = True
                self._press_at = now
                self._hold_done = False
                self._soft_done = False
            elif not self._hold_done:
                held = time.ticks_diff(now, self._press_at)
                if not self._soft_done and self.notif is None and held >= SOFT_HOLD_MS:
                    try:
                        used = bool(self.pages[self.idx].soft_hold(self))
                    except Exception as e:
                        used = False
                        print("soft: %s" % e)
                    if used:
                        self._soft_done = True
                if held >= HOLD_MS:
                    self._switch_mode(1 - self.mode)
        elif self._down:
            self._down = False
            if not self._hold_done and not self._soft_done:
                self._clicks.append(now)

        if self._clicks and time.ticks_diff(now, self._clicks[-1]) > CLICK_GAP_MS:
            n = len(self._clicks)
            self._clicks = []
            self._gesture(n)

    def _gesture(self, n):
        if self.notif is not None:
            self._dismiss_notif()
            return
        if self.mode == 1:
            if n == 1:
                self._spotify("/spotify_toggle")
            elif n == 2:
                self._spotify("/spotify_next")
            else:
                self._spotify("/spotify_previous")
        else:
            if n == 1:
                self.next_page()
            else:
                pg = self.pages[self.idx]
                pg.action(self)
                pg.draw(self)

    def _spotify(self, path):
        d = self._http_get(path)
        if d and d.get("status") not in (None, "success", "triggered", "queued"):
            print("spotify %s: %s" % (path, d.get("message")))
        # next status poll should happen soon after the command
        try:
            self.spot_page.at = time.ticks_ms() - 10000
        except Exception:
            pass

    def _hub_addr(self):
        host = self.net.hub_host
        port = self.net.hub_port
        if not host and self.net.hub_targets:
            host, port, _ = self.net.hub_targets[0]
        if not host:
            host = "127.0.0.1"
        return host, int(port or 8765)

    def _http_get(self, path):
        """GET path from the hub using the same socket helper as net.py.

        urequests is not part of this UIFlow2 build, and the first configured
        hub URL may be an offline machine, so every known target is tried.
        """
        self.http_err = ""
        if not self.net.connected:
            self.http_err = "NO WIFI"
            return None
        targets = []
        if self.net.hub_host:
            targets.append((self.net.hub_host, int(self.net.hub_port or 8765)))
        for t in self.net.hub_targets:
            targets.append((t[0], int(t[1] or 8765)))
        seen = []
        for host, port in targets:
            if (host, port) in seen:
                continue
            seen.append((host, port))
            try:
                p = netmod.with_token(path, self.net.hub_token)
                raw = netmod.http_get(host, p, port=port,
                                      timeout=3.0, max_size=4096)
                if netmod.http_status(raw) == 401:
                    self.http_err = "BAD TOKEN"
                    print("hub %s:%d wrong token" % (host, port))
                    continue
                body = netmod.http_body(raw)
                if not body:
                    self.http_err = "EMPTY %s" % host
                    continue
                data = json.loads(body)
                self.net.hub_host, self.net.hub_port = host, port
                return data
            except Exception as e:
                msg = str(e)
                # ESP-IDF errno values: 116 = timeout, 111/104/113 = refused/reset/unreachable
                if "116" in msg or "110" in msg:
                    self.http_err = "HUB TIMEOUT"
                elif "111" in msg or "104" in msg or "113" in msg:
                    self.http_err = "HUB OFFLINE"
                else:
                    self.http_err = msg[:22]
                print("spotify %s:%d %s" % (host, port, e))
        return None

    # ---- main loop ----------------------------------------------------
    def run(self):
        M5.Display.setBrightness(self.levels[0])
        u.splash()
        now = time.ticks_ms()
        self.net.poll(now)
        pg = self.pages[0]
        pg.enter(self)
        pg.draw(self)
        while True:
            now = time.ticks_ms()
            M5.update()
            self.net.poll(now)
            if time.ticks_diff(now, self.hub_poll) > 30000:
                self.hub_poll = now
                self.net.maybe_hub()
            self.pom.update(now)
            level = self.auto.tick(now)
            self.light = self.auto.pct
            if level is not None:
                self.levels = self._levels(level)
                self.alerter.brightness(self.levels[self.lvl])
            try:
                self._buttons()
            except Exception as e:
                print("btn: %s" % e)
            if time.ticks_diff(now, self.notif_poll) > NOTIF_POLL_MS:
                self.notif_poll = now
                self.net.maybe_events()
            self._tick_notif(now)
            if self._page_needs_draw:
                self._page_needs_draw = False
                self.pages[self.idx].draw(self)
            self.alerter.update(now, self.blink and self.net.alert)
            if self.notif is not None and not self.net.alert:
                self.alerter.flash((now // 250) % 2 == 0)
            pg = self.pages[self.idx]
            if pg.tick(self, now):
                pg.draw(self)
                if self.notif is not None:
                    self.notif_dirty = True
            if self.notif is not None and self.notif_dirty:
                self.notif_dirty = False
                self._draw_notif()
            time.sleep_ms(5 if pg.animating else 20)
