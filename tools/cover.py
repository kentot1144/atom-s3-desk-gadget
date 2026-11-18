"""Build a Project Zone cover image from the rendered gallery sheets.

    python tools/cover.py [out.png]

    python tools/cover.py ../atom-s3-desktop-thing-github/docs/images/cover.png
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGES = os.path.join(ROOT, "docs", "images")
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

W, H = 1200, 675
BG = (11, 18, 28)
TITLE = (238, 244, 250)
SUB = (140, 180, 205)
ACCENT = (88, 196, 230)
FOOT = (86, 101, 118)

# gallery sheets use 4x panels, a 20px gap and a 24px caption strip
PANEL = 128 * 4
CELL_W = PANEL + 20 * 4
CELL_H = PANEL + 24 * 4 + 20 * 4


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def crop_panel(sheet, col, row):
    img = Image.open(os.path.join(IMAGES, sheet)).convert("RGB")
    x = col * CELL_W + 20 * 4
    y = row * CELL_H + 24 * 4 + 20 * 4
    return img.crop((x, y, x + PANEL, y + PANEL))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(IMAGES, "cover.png")

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # soft accent glow behind the title
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -320, 760, 380), fill=(18, 52, 72))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(90)), 0.9)
    d = ImageDraw.Draw(img)

    d.text((70, 62), "AtomS3 Desk Gadget", font=font("segoeuib.ttf", 62),
           fill=TITLE)
    d.text((74, 142),
           "Clock  /  Weather  /  Pomodoro  /  Spotify Lyrics  /  PC Stats",
           font=font("segoeui.ttf", 25), fill=SUB)
    d.rectangle((70, 192, 230, 196), fill=ACCENT)

    panels = [
        ("CLOCK", crop_panel("normal-pages.png", 0, 0)),
        ("LYRICS", crop_panel("spotify-pages.png", 1, 0)),
        ("STATUS", crop_panel("status-pages.png", 1, 1)),
    ]
    pw, ph, gap, top = 300, 300, 34, 238
    x0 = (W - (pw * 3 + gap * 2)) // 2
    cap = font("segoeuib.ttf", 18)
    for i, (label, panel) in enumerate(panels):
        x = x0 + i * (pw + gap)
        d.rounded_rectangle((x - 5, top - 5, x + pw + 5, top + ph + 5),
                            radius=12, fill=(32, 48, 62))
        img.paste(panel.resize((pw, ph), Image.LANCZOS), (x, top))
        d.text((x, top + ph + 14), label, font=cap, fill=ACCENT)

    d.text((70, H - 46),
           "github.com/kentot1144/atom-s3-desk-gadget",
           font=font("segoeui.ttf", 19), fill=FOOT)
    # 256 colours keeps the file small (~30 KB) so it can be uploaded as a
    # cover or used as a UIFlow project resource.
    img.quantize(colors=256, method=Image.FASTOCTREE,
                 dither=Image.FLOYDSTEINBERG).save(out, optimize=True)
    print("wrote", out, img.size)


if __name__ == "__main__":
    main()
