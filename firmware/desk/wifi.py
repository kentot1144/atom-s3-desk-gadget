"""Wi-Fi roaming: home network first, travel networks only if the hub is unreachable."""
import json
import time
from desk.const import HOME_AFTER_MS, ROAM_AFTER_MS

# ------------------------------------------------------------------- config
def load_cfg():
    try:
        f = open("/flash/config.json")
        try:
            return json.load(f)
        finally:
            f.close()
    except Exception:
        return {}


def _nets(cfg):
    w = cfg.get("wifi")
    if isinstance(w, dict) and isinstance(w.get("networks"), list):
        w = w["networks"]
    elif isinstance(w, dict):
        w = [w]
    elif not isinstance(w, list):
        w = []
    out = []
    for n in w:
        if isinstance(n, dict) and n.get("ssid"):
            out.append((n["ssid"], n.get("password") or ""))
    return out


# --------------------------------------------------------------------- wifi
class Wifi:
    def __init__(self, wlan, cfg):
        self.wlan = wlan
        self.nets = _nets(cfg)
        self.idx = 0
        self.home = self.nets[0][0] if self.nets else ""
        self.ssid = ""
        self.try_ssid = ""
        self.out_at = 0
        self.foreign_at = 0
        self.next_try = time.ticks_ms()
        self.fail = 0
        self.err = ""
        self._radio()

    def _radio(self):
        w = self.wlan
        try:
            if not w.active():
                w.active(True)
        except Exception as e:
            self.err = "act " + str(e)[:16]
            return
        try:
            w.config(pm=0)            # no power save: weak APs stall otherwise
        except Exception:
            pass
        try:
            w.config(reconnects=0)    # this loop owns reconnects
        except Exception:
            pass
        try:
            w.config(protocol=3)      # 11b/g only, no HT aggregation
        except Exception:
            pass
        try:
            w.config(txpower=10.0)
        except Exception:
            pass

    def up(self):
        try:
            return bool(self.wlan.isconnected())
        except Exception:
            return False

    def poll(self, now, hub_ok):
        up = self.up()
        if up:
            self.out_at = 0
            self.fail = 0
            if self.try_ssid:
                for i, n in enumerate(self.nets):
                    if n[0] == self.try_ssid:
                        self.idx = i
                        break
                self.try_ssid = ""
            if self.home and self.ssid and self.ssid != self.home:
                # a travel AP took us; if the hub is unreachable, go home
                if not self.foreign_at:
                    self.foreign_at = now
                elif (not hub_ok and
                      time.ticks_diff(now, self.foreign_at) > HOME_AFTER_MS):
                    self.foreign_at = 0
                    self.idx = 0
                    self.ssid = self.home
                    self.next_try = time.ticks_add(now, 2000)
                    try:
                        self.wlan.disconnect()
                    except Exception:
                        pass
            else:
                self.foreign_at = 0
        else:
            if not self.out_at:
                self.out_at = now
                self.foreign_at = 0
            if time.ticks_diff(now, self.next_try) >= 0:
                self._connect(now)
        return up

    def _connect(self, now):
        self.next_try = time.ticks_add(now, 3000)
        if not self.nets:
            return
        try:
            if not self.wlan.active():
                self._radio()
                return
            st = self.wlan.status()
        except Exception as e:
            self.err = "st " + str(e)[:14]
            self.next_try = time.ticks_add(now, 30000)
            return
        if st in (1001, 1010):
            self.fail = 0
            self.next_try = time.ticks_add(now, 1500)
            return
        nets = self.nets
        if self.out_at and time.ticks_diff(now, self.out_at) < ROAM_AFTER_MS:
            nets = nets[:1]           # stay home before trying travel networks
        if self.idx >= len(nets):
            self.idx = 0
        ssid, pw = nets[self.idx]
        try:
            if pw:
                self.wlan.connect(ssid, pw)
            else:
                self.wlan.connect(ssid)
            self.ssid = ssid
            self.try_ssid = ssid
            self.err = ""
            self.fail = 0
            self.idx = (self.idx + 1) % len(nets)
            self.next_try = time.ticks_add(now, 5000)
        except Exception as e:
            self.err = "wifi " + str(e)[:14]
            self.fail += 1
            self.idx = (self.idx + 1) % len(nets)
            if self.fail >= 5:
                self.fail = 0
                try:
                    self.wlan.active(False)
                except Exception:
                    pass
                time.sleep_ms(300)
                self._radio()
                self.next_try = time.ticks_add(now, 10000)
            else:
                self.next_try = time.ticks_add(now, 6000)
