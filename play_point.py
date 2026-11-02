#!/usr/bin/env python3
"""PLAY POINT — single-file Telegram store (Python 3.11+, aiogram 3).
Install: pip install aiogram aiosqlite python-dotenv
Configure environment variables; never commit real tokens.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, FSInputFile)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configuration is read from environment/.env; secrets never belong in source control.
USER_BOT_TOKEN = os.getenv('USER_BOT_TOKEN', '').strip()
ADMIN_BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN', '').strip()
OWNER_ID_RAW = os.getenv('OWNER_ID', '').strip()
DATABASE_PATH = os.getenv('DATABASE_PATH', './data/play_point.db').strip()
PROOF_CHANNEL_ID = os.getenv('PROOF_CHANNEL_ID', '@playpo4').strip()
TIMEZONE = os.getenv('TIMEZONE', 'Africa/Algiers').strip()
BACKUP_INTERVAL_HOURS = os.getenv('BACKUP_INTERVAL_HOURS', '6').strip()
BACKUP_DIR = os.getenv('BACKUP_DIR', './backups').strip()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('play_point')
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@gmail\.com$', re.I)
PLAY_RE = re.compile(r'^https://play\.google\.com/store/apps/details\?id=[A-Za-z0-9._-]+(?:&[^\s]*)?$', re.I)
ACTIVE = ('pending_review', 'accepted', 'in_progress')
STATUS_AR = {'pending_review':'قيد المراجعة','accepted':'مقبول','in_progress':'قيد التنفيذ','completed':'مكتمل','rejected':'مرفوض','cancelled':'ملغي'}
REFUSAL_REASONS = ['رابط الدفع خاطئ','البريد الإلكتروني خاطئ','التطبيق غير مطابق','المعلومات غير مكتملة']
MAIN_BUTTONS = ['💎 تفعيل الجائزة الأسبوعية','📦 طلباتي','🎧 الدعم','💡 الاقتراحات والإضافات','📊 إحصائياتي','👥 دعواتي','📖 كيف يعمل؟ / الأسئلة الشائعة']

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, full_name TEXT NOT NULL DEFAULT '', balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0), joined_at TEXT NOT NULL DEFAULT(datetime('now')), is_banned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS servers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,flag TEXT NOT NULL,price INTEGER NOT NULL DEFAULT 0 CHECK(price>=0),active INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mandatory_channels(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,channel_id TEXT NOT NULL UNIQUE,invite_url TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS referrals(invited_id INTEGER PRIMARY KEY,inviter_id INTEGER NOT NULL REFERENCES users(id),reward INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT(datetime('now')),CHECK(inviter_id<>invited_id));
CREATE TABLE IF NOT EXISTS point_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),amount INTEGER NOT NULL,kind TEXT NOT NULL,reason TEXT NOT NULL,order_id INTEGER,actor TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),server_id INTEGER NOT NULL REFERENCES servers(id),price INTEGER NOT NULL,app1 TEXT NOT NULL,app2 TEXT NOT NULL,email TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending_review',rejection_reason TEXT,refunded INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT(datetime('now')),updated_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE UNIQUE INDEX IF NOT EXISTS one_active_order ON orders(user_id) WHERE status IN ('pending_review','accepted','in_progress');
CREATE TABLE IF NOT EXISTS order_events(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL REFERENCES orders(id),old_status TEXT,new_status TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT,created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS email_weekly_usage(email TEXT NOT NULL,week_start TEXT NOT NULL,order_id INTEGER NOT NULL REFERENCES orders(id),released INTEGER NOT NULL DEFAULT 0,UNIQUE(email,week_start));
CREATE TABLE IF NOT EXISTS drafts(user_id INTEGER PRIMARY KEY REFERENCES users(id),server_id INTEGER NOT NULL REFERENCES servers(id),step TEXT NOT NULL,app1 TEXT,app2 TEXT,updated_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS user_states(user_id INTEGER PRIMARY KEY REFERENCES users(id),state TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS support_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL REFERENCES support_tickets(id),sender_id INTEGER NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS suggestions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'new',created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS faq_items(id INTEGER PRIMARY KEY AUTOINCREMENT,question TEXT NOT NULL,answer TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS admin_audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL,action TEXT NOT NULL,target TEXT,reason TEXT,created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS proof_posts(order_id INTEGER PRIMARY KEY REFERENCES orders(id),message_id INTEGER UNIQUE,status TEXT NOT NULL DEFAULT 'sending',last_error TEXT,created_at TEXT NOT NULL DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS backup_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL DEFAULT(datetime('now')),result TEXT NOT NULL,path TEXT);
CREATE INDEX IF NOT EXISTS orders_user_idx ON orders(user_id,status);
CREATE INDEX IF NOT EXISTS ledger_user_idx ON point_transactions(user_id,created_at);
"""

