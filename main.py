from __future__ import annotations
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import Settings
from database.db import Database

router = Router()


@router.message(Command("admin"))
async def admin_panel(message: Message, db: Database, settings: Settings):
    if message.from_user.id != settings.owner_id:
        await message.answer("ممنوع: هذا الأمر للمالك فقط.")
        return
    await message.answer("لوحة الإدارة جاهزة — إدارتك ستُضاف هنا لاحقاً.")


@router.message(Command("stats"))
async def stats(message: Message, db: Database, settings: Settings):
    if message.from_user.id != settings.owner_id:
        await message.answer("غير مصرح.")
        return
    users = db.list_users()
    total_users = len(users)
    await message.answer(f"إجمالي المستخدمين: {total_users}")
