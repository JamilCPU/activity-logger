"""Flask web dashboard — serves the UI and JSON API."""

from __future__ import annotations

import datetime
import time

from flask import Flask, jsonify, render_template, request

from activity_logger.analysis.exporter import export_report
from activity_logger.config import Config
from activity_logger.storage.db import Database


def create_app(db: Database, config: Config) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["db"] = db
    app.config["cfg"] = config

    @app.route("/")
    def index():
        today = datetime.date.today().isoformat()
        return render_template("index.html", today=today)

    # ── Summary ───────────────────────────────────────────────────────────────

    @app.route("/api/summary")
    def api_summary():
        date_str = request.args.get("date", datetime.date.today().isoformat())
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify(error="Invalid date"), 400

        start_ts, end_ts = _day_range(date)
        db: Database = app.config["db"]

        app_totals = db.get_app_totals(start_ts, end_ts)
        cat_totals = db.get_category_totals(start_ts, end_ts)
        idle_secs = db.get_idle_total(start_ts, end_ts)
        active_secs = sum(r["total_seconds"] for r in app_totals)
        hourly = db.get_hourly_breakdown(start_ts, end_ts)

        return jsonify(
            date=date_str,
            active_seconds=active_secs,
            idle_seconds=idle_secs,
            apps=app_totals,
            categories=cat_totals,
            hourly={str(k): v for k, v in hourly.items()},
        )

    # ── Timeline ──────────────────────────────────────────────────────────────

    @app.route("/api/timeline")
    def api_timeline():
        date_str = request.args.get("date", datetime.date.today().isoformat())
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify(error="Invalid date"), 400

        start_ts, end_ts = _day_range(date)
        db: Database = app.config["db"]
        sessions = db.get_sessions(start_ts, end_ts, include_idle=True)

        return jsonify(sessions=[
            {
                "app": s.display_name,
                "category": s.category,
                "title": s.window_title,
                "start": s.start_time,
                "end": s.end_time,
                "duration": s.duration_seconds,
                "is_idle": s.is_idle,
            }
            for s in sessions
        ])

    # ── Heatmap (last N days) ─────────────────────────────────────────────────

    @app.route("/api/heatmap")
    def api_heatmap():
        days = int(request.args.get("days", 28))
        db: Database = app.config["db"]

        today = datetime.date.today()
        result: list[dict] = []
        for i in range(days):
            d = today - datetime.timedelta(days=days - 1 - i)
            start_ts, end_ts = _day_range(d)
            active = sum(r["total_seconds"] for r in db.get_app_totals(start_ts, end_ts))
            result.append({"date": d.isoformat(), "active_seconds": active})

        return jsonify(days=result)

    # ── Available dates ───────────────────────────────────────────────────────

    @app.route("/api/dates")
    def api_dates():
        db: Database = app.config["db"]
        return jsonify(dates=db.get_available_dates())

    # ── Export ────────────────────────────────────────────────────────────────

    @app.route("/api/export")
    def api_export():
        start_str = request.args.get("start", datetime.date.today().isoformat())
        end_str = request.args.get("end", start_str)
        fmt = request.args.get("format", "json")
        db: Database = app.config["db"]
        cfg: Config = app.config["cfg"]

        try:
            start = datetime.date.fromisoformat(start_str)
            end = datetime.date.fromisoformat(end_str)
        except ValueError:
            return jsonify(error="Invalid date"), 400

        report = export_report(
            db, start, end,
            fmt="json" if fmt == "json" else "markdown",
            focus_threshold_minutes=cfg.focus_session_minutes,
        )

        if fmt == "json":
            import json
            return jsonify(json.loads(report))
        else:
            return report, 200, {"Content-Type": "text/plain; charset=utf-8"}

    # ── Recent activity ───────────────────────────────────────────────────────

    @app.route("/api/recent")
    def api_recent():
        limit = int(request.args.get("limit", 20))
        db: Database = app.config["db"]
        return jsonify(sessions=db.get_recent_activity(limit))

    return app


def _day_range(date: datetime.date) -> tuple[float, float]:
    start = datetime.datetime.combine(date, datetime.time.min).timestamp()
    end = datetime.datetime.combine(date, datetime.time.max).timestamp()
    return start, end
