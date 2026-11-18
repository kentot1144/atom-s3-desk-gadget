"""Render a gallery of every firmware page to PNG sheets.

    python tools/gallery.py [out_dir]

The firmware cannot be screenshotted over USB, so this replays the real page
renderers through `render_test`'s fake board and paints the recorded draw calls
with Pillow.  Fonts are approximations of the panel fonts, but positions,
colours and layout are exactly what the device draws.

The images are committed to the public repo under docs/images so people can see
what the gadget looks like without building one.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_test as rt  # noqa: E402  (defines the fake M5 stack)
import preview as pv  # noqa: E402  (Recorder, paint, build_app, spot_frame)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

LONG_LINE = ("I never knew that every road would lead me home and every "
             "turning tide would bring me back to you again tonight")
GAP = 20
LABEL_H = 24
BG = (16, 24, 32)
LABEL = (125, 133, 144)


def draw(app, fn, *args):
    """Call a renderer with a clean recorder and return one painted panel."""
    del pv.OPS[:]
    fn(*args)
    return pv.paint(list(pv.OPS))


def sheet(title, frames, cols, path):
    """Arrange (label, image) panels in a grid with a caption above each."""
    rows = (len(frames) + cols - 1) // cols
    cw = pv.PANEL * pv.SCALE + GAP * pv.SCALE
    ch = (pv.PANEL + LABEL_H) * pv.SCALE + GAP * pv.SCALE
    img = Image.new("RGB", (cols * cw, rows * ch), BG)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(os.path.join(pv.FONT_DIR, "segoeui.ttf"),
                              11 * pv.SCALE)
    for i, (label, panel) in enumerate(frames):
        x = (i % cols) * cw + GAP * pv.SCALE
        y = (i // cols) * ch + GAP * pv.SCALE
        d.text((x, y), label, font=font, fill=LABEL)
        img.paste(panel, (x, y + LABEL_H * pv.SCALE))
    img.save(path)
    print("wrote", os.path.basename(path), img.size)
    return img


def spot_track(app, playing=True, offline=False):
    """Plain Spotify track page, or the hub-offline error state."""
    app.mode = 1
    app.idx = 0
    app.spot.lyrics = False
    app.spot.sig = "HUB OFFLINE" if offline else None
    app.hub.sp = dict(rt.STATE["sp"], is_playing=playing)
    app.hub.ok_at = rt.time.ticks_ms()
    app.spot.dirty_full = True
    return draw(app, app.spot.draw)


def paged_frame(app, page):
    """One page of an extra-long lyric line."""
    pv.spot_frame(app, LONG_LINE, "the next line", "and the one after")
    app.spot.line_page = page
    app.spot._build_disp()
    app.spot.dirty_full = True
    return draw(app, app.spot.draw)


def normal_sheet(app, path):
    app.mode = 0
    app.st_page = 0
    app.pom.rem = 17 * 60000 + 23000
    app.pom.running = True
    app.pom.sessions = 3
    app.face.update(st=1, dev=4, breath=8)
    frames = []
    for i, page in enumerate(app.page_list()):
        app.idx = i
        app.st_page = 0
        app.enter_page()
        frames.append((page.get("t") or page.get("k") or "PAGE",
                       draw(app, app.draw, page)))
    return sheet("Normal mode", frames, 5, path)


def spotify_sheet(app, path):
    frames = [
        ("SPOTIFY - track", spot_track(app)),
        ("LYRICS - 3 rows", pv.spot_frame(
            app, "Every little thing you said",
            "is ringing in my head", "and I cannot let it go")),
        ("LYRICS - 4 rows", pv.spot_frame(
            app, "I never knew that every road would lead me home",
            "but here I am again", "standing at your door")),
        ("LYRICS - long, page 1/2", paged_frame(app, 0)),
        ("LYRICS - long, page 2/2", paged_frame(app, 1)),
        ("SPOTIFY - paused", spot_track(app, playing=False)),
        ("SPOTIFY - hub offline", spot_track(app, offline=True)),
    ]
    return sheet("Spotify mode", frames, 4, path)


def status_sheet(app, path):
    page = {"k": "status", "t": "STATUS", "up": 3, "tot": 3, "last": 0}
    app.mode = 0
    app.hub.sp = dict(rt.STATE["sp"])
    frames = []
    for playing, label in ((True, "playing"), (False, "paused")):
        app.hub.sp = dict(rt.STATE["sp"], is_playing=playing)
        for st, name in ((0, "STATUS 1/2 - health"),
                         (1, "STATUS 2/2 - " + label)):
            app.st_page = st
            frames.append((name, draw(app, app.draw_status, page)))
    return sheet("Status sub-pages", frames, 2, path)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/images"
    os.makedirs(out, exist_ok=True)
    recorder = pv.Recorder()
    rt.sys.modules["M5"].Display = recorder

    app = pv.build_app()
    app.enter_page()
    normal_sheet(app, os.path.join(out, "normal-pages.png"))
    spotify_sheet(app, os.path.join(out, "spotify-pages.png"))
    status_sheet(app, os.path.join(out, "status-pages.png"))


if __name__ == "__main__":
    main()
