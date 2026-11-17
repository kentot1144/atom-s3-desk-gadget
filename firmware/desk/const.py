"""Shared constants for the thin display client."""

HUB_TIMEOUT_S = 1.5
HUB_BAD_MS = 15000
HUB_POLL_MS = 3000
HUB_POLL_LIVE_MS = 1000
ROAM_AFTER_MS = 180000
HOME_AFTER_MS = 45000
NTP_FALLBACK_MS = 15000

# Local page the hub cannot override, so Wi-Fi / hub health is always visible.
SYS_PAGE = {"k": "sys", "t": "DEVICE"}
FALLBACK = [{"k": "clock"}, {"k": "focus", "t": "FOCUS"}, SYS_PAGE]

# ---------------------------------------------------------------------- app
HOLD_MS = 3000
SOFT_HOLD_MS = 1000
CLICK_GAP_MS = 400
NOTIF_SHOW_MS = 9000
NOTIF_MAX_AGE_S = 300

# Auto-advancing rows page (servers): one sub-page at a time, then wrap.
ROWS_PAGE_MS = 3600

# Status alternates between system health and the bare Spotify state.
STATUS_PAGE_MS = 5000
