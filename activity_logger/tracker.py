"""Main tracking loop — session state machine.

The tracker runs in a background thread. Every `poll_interval` seconds it:
1. Checks how long the user has been idle.
2. If idle (>= threshold): opens or continues an idle session.
3. If active: checks the focused window. If it changed, closes the old
   session and opens a new one.

Sessions shorter than `min_session_seconds` are discarded on close.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from activity_logger.collectors import get_active_window, get_idle_seconds, WindowInfo
from activity_logger.config import Config
from activity_logger.storage.db import Database

logger = logging.getLogger(__name__)


@dataclass
class _ActiveSession:
    db_id: int
    is_idle: bool
    app_name: str      # process name key for change detection
    window_title: str  # title at session open (informational)


class Tracker:
    def __init__(self, config: Config, db: Database) -> None:
        self._config = config
        self._db = db
        self._current: Optional[_ActiveSession] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start tracking in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="activity-tracker", daemon=True
        )
        self._thread.start()
        logger.info("Tracker started (poll interval: %ds)", self._config.poll_interval)

    def stop(self) -> None:
        """Signal the tracker to stop and wait for it."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._config.poll_interval + 2)
        self._close_current()
        logger.info("Tracker stopped")

    def run_forever(self) -> None:
        """Block the calling thread, running the tracker loop directly.
        Handles SIGINT/SIGTERM for clean shutdown."""
        def _handle_signal(signum, _frame):
            logger.info("Received signal %d — shutting down", signum)
            self._stop_event.set()

        signal.signal(signal.SIGINT, _handle_signal)
        try:
            signal.signal(signal.SIGTERM, _handle_signal)
        except (OSError, AttributeError):
            pass  # SIGTERM not available on all platforms

        self._run()
        self._close_current()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Error in tracker tick")
            self._stop_event.wait(timeout=self._config.poll_interval)

    def _tick(self) -> None:
        idle_secs = get_idle_seconds()
        is_idle = idle_secs >= self._config.idle_threshold_seconds

        if is_idle:
            if self._current is None or not self._current.is_idle:
                self._close_current()
                self._open_idle_session()
        else:
            window = get_active_window()
            if not window.is_valid():
                return  # OS hiccup — keep existing session

            if (
                self._current is None
                or self._current.is_idle
                or self._session_changed(window)
            ):
                self._close_current()
                self._open_window_session(window)

    def _session_changed(self, window: WindowInfo) -> bool:
        """True if the focused process has changed since the current session opened."""
        if self._current is None:
            return True
        return window.process_name.lower() != self._current.app_name.lower()

    # ── Session management ────────────────────────────────────────────────────

    def _open_window_session(self, window: WindowInfo) -> None:
        cfg = self._config
        display = cfg.resolve_display_name(window.process_name)
        category = cfg.resolve_category(window.process_name, window.window_title)

        db_id = self._db.open_session(
            app_name=window.process_name,
            display_name=display,
            window_title=window.window_title,
            process_name=window.process_name,
            exe_path=window.exe_path,
            category=category,
            is_idle=False,
        )
        self._current = _ActiveSession(
            db_id=db_id,
            is_idle=False,
            app_name=window.process_name,
            window_title=window.window_title,
        )
        logger.debug("Session opened: %s [%s]", display, category)

    def _open_idle_session(self) -> None:
        db_id = self._db.open_session(
            app_name="[idle]",
            display_name="Idle",
            window_title="",
            process_name="",
            exe_path="",
            category="Idle",
            is_idle=True,
        )
        self._current = _ActiveSession(
            db_id=db_id,
            is_idle=True,
            app_name="[idle]",
            window_title="",
        )
        logger.debug("Idle session opened")

    def _close_current(self) -> None:
        if self._current is None:
            return
        duration = self._db.close_session(
            self._current.db_id,
            min_duration=self._config.min_session_seconds,
        )
        if duration > 0:
            logger.debug(
                "Session closed: %s (%.0fs)",
                self._current.app_name,
                duration,
            )
        self._current = None
