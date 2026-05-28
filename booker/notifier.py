"""Email notifications for booking results."""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class BookingResult:
    success: bool
    course_name: str
    target_date: str
    booked_time: str | None = None
    players: int = 0
    confirmation_number: str | None = None
    error_message: str | None = None
    available_times: list[str] | None = None


def send_notification(result: BookingResult) -> None:
    notify_email = os.environ.get("NOTIFY_EMAIL")
    smtp_from = os.environ.get("SMTP_FROM")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    if not all([notify_email, smtp_from, smtp_password]):
        log.warning(
            "Notification skipped — NOTIFY_EMAIL, SMTP_FROM, or SMTP_PASSWORD not set"
        )
        return

    subject, body = _build_message(result)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = notify_email
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_from, smtp_password)
            server.sendmail(smtp_from, [notify_email], msg.as_string())
        log.info("Notification sent to %s", notify_email)
    except Exception as exc:
        log.error("Failed to send notification: %s", exc)


def _build_message(result: BookingResult) -> tuple[str, str]:
    status = "SUCCESS" if result.success else "FAILED"
    subject = f"[Tee Time Bot] {status} — {result.course_name} on {result.target_date}"

    if result.success:
        body = (
            f"Tee time booked successfully!\n\n"
            f"Course:              {result.course_name}\n"
            f"Date:                {result.target_date}\n"
            f"Time:                {result.booked_time}\n"
            f"Players:             {result.players}\n"
            f"Confirmation #:      {result.confirmation_number or 'N/A'}\n"
        )
    else:
        body = (
            f"Tee time booking FAILED.\n\n"
            f"Course:  {result.course_name}\n"
            f"Date:    {result.target_date}\n"
            f"Error:   {result.error_message or 'Unknown error'}\n"
        )

    if result.available_times:
        body += "\nAll available times found:\n" + "\n".join(
            f"  - {t}" for t in result.available_times
        )

    return subject, body
