"""Save Edge/Chrome Spotify cookies to hub/spotify_cookies.json.

Run this once (as Administrator, because Edge cookies are app-bound
encrypted since Chromium 127):

    python tools\\get_spotify_cookies.py

Then make sure Edge has been used to log in at https://open.spotify.com .
A transcript is written to hub/spotify_cookies.log so failures are visible
even when the elevated console closes.
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.join(os.path.dirname(HERE), "hub")
TMP = os.path.join(os.environ.get("TEMP", "."), "opencode")
if not os.path.isdir(TMP):
    TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "spotify_cookies.json")
LOG = os.path.join(TMP, "spotify_cookies.log")

_lines = []


def say(msg=""):
    print(msg)
    _lines.append(str(msg))


def main():
    say("user=%s admin=%s" % (os.environ.get("USERNAME"),
                              _is_admin()))
    try:
        import browser_cookie3
    except ImportError:
        say("browser_cookie3 is missing.  Run:")
        say("  python -m pip install browser_cookie3")
        return 1

    say("browser_cookie3 %s" % getattr(browser_cookie3, "__version__", "?"))

    for name in ("edge", "chrome", "brave", "firefox"):
        fn = getattr(browser_cookie3, name, None)
        if fn is None:
            continue
        try:
            jar = fn(domain_name=".spotify.com")
            cookies = {c.name: c.value for c in jar if "spotify" in (c.domain or "")}
        except Exception as e:
            say("%-7s skipped: %s" % (name, repr(e)[:160]))
            continue
        say("%-7s returned %d spotify cookies: %s"
            % (name, len(cookies), ",".join(sorted(cookies)[:12])))
        if not cookies:
            continue
        if "sp_dc" not in cookies:
            say("%-7s has no sp_dc, ignoring" % name)
            continue
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        say("%-7s saved %d cookies to %s" % (name, len(cookies), OUT))
        say("sp_dc length: %d" % len(cookies["sp_dc"]))
        try:
            import shutil

            dest = os.path.join(HUB, "spotify_cookies.json")
            shutil.copy2(OUT, dest)
            say("copied to %s" % dest)
        except Exception as e:
            say("could not copy into hub folder: %s" % repr(e)[:120])
        return 0

    say("")
    say("Nothing saved.")
    say("1. Open Edge, log in at https://open.spotify.com")
    say("2. Run this script again as administrator.")
    return 2


def _is_admin():
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


if __name__ == "__main__":
    code = 3
    try:
        code = main()
    except Exception:
        say(traceback.format_exc())
    try:
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
    except Exception:
        pass
    sys.exit(code)
