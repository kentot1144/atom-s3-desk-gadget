# Sharing on UIFlow2 (M5Stack)

UIFlow2's **Project Zone** is the M5Stack community project library. Publishing
to it is done from a **logged-in UIFlow2 Web IDE** project, and M5Stack admins
approve each submission before it appears. I can prepare the project files and
the text, but the final **Publish** click has to come from your M5Stack account.

This repository is a **plain MicroPython project**, not a native block-based
UIFlow project. UIFlow2 can still run it: select AtomS3, switch to **Python**
mode, and upload the `.py` files as project files.

## Fields to fill in

Copy these straight into the create-project / project-details form:

**Name**

```
AtomS3 Desk Gadget - Clock, Spotify Lyrics & PC Stats
```

**Description**

```
Hub-driven AtomS3 desk display with clock, weather, pomodoro, PC stats, service
monitoring, notices, and live Spotify playback with synced lyrics. Python-mode
project for AtomS3; requires a PC running the included hub. Source and setup:
https://github.com/kentot1144/atom-s3-desk-gadget
```

**Cover image**

Upload [`docs/images/cover.png`](images/cover.png) from this repository
(1200x675 PNG). It shows the real clock, lyric and status pages rendered from
the firmware. If the form asks for a URL instead of a file, use:

```
https://raw.githubusercontent.com/kentot1144/atom-s3-desk-gadget/main/docs/images/cover.png
```

## 1. Prepare the UIFlow project

1. Download [`uiflow/atoms3-desk-uiflow.zip`](../uiflow/atoms3-desk-uiflow.zip)
   from this repository and unzip it. It contains **only** device-side code —
   no cookies, tokens, Wi‑Fi passwords or personal configuration.
2. Go to <https://uiflow2.m5stack.com/> and **log in**.
3. Select controller **AtomS3** and create a new project.
4. Use **Code Preview / Python** to switch from blocks to the Python editor.
5. Open **Project Files** and upload:
   - `app.py`
   - the whole `desk/` folder (keep that exact folder name)
   - a `config.json` you create from `config.json.example`
6. Replace the editor's generated `main.py` with the `main.py` from the bundle,
   then **Save**.
7. Connect the AtomS3 by USB. Use **Run Once** to test, then **Run Always** to
   keep it on the device. In the flashing options choose **Run main.py
   directly** if you want it to boot straight into the gadget.

> **Never publish your real `config.json`.** It holds Wi‑Fi passwords and hub
> addresses. For the public Project Zone project, ship only
> `config.json.example` with placeholder values, and tell people to create
> their own `config.json` on their own device.

## 2. Fill in `config.json` for your own device

```json
{
  "wifi": {
    "networks": [
      { "ssid": "YOUR_HOME_SSID", "password": "YOUR_HOME_PASSWORD" },
      { "ssid": "YOUR_HOTSPOT_SSID", "password": "YOUR_HOTSPOT_PASSWORD" }
    ]
  },
  "location": { "lat": 0.0, "lon": 0.0, "tz": 0, "name": "Your City" },
  "hub": { "urls": ["http://YOUR_HUB_HOST:8765"] }
}
```

The first network is treated as "home" and is preferred when visible. The PC
hub is a **separate program** from this UIFlow project — see the main
[README](../README.md) for `hub/install_hub.ps1`. Without the hub the clock and
pomodoro still work, but Spotify lyrics, weather, PC stats and notices stay
empty.

## 3. Publish to Project Zone

In the UIFlow2 Web IDE: **Project Management → My Project** → set the project
to **Private** and fill in the details → submit. M5Stack reviews it; once
approved it appears in **Project Zone** and others can import it.

Paste these fields:

**Name**

```
AtomS3 Desk Gadget - Clock, Spotify Lyrics & PC Stats
```

**Short description**

```
Hub-driven AtomS3 desk display: clock, weather, pomodoro, PC stats, service
monitoring, notices, and live Spotify playback with synced lyrics.
```

**Description / details**

```
A multi-page desk gadget for the M5Stack AtomS3 (128x128). The PC runs a small
Python hub that gathers Spotify state, weather, calendar, PC stats and service
checks, then pushes one revisioned state object to the device over a streaming
HTTP connection. The device only renders what the hub sends, plus its own clock
and pomodoro timer.

Highlights
- Normal mode: clock + weather + next calendar event, pomodoro, CPU/RAM/GPU,
  service status (4 per page), notification centre, idle mascot, restart
  Spotify, and a STATUS sub-page that alternates hub health / Spotify status.
- Spotify mode: track, artist, device and progress bar; play/pause, next,
  previous, and a lyric overlay that follows the real playback position.
- Lyrics use one fixed mid-size font, up to four evenly-spaced rows, with long
  lines continuing in reading order and extra-long lines paged over time.
- Wi-Fi roaming prefers your home network when visible.
- Dark, cool-themed English UI.

Notes
- This is a Python-mode project, not a block project: the .py files are
  included as project files. Replace main.py with the one in the bundle.
- Create your own config.json from config.json.example before running. Do not
  share filled-in configs (they contain Wi-Fi credentials).
- The PC hub is required for the data pages; install instructions and the full
  source are on GitHub.

Full source, setup guide and rendered previews:
https://github.com/kentot1144/atom-s3-desk-gadget
```

**Preview image URLs** (use these in the description if the form allows
Markdown or image links):

```
https://raw.githubusercontent.com/kentot1144/atom-s3-desk-gadget/main/docs/images/normal-pages.png
https://raw.githubusercontent.com/kentot1144/atom-s3-desk-gadget/main/docs/images/spotify-pages.png
https://raw.githubusercontent.com/kentot1144/atom-s3-desk-gadget/main/docs/images/status-pages.png
```

## 4. After publishing

- M5Stack approval can take a while; the project stays private until approved.
- If you change the firmware or README, push to GitHub and re-upload the new
  `.py` files to the same UIFlow project, then save again.
- If you want a plain shareable link instead, GitHub Pages can serve the
  README and gallery — ask me and I'll enable it.
