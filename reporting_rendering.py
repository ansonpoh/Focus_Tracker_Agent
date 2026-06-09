from __future__ import annotations

from datetime import date
from typing import Any

from reporting_analysis import (
    BrowserSiteSummary,
    FocusBlock,
    category_distribution,
    confidence_label,
    delta_label,
    format_duration,
    format_duration_share,
    percentage_of_total,
    pretty_site_label,
)


def markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def build_report_text(
    *,
    label: str,
    anchor_date: date,
    totals: dict[str, int],
    focus_score: int,
    confidence_score: int,
    switch_count: int,
    longest_session_seconds: int,
    top_apps: list[dict[str, Any]],
    top_titles: list[dict[str, Any]],
    browser_sites: list[BrowserSiteSummary],
    focus_blocks: list[FocusBlock],
    meaningful_blocks: int,
    interruptions: int,
    recovery_seconds: list[int],
    hourly_rows: list[tuple[int, int, int, int, int]],
    tag_rows: list[tuple[str, int]],
    timeline_notes: dict[int, str],
    previous_day: Any,
    week_snapshots: list[Any],
    best_week_day: Any,
    quality_warnings: list[str],
    main_finding: str,
    recommendation: str,
    recent_focus_block_limit: int,
) -> str:
    report_lines = [
        f"# {label}",
        "",
        "## Snapshot",
        f"Total tracked time: {format_duration(totals['total_seconds'])}",
        f"Focus score: {focus_score}/100",
        f"Report confidence: {confidence_score}/100 ({confidence_label(confidence_score)})",
        f"Productive: {format_duration_share(totals['productive_seconds'], totals['total_seconds'])}",
        f"Distracting: {format_duration_share(totals['distracting_seconds'], totals['total_seconds'])}",
        f"Neutral: {format_duration_share(totals['neutral_seconds'], totals['total_seconds'])}",
        f"Unknown: {format_duration_share(totals['unknown_seconds'], totals['total_seconds'])}",
        f"App/window switches: {switch_count}",
        f"Longest focus session: {format_duration(longest_session_seconds)}" if longest_session_seconds > 0 else "Longest focus session: 0s",
        "",
        "## Main Finding",
        main_finding,
        "",
        "## Time Breakdown",
        *markdown_table(
            ["Category", "Time", "Share"],
            [
                [name, format_duration(value), f"{percentage_of_total(value, totals['total_seconds'])}%"]
                for name, value in category_distribution(totals)
            ],
        ),
        "",
        "## Top Apps",
    ]

    if top_apps:
        report_lines.extend(
            markdown_table(
                ["App", "Time", "Classification"],
                [
                    [row["app_name"], format_duration(int(row["total_seconds"] or 0)), row["classification"]]
                    for row in top_apps
                ],
            )
        )
    else:
        report_lines.append("No activity recorded.")

    report_lines.extend(["", "## Top Distraction"])
    if top_titles:
        top_title = top_titles[0]
        report_lines.extend(
            markdown_table(
                ["Window", "Time"],
                [[top_title["title"], format_duration(int(top_title["total_seconds"] or 0))]],
            )
        )
    else:
        report_lines.append("No distracting sessions recorded.")

    report_lines.extend(["", "## Browser Sites"])
    if browser_sites:
        for site in browser_sites:
            if site.detected_from_title:
                report_lines.append(
                    f"- {pretty_site_label(site.label)} \u2014 {format_duration(site.total_seconds)}, detected from window title because browser URL/domain data was unavailable."
                )
            else:
                report_lines.append(f"- {site.label} \u2014 {format_duration(site.total_seconds)}")
    else:
        report_lines.append("No browser activity with a recognizable site was detected.")

    report_lines.extend(
        [
            "",
            "## Focus Sessions",
            f"Focus attempts: {len(focus_blocks)}",
            f"Meaningful focus blocks: {meaningful_blocks}",
            f"Longest sustained focus: {format_duration(longest_session_seconds)}" if longest_session_seconds > 0 else "Longest sustained focus: 0s",
            f"Interruptions: {interruptions}",
        ]
    )
    if recovery_seconds:
        average_recovery = sum(recovery_seconds) // len(recovery_seconds)
        report_lines.append(f"Average recovery after distraction: {format_duration(average_recovery)}")
    else:
        report_lines.append("Average recovery after distraction: n/a")

    if focus_blocks:
        report_lines.extend(["", "Recent Focus Blocks:"])
        for block in sorted(focus_blocks, key=lambda item: item.start)[-recent_focus_block_limit:]:
            tag_text = f" \u2014 {', '.join(block.context_tags)}" if block.context_tags else ""
            report_lines.append(
                f"{block.start.strftime('%H:%M')}\u2013{block.end.strftime('%H:%M')} \u2014 "
                f"{block.primary_app} \u2014 {format_duration(block.duration_seconds)}{tag_text}"
            )
    else:
        report_lines.append("No focus attempts detected.")

    report_lines.extend(["", "## Timeline"])
    if hourly_rows:
        report_lines.extend(
            markdown_table(
                ["Hour", "Productive", "Distracting", "Notes"],
                [
                    [
                        f"{hour:02d}:00",
                        format_duration(productive),
                        format_duration(distracting),
                        timeline_notes.get(hour, ""),
                    ]
                    for hour, productive, distracting, _, _ in hourly_rows
                ],
            )
        )
    else:
        report_lines.append("No hourly data recorded.")

    report_lines.extend(["", "## Context Tags"])
    if tag_rows:
        report_lines.extend(
            markdown_table(
                ["Tag", "Time", "Share"],
                [
                    [tag, format_duration(total_seconds), f"{percentage_of_total(total_seconds, totals['total_seconds'])}%"]
                    for tag, total_seconds in tag_rows
                ],
            )
        )
    else:
        report_lines.append("No context tags recorded.")

    report_lines.extend(["", "## Comparisons"])
    if previous_day is not None:
        report_lines.append(f"Yesterday productive time: {delta_label(totals['productive_seconds'], previous_day.totals['productive_seconds'])}")
        report_lines.append(f"Yesterday distracting time: {delta_label(totals['distracting_seconds'], previous_day.totals['distracting_seconds'])}")
        report_lines.append(f"Yesterday focus attempts: {len(focus_blocks) - previous_day.focus_block_count:+d}")
        report_lines.append(f"Yesterday app/window switches: {switch_count - previous_day.switch_count:+d}")
    else:
        report_lines.append("No previous day data is available.")

    if len(week_snapshots) >= 2 and best_week_day is not None:
        if best_week_day.date_value == anchor_date:
            report_lines.append("Today is currently the strongest comparable day this week.")
        else:
            gap = max(0, best_week_day.totals["productive_seconds"] - totals["productive_seconds"])
            report_lines.append(
                f"Best comparable day this week: {best_week_day.date_value.isoformat()} "
                f"with {format_duration(best_week_day.totals['productive_seconds'])} productive time."
            )
            report_lines.append(f"Gap to that day: {format_duration(gap)}")
    else:
        report_lines.append("No meaningful weekly comparison yet. More than one day of data is needed.")

    report_lines.extend(["", "## Data Quality"])
    if quality_warnings:
        report_lines.extend(f"- {warning}" for warning in quality_warnings)
    else:
        report_lines.append("No major data quality warnings detected.")

    report_lines.extend(["", "## Recommendation", recommendation])
    return "\n".join(report_lines).strip() + "\n"
