from __future__ import annotations

import html as html_lib
import re
import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _plain(html: str) -> str:
    text = re.sub(r"<br\s*/?>|</li>|</p>|</h\d>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def send_email(subject: str, html: str, user: str, password: str, to: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to or user
    msg.set_content(_plain(html))
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.login(user, password)
        s.send_message(msg)
