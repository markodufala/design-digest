"""Send the digest. Uses Resend if RESEND_API_KEY is set, otherwise SMTP (e.g. Gmail)."""
import os
import smtplib
from email.message import EmailMessage

import httpx


def send(subject: str, html: str, plain: str) -> str:
    to = os.environ["MAIL_TO"]
    if key := os.environ.get("RESEND_API_KEY"):
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "from": os.environ.get("MAIL_FROM") or "Design Digest <onboarding@resend.dev>",
                "to": [to], "subject": subject, "html": html, "text": plain,
            },
            timeout=30,
        )
        r.raise_for_status()
        return f"resend → {to}"

    user = os.environ.get("SMTP_USER")
    if not user or not os.environ.get("SMTP_PASS"):
        raise SystemExit("No mail credentials: set RESEND_API_KEY, or SMTP_USER + SMTP_PASS.")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("MAIL_FROM") or f"Design Digest <{user}>", to
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT") or 465)) as s:
        s.login(user, os.environ["SMTP_PASS"])
        s.send_message(msg)
    return f"smtp {host} → {to}"
