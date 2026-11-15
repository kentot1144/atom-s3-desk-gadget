"""Spotify page state machine: scrolling, lyric sync and timing."""
import time
from desk.ui import F_JA, F_SM, F_TINY, W, tw
from desk.spotdraw import SpotDraw

class Spot(SpotDraw):
    def __init__(self, app):
        self.app = app
        self.pos = 0
        self.dir = 1
        self.hold_until = 0
        self.max_off = 0
        self.long = False
        self.step = 1
        self.frame_at = 0
        self.prog_at = 0
        self.dirty_full = True
        self.dirty_lines = False
        self.dirty_prog = False
        self.animating = False
        self.lyrics = False
        self.lyr_key = None
        self.line = ""
        self.rows = []
        self.row_y = 22
        self.row_dy = 25
        self.next_y = 73
        self.lyr_font = F_JA
        self.lyr_scroll = False
        self.sig = None

    def enter(self):
        self.sig = None
        self.pos = 0
        self.dir = 1
        self.dirty_full = True
        self.frame_at = time.ticks_ms()
        self.hold_until = time.ticks_add(time.ticks_ms(), self.HOLD_MS)

    def soft_hold(self):
        self.lyrics = not self.lyrics
        self.lyr_key = None
        self.rows = []
        self.line = ""
        self.long = False
        self.pos = 0
        self.dir = 1
        self.dirty_full = True

    def _pos(self):
        return self.app.hub.progress()

    def refresh(self, now):
        hub = self.app.hub
        sp = hub.sp
        fresh = hub.fresh(10000)
        if not sp or not fresh:
            if not self.app.wifi.up():
                self._fail("NO WIFI")
            else:
                self._fail(hub.err if hub.err else "HUB OFFLINE")
            return self.dirty_full
        st = sp.get("status")
        if st not in ("success", "not_playing", "paused"):
            self._fail(sp.get("message") or st or "ERROR")
            return self.dirty_full
        sig = (sp.get("song"), sp.get("artist"), sp.get("device"),
               bool(sp.get("is_playing")))
        prev = self.sig
        self.sig = sig
        if sig != prev:
            self.pos = 0
            self.dir = 1
            self.frame_at = now
            self.hold_until = time.ticks_add(now, self.HOLD_MS)
            self._plan(sp)
            self.dirty_full = True
        elif time.ticks_diff(now, self.prog_at) > 250:
            self.prog_at = now
            self.dirty_prog = True
        if self.lyrics:
            self._sync_lyrics(now)
        return (self.dirty_full or self.dirty_lines or self.dirty_prog)

    def _fail(self, msg):
        msg = (msg or "HUB OFFLINE")[:22]
        if self.sig != msg or not self.rows:
            self.sig = msg
            self.rows = []
            self.long = False
            self.animating = False
            self.dirty_full = True

    def _sync_lyrics(self, now):
        ly = self.app.hub.ly
        if not ly:
            return
        line = ""
        if ly.get("status") == "success":
            line = ly.get("line") or ""
        key = (ly.get("status"), line)
        if key != self.lyr_key:
            self.lyr_key = key
            self.line = line
            self._plan_lyrics(now)
            self.dirty_lines = True
        elif self.long:
            self.dirty_lines = True

    def _is_latin(self, s):
        for ch in s:
            if ord(ch) > 0x2E7F:
                return False
        return True

    def _wrap(self, s, f, max_rows):
        limit = W - 12
        words = s.split()
        if not words:
            return None
        rows = []
        cur = ""
        for word in words:
            while tw(word, f) > limit:
                lo, hi = 1, len(word)
                while lo < hi:
                    m = (lo + hi + 1) // 2
                    if tw(word[:m], f) <= limit:
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
            if tw(cand, f) <= limit:
                cur = cand
            else:
                if cur:
                    rows.append(cur)
                cur = word
                if len(rows) >= max_rows and tw(cur, f) > limit:
                    return None
            if len(rows) > max_rows:
                return None
        if cur:
            rows.append(cur)
        if len(rows) > max_rows:
            return None
        return rows

    def _split_two(self, s, f):
        total = tw(s, f)
        if total <= W - 12:
            return [s]
        target = total // 2
        lo, hi = 1, len(s) - 1
        best = max(1, len(s) // 2)
        while lo <= hi:
            m = (lo + hi) // 2
            if tw(s[:m], f) < target:
                lo = m + 1
            else:
                best = m
                hi = m - 1
        p1 = s[:best].strip()
        p2 = s[best:].strip()
        if not p1:
            return [s]
        return [p1, p2] if p2 else [p1]

    def _lyric_step(self):
        if not self.long:
            return 1
        step = self.LYRIC_STEP
        na = (self.app.hub.ly or {}).get("next_at")
        if na:
            remain = int(na) - self._pos() - 2 * self.LYRIC_HOLD_MS
            if remain < 200:
                remain = 200
            frames = max(1, remain // self.LYRIC_FRAME_MS)
            need = self.max_off // frames + 1
            if need > step:
                step = need
        if step > self.LYRIC_STEP_MAX:
            step = self.LYRIC_STEP_MAX
        return step

    def _plan_lyrics(self, now):
        s = (self.line or "").strip()
        limit = W - 12
        rows = None
        font = F_JA
        dy = 25
        y = 22
        scroll = False
        if not s:
            rows = [""]
            y = 33
        elif tw(s, F_JA) <= limit:
            rows = [s]
            y = 33
        elif self._is_latin(s):
            rows = self._wrap(s, F_SM, 4)
            if rows:
                font = F_SM
                dy = 13
                y = 21
            else:
                rows = self._wrap(s, F_TINY, 4)
                if rows:
                    font = F_TINY
                    dy = 12
                    y = 20
        if not rows:
            rows = self._split_two(s, F_JA)
            font = F_JA
            dy = 25
            y = 22
            scroll = True
        elif len(rows) == 1:
            y = 33
        self.rows = rows
        self.lyr_font = font
        self.lyr_scroll = scroll
        self.row_y = y
        self.row_dy = dy
        if not scroll and len(rows) == 1:
            self.next_y = 64
        else:
            self.next_y = min(74, max(64, y + dy * len(rows)))
        w = 0
        if scroll:
            for row in rows:
                rw = tw(row, font)
                if rw > w:
                    w = rw
        self.max_off = max(0, w - limit)
        self.long = self.max_off > 0
        self.pos = 0
        self.dir = 1
        self.frame_at = now
        self.hold_until = time.ticks_add(now, self.LYRIC_HOLD_MS)
        self.step = self._lyric_step()

    def _plan(self, d):
        self.max_off = 0
        self.step = 1
        for s, f in self._lines(d):
            w = tw(s, f)
            if w > W - 12 and w - (W - 12) > self.max_off:
                self.max_off = w - (W - 12)
        self.long = self.max_off > 0

    def _lines(self, d):
        return ((d.get("song") or "-", F_JA),
                (d.get("artist") or "-", F_JA),
                ("ON " + (d.get("device") or "-"), F_TINY))

    def tick(self, now):
        redraw = self.refresh(now)
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
