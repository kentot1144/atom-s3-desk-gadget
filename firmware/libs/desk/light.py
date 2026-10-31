"""Ambient-light auto brightness.

The Kanazawa HelloATOMS3 back board has a CdS cell on GPIO6 (ADC1).
It is read exactly like the original example:

    ADC(Pin(6), atten=ADC.ATTN_11DB)   # 0..4095
    light = raw / 4095 * 100           # 0..100 %

The value is mapped to a backlight level between ``display.min`` and
``display.max``.  Reading is slow on purpose (2 s) and changes smaller than
3 levels are ignored, so the screen never flickers.
"""

import math
import time

try:
    from hardware import ADC, Pin
    _HAVE_ADC = True
except Exception:
    try:
        from machine import ADC, Pin
        _HAVE_ADC = True
    except Exception:
        ADC = None
        Pin = None
        _HAVE_ADC = False


class AutoBrightness:
    def __init__(self, cfg):
        d = cfg.get("display") or {}
        self.on = bool(d.get("auto", True))
        self.min = int(d.get("min", 10))
        self.max = int(d.get("max", d.get("brightness", 120)))
        self.interval = int(d.get("interval", 2000))
        self.pin = int(d.get("light_pin", 6))
        if self.min < 1:
            self.min = 1
        if self.max < self.min:
            self.max = self.min
        self.pct = 100
        self.value = int(d.get("brightness", self.max))
        if self.value < self.min:
            self.value = self.min
        elif self.value > self.max:
            self.value = self.max
        self.at = 0
        self.adc = None
        if self.on and _HAVE_ADC:
            try:
                try:
                    self.adc = ADC(Pin(self.pin), atten=ADC.ATTN_11DB)
                except TypeError:
                    self.adc = ADC(Pin(self.pin))
                    try:
                        self.adc.atten(ADC.ATTN_11DB)
                    except Exception:
                        pass
            except Exception:
                self.adc = None
        if self.adc is None:
            self.on = False

    def _read(self):
        try:
            v = self.adc.read()
            return v if v >= 0 else 0
        except Exception:
            return None

    def tick(self, now):
        """Return a new brightness level, or None when nothing changed."""
        if not self.on or self.adc is None:
            return None
        if time.ticks_diff(now, self.at) < self.interval:
            return None
        self.at = now
        a = self._read()
        b = self._read()
        c = self._read()
        if a is None or b is None or c is None:
            return None
        raw = (a + b + c) // 3
        pct = raw * 100 // 4095
        if pct < 0:
            pct = 0
        elif pct > 100:
            pct = 100
        self.pct = pct
        # Perceptual curve: mid light levels should not make the screen
        # unreadable.  sqrt(32 %) -> 56 % of the brightness range.
        adj = int(math.sqrt(pct) * 10)
        if adj > 100:
            adj = 100
        value = self.min + (self.max - self.min) * adj // 100
        if abs(value - self.value) < 3:
            return None
        self.value = value
        return value
