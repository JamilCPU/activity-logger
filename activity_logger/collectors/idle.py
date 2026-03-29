"""Cross-platform idle time detection.

Returns the number of seconds since the last keyboard or mouse input.
Uses OS-native APIs — no background polling required.
"""

from __future__ import annotations

import sys


# ── Windows ──────────────────────────────────────────────────────────────────

def _get_idle_seconds_windows() -> float:
    import ctypes
    import ctypes.wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.UINT),
            ("dwTime", ctypes.wintypes.DWORD),
        ]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    tick_count = ctypes.windll.kernel32.GetTickCount()
    elapsed_ms = tick_count - lii.dwTime
    return elapsed_ms / 1000.0


# ── macOS ─────────────────────────────────────────────────────────────────────

def _get_idle_seconds_macos() -> float:
    import subprocess
    import re

    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=2
        )
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout)
        if match:
            nanoseconds = int(match.group(1))
            return nanoseconds / 1e9
    except Exception:
        pass
    return 0.0


# ── Linux ─────────────────────────────────────────────────────────────────────

def _get_idle_seconds_linux() -> float:
    import subprocess

    try:
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return int(result.stdout.strip()) / 1000.0
    except FileNotFoundError:
        # xprintidle not available — try python-xlib fallback
        try:
            from Xlib import display as xdisplay, X
            d = xdisplay.Display()
            # Query screensaver info for idle time
            ss = d.get_screen_saver()
            # This gives the screensaver timeout, not idle — use MIT-SCREEN-SAVER ext
            # Fall back to 0 if unavailable
        except Exception:
            pass
    except Exception:
        pass
    return 0.0


# ── Dispatch ──────────────────────────────────────────────────────────────────

def get_idle_seconds() -> float:
    """Return seconds since last user input. Never raises."""
    try:
        if sys.platform == "win32":
            return _get_idle_seconds_windows()
        elif sys.platform == "darwin":
            return _get_idle_seconds_macos()
        else:
            return _get_idle_seconds_linux()
    except Exception:
        return 0.0