class StoreDB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    async def open(self):
        db = await aiosqlite.connect(self.path, timeout=30)
        db.row_factory = aiosqlite.Row
        await db.execute('PRAGMA foreign_keys=ON')
        await db.execute('PRAGMA busy_timeout=30000')
        return db

    async def init(self):
        async with await self.open() as db:
            await db.executescript(SCHEMA)
            defaults = {'referral_reward':'10','maintenance':'0','join_text':'يرجى الاشتراك في جميع القنوات التالية ثم اضغط زر التحقق.','verify_button':'تحقق من الاشتراك ✅','faq_text':'اختر السيرفر، أرسل البيانات المطلوبة، ثم تابع الطلب من قسم طلباتي.','proof_hide_username':'0','menu_labels':'|'.join(MAIN_BUTTONS),'spam_cooldown_seconds':'1'}
            await db.executemany('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', defaults.items())
            for name, flag in [('الأمريكي','🇺🇸'),('الياباني','🇯🇵'),('البريطاني','🇬🇧'),('الألماني','🇩🇪')]:
                await db.execute('INSERT INTO servers(name,flag,price,active) SELECT ?,?,0,0 WHERE NOT EXISTS(SELECT 1 FROM servers WHERE name=?)',(name,flag,name))
            await db.commit()

    async def setting(self, key, default=''):
        async with await self.open() as db:
            cur=await db.execute('SELECT value FROM settings WHERE key=?',(key,)); r=await cur.fetchone(); return r['value'] if r else default
    async def set_setting(self,key,value):
        async with await self.open() as db:
            await db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value))); await db.commit()
    async def ensure_user(self, uid, username, fullname):
        async with await self.open() as db:
            await db.execute("INSERT INTO users(id,username,full_name) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name",(uid,username,fullname or '')); await db.commit()
    async def user(self,uid):
        async with await self.open() as db:
            c=await db.execute('SELECT * FROM users WHERE id=?',(uid,));r=await c.fetchone();return dict(r) if r else None
    async def servers(self,all_rows=False):
        async with await self.open() as db:
            c=await db.execute('SELECT * FROM servers'+('' if all_rows else ' WHERE active=1 AND price>0')+' ORDER BY id');return [dict(x) for x in await c.fetchall()]
    async def upsert_server(self,name,flag,price,active=1,sid=None):
        async with await self.open() as db:
            if sid:
                c=await db.execute('UPDATE servers SET name=?,flag=?,price=?,active=? WHERE id=?',(name,flag,price,int(active),sid))
                if not c.rowcount:raise ValueError('السيرفر غير موجود')
            else:
                c=await db.execute('INSERT INTO servers(name,flag,price,active) VALUES(?,?,?,?)',(name,flag,price,int(active)));sid=c.lastrowid
            await db.commit();return sid
    async def channels(self):
        async with await self.open() as db:
            c=await db.execute('SELECT * FROM mandatory_channels ORDER BY id');return [dict(x) for x in await c.fetchall()]
    async def add_channel(self,name,cid,url):
        async with await self.open() as db:
            await db.execute('INSERT INTO mandatory_channels(name,channel_id,invite_url) VALUES(?,?,?) ON CONFLICT(channel_id) DO UPDATE SET name=excluded.name,invite_url=excluded.invite_url,active=1',(name,cid,url));await db.commit()
    async def state(self,uid):
        async with await self.open() as db:
            c=await db.execute('SELECT state FROM user_states WHERE user_id=?',(uid,));r=await c.fetchone();return r['state'] if r else None
    async def set_state(self,uid,state):
        async with await self.open() as db:
            await db.execute('INSERT INTO user_states(user_id,state) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,updated_at=datetime(\'now\')',(uid,state));await db.commit()
    async def clear_state(self,uid):
        async with await self.open() as db:await db.execute('DELETE FROM user_states WHERE user_id=?',(uid,));await db.commit()
    async def save_draft(self,uid,sid,step,app1=None,app2=None):
        async with await self.open() as db:
            await db.execute("INSERT INTO drafts(user_id,server_id,step,app1,app2) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET server_id=excluded.server_id,step=excluded.step,app1=COALESCE(excluded.app1,drafts.app1),app2=COALESCE(excluded.app2,drafts.app2),updated_at=datetime('now')",(uid,sid,step,app1,app2));await db.commit()
    async def draft(self,uid):
        async with await self.open() as db:
            c=await db.execute('SELECT * FROM drafts WHERE user_id=?',(uid,));r=await c.fetchone();return dict(r) if r else None
    async def active_order(self,uid):
        async with await self.open() as db:return bool(await (await db.execute("SELECT 1 FROM orders WHERE user_id=? AND status IN ('pending_review','accepted','in_progress')",(uid,))).fetchone())
    async def create_order(self,uid,sid,app1,app2,email,week):
        async with await self.open() as db:
            try:
                await db.execute('BEGIN IMMEDIATE')
                s=await (await db.execute('SELECT * FROM servers WHERE id=? AND active=1 AND price>0',(sid,))).fetchone()
                u=await (await db.execute('SELECT * FROM users WHERE id=? AND is_banned=0',(uid,))).fetchone()
                if not s:raise ValueError('السيرفر متوقف أو لم يحدد المالك سعره.')
                if not u:raise ValueError('حسابك غير موجود أو محظور.')
                if await (await db.execute("SELECT 1 FROM orders WHERE user_id=? AND status IN ('pending_review','accepted','in_progress')",(uid,))).fetchone():raise ValueError('لديك طلب نشط بالفعل.')
                if u['balance']<s['price']:raise ValueError('رصيدك غير كافٍ.')
                if await (await db.execute('SELECT 1 FROM email_weekly_usage WHERE email=? AND week_start=? AND released=0',(email.lower(),week))).fetchone():raise ValueError('هذا Gmail مستخدم خلال هذا الأسبوع.')
                cur=await db.execute('INSERT INTO orders(user_id,server_id,price,app1,app2,email) VALUES(?,?,?,?,?,?)',(uid,sid,s['price'],app1,app2,email.lower()));oid=cur.lastrowid
                await db.execute('INSERT INTO email_weekly_usage(email,week_start,order_id) VALUES(?,?,?)',(email.lower(),week,oid))
                await db.execute('UPDATE users SET balance=balance-? WHERE id=? AND balance>=?',(s['price'],uid,s['price']))
                await db.execute("INSERT INTO point_transactions(user_id,amount,kind,reason,order_id,actor) VALUES(?,?,'spend','خصم طلب',?,'system')",(uid,-s['price'],oid))
                await db.execute("INSERT INTO order_events(order_id,new_status,actor) VALUES(?,'pending_review','system')",(oid,))
                await db.execute('DELETE FROM drafts WHERE user_id=?',(uid,));await db.commit();return oid
            except Exception:await db.rollback();raise
    async def change_order(self,oid,new,actor,reason=None):
        transitions={'pending_review':{'accepted','rejected'},'accepted':{'in_progress','rejected'},'in_progress':{'completed','rejected'}}
        async with await self.open() as db:
            try:
                await db.execute('BEGIN IMMEDIATE');o=await (await db.execute('SELECT * FROM orders WHERE id=?',(oid,))).fetchone()
                if not o or new not in transitions.get(o['status'],set()):raise ValueError('حالة الطلب لا تسمح بهذا الإجراء أو تم تنفيذه سابقاً.')
                await db.execute('UPDATE orders SET status=?,rejection_reason=?,updated_at=datetime(\'now\') WHERE id=?',(new,reason,oid))
                await db.execute('INSERT INTO order_events(order_id,old_status,new_status,actor,reason) VALUES(?,?,?,?,?)',(oid,o['status'],new,str(actor),reason))
                if new=='rejected':
                    await db.execute('UPDATE users SET balance=balance+? WHERE id=?',(o['price'],o['user_id']))
                    await db.execute('UPDATE orders SET refunded=1 WHERE id=?',(oid,))
                    await db.execute("INSERT INTO point_transactions(user_id,amount,kind,reason,order_id,actor) VALUES(?,?,'refund',?,?,?)",(o['user_id'],o['price'],reason or 'رفض الطلب',oid,str(actor)))
                    await db.execute('UPDATE email_weekly_usage SET released=1 WHERE order_id=?',(oid,))
                await db.commit();return dict(o)
            except Exception:await db.rollback();raise
    async def order(self,oid):
        async with await self.open() as db:
            c=await db.execute('SELECT o.*,u.username,u.full_name,s.name server_name,s.flag server_flag FROM orders o JOIN users u ON u.id=o.user_id JOIN servers s ON s.id=o.server_id WHERE o.id=?',(oid,));r=await c.fetchone();return dict(r) if r else None
    async def user_orders(self,uid):
        async with await self.open() as db:
            c=await db.execute('SELECT o.*,s.name server_name,s.flag server_flag FROM orders o JOIN servers s ON s.id=o.server_id WHERE o.user_id=? ORDER BY o.id DESC',(uid,));return [dict(x) for x in await c.fetchall()]
    async def reward_referral(self,inviter,invited,verified):
        if not verified or inviter==invited:return False
        async with await self.open() as db:
            try:
                await db.execute('BEGIN IMMEDIATE')
                if await (await db.execute('SELECT 1 FROM referrals WHERE invited_id=?',(invited,))).fetchone():return False
                if not await (await db.execute('SELECT 1 FROM users WHERE id=?',(inviter,))).fetchone():return False
                reward=int((await (await db.execute("SELECT value FROM settings WHERE key='referral_reward'")).fetchone())['value'])
                await db.execute('INSERT INTO referrals(invited_id,inviter_id,reward) VALUES(?,?,?)',(invited,inviter,reward))
                await db.execute('UPDATE users SET balance=balance+? WHERE id=?',(reward,inviter))
                await db.execute("INSERT INTO point_transactions(user_id,amount,kind,reason,actor) VALUES(?,?,'referral','مكافأة إحالة','system')",(inviter,reward));await db.commit();return True
            except Exception:await db.rollback();raise
    async def ledger_adjust(self,uid,amount,reason,actor):
        async with await self.open() as db:
            try:
                await db.execute('BEGIN IMMEDIATE');c=await db.execute('UPDATE users SET balance=balance+? WHERE id=? AND balance+?>=0',(amount,uid,amount))
                if c.rowcount!=1:raise ValueError('المستخدم غير موجود أو الرصيد لا يكفي')
                await db.execute('INSERT INTO point_transactions(user_id,amount,kind,reason,actor) VALUES(?,? ,\'admin\',?,?)',(uid,amount,reason,str(actor)));await db.execute('INSERT INTO admin_audit_logs(actor_id,action,target,reason) VALUES(?,?,?,?)',(actor,'balance',str(uid),reason));await db.commit()
            except Exception:await db.rollback();raise
    async def user_search(self,q):
        async with await self.open() as db:
            c=await db.execute('SELECT * FROM users WHERE id=?' if str(q).isdigit() else 'SELECT * FROM users WHERE username=?',(int(q) if str(q).isdigit() else str(q).lstrip('@'),));r=await c.fetchone();return dict(r) if r else None
    async def all_users(self):
        async with await self.open() as db:return [dict(r) for r in await (await db.execute('SELECT * FROM users')).fetchall()]
    async def ticket(self,uid,text):
        async with await self.open() as db:
            c=await db.execute('INSERT INTO support_tickets(user_id) VALUES(?)',(uid,));tid=c.lastrowid;await db.execute('INSERT INTO support_messages(ticket_id,sender_id,body) VALUES(?,?,?)',(tid,uid,text));await db.commit();return tid
    async def ticket_user(self,tid):
        async with await self.open() as db:
            r=await (await db.execute('SELECT user_id FROM support_tickets WHERE id=?',(tid,))).fetchone();return r['user_id'] if r else None
    async def ticket_reply(self,tid,uid,text):
        async with await self.open() as db:await db.execute('INSERT INTO support_messages(ticket_id,sender_id,body) VALUES(?,?,?)',(tid,uid,text));await db.commit()
    async def add_suggestion(self,uid,text):
        async with await self.open() as db:await db.execute('INSERT INTO suggestions(user_id,body) VALUES(?,?)',(uid,text));await db.commit()
    async def stats(self,uid=None):
        async with await self.open() as db:
            if uid:
                u=dict(await (await db.execute('SELECT * FROM users WHERE id=?',(uid,))).fetchone()); rows=await (await db.execute('SELECT status,count(*) n FROM orders WHERE user_id=? GROUP BY status',(uid,))).fetchall();spent=await (await db.execute("SELECT COALESCE(-sum(amount),0) n FROM point_transactions WHERE user_id=? AND kind='spend'",(uid,))).fetchone();rank=await (await db.execute('SELECT count(*)+1 n FROM users WHERE (SELECT referral_count FROM users WHERE id=?) < (SELECT count(*) FROM referrals WHERE inviter_id=users.id)',(uid,))).fetchone();return {'user':u,'states':{r['status']:r['n'] for r in rows},'spent':spent['n'],'rank':rank['n']}
            vals={}
            for key,sql in {'users':'SELECT count(*) n FROM users','orders':'SELECT count(*) n FROM orders','completed':"SELECT count(*) n FROM orders WHERE status='completed'",'rejected':"SELECT count(*) n FROM orders WHERE status='rejected'",'referral_points':'SELECT COALESCE(sum(reward),0) n FROM referrals','spent':"SELECT COALESCE(-sum(amount),0) n FROM point_transactions WHERE kind='spend'",'refunded':"SELECT COALESCE(sum(amount),0) n FROM point_transactions WHERE kind='refund'"}.items():vals[key]=(await (await db.execute(sql)).fetchone())['n']
            return vals
    async def claim_proof(self,oid):
        async with await self.open() as db:
            try:
                await db.execute('BEGIN IMMEDIATE');o=await (await db.execute('SELECT status FROM orders WHERE id=?',(oid,))).fetchone()
                if not o or o['status']!='completed':raise ValueError('لا ينشر الإثبات إلا بعد إتمام الطلب')
                c=await db.execute("INSERT OR IGNORE INTO proof_posts(order_id,status) VALUES(?,'sending')",(oid,));await db.commit();return c.rowcount==1
            except Exception:await db.rollback();raise
    async def proof_saved(self,oid,msgid):
        async with await self.open() as db:
            c=await db.execute("UPDATE proof_posts SET message_id=?,status='posted' WHERE order_id=? AND status='sending' AND message_id IS NULL",(msgid,oid));await db.commit()
            if not c.rowcount:raise ValueError('سبق تسجيل إثبات الطلب')
    async def proof_fail(self,oid,error):
        # Don't automatically retry an ambiguous Telegram send: it could create a duplicate post.
        async with await self.open() as db:await db.execute("UPDATE proof_posts SET status='failed',last_error=? WHERE order_id=? AND status='sending'",(error[:400],oid));await db.commit()
    async def backup(self,directory):
        Path(directory).mkdir(parents=True,exist_ok=True);target=str(Path(directory)/f"play_point_{datetime.utcnow():%Y%m%d_%H%M%S_%f}.db")
        def make():
            src=sqlite3.connect(self.path);dst=sqlite3.connect(target)
            try:src.backup(dst)
            finally:src.close();dst.close()
            with sqlite3.connect(target) as check:
                if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('نسخة احتياطية غير سليمة')
        await asyncio.to_thread(make)
        async with await self.open() as db:await db.execute("INSERT INTO backup_logs(result,path) VALUES('ok',?)",(target,));await db.commit()
        return target
    async def restore(self,source):
        def perform():
            if not Path(source).is_file():raise FileNotFoundError(source)
            with sqlite3.connect(source) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('قاعدة النسخة تالفة')
                if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():raise ValueError('الملف ليس قاعدة PLAY POINT')
            tmp=self.path+'.restore.tmp';shutil.copy2(source,tmp)
            with sqlite3.connect(tmp) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':Path(tmp).unlink(missing_ok=True);raise ValueError('فشل تحقق النسخة')
            os.replace(tmp,self.path)
        await asyncio.to_thread(perform)


