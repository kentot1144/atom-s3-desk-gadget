"""Spotify bridge for the AtomS3 desk hub.

Two backends, picked automatically:

  spotapi  - used when hub/spotify_cookies.json exists (SpotAPI, no Premium
             needed).  Login + websocket state are expensive, so everything
             runs in one worker thread and HTTP only reads a cached dict.
  smtc     - Windows Global System Media Transport Controls.  Controls
             whatever media session is active (Edge Spotify tab, Spotify
             desktop app, ...).  No login or cookies required.

Endpoints exposed by hub.py:
  GET /spotify_status     -> {"status","song","artist","device",...}
  GET /spotify_toggle     -> queue play/pause
  GET /spotify_next       -> queue skip_next
  GET /spotify_previous   -> queue skip_prev
  GET /spotify_restart    -> kill and relaunch the Spotify desktop app
  GET /lyrics[?pos=ms]    -> current + next synced lyric line
"""

import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "spotify_cookies.json")
LOG_FILE = os.path.join(HERE, "spotify_bridge.log")

_lock = threading.Lock()
_wake = threading.Event()
_commands = []
_track_cache = {}
_last_read = 0.0

_lyrics_lock = threading.Lock()
_lyrics_cache = {}
_lyrics_pending = set()
LYRICS_URL = "https://lrclib.net/api/get"
LYRICS_UA = "AtomS3DeskHub/1.0 (https://github.com/kentot1144)"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) \
    if os.name == "nt" else 0

_restart_at = 0.0

_state = {
    "status": "starting",
    "message": "spotify bridge starting",
    "is_playing": False,
    "song": "-",
    "artist": "-",
    "device": "-",
    "progress_ms": 0,
    "duration_ms": 0,
}

_player = None
_backend = None
_started = False
_spotapi_retry_at = 0.0
_spot_fetch_at = 0.0
_spot_last = None


# ---------------------------------------------------------------- public API
def status():
    global _last_read
    with _lock:
        _last_read = time.time()
        st = dict(_state)
    at = st.pop("_at", None)
    if at and st.get("is_playing"):
        dur = int(st.get("duration_ms") or 0)
        pos = int(st.get("progress_ms") or 0) + int((time.time() - at) * 1000)
        if pos < 0:
            pos = 0
        if dur and pos > dur:
            pos = dur
        st["progress_ms"] = pos
    return st


def command(name):
    """Queue one of: toggle / next / previous / restart."""
    if name not in ("toggle", "next", "previous", "restart"):
        return False
    with _lock:
        if len(_commands) < 8:
            _commands.append(name)
    _wake.set()
    return True


def lyrics(pos=None):
    """Current + next lyric line for whatever the bridge is playing.

    The device sends its own progress (pos, milliseconds) so the line lines
    up with the bar even when the hub state is a few hundred ms old.
    """
    st = status()
    song = st.get("song") or ""
    if not song or song == "-":
        return {"status": "none", "message": "nothing playing"}
    artist = st.get("artist") or ""
    dur = int(st.get("duration_ms") or 0)
    _lyrics_prefetch(song, artist, dur)

    key = _music_key(song, artist, dur)
    with _lyrics_lock:
        entry = _lyrics_cache.get(key)
    if entry is None:
        return {"status": "loading", "message": "fetching lyrics"}
    if entry.get("miss"):
        return {"status": "not_found", "message": "no lyrics"}

    try:
        p = int(pos) if pos is not None else int(st.get("progress_ms") or 0)
    except Exception:
        p = int(st.get("progress_ms") or 0)

    synced = entry.get("synced") or []
    plain = entry.get("plain") or []
    line = ""
    nxt = ""
    line_at = None
    next_at = None
    if synced:
        i = -1
        for j, row in enumerate(synced):
            if row[0] <= p:
                i = j
            else:
                break
        if i >= 0:
            line = synced[i][1]
            line_at = synced[i][0]
        if i + 1 < len(synced):
            nxt = synced[i + 1][1]
            next_at = synced[i + 1][0]
        elif i < 0:
            nxt = synced[0][1]
            next_at = synced[0][0]
    elif plain:
        i = 0
        if dur > 0:
            i = int(p * len(plain) / dur)
        i = max(0, min(len(plain) - 1, i))
        line = plain[i]
        span = (dur / len(plain)) if (dur and plain) else 0
        if span:
            line_at = int(i * span)
            next_at = int((i + 1) * span)
        if i + 1 < len(plain):
            nxt = plain[i + 1]

    return {
        "status": "success",
        "synced": bool(synced),
        "line": line,
        "next": nxt,
        # line timing lets the device scroll a long line just fast enough
        # to finish before the next one starts
        "line_at": line_at,
        "next_at": next_at,
        "track": song,
        "at": p,
    }


