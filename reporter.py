from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from database import FocusDatabase, count_switches
from reporting_analysis import (
    DEFAULT_MEANINGFUL_FOCUS_SECONDS,
    best_day_this_week,
    build_focus_blocks,
    calculate_focus_score,
    calculate_totals,
    comparable_week_snapshots,
    data_quality_warnings,
    extract_browser_sites,
    longest_productive_session,
    main_finding_for_report,
    meaningful_focus_block_count,
    previous_day_snapshot,
    recommendation_for_low_confidence,
    recommendation_for_patterns,
    report_confidence_score,
    timeline_notes,
    top_apps,
    top_context_tags,
    top_distracting_sites,
    top_distracting_titles,
    interruption_metrics,
    hourly_breakdown,
)
from reporting_rendering import build_report_text


RECENT_FOCUS_BLOCK_LIMIT = 3


class DailyReporter:
    def __init__(
        self,
        database: FocusDatabase,
        reports_dir: Path,
        *,
        meaningful_focus_block_seconds: int = DEFAULT_MEANINGFUL_FOCUS_SECONDS,
    ) -> None:
        self.database = database
        self.reports_dir = reports_dir
        self.meaningful_focus_block_seconds = meaningful_focus_block_seconds
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(self, report_date: str | None = None) -> Path:
        date_value = report_date or datetime.now().date().isoformat()
        start = datetime.fromisoformat(f"{date_value}T00:00:00")
        end = start + timedelta(days=1)
        return self._generate_period_report(
            label=f"Focus Report \u2014 {date_value}",
            report_filename=f"focus-report-{date_value}.md",
            date_key=date_value,
            anchor_date=date.fromisoformat(date_value),
            rows=self.database.query_activity_between(start, end),
        )

    def generate_weekly_report(self, any_date: str | None = None) -> Path:
        target_date = date.fromisoformat(any_date) if any_date else datetime.now().date()
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = week_start + timedelta(days=7)
        return self._generate_period_report(
            label=f"Focus Report \u2014 Week of {week_start.isoformat()}",
            report_filename=f"focus-report-week-{week_start.isoformat()}.md",
            date_key=f"{week_start.isoformat()}_week",
            anchor_date=target_date,
            rows=self.database.query_activity_between(
                datetime.combine(week_start, datetime.min.time()),
                datetime.combine(week_end, datetime.min.time()),
            ),
        )

    def generate_monthly_report(self, any_date: str | None = None) -> Path:
        target_date = date.fromisoformat(any_date) if any_date else datetime.now().date()
        month_start = target_date.replace(day=1)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)

        return self._generate_period_report(
            label=f"Focus Report \u2014 {month_start.strftime('%Y-%m')}",
            report_filename=f"focus-report-month-{month_start.strftime('%Y-%m')}.md",
            date_key=f"{month_start.strftime('%Y-%m')}_month",
            anchor_date=target_date,
            rows=self.database.query_activity_between(
                datetime.combine(month_start, datetime.min.time()),
                datetime.combine(next_month, datetime.min.time()),
            ),
        )

    def _generate_period_report(
        self,
        *,
        label: str,
        report_filename: str,
        date_key: str,
        anchor_date: date,
        rows: list[dict[str, Any]],
    ) -> Path:
        totals = calculate_totals(rows)
        app_rows = top_apps(rows)
        title_rows = top_distracting_titles(rows)
        browser_sites = extract_browser_sites(rows)
        top_distracting_site_rows = top_distracting_sites(rows)
        tag_rows = top_context_tags(rows)
        hourly_rows = hourly_breakdown(rows)
        switch_count = count_switches(rows)
        longest_session_seconds, _ = longest_productive_session(rows)
        focus_blocks = build_focus_blocks(rows)
        interruptions, recovery_seconds = interruption_metrics(rows)
        previous_day = previous_day_snapshot(self.database, anchor_date)
        week_snapshots = comparable_week_snapshots(self.database, anchor_date)
        best_week_day = best_day_this_week(self.database, anchor_date)
        focus_score = calculate_focus_score(
            totals=totals,
            switch_count=switch_count,
            longest_session_seconds=longest_session_seconds,
            recovery_seconds=recovery_seconds,
        )
        meaningful_blocks = meaningful_focus_block_count(
            focus_blocks,
            threshold_seconds=self.meaningful_focus_block_seconds,
        )
        quality_warnings = data_quality_warnings(
            rows=rows,
            totals=totals,
            previous_day=previous_day,
        )
        confidence_score = report_confidence_score(
            totals=totals,
            quality_warnings=quality_warnings,
            rows=rows,
        )
        recommendation = (
            recommendation_for_patterns(
                totals=totals,
                switch_count=switch_count,
                longest_session_seconds=longest_session_seconds,
                top_distracting_site_rows=top_distracting_site_rows,
                focus_blocks=focus_blocks,
                interruptions=interruptions,
                recovery_seconds=recovery_seconds,
                hourly_rows=hourly_rows,
                previous_day=previous_day,
                best_week_day=best_week_day,
            )
            if confidence_score >= 55
            else recommendation_for_low_confidence(confidence_score)
        )
        main_finding = main_finding_for_report(
            totals=totals,
            top_titles=title_rows,
            top_apps=app_rows,
            browser_sites=browser_sites,
            longest_session_seconds=longest_session_seconds,
            meaningful_blocks=meaningful_blocks,
        )
        report_text = build_report_text(
            label=label,
            anchor_date=anchor_date,
            totals=totals,
            focus_score=focus_score,
            confidence_score=confidence_score,
            switch_count=switch_count,
            longest_session_seconds=longest_session_seconds,
            top_apps=app_rows,
            top_titles=title_rows,
            browser_sites=browser_sites,
            focus_blocks=focus_blocks,
            meaningful_blocks=meaningful_blocks,
            interruptions=interruptions,
            recovery_seconds=recovery_seconds,
            hourly_rows=hourly_rows,
            tag_rows=tag_rows,
            timeline_notes=timeline_notes(rows, title_rows),
            previous_day=previous_day,
            week_snapshots=week_snapshots,
            best_week_day=best_week_day,
            quality_warnings=quality_warnings,
            main_finding=main_finding,
            recommendation=recommendation,
            recent_focus_block_limit=RECENT_FOCUS_BLOCK_LIMIT,
        )

        report_path = self.reports_dir / report_filename
        report_path.write_text(report_text, encoding="utf-8")

        self.database.upsert_daily_summary(
            date=date_key,
            productive_seconds=totals["productive_seconds"],
            distracting_seconds=totals["distracting_seconds"],
            neutral_seconds=totals["neutral_seconds"],
            unknown_seconds=totals["unknown_seconds"],
            summary_text=recommendation,
        )

        return report_path


__all__ = ["DailyReporter", "longest_productive_session", "DEFAULT_MEANINGFUL_FOCUS_SECONDS"]
