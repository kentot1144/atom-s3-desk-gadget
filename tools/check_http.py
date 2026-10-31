"""Quick HTTP reachability probe (no redirects, ignores bad certificates).

Usage:
    python tools/check_http.py https://example.com
    python tools/check_http.py https://example.com http://192.168.1.10/
"""
import ssl
import sys
import time
import urllib.error
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(
    NoRedirect, urllib.request.HTTPSHandler(context=ctx))


def check(url, timeout=5):
    t0 = time.time()
    ms = lambda: "%.0fms" % ((time.time() - t0) * 1000)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AtomS3-Desk/1.0"})
        with opener.open(req, timeout=timeout) as r:
            print("OK     ", url, r.status, ms())
    except urllib.error.HTTPError as e:
        print("HTTPERR", url, e.code, ms())
    except Exception as e:
        print("ERR    ", url, repr(e), ms())


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    for url in argv:
        check(url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
