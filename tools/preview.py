"""Render the STATUS sub-pages to a PNG for a quick visual check.

    python tools/preview.py [out.png]

The firmware itself cannot be screenshotted over USB, so this replays the real
renderers through `render_test`'s fake board and paints the recorded draw calls
with Pillow (`pip install pillow`).  Fonts are only approximations of the panel
fonts, but positions and colours are exact.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_test as rt  # noqa: E402  (defines the fake M5 stack)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

SCALE = 4
PANEL = 128
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_FILES = {
    "9": "consola.ttf",
    "12": "consola.ttf",
    "18": "consola.ttf",
    "40": "consolab.ttf",
    "ja": "YuGothR.ttc",
}
FONT_PX = {"9": 10, "12": 13, "18": 18, "40": 40, "ja": 12}
OPS = []


def rgb(c):
    return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


class Recorder:
    """A Display that records draw calls instead of writing to a screen."""

    def __init__(self):
        self.font = rt.FONTS.DejaVu12
        self.size = 1.0
        self.cursor = (0, 0)
        self.color = 0xFFFFFF

    def fillScreen(self, c):
        OPS.append(("screen", c))

    def fillRect(self, x, y, w, h, c):
        OPS.append(("rect", int(x), int(y), int(w), int(h), c))

    def fillCircle(self, x, y, r, c):
        OPS.append(("circle", int(x), int(y), int(r), c, True))

    def drawCircle(self, x, y, r, c):
        OPS.append(("circle", int(x), int(y), int(r), c, False))

    def fillTriangle(self, *a):
        pass

    def fillArc(self, *a):
        pass

    def setFont(self, f):
        self.font = f

    def setTextColor(self, c, *a):
        self.color = c

    def setCursor(self, x, y):
        self.cursor = (int(x), int(y))

    def setTextSize(self, v):
        self.size = v

    def getTextSize(self):
        return self.size

    def setBrightness(self, v):
        pass

    def textWidth(self, s, f):
        return int(len(s) * f.w * self.size)

    def drawString(self, s, x, y):
        OPS.append(("text", s, int(x), int(y), self.color,
                    self.font.name, self.size))

    def print(self, s):
        OPS.append(("text", s, self.cursor[0], self.cursor[1], self.color,
                    self.font.name, self.size))


def paint(ops):
    img = Image.new("RGB", (PANEL * SCALE, PANEL * SCALE), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for op in ops:
        kind = op[0]
        if kind == "screen":
            d.rectangle([0, 0, PANEL * SCALE, PANEL * SCALE], fill=rgb(op[1]))
        elif kind == "rect":
            _, x, y, w, h, c = op
            d.rectangle([x * SCALE, y * SCALE,
                         (x + w) * SCALE - 1, (y + h) * SCALE - 1],
                        fill=rgb(c))
        elif kind == "circle":
            _, x, y, r, c, filled = op
            box = [(x - r) * SCALE, (y - r) * SCALE,
                   (x + r) * SCALE, (y + r) * SCALE]
            if filled:
                d.ellipse(box, fill=rgb(c))
            else:
                d.ellipse(box, outline=rgb(c))
        elif kind == "text":
            _, s, x, y, c, name, size = op
            px = max(6, int(FONT_PX.get(name, 12) * size * SCALE))
            path = os.path.join(FONT_DIR, FONT_FILES.get(name, "consola.ttf"))
            font = ImageFont.truetype(path, px)
            d.text((x * SCALE, y * SCALE), s, font=font, fill=rgb(c))
    return img


def build_app():
    sys.path.insert(0, rt.ROOT)
    import desk.app as dapp

    cfg = {"location": {"tz": 9},
           "display": {"auto": False, "brightness": 120},
           "pomodoro": {"focus": 25, "break": 5},
           "hub": {"urls": ["http://127.0.0.1:8765"], "token": ""},
           "wifi": [{"ssid": "test", "password": ""}]}
    app = dapp.App(rt.FakeWlan(), cfg)
    app.hub.ui = app.ui = rt.STATE["ui"]
    app.hub.sp = dict(rt.STATE["sp"])
    app.hub.ly = rt.STATE["ly"]
    app.hub.ok_at = rt.time.ticks_ms()

    def default_get(path, **kw):
        return app.hub.ly if path.startswith("/lyrics") else None
    app.hub.get = default_get
    return app


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "status_preview.png"
    recorder = Recorder()
    rt.sys.modules["M5"].Display = recorder

    app = build_app()
    page = {"k": "status", "t": "STATUS", "up": 3, "tot": 3, "last": 0}

    frames = []
    app.st_page = 0
    del OPS[:]
    app.draw_status(page)
    frames.append(("STATUS 1/2 - system", paint(list(OPS))))

    for playing, label in ((True, "PLAYING"), (False, "PAUSED")):
        app.st_page = 1
        app.hub.sp = dict(rt.STATE["sp"], is_playing=playing)
        del OPS[:]
        app.draw_status(page)
        frames.append(("STATUS 2/2 - " + label, paint(list(OPS))))

    gap = 24
    sheet = Image.new(
        "RGB", ((PANEL * len(frames) + gap * (len(frames) + 1)) * SCALE,
                (PANEL + 26) * SCALE), (16, 24, 32))
    d = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(os.path.join(FONT_DIR, "segoeui.ttf"),
                                    11 * SCALE)
    for i, (title, img) in enumerate(frames):
        x = (gap + i * (PANEL + gap)) * SCALE
        d.text((x, 6 * SCALE), title, font=label_font, fill=(125, 133, 144))
        sheet.paste(img, (x, 26 * SCALE))
    sheet.save(out)
    print("wrote", out, sheet.size)


if __name__ == "__main__":
    main()
