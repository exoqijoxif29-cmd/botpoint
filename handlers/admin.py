from __future__ import annotations
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import Settings
from database.db import Database
from handlers.user import user_start, user_support, user_suggestion, user_order_start, user_server_callback

router = Router()


@router.message(Command("start"))
async def start(message: Message, db: Database, settings: Settings):
    await user_start(message, db, settings)


@router.message(Command("admin"))
async def admin_only(message: Message, db: Database, settings: Settings):
    if message.from_user.id != settings.owner_id:
        await message.answer("ليس لديك صلاحية الوصول إلى لوحة الإدارة.")
        return
    await message.answer("لوحة الإدارة متاحة فقط للبوت الخاص بالمالك.")


@router.message(lambda m: m.text == "🎧 الدعم")
async def support(message: Message, db: Database):
    await user_support(message, db)


@router.message(lambda m: m.text == "💡 الاقتراحات والإضافات")
async def suggestion(message: Message, db: Database):
    await user_suggestion(message, db)


@router.message(lambda m: m.text == "📦 طلباتي")
async def orders(message: Message, db: Database):
    await user_order_start(message, db)


@router.message(lambda m: m.text == "💎 تفعيل الجائزة الأسبوعية")
async def prize_start(message: Message, db: Database):
    await user_order_start(message, db)
