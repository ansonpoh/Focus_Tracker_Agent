from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
import os
import smtplib

from email_rendering import build_email_body, build_email_html, render_report_pdf


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
        pdf_path = render_report_pdf(report_path, report_text)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message.set_content(build_email_body(report_text, pdf_path.name))
        message.add_alternative(build_email_html(report_text, pdf_path.name), subtype="html")

        if self.settings.attach_report_file:
            message.add_attachment(
                pdf_path.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=pdf_path.name,
            )

        with smtplib.SMTP(self.settings.smtp_server, self.settings.smtp_port, timeout=30) as smtp:
            if self.settings.use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)


__all__ = ["EmailSettings", "ReportEmailer", "build_email_body", "build_email_html", "render_report_pdf"]
