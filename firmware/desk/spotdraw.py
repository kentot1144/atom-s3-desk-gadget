"""Spotify page renderer: song rows, synced lyrics and progress."""
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

    def _draw_full(self):
        app = self.app
        LCD.fillScreen(BLACK)
        d = app.hub.sp
        playing = bool(d and d.get("is_playing"))
        header("SPOTIFY", app.idx, len(app.page_list()),
               GREEN if playing else ACCENT)
        dots(len(app.page_list()), app.idx)
        if not d or isinstance(self.sig, str):
            ctext("SPOTIFY", F_SM, 34, MUTED)
            ctext(self.sig if isinstance(self.sig, str)
                  else "HUB OFFLINE", F_TINY, 54, RED)
            ctext("hold 3s: back", F_TINY, 104, MUTED)
            return
        st = d.get("status")
        if st not in ("success", "not_playing", "paused"):
            ctext("SPOTIFY", F_SM, 34, MUTED)
            ctext((d.get("message") or st or "ERROR")[:22], F_TINY, 54, RED)
            ctext("hold 3s: back", F_TINY, 104, MUTED)
            return
        self._draw_lines()
        self._draw_prog()

    def _draw_lines(self):
        LCD.fillRect(0, self.BAND_Y, W, self.BAND_H, BLACK)
        d = self.app.hub.sp
        if not d:
            return
        if self.lyrics:
            self._draw_lyrics()
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
        LCD.fillRect(0, self.BAND_Y, W, self.BAND_H, BLACK)
        d = self.app.hub.ly or {}
        st = d.get("status")
        if st == "success":
            rows = self.rows or [""]
            nxt = (d.get("next") or "").strip()
            if not self.lyr_scroll and len(rows) <= 1:
                t1("LYRICS", F_TINY, 6, 22, LINE)
            y = self.row_y
            center = len(rows) == 1
            if not rows[0]:
                ctext("...", F_JA, y, LINE)
            else:
                for row in rows:
                    if self.lyr_scroll:
                        self._scroll(row, self.lyr_font, y, WHITE, False)
                    elif center:
                        self._scroll(row, self.lyr_font, y, WHITE, True)
                    else:
                        t1(row, self.lyr_font, 6, y, WHITE)
                    y += self.row_dy
            if nxt:
                if tw(nxt, F_TINY) <= W - 12:
                    ctext(nxt, F_TINY, self.next_y, ACCENT)
                else:
                    t1(nxt, F_TINY, 6, self.next_y, ACCENT)
        elif st in (None, "loading"):
            ctext("FETCHING...", F_SM, 42, MUTED)
        elif st == "not_found":
            ctext("NO LYRICS", F_SM, 38, MUTED)
            ctext("hold 1s to hide", F_TINY, 58, LINE)
        else:
            ctext("LYRICS ERROR", F_SM, 38, RED)
            ctext((d.get("message") or "")[:18], F_TINY, 58, MUTED)

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
