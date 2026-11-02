import M5

LCD = M5.Display
F = M5.Lcd.FONTS

F_TINY = F.DejaVu9
F_SM = F.DejaVu12
F_MD = F.DejaVu18
F_MED = F.DejaVu24
F_BIG = F.DejaVu40

W = 128
H = 128

# dark "desk computer" palette
BLACK = 0x000000
PANEL = 0x101820
GHOST = 0x151F29
LINE = 0x22303C
WHITE = 0xE6EDF3
MUTED = 0x7D8590
ACCENT = 0x22D3EE
ACCENT2 = 0xF472B6
GREEN = 0x3FB950
AMBER = 0xD29922
ORANGE = 0xF0883E
YELLOW = 0xE3B341
RED = 0xF85149
PURPLE = 0xA78BFA


def clear(color=BLACK):
    LCD.fillScreen(color)


def tw(s, font):
    return LCD.textWidth(s, font)


def text(s, font, x, y, color=WHITE, bg=BLACK):
    LCD.setFont(font)
    LCD.setTextColor(color, bg)
    LCD.setCursor(int(x), int(y))
    LCD.print(s)


def text1(s, font, x, y, color=WHITE, bg=BLACK):
    """Single-line text: never wraps onto a second row."""
    LCD.setFont(font)
    LCD.setTextColor(color, bg)
    LCD.drawString(s, int(x), int(y))


def ctext(s, font, y, color=WHITE, bg=BLACK):
    text(s, font, (W - tw(s, font)) // 2, y, color, bg)


def rtext(s, font, x_right, y, color=WHITE, bg=BLACK):
    text(s, font, x_right - tw(s, font), y, color, bg)


def temp_w(s, font, r=2):
    return tw(s, font) + 2 * r + 3 + tw("C", font)


def temp(s, font, x, y, color=WHITE, bg=BLACK, r=2):
    """Draw '18°C' without relying on the degree glyph (fonts lack it)."""
    w = tw(s, font)
    text(s, font, x, y, color, bg)
    cx = x + w + r + 1
    cy = y + r + 2
    LCD.drawCircle(cx, cy, r, color)
    LCD.fillCircle(cx, cy, r - 1, bg)
    text("C", font, cx + r + 2, y, color, bg)


def ctemp(s, font, y, color=WHITE, bg=BLACK, r=2):
    temp(s, font, (W - temp_w(s, font, r)) // 2, y, color, bg, r)


def rtemp(s, font, x_right, y, color=WHITE, bg=BLACK, r=2):
    temp(s, font, x_right - temp_w(s, font, r), y, color, bg, r)


SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}


def _seg_rects(x, y, w, h, t):
    hw = (h - 3 * t) // 2
    return {
        "a": (x + t, y, w - 2 * t, t),
        "b": (x + w - t, y + t, t, hw),
        "c": (x + w - t, y + 2 * t + hw, t, hw),
        "d": (x + t, y + h - t, w - 2 * t, t),
        "e": (x, y + 2 * t + hw, t, hw),
        "f": (x, y + t, t, hw),
        "g": (x + t, y + t + hw, w - 2 * t, t),
    }


def seg_time(s, y, color=WHITE, ghost=GHOST):
    # 4 digits as HHMM: "2147"
    dw, dh, t, gap, cw = 24, 46, 5, 5, 8
    x = (W - (4 * dw + cw + 4 * gap)) // 2
    for i, ch in enumerate(s):
        if i == 2:
            LCD.fillRect(x + 1, y + 14, 5, 5, color)
            LCD.fillRect(x + 1, y + 28, 5, 5, color)
            x += cw + gap
        rects = _seg_rects(x, y, dw, dh, t)
        for k in "abcdefg":
            r = rects[k]
            LCD.fillRect(r[0], r[1], r[2], r[3], ghost)
        for k in SEGMENTS.get(ch, ""):
            r = rects[k]
            LCD.fillRect(r[0], r[1], r[2], r[3], color)
        x += dw + gap


def bar(x, y, w, h, pct, color, bg=LINE):
    if pct < 0:
        pct = 0
    if pct > 100:
        pct = 100
    LCD.fillRect(x, y, w, h, bg)
    fw = int(w * pct / 100)
    if fw > 0:
        LCD.fillRect(x, y, fw, h, color)


def header(title, idx, n, color=ACCENT):
    text(title, F_SM, 6, 5, color)
    rtext("%d/%d" % (idx + 1, n), F_TINY, 122, 7, MUTED)
    LCD.fillRect(0, 20, W, 1, LINE)


def dots(n, idx, y=122):
    total = n * 6 - 2
    x = (W - total) // 2
    for i in range(n):
        c = ACCENT if i == idx else LINE
        LCD.fillRect(x + i * 6, y, 4, 4, c)


def wifi(x, y, on):
    c = GREEN if on else RED
    for i, hh in enumerate((3, 5, 7)):
        LCD.fillRect(x + i * 4, y + (7 - hh), 2, hh, c)


# --- HelloATOMS3 mascot -------------------------------------------------
# Geometry and expressions ported from the Kanazawa University example
# "HelloATOMS3" by akita11 (MIT License, 2025-07-14).  The face is drawn
# directly on the LCD, so it does not need a Timer callback.
M_EYE_X, M_EYE_Y, M_EYE_R = 32, 42, 6
M_MOUTH_W, M_MOUTH_H = 48, 4
M_MOUTH_X, M_MOUTH_Y = (W - M_MOUTH_W) // 2, 93


def atom_face(mood="normal", dev=0, alert=False):
    """mood: normal / happy / sleep / blink.  dev = breathing offset."""
    ey = M_EYE_Y + dev
    for ex in (M_EYE_X, W - M_EYE_X):
        if mood == "blink":
            LCD.fillRect(ex - 5, ey - 1, 10, 3, WHITE)
        elif mood == "happy":
            LCD.fillArc(ex, ey, M_EYE_R + 2, M_EYE_R - 2, 180, 360, WHITE)
        elif mood == "sleep":
            LCD.fillArc(ex, ey, M_EYE_R + 2, M_EYE_R - 2, 0, 180, WHITE)
        else:
            LCD.fillCircle(ex, ey, M_EYE_R, WHITE)
    LCD.fillRect(M_MOUTH_X, M_MOUTH_Y - M_MOUTH_H // 2, M_MOUTH_W, M_MOUTH_H, WHITE)
    if alert:
        # the original "!" expression, moved below the header
        LCD.fillRect(118, 24, 5, 18, RED)
        LCD.fillRect(118, 46, 5, 5, RED)


def splash(sub="multi-function desk computer"):
    clear()
    LCD.fillRect(16, 42, 3, 64, ACCENT)
    text("ATOM", F_SM, 27, 38, MUTED)
    text("DESK", F_BIG, 26, 50, WHITE)
    text(sub, F_TINY, 27, 96, MUTED)
    text("starting...", F_TINY, 27, 116, ACCENT)
