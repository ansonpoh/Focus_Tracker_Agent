from __future__ import annotations

from datetime import date
from typing import Any

from reporting_analysis import (
    BrowserSiteSummary,
    FocusBlock,
    InterventionSummary,
    SessionStateSummary,
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
    goal_rows: list[dict[str, Any]],
    session_state_rows: list[SessionStateSummary],
    intervention_rows: list[InterventionSummary],
    quality_warnings: list[str],
    main_finding: str,
    recommendation: str,
    recent_focus_block_limit: int,
) -> str:
    report_lines = [
        f"# {label}",
        "",
        "## Snapshot",
    ]
    snapshot_rows = [
        ["Metric", "Value"],
        ["Total tracked time", format_duration(totals["total_seconds"])],
        ["Focus score", f"{focus_score}/100"],
        ["Report confidence", f"{confidence_score}/100 ({confidence_label(confidence_score)})"],
        ["Productive", format_duration_share(totals["productive_seconds"], totals["total_seconds"])],
        ["Distracting", format_duration_share(totals["distracting_seconds"], totals["total_seconds"])],
        ["Neutral", format_duration_share(totals["neutral_seconds"], totals["total_seconds"])],
        ["Unknown", format_duration_share(totals["unknown_seconds"], totals["total_seconds"])],
        ["App/window switches", str(switch_count)],
        [
            "Longest focus session",
            format_duration(longest_session_seconds) if longest_session_seconds > 0 else "0s",
        ],
    ]
    report_lines.extend(
        markdown_table(snapshot_rows[0], snapshot_rows[1:])
        + [
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
    )

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
        ]
    )
    focus_session_rows = [
        ["Metric", "Value"],
        ["Focus attempts", str(len(focus_blocks))],
        ["Meaningful focus blocks", str(meaningful_blocks)],
        [
            "Longest sustained focus",
            format_duration(longest_session_seconds) if longest_session_seconds > 0 else "0s",
        ],
        ["Interruptions", str(interruptions)],
    ]
    if recovery_seconds:
        average_recovery = sum(recovery_seconds) // len(recovery_seconds)
        focus_session_rows.append(["Average recovery after distraction", format_duration(average_recovery)])
    else:
        focus_session_rows.append(["Average recovery after distraction", "n/a"])
    report_lines.extend(markdown_table(focus_session_rows[0], focus_session_rows[1:]))

    if focus_blocks:
        report_lines.extend(["", "Recent Focus Blocks:"])
        for block in sorted(focus_blocks, key=lambda item: item.start)[-recent_focus_block_limit:]:
            tag_text = f" \u2014 {', '.join(block.context_tags)}" if block.context_tags else ""
            report_lines.append(
                f"- {block.start.strftime('%H:%M')}\u2013{block.end.strftime('%H:%M')} \u2014 "
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

    report_lines.extend(["", "## Goal Progress"])
    if goal_rows:
        report_lines.extend(
            markdown_table(
                ["Goal", "Status", "Progress", "Notes"],
                [
                    [
                        str(row["name"]),
                        str(row["status"]),
                        f"{int(row['progress_value'])}/{int(row['target_value'])}",
                        str(row["detail"]),
                    ]
                    for row in goal_rows
                ],
            )
        )
    else:
        report_lines.append("No goal evaluations were recorded for this period.")

    report_lines.extend(["", "## Session States"])
    if session_state_rows:
        report_lines.extend(
            markdown_table(
                ["State", "Samples"],
                [[row.state, str(row.count)] for row in session_state_rows],
            )
        )
    else:
        report_lines.append("No session-state snapshots were recorded for this period.")

    report_lines.extend(["", "## Interventions"])
    if intervention_rows:
        report_lines.extend(
            markdown_table(
                ["Action", "Count", "Success", "Partial", "Failure"],
                [
                    [row.action, str(row.count), str(row.success), str(row.partial), str(row.failure)]
                    for row in intervention_rows
                ],
            )
        )
    else:
        report_lines.append("No adaptive-coach interventions were recorded for this period.")

    report_lines.extend(["", "## Data Quality"])
    if quality_warnings:
        report_lines.extend(f"- {warning}" for warning in quality_warnings)
    else:
        report_lines.append("No major data quality warnings detected.")

    report_lines.extend(["", "## Recommendation", recommendation])
    return "\n".join(report_lines).strip() + "\n"
