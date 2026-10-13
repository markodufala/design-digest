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


BATCH = 100               # Resend's batch endpoint takes up to 100 mails per call
DAILY_LIMIT = 100         # Resend free tier: 100 mails a day (the owner's copy included)


def send_subscribers(subject: str, html: str, plain: str, subs: list[dict], unsubscribe_url, placeholder: str) -> dict:
    """Same digest to every confirmed subscriber, each with their own one-click unsubscribe."""
    key, sender = os.environ.get("RESEND_API_KEY"), os.environ.get("MAIL_FROM")
    if not key or not sender:
        # onboarding@resend.dev may only mail the account owner — subscribers need a verified domain
        return {"skipped": "needs RESEND_API_KEY and MAIL_FROM on a verified domain"}
    owner = os.environ.get("MAIL_TO", "").lower()
    subs = [s for s in subs if s["email"].lower() != owner][: DAILY_LIMIT - 1]
    sent = 0
    for i in range(0, len(subs), BATCH):
        mails = []
        for s in subs[i:i + BATCH]:
            link = unsubscribe_url(s["id"])
            mails.append({
                "from": sender, "to": [s["email"]], "subject": subject,
                "html": html.replace(placeholder, link), "text": plain.replace(placeholder, link),
                "headers": {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
            })
        r = httpx.post("https://api.resend.com/emails/batch", headers={"Authorization": f"Bearer {key}"},
                       json=mails, timeout=60)
        r.raise_for_status()
        sent += len(mails)
    return {"sent": sent}
