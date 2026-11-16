"""Merge every saved Windows Wi-Fi profile into firmware/config.json.

The AtomS3 boots from the list in config.json, so every network this PC knows
should be in there.  Existing entries and their order are kept (the first one
is the home network); only missing SSIDs are appended.

    python tools/wifi_import.py
    python tools/wifi_import.py --home YOUR_HOME_SSID
    python tools/wifi_import.py --dry-run

config.json contains passwords, so it stays local and is never committed.
"""
import argparse
import json
import locale
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "firmware", "config.json")


def _run(args):
    try:
        p = subprocess.run(args, capture_output=True)
    except OSError as e:
        print("netsh failed:", e)
        return ""
    enc = locale.getpreferredencoding(False) or "utf-8"
    data = p.stdout or b""
    try:
        return data.decode(enc)
    except Exception:
        return data.decode("utf-8", "replace")


def profiles():
    """All saved user profile names."""
    out = []
    for line in _run(["netsh", "wlan", "show", "profiles"]).splitlines():
        if ":" not in line:
            continue
        s = line.split(":", 1)[1].strip()
        if s and s != "<なし>" and s.lower() != "none":
            out.append(s)
    return out


def password(ssid):
    """Cleartext key for one profile, or '' for open networks."""
    text = _run(["netsh", "wlan", "show", "profile", "name=" + ssid,
                 "key=clear"])
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, val = line.split(":", 1)
        low = label.strip().lower()
        if low.startswith("key content") or "キー コンテンツ" in label:
            return val.strip()
    return ""


def load():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", help="SSID to move to the front (home network)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load()
    nets = cfg.get("wifi")
    if isinstance(nets, dict) and isinstance(nets.get("networks"), list):
        nets = nets["networks"]
    elif isinstance(nets, dict):
        nets = [nets]
    elif not isinstance(nets, list):
        nets = []
    nets = [n for n in nets if isinstance(n, dict) and n.get("ssid")]

    have = {n["ssid"] for n in nets}
    added = []
    for ssid in profiles():
        if ssid in have:
            continue
        pw = password(ssid)
        nets.append({"ssid": ssid, "password": pw})
        have.add(ssid)
        added.append(ssid)

    if args.home:
        for i, n in enumerate(nets):
            if n["ssid"] == args.home:
                nets.insert(0, nets.pop(i))
                break

    cfg["wifi"] = nets
    print("%d saved profiles, %d in config" % (len(profiles()), len(have)))
    if added:
        print("added:", ", ".join(added))
    else:
        print("already up to date - every saved profile is in config.json")
    if not added and not args.home:
        return 0
    if args.dry_run:
        print("dry run: config.json not changed")
        return 0
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote", CONFIG, "(%d networks, home %r)" % (len(nets), nets[0]["ssid"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
