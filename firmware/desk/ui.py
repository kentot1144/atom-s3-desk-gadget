"""Palette, fonts and drawing helpers for the 128x128 panel."""
import M5

LCD = M5.Display
F = M5.Lcd.FONTS
F_TINY = F.DejaVu9
F_SM = F.DejaVu12
F_MD = F.DejaVu18
F_BIG = F.DejaVu40
try:
    F_JA = F.EFontJA24
except Exception:
    F_JA = F_MD

W = 128
H = 128

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

# Index order shared with the hub's ui page builder.
PAL = (BLACK, PANEL, GHOST, LINE, WHITE, MUTED, ACCENT, ACCENT2,
       GREEN, AMBER, ORANGE, YELLOW, RED, PURPLE)

WD = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
       "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def pc(ci, default=WHITE):
    try:
        return PAL[ci]
    except Exception:
        return default


# ------------------------------------------------------------------ drawing
def tl(s, f, x, y, c=WHITE, bg=BLACK):
    LCD.setFont(f)
    LCD.setTextColor(c, bg)
    LCD.setCursor(int(x), int(y))
    LCD.print(s)


def t1(s, f, x, y, c=WHITE, bg=BLACK):
    LCD.setFont(f)
    LCD.setTextColor(c, bg)
    LCD.drawString(s, int(x), int(y))


def tw(s, f):
    return LCD.textWidth(s, f)


def ctext(s, f, y, c=WHITE, bg=BLACK):
    tl(s, f, (W - tw(s, f)) // 2, y, c, bg)


def rtext(s, f, xr, y, c=WHITE, bg=BLACK):
    tl(s, f, xr - tw(s, f), y, c, bg)


def temp(s, f, x, y, c=WHITE, bg=BLACK, r=2):
    w = tw(s, f)
    tl(s, f, x, y, c, bg)
    cx = x + w + r + 1
    cy = y + r + 2
    LCD.drawCircle(cx, cy, r, c)
    LCD.fillCircle(cx, cy, r - 1, bg)
    tl("C", f, cx + r + 2, y, c, bg)


def ctemp(s, f, y, c=WHITE, bg=BLACK, r=2):
    w = tw(s, f) + 2 * r + 3 + tw("C", f)
    temp(s, f, (W - w) // 2, y, c, bg, r)


def rtemp(s, f, xr, y, c=WHITE, bg=BLACK, r=2):
    w = tw(s, f) + 2 * r + 3 + tw("C", f)
    temp(s, f, xr - w, y, c, bg, r)


def bar(x, y, w, h, pct, c, bg=LINE):
    if pct < 0:
        pct = 0
    if pct > 100:
        pct = 100
    LCD.fillRect(int(x), int(y), int(w), int(h), bg)
    fw = int(w * pct / 100)
    if fw > 0:
        LCD.fillRect(int(x), int(y), fw, int(h), c)


def header(title, idx, n, c=ACCENT):
    tl(title, F_SM, 6, 5, c)
    rtext("%d/%d" % (idx + 1, n), F_TINY, 122, 7, MUTED)
    LCD.fillRect(0, 20, W, 1, LINE)


def dots(n, idx, y=122):
    total = n * 6 - 2
    x = (W - total) // 2
    for i in range(n):
        LCD.fillRect(x + i * 6, y, 4, 4, ACCENT if i == idx else LINE)


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


def seg_time(s, y, c=WHITE, ghost=GHOST):
    dw, dh, t, gap, cw = 24, 46, 5, 5, 8
    x = (W - (4 * dw + cw + 4 * gap)) // 2
    for i, ch in enumerate(s):
        if i == 2:
            LCD.fillRect(x + 1, y + 14, 5, 5, c)
            LCD.fillRect(x + 1, y + 28, 5, 5, c)
            x += cw + gap
        rects = _seg_rects(x, y, dw, dh, t)
        for k in "abcdefg":
            r = rects[k]
            LCD.fillRect(r[0], r[1], r[2], r[3], ghost)
        for k in SEGMENTS.get(ch, ""):
            r = rects[k]
            LCD.fillRect(r[0], r[1], r[2], r[3], c)
        x += dw + gap


def wrap(s, f, max_w, max_lines=3):
    out = []
    for para in str(s).split("\n"):
        line = ""
        for word in para.split(" "):
            while word and tw(word, f) > max_w:
                cut = len(word)
                while cut > 1 and tw(word[:cut], f) > max_w:
                    cut -= 1
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:cut])
                word = word[cut:]
                if len(out) >= max_lines:
                    return out
            cand = word if not line else line + " " + word
            if line and tw(cand, f) > max_w:
                out.append(line)
                line = word
            else:
                line = cand
            if len(out) >= max_lines:
                return out
        if line or not para.split(" "):
            out.append(line)
        if len(out) >= max_lines:
            return out
    return out[:max_lines]


def atom_face(mood="normal", dev=0, alert=False):
    ey = 42 + dev
    for ex in (32, W - 32):
        if mood == "blink":
            LCD.fillRect(ex - 5, ey - 1, 10, 3, WHITE)
        elif mood == "happy":
            LCD.fillArc(ex, ey, 8, 4, 180, 360, WHITE)
        elif mood == "sleep":
            LCD.fillArc(ex, ey, 8, 4, 0, 180, WHITE)
        else:
            LCD.fillCircle(ex, ey, 6, WHITE)
    LCD.fillRect(40, 91, 48, 4, WHITE)
    if alert:
        LCD.fillRect(118, 24, 5, 18, RED)
        LCD.fillRect(118, 46, 5, 5, RED)


def splash(sub="thin display - hub driven"):
    LCD.fillScreen(BLACK)
    LCD.fillRect(16, 42, 3, 64, ACCENT)
    tl("ATOM", F_SM, 27, 38, MUTED)
    tl("DESK", F_BIG, 26, 50, WHITE)
    tl(sub, F_TINY, 27, 96, MUTED)
    tl("starting...", F_TINY, 27, 116, ACCENT)


def splash_line(text, color=ACCENT):
    """Redraw only the splash status line (used while joining Wi-Fi)."""
    LCD.fillRect(0, 110, W, 18, BLACK)
    t1(text[:22], F_TINY, 27, 114, color)


def age_text(sec):
    if sec < 60:
        return "NOW"
    if sec < 3600:
        return "%dm" % (sec // 60)
    if sec < 86400:
        return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)
    return "%dd%02dh" % (sec // 86400, (sec % 86400) // 3600)


def mmss(ms):
    s = int(ms or 0) // 1000
    return "%d:%02d" % (s // 60, s % 60)