def main_keyboard(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x)] for x in MAIN_BUTTONS],resize_keyboard=True)
def subscribe_keyboard(db,channels):
    rows=[[InlineKeyboardButton(text=x['name'] or 'اشترك بالقناة',url=x['invite_url'])] for x in channels]
    rows.append([InlineKeyboardButton(text='تحقق من الاشتراك ✅',callback_data='sub:check')]);return InlineKeyboardMarkup(inline_keyboard=rows)
async def check_member(bot,db,uid):
    chans=[x for x in await db.channels() if x['active']]
    for ch in chans:
        try:
            member=await bot.get_chat_member(ch['channel_id'],uid)
            if member.status in ('left','kicked'):return False,chans
        except Exception as exc:
            log.warning('Subscription API check failed for channel id %s (%s)',ch['id'],type(exc).__name__);return None,chans
    return True,chans
async def gate(m,db):
    ok,chans=await check_member(m.bot,db,m.from_user.id)
    if ok is True:return True
    if ok is None:await m.answer('تعذر التحقق من عضويتك حالياً. حاول لاحقاً؛ يجب منح البوت صلاحية فحص العضوية.')
    else:await m.answer(await db.setting('join_text'),reply_markup=subscribe_keyboard(db,chans))
    return False

async def proof_publish(bot,db,settings,oid):
    if not await db.claim_proof(oid):return
    o=await db.order(oid);username=o['username']
    if username and await db.setting('proof_hide_username','0')=='1':username=username[:2]+'***'
    text=(f"🎉 تم تنفيذ طلبك بنجاح! ✅\n━━━━━━━━━━━━━━━━━━\n💎 PLAY POINT\n━━━━━━━━━━━━━━━━━━\n🛍️ الخدمة: تفعيل الجائزة الأسبوعية\n{o['server_flag']} السيرفر: {o['server_name']}\n👤 العميل: {'@'+username if username else 'عميل'}\n🧾 رقم الطلب: #{oid}\n💰 السعر: {o['price']} نقطة\n🟢 الحالة: مكتمل بنجاح\n🕒 الوقت: {o['updated_at']}\n━━━━━━━━━━━━━━━━━━\n🏆 PLAY POINT\n⚡ سرعة التنفيذ | 💎 جودة الخدمة\n🤝 ثقتكم شرف لنا!\n━━━━━━━━━━━━━━━━━━")
    try:
        msg=await bot.send_message(settings['proof_channel'],text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🚀 اطلب الآن',url=f"https://t.me/{settings['user_username']}")]]))
        await db.proof_saved(oid,msg.message_id)
    except Exception as exc:
        log.exception('Proof publish failure for order %s',oid);await db.proof_fail(oid,str(exc))
        try:await bot.send_message(settings['owner'],f'تعذر نشر إثبات الطلب #{oid}؛ تحقق من صلاحيات قناة الإثبات. لم تتم إعادة المحاولة تلقائياً لمنع التكرار المحتمل.')
        except Exception:pass


def user_router(db,settings):
    r=Router()
    @r.message(CommandStart())
    async def start(m:Message):
        await db.ensure_user(m.from_user.id,m.from_user.username,m.from_user.full_name)
        ok,chans=await check_member(m.bot,db,m.from_user.id)
        if ok is None:await m.answer('تعذر التحقق من الاشتراك الآن. أعد المحاولة لاحقاً.');return
        if not ok:await m.answer(await db.setting('join_text'),reply_markup=subscribe_keyboard(db,chans));return
        args=(m.text or '').split(maxsplit=1)
        if len(args)>1 and args[1].startswith('ref_'):
            try:await db.reward_referral(int(args[1][4:]),m.from_user.id,True)
            except (ValueError,TypeError):pass
        d=await db.draft(m.from_user.id)
        await m.answer(f"لديك مسودة محفوظة، الخطوة: {d['step']}" if d else 'أهلاً بك في PLAY POINT 💎',reply_markup=main_keyboard())
    @r.callback_query(F.data=='sub:check')
    async def subcheck(q:CallbackQuery):
        ok,chans=await check_member(q.bot,db,q.from_user.id)
        if ok is True:await q.message.answer('تم التحقق من الاشتراك ✅',reply_markup=main_keyboard());await q.answer()
        elif ok is False:await q.answer('اشترك في جميع القنوات أولاً.',show_alert=True)
        else:await q.answer('تعذر التحقق حالياً.',show_alert=True)
    @r.message(F.text=='💎 تفعيل الجائزة الأسبوعية')
    async def begin(m:Message):
        if not await gate(m,db):return
        u=await db.user(m.from_user.id)
        if not u or u['is_banned'] or await db.setting('maintenance')=='1':await m.answer('الخدمة غير متاحة حالياً.');return
        if await db.active_order(m.from_user.id):await m.answer('لديك طلب نشط بالفعل. راجعه من طلباتي.');return
        servers=await db.servers()
        if not servers:await m.answer('لا توجد سيرفرات مفعلة ومسعرة حالياً.');return
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{s['flag']} {s['name']} — {s['price']} نقطة",callback_data=f"srv:{s['id']}")] for s in servers]);await m.answer('اختر السيرفر، السعر بالنقاط:',reply_markup=kb)
    @r.callback_query(F.data.startswith('srv:'))
    async def choose(q:CallbackQuery):
        ok,_=await check_member(q.bot,db,q.from_user.id)
        if ok is not True:await q.answer('يلزم الاشتراك الإجباري.',show_alert=True);return
        try:sid=int(q.data.split(':')[1])
        except Exception:await q.answer('اختيار غير صالح.');return
        s=next((x for x in await db.servers() if x['id']==sid),None)
        if not s or await db.active_order(q.from_user.id):await q.answer('السيرفر غير متاح أو لديك طلب نشط.',show_alert=True);return
        await db.save_draft(q.from_user.id,sid,'app1')
        await q.message.answer('⚠️ ملاحظة هامة:\nابحث عن هذين التطبيقين في قوقل بلاي وأرسل رابط دفع لكل منهما:\n1️⃣ Upping Floors Stack Blocks\n2️⃣ Neon Valley AMOLED\n\n📥 أرسل الآن رابط دفع التطبيق الأول:');await q.answer()
    @r.message(F.text=='📦 طلباتي')
    async def orders(m:Message):
        if not await gate(m,db):return
        rows=await db.user_orders(m.from_user.id)
        if not rows:await m.answer('لا توجد طلبات.');return
        await m.answer('\n'.join(f"#{x['id']} | {x['server_flag']} {x['server_name']} | {x['price']} نقطة | {STATUS_AR.get(x['status'],x['status'])} | {x['created_at']}" for x in rows))
    @r.message(F.text=='📊 إحصائياتي')
    async def stats(m:Message):
        if not await gate(m,db):return
        s=await db.stats(m.from_user.id);u=s['user'];bot=await m.bot.get_me();st=s['states'];active=sum(st.get(x,0) for x in ACTIVE)
        await m.answer(f"الرصيد: {u['balance']} نقطة\nالدعوات الناجحة: {u['referral_count'] if 'referral_count' in u else 0}\nالنقاط المصروفة: {s['spent']}\nمكتملة: {st.get('completed',0)} | نشطة: {active} | مرفوضة: {st.get('rejected',0)}\nتاريخ الانضمام: {u['joined_at']}\nترتيب الداعين: {s['rank']}\nرابط الدعوة: https://t.me/{bot.username}?start=ref_{u['id']}")
    @r.message(F.text=='👥 دعواتي')
    async def invites(m:Message):
        if not await gate(m,db):return
        u=await db.user(m.from_user.id);bot=await m.bot.get_me()
        async with await db.open() as con:
            c=await con.execute('SELECT u.full_name,r.reward FROM referrals r JOIN users u ON u.id=r.invited_id WHERE r.inviter_id=? ORDER BY r.created_at DESC LIMIT 30',(m.from_user.id,));rows=await c.fetchall()
            total=await (await con.execute('SELECT COALESCE(sum(reward),0) FROM referrals WHERE inviter_id=?',(m.from_user.id,))).fetchone()
        list_text='\n'.join(f"{x['full_name']} (+{x['reward']})" for x in rows) or 'لا توجد دعوات بعد.'
        await m.answer(f"رابطك: https://t.me/{bot.username}?start=ref_{u['id']}\nعدد الدعوات: {len(rows)}\nمجموع المكافآت: {total[0]} نقطة\n{list_text}")
    @r.message(F.text=='📖 كيف يعمل؟ / الأسئلة الشائعة')
    async def faq(m:Message):
        if await gate(m,db):await m.answer(await db.setting('faq_text'))
    @r.message(F.text=='🎧 الدعم')
    async def support_start(m:Message):
        if await gate(m,db):await db.set_state(m.from_user.id,'support');await m.answer('اكتب رسالتك للدعم الآن.')
    @r.message(F.text=='💡 الاقتراحات والإضافات')
    async def suggest_start(m:Message):
        if await gate(m,db):await db.set_state(m.from_user.id,'suggestion');await m.answer('أرسل اقتراحك الآن.')
    @r.message(F.text)
    async def collect(m:Message):
        if not await gate(m,db):return
        text=(m.text or '').strip();uid=m.from_user.id
        if not text or text.startswith('/'):return
        if any(s in text.lower() for s in ('password','pass:','كلمة السر','كلمة المرور','كلمةالسر')):
            try:await m.delete()
            except Exception:pass
            await m.answer('لا ترسل كلمة مرور Gmail مطلقاً؛ أرسل عنوان البريد فقط.');return
        state=await db.state(uid);draft=await db.draft(uid)
        if state=='support':
            tid=await db.ticket(uid,text);await db.clear_state(uid);await m.answer(f'وصلت رسالتك للدعم، رقم التذكرة #{tid}.')
            try:await m.bot.send_message(settings['owner'],f'🎧 تذكرة #{tid} من {uid} @{m.from_user.username or "—"}:\n{text}')
            except Exception:log.exception('Support notification failed')
            return
        if state=='suggestion':
            await db.add_suggestion(uid,text);await db.clear_state(uid);await m.answer('تم استلام اقتراحك.')
            try:await m.bot.send_message(settings['owner'],f'💡 اقتراح من {uid}:\n{text}')
            except Exception:log.exception('Suggestion notification failed')
            return
        if not draft:return
        if draft['step'] in ('app1','app2'):
            if not PLAY_RE.fullmatch(text):await m.answer('الرابط غير صالح. أرسل رابط Google Play من صفحة التطبيق؛ لا يمكن إثبات الدفع آلياً.');return
            if draft['step']=='app1':await db.save_draft(uid,draft['server_id'],'app2',app1=text);await m.answer('📥 ممتاز. الآن أرسل رابط دفع التطبيق الثاني (Neon Valley AMOLED):')
            else:await db.save_draft(uid,draft['server_id'],'email',app2=text);await m.answer('📧 رائع. الآن أرسل البريد الإلكتروني (Gmail فقط بدون كلمة سر):')
            return
        if draft['step']=='email':
            if not EMAIL_RE.fullmatch(text):await m.answer('عنوان Gmail غير صالح. أرسل عنوان @gmail.com دون كلمة مرور.');return
            local=datetime.now(ZoneInfo(settings['timezone']));week=(local.date()-timedelta(days=local.weekday())).isoformat()
            try:oid=await db.create_order(uid,draft['server_id'],draft['app1'],draft['app2'],text,week)
            except ValueError as exc:await m.answer(str(exc));return
            u=await db.user(uid);await m.answer(f"✅ تم استلام طلبك!\n━━━━━━━━━━━━━━\n🔢 رقم الطلب: #{oid}\n\n⏳ الحالة: قيد المراجعة\n🔔 سيصلك إشعار هنا فور تحديث الحالة.\nرصيدك الحالي: {u['balance']} نقطة")
            try:
                o=await db.order(oid);kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ قبول',callback_data=f'ord:accepted:{oid}'),InlineKeyboardButton(text='🔄 بدء التنفيذ',callback_data=f'ord:in_progress:{oid}')],[InlineKeyboardButton(text='🏁 إتمام',callback_data=f'ord:completed:{oid}'),InlineKeyboardButton(text='❌ رفض',callback_data=f'ord:reject:{oid}')]])
                await m.bot.send_message(settings['owner'],f"طلب #{oid}\nTelegram ID: {uid}\nusername: @{o['username'] or '—'}\nالسيرفر: {o['server_name']}\nالسعر: {o['price']}\nرابط التطبيق الأول: {o['app1']}\nرابط التطبيق الثاني: {o['app2']}\nGmail: {o['email']}\nالتاريخ: {o['created_at']}",reply_markup=kb)
            except Exception:log.exception('New order admin notification failed; order remains saved')
    return r


def admin_router(db,settings):
    r=Router();owner=settings['owner']
    def is_owner(uid):return uid==owner
    @r.message(CommandStart())
    async def start(m:Message):
        if not is_owner(m.from_user.id):await m.answer('هذا البوت خاص بمالك PLAY POINT.');return
        await m.answer('لوحة PLAY POINT. استخدم /admin لعرض الأوامر.')
    @r.message(Command('admin'))
    async def help(m:Message):
        if not is_owner(m.from_user.id):await m.answer('غير مصرح.');return
        await m.answer('/servers | /price ID PRICE | /server_add الاسم|العلم|السعر | /server_toggle ID on|off\n/channels | /channel_add @id|url|name | /channel_toggle ID on|off | /channel_delete ID\n/reward N | /maintenance on|off | /join_text نص | /verify_text نص | /faq نص\n/user ID أو username | /points ID +/-N سبب | /setbalance ID N سبب | /ban ID | /unban ID\n/stats | /reply TICKET_ID نص | /broadcast نص | /backup\nإرسال ملف قاعدة بيانات صالح لاستعادته بعد التحقق.')
    @r.message(Command('servers'))
    async def servers(m):
        if not is_owner(m.from_user.id):return
        rows=await db.servers(True);await m.answer('\n'.join(f"{s['id']} {s['flag']} {s['name']} — {s['price']} نقطة — {'فعال' if s['active'] else 'متوقف'}" for s in rows) or 'لا توجد سيرفرات')
    @r.message(Command('price'))
    async def price(m):
        if not is_owner(m.from_user.id):return
        try:
            _,sid,p=m.text.split();sid=int(sid);p=int(p)
            if p<1:raise ValueError
            s=next(x for x in await db.servers(True) if x['id']==sid);await db.upsert_server(s['name'],s['flag'],p,1,sid);await db.set_setting('last_price_change',f'{owner}:{sid}:{p}');await m.answer('تم حفظ السعر وتفعيل السيرفر.')
        except Exception:await m.answer('الصيغة: /price ID PRICE؛ يجب أن يكون السعر موجباً. راجع /servers')
    @r.message(Command('server_add'))
    async def server_add(m):
        if not is_owner(m.from_user.id):return
        try:n,f,p=m.text.partition(' ')[2].split('|');sid=await db.upsert_server(n.strip(),f.strip(),int(p),1);await m.answer(f'أضيف السيرفر #{sid}.')
        except Exception:await m.answer('الصيغة: /server_add الاسم|العلم|السعر')
    @r.message(Command('server_toggle'))
    async def server_toggle(m):
        if not is_owner(m.from_user.id):return
        try:_,sid,mode=m.text.split();assert mode in ('on','off')
        except Exception:await m.answer('الصيغة: /server_toggle ID on|off');return
        async with await db.open() as c:await c.execute('UPDATE servers SET active=? WHERE id=?',(mode=='on',int(sid)));await c.commit()
        await m.answer('تم تغيير حالة السيرفر.')
    @r.message(Command('channels'))
    async def channels(m):
        if not is_owner(m.from_user.id):return
        rows=await db.channels();await m.answer('\n'.join(f"{x['id']} {x['name']} {x['channel_id']} {x['invite_url']} active={x['active']}" for x in rows) or 'لا توجد قنوات')
    @r.message(Command('channel_add'))
    async def channel_add(m):
        if not is_owner(m.from_user.id):return
        try:cid,url,name=m.text.partition(' ')[2].split('|');await db.add_channel(name.strip(),cid.strip(),url.strip());await m.answer('تمت إضافة/تحديث القناة.')
        except Exception:await m.answer('الصيغة: /channel_add @channel|https://t.me/channel|الاسم')
    @r.message(Command('channel_toggle'))
    async def channel_toggle(m):
        if not is_owner(m.from_user.id):return
        try:_,sid,mode=m.text.split();assert mode in ('on','off')
        except Exception:await m.answer('الصيغة: /channel_toggle ID on|off');return
        async with await db.open() as c:await c.execute('UPDATE mandatory_channels SET active=? WHERE id=?',(mode=='on',int(sid)));await c.commit()
        await m.answer('تم تحديث حالة القناة.')
    @r.message(Command('channel_delete'))
    async def channel_delete(m):
        if not is_owner(m.from_user.id):return
        try:async with_dummy=0
        except Exception:pass
        try:
            sid=int(m.text.split()[1])
            async with await db.open() as c:await c.execute('DELETE FROM mandatory_channels WHERE id=?',(sid,));await c.commit()
            await m.answer('تم حذف القناة.')
        except Exception:await m.answer('الصيغة: /channel_delete ID')
    @r.message(Command('reward'))
    async def reward(m):
        if not is_owner(m.from_user.id):return
        try:v=int(m.text.split()[1]);assert v>=0;await db.set_setting('referral_reward',v);await m.answer('تم تحديث مكافأة الإحالة.')
        except Exception:await m.answer('الصيغة: /reward عدد غير سالب')
    @r.message(Command('maintenance'))
    async def maintenance(m):
        if is_owner(m.from_user.id):await db.set_setting('maintenance','1' if m.text.endswith('on') else '0');await m.answer('تم تحديث وضع الصيانة.')
    @r.message(Command('join_text'))
    async def jointext(m):
        if is_owner(m.from_user.id):await db.set_setting('join_text',m.text.partition(' ')[2]);await m.answer('تم حفظ الرسالة.')
    @r.message(Command('verify_text'))
    async def verifytext(m):
        if is_owner(m.from_user.id):await db.set_setting('verify_button',m.text.partition(' ')[2] or 'تحقق من الاشتراك ✅');await m.answer('تم حفظ نص الزر. (أعد تشغيل البوت لتحديث لوحة تحقق موجودة مسبقاً)')
    @r.message(Command('faq'))
    async def faq(m):
        if is_owner(m.from_user.id):await db.set_setting('faq_text',m.text.partition(' ')[2]);await m.answer('تم تحديث نص كيف يعمل والأسئلة الشائعة.')
    @r.message(Command('user'))
    async def user(m):
        if not is_owner(m.from_user.id):return
        u=await db.user_search(m.text.partition(' ')[2].strip())
        if not u:await m.answer('المستخدم غير موجود.');return
        orders=await db.user_orders(u['id']);await m.answer(f"الاسم: {u['full_name']} | @{u['username']} | ID {u['id']}\nالرصيد: {u['balance']} | محظور: {bool(u['is_banned'])}\nالطلبات: {len(orders)} | تاريخ الانضمام: {u['joined_at']}")
    @r.message(Command('points'))
    async def points(m):
        if not is_owner(m.from_user.id):return
        try:_,uid,amount,*why=m.text.split();uid=int(uid);amount=int(amount);reason=' '.join(why);assert reason
        except Exception:await m.answer('الصيغة: /points ID +N|-N سبب');return
        try:await db.ledger_adjust(uid,amount,reason,m.from_user.id);await m.bot.send_message(uid,f'تم تعديل رصيدك {amount:+d}. السبب: {reason}. رصيدك: {(await db.user(uid))["balance"]}');await m.answer('تم تعديل الرصيد وتسجيل العملية.')
        except Exception as e:await m.answer(str(e))
    @r.message(Command('setbalance'))
    async def setbalance(m):
        if not is_owner(m.from_user.id):return
        try:_,uid,balance,*why=m.text.split();uid=int(uid);balance=int(balance);reason=' '.join(why);assert balance>=0 and reason;u=await db.user(uid);await db.ledger_adjust(uid,balance-u['balance'],reason,m.from_user.id);await m.answer('تم ضبط الرصيد وتسجيل الحركة.')
        except Exception:await m.answer('الصيغة: /setbalance ID رصيد سبب')
    async def ban_command(m,banned):
        if not is_owner(m.from_user.id):return
        try:uid=int(m.text.split()[1])
        except Exception:await m.answer('حدد Telegram ID.');return
        async with await db.open() as c:await c.execute('UPDATE users SET is_banned=? WHERE id=?',(int(banned),uid));await c.execute('INSERT INTO admin_audit_logs(actor_id,action,target) VALUES(?,?,?)',(owner,'ban' if banned else 'unban',str(uid)));await c.commit()
        await m.answer('تم تحديث حالة الحظر.')
    @r.message(Command('ban'))
    async def ban(m):await ban_command(m,True)
    @r.message(Command('unban'))
    async def unban(m):await ban_command(m,False)
    @r.message(Command('stats'))
    async def stats(m):
        if not is_owner(m.from_user.id):return
        s=await db.stats();await m.answer(f"المستخدمون {s['users']} | الطلبات {s['orders']} | مكتملة {s['completed']} | مرفوضة {s['rejected']}\nنقاط الإحالات {s['referral_points']} | المصروفة {s['spent']} | المسترجعة {s['refunded']}")
    @r.message(Command('reply'))
    async def reply(m):
        if not is_owner(m.from_user.id):return
        try:_,tid,*parts=m.text.split();tid=int(tid);text=' '.join(parts);uid=await db.ticket_user(tid);assert uid and text
        except Exception:await m.answer('الصيغة: /reply TICKET_ID نص');return
        await db.ticket_reply(tid,owner,text);await m.bot.send_message(uid,f'رد الدعم على التذكرة #{tid}:\n{text}');await m.answer('تم إرسال الرد لصاحب التذكرة.')
    @r.message(Command('broadcast'))
    async def broadcast(m):
        if not is_owner(m.from_user.id):return
        text=m.text.partition(' ')[2].strip()
        if not text:await m.answer('أرفق نص الرسالة.');return
        users=await db.all_users();ok=fail=0
        for u in users:
            try:await m.bot.send_message(u['id'],text);ok+=1;await asyncio.sleep(.04)
            except Exception:fail+=1
        await m.answer(f'انتهى البث: نجح {ok} وفشل {fail}.')
    @r.message(Command('backup'))
    async def backup(m):
        if not is_owner(m.from_user.id):return
        try:path=await db.backup(BACKUP_DIR);await m.answer_document(FSInputFile(path),caption='نسخة PLAY POINT الاحتياطية')
        except Exception:log.exception('Backup failed');await m.answer('فشل إنشاء النسخة الاحتياطية.')
    @r.message(F.document)
    async def restore(m):
        if not is_owner(m.from_user.id):return
        if not (m.document.file_name or '').lower().endswith(('.db','.sqlite','.sqlite3')):return
        local=str(Path(tempfile.gettempdir())/f'play_point_restore_{owner}.db')
        try:await m.bot.download(m.document,destination=local);await db.restore(local);await m.answer('تم التحقق من سلامة نسخة SQLite واستعادتها. أعد تشغيل البوت للتأكد من الاتصالات النشطة.')
        except Exception as e:await m.answer(f'تعذرت الاستعادة: {str(e)[:160]}')
    @r.callback_query(F.data.startswith('ord:'))
    async def order_callback(q:CallbackQuery):
        if not is_owner(q.from_user.id):await q.answer('غير مصرح.',show_alert=True);return
        try:
            _,action,s=q.data.split(':');oid=int(s)
            if action=='reject':
                buttons=[[InlineKeyboardButton(text=x,callback_data=f'rej:{oid}:{i}') for i,x in enumerate(REFUSAL_REASONS[:2])],[InlineKeyboardButton(text=x,callback_data=f'rej:{oid}:{i}') for i,x in enumerate(REFUSAL_REASONS[2:])]]
                buttons.append([InlineKeyboardButton(text='إلغاء',callback_data=f'rejcancel:{oid}')]);await q.message.answer(f'تأكيد رفض الطلب #{oid}: اختر السبب.',reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons));await q.answer();return
            o=await db.change_order(oid,action,owner)
            await q.bot.send_message(o['user_id'],STATUS_AR[action]+f"\nرصيدك: {(await db.user(o['user_id']))['balance']} نقطة")
            if action=='completed':await proof_publish(q.bot,db,settings,oid)
            await q.answer('تم تحديث الحالة.')
        except Exception as e:await q.answer(str(e)[:180],show_alert=True)
    @r.callback_query(F.data.startswith('rej:'))
    async def reject(q:CallbackQuery):
        if not is_owner(q.from_user.id):await q.answer('غير مصرح.',show_alert=True);return
        try:
            _,s,i=q.data.split(':');oid=int(s);reason=REFUSAL_REASONS[int(i)];o=await db.change_order(oid,'rejected',owner,reason);await q.bot.send_message(o['user_id'],f'تم رفض الطلب #{oid}. السبب: {reason}. أُعيدت النقاط مرة واحدة. رصيدك: {(await db.user(o["user_id"]))["balance"]}');await q.answer('تم الرفض وإرجاع النقاط.')
        except Exception as e:await q.answer(str(e)[:180],show_alert=True)
    @r.callback_query(F.data.startswith('rejcancel:'))
    async def reject_cancel(q):
        if is_owner(q.from_user.id):await q.answer('تم إلغاء إجراء الرفض.');await q.message.edit_reply_markup(reply_markup=None)
        else:await q.answer('غير مصرح.',show_alert=True)
    return r

