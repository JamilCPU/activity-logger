"""Sync phone activity from ActivityWatch Android via Tailscale."""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from activity_logger.config import Config
from activity_logger.storage.db import Database

logger = logging.getLogger(__name__)

STATE_FILE = Path.home() / ".activity-logger" / "phone_sync_state.json"

# Android package name → friendly display name
_PACKAGE_NAMES: dict[str, str] = {
    "com.android.chrome": "Chrome",
    "com.google.android.youtube": "YouTube",
    "com.google.android.gm": "Gmail",
    "com.google.android.maps": "Google Maps",
    "com.google.android.apps.maps": "Google Maps",
    "com.google.android.apps.messaging": "Messages",
    "com.google.android.dialer": "Phone",
    "com.google.android.apps.photos": "Photos",
    "com.google.android.googlequicksearchbox": "Google",
    "com.google.android.apps.youtube.music": "YouTube Music",
    "com.google.android.apps.meetings": "Google Meet",
    "com.google.android.keep": "Google Keep",
    "com.instagram.android": "Instagram",
    "com.twitter.android": "Twitter/X",
    "com.facebook.katana": "Facebook",
    "com.snapchat.android": "Snapchat",
    "com.whatsapp": "WhatsApp",
    "com.discord": "Discord",
    "com.spotify.music": "Spotify",
    "com.netflix.mediaclient": "Netflix",
    "com.amazon.mShop.android.shopping": "Amazon",
    "com.reddit.frontpage": "Reddit",
    "com.tiktok.musical.ly": "TikTok",
    "com.ss.android.ugc.trill": "TikTok",
    "com.zhiliaoapp.musically": "TikTok",
    "org.telegram.messenger": "Telegram",
    "tv.twitch.android.app": "Twitch",
    "com.microsoft.teams": "Microsoft Teams",
    "com.microsoft.outlook": "Outlook",
    "com.slack": "Slack",
    "com.zoom.videomeetings": "Zoom",
    "com.notion.id": "Notion",
    "com.samsung.android.messaging": "Samsung Messages",
    "com.samsung.android.dialer": "Samsung Phone",
    "com.android.settings": "Settings",
    "com.android.launcher3": "Home Screen",
    "com.sec.android.app.launcher": "Home Screen",
    "com.miui.home": "Home Screen",
    "com.oneplus.launcher": "Home Screen",
    "com.google.android.apps.nexuslauncher": "Home Screen",
}

# Package → category
_PACKAGE_CATEGORIES: dict[str, str] = {
    "com.android.chrome": "Browsing",
    "com.google.android.youtube": "Media",
    "com.google.android.apps.youtube.music": "Media",
    "com.spotify.music": "Media",
    "com.netflix.mediaclient": "Media",
    "tv.twitch.android.app": "Media",
    "com.tiktok.musical.ly": "Media",
    "com.ss.android.ugc.trill": "Media",
    "com.zhiliaoapp.musically": "Media",
    "com.instagram.android": "Social",
    "com.twitter.android": "Social",
    "com.facebook.katana": "Social",
    "com.snapchat.android": "Social",
    "com.reddit.frontpage": "Social",
    "com.whatsapp": "Communication",
    "com.discord": "Communication",
    "org.telegram.messenger": "Communication",
    "com.google.android.apps.messaging": "Communication",
    "com.google.android.dialer": "Communication",
    "com.samsung.android.messaging": "Communication",
    "com.samsung.android.dialer": "Communication",
    "com.microsoft.teams": "Communication",
    "com.slack": "Communication",
    "com.zoom.videomeetings": "Communication",
    "com.google.android.apps.meetings": "Communication",
    "com.microsoft.outlook": "Communication",
    "com.google.android.gm": "Communication",
    "com.notion.id": "Productivity",
    "com.google.android.keep": "Productivity",
}


def _resolve_display_name(package: str, config: Config) -> str:
    for key, friendly in config.display_names.items():
        if key.lower() in package.lower():
            return friendly
    if package in _PACKAGE_NAMES:
        return _PACKAGE_NAMES[package]
    parts = package.split(".")
    name = parts[-1] if parts else package
    return name.replace("_", " ").replace("-", " ").title()


