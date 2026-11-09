# AtomS3 Desk Gadget

A multi-functional desk gadget built with an M5Stack AtomS3 (MicroPython) and a
small Python hub that runs on a PC in the same LAN.

## Features

- **Clock & weather** on the 128x128 screen, plus a cute idle mascot.
- **Next calendar event**: the clock page shows a countdown to the next event
  from any iCalendar (ICS) feed. No Google/Microsoft OAuth is needed; the hub
  parses the feed including the common `RRULE` cases.
- **PC stats** (CPU, RAM, GPU, uptime) served by the local hub.
- **Service monitoring**: HTTP / TCP / Minecraft checks with alert rules.
  Minecraft player counts come from the Crafty API, and join/leave/move events
  are pushed into the notification center.
- **Notification center**: `POST /notify` on the hub, and the device shows a
  dismissable full-screen banner, flashes the LED and keeps the last 20 events
  on a NOTICES page. Minecraft joins and leaves are generated automatically.
- **Service uptime sparkline** on the STATUS page, plus the last time a service
  went down.
- **Alert LED** on GPIO5 flashes when a critical service is down.
- **Auto-brightness** from the on-board light sensor (GPIO6).
- **Spotify screen**: shows track, artist and the playing device, with
  play/pause, next and previous controls on the device button. Track and artist
  use a CJK font (Latin, kana and kanji) on a single clipped line; long text
  slides sideways and bounces back like a radio VFD (30 fps animation), while
  track data refreshes at 5 fps. Only the text band and the progress area are
  repainted, so the screen does not flash.
