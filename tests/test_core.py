# PLAY POINT

مشروع تجريبي/إطار عملي لبوتات Telegram باستخدام Python + aiogram + SQLite.

## المتطلبات

- Python 3.11+
- pip
- مجلد `data/` للمشروع

## التثبيت

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# أو
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## ملف البيئة

أنشئ ملف `.env` من `.env.example` وأملأ القيم الفعلية:

```env
USER_BOT_TOKEN=
ADMIN_BOT_TOKEN=
OWNER_ID=
DATABASE_PATH=./data/play_point.db
PROOF_CHANNEL_ID=@playpo4
TIMEZONE=Africa/Algiers
BACKUP_INTERVAL_HOURS=6
```

## التشغيل

```bash
python main.py
```

## الاختبارات

```bash
pytest -q
```

## ملاحظات

- تم بناء هيكل المشروع مع نماذج أساسية لقاعدة البيانات وبنية البوتين.
- لا تضع أي توكن حقيقي في الملفات أو في GitHub.
- قبل النشر، اجعل المشروع يعمل محلياً ويُختبر.
- هذا الإطار يغطّي البنية الأساسية ويستهدف التشغيل المحلي والاختبار السليم.

## النشر على Railway

1. أنشئ مشروع جديد على Railway.
2. اربط مستودع GitHub.
3. أضف متغيرات البيئة المطلوبة.
4. استخدم Volume دائم في المسار `/data`.
5. اجعل `DATABASE_PATH=/data/play_point.db`.
6. فعّل خدمة واحدة لكل بوت باستخدام Tokens مختلفة.
