import os
import requests


def send_email_sendgrid(subject: str, body_text: str) -> None:
    """
    Sends an email using SendGrid v3 API.

    Required environment variables:
      - SENDGRID_API_KEY
      - EMAIL_TO
      - EMAIL_FROM   (must be verified in SendGrid: Single Sender or Domain Auth)
    """
    api_key = os.environ["SENDGRID_API_KEY"]
    email_to = os.environ["EMAIL_TO"]
    email_from = os.environ["EMAIL_FROM"]

    url = "https://api.sendgrid.com/v3/mail/send"
    payload = {
        "personalizations": [{"to": [{"email": email_to}]}],
        "from": {"email": email_from},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body_text}],
    }

    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    # SendGrid returns 202 Accepted for success
    if r.status_code != 202:
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")
