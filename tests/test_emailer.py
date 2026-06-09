import os
from pathlib import Path

from emailer import EmailSettings, ReportEmailer, build_email_body, build_email_html, render_report_pdf


def test_render_report_pdf_writes_pdf_file(tmp_path) -> None:
    report_path = tmp_path / "focus-report-2026-06-09.md"
    report_text = "\n".join(
        [
            "# Focus Report - 2026-06-09",
            "",
            "## Snapshot",
            "Total tracked time: 12m 15s",
            "| Category | Time | Share |",
            "| Productive | 2m | 16% |",
        ]
    )
    report_path.write_text(report_text, encoding="utf-8")

    pdf_path = render_report_pdf(report_path, report_text)
    pdf_bytes = pdf_path.read_bytes()

    assert pdf_path.name == "focus-report-2026-06-09.pdf"
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"/Helvetica-Bold" in pdf_bytes
    assert b"re S" in pdf_bytes


def test_send_report_attaches_pdf_instead_of_markdown(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / "focus-report-2026-06-09.md"
    report_path.write_text("# Focus Report - 2026-06-09\n\n## Snapshot\nTotal tracked time: 12m 15s\n", encoding="utf-8")

    original_username = os.environ.get("FOCUS_TRACKER_EMAIL_USERNAME")
    original_password = os.environ.get("FOCUS_TRACKER_EMAIL_PASSWORD")
    os.environ["FOCUS_TRACKER_EMAIL_USERNAME"] = "sender@example.com"
    os.environ["FOCUS_TRACKER_EMAIL_PASSWORD"] = "secret"

    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, server: str, port: int, timeout: int) -> None:
            sent["server"] = server
            sent["port"] = port
            sent["timeout"] = timeout

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def starttls(self) -> None:
            sent["starttls"] = True

        def login(self, username: str, password: str) -> None:
            sent["login"] = (username, password)

        def send_message(self, message) -> None:
            sent["message"] = message

    monkeypatch.setattr("emailer.smtplib.SMTP", FakeSMTP)

    emailer = ReportEmailer(
        EmailSettings(
            enabled=True,
            sender="agent@example.com",
            recipient="user@example.com",
        )
    )

    try:
        emailer.send_report(report_path)
    finally:
        if original_username is None:
            os.environ.pop("FOCUS_TRACKER_EMAIL_USERNAME", None)
        else:
            os.environ["FOCUS_TRACKER_EMAIL_USERNAME"] = original_username

        if original_password is None:
            os.environ.pop("FOCUS_TRACKER_EMAIL_PASSWORD", None)
        else:
            os.environ["FOCUS_TRACKER_EMAIL_PASSWORD"] = original_password

    message = sent["message"]
    attachments = list(message.iter_attachments())
    html_body = message.get_body(preferencelist=("html",))

    assert sent["login"] == ("sender@example.com", "secret")
    assert sent["starttls"] is True
    assert "Your focus report is attached as a PDF for easier reading." in message.get_body(preferencelist=("plain",)).get_content()
    assert html_body is not None
    assert "<html>" in html_body.get_content()
    assert "Focus Report - 2026-06-09" in html_body.get_content()
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "focus-report-2026-06-09.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True).startswith(b"%PDF-1.4")


def test_build_email_body_mentions_attachment() -> None:
    body = build_email_body("# Focus Report\n\n## Snapshot\nTotal tracked time: 12m 15s\n", "focus-report.pdf")

    assert "Attachment: focus-report.pdf" in body
    assert "Preview:" in body


def test_build_email_html_renders_headings_and_table() -> None:
    html_body = build_email_html(
        "\n".join(
            [
                "# Focus Report - 2026-06-09",
                "",
                "## Snapshot",
                "Total tracked time: 12m 15s",
                "| Category | Time | Share |",
                "| Productive | 2m | 16% |",
            ]
        ),
        "focus-report.pdf",
    )

    assert "Attachment: focus-report.pdf" in html_body
    assert "<h1" in html_body
    assert "<h2" in html_body
    assert "<table" in html_body
    assert "Productive" in html_body
    assert "Total tracked time" in html_body


def test_build_email_html_renders_snapshot_as_table() -> None:
    html_body = build_email_html(
        "\n".join(
            [
                "# Focus Report - 2026-06-09",
                "",
                "## Snapshot",
                "| Metric | Value |",
                "| --- | --- |",
                "| Total tracked time | 12m 15s |",
                "| Focus score | 64/100 |",
                "| Report confidence | 82/100 (High) |",
                "| Productive | 2m — 16% |",
            ]
        ),
        "focus-report.pdf",
    )

    assert "<table" in html_body
    assert "Total tracked time" in html_body
    assert "64/100" in html_body


def test_build_email_html_renders_focus_sessions_as_table_and_bullets() -> None:
    html_body = build_email_html(
        "\n".join(
            [
                "# Focus Report - 2026-06-09",
                "",
                "## Focus Sessions",
                "| Metric | Value |",
                "| --- | --- |",
                "| Focus attempts | 10 |",
                "| Meaningful focus blocks | 0 |",
                "| Longest sustained focus | 1m |",
                "| Interruptions | 2 |",
                "| Average recovery after distraction | 10m |",
                "",
                "Recent Focus Blocks:",
                "- 15:23–15:23 — Code.exe — 5s",
                "- 16:21–16:22 — Code.exe — 10s — work, coding",
            ]
        ),
        "focus-report.pdf",
    )

    assert html_body.count("<table") >= 1
    assert "Focus attempts" in html_body
    assert "&bull; 15:23" in html_body
    assert "&bull; 16:21" in html_body
