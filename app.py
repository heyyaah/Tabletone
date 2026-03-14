import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from sqlalchemy import case
import os
import json
import time
import random
import secrets
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///messenger.db')
# Render даёт postgres://, SQLAlchemy требует postgresql://
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql+pg8000://', 1)
elif _db_url.startswith('postgresql://'):
    _db_url = _db_url.replace('postgresql://', 'postgresql+pg8000://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/media'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['PROPAGATE_EXCEPTIONS'] = True

# Разрешенные расширения
ALLOWED_AUDIO = {'webm', 'ogg', 'mp3', 'wav'}
ALLOWED_VIDEO = {'webm', 'mp4'}
ALLOWED_IMAGES = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'pdf', 'doc', 'docx', 'txt'}

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Rate limiter — защита от DDoS
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per minute", "30 per second"],
    storage_uri="memory://"
)

# Словарь для отслеживания онлайн пользователей
online_users = {}

# Список жалоб (in-memory)
reports = []

# Глобальный обработчик ошибок для подавления ошибок разрыва соединения
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Слишком много запросов. Подождите немного.'}), 429

@app.errorhandler(Exception)
def handle_exception(e):
    from jinja2 import TemplateNotFound
    # Не перехватываем ошибки шаблонов — пусть Flask показывает нормальную страницу ошибки
    if isinstance(e, TemplateNotFound):
        raise e
    # Игнорируем ошибки разрыва соединения (нормально для видео/аудио стриминга)
    if isinstance(e, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
        return '', 200
    # Для других ошибок возвращаем JSON
    return jsonify({'error': str(e)}), 500

# Middleware для обновления last_seen при каждом запросе
@app.before_request
def update_last_seen():
    if 'user_id' in session and request.endpoint not in ['static', None]:
        try:
            user_id = session['user_id']
            
            # Проверяем когда последний раз обновляли (используем глобальный словарь вместо session)
            if not hasattr(app, 'last_seen_cache'):
                app.last_seen_cache = {}
            
            current_time = time.time()
            last_update = app.last_seen_cache.get(user_id, 0)
            
            # Обновляем раз в 30 секунд
            if current_time - last_update > 30:
                user = User.query.get(user_id)
                if user:
                    user.last_seen = datetime.utcnow()
                    db.session.commit()
                    app.last_seen_cache[user_id] = current_time
                    print(f"✓ Updated last_seen for user {user_id}: {user.last_seen}")
        except Exception as e:
            print(f"Error updating last_seen: {e}")
            pass  # Игнорируем ошибки обновления last_seen

# Модели базы данных
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    display_name = db.Column(db.String(100))
    bio = db.Column(db.String(200))
    avatar_color = db.Column(db.String(7), default='#667eea')
    avatar_url = db.Column(db.String(500))  # URL загруженной аватарки
    theme = db.Column(db.String(20), default='light')  # light, dark, liquid
    chat_wallpaper = db.Column(db.String(50), default='default')  # Обои для чата
    is_verified = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    admin_role = db.Column(db.String(20), default=None)  # moderator, admin, senior_admin, owner
    is_banned = db.Column(db.Boolean, default=False)
    is_premium = db.Column(db.Boolean, default=False)
    is_spam_blocked = db.Column(db.Boolean, default=False)
    spam_block_until = db.Column(db.DateTime, nullable=True)
    premium_emoji = db.Column(db.String(10))  # Эмодзи для премиум пользователей
    timezone = db.Column(db.String(50), default='Europe/Moscow')
    is_bot = db.Column(db.Boolean, default=False)  # Является ли аккаунт ботом
    two_fa_enabled = db.Column(db.Boolean, default=False)  # Двухэтапная аутентификация
    two_fa_code = db.Column(db.String(8))           # Текущий код 2FA
    two_fa_code_expires = db.Column(db.DateTime)    # Срок действия кода
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_avatar_letter(self):
        return (self.display_name or self.username)[0].upper()

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default='text')  # text, voice, video_note
    media_url = db.Column(db.String(500))  # URL для медиа файлов
    duration = db.Column(db.Integer)  # Длительность для голосовых и видео
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    edited_at = db.Column(db.DateTime)
    is_deleted = db.Column(db.Boolean, default=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    bot_buttons = db.Column(db.Text, default='[]')  # JSON кнопки бота
    
    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    reply_to = db.relationship('Message', foreign_keys=[reply_to_id], remote_side='Message.id')

    media_files = db.relationship('MessageMedia', backref='message', lazy=True, cascade='all, delete-orphan')

# Модель для множественных медиа файлов в одном сообщении
class MessageMedia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)  # image, file, video
    media_url = db.Column(db.String(500), nullable=False)
    file_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    order_index = db.Column(db.Integer, default=0)

class GroupMessageMedia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)  # image, file, video
    media_url = db.Column(db.String(500), nullable=False)
    file_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    order_index = db.Column(db.Integer, default=0)

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=True)
    description = db.Column(db.String(500))
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    avatar_color = db.Column(db.String(7), default='#667eea')
    avatar_url = db.Column(db.String(500))
    is_channel = db.Column(db.Boolean, default=False)
    is_public = db.Column(db.Boolean, default=True)
    invite_link = db.Column(db.String(100), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    creator = db.relationship('User', foreign_keys=[creator_id])

class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_muted = db.Column(db.Boolean, default=False)  # Уведомления отключены
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    group = db.relationship('Group', foreign_keys=[group_id])
    user = db.relationship('User', foreign_keys=[user_id])

class GroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    edited_at = db.Column(db.DateTime)
    is_deleted = db.Column(db.Boolean, default=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=True)
    
    group = db.relationship('Group', foreign_keys=[group_id])
    sender = db.relationship('User', foreign_keys=[sender_id])
    reply_to = db.relationship('GroupMessage', foreign_keys=[reply_to_id], remote_side='GroupMessage.id')

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reported_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'))
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_at = db.Column(db.DateTime)
    
    reporter = db.relationship('User', foreign_keys=[reporter_id])
    reported_user = db.relationship('User', foreign_keys=[reported_user_id])
    message = db.relationship('Message', foreign_keys=[message_id])
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

class VerificationRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id])
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

class Bot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)  # Аккаунт бота
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Владелец
    token = db.Column(db.String(100), unique=True, nullable=False)  # API токен
    webhook_url = db.Column(db.String(500))  # URL для получения обновлений
    description = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    review_status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    review_note = db.Column(db.String(500))  # Комментарий модератора
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bot_user = db.relationship('User', foreign_keys=[user_id])
    owner = db.relationship('User', foreign_keys=[owner_id])

class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    session_token = db.Column(db.String(200), unique=True, nullable=False)
    device_name = db.Column(db.String(200))
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    user = db.relationship('User', foreign_keys=[user_id])

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    media_type = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    
    user = db.relationship('User', foreign_keys=[user_id])

# Модель для отслеживания последнего прочитанного сообщения в личных чатах
class LastReadMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    chat_with_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_read_message_id = db.Column(db.Integer, db.ForeignKey('message.id'))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id])
    chat_with_user = db.relationship('User', foreign_keys=[chat_with_user_id])
    
    __table_args__ = (db.UniqueConstraint('user_id', 'chat_with_user_id', name='_user_chat_uc'),)

# Модель для отслеживания последнего прочитанного сообщения в группах
class LastReadGroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    last_read_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id])
    group = db.relationship('Group', foreign_keys=[group_id])
    
    __table_args__ = (db.UniqueConstraint('user_id', 'group_id', name='_user_group_uc'),)

# Избранные сообщения (как в Telegram — "Избранное")
class FavoriteMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # Либо личное сообщение, либо групповое
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=True)
    # Сохранённый текст/тип на случай удаления оригинала
    saved_content = db.Column(db.Text)
    saved_type = db.Column(db.String(20), default='text')
    saved_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])
    message = db.relationship('Message', foreign_keys=[message_id])
    group_message = db.relationship('GroupMessage', foreign_keys=[group_message_id])

class BotCommand(db.Model):
    """Команда бота-конструктора. Если нет webhook — бот отвечает сам."""
    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey('bot.id'), nullable=False)
    trigger = db.Column(db.String(100), nullable=False)   # /start, /help, привет, *
    response_text = db.Column(db.Text, nullable=False)    # Текст ответа
    # JSON-список кнопок: [{"label": "Кнопка 1", "reply": "/start"}, ...]
    buttons = db.Column(db.Text, default='[]')
    order_index = db.Column(db.Integer, default=0)

    bot = db.relationship('Bot', foreign_keys=[bot_id])

class PasswordResetRequest(db.Model):
    """Заявка на восстановление аккаунта / сброс пароля."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    request_type = db.Column(db.String(30), default='password')  # password, 2fa_lost
    status = db.Column(db.String(20), default='pending')  # pending, resolved, rejected
    admin_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_at = db.Column(db.DateTime)

    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

class SupportTicket(db.Model):
    """Тикет поддержки — одно обращение пользователя."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message_text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='open')  # open, closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id])

