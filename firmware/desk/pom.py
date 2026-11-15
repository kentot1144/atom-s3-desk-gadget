"""Local pomodoro timer, ambient-light brightness and the alert LED."""
import time
from desk.ui import LCD

# ------------------------------------------------------------------ pomodoro
class Pomodoro:
    def __init__(self, cfg):
        p = cfg.get("pomodoro") or {}
        self.focus = int(p.get("focus", 25)) * 60000
        self.brk = int(p.get("break", 5)) * 60000
        self.mode = "focus"
        self.running = False
        self.rem = self.focus
        self.end = 0
        self.sessions = 1

    def toggle(self):
        if self.running:
            self.rem = time.ticks_diff(self.end, time.ticks_ms())
            if self.rem < 0:
                self.rem = 0
            self.running = False
        else:
            self.end = time.ticks_add(time.ticks_ms(), self.rem)
            self.running = True

    def update(self, now):
        if not self.running:
            return False
        rem = time.ticks_diff(self.end, now)
        if rem <= 0:
            if self.mode == "focus":
                self.mode = "break"
                self.rem = self.brk
            else:
                self.mode = "focus"
                self.sessions += 1
                self.rem = self.focus
            self.end = time.ticks_add(now, self.rem)
            return True
        self.rem = rem
        return False


# ------------------------------------------------------------- brightness
class Light:
    def __init__(self, cfg):
        d = cfg.get("display") or {}
        self.on = bool(d.get("auto", True))
        self.min = int(d.get("min", 10))
        self.max = int(d.get("max", d.get("brightness", 120)))
        if self.min < 1:
            self.min = 1
        if self.max < self.min:
            self.max = self.min
        self.interval = int(d.get("interval", 2000))
        self.pct = 100
        self.avg = None
        self.value = int(d.get("brightness", self.max))
        if self.value < self.min:
            self.value = self.min
        elif self.value > self.max:
            self.value = self.max
        self.at = 0
        self.adc = None
        if self.on:
            try:
                from machine import ADC, Pin
                pin = int(d.get("light_pin", 6))
                try:
                    self.adc = ADC(Pin(pin), atten=ADC.ATTN_11DB)
                except TypeError:
                    self.adc = ADC(Pin(pin))
                    try:
                        self.adc.atten(ADC.ATTN_11DB)
                    except Exception:
                        pass
            except Exception:
                self.adc = None
        if self.adc is None:
            self.on = False

    def tick(self, now):
        if not self.on or self.adc is None:
            return None
        if time.ticks_diff(now, self.at) < self.interval:
            return None
        self.at = now
        try:
            a = self.adc.read()
            b = self.adc.read()
            c = self.adc.read()
        except Exception:
            return None
        raw = sorted((a, b, c))[1]   # median ignores single-sample spikes
        if self.avg is None:
            self.avg = raw
        else:
            self.avg = (self.avg * 3 + raw) // 4
        self.pct = max(0, min(100, self.avg * 100 // 4095))
        adj = min(100, int(self.pct ** 0.5 * 10))
        target = self.min + (self.max - self.min) * adj // 100
        # Slew-limit plus a deadband: a noisy sensor or light that shines on
        # the panel must not make the backlight pulse between bright and off.
        if target > self.value + 18:
            target = self.value + 18
        elif target < self.value - 18:
            target = self.value - 18
        if abs(target - self.value) < 6:
            return None
        self.value = target
        return target


class Led:
    """Critical-service LED.  The screen backlight can pulse as a fallback."""

    PERIOD = 900

    def __init__(self, bright, cfg):
        a = cfg.get("alerts") or {}
        self.bright = bright
        self.dim_level = max(2, bright // 6)
        self.backlight = bool(a.get("backlight", False))
        self.at = 0
        self.dim = False
        self.led = None
        try:
            from machine import Pin
            gpio = int(a.get("led_gpio", 5))
            if gpio:
                self.led = Pin(gpio, Pin.OUT)
                self.led.value(0)
        except Exception:
            self.led = None

    def _lcd(self, v):
        try:
            LCD.setBrightness(v)
        except Exception:
            pass

    def _pin(self, on):
        if self.led is None:
            return
        try:
            self.led.value(1 if on else 0)
        except Exception:
            self.led = None

    def set_bright(self, v):
        self.bright = v
        self.dim_level = max(2, v // 6)
        if not self.dim:
            self._lcd(v)

    def flash(self, on):
        self._pin(on)

    def update(self, now, active):
        if not active:
            if self.dim:
                self.dim = False
                if self.backlight:
                    self._lcd(self.bright)
            self._pin(False)
            return
        if time.ticks_diff(now, self.at) < self.PERIOD:
            return
        self.at = now
        self.dim = not self.dim
        self._pin(self.dim)
        if self.backlight:
            self._lcd(self.dim_level if self.dim else self.bright)
