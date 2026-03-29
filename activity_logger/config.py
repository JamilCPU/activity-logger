"""Load and merge YAML config with built-in defaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "tracking": {
        "poll_interval_seconds": 5,
        "idle_threshold_minutes": 5,
        "min_session_seconds": 3,
    },
    "storage": {
        "db_path": "~/.activity-logger/activity.db",
    },
    "dashboard": {
        "port": 7070,
        "host": "127.0.0.1",
        "auto_open_browser": True,
    },
    "export": {
        "focus_session_minutes": 30,
    },
    "categories": [
        {
            "name": "Gaming",
            "apps": ["MarvelRivals", "steam", "EpicGamesLauncher", "Battle.net",
                     "Valorant", "LeagueofLegends", "RiotClientServices", "destiny2",
                     "Minecraft", "FortniteClient"],
            "keywords": [],
        },
        {
            "name": "Development",
            "apps": ["Code", "cursor", "pycharm64", "idea64", "webstorm64",
                     "WindowsTerminal", "cmd", "pwsh", "powershell", "bash",
                     "wsl", "devenv"],
            "keywords": ["GitHub", "GitLab", "localhost", "Stack Overflow"],
        },
        {
            "name": "Communication",
            "apps": ["Discord", "Slack", "Teams", "zoom", "WhatsApp",
                     "Telegram", "signal", "outlook", "OUTLOOK", "thunderbird"],
            "keywords": [],
        },
        {
            "name": "Productivity",
            "apps": ["WINWORD", "EXCEL", "POWERPNT", "Notion", "Obsidian",
                     "onenote", "acrobat"],
            "keywords": ["Notion", "Obsidian"],
        },
        {
            "name": "Media",
            "apps": ["Spotify", "vlc", "mpv", "wmplayer"],
            "keywords": ["YouTube", "Netflix", "Twitch", "Hulu", "Disney+",
                         "Prime Video", "Spotify"],
        },
        {
            "name": "Browsing",
            "apps": ["chrome", "firefox", "msedge", "brave", "opera",
                     "vivaldi", "safari"],
            "keywords": [],
        },
        {
            "name": "System",
            "apps": ["explorer", "taskmgr", "regedit", "mmc",
                     "SystemSettings", "SearchApp"],
            "keywords": [],
        },
    ],
    "display_names": {
        "chrome": "Google Chrome",
        "msedge": "Microsoft Edge",
        "Code": "VS Code",
        "cursor": "Cursor",
        "WINWORD": "Microsoft Word",
        "EXCEL": "Microsoft Excel",
        "POWERPNT": "Microsoft PowerPoint",
        "MarvelRivals": "Marvel Rivals",
        "EpicGamesLauncher": "Epic Games",
        "LeagueofLegends": "League of Legends",
        "pycharm64": "PyCharm",
        "idea64": "IntelliJ IDEA",
        "WindowsTerminal": "Windows Terminal",
        "pwsh": "PowerShell",
        "devenv": "Visual Studio",
    },
}

_DEFAULT_CONFIG_PATHS = [
    Path.home() / ".activity-logger" / "config.yaml",
    Path.home() / ".config" / "activity-logger" / "config.yaml",
]


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class Config:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    # --- Convenience accessors ---

    @property
    def poll_interval(self) -> int:
        return self._data["tracking"]["poll_interval_seconds"]

    @property
    def idle_threshold_seconds(self) -> int:
        return self._data["tracking"]["idle_threshold_minutes"] * 60

    @property
    def min_session_seconds(self) -> int:
        return self._data["tracking"]["min_session_seconds"]

    @property
    def db_path(self) -> Path:
        raw = self._data["storage"]["db_path"]
        return Path(os.path.expanduser(raw))

    @property
    def dashboard_port(self) -> int:
        return self._data["dashboard"]["port"]

    @property
    def dashboard_host(self) -> str:
        return self._data["dashboard"]["host"]

    @property
    def auto_open_browser(self) -> bool:
        return self._data["dashboard"]["auto_open_browser"]

    @property
    def focus_session_minutes(self) -> int:
        return self._data["export"]["focus_session_minutes"]

    @property
    def categories(self) -> list[dict]:
        return self._data["categories"]

    @property
    def display_names(self) -> dict[str, str]:
        return self._data.get("display_names", {})

    def resolve_category(self, app_name: str, window_title: str) -> str:
        """Return the first matching category name, or 'Other'."""
        app_lower = app_name.lower()
        title_lower = (window_title or "").lower()
        for cat in self.categories:
            for pattern in cat.get("apps", []):
                if pattern.lower() in app_lower:
                    return cat["name"]
            for kw in cat.get("keywords", []):
                if kw.lower() in title_lower:
                    return cat["name"]
        return "Other"

    def resolve_display_name(self, process_name: str) -> str:
        """Return a human-friendly app name."""
        for key, friendly in self.display_names.items():
            if key.lower() in process_name.lower():
                return friendly
        # Auto-clean: strip extension, replace hyphens/underscores, title-case
        name = process_name
        if "." in name:
            name = name.rsplit(".", 1)[0]
        return name.replace("-", " ").replace("_", " ").strip()


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, merging with defaults."""
    data = dict(DEFAULT_CONFIG)

    if path is not None:
        candidates = [path]
    else:
        candidates = _DEFAULT_CONFIG_PATHS

    for candidate in candidates:
        if candidate.exists():
            with open(candidate) as f:
                user_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, user_data)
            break

    return Config(data)
