"""Wi-Fi roaming: always try the 5 GHz home AP first, then check the hub."""
import json
import time
from desk.const import HOME_AFTER_MS, ROAM_AFTER_MS

# The home 5 GHz LAN is the only one that can reach the hub.  Detect it by
# its "-5p" suffix instead of storing a real (private) SSID in the source.
HOME_SUFFIX = "-5p"

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
    SCAN_MS = 12000          # re-scan visible APs at most this often

    def __init__(self, wlan, cfg):
        self.wlan = wlan
        self.nets = _nets(cfg)
        self.idx = 0
        home = next((n[0] for n in self.nets
                     if n[0].endswith(HOME_SUFFIX)), "")
        self.home = home or (self.nets[0][0] if self.nets else "")
        self.ssid = ""
        self.try_ssid = ""
        self.out_at = 0
        self.foreign_at = 0
        self.next_try = time.ticks_ms()
        self.fail = 0
        self.err = ""
        self.vis = None           # visible SSID -> RSSI from the last scan
        self.scan_at = time.ticks_add(time.ticks_ms(), -self.SCAN_MS)
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

    def _scan(self, now, force=False):
        """Visible SSIDs and their signal, or None when scanning is unsupported."""
        if not force and self.vis is not None:
            if time.ticks_diff(now, self.scan_at) < self.SCAN_MS:
                return self.vis
        self.scan_at = now
        try:
            rows = self.wlan.scan()
        except Exception:
            self.vis = None
            return None
        vis = {}
        for row in rows or ():
            try:
                ssid = row[0]
                if isinstance(ssid, bytes):
                    ssid = ssid.decode()
                if not ssid:
                    continue
                rssi = int(row[3])
            except Exception:
                continue
            if ssid not in vis or rssi > vis[ssid]:
                vis[ssid] = rssi
        self.vis = vis
        return vis

    def _order(self, now, force=False):
        """Known networks to try: 5p home first, then the strongest visible.

        Returns None when the radio cannot scan, so the caller can fall back
        to cycling the configured list.  The home AP is tried first even if
        the scan does not list it; hub reachability decides whether we stay.
        """
        vis = self._scan(now, force)
        out = []
        for n in self.nets:
            if n[0] == self.home:
                out.append(n)
                break
        if vis is not None:
            rest = [n for n in self.nets if n[0] != self.home and n[0] in vis]
            rest.sort(key=lambda n: vis.get(n[0], -999), reverse=True)
            out += rest
        # Hidden / unscanned networks still get a turn after the visible ones.
        out += [n for n in self.nets
                if n[0] != self.home and n not in out]
        return out or None

    def poll(self, now, hub_ok):
        up = self.up()
        if up:
            self.out_at = 0
            self.fail = 0
            if self.try_ssid:
                self.try_ssid = ""
                self.idx = 0
                self.vis = None    # next drop gets a fresh scan
            if self.home and self.ssid and self.ssid != self.home:
                # a travel AP took us; if the hub is unreachable, go home
                if not self.foreign_at:
                    self.foreign_at = now
                elif (not hub_ok and
                      time.ticks_diff(now, self.foreign_at) > HOME_AFTER_MS):
                    self.foreign_at = 0
                    vis = self._scan(now, force=True)
                    if vis is None or self.home in vis:
                        self.idx = 0
                        self.ssid = self.home
                        self.next_try = time.ticks_add(now, 2000)
                        try:
                            self.wlan.disconnect()
                        except Exception:
                            pass
                    else:
                        # home is not in range: stay on the working AP
                        self.next_try = time.ticks_add(now, 30000)
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
        nets = self._order(now)
        if nets is None:
            nets = self.nets
            if self.out_at and time.ticks_diff(now, self.out_at) < ROAM_AFTER_MS:
                nets = nets[:1]       # no scan support: stay home first
        if not nets:
            return
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
