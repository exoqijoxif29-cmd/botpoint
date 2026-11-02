from __future__ import annotations
import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@gmail\.com$", re.IGNORECASE)


def valid_gmail(email: str) -> bool:
    if not email or not isinstance(email, str):
        return False
    return bool(EMAIL_RE.fullmatch(email.strip()))


def valid_google_play_link(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    return value.startswith("https://play.google.com/") and len(value) < 1200


def slug_user(user_id: int) -> str:
    return f"ref_{user_id}"