async def scheduled_backups(db,admin_bot,owner,hours):
    while True:
        await asyncio.sleep(hours*3600)
        try:
            path=await db.backup(BACKUP_DIR);await admin_bot.send_document(owner,FSInputFile(path),caption='نسخة PLAY POINT الدورية')
        except Exception:log.exception('Scheduled backup failed')

async def check_configuration():
    if not USER_BOT_TOKEN or not ADMIN_BOT_TOKEN or not OWNER_ID_RAW:raise RuntimeError('املأ USER_BOT_TOKEN وADMIN_BOT_TOKEN وOWNER_ID في البيئة/.env')
    if USER_BOT_TOKEN==ADMIN_BOT_TOKEN:raise RuntimeError('يجب استخدام Token مختلف لكل بوت')
    try:owner=int(OWNER_ID_RAW);hours=int(BACKUP_INTERVAL_HOURS);ZoneInfo(TIMEZONE)
    except (ValueError,ZoneInfoNotFoundError) as e:raise RuntimeError('تحقق من OWNER_ID وBACKUP_INTERVAL_HOURS وTIMEZONE') from e
    if owner<=0 or hours<=0:raise RuntimeError('OWNER_ID وBACKUP_INTERVAL_HOURS يجب أن يكونا موجبين')
    return {'owner':owner,'timezone':TIMEZONE,'hours':hours,'proof_channel':PROOF_CHANNEL_ID,'user_username':''}

