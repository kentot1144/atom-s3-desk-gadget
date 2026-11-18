"""Execute the generated .m5f2 launcher against a mock filesystem.

Verifies that the launcher reconstructs every embedded module byte-for-byte,
creates the desk/ directory, honours "only if missing" for config.json, and
hands over to app.run(wlan).
"""
import builtins
import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
M5F = os.path.join(ROOT, "uiflow", "atoms3-desk-gadget.m5f2")
FW = os.path.join(ROOT, "firmware")


class FakeWlan:
    pass


def main():
    doc = json.load(open(M5F, encoding="utf-8"))
    code = doc["pythonCode"]
    tmp = tempfile.mkdtemp(prefix="m5f2test-")
    os.mkdir(os.path.join(tmp, "flash"))  # stand in for /flash
    written = {}
    real_open = builtins.open

    def fake_open(path, mode="r", *a, **kw):
        if "w" in mode:
            return _Writer(path, written)
        return real_open(path, mode, *a, **kw)

    class _Writer(io.StringIO):
        def __init__(self, path, sink):
            super().__init__()
            self._path, self._sink = path, sink

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._sink[self._path] = self.getvalue()
            return False

        def close(self):
            self._sink[self._path] = self.getvalue()
            super().close()

    # A tiny /flash-like filesystem rooted in tmp.
    class FakeOS(types.ModuleType):
        def stat(self, path):
            full = os.path.join(tmp, path.lstrip("/"))
            if not os.path.exists(full):
                raise OSError("missing")
            return os.stat(full)

        def mkdir(self, path):
            os.mkdir(os.path.join(tmp, path.lstrip("/")))

    fake_os = FakeOS("os")
    called = []
    fake_app = types.ModuleType("app")
    fake_app.run = lambda wlan: called.append(wlan)

    env = {
        "__name__": "__main__",
        "gc": types.SimpleNamespace(collect=lambda: None),
        "os": fake_os,
        "sys": sys,
        "time": types.SimpleNamespace(ticks_ms=lambda: 0),
        "network": types.SimpleNamespace(
            WLAN=lambda mode: FakeWlan(), STA_IF=1),
        "app": fake_app,
    }
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name in env:
            return env[name]
        return real_import(name, *a, **kw)

    fake_builtins = types.ModuleType("builtins")
    fake_builtins.__dict__.update(vars(builtins))
    fake_builtins.__import__ = fake_import
    fake_builtins.open = fake_open
    env["__builtins__"] = fake_builtins

    try:
        exec(compile(code, "<m5f2>", "exec"), env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    expected = ["/flash/config.json", "/flash/app.py"] + [
        "/flash/desk/" + n for n in os.listdir(os.path.join(FW, "desk"))
        if n.endswith(".py")
    ]
    for path in expected:
        assert path in written, "not written: " + path
    for path, data in written.items():
        rel = path[len("/flash/"):]
        name = "config.json.example" if rel == "config.json" else rel
        src = os.path.join(FW, *name.split("/"))
        assert data == open(src, encoding="utf-8").read(), "mismatch: " + rel
    assert len(called) == 1 and isinstance(called[0], FakeWlan), "app.run not called"
    print("OK: launcher wrote %d files and started the app" % len(written))


if __name__ == "__main__":
    main()
