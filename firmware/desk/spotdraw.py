"""Spotify page renderer: song rows, sliding lyrics and progress."""
from desk.ui import ACCENT, AMBER, BLACK, F_JA, F_SM, F_TINY, GREEN, LCD, LINE, MUTED, RED, W, WHITE, bar, ctext, dots, header, mmss, rtext, t1, tl, tw


class SpotDraw:
    FRAME_MS = 33
    LYRIC_FRAME_MS = 15      # fast lyric scrolling frame rate
    LYRIC_STEP = 4           # 4 px/frame = ~266 px/s
    LYRIC_STEP_MAX = 8
    LYRIC_HOLD_MS = 300      # pause at each end of a lyric line
    HOLD_MS = 1000
    BAND_Y, BAND_H = 21, 63
    PROG_Y, PROG_H = 84, 34

    # Lyrics are a teleprompter: three rows are fully visible, a long line is
    # wrapped into more rows, and the block slides up by the rows the finished
    # line occupied.  EFontJA24 drawn at 0.8 gives 19 px glyphs in 21 px rows.
    SLOT_Y = 22
    SLOT_H = 21
    TXT = 0.8
    SLIDE_MS = 260

    def _scroll(self, s, f, y, c, center=True):
        w = tw(s, f)
        if w <= W - 12:
            x = (W - w) // 2 if center else 6
            t1(s, f, x, y, c)
            return
        off = min(self.pos, w - (W - 12))
        t1(s, f, 6 - off, y, c)

    def draw(self):
        if self.dirty_full:
            self.dirty_full = False
            self.dirty_lines = False
            self.dirty_prog = False
            self._draw_full()
            return
        if self.dirty_lines:
            self.dirty_lines = False
            self._draw_lines()
        if self.dirty_prog:
            self.dirty_prog = False
            self._draw_prog()

    def _chrome(self):
        """Header and dots.  The slide can push text into the top strip."""
        app = self.app
        d = app.hub.sp or {}
        header("SPOTIFY", app.idx, len(app.page_list()),
               GREEN if d.get("is_playing") else ACCENT)
        dots(len(app.page_list()), app.idx)

    def _draw_full(self):
        app = self.app
        d = app.hub.sp
        LCD.fillScreen(BLACK)
        if not d or isinstance(self.sig, str):
            self._chrome()
            ctext("SPOTIFY", F_SM, 34, MUTED)
            ctext(self.sig if isinstance(self.sig, str)
                  else "HUB OFFLINE", F_TINY, 54, RED)
            ctext("hold 3s: back", F_TINY, 104, MUTED)
            return
        st = d.get("status")
        if st not in ("success", "not_playing", "paused"):
            self._chrome()
            ctext("SPOTIFY", F_SM, 34, MUTED)
            ctext((d.get("message") or st or "ERROR")[:22], F_TINY, 54, RED)
            ctext("hold 3s: back", F_TINY, 104, MUTED)
            return
        self._draw_lines()
        self._draw_prog()
        self._chrome()

    def _draw_lines(self):
        LCD.fillRect(0, self.BAND_Y, W, self.BAND_H, BLACK)
        d = self.app.hub.sp
        if not d:
            return
        if self.lyrics:
            self._draw_lyrics()
            # The block slides through the band edges, so paint the strips it
            # ran over: header on top, progress bar below.
            LCD.fillRect(0, 0, W, self.BAND_Y, BLACK)
            LCD.fillRect(0, self.PROG_Y, W, self.PROG_H, BLACK)
            self._chrome()
            self._draw_prog()
            return
        song, artist, dev = self._lines(d)
        self._scroll(song[0], song[1], 22, WHITE)
        self._scroll(artist[0], artist[1], 47, MUTED)
        if tw(dev[0], dev[1]) > W - 12:
            self._scroll(dev[0], dev[1], 73, ACCENT)
        else:
            t1("ON", F_TINY, 6, 73, LINE)
            t1(dev[0][3:][:16], F_TINY, 24, 73, ACCENT)

    def _draw_lyrics(self):
        d = self.app.hub.ly or {}
        st = d.get("status")
        if st != "success":
            if st in (None, "loading"):
                ctext("FETCHING...", F_SM, 42, MUTED)
            elif st == "not_found":
                ctext("NO LYRICS", F_SM, 38, MUTED)
                ctext("hold 1s to hide", F_TINY, 58, LINE)
            else:
                ctext("LYRICS ERROR", F_SM, 38, RED)
                ctext((d.get("message") or "")[:18], F_TINY, 58, MUTED)
            return
        disp = getattr(self, "disp", ())
        if not disp:
            ctext("...", F_JA, 34, LINE)
            return
        rows = getattr(self, "cur_rows", 1)
        for k in range(6):
            yy = self.SLOT_Y - self.slide + k * self.SLOT_H
            if yy > self.PROG_Y:
                break
            if k < len(disp) and yy + self.SLOT_H > self.BAND_Y:
                # Rows of the line being sung are bright; the rest are hints.
                self._lyric_row(disp[k], yy, WHITE if k < rows else MUTED)

    def _lyric_row(self, s, y, c):
        # Font first, then the size: setFont can reset the scale on some
        # builds, so the order matters.
        LCD.setFont(F_JA)
        try:
            LCD.setTextSize(self.TXT)
        except Exception:
            pass
        LCD.setTextColor(c, BLACK)
        LCD.drawString(s, 6, int(y))

    def _draw_prog(self):
        d = self.app.hub.sp
        if not d:
            return
        LCD.fillRect(0, self.PROG_Y, W, self.PROG_H, BLACK)
        playing = bool(d.get("is_playing"))
        dur = int(d.get("duration_ms") or 0)
        pos = self._pos()
        pct = (pos * 100 // dur) if dur else 0
        bar(6, 85, 116, 5, pct, GREEN if playing else LINE)
        tl(mmss(pos), F_TINY, 6, 92, MUTED)
        rtext(mmss(dur), F_TINY, 122, 92, MUTED)
        ctext("PLAYING" if playing else "PAUSED", F_TINY, 104,
              GREEN if playing else AMBER)
