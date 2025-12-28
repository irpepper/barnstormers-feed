import os
import smtplib
from email.message import EmailMessage


def send_email_gmail_smtp(subject: str, body_text: str) -> None:
    """
    Send an email via Gmail SMTP.

    Required environment variables:
      - SMTP_USER   (your full gmail address, e.g. you@gmail.com)
      - SMTP_PASS   (Gmail App Password, NOT your normal password)
      - EMAIL_TO    (destination email; can be same as SMTP_USER)
    Optional:
      - EMAIL_FROM  (defaults to SMTP_USER)
    """
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    email_to = os.environ["EMAIL_TO"]
    email_from = os.getenv("EMAIL_FROM", smtp_user)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(body_text)

    # Gmail SMTP (STARTTLS)
    host = "smtp.gmail.com"
    port = 587

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
