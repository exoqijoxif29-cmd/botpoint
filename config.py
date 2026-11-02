from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    user_bot_token: str
    admin_bot_token: str
    owner_id: int
    database_path: str
    proof_channel_id: str
    timezone: str
    backup_interval_hours: int


def load_settings() -> Settings:
    load_dotenv()
    required = ["USER_BOT_TOKEN", "ADMIN_BOT_TOKEN", "OWNER_ID"]
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise RuntimeError(f"متغيرات البيئة المفقودة: {', '.join(missing)}")
    try:
        owner_id = int(os.environ["OWNER_ID"])
        backup_interval = int(os.getenv("BACKUP_INTERVAL_HOURS", "6"))
        if owner_id <= 0 or backup_interval <= 0:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("تحقق من OWNER_ID و BACKUP_INTERVAL_HOURS.") from exc

    database_path = os.getenv("DATABASE_PATH", "./data/play_point.db")
    if not database_path:
        raise RuntimeError("DATABASE_PATH مطلوب.")
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        user_bot_token=os.environ["USER_BOT_TOKEN"],
        admin_bot_token=os.environ["ADMIN_BOT_TOKEN"],
        owner_id=owner_id,
        database_path=database_path,
        proof_channel_id=os.getenv("PROOF_CHANNEL_ID", "@playpo4"),
        timezone=os.getenv("TIMEZONE", "Africa/Algiers"),
        backup_interval_hours=backup_interval,
    )
