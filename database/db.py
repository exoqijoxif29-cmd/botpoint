from __future__ import annotations
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


SQL_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    balance INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_banned INTEGER NOT NULL DEFAULT 0,
    referral_count INTEGER NOT NULL DEFAULT 0,
    referral_total INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    flag TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_id INTEGER NOT NULL,
    invited_id INTEGER NOT NULL UNIQUE,
    reward INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(inviter_id) REFERENCES users(id),
    FOREIGN KEY(invited_id) REFERENCES users(id),
    CHECK(inviter_id != invited_id)
);

CREATE TABLE IF NOT EXISTS point_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT,
    order_id INTEGER,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    app1 TEXT,
    app2 TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'pending_review',
    rejection_reason TEXT,
    refunded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    proof_message_id TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(server_id) REFERENCES servers(id)
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS mandatory_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    channel_id TEXT NOT NULL UNIQUE,
    invite_url TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(ticket_id) REFERENCES support_tickets(id)
);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS email_weekly_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    week_start TEXT NOT NULL,
    order_id INTEGER,
    UNIQUE(email, week_start)
);

CREATE TABLE IF NOT EXISTS drafts (
    user_id INTEGER PRIMARY KEY,
    server_id INTEGER,
    step TEXT NOT NULL,
    app1 TEXT,
    app2 TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS proof_posts (
    order_id INTEGER PRIMARY KEY,
    message_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_error TEXT,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS backup_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    result TEXT NOT NULL,
    path TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_id ON users(id);
CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_support_ticket ON support_messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON point_transactions(user_id);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self):
        with self._connect() as conn:
            conn.executescript(SQL_SCHEMA)
            defaults = {
                "referral_reward": "10",
                "maintenance": "0",
                "join_text": "يرجى الاشتراك بالقنوات ثم اضغط تحقق من الاشتراك ✅",
                "support_message": "تم استلام طلبك.",
                "proof_message": "✅ تم استلام طلبك!\n━━━━━━━━━━━━━━\n🔢 رقم الطلب: #",
            }
            for key, value in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (key, value),
                )
            conn.execute(
                "INSERT OR IGNORE INTO servers(name, flag, price, active) VALUES (?, ?, ?, 1)",
                ("الأمريكي", "🇺🇸", 0),
            )
            conn.execute(
                "INSERT OR IGNORE INTO servers(name, flag, price, active) VALUES (?, ?, ?, 1)",
                ("الياباني", "🇯🇵", 0),
            )
            conn.execute(
                "INSERT OR IGNORE INTO servers(name, flag, price, active) VALUES (?, ?, ?, 1)",
                ("البريطاني", "🇬🇧", 0),
            )
            conn.execute(
                "INSERT OR IGNORE INTO servers(name, flag, price, active) VALUES (?, ?, ?, 1)",
                ("الألماني", "🇩🇪", 0),
            )
            conn.commit()

    def ensure_user(self, user_id: int, username: str | None, full_name: str | None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users(id, username, full_name, balance) VALUES (?, ?, ?, 0) ON CONFLICT(id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
                (user_id, username, full_name),
            )
            conn.commit()

    def get_user(self, user_id: int):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def list_servers(self, only_active: bool = True):
        with self._connect() as conn:
            if only_active:
                rows = conn.execute("SELECT * FROM servers WHERE active = 1 ORDER BY id").fetchall()
            else:
                rows = conn.execute("SELECT * FROM servers ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def upsert_server(self, name: str, flag: str, price: int, active: int = 1):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO servers(name, flag, price, active) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, flag=excluded.flag, price=excluded.price, active=excluded.active",
                (name, flag, price, active),
            )
            conn.commit()
            return cur.lastrowid

    def has_active_order(self, user_id: int):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM orders WHERE user_id = ? AND status IN ('pending_review', 'accepted', 'in_progress') ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return bool(row)

    def create_order_atomic(self, user_id: int, server_id: int, app1: str, app2: str, email: str, week_start: str):
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                server = conn.execute("SELECT * FROM servers WHERE id = ? AND active = 1", (server_id,)).fetchone()
                if not server:
                    raise ValueError("السيرفر غير متاح.")
                user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                if not user:
                    raise ValueError("المستخدم غير موجود.")
                if user["balance"] < server["price"]:
                    raise ValueError("الرصيد غير كافٍ.")
                if conn.execute(
                    "SELECT 1 FROM orders WHERE user_id = ? AND status IN ('pending_review', 'accepted', 'in_progress') LIMIT 1",
                    (user_id,),
                ).fetchone():
                    raise ValueError("لديك طلب معلّق بالفعل.")
                if conn.execute(
                    "SELECT 1 FROM email_weekly_usage WHERE email = ? AND week_start = ? LIMIT 1",
                    (email.lower(), week_start),
                ).fetchone():
                    raise ValueError("هذا البريد مستخدم في نفس الأسبوع.")

                cur = conn.execute(
                    "INSERT INTO orders(user_id, server_id, price, app1, app2, email, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending_review', datetime('now'))",
                    (user_id, server_id, server["price"], app1, app2, email.lower()),
                )
                order_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO email_weekly_usage(email, week_start, order_id) VALUES (?, ?, ?)",
                    (email.lower(), week_start, order_id),
                )
                conn.execute(
                    "UPDATE users SET balance = balance - ? WHERE id = ?",
                    (server["price"], user_id),
                )
                conn.execute(
                    "INSERT INTO point_transactions(user_id, amount, kind, reason, order_id, actor) VALUES (?, ?, 'spend', 'خصم النقاط لطلب جديد', ?, 'system')",
                    (user_id, -server["price"], order_id),
                )
                conn.execute(
                    "INSERT INTO order_events(order_id, old_status, new_status, actor, reason) VALUES (?, NULL, 'pending_review', 'system', 'إنشاء الطلب')",
                    (order_id,),
                )
                conn.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))
                conn.commit()
                return order_id
            except Exception:
                conn.rollback()
                raise

    def change_order_status(self, order_id: int, new_status: str, actor: str, reason: str | None = None):
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if not order:
                    raise ValueError("الطلب غير موجود")
                allowed = {
                    "pending_review": {"accepted", "rejected", "cancelled"},
                    "accepted": {"in_progress", "rejected"},
                    "in_progress": {"completed", "rejected"},
                    "completed": set(),
                    "rejected": set(),
                    "cancelled": set(),
                }
                if new_status not in allowed.get(order["status"], set()):
                    raise ValueError("لا يمكن الانتقال إلى هذه الحالة.")

                if new_status == "rejected" and order["refunded"] == 0:
                    conn.execute(
                        "UPDATE users SET balance = balance + ? WHERE id = ?",
                        (order["price"], order["user_id"]),
                    )
                    conn.execute(
                        "INSERT INTO point_transactions(user_id, amount, kind, reason, order_id, actor) VALUES (?, ?, 'refund', ?, ?, ?)",
                        (order["user_id"], order["price"], reason or "رفض الطلب", order_id, actor),
                    )
                    conn.execute(
                        "UPDATE orders SET refunded = 1 WHERE id = ?",
                        (order_id,),
                    )
                conn.execute(
                    "UPDATE orders SET status = ?, rejection_reason = ?, updated_at = datetime('now') WHERE id = ?",
                    (new_status, reason, order_id),
                )
                conn.execute(
                    "INSERT INTO order_events(order_id, old_status, new_status, actor, reason) VALUES (?, ?, ?, ?, ?)",
                    (order_id, order["status"], new_status, actor, reason),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def list_orders_for_user(self, user_id: int):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT o.*, s.name AS server_name, s.flag AS server_flag FROM orders o LEFT JOIN servers s ON s.id = o.server_id WHERE o.user_id = ? ORDER BY o.id DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def process_referral(self, inviter_id: int, invited_id: int):
        if inviter_id == invited_id:
            return False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute("SELECT 1 FROM referrals WHERE invited_id = ?", (invited_id,)).fetchone()
                if existing:
                    conn.rollback()
                    return False
                reward = int(self.get_setting("referral_reward", "10"))
                inviter = conn.execute("SELECT * FROM users WHERE id = ?", (inviter_id,)).fetchone()
                if not inviter:
                    conn.rollback()
                    return False
                conn.execute(
                    "INSERT INTO referrals(inviter_id, invited_id, reward) VALUES (?, ?, ?)",
                    (inviter_id, invited_id, reward),
                )
                conn.execute(
                    "UPDATE users SET balance = balance + ?, referral_count = referral_count + 1, referral_total = referral_total + ? WHERE id = ?",
                    (reward, reward, inviter_id),
                )
                conn.execute(
                    "INSERT INTO point_transactions(user_id, amount, kind, reason, actor) VALUES (?, ?, 'referral', 'مكافأة إحالة', 'system')",
                    (inviter_id, reward),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def create_support_ticket(self, user_id: int, body: str):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO support_tickets(user_id, status) VALUES (?, 'open')",
                (user_id,),
            )
            ticket_id = cur.lastrowid
            conn.execute(
                "INSERT INTO support_messages(ticket_id, sender_id, body) VALUES (?, ?, ?)",
                (ticket_id, user_id, body),
            )
            conn.commit()
            return ticket_id

    def add_support_reply(self, ticket_id: int, sender_id: int, body: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO support_messages(ticket_id, sender_id, body) VALUES (?, ?, ?)",
                (ticket_id, sender_id, body),
            )
            conn.commit()

    def add_suggestion(self, user_id: int, body: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO suggestions(user_id, body) VALUES (?, ?)",
                (user_id, body),
            )
            conn.commit()

    def save_draft(self, user_id: int, server_id: int, step: str, app1: str | None = None, app2: str | None = None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO drafts(user_id, server_id, step, app1, app2, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id) DO UPDATE SET server_id=excluded.server_id, step=excluded.step, app1=excluded.app1, app2=excluded.app2, updated_at=datetime('now')",
                (user_id, server_id, step, app1, app2),
            )
            conn.commit()

    def get_draft(self, user_id: int):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def record_email_usage(self, email: str, week_start: str, order_id: int):
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT 1 FROM email_weekly_usage WHERE email = ? AND week_start = ? LIMIT 1",
                    (email.lower(), week_start),
                ).fetchone()
                if row:
                    raise ValueError("الايميل مستخدم في نفس الأسبوع")
                conn.execute(
                    "INSERT INTO email_weekly_usage(email, week_start, order_id) VALUES (?, ?, ?)",
                    (email.lower(), week_start, order_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_backup(self, backup_dir: str):
        backup_path = Path(backup_dir)
        backup_path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dest = backup_path / f"play_point_{stamp}.db"
        shutil.copy2(self.db_path, dest)
        with self._connect() as conn:
            conn.execute("INSERT INTO backup_logs(created_at, result, path) VALUES (?, 'ok', ?)", (datetime.utcnow().isoformat(), str(dest)))
            conn.commit()
        return str(dest)

    def restore_backup(self, backup_path: str, verify: bool = True):
        if verify:
            with sqlite3.connect(backup_path) as conn:
                res = conn.execute("PRAGMA integrity_check").fetchone()
                if not res or res[0] != "ok":
                    raise ValueError("النسخة الاحتياطية تالفة.")
        shutil.copy2(backup_path, self.db_path)
        return True

    def save_proof_post(self, order_id: int, message_id: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO proof_posts(order_id, message_id, status) VALUES (?, ?, 'posted') ON CONFLICT(order_id) DO UPDATE SET message_id = excluded.message_id, status = 'posted'",
                (order_id, str(message_id)),
            )
            conn.commit()

    def get_proof_post(self, order_id: int):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM proof_posts WHERE order_id = ?", (order_id,)).fetchone()
            return dict(row) if row else None

    def is_owner(self, user_id: int, owner_id: int) -> bool:
        return user_id == owner_id

    def list_users(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def add_admin_log(self, actor_id: int, action: str, target: str | None = None, reason: str | None = None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO admin_audit_logs(actor_id, action, target, reason) VALUES (?, ?, ?, ?)",
                (actor_id, action, target, reason),
            )
            conn.commit()

    def get_order(self, order_id: int):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            return dict(row) if row else None

    def add_channel(self, name: str, channel_id: str, invite_url: str, active: int = 1):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mandatory_channels(name, channel_id, invite_url, active) VALUES (?, ?, ?, ?) ON CONFLICT(channel_id) DO UPDATE SET name=excluded.name, invite_url=excluded.invite_url, active=excluded.active",
                (name, channel_id, invite_url, active),
            )
            conn.commit()

    def list_channels(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM mandatory_channels ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def close(self):
        return


DEFAULT_DB_PATH = "./data/play_point.db"
