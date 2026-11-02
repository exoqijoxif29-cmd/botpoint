from __future__ import annotations
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from config import load_settings
from database.db import Database
from handlers.user import router as user_router
from handlers.admin import router as admin_router


async def start_user_bot(settings):
    db = Database(settings.database_path)
    db.initialize()
    bot = Bot(token=settings.user_bot_token)
    dp = Dispatcher()
    dp.include_router(user_router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


async def start_admin_bot(settings):
    db = Database(settings.database_path)
    db.initialize()
    bot = Bot(token=settings.admin_bot_token)
    dp = Dispatcher()
    dp.include_router(admin_router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


async def main():
    settings = load_settings()
    await asyncio.gather(start_user_bot(settings), start_admin_bot(settings))


if __name__ == "__main__":
    asyncio.run(main())
