# Activity Logger

A lightweight personal activity tracker that logs every app you use, detects when you're idle, and produces clean reports you can feed directly to an AI for habit analysis and insights.

**All data stays on your machine — nothing is ever transmitted anywhere.**

---

## Features

- **Accurate app tracking** — records the focused window every 5 seconds using OS-native APIs
- **Idle detection** — uses OS-level last-input events (not screen timeout) to distinguish genuine away time from passive computer use
- **Smart categorization** — maps apps to categories (Gaming, Development, Browsing, Communication, etc.) via a configurable rules file
- **Web dashboard** — beautiful local UI at `localhost:7070` with charts, timeline, and heatmap
- **LLM-optimized export** — `activity-logger export` generates a structured Markdown or JSON report you can paste directly into Claude, ChatGPT, or any AI
- **Cross-platform** — Windows, macOS, Linux
- **Autostart** — one command to register as a login service on any platform

---

## Install

```bash
git clone https://github.com/YOUR_USERNAME/activity-logger
cd activity-logger
pip install -e .
```

> **Windows users:** `pywin32` is required and installed automatically. If you see import errors, run `pip install pywin32` and then `python Scripts/pywin32_postinstall.py -install`.
>
> **Linux users:** install `xdotool` and `xprintidle` for full functionality:
> ```bash
> sudo apt install xdotool xprintidle   # Debian/Ubuntu
> sudo dnf install xdotool              # Fedora
> ```

---

## Quick Start

```bash
# Start tracking in the background
activity-logger start

# Open the web dashboard
activity-logger dashboard

# See today's summary in the terminal
activity-logger status

# Export for AI analysis (copy and paste into Claude/ChatGPT)
activity-logger export

# Export the last 7 days
activity-logger export --days 7

# Stop the tracker
activity-logger stop
```

---

## Configuration

Copy the example config and customize it:

```bash
mkdir -p ~/.activity-logger
cp config.example.yaml ~/.activity-logger/config.yaml
```

Key settings in `~/.activity-logger/config.yaml`:

```yaml
tracking:
  poll_interval_seconds: 5      # sampling frequency
  idle_threshold_minutes: 5     # minutes without input = idle

categories:
  - name: Gaming
    apps: [MarvelRivals, steam, ...]
```

Add your own apps under the right categories. The matching is case-insensitive substring — `"MarvelRivals"` matches a process named `MarvelRivals.exe`.

---

## Autostart (start on login)

```bash
python scripts/install_autostart.py
```

This registers a login service appropriate for your OS:
- **Windows** — Task Scheduler entry
- **macOS** — `~/Library/LaunchAgents/` plist
- **Linux** — `systemd --user` service

To remove:
```bash
python scripts/install_autostart.py --remove
```

---

## Exporting for AI Analysis

The `export` command generates a report optimized for pasting into an AI assistant:

```bash
# Today's report (Markdown — best for LLMs)
activity-logger export

# Last 7 days
activity-logger export --days 7

# Save to file
activity-logger export --days 30 --output monthly_report.md

# JSON (for programmatic use)
activity-logger export --format json
```

**Sample output:**
```
# Activity Report: 2026-03-29 (Sunday)

## Overview
- Total tracked: 9h 12m
- Active time: 7h 30m
- Idle time: 1h 42m
- Sessions recorded: 312

## Time by Application
| App           | Category    | Duration | % of Active |
|---------------|-------------|----------|-------------|
| Marvel Rivals | Gaming      | 2h 15m   | 30.0%       |
| Google Chrome | Browsing    | 1h 45m   | 23.3%       |
| VS Code       | Development | 1h 20m   | 17.8%       |

## Focus Sessions (≥30m uninterrupted)
- **09:30–11:45** Marvel Rivals (2h 15m)
- **14:00–15:30** VS Code (1h 30m)
```

Paste this into an AI and ask questions like:
- *"What habits do you notice in my computer usage?"*
- *"How much time did I actually spend being productive vs. gaming?"*
- *"What time of day am I most focused?"*

---

## CLI Reference

```
activity-logger start         Start the tracker daemon
  --foreground / -f           Run in the terminal (don't detach)
  --verbose / -v              Show debug output

activity-logger stop          Stop the running daemon
activity-logger status        Show running status + today's top apps

activity-logger dashboard     Open the web UI
  --port PORT                 Override dashboard port (default: 7070)
  --no-browser                Don't auto-open the browser

activity-logger export        Generate an activity report
  --date YYYY-MM-DD           Specific date (default: today)
  --days N                    Last N days
  --format markdown|json      Output format (default: markdown)
  --output FILE               Write to file instead of stdout

activity-logger summary       Quick N-day terminal table
  --days N                    Number of days (default: 7)
```

---

## Data Storage

Data is stored in a local SQLite database at `~/.activity-logger/activity.db`.

```
~/.activity-logger/
├── activity.db       ← all your data
├── config.yaml       ← your configuration
└── tracker.pid       ← running process ID
```

You can query it directly with any SQLite tool:

```sql
-- Total time per app today
SELECT display_name, SUM(duration_seconds)/3600.0 as hours
FROM sessions
WHERE date(start_time, 'unixepoch', 'localtime') = date('now', 'localtime')
  AND is_idle = 0
GROUP BY display_name ORDER BY hours DESC;
```

---

## Privacy

- All data is stored **locally on your machine**
- Nothing is sent to any server, API, or third party
- The dashboard only binds to `127.0.0.1` (localhost) by default
- You can delete `~/.activity-logger/activity.db` at any time to erase all history

---

## Platform Notes

| Platform | Window Tracking | Idle Detection | Tray Icon |
|----------|----------------|----------------|-----------|
| Windows  | `pywin32` + `psutil` | Win32 `GetLastInputInfo` | `pystray` |
| macOS    | AppleScript via `osascript` | `ioreg -c IOHIDSystem` | `pystray` |
| Linux    | `xdotool` | `xprintidle` | Not included |

On **macOS**, you may need to grant Accessibility permissions to Terminal/your shell the first time.

On **Linux**, the tracker requires a running X session. Wayland support via `xdotool` depends on your compositor's XWayland compatibility layer.

---

## License

MIT
