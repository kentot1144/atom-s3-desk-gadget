"""Flash the AtomS3 desk firmware over the MicroPython raw REPL.

Usage:
    python tools/push.py                 # COM3
    set PORT=COM5 && python tools/push.py

Every file under firmware/ is written to /flash on the board, then the board
is reset. config.json is required; copy firmware/config.json.example first.
"""
import base64
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "firmware")
PORT = os.environ.get("PORT", "COM3")
CHUNK = 1536
LOG = os.path.join(os.path.dirname(HERE), "push.log")

SKIP_NAMES = {"config.json.example", ".DS_Store"}
SKIP_DIRS = {"__pycache__", ".git"}


def firmware_files():
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name in SKIP_NAMES or name.endswith((".pyc", ".log")):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            out.append(rel)
    return out


class Dev:
    def __init__(self, port, log=None):
        import serial

        self.s = serial.Serial()
        self.s.port = port
        self.s.baudrate = 115200
        self.s.timeout = 0.15
        self.s.dtr = False
        self.s.rts = False
        self.s.open()
        self.log = open(log, "w", encoding="utf-8") if log else None

    def _rd(self, t):
        buf = b""
        end = time.time() + t
        while time.time() < end:
            try:
                c = self.s.read(self.s.in_waiting or 1)
            except Exception:
                return buf + b"<PORT_LOST>"
            if c:
                buf += c
        return buf

    def wait(self, marker, timeout):
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            try:
                c = self.s.read(self.s.in_waiting or 1)
            except Exception:
                return buf + b"<PORT_LOST>", False
            if c:
                buf += c
                if marker in buf:
                    return buf, True
        return buf, False

    def enter_raw_repl(self):
        self.s.write(b"\x02")
        time.sleep(0.2)
        for _ in range(2):
            self.s.write(b"\x03")
            time.sleep(0.15)
        time.sleep(0.3)
        self._rd(0.3)
        self.s.write(b"\x01")
        _, ok = self.wait(b"raw REPL", 3.0)
        return ok

    def cmd(self, code, timeout=40.0):
        self.s.write(code.encode() + b"\x04")
        resp, ok = self.wait(b"\x04\x04>", timeout)
        if resp.startswith(b"OK"):
            resp = resp[2:]
        if resp.endswith(b"\x04\x04>"):
            resp = resp[:-5]
        body, _, err = resp.partition(b"\x04")
        if self.log:
            self.log.write("\n===== complete=%s =====\n%s\n----- OUT -----\n"
                           % (ok, code))
            self.log.write(body.decode("utf-8", "replace"))
            if err.strip():
                self.log.write("\n---ERROR---\n" + err.decode("utf-8", "replace"))
            self.log.write("\n===== END =====\n")
            self.log.flush()
        self._rd(0.15)
        return body.decode("utf-8", "replace"), err.decode("utf-8", "replace"), ok

    def close(self):
        try:
            self.s.close()
        finally:
            if self.log:
                self.log.close()


def main():
    if not os.path.isdir(ROOT):
        print("firmware folder not found:", ROOT)
        return 1
    if not os.path.isfile(os.path.join(ROOT, "config.json")):
        print("firmware/config.json is missing.")
        print("Copy firmware/config.json.example to firmware/config.json first.")
        return 1

    files = firmware_files()
    print("port:", PORT)

    d = Dev(PORT, log=LOG)
    try:
        if not d.enter_raw_repl():
            print("could not enter the raw REPL (is the board on %s?)" % PORT)
            return 1
        d.cmd("import os\n"
              "for p in ('/flash/libs', '/flash/libs/desk'):\n"
              "    try: os.mkdir(p)\n"
              "    except Exception: pass\n"
              "print('dirs ok')")

        for rel in files:
            local = os.path.join(ROOT, rel.replace("/", os.sep))
            remote = "/flash/" + rel
            data = open(local, "rb").read()
            d.cmd("import os\ntry: os.remove('%s')\nexcept Exception: pass" % remote)
            b64 = base64.b64encode(data).decode()
            chunks = 0
            for i in range(0, len(b64), CHUNK):
                part = b64[i:i + CHUNK]
                d.cmd("import ubinascii\n"
                      "f=open('%s','ab')\nf.write(ubinascii.a2b_base64('%s'))\n"
                      "f.close()" % (remote, part), timeout=20)
                chunks += 1
            out, err, _ = d.cmd("import os\nprint(os.stat('%s')[6])" % remote)
            size = out.strip().splitlines()[-1] if out.strip() else "?"
            print("%-28s %6d bytes  (%d chunks)  remote=%s"
                  % (rel, len(data), chunks, size))
            if err.strip():
                print("   err:", err.strip()[:200])

        print("resetting board...")
        d.cmd("import machine, time\ntime.sleep_ms(100)\nmachine.reset()", timeout=3)
    finally:
        d.close()

    time.sleep(5)
    try:
        import serial

        s = serial.Serial(PORT, 115200, timeout=0.2)
        s.dtr = False
        s.rts = False
        end = time.time() + 8
        buf = b""
        while time.time() < end:
            c = s.read(s.in_waiting or 1)
            if c:
                buf += c
        s.write(b"\x03")
        time.sleep(0.3)
        end = time.time() + 2
        while time.time() < end:
            c = s.read(s.in_waiting or 1)
            if c:
                buf += c
        s.close()
        text = buf.decode("utf-8", "replace")
        print("--- boot log ---")
        print(text[-3000:])
    except Exception as e:
        print("could not read the boot log:", repr(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
