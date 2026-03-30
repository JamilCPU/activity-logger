"""Command-line interface for activity-logger."""

from __future__ import annotations

import datetime
import logging
import os
import sys
import time
from pathlib import Path

import click

from activity_logger.config import load_config
from activity_logger.storage.db import Database

# ── Helpers ───────────────────────────────────────────────────────────────────

PID_FILE = Path.home() / ".activity-logger" / "tracker.pid"


def _get_db_and_config(config_path: str | None):
    cfg = load_config(Path(config_path) if config_path else None)
    db = Database(cfg.db_path)
    db.connect()
    return db, cfg


def _fmt(secs: float) -> str:
    if secs < 60:
        return f"{int(secs)}s"
    m = int(secs // 60)
    if m < 60:
        return f"{m}m"
    h, rm = divmod(m, 60)
    return f"{h}h {rm}m" if rm else f"{h}h"


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def _clear_pid() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def _is_running(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
@click.option("--config", default=None, help="Path to config.yaml")
@click.pass_context
def cli(ctx: click.Context, config: str | None) -> None:
    """Personal activity logger — track time, export insights."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ── start ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
@click.option("--verbose", "-v", is_flag=True, help="Show debug logs")
@click.pass_context
def start(ctx: click.Context, foreground: bool, verbose: bool) -> None:
    """Start the activity tracker daemon."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    existing_pid = _read_pid()
    if existing_pid and _is_running(existing_pid):
        click.echo(f"Tracker is already running (PID {existing_pid})")
        return

    cfg_path = ctx.obj.get("config_path")
    db, cfg = _get_db_and_config(cfg_path)

    from activity_logger.tracker import Tracker
    tracker = Tracker(config=cfg, db=db)

    if foreground:
        click.echo("Starting activity tracker in foreground (Ctrl+C to stop)…")
        _write_pid(os.getpid())
        try:
            tracker.run_forever()
        finally:
            _clear_pid()
            db.close()
    else:
        # Spawn as detached background process
        import subprocess
        args = [sys.executable, "-m", "activity_logger", "start", "--foreground"]
        if cfg_path:
            args += ["--config", cfg_path]
        if verbose:
            args += ["--verbose"]

        if sys.platform == "win32":
            proc = subprocess.Popen(
                args,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )

        time.sleep(0.5)  # brief wait for PID file
        click.echo(f"Activity tracker started (PID {proc.pid})")
        click.echo(f"Data stored at: {cfg.db_path}")
        click.echo("Run 'activity-logger dashboard' to open the web UI.")


# ── stop ──────────────────────────────────────────────────────────────────────

@cli.command()
def stop() -> None:
    """Stop the running tracker daemon."""
    pid = _read_pid()
    if not pid:
        click.echo("No tracker appears to be running (no PID file found)")
        return
    if not _is_running(pid):
        click.echo(f"Tracker PID {pid} is not running — cleaning up")
        _clear_pid()
        return

    import signal as _signal
    try:
        os.kill(pid, _signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.25)
            if not _is_running(pid):
                break
        _clear_pid()
        click.echo(f"Tracker stopped (was PID {pid})")
    except OSError as e:
        click.echo(f"Failed to stop tracker: {e}", err=True)


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show tracker status and today's top apps."""
    pid = _read_pid()
    if pid and _is_running(pid):
        click.echo(click.style(f"[ON]  Tracker is running  (PID {pid})", fg="green"))
    else:
        click.echo(click.style("[OFF] Tracker is NOT running", fg="yellow"))
        if pid:
            _clear_pid()

    db, cfg = _get_db_and_config(ctx.obj.get("config_path"))
    today = datetime.date.today()
    start_ts = datetime.datetime.combine(today, datetime.time.min).timestamp()
    end_ts = datetime.datetime.combine(today, datetime.time.max).timestamp()

    app_totals = db.get_app_totals(start_ts, end_ts)
    idle_secs = db.get_idle_total(start_ts, end_ts)
    active_secs = sum(r["total_seconds"] for r in app_totals)

    click.echo(f"\nToday ({today}):")
    click.echo(f"  Active: {_fmt(active_secs)}   Idle: {_fmt(idle_secs)}")

    if app_totals:
        click.echo("\nTop apps:")
        for row in app_totals[:10]:
            pct = row["total_seconds"] / active_secs * 100 if active_secs else 0
            bar = "#" * int(pct / 5)
            click.echo(f"  {row['display_name']:<28} {_fmt(row['total_seconds']):<8} {bar} {pct:.1f}%")
    else:
        click.echo("  No activity recorded yet today.")

    db.close()


# ── dashboard ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port", default=None, type=int, help="Port (default from config)")
@click.option("--no-browser", is_flag=True, help="Don't auto-open the browser")
@click.pass_context
def dashboard(ctx: click.Context, port: int | None, no_browser: bool) -> None:
    """Start the web dashboard."""
    db, cfg = _get_db_and_config(ctx.obj.get("config_path"))
    effective_port = port or cfg.dashboard_port
    host = cfg.dashboard_host
    url = f"http://{host}:{effective_port}"

    from activity_logger.dashboard import create_app
    app = create_app(db, cfg)

    if cfg.auto_open_browser and not no_browser:
        import threading, webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    click.echo(f"Dashboard running at {url}  (Ctrl+C to stop)")
    app.run(host=host, port=effective_port, debug=False, use_reloader=False)


# ── export ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--date", default=None, help="Single date YYYY-MM-DD (default: today)")
@click.option("--days", default=1, type=int, help="Last N days (overrides --date)")
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]), help="Output format")
@click.option("--output", "-o", default=None, help="Write to file instead of stdout")
@click.pass_context
def export(ctx: click.Context, date: str | None, days: int, fmt: str, output: str | None) -> None:
    """Export activity data for AI analysis."""
    db, cfg = _get_db_and_config(ctx.obj.get("config_path"))

    if days > 1:
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days - 1)
    else:
        target = datetime.date.fromisoformat(date) if date else datetime.date.today()
        start_date = end_date = target

    from activity_logger.analysis.exporter import export_report
    report = export_report(
        db, start_date, end_date,
        fmt=fmt,  # type: ignore[arg-type]
        focus_threshold_minutes=cfg.focus_session_minutes,
    )
    db.close()

    if output:
        Path(output).write_text(report, encoding="utf-8")
        click.echo(f"Report written to {output}")
    else:
        click.echo(report)


# ── summary ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--days", default=7, type=int, help="Number of days to show")
@click.pass_context
def summary(ctx: click.Context, days: int) -> None:
    """Quick terminal summary of the last N days."""
    db, cfg = _get_db_and_config(ctx.obj.get("config_path"))

    click.echo(f"\n{'Date':<12} {'Active':>8} {'Idle':>8}  Top App")
    click.echo("─" * 60)

    today = datetime.date.today()
    for i in range(days - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        start_ts = datetime.datetime.combine(d, datetime.time.min).timestamp()
        end_ts = datetime.datetime.combine(d, datetime.time.max).timestamp()

        apps = db.get_app_totals(start_ts, end_ts)
        idle = db.get_idle_total(start_ts, end_ts)
        active = sum(r["total_seconds"] for r in apps)
        top_app = apps[0]["display_name"] if apps else "—"

        marker = " <- today" if i == 0 else ""
        click.echo(f"{str(d):<12} {_fmt(active):>8} {_fmt(idle):>8}  {top_app}{marker}")

    click.echo()
    db.close()
