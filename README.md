# Focus Tracker Agent

This is a local Windows background agent that samples the foreground window every few seconds, classifies the activity, stores usage in SQLite, sends gentle desktop nudges when distraction thresholds are hit, and writes daily, weekly, and monthly Markdown reports.

## What it does

- Tracks the active window title and process name.
- Classifies each sample as `productive`, `neutral`, `distracting`, or `unknown`.
- Detects common browser sites more explicitly for Chrome, Edge, Firefox, and Brave by matching browser-specific site/domain rules.
- Tags samples with context labels such as `work`, `study`, `job_search`, `planning`, `communication`, and `research`.
- Stores all activity locally in SQLite.
- Rotates operational logs to `data/focus_tracker.log`.
- Sends non-invasive desktop nudges when behavior patterns suggest distraction or frequent switching.
- Generates daily, weekly, and monthly Markdown reports in `reports/`.
- Adds a confidence score to each report so low-quality data does not drive overconfident recommendations.
- Evaluates explicit focus goals, session states, and intervention outcomes to act more like an adaptive coach.
- Can optionally email daily, weekly, and monthly reports through Gmail SMTP.

## Privacy boundaries

- No keystrokes are logged.
- No screenshots or screen recordings are taken.
- No data is uploaded anywhere unless you explicitly enable email delivery.
- All tracking stays on the local machine.

## How it works

1. `observer.py` reads the current foreground window using Windows APIs via `ctypes`.
2. `classifier.py` applies local heuristics from `config/rules.json`, then the dynamic classifier reuses learned labels or calls the OpenAI Responses API for unseen apps when enabled.
3. `database.py` stores activity, browser site hints, context tags, and nudges in `data/focus_tracker.db`.
   It also applies schema migrations automatically and can purge old raw activity rows based on retention settings.
4. `adaptive_coach.py` evaluates session state, active goals, and prior intervention outcomes to choose gentle interventions.
5. `nudger.py` provides the notification backend used for desktop messages and legacy threshold nudges in tests.
6. `reporter.py` builds daily, weekly, and monthly Markdown reports, including focus blocks, goal progress, session-state summaries, and intervention effectiveness.
7. `main.py` runs the loop, schedules report delivery, and performs startup catch-up for missed sends.

## Setup

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Docker

This repo now includes a container image and `docker-compose.yml`.

Build and run it with:

```powershell
docker compose up --build
```

The compose setup mounts these folders so state persists across restarts:

- `config/`
- `data/`
- `reports/`

If you use Gmail delivery, keep your SMTP variables in `.env` and Compose will pass them into the container.

Important limitation:

- The tracker depends on Windows foreground-window APIs and interactive desktop notifications.
- Inside a normal Docker container, especially on Linux, it cannot observe the host's active window in a useful way.
- In that environment the app may still run, write fallback data, and generate reports, but live desktop tracking is not a realistic deployment target.

So Docker is mainly useful here for packaging, running tests, and non-interactive/report-related workflows, not for full host desktop monitoring.

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
- `distracting_apps` matches by exact process name.
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
- `raw_activity_retention_days`
- `nudge_thresholds`
- `scheduled_delivery_time`
- `email_reports`
- `classifier`

`raw_activity_retention_days` controls how long raw per-sample activity rows are kept before automatic cleanup.
`scheduled_delivery_time` controls when the app sends the previous day's report, plus any due weekly or monthly summary email.

`classifier` controls the dynamic categorisation layer:

- `enabled`
- `mode`
- `model`
- `api_base_url`
- `api_key_env`
- `api_timeout_seconds`
- `request_max_retries`
- `min_confidence_threshold`
- `reuse_provisional`
- `max_output_tokens`

Set `OPENAI_API_KEY` in your environment or `.env` if you want the tracker to classify new apps with the OpenAI API instead of falling back to provisional neutral labels.

`agent` controls the adaptive coach layer:

- `enabled`
- `default_mode`
- `policy`
- `goal_defaults`
- `intervention_cooldowns`
- `outcome_window_minutes`

### Classification Review Workflow

Review recent low-confidence or provisional learned labels:

```powershell
python classification_admin.py list-review
```

Save a correction that always overrides future automatic classification:

```powershell
python classification_admin.py override --scope app --key spotify.exe --category distracting --tags music
```

### Adaptive Coach Workflow

List active goals:

```powershell
python agent_admin.py list-goals
```

Add a goal:

```powershell
python agent_admin.py add-goal --type daily_productive_minutes --name "Daily productive minutes" --target 180
```

Disable a goal:

```powershell
python agent_admin.py disable-goal --id 1
```

Review intervention effectiveness:

```powershell
python agent_admin.py list-intervention-stats
```

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

Use a Gmail app password, not your normal Google account password. Once enabled, the agent still writes the local Markdown file and emails the due daily, weekly, and monthly reports with a rendered PDF attached by default.

## Sample report

```markdown
# Focus Report - 2026-06-09

## Snapshot
- Total tracked time: 6h 20m
- Focus score: 78/100
- Report confidence: 92/100 (High)
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

## Operational files

- `data/focus_tracker.db`: raw activity, nudges, and daily summaries
- `data/focus_tracker.log`: rotating runtime log for startup, report delivery, cleanup, and warning events
- `data/emailed_report_receipts.json`: local delivery receipts used to avoid resending the same daily, weekly, or monthly period
- `reports/*.pdf`: generated when a report is rendered for email attachment
