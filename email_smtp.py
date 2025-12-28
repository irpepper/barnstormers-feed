import os
import smtplib
from email.message import EmailMessage


def send_email_gmail_smtp(subject: str, body_text: str, body_html: str | None = None) -> None:
    """
    Send an email via Gmail SMTP.

    Required env vars:
      - SMTP_USER: full email (e.g. you@gmail.com)
      - SMTP_PASS: Google App Password (16 chars), NOT your normal password
      - EMAIL_TO: destination email

    Optional:
      - EMAIL_FROM (defaults to SMTP_USER)
      - SMTP_MODE: "starttls" (default) or "ssl"
    """
    smtp_user = os.environ["SMTP_USER"].strip()
    smtp_pass = os.environ["SMTP_PASS"].replace(" ", "").strip()
    email_to = os.environ["EMAIL_TO"].strip()
    email_from = os.getenv("EMAIL_FROM", smtp_user).strip()
    mode = os.getenv("SMTP_MODE", "starttls").strip().lower()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    # Plain-text fallback (always)
    msg.set_content(body_text)

    # HTML version (optional)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        if mode == "ssl":
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "Gmail SMTP auth failed (535). Most common causes:\n"
            " - SMTP_PASS is NOT a Google App Password (it must be a 16-char app password)\n"
            " - 2-Step Verification is not enabled on the sending account\n"
            " - Wrong SMTP_USER/SMTP_PASS pair (wrong account)\n"
            " - Google Workspace policy blocks app passwords / SMTP\n"
            f"\nRaw error: {e}"
        ) from e
