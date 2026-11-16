"""Spotify page state machine: horizontal scroll, lyric slide-up and timing.

Lyrics behave like a small teleprompter: the current line sits at the top,
the coming lines sit below it, and when the next line starts the whole block
slides up by one row.  Long lines still ping-pong sideways, but only inside
their own row.
"""
import time

from desk.ui import F_JA, F_TINY, W, tw
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
        self.sig = None
        self.lyr_key = None
        self.buf = ("", "", "")     # current line, next line, next-next line
        self.line = ""
        self.slide = 0              # px the lyric block has slid up
        self.slide_at = 0
        self.armed_for = None       # next_at value already animated for

    def enter(self):
        self.sig = None
        self.pos = 0
        self.dir = 1
        self.dirty_full = True
        self.frame_at = time.ticks_ms()
        self.hold_until = time.ticks_add(time.ticks_ms(), self.HOLD_MS)
        self.lyr_key = None
        self.buf = ("", "", "")
        self.line = ""
        self.slide = 0
        self.armed_for = None

    def soft_hold(self):
        self.lyrics = not self.lyrics
        self.lyr_key = None
        self.buf = ("", "", "")
        self.line = ""
        self.slide = 0
        self.armed_for = None
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
            # a different track: drop the lyric block so it cuts, not slides
            self.lyr_key = None
            self.buf = ("", "", "")
            self.line = ""
            self.slide = 0
            self.armed_for = None
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
        if self.sig != msg or not self.buf[0]:
            self.sig = msg
            self.buf = ("", "", "")
            self.long = False
            self.animating = False
            self.slide = 0
            self.dirty_full = True

    # -- lyrics ------------------------------------------------------
    def _sync_lyrics(self, now):
        ly = self.app.hub.ly
        if not ly:
            return
        st = ly.get("status")
        if st != "success":
            key = (st, "")
            if key != self.lyr_key:
                self.lyr_key = key
                self.buf = ("", "", "")
                self.line = ""
                self.long = False
                self.slide = 0
                self.dirty_full = True
            return
        line = (ly.get("line") or "").strip()
        new = (line, (ly.get("next") or "").strip(),
               (ly.get("next2") or "").strip())
        key = ("success", line)
        if key == self.lyr_key:
            # Same sung line; still take a fresher lookahead so the row that
            # enters from below is already filled in.
            if (not self.slide and new[1] == self.buf[1]
                    and new[2] and new[2] != self.buf[2]):
                self.buf = (self.buf[0], self.buf[1], new[2])
                self.dirty_lines = True
            return
        self.lyr_key = key
        cur = self.buf[0]
        if self.slide:
            # The animation is already running: the hub has caught up, so all
            # that can change is the line entering from below.
            if new[0] == self.buf[1]:
                self.buf = (self.buf[0], self.buf[1], new[2])
            return
        if new[0] == cur:
            # Same line, later payload (lookahead moved on).
            self.buf = new
            return
        if new[0] == self.buf[1]:
            # The hub is one line ahead of what we show: slide now.
            self.buf = (self.buf[0], self.buf[1], new[2])
            self._start_slide(now)
            return
        # A jump (seek, or non-synced lyrics): cut instead of animating.
        self.buf = new
        self.line = line
        self.slide = 0
        self.armed_for = None
        self._plan_lyrics(now)
        self.dirty_full = True

    def _start_slide(self, now):
        if not self.buf[1]:
            return False
        self.slide = 1
        self.slide_at = now
        self.pos = 0
        self.dir = 1
        self.dirty_full = True
        return True

    def _land(self, now):
        self.buf = (self.buf[1], self.buf[2], "")
        self.line = self.buf[0]
        self.slide = 0
        self._plan_lyrics(now)
        self.dirty_full = True

    def _plan_lyrics(self, now):
        s = (self.buf[0] or "").strip()
        self.max_off = max(0, tw(s, F_JA) - (W - 12))
        self.long = self.max_off > 0
        self.pos = 0
        self.dir = 1
        self.frame_at = now
        self.hold_until = time.ticks_add(now, self.LYRIC_HOLD_MS)
        self.step = self._lyric_step()

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

        # Start the slide a moment before the next line so it lands on time
        # instead of reacting to a hub update that arrives late.
        if self.lyrics and not self.slide and self.buf[1]:
            na = (self.app.hub.ly or {}).get("next_at")
            if na and na != self.armed_for:
                remain = int(na) - self._pos()
                if 0 < remain <= self.SLIDE_MS:
                    self.armed_for = na
                    self._start_slide(now)

        if self.slide:
            el = time.ticks_diff(now, self.slide_at)
            if el >= self.SLIDE_MS:
                self._land(now)
            else:
                t = el / self.SLIDE_MS
                # ease out: the line settles instead of stopping dead
                self.slide = int(self.SLOT_H * (1.0 - (1.0 - t) * (1.0 - t)))
                if self.slide < 1:
                    self.slide = 1
                self.dirty_lines = True
            self.animating = True
            return True

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