def restarting():
    return (time.time() - _restart_at) < 20 if _restart_at else False


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, name="spotify", daemon=True).start()


# ------------------------------------------------------------------ helpers
def _set(**kw):
    kw["_at"] = time.time()
    with _lock:
        _state.update(kw)


def _log(msg):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200000:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write("")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _short(err, n=120):
    s = str(err).replace("\n", " ").strip()
    return s[:n] if s else err.__class__.__name__


def _parse_cookie_string(text):
    out = {}
    for part in text.replace(" ", "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k:
                out[k] = v
    return out


def _load_cookies():
    if not os.path.exists(COOKIE_FILE):
        return None
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError("spotify_cookies.json: %s" % _short(e))
    if isinstance(data, dict) and "cookies" in data:
        data = data["cookies"]
    if isinstance(data, str):
        data = _parse_cookie_string(data)
    if not isinstance(data, dict) or not data:
        raise RuntimeError("spotify_cookies.json has no cookies")
    return data


def _pick_backend():
    if os.path.exists(COOKIE_FILE):
        return "spotapi"
    try:
        import winrt.windows.media.control  # noqa: F401

        return "smtc"
    except Exception:
        return None


# ------------------------------------------------------------- spotapi side
def _patch_spotapi():
    """Work around two spotapi 1.2.8 problems.

    1. MetadataMap needs device_address_mask/debug_level/tier1_port, but
       from_dict() only forwards keys that exist in the payload, so a device
       without them crashes Player() before playback can be read.
    2. WebsocketStreamer.__init__ calls signal.signal(SIGINT, ...), which
       raises "signal only works in main thread" because the bridge runs in
       a worker thread.  Ignore that call outside the main thread.
    """
    try:
        from spotapi.types.data import (
            AudioOutputDeviceInfo,
            Capabilities,
            Device,
            MetadataMap,
        )

        def _from_dict(data):
            d = data or {}
            return MetadataMap(
                device_address_mask=str(d.get("device_address_mask") or ""),
                debug_level=str(d.get("debug_level") or ""),
                tier1_port=str(d.get("tier1_port") or ""),
            )

        _from_dict._desk_patched = True
        MetadataMap.from_dict = staticmethod(_from_dict)

        # Device.__init__ has 12 required fields and from_dict only forwards
        # the ones present in the payload, so an older client (or a device
        # that omits "license") crashes Player() right after a restart.
        def _device_from_dict(data):
            d = data or {}
            caps = d.get("capabilities")
            if isinstance(caps, dict):
                caps = Capabilities.from_dict(caps)
            elif caps is None:
                caps = Capabilities.from_dict({})
            mm = d.get("metadata_map")
            if isinstance(mm, dict):
                mm = MetadataMap.from_dict(mm)
            aod = d.get("audio_output_device_info")
            if isinstance(aod, dict):
                try:
                    aod = AudioOutputDeviceInfo.from_dict(aod)
                except Exception:
                    aod = None
            return Device(
                can_play=bool(d.get("can_play")),
                volume=int(d.get("volume") or 0),
                name=str(d.get("name") or ""),
                capabilities=caps,
                device_software_version=str(
                    d.get("device_software_version") or ""),
                device_type=str(d.get("device_type") or ""),
                device_id=str(d.get("device_id") or ""),
                client_id=str(d.get("client_id") or ""),
                brand=str(d.get("brand") or ""),
                model=str(d.get("model") or ""),
                public_ip=str(d.get("public_ip") or ""),
                license=str(d.get("license") or ""),
                spirc_version=d.get("spirc_version"),
                metadata_map=mm,
                audio_output_device_info=aod,
            )

        _device_from_dict._desk_patched = True
        Device.from_dict = staticmethod(_device_from_dict)
    except Exception:
        pass

    try:
        import signal as _signal

        if not getattr(_signal.signal, "_desk_patched", False):
            _orig_signal = _signal.signal

            def _safe_signal(signum, handler):
                if threading.current_thread() is threading.main_thread():
                    return _orig_signal(signum, handler)
                return None

            _safe_signal._desk_patched = True
            _signal.signal = _safe_signal
    except Exception:
        pass


def _connect():
    global _player, _spot_last, _spot_fetch_at
    import spotapi

    cookies = _load_cookies()
    if not cookies:
        raise RuntimeError("no Spotify cookies")
    if "sp_dc" not in cookies:
        raise RuntimeError("cookie file has no sp_dc value")

    cfg = spotapi.Config(solver=None, logger=spotapi.NoopLogger())
    login = spotapi.Login.from_cookies(
        {"identifier": "edge", "cookies": cookies}, cfg)
    _patch_spotapi()
    _log("spotapi: connecting")
    _player = spotapi.Player(login)
    _spot_last = None
    _spot_fetch_at = 0.0
    _log("spotapi: connected")
    return _player


def _track_info(player, uri):
    """Resolve title/artist from a track URI via Spotify's pathfinder API.

    The websocket connect payload only carries a track URI, so this is the
    only way to get a real title/artist with SpotAPI (the public Web API is
    rate-limited for web tokens).  Results are cached per URI.
    """
    if not uri:
        return {}
    if uri in _track_cache:
        return _track_cache[uri]
    info = {}
    try:
        track_id = uri.rsplit(":", 1)[-1]
        base = player.base
        params = {
            "operationName": "getTrack",
            "variables": json.dumps({"uri": "spotify:track:" + track_id}),
            "extensions": json.dumps({
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": base.part_hash("getTrack"),
                }
            }),
        }
        resp = base.client.post(
            "https://api-partner.spotify.com/pathfinder/v1/query",
            params=params, authenticate=True)
        body = resp.response if isinstance(resp.response, dict) else {}
        t = (body.get("data") or {}).get("trackUnion") or {}
        names = []
        first = ((t.get("firstArtist") or {}).get("items") or [{}])[0]
        name = (first.get("profile") or {}).get("name")
        if name:
            names.append(name)
        for a in ((t.get("otherArtists") or {}).get("items") or []):
            name = (a.get("profile") or {}).get("name")
            if name:
                names.append(name)
        dur = (t.get("duration") or {}).get("totalMilliseconds") or 0
        info = {"title": t.get("name") or "",
                "artist": ", ".join(names),
                "duration_ms": int(dur)}
    except Exception:
        info = {}
    if len(_track_cache) > 200:
        _track_cache.clear()
    _track_cache[uri] = info
    return info


