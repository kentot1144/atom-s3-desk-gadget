"""Main loop: polling, input gestures, notifications and drawing."""
import M5
import gc
import math
import time
from desk.const import CLICK_GAP_MS, HOLD_MS, HUB_POLL_LIVE_MS, HUB_POLL_MS, NOTIF_MAX_AGE_S, NOTIF_SHOW_MS, NTP_FALLBACK_MS, ROWS_PAGE_MS, SOFT_HOLD_MS
from desk.ui import AMBER, GREEN, RED, splash, splash_line


def _is_down(btn):
    """MicroPython builds differ in the button API; try each spelling."""
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


class AppLoop:
    # -- spotify / restart actions -----------------------------------
    def spotify(self, path):
        self.hub.get(path, timeout=2.0, max_size=2048)
        self.hub.at = time.ticks_add(time.ticks_ms(), -1000)

    def restart_spotify(self):
        now = time.ticks_ms()
        d = self.hub.get("/spotify_restart", timeout=2.0, max_size=2048)
        r = self.rst
        if d and d.get("status") in ("queued", "success", "triggered"):
            r["state"] = "busy"
            r["msg"] = (d.get("message") or "restarting")[:20]
            r["sent"] = now
        else:
            r["state"] = "error"
            r["msg"] = (self.hub.err or "HUB OFFLINE")[:18]
            r["sent"] = now
        r["at"] = time.ticks_add(now, -5000)
        r["check_at"] = 0
        r["checks"] = 0
        r["good"] = 0
        r["prog"] = -1
        r["song"] = ""
        self.draw_restart(self.page())

    def restart_check(self, now):
        """Prove playback really resumed after the restart.

        Polls the hub directly about once a second for six seconds and counts
        samples where the same song is playing and the position has moved, so a
        stale payload copied over from before the restart cannot pass.
        """
        r = self.rst
        if time.ticks_diff(now, r["at"]) < 900:
            return False
        r["at"] = now
        r["dots"] = (r["dots"] + 1) % 4
        d = self.hub.get("/state?only=sp", timeout=2.0, max_size=2048)
        if d:
            self.hub.apply(d)
            sp = self.hub.sp or {}
            ok = (sp.get("status") == "success" and sp.get("is_playing")
                  and sp.get("song"))
            p = int(sp.get("progress_ms") or 0)
            song = sp.get("song") or ""
            if ok and r["prog"] >= 0 and p != r["prog"] and song == r["song"]:
                r["good"] += 1
            r["prog"] = p if ok else -1
            r["song"] = song
        if time.ticks_diff(now, r["check_at"]) >= 6000:
            r["state"] = "done" if r["good"] >= 2 else "notsynced"
        return True

    def action(self):
        p = self.page()
        k = p.get("k")
        if k == "focus":
            self.pom.toggle()
            self.focus_at = 0
            self.dirty = True
        elif k == "restart":
            self.restart_spotify()
        elif k in ("clock", "big", "rows", "notice", "status", "sys"):
            self.hub.at = 0
            self.clk_sec = -1
            self.status_at = 0
            self.dirty = True

    # -- auto-advancing rows -----------------------------------------
    def rows_tick(self, now, npages):
        """Advance the rows sub-page.  True when the page needs a redraw."""
        if npages < 2:
            return False
        if time.ticks_diff(now, self.rs_at) < ROWS_PAGE_MS:
            return False
        self.rs_at = now
        self.rs_page = (self.rs_page + 1) % npages
        return True

    # -- buttons -----------------------------------------------------
    def buttons(self, now):
        b = getattr(M5, "BtnA", None)
        if b is None:
            return
        down = _is_down(b)
        if down:
            if not self.down:
                self.down = True
                self.press_at = now
                self.hold_done = False
                self.soft_done = False
            elif not self.hold_done:
                held = time.ticks_diff(now, self.press_at)
                if (not self.soft_done and self.banner is None and
                        held >= SOFT_HOLD_MS):
                    if self.mode == 1 and self.page().get("k") == "spotify":
                        self.spot.soft_hold()
                        self.soft_done = True
                if held >= HOLD_MS:
                    self.switch_mode()
        elif self.down:
            self.down = False
            if not self.hold_done and not self.soft_done:
                self.clicks.append(now)
        if self.clicks and time.ticks_diff(now, self.clicks[-1]) > CLICK_GAP_MS:
            n = len(self.clicks)
            self.clicks = []
            self.gesture(n)

    def gesture(self, n):
        if self.banner is not None:
            self.banner = None
            self.banner_dirty = False
            self.enter_page()
            return
        if self.mode == 1:
            if n == 1:
                self.spotify("/spotify_toggle")
            elif n == 2:
                self.spotify("/spotify_next")
            else:
                self.spotify("/spotify_previous")
        elif n == 1:
            self.next_page()
        else:
            self.action()

    # -- notifications ----------------------------------------------
    def tick_notify(self, now):
        if self.hub.new_events:
            for e in self.hub.new_events:
                ts = int(e.get("ts") or 0)
                if not ts or self.now() - ts <= NOTIF_MAX_AGE_S:
                    self.banners.append(e)
            self.hub.new_events = []
            if len(self.banners) > 6:
                del self.banners[:-6]
        if self.banner is not None:
            if time.ticks_diff(now, self.banner_at) > NOTIF_SHOW_MS:
                self.banner = None
                self.enter_page()
        if self.banner is None and self.banners:
            self.banner = self.banners.pop(0)
            self.banner_at = now
            self.banner_dirty = True

    # -- main loop ---------------------------------------------------
    def run(self):
        M5.Display.setBrightness(self.bright)
        splash()
        self.started = time.ticks_ms()
        boot_at = self.started
        booting = True
        while True:
            now = time.ticks_ms()
            M5.update()

            up = self.wifi.poll(now, self.hub.fresh(30000))
            if up and not self.connected_at:
                self.connected_at = now
                self.dirty = True
            if booting:
                # Keep the splash up while joining, but never block.  A long
                # stall here trips the task watchdog and the board resets in a
                # loop instead of ever reaching the first page.
                if up:
                    try:
                        ip = self.wifi.wlan.ifconfig()[0]
                    except Exception:
                        ip = "?"
                    splash_line("%s %s" % (
                        (self.wifi.ssid or "WIFI")[:13], ip), GREEN)
                    booting = False
                    self.dirty = True
                elif time.ticks_diff(now, boot_at) > 12000:
                    splash_line("NO WIFI - CHECK CONFIG", RED)
                    booting = False
                    self.dirty = True
                else:
                    splash_line("JOIN %s" % (self.wifi.try_ssid or
                                             self.wifi.err or "..."), AMBER)
                time.sleep_ms(60)
                continue
            if (up and not self.ntp_done and not self.hub.ts and
                    time.ticks_diff(now, self.connected_at) > NTP_FALLBACK_MS):
                # One-shot fallback for the first boot without any hub time.
                self.ntp_done = True
                try:
                    import ntptime
                    try:
                        ntptime.settime("216.239.35.0")
                    except TypeError:
                        ntptime.settime()
                except Exception:
                    pass

            p = self.page()
            kind = p.get("k") or ""
            if kind in ("spotify", "rows"):
                interval = HUB_POLL_LIVE_MS
            elif kind == "status":
                interval = HUB_POLL_LIVE_MS
            else:
                interval = HUB_POLL_MS
            if kind == "spotify" and self.spot.lyrics:
                interval = 400
            if up:
                self.hub.poll(now, interval)
            if self.hub.ui is not self.last_ui:
                self.last_ui = self.hub.ui
                self.ui = self.hub.ui
                n = len(self.page_list())
                if self.idx >= n:
                    self.idx = 0
                self.dirty = True

            self.pom.update(now)
            level = self.light.tick(now)
            if level is not None:
                self.bright = level
                self.led.set_bright(level)
            self.bright = self.light.value

            try:
                self.buttons(now)
            except Exception as e:
                print("btn", e)
            self.tick_notify(now)

            p = self.page()
            kind = p.get("k") or ""
            if self.banner is not None:
                if self.banner_dirty:
                    self.banner_dirty = False
                    try:
                        self.draw_banner()
                    except Exception as e:
                        print("banner", e)
            elif kind == "clock":
                s = self.local()[5]
                if s != self.clk_sec:
                    try:
                        self.draw_clock(p)
                    except Exception as e:
                        print("draw", e)
            elif kind == "focus":
                if time.ticks_diff(now, self.focus_at) >= 250:
                    try:
                        self.draw_focus(p)
                    except Exception as e:
                        print("draw", e)
            elif kind == "face":
                if time.ticks_diff(now, self.face_at) >= 100:
                    self.face_at = now
                    f = self.face
                    f["breath"] = (f["breath"] + 1) % 30
                    f["dev"] = int(4 * math.sin(
                        f["breath"] * 2 * math.pi / 30))
                    f["tm"] += 100
                    if f["st"] == 0:
                        if f["tm"] >= 3000:
                            f["st"] = 1
                            f["tm"] = 0
                            f["open"] = 2000 + (f["breath"] % 5) * 1000
                    elif f["tm"] >= f["open"]:
                        f["st"] = 0
                        f["tm"] = 0
                    try:
                        self.draw_face(p)
                    except Exception as e:
                        print("draw", e)
            elif kind == "status":
                if time.ticks_diff(now, self.status_at) >= 1000:
                    try:
                        self.draw_status(p)
                    except Exception as e:
                        print("draw", e)
            elif kind == "sys":
                if time.ticks_diff(now, self.status_at) >= 1000:
                    try:
                        self.draw_sys(p)
                    except Exception as e:
                        print("draw", e)
            elif kind == "spotify":
                try:
                    if self.dirty:
                        self.dirty = False
                        self.spot.dirty_full = True
                    self.spot.tick(now)
                    self.spot.draw()
                except Exception as e:
                    print("spot", e)
            elif kind == "restart":
                r = self.rst
                if r["state"] == "busy":
                    if time.ticks_diff(now, r["sent"]) > 10000:
                        r["state"] = "check"
                        r["check_at"] = now
                        r["at"] = time.ticks_add(now, -1000)
                        r["good"] = 0
                        r["prog"] = -1
                        r["song"] = ""
                        self.dirty = True
                    elif time.ticks_diff(now, r["at"]) > 400:
                        r["at"] = now
                        r["dots"] = (r["dots"] + 1) % 4
                        self.dirty = True
                elif r["state"] == "check":
                    if self.restart_check(now):
                        self.dirty = True
                elif (r["state"] == "error" and
                      time.ticks_diff(now, r["sent"]) > 5000):
                    r["state"] = "idle"
                    self.dirty = True
                if self.dirty:
                    try:
                        self.draw_restart(p)
                    except Exception as e:
                        print("draw", e)
                    self.dirty = False
            elif kind == "rows":
                if self.rows_tick(now, self.rows_pages(p)):
                    self.dirty = True
                if self.dirty:
                    try:
                        self.draw_rows(p)
                    except Exception as e:
                        print("draw", e)
                    self.dirty = False
            else:
                if self.dirty:
                    try:
                        self.draw(p)
                    except Exception as e:
                        print("draw", e)
                    self.dirty = False

            self.led.update(now, self.alert())
            if self.banner is not None and not self.alert():
                self.led.flash((now // 250) % 2 == 0)

            if (time.ticks_diff(now, self.started) > 60000 and
                    gc.mem_free() < 30000):
                gc.collect()
            time.sleep_ms(5 if self.spot.animating else 20)
