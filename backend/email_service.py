"""
Email service for sending verification codes via Mailgun.

Configuration via environment variables:
- MAILGUN_API_KEY: Mailgun API key
- MAILGUN_DOMAIN: Mailgun domain (e.g., mg.example.com)
- MAILGUN_FROM_EMAIL: Sender email address (optional, defaults to noreply@<domain>)
"""

import os
import requests
from typing import Optional

MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
MAILGUN_FROM_EMAIL = os.getenv("MAILGUN_FROM_EMAIL")

# Default sender email if not specified
if not MAILGUN_FROM_EMAIL and MAILGUN_DOMAIN:
    MAILGUN_FROM_EMAIL = f"noreply@{MAILGUN_DOMAIN}"


def send_verification_code(email: str, code: str) -> bool:
    """
    Send verification code via Mailgun.

    Args:
        email: Recipient email address
        code: 6-digit verification code

    Returns:
        True if email sent successfully, False otherwise
    """
    # If Mailgun not configured, log to console
    if not MAILGUN_API_KEY or not MAILGUN_DOMAIN:
        print(f"[Email] Mailgun not configured. Verification code for {email}: {code}")
        return False

    try:
        response = requests.post(
            f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
            auth=("api", MAILGUN_API_KEY),
            data={
                "from": MAILGUN_FROM_EMAIL,
                "to": email,
                "subject": "Your DeFi Wallet Analyzer verification code",
                "text": (
                    f"Your verification code is: {code}\n\n"
                    f"This code will expire in 10 minutes.\n\n"
                    f"If you didn't request this code, please ignore this email."
                )
            },
            timeout=10
        )

        if response.status_code == 200:
            print(f"[Email] Sent verification code to {email}")
            return True
        else:
            print(f"[Email] Failed to send to {email}: {response.status_code} {response.text}")
            return False

    except Exception as e:
        print(f"[Email] Error sending verification code to {email}: {e}")
        return False
