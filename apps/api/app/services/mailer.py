"""Outgoing e-mail (invitations). SMTP only, so any provider works (Azure Communication Services, SendGrid, Microsoft 365).

With no SMTP host configured nothing is sent and `send` returns False: the caller then shows the administrator the
link to share by hand. Nothing here ever logs message bodies (they contain one-time links).
"""
import smtplib
from email.message import EmailMessage

from ..config import get_settings


def enabled() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def send(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not enabled():
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as c:
            if s.smtp_starttls:
                c.starttls()
            if s.smtp_username:
                c.login(s.smtp_username, s.smtp_password)
            c.send_message(msg)
        return True
    except Exception:
        return False
