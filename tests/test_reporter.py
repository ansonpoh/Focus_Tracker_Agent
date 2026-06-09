from reporter import longest_productive_session


def test_longest_productive_session_breaks_on_large_gap() -> None:
    rows = [
        {"timestamp": "2026-06-09T09:00:00", "app_name": "Code.exe", "category": "productive", "duration_seconds": 5},
        {"timestamp": "2026-06-09T09:00:05", "app_name": "Code.exe", "category": "productive", "duration_seconds": 5},
        {"timestamp": "2026-06-09T09:00:30", "app_name": "Chrome.exe", "category": "productive", "duration_seconds": 5},
    ]

    duration, label = longest_productive_session(rows)

    assert duration == 10
    assert label == "Code.exe"