async def run():
    settings=await check_configuration();db=StoreDB(DATABASE_PATH);await db.init()
    if '--check' in sys.argv:
        async with await db.open() as con:
            result=await (await con.execute('PRAGMA integrity_check')).fetchone()
            if result[0]!='ok':raise RuntimeError('قاعدة البيانات غير سليمة')
        print(f'PLAY POINT DB OK: {DATABASE_PATH}');return
    user_bot=Bot(USER_BOT_TOKEN);admin_bot=Bot(ADMIN_BOT_TOKEN)
    try:
        settings['user_username']=(await user_bot.get_me()).username
        user_dp=Dispatcher();user_dp.include_router(user_router(db,settings))
        admin_dp=Dispatcher();admin_dp.include_router(admin_router(db,settings))
        backup_task=asyncio.create_task(scheduled_backups(db,admin_bot,settings['owner'],settings['hours']))
        try:await asyncio.gather(user_dp.start_polling(user_bot,drop_pending_updates=False),admin_dp.start_polling(admin_bot,drop_pending_updates=False))
        finally:backup_task.cancel();await asyncio.gather(backup_task,return_exceptions=True)
    finally:await user_bot.session.close();await admin_bot.session.close()

if __name__=='__main__':
    try:asyncio.run(run())
    except KeyboardInterrupt:log.info('PLAY POINT stopped by operator')
    except Exception:log.exception('Startup/runtime failure');raise