class AdminApplication(db.Model):
    """Заявка на вступление в администрацию."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    experience = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    admin_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id])

class BannedIP(db.Model):
    """Заблокированный IP-адрес."""
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50), unique=True, nullable=False)
    reason = db.Column(db.String(500))
    banned_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship('User', foreign_keys=[banned_by])

# Создание таблиц
with app.app_context():
    from sqlalchemy import text

    # Создаём таблицы без новых колонок — временно отключаем email в метаданных
    # через прямой SQL чтобы избежать конфликта
    db.create_all()

    # Миграции с IF NOT EXISTS (PostgreSQL 9.6+)
    is_postgres = db.engine.dialect.name == 'postgresql'
    user_table = '"user"' if is_postgres else 'user'
    ts_type = 'TIMESTAMP' if is_postgres else 'DATETIME'

    if is_postgres:
        migrations = [
            f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS email VARCHAR(200)",
            f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS two_fa_enabled BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS two_fa_code VARCHAR(8)",
            f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS two_fa_code_expires TIMESTAMP",
            f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS admin_role VARCHAR(20)",
            "ALTER TABLE message ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES message(id)",
            "ALTER TABLE message ADD COLUMN IF NOT EXISTS bot_buttons TEXT DEFAULT '[]'",
            "ALTER TABLE group_message ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES group_message(id)",
            "ALTER TABLE password_reset_request ADD COLUMN IF NOT EXISTS request_type VARCHAR(30) DEFAULT 'password'",
        ]
    else:
        migrations = [
            f"ALTER TABLE {user_table} ADD COLUMN two_fa_enabled BOOLEAN DEFAULT 0",
            f"ALTER TABLE {user_table} ADD COLUMN two_fa_code VARCHAR(8)",
            f"ALTER TABLE {user_table} ADD COLUMN two_fa_code_expires DATETIME",
            f"ALTER TABLE {user_table} ADD COLUMN admin_role VARCHAR(20)",
            f"ALTER TABLE {user_table} ADD COLUMN email VARCHAR(200)",
            "ALTER TABLE message ADD COLUMN reply_to_id INTEGER REFERENCES message(id)",
            "ALTER TABLE message ADD COLUMN bot_buttons TEXT DEFAULT '[]'",
            "ALTER TABLE group_message ADD COLUMN reply_to_id INTEGER REFERENCES group_message(id)",
            "ALTER TABLE password_reset_request ADD COLUMN request_type VARCHAR(30) DEFAULT 'password'",
        ]

    with db.engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass

    # ── Сид: бот Tabletone Premium ──────────────────────────────────────────
    _PREMIUM_BOT_USERNAME = 'tabletone_premiumbot'
    _PREMIUM_BOT_NAME = 'Tabletone Premium'
    _PREMIUM_OWNER_ID = 1  # romancev228

    # Назначаем romancev228 роль owner (ищем по username, не по id)
    _owner = User.query.filter_by(username='romancev228').first()
    if _owner:
        if _owner.admin_role != 'owner':
            _owner.is_admin = True
            _owner.admin_role = 'owner'
            db.session.commit()
            print("✓ romancev228 назначен owner")
        # Обновляем _PREMIUM_OWNER_ID на реальный id
        _PREMIUM_OWNER_ID = _owner.id

    _pbot_user = User.query.filter_by(username=_PREMIUM_BOT_USERNAME).first()
    if not _pbot_user:
        _pbot_user = User(
            username=_PREMIUM_BOT_USERNAME,
            display_name=_PREMIUM_BOT_NAME,
            bio='Официальный бот для покупки Premium подписки',
            avatar_color='#667eea',
            is_bot=True,
            is_verified=True,
            password_hash=generate_password_hash(secrets.token_hex(32))
        )
        db.session.add(_pbot_user)
        db.session.flush()

        _pbot = Bot(
            user_id=_pbot_user.id,
            owner_id=_PREMIUM_OWNER_ID,
            token=f"{_pbot_user.id}:{secrets.token_urlsafe(32)}",
            description='Покупка Premium подписки',
            is_active=True,
            review_status='approved'
        )
        db.session.add(_pbot)
        db.session.flush()

        _start_text = (
            "👑 Привет! Я помогу оформить Premium подписку.\n\n"
            "На какой срок хотите Premium?\n\n"
            "Выберите вариант ниже 👇"
        )
        _buttons = json.dumps([
            {"label": "7 дней — 59 ₽", "reply": "/buy_7"},
            {"label": "14 дней — 99 ₽ (выгоднее!)", "reply": "/buy_14"},
            {"label": "30 дней — 149 ₽ (скидка 10%!)", "reply": "/buy_30"},
            {"label": "6 месяцев — 499 ₽ (скидка 30,7%!)", "reply": "/buy_180"},
            {"label": "Год — 799 ₽ (скидка 20%)", "reply": "/buy_365"},
        ])
        _buy_url = "https://t.me/kotakbaslife"
        _buy_text = "💳 Для оплаты перейдите по ссылке:\n" + _buy_url + "\n\nПосле оплаты напишите нам — мы активируем Premium вручную."

        _cmds = [
            BotCommand(bot_id=_pbot.id, trigger='/start', response_text=_start_text, buttons=_buttons, order_index=1),
            BotCommand(bot_id=_pbot.id, trigger='/buy_7',   response_text=f"✅ Вы выбрали: 7 дней — 59 ₽\n\n{_buy_text}",   buttons='[]', order_index=2),
            BotCommand(bot_id=_pbot.id, trigger='/buy_14',  response_text=f"✅ Вы выбрали: 14 дней — 99 ₽\n\n{_buy_text}",  buttons='[]', order_index=3),
            BotCommand(bot_id=_pbot.id, trigger='/buy_30',  response_text=f"✅ Вы выбрали: 30 дней — 149 ₽\n\n{_buy_text}", buttons='[]', order_index=4),
            BotCommand(bot_id=_pbot.id, trigger='/buy_180', response_text=f"✅ Вы выбрали: 6 месяцев — 499 ₽\n\n{_buy_text}", buttons='[]', order_index=5),
            BotCommand(bot_id=_pbot.id, trigger='/buy_365', response_text=f"✅ Вы выбрали: Год — 799 ₽\n\n{_buy_text}", buttons='[]', order_index=6),
            BotCommand(bot_id=_pbot.id, trigger='*', response_text="Напишите /start чтобы увидеть варианты подписки 👑", buttons='[]', order_index=99),
        ]
        for _c in _cmds:
            db.session.add(_c)
        db.session.commit()
        print("✓ Бот Tabletone Premium создан")

    # ── Сид: бот Tabletone (официальный, приветствие + 2FA) ─────────────────
    _TBL_USERNAME = 'tabletonebot'
    if not User.query.filter_by(username=_TBL_USERNAME).first():
        _tbl_user = User(
            username=_TBL_USERNAME,
            display_name='Tabletone',
            bio='Официальный бот мессенджера Tabletone',
            avatar_color='#667eea',
            is_bot=True, is_verified=True,
            password_hash=generate_password_hash(secrets.token_hex(32))
        )
        db.session.add(_tbl_user)
        db.session.flush()
        _tbl_bot = Bot(
            user_id=_tbl_user.id, owner_id=_PREMIUM_OWNER_ID,
            token=f"{_tbl_user.id}:{secrets.token_urlsafe(32)}",
            description='Официальный бот Tabletone', is_active=True, review_status='approved'
        )
        db.session.add(_tbl_bot)
        db.session.commit()
        print("✓ Бот Tabletone создан")

    # ── Сид: бот Tabletone Support ──────────────────────────────────────────
    _SUP_USERNAME = 'tabletone_supportbot'
    if not User.query.filter_by(username=_SUP_USERNAME).first():
        _sup_user = User(
            username=_SUP_USERNAME,
            display_name='Tabletone Support',
            bio='Поддержка мессенджера Tabletone',
            avatar_color='#38a169',
            is_bot=True, is_verified=True,
            password_hash=generate_password_hash(secrets.token_hex(32))
        )
        db.session.add(_sup_user)
        db.session.flush()
        _sup_bot = Bot(
            user_id=_sup_user.id, owner_id=_PREMIUM_OWNER_ID,
            token=f"{_sup_user.id}:{secrets.token_urlsafe(32)}",
            description='Поддержка Tabletone', is_active=True, review_status='approved'
        )
        db.session.add(_sup_bot)
        db.session.flush()
        _sup_buttons = json.dumps([
            {"label": "❓ FAQ", "reply": "/faq"},
            {"label": "💬 Написать в поддержку", "reply": "/support"},
            {"label": "📢 Официальный канал", "reply": "/channel"},
        ])
        _sup_cmds = [
            BotCommand(bot_id=_sup_bot.id, trigger='/start', order_index=1,
                response_text="👋 Привет! Я бот поддержки Tabletone.\n\nЧем могу помочь?",
                buttons=_sup_buttons),
            BotCommand(bot_id=_sup_bot.id, trigger='/faq', order_index=2,
                response_text=(
                    "❓ *Часто задаваемые вопросы*\n\n"
                    "📌 Что такое Tabletone?\n"
                    "Tabletone — современный мессенджер с поддержкой групп, каналов, ботов и медиафайлов.\n\n"
                    "📌 Как создать группу или канал?\n"
                    "Нажмите кнопку ✏️ в боковой панели → выберите «Создать группу» или «Создать канал».\n\n"
                    "📌 Что даёт Premium?\n"
                    "Premium открывает: загрузку аватара, смену обоев, неограниченное количество ботов, истории и кастомный эмодзи.\n\n"
                    "📌 Как купить Premium?\n"
                    "Напишите боту @tabletone_premiumbot или нажмите /start там.\n\n"
                    "📌 Как включить двухэтапную аутентификацию?\n"
                    "Зайдите в Профиль → раздел «Безопасность» → включите 2FA.\n\n"
                    "📌 Как создать бота?\n"
                    "Перейдите в раздел «Боты» через кнопку 🤖 в боковой панели."
                ), buttons='[]'),
            BotCommand(bot_id=_sup_bot.id, trigger='/support', order_index=3,
                response_text=(
                    "💬 Напишите ваш вопрос следующим сообщением — администрация получит его и ответит вам.\n\n"
                    "⏱ Время ответа: обычно до 24 часов."
                ), buttons='[]'),
            BotCommand(bot_id=_sup_bot.id, trigger='/channel', order_index=4,
                response_text=(
                    "📢 Официальный канал Tabletone:\n\nhttps://t.me/kotakbaslife\n\nПодписывайтесь, чтобы быть в курсе новостей!"
                ), buttons='[]'),
            BotCommand(bot_id=_sup_bot.id, trigger='*', order_index=99,
                response_text="Напишите /start чтобы увидеть меню поддержки 👋",
                buttons='[]'),
        ]
        for _c in _sup_cmds:
            db.session.add(_c)
        db.session.commit()
        print("✓ Бот Tabletone Support создан")

# ── Система ролей ────────────────────────────────────────────────────────────
ROLE_LEVELS = {'moderator': 1, 'admin': 2, 'senior_admin': 3, 'owner': 4}

def _has_role(user, min_role):
    """Проверяет, имеет ли пользователь минимальную роль."""
    if not user or not user.is_admin:
        return False
    level = ROLE_LEVELS.get(user.admin_role or '', 0)
    return level >= ROLE_LEVELS.get(min_role, 99)

# Middleware для обновления активности сессии
@app.before_request
def update_session_activity():
    # Обновляем активность только если есть токен сессии
    if 'user_id' in session and 'session_token' in session:
        try:
            user_session = UserSession.query.filter_by(
                session_token=session['session_token'],
                is_active=True
            ).first()
            if user_session:
                user_session.last_activity = datetime.utcnow()
                db.session.commit()
        except Exception as e:
            print(f"Error updating session activity: {e}")
            db.session.rollback()
    # Если нет токена сессии, но пользователь авторизован (старая сессия)
    # просто продолжаем работу без обновления активности

@app.before_request
def check_ip_ban():
    """Блокируем запросы с забаненных IP."""
    ip = request.remote_addr
    if ip and BannedIP.query.filter_by(ip_address=ip).first():
        return jsonify({'error': 'Ваш IP заблокирован.'}), 403

@app.route('/admin/reports', methods=['GET'])
def get_reports():
    """Возвращает список всех жалоб"""
    return jsonify({"reports": reports})

@app.route('/admin/report', methods=['POST'])
def create_report():
    """Создает новую жалобу"""
    data = request.json
    report = {
        "id": len(reports) + 1,
        "reported_user": data.get("username"),
        "category": data.get("category"),
        "details": data.get("details"),
        "reporter": data.get("reporter", "Anonymous"),
        "target_id": data.get("target_id"),
        "target_type": data.get("target_type"),
        "status": "new",
        "created_at": datetime.now().isoformat(),
        "resolved_at": None
    }
    reports.append(report)

    # Проверяем порог спам-блока (3 жалобы на одного пользователя)
    target_id = data.get("target_id")
    target_type = data.get("target_type")
    if target_type == 'user' and target_id:
        count = sum(1 for r in reports if r.get("target_id") == target_id and r.get("target_type") == 'user')
        if count >= 3:
            user = User.query.get(int(target_id))
            if user and not user.is_spam_blocked:
                from datetime import timedelta
                user.is_spam_blocked = True
                user.spam_block_until = datetime.utcnow() + timedelta(days=1)
                db.session.commit()

    return jsonify({"success": True, "report": report})


@app.route('/admin/report/<int:report_id>/resolve', methods=['POST'])
def resolve_report(report_id):
    """Помечает жалобу как решенную"""
    for report in reports:
        if report["id"] == report_id:
            report["status"] = "resolved"
            report["resolved_at"] = datetime.now().isoformat()
            return jsonify({"success": True})
    return jsonify({"success": False, "error": "Report not found"})

@app.route('/admin/report/<int:report_id>/reject', methods=['POST'])
def reject_report(report_id):
    """Отклоняет жалобу"""
    for report in reports:
        if report["id"] == report_id:
            report["status"] = "rejected"
            return jsonify({"success": True})
    return jsonify({"success": False, "error": "Report not found"})    

# ── Восстановление аккаунта ──────────────────────────────────────────────────

@app.route('/account/recovery', methods=['POST'])
def account_recovery():
    data = request.get_json() or {}
    username = data.get('username', '').strip().lower()
    reason = data.get('reason', '').strip()
    request_type = data.get('type', 'password')  # password | 2fa_lost
    if not username or not reason:
        return jsonify({'error': 'Заполните все поля'}), 400
    req = PasswordResetRequest(username=username, reason=reason, request_type=request_type)
    db.session.add(req)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/recovery-requests', methods=['GET'])
def admin_recovery_requests():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if not _has_role(user, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    reqs = PasswordResetRequest.query.order_by(PasswordResetRequest.created_at.desc()).all()
    return jsonify({'requests': [{
        'id': r.id,
        'username': r.username,
        'reason': r.reason,
        'request_type': r.request_type or 'password',
        'status': r.status,
        'admin_note': r.admin_note,
        'created_at': r.created_at.strftime('%d.%m.%Y %H:%M'),
        'reviewed_at': r.reviewed_at.strftime('%d.%m.%Y %H:%M') if r.reviewed_at else None,
    } for r in reqs]})

@app.route('/admin/recovery-requests/<int:req_id>/resolve', methods=['POST'])
def admin_resolve_recovery(req_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    req = PasswordResetRequest.query.get(req_id)
    if not req:
        return jsonify({'error': 'Не найдено'}), 404
    data = request.get_json() or {}
    req.status = 'resolved'
    req.admin_note = data.get('note', '').strip()
    req.reviewed_by = session['user_id']
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/recovery-requests/<int:req_id>/reject', methods=['POST'])
def admin_reject_recovery(req_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    req = PasswordResetRequest.query.get(req_id)
    if not req:
        return jsonify({'error': 'Не найдено'}), 404
    data = request.get_json() or {}
    req.status = 'rejected'
    req.admin_note = data.get('note', '').strip()
    req.reviewed_by = session['user_id']
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/users/<int:user_id>/disable_2fa', methods=['POST'])
def admin_disable_2fa(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    target = User.query.get(user_id)
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404
    target.two_fa_enabled = False
    target.two_fa_code = None
    target.two_fa_code_expires = None
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/users/<int:user_id>/set_role', methods=['POST'])
def admin_set_role(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Только owner может менять роли'}), 403
    target = User.query.get(user_id)
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if target.admin_role == 'owner' and target.id != admin.id:
        return jsonify({'error': 'Нельзя изменить роль другого owner'}), 403
    data = request.get_json() or {}
    new_role = data.get('role')  # moderator | admin | senior_admin | owner | null
    if new_role and new_role not in ROLE_LEVELS:
        return jsonify({'error': 'Неверная роль'}), 400
    target.admin_role = new_role or None
    target.is_admin = bool(new_role)
    db.session.commit()
    return jsonify({'success': True})

# ── Заявки на администратора ─────────────────────────────────────────────────

@app.route('/admin/apply', methods=['POST'])
def admin_apply():
    """Подача заявки на вступление в администрацию."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if user.is_admin:
        return jsonify({'error': 'Вы уже являетесь администратором'}), 400
    # Проверяем нет ли уже pending заявки
    existing = AdminApplication.query.filter_by(user_id=user.id, status='pending').first()
    if existing:
        return jsonify({'error': 'Заявка уже подана и ожидает рассмотрения'}), 400
    data = request.get_json() or {}
    reason = data.get('reason', '').strip()
    experience = data.get('experience', '').strip()
    if not reason:
        return jsonify({'error': 'Укажите причину'}), 400
    app_req = AdminApplication(user_id=user.id, reason=reason, experience=experience or None)
    db.session.add(app_req)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/applications', methods=['GET'])
def admin_get_applications():
    """Список заявок — только owner (romancev228)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Только owner может просматривать заявки'}), 403
    apps = AdminApplication.query.order_by(AdminApplication.created_at.desc()).all()
    return jsonify({'applications': [{
        'id': a.id,
        'user_id': a.user_id,
        'username': a.user.username,
        'display_name': a.user.display_name or a.user.username,
        'reason': a.reason,
        'experience': a.experience or '',
        'status': a.status,
        'admin_note': a.admin_note or '',
        'created_at': a.created_at.strftime('%d.%m.%Y %H:%M'),
        'reviewed_at': a.reviewed_at.strftime('%d.%m.%Y %H:%M') if a.reviewed_at else None,
    } for a in apps]})

@app.route('/admin/applications/<int:app_id>/approve', methods=['POST'])
def admin_approve_application(app_id):
    """Одобрить заявку — только owner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Только owner может принимать заявки'}), 403
    a = AdminApplication.query.get(app_id)
    if not a:
        return jsonify({'error': 'Заявка не найдена'}), 404
    data = request.get_json() or {}
    role = data.get('role', 'moderator')
    if role not in ROLE_LEVELS:
        role = 'moderator'
    a.status = 'approved'
    a.admin_note = data.get('note', '').strip() or None
    a.reviewed_at = datetime.utcnow()
    # Назначаем роль пользователю
    a.user.is_admin = True
    a.user.admin_role = role
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/applications/<int:app_id>/reject', methods=['POST'])
def admin_reject_application(app_id):
    """Отклонить заявку — только owner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Только owner может отклонять заявки'}), 403
    a = AdminApplication.query.get(app_id)
    if not a:
        return jsonify({'error': 'Заявка не найдена'}), 404
    data = request.get_json() or {}
    a.status = 'rejected'
    a.admin_note = data.get('note', '').strip() or None
    a.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})

# ── IP-баны (только owner) ────────────────────────────────────────────────────

@app.route('/admin/ip-bans', methods=['GET'])
def admin_get_ip_bans():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    bans = BannedIP.query.order_by(BannedIP.created_at.desc()).all()
    return jsonify({'bans': [{
        'id': b.id,
        'ip_address': b.ip_address,
        'reason': b.reason or '',
        'created_at': b.created_at.strftime('%d.%m.%Y %H:%M'),
        'banned_by': b.admin.username if b.admin else '?'
    } for b in bans]})

@app.route('/admin/ip-bans/add', methods=['POST'])
def admin_add_ip_ban():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    data = request.get_json() or {}
    ip = data.get('ip', '').strip()
    if not ip:
        return jsonify({'error': 'Укажите IP'}), 400
    if BannedIP.query.filter_by(ip_address=ip).first():
        return jsonify({'error': 'Этот IP уже заблокирован'}), 400
    ban = BannedIP(ip_address=ip, reason=data.get('reason', '').strip() or None, banned_by=admin.id)
    db.session.add(ban)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/ip-bans/<int:ban_id>/remove', methods=['POST'])
def admin_remove_ip_ban(ban_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    ban = BannedIP.query.get(ban_id)
    if not ban:
        return jsonify({'error': 'Не найдено'}), 404
    db.session.delete(ban)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/run-migrations', methods=['POST'])
def admin_run_migrations():
    """Выполняет миграции БД — только owner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    from sqlalchemy import text
    results = []
    migrations = [
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS email VARCHAR(200)',
    ]
    with db.engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                results.append(f'OK: {sql}')
            except Exception as e:
                results.append(f'SKIP: {e}')
    return jsonify({'results': results})

@app.route('/admin/dialogs', methods=['GET'])
def admin_get_dialogs():
    """Список всех личных диалогов — только owner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    # Получаем уникальные пары пользователей
    from sqlalchemy import func, or_, and_
    pairs = db.session.query(
        func.min(Message.sender_id, Message.receiver_id).label('u1'),
        func.max(Message.sender_id, Message.receiver_id).label('u2'),
        func.max(Message.id).label('last_msg_id'),
        func.count(Message.id).label('msg_count')
    ).filter(Message.is_deleted == False).group_by(
        func.min(Message.sender_id, Message.receiver_id),
        func.max(Message.sender_id, Message.receiver_id)
    ).order_by(func.max(Message.id).desc()).limit(200).all()

    result = []
    for p in pairs:
        u1 = User.query.get(p.u1)
        u2 = User.query.get(p.u2)
        last_msg = Message.query.get(p.last_msg_id)
        if not u1 or not u2:
            continue
        result.append({
            'user1': {'id': u1.id, 'username': u1.username, 'display_name': u1.display_name or u1.username},
            'user2': {'id': u2.id, 'username': u2.username, 'display_name': u2.display_name or u2.username},
            'msg_count': p.msg_count,
            'last_message': last_msg.content[:80] if last_msg and not last_msg.is_deleted else '[удалено]',
            'last_time': last_msg.timestamp.strftime('%d.%m.%Y %H:%M') if last_msg else '',
        })
    return jsonify({'dialogs': result})

@app.route('/admin/dialogs/<int:u1>/<int:u2>', methods=['GET'])
def admin_get_dialog_messages(u1, u2):
    """Сообщения конкретного диалога — только owner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    msgs = Message.query.filter(
        ((Message.sender_id == u1) & (Message.receiver_id == u2)) |
        ((Message.sender_id == u2) & (Message.receiver_id == u1))
    ).order_by(Message.timestamp.asc()).limit(500).all()
    user1 = User.query.get(u1)
    user2 = User.query.get(u2)
    return jsonify({
        'user1': {'id': u1, 'username': user1.username if user1 else '?', 'display_name': (user1.display_name or user1.username) if user1 else '?'},
        'user2': {'id': u2, 'username': user2.username if user2 else '?', 'display_name': (user2.display_name or user2.username) if user2 else '?'},
        'messages': [{
            'id': m.id,
            'sender_id': m.sender_id,
            'content': m.content if not m.is_deleted else '[удалено]',
            'message_type': m.message_type or 'text',
            'media_url': m.media_url,
            'timestamp': m.timestamp.strftime('%d.%m.%Y %H:%M'),
            'is_deleted': m.is_deleted,
            'edited_at': m.edited_at.strftime('%H:%M') if m.edited_at else None,
        } for m in msgs]
    })
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get( session['user_id'])
    
    # Проверяем, не забанен ли пользователь
    if user and user.is_banned:
        session.pop('user_id', None)
        return redirect(url_for('login'))
    
    return render_template('index.html', user=user)

