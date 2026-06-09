# Focus Tracker Agent

This is a local Windows background agent that samples the foreground window every few seconds, classifies the activity, stores usage in SQLite, sends gentle desktop nudges when distraction thresholds are hit, and writes a daily Markdown report.

## What it does

- Tracks the active window title and process name.
- Classifies each sample as `productive`, `neutral`, `distracting`, or `unknown`.
- Stores all activity locally in SQLite.
- Sends non-invasive desktop nudges when behavior patterns suggest distraction or frequent switching.
- Generates a daily Markdown report in `reports/`.

## Privacy boundaries

- No keystrokes are logged.
- No screenshots or screen recordings are taken.
- No data is uploaded anywhere.
- All tracking stays on the local machine.

## How it works

1. `observer.py` reads the current foreground window using Windows APIs via `ctypes`.
2. `classifier.py` applies the rules from `config/rules.json`.
3. `database.py` stores activity and nudges in `data/focus_tracker.db`.
4. `nudger.py` checks recent usage patterns and shows desktop notifications.
5. `reporter.py` builds a Markdown report for the current day.
6. `main.py` runs the loop, handles shutdown, and triggers the final report.

## Setup

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Configuration

### `config/rules.json`

Edit this file to change classification behavior:

- `productive_apps` matches by exact process name.
- `neutral_apps` matches by exact process name.
- `productive_keywords` and `distracting_keywords` are matched inside the window title.

Matching is case-insensitive.

### `config/settings.json`

This file controls runtime behavior:

- `tracking_interval_seconds`
- `nudge_cooldown_minutes`
- `nudge_thresholds`
- `daily_report_time`

## Sample report

```markdown
# Focus Report - 2026-06-09

## Summary
- Total tracked time: 6h 20m
- Productive: 3h 10m
- Distracting: 1h 05m
- Neutral: 1h 30m
- Unknown: 35m
- App/window switches: 18

## Top Apps
1. Code.exe - 2h 15m
2. chrome.exe - 1h 40m

## Distractions
1. YouTube - Google Chrome - 42m

## Longest Focus Session
47 minutes on Code.exe

## Recommendation
Your strongest focus period was in the morning. Schedule coding or deep work before lunch.
```

## Future improvements

- Per-app allow/block lists.
- More precise session grouping across brief interruptions.
- Optional tray icon and pause/resume controls.
- Weekly report aggregation.
