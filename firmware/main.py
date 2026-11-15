# Thin display boot: WiFi first, then the app.
#
# The ESP32-S3 has only a small IDF heap free at boot.  MicroPython compiles a
# whole file before running it, so this file stays tiny and grabs the radio
# before anything else is imported.  The app itself lives in /flash/desk/*,
# split into small modules so each one fits the heap.
import gc
import sys
import time

import network

gc.collect()
print("boot free", gc.mem_free())
wlan = network.WLAN(network.STA_IF)
gc.collect()
print("boot wlan", gc.mem_free())

try:
    import app

    print("boot app", gc.mem_free())
    app.run(wlan)
except Exception as e:
    print("boot fail", repr(e))
    sys.print_exception(e)
    # The screen is the only console we have.  Print the error and stay put
    # instead of vanishing into a reset loop.
    try:
        import M5

        M5.Display.fillScreen(0x000000)
        M5.Display.setTextColor(0xF85149, 0x000000)
        M5.Display.setCursor(4, 4)
        M5.Display.print("BOOT ERROR")
        msg = repr(e)
        y = 20
        while msg and y < 120:
            M5.Display.setCursor(4, y)
            M5.Display.print(msg[:21])
            msg = msg[21:]
            y += 12
    except Exception:
        pass
    time.sleep(10)
