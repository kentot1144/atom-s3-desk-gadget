"""Render every firmware page on the PC, against a fake M5 stack.

The gadget only draws the pages you actually page to, so a broken page can sit
undetected for days.  This walks the real renderers with representative hub
data and fails if any of them raises.

    python tools/render_test.py
    python tools/render_test.py state.json     # captured /state?only=ui,sp,ly

`state.json` is what the hub returns for `/state?only=ui,sp,ly&k=...`; without
it a synthetic state is used.
"""
import gc
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "firmware")

# ---------------------------------------------------------------- fake board
time.ticks_ms = lambda: int(time.monotonic() * 1000) & 0x3FFFFFFF
time.ticks_add = lambda a, b: (a + b) & 0x3FFFFFFF
time.ticks_diff = lambda a, b: ((a - b + 0x20000000) & 0x3FFFFFFF) - 0x20000000
time.sleep_ms = lambda ms: None
gc.mem_free = lambda: 100352

DRAWS = []
COLORS = []
TEXTS = []


class Font:
    def __init__(self, name, w):
        self.name = name
        self.w = w


FONTS = types.SimpleNamespace(
    DejaVu9=Font("9", 4), DejaVu12=Font("12", 6), DejaVu18=Font("18", 9),
    DejaVu40=Font("40", 20), EFontJA24=Font("ja", 12))


class Display:
    font = FONTS.DejaVu12
    size = 1.0

    def fillScreen(self, *a): DRAWS.append("fillScreen")
    def fillRect(self, *a): DRAWS.append("fillRect")
    def fillCircle(self, *a): DRAWS.append("fillCircle")
    def fillTriangle(self, *a): DRAWS.append("fillTriangle")
    def fillArc(self, *a): DRAWS.append("fillArc")
    def drawCircle(self, *a): DRAWS.append("drawCircle")
    def setFont(self, f): self.font = f
    def setTextColor(self, c, *a): COLORS.append(c)
    def setCursor(self, *a): pass
    def setTextSize(self, v): self.size = v
    def getTextSize(self): return self.size
    def setBrightness(self, v): pass
    def drawString(self, s, *a):
        DRAWS.append("text")
        TEXTS.append(s)

    def print(self, s):
        if not isinstance(s, str):
            raise TypeError("display.print got %r" % (s,))
        DRAWS.append("text")
        TEXTS.append(s)

    def textWidth(self, s, f):
        return int(len(s) * f.w * self.size)


sys.modules["M5"] = types.SimpleNamespace(
    Display=Display(),
    Lcd=types.SimpleNamespace(FONTS=FONTS),
    Imu=types.SimpleNamespace(getAccel=lambda: (0.1, 0.0, -1.0)),
    begin=lambda: None,
    update=lambda: None,
)

machine = types.ModuleType("machine")


class Pin:
    OUT = 1

    def __init__(self, *a, **k): self.v = 0

    def value(self, v=None):
        if v is None:
            return self.v
        self.v = v


class ADC:
    ATTN_11DB = 3

    def __init__(self, *a, **k): pass
    def read(self): return 2048
    def atten(self, *a): pass


machine.Pin = Pin
machine.ADC = ADC
sys.modules["machine"] = machine


class FakeWlan:
    def active(self, *a): return True
    def config(self, **k): pass
    def isconnected(self): return True
    def ifconfig(self, *a): return ("192.168.0.50", "255.255.255.0",
                                    "192.168.0.1", "192.168.0.1")
    def status(self, *a): return -55 if a else 1010
    def connect(self, *a): pass
    def disconnect(self): pass


