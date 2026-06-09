# Focus Tracker Agent

This is a local Windows background agent that samples the foreground window every few seconds, classifies the activity, stores usage in SQLite, sends gentle desktop nudges when distraction thresholds are hit, and writes daily, weekly, and monthly Markdown reports.

## What it does

- Tracks the active window title and process name.
- Classifies each sample as `productive`, `neutral`, `distracting`, or `unknown`.
- Detects common browser sites more explicitly for Chrome, Edge, Firefox, and Brave by matching browser-specific site/domain rules.
- Tags samples with context labels such as `work`, `study`, `job_search`, `planning`, `communication`, and `research`.
- Stores all activity locally in SQLite.
- Sends non-invasive desktop nudges when behavior patterns suggest distraction or frequent switching.
- Generates daily, weekly, and monthly Markdown reports in `reports/`.
- Can optionally email the daily report through Gmail SMTP.

## Privacy boundaries

- No keystrokes are logged.
- No screenshots or screen recordings are taken.
- No data is uploaded anywhere unless you explicitly enable email delivery.
- All tracking stays on the local machine.

## How it works

1. `observer.py` reads the current foreground window using Windows APIs via `ctypes`.
2. `classifier.py` applies the rules from `config/rules.json`.
3. `database.py` stores activity, browser site hints, context tags, and nudges in `data/focus_tracker.db`.
4. `nudger.py` checks recent usage patterns and shows desktop notifications.
5. `reporter.py` builds daily, weekly, and monthly Markdown reports, including focus blocks and interruption metrics.
6. `main.py` runs the loop, handles shutdown, and triggers the final report.

## Setup

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Run At Startup

This app can auto-start when the current Windows user signs in. It does not run before login because it depends on the interactive desktop session to read the foreground window and show notifications.

To install or update the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_startup_task.ps1
```

What this does:

- Creates a Windows Scheduled Task named `FocusTrackerAgent`
- Triggers `At log on` for the current user
- Waits 30 seconds, then launches the tracker hidden
- Uses the repo root as the working directory so `.env`, `config\`, `data\`, and `reports\` work normally

To remove the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove_startup_task.ps1
```

You can also disable or inspect it in Windows Task Scheduler under the task name `FocusTrackerAgent`.

## Configuration

### `config/rules.json`

Edit this file to change classification behavior:

- `productive_apps` matches by exact process name.
- `neutral_apps` matches by exact process name.
- `browser_apps` defines which processes should use browser-specific site/domain matching.
- `productive_domains`, `neutral_domains`, and `distracting_domains` classify known sites more explicitly than title keywords alone.
- `domain_aliases` maps common tab labels like `GitHub` or `YouTube` to a site hint.
- `app_context_tags`, `keyword_context_tags`, and `domain_context_tags` assign one or more context labels to captured activity.
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

## Browser Sites
1. github.com - 1h 10m
2. youtube.com - 42m

## Context Tags
1. work - 2h 45m
2. coding - 2h 10m
3. research - 35m

## Longest Focus Session
47 minutes on Code.exe

## Focus Blocks
- Focus blocks completed: 6
- Interruptions: 5
- Average recovery after distraction: 8m
1. 09:10 - 09:57 on Code.exe - 47m [work, coding]

## Recommendation
Your strongest focus period was in the morning. Schedule coding or deep work before lunch.
```