- **Synced lyrics**: hold the button for ~1 s on the Spotify screen to overlay
  the current and next lyric line, fetched from [LRCLIB](https://lrclib.net/)
  through the hub. English lines word-wrap into up to four small static rows so
  the whole line is visible without scrolling. CJK or very long lines split in
  half and scroll together, and the speed adapts to the time left before the
  next line so the line finishes in its slot.
- **Remote Spotify restart**: a Normal-mode page that kills and relaunches the
  Spotify desktop app on the hub PC, for when it hangs and needs an Alt+F4.
- **Several remembered Wi-Fi networks**: list every SSID/password pair you use
  and the device tries them in turn until one connects.

## Two UI modes

| Mode | Screens |
| --- | --- |
| Normal | clock, weather, pomodoro, PC stats, services (4 per page), notices, restart Spotify, mascot, status |
| Spotify | track, artist, device, progress bar |

Switch between them with a **3 second button hold**. In Normal mode:

| Gesture | Action |
| --- | --- |
| single click | next page |
| double click | page action (refresh, clear notices, restart Spotify, ...) |

While a notification banner is visible, any click dismisses it. In Spotify mode:

| Gesture | Action |
| --- | --- |
| single click | play / pause |
| double click | next track |
| triple click | previous track |
| hold ~1 s | show / hide lyrics |

## Project structure

```text
firmware/            MicroPython code for the AtomS3
  boot.py main.py    entry points
  config.json        Wi-Fi, location, hub URLs, pins (copy from .example)
  libs/desk/         app, pages, ui, net, led, light
hub/                 PC-side hub
  hub.py             HTTP server: /stats, /events, /notify, /calendar, /spotify_*, /lyrics
  spotify_bridge.py  Spotify backend (SMTC by default)
  hub_config.json    services + keys (copy from .example)
  start_hub.bat      silent launcher (hands off to the VBS)
  start_hub_silent.vbs  fully window-less launcher
  start_hub_debug.bat   visible console with logs (for debugging)
  check_hub.vbs      silent watchdog: restarts the hub if it stops answering
  install_hub.bat/.ps1  firewall rule + silent autostart + watchdog task
tools/               flashing and network diagnostics helpers
```

## Setup

### 1. Device

1. Install the UIFlow2 firmware on the AtomS3 (the stock environment can be
   restored at any time from UIFlow2).
2. Copy `firmware/config.json.example` to `firmware/config.json` and fill in
   your location, the hub URL(s) and one or more Wi-Fi networks:

   ```json
   "wifi": [
     { "ssid": "home", "password": "..." },
     { "ssid": "phone hotspot", "password": "..." }
   ]
   ```

   A single object (`{"ssid": ..., "password": ...}`) still works. When the
   device is offline it cycles through the list every few seconds and
   remembers the network that worked.

   The hub can also be found automatically. Add its MAC address to pin the
   right PC (optional but recommended):

   ```json
   "hub": {
     "urls": ["http://192.168.1.10:8765"],
     "mac": "00:11:22:33:44:55",
     "discovery_port": 8766
   }
   ```

   When none of the `urls` answers, the device broadcasts a UDP packet on
   port 8766. The hub replies with its port and MAC addresses, so a DHCP
   address change no longer breaks PC stats or Spotify. With `mac` set, only
   that machine is accepted; leave it empty to accept any hub. `host_mac` in
   `/stats` shows the PC's MAC. Discovery needs direct device-to-PC traffic:
   guest Wi-Fi with AP isolation blocks both the hub and discovery.
3. Flash with:

   ```bat
   python tools\push.py
   ```

   The script writes every file under `firmware/` to `/flash` over the serial
   port and reboots the device. Set the `PORT` environment variable if your
   board is not on `COM3`.

### 2. Hub (PC)

1. Install Python 3.10+ and `pip install psutil`.
2. Copy `hub/hub_config.json.example` to `hub/hub_config.json` and replace every
   placeholder (`YOUR_...`) with your own URLs, IPs and API key.
3. Double-click `hub/start_hub_silent.vbs` (or run `hub/install_hub.bat` once to
   add the firewall rule, a silent autostart shortcut and a watchdog task).
4. Open <http://localhost:8765/stats> to check it works.

The hub is single-instance: if another copy is already listening on port 8765,
a new copy prints a message and exits instead of competing for requests.
`check_hub.vbs` runs every 2 minutes from Task Scheduler; if `/ping` does not
answer, it starts the hub again. It is windowless, like the launcher.

#### Reaching the hub over the internet

Run the hub on the machine that stays on (for example a home server) and add
its public address as a second URL. Order matters: LAN first, public second,
so at home the device stays local.

```json
"hub": {
  "urls": ["http://192.168.1.10:8765", "http://YOUR_DDNS_HOST:8765"],
  "token": "CHANGE_ME_LONG_RANDOM_TOKEN",
  "discovery_port": 8766
}
```

Put the same string in `hub_config.json` as `"token"`. When a token is set,
every request except `/ping` needs `?k=TOKEN`; the firmware appends it
automatically. **Never expose the hub without a token** — port 8765 has no
TLS, so the token is the only protection. Open the ports on the hub PC:

```bat
netsh advfirewall firewall add rule name="AtomS3 Hub 8765" dir=in action=allow protocol=TCP localport=8765
netsh advfirewall firewall add rule name="AtomS3 Hub discovery 8766" dir=in action=allow protocol=UDP localport=8766
```

If the PC is behind a router, forward TCP 8765 to it (and UDP 8766 for LAN
discovery). The Spotify page polls faster on a LAN and backs off to 2 Hz when
the round trip is slow, so the marquee keeps its frame rate over the internet.

### 3. Service checks

`hub/hub_config.json` describes what to monitor. Supported types:

- `http` – up when the status code is below 500 (redirects are not followed)
- `tcp` – up when the TCP port accepts a connection
- `mc` – Minecraft server list ping

Each service may list several `hosts`; the first one that answers wins. Mark a
service `"critical": true` to make the device LED flash when it is down.

For live Minecraft numbers, add `"crafty": "<server name or id>"` to an `mc`
service. The hub reads `/api/v2/servers/<id>/stats` from Crafty, which returns
the running state, player count and player names. The legacy server-list ping
(`type: "mc"` without `crafty`) still works and only reports counts.

### 4. Notifications and calendar

The hub keeps a small in-memory event log:

- `POST /notify` with a JSON body `{"title": "...", "body": "...", "kind": "manual"}`.
  `GET /notify?title=...&body=...&kind=...` also works.
- `GET /events?since=<last id>` returns `{"now", "last", "events": [...]}`.
  Event ids restart at 1 when the hub restarts; the device notices a smaller
  `last` and replays from zero.
- `GET /calendar` returns the next event parsed from the ICS feed, or
  `{"status": "disabled"}` until `calendar.ics_url` is set.

```bash
curl -X POST http://192.168.1.10:8765/notify?k=TOKEN \
     -H "Content-Type: application/json" \
     -d '{"title":"Build done","body":"CI finished in 2m","kind":"manual"}'
```

The device polls `/events` about every 15 seconds, shows an LED-flashing banner
for ~9 seconds (a click dismisses it), and keeps the latest 20 events on the
NOTICES page. Double click on that page clears the local list. `events.keep`
in `hub_config.json` controls how many events the hub retains.

Minecraft joins, leaves and server moves become `mc_join`, `mc_leave` and
`mc_move` events automatically. If a Crafty request times out, the hub keeps the
last known player list for that server, so a slow API never looks like a mass
disconnect.

For the calendar countdown, set the feed and the fixed UTC offset:

```json
"calendar": {
  "ics_url": "https://calendar.example.com/private/feed.ics",
  "tz_offset": 9,
  "days": 14
}
```

Most calendar services offer a secret/public ICS address (Google Calendar:
"Secret address in iCal format"; Outlook: publish calendar). The feed is fetched
at most once a minute, and the device asks for the next event every 5 minutes.
The common `RRULE` cases (daily, weekly with `BYDAY`, monthly, yearly,
`INTERVAL`, `COUNT`, `UNTIL`) are expanded locally. No OAuth token is stored.

### 5. Spotify

Two backends are supported, chosen automatically:

1. **SpotAPI** – preferred when `hub/spotify_cookies.json` exists (see
   [Aran404/SpotAPI](https://github.com/Aran404/SpotAPI)). It talks to Spotify's
   own connect-state endpoint, so titles and artists are canonical and the
   lyrics lookup works in every language.
2. **Windows media sessions (SMTC)** – the fallback. Works with the Spotify
   desktop app and browser tabs, no login required, but Windows localizes
   artist names (which can defeat lyrics matching).

If the SpotAPI call fails, the hub falls back to SMTC and retries SpotAPI a few
minutes later. Lyrics come from the free [LRCLIB](https://lrclib.net/) API,
cached per track, with a duration-matched search fallback for localized names.
`/lyrics?pos=<ms>` returns the current and next line for the device's own
playback position.

## Security

**Never commit real configuration files.** `hub_config.json`,
`firmware/config.json`, `spotify_cookies.json` and the cookie export tool can
contain API keys, internal IPs and account cookies. They are listed in
`.gitignore`; use the provided `*.example` templates instead.

If a key was ever committed, revoke/regenerate it — deleting the file later
does not remove it from Git history.

## License

MIT