# ---------------------------------------------------------------- fake state
STATE = {
    "ui": {
        "al": 0,
        "norm": [
            {"k": "clock", "temp": 21, "cal": "IN 2d 9h"},
            {"k": "msg", "t": "WEATHER", "m": "CLEAR", "m2": "21C"},
            {"k": "focus", "t": "FOCUS"},
            {"k": "rows", "t": "PC", "hc": 6, "rows": [
                {"l": "CPU", "v": "16%", "p": 16, "pc": 8, "vc": 4},
                {"l": "MEM", "v": "76%", "p": 76, "pc": 9, "vc": 4},
                {"l": "GPU", "v": "--", "vc": 3}],
             "foot": "UPTIME 15d 14h 2m"},
            {"k": "rows", "t": "SERVERS", "hc": 8, "auto": 1, "rows": [
                {"l": "MAIN", "v": "UP 40ms", "vc": 8},
                {"l": "VNC", "v": "UP 120ms", "vc": 8},
                {"l": "VNC SUB", "v": "UP 90ms", "vc": 8},
                {"l": "CRAFTY", "v": "UP 210ms", "vc": 9},
                {"l": "PROXY", "v": "UP 60ms", "vc": 8},
                {"l": "VELOCITY", "v": "4/20", "vc": 8},
                {"l": "MC HUB", "v": "0/10", "vc": 8},
                {"l": "SMP", "v": "2/30", "vc": 8},
                {"l": "LIMBO", "v": "DOWN", "vc": 12,
                 "s": "30000"}], "foot": "8/9 UP", "fc": 9},
            {"k": "notice", "t": "NOTICES", "rows": [
                {"l": "build finished", "s": "12m ago", "d": 8},
                {"l": "disk almost full", "s": "1h ago", "d": 12}],
             "more": 4},
            {"k": "restart", "t": "RESTART"},
            {"k": "face", "t": "FACE"},
            {"k": "status", "t": "STATUS", "up": 2, "tot": 3,
             "last": int(time.time()) - 3600},
            {"k": "big", "t": "TEMP", "v": "21", "cond": "CLEAR",
             "sub": "feels like 20"},
        ],
        "spot": [{"k": "spotify", "t": "SPOTIFY"}],
    },
    "sp": {"status": "success", "message": "", "is_playing": True,
           "song": "Way Back Home", "artist": "SHAUN",
           "device": "DESKTOP-PC", "progress_ms": 52000,
           "duration_ms": 193000},
    "ly": {"status": "success", "synced": True,
           "line": "Meomchun sigan sok jamdeun neoreul chajaga",
           "next": "Amuri magado gyeolguk neoui gyeotin geol",
           "next2": "Iksukan gireul ttara geotdaga bomyeon",
           "line_at": 520, "next_at": 4920},
}


def load_state():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            return json.load(f)
    return STATE


