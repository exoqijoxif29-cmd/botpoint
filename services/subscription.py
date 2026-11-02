from __future__ import annotations
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from config import Settings
from database.db import Database


async def ensure_subscription(bot: Bot, db: Database, user_id: int):
    rows = db.list_channels()
    for row in rows:
        if row.get("active") != 1:
            continue
        try:
            member = await bot.get_chat_member(row["channel_id"], user_id)
            if member.status in {"left", "kicked"}:
                return False, rows
        except Exception:
            return None, rows
    return True, rows


async def send_join_prompt(message: Message, db: Database, rows):
    text = db.get_setting("join_text", "يرجى الاشتراك بالقنوات.")
    # Minimal prompt with channel links, no Telegram-specific requirements beyond the generic flow.
    if rows:
        if message.reply_markup is not None:
            return
        await message.answer(text)
        for r in rows:
            if r.get("invite_url"):
                await message.answer(f"{r.get('name') or 'القناة'}: {r.get('invite_url')}")
