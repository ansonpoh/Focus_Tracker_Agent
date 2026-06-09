from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
import os
import smtplib


@dataclass
class EmailSettings:
    enabled: bool = False
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender: str = ""
    recipient: str = ""
    username_env: str = "FOCUS_TRACKER_EMAIL_USERNAME"
    password_env: str = "FOCUS_TRACKER_EMAIL_PASSWORD"
    use_tls: bool = True
    attach_report_file: bool = True


class ReportEmailer:
    def __init__(self, settings: EmailSettings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return self.settings.enabled and bool(self.settings.sender and self.settings.recipient)

    def send_report(self, report_path: Path) -> None:
        if not self.is_configured():
            return

        username = os.getenv(self.settings.username_env, "").strip()
        password = os.getenv(self.settings.password_env, "").strip()
        if not username or not password:
            raise RuntimeError(
                "Email delivery is enabled but SMTP credentials are missing from the configured environment variables."
            )

        report_text = report_path.read_text(encoding="utf-8")
        subject = f"Focus report - {report_path.stem.replace('focus-report-', '')}"

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message.set_content(report_text)

        if self.settings.attach_report_file:
            message.add_attachment(
                report_text.encode("utf-8"),
                maintype="text",
                subtype="markdown",
                filename=report_path.name,
            )

        with smtplib.SMTP(self.settings.smtp_server, self.settings.smtp_port, timeout=30) as smtp:
            if self.settings.use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
