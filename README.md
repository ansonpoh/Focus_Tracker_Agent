# Focus Tracker Agent

This is a local Windows background agent that samples the foreground window every few seconds, classifies the activity, stores usage in SQLite, sends gentle desktop nudges when distraction thresholds are hit, and writes a daily Markdown report.

## What it does

- Tracks the active window title and process name.
- Classifies each sample as `productive`, `neutral`, `distracting`, or `unknown`.
- Stores all activity locally in SQLite.
- Sends non-invasive desktop nudges when behavior patterns suggest distraction or frequent switching.
- Generates a daily Markdown report in `reports/`.
- Can optionally email the daily report through Gmail SMTP.

## Privacy boundaries

- No keystrokes are logged.
- No screenshots or screen recordings are taken.
- No data is uploaded anywhere unless you explicitly enable email delivery.
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
- `email_reports`

### Gmail delivery

To email reports to a Gmail inbox, enable `email_reports` in `config/settings.json`:

```json
{
  "email_reports": {
    "enabled": true,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender": "your-gmail-address@gmail.com",
    "recipient": "your-gmail-address@gmail.com",
    "username_env": "FOCUS_TRACKER_EMAIL_USERNAME",
    "password_env": "FOCUS_TRACKER_EMAIL_PASSWORD",
    "use_tls": true,
    "attach_report_file": true
  }
}
```

Then either set these environment variables before starting the app:

```powershell
$env:FOCUS_TRACKER_EMAIL_USERNAME="your-gmail-address@gmail.com"
$env:FOCUS_TRACKER_EMAIL_PASSWORD="your-gmail-app-password"
python main.py
```

Or create a `.env` file in the project root:

```dotenv
FOCUS_TRACKER_EMAIL_USERNAME=your-gmail-address@gmail.com
FOCUS_TRACKER_EMAIL_PASSWORD=your-gmail-app-password
```

Use a Gmail app password, not your normal Google account password. Once enabled, the agent still writes the local Markdown file and also emails the report body, with the `.md` file attached by default.

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
