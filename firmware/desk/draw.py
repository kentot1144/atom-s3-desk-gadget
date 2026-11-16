"""Page renderers for the hub-driven pages."""
import M5
import gc
import sys
import time
from desk.ui import ACCENT, ACCENT2, AMBER, BLACK, F_BIG, F_MD, F_SM, F_TINY, GREEN, LCD, LINE, MON, MUTED, ORANGE, PANEL, PURPLE, RED, W, WD, WHITE, age_text, atom_face, bar, ctemp, ctext, dots, header, pc, rtemp, rtext, seg_time, t1, temp, tl, wrap

class AppDraw:
    def _head(self, p, color=None):
        n = len(self.page_list())
        header(p.get("t") or "", self.idx, n,
               pc(p.get("hc")) if color is None and p.get("hc") is not None
               else (color or ACCENT))
        dots(n, self.idx)

    def draw_clock(self, p):
        LCD.fillScreen(BLACK)
        t = self.local()
        ctext("%s %02d %s" % (WD[t[6]], t[2], MON[t[1] - 1]), F_SM, 6, MUTED)
        seg_time("%02d%02d" % (t[3], t[4]), 24, WHITE)
        LCD.fillRect(14, 76, 100, 3, LINE)
        if t[5] > 0:
            LCD.fillRect(14, 76, t[5] * 100 // 60, 3, ACCENT)
        if p.get("temp") is not None:
            temp("%d" % int(p["temp"]), F_MD, 14, 88, AMBER)
        else:
            tl("--", F_MD, 14, 88, LINE)
        if self.wifi.up():
            if p.get("al"):
                rtext("ALERT", F_SM, 120, 90, RED)
            else:
                rtext("ONLINE", F_SM, 120, 90, GREEN)
        else:
            rtext("NO WIFI", F_SM, 120, 90, RED)
        cal = p.get("cal") or ""
        if cal:
            t1(cal[:25], F_TINY, 4, 109, ACCENT2)
        dots(len(self.page_list()), self.idx)
        self.clk_sec = t[5]

    def draw_big(self, p):
        LCD.fillScreen(BLACK)
        self._head(p)
        ctemp(p.get("v", "--"), F_BIG, 34, WHITE, r=3)
        ctext(p.get("cond", ""), F_MD, 84, ACCENT)
        ctext(p.get("sub", ""), F_SM, 106, MUTED)

    # Rows pages: four entries fit, and an "auto" page with more entries is
    # split into sub-pages that the app loop advances on a timer.
    ROWS_PER = 4
    ROW_Y0 = 28
    ROW_PITCH = 22

    def _row(self, r, y):
        if r.get("d") is not None:
            LCD.fillRect(5, y + 3, 5, 5, pc(r["d"], GREEN))
            x = 14
        else:
            x = 4
        tl(r.get("l", ""), F_SM, x, y, pc(r.get("c")))
        v = r.get("v") or ""
        if v:
            if r.get("deg"):
                rtemp(v, F_SM, 124, y, pc(r.get("vc"), ORANGE))
            elif len(v) > 5:
                rtext(v, F_TINY, 123, y + 2, pc(r.get("vc")))
            else:
                rtext(v, F_SM, 123, y, pc(r.get("vc")))
        if r.get("p") is not None:
            bar(34, y + 3, 56, 7, r["p"], pc(r.get("pc"), GREEN))
        s = r.get("s")
        if s:
            tl(s, F_TINY, 14, y + 13, pc(r.get("sc"), MUTED))

    def rows_pages(self, p):
        n = len(p.get("rows") or [])
        if not p.get("auto") or n <= self.ROWS_PER:
            return 1
        return (n + self.ROWS_PER - 1) // self.ROWS_PER

    def draw_rows(self, p):
        rows = p.get("rows") or []
        npages = self.rows_pages(p)
        i = (self.rs_page % npages) if npages > 1 else 0
        LCD.fillScreen(BLACK)
        if npages > 1:
            chunk = rows[i * self.ROWS_PER:(i + 1) * self.ROWS_PER]
        else:
            chunk = rows
        y = self.ROW_Y0
        for r in chunk:
            if y > 104:
                break
            self._row(r, y)
            y += self.ROW_PITCH
        if npages > 1:
            # The page ring already shows SERVERS as one page, so the
            # sub-page counter belongs in the title.
            header("%s %d/%d" % (p.get("t") or "ROWS", i + 1, npages),
                   self.idx, len(self.page_list()),
                   pc(p.get("hc")) if p.get("hc") is not None else ACCENT)
            dots(len(self.page_list()), self.idx)
        else:
            self._head(p)
        if p.get("foot"):
            ctext(p["foot"], F_TINY, 107, pc(p.get("fc"), MUTED))

    def draw_msg(self, p):
        LCD.fillScreen(BLACK)
        self._head(p)
        ctext((p.get("m") or "")[:22], F_SM, 58, MUTED)
        if p.get("m2"):
            ctext((p.get("m2") or "")[:22], F_TINY, 76, LINE)

    def draw_focus(self, p):
        LCD.fillScreen(BLACK)
        self._head(p)
        q = self.pom
        if q.mode == "focus":
            c, label = RED, "FOCUS"
        else:
            c, label = GREEN, "BREAK"
        ctext(label, F_SM, 28, c)
        rem = max(0, q.rem)
        seg_time("%02d%02d" % (rem // 60000, (rem % 60000) // 1000),
                 42, WHITE)
        total = q.focus if q.mode == "focus" else q.brk
        bar(14, 94, 100, 5, rem * 100 // total if total else 0, c)
        ctext("2x CLICK PAUSE" if q.running else "2x CLICK START",
              F_TINY, 104, MUTED)
        rtext("S%d" % q.sessions, F_TINY, 122, 28, MUTED)
        self.focus_at = time.ticks_ms()

    def draw_restart(self, p):
        LCD.fillScreen(BLACK)
        self._head(p, PURPLE)
        r = self.rst
        st = r["state"]
        c = GREEN if st == "busy" else (
            RED if st in ("error", "notsynced") else ACCENT2)
        LCD.fillCircle(64, 46, 19, c)
        LCD.fillTriangle(58, 35, 58, 57, 76, 46, BLACK)
        ctext("SPOTIFY", F_SM, 70, WHITE)
        if st == "busy":
            ctext("RESTARTING" + "." * r["dots"], F_TINY, 90, AMBER)
            ctext("on the hub PC", F_TINY, 104, LINE)
        elif st == "check":
            ctext("VERIFYING" + "." * r["dots"], F_TINY, 90, AMBER)
            ctext("playback is live", F_TINY, 104, LINE)
        elif st == "done":
            ctext("SYNCED", F_SM, 90, GREEN)
            ctext("spotify is playing", F_TINY, 106, MUTED)
        elif st == "notsynced":
            ctext("NOT SYNCED", F_SM, 90, RED)
            ctext("double click retry", F_TINY, 106, LINE)
        elif st == "error":
            ctext(r["msg"], F_TINY, 90, RED)
            ctext("try again", F_TINY, 104, LINE)
        else:
            ctext("DOUBLE CLICK", F_TINY, 88, MUTED)
            ctext("to restart", F_TINY, 102, LINE)
        dots(len(self.page_list()), self.idx)

    def draw_face(self, p):
        LCD.fillScreen(BLACK)
        self._head(p, RED if p.get("al") else ACCENT)
        f = self.face
        try:
            ax, _ay, _az = M5.Imu.getAccel()
        except Exception:
            ax = 0.0
        if ax > 0.35:
            mood, label, c = "happy", "HAPPY", AMBER
        elif ax < -0.35:
            mood, label, c = "sleep", "SLEEPY", PURPLE
        else:
            mood, label, c = "normal", "CHILL", ACCENT
        if mood != "sleep" and f["st"] == 0:
            mood = "blink"
        if p.get("al"):
            label, c = "ALERT", RED
        atom_face(mood, f["dev"], bool(p.get("al")))
        ctext(label, F_TINY, 107, c)
        dots(len(self.page_list()), self.idx)

    def draw_notice(self, p):
        LCD.fillScreen(BLACK)
        self._head(p, ACCENT2)
        rows = p.get("rows") or []
        if not rows:
            ctext("NO NOTICES", F_SM, 52, MUTED)
            ctext("hub events appear here", F_TINY, 72, LINE)
        else:
            y = 25
            for r in rows:
                LCD.fillRect(4, y + 3, 4, 4, pc(r.get("d"), ACCENT))
                t1(r.get("l", ""), F_SM, 12, y, pc(r.get("c")))
                t1(r.get("s", ""), F_TINY, 12, y + 12, MUTED)
                y += 24
            if p.get("more"):
                tl("+%d MORE" % p["more"], F_TINY, 6, 99, MUTED)
        ctext("HUB EVENT LOG", F_TINY, 110, LINE)
        dots(len(self.page_list()), self.idx)

    def draw_status(self, p):
        LCD.fillScreen(BLACK)
        self._head(p, RED if self.alert() else ACCENT)
        ip, rssi = "-", 0
        if self.wifi.up():
            try:
                ip = self.wifi.wlan.ifconfig()[0]
            except Exception:
                ip = "?"
            try:
                rssi = self.wifi.wlan.status("rssi")
            except Exception:
                rssi = 0
        gc.collect()
        free = gc.mem_free() // 1024
        up = time.ticks_diff(time.ticks_ms(), self.started) // 1000
        rows = (("IP", ip), ("RSSI", "%d dBm" % rssi),
                ("HEAP", "%d KB" % free),
                ("UPTIME", "%dd %02dh %02dm" % (
                    up // 86400, (up % 86400) // 3600,
                    (up % 3600) // 60)),
                ("LIGHT", "%d%%  B%d" % (self.light.pct, self.bright)))
        y = 23
        for name, val in rows:
            tl(name, F_TINY, 6, y, MUTED)
            rtext(val, F_SM, 122, y - 1, WHITE)
            y += 12
        upn = int(p.get("up") or 0)
        tot = int(p.get("tot") or 0)
        tl("SVC", F_TINY, 6, 86, MUTED)
        rtext("%d/%d UP" % (upn, tot), F_SM, 122, 85,
              GREEN if upn == tot else AMBER)
        last = int(p.get("last") or 0)
        if last:
            ago = max(0, self.now() - last)
            tl(("DOWN %s AGO" % age_text(ago))[:21], F_TINY, 6, 100,
               RED if upn < tot else AMBER)
        else:
            tl("NO DOWNTIME", F_TINY, 6, 100, GREEN)
        tl("HUB", F_TINY, 6, 110, MUTED)
        if self.hub.fresh(15000):
            txt, c = "OK", GREEN
        elif self.hub.ui:
            txt, c = "STALE", AMBER
        else:
            txt, c = (self.hub.err or "OFFLINE")[:10], RED
        rtext(txt, F_TINY, 122, 110, c)
        dots(len(self.page_list()), self.idx)
        self.status_at = time.ticks_ms()

    def draw_sys(self, p):
        """Local health page.  Renders even if the hub never answered."""
        LCD.fillScreen(BLACK)
        self._head(p, RED if self.alert() else ACCENT)
        up = self.wifi.up()
        ip = "-"
        rssi = 0
        if up:
            try:
                ip = self.wifi.wlan.ifconfig()[0]
            except Exception:
                ip = "?"
            try:
                rssi = int(self.wifi.wlan.status("rssi"))
            except Exception:
                rssi = 0
            if rssi > -67:
                rc = GREEN
            elif rssi > -75:
                rc = AMBER
            else:
                rc = RED
            rtxt = "%d dBm" % rssi
        else:
            rc = RED
            rtxt = "--"
        age = self.hub.age()
        if self.hub.fresh(15000):
            htxt, hc = "OK %s" % age_text(age), GREEN
        elif self.hub.ui:
            htxt, hc = "STALE %s" % age_text(age), AMBER
        else:
            htxt, hc = (self.hub.err or "OFFLINE")[:12], RED
        secs = time.ticks_diff(time.ticks_ms(), self.started) // 1000
        try:
            v = sys.implementation.version
            fw = "%d.%d.%d" % (v[0], v[1], v[2])
        except Exception:
            fw = "?"
        rows = (
            ("WIFI", (self.wifi.ssid or "-")[:16], GREEN if up else RED,
             F_TINY),
            ("IP", ip, WHITE, F_SM),
            ("RSSI", rtxt, rc, F_SM),
            ("HUB", htxt, hc, F_SM),
            ("HEAP", "%d KB" % (gc.mem_free() // 1024), WHITE, F_SM),
            ("UP", "%dd %02dh %02dm" % (secs // 86400,
                                        (secs % 86400) // 3600,
                                        (secs % 3600) // 60), WHITE, F_SM),
            ("FW", fw, MUTED, F_SM),
        )
        y = 22
        for name, val, col, fnt in rows:
            tl(name, F_TINY, 6, y + 1, MUTED)
            rtext(val, fnt, 122, y, col)
            y += 12
        tl("2x CLICK = POLL", F_TINY, 6, 108, LINE)
        self.status_at = time.ticks_ms()

    def draw_banner(self):
        ev = self.banner
        if not ev:
            return
        kind = ev.get("kind") or ""
        c = (GREEN if kind == "mc_join" else RED if kind == "mc_leave"
             else AMBER if kind == "mc_move" else ACCENT)
        LCD.fillScreen(PANEL)
        LCD.fillRect(0, 0, W, 3, c)
        t1(("NEW " + kind.upper().replace("_", " "))[:20],
           F_TINY, 6, 7, c)
        ts = int(ev.get("ts") or 0)
        if ts:
            t = time.localtime(ts + self.tz * 3600)
            rtext("%02d:%02d" % (t[3], t[4]), F_TINY, 122, 7, MUTED)
        ty = 24
        for line in wrap(str(ev.get("title") or "NOTICE"), F_MD, 116, 2):
            ctext(line, F_MD, ty, WHITE)
            ty += 23
        by = ty + 2
        for line in wrap(str(ev.get("body") or ""), F_SM, 116, 2):
            t1(line, F_SM, 6, by, MUTED)
            by += 14
        ctext("CLICK TO DISMISS", F_TINY, 113, LINE)
