"""Cross-platform active window detection.

Returns a WindowInfo with:
  - process_name: raw executable name (e.g. "chrome.exe")
  - window_title: current foreground window title
  - pid: process ID
  - exe_path: full path to executable (best-effort)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass
class WindowInfo:
    process_name: str = ""
    window_title: str = ""
    pid: int = 0
    exe_path: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WindowInfo):
            return False
        return self.process_name == other.process_name

    def is_valid(self) -> bool:
        return bool(self.process_name)


# ── Windows ──────────────────────────────────────────────────────────────────

def _get_active_window_windows() -> WindowInfo:
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return WindowInfo()

        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        try:
            proc = psutil.Process(pid)
            process_name = proc.name()
            exe_path = proc.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "Unknown"
            exe_path = ""

        return WindowInfo(
            process_name=process_name,
            window_title=title,
            pid=pid,
            exe_path=exe_path,
        )
    except Exception:
        return WindowInfo()


# ── macOS ─────────────────────────────────────────────────────────────────────

def _get_active_window_macos() -> WindowInfo:
    import subprocess
    import re

    script = """
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set appName to name of frontApp
        set appPID to unix id of frontApp
        try
            set winTitle to name of front window of frontApp
        on error
            set winTitle to ""
        end try
        return appName & "|" & appPID & "|" & winTitle
    end tell
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode != 0:
            return WindowInfo()
        parts = result.stdout.strip().split("|", 2)
        if len(parts) < 2:
            return WindowInfo()
        app_name = parts[0]
        pid = int(parts[1]) if parts[1].isdigit() else 0
        title = parts[2] if len(parts) > 2 else ""

        # Try to get exe path via psutil
        exe_path = ""
        try:
            import psutil
            proc = psutil.Process(pid)
            exe_path = proc.exe()
        except Exception:
            pass

        return WindowInfo(
            process_name=app_name,
            window_title=title,
            pid=pid,
            exe_path=exe_path,
        )
    except Exception:
        return WindowInfo()


# ── Linux ─────────────────────────────────────────────────────────────────────

def _get_active_window_linux() -> WindowInfo:
    import subprocess

    try:
        # Get window ID
        wid_result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2
        )
        if wid_result.returncode != 0:
            return WindowInfo()
        wid = wid_result.stdout.strip()

        # Get window title
        title_result = subprocess.run(
            ["xdotool", "getwindowname", wid],
            capture_output=True, text=True, timeout=2
        )
        title = title_result.stdout.strip()

        # Get PID
        pid_result = subprocess.run(
            ["xdotool", "getwindowpid", wid],
            capture_output=True, text=True, timeout=2
        )
        pid = int(pid_result.stdout.strip()) if pid_result.stdout.strip().isdigit() else 0

        process_name = ""
        exe_path = ""
        if pid:
            try:
                import psutil
                proc = psutil.Process(pid)
                process_name = proc.name()
                exe_path = proc.exe()
            except Exception:
                pass

        return WindowInfo(
            process_name=process_name or title,
            window_title=title,
            pid=pid,
            exe_path=exe_path,
        )
    except FileNotFoundError:
        # xdotool not installed
        return WindowInfo(
            process_name="xdotool-missing",
            window_title="Install xdotool for window tracking",
        )
    except Exception:
        return WindowInfo()


# ── Dispatch ──────────────────────────────────────────────────────────────────

def get_active_window() -> WindowInfo:
    """Return the currently focused window. Never raises."""
    if sys.platform == "win32":
        return _get_active_window_windows()
    elif sys.platform == "darwin":
        return _get_active_window_macos()
    else:
        return _get_active_window_linux()
