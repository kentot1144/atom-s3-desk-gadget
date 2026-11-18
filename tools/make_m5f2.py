"""Build a UIFlow2-importable .m5f2 for the AtomS3 desk gadget.

UIFlow2's .m5f2 format is JSON with one code field, ``pythonCode``.  Our
firmware is deliberately split into small modules: MicroPython compiles a
whole file before running it, and a single ~90 KB file will not compile in the
ESP32-S3 boot heap.  This tool embeds those modules as text in a tiny launcher
which writes them to flash on first run and then starts the normal app boot.

Only ``config.json.example`` is embedded (as a placeholder ``config.json``).
The real config, Wi-Fi passwords, tokens and personal data are never read.

    python tools/make_m5f2.py [out.m5f2]
    python tools/make_m5f2.py --verify [file.m5f2]
"""
import ast
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
DEFAULT_OUT = os.path.join(ROOT, "uiflow", "atoms3-desk-gadget.m5f2")

# (relative path in /flash, create only if missing).  The desk modules are
# enumerated from disk so a new module can never be left out by accident.
FILES = [("config.json", True)]  # built from config.json.example below

# Minimal UIFlow2 setup/loop workspace.  The importer requires an element with
# id="setup_block"; the actual program lives in pythonCode (custom-code mode).
BLOCKLY = (
    '<variables></variables>'
    '<block type="basic_on_setup" id="setup_block" deletable="false" '
    'x="50" y="50"><mutation isBegin="true"></mutation>'
    '<field name="UPDATEOP">true</field><statement name="FUNC">'
    '<block type="system_m5_begin" id="system_m5_begin"></block>'
    '</statement></block>'
    '<block type="basic_on_loop" id="loop_block" deletable="false" '
    'x="70" y="270"><mutation isUpdate="true"></mutation>'
    '<field name="UPDATEOP">true</field><statement name="FUNC">'
    '<block type="system_m5_update" id="system_m5_update"></block>'
    '</statement></block>'
)

LAUNCHER_HEAD = '''"""AtomS3 Desk Gadget - UIFlow2 Python-mode launcher.

Writes the split desk modules to flash, then starts the normal app boot.
Safe to share: only placeholder config is embedded (config.json.example).
After importing, edit /flash/config.json in the UIFlow file manager.
"""
import gc
import os
import sys
import time
import network

# Grab the radio while the heap is still fresh, just like the original boot.
gc.collect()
wlan = network.WLAN(network.STA_IF)

try:
    os.stat("/flash")
    base = "/flash"
except OSError:
    base = ""


def _mk(path):
    d = base
    for part in path.split("/")[:-1]:
        d = (d + "/" + part) if d else part
        try:
            os.stat(d)
        except OSError:
            try:
                os.mkdir(d)
            except OSError:
                pass


def _w(path, data, only_if_missing=False):
    full = (base + "/" + path) if base else path
    if only_if_missing:
        try:
            os.stat(full)
            return
        except OSError:
            pass
    _mk(path)
    f = open(full, "w")
    try:
        f.write(data)
    finally:
        f.close()

'''

LAUNCHER_TAIL = '''
del _w, _mk

# Same entry point as the standalone firmware (desk.app.run calls M5.begin).
import app

app.run(wlan)
'''


def literal(text):
    """Return a MicroPython-safe string literal for ASCII source text."""
    for ch in text:
        n = ord(ch)
        if n in (9, 10, 13):
            continue
        if n < 32 or n > 126:
            raise ValueError("non-ASCII/control character in embedded source")
    return json.dumps(text)


def source_files():
    desk = os.path.join(FW, "desk")
    mods = sorted(f for f in os.listdir(desk) if f.endswith(".py"))
    return ["app.py"] + ["desk/" + f for f in mods]


def file_specs():
    return FILES + [(rel, False) for rel in source_files()]


def read_sources():
    cfg = open(os.path.join(FW, "config.json.example"), encoding="utf-8").read()
    srcs = {"config.json": cfg}
    for rel in source_files():
        srcs[rel] = open(os.path.join(FW, *rel.split("/")), encoding="utf-8").read()
    return srcs


def build_python_code(srcs):
    lines = [LAUNCHER_HEAD]
    for rel, only_missing in file_specs():
        extra = ", True" if only_missing else ""
        lines.append("_w(%s, %s%s)\n" % (literal(rel), literal(srcs[rel]), extra))
    lines.append(LAUNCHER_TAIL)
    code = "".join(lines)
    compile(code, "<m5f2-launcher>", "exec")  # syntax check on the host
    return code


def build_m5f(python_code):
    return {
        "version": "V2.0",
        "versionNumber": "V2.5.4",
        "type": "atoms3",
        "components": [],
        "resources": [],
        "units": [],
        "hats": [],
        "caps": [],
        "chains": [],
        "bases": [],
        "plcmodules": [],
        "stamps": [],
        "tab5modules": [],
        "poep4modules": [],
        "i2cs": [],
        "chainBus": [],
        "blockly": BLOCKLY,
        "screen": [{
            "simulationName": "Built-in",
            "type": "builtin",
            "width": 128,
            "height": 128,
            "scale": 1,
            "screenName": "",
            "blockId": "",
            "screenColorType": 0,
            "id": "builtin",
            "createTime": int(time.time() * 1000),
        }],
        "logicWhenNum": 0,
        "pythonCode": python_code,
        "customList": [],
    }


def embedded_sources(code):
    """Recover the _w(path, data) payloads from the launcher for verification."""
    out = {}
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Name) or fn.id != "_w" or len(node.args) < 2:
            continue
        path, data = node.args[0], node.args[1]
        if isinstance(path, ast.Constant) and isinstance(data, ast.Constant):
            out[path.value] = data.value
    return out


def verify(path):
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["version"] == "V2.0", "bad version"
    assert doc["type"] == "atoms3", "bad device type"
    assert 'id="setup_block"' in doc["blockly"], "missing setup_block"
    assert doc["screen"][0]["width"] == 128, "bad screen width"
    srcs = embedded_sources(doc["pythonCode"])
    expected = read_sources()
    assert set(srcs) == set(expected), "unexpected embedded file list"
    for rel, text in expected.items():
        assert srcs[rel] == text, "embedded source differs: " + rel
    blob = json.dumps(doc)
    # Placeholders such as YOUR_WIFI_SSID are intentional; only real values
    # must never appear.
    for secret in ("aterm-", "9732c1fe", "ikd.f5.si", "192.168.0.22",
                   "kentot1144@gmail.com"):

        assert secret not in blob, "secret-looking string in file: " + secret
    print("OK: %s (%d bytes, %d embedded files)" %
          (os.path.basename(path), os.path.getsize(path), len(srcs)))


def main():
    verify_mode = "--verify" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else DEFAULT_OUT
    if verify_mode:
        verify(out)
        return
    srcs = read_sources()
    code = build_python_code(srcs)
    doc = build_m5f(code)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    verify(out)


if __name__ == "__main__":
    main()
