"""Register activity-logger to start automatically on login.

Usage:
    python scripts/install_autostart.py          # install
    python scripts/install_autostart.py --remove # uninstall
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_executable() -> str:
    exe = shutil.which("activity-logger")
    if exe:
        return exe
    # Try in the same venv as the current Python
    if sys.platform == "win32":
        candidate = Path(sys.prefix) / "Scripts" / "activity-logger.exe"
    else:
        candidate = Path(sys.prefix) / "bin" / "activity-logger"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError(
        "activity-logger not found on PATH. "
        "Make sure you've run: pip install -e ."
    )


# ── Windows ───────────────────────────────────────────────────────────────────

def install_windows(exe: str) -> None:
    task_name = "ActivityLogger"
    cmd = (
        f'schtasks /Create /TN "{task_name}" '
        f'/TR "\\"{exe}\\" start" '
        '/SC ONLOGON /RL LIMITED /F'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Task Scheduler entry '{task_name}' created.")
        print("Activity logger will start automatically on next login.")
    else:
        print(f"Failed to create task: {result.stderr}", file=sys.stderr)
        sys.exit(1)


def remove_windows() -> None:
    task_name = "ActivityLogger"
    result = subprocess.run(
        f'schtasks /Delete /TN "{task_name}" /F',
        shell=True, capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"Task Scheduler entry '{task_name}' removed.")
    else:
        print(f"Could not remove task (may not exist): {result.stderr}")


# ── macOS ─────────────────────────────────────────────────────────────────────

MACOS_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.activity-logger.plist"
MACOS_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.activity-logger</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>start</string>
        <string>--foreground</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/activity-logger.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/activity-logger-error.log</string>
</dict>
</plist>
"""


def install_macos(exe: str) -> None:
    log_dir = Path.home() / ".activity-logger"
    log_dir.mkdir(parents=True, exist_ok=True)
    MACOS_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MACOS_PLIST_PATH.write_text(
        MACOS_PLIST_TEMPLATE.format(exe=exe, log_dir=log_dir)
    )
    subprocess.run(["launchctl", "load", str(MACOS_PLIST_PATH)], check=True)
    print(f"LaunchAgent installed: {MACOS_PLIST_PATH}")
    print("Activity logger will start automatically on next login.")


def remove_macos() -> None:
    if MACOS_PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(MACOS_PLIST_PATH)])
        MACOS_PLIST_PATH.unlink()
        print("LaunchAgent removed.")
    else:
        print("No LaunchAgent found to remove.")


# ── Linux ─────────────────────────────────────────────────────────────────────

LINUX_SERVICE_DIR = Path.home() / ".config" / "systemd" / "user"
LINUX_SERVICE_PATH = LINUX_SERVICE_DIR / "activity-logger.service"
LINUX_SERVICE_TEMPLATE = """[Unit]
Description=Activity Logger — personal activity tracking daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart={exe} start --foreground
Restart=on-failure
RestartSec=10
Environment=DISPLAY=:0

[Install]
WantedBy=default.target
"""


def install_linux(exe: str) -> None:
    LINUX_SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    LINUX_SERVICE_PATH.write_text(LINUX_SERVICE_TEMPLATE.format(exe=exe))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "activity-logger"], check=True)
    subprocess.run(["systemctl", "--user", "start", "activity-logger"], check=True)
    print(f"systemd user service installed: {LINUX_SERVICE_PATH}")
    print("Activity logger is running and will start automatically on login.")


def remove_linux() -> None:
    subprocess.run(["systemctl", "--user", "stop", "activity-logger"])
    subprocess.run(["systemctl", "--user", "disable", "activity-logger"])
    if LINUX_SERVICE_PATH.exists():
        LINUX_SERVICE_PATH.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print("systemd user service removed.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Install/remove activity-logger autostart")
    parser.add_argument("--remove", action="store_true", help="Remove the autostart entry")
    args = parser.parse_args()

    if args.remove:
        if sys.platform == "win32":
            remove_windows()
        elif sys.platform == "darwin":
            remove_macos()
        else:
            remove_linux()
    else:
        exe = find_executable()
        print(f"Installing autostart for: {exe}")
        if sys.platform == "win32":
            install_windows(exe)
        elif sys.platform == "darwin":
            install_macos(exe)
        else:
            install_linux(exe)


if __name__ == "__main__":
    main()