def _resolve_category(package: str, config: Config) -> str:
    if package in _PACKAGE_CATEGORIES:
        return _PACKAGE_CATEGORIES[package]
    display = _resolve_display_name(package, config)
    return config.resolve_category(package, display)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _aw_get(ip: str, port: int, path: str, timeout: int = 10) -> Any:
    url = f"http://{ip}:{port}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _find_android_bucket(ip: str, port: int) -> str | None:
    buckets = _aw_get(ip, port, "/api/0/buckets/")
    for bucket_id in buckets:
        if "android" in bucket_id.lower():
            return bucket_id
    return None


def _iso_to_ts(iso_str: str) -> float:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _ts_to_aw_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def sync_once(config: Config, db: Database) -> int:
    """Perform one sync cycle. Returns the number of sessions inserted."""
    ip = config.phone_sync_ip
    port = config.phone_sync_port

    if not ip:
        logger.warning("phone_sync.tailscale_ip is not configured")
        return 0

    state = _load_state()
    bucket_id: str | None = state.get("bucket_id")
    last_event_ts: float | None = state.get("last_event_ts")

    # Find the ActivityWatch Android bucket
    try:
        if not bucket_id:
            bucket_id = _find_android_bucket(ip, port)
            if not bucket_id:
                logger.warning(
                    "No Android bucket found in ActivityWatch at %s:%d — "
                    "is the app running?", ip, port
                )
                return 0
            state["bucket_id"] = bucket_id
            logger.info("Found ActivityWatch Android bucket: %s", bucket_id)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Cannot reach ActivityWatch at %s:%d — %s", ip, port, exc)
        return 0

    # Determine sync start point
    if last_event_ts is None:
        since_dt = datetime.now(tz=timezone.utc) - timedelta(days=7)
    else:
        # Advance by 1ms past the last event to avoid re-inserting it
        since_dt = datetime.fromtimestamp(last_event_ts + 0.001, tz=timezone.utc)

    since_str = _ts_to_aw_iso(since_dt.timestamp())
    encoded_bucket = urllib.parse.quote(bucket_id, safe="")
    encoded_since = urllib.parse.quote(since_str, safe="")
    path = (
        f"/api/0/buckets/{encoded_bucket}/events"
        f"?start={encoded_since}&limit=1000"
    )

    try:
        events = _aw_get(ip, port, path)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Failed to fetch events from ActivityWatch: %s", exc)
        return 0

    if not events:
        return 0

    inserted = 0
    latest_event_ts = last_event_ts or 0.0

    for event in events:
        try:
            start_ts = _iso_to_ts(event["timestamp"])
            duration = float(event.get("duration", 0))
            if duration < 3:
                continue

            data = event.get("data", {})
            package = data.get("app", "") or data.get("package", "")

            # Skip screen-off / lock events
            if not package or data.get("locked", False):
                continue

            end_ts = start_ts + duration
            display = _resolve_display_name(package, config)
            category = _resolve_category(package, config)

            db.insert_completed_session(
                app_name=package,
                display_name=display,
                window_title="",
                process_name=package,
                exe_path="",
                category=category,
                is_idle=False,
                start_time=start_ts,
                end_time=end_ts,
                device="phone",
            )
            inserted += 1
            if start_ts > latest_event_ts:
                latest_event_ts = start_ts

        except Exception:
            logger.exception("Error processing ActivityWatch event: %s", event)

    if latest_event_ts > (last_event_ts or 0):
        state["last_event_ts"] = latest_event_ts
        _save_state(state)

    if inserted > 0:
        logger.info("Phone sync: inserted %d sessions (bucket: %s)", inserted, bucket_id)
    return inserted


class PhoneSyncThread:
    """Background thread that periodically syncs phone activity."""

    def __init__(self, config: Config, db: Database) -> None:
        self._config = config
        self._db = db
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="phone-sync", daemon=True
        )
        self._thread.start()
        logger.info(
            "Phone sync started (interval: %ds, target: %s:%d)",
            self._config.phone_sync_interval,
            self._config.phone_sync_ip,
            self._config.phone_sync_port,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sync_once(self._config, self._db)
            except Exception:
                logger.exception("Phone sync error")
            self._stop.wait(self._config.phone_sync_interval)