def _device_name(player):
    try:
        devs = player._devices or {}
        active = (player._device_dump or {}).get("active_device_id")
        name = (devs.get(active) or {}).get("name")
        if name:
            return name
    except Exception:
        pass
    try:
        devs = player.device_ids
        for d in devs.devices:
            if d.device_id == devs.active_device_id:
                return d.name or "-"
    except Exception:
        pass
    return "-"


def _refresh_spotapi():
    """Read spotapi state at most twice a second, interpolating in between.

    p.state performs an HTTP request to Spotify's connect-state endpoint, so
    polling it at the device's 5 Hz would be wasteful.  The progress bar is
    kept smooth by status(), which adds the elapsed time to progress_ms.
    """
    global _spot_fetch_at, _spot_last
    p = _player
    now = time.time()

    if _spot_last is not None and (now - _spot_fetch_at) < 0.5:
        # Reuse the cached read: status() keeps the bar smooth by adding the
        # elapsed time to progress_ms, so don't reset its anchor here.
        return

    st = p.state
    _spot_fetch_at = now
    track = st.track
    meta = track.metadata if track else None

    # track.uri is the track actually playing; metadata.context_uri can
    # point at a different item (e.g. the previously requested track).
    uri = (track.uri if track else "") or ""
    if not uri.startswith("spotify:track:") and meta:
        uri = (meta.context_uri or meta.entity_uri
               or getattr(meta, "requested_uri", None) or "")

    song = (meta.title if meta else "") or ""
    artist = ""
    dur = int(st.duration or 0)
    info = _track_info(p, uri) if uri.startswith("spotify:track:") else {}
    # the connect-state duration can describe a different item; the
    # pathfinder duration is canonical and what lyrics matching needs
    song = info.get("title") or song
    artist = info.get("artist") or ""
    if info.get("duration_ms"):
        dur = info["duration_ms"]
    if not artist and meta and meta.album_title:
        artist = meta.album_title

    playing = st.is_playing
    if playing is None:
        playing = not bool(st.is_paused)

    pos = int(st.position_as_of_timestamp or 0)
    try:
        ts = int(st.timestamp or 0)
        if ts:
            if ts < 100000000000:      # seconds, not milliseconds
                ts *= 1000
            age = int(time.time() * 1000) - ts
            if playing and 0 <= age < 30000:
                pos += age
    except Exception:
        pass
    if pos < 0:
        pos = 0
    if dur and pos > dur:
        pos = dur

    _spot_last = {
        "song": song or "-",
        "artist": artist or "-",
        "duration_ms": dur,
        "progress_ms": pos,
        "playing": bool(playing),
    }
    _lyrics_prefetch(song, artist, dur)

    last = _spot_last
    _set(
        status="success",
        message="spotapi",
        is_playing=last["playing"],
        song=last["song"],
        artist=last["artist"],
        device=_device_name(p),
        progress_ms=last["progress_ms"],
        duration_ms=last["duration_ms"],
    )


