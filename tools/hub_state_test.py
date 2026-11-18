"""Deterministic checks for the hub-side Spotify pause detection.

The AtomS3 shows PLAYING from sp.is_playing, so the hub must never keep that
true for a paused track.  These tests drive the bridge's stall watchdog with
synthetic positions; no network or Spotify login is needed.

    python tools/hub_state_test.py
"""
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.join(os.path.dirname(HERE), "hub")

spec = importlib.util.spec_from_file_location(
    "spotify_bridge_under_test", os.path.join(HUB, "spotify_bridge.py"))
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

FAILS = []


def check(name, ok, detail=""):
    print("%s %-40s %s" % ("ok  " if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def reset():
    sb._watch = {"key": None, "samples": [], "stalled": False}
    sb._music = {"key": None, "pos": 0, "at": 0.0, "playing": False,
                 "lag": 0, "stale": False}


def frozen(jitter=700):
    reset()
    out = []
    for i in range(16):
        out.append(sb._advance_ok(
            "k", 209000 + (jitter if i % 2 else -jitter), True,
            now=i * 0.5))
    return out


def playing():
    reset()
    return [sb._advance_ok("k", 100000 + i * 500, True, now=i * 0.5)
            for i in range(16)]


def resume():
    reset()
    t = 0.0
    out = []
    for i in range(16):
        out.append(sb._advance_ok(
            "k", 209000 + (700 if i % 2 else -700), True, now=t))
        t += 0.5
    stall_end = len(out)
    for i in range(20):
        out.append(sb._advance_ok("k", 209000 + i * 500, True, now=t))
        t += 0.5
    return out, stall_end


def test_watchdog():
    out = frozen()
    check("frozen jitter stalls", out[-1] is False)
    check("stall takes about 4 s", out[7] is True and out[8] is False)

    out = playing()
    check("advancing position stays playing", out[-1] is True)

    out, stall_end = resume()
    check("paused line is marked paused", out[stall_end - 1] is False)
    first = next((i for i in range(stall_end, len(out)) if out[i]), None)
    delay = (first - stall_end) * 0.5 if first is not None else -1
    check("resume clears the stall", 0 <= delay <= 4.0,
          "(%.1fs after resume)" % delay)

    reset()
    for i in range(12):
        sb._advance_ok("k", 100000 + i * 500, True, now=i * 0.5)
    check("backward seek starts fresh",
          sb._advance_ok("k", 70000, True, now=6.0) is True)

    reset()
    check("track change starts fresh",
          sb._advance_ok(("new", 1), 5000, True, now=0) is True)

    reset()
    check("paused input is not playing",
          sb._advance_ok("k", 1000, False, now=0) is False)


def test_music_clock():
    """A stale connect-state frame must not rewind the bar or the lyrics."""
    reset()
    old_log = sb._log
    sb._log = lambda msg: None
    try:
        key = ("song", "artist", 200000)
        out = [sb._music_pos(key, 100000, True, now=0.0),
               sb._music_pos(key, 100500, True, now=0.5)]
        check("clock advances at 1x", out == [100000, 100500],
              str(out))

        # Spotapi hands back a frame ~30 s behind while the music keeps going.
        out.append(sb._music_pos(key, 71000, True, now=1.0))
        check("stale frame does not rewind", out[-1] >= 101000,
              "(got %d)" % out[-1])
        check("stale frame is recorded", sb._music["stale"] is True)
        out.append(sb._music_pos(key, 71500, True, now=1.5))
        check("clock runs while frame is stale", out[-1] >= 101500,
              "(got %d)" % out[-1])
        check("stale flag persists", sb._music["stale"] is True)

        # The backend catches up: trust it again.
        out.append(sb._music_pos(key, 105000, True, now=4.0))
        check("caught-up frame re-anchors", out[-1] == 105000,
              "(got %d)" % out[-1])
        check("stale flag clears", sb._music["stale"] is False)

        # A different track starts wherever the backend says it does.
        out.append(sb._music_pos(("other", "artist", 190000), 1200, True,
                                 now=4.5))
        check("new track re-anchors", out[-1] == 1200)
        sb._music_pos(("other", "artist", 190000), 1200, False, now=5.0)
        check("pause clears the clock", sb._music["playing"] is False)
    finally:
        sb._log = old_log
        reset()


def test_is_paused_wins():
    class State(object):
        is_playing = True
        is_paused = True
        position_as_of_timestamp = 209000
        timestamp = int(time.time() * 1000)
        duration = 227000
        track = None

    class Player(object):
        state = State()

    old = (sb._player, sb._track_info, sb._lyrics_prefetch,
           sb._device_name, sb._spot_last, sb._spot_fetch_at)
    try:
        sb._player = Player()
        sb._spot_last = None
        sb._spot_fetch_at = 0.0
        sb._track_info = lambda p, uri: {}
        sb._lyrics_prefetch = lambda *a: None
        sb._device_name = lambda p: "-"
        reset()
        sb._refresh_spotapi()
        check("is_paused overrides is_playing",
              sb._state["is_playing"] is False)
    finally:
        (sb._player, sb._track_info, sb._lyrics_prefetch,
         sb._device_name, sb._spot_last, sb._spot_fetch_at) = old


def test_stale_state():
    old = dict(sb._state)
    try:
        sb._set(status="success", message="spotapi", is_playing=True,
                song="x", artist="y", duration_ms=200000, progress_ms=50000,
                device="-")
        fresh = sb.status()
        check("fresh playing stays playing", fresh["is_playing"] is True)
        sb._state["_at"] = time.time() - 12
        stale = sb.status()
        check("dead bridge is not playing", stale["is_playing"] is False)
        check("dead bridge is marked paused", stale.get("stalled") is True)
    finally:
        sb._state.clear()
        sb._state.update(old)


def main():
    test_watchdog()
    test_music_clock()
    test_is_paused_wins()
    test_stale_state()
    print("FAILURES:", len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
