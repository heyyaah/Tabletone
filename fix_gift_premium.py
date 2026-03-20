"""
Миграция: расшифровывает content у всех gift_premium сообщений в БД.
Запуск: python fix_gift_premium.py
"""
import os
import base64
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Укажи SECRET_KEY от сервера ──────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    SECRET_KEY = input('Введи SECRET_KEY: ').strip()

def get_key():
    raw = os.environ.get('MESSAGE_ENCRYPTION_KEY', '')
    if raw:
        try:
            k = bytes.fromhex(raw)
            if len(k) == 32: return k
        except ValueError:
            pass
        try:
            k = base64.b64decode(raw)
            if len(k) == 32: return k
        except Exception:
            pass
    return hashlib.sha256(SECRET_KEY.encode()).digest()

def decrypt(token: str) -> str:
    try:
        key = get_key()
        raw = base64.b64decode(token)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode('utf-8')
    except Exception:
        return None  # уже не зашифровано или другой ключ

# ── Подключение к БД ─────────────────────────────────────────────────────────
DB_PATH = os.environ.get('DATABASE_URL', 'sqlite:///instance/messenger.db')
if DB_PATH.startswith('sqlite:///'):
    import sqlite3
    db_file = DB_PATH.replace('sqlite:///', '')
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM message WHERE message_type = 'gift_premium'")
    rows = cur.fetchall()
    fixed = 0
    for msg_id, content in rows:
        if not content:
            continue
        decrypted = decrypt(content)
        if decrypted and decrypted != content:
            cur.execute("UPDATE message SET content = ? WHERE id = ?", (decrypted, msg_id))
            print(f"  ✅ #{msg_id}: {decrypted}")
            fixed += 1
        else:
            print(f"  ⏭  #{msg_id}: уже читаемый или не расшифровать")
    conn.commit()
    conn.close()
    print(f"\nГотово: исправлено {fixed} из {len(rows)} сообщений.")
else:
    # PostgreSQL
    import psycopg2
    conn = psycopg2.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM message WHERE message_type = 'gift_premium'")
    rows = cur.fetchall()
    fixed = 0
    for msg_id, content in rows:
        if not content:
            continue
        decrypted = decrypt(content)
        if decrypted and decrypted != content:
            cur.execute("UPDATE message SET content = %s WHERE id = %s", (decrypted, msg_id))
            print(f"  ✅ #{msg_id}: {decrypted}")
            fixed += 1
        else:
            print(f"  ⏭  #{msg_id}: уже читаемый или не расшифровать")
    conn.commit()
    cur.close()
    conn.close()
    print(f"\nГотово: исправлено {fixed} из {len(rows)} сообщений.")
