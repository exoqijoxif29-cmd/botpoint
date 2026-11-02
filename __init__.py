import os
import sqlite3

import pytest

from database.db import Database


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "play_point.db"
    db = Database(str(path))
    db.initialize()
    return str(path)


@pytest.mark.asyncio
async def test_new_user_creation(db_path):
    db = Database(db_path)
    db.ensure_user(1001, "user1", "User One")
    user = db.get_user(1001)
    assert user["id"] == 1001
    assert user["balance"] == 0


@pytest.mark.asyncio
async def test_referral_reward_once(db_path):
    db = Database(db_path)
    db.ensure_user(2001, "inv", "Inviter")
    db.ensure_user(2002, "dub", "Invitee")
    assert db.process_referral(2001, 2002) is True
    assert db.process_referral(2001, 2002) is False
    user = db.get_user(2001)
    assert user["balance"] == 10


@pytest.mark.asyncio
async def test_self_referral_rejected(db_path):
    db = Database(db_path)
    db.ensure_user(3001, "me", "Same")
    assert db.process_referral(3001, 3001) is False


@pytest.mark.asyncio
async def test_order_deducts_and_persists(db_path):
    db = Database(db_path)
    db.ensure_user(4001, "buyer", "Buyer")
    db.set_setting("referral_reward", "10")
    db.ensure_user(4002, "server", "Server")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE servers SET price = 5 WHERE flag = '🇺🇸'")
    conn.commit()
    conn.close()

    db.get_user(4001)["balance"]
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET balance = 10 WHERE id = 4001")
    conn.commit()
    conn.close()

    order_id = db.create_order_atomic(4001, 1, "app1", "app2", "buyer@gmail.com", "2026-01-05")
    assert order_id is not None
    assert db.get_user(4001)["balance"] == 5

    db2 = Database(db_path)
    assert db2.get_user(4001)["balance"] == 5


@pytest.mark.asyncio
async def test_insufficient_funds_blocked(db_path):
    db = Database(db_path)
    db.ensure_user(5001, "poor", "Poor")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET balance = 0 WHERE id = 5001")
    conn.execute("UPDATE servers SET price = 5 WHERE flag = '🇺🇸'")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError):
        db.create_order_atomic(5001, 1, "app1", "app2", "poor@gmail.com", "2026-01-05")


@pytest.mark.asyncio
async def test_only_one_pending_order(db_path):
    db = Database(db_path)
    db.ensure_user(6001, "one", "One")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET balance = 50 WHERE id = 6001")
    conn.execute("UPDATE servers SET price = 5 WHERE flag = '🇺🇸'")
    conn.commit()
    conn.close()
    db.create_order_atomic(6001, 1, "a", "b", "one1@gmail.com", "2026-01-06")
    with pytest.raises(ValueError):
        db.create_order_atomic(6001, 1, "x", "y", "one2@gmail.com", "2026-01-07")


@pytest.mark.asyncio
async def test_repeated_gmail_week_blocked(db_path):
    db = Database(db_path)
    db.ensure_user(7001, "mail", "Mail")
    db.record_email_usage("same@gmail.com", "2026-01-05", 1)
    with pytest.raises(ValueError):
        db.record_email_usage("same@gmail.com", "2026-01-05", 2)


@pytest.mark.asyncio
async def test_refund_once_only(db_path):
    db = Database(db_path)
    db.ensure_user(8001, "buyer", "Buyer")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET balance = 10 WHERE id = 8001")
    conn.execute("UPDATE servers SET price = 5 WHERE flag = '🇺🇸'")
    conn.commit()
    conn.close()
    order_id = db.create_order_atomic(8001, 1, "app1", "app2", "buyer@gmail.com", "2026-01-08")
    db.change_order_status(order_id, "rejected", "owner", "wrong link")
    assert db.get_user(8001)["balance"] == 10
    with pytest.raises(ValueError):
        db.change_order_status(order_id, "rejected", "owner", "wrong link again")


@pytest.mark.asyncio
async def test_backup_and_restore(db_path):
    db = Database(db_path)
    db.ensure_user(9001, "u", "U")
    backup = db.create_backup(str(os.path.join(os.path.dirname(db_path), "backups")))
    assert os.path.exists(backup)
    db2 = Database(db_path)
    assert db2.get_user(9001) is not None


@pytest.mark.asyncio
async def test_proof_post_not_duplicated(db_path):
    db = Database(db_path)
    db.ensure_user(10001, "u", "U")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET balance = 10 WHERE id = 10001")
    conn.execute("UPDATE servers SET price = 5 WHERE flag = '🇺🇸'")
    conn.commit()
    conn.close()
    order_id = db.create_order_atomic(10001, 1, "a", "b", "user10001@gmail.com", "2026-01-09")
    db.save_proof_post(order_id, "msg_123")
    db.save_proof_post(order_id, "msg_456")
    assert db.get_proof_post(order_id)["message_id"] == "msg_456"
