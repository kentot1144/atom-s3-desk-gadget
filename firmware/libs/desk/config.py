import json

DEFAULTS = {
    "wifi": {"ssid": "", "password": ""},
    "location": {"lat": 35.68, "lon": 139.69, "tz": 9, "name": "Tokyo"},
    "hub": {"url": "", "urls": []},
    "display": {
        "brightness": 120, "auto": True, "light_pin": 6,
        "min": 10, "max": 200, "interval": 2000,
    },
    "alerts": {
        "blink": True, "led_gpio": 5, "backlight": False, "rgb_gpio": 0,
    },
    "pomodoro": {"focus": 25, "break": 5},
}

PATH = "/flash/config.json"


def load(path=PATH):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    out = {}
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            merged = dict(v)
            got = cfg.get(k)
            if isinstance(got, dict):
                merged.update(got)
            out[k] = merged
        else:
            out[k] = cfg.get(k, v)
    return out


def save(cfg, path=PATH):
    with open(path, "w") as f:
        json.dump(cfg, f)