def main():
    sys.path.insert(0, ROOT)
    import desk.app as dapp
    from desk.ui import ACCENT, F_JA, LCD, MUTED, SKY, W, WHITE, tw

    cfg = {"location": {"tz": 9},
           "display": {"auto": False, "brightness": 120},
           "pomodoro": {"focus": 25, "break": 5},
           "hub": {"urls": ["http://127.0.0.1:8765"], "token": ""},
           "wifi": [{"ssid": "test", "password": ""}]}

    app = dapp.App(FakeWlan(), cfg)
    state = load_state()
    app.ui = state.get("ui") or {}
    app.hub.ui = app.ui
    app.hub.sp = state.get("sp") or {}
    app.hub.ly = state.get("ly") or {}
    app.hub.ok_at = time.ticks_ms()

    # Tests must never touch the network: answer lyric syncs from fake state.
    def default_get(path, **kw):
        if path.startswith("/lyrics"):
            return app.hub.ly
        return None
    app.hub.get = default_get

    failures = 0
    pages = app.page_list()
    print("pages:", [p.get("k") for p in pages])
    for i, page in enumerate(pages):
        app.idx = i
        del DRAWS[:]
        try:
            app.draw(page)
            print("ok   %-9s %-14s %3d draws"
                  % (page.get("k"), page.get("t") or "", len(DRAWS)))
        except Exception as e:
            failures += 1
            print("FAIL %-9s %r" % (page.get("k"), e))

    for name in ("draw_status", "draw_sys"):
        del DRAWS[:]
        try:
            getattr(app, name)({"k": "sys", "t": "DEVICE", "up": 2, "tot": 3,
                                "last": int(time.time()) - 3600})
            print("ok   %-9s %-14s %3d draws" % (name, "", len(DRAWS)))
        except Exception as e:
            failures += 1
            print("FAIL %-9s %r" % (name, e))

    # Status sub-page 2: the bare Spotify state, playing and paused.
    saved_sp = app.hub.sp
    app.st_page = 1
    for playing, want in ((True, "PLAYING"), (False, "PAUSED")):
        app.hub.sp = dict(saved_sp, is_playing=playing)
        del TEXTS[:]
        try:
            app.draw_status({"k": "status", "t": "STATUS"})
            missing = [w for w in (want, "STATUS 2/2", "Way Back Home")
                       if w not in TEXTS]
            if missing:
                failures += 1
                print("FAIL status_sp %s missing %s" % (want, missing))
            else:
                print("ok   status_sp   %-8s %3d draws"
                      % (want, len(DRAWS)))
        except Exception as e:
            failures += 1
            print("FAIL status_sp %r" % (e,))
    app.hub.sp = saved_sp
    app.st_page = 0

    # a leftover lyric scale must never shrink normal page text
    LCD.setTextSize(0.8)
    app.idx = 0
    try:
        app.draw(pages[0])
        print("ok   scale     reset to %s" % LCD.size)
        if LCD.size != 1.0:
            failures += 1
            print("FAIL scale     normal page kept size %s" % LCD.size)
    except Exception as e:
        failures += 1
        print("FAIL scale     %r" % (e,))
    LCD.setTextSize(1)

    for kind in ("mc_join", "mc_leave", "mc_move", "other"):
        app.banner = {"kind": kind, "ts": int(time.time()),
                      "title": "A fairly long notice title here",
                      "body": "and a body that has to wrap across lines"}
        del DRAWS[:]
        try:
            app.draw_banner()
            print("ok   banner    %-14s %3d draws" % (kind, len(DRAWS)))
        except Exception as e:
            failures += 1
            print("FAIL banner    %-14s %r" % (kind, e))
    app.banner = None

    app.mode = 1
    app.idx = 0
    base = dict(app.hub.sp)
    for label, patch in (("playing", {"is_playing": True}),
                         ("paused", {"is_playing": False}),
                         ("empty", {"song": "", "artist": "",
                                    "is_playing": True})):
        app.hub.sp = dict(base, **patch)
        app.spot.enter()
        for lyrics in (False, True):
            app.spot.lyrics = lyrics
            app.spot.dirty_full = True
            del DRAWS[:]
            try:
                app.spot.refresh(time.ticks_ms())
                app.spot.draw()
                print("ok   spotify   %-14s %3d draws"
                      % ("%s lyrics=%d" % (label, lyrics), len(DRAWS)))
                if lyrics and LCD.size != 1.0:
                    failures += 1
                    print("FAIL spotify   lyric size leaked: %s"
                          % LCD.size)
            except Exception as e:
                failures += 1
                print("FAIL spotify   %-14s %r"
                      % ("%s lyrics=%d" % (label, lyrics), e))

    # A paused payload must actually draw PAUSED, and its progress must stop
    # instead of creeping forward.
    app.hub.sp = dict(base, is_playing=False)
    app.hub.sp_pos = 52000
    app.hub.sp_at = time.ticks_add(time.ticks_ms(), -5000)
    app.hub.ok_at = time.ticks_ms()
    app.spot.enter()
    app.spot.lyrics = False
    app.spot.dirty_full = True
    del TEXTS[:]
    app.spot.refresh(time.ticks_ms())
    app.spot.draw()
    paused_pos = app.hub.progress()
    if "PAUSED" not in TEXTS or "PLAYING" in TEXTS:
        failures += 1
        print("FAIL spotify   paused did not draw PAUSED")
    else:
        print("ok   spotify   paused draws PAUSED")
    if paused_pos != 52000:
        failures += 1
        print("FAIL spotify   paused progress moved to %d" % paused_pos)
    else:
        print("ok   spotify   paused progress frozen")

    # A stale hub feed must not keep showing the last playing state.
    app.hub.sp = dict(base, is_playing=True)
    app.hub.ok_at = time.ticks_add(time.ticks_ms(), -15000)
    app.spot.enter()
    app.spot.lyrics = False
    app.spot.dirty_full = True
    del TEXTS[:]
    app.spot.refresh(time.ticks_ms())
    app.spot.draw()
    if "HUB OFFLINE" not in TEXTS or "PLAYING" in TEXTS:
        failures += 1
        print("FAIL spotify   stale hub still showed playing")
    else:
        print("ok   spotify   stale hub shows offline")
    app.hub.sp = dict(base)
    app.hub.ok_at = time.ticks_ms()

    # Defense in depth: even if an old hub keeps is_playing=True, the device
    # must notice a frozen position and stop drawing PLAYING.
    now = time.ticks_ms()
    frozen = dict(base, is_playing=True, progress_ms=52000)
    app.hub.sp = frozen
    app.hub.sp_pos = 52000
    app.hub.sp_at = now
    app.hub.sp_stalled = False
    app.hub.sp_playing = True
    app.hub.sp_key = (frozen.get("song"), frozen.get("artist"),
                      int(frozen.get("duration_ms") or 0))
    app.hub.sp_hist = [(time.ticks_add(now, -10000), 52000),
                       (time.ticks_add(now, -4000), 52000)]
    app.hub._watch_sp()
    app.spot.enter()
    app.spot.lyrics = False
    app.spot.dirty_full = True
    del TEXTS[:]
    app.spot.refresh(time.ticks_ms())
    app.spot.draw()
    if app.hub.playing() or "PLAYING" in TEXTS or "PAUSED" not in TEXTS:
        failures += 1
        print("FAIL spotify   frozen device feed still playing")
    else:
        print("ok   spotify   frozen feed draws PAUSED")

    # A frozen device feed must clear within a few seconds of real movement.
    now = time.ticks_ms()
    app.hub.sp_stalled = True
    app.hub.sp_playing = True
    app.hub.sp_pos = 53500
    app.hub.sp_hist = [(time.ticks_add(now, -3000), 51500)]
    app.hub._watch_sp()
    if app.hub.sp_stalled or not app.hub.playing():
        failures += 1
        print("FAIL spotify   device stall did not clear on resume")
    else:
        print("ok   spotify   device stall clears on resume")
    app.hub.sp = dict(base)
    app.hub.sp_stalled = False
    app.hub.sp_hist = []
    app.hub.sp_playing = True
    app.hub.ok_at = time.ticks_ms()

    app.spot.lyrics = True
    long_lines = (
        "This is an extremely long lyric line that cannot possibly fit on a "
        "128 pixel wide display in one row",
        "これは日本語のとても長い歌詞の行で、128ピクセルの画面には収まりません",
    )
    for line in long_lines:
        rows = app.spot._wrap(line, app.spot.MAX_ROWS, app.spot.TXT)
        app.spot.txt = app.spot.TXT
        app.spot.slot_h = app.spot.SLOT_H
        app.spot.buf = (line, "the line after it", "and one more after that")
        app.spot.line = line
        app.spot._plan_lyrics(time.ticks_ms())
        app.spot.dirty_full = True
        del DRAWS[:]
        try:
            app.spot.draw()
            print("ok   spotify   long lyric rows=%d scale=%s %3d draws"
                  % (len(rows), app.spot.txt, len(DRAWS)))
            if LCD.size != 1.0:
                failures += 1
                print("FAIL spotify   lyric size leaked: %s" % LCD.size)
        except Exception as e:
            failures += 1
            print("FAIL spotify   long lyric %r" % e)
        if not rows or len(rows) > app.spot.MAX_ROWS:
            failures += 1
            print("FAIL wrap      rows=%r" % (rows,))
            continue
        for r in rows:
            LCD.setTextSize(app.spot.TXT)
            w = tw(r, F_JA)
            LCD.setTextSize(1)
            if w > W - 12:
                failures += 1
                print("FAIL wrap      row too wide %r (%d px)" % (r, w))
        if " " in line:
            words = line.split()
            pos = 0
            for r in rows:
                rw = r.split()
                if words[pos:pos + len(rw)] != rw:
                    failures += 1
                    print("FAIL wrap      ascii words cut: %r" % (rows,))
                    break
                pos += len(rw)
        elif len(rows) < 2:
            failures += 1
            print("FAIL wrap      japanese line was not split: %r" % (rows,))

    # a line that needs a fourth row shrinks the block instead of clipping it
    four = "one two three four five six seven"
    base_rows = app.spot._wrap(four, app.spot.MAX_ROWS, app.spot.TXT)
    app.spot.buf = (four, "next line", "")
    app.spot._build_disp()
    print("ok   spotify   four-row base=%d scale=%s rows=%d pitch=%d"
          % (len(base_rows), app.spot.txt, app.spot.cur_rows,
             app.spot.slot_h))
    if len(base_rows) < 3:
        failures += 1
        print("FAIL wrap      four-row fixture only wrapped to %d"
              % len(base_rows))
    if app.spot.txt != app.spot.TXT_SM:
        failures += 1
        print("FAIL scale     four rows did not shrink text (scale %s)"
              % app.spot.txt)
    if app.spot.cur_rows > app.spot.MAX_ROWS:
        failures += 1
        print("FAIL scale     %d rows still exceed the band" % app.spot.cur_rows)
    if app.spot.cur_rows * app.spot.slot_h > app.spot.BAND_H:
        failures += 1
        print("FAIL scale     %d rows at %d px do not fit %d px"
              % (app.spot.cur_rows, app.spot.slot_h, app.spot.BAND_H))
    del DRAWS[:]
    try:
        app.spot.slide = 0
        app.spot.dirty_full = True
        app.spot.draw()
        print("ok   spotify   four-row draw %3d draws" % len(DRAWS))
        if LCD.size != 1.0:
            failures += 1
            print("FAIL spotify   four-row size leaked: %s" % LCD.size)
    except Exception as e:
        failures += 1
        print("FAIL spotify   four-row draw %r" % e)

    # a line longer than four rows pages through every row instead of clipping
    huge = (long_lines[0] + " and it keeps going with even more words to "
            "force a fifth and a sixth row onto the little display")
    app.spot.buf = (huge, "after it", "")
    app.spot.page_line = ""
    app.spot.line_page = 0
    app.spot._build_disp()
    total = len(app.spot._wrap(huge, app.spot.MAX_WRAP, app.spot.txt))
    print("ok   spotify   page rows=%d pages=%d shown=%d"
          % (app.spot.line_rows, app.spot.line_pages, len(app.spot.disp)))
    if app.spot.line_rows <= app.spot.MAX_ROWS:
        failures += 1
        print("FAIL page      fixture did not exceed four rows")
    if app.spot.line_rows != total or app.spot.line_pages < 2:
        failures += 1
        print("FAIL page      long line was not paged")
    if (len(app.spot.disp) > app.spot.MAX_ROWS
            or app.spot.cur_rows > app.spot.MAX_ROWS):
        failures += 1
        print("FAIL page      a page is taller than the band")
    seen_rows = []
    for pi in range(app.spot.line_pages):
        app.spot.line_page = pi
        app.spot._build_disp()
        page_rows = min(app.spot.MAX_ROWS,
                        app.spot.line_rows - pi * app.spot.MAX_ROWS)
        seen_rows += list(app.spot.disp[:page_rows])
        if len(app.spot.disp) > app.spot.MAX_ROWS:
            failures += 1
            print("FAIL page      page %d has %d rows"
                  % (pi, len(app.spot.disp)))
    if tuple(seen_rows) != app.spot.w_cur:
        failures += 1
        print("FAIL page      only %d of %d rows are reachable"
              % (len(seen_rows), app.spot.line_rows))

    # the page advances on its own while the same line is still being sung
    app.spot.buf = (huge, "after it", "")
    app.spot.page_line = huge
    app.spot.line_page = 0
    app.spot._build_disp()
    app.hub.ok_at = time.ticks_ms()
    app.hub.sp = dict(base, is_playing=True)
    app.hub.ly = {"status": "success", "line": huge, "next": "after it",
                  "next2": "", "line_at": 0, "next_at": 60000}
    app.spot.lyrics = True
    app.spot.lyr_key = ("success", huge)
    app.spot.sig = (base["song"], base["artist"], base["device"], True)
    app.spot.sync_at = time.ticks_add(time.ticks_ms(), 100000)
    app.spot.page_at = time.ticks_add(time.ticks_ms(), -10000)
    app.spot.slide = 0
    app.spot.tick(time.ticks_ms())
    print("ok   spotify   page advanced=%d/%d"
          % (app.spot.line_page + 1, app.spot.line_pages))
    if app.spot.line_page < 1:
        failures += 1
        print("FAIL page      long line did not advance")

    # lyric slide-up: the block is drawn at every offset of the animation
    app.spot.buf = ("now the line", "the coming line", "the one after")
    app.spot.line = "now the line"
    app.spot.lyrics = True
    app.spot._plan_lyrics(time.ticks_ms())
    for off in (0, 6, 13, 20):
        app.spot.slide = off
        app.spot.dirty_full = True
        del DRAWS[:]
        try:
            app.spot.draw()
            print("ok   spotify   %-14s %3d draws"
                  % ("slide px=%d" % off, len(DRAWS)))
            if LCD.size != 1.0:
                failures += 1
                print("FAIL spotify   lyric size leaked: %s" % LCD.size)
        except Exception as e:
            failures += 1
            print("FAIL spotify   slide px=%d %r" % (off, e))
    app.spot.slide = 0

    # slide timeline: the block must shift exactly one line and land at 0 px
    app.hub.ly = {"status": "success", "synced": True, "line": "A",
                  "next": "B", "next2": "C", "line_at": 0,
                  "next_at": 60000}
    app.spot.lyrics = True
    app.spot.buf = ("A", "B", "C")
    app.spot.lyr_key = ("success", "A")
    app.spot.slide = 0
    now = time.ticks_ms()
    if not app.spot._start_slide(now):
        failures += 1
        print("FAIL spotify   slide did not start")
    else:
        if app.spot.slide_px != app.spot.SLOT_H:
            failures += 1
            print("FAIL spotify   one-row slide_px=%d" % app.spot.slide_px)
        app.spot.tick(now + 120)
        mid = app.spot.slide
        app.spot.tick(now + app.spot.SLIDE_MS + 10)
        print("ok   spotify   slide mid=%-2d land buf=%r slide=%d"
              % (mid, app.spot.buf, app.spot.slide))
        if not (0 < mid < app.spot.SLOT_H):
            failures += 1
            print("FAIL spotify   slide mid=%d not between 0 and %d"
                  % (mid, app.spot.SLOT_H))
        if app.spot.buf[:2] != ("B", "C") or app.spot.slide:
            failures += 1
            print("FAIL spotify   slide did not land on the next line")

    # a wrapped line moves the block by all of its rows, at its own pitch
    wrapped = long_lines[1]
    app.spot.buf = (wrapped, "B", "C")
    app.spot.slide = 0
    app.spot._start_slide(time.ticks_ms())
    want = min(app.spot.MAX_ROWS, app.spot.cur_rows) * app.spot.slot_h
    print("ok   spotify   wrapped slide_px=%d rows=%d pitch=%d"
          % (app.spot.slide_px, app.spot.cur_rows, app.spot.slot_h))
    if app.spot.slide_px != want:
        failures += 1
        print("FAIL spotify   wrapped slide_px=%d want %d"
              % (app.spot.slide_px, want))
    app.spot.slide = 0

    # the coming lines are sky blue, the sung line stays white
    app.hub.ly = {"status": "success", "line": "A", "next": "B",
                  "next2": "", "line_at": 0, "next_at": 60000}
    app.spot.buf = ("A", "B", "")
    app.spot._build_disp()
    app.spot.slide = 0
    del COLORS[:]
    try:
        app.spot._draw_lyrics()
        print("ok   spotify   lyric colors %r" % (COLORS,))
        if WHITE not in COLORS or SKY not in COLORS:
            failures += 1
            print("FAIL colors    expected white sung row and sky-blue next")
        if MUTED in COLORS or ACCENT in COLORS:
            failures += 1
            print("FAIL colors    next line is not sky blue")
    except Exception as e:
        failures += 1
        print("FAIL colors    %r" % (e,))

    # periodic alignment: a due sync asks for the line at our own position
    align_calls = []

    def fake_align(path, **kw):
        align_calls.append(path)
        return {"status": "success", "line": "aligned line",
                "next": "aligned next", "next2": "", "next_at": 60000}

    real_get = app.hub.get
    app.hub.get = fake_align
    app.hub.ok_at = time.ticks_ms()
    app.spot.lyrics = True
    app.spot.slide = 0
    app.spot.buf = ("old line", "other", "")
    app.spot.line = "old line"
    app.spot.lyr_key = ("success", "old line")
    t = time.ticks_ms()
    app.spot.sync_at = time.ticks_add(t, -1)
    app.spot._sync_tick(t)
    print("ok   spotify   align calls=%d line=%r"
          % (len(align_calls), app.spot.buf[0]))
    if not align_calls or not align_calls[0].startswith("/lyrics?pos="):
        failures += 1
        print("FAIL align     no /lyrics position request")
    if app.spot.buf[0] != "aligned line":
        failures += 1
        print("FAIL align     did not take the aligned line: %r"
              % (app.spot.buf,))
    before = len(align_calls)
    app.spot._sync_tick(t + 100)
    if len(align_calls) != before:
        failures += 1
        print("FAIL align     sync was not throttled")
    app.hub.get = real_get

    # restart page: busy -> check -> synced, verified from live progress
    for st in ("idle", "busy", "check", "done", "notsynced", "error"):
        app.rst["state"] = st
        app.rst["msg"] = "restarting"
        app.idx = 0
        del DRAWS[:]
        try:
            app.draw_restart({"k": "restart", "t": "RESTART"})
            print("ok   restart   %-14s %3d draws" % (st, len(DRAWS)))
        except Exception as e:
            failures += 1
            print("FAIL restart   %-14s %r" % (st, e))
    app.rst["state"] = "idle"

    prog = [5000]

    def fake_sp(path, **k):
        prog[0] += 1000
        return {"sp": {"status": "success", "is_playing": True,
                       "song": "Way Back Home", "progress_ms": prog[0],
                       "duration_ms": 193000}}

    real_get = app.hub.get
    app.hub.get = fake_sp
    r = app.rst
    base_t = time.ticks_ms()
    r["state"] = "check"
    r["check_at"] = base_t
    r["at"] = time.ticks_add(base_t, -1000)
    r["good"] = 0
    r["prog"] = -1
    r["song"] = ""
    for i in range(7):
        app.restart_check(base_t + i * 1000)
    print("ok   restart   check moved state=%s good=%d"
          % (r["state"], r["good"]))
    if r["state"] != "done" or r["good"] < 2:
        failures += 1
        print("FAIL restart   playback check did not confirm sync")

    # a frozen payload must not pass as synced
    r["state"] = "check"
    r["check_at"] = base_t
    r["at"] = time.ticks_add(base_t, -1000)
    r["good"] = 0
    r["prog"] = -1
    r["song"] = ""
    app.hub.get = lambda path, **k: {
        "sp": {"status": "success", "is_playing": True,
               "song": "Stale", "progress_ms": 42000,
               "duration_ms": 193000}}
    for i in range(7):
        app.restart_check(base_t + i * 1000)
    print("ok   restart   frozen state=%s good=%d"
          % (r["state"], r["good"]))
    if r["state"] != "notsynced":
        failures += 1
        print("FAIL restart   frozen payload was not rejected")
    app.hub.get = real_get
    r["state"] = "idle"

    # rows sub-pages: each page draws, and the timer advances then wraps
    auto = None
    for page in pages:
        if page.get("k") == "rows" and page.get("auto"):
            auto = page
            app.idx = pages.index(page)
            break
    if auto is None:
        failures += 1
        print("FAIL rows      no auto page in state")
    else:
        npages = app.rows_pages(auto)
        for pi in range(npages):
            app.rs_page = pi
            del DRAWS[:]
            try:
                app.draw_rows(auto)
                print("ok   rows      %-14s %3d draws"
                      % ("sub %d/%d" % (pi + 1, npages), len(DRAWS)))
            except Exception as e:
                failures += 1
                print("FAIL rows      sub %d/%d %r" % (pi + 1, npages, e))
        app.rs_page = 0
        app.rs_at = time.ticks_ms()
        tick = time.ticks_ms()
        seen = []
        for _ in range(npages * 2):
            tick += 3700
            if app.rows_tick(tick, npages):
                seen.append(app.rs_page)
        print("ok   rows_tick npages=%d seen=%s end=%d"
              % (npages, seen, app.rs_page))
        if seen[:npages] != [((i + 1) % npages) for i in range(npages)] \
                or app.rs_page != 0:
            failures += 1
            print("FAIL rows_tick expected 1..%d then wrap" % npages)

    # Wi-Fi: home first when visible, otherwise the strongest known AP
    from desk.wifi import Wifi as WifiCls

    class ScanWlan(FakeWlan):
        def __init__(self, aps):
            self.aps = aps

        def scan(self):
            return self.aps

    sw = ScanWlan([("far-cafe", b"", 1, -80, 0, 0),
                   ("home", b"", 6, -41, 0, 0),
                   ("neighbour", b"", 11, -55, 0, 0)])
    wf = WifiCls(sw, {"wifi": [{"ssid": "far-cafe", "password": "a"},
                               {"ssid": "neighbour", "password": "b"},
                               {"ssid": "home", "password": "c"}]})
    wf.home = "home"
    order = wf._order(time.ticks_ms(), force=True)
    names = [n[0] for n in order] if order else []
    print("ok   wifi      home order=%r" % (names,))
    if names[:3] != ["home", "neighbour", "far-cafe"]:
        failures += 1
        print("FAIL wifi      expected home then strongest visible")
    sw.aps = [("far-cafe", b"", 1, -80, 0, 0),
              ("neighbour", b"", 11, -55, 0, 0)]
    order = wf._order(time.ticks_ms(), force=True)
    names = [n[0] for n in order] if order else []
    print("ok   wifi      away order=%r" % (names,))
    if names[:2] != ["neighbour", "far-cafe"]:
        failures += 1
        print("FAIL wifi      strongest visible was not first")

    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
