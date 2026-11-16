"""Spotify page state machine: horizontal scroll, lyric slide-up and timing.

Lyrics behave like a small teleprompter.  The line being sung is on top and the
coming lines are below; a line that is wider than the panel is wrapped into
extra rows instead of scrolling sideways, and when the line changes the block
slides up by however many rows the finished line used.
"""
import time

from desk.ui import F_JA, F_TINY, LCD, W, tw
from desk.spotdraw import SpotDraw


def _atoms(s):
    """Split a line into units that a row break may fall between.

    Latin words stay whole so they are never cut mid-word; CJK characters
    each stand alone, which is where Japanese lyrics naturally break.
    """
    out = []
    word = ""
    for ch in s:
        if ch == " ":
            if word:
                out.append(word)
                word = ""
            out.append(" ")
        elif ord(ch) > 0x2FF:
            if word:
                out.append(word)
                word = ""
            out.append(ch)
        else:
            word += ch
    if word:
        out.append(word)
    return out


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
        self.w_cur = ()             # wrapped rows of each buffered line
        self.w_next = ()
        self.w_n2 = ()
        self.disp = ()              # the rows on screen, top to bottom
        self.cur_rows = 1           # rows the line being sung occupies
        self.txt = self.TXT         # chosen lyric scale for the current line
        self.slot_h = self.SLOT_H   # matching row pitch
        self.slide = 0              # px the lyric block has slid up
        self.slide_px = self.SLOT_H
        self.slide_at = 0
        self.armed_for = None       # next_at value already animated for
        self.sync_at = 0            # next direct lyric alignment with Spotify

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
        self._build_disp()
        self.slide = 0
        self.armed_for = None
        # Align with the live Spotify position shortly after entering (and
        # every few seconds after that) instead of trusting the first payload.
        self.sync_at = time.ticks_add(time.ticks_ms(), 2000)

    def soft_hold(self):
        self.lyrics = not self.lyrics
        self.lyr_key = None
        self.buf = ("", "", "")
        self.line = ""
        self._build_disp()
        self.slide = 0
        self.armed_for = None
        self.long = False
        self.pos = 0
        self.dir = 1
        self.dirty_full = True
        if not self.lyrics:
            # Back to the song rows: re-measure them for sideways scrolling.
            self._plan(self.app.hub.sp or {})

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
    def _sync_tick(self, now):
        """Ask the hub for the line at our exact playback position.

        The state feed only changes when a line changes and is built once a
        second, so it can lag a little.  Every few seconds we re-read the
        current line with our own interpolated position, which keeps the
        teleprompter aligned with what Spotify is actually singing.
        """
        if self.slide:
            return
        if time.ticks_diff(now, self.sync_at) < 0:
            return
        self.sync_at = time.ticks_add(now, self.SYNC_MS)
        hub = self.app.hub
        if not hub.fresh(15000):
            return
        get = getattr(hub, "get", None)
        if get is None:
            return
        try:
            ly = get("/lyrics?pos=%d" % int(self._pos()),
                     timeout=1.2, max_size=3072)
        except Exception:
            return
        if not isinstance(ly, dict) or not ly.get("status"):
            return
        hub.ly = ly
        self._sync_lyrics(now, ly, force=True)

    def _sync_lyrics(self, now, ly=None, force=False):
        if ly is None:
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
        if key == self.lyr_key and not force:
            # Same sung line; still take a fresher lookahead so the row that
            # enters from below is already filled in.
            if (not self.slide and new[1] == self.buf[1]
                    and new[2] and new[2] != self.buf[2]):
                self.buf = (self.buf[0], self.buf[1], new[2])
                self._build_disp()
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
            self._build_disp()
            self.dirty_lines = True
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
        self._build_disp()
        # A long line can use two to four rows; the block moves by all of them
        # so the next line lands at the top.
        self.slide_px = min(self.MAX_ROWS, self.cur_rows) * self.slot_h
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
        self._build_disp()
        self.long = False
        self.max_off = 0
        self.pos = 0
        self.dir = 1
        self.frame_at = now
        self.hold_until = time.ticks_add(now, self.LYRIC_HOLD_MS)
        self.step = 1

    # -- lyric wrapping ----------------------------------------------
    def _wrap(self, s, limit=None, size=None):
        """Rows for one lyric line, measured at the scale that will draw it."""
        if limit is None:
            limit = self.MAX_ROWS
        if size is None:
            size = getattr(self, "txt", self.TXT)
        s = (s or "").strip()
        if not s:
            return ()
        try:
            LCD.setTextSize(size)
        except Exception:
            pass
        maxw = W - 12
        rows = []
        row = ""
        try:
            for atom in _atoms(s):
                if not row and atom == " ":
                    continue
                if tw(row + atom, F_JA) <= maxw:
                    row += atom
                    continue
                if row.strip():
                    rows.append(row.strip())
                    row = ""
                    if len(rows) >= limit:
                        return tuple(rows)
                if atom == " ":
                    continue
                # A single word wider than the panel: split it by characters.
                for ch in atom:
                    if row and tw(row + ch, F_JA) > maxw:
                        rows.append(row)
                        row = ""
                        if len(rows) >= limit:
                            return tuple(rows)
                    row += ch
            if row.strip() and len(rows) < limit:
                rows.append(row.strip())
        finally:
            try:
                LCD.setTextSize(1)
            except Exception:
                pass
        return tuple(rows[:limit])

    def _build_disp(self):
        # Wrap at the normal scale first.  If that line plus one preview row
        # would need a fourth row, shrink the whole block so four rows fit.
        base = self._wrap(self.buf[0], self.MAX_ROWS, self.TXT)
        need = len(base) + (1 if (self.buf[1] or "").strip() else 0)
        if need > 3:
            self.txt, self.slot_h = self.TXT_SM, self.SLOT_H_SM
        else:
            self.txt, self.slot_h = self.TXT, self.SLOT_H
        self.w_cur = self._wrap(self.buf[0], self.MAX_ROWS, self.txt)
        self.w_next = self._wrap(self.buf[1], self.MAX_ROWS, self.txt)
        self.w_n2 = self._wrap(self.buf[2], self.MAX_ROWS, self.txt)
        self.disp = self.w_cur + self.w_next + self.w_n2
        self.cur_rows = len(self.w_cur) or 1

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
        # Re-align with the live playback position every few seconds.
        if self.lyrics:
            self._sync_tick(now)

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
                # ease out: the block settles instead of stopping dead
                self.slide = int(self.slide_px
                                 * (1.0 - (1.0 - t) * (1.0 - t)))
                if self.slide < 1:
                    self.slide = 1
                self.dirty_lines = True
            self.animating = True
            return True

        # Lyrics never scroll sideways: rows are wrapped instead.
        if not self.long or self.lyrics:
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
