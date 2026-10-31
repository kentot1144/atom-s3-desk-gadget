import M5
from M5 import *
import time, gc

from desk.config import load
from desk.net import Net
from desk.app import App


def main():
    M5.begin()
    gc.collect()
    cfg = load()
    net = Net(cfg)
    app = App(cfg, net)
    app.run()


try:
    main()
except Exception as e:
    # Show the problem on screen, keep the REPL usable for debugging
    try:
        from desk import ui as u
        u.clear()
        u.ctext("ERROR", u.F_MED, 6, u.RED)
        msg = repr(e)
        y = 34
        while msg and y < 110:
            u.text(msg[:16], u.F_SM, 4, y, u.WHITE)
            msg = msg[16:]
            y += 13
    except Exception:
        pass
    raise
