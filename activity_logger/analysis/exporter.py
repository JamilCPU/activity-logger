"""Generate LLM-optimized activity reports (Markdown and JSON).

The Markdown output is designed to be pasted directly into an AI chat
to get insights about habits, time usage, and patterns.
"""

from __future__ import annotations

import datetime
import json
from typing import Literal

from activity_logger.storage.db import Database


def _fmt_duration(seconds: float) -> str:
    """Format seconds as human-readable duration: '2h 15m' or '43m' or '30s'."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}m"


def _day_range(date: datetime.date) -> tuple[float, float]:
    """Return Unix timestamps for start and end of a local calendar day."""
    start = datetime.datetime.combine(date, datetime.time.min)
    end = datetime.datetime.combine(date, datetime.time.max)
    return start.timestamp(), end.timestamp()


def _bar(seconds: float, max_seconds: float, width: int = 20) -> str:
    if max_seconds == 0:
        return ""
    filled = int(width * seconds / max_seconds)
    return "█" * filled + "░" * (width - filled)


def export_report(
    db: Database,
    start_date: datetime.date,
    end_date: datetime.date,
    fmt: Literal["markdown", "json"] = "markdown",
    focus_threshold_minutes: int = 30,
) -> str:
    """Generate a report covering [start_date, end_date] inclusive."""
    if fmt == "json":
        return _export_json(db, start_date, end_date)
    return _export_markdown(db, start_date, end_date, focus_threshold_minutes)


# ── Markdown ──────────────────────────────────────────────────────────────────

def _export_markdown(
    db: Database,
    start_date: datetime.date,
    end_date: datetime.date,
    focus_threshold_minutes: int,
) -> str:
    lines: list[str] = []
    multi_day = start_date != end_date

    if multi_day:
        lines.append(f"# Activity Report: {start_date} to {end_date}\n")
    else:
        dow = start_date.strftime("%A")
        lines.append(f"# Activity Report: {start_date} ({dow})\n")

    # Collect data across the full range
    range_start = datetime.datetime.combine(start_date, datetime.time.min).timestamp()
    range_end = datetime.datetime.combine(end_date, datetime.time.max).timestamp()

    sessions = db.get_sessions(range_start, range_end, include_idle=True)
    active_sessions = [s for s in sessions if not s.is_idle]
    idle_sessions = [s for s in sessions if s.is_idle]

    total_seconds = sum(s.duration_seconds or 0 for s in sessions)
    active_seconds = sum(s.duration_seconds or 0 for s in active_sessions)
    idle_seconds = sum(s.duration_seconds or 0 for s in idle_sessions)
    session_count = len(sessions)

    # ── Overview ──────────────────────────────────────────────────────────────
    lines.append("## Overview\n")
    lines.append(f"- **Total tracked:** {_fmt_duration(total_seconds)}")
    lines.append(f"- **Active time:** {_fmt_duration(active_seconds)}")
    lines.append(f"- **Idle time:** {_fmt_duration(idle_seconds)}")
    lines.append(f"- **Sessions recorded:** {session_count}")
    if active_seconds > 0:
        idle_pct = 100 * idle_seconds / (active_seconds + idle_seconds)
        lines.append(f"- **Idle %:** {idle_pct:.1f}%")
    lines.append("")

    # ── Time by Application ───────────────────────────────────────────────────
    app_totals = db.get_app_totals(range_start, range_end, include_idle=False)
    if app_totals:
        lines.append("## Time by Application\n")
        lines.append("| App | Category | Duration | % of Active |")
        lines.append("|-----|----------|----------|-------------|")
        for row in app_totals[:20]:  # top 20
            pct = (row["total_seconds"] / active_seconds * 100) if active_seconds else 0
            lines.append(
                f"| {row['display_name']} "
                f"| {row['category'] or 'Other'} "
                f"| {_fmt_duration(row['total_seconds'])} "
                f"| {pct:.1f}% |"
            )
        lines.append("")

    # ── Time by Category ──────────────────────────────────────────────────────
    cat_totals = db.get_category_totals(range_start, range_end)
    if cat_totals:
        lines.append("## Time by Category\n")
        lines.append("| Category | Duration | % of Active |")
        lines.append("|----------|----------|-------------|")
        for row in cat_totals:
            pct = (row["total_seconds"] / active_seconds * 100) if active_seconds else 0
            lines.append(
                f"| {row['category']} "
                f"| {_fmt_duration(row['total_seconds'])} "
                f"| {pct:.1f}% |"
            )
        lines.append("")

    # ── Hourly Activity (single-day only) ─────────────────────────────────────
    if not multi_day:
        hourly = db.get_hourly_breakdown(range_start, range_end)
        max_h = max(hourly.values()) if hourly else 1
        has_any = any(v > 0 for v in hourly.values())
        if has_any:
            lines.append("## Hourly Activity\n")
            lines.append("```")
            for hour in range(24):
                secs = hourly.get(hour, 0)
                if secs > 0:
                    bar = _bar(secs, max_h, width=24)
                    label = f"{hour:02d}:00"
                    lines.append(f"{label}  {bar}  {_fmt_duration(secs)}")
            lines.append("```")
            lines.append("")

    # ── Idle Periods ──────────────────────────────────────────────────────────
    notable_idle = [
        s for s in idle_sessions
        if (s.duration_seconds or 0) >= 120  # only show idle > 2 minutes
    ]
    if notable_idle:
        lines.append("## Idle Periods\n")
        for s in notable_idle:
            start_str = datetime.datetime.fromtimestamp(s.start_time).strftime("%H:%M")
            end_str = datetime.datetime.fromtimestamp(s.end_time).strftime("%H:%M") if s.end_time else "?"
            dur = _fmt_duration(s.duration_seconds or 0)
            lines.append(f"- {start_str}–{end_str} ({dur})")
        lines.append("")

    # ── Focus Sessions ────────────────────────────────────────────────────────
    threshold_secs = focus_threshold_minutes * 60
    focus_sessions = [
        s for s in active_sessions
        if (s.duration_seconds or 0) >= threshold_secs
    ]
    if focus_sessions:
        lines.append(f"## Focus Sessions (≥{focus_threshold_minutes}m uninterrupted)\n")
        for s in sorted(focus_sessions, key=lambda x: x.start_time):
            start_str = datetime.datetime.fromtimestamp(s.start_time).strftime("%H:%M")
            end_str = datetime.datetime.fromtimestamp(s.end_time).strftime("%H:%M") if s.end_time else "?"
            dur = _fmt_duration(s.duration_seconds or 0)
            lines.append(f"- **{start_str}–{end_str}** {s.display_name} ({dur})")
        lines.append("")

    # ── AI Analysis Notes ─────────────────────────────────────────────────────
    lines.append("## Notes for AI Analysis\n")

    if active_sessions:
        first_active = min(active_sessions, key=lambda s: s.start_time)
        last_active = max(active_sessions, key=lambda s: s.end_time or 0)
        first_str = datetime.datetime.fromtimestamp(first_active.start_time).strftime("%H:%M")
        last_str = datetime.datetime.fromtimestamp(last_active.end_time or last_active.start_time).strftime("%H:%M")
        lines.append(f"- Active window: {first_str} to {last_str}")

    # Peak hour
    if not multi_day:
        hourly = db.get_hourly_breakdown(range_start, range_end)
        if any(v > 0 for v in hourly.values()):
            peak_hour = max(hourly, key=hourly.get)  # type: ignore[arg-type]
            lines.append(f"- Peak activity hour: {peak_hour:02d}:00")

    # Top app
    if app_totals:
        top = app_totals[0]
        lines.append(
            f"- Most used app: {top['display_name']} ({_fmt_duration(top['total_seconds'])})"
        )

    # Top category
    if cat_totals:
        top_cat = cat_totals[0]
        lines.append(
            f"- Dominant category: {top_cat['category']} ({_fmt_duration(top_cat['total_seconds'])})"
        )

    if focus_sessions:
        longest_focus = max(focus_sessions, key=lambda s: s.duration_seconds or 0)
        lines.append(
            f"- Longest focus block: {_fmt_duration(longest_focus.duration_seconds or 0)} "
            f"on {longest_focus.display_name}"
        )

    lines.append(
        "\n*This data was automatically collected by activity-logger. "
        "All tracking is local — no data leaves your machine.*"
    )

    return "\n".join(lines)


# ── JSON ──────────────────────────────────────────────────────────────────────

def _export_json(
    db: Database,
    start_date: datetime.date,
    end_date: datetime.date,
) -> str:
    range_start = datetime.datetime.combine(start_date, datetime.time.min).timestamp()
    range_end = datetime.datetime.combine(end_date, datetime.time.max).timestamp()

    sessions = db.get_sessions(range_start, range_end, include_idle=True)
    active_sessions = [s for s in sessions if not s.is_idle]
    idle_seconds = db.get_idle_total(range_start, range_end)
    active_seconds = sum(s.duration_seconds or 0 for s in active_sessions)

    data = {
        "report": {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "generated_at": datetime.datetime.now().isoformat(),
        },
        "overview": {
            "total_seconds": active_seconds + idle_seconds,
            "active_seconds": active_seconds,
            "idle_seconds": idle_seconds,
            "session_count": len(sessions),
        },
        "by_app": db.get_app_totals(range_start, range_end),
        "by_category": db.get_category_totals(range_start, range_end),
        "sessions": [
            {
                "app": s.display_name,
                "category": s.category,
                "title": s.window_title,
                "start": datetime.datetime.fromtimestamp(s.start_time).isoformat(),
                "end": datetime.datetime.fromtimestamp(s.end_time).isoformat() if s.end_time else None,
                "duration_seconds": s.duration_seconds,
                "is_idle": s.is_idle,
            }
            for s in sessions
        ],
    }
    return json.dumps(data, indent=2, default=str)