# Регистрация
@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 3 per second")
def register():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']
        display_name = request.form.get('display_name', '').strip()
        
        # Проверка на существование пользователя
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Пользователь с таким именем уже существует')
        
        # Генерация случайного цвета для аватара
        import random
        colors = ['#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b', '#fa709a', '#fee140', '#30cfd0']
        
        user = User(
            username=username,
            display_name=display_name or username,
            avatar_color=random.choice(colors),
            timezone=request.form.get('timezone', 'Europe/Moscow'),
            email=request.form.get('email', '').strip() or None
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        session['user_id'] = user.id

        # Приветственное сообщение от официального бота Tabletone
        try:
            _send_tabletone_welcome(user.id)
        except Exception as e:
            print(f"Welcome message error: {e}")

        return redirect(url_for('index'))
    
    return render_template('register.html')

# Вход
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("20 per minute; 5 per second")
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Проверяем, не забанен ли пользователь
            if user.is_banned:
                return render_template('login.html', error='Ваш аккаунт заблокирован администратором')
            
            # Проверяем 2FA
            if user.two_fa_enabled:
                code = str(random.randint(100000, 999999))
                from datetime import timedelta
                user.two_fa_code = code
                user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=10)
                db.session.commit()
                # Отправляем код через tabletonebot
                try:
                    _send_2fa_code(user.id, code)
                except Exception as e:
                    print(f"2FA send error: {e}")
                # Сохраняем user_id во временной сессии для верификации
                session['2fa_pending_user_id'] = user.id
                return redirect(url_for('login_2fa'))

            session['user_id'] = user.id
            user.last_seen = datetime.utcnow()
            
            # Создаем новую сессию
            session_token = secrets.token_urlsafe(32)
            
            # Получаем информацию об устройстве
            user_agent = request.headers.get('User-Agent', '')
            ip_address = request.remote_addr
            
            # Определяем название устройства из User-Agent
            device_name = 'Unknown Device'
            if 'Windows' in user_agent:
                device_name = 'Windows PC'
            elif 'Mac' in user_agent:
                device_name = 'Mac'
            elif 'Linux' in user_agent:
                device_name = 'Linux PC'
            elif 'iPhone' in user_agent:
                device_name = 'iPhone'
            elif 'iPad' in user_agent:
                device_name = 'iPad'
            elif 'Android' in user_agent:
                device_name = 'Android'
            
            new_session = UserSession(
                user_id=user.id,
                session_token=session_token,
                device_name=device_name,
                ip_address=ip_address,
                user_agent=user_agent
            )
            db.session.add(new_session)
            
            # Сохраняем токен сессии в Flask session
            session['session_token'] = session_token
            
            db.session.commit()

            # Уведомление администратору о новых обращениях в поддержку
            if user.is_admin:
                try:
                    _notify_admin_support(user.id)
                except Exception as e:
                    print(f"Admin support notify error: {e}")

            return redirect(url_for('index'))
        
        return render_template('login.html', error='Неверное имя пользователя или пароль')
    
    return render_template('login.html')

# Выход
@app.route('/logout')
def logout():
    # Деактивируем текущую сессию
    if 'session_token' in session:
        user_session = UserSession.query.filter_by(session_token=session['session_token']).first()
        if user_session:
            user_session.is_active = False
            db.session.commit()
    
    session.pop('user_id', None)
    session.pop('session_token', None)
    return redirect(url_for('login'))

# Поиск пользователей
@app.route('/search')
def search():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify({'users': [], 'groups': []})
    
    # Поиск пользователей
    users = User.query.filter(
        User.username.contains(query)
    ).filter(
        User.id != session['user_id']
    ).limit(10).all()
    
    # Поиск групп и каналов (только с username - публичные)
    groups = Group.query.filter(
        (Group.name.contains(query)) | (Group.username.contains(query))
    ).filter(
        Group.username.isnot(None),  # Только группы/каналы с username (публичные)
        Group.username != ''  # Username не пустой
    ).limit(10).all()
    
    return jsonify({
        'users': [{
            'id': user.id, 
            'username': user.username,
            'display_name': user.display_name or user.username,
            'avatar_color': user.avatar_color,
            'avatar_url': user.avatar_url,
            'avatar_letter': user.get_avatar_letter(),
            'bio': user.bio,
            'is_bot': user.is_bot
        } for user in users],
        'groups': [{
            'id': group.id,
            'name': group.name,
            'username': group.username,
            'description': group.description,
            'avatar_color': group.avatar_color,
            'avatar_url': group.avatar_url,
            'avatar_letter': group.name[0].upper(),
            'is_channel': group.is_channel,
            'members_count': GroupMember.query.filter_by(group_id=group.id).count()
        } for group in groups]
    })