# ------------------------------------------------------------------- lyrics
def _music_key(song, artist, duration_ms):
    return "%s|%s|%d" % ((song or "").strip().lower(),
                         (artist or "").strip().lower(),
                         int((duration_ms or 0) / 1000))


def _lrc_parse(text):
    """Turn an LRC file into a sorted list of (milliseconds, line)."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        stamps = re.findall(r"\[(\d+):(\d+(?:[.:]\d+)?)\]", line)
        if not stamps:
            continue
        body = re.sub(r"\[[^\]]*\]", "", line).strip()
        if not body:
            continue
        for mm, ss in stamps:
            try:
                ms = int(mm) * 60000 + int(float(ss.replace(":", ".")) * 1000)
            except Exception:
                continue
            rows.append((ms, body))
    rows.sort(key=lambda r: r[0])
    return rows


def _http_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": LYRICS_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _entry_from(data):
    if not isinstance(data, dict):
        return None
    synced = _lrc_parse(data.get("syncedLyrics") or "")
    plain = [ln.strip() for ln in
             (data.get("plainLyrics") or "").splitlines() if ln.strip()]
    if synced or plain:
        return {"synced": synced, "plain": plain, "at": time.time()}
    if data.get("instrumental"):
        return {"synced": [], "plain": ["\u266a instrumental \u266a"],
                "at": time.time()}
    return None


def _lyrics_lookup(song, artist, duration_ms):
    """Find lyrics on LRCLIB.  Returns (entry, network_failed).

    The Windows media session localizes artist names (a Japanese UI turns
    "Alan Walker" into katakana), so an exact lookup misses a lot of Western
    tracks.  When that happens, search by the track title and pick the entry
    whose duration matches Spotify's within a few seconds.
    """
    dur_s = int((duration_ms or 0) / 1000)
    params = urllib.parse.urlencode({
        "track_name": song or "",
        "artist_name": artist or "",
        "duration": dur_s,
    })
    failed = False
    entry = None
    try:
        entry = _entry_from(_http_json(LYRICS_URL + "?" + params))
        if entry:
            return entry, False
    except urllib.error.HTTPError as e:
        if e.code not in (400, 404):
            failed = True
    except Exception:
        failed = True

    if failed:
        return None, True

    # Strip extra suffixes like " (with ...)" or " - Live" for a cleaner query
    clean_song = re.sub(r"\s*[\(\-].*$", "", song or "").strip()
    queries = [song or ""]
    if clean_song and clean_song.lower() != (song or "").strip().lower():
        queries.append(clean_song)

    for q in queries:
        try:
            rows = _http_json("https://lrclib.net/api/search?" +
                              urllib.parse.urlencode({"track_name": q}))
        except Exception:
            return None, True
        if not isinstance(rows, list):
            continue

        best = None
        best_score = None
        target = q.strip().lower()
        for row in rows:
            rn = (row.get("trackName") or "").strip().lower()
            if rn != target and not rn.startswith(target):
                continue
            try:
                gap = abs(int(row.get("duration") or 0) - dur_s)
            except Exception:
                continue
            if gap > 8:
                continue
            score = (gap, 0 if row.get("syncedLyrics") else 1)
            if best_score is None or score < best_score:
                best, best_score = row, score
        if best:
            e = _entry_from(best)
            if e:
                return e, False
    return None, False


def _lyrics_fetch(key, song, artist, duration_ms):
    entry, failed = _lyrics_lookup(song, artist, duration_ms)

    now = time.time()
    with _lyrics_lock:
        _lyrics_pending.discard(key)
        if entry is None:
            entry = {
                "synced": [],
                "plain": [],
                "at": now,
                "miss": True,
                # a network hiccup retries later; a real "no lyrics" sticks
                "retry_at": (now + 120) if failed else None,
            }
        _lyrics_cache[key] = entry
        if len(_lyrics_cache) > 80:
            old = sorted(_lyrics_cache.items(),
                         key=lambda kv: kv[1].get("at", 0))
            for k, _ in old[:20]:
                _lyrics_cache.pop(k, None)


def _lyrics_prefetch(song, artist, duration_ms):
    if not song or song == "-":
        return
    key = _music_key(song, artist, duration_ms)
    now = time.time()
    with _lyrics_lock:
        entry = _lyrics_cache.get(key)
        if entry is not None:
            retry = entry.get("retry_at")
            if retry is None or now < retry:
                return
            _lyrics_cache.pop(key, None)
        if key in _lyrics_pending:
            return
        _lyrics_pending.add(key)
    threading.Thread(target=_lyrics_fetch,
                     args=(key, song, artist, duration_ms),
                     name="lyrics", daemon=True).start()


# ------------------------------------------------------------------ restart
def _restart_spotify():
    """Kill the Spotify desktop app and start it again.

    The app sometimes hangs with a WinError and the only thing that fixes it
    is Alt+F4 + relaunch, so the desk gadget gets a button for that.
    """
    global _restart_at
    _restart_at = time.time()
    _set(status="restarting", message="restarting Spotify")
    _log("restart: killing Spotify")
    for image in ("Spotify.exe", "SpotifyWebHelper.exe"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", image],
                           capture_output=True, timeout=12,
                           creationflags=NO_WINDOW)
        except Exception:
            pass
    time.sleep(1.5)

    exe = os.path.join(os.environ.get("APPDATA") or "",
                       "Spotify", "Spotify.exe")
    launched = False
    if os.path.exists(exe):
        try:
            subprocess.Popen([exe], close_fds=True,
                             creationflags=NO_WINDOW)
            launched = True
        except Exception:
            launched = False
    if not launched and hasattr(os, "startfile"):
        try:
            os.startfile("spotify:")
            launched = True
        except Exception:
            pass

    # Give the app time to sign in and publish a media session again.
    time.sleep(10.0)
    _log("restart: launched=%s" % launched)
    if not launched:
        _set(status="error", message="Spotify.exe not found")


def _run_spotapi(cmd):
    p = _player
    if cmd == "next":
        p.skip_next()
    elif cmd == "previous":
        p.skip_prev()
    else:
        st = p.state
        playing = st.is_playing if st.is_playing is not None else not st.is_paused
        if playing:
            p.pause()
        else:
            p.resume()


# ----------------------------------------------------------------- smtc side
def _app_label(aumid):
    a = (aumid or "").lower()
    if "spotify" in a:
        return "Spotify"
    if "edge" in a:
        return "Edge"
    if "chrome" in a:
        return "Chrome"
    if "zunemusic" in a or "mediaplayer" in a:
        return "Media Player"
    base = a.replace(".exe", "").split("!")[-1].split(".")[-1]
    return base[:16] or "-"


def _td_ms(td):
    try:
        return int(td.total_seconds() * 1000)
    except Exception:
        return 0


async def _smtc_pick():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    mgr = await Manager.request_async()
    sessions = list(mgr.get_sessions())
    if not sessions:
        return None
    for s in sessions:
        if "spotify" in (s.source_app_user_model_id or "").lower():
            return s
    return sessions[0]


async def _smtc_read():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )

    s = await _smtc_pick()
    if s is None:
        return {
            "status": "not_playing", "message": "smtc",
            "is_playing": False, "song": "-", "artist": "-",
            "device": "-", "progress_ms": 0, "duration_ms": 0,
        }
    props = await s.try_get_media_properties_async()
    info = s.get_playback_info()
    tl = s.get_timeline_properties()
    playing = info.playback_status == Status.PLAYING
    pos = _td_ms(tl.position)
    dur = _td_ms(tl.end_time)
    if playing:
        # Position is reported as of last_updated_time; add the elapsed
        # time so the device gets a smooth, current progress value.
        try:
            age = time.time() - tl.last_updated_time.timestamp()
            if 0 <= age < 30:
                pos += int(age * 1000)
        except Exception:
            pass
        if dur and pos > dur:
            pos = dur
    return {
        "status": "success",
        "message": "smtc",
        "is_playing": playing,
        "song": (props.title or "-"),
        "artist": (props.artist or props.album_title or "-"),
        "device": _app_label(s.source_app_user_model_id),
        "progress_ms": pos,
        "duration_ms": dur,
    }


async def _smtc_send(cmd):
    s = await _smtc_pick()
    if s is None:
        return False
    if cmd == "next":
        return bool(await s.try_skip_next_async())
    if cmd == "previous":
        return bool(await s.try_skip_previous_async())
    return bool(await s.try_toggle_play_pause_async())


def _smtc_refresh():
    import asyncio

    data = asyncio.run(_smtc_read())
    _set(**data)
    if data.get("status") == "success":
        _lyrics_prefetch(data.get("song"), data.get("artist"),
                         data.get("duration_ms"))


def _smtc_command(cmds):
    import asyncio

    for cmd in cmds:
        asyncio.run(_smtc_send(cmd))


# -------------------------------------------------------------------- worker
def _worker():
    global _player, _backend, _spotapi_retry_at
    while True:
        with _lock:
            todo = _commands[:]
            del _commands[:]

        restart = "restart" in todo
        if restart:
            todo = [c for c in todo if c != "restart"]

        # After a SpotAPI hiccup we fall back to the media session, but keep
        # trying the real backend: it has canonical titles/artists (the media
        # session localizes them) which the lyrics lookup needs.
        if (_backend == "smtc" and os.path.exists(COOKIE_FILE)
                and time.time() >= _spotapi_retry_at):
            _backend = None

        if _backend is None:
            _backend = _pick_backend()
            _log("backend: %s" % _backend)
            if _backend is None and not restart:
                _set(status="error",
                     message="need spotapi cookies or a media session")
                time.sleep(10)
                continue

        if restart:
            _restart_spotify()
            if _backend == "spotapi":
                _player = None

        try:
            if _backend == "smtc":
                if todo:
                    _smtc_command(todo)
                    time.sleep(0.3)
                _smtc_refresh()
            else:
                if _player is None:
                    _set(status="connecting", message="logging in to Spotify")
                    _connect()
                for cmd in todo:
                    _run_spotapi(cmd)
                if todo:
                    time.sleep(0.6)
                _refresh_spotapi()
        except Exception as e:
            if _backend == "spotapi":
                _log("spotapi failed: %r" % e)
                _set(status="error",
                     message="SpotAPI failed (%s); using media session"
                             % _short(e, 60))
                _backend = "smtc"
                _player = None
                _spotapi_retry_at = time.time() + 300
                continue
            _set(status="error", message="media session: " + _short(e))
            time.sleep(5)

        # Refresh at 5 Hz while a device is polling the status endpoint,
        # otherwise idle at 1 Hz to keep the PC quiet.
        with _lock:
            active = (time.time() - _last_read) < 3.0
        _wake.wait(0.2 if active else 1.0)
        _wake.clear()
