"""Thin display client: wires the modules together and owns app state."""
import M5
import gc
import time
from desk.const import FALLBACK, SYS_PAGE
from desk.hub import Hub
from desk.pom import Led, Light, Pomodoro
from desk.spot import Spot
from desk.wifi import Wifi, load_cfg

from desk.draw import AppDraw
from desk.loop import AppLoop

from desk.draw import AppDraw
from desk.loop import AppLoop


class App(AppDraw, AppLoop):
    def __init__(self, wlan, cfg):
        self.cfg = cfg
        self.tz = int((cfg.get("location") or {}).get("tz", 9))
        self.wifi = Wifi(wlan, cfg)
        self.hub = Hub(wlan, cfg)
        self.pom = Pomodoro(cfg)
        self.light = Light(cfg)
        self.led = Led(self.light.value, cfg)
        self.spot = Spot(self)

        self.mode = 0
        self.idx = 0
        self.ui = None
        self.last_ui = None
        self._ring = None
        self._ring_src = None
        self.dirty = True
        self.clk_sec = -1
        self.focus_at = 0
        self.face_at = 0
        self.status_at = 0
        self.started = time.ticks_ms()
        self.connected_at = 0
        self.ntp_done = False

        self.banners = []
        self.banner = None
        self.banner_at = 0
        self.banner_dirty = False

        self.face = {"breath": 0, "dev": 0, "tm": 0, "st": 0, "open": 3000}
        self.rst = {"state": "idle", "msg": "", "at": 0, "sent": 0,
                    "dots": 0}
        self.bright = self.light.value

        self.down = False
        self.press_at = 0
        self.hold_done = False
        self.soft_done = False
        self.clicks = []

    # -- helpers -----------------------------------------------------
    def now(self):
        return self.hub.now()

    def local(self):
        return time.localtime(self.now() + self.tz * 3600)

    def alert(self):
        return bool((self.ui or {}).get("al"))

    def page_list(self):
        u = self.ui or {}
        if self.mode == 1:
            return u.get("spot") or [{"k": "spotify", "t": "SPOTIFY"}]
        ring = u.get("norm")
        if not ring:
            return FALLBACK
        if ring is not self._ring_src:
            # Copy so the hub dict is never mutated; cache so the ring is not
            # rebuilt on every draw call.
            self._ring_src = ring
            r = list(ring)
            r.append(SYS_PAGE)
            self._ring = r
        return self._ring

    def page(self):
        pages = self.page_list()
        if self.idx >= len(pages):
            self.idx = 0
        return pages[self.idx]

    def enter_page(self):
        self.dirty = True
        self.clk_sec = -1
        self.focus_at = 0
        self.face_at = 0
        self.status_at = 0
        p = self.page()
        if p.get("k") == "spotify":
            self.spot.enter()
        elif p.get("k") == "restart":
            self.rst["state"] = "idle"
            self.rst["msg"] = ""

    def next_page(self):
        n = len(self.page_list())
        self.idx = (self.idx + 1) % n
        self.enter_page()

    def switch_mode(self):
        self.mode = 1 - self.mode
        self.idx = 0
        self.banner = None
        self.clicks = []
        self.hold_done = True
        self.enter_page()

    # -- drawing -----------------------------------------------------
    def draw(self, p=None):
        if p is None:
            p = self.page()
        k = p.get("k")
        if k == "clock":
            self.draw_clock(p)
        elif k == "big":
            self.draw_big(p)
        elif k == "rows":
            self.draw_rows(p)
        elif k == "focus":
            self.draw_focus(p)
        elif k == "restart":
            self.draw_restart(p)
        elif k == "face":
            self.draw_face(p)
        elif k == "notice":
            self.draw_notice(p)
        elif k == "status":
            self.draw_status(p)
        elif k == "spotify":
            self.spot.dirty_full = True
            self.spot.draw()
        else:
            self.draw_msg(p)


def run(wlan):
    gc.collect()
    cfg = load_cfg()
    M5.begin()
    gc.collect()
    print("app free", gc.mem_free())
    app = App(wlan, cfg)
    app.run()
