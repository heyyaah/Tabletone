"""Add new models: BlockedUser, GroupRole, SecretChat + migrations"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

# 1. Add new models before "with app.app_context():"
new_models = '''
# ── Блокировка пользователей ──────────────────────────────────────────────────
class BlockedUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'blocked_id', name='_block_uc'),)
    user = db.relationship('User', foreign_keys=[user_id])
    blocked = db.relationship('User', foreign_keys=[blocked_id])

# ── Роли в группах ────────────────────────────────────────────────────────────
class GroupRole(db.Model):
    """Кастомная роль в группе (цвет, название)."""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    color = db.Column(db.String(7), default='#667eea')
    permissions = db.Column(db.Text, default='{}')  # JSON
    order_index = db.Column(db.Integer, default=0)
    group = db.relationship('Group', foreign_keys=[group_id])

class GroupMemberRole(db.Model):
    """Назначение роли участнику группы."""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('group_role.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='_gmember_role_uc'),)

# ── Секретные чаты ────────────────────────────────────────────────────────────
class SecretChat(db.Model):
    """Секретный чат между двумя пользователями."""
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    key_hash = db.Column(db.String(200))  # хэш общего ключа для верификации
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    user1 = db.relationship('User', foreign_keys=[user1_id])
    user2 = db.relationship('User', foreign_keys=[user2_id])

class SecretMessage(db.Model):
    """Сообщение в секретном чате (хранится зашифрованным)."""
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('secret_chat.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content_encrypted = db.Column(db.Text, nullable=False)  # base64 зашифрованный текст
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    self_destruct_seconds = db.Column(db.Integer, default=0)  # 0 = не уничтожать
    destroyed_at = db.Column(db.DateTime)
    chat = db.relationship('SecretChat', foreign_keys=[chat_id])
    sender = db.relationship('User', foreign_keys=[sender_id])

# ── Медленный режим — last_message per user ───────────────────────────────────
# (уже есть SlowModeTracker)

# ── Закреплённые сообщения в группах (расширение) ────────────────────────────
# (уже есть PinnedMessage)

'''

insert_before = 'with app.app_context():'
src = src.replace(insert_before, new_models + insert_before, 1)

# 2. Add migrations for new tables
pg_new = '''            'ALTER TABLE "group" ADD COLUMN IF NOT EXISTS pinned_message_id INTEGER',
            f'ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS last_seen_visible BOOLEAN DEFAULT TRUE',
            f'ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS blocked_users_list TEXT DEFAULT \'[]\'',
            'ALTER TABLE group_member ADD COLUMN IF NOT EXISTS role_title VARCHAR(50)',
            'ALTER TABLE group_member ADD COLUMN IF NOT EXISTS slow_mode_until TIMESTAMP',
            'ALTER TABLE message ADD COLUMN IF NOT EXISTS is_secret BOOLEAN DEFAULT FALSE',
            'ALTER TABLE message ADD COLUMN IF NOT EXISTS secret_chat_id INTEGER',
'''
sqlite_new = '''            "ALTER TABLE 'group' ADD COLUMN pinned_message_id INTEGER",
            f"ALTER TABLE {user_table} ADD COLUMN last_seen_visible BOOLEAN DEFAULT 1",
            f"ALTER TABLE {user_table} ADD COLUMN blocked_users_list TEXT DEFAULT '[]'",
            "ALTER TABLE group_member ADD COLUMN role_title VARCHAR(50)",
            "ALTER TABLE group_member ADD COLUMN slow_mode_until DATETIME",
            "ALTER TABLE message ADD COLUMN is_secret BOOLEAN DEFAULT 0",
            "ALTER TABLE message ADD COLUMN secret_chat_id INTEGER",
'''

# Insert before the closing bracket of postgres migrations list
pg_marker = "            'ALTER TABLE message ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE',\n        ]"
src = src.replace(pg_marker, "            'ALTER TABLE message ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE',\n" + pg_new + "        ]", 1)

sqlite_marker = '            "ALTER TABLE message ADD COLUMN is_read BOOLEAN DEFAULT 0",\n        ]'
src = src.replace(sqlite_marker, '            "ALTER TABLE message ADD COLUMN is_read BOOLEAN DEFAULT 0",\n' + sqlite_new + "        ]", 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print("fix1 done")
