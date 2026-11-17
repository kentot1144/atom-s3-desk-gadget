#!/usr/bin/env python3
"""Install the fixed Spotify bridge on the PC that runs the desk hub.

Run this ON the hub PC (Kento_pc), not on the laptop:

    python tools\\deploy_hub.py --dry-run
    python tools\\deploy_hub.py --yes
    python tools\\deploy_hub.py --yes --watch

It backs up the old hub/spotify_bridge.py, writes the fixed one, restarts the
hub, waits for /ping, and (with --watch) prints /state?only=sp once a second
so you can pause Spotify and see is_playing flip to false.

The new bridge comes from this repo's hub/spotify_bridge.py when the script is
run from tools/.  If that file is not there it downloads the public copy.  Use
--source to point at a local file or URL.

Useful checks:
    python tools\\deploy_hub.py --probe-only --watch
    python tools\\deploy_hub.py --probe-only --url http://ikd.f5.si:8765
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

RAW_URL = ("https://raw.githubusercontent.com/kentot1144/"
           "atom-s3-desk-gadget/{ref}/hub/spotify_bridge.py")
MARKERS = ("def status", "def _refresh_spotapi", "_advance_ok", "is_paused")


def log(msg=""):
    print(msg, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_source(args, script_dir):
    """Return (text, description) for the new bridge."""
    if args.source:
        if args.source.startswith(("http://", "https://")):
            url = args.source
        else:
            p = Path(args.source).expanduser()
            if not p.exists():
                raise SystemExit("source not found: %s" % p)
            return p.read_text(encoding="utf-8"), str(p)
    else:
        # Running from tools/ in a checkout: use the fixed file next to it.
        local = script_dir.parent / "hub" / "spotify_bridge.py"
        if script_dir.name.lower() == "tools" and local.exists():
            return local.read_text(encoding="utf-8"), str(local)
        url = RAW_URL.format(ref=args.ref)
    req = urllib.request.Request(
        url, headers={"User-Agent": "AtomS3-Desk-Deploy/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8"), url


def validate(text):
    missing = [m for m in MARKERS if m not in text]
    if missing:
        raise SystemExit("this does not look like the fixed bridge "
                         "(missing: %s)" % ", ".join(missing))


def powershell():
    return shutil.which("powershell") or shutil.which("pwsh")


def run_ps(command):
    ps = powershell()
    if not ps:
        raise RuntimeError("PowerShell was not found")
    return subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True)


def find_hub_procs():
    """Return [(pid, commandline, cwd)] for python processes running hub.py."""
    try:
        import psutil
    except ImportError:
        return find_hub_procs_ps()
    out = []
    for p in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            info = p.info
            cmd = " ".join(info.get("cmdline") or [])
            low = cmd.lower()
            if "hub.py" not in low or "deploy_hub.py" in low:
                continue
            out.append((p.pid, cmd, str(info.get("cwd") or "")))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def find_hub_procs_ps():
    ps = powershell()
    if not ps:
        return []
    cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match 'hub[.]py' "
        "-and $_.CommandLine -notmatch 'deploy_hub[.]py' } | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    r = run_ps(cmd)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return []
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for item in data or []:
        try:
            pid = int(item.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            out.append((pid, str(item.get("CommandLine") or ""), ""))
    return out


def stop_pids(pids):
    if not pids:
        return
    cmd = "; ".join(
        "Stop-Process -Id %d -Force -ErrorAction SilentlyContinue" % p
        for p in pids)
    r = run_ps(cmd)
    if r.returncode != 0:
        log("stop failed: %s" % ((r.stderr or r.stdout or "").strip()))


def start_hub(hub_dir):
    vbs = hub_dir / "start_hub_silent.vbs"
    if vbs.exists() and shutil.which("wscript"):
        subprocess.Popen(["wscript.exe", str(vbs)], cwd=str(hub_dir),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "wscript start_hub_silent.vbs"
    bat = hub_dir / "start_hub.bat"
    if bat.exists():
        subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(hub_dir),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "start_hub.bat"
    pyw = shutil.which("pythonw") or sys.executable.replace(
        "python.exe", "pythonw.exe")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008      # DETACHED_PROCESS
    else:
        kw["start_new_session"] = True
    subprocess.Popen([pyw, str(hub_dir / "hub.py")], cwd=str(hub_dir),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    return "%s hub.py" % pyw


def http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def http_json(url, token=None, timeout=5):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Atom-Key", token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_ping(base, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if http_ok(base + "/ping"):
            return True
        time.sleep(0.5)
    return False


def read_token(hub_dir, explicit):
    if explicit:
        return explicit.strip()
    for name in ("hub_config.json", "hub_config.json.example"):
        p = hub_dir / name
        if p.exists():
            try:
                return str((json.loads(p.read_text(encoding="utf-8")) or {})
                           .get("token") or "").strip()
            except Exception:
                pass
    return ""


def print_state(base, token):
    try:
        d = http_json(base + "/state?only=sp", token)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log("state: unauthorized (pass --token or check hub_config.json)")
        else:
            log("state: HTTP %s" % e.code)
        return None
    except Exception as e:
        log("state: %s" % e)
        return None
    sp = (d or {}).get("sp") or {}
    log("  playing=%-5s pos=%-7s dur=%-7s stalled=%-5s song=%r" % (
        sp.get("is_playing"), sp.get("progress_ms"), sp.get("duration_ms"),
        sp.get("stalled"), sp.get("song")))
    return sp


def watch(base, token, seconds):
    log("Watching %s/state for %d s - pause/resume Spotify now."
        % (base, seconds))
    end = time.time() + seconds
    while time.time() < end:
        print_state(base, token)
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(
        description="Install the fixed Spotify bridge and restart the hub.")
    ap.add_argument("--hub-dir", default=os.environ.get("HUB_DIR", r"C:\KW\hub"),
                    help="folder containing hub.py (default C:\\KW\\hub)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--url", default="",
                    help="base URL to probe (default http://127.0.0.1:PORT)")
    ap.add_argument("--source", default="",
                    help="local file or URL for spotify_bridge.py")
    ap.add_argument("--ref", default="main",
                    help="git ref to download (default main)")
    ap.add_argument("--token", default="",
                    help="hub token (default: hub_config.json)")
    ap.add_argument("--watch", nargs="?", const=20, default=0, type=int,
                    metavar="SECONDS", help="watch /state after restart")
    ap.add_argument("--dry-run", action="store_true",
                    help="download and check, change nothing")
    ap.add_argument("--probe-only", action="store_true",
                    help="just print /state, do not install")
    ap.add_argument("--no-restart", action="store_true",
                    help="install but do not stop/start the hub")
    ap.add_argument("--force", action="store_true",
                    help="allow a hub.py in a different folder")
    ap.add_argument("--yes", action="store_true",
                    help="do not ask before restarting")
    args = ap.parse_args()

    hub_dir = Path(args.hub_dir).expanduser().resolve()
    script_dir = Path(__file__).resolve().parent
    base = (args.url.rstrip("/") if args.url
            else "http://127.0.0.1:%d" % args.port)
    token = read_token(hub_dir, args.token)

    if args.probe_only:
        print_state(base, token)
        if args.watch:
            watch(base, token, args.watch)
        return 0

    log("hub folder : %s" % hub_dir)
    if not (hub_dir / "hub.py").exists():
        log("ERROR: hub.py was not found there. Pass --hub-dir.")
        return 1

    try:
        text, source = read_source(args, script_dir)
    except Exception as e:
        log("ERROR: could not read the new bridge: %s" % e)
        return 1
    validate(text)

    target = hub_dir / "spotify_bridge.py"
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    log("source     : %s" % source)
    log("installed  : %s (%s, sha %s)" % (
        target, "present" if old else "missing",
        sha256(target)[:12] if target.exists() else "-"))
    log("bridge     : %s" % ("already up to date" if old == text
                             else "needs update"))

    procs = find_hub_procs()
    target_l = str(hub_dir).lower()

    def in_target(cmd, cwd):
        return (target_l in cmd.lower()
                or (cwd or "").lower() == target_l)

    mine = [(p, c, w) for p, c, w in procs if in_target(c, w)]
    mine_ids = set(p for p, _, _ in mine)
    others = [(p, c, w) for p, c, w in procs if p not in mine_ids]
    if mine:
        log("hub process: %s" % ", ".join(str(p) for p, _, _ in mine))
    else:
        log("hub process: not running")
    if others:
        log("WARNING: other hub.py processes:")
        for p, c, w in others:
            log("  pid %d  %s%s" % (p, c, ("  cwd=" + w) if w else ""))
        if not args.force:
            log("Refusing to touch a hub in another folder (use --force).")
            return 1

    if args.dry_run:
        log("dry run    : no files changed, no processes stopped")
        if args.watch:
            watch(base, token, args.watch)
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            log("Refusing to restart without --yes (non-interactive).")
            return 2
        ans = input("Install the bridge and restart the hub? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            log("cancelled")
            return 2

    if old != text:
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = target.with_name("spotify_bridge.py.bak-" + stamp)
            shutil.copy2(target, backup)
            log("backup     : %s" % backup)
            for b in sorted(target.parent.glob("spotify_bridge.py.bak-*"))[:-5]:
                try:
                    b.unlink()
                except OSError:
                    pass
        tmp = target.with_name("spotify_bridge.py.new")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
        log("wrote      : %s (%d bytes)" % (target, target.stat().st_size))
    else:
        log("wrote      : skipped (already up to date)")

    if args.no_restart:
        log("restart    : skipped (--no-restart)")
    else:
        if mine:
            log("stopping   : %s" % ", ".join(str(p) for p, _, _ in mine))
            stop_pids([p for p, _, _ in mine])
            time.sleep(0.5)
            left = [p for p, c, w in find_hub_procs() if in_target(c, w)]
            if left:
                log("ERROR: still running (run this as administrator): %s" % left)
                return 1
        if not http_ok(base + "/ping"):
            log("starting   : %s" % start_hub(hub_dir))
        else:
            log("starting   : watchdog already restarted it")
        if not wait_ping(base, 40):
            log("ERROR: /ping did not answer. Check the hub, or reboot the PC.")
            return 1
        log("ping       : ok")

    print_state(base, token)
    if args.watch:
        watch(base, token, args.watch)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
