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
"""

import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "spotify_cookies.json")

_lock = threading.Lock()
_wake = threading.Event()
_commands = []
_artist_cache = {}

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


# ---------------------------------------------------------------- public API
def status():
    with _lock:
        return dict(_state)


def command(name):
    """Queue one of: toggle / next / previous."""
    if name not in ("toggle", "next", "previous"):
        return False
    with _lock:
        if len(_commands) < 8:
            _commands.append(name)
    _wake.set()
    return True


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, name="spotify", daemon=True).start()


# ------------------------------------------------------------------ helpers
def _set(**kw):
    with _lock:
        _state.update(kw)


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
def _connect():
    global _player
    import spotapi

    cookies = _load_cookies()
    if not cookies:
        raise RuntimeError("no Spotify cookies")
    if "sp_dc" not in cookies:
        raise RuntimeError("cookie file has no sp_dc value")

    cfg = spotapi.Config(solver=None, logger=spotapi.NoopLogger())
    login = spotapi.Login.from_cookies(
        {"identifier": "edge", "cookies": cookies}, cfg)
    _player = spotapi.Player(login)
    return _player


def _artist(uri):
    if not uri:
        return ""
    if uri in _artist_cache:
        return _artist_cache[uri]
    name = ""
    try:
        import urllib.request

        track_id = uri.split(":")[-1]
        url = ("https://open.spotify.com/oembed?url="
               "https://open.spotify.com/track/%s" % track_id)
        with urllib.request.urlopen(url, timeout=6) as r:
            name = json.loads(r.read().decode("utf-8")).get("author_name") or ""
    except Exception:
        name = ""
    _artist_cache[uri] = name
    return name


def _device_name(player):
    try:
        devs = player.device_ids
        for d in devs.devices:
            if d.device_id == devs.active_device_id:
                return d.name or "-"
    except Exception:
        pass
    return "-"


def _refresh_spotapi():
    p = _player
    st = p.state
    track = st.track
    meta = track.metadata if track else None
    song = (meta.title if meta else "") or "-"
    artist = _artist(meta.artist_uri if meta else "")
    if not artist and meta and meta.album_title:
        artist = meta.album_title

    playing = st.is_playing
    if playing is None:
        playing = not bool(st.is_paused)

    _set(
        status="success",
        message="spotapi",
        is_playing=bool(playing),
        song=song,
        artist=artist or "-",
        device=_device_name(p),
        progress_ms=int(st.position_as_of_timestamp or 0),
        duration_ms=int(st.duration or 0),
    )


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
    return {
        "status": "success",
        "message": "smtc",
        "is_playing": info.playback_status == Status.PLAYING,
        "song": (props.title or "-"),
        "artist": (props.artist or props.album_title or "-"),
        "device": _app_label(s.source_app_user_model_id),
        "progress_ms": _td_ms(tl.position),
        "duration_ms": _td_ms(tl.end_time),
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

    _set(**asyncio.run(_smtc_read()))


def _smtc_command(cmds):
    import asyncio

    for cmd in cmds:
        asyncio.run(_smtc_send(cmd))


# -------------------------------------------------------------------- worker
def _worker():
    global _player, _backend
    while True:
        with _lock:
            todo = _commands[:]
            del _commands[:]

        if _backend is None:
            _backend = _pick_backend()
            if _backend is None:
                _set(status="error",
                     message="need spotapi cookies or a media session")
                time.sleep(10)
                continue

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
                _set(status="error",
                     message="SpotAPI failed (%s); using media session"
                             % _short(e, 60))
                _backend = "smtc"
                _player = None
                continue
            _set(status="error", message="media session: " + _short(e))
            time.sleep(5)

        _wake.wait(3.0)
        _wake.clear()
