from __future__ import annotations
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from config import Settings
from database.db import Database
from services.validation import valid_gmail, valid_google_play_link
from services.subscription import ensure_subscription


def build_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💎 تفعيل الجائزة الأسبوعية"), KeyboardButton(text="📦 طلباتي")],
            [KeyboardButton(text="🎧 الدعم"), KeyboardButton(text="💡 الاقتراحات والإضافات")],
            [KeyboardButton(text="📊 إحصائياتي"), KeyboardButton(text="👥 دعواتي")],
            [KeyboardButton(text="📖 كيف يعمل؟ / الأسئلة الشائعة")],
        ],
        resize_keyboard=True,
    )


async def user_start(message: Message, db: Database, settings: Settings):
    db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    if message.get_args():
        arg = message.get_args().split()[0]
        if arg.startswith("ref_"):
            try:
                inviter_id = int(arg.split("_", 1)[1])
                db.process_referral(inviter_id, message.from_user.id)
            except ValueError:
                pass
    ok, rows = await ensure_subscription(message.bot, db, message.from_user.id)
    if ok is not True:
        await message.answer(db.get_setting("join_text", "يرجى الاشتراك."))
        return
    await message.answer("أهلاً بك في PLAY POINT 💎", reply_markup=build_main_menu())


async def user_support(message: Message, db: Database):
    body = message.text or ""
    if body.startswith("/"):
        return
    ticket_id = db.create_support_ticket(message.from_user.id, body)
    await message.answer(f"تم إرسال رسالتك بنجاح. رقم التذكرة: #{ticket_id}")


async def user_suggestion(message: Message, db: Database):
    body = message.text or ""
    if body.startswith("/"):
        return
    db.add_suggestion(message.from_user.id, body)
    await message.answer("تم حفظ اقتراحك بنجاح.")


async def user_order_start(message: Message, db: Database):
    ok, rows = await ensure_subscription(message.bot, db, message.from_user.id)
    if ok is not True:
        await message.answer(db.get_setting("join_text", "يرجى الاشتراك."))
        return
    if db.has_active_order(message.from_user.id):
        await message.answer("لديك طلب معلّق بالفعل، لا يمكنك إنشاء طلب جديد")
        return
    servers = db.list_servers()
    if not servers:
        await message.answer("لا توجد سيرفرات متاحة الآن.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{s['flag']} {s['name']} — {s['price']} نقطة", callback_data=f"server:{s['id']}")] for s in servers])
    await message.answer("اختر السيرفر المناسب لك:", reply_markup=kb)


async def user_server_callback(callback: CallbackQuery, db: Database):
    if not callback.data or not callback.data.startswith("server:"):
        await callback.answer("إجراء غير صالح.")
        return
    server_id = int(callback.data.split(":", 1)[1])
    server = next((s for s in db.list_servers() if s["id"] == server_id), None)
    if not server:
        await callback.answer("السيرفر غير موجود")
        return
    db.save_draft(callback.from_user.id, server_id, "app1")
    await callback.message.answer("⚠️ ملاحظة هامة:\nابحث عن هذين التطبيقين في قوقل بلاي وأرسل رابط دفع لكل منهما:\n1️⃣ Upping Floors Stack Blocks\n2️⃣ Neon Valley AMOLED\n\n📥 أرسل الآن رابط دفع التطبيق الأول:")
    await callback.answer()


async def handle_order_app1(message: Message, db: Database):
    value = (message.text or "").strip()
    if not valid_google_play_link(value):
        await message.answer("رابط التطبيق غير صالح. أعد إرسال رابط Google Play صحيح.")
        return
    user_id = message.from_user.id
    draft = db.get_draft(user_id)
    if not draft:
        await message.answer("ابدأ العملية من جديد.")
        return
    db.save_draft(user_id, draft["server_id"], "app2", app1=value)
    await message.answer("📥 ممتاز. الآن أرسل رابط دفع التطبيق الثاني (Neon Valley AMOLED):")


async def handle_order_app2(message: Message, db: Database):
    value = (message.text or "").strip()
    if not valid_google_play_link(value):
        await message.answer("رابط التطبيق الثاني غير صالح. أعد إرساله من جديد.")
        return
    user_id = message.from_user.id
    draft = db.get_draft(user_id)
    if not draft:
        await message.answer("ابدأ العملية من جديد.")
        return
    db.save_draft(user_id, draft["server_id"], "email", draft["app1"], value)
    await message.answer("📧 رائع. الآن أرسل البريد الإلكتروني (Gmail فقط بدون كلمة سر):")


async def handle_order_email(message: Message, db: Database):
    value = (message.text or "").strip()
    if not valid_gmail(value):
        await message.answer("البريد غير صالح. استخدم Gmail فقط بدون كلمة مرور.")
        return
    user_id = message.from_user.id
    draft = db.get_draft(user_id)
    if not draft:
        await message.answer("ابدأ العملية من جديد.")
        return
    week_start = "2026-01-01"
    try:
        db.record_email_usage(value, week_start, 0)
    except ValueError:
        await message.answer("هذا البريد مستخدم بالفعل خلال نفس الأسبوع.")
        return
    server = next((s for s in db.list_servers() if s["id"] == draft["server_id"]), None)
    if not server:
        await message.answer("السيرفر غير موجود.")
        return
    user = db.get_user(user_id)
    if not user or int(user["balance"]) < int(server["price"]):
        await message.answer("رصيدك غير كافٍ لإنهاء الطلب.")
        return
    order_id = db.create_order_atomic(user_id, draft["server_id"], draft["app1"], draft["app2"], value, week_start)
    await message.answer(
        "✅ تم استلام طلبك!\n━━━━━━━━━━━━━━\n🔢 رقم الطلب: #" + str(order_id) + "\n\n⏳ الحالة: قيد المراجعة\n🔔 سيصلك إشعار هنا فور تحديث حالة طلبك."
    )
