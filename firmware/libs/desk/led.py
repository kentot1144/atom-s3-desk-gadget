"""Critical-service alarm.

The HelloATOMS3 back board has a plain LED on GPIO5, so that is the real
alarm indicator now.  The screen backlight can also pulse if
``alerts.backlight`` is true (the old behaviour on boards without an LED),
and an optional WS2812 on ``alerts.rgb_gpio`` is still supported.
"""

import time

import M5

try:
    from machine import Pin
except Exception:
    Pin = None

try:
    import neopixel
except Exception:
    neopixel = None


class Alerter:
    PERIOD = 900

    def __init__(self, bright, led_gpio=5, backlight=False, rgb_gpio=0):
        self.bright = bright
        self.dim_level = max(2, bright // 6)
        self.at = 0
        self.dim = False
        self.backlight = bool(backlight)
        self.led = None
        if Pin is not None and led_gpio:
            try:
                self.led = Pin(int(led_gpio), Pin.OUT)
                self.led.value(0)
            except Exception:
                self.led = None
        self.np = None
        if neopixel is not None and Pin is not None and rgb_gpio:
            try:
                self.np = neopixel.NeoPixel(Pin(int(rgb_gpio)), 1)
                self.np[0] = (0, 0, 0)
                self.np.write()
            except Exception:
                self.np = None

    def brightness(self, value):
        self.bright = value
        self.dim_level = max(2, value // 6)
        if not self.dim:
            self._lcd(value)

    def _lcd(self, value):
        try:
            M5.Display.setBrightness(value)
        except Exception:
            pass

    def _led(self, on):
        if self.led is None:
            return
        try:
            self.led.value(1 if on else 0)
        except Exception:
            self.led = None

    def _rgb(self, r, g, b):
        if self.np is None:
            return
        try:
            self.np[0] = (r, g, b)
            self.np.write()
        except Exception:
            self.np = None

    def update(self, now, active):
        if not active:
            if self.dim:
                self.dim = False
                if self.backlight:
                    self._lcd(self.bright)
            self._led(False)
            self._rgb(0, 0, 0)
            return
        if time.ticks_diff(now, self.at) < self.PERIOD:
            return
        self.at = now
        self.dim = not self.dim
        if self.dim:
            self._led(True)
            if self.backlight:
                self._lcd(self.dim_level)
            self._rgb(48, 0, 0)
        else:
            self._led(False)
            if self.backlight:
                self._lcd(self.bright)
            self._rgb(255, 0, 0)
