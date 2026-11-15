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


class Font:
    def __init__(self, name, w):
        self.name = name
        self.w = w


FONTS = types.SimpleNamespace(
    DejaVu9=Font("9", 4), DejaVu12=Font("12", 6), DejaVu18=Font("18", 9),
    DejaVu40=Font("40", 20), EFontJA24=Font("ja", 12))


class Display:
    font = FONTS.DejaVu12

    def fillScreen(self, *a): DRAWS.append("fillScreen")
    def fillRect(self, *a): DRAWS.append("fillRect")
    def fillCircle(self, *a): DRAWS.append("fillCircle")
    def fillTriangle(self, *a): DRAWS.append("fillTriangle")
    def fillArc(self, *a): DRAWS.append("fillArc")
    def drawCircle(self, *a): DRAWS.append("drawCircle")
    def setFont(self, f): self.font = f
    def setTextColor(self, *a): pass
    def setCursor(self, *a): pass
    def setBrightness(self, v): pass
    def drawString(self, *a): DRAWS.append("text")

    def print(self, s):
        if not isinstance(s, str):
            raise TypeError("display.print got %r" % (s,))
        DRAWS.append("text")

    def textWidth(self, s, f):
        return len(s) * f.w


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
            {"k": "rows", "t": "SERVERS 1/1", "hc": 8, "rows": [
                {"l": "hub", "v": "UP", "d": 8, "vc": 8},
                {"l": "ssh", "v": "DOWN", "d": 12, "vc": 12,
                 "s": "since 12m"}], "foot": "3 CHECKS"},
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
            except Exception as e:
                failures += 1
                print("FAIL spotify   %-14s %r"
                      % ("%s lyrics=%d" % (label, lyrics), e))

    app.spot.lyrics = True
    for line in ("This is an extremely long lyric line that cannot possibly "
                 "fit on a 128 pixel wide display in one row",
                 "これは日本語のとても長い歌詞の行で、128ピクセルの画面には収まりません"):
        app.spot.line = line
        app.spot.rows = []
        app.spot._plan_lyrics(time.ticks_ms())
        app.spot.dirty_full = True
        try:
            app.spot.draw()
            print("ok   spotify   %-14s %3d draws"
                  % ("long lyric", len(DRAWS)))
        except Exception as e:
            failures += 1
            print("FAIL spotify   long lyric %r" % e)

    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