# Получение чата с пользователем
@app.route('/chat/<int:user_id>')
def get_chat(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Получаем параметр last_check для polling
    last_check = request.args.get('last_check')
    
    query = Message.query.filter(
        ((Message.sender_id == session['user_id']) & (Message.receiver_id == user_id)) |
        ((Message.sender_id == user_id) & (Message.receiver_id == session['user_id']))
    )
    
    # Если есть last_check, фильтруем только новые сообщения
    if last_check:
        try:
            last_check_time = datetime.fromtimestamp(int(last_check) / 1000)
            query = query.filter(Message.timestamp > last_check_time)
        except:
            pass
    
    messages = query.order_by(Message.timestamp.asc()).all()
    
    other_user = User.query.get( user_id)
    
    return jsonify({
        'other_user': {
            'id': other_user.id, 
            'username': other_user.username,
            'display_name': other_user.display_name or other_user.username,
            'avatar_color': other_user.avatar_color,
            'avatar_url': other_user.avatar_url,
            'avatar_letter': other_user.get_avatar_letter(),
            'is_verified': other_user.is_verified,
            'is_online': other_user.id in online_users,
            'bio': other_user.bio,
            'last_seen': other_user.last_seen.isoformat() if other_user.last_seen else None
        },
        'messages': [{
            'id': msg.id,
            'sender_id': msg.sender_id,
            'content': msg.content,
            'message_type': msg.message_type or 'text',
            'media_url': msg.media_url,
            'media_files': [{
                'media_type': mf.media_type,
                'media_url': mf.media_url,
                'file_name': mf.file_name,
                'file_size': mf.file_size
            } for mf in msg.media_files] if msg.media_files else [],
            'duration': msg.duration,
            'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': msg.timestamp.isoformat() + 'Z',
            'edited_at': msg.edited_at.strftime('%H:%M %d.%m') if msg.edited_at else None,
            'is_deleted': msg.is_deleted,
            'is_mine': msg.sender_id == session['user_id'],
            'reply_to': {
                'id': msg.reply_to.id,
                'content': msg.reply_to.content if not msg.reply_to.is_deleted else '[удалено]',
                'sender_name': (msg.reply_to.sender.display_name or msg.reply_to.sender.username) if msg.reply_to.sender else '?'
            } if msg.reply_to_id and msg.reply_to else None,
            'bot_buttons': _get_bot_buttons_for_msg(msg)
        } for msg in messages]
    })

# Отметить сообщения как прочитанные в личном чате
@app.route('/chat/<int:user_id>/mark_read', methods=['POST'])
def mark_chat_as_read(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        # Находим последнее сообщение от этого пользователя
        last_message = Message.query.filter(
            Message.sender_id == user_id,
            Message.receiver_id == session['user_id']
        ).order_by(Message.timestamp.desc()).first()
        
        if last_message:
            # Обновляем или создаем запись о последнем прочитанном сообщении
            last_read = LastReadMessage.query.filter_by(
                user_id=session['user_id'],
                chat_with_user_id=user_id
            ).first()
            
            if last_read:
                last_read.last_read_message_id = last_message.id
                last_read.updated_at = datetime.utcnow()
            else:
                last_read = LastReadMessage(
                    user_id=session['user_id'],
                    chat_with_user_id=user_id,
                    last_read_message_id=last_message.id
                )
                db.session.add(last_read)
            
            db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error marking chat as read: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Отметить сообщения как прочитанные в группе
@app.route('/groups/<int:group_id>/mark_read', methods=['POST'])
def mark_group_as_read(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        # Проверяем членство
        membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
        if not membership:
            return jsonify({'error': 'Вы не состоите в этой группе'}), 403
        
        # Находим последнее сообщение в группе
        last_message = GroupMessage.query.filter_by(group_id=group_id).order_by(GroupMessage.timestamp.desc()).first()
        
        if last_message:
            # Обновляем или создаем запись о последнем прочитанном сообщении
            last_read = LastReadGroupMessage.query.filter_by(
                user_id=session['user_id'],
                group_id=group_id
            ).first()
            
            if last_read:
                last_read.last_read_message_id = last_message.id
                last_read.updated_at = datetime.utcnow()
            else:
                last_read = LastReadGroupMessage(
                    user_id=session['user_id'],
                    group_id=group_id,
                    last_read_message_id=last_message.id
                )
                db.session.add(last_read)
            
            db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error marking group as read: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Отправка сообщения

def check_spam_block(sender):
    """Возвращает (True, until_str) если заблокирован, иначе (False, None)"""
    if not sender or not sender.is_spam_blocked:
        return False, None
    if sender.spam_block_until and datetime.utcnow() > sender.spam_block_until:
        sender.is_spam_blocked = False
        sender.spam_block_until = None
        db.session.commit()
        return False, None
    from datetime import timedelta
    until = sender.spam_block_until
    until_msk = (until + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if until else 'неизвестно'
    return True, until_msk

@app.route('/send', methods=['POST'])
def send_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    sender = User.query.get( session['user_id'])
    
    # Проверяем, не забанен ли отправитель
    if sender and sender.is_banned:
        return jsonify({'error': 'Ваш аккаунт заблокирован'}), 403

    blocked, until_str = check_spam_block(sender)
    if blocked:
        return jsonify({'error': 'spam_blocked', 'until': until_str}), 403
    
    data = request.get_json()
    receiver_id = data.get('receiver_id')
    content = data.get('content', '').strip() if data else None
    
    if not receiver_id or not content:
        return jsonify({'error': 'Неверные данные'}), 400
    
    # Проверяем максимальную длину сообщения
    if len(content) > 4096:
        return jsonify({'error': 'Сообщение слишком длинное (максимум 4096 символов)'}), 400
    
    # Проверяем, существует ли получатель
    receiver = User.query.get( receiver_id)
    if not receiver:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    # Проверяем, не забанен ли получатель
    if receiver.is_banned:
        return jsonify({'error': 'Этот пользователь заблокирован'}), 403
    
    # Проверяем, не пытается ли пользователь отправить сообщение самому себе
    if receiver_id == session['user_id']:
        return jsonify({'error': 'Вы не можете отправить сообщение самому себе'}), 400
    
    message = Message(
        sender_id=session['user_id'],
        receiver_id=receiver_id,
        content=content,
        reply_to_id=data.get('reply_to_id') or None
    )
    
    db.session.add(message)
    db.session.commit()
    
    # Информация об отправителе для обновления аватарок
    sender_info = {
        'id': sender.id,
        'username': sender.username,
        'display_name': sender.display_name or sender.username,
        'avatar_color': sender.avatar_color,
        'avatar_letter': sender.get_avatar_letter()
    }
    
    # Отправляем сообщение через WebSocket
    message_data = {
        'id': message.id,
        'sender_id': message.sender_id,
        'receiver_id': message.receiver_id,
        'content': message.content,
        'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': message.timestamp.isoformat() + 'Z',
        'is_mine': False  # Для получателя
    }
    
    try:
        # Отправляем отправителю
        socketio.emit('new_message', {
            'message': {
                'id': message.id,
                'sender_id': message.sender_id,
                'content': message.content,
                'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': message.timestamp.isoformat() + 'Z',
                'is_mine': True
            },
            'other_user_id': receiver_id,
            'sender_info': sender_info
        }, room=f'user_{session["user_id"]}', namespace='/')
        
        # Отправляем получателю
        socketio.emit('new_message', {
            'message': {
                'id': message.id,
                'sender_id': message.sender_id,
                'content': message.content,
                'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': message.timestamp.isoformat() + 'Z',
                'is_mine': False
            },
            'other_user_id': session['user_id'],
            'sender_info': sender_info
        }, room=f'user_{receiver_id}', namespace='/')
    except Exception as e:
        print(f"Error emitting message: {e}")
    
    # Если получатель — бот, триггерим его webhook
    if receiver.is_bot:
        bot = Bot.query.filter_by(user_id=receiver_id, is_active=True).first()
        if bot:
            _trigger_webhook(bot, {
                'message': {
                    'message_id': message.id,
                    'from': {'id': sender.id, 'username': sender.username, 'display_name': sender.display_name or sender.username},
                    'chat': {'id': sender.id},
                    'text': content,
                    'date': message.timestamp.isoformat() + 'Z'
                }
            })

    return jsonify({
        'success': True,
        'message_id': message.id,
        'timestamp': message.timestamp.strftime('%H:%M %d.%m')
    })

# Отправка голосового сообщения
@app.route('/send/voice', methods=['POST'])
def send_voice_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    sender = User.query.get( session['user_id'])
    if sender and sender.is_banned:
        return jsonify({'error': 'Ваш аккаунт заблокирован'}), 403
    
    blocked, until_str = check_spam_block(sender)
    if blocked:
        return jsonify({'error': 'spam_blocked', 'until': until_str}), 403

    if 'audio' not in request.files:
        return jsonify({'error': 'Аудио файл не найден'}), 400
    
    audio_file = request.files['audio']
    receiver_id = request.form.get('receiver_id')
    duration = request.form.get('duration', 0)
    
    if not receiver_id:
        return jsonify({'error': 'Получатель не указан'}), 400
    
    receiver = User.query.get( int(receiver_id))
    if not receiver or receiver.is_banned:
        return jsonify({'error': 'Получатель не найден'}), 404
    
    # Сохраняем файл
    filename = secure_filename(f"voice_{session['user_id']}_{int(time.time())}.webm")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'voice', filename)
    audio_file.save(filepath)
    
    # Создаем сообщение
    message = Message(
        sender_id=session['user_id'],
        receiver_id=receiver_id,
        content='[Голосовое сообщение]',
        message_type='voice',
        media_url=f'/static/media/voice/{filename}',
        duration=int(duration)
    )
    
    db.session.add(message)
    db.session.commit()
    
    # Отправляем через Socket.IO
    message_data = {
        'id': message.id,
        'sender_id': message.sender_id,
        'content': message.content,
        'message_type': 'voice',
        'media_url': message.media_url,
        'duration': message.duration,
        'timestamp': message.timestamp.strftime('%H:%M %d.%m')
    }
    
    try:
        socketio.emit('new_message', {
            'message': {**message_data, 'is_mine': True},
            'other_user_id': receiver_id
        }, room=f'user_{session["user_id"]}', namespace='/')
        
        socketio.emit('new_message', {
            'message': {**message_data, 'is_mine': False},
            'other_user_id': session['user_id']
        }, room=f'user_{receiver_id}', namespace='/')
    except Exception as e:
        print(f"Error emitting voice message: {e}")
    
    return jsonify({
        'success': True,
        'message_id': message.id,
        'media_url': message.media_url
    })

# Отправка видео кружочка
@app.route('/send/video-circle', methods=['POST'])
def send_video_circle():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        sender = User.query.get( session['user_id'])
        if sender and sender.is_banned:
            return jsonify({'error': 'Ваш аккаунт заблокирован'}), 403
        
        blocked, until_str = check_spam_block(sender)
        if blocked:
            return jsonify({'error': 'spam_blocked', 'until': until_str}), 403

        if 'video' not in request.files:
            return jsonify({'error': 'Видео файл не найден'}), 400
        
        video_file = request.files['video']
        receiver_id = request.form.get('receiver_id')
        duration = request.form.get('duration', 0)
        
        if not receiver_id:
            return jsonify({'error': 'Получатель не указан'}), 400
        
        receiver = User.query.get( int(receiver_id))
        if not receiver or receiver.is_banned:
            return jsonify({'error': 'Получатель не найден'}), 404
        
        # Создаем папку если не существует
        video_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'video')
        os.makedirs(video_folder, exist_ok=True)
        
        # Сохраняем файл
        filename = secure_filename(f"video_{session['user_id']}_{int(time.time())}.webm")
        filepath = os.path.join(video_folder, filename)
        video_file.save(filepath)
        
        # Создаем сообщение
        message = Message(
            sender_id=session['user_id'],
            receiver_id=receiver_id,
            content='[Видео кружочек]',
            message_type='video_note',
            media_url=f'/static/media/video/{filename}',
            duration=int(duration)
        )
        
        db.session.add(message)
        db.session.commit()
        
        # Информация об отправителе
        sender_info = {
            'id': sender.id,
            'username': sender.username,
            'display_name': sender.display_name or sender.username,
            'avatar_color': sender.avatar_color,
            'avatar_letter': sender.get_avatar_letter()
        }
        
        # Отправляем через Socket.IO
        message_data = {
            'id': message.id,
            'sender_id': message.sender_id,
            'content': message.content,
            'message_type': 'video_note',
            'media_url': message.media_url,
            'duration': message.duration,
            'timestamp': message.timestamp.strftime('%H:%M %d.%m')
        }
        
        try:
            socketio.emit('new_message', {
                'message': {**message_data, 'is_mine': True},
                'other_user_id': receiver_id,
                'sender_info': sender_info
            }, room=f'user_{session["user_id"]}', namespace='/')
            
            socketio.emit('new_message', {
                'message': {**message_data, 'is_mine': False},
                'other_user_id': session['user_id'],
                'sender_info': sender_info
            }, room=f'user_{receiver_id}', namespace='/')
        except Exception as e:
            print(f"Error emitting video message: {e}")
        
        return jsonify({
            'success': True,
            'message_id': message.id,
            'media_url': message.media_url
        })
    
    except Exception as e:
        print(f"Error in send_video_circle: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500

# Отправка изображения
@app.route('/send/image', methods=['POST'])
def send_image():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        sender = User.query.get( session['user_id'])
        if sender and sender.is_banned:
            return jsonify({'error': 'Ваш аккаунт заблокирован'}), 403
        
        blocked, until_str = check_spam_block(sender)
        if blocked:
            return jsonify({'error': 'spam_blocked', 'until': until_str}), 403

        if 'image' not in request.files:
            return jsonify({'error': 'Изображение не найдено'}), 400
        
        image_file = request.files['image']
        receiver_id = request.form.get('receiver_id')
        
        if not receiver_id:
            return jsonify({'error': 'Получатель не указан'}), 400
        
        receiver = User.query.get( int(receiver_id))
        if not receiver or receiver.is_banned:
            return jsonify({'error': 'Получатель не найден'}), 404
        
        # Создаем папку если не существует
        images_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'images')
        os.makedirs(images_folder, exist_ok=True)
        
        # Сохраняем файл
        ext = image_file.filename.split('.')[-1] if '.' in image_file.filename else 'jpg'
        filename = secure_filename(f"img_{session['user_id']}_{int(time.time())}.{ext}")
        filepath = os.path.join(images_folder, filename)
        image_file.save(filepath)
        
        # Создаем сообщение
        message = Message(
            sender_id=session['user_id'],
            receiver_id=receiver_id,
            content='[Изображение]',
            message_type='image',
            media_url=f'/static/media/images/{filename}'
        )
        
        db.session.add(message)
        db.session.commit()
        
        # Информация об отправителе
        sender_info = {
            'id': sender.id,
            'username': sender.username,
            'display_name': sender.display_name or sender.username,
            'avatar_color': sender.avatar_color,
            'avatar_letter': sender.get_avatar_letter()
        }
        
        # Отправляем через Socket.IO
        message_data = {
            'id': message.id,
            'sender_id': message.sender_id,
            'content': message.content,
            'message_type': 'image',
            'media_url': message.media_url,
            'timestamp': message.timestamp.strftime('%H:%M %d.%m')
        }
        
        try:
            socketio.emit('new_message', {
                'message': {**message_data, 'is_mine': True},
                'other_user_id': receiver_id,
                'sender_info': sender_info
            }, room=f'user_{session["user_id"]}', namespace='/')
            
            socketio.emit('new_message', {
                'message': {**message_data, 'is_mine': False},
                'other_user_id': session['user_id'],
                'sender_info': sender_info
            }, room=f'user_{receiver_id}', namespace='/')
        except Exception as e:
            print(f"Error emitting image message: {e}")
        
        return jsonify({
            'success': True,
            'message_id': message.id,
            'media_url': message.media_url
        })
    
    except Exception as e:
        print(f"Error in send_image: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500

# Отправка множественных файлов
@app.route('/send_multiple_files', methods=['POST'])
def send_multiple_files():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        receiver_id = int(request.form.get('receiver_id'))
        caption = request.form.get('caption', '')
        files = request.files.getlist('files')
        
        if not files:
            return jsonify({'error': 'Файлы не выбраны'}), 400
        
        # Создаем сообщение-альбом
        message = Message(
            sender_id=session['user_id'],
            receiver_id=receiver_id,
            content=caption or 'Файлы',
            message_type='album'
        )
        db.session.add(message)
        db.session.flush()  # Получаем ID сообщения
        
        # Сохраняем каждый файл
        media_files = []
        for index, file in enumerate(files):
            if file and allowed_file(file.filename, ALLOWED_EXTENSIONS):
                filename = secure_filename(file.filename)
                timestamp = int(time.time() * 1000)
                unique_filename = f"{filename.rsplit('.', 1)[0]}_{timestamp}.{filename.rsplit('.', 1)[1]}"
                
                # Определяем тип файла
                if file.content_type.startswith('image/'):
                    media_type = 'image'
                    folder = 'images'
                elif file.content_type.startswith('video/'):
                    media_type = 'video'
                    folder = 'videos'
                else:
                    media_type = 'file'
                    folder = 'files'
                
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], folder, unique_filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                
                media_url = f'/static/media/{folder}/{unique_filename}'
                
                # Создаем запись медиа
                message_media = MessageMedia(
                    message_id=message.id,
                    media_type=media_type,
                    media_url=media_url,
                    file_name=filename,
                    file_size=os.path.getsize(filepath),
                    order_index=index
                )
                db.session.add(message_media)
                media_files.append({
                    'media_type': media_type,
                    'media_url': media_url,
                    'file_name': filename
                })
        
        db.session.commit()
        
        # Отправляем через Socket.IO получателю И отправителю
        try:
            sender = User.query.get(session['user_id'])
            sender_info = {
                'id': sender.id,
                'username': sender.username,
                'display_name': sender.display_name or sender.username,
                'avatar_color': sender.avatar_color,
                'avatar_url': sender.avatar_url,
                'avatar_letter': sender.get_avatar_letter(),
                'is_verified': sender.is_verified
            }
            
            message_data = {
                'id': message.id,
                'sender_id': message.sender_id,
                'content': message.content,
                'message_type': 'album',
                'media_files': media_files,
                'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': message.timestamp.isoformat() + 'Z',
                'is_mine': False
            }
            
            # Отправляем получателю
            socketio.emit('new_message', {
                'message': message_data,
                'other_user_id': session['user_id'],
                'sender_info': sender_info
            }, room=f'user_{receiver_id}', namespace='/')
            
            # Отправляем отправителю (с is_mine: True)
            message_data_sender = message_data.copy()
            message_data_sender['is_mine'] = True
            socketio.emit('new_message', {
                'message': message_data_sender,
                'other_user_id': receiver_id,
                'sender_info': sender_info
            }, room=f'user_{session["user_id"]}', namespace='/')
            
        except Exception as e:
            print(f"Error emitting multiple files message: {e}")
        
        return jsonify({
            'success': True,
            'message_id': message.id,
            'media_files': media_files
        })
    
    except Exception as e:
        print(f"Error in send_multiple_files: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500

# Отправка множественных файлов в группу
@app.route('/send_multiple_files_group', methods=['POST'])
def send_multiple_files_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        group_id = int(request.form.get('group_id'))
        caption = request.form.get('caption', '')
        files = request.files.getlist('files')
        
        if not files:
            return jsonify({'error': 'Файлы не выбраны'}), 400
        
        # Проверяем членство
        membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
        if not membership:
            return jsonify({'error': 'Вы не состоите в этой группе'}), 403
        
        # Создаем сообщение-альбом
        message = GroupMessage(
            group_id=group_id,
            sender_id=session['user_id'],
            content=caption or 'Файлы'
        )
        db.session.add(message)
        db.session.commit()
        
        # Сохраняем файлы
        media_files = []
        for index, file in enumerate(files):
            if file and allowed_file(file.filename, ALLOWED_EXTENSIONS):
                filename = secure_filename(file.filename)
                timestamp = int(time.time() * 1000)
                unique_filename = f"{filename.rsplit('.', 1)[0]}_{timestamp}.{filename.rsplit('.', 1)[1]}"
                
                if file.content_type.startswith('image/'):
                    media_type = 'image'
                    folder = 'images'
                elif file.content_type.startswith('video/'):
                    media_type = 'video'
                    folder = 'videos'
                else:
                    media_type = 'file'
                    folder = 'files'
                
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], folder, unique_filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                
                media_url = f'/static/media/{folder}/{unique_filename}'
                
                # Сохраняем информацию о медиафайле в базу данных
                media = GroupMessageMedia(
                    message_id=message.id,
                    media_type=media_type,
                    media_url=media_url,
                    file_name=filename,
                    file_size=os.path.getsize(filepath),
                    order_index=index
                )
                db.session.add(media)
                
                media_files.append({
                    'media_type': media_type,
                    'media_url': media_url,
                    'file_name': filename
                })
        
        db.session.commit()
        
        # Получаем информацию об отправителе
        sender = User.query.get(session['user_id'])
        
        # Формируем данные сообщения для Socket.IO
        message_data = {
            'id': message.id,
            'sender_id': sender.id,
            'sender_name': sender.display_name or sender.username,
            'sender_avatar_color': sender.avatar_color,
            'sender_avatar_url': sender.avatar_url,
            'sender_avatar_letter': sender.get_avatar_letter(),
            'content': caption or 'Файлы',
            'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': message.timestamp.isoformat() + 'Z',
            'media_files': media_files
        }
        
        # Отправляем через Socket.IO всем участникам группы (включая отправителя)
        socketio.emit('new_group_message', {
            'group_id': group_id,
            'message': message_data
        }, room=f'group_{group_id}', include_self=True)
        
        return jsonify({
            'success': True,
            'message': message_data
        })
    
    except Exception as e:
        print(f"Error in send_multiple_files_group: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500

# Получение списка пользователей для боковой панели
@app.route('/users')
def get_users():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        # Получаем пользователей, с которыми есть переписка
        # Используем CASE для эмуляции greatest/least в SQLite
        subquery = db.session.query(
            case(
                (Message.sender_id > Message.receiver_id, Message.sender_id),
                else_=Message.receiver_id
            ).label('user1'),
            case(
                (Message.sender_id > Message.receiver_id, Message.receiver_id),
                else_=Message.sender_id
            ).label('user2')
        ).filter(
            (Message.sender_id == session['user_id']) | (Message.receiver_id == session['user_id'])
        ).distinct()
        
        user_ids = set()
        for user1, user2 in subquery:
            if user1 == session['user_id']:
                user_ids.add(user2)
            else:
                user_ids.add(user1)
        
        # Получаем пользователей с информацией о последнем сообщении
        users_data = []
        for user_id in user_ids:
            user = User.query.get(user_id)
            if user:
                # Находим последнее сообщение с этим пользователем
                last_message = Message.query.filter(
                    ((Message.sender_id == session['user_id']) & (Message.receiver_id == user_id)) |
                    ((Message.sender_id == user_id) & (Message.receiver_id == session['user_id']))
                ).order_by(Message.timestamp.desc()).first()
                
                # Используем timestamp последнего сообщения или время создания пользователя
                sort_timestamp = last_message.timestamp if last_message else user.created_at
                
                # Подсчитываем непрочитанные сообщения
                # Получаем последнее прочитанное сообщение
                last_read = LastReadMessage.query.filter_by(
                    user_id=session['user_id'],
                    chat_with_user_id=user_id
                ).first()
                
                # Считаем непрочитанные сообщения от этого пользователя
                unread_query = Message.query.filter(
                    Message.sender_id == user_id,
                    Message.receiver_id == session['user_id']
                )
                
                if last_read and last_read.last_read_message_id:
                    # Считаем сообщения после последнего прочитанного
                    unread_query = unread_query.filter(Message.id > last_read.last_read_message_id)
                
                unread_count = unread_query.count()
                
                # Форматируем последнее сообщение для отображения
                last_message_text = None
                if last_message:
                    if last_message.sender_id == session['user_id']:
                        # Мое сообщение
                        last_message_text = f"Вы: {last_message.content[:50]}"
                    else:
                        # Сообщение от собеседника
                        last_message_text = last_message.content[:50]
                
                users_data.append({
                    'id': user.id,
                    'username': user.username,
                    'display_name': user.display_name or user.username,
                    'avatar_color': user.avatar_color,
                    'avatar_url': user.avatar_url,
                    'avatar_letter': user.get_avatar_letter(),
                    'is_verified': user.is_verified,
                    'is_bot': user.is_bot,
                    'is_online': user.id in online_users,
                    'last_seen': user.last_seen.isoformat() if user.last_seen else None,
                    'last_message_time': sort_timestamp.isoformat() + 'Z',  # ISO формат UTC для клиента
                    'last_message': last_message_text,
                    'unread_count': unread_count
                })
        
        # Сортируем по времени последнего сообщения (новые сверху)
        users_data.sort(key=lambda x: x['last_message_time'], reverse=True)
        
        return jsonify({'users': users_data})
    except Exception as e:
        print(f"Error in /users: {e}")
        return jsonify({'users': []})

# Профиль пользователя
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get( session['user_id'])
    verification_request = VerificationRequest.query.filter_by(user_id=user.id).order_by(VerificationRequest.created_at.desc()).first()
    return render_template('profile.html', user=user, verification_request=verification_request)

# API для получения информации о пользователе
@app.route('/api/user/<int:user_id>')
def get_user_info(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    return jsonify({
        'id': user.id,
        'username': user.username,
        'display_name': user.display_name or user.username,
        'bio': user.bio,
        'avatar_color': user.avatar_color,
        'avatar_url': user.avatar_url,
        'avatar_letter': user.get_avatar_letter(),
        'is_verified': user.is_verified,
        'is_premium': user.is_premium,
        'is_bot': user.is_bot,
        'premium_emoji': user.premium_emoji,
        'created_at': user.created_at.strftime('%d.%m.%Y')
    })

# Обновление профиля
@app.route('/profile/update', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    data = request.get_json()
    
    if 'display_name' in data:
        user.display_name = data['display_name'].strip() or user.username
    
    if 'bio' in data:
        user.bio = data['bio'].strip()
    
    if 'avatar_color' in data:
        user.avatar_color = data['avatar_color']
    
    if 'chat_wallpaper' in data:
        if not user.is_premium and not user.is_admin and data['chat_wallpaper'] != 'default':
            return jsonify({'error': 'premium_required', 'message': 'Смена обоев доступна только для Premium пользователей'}), 403
        user.chat_wallpaper = data['chat_wallpaper']

    if 'timezone' in data:
        user.timezone = data['timezone']
    
    db.session.commit()
    
    # Отправляем обновление профиля через Socket.IO всем, кто с нами общается
    user_info = {
        'id': user.id,
        'username': user.username,
        'display_name': user.display_name,
        'bio': user.bio,
        'avatar_color': user.avatar_color,
        'avatar_letter': user.get_avatar_letter()
    }
    
    # Получаем всех пользователей, с которыми есть переписка
    messages = Message.query.filter(
        (Message.sender_id == user.id) | (Message.receiver_id == user.id)
    ).all()
    
    notified_users = set()
    for msg in messages:
        other_user_id = msg.receiver_id if msg.sender_id == user.id else msg.sender_id
        if other_user_id not in notified_users:
            socketio.emit('profile_updated', user_info, room=f'user_{other_user_id}', namespace='/')
            notified_users.add(other_user_id)
    
    # Также отправляем всем онлайн пользователям
    for online_user_id in online_users.keys():
        if online_user_id != user.id and online_user_id not in notified_users:
            socketio.emit('profile_updated', user_info, room=f'user_{online_user_id}', namespace='/')
            notified_users.add(online_user_id)
    
    return jsonify({
        'success': True,
        'user': user_info
    })

# Загрузка аватарки пользователя
@app.route('/profile/upload_avatar', methods=['POST'])
def upload_avatar():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if not user.is_premium and not user.is_admin:
        return jsonify({'error': 'premium_required', 'message': 'Загрузка аватара доступна только для Premium пользователей'}), 403
    
    if 'avatar' not in request.files:
        return jsonify({'error': 'Файл не найден'}), 400
    
    file = request.files['avatar']
    
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    
    if not allowed_file(file.filename, ALLOWED_IMAGES):
        return jsonify({'error': 'Недопустимый формат файла. Разрешены: PNG, JPG, JPEG, GIF, WEBP'}), 400
    
    try:
        
        # Создаем папку для аватарок если её нет
        avatars_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'avatars')
        os.makedirs(avatars_folder, exist_ok=True)
        
        # Генерируем уникальное имя файла
        timestamp = int(time.time())
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"avatar_{user.id}_{timestamp}.{ext}"
        filepath = os.path.join(avatars_folder, filename)
        
        # Удаляем старую аватарку если есть
        if user.avatar_url:
            old_path = os.path.join('static', user.avatar_url.lstrip('/static/'))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
        
        # Сохраняем новую аватарку
        file.save(filepath)
        
        # Обновляем URL в базе данных
        user.avatar_url = f"/static/media/avatars/{filename}"
        db.session.commit()
        
        # Отправляем обновление через Socket.IO
        user_info = {
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name,
            'bio': user.bio,
            'avatar_color': user.avatar_color,
            'avatar_url': user.avatar_url,
            'avatar_letter': user.get_avatar_letter()
        }
        
        # Уведомляем всех пользователей с которыми есть переписка
        messages = Message.query.filter(
            (Message.sender_id == user.id) | (Message.receiver_id == user.id)
        ).all()
        
        notified_users = set()
        for msg in messages:
            other_user_id = msg.receiver_id if msg.sender_id == user.id else msg.sender_id
            if other_user_id not in notified_users:
                socketio.emit('profile_updated', user_info, room=f'user_{other_user_id}', namespace='/')
                notified_users.add(other_user_id)
        
        return jsonify({
            'success': True,
            'avatar_url': user.avatar_url
        })
        
    except Exception as e:
        print(f"Error uploading avatar: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Ошибка загрузки аватарки'}), 500

# Удаление аватарки пользователя
@app.route('/profile/delete_avatar', methods=['POST'])
def delete_avatar():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        user = User.query.get(session['user_id'])
        
        # Удаляем файл аватарки
        if user.avatar_url:
            old_path = os.path.join('static', user.avatar_url.lstrip('/static/'))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
        
        # Удаляем URL из базы данных
        user.avatar_url = None
        db.session.commit()
        
        # Отправляем обновление через Socket.IO
        user_info = {
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name,
            'bio': user.bio,
            'avatar_color': user.avatar_color,
            'avatar_url': None,
            'avatar_letter': user.get_avatar_letter()
        }
        
        # Уведомляем всех пользователей
        messages = Message.query.filter(
            (Message.sender_id == user.id) | (Message.receiver_id == user.id)
        ).all()
        
        notified_users = set()
        for msg in messages:
            other_user_id = msg.receiver_id if msg.sender_id == user.id else msg.sender_id
            if other_user_id not in notified_users:
                socketio.emit('profile_updated', user_info, room=f'user_{other_user_id}', namespace='/')
                notified_users.add(other_user_id)
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error deleting avatar: {e}")
        return jsonify({'error': 'Ошибка удаления аватарки'}), 500

# Получение настроек текущего пользователя
@app.route('/user/settings')
def get_user_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    return jsonify({
        'chat_wallpaper': user.chat_wallpaper or 'default',
        'theme': user.theme or 'light'
    })

# Получение активных сессий пользователя
@app.route('/user/sessions')
def get_user_sessions():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    sessions = UserSession.query.filter_by(
        user_id=session['user_id'],
        is_active=True
    ).order_by(UserSession.last_activity.desc()).all()
    
    current_token = session.get('session_token')
    
    return jsonify({
        'sessions': [{
            'id': s.id,
            'device_name': s.device_name,
            'ip_address': s.ip_address,
            'created_at': s.created_at.strftime('%d.%m.%Y %H:%M'),
            'last_activity': s.last_activity.strftime('%d.%m.%Y %H:%M'),
            'is_current': s.session_token == current_token
        } for s in sessions]
    })

# Завершение сессии
@app.route('/user/sessions/<int:session_id>/terminate', methods=['POST'])
def terminate_session(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_session = UserSession.query.get(session_id)
    
    if not user_session or user_session.user_id != session['user_id']:
        return jsonify({'error': 'Сессия не найдена'}), 404
    
    user_session.is_active = False
    db.session.commit()
    
    return jsonify({'success': True})

# Завершение всех сессий кроме текущей
@app.route('/user/sessions/terminate_all', methods=['POST'])
def terminate_all_sessions():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    current_token = session.get('session_token')
    
    sessions = UserSession.query.filter_by(
        user_id=session['user_id'],
        is_active=True
    ).all()
    
    for s in sessions:
        if s.session_token != current_token:
            s.is_active = False
    
    db.session.commit()
    
    return jsonify({'success': True})

# Редактирование сообщения
@app.route('/message/<int:message_id>/edit', methods=['POST'])
def edit_message(message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    
    # Проверяем, не забанен ли пользователь
    if user and user.is_banned:
        return jsonify({'error': 'Ваш аккаунт заблокирован'}), 403
    
    message = Message.query.get( message_id)
    if not message:
        return jsonify({'error': 'Сообщение не найдено'}), 404
    
    if message.sender_id != session['user_id']:
        return jsonify({'error': 'Вы не можете редактировать это сообщение'}), 403
    
    data = request.get_json()
    new_content = data.get('content', '').strip()
    
    if not new_content:
        return jsonify({'error': 'Сообщение не может быть пустым'}), 400
    
    message.content = new_content
    message.edited_at = datetime.utcnow()
    db.session.commit()
    
    # Отправляем событие через Socket.IO обоим пользователям
    edit_data = {
        'message_id': message.id,
        'content': message.content,
        'edited_at': message.edited_at.strftime('%H:%M %d.%m')
    }
    
    try:
        # Отправителю
        socketio.emit('message_edited', edit_data, room=f'user_{message.sender_id}', namespace='/')
        # Получателю
        socketio.emit('message_edited', edit_data, room=f'user_{message.receiver_id}', namespace='/')
    except Exception as e:
        print(f"Error emitting message_edited: {e}")
    
    return jsonify({
        'success': True,
        'message': {
            'id': message.id,
            'content': message.content,
            'edited_at': message.edited_at.strftime('%H:%M %d.%m'),
            'timestamp': message.timestamp.strftime('%H:%M %d.%m')
        }
    })

# Удаление сообщения
@app.route('/message/<int:message_id>/delete', methods=['POST'])
def delete_message(message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    
    # Проверяем, не забанен ли пользователь
    if user and user.is_banned:
        return jsonify({'error': 'Ваш аккаунт заблокирован'}), 403
    
    message = Message.query.get( message_id)
    if not message:
        return jsonify({'error': 'Сообщение не найдено'}), 404
    
    if message.sender_id != session['user_id']:
        return jsonify({'error': 'Вы не можете удалить это сообщение'}), 403
    
    message.is_deleted = True
    message.content = '[Сообщение удалено]'
    db.session.commit()
    
    # Отправляем событие через Socket.IO обоим пользователям
    delete_data = {
        'message_id': message.id
    }
    
    try:
        # Отправителю
        socketio.emit('message_deleted', delete_data, room=f'user_{message.sender_id}', namespace='/')
        # Получателю
        socketio.emit('message_deleted', delete_data, room=f'user_{message.receiver_id}', namespace='/')
    except Exception as e:
        print(f"Error emitting message_deleted: {e}")
    
    return jsonify({'success': True, 'message_id': message.id})

# Админ панель
@app.route('/admin')
def admin():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    if not user or not user.is_admin:
        return redirect(url_for('index'))
    
    return render_template('admin.html', admin_role=user.admin_role or 'moderator')

# API для админ панели - список пользователей
@app.route('/admin/users')
def admin_users():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    if not user or not user.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    users = User.query.all()
    
    return jsonify({
        'users': [{
            'id': u.id,
            'username': u.username,
            'display_name': u.display_name or u.username,
            'is_verified': u.is_verified,
            'is_admin': u.is_admin,
            'admin_role': u.admin_role,
            'is_banned': u.is_banned,
            'two_fa_enabled': u.two_fa_enabled,
            'created_at': u.created_at.strftime('%d.%m.%Y %H:%M')
        } for u in users]
    })

# Верификация пользователя
@app.route('/admin/verify/<int:user_id>', methods=['POST'])
def verify_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    admin = User.query.get( session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    user = User.query.get( user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    user.is_verified = True
    db.session.commit()
    
    return jsonify({'success': True})

# Подать заявку на верификацию
@app.route('/profile/request-verification', methods=['POST'])
def request_verification():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    user = User.query.get(session['user_id'])
    if user.is_verified:
        return jsonify({'error': 'Вы уже верифицированы'}), 400

    existing = VerificationRequest.query.filter_by(user_id=user.id, status='pending').first()
    if existing:
        return jsonify({'error': 'Заявка уже подана и ожидает рассмотрения'}), 400

    data = request.json or {}
    reason = data.get('reason', '').strip()
    if not reason:
        return jsonify({'error': 'Укажите причину верификации'}), 400

    req = VerificationRequest(user_id=user.id, reason=reason)
    db.session.add(req)
    db.session.commit()
    return jsonify({'success': True})

# Список заявок на верификацию (для админа)
@app.route('/admin/verification-requests', methods=['GET'])
def get_verification_requests():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403

    requests_list = VerificationRequest.query.order_by(VerificationRequest.created_at.desc()).all()
    return jsonify({'requests': [{
        'id': r.id,
        'user_id': r.user_id,
        'username': r.user.username,
        'display_name': r.user.display_name or r.user.username,
        'reason': r.reason,
        'status': r.status,
        'created_at': r.created_at.strftime('%d.%m.%Y %H:%M')
    } for r in requests_list]})

# Одобрить заявку на верификацию
@app.route('/admin/verification/<int:req_id>/approve', methods=['POST'])
def approve_verification(req_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403

    req = VerificationRequest.query.get(req_id)
    if not req:
        return jsonify({'error': 'Заявка не найдена'}), 404

    req.status = 'approved'
    req.reviewed_by = admin.id
    req.reviewed_at = datetime.utcnow()
    req.user.is_verified = True
    db.session.commit()
    return jsonify({'success': True})

# Отклонить заявку на верификацию
@app.route('/admin/verification/<int:req_id>/reject', methods=['POST'])
def reject_verification(req_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403

    req = VerificationRequest.query.get(req_id)
    if not req:
        return jsonify({'error': 'Заявка не найдена'}), 404

    req.status = 'rejected'
    req.reviewed_by = admin.id
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})

# Список пользователей для спам-блока (3+ жалобы)
@app.route('/admin/spamblock-candidates', methods=['GET'])
def get_spamblock_candidates():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403

    # Считаем жалобы по target_id
    from collections import Counter
    counts = Counter(
        r['target_id'] for r in reports
        if r.get('target_type') == 'user' and r.get('target_id')
    )
    candidates = []
    for target_id, count in counts.items():
        if count >= 3:
            user = User.query.get(int(target_id))
            if user:
                candidates.append({
                    'id': user.id,
                    'username': user.username,
                    'display_name': user.display_name or user.username,
                    'report_count': count,
                    'is_spam_blocked': user.is_spam_blocked,
                    'spam_block_until': (user.spam_block_until + __import__('datetime').timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if user.spam_block_until else None
                })
    return jsonify({'candidates': candidates})

# Выдать спам-блок
@app.route('/admin/spamblock/<int:user_id>', methods=['POST'])
def give_spamblock(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    from datetime import timedelta
    user.is_spam_blocked = True
    user.spam_block_until = datetime.utcnow() + timedelta(days=1)
    db.session.commit()
    return jsonify({'success': True})

# Снять спам-блок
@app.route('/admin/spamblock/<int:user_id>/remove', methods=['POST'])
def remove_spamblock(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещен'}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    user.is_spam_blocked = False
    user.spam_block_until = None
    db.session.commit()
    return jsonify({'success': True})

# Удаление пользователя
@app.route('/admin/remove/<int:user_id>', methods=['POST'])
def remove_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'senior_admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    
    if user_id == session['user_id']:
        return jsonify({'error': 'Вы не можете удалить себя'}), 400
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    # Удаляем все сообщения пользователя
    Message.query.filter(
        (Message.sender_id == user_id) | (Message.receiver_id == user_id)
    ).delete()
    
    db.session.delete(user)
    db.session.commit()
    
    return jsonify({'success': True})

# Бан пользователя
@app.route('/admin/ban/<int:user_id>', methods=['POST'])
def ban_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    
    if user_id == session['user_id']:
        return jsonify({'error': 'Вы не можете забанить себя'}), 400
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    user.is_banned = True
    db.session.commit()
    
    return jsonify({'success': True})

# Разбан пользователя
@app.route('/admin/unban/<int:user_id>', methods=['POST'])
def unban_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    user.is_banned = False
    db.session.commit()
    
    return jsonify({'success': True})

# Обновление темы
@app.route('/theme/update', methods=['POST'])
def update_theme():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    data = request.get_json()
    theme = data.get('theme', 'light')
    
    if theme not in ['light', 'dark', 'liquid']:
        return jsonify({'error': 'Неверная тема'}), 400
    
    user.theme = theme
    db.session.commit()
    
    # Отправляем событие через Socket.IO
    socketio.emit('theme_changed', {
        'user_id': user.id,
        'theme': theme
    }, room=f'user_{user.id}', namespace='/')
    
    return jsonify({'success': True, 'theme': theme})

# Премиум система
@app.route('/admin/premium/<int:user_id>', methods=['POST'])
def toggle_premium(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'senior_admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    user.is_premium = not user.is_premium
    db.session.commit()
    
    return jsonify({'success': True, 'is_premium': user.is_premium})

# Обновление премиум эмодзи
@app.route('/profile/premium-emoji', methods=['POST'])
def update_premium_emoji():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    if not user or not user.is_premium:
        return jsonify({'error': 'Требуется премиум'}), 403
    
    data = request.get_json()
    emoji = data.get('emoji', '').strip()
    
    if len(emoji) > 10:
        return jsonify({'error': 'Эмодзи слишком длинный'}), 400
    
    user.premium_emoji = emoji
    db.session.commit()
    
    return jsonify({'success': True, 'emoji': emoji})

# Создание истории
@app.route('/story/create', methods=['POST'])
def create_story():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user = User.query.get( session['user_id'])
    if not user or not user.is_premium:
        return jsonify({'error': 'Истории доступны только для премиум пользователей'}), 403
    
    data = request.get_json()
    content = data.get('content', '').strip()
    
    if not content or len(content) > 500:
        return jsonify({'error': 'Неверное содержимое истории'}), 400
    
    # Истории исчезают через 24 часа
    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(hours=24)
    
    story = Story(
        user_id=user.id,
        content=content,
        media_type='text',
        expires_at=expires_at
    )
    
    db.session.add(story)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'story': {
            'id': story.id,
            'content': story.content,
            'created_at': story.created_at.strftime('%H:%M %d.%m'),
            'expires_at': story.expires_at.strftime('%H:%M %d.%m')
        }
    })

# Получение историй
@app.route('/stories')
def get_stories():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Удаляем истекшие истории
    Story.query.filter(Story.expires_at < datetime.utcnow()).delete()
    db.session.commit()
    
    # Получаем активные истории от пользователей, с которыми есть переписка
    messages = Message.query.filter(
        (Message.sender_id == session['user_id']) | (Message.receiver_id == session['user_id'])
    ).all()
    
    user_ids = set()
    for msg in messages:
        other_user_id = msg.receiver_id if msg.sender_id == session['user_id'] else msg.sender_id
        user_ids.add(other_user_id)
    
    stories = Story.query.filter(
        Story.user_id.in_(user_ids),
        Story.expires_at > datetime.utcnow()
    ).order_by(Story.created_at.desc()).all()
    
    stories_data = []
    for story in stories:
        user = User.query.get( story.user_id)
        stories_data.append({
            'id': story.id,
            'user': {
                'id': user.id,
                'username': user.username,
                'display_name': user.display_name or user.username,
                'avatar_color': user.avatar_color,
                'avatar_letter': user.get_avatar_letter(),
                'is_premium': user.is_premium,
                'premium_emoji': user.premium_emoji
            },
            'content': story.content,
            'created_at': story.created_at.strftime('%H:%M %d.%m'),
            'expires_at': story.expires_at.strftime('%H:%M %d.%m')
        })
    
    return jsonify({'stories': stories_data})

# Удаление истории
@app.route('/story/<int:story_id>/delete', methods=['POST'])
def delete_story(story_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    story = Story.query.get( story_id)
    if not story:
        return jsonify({'error': 'История не найдена'}), 404
    
    if story.user_id != session['user_id']:
        return jsonify({'error': 'Вы не можете удалить эту историю'}), 403
    
    db.session.delete(story)
    db.session.commit()
    
    return jsonify({'success': True})

# ─── Избранное ────────────────────────────────────────────────────────────────

@app.route('/favorites')
def get_favorites():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    favs = FavoriteMessage.query.filter_by(user_id=session['user_id']).order_by(FavoriteMessage.saved_at.desc()).all()
    result = []
    for f in favs:
        content = f.saved_content or ''
        msg_type = f.saved_type or 'text'
        timestamp = f.saved_at.strftime('%H:%M %d.%m.%Y')
        # Пробуем взять актуальный контент из оригинала
        if f.message and not f.message.is_deleted:
            content = f.message.content or content
            msg_type = f.message.message_type or msg_type
        elif f.group_message and not f.group_message.is_deleted:
            content = f.group_message.content or content
        result.append({
            'id': f.id,
            'content': content,
            'type': msg_type,
            'saved_at': timestamp
        })
    return jsonify({'favorites': result})

@app.route('/favorites/add', methods=['POST'])
def add_to_favorites():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    msg_id = data.get('message_id')
    grp_msg_id = data.get('group_message_id')
    if not msg_id and not grp_msg_id:
        return jsonify({'error': 'Не указано сообщение'}), 400

    # Проверяем дубликат
    existing = FavoriteMessage.query.filter_by(
        user_id=session['user_id'],
        message_id=msg_id if msg_id else None,
        group_message_id=grp_msg_id if grp_msg_id else None
    ).first()
    if existing:
        return jsonify({'success': True, 'already': True})

    content, msg_type = '', 'text'
    if msg_id:
        msg = Message.query.get(msg_id)
        if msg:
            content = msg.content or ''
            msg_type = msg.message_type or 'text'
    elif grp_msg_id:
        msg = GroupMessage.query.get(grp_msg_id)
        if msg:
            content = msg.content or ''

    fav = FavoriteMessage(
        user_id=session['user_id'],
        message_id=msg_id,
        group_message_id=grp_msg_id,
        saved_content=content,
        saved_type=msg_type
    )
    db.session.add(fav)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/favorites/note', methods=['POST'])
def add_favorite_note():
    """Отправить заметку себе (текст сохраняется прямо в избранное)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'Пустое сообщение'}), 400
    if len(content) > 4096:
        return jsonify({'error': 'Слишком длинное сообщение'}), 400
    fav = FavoriteMessage(
        user_id=session['user_id'],
        saved_content=content,
        saved_type='text'
    )
    db.session.add(fav)
    db.session.commit()
    return jsonify({'success': True, 'id': fav.id, 'saved_at': fav.saved_at.strftime('%H:%M %d.%m.%Y'), 'content': content})

@app.route('/favorites/<int:fav_id>/delete', methods=['POST'])
def delete_favorite(fav_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    fav = FavoriteMessage.query.get(fav_id)
    if not fav or fav.user_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    db.session.delete(fav)
    db.session.commit()
    return jsonify({'success': True})

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        try:
            user = User.query.get(session['user_id'])
            if not user:
                return False
            
            user_id = session['user_id']
            
            # Добавляем пользователя в список онлайн
            online_users[user_id] = {
                'username': user.username,
                'display_name': user.display_name or user.username,
                'sid': request.sid
            }
            
            join_room(f"user_{user_id}")
            
            # Отправляем уведомление о том, что пользователь онлайн всем остальным
            socketio.emit('user_online', {
                'user_id': user_id,
                'username': user.username,
                'display_name': user.display_name or user.username
            }, skip_sid=request.sid, namespace='/')
            
            # Отправляем текущему пользователю список всех онлайн пользователей
            emit('online_users_list', {
                'online_users': [
                    {'user_id': uid, 'username': data['username'], 'display_name': data['display_name']}
                    for uid, data in online_users.items()
                ]
            })
            
            emit('status', {'status': 'connected', 'user_id': user_id})
        except Exception as e:
            print(f"Error in handle_connect: {e}")
            return False

@socketio.on('join')
def on_join(data):
    user_id = data.get('user_id')
    if user_id:
        join_room(f"user_{user_id}")

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        try:
            user_id = session['user_id']
            
            # Удаляем пользователя из списка онлайн
            if user_id in online_users:
                del online_users[user_id]
            
            user = User.query.get( session['user_id'])
            if user:
                user.last_seen = datetime.utcnow()
                db.session.commit()
                
                leave_room(f"user_{user_id}")
                
                # Отправляем уведомление о том, что пользователь офлайн всем остальным
                socketio.emit('user_offline', {
                    'user_id': user_id,
                    'username': user.username,
                    'last_seen': user.last_seen.isoformat()
                }, skip_sid=request.sid, namespace='/')
        except Exception as e:
            print(f"Error in handle_disconnect: {e}")

# ============================================
# ГРУППЫ И КАНАЛЫ
# ============================================

@app.route('/groups/create', methods=['POST'])
def create_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        username = (data.get('username') or '').strip()
        description = (data.get('description') or '').strip()
        is_channel = data.get('is_channel', False)
        is_public = data.get('is_public', True)
        
        if not name or len(name) > 100:
            return jsonify({'error': 'Некорректное название'}), 400
        
        # Проверяем username если указан
        if username:
            if len(username) < 3 or len(username) > 50:
                return jsonify({'error': 'Username должен содержать от 3 до 50 символов'}), 400
            
            # Проверяем что username содержит только разрешенные символы
            if not all(c.isalnum() or c == '_' for c in username):
                return jsonify({'error': 'Username может содержать только буквы, цифры и подчеркивание'}), 400
            
            # Проверяем уникальность
            existing = Group.query.filter_by(username=username).first()
            if existing:
                return jsonify({'error': 'Этот username уже занят'}), 400
        
        # Создаем группу
        group = Group(
            name=name,
            username=username if username else None,
            description=description,
            creator_id=session['user_id'],
            is_channel=is_channel,
            is_public=is_public,
            avatar_color=f"#{random.randint(0, 0xFFFFFF):06x}"
        )
        db.session.add(group)
        db.session.commit()
        
        # Добавляем создателя как админа
        member = GroupMember(
            group_id=group.id,
            user_id=session['user_id'],
            is_admin=True
        )
        db.session.add(member)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'group': {
                'id': group.id,
                'name': group.name,
                'username': group.username,
                'description': group.description,
                'is_channel': group.is_channel,
                'avatar_color': group.avatar_color
            }
        })
    except Exception as e:
        print(f"Error creating group: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({'error': f'Ошибка создания группы: {str(e)}'}), 500

@app.route('/groups')
def get_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        # Получаем группы пользователя
        memberships = GroupMember.query.filter_by(user_id=session['user_id']).all()
        
        groups_data = []
        for membership in memberships:
            group = membership.group
            
            # Получаем последнее сообщение
            last_message = GroupMessage.query.filter_by(group_id=group.id).order_by(GroupMessage.timestamp.desc()).first()
            
            # Считаем участников
            members_count = GroupMember.query.filter_by(group_id=group.id).count()
            
            # Используем timestamp последнего сообщения или время создания группы для сортировки
            sort_timestamp = last_message.timestamp if last_message else group.created_at
            
            # Подсчитываем непрочитанные сообщения
            last_read = LastReadGroupMessage.query.filter_by(
                user_id=session['user_id'],
                group_id=group.id
            ).first()
            
            # Считаем непрочитанные сообщения в группе
            unread_query = GroupMessage.query.filter(
                GroupMessage.group_id == group.id,
                GroupMessage.sender_id != session['user_id']  # Не считаем свои сообщения
            )
            
            if last_read and last_read.last_read_message_id:
                # Считаем сообщения после последнего прочитанного
                unread_query = unread_query.filter(GroupMessage.id > last_read.last_read_message_id)
            
            unread_count = unread_query.count()
            
            groups_data.append({
                'id': group.id,
                'name': group.name,
                'username': group.username,
                'description': group.description,
                'is_channel': group.is_channel,
                'avatar_color': group.avatar_color,
                'avatar_url': group.avatar_url,
                'avatar_letter': group.name[0].upper(),
                'members_count': members_count,
                'is_admin': membership.is_admin,
                'last_message': last_message.content[:50] if last_message else None,
                'last_message_time': sort_timestamp.isoformat() + 'Z',  # ISO формат UTC для клиента
                'last_message_display': sort_timestamp.strftime('%H:%M') if last_message else None,  # Для отображения
                'unread_count': unread_count
            })
        
        # Сортируем по времени последнего сообщения (новые сверху)
        groups_data.sort(key=lambda x: x['last_message_time'], reverse=True)
        
        return jsonify({'groups': groups_data})
    except Exception as e:
        print(f"Error in get_groups: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Ошибка загрузки групп', 'details': str(e)}), 500

@app.route('/groups/<int:group_id>')
def get_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем членство
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Вы не состоите в этой группе'}), 403
    
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    
    # Получаем сообщения
    messages = GroupMessage.query.filter_by(group_id=group_id).order_by(GroupMessage.timestamp.asc()).limit(100).all()
    
    messages_data = []
    for msg in messages:
        sender = msg.sender
        
        # Получаем медиафайлы для сообщения
        media_files = GroupMessageMedia.query.filter_by(message_id=msg.id).order_by(GroupMessageMedia.order_index).all()
        media_data = [{
            'media_type': m.media_type,
            'media_url': m.media_url,
            'file_name': m.file_name,
            'file_size': m.file_size
        } for m in media_files]
        
        messages_data.append({
            'id': msg.id,
            'sender_id': msg.sender_id,
            'sender_name': sender.display_name or sender.username,
            'sender_avatar_color': sender.avatar_color,
            'sender_avatar_url': sender.avatar_url,
            'sender_avatar_letter': sender.get_avatar_letter(),
            'content': msg.content,
            'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': msg.timestamp.isoformat() + 'Z',
            'edited_at': msg.edited_at.strftime('%H:%M %d.%m') if msg.edited_at else None,
            'is_deleted': msg.is_deleted,
            'is_mine': msg.sender_id == session['user_id'],
            'media_files': media_data,
            'reply_to': {
                'id': msg.reply_to.id,
                'content': msg.reply_to.content if not msg.reply_to.is_deleted else '[удалено]',
                'sender_name': (msg.reply_to.sender.display_name or msg.reply_to.sender.username) if msg.reply_to.sender else '?'
            } if msg.reply_to_id and msg.reply_to else None
        })
    
    # Получаем участников
    members = GroupMember.query.filter_by(group_id=group_id).all()
    members_data = []
    for member in members:
        user = member.user
        members_data.append({
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name or user.username,
            'avatar_color': user.avatar_color,
            'avatar_url': user.avatar_url,
            'avatar_letter': user.get_avatar_letter(),
            'is_admin': member.is_admin,
            'is_self': user.id == session['user_id']
        })
    
    return jsonify({
        'group': {
            'id': group.id,
            'name': group.name,
            'username': group.username,
            'description': group.description,
            'is_channel': group.is_channel,
            'is_public': group.is_public,
            'invite_link': group.invite_link,
            'avatar_color': group.avatar_color,
            'avatar_url': group.avatar_url,
            'avatar_letter': group.name[0].upper() if group.name else 'G',
            'is_admin': membership.is_admin,
            'is_muted': membership.is_muted,
            'is_creator': group.creator_id == session['user_id']
        },
        'messages': messages_data,
        'members': members_data
    })

@app.route('/groups/<int:group_id>/send', methods=['POST'])
def send_group_message(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем членство
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Вы не состоите в этой группе'}), 403
    
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    
    # Если это канал, только админы могут писать
    if group.is_channel and not membership.is_admin:
        return jsonify({'error': 'Только админы могут писать в канале'}), 403
    
    data = request.get_json()
    content = data.get('content', '').strip()
    
    if not content or len(content) > 4096:
        return jsonify({'error': 'Некорректное сообщение'}), 400
    
    # Создаем сообщение
    message = GroupMessage(
        group_id=group_id,
        sender_id=session['user_id'],
        content=content,
        reply_to_id=data.get('reply_to_id') or None
    )
    db.session.add(message)
    db.session.commit()
    
    sender = User.query.get(session['user_id'])
    
    # Формируем данные сообщения
    message_data = {
        'id': message.id,
        'sender_id': sender.id,
        'sender_name': sender.display_name or sender.username,
        'sender_avatar_color': sender.avatar_color,
        'sender_avatar_url': sender.avatar_url,
        'sender_avatar_letter': sender.get_avatar_letter(),
        'content': content,
        'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': message.timestamp.isoformat() + 'Z',
        'media_files': []
    }
    
    # Отправляем через Socket.IO всем участникам группы (включая отправителя)
    socketio.emit('new_group_message', {
        'group_id': group_id,
        'message': message_data
    }, room=f'group_{group_id}', include_self=True)
    
    return jsonify({
        'success': True,
        'message': message_data
    })

@app.route('/groups/<int:group_id>/messages/<int:message_id>/delete', methods=['POST'])
def delete_group_message(group_id, message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Нет доступа'}), 403
    msg = GroupMessage.query.filter_by(id=message_id, group_id=group_id).first()
    if not msg:
        return jsonify({'error': 'Сообщение не найдено'}), 404
    if msg.sender_id != session['user_id'] and not membership.is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    msg.is_deleted = True
    msg.content = '[Сообщение удалено]'
    db.session.commit()
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/join', methods=['POST'])
def join_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    
    # Проверяем, не состоит ли уже
    existing = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if existing:
        return jsonify({'error': 'Вы уже состоите в этой группе'}), 400
    
    # Добавляем участника
    member = GroupMember(
        group_id=group_id,
        user_id=session['user_id'],
        is_admin=False
    )
    db.session.add(member)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/leave', methods=['POST'])
def leave_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Вы не состоите в этой группе'}), 404
    
    db.session.delete(membership)
    db.session.commit()
    
    return jsonify({'success': True})

# Обновление настроек группы
@app.route('/groups/<int:group_id>/update', methods=['POST'])
def update_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут изменять настройки'}), 403
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    data = request.get_json() or {}
    if 'name' in data:
        name = data['name'].strip()
        if not name or len(name) > 100:
            return jsonify({'error': 'Некорректное название'}), 400
        group.name = name
    if 'description' in data:
        group.description = data['description'].strip()[:500]
    if 'username' in data:
        uname = data['username'].strip().lower()
        if uname:
            if len(uname) < 3 or len(uname) > 50:
                return jsonify({'error': 'Username: от 3 до 50 символов'}), 400
            if not all(c.isalnum() or c == '_' for c in uname):
                return jsonify({'error': 'Username: только буквы, цифры и _'}), 400
            existing = Group.query.filter(Group.username == uname, Group.id != group_id).first()
            if existing:
                return jsonify({'error': 'Username уже занят'}), 400
            group.username = uname
        else:
            group.username = None
    if 'is_public' in data:
        group.is_public = bool(data['is_public'])
    db.session.commit()
    return jsonify({'success': True, 'group': {
        'id': group.id, 'name': group.name, 'description': group.description,
        'username': group.username, 'is_public': group.is_public
    }})

# Удаление группы
@app.route('/groups/<int:group_id>/delete', methods=['POST'])
def delete_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    if group.creator_id != session['user_id']:
        return jsonify({'error': 'Только создатель может удалить группу'}), 403
    GroupMember.query.filter_by(group_id=group_id).delete()
    GroupMessage.query.filter_by(group_id=group_id).delete()
    db.session.delete(group)
    db.session.commit()
    return jsonify({'success': True})

# Удалить участника из группы
@app.route('/groups/<int:group_id>/members/<int:user_id>/remove', methods=['POST'])
def remove_group_member(group_id, user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут удалять участников'}), 403
    group = Group.query.get(group_id)
    if group and group.creator_id == user_id:
        return jsonify({'error': 'Нельзя удалить создателя группы'}), 400
    target = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not target:
        return jsonify({'error': 'Участник не найден'}), 404
    db.session.delete(target)
    db.session.commit()
    return jsonify({'success': True})

# Повысить/понизить участника
@app.route('/groups/<int:group_id>/members/<int:user_id>/role', methods=['POST'])
def set_group_member_role(group_id, user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    if group.creator_id != session['user_id']:
        return jsonify({'error': 'Только создатель может управлять ролями'}), 403
    target = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not target:
        return jsonify({'error': 'Участник не найден'}), 404
    data = request.get_json() or {}
    target.is_admin = bool(data.get('is_admin', False))
    db.session.commit()
    return jsonify({'success': True, 'is_admin': target.is_admin})

# Инвайт-ссылка
@app.route('/groups/<int:group_id>/invite_link', methods=['GET', 'POST'])
def group_invite_link(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут управлять ссылкой'}), 403
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    if request.method == 'POST':
        # Перегенерировать ссылку
        group.invite_link = secrets.token_urlsafe(16)
        db.session.commit()
    elif not group.invite_link:
        group.invite_link = secrets.token_urlsafe(16)
        db.session.commit()
    return jsonify({'success': True, 'invite_link': group.invite_link})

# Вступить по инвайт-ссылке
@app.route('/invite/<token>')
def join_by_invite(token):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    group = Group.query.filter_by(invite_link=token).first()
    if not group:
        return redirect(url_for('index'))
    existing = GroupMember.query.filter_by(group_id=group.id, user_id=session['user_id']).first()
    if not existing:
        member = GroupMember(group_id=group.id, user_id=session['user_id'], is_admin=False)
        db.session.add(member)
        db.session.commit()
    return redirect(url_for('index'))

# Включить/выключить уведомления группы
@app.route('/groups/<int:group_id>/mute', methods=['POST'])
def toggle_group_mute(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Вы не состоите в этой группе'}), 403
    membership.is_muted = not membership.is_muted
    db.session.commit()
    return jsonify({'success': True, 'is_muted': membership.is_muted})

# Загрузка аватарки группы
@app.route('/groups/<int:group_id>/upload_avatar', methods=['POST'])
def upload_group_avatar(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем, что пользователь - админ группы
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут изменять аватарку группы'}), 403
    
    if 'avatar' not in request.files:
        return jsonify({'error': 'Файл не найден'}), 400
    
    file = request.files['avatar']
    
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    
    if not allowed_file(file.filename, ALLOWED_IMAGES):
        return jsonify({'error': 'Недопустимый формат файла. Разрешены: PNG, JPG, JPEG, GIF, WEBP'}), 400
    
    try:
        group = Group.query.get(group_id)
        
        # Создаем папку для аватарок групп если её нет
        avatars_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'group_avatars')
        os.makedirs(avatars_folder, exist_ok=True)
        
        # Генерируем уникальное имя файла
        timestamp = int(time.time())
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"group_{group_id}_{timestamp}.{ext}"
        filepath = os.path.join(avatars_folder, filename)
        
        # Удаляем старую аватарку если есть
        if group.avatar_url:
            old_path = os.path.join('static', group.avatar_url.lstrip('/static/'))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
        
        # Сохраняем новую аватарку
        file.save(filepath)
        
        # Обновляем URL в базе данных
        group.avatar_url = f"/static/media/group_avatars/{filename}"
        db.session.commit()
        
        return jsonify({
            'success': True,
            'avatar_url': group.avatar_url
        })
        
    except Exception as e:
        print(f"Error uploading group avatar: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Ошибка загрузки аватарки'}), 500

# Удаление аватарки группы
@app.route('/groups/<int:group_id>/delete_avatar', methods=['POST'])
def delete_group_avatar(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем, что пользователь - админ группы
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут удалять аватарку группы'}), 403
    
    try:
        group = Group.query.get(group_id)
        
        # Удаляем файл аватарки
        if group.avatar_url:
            old_path = os.path.join('static', group.avatar_url.lstrip('/static/'))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
        
        # Удаляем URL из базы данных
        group.avatar_url = None
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error deleting group avatar: {e}")
        return jsonify({'error': 'Ошибка удаления аватарки'}), 500

@app.route('/groups/<int:group_id>/add_member', methods=['POST'])
def add_group_member(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем, что текущий пользователь - админ группы
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут добавлять участников'}), 403
    
    data = request.get_json()
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'error': 'Не указан пользователь'}), 400
    
    # Проверяем, существует ли пользователь
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    
    # Проверяем, не состоит ли уже
    existing = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if existing:
        return jsonify({'error': 'Пользователь уже в группе'}), 400
    
    # Добавляем участника
    member = GroupMember(
        group_id=group_id,
        user_id=user_id,
        is_admin=False
    )
    db.session.add(member)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/users/search_for_group')
def search_users_for_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    query = request.args.get('q', '').strip()
    group_id = request.args.get('group_id')
    
    if not query:
        return jsonify({'users': []})
    
    # Ищем пользователей
    users = User.query.filter(
        User.username.like(f'%{query}%'),
        User.id != session['user_id']
    ).limit(10).all()
    
    # Если указана группа, исключаем уже состоящих
    if group_id:
        existing_members = [m.user_id for m in GroupMember.query.filter_by(group_id=group_id).all()]
        users = [u for u in users if u.id not in existing_members]
    
    users_data = []
    for user in users:
        users_data.append({
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name or user.username,
            'avatar_color': user.avatar_color,
            'avatar_letter': user.get_avatar_letter(),
            'bio': user.bio
        })
    
    return jsonify({'users': users_data})

# Socket.IO для групп
@socketio.on('join_group')
def handle_join_group(data):
    group_id = data.get('group_id')
    if group_id and 'user_id' in session:
        # Проверяем членство
        membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
        if membership:
            join_room(f'group_{group_id}')
            print(f"User {session['user_id']} joined group {group_id}")

@socketio.on('leave_group')
def handle_leave_group(data):
    group_id = data.get('group_id')
    if group_id:
        leave_room(f'group_{group_id}')
        print(f"User {session.get('user_id')} left group {group_id}")

# ============================================
# БОТЫ
# ============================================

# Список всех ботов для модерации (только для админов)
@app.route('/admin/bots', methods=['GET'])
def admin_get_bots():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403

    bots = Bot.query.order_by(Bot.created_at.desc()).all()
    return jsonify({'bots': [{
        'id': b.id,
        'name': b.bot_user.display_name,
        'username': b.bot_user.username,
        'description': b.description or '',
        'owner_username': b.owner.username,
        'owner_display_name': b.owner.display_name or b.owner.username,
        'review_status': b.review_status or 'pending',
        'review_note': b.review_note or '',
        'is_active': b.is_active,
        'created_at': b.created_at.strftime('%d.%m.%Y %H:%M'),
        'webhook_url': b.webhook_url or ''
    } for b in bots]})

# Одобрить бота
@app.route('/admin/bots/<int:bot_id>/approve', methods=['POST'])
def admin_approve_bot(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({'error': 'Бот не найден'}), 404

    data = request.get_json() or {}
    bot.review_status = 'approved'
    bot.review_note = data.get('note', '').strip() or None
    bot.is_active = True
    db.session.commit()
    return jsonify({'success': True})

# Отклонить бота
@app.route('/admin/bots/<int:bot_id>/reject', methods=['POST'])
def admin_reject_bot(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({'error': 'Бот не найден'}), 404

    data = request.get_json() or {}
    bot.review_status = 'rejected'
    bot.review_note = data.get('note', '').strip() or None
    bot.is_active = False
    db.session.commit()
    return jsonify({'success': True})

def _get_bot_buttons_for_msg(msg):
    """Возвращает кнопки для сообщения бота из колонки bot_buttons."""
    try:
        return json.loads(msg.bot_buttons or '[]')
    except Exception:
        return []

def _bot_send_message(bot_user_id, receiver_id, content, buttons=None):
    """Внутренняя функция отправки сообщения от бота"""
    message = Message(
        sender_id=bot_user_id,
        receiver_id=receiver_id,
        content=content,
        bot_buttons=json.dumps(buttons or [])
    )
    db.session.add(message)
    db.session.commit()

    bot_user = User.query.get(bot_user_id)
    sender_info = {
        'id': bot_user.id,
        'username': bot_user.username,
        'display_name': bot_user.display_name or bot_user.username,
        'avatar_color': bot_user.avatar_color,
        'avatar_letter': bot_user.get_avatar_letter(),
        'is_bot': True
    }
    msg_data = {
        'id': message.id,
        'sender_id': message.sender_id,
        'content': message.content,
        'message_type': 'text',
        'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
        'timestamp_iso': message.timestamp.isoformat() + 'Z',
        'bot_buttons': buttons or [],
    }
    socketio.emit('new_message', {
        'message': {**msg_data, 'is_mine': True},
        'other_user_id': receiver_id,
        'sender_info': sender_info
    }, room=f'user_{bot_user_id}', namespace='/')
    socketio.emit('new_message', {
        'message': {**msg_data, 'is_mine': False},
        'other_user_id': bot_user_id,
        'sender_info': sender_info
    }, room=f'user_{receiver_id}', namespace='/')
    return message

# Словарь для отслеживания состояния диалога поддержки
# user_id -> 'waiting_message' | 'waiting_close_confirm'
_support_pending = {}

def _trigger_webhook(bot, update):
    """Отправляет update на webhook бота. Если webhook не задан — ищет команду в конструкторе."""
    text = ''
    try:
        text = update.get('message', {}).get('text', '').strip()
    except Exception:
        pass

    if not bot.webhook_url:
        if text:
            sender_id = None
            try:
                sender_id = update['message']['from']['id']
            except Exception:
                pass
            if sender_id:
                bot_user = User.query.get(bot.user_id)
                is_support = bot_user and bot_user.username == 'tabletone_supportbot'

                if is_support:
                    state = _support_pending.get(sender_id)

                    # Если отправитель — администратор, показываем меню поддержки
                    sender_user = User.query.get(sender_id)
                    sender_is_admin = sender_user and sender_user.is_admin

                    if sender_is_admin:
                        # Кнопка "Закрыть диалог"
                        if text.lower().startswith('/close_support_'):
                            try:
                                target_user_id = int(text.split('_')[-1])
                                _support_pending[target_user_id] = 'waiting_close_confirm'
                                target_user = User.query.get(target_user_id)
                                target_name = (target_user.display_name or target_user.username) if target_user else str(target_user_id)
                                _bot_send_message(bot.user_id, target_user_id, "💬 Администрация: Ваш вопрос решён?")
                                _bot_send_message(bot.user_id, sender_id, f"Запрос на закрытие диалога с {target_name} отправлен.")
                            except (ValueError, IndexError):
                                pass
                            return

                        # Показываем открытые тикеты при любом сообщении от админа
                        tickets = SupportTicket.query.filter_by(status='open').order_by(SupportTicket.created_at.desc()).limit(10).all()
                        if not tickets:
                            _bot_send_message(bot.user_id, sender_id,
                                "✅ Открытых обращений нет.\n\nКак только пользователи напишут — вы получите уведомление.")
                        else:
                            _bot_send_message(bot.user_id, sender_id,
                                f"📬 Открытых обращений: {len(tickets)}\n\nПоследние обращения:")
                            for t in tickets:
                                u = User.query.get(t.user_id)
                                uname = (u.username if u else str(t.user_id))
                                udisp = (u.display_name or u.username if u else str(t.user_id))
                                msg_text = (
                                    f"📩 Обращение #{t.id}\n"
                                    f"От: @{uname} ({udisp})\n"
                                    f"Дата: {t.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                                    f"{t.message_text}"
                                )
                                close_btns = [{"label": "✅ Закрыть диалог", "reply": f"/close_support_{t.user_id}"}]
                                _bot_send_message(bot.user_id, sender_id, msg_text, buttons=close_btns)
                        return

                    # ── Обычный пользователь ──────────────────────────────────

                    # Пользователь ждёт подтверждения закрытия
                    if state == 'waiting_close_confirm':
                        answer = text.strip().lower()
                        if answer in ('да', 'yes', 'д', 'y', '👍'):
                            _support_pending.pop(sender_id, None)
                            # Закрываем открытые тикеты пользователя
                            SupportTicket.query.filter_by(user_id=sender_id, status='open').update(
                                {'status': 'closed', 'closed_at': datetime.utcnow()})
                            db.session.commit()
                            _bot_send_message(bot.user_id, sender_id,
                                "✅ Диалог был закрыт! Для повторного обращения напишите /start")
                        else:
                            _support_pending[sender_id] = 'waiting_message'
                            _bot_send_message(bot.user_id, sender_id,
                                "Хорошо, диалог остаётся открытым. Напишите ваш следующий вопрос.")
                        return

                    # Администратор нажал кнопку "Закрыть диалог" (/close_support_<user_id>)
                    if text.lower().startswith('/close_support_'):
                        try:
                            target_user_id = int(text.split('_')[-1])
                            _support_pending[target_user_id] = 'waiting_close_confirm'
                            target_user = User.query.get(target_user_id)
                            target_name = (target_user.display_name or target_user.username) if target_user else str(target_user_id)
                            # Уведомляем пользователя
                            _bot_send_message(bot.user_id, target_user_id,
                                "💬 Администрация: Ваш вопрос решён?")
                            # Подтверждение админу
                            _bot_send_message(bot.user_id, sender_id,
                                f"Запрос на закрытие диалога с {target_name} отправлен. Ожидаем ответа пользователя.")
                        except (ValueError, IndexError):
                            pass
                        return

                    # Администратор запросил список тикетов
                    if text.lower() == '/view_support':
                        tickets = SupportTicket.query.filter_by(status='open').order_by(SupportTicket.created_at.desc()).limit(10).all()
                        if not tickets:
                            _bot_send_message(bot.user_id, sender_id, "✅ Открытых обращений нет.")
                        else:
                            for t in tickets:
                                u = User.query.get(t.user_id)
                                uname = (u.username if u else str(t.user_id))
                                udisp = (u.display_name or u.username if u else str(t.user_id))
                                msg_text = (
                                    f"📩 Обращение #{t.id}\n"
                                    f"От: @{uname} ({udisp})\n"
                                    f"Дата: {t.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                                    f"{t.message_text}"
                                )
                                close_btns = [{"label": "✅ Закрыть диалог", "reply": f"/close_support_{t.user_id}"}]
                                _bot_send_message(bot.user_id, sender_id, msg_text, buttons=close_btns)
                        return

                    # Пользователь пишет сообщение в поддержку
                    if state == 'waiting_message':
                        _support_pending.pop(sender_id, None)
                        _handle_support_message(bot, sender_id, text)
                        return

                    # Команда /support — ставим флаг ожидания сообщения
                    if text.lower() == '/support':
                        _support_pending[sender_id] = 'waiting_message'
                        _bot_auto_reply(bot, sender_id, text)
                        return

                    # Любое произвольное сообщение не-команда → сразу в поддержку
                    if not text.startswith('/'):
                        _handle_support_message(bot, sender_id, text)
                        return

                _bot_auto_reply(bot, sender_id, text)
        return

    import threading
    import urllib.request
    def _post():
        try:
            data = json.dumps(update).encode('utf-8')
            req = urllib.request.Request(
                bot.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f'Webhook error for bot {bot.id}: {e}')
    threading.Thread(target=_post, daemon=True).start()


def _bot_auto_reply(bot, sender_id, text):
    """Ищет подходящую команду и отвечает пользователю."""
    commands = BotCommand.query.filter_by(bot_id=bot.id).order_by(BotCommand.order_index).all()
    matched = None
    fallback = None
    for cmd in commands:
        if cmd.trigger == '*':
            fallback = cmd
        elif text.lower().startswith(cmd.trigger.lower()):
            matched = cmd
            break
    cmd = matched or fallback
    if not cmd:
        return
    reply = cmd.response_text
    buttons = []
    try:
        buttons = json.loads(cmd.buttons or '[]')
    except Exception:
        pass
    _bot_send_message(bot.user_id, sender_id, reply, buttons=buttons)


# ── Вспомогательные функции для системных ботов ─────────────────────────────

def _send_tabletone_welcome(user_id):
    """Отправляет приветственное сообщение от @tabletonebot новому пользователю."""
    bot_user = User.query.filter_by(username='tabletonebot').first()
    if not bot_user:
        return
    welcome = (
        "👋 Добро пожаловать в Tabletone!\n\n"
        "Я официальный бот мессенджера. Вот что тебя ждёт:\n\n"
        "💬 Личные чаты и групповые беседы\n"
        "📢 Каналы для публикаций\n"
        "🤖 Боты — создавай своих или используй готовых\n"
        "🎙 Голосовые сообщения и видео-кружочки\n"
        "📎 Отправка файлов, фото и видео\n\n"
        "👑 Хочешь больше возможностей? Оформи Premium:\n"
        "• Загрузка аватара\n"
        "• Смена обоев чата\n"
        "• Неограниченное количество ботов\n"
        "• Кастомный эмодзи в профиле\n\n"
        "Напиши боту @tabletone_premiumbot чтобы узнать подробнее.\n\n"
        "🔒 Совет по безопасности: включи двухэтапную аутентификацию в Профиле → Безопасность.\n\n"
        "Приятного общения! 🚀"
    )
    _bot_send_message(bot_user.id, user_id, welcome)


def _send_2fa_code(user_id, code):
    """Отправляет код 2FA через @tabletonebot и на email если указан."""
    bot_user = User.query.filter_by(username='tabletonebot').first()
    text = (
        f"🔐 Код для входа в Tabletone:\n\n"
        f"  {code}  \n\n"
        f"⏱ Код действителен 10 минут.\n\n"
        f"⚠️ НЕ ПЕРЕДАВАЙТЕ ЭТОТ КОД ТРЕТЬИМ ЛИЦАМ!\n"
        f"Администрация Tabletone никогда не запрашивает коды."
    )
    if bot_user:
        _bot_send_message(bot_user.id, user_id, text)


def _send_email_2fa(to_email, code):
    """Отправляет код 2FA на email через Gmail SMTP."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    if not smtp_user or not smtp_pass:
        print("SMTP не настроен — пропускаем email 2FA")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'Код входа в Tabletone: {code}'
    msg['From'] = f'Tabletone <{smtp_user}>'
    msg['To'] = to_email

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:30px;background:#f7f8fc;border-radius:12px;">
      <h2 style="color:#667eea;margin-bottom:8px;">🔐 Код входа в Tabletone</h2>
      <p style="color:#4a5568;">Используйте этот код для входа в аккаунт:</p>
      <div style="font-size:36px;font-weight:bold;letter-spacing:8px;color:#2d3748;background:#fff;padding:20px;border-radius:8px;text-align:center;margin:20px 0;">{code}</div>
      <p style="color:#718096;font-size:13px;">⏱ Код действителен 10 минут.</p>
      <p style="color:#e53e3e;font-size:13px;">⚠️ Никому не передавайте этот код. Администрация Tabletone никогда не запрашивает коды.</p>
    </div>
    """
    msg.attach(MIMEText(html, 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())


def _notify_admin_support(admin_user_id):
    """Отправляет администратору уведомление об открытых обращениях при логине."""
    open_count = SupportTicket.query.filter_by(status='open').count()
    if open_count == 0:
        return
    bot_user = User.query.filter_by(username='tabletone_supportbot').first()
    if not bot_user:
        return
    buttons = json.dumps([
        {"label": "📋 Просмотреть сообщения", "reply": "/view_support"}
    ])
    n = open_count
    if 11 <= n % 100 <= 19:
        word = 'сообщений'
    elif n % 10 == 1:
        word = 'сообщение'
    elif 2 <= n % 10 <= 4:
        word = 'сообщения'
    else:
        word = 'сообщений'
    text = (
        f"👋 Приветствую!\n\n"
        f"У вас {open_count} {word} в поддержке."
    )
    _bot_send_message(bot_user.id, admin_user_id, text, buttons=json.loads(buttons))


def _handle_support_message(bot, sender_id, text):
    """Пересылает сообщение пользователя администратору и сохраняет тикет."""
    sender = User.query.get(sender_id)
    sender_name = (sender.display_name or sender.username) if sender else f'id={sender_id}'
    sender_username = sender.username if sender else str(sender_id)

    # Сохраняем тикет
    ticket = SupportTicket(user_id=sender_id, message_text=text)
    db.session.add(ticket)
    db.session.commit()

    # Считаем открытые тикеты
    open_count = SupportTicket.query.filter_by(status='open').count()

    admin_text = (
        f"📩 Новое обращение в поддержку\n"
        f"От: @{sender_username} ({sender_name})\n\n"
        f"{text}\n\n"
        f"Всего открытых обращений: {open_count}"
    )
    close_buttons = json.dumps([
        {"label": "✅ Закрыть диалог", "reply": f"/close_support_{sender_id}"}
    ])
    bot_user = User.query.filter_by(username='tabletone_supportbot').first()
    if bot_user:
        owner_user = User.query.filter_by(username='romancev228').first()
        if owner_user:
            _bot_send_message(bot_user.id, owner_user.id, admin_text, buttons=json.loads(close_buttons))

    # Подтверждение пользователю
    _bot_send_message(bot.user_id, sender_id,
        "✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время!\n\n"
        "⏱ Время ответа: обычно до 24 часов.")


# ── Маршруты 2FA ─────────────────────────────────────────────────────────────

@app.route('/login/2fa', methods=['GET', 'POST'])
def login_2fa():
    pending_id = session.get('2fa_pending_user_id')
    if not pending_id:
        return redirect(url_for('login'))

    if request.method == 'POST':
        entered = request.form.get('code', '').strip()
        user = User.query.get(pending_id)
        if not user:
            session.pop('2fa_pending_user_id', None)
            return redirect(url_for('login'))

        if (user.two_fa_code == entered and
                user.two_fa_code_expires and
                datetime.utcnow() < user.two_fa_code_expires):
            # Код верный — завершаем вход
            user.two_fa_code = None
            user.two_fa_code_expires = None
            db.session.commit()
            session.pop('2fa_pending_user_id', None)
            session['user_id'] = user.id
            user.last_seen = datetime.utcnow()
            # Создаём сессию
            session_token = secrets.token_urlsafe(32)
            user_agent = request.headers.get('User-Agent', '')
            ip_address = request.remote_addr
            device_name = 'Unknown Device'
            if 'Windows' in user_agent: device_name = 'Windows PC'
            elif 'Mac' in user_agent: device_name = 'Mac'
            elif 'Linux' in user_agent: device_name = 'Linux PC'
            elif 'iPhone' in user_agent: device_name = 'iPhone'
            elif 'iPad' in user_agent: device_name = 'iPad'
            elif 'Android' in user_agent: device_name = 'Android'
            new_session = UserSession(
                user_id=user.id, session_token=session_token,
                device_name=device_name, ip_address=ip_address, user_agent=user_agent
            )
            db.session.add(new_session)
            session['session_token'] = session_token
            db.session.commit()
            return redirect(url_for('index'))
        else:
            return render_template('login_2fa.html', error='Неверный или просроченный код')

    return render_template('login_2fa.html')


@app.route('/profile/2fa/enable', methods=['POST'])
def enable_2fa():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if user.two_fa_enabled:
        return jsonify({'error': 'Уже включено'}), 400
    code = str(random.randint(100000, 999999))
    from datetime import timedelta
    user.two_fa_code = code
    user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=10)
    user.two_fa_enabled = True
    db.session.commit()
    try:
        _send_2fa_code(user.id, code)
    except Exception as e:
        print(f"2FA enable send error: {e}")
    return jsonify({'success': True, 'message': 'Код отправлен через бота Tabletone'})


@app.route('/profile/2fa/disable', methods=['POST'])
def disable_2fa():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    user.two_fa_enabled = False
    user.two_fa_code = None
    user.two_fa_code_expires = None
    db.session.commit()
    return jsonify({'success': True})


# ── Конструктор команд бота ──────────────────────────────────────────────────

@app.route('/bots/<int:bot_id>/commands', methods=['GET'])
def get_bot_commands(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    cmds = BotCommand.query.filter_by(bot_id=bot_id).order_by(BotCommand.order_index).all()
    return jsonify({'commands': [{
        'id': c.id, 'trigger': c.trigger,
        'response_text': c.response_text,
        'buttons': json.loads(c.buttons or '[]'),
        'order_index': c.order_index
    } for c in cmds]})


@app.route('/bots/<int:bot_id>/commands', methods=['POST'])
def create_bot_command(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    data = request.get_json() or {}
    trigger = (data.get('trigger') or '').strip()
    response_text = (data.get('response_text') or '').strip()
    if not trigger or not response_text:
        return jsonify({'error': 'trigger и response_text обязательны'}), 400
    buttons = json.dumps(data.get('buttons') or [])
    max_order = db.session.query(db.func.max(BotCommand.order_index)).filter_by(bot_id=bot_id).scalar() or 0
    cmd = BotCommand(bot_id=bot_id, trigger=trigger, response_text=response_text,
                     buttons=buttons, order_index=max_order + 1)
    db.session.add(cmd)
    db.session.commit()
    return jsonify({'success': True, 'id': cmd.id})


@app.route('/bots/<int:bot_id>/commands/<int:cmd_id>/update', methods=['POST'])
def update_bot_command(bot_id, cmd_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    cmd = BotCommand.query.filter_by(id=cmd_id, bot_id=bot_id).first()
    if not cmd:
        return jsonify({'error': 'Команда не найдена'}), 404
    data = request.get_json() or {}
    if 'trigger' in data:
        cmd.trigger = data['trigger'].strip()
    if 'response_text' in data:
        cmd.response_text = data['response_text'].strip()
    if 'buttons' in data:
        cmd.buttons = json.dumps(data['buttons'] or [])
    db.session.commit()
    return jsonify({'success': True})


@app.route('/bots/<int:bot_id>/commands/<int:cmd_id>/delete', methods=['POST'])
def delete_bot_command(bot_id, cmd_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    cmd = BotCommand.query.filter_by(id=cmd_id, bot_id=bot_id).first()
    if not cmd:
        return jsonify({'error': 'Команда не найдена'}), 404
    db.session.delete(cmd)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/bots')
def bots_page():
    """Страница управления ботами"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    my_bots = Bot.query.filter_by(owner_id=session['user_id']).all()
    return render_template('bots.html', user=user, bots=my_bots)

@app.route('/bots/create', methods=['POST'])
def create_bot():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json()
    name = (data.get('name') or '').strip()
    username = (data.get('username') or '').strip().lower()
    description = (data.get('description') or '').strip()

    owner = User.query.get(session['user_id'])
    if not owner:
        return jsonify({'error': 'Не авторизован'}), 401

    # Обычные пользователи могут создать только 1 бота
    if not owner.is_premium and not owner.is_admin:
        existing_count = Bot.query.filter_by(owner_id=session['user_id']).count()
        if existing_count >= 1:
            return jsonify({'error': 'premium_required', 'message': 'Бесплатный аккаунт позволяет создать только 1 бота. Оформите Premium для неограниченного количества.'}), 403

    if not name or not username:
        return jsonify({'error': 'Имя и username обязательны'}), 400
    if not username.endswith('bot'):
        return jsonify({'error': 'Username бота должен заканчиваться на "bot"'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username уже занят'}), 400

    colors = ['#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b', '#fa709a']
    bot_user = User(
        username=username,
        display_name=name,
        bio=description,
        avatar_color=random.choice(colors),
        is_bot=True,
        password_hash=generate_password_hash(secrets.token_hex(32))  # недоступный пароль
    )
    db.session.add(bot_user)
    db.session.flush()

    token = f"{bot_user.id}:{secrets.token_urlsafe(32)}"
    bot = Bot(
        user_id=bot_user.id,
        owner_id=session['user_id'],
        token=token,
        description=description
    )
    db.session.add(bot)
    db.session.commit()
    return jsonify({'success': True, 'bot_id': bot.id, 'token': token})

@app.route('/bots/<int:bot_id>', methods=['GET'])
def get_bot(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    return jsonify({
        'id': bot.id,
        'name': bot.bot_user.display_name,
        'username': bot.bot_user.username,
        'description': bot.description,
        'token': bot.token,
        'webhook_url': bot.webhook_url,
        'is_active': bot.is_active,
        'review_status': bot.review_status or 'pending',
        'review_note': bot.review_note or '',
        'avatar_color': bot.bot_user.avatar_color,
        'avatar_letter': bot.bot_user.get_avatar_letter()
    })

@app.route('/bots/<int:bot_id>/update', methods=['POST'])
def update_bot(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    data = request.get_json()
    if 'name' in data:
        bot.bot_user.display_name = data['name'].strip()
    if 'description' in data:
        bot.description = data['description'].strip()
        bot.bot_user.bio = bot.description
    if 'webhook_url' in data:
        bot.webhook_url = data['webhook_url'].strip() or None
    db.session.commit()
    return jsonify({'success': True})

@app.route('/bots/<int:bot_id>/regenerate_token', methods=['POST'])
def regenerate_bot_token(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    bot.token = f"{bot.user_id}:{secrets.token_urlsafe(32)}"
    db.session.commit()
    return jsonify({'success': True, 'token': bot.token})

@app.route('/bots/<int:bot_id>/delete', methods=['POST'])
def delete_bot(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    bot = Bot.query.get(bot_id)
    if not bot or bot.owner_id != session['user_id']:
        return jsonify({'error': 'Не найдено'}), 404
    bot_user = bot.bot_user
    db.session.delete(bot)
    db.session.delete(bot_user)
    db.session.commit()
    return jsonify({'success': True})

# Просмотр сообщений бота (для модерации)
@app.route('/admin/bots/<int:bot_id>/messages')
def admin_bot_messages(bot_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'admin'):
        return jsonify({'error': 'Недостаточно прав'}), 403

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({'error': 'Бот не найден'}), 404

    sent = Message.query.filter_by(sender_id=bot.user_id).order_by(Message.timestamp.desc()).limit(100).all()
    received = Message.query.filter_by(receiver_id=bot.user_id).order_by(Message.timestamp.desc()).limit(100).all()

    def fmt(m, direction):
        other_id = m.receiver_id if direction == 'sent' else m.sender_id
        other = User.query.get(other_id)
        return {
            'id': m.id,
            'direction': direction,
            'other_username': other.username if other else '?',
            'other_display_name': (other.display_name or other.username) if other else '?',
            'content': m.content or f'[{m.message_type}]',
            'message_type': m.message_type,
            'timestamp': m.timestamp.strftime('%d.%m.%Y %H:%M')
        }

    messages = [fmt(m, 'sent') for m in sent] + [fmt(m, 'received') for m in received]
    messages.sort(key=lambda x: x['timestamp'], reverse=True)
    return jsonify({'messages': messages[:150], 'bot_name': bot.bot_user.display_name, 'bot_username': bot.bot_user.username})

# Bot API — отправка сообщения
@app.route('/bot/sendMessage', methods=['POST'])
def bot_send_message():
    """Bot API: отправить сообщение пользователю"""
    data = request.get_json() or {}
    token = data.get('token') or request.headers.get('X-Bot-Token')
    if not token:
        return jsonify({'error': 'Токен не указан'}), 401

    bot = Bot.query.filter_by(token=token, is_active=True).first()
    if not bot:
        return jsonify({'error': 'Неверный токен'}), 401

    chat_id = data.get('chat_id')  # user_id получателя
    text = (data.get('text') or '').strip()
    if not chat_id or not text:
        return jsonify({'error': 'chat_id и text обязательны'}), 400
    if len(text) > 4096:
        return jsonify({'error': 'Сообщение слишком длинное'}), 400

    receiver = User.query.get(int(chat_id))
    if not receiver or receiver.is_banned:
        return jsonify({'error': 'Получатель не найден'}), 404

    message = _bot_send_message(bot.user_id, int(chat_id), text)
    return jsonify({'success': True, 'message_id': message.id})

# Bot API — получить информацию о боте
@app.route('/bot/getMe', methods=['POST', 'GET'])
def bot_get_me():
    token = request.args.get('token') or (request.get_json() or {}).get('token') or request.headers.get('X-Bot-Token')
    bot = Bot.query.filter_by(token=token, is_active=True).first() if token else None
    if not bot:
        return jsonify({'error': 'Неверный токен'}), 401
    return jsonify({
        'id': bot.user_id,
        'username': bot.bot_user.username,
        'name': bot.bot_user.display_name,
        'is_bot': True
    })

# Bot API — установить webhook
@app.route('/bot/setWebhook', methods=['POST'])
def bot_set_webhook():
    data = request.get_json() or {}
    token = data.get('token') or request.headers.get('X-Bot-Token')
    bot = Bot.query.filter_by(token=token, is_active=True).first() if token else None
    if not bot:
        return jsonify({'error': 'Неверный токен'}), 401
    bot.webhook_url = data.get('url', '').strip() or None
    db.session.commit()
    return jsonify({'success': True})

# Подавление ошибок разрыва соединения
import logging
import warnings

# Полностью отключаем логи eventlet
logging.getLogger('eventlet.wsgi.server').setLevel(logging.CRITICAL)
logging.getLogger('eventlet.wsgi').setLevel(logging.CRITICAL)
logging.getLogger('eventlet').setLevel(logging.CRITICAL)

# Подавляем warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

if __name__ == '__main__':
    import sys
    
    print("=" * 50)
    print("🚀 Сервер запущен на http://localhost:5000")
    print("=" * 50)
    
    try:
        socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n👋 Сервер остановлен")
    except Exception as e:
        print(f"❌ Ошибка сервера: {e}")


