import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
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
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql+pg8000://', 1)
elif _db_url.startswith('postgresql://') and 'pg8000' not in _db_url and 'psycopg2' not in _db_url:
    _db_url = _db_url.replace('postgresql://', 'postgresql+pg8000://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'max_overflow': 10,
    'pool_timeout': 10,
    'pool_recycle': 1800,
    'pool_pre_ping': True,
}
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


def get_client_ip():
    """Возвращает реальный IP клиента с учётом прокси/Heroku."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or '0.0.0.0'


# ══════════════════════════════════════════════════════════════════════════════
# AES-256-GCM MESSAGE ENCRYPTION
# ══════════════════════════════════════════════════════════════════════════════
import base64 as _b64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
import os as _os

def _get_msg_key():
    """Return 32-byte AES key from env or generate a stable fallback."""
    raw = _os.environ.get('MESSAGE_ENCRYPTION_KEY', '')
    if raw:
        # Accept hex (64 chars) or base64 (44 chars)
        try:
            key = bytes.fromhex(raw)
            if len(key) == 32:
                return key
        except ValueError:
            pass
        try:
            key = _b64.b64decode(raw)
            if len(key) == 32:
                return key
        except Exception:
            pass
    # Fallback: derive from SECRET_KEY (not ideal but keeps app running)
    import hashlib
    return hashlib.sha256(app.config['SECRET_KEY'].encode()).digest()

def encrypt_msg(plaintext: str) -> str:
    """Encrypt message content. Returns base64-encoded nonce+ciphertext."""
    if not plaintext:
        return plaintext
    key = _get_msg_key()
    aesgcm = _AESGCM(key)
    nonce = _os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    return _b64.b64encode(nonce + ct).decode('ascii')

def decrypt_msg(token: str) -> str:
    """Decrypt message content. Returns original plaintext."""
    if not token:
        return token
    try:
        key = _get_msg_key()
        raw = _b64.b64decode(token)
        nonce, ct = raw[:12], raw[12:]
        aesgcm = _AESGCM(key)
        return aesgcm.decrypt(nonce, ct, None).decode('utf-8')
    except Exception:
        # If decryption fails, return as-is (unencrypted legacy message)
        return token

# Словарь для отслеживания онлайн пользователей
online_users = {}

# Список жалоб (in-memory)
reports = []

# Режим обслуживания (True = мессенджер отключён для обычных пользователей)
MAINTENANCE_MODE = False

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

@app.teardown_appcontext
def shutdown_session(exception=None):
    if exception:
        db.session.rollback()
    db.session.remove()

# Middleware для обновления last_seen при каждом запросе
@app.before_request
def update_last_seen():
    if request.path.startswith('/socket.io') or request.path.startswith('/static'):
        return
    if 'user_id' in session and request.endpoint not in ['static', None]:
        try:
            user_id = session['user_id']
            if not hasattr(app, 'last_seen_cache'):
                app.last_seen_cache = {}
            current_time = time.time()
            last_update = app.last_seen_cache.get(user_id, 0)
            # Обновляем раз в 5 минут — меньше запросов к БД
            if current_time - last_update > 300:
                user = User.query.get(user_id)
                if user:
                    user.last_seen = datetime.utcnow()
                    db.session.commit()
                    app.last_seen_cache[user_id] = current_time
        except Exception as e:
            db.session.rollback()
            app.logger.debug(f"Error updating last_seen: {e}")

@app.teardown_appcontext
def shutdown_session(exception=None):
    """Возвращаем соединение в пул после каждого запроса."""
    if exception:
        db.session.rollback()
    db.session.remove()

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
    premium_until = db.Column(db.DateTime, nullable=True)  # Дата окончания Premium
    is_spam_blocked = db.Column(db.Boolean, default=False)
    spam_block_until = db.Column(db.DateTime, nullable=True)
    premium_emoji = db.Column(db.String(10))  # Эмодзи для премиум пользователей
    timezone = db.Column(db.String(50), default='Europe/Moscow')
    is_bot = db.Column(db.Boolean, default=False)  # Является ли аккаунт ботом
    two_fa_enabled = db.Column(db.Boolean, default=False)  # Двухэтапная аутентификация
    two_fa_code = db.Column(db.String(8))           # Текущий код 2FA
    two_fa_code_expires = db.Column(db.DateTime)    # Срок действия кода
    email = db.Column(db.String(200), nullable=True)  # Email для 2FA
    email_verified = db.Column(db.Boolean, default=False)  # Email подтверждён
    telegram_chat_id = db.Column(db.String(50), nullable=True)  # Telegram chat_id для 2FA
    telegram_link_code = db.Column(db.String(20), nullable=True)  # Код привязки Telegram
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    # Новые поля
    status_text = db.Column(db.String(100))          # Текстовый статус
    theme_schedule = db.Column(db.String(50))        # "08:00-22:00:light,22:00-08:00:dark"
    hidden_chat_pin = db.Column(db.String(6))        # PIN для скрытого чата
    chat_folders = db.Column(db.Text, default='[]')  # JSON папки чатов
    admin_apply_blocked_until = db.Column(db.DateTime, nullable=True)  # Блок подачи заявки в адм.
    reputation = db.Column(db.Integer, default=100)  # Репутация 0-100
    auto_reply_text = db.Column(db.String(200), nullable=True)  # Автоответ
    msg_price = db.Column(db.Integer, nullable=True, default=None)  # Цена сообщения в искрах (None = бесплатно)

    # ── Настройки приватности ─────────────────────────────────────────────────
    # Значения: 'everyone' | 'contacts' | 'nobody' | 'premium' (только для who_can_message)
    privacy_who_can_message = db.Column(db.String(20), default='everyone')
    privacy_who_can_call    = db.Column(db.String(20), default='everyone')
    privacy_who_can_add_to_groups = db.Column(db.String(20), default='everyone')
    privacy_show_last_seen  = db.Column(db.String(20), default='everyone')
    privacy_show_phone      = db.Column(db.String(20), default='nobody')
    privacy_show_profile    = db.Column(db.String(20), default='everyone')

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
    media_url = db.Column(db.Text)  # URL для медиа файлов (Text для data URLs)
    duration = db.Column(db.Integer)  # Длительность для голосовых и видео
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    edited_at = db.Column(db.DateTime)
    is_deleted = db.Column(db.Boolean, default=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    bot_buttons = db.Column(db.Text, default='[]')  # JSON кнопки бота
    expires_at = db.Column(db.DateTime, nullable=True)  # Самоуничтожение
    is_hidden_chat = db.Column(db.Boolean, default=False)  # Скрытый чат
    hidden_for_sender = db.Column(db.Boolean, default=False)   # Скрыто отправителем (очистка истории)
    hidden_for_receiver = db.Column(db.Boolean, default=False) # Скрыто получателем (очистка истории)
    
    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    reply_to = db.relationship('Message', foreign_keys=[reply_to_id], remote_side='Message.id')

    media_files = db.relationship('MessageMedia', backref='message', lazy=True, cascade='all, delete-orphan')

    @property
    def decrypted_content(self):
        return decrypt_msg(self.content) if self.content else self.content

# Модель для множественных медиа файлов в одном сообщении
class MessageMedia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)  # image, file, video
    media_url = db.Column(db.Text, nullable=False)
    file_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    order_index = db.Column(db.Integer, default=0)

class GroupMessageMedia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)  # image, file, video
    media_url = db.Column(db.Text, nullable=False)
    file_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    order_index = db.Column(db.Integer, default=0)


# ── Расписание сообщений ──────────────────────────────────────────────────────
class ScheduledMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)   # личный чат
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=True)     # группа
    content = db.Column(db.Text, nullable=False)
    send_at = db.Column(db.DateTime, nullable=False)
    sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship('User', foreign_keys=[sender_id])

# ── Прочитанность сообщений в группе (кто видел) ─────────────────────────────
class GroupMessageRead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    read_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('message_id', 'user_id', name='_gmr_uc'),)
    user = db.relationship('User', foreign_keys=[user_id])

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
    # Новые поля
    slow_mode_seconds = db.Column(db.Integer, default=0)       # 0 = выключен
    welcome_message = db.Column(db.String(500))                # Приветствие новых участников
    spam_keywords = db.Column(db.Text, default='[]')           # JSON список стоп-слов
    is_verified = db.Column(db.Boolean, default=False)         # Верифицированный канал/группа
    
    creator = db.relationship('User', foreign_keys=[creator_id])

class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_muted = db.Column(db.Boolean, default=False)  # Уведомления отключены
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Права администратора (JSON: {"delete_messages":true,"ban_members":true,"pin_messages":true,"invite_users":true,"edit_group":true})
    admin_permissions = db.Column(db.Text, default='{}')
    # Ограничения участника (JSON: {"can_send_messages":true,"can_send_media":true,"can_react":true,"allowed_reactions":[]})
    member_restrictions = db.Column(db.Text, default='{}')

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
    is_paid = db.Column(db.Boolean, default=False)    # Платный пост
    paid_price = db.Column(db.Integer, default=0)     # Цена в искрах
    message_type = db.Column(db.String(50), default='text')  # text, sticker, poll, etc.
    
    group = db.relationship('Group', foreign_keys=[group_id])
    sender = db.relationship('User', foreign_keys=[sender_id])
    reply_to = db.relationship('GroupMessage', foreign_keys=[reply_to_id], remote_side='GroupMessage.id')

    @property
    def decrypted_content(self):
        return decrypt_msg(self.content) if self.content else self.content

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

class FCMToken(db.Model):
    """FCM токен устройства для push-уведомлений."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.String(500), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id])

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    media_type = db.Column(db.String(20), default='text')  # text, image, video
    media_url = db.Column(db.String(500))  # URL медиа файла
    views_count = db.Column(db.Integer, default=0)  # Счётчик просмотров
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
    ip_address = db.Column(db.String(45))  # IPv4/IPv6
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

# ── Реакции на сообщения ─────────────────────────────────────────────────────
class MessageReaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=True)
    emoji = db.Column(db.String(500), nullable=False)  # может быть URL кастомной реакции
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint('user_id', 'message_id', 'emoji', name='_user_msg_emoji_uc'),
        db.UniqueConstraint('user_id', 'group_message_id', 'emoji', name='_user_gmsg_emoji_uc'),
    )
    user = db.relationship('User', foreign_keys=[user_id])

# ── Закреплённые сообщения ───────────────────────────────────────────────────
class PinnedMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=True)
    # Для личных чатов: user1_id < user2_id
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=True)
    pinned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    pinned_at = db.Column(db.DateTime, default=datetime.utcnow)
    content_preview = db.Column(db.String(200))

# ── Опросы ───────────────────────────────────────────────────────────────────
class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question = db.Column(db.String(500), nullable=False)
    is_anonymous = db.Column(db.Boolean, default=True)
    is_multiple = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Привязка к сообщению
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=True)
    creator = db.relationship('User', foreign_keys=[creator_id])

class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    text = db.Column(db.String(200), nullable=False)
    order_index = db.Column(db.Integer, default=0)
    poll = db.relationship('Poll', foreign_keys=[poll_id])

class PollVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_option.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    voted_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('poll_id', 'option_id', 'user_id', name='_poll_vote_uc'),)

# ── Искры (Sparks) ───────────────────────────────────────────────────────────
class SparkBalance(db.Model):
    """Баланс искр пользователя."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    balance = db.Column(db.Integer, default=0)
    user = db.relationship('User', foreign_keys=[user_id])

class SparkTransaction(db.Model):
    """История транзакций искр."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)  # + пополнение, - списание
    reason = db.Column(db.String(100), nullable=False)  # spark_reaction, gift_buy, gift_sell, post_pay, withdraw
    ref_id = db.Column(db.Integer)  # id связанного объекта
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id])

class SparkReaction(db.Model):
    """Искорная реакция на пост канала."""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False)
    amount = db.Column(db.Integer, default=1)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('sender_id', 'group_message_id', name='_spark_reaction_uc'),)
    sender = db.relationship('User', foreign_keys=[sender_id])

class ChannelSparkWithdraw(db.Model):
    """Запрос на вывод искр владельцем канала."""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, done
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ── Подарки ───────────────────────────────────────────────────────────────────
class GiftType(db.Model):
    """Тип подарка (каталог)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    emoji = db.Column(db.String(10), nullable=False)
    description = db.Column(db.String(300))
    price_sparks = db.Column(db.Integer, nullable=False)  # Цена в искрах
    rarity = db.Column(db.String(20), default='common')  # common, rare, epic, legendary
    is_active = db.Column(db.Boolean, default=True)

class UserGift(db.Model):
    """Подарок, полученный пользователем."""
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    gift_type_id = db.Column(db.Integer, db.ForeignKey('gift_type.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # кто подарил
    is_displayed = db.Column(db.Boolean, default=False)  # показывать в профиле
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner = db.relationship('User', foreign_keys=[owner_id])
    sender = db.relationship('User', foreign_keys=[sender_id])
    gift_type = db.relationship('GiftType', foreign_keys=[gift_type_id])

# ── Платные посты ─────────────────────────────────────────────────────────────
class PaidPost(db.Model):
    """Платный пост в канале."""
    id = db.Column(db.Integer, primary_key=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False, unique=True)
    price_sparks = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group_message = db.relationship('GroupMessage', foreign_keys=[group_message_id])

class PaidPostPurchase(db.Model):
    """Факт покупки платного поста пользователем."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    paid_post_id = db.Column(db.Integer, db.ForeignKey('paid_post.id'), nullable=False)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'paid_post_id', name='_post_purchase_uc'),)
    user = db.relationship('User', foreign_keys=[user_id])
    paid_post = db.relationship('PaidPost', foreign_keys=[paid_post_id])

# ── NFT Коллекционные предметы ────────────────────────────────────────────────
class NFTCollection(db.Model):
    """Коллекция NFT-предметов."""
    __tablename__ = 'nft_collection'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)          # "Cosmic Cats"
    description = db.Column(db.String(500))
    total_supply = db.Column(db.Integer, nullable=False)      # сколько всего выпущено
    price_sparks = db.Column(db.Integer, default=0)           # цена покупки в искрах
    image_url = db.Column(db.String(500))                     # базовое изображение
    bg_color = db.Column(db.String(20), default='#2d3748')    # цвет фона карточки
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('NFTItem', backref='collection', lazy='dynamic')

class NFTItem(db.Model):
    """Конкретный экземпляр NFT с атрибутами."""
    __tablename__ = 'nft_item'
    id = db.Column(db.Integer, primary_key=True)
    collection_id = db.Column(db.Integer, db.ForeignKey('nft_collection.id'), nullable=False)
    serial_number = db.Column(db.Integer, nullable=False)     # порядковый номер (1, 2, 3...)
    attributes = db.Column(db.Text, default='{}')             # JSON: {model, background, pattern, ...}
    value_sparks = db.Column(db.Integer, default=0)           # оценочная стоимость
    is_minted = db.Column(db.Boolean, default=False)          # выдан ли кому-то
    minted_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (db.UniqueConstraint('collection_id', 'serial_number', name='_nft_serial_uc'),)

class UserNFT(db.Model):
    """NFT в коллекции пользователя."""
    __tablename__ = 'user_nft'
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nft_item_id = db.Column(db.Integer, db.ForeignKey('nft_item.id'), nullable=False, unique=True)
    is_displayed = db.Column(db.Boolean, default=True)        # показывать в профиле
    acquired_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner = db.relationship('User', foreign_keys=[owner_id])
    nft_item = db.relationship('NFTItem', foreign_keys=[nft_item_id])


# ── Скрытые чаты ─────────────────────────────────────────────────────────────
class HiddenChat(db.Model):
    """Чат скрыт за PIN-кодом."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    other_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'other_user_id', name='_hidden_chat_uc'),)

# ── Медленный режим (last_message_time per user per group) ────────────────────
class SlowModeTracker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='_slow_mode_uc'),)

# ── Контакты ─────────────────────────────────────────────────────────────────
class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'contact_id', name='_contact_uc'),)
    user = db.relationship('User', foreign_keys=[user_id])
    contact = db.relationship('User', foreign_keys=[contact_id])

# ── Стикеры ───────────────────────────────────────────────────────────────────
class StickerPack(db.Model):
    """Пак стикеров."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    cover_url = db.Column(db.Text)   # первый стикер как обложка
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    creator = db.relationship('User', foreign_keys=[creator_id])
    stickers = db.relationship('Sticker', backref='pack', lazy=True, cascade='all, delete-orphan')

class Sticker(db.Model):
    """Один стикер в паке."""
    id = db.Column(db.Integer, primary_key=True)
    pack_id = db.Column(db.Integer, db.ForeignKey('sticker_pack.id'), nullable=False)
    image_url = db.Column(db.Text, nullable=False)
    emoji_hint = db.Column(db.String(10), default='😊')  # связанный эмодзи
    order_index = db.Column(db.Integer, default=0)

class UserStickerPack(db.Model):
    """Пак стикеров, добавленный пользователем в коллекцию."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    pack_id = db.Column(db.Integer, db.ForeignKey('sticker_pack.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'pack_id', name='_user_stickerpack_uc'),)

class StickerBotState(db.Model):
    """Состояние диалога пользователя с ботом @stickers (персистентное)."""
    __tablename__ = 'sticker_bot_state'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    state = db.Column(db.String(50), nullable=False)
    pack_id = db.Column(db.Integer, nullable=True)
    pack_name = db.Column(db.String(100), nullable=True)
    count = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── Кастомные реакции (Premium) ───────────────────────────────────────────────
class CustomReactionPack(db.Model):
    """Пак кастомных реакций."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    creator = db.relationship('User', foreign_keys=[creator_id])
    reactions = db.relationship('CustomReaction', backref='pack', lazy=True, cascade='all, delete-orphan')

class CustomReaction(db.Model):
    """Одна кастомная реакция."""
    id = db.Column(db.Integer, primary_key=True)
    pack_id = db.Column(db.Integer, db.ForeignKey('custom_reaction_pack.id'), nullable=False)
    image_url = db.Column(db.String(500), nullable=False)
    name = db.Column(db.String(50))  # название реакции
    order_index = db.Column(db.Integer, default=0)

class UserCustomReactionPack(db.Model):
    """Пак кастомных реакций, добавленный пользователем."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    pack_id = db.Column(db.Integer, db.ForeignKey('custom_reaction_pack.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'pack_id', name='_user_reactpack_uc'),)
    user = db.relationship('User', foreign_keys=[user_id])
    pack = db.relationship('CustomReactionPack', foreign_keys=[pack_id])

class AdminWarning(db.Model):
    """Предупреждение администратору от owner."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    issued_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    user = db.relationship('User', foreign_keys=[user_id])
    issuer = db.relationship('User', foreign_keys=[issued_by])

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

def _init_db():
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
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS telegram_chat_id VARCHAR(50)",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS telegram_link_code VARCHAR(20)",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS status_text VARCHAR(100)",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS theme_schedule TEXT",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS hidden_chat_pin VARCHAR(6)",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS chat_folders TEXT DEFAULT '[]'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS premium_until TIMESTAMP",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS auto_reply_text VARCHAR(500)",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS premium_emoji VARCHAR(10)",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS msg_price INTEGER",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS privacy_who_can_message VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS privacy_who_can_call VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS privacy_who_can_add_to_groups VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS privacy_show_last_seen VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS privacy_show_phone VARCHAR(20) DEFAULT 'nobody'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS privacy_show_profile VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE",
                "ALTER TABLE message ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES message(id)",
                "ALTER TABLE message ADD COLUMN IF NOT EXISTS bot_buttons TEXT DEFAULT '[]'",
                "ALTER TABLE message ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
                "ALTER TABLE message ADD COLUMN IF NOT EXISTS is_hidden_chat BOOLEAN DEFAULT FALSE",
                "ALTER TABLE group_message ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES group_message(id)",
                "ALTER TABLE group_message ADD COLUMN IF NOT EXISTS is_paid BOOLEAN DEFAULT FALSE",
                "ALTER TABLE group_message ADD COLUMN IF NOT EXISTS paid_price INTEGER DEFAULT 0",
                "ALTER TABLE group_message ADD COLUMN IF NOT EXISTS message_type VARCHAR(50) DEFAULT 'text'",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS admin_apply_blocked_until TIMESTAMP",
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS reputation INTEGER DEFAULT 100",
                "ALTER TABLE story ADD COLUMN IF NOT EXISTS media_url VARCHAR(500)",
                "ALTER TABLE story ADD COLUMN IF NOT EXISTS views_count INTEGER DEFAULT 0",
                "ALTER TABLE password_reset_request ADD COLUMN IF NOT EXISTS request_type VARCHAR(30) DEFAULT 'password'",
                "ALTER TABLE password_reset_request ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45)",
                'ALTER TABLE "group" ADD COLUMN IF NOT EXISTS slow_mode_seconds INTEGER DEFAULT 0',
                'ALTER TABLE "group" ADD COLUMN IF NOT EXISTS welcome_message VARCHAR(500)',
                'ALTER TABLE "group" ADD COLUMN IF NOT EXISTS spam_keywords TEXT DEFAULT \'[]\'',
                'ALTER TABLE "group" ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE',
                'ALTER TABLE group_member ADD COLUMN IF NOT EXISTS admin_permissions TEXT DEFAULT \'{}\'',
                'ALTER TABLE group_member ADD COLUMN IF NOT EXISTS member_restrictions TEXT DEFAULT \'{}\'',
                'ALTER TABLE message_reaction ALTER COLUMN emoji TYPE VARCHAR(500)',
                'ALTER TABLE message ADD COLUMN IF NOT EXISTS hidden_for_sender BOOLEAN DEFAULT FALSE',
                'ALTER TABLE message ADD COLUMN IF NOT EXISTS hidden_for_receiver BOOLEAN DEFAULT FALSE',
                'ALTER TABLE message ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE',
                'ALTER TABLE "group" ADD COLUMN IF NOT EXISTS pinned_message_id INTEGER',
                f'ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS last_seen_visible BOOLEAN DEFAULT TRUE',
                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS blocked_users_list TEXT DEFAULT '[]'",
                'ALTER TABLE group_member ADD COLUMN IF NOT EXISTS role_title VARCHAR(50)',
                'ALTER TABLE group_member ADD COLUMN IF NOT EXISTS slow_mode_until TIMESTAMP',
                'ALTER TABLE message ADD COLUMN IF NOT EXISTS is_secret BOOLEAN DEFAULT FALSE',
                'ALTER TABLE message ADD COLUMN IF NOT EXISTS secret_chat_id INTEGER',
                'ALTER TABLE message ALTER COLUMN media_url TYPE TEXT',
                'ALTER TABLE message_media ALTER COLUMN media_url TYPE TEXT',
            ]
        else:
            migrations = [
                f"ALTER TABLE {user_table} ADD COLUMN two_fa_enabled BOOLEAN DEFAULT 0",
                f"ALTER TABLE {user_table} ADD COLUMN two_fa_code VARCHAR(8)",
                f"ALTER TABLE {user_table} ADD COLUMN two_fa_code_expires DATETIME",
                f"ALTER TABLE {user_table} ADD COLUMN admin_role VARCHAR(20)",
                f"ALTER TABLE {user_table} ADD COLUMN email VARCHAR(200)",
                f"ALTER TABLE {user_table} ADD COLUMN telegram_chat_id VARCHAR(50)",
                f"ALTER TABLE {user_table} ADD COLUMN telegram_link_code VARCHAR(20)",
                f"ALTER TABLE {user_table} ADD COLUMN status_text VARCHAR(100)",
                f"ALTER TABLE {user_table} ADD COLUMN theme_schedule TEXT",
                f"ALTER TABLE {user_table} ADD COLUMN hidden_chat_pin VARCHAR(6)",
                f"ALTER TABLE {user_table} ADD COLUMN chat_folders TEXT DEFAULT '[]'",
                f"ALTER TABLE {user_table} ADD COLUMN auto_reply_text VARCHAR(500)",
                f"ALTER TABLE {user_table} ADD COLUMN premium_emoji VARCHAR(10)",
                f"ALTER TABLE {user_table} ADD COLUMN msg_price INTEGER",
                f"ALTER TABLE {user_table} ADD COLUMN privacy_who_can_message VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN privacy_who_can_call VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN privacy_who_can_add_to_groups VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN privacy_show_last_seen VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN privacy_show_phone VARCHAR(20) DEFAULT 'nobody'",
                f"ALTER TABLE {user_table} ADD COLUMN privacy_show_profile VARCHAR(20) DEFAULT 'everyone'",
                f"ALTER TABLE {user_table} ADD COLUMN email_verified BOOLEAN DEFAULT 0",
                "ALTER TABLE message ADD COLUMN reply_to_id INTEGER REFERENCES message(id)",
                "ALTER TABLE message ADD COLUMN bot_buttons TEXT DEFAULT '[]'",
                "ALTER TABLE message ADD COLUMN expires_at DATETIME",
                "ALTER TABLE message ADD COLUMN is_hidden_chat BOOLEAN DEFAULT 0",
                "ALTER TABLE group_message ADD COLUMN reply_to_id INTEGER REFERENCES group_message(id)",
                "ALTER TABLE group_message ADD COLUMN is_paid BOOLEAN DEFAULT 0",
                "ALTER TABLE group_message ADD COLUMN paid_price INTEGER DEFAULT 0",
                "ALTER TABLE group_message ADD COLUMN message_type VARCHAR(50) DEFAULT 'text'",
                f"ALTER TABLE {user_table} ADD COLUMN admin_apply_blocked_until DATETIME",
                f"ALTER TABLE {user_table} ADD COLUMN reputation INTEGER DEFAULT 100",
                "ALTER TABLE story ADD COLUMN media_url VARCHAR(500)",
                "ALTER TABLE story ADD COLUMN views_count INTEGER DEFAULT 0",
                "ALTER TABLE password_reset_request ADD COLUMN request_type VARCHAR(30) DEFAULT 'password'",
                "ALTER TABLE password_reset_request ADD COLUMN ip_address VARCHAR(45)",
                "ALTER TABLE 'group' ADD COLUMN slow_mode_seconds INTEGER DEFAULT 0",
                "ALTER TABLE 'group' ADD COLUMN welcome_message VARCHAR(500)",
                "ALTER TABLE 'group' ADD COLUMN spam_keywords TEXT DEFAULT '[]'",
                "ALTER TABLE 'group' ADD COLUMN is_verified BOOLEAN DEFAULT 0",
                "ALTER TABLE group_member ADD COLUMN admin_permissions TEXT DEFAULT '{}'",
                "ALTER TABLE group_member ADD COLUMN member_restrictions TEXT DEFAULT '{}'",
                "ALTER TABLE message ADD COLUMN hidden_for_sender BOOLEAN DEFAULT 0",
                "ALTER TABLE message ADD COLUMN hidden_for_receiver BOOLEAN DEFAULT 0",
                "ALTER TABLE message ADD COLUMN is_read BOOLEAN DEFAULT 0",
                "ALTER TABLE 'group' ADD COLUMN pinned_message_id INTEGER",
                f"ALTER TABLE {user_table} ADD COLUMN last_seen_visible BOOLEAN DEFAULT 1",
                f"ALTER TABLE {user_table} ADD COLUMN blocked_users_list TEXT DEFAULT '[]'",
                "ALTER TABLE group_member ADD COLUMN role_title VARCHAR(50)",
                "ALTER TABLE group_member ADD COLUMN slow_mode_until DATETIME",
                "ALTER TABLE message ADD COLUMN is_secret BOOLEAN DEFAULT 0",
                "ALTER TABLE message ADD COLUMN secret_chat_id INTEGER",
            ]

        with db.engine.connect() as conn:
            for sql in migrations:
                try:
                    conn.execute(text(sql))
                    conn.commit()
                except Exception:
                    pass

        # ── Сид: NFT коллекции ────────────────────────────────────────────────────
        import json as _json
        if NFTCollection.query.count() == 0:
            _nft_collections = [
                {
                    "name": "Cosmic Cats",
                    "description": "Коллекция космических котов",
                    "total_supply": 10000,
                    "price_sparks": 500,
                    "image_url": "",
                    "bg_color": "#1a202c",
                    "attrs": [
                        {"trait": "Модель", "values": [("Astro", 5), ("Nebula", 10), ("Void", 20), ("Solar", 65)]},
                        {"trait": "Фон", "values": [("Deep Space", 3), ("Aurora", 8), ("Starfield", 25), ("Cosmos", 64)]},
                        {"trait": "Узор", "values": [("Supernova", 1), ("Galaxy", 5), ("Meteor", 15), ("Stars", 79)]},
                    ]
                },
                {
                    "name": "Neon Punks",
                    "description": "Неоновые панки будущего",
                    "total_supply": 5000,
                    "price_sparks": 1000,
                    "image_url": "",
                    "bg_color": "#0d1117",
                    "attrs": [
                        {"trait": "Модель", "values": [("Cyber", 4), ("Glitch", 9), ("Pixel", 22), ("Neon", 65)]},
                        {"trait": "Фон", "values": [("Matrix", 2), ("Hologram", 7), ("Grid", 20), ("Dark", 71)]},
                        {"trait": "Узор", "values": [("Circuit", 1), ("Laser", 4), ("Wave", 18), ("Static", 77)]},
                    ]
                },
                {
                    "name": "Forest Spirits",
                    "description": "Духи древнего леса",
                    "total_supply": 7500,
                    "price_sparks": 750,
                    "image_url": "",
                    "bg_color": "#1a3a2a",
                    "attrs": [
                        {"trait": "Модель", "values": [("Ancient", 3), ("Elder", 8), ("Wisp", 18), ("Sprite", 71)]},
                        {"trait": "Фон", "values": [("Enchanted", 2), ("Misty", 6), ("Dusk", 22), ("Forest", 70)]},
                        {"trait": "Узор", "values": [("Rune", 1), ("Leaf", 5), ("Moss", 16), ("Bark", 78)]},
                    ]
                },
            ]
            for _nc in _nft_collections:
                _col = NFTCollection(
                    name=_nc["name"],
                    description=_nc["description"],
                    total_supply=_nc["total_supply"],
                    price_sparks=_nc["price_sparks"],
                    image_url=_nc.get("image_url", ""),
                    bg_color=_nc.get("bg_color", "#2d3748"),
                )
                db.session.add(_col)
                db.session.flush()
                # Генерируем первые 20 экземпляров для каждой коллекции
                import random as _rnd
                for _sn in range(1, 21):
                    _attrs = {}
                    for _at in _nc["attrs"]:
                        _vals = [v for v, _ in _at["values"]]
                        _weights = [w for _, w in _at["values"]]
                        _attrs[_at["trait"]] = _rnd.choices(_vals, weights=_weights, k=1)[0]
                    _item = NFTItem(
                        collection_id=_col.id,
                        serial_number=_sn,
                        attributes=_json.dumps(_attrs, ensure_ascii=False),
                        value_sparks=_nc["price_sparks"],
                    )
                    db.session.add(_item)
            db.session.commit()
            print("✓ NFT коллекции созданы")

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
                "👑 Привет! Я помогу оформить Premium подписку или купить Искры.\n\n"
                "Что вас интересует?\n\n"
                "Выберите вариант ниже 👇"
            )
            _buttons = json.dumps([
                {"label": "👑 Premium подписка", "reply": "/premium"},
                {"label": "✨ Купить Искры", "reply": "/sparks"},
            ])
            _premium_buttons = json.dumps([
                {"label": "7 дней — 59 ₽", "reply": "/buy_7"},
                {"label": "14 дней — 99 ₽ (выгоднее!)", "reply": "/buy_14"},
                {"label": "30 дней — 149 ₽ (скидка 10%!)", "reply": "/buy_30"},
                {"label": "6 месяцев — 499 ₽ (скидка 30,7%!)", "reply": "/buy_180"},
                {"label": "Год — 799 ₽ (скидка 20%)", "reply": "/buy_365"},
            ])
            _sparks_buttons = json.dumps([
                {"label": "✨ 100 искр — 29 ₽", "reply": "/buy_sparks_100"},
                {"label": "✨ 300 искр — 79 ₽ (выгоднее!)", "reply": "/buy_sparks_300"},
                {"label": "✨ 700 искр — 149 ₽ (скидка 15%!)", "reply": "/buy_sparks_700"},
                {"label": "✨ 1500 искр — 299 ₽ (скидка 20%!)", "reply": "/buy_sparks_1500"},
                {"label": "✨ 5000 искр — 799 ₽ (скидка 36%!)", "reply": "/buy_sparks_5000"},
            ])
            _buy_url = "https://t.me/kotakbaslife"
            _buy_text = (
                "💳 Для завершения покупки нажмите кнопку *Оплатить* ниже.\n"
                "Вы попадёте в Telegram-бот, который покажет реквизиты и примет скриншот оплаты."
            )
            _pay_buttons_7   = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_7"},   {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_14  = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_14"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_30  = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_30"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_180 = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_180"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_365 = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_365"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_s100  = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_100"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_s300  = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_300"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_s700  = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_700"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_s1500 = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_1500"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}])
            _pay_buttons_s5000 = json.dumps([{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_5000"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}])

            _cmds = [
                BotCommand(bot_id=_pbot.id, trigger='/start', response_text=_start_text, buttons=_buttons, order_index=1),
                BotCommand(bot_id=_pbot.id, trigger='/premium', response_text="👑 Выберите срок Premium подписки:", buttons=_premium_buttons, order_index=2),
                BotCommand(bot_id=_pbot.id, trigger='/sparks', response_text="✨ Выберите количество Искр:", buttons=_sparks_buttons, order_index=3),
                BotCommand(bot_id=_pbot.id, trigger='/buy_7',   response_text=f"✅ Вы выбрали: 7 дней — 59 ₽\n\n{_buy_text}",   buttons=_pay_buttons_7,   order_index=4),
                BotCommand(bot_id=_pbot.id, trigger='/buy_14',  response_text=f"✅ Вы выбрали: 14 дней — 99 ₽\n\n{_buy_text}",  buttons=_pay_buttons_14,  order_index=5),
                BotCommand(bot_id=_pbot.id, trigger='/buy_30',  response_text=f"✅ Вы выбрали: 30 дней — 149 ₽\n\n{_buy_text}", buttons=_pay_buttons_30,  order_index=6),
                BotCommand(bot_id=_pbot.id, trigger='/buy_180', response_text=f"✅ Вы выбрали: 6 месяцев — 499 ₽\n\n{_buy_text}", buttons=_pay_buttons_180, order_index=7),
                BotCommand(bot_id=_pbot.id, trigger='/buy_365', response_text=f"✅ Вы выбрали: Год — 799 ₽\n\n{_buy_text}", buttons=_pay_buttons_365, order_index=8),
                BotCommand(bot_id=_pbot.id, trigger='/buy_sparks_100',  response_text=f"✅ Вы выбрали: 100 искр — 29 ₽\n\n{_buy_text}",  buttons=_pay_buttons_s100,  order_index=9),
                BotCommand(bot_id=_pbot.id, trigger='/buy_sparks_300',  response_text=f"✅ Вы выбрали: 300 искр — 79 ₽\n\n{_buy_text}",  buttons=_pay_buttons_s300,  order_index=10),
                BotCommand(bot_id=_pbot.id, trigger='/buy_sparks_700',  response_text=f"✅ Вы выбрали: 700 искр — 149 ₽\n\n{_buy_text}", buttons=_pay_buttons_s700,  order_index=11),
                BotCommand(bot_id=_pbot.id, trigger='/buy_sparks_1500', response_text=f"✅ Вы выбрали: 1500 искр — 299 ₽\n\n{_buy_text}", buttons=_pay_buttons_s1500, order_index=12),
                BotCommand(bot_id=_pbot.id, trigger='/buy_sparks_5000', response_text=f"✅ Вы выбрали: 5000 искр — 799 ₽\n\n{_buy_text}", buttons=_pay_buttons_s5000, order_index=13),
                BotCommand(bot_id=_pbot.id, trigger='/pay_cancel', response_text="❌ Оплата отменена. Возвращайтесь когда будете готовы!", buttons='[]', order_index=14),
                BotCommand(bot_id=_pbot.id, trigger='*', response_text="Напишите /start чтобы увидеть меню 👑", buttons='[]', order_index=99),
            ]
            for _c in _cmds:
                db.session.add(_c)
            db.session.commit()
            print("✓ Бот Tabletone Premium создан")
        else:
            # Обновляем кнопки существующих команд /buy_* и добавляем /pay_cancel если нет
            _pbot_obj = Bot.query.filter_by(user_id=_pbot_user.id).first()
            if _pbot_obj:
                _buy_text_upd = (
                    "💳 Для завершения покупки нажмите кнопку *Оплатить* ниже.\n"
                    "Вы попадёте в Telegram-бот, который покажет реквизиты и примет скриншот оплаты."
                )
                _upd_map = {
                    '/buy_7':   (f"✅ Вы выбрали: 7 дней — 59 ₽\n\n{_buy_text_upd}",   [{"label": "✅ Оплатить", "reply": "/pay_confirm_7"},   {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_14':  (f"✅ Вы выбрали: 14 дней — 99 ₽\n\n{_buy_text_upd}",  [{"label": "✅ Оплатить", "reply": "/pay_confirm_14"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_30':  (f"✅ Вы выбрали: 30 дней — 149 ₽\n\n{_buy_text_upd}", [{"label": "✅ Оплатить", "reply": "/pay_confirm_30"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_180': (f"✅ Вы выбрали: 6 месяцев — 499 ₽\n\n{_buy_text_upd}",[{"label": "✅ Оплатить", "reply": "/pay_confirm_180"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_365': (f"✅ Вы выбрали: Год — 799 ₽\n\n{_buy_text_upd}",      [{"label": "✅ Оплатить", "reply": "/pay_confirm_365"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_sparks_100':  (f"✅ Вы выбрали: 100 искр — 29 ₽\n\n{_buy_text_upd}",  [{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_100"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_sparks_300':  (f"✅ Вы выбрали: 300 искр — 79 ₽\n\n{_buy_text_upd}",  [{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_300"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_sparks_700':  (f"✅ Вы выбрали: 700 искр — 149 ₽\n\n{_buy_text_upd}", [{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_700"},  {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_sparks_1500': (f"✅ Вы выбрали: 1500 искр — 299 ₽\n\n{_buy_text_upd}",[{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_1500"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                    '/buy_sparks_5000': (f"✅ Вы выбрали: 5000 искр — 799 ₽\n\n{_buy_text_upd}",[{"label": "✅ Оплатить", "reply": "/pay_confirm_sparks_5000"}, {"label": "❌ Отклонить", "reply": "/pay_cancel"}]),
                }
                for trigger, (resp, btns) in _upd_map.items():
                    cmd = BotCommand.query.filter_by(bot_id=_pbot_obj.id, trigger=trigger).first()
                    if cmd:
                        cmd.response_text = resp
                        cmd.buttons = json.dumps(btns)
                # Добавить /pay_cancel если нет
                if not BotCommand.query.filter_by(bot_id=_pbot_obj.id, trigger='/pay_cancel').first():
                    db.session.add(BotCommand(bot_id=_pbot_obj.id, trigger='/pay_cancel',
                        response_text="❌ Оплата отменена. Возвращайтесь когда будете готовы!", buttons='[]', order_index=14))
                db.session.commit()
                print("✓ Команды бота Tabletone Premium обновлены")

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

        # ── Сид: бот Stickers ───────────────────────────────────────────────────
        _STK_USERNAME = 'stickers'
        if not User.query.filter_by(username=_STK_USERNAME).first():
            _stk_user = User(
                username=_STK_USERNAME,
                display_name='Stickers',
                bio='Создавай и управляй паками стикеров',
                avatar_color='#f6ad55',
                is_bot=True, is_verified=True,
                password_hash=generate_password_hash(secrets.token_hex(32))
            )
            db.session.add(_stk_user)
            db.session.flush()
            _stk_bot = Bot(
                user_id=_stk_user.id, owner_id=_PREMIUM_OWNER_ID,
                token=f"{_stk_user.id}:{secrets.token_urlsafe(32)}",
                description='Создание паков стикеров', is_active=True, review_status='approved'
            )
            db.session.add(_stk_bot)
            db.session.commit()
            print("✓ Бот Stickers создан")

        # ── Сид: бот Premium Support ─────────────────────────────────────────────
        _PRM_USERNAME = 'premium_support'
        if not User.query.filter_by(username=_PRM_USERNAME).first():
            _prm_user = User(
                username=_PRM_USERNAME,
                display_name='⭐ Premium Support',
                bio='Премиальная поддержка от разработчика — только для Premium пользователей',
                avatar_color='#f6ad55',
                is_bot=True, is_verified=True, is_premium=True,
                password_hash=generate_password_hash(secrets.token_hex(32))
            )
            db.session.add(_prm_user)
            db.session.flush()
            _prm_bot = Bot(
                user_id=_prm_user.id, owner_id=_PREMIUM_OWNER_ID,
                token=f"{_prm_user.id}:{secrets.token_urlsafe(32)}",
                description='Премиальная поддержка от разработчика', is_active=True, review_status='approved'
            )
            db.session.add(_prm_bot)
            db.session.flush()
            _prm_buttons = json.dumps([
                {"label": "🐛 Сообщить об ошибке", "reply": "/bug"},
                {"label": "💡 Предложить идею", "reply": "/idea"},
                {"label": "❓ Вопрос разработчику", "reply": "/ask"},
            ])
            _prm_cmds = [
                BotCommand(bot_id=_prm_bot.id, trigger='/start', order_index=1,
                    response_text=(
                        "⭐ *Добро пожаловать в Premium Support!*\n\n"
                        "Вы получаете приоритетную поддержку от разработчика Tabletone.\n\n"
                        "Ваши обращения рассматриваются в первую очередь.\n\n"
                        "Выберите тему обращения:"
                    ), buttons=_prm_buttons),
                BotCommand(bot_id=_prm_bot.id, trigger='/bug', order_index=2,
                    response_text=(
                        "🐛 *Сообщение об ошибке*\n\n"
                        "Опишите проблему следующим сообщением:\n"
                        "— Что происходит?\n"
                        "— Как воспроизвести?\n"
                        "— На каком устройстве/браузере?\n\n"
                        "Разработчик получит ваш отчёт и ответит в ближайшее время."
                    ), buttons='[]'),
                BotCommand(bot_id=_prm_bot.id, trigger='/idea', order_index=3,
                    response_text=(
                        "💡 *Предложение по улучшению*\n\n"
                        "Опишите вашу идею следующим сообщением.\n"
                        "Лучшие идеи от Premium пользователей реализуются в первую очередь! 🚀"
                    ), buttons='[]'),
                BotCommand(bot_id=_prm_bot.id, trigger='/ask', order_index=4,
                    response_text=(
                        "❓ *Вопрос разработчику*\n\n"
                        "Напишите ваш вопрос следующим сообщением.\n"
                        "Отвечаем в течение нескольких часов."
                    ), buttons='[]'),
                BotCommand(bot_id=_prm_bot.id, trigger='*', order_index=99,
                    response_text="⭐ Ваше обращение принято! Разработчик ответит вам в ближайшее время.\n\nНапишите /start чтобы увидеть меню.",
                    buttons='[]'),
            ]
            for _c in _prm_cmds:
                db.session.add(_c)
            db.session.commit()
            print("✓ Бот Premium Support создан")

        # ── Сид: бот Nexus AI ─────────────────────────────────────────────────────
        _NEXUS_USERNAME = 'nexus'
        if not User.query.filter_by(username=_NEXUS_USERNAME).first():
            _nexus_user = User(
                username=_NEXUS_USERNAME,
                display_name='⚡ Nexus',
                bio='Умный ИИ-ассистент. Задай любой вопрос — отвечу мгновенно.',
                avatar_color='#7c3aed',
                is_bot=True, is_verified=True,
                password_hash=generate_password_hash(secrets.token_hex(32))
            )
            db.session.add(_nexus_user)
            db.session.flush()
            _nexus_bot = Bot(
                user_id=_nexus_user.id, owner_id=_PREMIUM_OWNER_ID,
                token=f"{_nexus_user.id}:{secrets.token_urlsafe(32)}",
                description='Умный ИИ-ассистент', is_active=True, review_status='approved'
            )
            db.session.add(_nexus_bot)
            db.session.flush()
            _nexus_cmds = [
                BotCommand(bot_id=_nexus_bot.id, trigger='/start', order_index=1,
                    response_text=(
                        "⚡ *Привет! Я Nexus — твой ИИ-ассистент.*\n\n"
                        "Я работаю на базе Google Gemini и могу помочь с:\n"
                        "• Ответами на любые вопросы\n"
                        "• Написанием текстов и кода\n"
                        "• Переводом и объяснением\n"
                        "• Идеями и советами\n\n"
                        "Просто напиши мне что-нибудь 💬"
                    ), buttons='[]'),
            ]
            for _c in _nexus_cmds:
                db.session.add(_c)
            db.session.commit()
            print("✓ Бот Nexus AI создан")

        # ── Сид: бот tabletone_publisher ─────────────────────────────────────────
        _PUB_USERNAME = 'tabletone_publisher'
        if not User.query.filter_by(username=_PUB_USERNAME).first():
            _pub_user = User(
                username=_PUB_USERNAME,
                display_name='📢 Tabletone Publisher',
                bio='Официальный бот для публикации новостей в канале',
                avatar_color='#3182ce',
                is_bot=True, is_verified=True,
                password_hash=generate_password_hash(secrets.token_hex(32))
            )
            db.session.add(_pub_user)
            db.session.flush()
            _pub_bot = Bot(
                user_id=_pub_user.id, owner_id=_PREMIUM_OWNER_ID,
                token=f"{_pub_user.id}:{secrets.token_urlsafe(32)}",
                description='Публикация новостей в канале tabletone_official',
                is_active=True, review_status='approved'
            )
            db.session.add(_pub_bot)
            db.session.flush()
            _pub_cmds = [
                BotCommand(bot_id=_pub_bot.id, trigger='/start', order_index=1,
                    response_text=(
                        "📢 *Tabletone Publisher*\n\n"
                        "Напиши мне заметки об обновлении — я оформлю их красиво и опубликую в канале @tabletone_official.\n\n"
                        "Просто напиши текст обновления 👇"
                    ), buttons='[]'),
                BotCommand(bot_id=_pub_bot.id, trigger='*', order_index=99,
                    response_text="Напиши /start для инструкций.", buttons='[]'),
            ]
            for _c in _pub_cmds:
                db.session.add(_c)
            db.session.commit()
            print("✓ Бот tabletone_publisher создан")

        # ── Сид: канал tabletone_official ────────────────────────────────────────
        _CHANNEL_USERNAME = 'tabletone_official'
        _channel = Group.query.filter_by(username=_CHANNEL_USERNAME).first()
        if not _channel and _owner:
            _channel = Group(
                name='Tabletone Official',
                username=_CHANNEL_USERNAME,
                description='Официальный канал мессенджера Tabletone. Новости, обновления, анонсы.',
                creator_id=_owner.id,
                avatar_color='#3182ce',
                is_channel=True,
                is_public=True,
                is_verified=True,
            )
            db.session.add(_channel)
            db.session.flush()
            # Добавляем владельца как администратора
            db.session.add(GroupMember(group_id=_channel.id, user_id=_owner.id, is_admin=True))
            # Добавляем бота как администратора
            _pub_user_obj = User.query.filter_by(username=_PUB_USERNAME).first()
            if _pub_user_obj:
                db.session.add(GroupMember(group_id=_channel.id, user_id=_pub_user_obj.id, is_admin=True))
            db.session.commit()
            print("✓ Канал tabletone_official создан")
        elif _channel and _owner:
            # Убедимся что бот является участником/админом канала
            _pub_user_obj = User.query.filter_by(username=_PUB_USERNAME).first()
            if _pub_user_obj:
                _pub_member = GroupMember.query.filter_by(group_id=_channel.id, user_id=_pub_user_obj.id).first()
                if not _pub_member:
                    db.session.add(GroupMember(group_id=_channel.id, user_id=_pub_user_obj.id, is_admin=True))
                    db.session.commit()
                    print("✓ Бот publisher добавлен в канал")

        # ── Сид: каталог подарков ────────────────────────────────────────────────
        if GiftType.query.count() == 0:
            _default_gifts = [
                # common
                GiftType(name='Сердечко',    emoji='❤️',  description='Маленький знак внимания',   price_sparks=5,    rarity='common'),
                GiftType(name='Звезда',      emoji='⭐',  description='Ты звезда!',                price_sparks=10,   rarity='common'),
                GiftType(name='Огонь',       emoji='🔥',  description='Горячий подарок',           price_sparks=15,   rarity='common'),
                GiftType(name='Роза',        emoji='🌹',  description='Цветок для тебя',           price_sparks=20,   rarity='common'),
                GiftType(name='Торт',        emoji='🎂',  description='С днём рождения!',          price_sparks=25,   rarity='common'),
                GiftType(name='Букет',       emoji='💐',  description='Весенний букет',            price_sparks=30,   rarity='common'),
                GiftType(name='Мишка',       emoji='🧸',  description='Плюшевый мишка',           price_sparks=35,   rarity='common'),
                GiftType(name='Шарики',      emoji='🎈',  description='Праздничные шарики',        price_sparks=40,   rarity='common'),
                GiftType(name='Подарок',     emoji='🎁',  description='Сюрприз внутри!',          price_sparks=45,   rarity='common'),
                GiftType(name='Кофе',        emoji='☕',  description='Чашечка кофе',             price_sparks=15,   rarity='common'),
                # rare
                GiftType(name='Алмаз',       emoji='💎',  description='Редкий и ценный',          price_sparks=50,   rarity='rare'),
                GiftType(name='Трофей',      emoji='🏆',  description='Ты победитель!',           price_sparks=75,   rarity='rare'),
                GiftType(name='Волшебная палочка', emoji='🪄', description='Исполни желание',     price_sparks=80,   rarity='rare'),
                GiftType(name='Кристалл',    emoji='🔮',  description='Магический кристалл',      price_sparks=90,   rarity='rare'),
                GiftType(name='Дракон',      emoji='🐉',  description='Могучий дракон',           price_sparks=100,  rarity='rare'),
                # epic
                GiftType(name='Корона',      emoji='👑',  description='Для настоящих королей',    price_sparks=150,  rarity='epic'),
                GiftType(name='Ракета',      emoji='🚀',  description='До луны и обратно',        price_sparks=200,  rarity='epic'),
                GiftType(name='Молния',      emoji='⚡',  description='Скорость света',           price_sparks=250,  rarity='epic'),
                GiftType(name='Галактика',   emoji='🌌',  description='Бесконечная вселенная',    price_sparks=300,  rarity='epic'),
                # legendary
                GiftType(name='Единорог',    emoji='🦄',  description='Легендарный подарок',      price_sparks=500,  rarity='legendary'),
                GiftType(name='Феникс',      emoji='🦅',  description='Возрождение из пепла',     price_sparks=750,  rarity='legendary'),
                GiftType(name='Бесконечность', emoji='♾️', description='Вечный подарок',         price_sparks=1000, rarity='legendary'),
            ]
            for _g in _default_gifts:
                db.session.add(_g)
            db.session.commit()
            print("✓ Каталог подарков создан")

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
    # Пропускаем socket.io и статику
    if request.path.startswith('/socket.io') or request.path.startswith('/static'):
        return
    # Обновляем активность только если есть токен сессии, раз в 60 секунд
    if 'user_id' in session and 'session_token' in session:
        try:
            token = session['session_token']
            if not hasattr(app, '_session_activity_cache'):
                app._session_activity_cache = {}
            now = time.time()
            if now - app._session_activity_cache.get(token, 0) < 60:
                return
            user_session = UserSession.query.filter_by(
                session_token=token,
                is_active=True
            ).first()
            if user_session:
                user_session.last_activity = datetime.utcnow()
                db.session.commit()
                app._session_activity_cache[token] = now
        except Exception as e:
            db.session.rollback()

_banned_ip_cache = {}
_banned_ip_cache_time = 0

@app.before_request
def check_ip_ban():
    """Блокируем запросы с забаненных IP."""
    # Пропускаем socket.io и статику
    if request.path.startswith('/socket.io') or request.path.startswith('/static'):
        return
    global _banned_ip_cache, _banned_ip_cache_time
    ip = request.remote_addr
    if not ip:
        return
    now = time.time()
    # Обновляем кэш раз в 60 секунд
    if now - _banned_ip_cache_time > 60:
        try:
            banned = BannedIP.query.all()
            _banned_ip_cache = {b.ip_address for b in banned}
            _banned_ip_cache_time = now
        except Exception:
            pass
    if ip in _banned_ip_cache:
        return jsonify({'error': 'Ваш IP заблокирован.'}), 403


_init_db()


@app.route('/admin/reports', methods=['GET'])
def get_reports():
    """Возвращает список всех жалоб"""
    return jsonify({"reports": reports})

@app.route('/admin/report', methods=['POST'])
def create_report():
    """Создает новую жалобу"""
    data = request.json
    
    # Антиспам жалоб: 1 жалоба на одного человека в день
    reporter = data.get("reporter")
    target_id = data.get("target_id")
    if reporter and target_id:
        from datetime import timedelta
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        existing_today = [r for r in reports if r.get("reporter") == reporter and str(r.get("target_id")) == str(target_id) and r.get("created_at", "") >= today_start.isoformat()]
        if existing_today:
            return jsonify({"error": "Вы уже подавали жалобу на этого пользователя сегодня"}), 429

    report = {
        "id": len(reports) + 1,
        "reported_user": data.get("username"),
        "category": data.get("category"),
        "details": data.get("details"),
        "reporter": reporter,
        "target_id": target_id,
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
                block_hours = 12 if user.is_premium else 24
                user.spam_block_until = datetime.utcnow() + timedelta(hours=block_hours)
                db.session.commit()

    return jsonify({"success": True, "report": report})


@app.route('/admin/report/<int:report_id>/resolve', methods=['POST'])
def resolve_report(report_id):
    """Помечает жалобу как решенную — снимает 10% репутации у нарушителя"""
    for report in reports:
        if report["id"] == report_id:
            report["status"] = "resolved"
            report["resolved_at"] = datetime.now().isoformat()
            # Снимаем 10% репутации у нарушителя
            target_id = report.get("target_id")
            if target_id:
                try:
                    target_user = User.query.get(int(target_id))
                    if target_user and not target_user.is_admin:
                        target_user.reputation = max(0, (target_user.reputation or 100) - 10)
                        # Авто-бан при репутации <= 0
                        if target_user.reputation <= 0 and not target_user.is_banned:
                            target_user.is_banned = True
                        db.session.commit()
                except Exception:
                    pass
            return jsonify({"success": True})
    return jsonify({"success": False, "error": "Report not found"})

@app.route('/admin/report/<int:report_id>/reject', methods=['POST'])
def reject_report(report_id):
    """Отклоняет жалобу — снимает 10% репутации у жалобщика (ложная жалоба)"""
    for report in reports:
        if report["id"] == report_id:
            report["status"] = "rejected"
            # Снимаем 10% репутации у жалобщика
            reporter_username = report.get("reporter")
            if reporter_username:
                try:
                    reporter_user = User.query.filter_by(username=reporter_username).first()
                    if reporter_user and not reporter_user.is_admin:
                        reporter_user.reputation = max(0, (reporter_user.reputation or 100) - 10)
                        db.session.commit()
                except Exception:
                    pass
            return jsonify({"success": True})
    return jsonify({"success": False, "error": "Report not found"})    


@app.route('/api/user/<int:user_id>/reputation', methods=['GET'])
def get_user_reputation(user_id):
    """Возвращает репутацию пользователя"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Не найден'}), 404
    rep = user.reputation if user.reputation is not None else 100
    if rep >= 80:
        level = 'Отличная'
        color = '#38a169'
    elif rep >= 60:
        level = 'Хорошая'
        color = '#667eea'
    elif rep >= 40:
        level = 'Средняя'
        color = '#d69e2e'
    elif rep >= 20:
        level = 'Плохая'
        color = '#e53e3e'
    else:
        level = 'Критическая'
        color = '#742a2a'
    return jsonify({'reputation': rep, 'level': level, 'color': color})


# ── Восстановление аккаунта ──────────────────────────────────────────────────

@app.route('/account/recovery', methods=['POST'])
def account_recovery():
    data = request.get_json() or {}
    username = data.get('username', '').strip().lower()
    reason = data.get('reason', '').strip()
    request_type = data.get('type', 'password')  # password | 2fa_lost
    if not username or not reason:
        return jsonify({'error': 'Заполните все поля'}), 400

    # Проверяем что пользователь существует
    user = User.query.filter_by(username=username).first()
    if not user:
        # Не раскрываем факт отсутствия — возвращаем успех (anti-enumeration)
        return jsonify({'success': True})

    # Rate limit: не более 2 заявок с одного IP за 24 часа
    ip = request.remote_addr
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_by_ip = PasswordResetRequest.query.filter(
        PasswordResetRequest.created_at > cutoff,
        PasswordResetRequest.ip_address == ip
    ).count()
    if recent_by_ip >= 2:
        return jsonify({'error': 'Слишком много заявок. Попробуйте через 24 часа.'}), 429

    # Проверяем нет ли уже pending-заявки от этого username
    existing = PasswordResetRequest.query.filter_by(
        username=username, status='pending'
    ).first()
    if existing:
        return jsonify({'error': 'Заявка уже отправлена и ожидает рассмотрения.'}), 400

    req = PasswordResetRequest(
        username=username,
        reason=reason,
        request_type=request_type,
        ip_address=ip
    )
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
    # Сбрасываем все предупреждения и блокировку заявок
    AdminWarning.query.filter_by(user_id=a.user_id).delete()
    a.user.admin_apply_blocked_until = None
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
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS telegram_chat_id VARCHAR(50)',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS telegram_link_code VARCHAR(20)',
        # Новые таблицы создаются через db.create_all() — здесь только колонки
        'ALTER TABLE sticker ALTER COLUMN image_url TYPE TEXT',
        'ALTER TABLE message ADD COLUMN IF NOT EXISTS hidden_for_sender BOOLEAN DEFAULT FALSE',
        'ALTER TABLE message ADD COLUMN IF NOT EXISTS hidden_for_receiver BOOLEAN DEFAULT FALSE',
        'ALTER TABLE message ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE',
        'ALTER TABLE sticker_pack ALTER COLUMN cover_url TYPE TEXT',
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


# ── Telegram 2FA привязка ─────────────────────────────────────────────────────

# Хранилище кодов привязки: code -> user_id
_tg_link_codes = {}

@app.route('/profile/telegram-link-code', methods=['POST'])
def generate_telegram_link_code():
    """Генерирует код привязки Telegram для текущего пользователя."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    code = 'TG-' + str(random.randint(100000, 999999))
    _tg_link_codes[code] = user.id
    # Код живёт 10 минут
    import threading
    def _expire():
        import time; time.sleep(600)
        _tg_link_codes.pop(code, None)
    threading.Thread(target=_expire, daemon=True).start()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    bot_username = ''
    if token:
        try:
            import urllib.request as _ur
            r = _ur.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
            info = json.loads(r.read())
            bot_username = info.get('result', {}).get('username', '')
        except Exception:
            pass
    return jsonify({'code': code, 'bot_username': bot_username})


@app.route('/profile/telegram-unlink', methods=['POST'])
def telegram_unlink():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    user.telegram_chat_id = None
    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/setup-telegram-webhook', methods=['POST'])
def setup_telegram_webhook():
    """Регистрирует webhook Telegram бота — только owner."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not _has_role(admin, 'owner'):
        return jsonify({'error': 'Нет доступа'}), 403
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        return jsonify({'error': 'TELEGRAM_BOT_TOKEN не задан'}), 400
    site_url = request.host_url.rstrip('/')
    webhook_url = f"{site_url}/telegram/webhook"
    import urllib.request as _ur
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    data = json.dumps({'url': webhook_url}).encode()
    req = _ur.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        resp = json.loads(_ur.urlopen(req, timeout=10).read())
        return jsonify(resp)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """Webhook от Telegram — обрабатывает привязку 2FA."""
    data = request.get_json(silent=True) or {}
    message = data.get('message', {})
    text = message.get('text', '').strip()
    chat_id = str(message.get('chat', {}).get('id', ''))
    if not chat_id:
        return jsonify({'ok': True})

    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')

    def tg_reply(msg):
        if not token:
            return
        import urllib.request as _ur
        payload = json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}).encode()
        req = _ur.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={'Content-Type': 'application/json'}
        )
        try:
            _ur.urlopen(req, timeout=5)
        except Exception:
            pass

    # Команда /start с кодом привязки
    if text.startswith('/start'):
        parts = text.split()
        code = parts[1] if len(parts) > 1 else ''
        if code in _tg_link_codes:
            user_id = _tg_link_codes.pop(code)
            u = User.query.get(user_id)
            if u:
                u.telegram_chat_id = chat_id
                db.session.commit()
                tg_reply(f"✅ Telegram успешно привязан к аккаунту <b>@{u.username}</b> в Tabletone!\n\nТеперь коды входа будут приходить сюда.")
            else:
                tg_reply("❌ Пользователь не найден.")
        else:
            tg_reply("👋 Привет! Чтобы привязать Telegram к Tabletone, перейди в профиль мессенджера и нажми «Привязать Telegram».")
    return jsonify({'ok': True})

# ── Webhook платёжного бота ───────────────────────────────────────────────────
_pay_user_data = {}  # tg_chat_id -> dict

def _pay_tg_send(token, chat_id, text, reply_markup=None, parse_mode='Markdown'):
    import urllib.request as _ur
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = _ur.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=raw, headers={'Content-Type': 'application/json; charset=utf-8'})
    try:
        resp = _ur.urlopen(req, timeout=8).read()
        result = json.loads(resp)
        if not result.get('ok'):
            print(f"pay_tg_send TG error: {result}")
        return result
    except Exception as e:
        print(f"pay_tg_send error: {e}")
        import traceback; traceback.print_exc()

def _pay_tg_answer(token, callback_id):
    import urllib.request as _ur
    data = json.dumps({'callback_query_id': callback_id}).encode()
    req = _ur.Request(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                      data=data, headers={'Content-Type': 'application/json'})
    try: _ur.urlopen(req, timeout=5)
    except: pass

def _pay_tg_edit(token, chat_id, msg_id, text, reply_markup=None):
    import urllib.request as _ur
    payload = {'chat_id': chat_id, 'message_id': msg_id, 'text': text, 'parse_mode': 'Markdown'}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    data = json.dumps(payload).encode()
    req = _ur.Request(f"https://api.telegram.org/bot{token}/editMessageText",
                      data=data, headers={'Content-Type': 'application/json'})
    try: _ur.urlopen(req, timeout=8)
    except: pass

def _pay_tg_send_photo(token, chat_id, photo, caption, reply_markup=None):
    import urllib.request as _ur
    payload = {'chat_id': chat_id, 'photo': photo, 'caption': caption, 'parse_mode': 'Markdown'}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    data = json.dumps(payload).encode()
    req = _ur.Request(f"https://api.telegram.org/bot{token}/sendPhoto",
                      data=data, headers={'Content-Type': 'application/json'})
    try: _ur.urlopen(req, timeout=8)
    except Exception as e: print(f"pay send_photo error: {e}")

PAY_PREMIUM_PLANS = {
    "premium_7":   {"label": "Premium 7 дней",    "price": "59 ₽",  "days": 7},
    "premium_14":  {"label": "Premium 14 дней",   "price": "99 ₽",  "days": 14},
    "premium_30":  {"label": "Premium 30 дней",   "price": "149 ₽", "days": 30},
    "premium_180": {"label": "Premium 6 месяцев", "price": "499 ₽", "days": 180},
    "premium_365": {"label": "Premium 1 год",     "price": "799 ₽", "days": 365},
}
PAY_SPARKS_PLANS = {
    "sparks_100":  {"label": "100 Искр ✨",  "price": "29 ₽",  "sparks": 100},
    "sparks_300":  {"label": "300 Искр ✨",  "price": "79 ₽",  "sparks": 300},
    "sparks_700":  {"label": "700 Искр ✨",  "price": "149 ₽", "sparks": 700},
    "sparks_1500": {"label": "1500 Искр ✨", "price": "299 ₽", "sparks": 1500},
    "sparks_5000": {"label": "5000 Искр ✨", "price": "799 ₽", "sparks": 5000},
}
PAY_CARD = "+79519603466"

def _pay_main_kb():
    return {"inline_keyboard": [
        [{"text": "👑 Купить Premium", "callback_data": "menu_premium"}],
        [{"text": "✨ Купить Искры",   "callback_data": "menu_sparks"}],
        [{"text": "🖼 Купить NFT",     "callback_data": "menu_nft"}],
    ]}

@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    token = os.environ.get('PAYMENT_BOT_TOKEN', '8705438057:AAEIeyFixNBr3eH4_4NIso57GKXOFvs3E_M')
    owner_tg_id = int(os.environ.get('OWNER_TELEGRAM_ID', '8081350794'))
    site_url_pay = os.environ.get('SITE_URL', 'https://hi-j5rs.onrender.com')
    pay_secret = os.environ.get('PAYMENT_SECRET', 'tabletone_payment_secret')
    data = request.get_json(silent=True) or {}

    # ── Callback query ────────────────────────────────────────────────────────
    if 'callback_query' in data:
        cq = data['callback_query']
        cq_id = cq['id']
        cq_data = cq.get('data', '')
        chat_id = str(cq['message']['chat']['id'])
        msg_id = cq['message']['message_id']
        ud = _pay_user_data.setdefault(chat_id, {})
        _pay_tg_answer(token, cq_id)

        if cq_data == 'menu_main':
            _pay_tg_edit(token, chat_id, msg_id, "Выбери что хочешь купить 👇", _pay_main_kb())
        elif cq_data == 'menu_premium':
            buttons = [[{"text": f"{p['label']} — {p['price']}", "callback_data": f"buy_{k}"}]
                       for k, p in PAY_PREMIUM_PLANS.items()]
            buttons.append([{"text": "◀️ Назад", "callback_data": "menu_main"}])
            _pay_tg_edit(token, chat_id, msg_id, "👑 *Выберите срок Premium:*", {"inline_keyboard": buttons})
        elif cq_data == 'menu_sparks':
            buttons = [[{"text": f"{p['label']} — {p['price']}", "callback_data": f"buy_{k}"}]
                       for k, p in PAY_SPARKS_PLANS.items()]
            buttons.append([{"text": "◀️ Назад", "callback_data": "menu_main"}])
            _pay_tg_edit(token, chat_id, msg_id, "✨ *Выберите количество Искр:*", {"inline_keyboard": buttons})
        elif cq_data == 'menu_nft':
            import urllib.request as _ur_nft
            try:
                resp_nft = json.loads(_ur_nft.urlopen(
                    f"{site_url_pay}/api/nft/collections", timeout=10
                ).read())
                cols = resp_nft.get("collections", [])
            except Exception:
                cols = []
            if not cols:
                _pay_tg_edit(token, chat_id, msg_id, "🖼 NFT коллекций пока нет.",
                    {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "menu_main"}]]})
            else:
                buttons = [[{"text": f"{c['name']} — {c.get('price','?')} ₽", "callback_data": f"buy_nft_{c['id']}"}]
                           for c in cols]
                buttons.append([{"text": "◀️ Назад", "callback_data": "menu_main"}])
                _pay_tg_edit(token, chat_id, msg_id, "🖼 *Выберите NFT:*", {"inline_keyboard": buttons})
        elif cq_data.startswith('buy_'):
            key = cq_data[4:]
            is_nft = key.startswith('nft_')
            if is_nft:
                nft_id = key[4:]
                import urllib.request as _ur_nft2
                try:
                    resp_nft2 = json.loads(_ur_nft2.urlopen(
                        f"{site_url_pay}/api/nft/collections", timeout=10
                    ).read())
                    cols2 = resp_nft2.get("collections", [])
                except Exception:
                    cols2 = []
                nft = next((c for c in cols2 if str(c['id']) == str(nft_id)), None)
                if nft:
                    ud['pending_key'] = key
                    ud['awaiting_username'] = True
                    ud['awaiting_screenshot'] = False
                    _pay_tg_edit(token, chat_id, msg_id,
                        f"✅ Вы выбрали: *{nft['name']}* — *{nft.get('price','?')} ₽*\n\nВведите ваш *username* в Tabletone (без @):",
                        {"inline_keyboard": [[{"text": "◀️ Отмена", "callback_data": "menu_main"}]]})
            else:
                plan = PAY_PREMIUM_PLANS.get(key) or PAY_SPARKS_PLANS.get(key)
                if plan:
                    ud['pending_key'] = key
                    ud['awaiting_username'] = True
                    ud['awaiting_screenshot'] = False
                    _pay_tg_edit(token, chat_id, msg_id,
                        f"✅ Вы выбрали: *{plan['label']}* — *{plan['price']}*\n\nВведите ваш *username* в Tabletone (без @):",
                        {"inline_keyboard": [[{"text": "◀️ Отмена", "callback_data": "menu_main"}]]})
        elif cq_data.startswith('confirm_') or cq_data.startswith('reject_'):
            if cq['from']['id'] != owner_tg_id:
                return jsonify({'ok': True})
            action = 'confirm' if cq_data.startswith('confirm_') else 'reject'
            raw = cq_data[len(action)+1:]
            key = None
            if raw.startswith('nft_'):
                # nft_{id}_{username}_{chat_id}
                parts_nft = raw.split('_')
                key = f"nft_{parts_nft[1]}"
                raw = '_'.join(parts_nft[2:])
            else:
                for k in list(PAY_PREMIUM_PLANS.keys()) + list(PAY_SPARKS_PLANS.keys()):
                    if raw.startswith(k + '_'):
                        key = k
                        raw = raw[len(k)+1:]
                        break
            if not key:
                return jsonify({'ok': True})
            last_ = raw.rfind('_')
            username = raw[:last_]
            user_chat_id = raw[last_+1:]
            is_nft_confirm = key.startswith('nft_')
            import urllib.request as _ur_pay
            if action == 'confirm':
                activated = False
                try:
                    if is_nft_confirm:
                        ep = f"{site_url_pay}/api/nft/give"
                        pl = json.dumps({"username": username, "nft_id": key[4:], "secret": pay_secret}).encode()
                    elif key.startswith('premium'):
                        plan = PAY_PREMIUM_PLANS[key]
                        ep = f"{site_url_pay}/api/payment/activate-premium"
                        pl = json.dumps({"username": username, "days": plan["days"], "secret": pay_secret}).encode()
                    else:
                        plan = PAY_SPARKS_PLANS[key]
                        ep = f"{site_url_pay}/api/payment/add-sparks"
                        pl = json.dumps({"username": username, "sparks": plan["sparks"], "secret": pay_secret}).encode()
                    req = _ur_pay.Request(ep, data=pl, headers={'Content-Type': 'application/json'})
                    resp = json.loads(_ur_pay.urlopen(req, timeout=10).read())
                    activated = resp.get('success', False)
                except Exception as e:
                    print(f"pay activate error: {e}")
                if is_nft_confirm:
                    user_msg = f"🎉 *Оплата подтверждена!*\n\n🖼 NFT выдан для @{username}!"
                elif key.startswith('premium'):
                    plan = PAY_PREMIUM_PLANS[key]; user_msg = f"🎉 *Оплата подтверждена!*\n\n👑 Premium на *{plan['days']} дней* активирован для @{username}!"
                else:
                    plan = PAY_SPARKS_PLANS[key]; user_msg = f"🎉 *Оплата подтверждена!*\n\n✨ *{plan['sparks']} Искр* зачислено для @{username}!"
                if not activated:
                    user_msg += "\n\n_(Автоактивация не удалась — администратор активирует вручную)_"
                _pay_tg_send(token, user_chat_id, user_msg)
                cap = cq['message'].get('caption', '') + f"\n\n✅ Подтверждено! {'✓' if activated else 'вручную'}"
            else:
                _pay_tg_send(token, user_chat_id,
                    "❌ *Оплата отклонена.*\n\nАдминистратор не подтвердил перевод.\n"
                    "Если вы уверены что оплатили — напишите @kotakbaslife.")
                cap = cq['message'].get('caption', '') + "\n\n❌ Отклонено."
            cap_pl = json.dumps({'chat_id': owner_tg_id, 'message_id': msg_id,
                                 'caption': cap, 'parse_mode': 'Markdown'}).encode()
            req_cap = _ur_pay.Request(f"https://api.telegram.org/bot{token}/editMessageCaption",
                                      data=cap_pl, headers={'Content-Type': 'application/json'})
            try: _ur_pay.urlopen(req_cap, timeout=5)
            except: pass
        return jsonify({'ok': True})

    # ── Обычное сообщение ─────────────────────────────────────────────────────
    message = data.get('message', {})
    if not message:
        return jsonify({'ok': True})
    chat_id = str(message.get('chat', {}).get('id', ''))
    text_msg = message.get('text', '').strip()
    photos = message.get('photo', [])
    tg_user = message.get('from', {})
    ud = _pay_user_data.setdefault(chat_id, {})

    if text_msg.startswith('/start'):
        try:
            ud.clear()
            # Deep link: /start pay_KEY_USERNAME — сразу к реквизитам
            parts = text_msg.split(maxsplit=1)
            start_param = parts[1] if len(parts) > 1 else ''
            if start_param.startswith('pay_'):
                # Формат: pay_premium_7_username или pay_sparks_100_username
                rest = start_param[4:]  # убираем "pay_"
                # Ищем ключ плана
                matched_key = None
                for k in list(PAY_PREMIUM_PLANS.keys()) + list(PAY_SPARKS_PLANS.keys()):
                    if rest.startswith(k + '_'):
                        matched_key = k
                        username = rest[len(k)+1:]
                        break
                if matched_key and username:
                    plan = PAY_PREMIUM_PLANS.get(matched_key) or PAY_SPARKS_PLANS.get(matched_key)
                    ud['pending_key'] = matched_key
                    ud['tabletone_username'] = username
                    ud['awaiting_username'] = False
                    ud['awaiting_screenshot'] = True
                    _pay_tg_send(token, chat_id,
                        f"✅ Вы выбрали: *{plan['label']}* — *{plan['price']}*\n\n"
                        f"💳 *Реквизиты для оплаты:*\n\n"
                        f"📱 Номер: `{PAY_CARD}`\n"
                        f"🏦 по номеру телефона (СБП / любой банк)\n"
                        f"💰 Сумма: *{plan['price']}*\n\n"
                        f"После перевода пришлите *скриншот* подтверждения оплаты.\n⏳ У вас есть *10 минут*.")
                    return jsonify({'ok': True})
            # Обычный /start
            _pay_tg_send(token, chat_id,
                "👋 Привет! Я бот оплаты *Tabletone*.\n\nВыбери что хочешь купить 👇", _pay_main_kb())
        except Exception as e:
            print(f"payment_webhook /start error: {e}")
            import traceback; traceback.print_exc()
    elif text_msg.startswith('/givepremium') and tg_user.get('id') == owner_tg_id:
        parts = text_msg.split()
        if len(parts) >= 3:
            uname = parts[1].lstrip('@')
            try:
                import urllib.request as _ur_pay
                pl = json.dumps({"username": uname, "days": int(parts[2]), "secret": pay_secret}).encode()
                req = _ur_pay.Request(f"{site_url_pay}/api/payment/activate-premium",
                                      data=pl, headers={'Content-Type': 'application/json'})
                resp = json.loads(_ur_pay.urlopen(req, timeout=10).read())
                _pay_tg_send(token, chat_id,
                    f"✅ Premium на *{parts[2]} дней* выдан @{uname}." if resp.get('success')
                    else f"❌ Ошибка: {resp.get('error','?')}")
            except Exception as e:
                _pay_tg_send(token, chat_id, f"❌ Ошибка: {e}")
        else:
            _pay_tg_send(token, chat_id, "Использование: `/givepremium <username> <дней>`")
    elif text_msg.startswith('/givesparks') and tg_user.get('id') == owner_tg_id:
        parts = text_msg.split()
        if len(parts) >= 3:
            uname = parts[1].lstrip('@')
            try:
                import urllib.request as _ur_pay
                pl = json.dumps({"username": uname, "sparks": int(parts[2]), "secret": pay_secret}).encode()
                req = _ur_pay.Request(f"{site_url_pay}/api/payment/add-sparks",
                                      data=pl, headers={'Content-Type': 'application/json'})
                resp = json.loads(_ur_pay.urlopen(req, timeout=10).read())
                _pay_tg_send(token, chat_id,
                    f"✅ {int(parts[2]):+} Искр у @{uname}." if resp.get('success')
                    else f"❌ Ошибка: {resp.get('error','?')}")
            except Exception as e:
                _pay_tg_send(token, chat_id, f"❌ Ошибка: {e}")
        else:
            _pay_tg_send(token, chat_id, "Использование: `/givesparks <username> <кол-во>`")
    elif text_msg.startswith('/ownerhelp') and tg_user.get('id') == owner_tg_id:
        _pay_tg_send(token, chat_id,
            "🔧 *Owner-команды:*\n\n`/givepremium <username> <дней>`\n`/givesparks <username> <кол-во>`")
    elif ud.get('awaiting_username') and text_msg and not text_msg.startswith('/'):
        username = text_msg.lstrip('@')
        if len(username) < 3:
            _pay_tg_send(token, chat_id, "❌ Слишком короткий username. Попробуйте ещё раз:")
        else:
            key = ud.get('pending_key')
            is_nft = key and key.startswith('nft_')
            plan = (None if is_nft else (PAY_PREMIUM_PLANS.get(key) or PAY_SPARKS_PLANS.get(key)))
            if is_nft:
                nft_id = key[4:]
                import urllib.request as _ur_nft3
                try:
                    resp_nft3 = json.loads(_ur_nft3.urlopen(
                        f"{site_url_pay}/api/nft/collections", timeout=10
                    ).read())
                    nft_item = next((c for c in resp_nft3.get("collections", []) if str(c['id']) == str(nft_id)), None)
                except Exception:
                    nft_item = None
                if nft_item:
                    ud['tabletone_username'] = username
                    ud['awaiting_username'] = False
                    ud['awaiting_screenshot'] = True
                    _pay_tg_send(token, chat_id,
                        f"💳 *Реквизиты для оплаты:*\n\n"
                        f"📱 Номер: `{PAY_CARD}`\n"
                        f"🏦 по номеру телефона (СБП / любой банк)\n"
                        f"💰 Сумма: *{nft_item.get('price','?')} ₽*\n\n"
                        f"После перевода пришлите *скриншот* подтверждения оплаты.\n⏳ У вас есть *10 минут*.")
            elif plan:
                ud['tabletone_username'] = username
                ud['awaiting_username'] = False
                ud['awaiting_screenshot'] = True
                _pay_tg_send(token, chat_id,
                    f"💳 *Реквизиты для оплаты:*\n\n"
                    f"📱 Номер: `{PAY_CARD}`\n"
                    f"🏦 по номеру телефона (СБП / любой банк)\n"
                    f"💰 Сумма: *{plan['price']}*\n\n"
                    f"После перевода пришлите *скриншот* подтверждения оплаты.\n⏳ У вас есть *10 минут*.")
    elif ud.get('awaiting_screenshot') and photos:
        ud['awaiting_screenshot'] = False
        key = ud.get('pending_key')
        username = ud.get('tabletone_username', '?')
        is_nft = key and key.startswith('nft_')
        if is_nft:
            nft_id = key[4:]
            import urllib.request as _ur_nft4
            try:
                resp_nft4 = json.loads(_ur_nft4.urlopen(
                    f"{site_url_pay}/api/nft/collections", timeout=10
                ).read())
                nft_item2 = next((c for c in resp_nft4.get("collections", []) if str(c['id']) == str(nft_id)), None)
            except Exception:
                nft_item2 = None
            label = nft_item2['name'] if nft_item2 else key
            price_str = f"{nft_item2.get('price','?')} ₽" if nft_item2 else "?"
        else:
            plan = PAY_PREMIUM_PLANS.get(key) or PAY_SPARKS_PLANS.get(key)
            if not plan:
                _pay_tg_send(token, chat_id, "Что-то пошло не так. Напишите /start")
                return jsonify({'ok': True})
            label = plan['label']
            price_str = plan['price']
        photo_file_id = photos[-1]['file_id']
        user_info = f"@{tg_user.get('username')}" if tg_user.get('username') else f"id:{tg_user.get('id')}"
        _pay_tg_send(token, chat_id,
            "📨 Скриншот получен! Ожидайте подтверждения от администратора.\nОбычно это занимает до 15 минут.")
        confirm_kb = {"inline_keyboard": [[
            {"text": "✅ Подтвердить", "callback_data": f"confirm_{key}_{username}_{chat_id}"},
            {"text": "❌ Отклонить",   "callback_data": f"reject_{key}_{username}_{chat_id}"},
        ]]}
        _pay_tg_send_photo(token, owner_tg_id, photo_file_id,
            f"⚠️ *Новая заявка на оплату!*\n\n"
            f"👤 TG: {user_info}\n🎮 Tabletone: @{username}\n"
            f"🛒 Товар: {label}\n💰 Сумма: {price_str}\n\nПодтвердить перевод?",
            confirm_kb)

    return jsonify({'ok': True})


@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    # Проверяем, не забанен ли пользователь
    if user and user.is_banned:
        session.pop('user_id', None)
        return redirect(url_for('login'))
    
    # Режим обслуживания — обычным пользователям показываем заглушку
    is_staff = user and user.admin_role in ('moderator', 'admin', 'senior_admin', 'owner')
    if MAINTENANCE_MODE and not is_staff:
        return render_template('maintenance.html')
    
    return render_template('index.html', user=user)

# Регистрация
@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 3 per second")
def register():
    import random as _random
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']
        display_name = request.form.get('display_name', '').strip()
        email = request.form.get('email', '').strip().lower()

        # Проверка капчи
        captcha_answer = request.form.get('captcha_answer', '').strip()
        captcha_expected = session.get('captcha_answer')
        if not captcha_answer or not captcha_expected or str(captcha_answer) != str(captcha_expected):
            a, b = _random.randint(1, 9), _random.randint(1, 9)
            session['captcha_answer'] = a + b
            return render_template('register.html', error='Неверный ответ на проверку. Попробуйте ещё раз.',
                                   captcha_q=f'{a} + {b}')
        session.pop('captcha_answer', None)

        # Проверка на существование пользователя
        if len(username) < 4:
            a, b = _random.randint(1, 9), _random.randint(1, 9)
            session['captcha_answer'] = a + b
            return render_template('register.html', error='Username должен содержать минимум 4 символа', captcha_q=f'{a} + {b}')
        if User.query.filter_by(username=username).first():
            a, b = _random.randint(1, 9), _random.randint(1, 9)
            session['captcha_answer'] = a + b
            return render_template('register.html', error='Пользователь с таким именем уже существует', captcha_q=f'{a} + {b}')
        if not email or '@' not in email or '.' not in email.split('@')[-1]:
            a, b = _random.randint(1, 9), _random.randint(1, 9)
            session['captcha_answer'] = a + b
            return render_template('register.html', error='Введите корректный email адрес', captcha_q=f'{a} + {b}')
        if User.query.filter_by(email=email).first():
            a, b = _random.randint(1, 9), _random.randint(1, 9)
            session['captcha_answer'] = a + b
            return render_template('register.html', error='Этот email уже используется', captcha_q=f'{a} + {b}')

        # Генерация случайного цвета для аватара
        colors = ['#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b', '#fa709a', '#fee140', '#30cfd0']

        user = User(
            username=username,
            display_name=display_name or username,
            avatar_color=_random.choice(colors),
            timezone=request.form.get('timezone', 'Europe/Moscow'),
            email=email,
            email_verified=False
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # Назначаем owner если это romancev228
        if username == 'romancev228':
            user.is_admin = True
            user.admin_role = 'owner'
            db.session.commit()

        # Отправляем код подтверждения email
        verify_code = str(_random.randint(100000, 999999))
        user.two_fa_code = verify_code
        user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=30)
        db.session.commit()

        import threading
        threading.Thread(target=_send_email_register_verify, args=(email, verify_code, username), daemon=True).start()

        session['verify_email_user_id'] = user.id
        return redirect(url_for('register_verify_email'))

    a, b = _random.randint(1, 9), _random.randint(1, 9)
    session['captcha_answer'] = a + b
    return render_template('register.html', captcha_q=f'{a} + {b}')


@app.route('/register/verify-email', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def register_verify_email():
    user_id = session.get('verify_email_user_id')
    if not user_id:
        return redirect(url_for('register'))
    user = User.query.get(user_id)
    if not user:
        return redirect(url_for('register'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if (user.two_fa_code == code and
                user.two_fa_code_expires and
                user.two_fa_code_expires > datetime.utcnow()):
            user.email_verified = True
            user.two_fa_code = None
            user.two_fa_code_expires = None
            db.session.commit()
            session.pop('verify_email_user_id', None)
            session['user_id'] = user.id
            import threading
            threading.Thread(target=_send_tabletone_welcome, args=(user.id,), daemon=True).start()
            return redirect(url_for('index'))
        return render_template('register_verify_email.html', error='Неверный или просроченный код', email=user.email)

    return render_template('register_verify_email.html', email=user.email)


@app.route('/register/resend-verify', methods=['POST'])
@limiter.limit("3 per minute")
def register_resend_verify():
    import random as _random
    user_id = session.get('verify_email_user_id')
    if not user_id:
        return jsonify({'error': 'Сессия истекла'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    code = str(_random.randint(100000, 999999))
    user.two_fa_code = code
    user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()
    import threading
    threading.Thread(target=_send_email_register_verify, args=(user.email, code, user.username), daemon=True).start()
    return jsonify({'success': True})

# Вход
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("20 per minute; 5 per second")
def login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        password = request.form['password']
        app.logger.warning(f"[LOGIN] start for {username}")
        
        user = User.query.filter_by(username=username).first()
        app.logger.warning(f"[LOGIN] user query done, found={user is not None}")
        
        if user and user.check_password(password):
            app.logger.warning(f"[LOGIN] password ok for {username}")
            # Проверяем, не забанен ли пользователь
            if user.is_banned:
                return render_template('login.html', error='Ваш аккаунт заблокирован администратором')
            
            # Trusted IP bypass
            import time as _time
            _trusted_ip = session.get('trusted_ip')
            _trusted_until = session.get('trusted_ip_until', 0)
            _current_ip = get_client_ip()
            _ip_trusted = (_trusted_ip == _current_ip and _time.time() < _trusted_until)

            # 2FA check
            if user.two_fa_enabled and not _ip_trusted:
                code = str(random.randint(100000, 999999))
                from datetime import timedelta
                user.two_fa_code = code
                user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=10)
                db.session.commit()
                # Отправляем код асинхронно чтобы не блокировать запрос
                _uid = user.id
                import threading
                threading.Thread(target=_send_2fa_code, args=(_uid, code), daemon=True).start()
                # Сохраняем user_id во временной сессии для верификации
                session['2fa_pending_user_id'] = user.id
                session['2fa_resend_count'] = 0
                session['2fa_last_resend'] = __import__('time').time()
                return redirect(url_for('login_2fa'))

            # Проверка нового IP — если email подтверждён и IP ранее не использовался
            if not _ip_trusted and user.email and user.email_verified:
                _known_ips = {s.ip_address for s in UserSession.query.filter_by(user_id=user.id, is_active=True).all()}
                if _current_ip not in _known_ips:
                    import threading
                    code = str(random.randint(100000, 999999))
                    user.two_fa_code = code
                    user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=15)
                    db.session.commit()
                    threading.Thread(target=_send_email_2fa, args=(user.email, code), daemon=True).start()
                    session['2fa_pending_user_id'] = user.id
                    session['2fa_resend_count'] = 0
                    session['2fa_last_resend'] = __import__('time').time()
                    session['2fa_reason'] = 'new_ip'
                    return redirect(url_for('login_2fa'))

            session['user_id'] = user.id
            user.last_seen = datetime.utcnow()
            
            # Создаем новую сессию
            session_token = secrets.token_urlsafe(32)
            
            # Получаем информацию об устройстве
            user_agent = request.headers.get('User-Agent', '')
            ip_address = get_client_ip()
            
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

            app.logger.warning(f"[LOGIN] before commit for {username}")
            db.session.commit()
            app.logger.warning(f"[LOGIN] after commit for {username}")

            # Уведомление о входе с нового устройства
            existing_sessions = UserSession.query.filter_by(
                user_id=user.id, is_active=True
            ).filter(UserSession.id != new_session.id).count()
            app.logger.warning(f"[LOGIN] existing_sessions={existing_sessions}")
            if existing_sessions == 0:
                # Первый вход — не уведомляем
                pass
            else:
                import threading
                threading.Thread(
                    target=_notify_new_login,
                    args=(user.id, device_name, ip_address),
                    daemon=True
                ).start()

            # Уведомление администратору о новых обращениях в поддержку
            if user.is_admin:
                import threading
                threading.Thread(target=_notify_admin_support, args=(user.id,), daemon=True).start()

            next_url = request.args.get('next') or request.form.get('next')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect(url_for('index'))
        
        return render_template('login.html', error='Неверное имя пользователя или пароль')
    
    next_url = request.args.get('next', '')
    return render_template('login.html', next_url=next_url)

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
            'is_bot': user.is_bot,
            'reputation': user.reputation if user.reputation is not None else 100
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
    
    try:
        # Получаем параметр last_check для polling
        last_check = request.args.get('last_check')
        
        uid = session['user_id']

        other_user = User.query.get(user_id)
        if not other_user:
            return jsonify({'error': 'Пользователь не найден'}), 404

        query = Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == uid, Message.receiver_id == user_id,
                        Message.hidden_for_sender == False),
                db.and_(Message.sender_id == user_id, Message.receiver_id == uid,
                        Message.hidden_for_receiver == False)
            )
        )
        
        # Если есть last_check, фильтруем только новые сообщения
        if last_check:
            try:
                last_check_time = datetime.fromtimestamp(int(last_check) / 1000)
                query = query.filter(Message.timestamp > last_check_time)
            except:
                pass
        
        messages = query.order_by(Message.timestamp.asc()).all()
        
        def safe_msg_dict(msg):
            try:
                decrypted = msg.decrypted_content
                reply = None
                if msg.reply_to_id and msg.reply_to:
                    try:
                        reply = {
                            'id': msg.reply_to.id,
                            'content': msg.reply_to.decrypted_content if not msg.reply_to.is_deleted else '[удалено]',
                            'sender_name': (msg.reply_to.sender.display_name or msg.reply_to.sender.username) if msg.reply_to.sender else '?'
                        }
                    except Exception:
                        reply = None
                return {
                    'id': msg.id,
                    'sender_id': msg.sender_id,
                    'content': decrypted,
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
                    'is_read': msg.is_read,
                    'reply_to': reply,
                    'bot_buttons': _get_bot_buttons_for_msg(msg),
                    'sticker_pack_id': _get_sticker_pack_id(decrypted) if msg.message_type == 'sticker' or (decrypted and decrypted.startswith('[sticker]')) else None,
                    'gift': _get_gift_data_for_msg(msg) if msg.message_type == 'gift' else None,
                }
            except Exception as e:
                app.logger.error(f'Error serializing message {msg.id}: {e}')
                return {
                    'id': msg.id, 'sender_id': msg.sender_id, 'content': '',
                    'message_type': msg.message_type or 'text', 'media_url': None,
                    'media_files': [], 'duration': None,
                    'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
                    'timestamp_iso': msg.timestamp.isoformat() + 'Z',
                    'edited_at': None, 'is_deleted': msg.is_deleted,
                    'is_mine': msg.sender_id == session['user_id'],
                    'is_read': msg.is_read, 'reply_to': None,
                    'bot_buttons': [], 'sticker_pack_id': None, 'gift': None,
                }

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
            'messages': [safe_msg_dict(msg) for msg in messages]
        })
    except Exception as e:
        app.logger.error(f'get_chat error for user {user_id}: {e}', exc_info=True)
        return jsonify({'error': 'Ошибка загрузки сообщений', 'detail': str(e)}), 500

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

            # Помечаем все непрочитанные сообщения от этого пользователя как прочитанные
            unread_msgs = Message.query.filter(
                Message.sender_id == user_id,
                Message.receiver_id == session['user_id'],
                Message.is_read == False
            ).all()
            msg_ids = [m.id for m in unread_msgs]
            for m in unread_msgs:
                m.is_read = True
            
            db.session.commit()

            # Уведомляем отправителя что его сообщения прочитаны
            if msg_ids:
                socketio.emit('messages_read', {
                    'by_user_id': session['user_id'],
                    'message_ids': msg_ids
                }, room=f'user_{user_id}', namespace='/')
        
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

    # Если в спам-блоке — можно писать взаимным контактам, ботам, или отвечать тем, кто написал первым
    if blocked:
        uid = session['user_id']
        # Боты — всегда разрешено
        if not receiver.is_bot:
            i_added = Contact.query.filter_by(user_id=uid, contact_id=receiver_id).first() is not None
            they_added = Contact.query.filter_by(user_id=receiver_id, contact_id=uid).first() is not None
            they_wrote_first = Message.query.filter_by(
                sender_id=receiver_id, receiver_id=uid
            ).first() is not None
            if not (i_added and they_added) and not they_wrote_first:
                return jsonify({'error': 'spam_blocked', 'until': until_str}), 403

    # Проверяем настройки приватности получателя (кто может писать)
    if not receiver.is_bot and receiver.id != session['user_id']:
        _sender_is_staff2 = sender.is_admin or bool(sender.admin_role)
        if not _sender_is_staff2 and not _privacy_allows(receiver, sender.id, 'privacy_who_can_message'):
            priv_val = receiver.privacy_who_can_message or 'everyone'
            if priv_val == 'nobody':
                return jsonify({'error': 'privacy', 'message': 'Этот пользователь не принимает сообщения'}), 403
            elif priv_val == 'contacts':
                return jsonify({'error': 'privacy', 'message': 'Этот пользователь принимает сообщения только от контактов'}), 403
            elif priv_val == 'premium':
                return jsonify({'error': 'privacy', 'message': 'Этот пользователь принимает сообщения только от Premium пользователей'}), 403

    # Premium Support доступен только для Premium пользователей
    _sender_is_staff = sender.is_admin or bool(sender.admin_role)
    if receiver.username == 'premium_support' and not sender.is_premium and not _sender_is_staff:
        return jsonify({'error': 'premium_required', 'message': 'Premium Support доступен только для Premium пользователей'}), 403

    # Обычная поддержка недоступна для Premium пользователей (кроме стаффа)
    if receiver.username == 'tabletone_supportbot' and sender.is_premium and not _sender_is_staff:
        return jsonify({'error': 'premium_required', 'message': 'У вас Premium — используйте Premium Support (@premium_support)'}), 403

    # Платные сообщения: если у получателя установлена цена — списываем искры
    _msg_price = getattr(receiver, 'msg_price', None)
    if _msg_price and _msg_price > 0 and not _sender_is_staff and sender.id != receiver.id:
        # Первое сообщение в диалоге — проверяем, было ли уже общение
        _already_talked = Message.query.filter(
            ((Message.sender_id == sender.id) & (Message.receiver_id == receiver.id)) |
            ((Message.sender_id == receiver.id) & (Message.receiver_id == sender.id))
        ).first()
        if not _already_talked:
            if not _spend_sparks(sender.id, _msg_price, 'msg_price', receiver.id):
                return jsonify({
                    'error': 'not_enough_sparks',
                    'message': f'Для первого сообщения этому пользователю нужно {_msg_price} ✨ искр',
                    'required': _msg_price
                }), 402

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

    # Данные reply_to для включения в сокет
    reply_to_data = None
    if message.reply_to_id and message.reply_to:
        reply_to_data = {
            'id': message.reply_to.id,
            'content': message.reply_to.content if not message.reply_to.is_deleted else '[удалено]',
            'sender_name': (message.reply_to.sender.display_name or message.reply_to.sender.username) if message.reply_to.sender else '?'
        }

    try:
        # Отправляем отправителю
        socketio.emit('new_message', {
            'message': {
                'id': message.id,
                'sender_id': message.sender_id,
                'content': message.decrypted_content,
                'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
                'timestamp_iso': message.timestamp.isoformat() + 'Z',
                'reply_to': reply_to_data,
                'is_mine': True,
                'is_read': False
            },
            'other_user_id': receiver_id,
            'sender_info': sender_info
        }, room=f'user_{session["user_id"]}', namespace='/')
        
        # Отправляем получателю
        socketio.emit('new_message', {
            'message': {
                'id': message.id,
                'sender_id': message.sender_id,
                'content': message.decrypted_content,
                'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
                'timestamp_iso': message.timestamp.isoformat() + 'Z',
                'reply_to': reply_to_data,
                'is_mine': False
            },
            'other_user_id': session['user_id'],
            'sender_info': sender_info
        }, room=f'user_{receiver_id}', namespace='/')
    except Exception as e:
        print(f"Error emitting message: {e}")
    
    # Если получатель — бот, триггерим его webhook
    if receiver.is_bot:
        # Проверка: бот @premium_support доступен только Premium пользователям
        if receiver.username == 'premium_support' and not sender.is_premium and not sender.is_admin:
            _bot_send_message(receiver_id, sender.id,
                "⭐ *Premium Support* доступен только для Premium пользователей.\n\n"
                "Оформите Premium чтобы получить приоритетную поддержку от разработчика.\n\n"
                "Для обычной поддержки напишите боту @tabletone_supportbot")
            return jsonify({'success': True, 'message': {'id': message.id}})
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

    # Автоответ — если у получателя включён
    try:
        if not receiver.is_bot and getattr(receiver, 'auto_reply_text', None):
            recent_auto = Message.query.filter_by(
                sender_id=receiver_id, receiver_id=session['user_id']
            ).filter(Message.timestamp >= datetime.utcnow() - timedelta(minutes=5)).first()
            if not recent_auto:
                auto_msg = Message(
                    sender_id=receiver_id,
                    receiver_id=session['user_id'],
                    content=encrypt_msg(receiver.auto_reply_text),
                    message_type='text'
                )
                db.session.add(auto_msg)
                db.session.flush()
                socketio.emit('new_message', {
                    'message': {
                        'id': auto_msg.id,
                        'sender_id': receiver_id,
                        'content': receiver.auto_reply_text,
                        'timestamp': auto_msg.timestamp.strftime('%H:%M %d.%m'),
                        'timestamp_iso': auto_msg.timestamp.isoformat() + 'Z',
                        'is_mine': False,
                        'is_auto_reply': True
                    },
                    'other_user_id': receiver_id,
                    'sender_info': {'id': receiver.id, 'username': receiver.username, 'display_name': receiver.display_name or receiver.username, 'avatar_color': receiver.avatar_color, 'avatar_letter': receiver.get_avatar_letter()}
                }, room=f'user_{session["user_id"]}', namespace='/')
                db.session.commit()
    except Exception as _e:
        db.session.rollback()
        print(f'[auto_reply] error: {_e}')

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
    
    receiver = User.query.get(int(receiver_id))
    if not receiver:
        app.logger.warning(f"send_voice: receiver {receiver_id} not found")
        return jsonify({'error': f'Получатель {receiver_id} не найден'}), 404
    if receiver.is_banned:
        return jsonify({'error': 'Получатель заблокирован'}), 404
    
    # Сохраняем как base64 data URL (Render ephemeral FS — файлы не переживают рестарт)
    import base64 as _b64v
    audio_bytes = audio_file.read()
    audio_b64 = _b64v.b64encode(audio_bytes).decode('ascii')
    media_url = f'data:audio/webm;base64,{audio_b64}'

    # Создаем сообщение
    message = Message(
        sender_id=session['user_id'],
        receiver_id=int(receiver_id),
        content='[Голосовое сообщение]',
        message_type='voice',
        media_url=media_url,
        duration=int(duration)
    )
    
    db.session.add(message)
    db.session.commit()
    
    # Отправляем через Socket.IO
    message_data = {
        'id': message.id,
        'sender_id': message.sender_id,
        'content': message.decrypted_content,
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
            'content': message.decrypted_content,
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
            'content': message.decrypted_content,
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

        # Если получатель — бот stickers, триггерим обработку файла
        if receiver.is_bot:
            bot = Bot.query.filter_by(user_id=int(receiver_id), is_active=True).first()
            if bot:
                _trigger_webhook(bot, {
                    'message': {
                        'message_id': message.id,
                        'from': {'id': session['user_id']},
                        'text': message.media_url,  # передаём URL как текст для stickers-бота
                        'date': message.timestamp.isoformat() + 'Z'
                    }
                })

        return jsonify({
            'success': True,
            'message_id': message.id,
            'media_url': message.media_url
        })
    
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"Error in send_image: {e}\n{tb}")
        return jsonify({'error': f'Ошибка сервера: {str(e)}', 'detail': tb}), 500

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
            if not file or not file.filename:
                continue
            orig_filename = file.filename
            if not allowed_file(orig_filename, ALLOWED_EXTENSIONS):
                continue
            filename = secure_filename(orig_filename)
            if not filename:
                filename = f'file_{index}'
            # Определяем расширение
            ext = orig_filename.rsplit('.', 1)[-1].lower() if '.' in orig_filename else 'bin'
            base = filename.rsplit('.', 1)[0] if '.' in filename else filename
            timestamp = int(time.time() * 1000)
            unique_filename = f"{base}_{timestamp}.{ext}"

            # Определяем тип файла по расширению (content_type может быть None)
            content_type = file.content_type or ''
            if content_type.startswith('image/') or ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
                media_type = 'image'
                folder = 'images'
            elif content_type.startswith('video/') or ext in ('mp4', 'webm'):
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

        if not media_files:
            db.session.rollback()
            return jsonify({'error': 'Нет допустимых файлов для отправки'}), 400

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
                'content': message.decrypted_content,
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

        # Если получатель — бот, триггерим для каждого файла
        receiver = User.query.get(receiver_id)
        if receiver and receiver.is_bot:
            bot = Bot.query.filter_by(user_id=receiver_id, is_active=True).first()
            if bot:
                for mf in media_files:
                    _trigger_webhook(bot, {
                        'message': {
                            'message_id': message.id,
                            'from': {'id': session['user_id']},
                            'text': mf['media_url'],
                            'date': message.timestamp.isoformat() + 'Z'
                        }
                    })

        return jsonify({
            'success': True,
            'message_id': message.id,
            'media_files': media_files
        })
    
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"Error in send_multiple_files: {e}\n{tb}")
        return jsonify({'error': f'Ошибка сервера: {str(e)}', 'detail': tb}), 500

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
            if not file or not file.filename:
                continue
            orig_filename = file.filename
            if not allowed_file(orig_filename, ALLOWED_EXTENSIONS):
                continue
            filename = secure_filename(orig_filename)
            if not filename:
                filename = f'file_{index}'
            ext = orig_filename.rsplit('.', 1)[-1].lower() if '.' in orig_filename else 'bin'
            base = filename.rsplit('.', 1)[0] if '.' in filename else filename
            timestamp = int(time.time() * 1000)
            unique_filename = f"{base}_{timestamp}.{ext}"

            content_type = file.content_type or ''
            if content_type.startswith('image/') or ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
                media_type = 'image'
                folder = 'images'
            elif content_type.startswith('video/') or ext in ('mp4', 'webm'):
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

        if not media_files:
            db.session.rollback()
            return jsonify({'error': 'Нет допустимых файлов для отправки'}), 400

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
                # Находим последнее НЕудалённое сообщение с этим пользователем
                last_message = Message.query.filter(
                    ((Message.sender_id == session['user_id']) & (Message.receiver_id == user_id)) |
                    ((Message.sender_id == user_id) & (Message.receiver_id == session['user_id'])),
                    Message.is_deleted == False,
                    Message.content != '[Сообщение удалено]'
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

# Регистрация FCM токена для push-уведомлений
@app.route('/fcm/register', methods=['POST'])
def fcm_register():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    token = request.json.get('token', '').strip()
    if not token:
        return jsonify({'error': 'Токен не указан'}), 400
    uid = session['user_id']
    existing = FCMToken.query.filter_by(token=token).first()
    if not existing:
        db.session.add(FCMToken(user_id=uid, token=token))
        db.session.commit()
    elif existing.user_id != uid:
        existing.user_id = uid
        db.session.commit()
    return jsonify({'success': True})


# Профиль пользователя
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get( session['user_id'])
    verification_request = VerificationRequest.query.filter_by(user_id=user.id).order_by(VerificationRequest.created_at.desc()).first()
    spark_balance = _get_spark_balance(user.id).balance
    displayed_gifts = UserGift.query.filter_by(owner_id=user.id, is_displayed=True).all()
    return render_template('profile.html', user=user, verification_request=verification_request,
                           spark_balance=spark_balance, displayed_gifts=displayed_gifts)

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
        'reputation': user.reputation if user.reputation is not None else 100,
        'premium_emoji': user.premium_emoji,
        'status_text': user.status_text,
        'last_seen': (user.last_seen.isoformat() if user.last_seen else None)
                     if _privacy_allows(user, user_id, 'privacy_show_last_seen') else None,
        'is_online': user.id in online_users,
        'created_at': user.created_at.strftime('%d.%m.%Y'),
        'msg_price': getattr(user, 'msg_price', None) or 0
    })

@app.route('/api/user-by-username/<username>')
def get_user_by_username(username):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    return jsonify({'id': user.id, 'username': user.username, 'display_name': user.display_name or user.username})

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

    if 'email' in data:
        user.email = data['email'].strip() or None
    
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


def _privacy_allows(target_user, viewer_id, setting):
    """Проверяет, разрешает ли настройка приватности target_user доступ viewer_id."""
    val = getattr(target_user, setting, 'everyone')
    if not val or val == 'everyone':
        return True
    if val == 'nobody':
        return False
    if val == 'premium':
        viewer = User.query.get(viewer_id)
        return bool(viewer and (viewer.is_premium or viewer.is_admin or viewer.admin_role))
    if val == 'contacts':
        # Взаимные контакты
        i_added = Contact.query.filter_by(user_id=target_user.id, contact_id=viewer_id).first() is not None
        they_added = Contact.query.filter_by(user_id=viewer_id, contact_id=target_user.id).first() is not None
        return i_added and they_added
    return True


@app.route('/profile/privacy', methods=['GET', 'POST'])
def profile_privacy():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if request.method == 'GET':
        return jsonify({
            'privacy_who_can_message':       user.privacy_who_can_message or 'everyone',
            'privacy_who_can_call':          user.privacy_who_can_call or 'everyone',
            'privacy_who_can_add_to_groups': user.privacy_who_can_add_to_groups or 'everyone',
            'privacy_show_last_seen':        user.privacy_show_last_seen or 'everyone',
            'privacy_show_phone':            user.privacy_show_phone or 'nobody',
            'privacy_show_profile':          user.privacy_show_profile or 'everyone',
        })
    data = request.get_json() or {}
    allowed_vals = {'everyone', 'contacts', 'nobody'}
    allowed_msg_vals = allowed_vals | {'premium'}
    fields = {
        'privacy_who_can_message':       allowed_msg_vals,
        'privacy_who_can_call':          allowed_vals,
        'privacy_who_can_add_to_groups': allowed_vals,
        'privacy_show_last_seen':        allowed_vals,
        'privacy_show_phone':            allowed_vals,
        'privacy_show_profile':          allowed_vals,
    }
    for field, valid in fields.items():
        if field in data and data[field] in valid:
            setattr(user, field, data[field])
    db.session.commit()
    return jsonify({'success': True})
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
        'content': message.decrypted_content,
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
            'content': message.decrypted_content,
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
    db.session.commit()
    
    # Отправляем событие через Socket.IO обоим пользователям
    delete_data = {
        'message_id': message.id,
        'other_user_id': message.receiver_id  # для отправителя
    }
    delete_data_receiver = {
        'message_id': message.id,
        'other_user_id': message.sender_id  # для получателя
    }

    try:
        # Отправителю
        socketio.emit('message_deleted', delete_data, room=f'user_{message.sender_id}', namespace='/')
        # Получателю
        socketio.emit('message_deleted', delete_data_receiver, room=f'user_{message.receiver_id}', namespace='/')
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

# Управление режимом обслуживания
@app.route('/admin/maintenance', methods=['GET', 'POST'])
def admin_maintenance():
    global MAINTENANCE_MODE
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if not user or user.admin_role != 'owner':
        return jsonify({'error': 'Только owner'}), 403
    if request.method == 'GET':
        return jsonify({'maintenance': MAINTENANCE_MODE})
    data = request.get_json()
    MAINTENANCE_MODE = bool(data.get('maintenance', False))
    return jsonify({'success': True, 'maintenance': MAINTENANCE_MODE})

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

# Заявка на удаление аккаунта
@app.route('/profile/request-deletion', methods=['POST'])
def request_account_deletion():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    data = request.json or {}
    reason = data.get('reason', '').strip()
    if not reason:
        return jsonify({'error': 'Укажите причину'}), 400
    # Проверяем нет ли уже активной заявки
    existing = SupportTicket.query.filter_by(user_id=user.id, status='open').filter(
        SupportTicket.message_text.like('[УДАЛЕНИЕ]%')
    ).first()
    if existing:
        return jsonify({'error': 'Заявка уже подана и ожидает рассмотрения'}), 400
    ticket = SupportTicket(user_id=user.id, message_text=f'[УДАЛЕНИЕ] {reason}')
    db.session.add(ticket)
    db.session.commit()
    return jsonify({'success': True})

# Список заявок на удаление (для owner)
@app.route('/admin/deletion-requests', methods=['GET'])
def admin_get_deletion_requests():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещён'}), 403
    tickets = SupportTicket.query.filter(
        SupportTicket.message_text.like('[УДАЛЕНИЕ]%')
    ).order_by(SupportTicket.created_at.desc()).all()
    return jsonify({'requests': [{
        'id': t.id,
        'user_id': t.user_id,
        'username': t.user.username,
        'display_name': t.user.display_name or t.user.username,
        'reason': t.message_text.replace('[УДАЛЕНИЕ] ', '', 1),
        'status': t.status,
        'created_at': t.created_at.strftime('%d.%m.%Y %H:%M')
    } for t in tickets]})

@app.route('/admin/deletion-requests/<int:ticket_id>/approve', methods=['POST'])
def admin_approve_deletion(ticket_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещён'}), 403
    ticket = SupportTicket.query.get_or_404(ticket_id)
    target_user = User.query.get(ticket.user_id)
    if target_user:
        db.session.delete(target_user)
    ticket.status = 'closed'
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/deletion-requests/<int:ticket_id>/reject', methods=['POST'])
def admin_reject_deletion(ticket_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Доступ запрещён'}), 403
    ticket = SupportTicket.query.get_or_404(ticket_id)
    ticket.status = 'closed'
    db.session.commit()
    return jsonify({'success': True})

# ── Предупреждения администраторам (только owner) ─────────────────────────────

@app.route('/admin/users/<int:user_id>/warn', methods=['POST'])
def admin_warn_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    issuer = User.query.get(session['user_id'])
    if not issuer or not _has_role(issuer, 'owner'):
        return jsonify({'error': 'Только owner'}), 403
    target = User.query.get_or_404(user_id)
    data = request.get_json() or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'Укажите причину'}), 400

    warning = AdminWarning(user_id=user_id, reason=reason, issued_by=issuer.id)
    db.session.add(warning)
    db.session.flush()

    total = AdminWarning.query.filter_by(user_id=user_id).count()
    demoted = total >= 3
    if demoted:
        target.admin_role = None
        target.is_admin = False
        target.admin_apply_blocked_until = datetime.utcnow() + timedelta(days=1)

    db.session.commit()
    return jsonify({'success': True, 'total_warnings': total, 'demoted': demoted})

@app.route('/admin/users/<int:user_id>/warnings', methods=['GET'])
def get_user_warnings(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    issuer = User.query.get(session['user_id'])
    if not issuer or not _has_role(issuer, 'owner'):
        return jsonify({'error': 'Только owner'}), 403
    warnings = AdminWarning.query.filter_by(user_id=user_id).order_by(AdminWarning.created_at.desc()).all()
    return jsonify({'warnings': [{
        'id': w.id,
        'reason': w.reason,
        'created_at': w.created_at.strftime('%d.%m.%Y %H:%M'),
        'is_read': w.is_read
    } for w in warnings]})

@app.route('/admin/warning/<int:warning_id>/cancel', methods=['POST'])
def cancel_admin_warning(warning_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    issuer = User.query.get(session['user_id'])
    if not issuer or not _has_role(issuer, 'owner'):
        return jsonify({'error': 'Только owner'}), 403
    warning = AdminWarning.query.get_or_404(warning_id)
    target_id = warning.user_id
    # Нельзя отменять если уже 3 предупреждения (снятие с поста необратимо)
    total = AdminWarning.query.filter_by(user_id=target_id).count()
    if total >= 3:
        return jsonify({'error': 'Нельзя отменить: достигнут лимит 3 предупреждения'}), 403
    db.session.delete(warning)
    db.session.flush()
    remaining = AdminWarning.query.filter_by(user_id=target_id).count()
    if remaining < 3:
        target = User.query.get(target_id)
        if target:
            target.admin_apply_blocked_until = None
    db.session.commit()
    return jsonify({'success': True, 'remaining': remaining})

@app.route('/admin/check-warning', methods=['GET'])
def check_admin_warning():
    if 'user_id' not in session:
        return jsonify({'warning': None})
    warning = AdminWarning.query.filter_by(user_id=session['user_id'], is_read=False)\
        .order_by(AdminWarning.created_at.asc()).first()
    if not warning:
        return jsonify({'warning': None})
    total = AdminWarning.query.filter_by(user_id=session['user_id']).count()
    return jsonify({
        'warning': {
            'id': warning.id,
            'reason': warning.reason,
            'created_at': warning.created_at.strftime('%d.%m.%Y %H:%M'),
            'total': total,
            'demoted': total >= 3
        }
    })

@app.route('/admin/warning/<int:warning_id>/read', methods=['POST'])
def mark_warning_read(warning_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    warning = AdminWarning.query.filter_by(id=warning_id, user_id=session['user_id']).first_or_404()
    warning.is_read = True
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

    from collections import Counter
    import datetime as _dt

    # Считаем жалобы по target_id
    counts = Counter(
        r['target_id'] for r in reports
        if r.get('target_type') == 'user' and r.get('target_id')
    )

    seen_ids = set()
    candidates = []

    # 1. Уже заблокированные пользователи (всегда показываем)
    blocked_users = User.query.filter_by(is_spam_blocked=True).all()
    for user in blocked_users:
        seen_ids.add(user.id)
        candidates.append({
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name or user.username,
            'report_count': counts.get(user.id, 0),
            'is_spam_blocked': True,
            'spam_block_until': (user.spam_block_until + _dt.timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if user.spam_block_until else None
        })

    # 2. Кандидаты с 3+ жалобами (ещё не заблокированные)
    for target_id, count in counts.items():
        if count >= 3:
            uid = int(target_id)
            if uid in seen_ids:
                continue
            user = User.query.get(uid)
            if user:
                seen_ids.add(uid)
                candidates.append({
                    'id': user.id,
                    'username': user.username,
                    'display_name': user.display_name or user.username,
                    'report_count': count,
                    'is_spam_blocked': user.is_spam_blocked,
                    'spam_block_until': (user.spam_block_until + _dt.timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if user.spam_block_until else None
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
    block_hours = 12 if user.is_premium else 24
    user.spam_block_until = datetime.utcnow() + timedelta(hours=block_hours)
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
    
    try:
        _delete_user_cascade(user_id)
        db.session.delete(user)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"DELETE USER {user_id} ERROR: {e}\n{traceback.format_exc()}")
        # Попробуем удалить напрямую через SQL
        try:
            _sql_exec('DELETE FROM "user" WHERE id = :uid', {"uid": user_id})
            return jsonify({'success': True})
        except Exception as e2:
            print(f"DELETE USER {user_id} SQL ERROR: {e2}")
            return jsonify({'error': f'Ошибка удаления: {str(e)}'}), 500
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

# Owner: редактирование профиля любого пользователя
@app.route('/admin/users/<int:user_id>/edit-profile', methods=['GET', 'POST'])
def admin_edit_user_profile(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or admin.admin_role != 'owner':
        return jsonify({'error': 'Только owner'}), 403
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if request.method == 'GET':
        return jsonify({
            'id': user.id,
            'username': user.username,
            'display_name': user.display_name or '',
            'bio': user.bio or '',
            'is_verified': user.is_verified,
            'is_premium': user.is_premium,
            'avatar_color': user.avatar_color,
        })
    data = request.get_json()
    if 'display_name' in data:
        user.display_name = data['display_name'][:100]
    if 'bio' in data:
        user.bio = data['bio'][:200]
    if 'is_verified' in data:
        user.is_verified = bool(data['is_verified'])
    if 'is_premium' in data:
        user.is_premium = bool(data['is_premium'])
        if user.is_premium and not user.premium_until:
            user.premium_until = datetime.utcnow() + timedelta(days=30)
        elif not user.is_premium:
            user.premium_until = None
    if 'avatar_color' in data:
        user.avatar_color = data['avatar_color']
    db.session.commit()
    return jsonify({'success': True})

# Owner: верификация каналов/групп
@app.route('/admin/groups', methods=['GET'])
def admin_get_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or admin.admin_role != 'owner':
        return jsonify({'error': 'Только owner'}), 403
    groups = Group.query.order_by(Group.created_at.desc()).all()
    return jsonify({'groups': [{
        'id': g.id,
        'name': g.name,
        'username': g.username or '',
        'is_channel': g.is_channel,
        'is_verified': g.is_verified,
        'creator': g.creator.username if g.creator else '?',
        'created_at': g.created_at.strftime('%d.%m.%Y'),
    } for g in groups]})

@app.route('/admin/groups/<int:group_id>/verify', methods=['POST'])
def admin_verify_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or admin.admin_role != 'owner':
        return jsonify({'error': 'Только owner'}), 403
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Не найдено'}), 404
    group.is_verified = not group.is_verified
    db.session.commit()
    return jsonify({'success': True, 'is_verified': group.is_verified})

@app.route('/admin/groups/<int:group_id>/delete', methods=['POST'])
def admin_delete_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or admin.admin_role != 'owner':
        return jsonify({'error': 'Только owner'}), 403
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Не найдено'}), 404
    _delete_group_cascade(group_id)
    db.session.delete(group)
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
    if user.is_premium:
        # При ручном включении через админку — даём 30 дней по умолчанию
        user.premium_until = datetime.utcnow() + timedelta(days=30)
    else:
        user.premium_until = None
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

    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'error': 'Не авторизован'}), 401

    # Лимит: обычные пользователи — 1 история в день
    if not user.is_premium:
        from datetime import timedelta
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = Story.query.filter(
            Story.user_id == user.id,
            Story.created_at >= today_start
        ).count()
        if today_count >= 1:
            return jsonify({'error': 'Обычные пользователи могут публиковать только 1 историю в день. Оформите Premium для безлимитных историй!'}), 429

    data = request.get_json()
    content = encrypt_msg(data.get('content', '').strip())
    media_url = data.get('media_url', '').strip()
    media_type = data.get('media_type', 'text')

    if not content and not media_url:
        return jsonify({'error': 'Пустая история'}), 400
    if len(content) > 500:
        return jsonify({'error': 'Слишком длинный текст (макс. 500 символов)'}), 400

    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(hours=24)

    story = Story(
        user_id=user.id,
        content=content or media_url,
        media_type=media_type,
        expires_at=expires_at
    )
    db.session.add(story)
    db.session.commit()

    return jsonify({
        'success': True,
        'story': {
            'id': story.id,
            'content': story.content,
            'media_type': story.media_type,
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
            if len(username) < 4 or len(username) > 50:
                return jsonify({'error': 'Username должен содержать от 4 до 50 символов'}), 400
            
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
            
            # Получаем последнее НЕудалённое сообщение
            last_message = GroupMessage.query.filter_by(group_id=group.id, is_deleted=False).order_by(GroupMessage.timestamp.desc()).first()
            
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
                'is_verified': group.is_verified,
                'avatar_color': group.avatar_color,
                'avatar_url': group.avatar_url,
                'avatar_letter': group.name[0].upper(),
                'members_count': members_count,
                'is_admin': membership.is_admin,
                'last_message': last_message.content[:50] if last_message else None,
                'last_message_time': sort_timestamp.isoformat() + 'Z',
                'last_message_display': sort_timestamp.strftime('%H:%M') if last_message else None,
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
    messages = GroupMessage.query.filter_by(group_id=group_id, is_deleted=False).order_by(GroupMessage.timestamp.asc()).limit(100).all()
    
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
        
        # Данные платного поста
        paid_post = PaidPost.query.filter_by(group_message_id=msg.id).first() if getattr(msg, 'is_paid', False) else None
        is_purchased = False
        if paid_post:
            if group.creator_id == session['user_id'] or msg.sender_id == session['user_id']:
                is_purchased = True
            else:
                is_purchased = bool(PaidPostPurchase.query.filter_by(user_id=session['user_id'], paid_post_id=paid_post.id).first())

        messages_data.append({
            'id': msg.id,
            'sender_id': msg.sender_id,
            'sender_name': sender.display_name or sender.username,
            'sender_avatar_color': sender.avatar_color,
            'sender_avatar_url': sender.avatar_url,
            'sender_avatar_letter': sender.get_avatar_letter(),
            'content': msg.decrypted_content if (not getattr(msg, 'is_paid', False) or is_purchased) else '🔒 Платный контент',
            'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': msg.timestamp.isoformat() + 'Z',
            'edited_at': msg.edited_at.strftime('%H:%M %d.%m') if msg.edited_at else None,
            'is_deleted': msg.is_deleted,
            'is_mine': msg.sender_id == session['user_id'],
            'media_files': media_data if (not getattr(msg, 'is_paid', False) or is_purchased) else [],
            'is_paid': getattr(msg, 'is_paid', False),
            'paid_price': getattr(msg, 'paid_price', 0),
            'paid_post_id': paid_post.id if paid_post else None,
            'is_purchased': is_purchased,
            'reply_to': {
                'id': msg.reply_to.id,
                'content': msg.reply_to.content if not msg.reply_to.is_deleted else '[удалено]',
                'sender_name': (msg.reply_to.sender.display_name or msg.reply_to.sender.username) if msg.reply_to.sender else '?'
            } if msg.reply_to_id and msg.reply_to else None,
            'sticker_pack_id': _get_sticker_pack_id(msg.content) if (msg.content and msg.content.startswith('[sticker]')) else None,
            'message_type': msg.message_type if msg.message_type else ('sticker' if (msg.content and msg.content.startswith('[sticker]')) else 'text'),
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
    content = encrypt_msg(data.get('content', '').strip())
    
    if not content or len(content) > 4096:
        return jsonify({'error': 'Некорректное сообщение'}), 400

    # Slow mode проверка (не для админов)
    if not membership.is_admin and group.slow_mode_seconds and group.slow_mode_seconds > 0:
        tracker = SlowModeTracker.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
        if tracker:
            elapsed = (datetime.utcnow() - tracker.last_message_at).total_seconds()
            if elapsed < group.slow_mode_seconds:
                wait = int(group.slow_mode_seconds - elapsed)
                return jsonify({'error': f'Медленный режим: подождите ещё {wait} сек.'}), 429

    # Антиспам по ключевым словам (не для админов)
    if not membership.is_admin and group.spam_keywords:
        try:
            keywords = json.loads(group.spam_keywords)
            content_lower = content.lower()
            for kw in keywords:
                if kw.strip().lower() in content_lower:
                    return jsonify({'error': 'Сообщение содержит запрещённые слова'}), 400
        except Exception:
            pass

    # Парсим @упоминания и уведомляем
    import re as _re
    mentioned_usernames = _re.findall(r'@(\w+)', content)
    mentioned_user_ids = []
    for uname in set(mentioned_usernames):
        mu = User.query.filter_by(username=uname).first()
        if mu and mu.id != session['user_id']:
            mentioned_user_ids.append(mu.id)

    # Создаем сообщение
    message = GroupMessage(
        group_id=group_id,
        sender_id=session['user_id'],
        content=content,
        reply_to_id=data.get('reply_to_id') or None
    )
    db.session.add(message)
    db.session.commit()

    # Обновляем slow mode tracker
    if not membership.is_admin and group.slow_mode_seconds and group.slow_mode_seconds > 0:
        tracker = SlowModeTracker.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
        if tracker:
            tracker.last_message_at = datetime.utcnow()
        else:
            db.session.add(SlowModeTracker(group_id=group_id, user_id=session['user_id'], last_message_at=datetime.utcnow()))
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
        'message_type': message.message_type or 'text',
        'media_files': []
    }
    
    # Отправляем через Socket.IO всем участникам группы (включая отправителя)
    socketio.emit('new_group_message', {
        'group_id': group_id,
        'message': message_data
    }, room=f'group_{group_id}', include_self=True)
    
    # Уведомляем упомянутых пользователей
    if mentioned_user_ids:
        group = Group.query.get(group_id)
        group_name = group.name if group else 'группе'
        for mid in mentioned_user_ids:
            socketio.emit('mention_notification', {
                'group_id': group_id,
                'group_name': group_name,
                'sender_name': sender.display_name or sender.username,
                'message_id': message.id,
                'content': content[:100]
            }, room=f'user_{mid}')

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
    db.session.commit()
    socketio.emit('group_message_deleted', {'message_id': message_id, 'group_id': group_id}, room=f'group_{group_id}', namespace='/')
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
        return jsonify({'success': True, 'already_member': True})
    
    # Добавляем участника
    member = GroupMember(
        group_id=group_id,
        user_id=session['user_id'],
        is_admin=False
    )
    db.session.add(member)
    db.session.commit()

    # Отправляем welcome message если настроено
    if group.welcome_message:
        joiner = User.query.get(session['user_id'])
        wm_text = group.welcome_message.replace('{name}', joiner.display_name or joiner.username)
        wm = GroupMessage(group_id=group_id, sender_id=session['user_id'], content=wm_text)
        db.session.add(wm)
        db.session.commit()
        socketio.emit('new_group_message', {
            'group_id': group_id,
            'message': {
                'id': wm.id, 'sender_id': joiner.id,
                'sender_name': joiner.display_name or joiner.username,
                'sender_avatar_color': joiner.avatar_color,
                'sender_avatar_url': joiner.avatar_url,
                'sender_avatar_letter': joiner.get_avatar_letter(),
                'content': wm_text,
                'timestamp': wm.timestamp.strftime('%H:%M %d.%m'),
                'timestamp_iso': wm.timestamp.isoformat() + 'Z',
                'media_files': []
            }
        }, room=f'group_{group_id}', include_self=True)
    
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
    _delete_group_cascade(group_id)
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

# Повысить/понизить участника + управление правами
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
    if 'admin_permissions' in data:
        target.admin_permissions = json.dumps(data['admin_permissions'])
    db.session.commit()
    return jsonify({'success': True, 'is_admin': target.is_admin})

# Управление ограничениями участника
@app.route('/groups/<int:group_id>/members/<int:user_id>/restrictions', methods=['POST'])
def set_member_restrictions(group_id, user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    me = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not me or not me.is_admin:
        return jsonify({'error': 'Только админы'}), 403
    if group.creator_id == user_id:
        return jsonify({'error': 'Нельзя ограничить создателя'}), 400
    target = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not target:
        return jsonify({'error': 'Участник не найден'}), 404
    data = request.get_json() or {}
    target.member_restrictions = json.dumps(data.get('restrictions', {}))
    db.session.commit()
    return jsonify({'success': True})

# Передача канала/группы другому пользователю (требует 2FA)
@app.route('/groups/<int:group_id>/transfer', methods=['POST'])
def transfer_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Группа не найдена'}), 404
    if group.creator_id != session['user_id']:
        return jsonify({'error': 'Только создатель может передать группу'}), 403
    data = request.get_json() or {}
    new_owner_username = data.get('username', '').strip().lstrip('@')
    tfa_code = data.get('tfa_code', '').strip()
    me = User.query.get(session['user_id'])
    # Проверяем 2FA если включена
    if me.two_fa_enabled:
        if not tfa_code:
            return jsonify({'error': 'Требуется код 2FA', 'need_2fa': True}), 400
        if me.two_fa_code != tfa_code or (me.two_fa_code_expires and me.two_fa_code_expires < datetime.utcnow()):
            return jsonify({'error': 'Неверный или истёкший код 2FA'}), 400
    new_owner = User.query.filter_by(username=new_owner_username).first()
    if not new_owner:
        return jsonify({'error': f'Пользователь @{new_owner_username} не найден'}), 404
    if new_owner.id == session['user_id']:
        return jsonify({'error': 'Нельзя передать самому себе'}), 400
    # Убеждаемся что новый владелец — участник
    new_member = GroupMember.query.filter_by(group_id=group_id, user_id=new_owner.id).first()
    if not new_member:
        new_member = GroupMember(group_id=group_id, user_id=new_owner.id, is_admin=True)
        db.session.add(new_member)
    else:
        new_member.is_admin = True
    group.creator_id = new_owner.id
    db.session.commit()
    return jsonify({'success': True})

# Получить права участника
@app.route('/groups/<int:group_id>/members/<int:user_id>/info', methods=['GET'])
def get_member_info(group_id, user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    target = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not target:
        return jsonify({'error': 'Не найден'}), 404
    return jsonify({
        'is_admin': target.is_admin,
        'admin_permissions': json.loads(target.admin_permissions or '{}'),
        'member_restrictions': json.loads(target.member_restrictions or '{}'),
    })

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
@app.route('/invite/<token>', methods=['GET', 'POST'])
def join_by_invite(token):
    if 'user_id' not in session:
        return redirect(url_for('login') + '?next=/invite/' + token)
    group = Group.query.filter_by(invite_link=token).first()
    if not group:
        return render_template('join.html', group=None, error='Ссылка недействительна'), 404
    member_count = GroupMember.query.filter_by(group_id=group.id).count()
    existing = GroupMember.query.filter_by(group_id=group.id, user_id=session['user_id']).first()
    if request.method == 'POST':
        if not existing:
            member = GroupMember(group_id=group.id, user_id=session['user_id'], is_admin=False)
            db.session.add(member)
            db.session.commit()
        return redirect(url_for('index') + '?open_group=' + str(group.id))
    return render_template('join.html', group=group, member_count=member_count, already_member=bool(existing))

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

    # Проверяем настройки приватности (кто может добавлять в группы)
    adder = User.query.get(session['user_id'])
    if adder and not (adder.is_admin or adder.admin_role):
        if not _privacy_allows(user, adder.id, 'privacy_who_can_add_to_groups'):
            return jsonify({'error': 'privacy', 'message': 'Этот пользователь ограничил добавление в группы'}), 403

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

def _get_sticker_pack_id(content):
    """Извлекает pack_id из URL стикера."""
    if not content:
        return None
    url = content.replace('[sticker]', '')
    # Если data URL — ищем стикер в БД по image_url
    if url.startswith('data:'):
        s = Sticker.query.filter_by(image_url=url).first()
        return s.pack_id if s else None
    # URL вида /static/media/stickers/{pack_id}/...
    try:
        parts = url.split('/')
        idx = parts.index('stickers')
        return int(parts[idx + 1])
    except Exception:
        return None


def _get_gift_data_for_msg(msg):
    """Возвращает данные подарка для gift-сообщения."""
    try:
        content = msg.content or ''
        # Формат: __GIFT__{gift_type_id}
        if '__GIFT__' in content:
            gift_type_id = int(content.replace('__GIFT__', ''))
            gt = GiftType.query.get(gift_type_id)
            if gt:
                return {
                    'id': gt.id, 'name': gt.name, 'emoji': gt.emoji,
                    'price': gt.price_sparks, 'rarity': gt.rarity,
                    'description': gt.description,
                }
    except Exception:
        pass
    return None

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
        'content': message.decrypted_content,
        'message_type': 'text',
        'media_url': None,
        'media_files': [],
        'duration': None,
        'timestamp': message.timestamp.strftime('%H:%M %d.%m'),
        'timestamp_iso': message.timestamp.isoformat() + 'Z',
        'edited_at': None,
        'is_deleted': False,
        'is_read': False,
        'reply_to': None,
        'bot_buttons': buttons or [],
        'sticker_pack_id': None,
        'gift': None,
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

# Словарь состояний бота стикеров
# user_id -> {'state': str, 'pack_name': str, 'pack_id': int}
_sticker_states = {}

def _handle_stickers_bot(bot_user_id, sender_id, text):
    """Обработчик диалога бота @stickers."""
    # Загружаем состояние из БД (персистентное, не теряется при рестарте)
    db_state = StickerBotState.query.get(sender_id)
    state = {}
    if db_state:
        state = {'state': db_state.state, 'pack_id': db_state.pack_id,
                 'pack_name': db_state.pack_name, 'count': db_state.count}

    def _save_state(s):
        row = StickerBotState.query.get(sender_id)
        if row is None:
            row = StickerBotState(user_id=sender_id)
            db.session.add(row)
        row.state = s.get('state', '')
        row.pack_id = s.get('pack_id')
        row.pack_name = s.get('pack_name')
        row.count = s.get('count', 0)
        db.session.commit()

    def _clear_state():
        row = StickerBotState.query.get(sender_id)
        if row:
            db.session.delete(row)
            db.session.commit()

    cmd = text.strip().lower()

    def reply(msg, buttons=None):
        _bot_send_message(bot_user_id, sender_id, msg, buttons)

    # /start или /menu — главное меню
    if cmd in ('/start', '/menu', ''):
        _clear_state()
        btns = [
            {"label": "📦 Создать пак", "reply": "/new"},
            {"label": "🗂 Мои паки", "reply": "/my"},
        ]
        reply("🎨 Привет! Я помогу создать и управлять паками стикеров.\n\nВыбери действие:", btns)
        return

    # /new — начать создание пака
    if cmd == '/new':
        _save_state({'state': 'waiting_name'})
        reply("📝 Введи название для нового пака стикеров:")
        return

    # /my — список паков
    if cmd == '/my':
        _clear_state()
        owned = StickerPack.query.filter_by(creator_id=sender_id).all()
        added_ids = [r.pack_id for r in UserStickerPack.query.filter_by(user_id=sender_id).all()]
        added = StickerPack.query.filter(StickerPack.id.in_(added_ids), StickerPack.creator_id != sender_id).all()
        all_packs = owned + added
        if not all_packs:
            reply("У тебя пока нет паков стикеров.\n\nНапиши /new чтобы создать первый!")
        else:
            lines = ["🗂 *Твои паки стикеров:*\n"]
            for p in all_packs:
                cnt = len(p.stickers)
                owner_mark = " ✏️" if p.creator_id == sender_id else ""
                lines.append(f"• {p.name} — {cnt} стик.{owner_mark}")
            lines.append("\n/new — создать новый пак")
            reply("\n".join(lines))
        return

    # /cancel — отмена текущего действия
    if cmd == '/cancel':
        _clear_state()
        reply("❌ Отменено. Напиши /menu чтобы вернуться в меню.")
        return

    # Состояние: ожидаем название пака
    if state.get('state') == 'waiting_name':
        name = text.strip()
        if len(name) < 2:
            reply("Название слишком короткое. Попробуй ещё раз:")
            return
        if len(name) > 100:
            reply("Название слишком длинное (макс. 100 символов). Попробуй ещё раз:")
            return
        # Создаём пак
        pack = StickerPack(name=name, creator_id=sender_id)
        db.session.add(pack)
        db.session.flush()
        usp = UserStickerPack(user_id=sender_id, pack_id=pack.id)
        db.session.add(usp)
        db.session.commit()
        _save_state({'state': 'waiting_stickers', 'pack_id': pack.id, 'pack_name': name, 'count': 0})
        reply(
            f"✅ Пак «{name}» создан!\n\n"
            "Теперь отправляй стикеры (PNG, GIF, WebP, JPG) — по одному или несколько.\n"
            "Когда закончишь — напиши /done\n"
            "Отмена — /cancel"
        )
        return

    # Состояние: ожидаем стикеры
    if state.get('state') == 'waiting_stickers':
        pack_id = state['pack_id']
        pack_name = state['pack_name']

        # Получили URL изображения — добавляем как стикер
        if text.startswith('/static/media/'):
            ext = text.rsplit('.', 1)[-1].lower() if '.' in text else ''
            if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
                count = state.get('count', 0)
                # Конвертируем в base64 — файлы на Render ephemeral, base64 хранится в БД
                try:
                    import base64 as _b64
                    fpath = os.path.join(os.getcwd(), text.lstrip('/').replace('/', os.sep))
                    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                            'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'image/png')
                    with open(fpath, 'rb') as fh:
                        image_url = f"data:{mime};base64,{_b64.b64encode(fh.read()).decode()}"
                except Exception:
                    image_url = text  # fallback если файл недоступен
                sticker = Sticker(pack_id=pack_id, image_url=image_url, order_index=count)
                db.session.add(sticker)
                if count == 0:
                    pack = StickerPack.query.get(pack_id)
                    if pack:
                        pack.cover_url = image_url
                db.session.commit()
                new_count = count + 1
                _save_state({'state': 'waiting_stickers', 'pack_id': pack_id, 'pack_name': pack_name, 'count': new_count})
                reply(f"✅ Стикер {new_count} добавлен! Отправляй ещё или напиши /done")
            else:
                reply("⚠️ Поддерживаются только PNG, GIF, WebP, JPG.")
            return

        if cmd == '/done':
            count = state.get('count', 0)
            _clear_state()
            if count == 0:
                reply(f"⚠️ В паке «{pack_name}» нет стикеров. Отправь хотя бы один файл или /cancel.")
            else:
                btns = [{"label": "🗂 Мои паки", "reply": "/my"}, {"label": "📦 Создать ещё", "reply": "/new"}]
                reply(f"🎉 Пак «{pack_name}» готов! Добавлено стикеров: {count}\n\nТеперь ты можешь использовать их в чатах.", btns)
            return

        reply("Отправляй файлы-стикеры (PNG/GIF/WebP/JPG), или напиши /done чтобы завершить, /cancel чтобы отменить.")
        return
    btns = [{"label": "📦 Создать пак", "reply": "/new"}, {"label": "🗂 Мои паки", "reply": "/my"}]
    reply("Не понял 🤔 Выбери действие:", btns)


def _sql_exec(sql, params=None):
    """Выполняет SQL запрос игнорируя ошибки (для каскадного удаления)."""
    from sqlalchemy import text as _t
    try:
        with db.engine.connect() as c:
            c.execute(_t(sql), params or {})
            c.commit()
    except Exception as e:
        print(f"_sql_exec skip [{sql[:60]}]: {e}")


def _send_fcm(user_id, title, body, notif_type='message'):
    """Отправляет FCM push через HTTP v1 API без firebase-admin."""
    if user_id in online_users:
        return
    try:
        import json as _json
        import requests as _req
        sa_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON', '')
        if not sa_json:
            return
        tokens = [t.token for t in FCMToken.query.filter_by(user_id=user_id).all()]
        if not tokens:
            return
        # Получаем OAuth2 токен через service account
        sa = _json.loads(sa_json)
        project_id = sa.get('project_id', '')
        # Используем google-auth если доступен, иначе пропускаем
        try:
            import google.auth
            import google.auth.transport.requests
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_info(
                sa, scopes=['https://www.googleapis.com/auth/firebase.messaging'])
            creds.refresh(google.auth.transport.requests.Request())
            access_token = creds.token
        except Exception:
            return
        url = f'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send'
        headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
        for token in tokens:
            try:
                payload = {'message': {
                    'token': token,
                    'notification': {'title': title, 'body': body},
                    'data': {'type': notif_type, 'title': title, 'body': body},
                    'android': {'priority': 'high'}
                }}
                r = _req.post(url, headers=headers, json=payload, timeout=5)
                if r.status_code == 404 or (r.status_code == 400 and 'INVALID_ARGUMENT' in r.text):
                    FCMToken.query.filter_by(token=token).delete()
                    db.session.commit()
            except Exception as e:
                print(f"FCM send error: {e}")
    except Exception as e:
        print(f"FCM error: {e}")




@app.route('/story/<int:story_id>/view', methods=['POST'])
def view_story(story_id):
    """Отмечает просмотр истории."""
    story = Story.query.get(story_id)
    if story:
        story.views_count = (story.views_count or 0) + 1
        db.session.commit()
    return jsonify({'success': True})


@app.route('/story/upload', methods=['POST'])
def upload_story_media():
    """Загружает медиа для истории."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не найден'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Пустой файл'}), 400
    import uuid, os as _os
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
    allowed = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4', 'mov'}
    if ext not in allowed:
        return jsonify({'error': 'Недопустимый формат'}), 400
    fname = f"story_{uuid.uuid4().hex[:12]}.{ext}"
    media_type = 'video' if ext in {'mp4', 'mov'} else 'image'
    save_dir = _os.path.join('static', 'media', 'images')
    _os.makedirs(save_dir, exist_ok=True)
    file.save(_os.path.join(save_dir, fname))
    return jsonify({'success': True, 'media_url': f'/static/media/images/{fname}', 'media_type': media_type})


@app.route('/u/<username>')
def public_profile(username):
    """Публичная страница профиля пользователя."""
    user = User.query.filter_by(username=username).first_or_404()
    if user.is_bot:
        return redirect(url_for('index'))
    viewer = None
    if 'user_id' in session:
        viewer = User.query.get(session['user_id'])
    # Проверяем приватность профиля
    if viewer and viewer.id != user.id and not (viewer.is_admin or viewer.admin_role):
        if not _privacy_allows(user, viewer.id, 'privacy_show_profile'):
            return render_template('public_profile.html',
                user=user, stories=[], rep=100, rep_level='—', rep_color='#718096',
                viewer=viewer, profile_hidden=True)
    # Активные истории
    stories = Story.query.filter(
        Story.user_id == user.id,
        Story.expires_at > datetime.utcnow()
    ).order_by(Story.created_at.desc()).all()
    # Репутация
    rep = user.reputation if user.reputation is not None else 100
    if rep >= 80: rep_level, rep_color = 'Отличная', '#38a169'
    elif rep >= 60: rep_level, rep_color = 'Хорошая', '#667eea'
    elif rep >= 40: rep_level, rep_color = 'Средняя', '#d69e2e'
    elif rep >= 20: rep_level, rep_color = 'Плохая', '#e53e3e'
    else: rep_level, rep_color = 'Критическая', '#742a2a'
    return render_template('public_profile.html',
        user=user, stories=stories,
        rep=rep, rep_level=rep_level, rep_color=rep_color,
        viewer=viewer, profile_hidden=False)

def _delete_group_cascade(group_id):
    """Удаляет все зависимые данные группы перед удалением самой группы."""
    gid = {"gid": group_id}
    for sql in [
        # spark/channel
        "DELETE FROM channel_spark_withdraw WHERE group_id = :gid",
        "DELETE FROM spark_reaction WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # paid posts
        "DELETE FROM paid_post_purchase WHERE paid_post_id IN (SELECT pp.id FROM paid_post pp JOIN group_message gm ON pp.group_message_id = gm.id WHERE gm.group_id = :gid)",
        "DELETE FROM paid_post WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # polls
        "DELETE FROM poll_vote WHERE poll_id IN (SELECT id FROM poll WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid))",
        "DELETE FROM poll_option WHERE poll_id IN (SELECT id FROM poll WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid))",
        "DELETE FROM poll WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # pinned messages
        "DELETE FROM pinned_message WHERE group_id = :gid",
        "DELETE FROM pinned_message WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # favorites
        "DELETE FROM favorite_message WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # media
        "DELETE FROM group_message_media WHERE message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # reactions (group_message_reaction table does not exist, reactions are in message_reaction)
        "DELETE FROM message_reaction WHERE group_message_id IN (SELECT id FROM group_message WHERE group_id = :gid)",
        # last read
        "DELETE FROM last_read_group_message WHERE group_id = :gid",
        # slow mode
        "DELETE FROM slow_mode_tracker WHERE group_id = :gid",
        # hidden chats
        "DELETE FROM hidden_chat WHERE group_id = :gid",
        # members & messages
        "DELETE FROM group_member WHERE group_id = :gid",
        "DELETE FROM group_message WHERE group_id = :gid",
    ]:
        _sql_exec(sql, gid)


def _delete_user_cascade(user_id):
    """Удаляет все зависимые данные пользователя перед его удалением."""
    uid = {"uid": user_id}
    # Удаляем группы пользователя
    from sqlalchemy import text as _t
    try:
        with db.engine.connect() as c:
            rows = c.execute(_t('SELECT id FROM "group" WHERE creator_id = :uid'), uid).fetchall()
            owned = [r[0] for r in rows]
            c.commit()
    except Exception:
        owned = []
    for gid in owned:
        _delete_group_cascade(gid)
        _sql_exec('DELETE FROM "group" WHERE id = :gid', {"gid": gid})
    for sql in [
        # sparks
        "DELETE FROM spark_reaction WHERE sender_id = :uid",
        "DELETE FROM spark_transaction WHERE user_id = :uid",
        "DELETE FROM spark_balance WHERE user_id = :uid",
        "DELETE FROM channel_spark_withdraw WHERE owner_id = :uid",
        # gifts
        "DELETE FROM user_gift WHERE owner_id = :uid OR sender_id = :uid",
        # polls
        "DELETE FROM poll_vote WHERE user_id = :uid",
        "DELETE FROM poll_option WHERE poll_id IN (SELECT id FROM poll WHERE creator_id = :uid)",
        "DELETE FROM poll WHERE creator_id = :uid",
        # paid posts
        "DELETE FROM paid_post_purchase WHERE user_id = :uid",
        # pinned messages
        "DELETE FROM pinned_message WHERE pinned_by = :uid OR user1_id = :uid OR user2_id = :uid",
        # favorites
        "DELETE FROM favorite_message WHERE user_id = :uid",
        # stories
        "DELETE FROM story WHERE user_id = :uid",
        # reports
        "DELETE FROM report WHERE reporter_id = :uid OR reported_user_id = :uid",
        # verification / admin apps
        "DELETE FROM verification_request WHERE user_id = :uid",
        "DELETE FROM admin_application WHERE user_id = :uid",
        # password_reset_request has no user_id column, only reviewed_by
        "UPDATE password_reset_request SET reviewed_by = NULL WHERE reviewed_by = :uid",
        # bots: commands first, then bot
        "DELETE FROM bot_command WHERE bot_id IN (SELECT id FROM bot WHERE owner_id = :uid OR user_id = :uid)",
        "DELETE FROM bot WHERE owner_id = :uid OR user_id = :uid",
        # hidden chats
        "DELETE FROM hidden_chat WHERE user_id = :uid OR other_user_id = :uid",
        # personal messages
        "DELETE FROM last_read_message WHERE user_id = :uid OR chat_with_user_id = :uid",
        "DELETE FROM message_reaction WHERE message_id IN (SELECT id FROM message WHERE sender_id = :uid OR receiver_id = :uid)",
        "DELETE FROM message_reaction WHERE user_id = :uid",
        "DELETE FROM message_media WHERE message_id IN (SELECT id FROM message WHERE sender_id = :uid OR receiver_id = :uid)",
        "DELETE FROM message WHERE sender_id = :uid OR receiver_id = :uid",
        # group membership (group_message_reaction table does not exist, reactions are in message_reaction)
        "DELETE FROM last_read_group_message WHERE user_id = :uid",
        "DELETE FROM group_member WHERE user_id = :uid",
        "DELETE FROM slow_mode_tracker WHERE user_id = :uid",
        # contacts, sessions, tickets, stickers
        "DELETE FROM contact WHERE user_id = :uid OR contact_id = :uid",
        "DELETE FROM user_session WHERE user_id = :uid",
        "DELETE FROM support_ticket WHERE user_id = :uid",
        "DELETE FROM user_sticker_pack WHERE user_id = :uid",
    ]:
        _sql_exec(sql, uid)


def _trigger_webhook(bot, update):
    """Отправляет update на webhook бота. Если webhook не задан — ищет команду в конструкторе."""
    text = ''
    try:
        text = update.get('message', {}).get('text', '').strip()
    except Exception:
        pass

    if not bot.webhook_url:
        if text or True:  # всегда обрабатываем (для файлов тоже)
            sender_id = None
            try:
                sender_id = update['message']['from']['id']
            except Exception:
                pass
            if not sender_id:
                return

            bot_user = User.query.get(bot.user_id)
            is_stickers = bot_user and bot_user.username == 'stickers'

            if is_stickers:
                _handle_stickers_bot(bot.user_id, sender_id, text)
                return

            if not text:
                return

            bot_user_obj = User.query.get(bot.user_id)
            is_support = bot_user_obj and bot_user_obj.username == 'tabletone_supportbot'

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

                        # Ответ пользователю: /reply_<user_id> <текст>
                        if text.lower().startswith('/reply_'):
                            try:
                                parts = text.split(' ', 1)
                                target_user_id = int(parts[0].split('_')[1])
                                reply_text = parts[1].strip() if len(parts) > 1 else ''
                                if reply_text:
                                    target_user = User.query.get(target_user_id)
                                    target_name = (target_user.display_name or target_user.username) if target_user else str(target_user_id)
                                    _bot_send_message(bot.user_id, target_user_id, f"💬 Ответ администрации:\n\n{reply_text}")
                                    _bot_send_message(bot.user_id, sender_id, f"✅ Ответ отправлен пользователю {target_name}.")
                                else:
                                    _bot_send_message(bot.user_id, sender_id, "⚠️ Укажите текст ответа после команды.\nПример: /reply_123 Ваш вопрос решён!")
                            except (ValueError, IndexError):
                                _bot_send_message(bot.user_id, sender_id, "⚠️ Неверный формат. Используйте: /reply_<id> <текст>")
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
                                    f"{t.message_text}\n\n"
                                    f"💡 Чтобы ответить: /reply_{t.user_id} <текст>"
                                )
                                close_btns = [
                                    {"label": "✅ Закрыть диалог", "reply": f"/close_support_{t.user_id}"},
                                    {"label": "💬 Ответить", "reply": f"/reply_{t.user_id} "},
                                ]
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

            # ── Premium Support — пересылаем сообщения владельцу ────────────
            is_premium_support = bot_user_obj and bot_user_obj.username == 'premium_support'
            if is_premium_support:
                sender_user = User.query.get(sender_id)
                owner = User.query.filter_by(username='romancev228').first()

                if not sender_user:
                    return

                sender_is_owner = owner and sender_id == owner.id

                if sender_is_owner:
                    # Владелец отвечает: /reply_<user_id> <текст>
                    if text.lower().startswith('/reply_'):
                        try:
                            parts = text.split(' ', 1)
                            target_user_id = int(parts[0].split('_')[1])
                            reply_text = parts[1].strip() if len(parts) > 1 else ''
                            if reply_text:
                                target_user = User.query.get(target_user_id)
                                target_name = (target_user.display_name or target_user.username) if target_user else str(target_user_id)
                                _bot_send_message(bot.user_id, target_user_id,
                                    f"⭐ *Ответ от разработчика:*\n\n{reply_text}")
                                _bot_send_message(bot.user_id, sender_id,
                                    f"✅ Ответ отправлен пользователю {target_name}.")
                            else:
                                _bot_send_message(bot.user_id, sender_id,
                                    "⚠️ Укажите текст: /reply_<id> <текст>")
                        except (ValueError, IndexError):
                            _bot_send_message(bot.user_id, sender_id,
                                "⚠️ Формат: /reply_<id> <текст>")
                        return
                    # Любое другое сообщение от владельца — показываем инструкцию
                    _bot_send_message(bot.user_id, sender_id,
                        "💡 Чтобы ответить пользователю:\n/reply_<user_id> <текст>")
                    return

                # Обычный пользователь — пересылаем владельцу
                if not text.startswith('/'):
                    if owner:
                        uname = sender_user.username
                        udisp = sender_user.display_name or sender_user.username
                        _bot_send_message(bot.user_id, owner.id,
                            f"⭐ *Premium Support*\n"
                            f"От: @{uname} ({udisp}) [id={sender_id}]\n\n"
                            f"{text}\n\n"
                            f"💡 Ответить: /reply_{sender_id} <текст>",
                            buttons=[{"label": f"💬 Ответить", "reply": f"/reply_{sender_id} "}])
                    _bot_send_message(bot.user_id, sender_id,
                        "✅ Ваше обращение получено. Разработчик ответит вам в ближайшее время.")
                    return

                # Команды — стандартный ответ
                _bot_auto_reply(bot, sender_id, text)
                return

            # ── Перехват /pay_confirm_* для premium бота ─────────────────────
            is_premium_bot = bot_user_obj and bot_user_obj.username == 'tabletone_premiumbot'
            if is_premium_bot and text.lower().startswith('/pay_confirm_'):
                plan_key_raw = text[len('/pay_confirm_'):].strip()
                _plan_map = {
                    '7': 'premium_7', '14': 'premium_14', '30': 'premium_30',
                    '180': 'premium_180', '365': 'premium_365',
                    'sparks_100': 'sparks_100', 'sparks_300': 'sparks_300',
                    'sparks_700': 'sparks_700', 'sparks_1500': 'sparks_1500',
                    'sparks_5000': 'sparks_5000',
                }
                plan_key = _plan_map.get(plan_key_raw)
                if plan_key:
                    sender_user = User.query.get(sender_id)
                    username = sender_user.username if sender_user else 'unknown'
                    pay_bot_username = os.environ.get('PAYMENT_BOT_USERNAME', 'TabletonePay_bot')
                    deep_link = f"https://t.me/{pay_bot_username}?start=pay_{plan_key}_{username}"
                    _bot_send_message(bot.user_id, sender_id,
                        f"💳 Перейдите в Telegram-бот для оплаты.\n"
                        f"Бот уже знает ваш выбор и сразу покажет реквизиты.",
                        buttons=[{"label": "💳 Перейти к оплате в Telegram", "url": deep_link}]
                    )
                return

            # ── tabletone_publisher — только для romancev228 ─────────────────
            is_publisher = bot_user_obj and bot_user_obj.username == 'tabletone_publisher'
            if is_publisher:
                _handle_publisher_bot(bot.user_id, sender_id, text)
                return

            # ── Nexus AI ────────────────────────────────────────────
            is_nexus = bot_user_obj and bot_user_obj.username == 'nexus'
            if is_nexus:
                _handle_nexus_bot(bot.user_id, sender_id, text)
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


def _handle_publisher_bot(bot_user_id, sender_id, text):
    """Обрабатывает сообщения для бота-публикатора. Только romancev228."""
    import threading, urllib.request, json as _json, os as _os

    owner = User.query.filter_by(username='romancev228').first()
    if not owner or sender_id != owner.id:
        _bot_send_message(bot_user_id, sender_id,
            "⛔ Этот бот доступен только администратору.")
        return

    if text.strip().lower() == '/start':
        _bot_send_message(bot_user_id, sender_id,
            "📢 *Tabletone Publisher*\n\n"
            "Напиши заметки об обновлении — я оформлю их красиво и опубликую в @tabletone_official.\n\n"
            "Просто напиши текст 👇")
        return

    # Не обрабатываем другие команды
    if text.startswith('/'):
        _bot_send_message(bot_user_id, sender_id, "Напиши текст обновления (не команду).")
        return

    _bot_send_message(bot_user_id, sender_id, "✍️ Оформляю пост через ИИ...")

    def _do_publish():
        try:
            gemini_key = _os.environ.get('GEMINI_API_KEY', '')
            formatted = text  # fallback

            if gemini_key:
                prompt = (
                    "Ты — редактор официального канала мессенджера Tabletone. "
                    "Оформи следующие заметки об обновлении в красивый пост для канала. "
                    "Используй эмодзи, структурированный текст, заголовок с версией/датой если есть. "
                    "Пиши на русском языке. Верни ТОЛЬКО готовый текст поста, без пояснений.\n\n"
                    f"Заметки:\n{text}"
                )
                payload = _json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}]
                }).encode('utf-8')
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = _json.loads(resp.read())
                        formatted = data['candidates'][0]['content']['parts'][0]['text'].strip()
                except Exception as e:
                    app.logger.warning(f"Publisher Gemini error: {e}")

            # Публикуем в канал
            with app.app_context():
                channel = Group.query.filter_by(username='tabletone_official').first()
                pub_user = User.query.filter_by(username='tabletone_publisher').first()
                if not channel or not pub_user:
                    _bot_send_message(bot_user_id, sender_id,
                        "⚠️ Канал tabletone_official не найден. Создайте его сначала.")
                    return

                msg = GroupMessage(
                    group_id=channel.id,
                    sender_id=pub_user.id,
                    content=formatted
                )
                db.session.add(msg)
                db.session.commit()

                msg_data = {
                    'id': msg.id,
                    'sender_id': pub_user.id,
                    'sender_name': pub_user.display_name or pub_user.username,
                    'sender_avatar_color': pub_user.avatar_color,
                    'sender_avatar_url': pub_user.avatar_url,
                    'sender_avatar_letter': pub_user.get_avatar_letter(),
                    'content': formatted,
                    'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
                    'timestamp_iso': msg.timestamp.isoformat() + 'Z',
                    'message_type': 'text',
                    'media_files': []
                }
                socketio.emit('new_group_message', {
                    'group_id': channel.id,
                    'message': msg_data
                }, room=f'group_{channel.id}', include_self=True)

                _bot_send_message(bot_user_id, sender_id,
                    f"✅ Пост опубликован в @tabletone_official!\n\n"
                    f"📝 *Текст поста:*\n{formatted[:300]}{'...' if len(formatted) > 300 else ''}")
        except Exception as e:
            app.logger.error(f"Publisher bot error: {e}")
            with app.app_context():
                _bot_send_message(bot_user_id, sender_id, f"⚠️ Ошибка публикации: {str(e)[:200]}")

    threading.Thread(target=_do_publish, daemon=True).start()


def _handle_nexus_bot(bot_user_id, sender_id, text):
    """Обрабатывает сообщения для Nexus AI (Cloudflare Workers AI)."""
    import threading

    if text.strip().lower() == '/start':
        _bot_send_message(bot_user_id, sender_id,
            "⚡ *Привет! Я Nexus — твой ИИ-ассистент.*\n\n"
            "Могу помочь с:\n"
            "• Вопросами о мессенджере Tabletone\n"
            "• Ответами на любые вопросы\n"
            "• Написанием текстов и кода\n"
            "• Переводом и объяснением\n"
            "• 🎨 Генерацией картинок — напиши /image <описание>\n\n"
            "Просто напиши мне что-нибудь 💬"
        )
        return

    # Проверяем запрос на генерацию картинки
    text_lower = text.strip().lower()
    image_prefixes = [
        '/image ', '/img ',
        'нарисуй ', 'нарисуй\n',
        'сгенерируй картинку ', 'сгенерируй фото ', 'сгенерируй изображение ',
        'сгенерируй ', 'генерируй ',
        'generate image ', 'draw ', 'create image ',
        'покажи картинку ', 'покажи фото ',
    ]
    image_prompt = None
    for prefix in image_prefixes:
        if text_lower.startswith(prefix):
            image_prompt = text[len(prefix):].strip()
            break

    if image_prompt:
        _bot_send_message(bot_user_id, sender_id, "🎨 Генерирую картинку, подожди...")
        def _gen_image():
            try:
                import urllib.request, urllib.error
                import json as _json
                import base64
                import os as _os
                import uuid

                account_id = _os.environ.get('CF_ACCOUNT_ID', '')
                api_token = _os.environ.get('CF_API_TOKEN', '')
                if not account_id or not api_token:
                    with app.app_context():
                        _bot_send_message(bot_user_id, sender_id, "⚠️ ИИ временно недоступен (нет ключей CF).")
                    return

                # Переводим промпт на английский через Gemini
                cf_prompt = image_prompt
                try:
                    gemini_key = _os.environ.get('GEMINI_API_KEY', '')
                    if gemini_key:
                        tr_payload = _json.dumps({
                            "contents": [{"parts": [{"text": f"Translate this image generation prompt to English. Return ONLY the translated prompt, nothing else: {image_prompt}"}]}]
                        }).encode('utf-8')
                        tr_req = urllib.request.Request(
                            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                            data=tr_payload,
                            headers={"Content-Type": "application/json"},
                            method="POST"
                        )
                        with urllib.request.urlopen(tr_req, timeout=10) as tr_resp:
                            tr_data = _json.loads(tr_resp.read())
                            translated = tr_data['candidates'][0]['content']['parts'][0]['text'].strip()
                            if translated:
                                cf_prompt = translated
                except Exception as tr_err:
                    app.logger.warning(f"Nexus prompt translation failed: {tr_err}")

                payload = _json.dumps({
                    "prompt": cf_prompt,
                    "num_steps": 20
                }).encode('utf-8')

                req = urllib.request.Request(
                    f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_token}"
                    },
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=90) as resp:
                        content_type = resp.headers.get('Content-Type', '')
                        img_bytes = resp.read()
                except urllib.error.HTTPError as he:
                    err_body = he.read().decode('utf-8', errors='replace')
                    app.logger.error(f"Nexus CF HTTP {he.code}: {err_body[:300]}")
                    with app.app_context():
                        _bot_send_message(bot_user_id, sender_id, f"⚠️ Ошибка генерации (HTTP {he.code}): {err_body[:120]}")
                    return

                # CF иногда возвращает JSON с ошибкой вместо PNG
                if img_bytes[:1] == b'{':
                    try:
                        err_json = _json.loads(img_bytes)
                        app.logger.error(f"Nexus CF returned JSON error: {err_json}")
                        err_msg = err_json.get('errors', [{}])[0].get('message', str(err_json))[:150]
                        with app.app_context():
                            _bot_send_message(bot_user_id, sender_id, f"⚠️ CF ошибка: {err_msg}")
                        return
                    except Exception:
                        pass

                # Сжимаем до 512x512 JPEG (~50-100KB вместо 2MB)
                try:
                    from PIL import Image as _PILImage
                    import io as _io
                    pil_img = _PILImage.open(_io.BytesIO(img_bytes)).convert('RGB')
                    pil_img.thumbnail((512, 512), _PILImage.LANCZOS)
                    buf = _io.BytesIO()
                    pil_img.save(buf, format='JPEG', quality=85)
                    img_bytes = buf.getvalue()
                    img_ext = 'jpg'
                except Exception as pil_err:
                    app.logger.warning(f"Nexus PIL compress failed: {pil_err}, using raw PNG")
                    img_ext = 'png'

                # data URL для хранения в БД (не зависит от ФС Render)
                mime = 'image/jpeg' if img_ext == 'jpg' else 'image/png'
                data_url = f"data:{mime};base64," + base64.b64encode(img_bytes).decode()
                fname = f"nexus_{uuid.uuid4().hex[:12]}.{img_ext}"

                with app.app_context():
                    bot_user = User.query.get(bot_user_id)
                    receiver = User.query.get(sender_id)
                    if not bot_user or not receiver:
                        return
                    msg = Message(
                        sender_id=bot_user_id,
                        receiver_id=sender_id,
                        content=f'🎨 {image_prompt}',
                        message_type='image',
                        media_url=data_url
                    )
                    db.session.add(msg)
                    db.session.commit()

                    media = MessageMedia(
                        message_id=msg.id,
                        media_url=data_url,
                        media_type='image',
                        file_name=fname
                    )
                    db.session.add(media)
                    db.session.commit()

                    msg_data = {
                        'id': msg.id,
                        'sender_id': bot_user_id,
                        'content': f'🎨 {image_prompt}',
                        'timestamp': msg.timestamp.strftime('%H:%M'),
                        'timestamp_iso': msg.timestamp.isoformat() + 'Z',
                        'is_mine': False,
                        'message_type': 'image',
                        'media_url': data_url,
                        'media_files': [{'media_url': data_url, 'media_type': 'image', 'file_name': fname}],
                        'duration': None, 'is_deleted': False, 'is_read': False,
                        'reply_to': None, 'bot_buttons': [], 'sticker_pack_id': None, 'gift': None,
                    }
                    _nexus_sender_info = {
                        'id': bot_user_id, 'username': 'nexus',
                        'display_name': 'Nexus AI', 'avatar_color': '#7c3aed',
                        'avatar_letter': 'N', 'avatar_url': None
                    }
                    socketio.emit('new_message', {
                        'message': msg_data,
                        'other_user_id': bot_user_id,
                        'sender_info': _nexus_sender_info,
                    }, room=f'user_{sender_id}', namespace='/')

            except Exception as e:
                import traceback
                app.logger.error(f"Nexus image gen error: {e}\n{traceback.format_exc()}")
                with app.app_context():
                    _bot_send_message(bot_user_id, sender_id, f"⚠️ Не удалось сгенерировать картинку: {str(e)[:120]}")

        threading.Thread(target=_gen_image, daemon=True).start()
        return

    socketio.emit('user_typing', {
        'chat_type': 'private',
        'name': 'Nexus'
    }, room=f'user_{sender_id}', namespace='/')

    def _ask_cf():
        reply_text = "⚠️ Произошла ошибка. Попробуй ещё раз."
        try:
            import urllib.request
            import json as _json

            account_id = os.environ.get('CF_ACCOUNT_ID', '')
            api_token = os.environ.get('CF_API_TOKEN', '')
            if not account_id or not api_token:
                reply_text = "⚠️ ИИ временно недоступен. Обратитесь к администратору."
            else:
                system_prompt = (
                    "Ты — Nexus, умный и дружелюбный ИИ-ассистент встроенный в мессенджер Tabletone. "
                    "Отвечай кратко, по делу и на том языке, на котором пишет пользователь. "
                    "Используй эмодзи умеренно. "
                    "ВАЖНО: никогда не раскрывай, на какой технологии, модели или платформе ты основан. "
                    "Если спросят — скажи только что ты Nexus, собственный ИИ мессенджера Tabletone.\n\n"
                    "=== ЗНАНИЯ О МЕССЕНДЖЕРЕ TABLETONE ===\n"
                    "Tabletone — современный мессенджер. Вот что умеет:\n"
                    "ОБЩЕНИЕ: личные чаты, группы (роли: владелец/админ/модератор/участник), каналы, публичные ссылки-приглашения.\n"
                    "СООБЩЕНИЯ: текст, фото, видео, голосовые, видеосообщения (кружочки), файлы, стикеры (@stickers), "
                    "reply, пересылка, редактирование, удаление, таймер удаления, реакции эмодзи, "
                    "галочки доставки (✓ отправлено, ✓✓ прочитано), избранное, предпросмотр медиа (Ctrl+V).\n"
                    "ЗВОНКИ: аудио и видеозвонки (кнопка трубки → выбор типа), управление микрофоном и камерой. Ботам звонить нельзя.\n"
                    "ПРОФИЛЬ: аватар, имя, bio, статус, 2FA, привязка Telegram, скрытый чат с PIN, папки чатов, тёмная/светлая тема.\n"
                    "PREMIUM: подписка с расширенными возможностями, Искры (внутренняя валюта), подарки. Оформить: @tabletone_premiumbot.\n"
                    "БОТЫ: @nexus (ИИ-ассистент), @tabletone_supportbot (поддержка), @tabletone_premiumbot (Premium/Искры), @stickers (стикеры), конструктор ботов.\n"
                    "БЕЗОПАСНОСТЬ: блокировка пользователей, антиспам, бан/мут участников групп.\n"
                    "Если спрашивают о функциях Tabletone — отвечай на основе этих знаний. "
                    "Если не знаешь точно — скажи честно и предложи @tabletone_supportbot."
                )

                payload = _json.dumps({
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text}
                    ],
                    "max_tokens": 512
                }).encode('utf-8')

                req = urllib.request.Request(
                    f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_token}"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = _json.loads(resp.read().decode('utf-8'))
                reply_text = result['result']['response'].strip()

        except Exception as e:
            import traceback
            print(f"Nexus AI error: {e}\n{traceback.format_exc()}")
            err_str = str(e).lower()
            if '401' in err_str or 'unauthorized' in err_str:
                reply_text = "⚠️ Ошибка авторизации. Проверьте CF_API_TOKEN."
            elif 'timeout' in err_str or 'timed out' in err_str:
                reply_text = "⏱ Запрос занял слишком много времени. Попробуй ещё раз."
            elif '429' in err_str or 'rate' in err_str:
                reply_text = "⏳ Слишком много запросов. Подожди немного и попробуй снова."
            else:
                reply_text = f"⚠️ Ошибка ИИ: {str(e)[:100]}"

        socketio.emit('user_stop_typing', {
            'chat_type': 'private'
        }, room=f'user_{sender_id}', namespace='/')

        with app.app_context():
            _bot_send_message(bot_user_id, sender_id, reply_text)

    threading.Thread(target=_ask_cf, daemon=True).start()


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
    """Отправляет код 2FA через @tabletonebot и в Telegram если привязан."""
    with app.app_context():
        user = User.query.get(user_id)
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
        else:
            print(f"[2FA] tabletonebot not found in DB — cannot send internal message to user {user_id}")

        # Отправка в Telegram если привязан
        if user and user.telegram_chat_id:
            try:
                _send_telegram_2fa(user.telegram_chat_id, code)
            except Exception as e:
                print(f"Telegram 2FA error: {e}")
        else:
            print(f"[2FA] User {user_id} has no telegram_chat_id linked")


def _send_email_register_verify(to_email, code, username):
    """Отправляет код подтверждения email при регистрации."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    if not smtp_user or not smtp_pass:
        print(f"[EMAIL VERIFY] SMTP не настроен — код: {code}")
        return
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'Подтверждение email — Tabletone'
    msg['From'] = f'Tabletone <{smtp_user}>'
    msg['To'] = to_email
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:30px;background:#f7f8fc;border-radius:12px;">
      <h2 style="color:#667eea;margin-bottom:8px;">👋 Добро пожаловать в Tabletone, {username}!</h2>
      <p style="color:#4a5568;">Для завершения регистрации введите этот код:</p>
      <div style="font-size:36px;font-weight:bold;letter-spacing:8px;color:#2d3748;background:#fff;padding:20px;border-radius:8px;text-align:center;margin:20px 0;">{code}</div>
      <p style="color:#718096;font-size:13px;">⏱ Код действителен 30 минут.</p>
      <p style="color:#e53e3e;font-size:13px;">⚠️ Если вы не регистрировались — просто проигнорируйте это письмо.</p>
    </div>
    """
    msg.attach(MIMEText(html, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
    except Exception as e:
        print(f"[EMAIL VERIFY] Ошибка отправки: {e}")


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


def _send_telegram_2fa(chat_id, code):
    """Отправляет код 2FA через Telegram бота."""
    import urllib.request
    import urllib.error
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        print("[2FA] TELEGRAM_BOT_TOKEN not set — cannot send Telegram 2FA")
        return
    text = (
        f"🔐 Код входа в Tabletone:\n\n"
        f"<b>{code}</b>\n\n"
        f"⏱ Действителен 10 минут.\n"
        f"⚠️ Никому не передавайте этот код."
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        print(f"[2FA] Telegram message sent to chat_id={chat_id}, status={resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f"[2FA] Telegram HTTPError {e.code} for chat_id={chat_id}: {body}")
        raise
    except Exception as e:
        print(f"[2FA] Telegram send failed for chat_id={chat_id}: {type(e).__name__}: {e}")
        raise




def _notify_new_login(user_id, device_name, ip_address):
    """Уведомляет пользователя о входе с нового устройства — в мессенджер и Telegram."""
    with app.app_context():
        user = User.query.get(user_id)
        if not user:
            return

        from datetime import datetime as _dt
        now = _dt.utcnow().strftime('%d.%m.%Y %H:%M') + ' UTC'

        # ── Уведомление в мессенджер (от tabletone_supportbot) ──
        bot_user = User.query.filter_by(username='tabletone_supportbot').first()
        if bot_user:
            nl = "\n"
            msg_text = (
                "🔔 *Новый вход в аккаунт*" + nl + nl +
                "📱 Устройство: " + device_name + nl +
                "🌐 IP-адрес: " + ip_address + nl +
                "🕐 Время: " + now + nl + nl +
                "Если это были не вы — немедленно смените пароль и завершите все сессии в настройках профиля."
            )
            _bot_send_message(bot_user.id, user_id, msg_text)

        # ── Уведомление в Telegram ──
        if user.telegram_chat_id:
            try:
                import urllib.request, json as _json
                token = os.environ.get('TELEGRAM_BOT_TOKEN')
                if token:
                    text = (
                        "\U0001f6e1 <b>Tabletone \u2014 \u041d\u043e\u0432\u044b\u0439 \u0432\u0445\u043e\u0434</b>\n\n"
                        "\U0001f464 \u0410\u043a\u043a\u0430\u0443\u043d\u0442: <b>@" + user.username + "</b>\n"
                        "\U0001f4f1 \u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e: <b>" + device_name + "</b>\n"
                        "\U0001f310 IP-\u0430\u0434\u0440\u0435\u0441: <code>" + ip_address + "</code>\n"
                        "\U0001f550 \u0412\u0440\u0435\u043c\u044f: <b>" + now + "</b>\n\n"
                        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                        "\u26a0\ufe0f \u0415\u0441\u043b\u0438 \u044d\u0442\u043e \u0431\u044b\u043b\u0438 \u043d\u0435 \u0432\u044b \u2014 \u043d\u0435\u043c\u0435\u0434\u043b\u0435\u043d\u043d\u043e \u0441\u043c\u0435\u043d\u0438\u0442\u0435 \u043f\u0430\u0440\u043e\u043b\u044c!"
                    )
                    url = "https://api.telegram.org/bot" + token + "/sendMessage"
                    data = _json.dumps({
                        'chat_id': user.telegram_chat_id,
                        'text': text,
                        'parse_mode': 'HTML'
                    }).encode()
                    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
                    urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                print("Login notify Telegram error: " + str(e))

def _notify_admin_support(admin_user_id):
    """Отправляет администратору уведомление об открытых обращениях при логине."""
    with app.app_context():
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
            user.two_fa_code = None
            user.two_fa_code_expires = None
            db.session.commit()
            # Trusted IP: store in session for 5 minutes
            if request.form.get('trust_ip'):
                import time as _time
                session['trusted_ip'] = get_client_ip()
                session['trusted_ip_until'] = _time.time() + 300  # 5 minutes
            session.pop('2fa_pending_user_id', None)
            session.pop('2fa_reason', None)
            session['user_id'] = user.id
            user.last_seen = datetime.utcnow()
            session_token = secrets.token_urlsafe(32)
            user_agent = request.headers.get('User-Agent', '')
            ip_address = get_client_ip()
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
            reason = session.get('2fa_reason', '')
            return render_template('login_2fa.html', error='Неверный или просроченный код',
                                   reason=reason, has_telegram=bool(user and user.telegram_chat_id))

    user = User.query.get(pending_id)
    has_telegram = bool(user and user.telegram_chat_id)
    reason = session.get('2fa_reason', '')
    return render_template('login_2fa.html', has_telegram=has_telegram, reason=reason)


@app.route('/login/2fa/resend', methods=['POST'])
def resend_2fa_code():
    """Повторная отправка кода 2FA с нарастающей задержкой."""
    pending_id = session.get('2fa_pending_user_id')
    if not pending_id:
        return jsonify({'error': 'Сессия истекла'}), 400
    user = User.query.get(pending_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    # Считаем сколько раз уже запрашивали — храним в сессии
    resend_count = session.get('2fa_resend_count', 0)
    last_resend = session.get('2fa_last_resend', 0)
    cooldown = 30 * (resend_count + 1)  # 30, 60, 90... секунд

    import time
    now = time.time()
    if last_resend and (now - last_resend) < cooldown:
        remaining = int(cooldown - (now - last_resend))
        return jsonify({'error': f'Подождите {remaining} сек.', 'remaining': remaining}), 429

    # Генерируем новый код
    code = str(random.randint(100000, 999999))
    from datetime import timedelta
    user.two_fa_code = code
    user.two_fa_code_expires = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    try:
        _send_2fa_code(user.id, code)
    except Exception as e:
        print(f"2FA resend error: {e}")

    session['2fa_resend_count'] = resend_count + 1
    session['2fa_last_resend'] = now
    next_cooldown = 30 * (resend_count + 2)
    return jsonify({'success': True, 'next_cooldown': next_cooldown})


@app.route('/profile/2fa/enable', methods=['POST'])
def enable_2fa():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if user.two_fa_enabled:
        return jsonify({'error': 'Уже включено'}), 400
    user.two_fa_enabled = True
    user.two_fa_code = None
    user.two_fa_code_expires = None
    db.session.commit()
    return jsonify({'success': True, 'message': '2FA включена. Код будет отправлен при следующем входе.'})


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
    if len(username) < 4:
        return jsonify({'error': 'Username бота должен содержать минимум 4 символа'}), 400
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
            'content': m.decrypted_content or f'[{m.message_type}]',
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

# ── Реакции ──────────────────────────────────────────────────────────────────

@app.route('/message/<int:msg_id>/react', methods=['POST'])
def react_message(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    emoji = (data.get('emoji') or '').strip()
    if not emoji:
        return jsonify({'error': 'Укажите эмодзи'}), 400
    msg = Message.query.get(msg_id)
    if not msg:
        return jsonify({'error': 'Не найдено'}), 404
    # Проверяем доступ
    uid = session['user_id']
    if msg.sender_id != uid and msg.receiver_id != uid:
        return jsonify({'error': 'Нет доступа'}), 403
    existing = MessageReaction.query.filter_by(user_id=uid, message_id=msg_id, emoji=emoji).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        action = 'removed'
    else:
        user = User.query.get(uid)
        # Обычный пользователь — только одна реакция (заменяем старую)
        if not user.is_premium:
            MessageReaction.query.filter_by(user_id=uid, message_id=msg_id).delete()
        r = MessageReaction(user_id=uid, message_id=msg_id, emoji=emoji)
        db.session.add(r)
        db.session.commit()
        action = 'added'
    # Собираем все реакции
    reactions = _get_message_reactions(msg_id=msg_id)
    # Уведомляем через Socket.IO
    other_id = msg.receiver_id if msg.sender_id == uid else msg.sender_id
    for room_uid in [uid, other_id]:
        socketio.emit('reaction_updated', {'message_id': msg_id, 'reactions': reactions}, room=f'user_{room_uid}', namespace='/')
    return jsonify({'success': True, 'action': action, 'reactions': reactions})

@app.route('/group-message/<int:msg_id>/react', methods=['POST'])
def react_group_message(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    emoji = (data.get('emoji') or '').strip()
    if not emoji:
        return jsonify({'error': 'Укажите эмодзи'}), 400
    msg = GroupMessage.query.get(msg_id)
    if not msg:
        return jsonify({'error': 'Не найдено'}), 404
    uid = session['user_id']
    membership = GroupMember.query.filter_by(group_id=msg.group_id, user_id=uid).first()
    if not membership:
        return jsonify({'error': 'Нет доступа'}), 403
    # Проверяем ограничения участника
    restrictions = json.loads(membership.member_restrictions or '{}')
    if restrictions.get('can_react') == False:
        return jsonify({'error': 'Реакции запрещены для вас'}), 403
    allowed = restrictions.get('allowed_reactions', [])
    if allowed and emoji not in allowed:
        return jsonify({'error': f'Реакция {emoji} вам недоступна'}), 403
    existing = MessageReaction.query.filter_by(user_id=uid, group_message_id=msg_id, emoji=emoji).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        action = 'removed'
    else:
        user = User.query.get(uid)
        # Обычный пользователь — только одна реакция (заменяем старую)
        if not user.is_premium:
            MessageReaction.query.filter_by(user_id=uid, group_message_id=msg_id).delete()
        r = MessageReaction(user_id=uid, group_message_id=msg_id, emoji=emoji)
        db.session.add(r)
        db.session.commit()
        action = 'added'
    reactions = _get_message_reactions(group_msg_id=msg_id)
    socketio.emit('reaction_updated', {'group_message_id': msg_id, 'reactions': reactions}, room=f'group_{msg.group_id}', namespace='/')
    return jsonify({'success': True, 'action': action, 'reactions': reactions})

def _get_message_reactions(msg_id=None, group_msg_id=None):
    """Возвращает словарь {emoji: {count, users, pack_id}}"""
    if msg_id:
        rows = MessageReaction.query.filter_by(message_id=msg_id).all()
    else:
        rows = MessageReaction.query.filter_by(group_message_id=group_msg_id).all()
    result = {}
    for r in rows:
        if r.emoji not in result:
            # Для кастомных реакций (URL) ищем pack_id
            pack_id = None
            if r.emoji.startswith('/static/media/reactions/'):
                # URL вида /static/media/reactions/{pack_id}/...
                try:
                    pack_id = int(r.emoji.split('/')[4])
                except Exception:
                    pass
            result[r.emoji] = {'count': 0, 'users': [], 'pack_id': pack_id}
        result[r.emoji]['count'] += 1
        result[r.emoji]['users'].append(r.user_id)
    return result

# ── Закреплённые сообщения ────────────────────────────────────────────────────

@app.route('/chat/<int:other_id>/pin', methods=['POST'])
def pin_message(other_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    msg_id = data.get('message_id')
    if not msg_id:
        return jsonify({'error': 'Укажите message_id'}), 400
    msg = Message.query.get(msg_id)
    if not msg or (msg.sender_id != uid and msg.receiver_id != uid):
        return jsonify({'error': 'Нет доступа'}), 403
    u1, u2 = min(uid, other_id), max(uid, other_id)
    # Удаляем старое закреплённое
    PinnedMessage.query.filter_by(user1_id=u1, user2_id=u2).delete()
    pin = PinnedMessage(user1_id=u1, user2_id=u2, message_id=msg_id, pinned_by=uid, content_preview=(msg.content or '')[:100])
    db.session.add(pin)
    db.session.commit()
    for room_uid in [uid, other_id]:
        socketio.emit('message_pinned', {'chat_type': 'private', 'other_id': other_id, 'message_id': msg_id, 'preview': pin.content_preview}, room=f'user_{room_uid}', namespace='/')
    return jsonify({'success': True})

@app.route('/chat/<int:other_id>/unpin', methods=['POST'])
def unpin_message(other_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    u1, u2 = min(uid, other_id), max(uid, other_id)
    PinnedMessage.query.filter_by(user1_id=u1, user2_id=u2).delete()
    db.session.commit()
    for room_uid in [uid, other_id]:
        socketio.emit('message_unpinned', {'chat_type': 'private', 'other_id': other_id}, room=f'user_{room_uid}', namespace='/')
    return jsonify({'success': True})

@app.route('/chat/<int:other_id>/pinned')
def get_pinned_message(other_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    u1, u2 = min(uid, other_id), max(uid, other_id)
    pin = PinnedMessage.query.filter_by(user1_id=u1, user2_id=u2).first()
    if not pin:
        return jsonify({'pinned': None})
    return jsonify({'pinned': {'message_id': pin.message_id, 'preview': pin.content_preview}})

@app.route('/groups/<int:group_id>/pin', methods=['POST'])
def pin_group_message(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут закреплять'}), 403
    data = request.get_json() or {}
    msg_id = data.get('message_id')
    msg = GroupMessage.query.get(msg_id)
    if not msg or msg.group_id != group_id:
        return jsonify({'error': 'Не найдено'}), 404
    PinnedMessage.query.filter_by(group_id=group_id).delete()
    pin = PinnedMessage(group_id=group_id, group_message_id=msg_id, pinned_by=uid, content_preview=(msg.content or '')[:100])
    db.session.add(pin)
    db.session.commit()
    socketio.emit('message_pinned', {'chat_type': 'group', 'group_id': group_id, 'message_id': msg_id, 'preview': pin.content_preview}, room=f'group_{group_id}', namespace='/')
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/unpin', methods=['POST'])
def unpin_group_message(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только админы могут откреплять'}), 403
    PinnedMessage.query.filter_by(group_id=group_id).delete()
    db.session.commit()
    socketio.emit('message_unpinned', {'chat_type': 'group', 'group_id': group_id}, room=f'group_{group_id}', namespace='/')
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/pinned')
def get_group_pinned(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    pin = PinnedMessage.query.filter_by(group_id=group_id).first()
    if not pin:
        return jsonify({'pinned': None})
    return jsonify({'pinned': {'message_id': pin.group_message_id, 'preview': pin.content_preview}})

# ── Статус "печатает..." ──────────────────────────────────────────────────────

@socketio.on('typing_start')
def on_typing_start(data):
    if 'user_id' not in session:
        return
    uid = session['user_id']
    user = User.query.get(uid)
    name = (user.display_name or user.username) if user else '?'
    target = data.get('to_user_id') or data.get('group_id')
    if data.get('to_user_id'):
        socketio.emit('user_typing', {'user_id': uid, 'name': name, 'chat_type': 'private'}, room=f'user_{target}', namespace='/')
    elif data.get('group_id'):
        socketio.emit('user_typing', {'user_id': uid, 'name': name, 'chat_type': 'group', 'group_id': target}, room=f'group_{target}', skip_sid=request.sid, namespace='/')

@socketio.on('typing_stop')
def on_typing_stop(data):
    if 'user_id' not in session:
        return
    uid = session['user_id']
    target = data.get('to_user_id') or data.get('group_id')
    if data.get('to_user_id'):
        socketio.emit('user_stop_typing', {'user_id': uid, 'chat_type': 'private'}, room=f'user_{target}', namespace='/')
    elif data.get('group_id'):
        socketio.emit('user_stop_typing', {'user_id': uid, 'chat_type': 'group', 'group_id': target}, room=f'group_{target}', skip_sid=request.sid, namespace='/')

# ── Опросы ────────────────────────────────────────────────────────────────────

@app.route('/poll/create', methods=['POST'])
def create_poll():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    question = (data.get('question') or '').strip()
    options = data.get('options', [])
    is_anonymous = data.get('is_anonymous', True)
    is_multiple = data.get('is_multiple', False)
    receiver_id = data.get('receiver_id')
    group_id = data.get('group_id')
    if not question or len(question) > 500:
        return jsonify({'error': 'Укажите вопрос (до 500 символов)'}), 400
    if len(options) < 2 or len(options) > 10:
        return jsonify({'error': 'Нужно от 2 до 10 вариантов'}), 400
    uid = session['user_id']
    poll = Poll(creator_id=uid, question=question, is_anonymous=is_anonymous, is_multiple=is_multiple)
    db.session.add(poll)
    db.session.flush()
    for i, opt_text in enumerate(options):
        opt_text = str(opt_text).strip()
        if not opt_text or len(opt_text) > 200:
            continue
        db.session.add(PollOption(poll_id=poll.id, text=opt_text, order_index=i))
    # Создаём сообщение-контейнер
    if receiver_id:
        msg = Message(sender_id=uid, receiver_id=int(receiver_id), content=f'📊 {question}', message_type='poll')
        db.session.add(msg)
        db.session.flush()
        poll.message_id = msg.id
        db.session.commit()
        sender = User.query.get(uid)
        msg_data = {
            'id': msg.id, 'sender_id': uid, 'content': msg.decrypted_content,
            'message_type': 'poll', 'poll_id': poll.id,
            'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': msg.timestamp.isoformat() + 'Z'
        }
        sender_info = {'id': sender.id, 'username': sender.username, 'display_name': sender.display_name or sender.username, 'avatar_color': sender.avatar_color, 'avatar_letter': sender.get_avatar_letter()}
        socketio.emit('new_message', {'message': {**msg_data, 'is_mine': True}, 'other_user_id': int(receiver_id), 'sender_info': sender_info}, room=f'user_{uid}', namespace='/')
        socketio.emit('new_message', {'message': {**msg_data, 'is_mine': False}, 'other_user_id': uid, 'sender_info': sender_info}, room=f'user_{receiver_id}', namespace='/')
        # Push-уведомление получателю
        _send_fcm(int(receiver_id), sender.display_name or sender.username, msg_data.get('content', 'Новое сообщение')[:100], 'message')
    elif group_id:
        membership = GroupMember.query.filter_by(group_id=int(group_id), user_id=uid).first()
        if not membership:
            return jsonify({'error': 'Нет доступа'}), 403
        msg = GroupMessage(group_id=int(group_id), sender_id=uid, content=f'📊 {question}')
        db.session.add(msg)
        db.session.flush()
        poll.group_message_id = msg.id
        db.session.commit()
        sender = User.query.get(uid)
        msg_data = {
            'id': msg.id, 'sender_id': uid,
            'sender_name': sender.display_name or sender.username,
            'sender_avatar_color': sender.avatar_color, 'sender_avatar_letter': sender.get_avatar_letter(),
            'content': msg.decrypted_content, 'message_type': 'poll', 'poll_id': poll.id,
            'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
            'timestamp_iso': msg.timestamp.isoformat() + 'Z', 'media_files': []
        }
        socketio.emit('new_group_message', {'group_id': int(group_id), 'message': msg_data}, room=f'group_{group_id}', include_self=True, namespace='/')
    else:
        db.session.commit()
    return jsonify({'success': True, 'poll_id': poll.id})

@app.route('/poll/<int:poll_id>')
def get_poll(poll_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    poll = Poll.query.get(poll_id)
    if not poll:
        return jsonify({'error': 'Не найдено'}), 404
    uid = session['user_id']
    options = PollOption.query.filter_by(poll_id=poll_id).order_by(PollOption.order_index).all()
    total_votes = PollVote.query.filter_by(poll_id=poll_id).count()
    my_votes = [v.option_id for v in PollVote.query.filter_by(poll_id=poll_id, user_id=uid).all()]
    opts_data = []
    for opt in options:
        votes = PollVote.query.filter_by(option_id=opt.id).count()
        opts_data.append({'id': opt.id, 'text': opt.text, 'votes': votes, 'percent': round(votes / total_votes * 100) if total_votes else 0})
    return jsonify({'poll': {'id': poll.id, 'question': poll.question, 'is_anonymous': poll.is_anonymous, 'is_multiple': poll.is_multiple, 'total_votes': total_votes, 'my_votes': my_votes, 'options': opts_data}})

@app.route('/poll/<int:poll_id>/vote', methods=['POST'])
def vote_poll(poll_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    poll = Poll.query.get(poll_id)
    if not poll:
        return jsonify({'error': 'Не найдено'}), 404
    uid = session['user_id']
    data = request.get_json() or {}
    option_ids = data.get('option_ids', [])
    if not option_ids:
        return jsonify({'error': 'Выберите вариант'}), 400
    if not poll.is_multiple and len(option_ids) > 1:
        option_ids = [option_ids[0]]
    # Удаляем старые голоса
    PollVote.query.filter_by(poll_id=poll_id, user_id=uid).delete()
    for oid in option_ids:
        opt = PollOption.query.filter_by(id=int(oid), poll_id=poll_id).first()
        if opt:
            db.session.add(PollVote(poll_id=poll_id, option_id=opt.id, user_id=uid))
    db.session.commit()
    # Уведомляем через Socket.IO
    if poll.message_id:
        msg = Message.query.get(poll.message_id)
        if msg:
            for room_uid in [msg.sender_id, msg.receiver_id]:
                socketio.emit('poll_updated', {'poll_id': poll_id, 'message_id': poll.message_id}, room=f'user_{room_uid}', namespace='/')
    elif poll.group_message_id:
        msg = GroupMessage.query.get(poll.group_message_id)
        if msg:
            socketio.emit('poll_updated', {'poll_id': poll_id, 'group_message_id': poll.group_message_id}, room=f'group_{msg.group_id}', namespace='/')
    return jsonify({'success': True})

# ── Контакты ──────────────────────────────────────────────────────────────────

@app.route('/contacts', methods=['GET'])
def get_contacts():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    contacts = Contact.query.filter_by(user_id=uid).all()
    result = []
    for c in contacts:
        u = c.contact
        result.append({
            'id': u.id, 'username': u.username,
            'display_name': u.display_name or u.username,
            'avatar_color': u.avatar_color, 'avatar_url': u.avatar_url,
            'avatar_letter': u.get_avatar_letter(), 'is_verified': u.is_verified
        })
    return jsonify({'contacts': result})

@app.route('/contacts/add', methods=['POST'])
def add_contact():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    target_id = data.get('user_id')
    if not target_id or int(target_id) == uid:
        return jsonify({'error': 'Некорректный пользователь'}), 400
    target = User.query.get(int(target_id))
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404
    existing = Contact.query.filter_by(user_id=uid, contact_id=int(target_id)).first()
    if existing:
        return jsonify({'success': True, 'already': True})
    db.session.add(Contact(user_id=uid, contact_id=int(target_id)))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/contacts/remove', methods=['POST'])
def remove_contact():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    target_id = data.get('user_id')
    Contact.query.filter_by(user_id=uid, contact_id=int(target_id)).delete()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/contacts/check/<int:target_id>')
def check_contact(target_id):
    """Проверяет взаимность контактов"""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    i_added = Contact.query.filter_by(user_id=uid, contact_id=target_id).first() is not None
    they_added = Contact.query.filter_by(user_id=target_id, contact_id=uid).first() is not None
    return jsonify({'i_added': i_added, 'they_added': they_added, 'mutual': i_added and they_added})

# ── Пересылка сообщений ───────────────────────────────────────────────────────

@app.route('/message/forward', methods=['POST'])
def forward_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    sender = User.query.get(uid)
    data = request.get_json() or {}
    # Источник
    msg_id = data.get('message_id')
    group_msg_id = data.get('group_message_id')
    # Назначение
    to_user_id = data.get('to_user_id')
    to_group_id = data.get('to_group_id')
    if not (msg_id or group_msg_id) or not (to_user_id or to_group_id):
        return jsonify({'error': 'Укажите источник и назначение'}), 400
    # Получаем контент
    if msg_id:
        orig = Message.query.get(msg_id)
        if not orig or (orig.sender_id != uid and orig.receiver_id != uid):
            return jsonify({'error': 'Нет доступа'}), 403
        content = orig.content
        msg_type = orig.message_type or 'text'
        media_url = orig.media_url
        orig_sender = User.query.get(orig.sender_id)
    else:
        orig = GroupMessage.query.get(group_msg_id)
        if not orig:
            return jsonify({'error': 'Не найдено'}), 404
        membership = GroupMember.query.filter_by(group_id=orig.group_id, user_id=uid).first()
        if not membership:
            return jsonify({'error': 'Нет доступа'}), 403
        content = orig.content
        msg_type = 'text'
        media_url = None
        orig_sender = User.query.get(orig.sender_id)
    fwd_prefix = f'↩ Переслано от {orig_sender.display_name or orig_sender.username}:\n' if orig_sender else '↩ Переслано:\n'
    fwd_content = fwd_prefix + content
    if to_user_id:
        new_msg = Message(sender_id=uid, receiver_id=int(to_user_id), content=fwd_content, message_type='text', media_url=media_url)
        db.session.add(new_msg)
        db.session.commit()
        msg_data = {'id': new_msg.id, 'sender_id': uid, 'content': fwd_content, 'message_type': 'text', 'timestamp': new_msg.timestamp.strftime('%H:%M %d.%m'), 'timestamp_iso': new_msg.timestamp.isoformat() + 'Z'}
        sender_info = {'id': sender.id, 'username': sender.username, 'display_name': sender.display_name or sender.username, 'avatar_color': sender.avatar_color, 'avatar_letter': sender.get_avatar_letter()}
        socketio.emit('new_message', {'message': {**msg_data, 'is_mine': True}, 'other_user_id': int(to_user_id), 'sender_info': sender_info}, room=f'user_{uid}', namespace='/')
        socketio.emit('new_message', {'message': {**msg_data, 'is_mine': False}, 'other_user_id': uid, 'sender_info': sender_info}, room=f'user_{to_user_id}', namespace='/')
    elif to_group_id:
        membership = GroupMember.query.filter_by(group_id=int(to_group_id), user_id=uid).first()
        if not membership:
            return jsonify({'error': 'Нет доступа к группе'}), 403
        new_msg = GroupMessage(group_id=int(to_group_id), sender_id=uid, content=fwd_content)
        db.session.add(new_msg)
        db.session.commit()
        msg_data = {'id': new_msg.id, 'sender_id': uid, 'sender_name': sender.display_name or sender.username, 'sender_avatar_color': sender.avatar_color, 'sender_avatar_letter': sender.get_avatar_letter(), 'content': fwd_content, 'timestamp': new_msg.timestamp.strftime('%H:%M %d.%m'), 'timestamp_iso': new_msg.timestamp.isoformat() + 'Z', 'media_files': []}
        socketio.emit('new_group_message', {'group_id': int(to_group_id), 'message': msg_data}, room=f'group_{to_group_id}', include_self=True, namespace='/')
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ИСКРЫ (SPARKS)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_spark_balance(user_id):
    sb = SparkBalance.query.filter_by(user_id=user_id).first()
    if not sb:
        sb = SparkBalance(user_id=user_id, balance=0)
        db.session.add(sb)
        db.session.commit()
    return sb

def _add_sparks(user_id, amount, reason, ref_id=None):
    sb = _get_spark_balance(user_id)
    sb.balance += amount
    tx = SparkTransaction(user_id=user_id, amount=amount, reason=reason, ref_id=ref_id)
    db.session.add(tx)
    db.session.commit()

def _spend_sparks(user_id, amount, reason, ref_id=None):
    sb = _get_spark_balance(user_id)
    if sb.balance < amount:
        return False
    sb.balance -= amount
    tx = SparkTransaction(user_id=user_id, amount=-amount, reason=reason, ref_id=ref_id)
    db.session.add(tx)
    db.session.commit()
    return True

@app.route('/sparks/balance')
def sparks_balance():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    sb = _get_spark_balance(session['user_id'])
    return jsonify({'balance': sb.balance})

@app.route('/sparks/history')
def sparks_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    txs = SparkTransaction.query.filter_by(user_id=session['user_id']).order_by(SparkTransaction.created_at.desc()).limit(50).all()
    return jsonify({'transactions': [{'amount': t.amount, 'reason': t.reason, 'created_at': t.created_at.isoformat()} for t in txs]})

@app.route('/sparks/react/<int:msg_id>', methods=['POST'])
def spark_react(msg_id):
    """Отправить искорную реакцию на пост канала."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    amount = max(1, min(int(data.get('amount', 1)), 100))

    msg = GroupMessage.query.get(msg_id)
    if not msg:
        return jsonify({'error': 'Сообщение не найдено'}), 404
    group = Group.query.get(msg.group_id)
    if not group or not group.is_channel:
        return jsonify({'error': 'Только для каналов'}), 400

    # Проверяем дубль — если уже есть, суммируем
    existing = SparkReaction.query.filter_by(sender_id=uid, group_message_id=msg_id).first()

    if not _spend_sparks(uid, amount, 'spark_reaction', msg_id):
        return jsonify({'error': 'Недостаточно искр'}), 400

    if existing:
        existing.amount += amount
        existing.sent_at = datetime.utcnow()
    else:
        sr = SparkReaction(sender_id=uid, group_message_id=msg_id, amount=amount)
        db.session.add(sr)
    db.session.commit()

    # Начисляем владельцу канала
    _add_sparks(group.creator_id, amount, 'spark_received', msg_id)

    total = db.session.query(db.func.sum(SparkReaction.amount)).filter_by(group_message_id=msg_id).scalar() or 0
    socketio.emit('spark_reaction', {'msg_id': msg_id, 'total': total}, room=f'group_{msg.group_id}', namespace='/')
    return jsonify({'success': True, 'total': total})

@app.route('/sparks/react/<int:msg_id>/cancel', methods=['POST'])
def spark_react_cancel(msg_id):
    """Отмена искорной реакции в течение 5 секунд."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    cancel_amount = int(data.get('amount', 0))

    sr = SparkReaction.query.filter_by(sender_id=uid, group_message_id=msg_id).first()
    if not sr:
        return jsonify({'error': 'Реакция не найдена'}), 404
    # Разрешаем отмену только в течение 10 секунд (с запасом)
    age = (datetime.utcnow() - sr.sent_at).total_seconds()
    if age > 10:
        return jsonify({'error': 'Время отмены истекло'}), 400
    msg = GroupMessage.query.get(msg_id)
    group = Group.query.get(msg.group_id) if msg else None
    refund = cancel_amount if cancel_amount > 0 else sr.amount
    # Возвращаем искры отправителю
    _add_sparks(uid, refund, 'spark_reaction_cancel', msg_id)
    # Снимаем с владельца канала
    if group:
        _spend_sparks(group.creator_id, refund, 'spark_reaction_cancel', msg_id)
    # Уменьшаем или удаляем запись
    if sr.amount <= refund:
        db.session.delete(sr)
    else:
        sr.amount -= refund
    db.session.commit()
    total = db.session.query(db.func.sum(SparkReaction.amount)).filter_by(group_message_id=msg_id).scalar() or 0
    if msg:
        socketio.emit('spark_reaction', {'msg_id': msg_id, 'total': total}, room=f'group_{msg.group_id}', namespace='/')
    return jsonify({'success': True, 'total': total})

@app.route('/sparks/post/<int:msg_id>/total')
def spark_post_total(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    total = db.session.query(db.func.sum(SparkReaction.amount)).filter_by(group_message_id=msg_id).scalar() or 0
    return jsonify({'total': total})

@app.route('/sparks/channel/<int:group_id>/withdraw', methods=['POST'])
def channel_spark_withdraw(group_id):
    """Вывод искр владельцем канала."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    group = Group.query.get(group_id)
    if not group or group.creator_id != uid:
        return jsonify({'error': 'Нет доступа'}), 403
    sb = _get_spark_balance(uid)
    if sb.balance < 100:
        return jsonify({'error': 'Для вывода нужно не менее 100 искр'}), 400
    amount = sb.balance
    _spend_sparks(uid, amount, 'withdraw', group_id)
    w = ChannelSparkWithdraw(group_id=group_id, owner_id=uid, amount=amount, status='pending')
    db.session.add(w)
    db.session.commit()
    return jsonify({'success': True, 'amount': amount})

# ═══════════════════════════════════════════════════════════════════════════════
# ПЛАТНЫЕ ПОСТЫ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/channel/<int:group_id>/paid-post', methods=['POST'])
def create_paid_post(group_id):
    """Создать платный пост в канале."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    group = Group.query.get(group_id)
    if not group or not group.is_channel:
        return jsonify({'error': 'Только для каналов'}), 400
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if not member or not member.is_admin:
        return jsonify({'error': 'Нет прав'}), 403

    data = request.form
    price = int(data.get('price_sparks', 10))
    content = encrypt_msg(data.get('content', '').strip())
    if not content:
        return jsonify({'error': 'Пустой пост'}), 400

    # Создаём сообщение с флагом is_paid
    msg = GroupMessage(group_id=group_id, sender_id=uid, content=content)
    msg.is_paid = True
    msg.paid_price = price
    db.session.add(msg)
    db.session.flush()

    # Обрабатываем медиа если есть
    media_files_data = []
    if 'media' in request.files:
        files = request.files.getlist('media')
        for i, f in enumerate(files[:10]):
            if f and f.filename:
                ext = f.filename.rsplit('.', 1)[-1].lower()
                if ext in ALLOWED_IMAGES:
                    mtype = 'image'
                    folder = 'images'
                elif ext in ALLOWED_VIDEO:
                    mtype = 'video'
                    folder = 'videos'
                else:
                    continue
                fname = secure_filename(f'{secrets.token_hex(8)}.{ext}')
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], folder, fname)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                f.save(fpath)
                url = f'/static/media/{folder}/{fname}'
                mm = GroupMessageMedia(message_id=msg.id, media_type=mtype, media_url=url, file_name=f.filename, order_index=i)
                db.session.add(mm)
                media_files_data.append({'media_type': mtype, 'media_url': url})

    pp = PaidPost(group_message_id=msg.id, price_sparks=price)
    db.session.add(pp)
    db.session.commit()

    sender = User.query.get(uid)
    msg_data = {
        'id': msg.id, 'sender_id': uid,
        'sender_name': sender.display_name or sender.username,
        'sender_avatar_color': sender.avatar_color,
        'sender_avatar_letter': sender.get_avatar_letter(),
        'content': content, 'is_paid': True, 'paid_price': price,
        'paid_post_id': pp.id,
        'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
        'timestamp_iso': msg.timestamp.isoformat() + 'Z',
        'media_files': media_files_data
    }
    socketio.emit('new_group_message', {'group_id': group_id, 'message': msg_data}, room=f'group_{group_id}', include_self=True, namespace='/')
    return jsonify({'success': True, 'message': msg_data})

@app.route('/paid-post/<int:post_id>/buy', methods=['POST'])
def buy_paid_post(post_id):
    """Купить доступ к платному посту."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    pp = PaidPost.query.get(post_id)
    if not pp:
        return jsonify({'error': 'Пост не найден'}), 404

    # Проверяем уже куплено
    existing = PaidPostPurchase.query.filter_by(user_id=uid, paid_post_id=post_id).first()
    if existing:
        return jsonify({'already_purchased': True})

    # Владелец канала видит бесплатно
    msg = GroupMessage.query.get(pp.group_message_id)
    group = Group.query.get(msg.group_id)
    if group.creator_id == uid:
        return jsonify({'already_purchased': True})

    if not _spend_sparks(uid, pp.price_sparks, 'post_pay', post_id):
        return jsonify({'error': 'Недостаточно искр'}), 400

    # Начисляем владельцу канала
    _add_sparks(group.creator_id, pp.price_sparks, 'post_income', post_id)

    purchase = PaidPostPurchase(user_id=uid, paid_post_id=post_id)
    db.session.add(purchase)
    db.session.commit()

    # Возвращаем полный контент
    media_files = GroupMessageMedia.query.filter_by(message_id=msg.id).order_by(GroupMessageMedia.order_index).all()
    return jsonify({
        'success': True,
        'content': msg.decrypted_content,
        'media_files': [{'media_type': m.media_type, 'media_url': m.media_url, 'file_name': m.file_name} for m in media_files]
    })

@app.route('/paid-post/<int:post_id>/check')
def check_paid_post(post_id):
    """Проверить, куплен ли пост."""
    if 'user_id' not in session:
        return jsonify({'purchased': False})
    uid = session['user_id']
    pp = PaidPost.query.get(post_id)
    if not pp:
        return jsonify({'purchased': False})
    msg = GroupMessage.query.get(pp.group_message_id)
    group = Group.query.get(msg.group_id)
    if group.creator_id == uid:
        return jsonify({'purchased': True})
    existing = PaidPostPurchase.query.filter_by(user_id=uid, paid_post_id=post_id).first()
    return jsonify({'purchased': bool(existing)})

# ═══════════════════════════════════════════════════════════════════════════════
# QR-КОД ПРОФИЛЯ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/profile/qr')
def profile_qr():
    if 'user_id' not in session:
        return redirect('/login')
    user = User.query.get(session['user_id'])
    base_url = request.host_url.rstrip('/')
    profile_url = f"{base_url}/user/{user.username}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={profile_url}"
    import urllib.request
    try:
        with urllib.request.urlopen(qr_url, timeout=5) as resp:
            img_data = resp.read()
        from flask import Response
        return Response(img_data, mimetype='image/png')
    except Exception:
        return redirect(qr_url)

# ═══════════════════════════════════════════════════════════════════════════════
# ТЕКСТОВЫЙ СТАТУС
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/profile/status', methods=['POST'])
def update_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    user = User.query.get(session['user_id'])
    user.status_text = (data.get('status_text') or data.get('status') or '').strip()[:100]
    db.session.commit()
    socketio.emit('status_updated', {'user_id': user.id, 'status': user.status_text}, namespace='/')
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ТЕМА ПО РАСПИСАНИЮ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/profile/theme-schedule', methods=['GET', 'POST'])
def update_theme_schedule():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    if request.method == 'GET':
        try:
            schedule = json.loads(user.theme_schedule) if user.theme_schedule else None
        except Exception:
            schedule = None
        return jsonify({'schedule': schedule})
    data = request.get_json() or {}
    if data.get('clear'):
        user.theme_schedule = None
    else:
        schedule = {'light_from': data.get('light_from', '08:00'), 'dark_from': data.get('dark_from', '22:00')}
        user.theme_schedule = json.dumps(schedule)
    db.session.commit()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# МЕДЛЕННЫЙ РЕЖИМ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/slow-mode', methods=['POST'])
def set_slow_mode(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if not member or not member.is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    data = request.get_json() or {}
    seconds = int(data.get('seconds', 0))
    group = Group.query.get(group_id)
    group.slow_mode_seconds = max(0, seconds)
    db.session.commit()
    return jsonify({'success': True, 'seconds': group.slow_mode_seconds})

# ═══════════════════════════════════════════════════════════════════════════════
# АНТИСПАМ ПО КЛЮЧЕВЫМ СЛОВАМ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/spam-keywords', methods=['GET', 'POST'])
def group_spam_keywords(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if not member or not member.is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    group = Group.query.get(group_id)
    if request.method == 'GET':
        return jsonify({'keywords': json.loads(group.spam_keywords or '[]')})
    data = request.get_json() or {}
    keywords = [k.strip().lower() for k in data.get('keywords', []) if k.strip()][:50]
    group.spam_keywords = json.dumps(keywords)
    db.session.commit()
    return jsonify({'success': True, 'keywords': keywords})

# ═══════════════════════════════════════════════════════════════════════════════
# ПРИВЕТСТВИЕ НОВЫХ УЧАСТНИКОВ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/welcome-message', methods=['GET', 'POST'])
def group_welcome_message(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if not member or not member.is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    group = Group.query.get(group_id)
    if request.method == 'GET':
        return jsonify({'welcome_message': group.welcome_message or ''})
    data = request.get_json() or {}
    group.welcome_message = (data.get('message') or '').strip()[:500]
    db.session.commit()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# САМОУНИЧТОЖАЮЩИЕСЯ СООБЩЕНИЯ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/message/<int:msg_id>/set-timer', methods=['POST'])
def set_message_timer(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    msg = Message.query.get(msg_id)
    if not msg or msg.sender_id != uid:
        return jsonify({'error': 'Нет доступа'}), 403
    data = request.get_json() or {}
    seconds = int(data.get('seconds', 0))
    if seconds > 0:
        from datetime import timedelta
        msg.expires_at = datetime.utcnow() + timedelta(seconds=seconds)
    else:
        msg.expires_at = None
    db.session.commit()
    return jsonify({'success': True, 'expires_at': msg.expires_at.isoformat() if msg.expires_at else None})

@app.route('/cleanup-expired-messages', methods=['POST'])
def cleanup_expired():
    """Вызывается периодически для удаления истёкших сообщений."""
    expired = Message.query.filter(Message.expires_at <= datetime.utcnow(), Message.is_deleted == False).all()
    for msg in expired:
        msg.is_deleted = True
        msg.content = '[Сообщение удалено]'
        socketio.emit('message_deleted', {'message_id': msg.id, 'other_user_id': msg.receiver_id}, room=f'user_{msg.sender_id}', namespace='/')
        socketio.emit('message_deleted', {'message_id': msg.id, 'other_user_id': msg.sender_id}, room=f'user_{msg.receiver_id}', namespace='/')
    db.session.commit()
    return jsonify({'deleted': len(expired)})

# ═══════════════════════════════════════════════════════════════════════════════
# СКРЫТЫЕ ЧАТЫ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/hidden-chats', methods=['GET'])
def get_hidden_chats():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    hidden = HiddenChat.query.filter_by(user_id=uid).all()
    return jsonify({'hidden': [{'other_user_id': h.other_user_id, 'group_id': h.group_id} for h in hidden]})

@app.route('/hidden-chats/toggle', methods=['POST'])
def toggle_hidden_chat():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    other_user_id = data.get('other_user_id')
    pin = data.get('pin', '')
    user = User.query.get(uid)
    if not user.hidden_chat_pin:
        return jsonify({'error': 'Сначала установите PIN в профиле'}), 400
    if pin != user.hidden_chat_pin:
        return jsonify({'error': 'Неверный PIN'}), 403
    existing = HiddenChat.query.filter_by(user_id=uid, other_user_id=other_user_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'hidden': False})
    hc = HiddenChat(user_id=uid, other_user_id=other_user_id)
    db.session.add(hc)
    db.session.commit()
    return jsonify({'hidden': True})

@app.route('/profile/hidden-chat-pin', methods=['POST'])
def set_hidden_chat_pin():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    pin = str(data.get('pin', '')).strip()
    if not pin.isdigit() or len(pin) != 4:
        return jsonify({'error': 'PIN должен быть 4 цифры'}), 400
    user = User.query.get(session['user_id'])
    user.hidden_chat_pin = pin
    db.session.commit()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ПАПКИ ЧАТОВ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/chat-folders', methods=['GET'])
def get_chat_folders():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    return jsonify({'folders': json.loads(user.chat_folders or '[]')})

@app.route('/chat-folders', methods=['POST'])
def save_chat_folders():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    folders = data.get('folders', [])
    user = User.query.get(session['user_id'])
    user.chat_folders = json.dumps(folders[:20])
    db.session.commit()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ПОИСК ПО СООБЩЕНИЯМ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/search/messages')
def search_messages():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session['user_id']
    q = request.args.get('q', '').strip()
    chat_id = request.args.get('chat_id', type=int)
    group_id = request.args.get('group_id', type=int)
    if not q or len(q) < 2:
        return jsonify({'results': []})
    results = []
    if group_id:
        msgs = GroupMessage.query.filter(
            GroupMessage.group_id == group_id,
            GroupMessage.content.ilike(f'%{q}%'),
            GroupMessage.is_deleted == False
        ).order_by(GroupMessage.timestamp.desc()).limit(30).all()
        for m in msgs:
            results.append({'id': m.id, 'content': m.decrypted_content, 'timestamp': m.timestamp.strftime('%d.%m %H:%M'), 'sender': m.sender.display_name or m.sender.username, 'type': 'group'})
    elif chat_id:
        msgs = Message.query.filter(
            ((Message.sender_id == uid) & (Message.receiver_id == chat_id)) |
            ((Message.sender_id == chat_id) & (Message.receiver_id == uid)),
            Message.content.ilike(f'%{q}%'),
            Message.is_deleted == False
        ).order_by(Message.timestamp.desc()).limit(30).all()
        for m in msgs:
            results.append({'id': m.id, 'content': m.decrypted_content, 'timestamp': m.timestamp.strftime('%d.%m %H:%M'), 'sender': m.sender.display_name or m.sender.username, 'type': 'private'})
    return jsonify({'results': results})

ALLOWED_STICKER_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'json'}

# ═══════════════════════════════════════════════════════════════════════════════
# PAYMENT API (для Telegram-бота оплаты)
# ═══════════════════════════════════════════════════════════════════════════════

PAYMENT_SECRET = os.environ.get('PAYMENT_SECRET', 'tabletone_payment_secret')

@app.route('/api/payment/activate-premium', methods=['POST'])
def payment_activate_premium():
    data = request.get_json() or {}
    if data.get('secret') != PAYMENT_SECRET:
        return jsonify({'error': 'Forbidden'}), 403
    username = data.get('username', '').strip().lstrip('@')
    days = int(data.get('days', 30))
    if not username or days <= 0:
        return jsonify({'error': 'Bad request'}), 400
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': f'Пользователь @{username} не найден'}), 404
    # Продлеваем от текущей даты окончания или от сейчас
    base = user.premium_until if (user.premium_until and user.premium_until > datetime.utcnow()) else datetime.utcnow()
    user.is_premium = True
    user.premium_until = base + timedelta(days=days)
    db.session.commit()
    print(f"✅ Premium активирован для @{username} до {user.premium_until}")
    return jsonify({'success': True, 'username': username, 'days': days, 'until': user.premium_until.isoformat()})

@app.route('/api/payment/add-sparks', methods=['POST'])
def payment_add_sparks():
    data = request.get_json() or {}
    if data.get('secret') != PAYMENT_SECRET:
        return jsonify({'error': 'Forbidden'}), 403
    username = data.get('username', '').strip().lstrip('@')
    sparks = int(data.get('sparks', 0))
    if not username or sparks == 0:
        return jsonify({'error': 'Bad request'}), 400
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': f'Пользователь @{username} не найден'}), 404
    if sparks > 0:
        _add_sparks(user.id, sparks, 'purchase', None)
    else:
        # Снятие искр (отрицательное значение)
        balance = _get_spark_balance(user.id)
        remove = min(abs(sparks), balance)
        if remove > 0:
            _spend_sparks(user.id, remove, 'admin_remove', None)
    print(f"✅ {sparks:+} искр у @{username}")
    return jsonify({'success': True, 'username': username, 'sparks': sparks})

# Подавление ошибок разрыва соединения
import logging
import warnings

# Полностью отключаем логи eventlet
logging.getLogger('eventlet.wsgi.server').setLevel(logging.CRITICAL)
logging.getLogger('eventlet.wsgi').setLevel(logging.CRITICAL)
logging.getLogger('eventlet').setLevel(logging.CRITICAL)

# Подавляем warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

# ── Фоновая задача: снятие Premium по истечении срока ────────────────────────
def _premium_expiry_worker():
    """Каждые 10 минут проверяет истёкшие Premium и снимает их."""
    import time as _time
    while True:
        _time.sleep(600)  # 10 минут
        try:
            with app.app_context():
                expired = User.query.filter(
                    User.is_premium == True,
                    User.premium_until != None,
                    User.premium_until <= datetime.utcnow()
                ).all()
                for u in expired:
                    u.is_premium = False
                    print(f"⏰ Premium истёк у @{u.username}")
                if expired:
                    db.session.commit()
        except Exception as e:
            print(f"Ошибка premium_expiry_worker: {e}")

# Запускаем воркер в фоне через eventlet
eventlet.spawn(_premium_expiry_worker)

# ── Встроенный платёжный Telegram-бот ────────────────────────────────────────
# Платёжный бот запускается отдельным процессом через Procfile (bot: python tg_payment_bot.py)

def _auto_register_telegram_webhook():
    """Авто-регистрация вебхуков обоих Telegram ботов при старте."""
    import time as _t, urllib.request as _ur
    _t.sleep(8)
    site_url = os.environ.get('SITE_URL', '').rstrip('/')
    if not site_url:
        return

    # Бот привязки 2FA
    token_2fa = os.environ.get('TELEGRAM_BOT_TOKEN')
    if token_2fa:
        try:
            url = f"https://api.telegram.org/bot{token_2fa}/setWebhook"
            data = json.dumps({'url': f"{site_url}/telegram/webhook", 'allowed_updates': ['message']}).encode()
            req = _ur.Request(url, data=data, headers={'Content-Type': 'application/json'})
            resp = json.loads(_ur.urlopen(req, timeout=10).read())
            print(f"✅ 2FA webhook: {resp.get('description', resp)}")
        except Exception as e:
            print(f"⚠️ 2FA webhook error: {e}")

    # Платёжный бот
    token_pay = os.environ.get('PAYMENT_BOT_TOKEN', '8705438057:AAEIeyFixNBr3eH4_4NIso57GKXOFvs3E_M')
    if token_pay:
        try:
            url = f"https://api.telegram.org/bot{token_pay}/setWebhook"
            data = json.dumps({'url': f"{site_url}/payment/webhook", 'allowed_updates': ['message', 'callback_query']}).encode()
            req = _ur.Request(url, data=data, headers={'Content-Type': 'application/json'})
            resp = json.loads(_ur.urlopen(req, timeout=10).read())
            print(f"✅ Payment webhook: {resp.get('description', resp)}")
        except Exception as e:
            print(f"⚠️ Payment webhook error: {e}")

import threading as _threading
_wh_thread = _threading.Thread(target=_auto_register_telegram_webhook, daemon=True)
_wh_thread.start()

# ── Стикеры ───────────────────────────────────────────────────────────────────

@app.route('/stickers/my', methods=['GET'])
def stickers_my():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    owned = StickerPack.query.filter_by(creator_id=uid).all()
    added_links = UserStickerPack.query.filter_by(user_id=uid).all()
    added_pack_ids = {l.pack_id for l in added_links}

    def pack_dict(p, is_owner):
        return {
            'id': p.id,
            'name': p.name,
            'cover_url': p.cover_url or (p.stickers[0].image_url if p.stickers else ''),
            'is_owner': is_owner,
            'stickers': [{'id': s.id, 'image_url': s.image_url, 'emoji_hint': s.emoji_hint, 'is_animated': s.image_url.startswith('data:application/json')} for s in p.stickers]
        }

    owned_list = [pack_dict(p, True) for p in owned]
    added_list = []
    for link in added_links:
        p = link.pack
        if p and p.creator_id != uid:
            added_list.append(pack_dict(p, False))

    return jsonify({'owned': owned_list, 'added': added_list})


@app.route('/stickers/pack/<int:pack_id>', methods=['GET'])
def sticker_pack_info(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    p = StickerPack.query.get_or_404(pack_id)
    is_added = UserStickerPack.query.filter_by(user_id=uid, pack_id=pack_id).first() is not None
    return jsonify({
        'id': p.id,
        'name': p.name,
        'cover_url': p.cover_url or (p.stickers[0].image_url if p.stickers else ''),
        'is_owner': p.creator_id == uid,
        'is_added': is_added,
        'stickers': [{'id': s.id, 'image_url': s.image_url, 'emoji_hint': s.emoji_hint, 'is_animated': s.image_url.startswith('data:application/json')} for s in p.stickers]
    })


@app.route('/stickers/pack/create', methods=['POST'])
def sticker_pack_create():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Укажите название'}), 400
    files = request.files.getlist('stickers')
    if not files:
        return jsonify({'error': 'Выберите файлы'}), 400

    pack = StickerPack(name=name, creator_id=uid)
    db.session.add(pack)
    db.session.flush()

    import base64 as _b64stk, os as _os
    sticker_dir = _os.path.join('static', 'media', 'stickers', str(pack.id))
    _os.makedirs(sticker_dir, exist_ok=True)

    for i, f in enumerate(files[:20]):
        ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else ''
        is_animated = ext == 'json'
        if not is_animated and not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        filename = f"sticker_{i}.{ext}"
        filepath = _os.path.join(sticker_dir, filename)
        if is_animated:
            raw = f.read()
            # Сохраняем JSON на диск
            with open(filepath, 'wb') as fh:
                fh.write(raw)
            url = f"/static/media/stickers/{pack.id}/{filename}"
        else:
            f.save(filepath)
            url = f"/static/media/stickers/{pack.id}/{filename}"
        sticker = Sticker(pack_id=pack.id, image_url=url, order_index=i)
        db.session.add(sticker)
        if i == 0:
            pack.cover_url = url

    db.session.commit()
    return jsonify({'success': True, 'pack_id': pack.id})


@app.route('/stickers/pack/<int:pack_id>/add', methods=['POST'])
def sticker_pack_add(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    p = StickerPack.query.get_or_404(pack_id)
    existing = UserStickerPack.query.filter_by(user_id=uid, pack_id=pack_id).first()
    if existing:
        return jsonify({'success': True, 'already_added': True})
    link = UserStickerPack(user_id=uid, pack_id=pack_id)
    db.session.add(link)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/stickers/pack/<int:pack_id>/delete', methods=['POST'])
def sticker_pack_delete(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    p = StickerPack.query.get_or_404(pack_id)
    if p.creator_id != uid:
        # Просто убираем из коллекции
        link = UserStickerPack.query.filter_by(user_id=uid, pack_id=pack_id).first()
        if link:
            db.session.delete(link)
            db.session.commit()
        return jsonify({'success': True})
    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/stickers/send', methods=['POST'])
def sticker_send():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    sticker_id = data.get('sticker_id')
    receiver_id = data.get('receiver_id')
    group_id = data.get('group_id')

    sticker = Sticker.query.get(sticker_id)
    if not sticker:
        return jsonify({'error': 'Стикер не найден'}), 404

    content = f"[sticker]{sticker.image_url}"

    if group_id:
        group = Group.query.get(group_id)
        if not group:
            return jsonify({'error': 'Группа не найдена'}), 404
        member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
        if not member:
            return jsonify({'error': 'Вы не в группе'}), 403
        msg = GroupMessage(group_id=group_id, sender_id=uid, content=content, message_type='sticker')
        db.session.add(msg)
        db.session.commit()
        sender = User.query.get(uid)
        socketio.emit('group_message', {
            'message': {
                'id': msg.id, 'content': content, 'message_type': 'sticker',
                'sender_id': uid, 'group_id': group_id,
                'timestamp': msg.timestamp.strftime('%H:%M'),
                'timestamp_iso': msg.timestamp.isoformat(),
                'sender_name': sender.display_name or sender.username,
                'sticker_pack_id': sticker.pack_id,
            }
        }, room=f'group_{group_id}', namespace='/')
        return jsonify({'success': True, 'message_id': msg.id})
    elif receiver_id:
        receiver = User.query.get(receiver_id)
        if not receiver:
            return jsonify({'error': 'Пользователь не найден'}), 404
        msg = Message(sender_id=uid, receiver_id=receiver_id, content=content, message_type='sticker')
        db.session.add(msg)
        db.session.commit()
        sender = User.query.get(uid)
        sender_info = {
            'id': sender.id,
            'username': sender.username,
            'display_name': sender.display_name or sender.username,
            'avatar_color': sender.avatar_color,
            'avatar_letter': sender.get_avatar_letter(),
            'avatar_url': sender.avatar_url or ''
        }
        for room_uid in [uid, int(receiver_id)]:
            socketio.emit('new_message', {
                'message': {
                    'id': msg.id, 'content': content, 'message_type': 'sticker',
                    'is_mine': room_uid == uid,
                    'timestamp': msg.timestamp.strftime('%H:%M'),
                    'timestamp_iso': msg.timestamp.isoformat(),
                    'sticker_pack_id': sticker.pack_id,
                },
                'other_user_id': int(receiver_id) if room_uid == uid else uid,
                'sender_info': sender_info,
            }, room=f'user_{room_uid}', namespace='/')
        return jsonify({'success': True, 'message_id': msg.id})
    return jsonify({'error': 'Укажите receiver_id или group_id'}), 400


# ── Кастомные реакции ─────────────────────────────────────────────────────────

@app.route('/reactions/my', methods=['GET'])
def reactions_my():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    user = User.query.get(uid)
    if not user.is_premium and not user.is_admin and not user.admin_role:
        return jsonify({'error': 'premium_required'}), 403

    owned = CustomReactionPack.query.filter_by(creator_id=uid).all()
    added_links = UserCustomReactionPack.query.filter_by(user_id=uid).all()

    def pack_dict(p, is_owner):
        return {
            'id': p.id,
            'name': p.name,
            'is_owner': is_owner,
            'reactions': [{'id': r.id, 'image_url': r.image_url, 'name': r.name} for r in p.reactions]
        }

    owned_list = [pack_dict(p, True) for p in owned]
    added_list = []
    for link in added_links:
        p = link.pack
        if p and p.creator_id != uid:
            added_list.append(pack_dict(p, False))

    return jsonify({'owned': owned_list, 'added': added_list})


@app.route('/reactions/pack/<int:pack_id>', methods=['GET'])
def reaction_pack_info(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    p = CustomReactionPack.query.get_or_404(pack_id)
    is_added = UserCustomReactionPack.query.filter_by(user_id=uid, pack_id=pack_id).first() is not None
    return jsonify({
        'id': p.id,
        'name': p.name,
        'is_owner': p.creator_id == uid,
        'is_added': is_added,
        'reactions': [{'id': r.id, 'image_url': r.image_url, 'name': r.name} for r in p.reactions]
    })


@app.route('/reactions/pack/create', methods=['POST'])
def reaction_pack_create():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    user = User.query.get(uid)
    if not user.is_premium and not user.is_admin and not user.admin_role:
        return jsonify({'error': 'premium_required', 'message': 'Кастомные реакции доступны только для Premium'}), 403

    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Укажите название'}), 400
    files = request.files.getlist('reactions')
    if not files:
        return jsonify({'error': 'Выберите файлы'}), 400

    pack = CustomReactionPack(name=name, creator_id=uid)
    db.session.add(pack)
    db.session.flush()

    upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'reactions', str(pack.id))
    os.makedirs(upload_dir, exist_ok=True)

    for i, f in enumerate(files[:20]):
        if not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        ext = f.filename.rsplit('.', 1)[1].lower()
        fname = f"{secrets.token_hex(8)}.{ext}"
        fpath = os.path.join(upload_dir, fname)
        f.save(fpath)
        url = f"/static/media/reactions/{pack.id}/{fname}"
        reaction = CustomReaction(pack_id=pack.id, image_url=url, order_index=i)
        db.session.add(reaction)

    db.session.commit()
    return jsonify({'success': True, 'pack_id': pack.id})


@app.route('/reactions/pack/<int:pack_id>/add', methods=['POST'])
def reaction_pack_add(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    user = User.query.get(uid)
    if not user.is_premium and not user.is_admin and not user.admin_role:
        return jsonify({'error': 'premium_required'}), 403
    CustomReactionPack.query.get_or_404(pack_id)
    existing = UserCustomReactionPack.query.filter_by(user_id=uid, pack_id=pack_id).first()
    if existing:
        return jsonify({'success': True, 'already_added': True})
    link = UserCustomReactionPack(user_id=uid, pack_id=pack_id)
    db.session.add(link)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/reactions/pack/<int:pack_id>/delete', methods=['POST'])
def reaction_pack_delete(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    p = CustomReactionPack.query.get_or_404(pack_id)
    if p.creator_id != uid:
        link = UserCustomReactionPack.query.filter_by(user_id=uid, pack_id=pack_id).first()
        if link:
            db.session.delete(link)
            db.session.commit()
        return jsonify({'success': True})
    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})


# ── Поиск пака стикеров по URL стикера ───────────────────────────────────────

@app.route('/stickers/find_by_url', methods=['GET'])
def sticker_find_by_url():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    url = request.args.get('url', '')
    sticker = Sticker.query.filter_by(image_url=url).first()
    if not sticker:
        return jsonify({'error': 'Не найдено'}), 404
    return jsonify({'pack_id': sticker.pack_id})


# ── Web Push уведомления ──────────────────────────────────────────────────────
import json as _json

# Генерируем VAPID ключи при первом запуске (храним в env или файле)
_VAPID_PRIVATE = os.environ.get('VAPID_PRIVATE_KEY', '')
_VAPID_PUBLIC  = os.environ.get('VAPID_PUBLIC_KEY', '')

class PushSubscription(db.Model):
    """Web Push подписка пользователя."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text)
    auth = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id])

# ── WebRTC звонки ─────────────────────────────────────────────────────────────

@socketio.on('call_offer')
def handle_call_offer(data):
    """Инициатор отправляет offer получателю."""
    if 'user_id' not in session:
        return
    to_user_id = data.get('to_user_id')
    caller = User.query.get(session['user_id'])
    if not caller or not to_user_id:
        return
    # Проверяем приватность звонков
    callee = User.query.get(to_user_id)
    if callee and not (caller.is_admin or caller.admin_role):
        if not _privacy_allows(callee, caller.id, 'privacy_who_can_call'):
            socketio.emit('call_rejected', {'from_user_id': to_user_id, 'reason': 'privacy'},
                          room=f'user_{caller.id}', namespace='/')
            return
    socketio.emit('call_incoming', {
        'from_user_id': session['user_id'],
        'from_name': caller.display_name or caller.username,
        'from_avatar_letter': caller.get_avatar_letter(),
        'from_avatar_color': caller.avatar_color,
        'sdp': data.get('sdp'),
        'is_video': data.get('is_video', False)
    }, room=f'user_{to_user_id}', namespace='/')
    call_type = 'Видеозвонок' if data.get('is_video') else 'Звонок'
    _send_fcm(to_user_id, f'{call_type} от {caller.display_name or caller.username}', 'Нажмите чтобы ответить', 'call')

@socketio.on('call_answer')
def handle_call_answer(data):
    """Получатель принял звонок, отправляет answer инициатору."""
    if 'user_id' not in session:
        return
    to_user_id = data.get('to_user_id')
    socketio.emit('call_answered', {
        'from_user_id': session['user_id'],
        'sdp': data.get('sdp')
    }, room=f'user_{to_user_id}', namespace='/')

@socketio.on('call_ice')
def handle_call_ice(data):
    """Передача ICE candidate между участниками."""
    if 'user_id' not in session:
        return
    to_user_id = data.get('to_user_id')
    socketio.emit('call_ice', {
        'from_user_id': session['user_id'],
        'candidate': data.get('candidate')
    }, room=f'user_{to_user_id}', namespace='/')

@socketio.on('call_end')
def handle_call_end(data):
    """Завершение звонка."""
    if 'user_id' not in session:
        return
    to_user_id = data.get('to_user_id')
    socketio.emit('call_ended', {
        'from_user_id': session['user_id']
    }, room=f'user_{to_user_id}', namespace='/')

@socketio.on('call_reject')
def handle_call_reject(data):
    """Отклонение входящего звонка."""
    if 'user_id' not in session:
        return
    to_user_id = data.get('to_user_id')
    socketio.emit('call_rejected', {
        'from_user_id': session['user_id']
    }, room=f'user_{to_user_id}', namespace='/')

@app.route('/chat/<int:other_id>/clear', methods=['POST'])
def clear_chat_history(other_id):
    """Удаляет историю чата. Если both_sides=True — у обоих пользователей."""
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    both_sides = data.get('both_sides', False)
    msgs = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == uid, Message.receiver_id == other_id),
            db.and_(Message.sender_id == other_id, Message.receiver_id == uid)
        )
    ).all()
    for msg in msgs:
        if both_sides:
            msg.hidden_for_sender = True
            msg.hidden_for_receiver = True
        else:
            if uid == msg.sender_id:
                msg.hidden_for_sender = True
            else:
                msg.hidden_for_receiver = True
    db.session.commit()
    # Уведомляем собеседника через сокет если удаляем у обоих
    if both_sides:
        socketio.emit('chat_history_cleared', {'by_user_id': uid}, room=f'user_{other_id}')
    return jsonify({'success': True, 'cleared': len(msgs)})

with app.app_context():
    db.create_all()
    # Auto-migrate: expand column types
    try:
        from sqlalchemy import text as _text
        with db.engine.connect() as _conn:
            _conn.execute(_text('ALTER TABLE sticker ALTER COLUMN image_url TYPE TEXT'))
            _conn.execute(_text('ALTER TABLE sticker_pack ALTER COLUMN cover_url TYPE TEXT'))
            _conn.commit()
    except Exception:
        pass

@app.route('/push/vapid-key')
def push_vapid_key():
    return jsonify({'public_key': _VAPID_PUBLIC or 'not_configured'})

@app.route('/push/subscribe', methods=['POST'])
def push_subscribe():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    endpoint = data.get('endpoint', '')
    keys = data.get('keys', {})
    if not endpoint:
        return jsonify({'error': 'No endpoint'}), 400
    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if not existing:
        sub = PushSubscription(
            user_id=session['user_id'],
            endpoint=endpoint,
            p256dh=keys.get('p256dh', ''),
            auth=keys.get('auth', '')
        )
        db.session.add(sub)
        db.session.commit()
    return jsonify({'success': True})

def _send_push_notification(user_id, title, body, url='/'):
    """Отправляет Web Push уведомление пользователю."""
    if not _VAPID_PRIVATE or not _VAPID_PUBLIC:
        return
    try:
        from pywebpush import webpush, WebPushException
        subs = PushSubscription.query.filter_by(user_id=user_id).all()
        payload = _json.dumps({'title': title, 'body': body, 'url': url})
        for sub in subs:
            try:
                webpush(
                    subscription_info={'endpoint': sub.endpoint, 'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}},
                    data=payload,
                    vapid_private_key=_VAPID_PRIVATE,
                    vapid_claims={'sub': 'mailto:support@tabletone.app'}
                )
            except WebPushException as e:
                if '410' in str(e) or '404' in str(e):
                    db.session.delete(sub)
                    db.session.commit()
    except ImportError:
        pass  # pywebpush не установлен — тихо пропускаем

# ── Превью ссылок ─────────────────────────────────────────────────────────────
@app.route('/link-preview')
def link_preview():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    url = request.args.get('url', '').strip()
    if not url or not url.startswith('http'):
        return jsonify({'error': 'Invalid URL'}), 400
    try:
        import urllib.request
        from html.parser import HTMLParser

        class OGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.og = {}
                self.title = ''
                self._in_title = False
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'meta':
                    prop = attrs.get('property', '') or attrs.get('name', '')
                    content = attrs.get('content', '')
                    if prop in ('og:title', 'og:description', 'og:image', 'og:url', 'twitter:title', 'twitter:description', 'twitter:image'):
                        key = prop.replace('og:', '').replace('twitter:', '')
                        if key not in self.og:
                            self.og[key] = content
                if tag == 'title':
                    self._in_title = True
            def handle_data(self, data):
                if self._in_title and not self.og.get('title'):
                    self.title += data
            def handle_endtag(self, tag):
                if tag == 'title':
                    self._in_title = False

        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read(32768).decode('utf-8', errors='ignore')

        parser = OGParser()
        parser.feed(html)
        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        preview = {
            'title': parser.og.get('title') or parser.title.strip() or domain,
            'description': parser.og.get('description', ''),
            'image': parser.og.get('image', ''),
            'url': parser.og.get('url') or url,
            'domain': domain
        }
        return jsonify({'preview': preview})
    except Exception:
        return jsonify({'preview': None})




# ═══════════════════════════════════════════════════════════════════════════════
# БЛОКИРОВКА ПОЛЬЗОВАТЕЛЕЙ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/user/<int:target_id>/block', methods=['POST'])
def block_user(target_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    if uid == target_id:
        return jsonify({'error': 'Нельзя заблокировать себя'}), 400
    existing = BlockedUser.query.filter_by(user_id=uid, blocked_id=target_id).first()
    if existing:
        return jsonify({'already': True})
    b = BlockedUser(user_id=uid, blocked_id=target_id)
    db.session.add(b)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/user/<int:target_id>/unblock', methods=['POST'])
def unblock_user(target_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    BlockedUser.query.filter_by(user_id=session['user_id'], blocked_id=target_id).delete()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/user/<int:target_id>/block_status')
def block_status(target_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    i_blocked = BlockedUser.query.filter_by(user_id=uid, blocked_id=target_id).first() is not None
    they_blocked = BlockedUser.query.filter_by(user_id=target_id, blocked_id=uid).first() is not None
    return jsonify({'i_blocked': i_blocked, 'they_blocked': they_blocked})

@app.route('/user/blocked_list')
def blocked_list():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    blocks = BlockedUser.query.filter_by(user_id=session['user_id']).all()
    result = []
    for b in blocks:
        u = User.query.get(b.blocked_id)
        if u:
            result.append({'id': u.id, 'username': u.username,
                           'display_name': u.display_name or u.username,
                           'avatar_color': u.avatar_color})
    return jsonify({'blocked': result})


# ═══════════════════════════════════════════════════════════════════════════════
# РОЛИ В ГРУППАХ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/roles', methods=['GET'])
def get_group_roles(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    roles = GroupRole.query.filter_by(group_id=group_id).order_by(GroupRole.order_index).all()
    return jsonify({'roles': [{'id': r.id, 'name': r.name, 'color': r.color} for r in roles]})

@app.route('/groups/<int:group_id>/roles', methods=['POST'])
def create_group_role(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get_or_404(group_id)
    if group.creator_id != session['user_id']:
        member = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id'], is_admin=True).first()
        if not member:
            return jsonify({'error': 'Нет прав'}), 403
    data = request.get_json()
    role = GroupRole(group_id=group_id, name=data.get('name','Роль')[:50],
                     color=data.get('color','#667eea'))
    db.session.add(role)
    db.session.commit()
    return jsonify({'success': True, 'id': role.id})

@app.route('/groups/<int:group_id>/roles/<int:role_id>', methods=['DELETE'])
def delete_group_role(group_id, role_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get_or_404(group_id)
    if group.creator_id != session['user_id']:
        return jsonify({'error': 'Нет прав'}), 403
    GroupRole.query.filter_by(id=role_id, group_id=group_id).delete()
    GroupMemberRole.query.filter_by(role_id=role_id).delete()
    db.session.commit()
    return jsonify({'success': True})


# ═══════════════════════════════════════════════════════════════════════════════
# СЕКРЕТНЫЕ ЧАТЫ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/secret/start/<int:other_id>', methods=['POST'])
def start_secret_chat(other_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    # Проверяем существующий активный чат
    chat = SecretChat.query.filter(
        ((SecretChat.user1_id == uid) & (SecretChat.user2_id == other_id)) |
        ((SecretChat.user1_id == other_id) & (SecretChat.user2_id == uid)),
        SecretChat.is_active == True
    ).first()
    if not chat:
        chat = SecretChat(user1_id=uid, user2_id=other_id)
        db.session.add(chat)
        db.session.commit()
        # Уведомляем второго пользователя
        socketio.emit('secret_chat_invite', {
            'chat_id': chat.id,
            'from_user': {'id': uid, 'username': User.query.get(uid).username}
        }, room=f'user_{other_id}')
    return jsonify({'success': True, 'chat_id': chat.id})

@app.route('/secret/<int:chat_id>/messages')
def get_secret_messages(chat_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    chat = SecretChat.query.get_or_404(chat_id)
    if uid not in (chat.user1_id, chat.user2_id):
        return jsonify({'error': 'Нет доступа'}), 403
    msgs = SecretMessage.query.filter_by(chat_id=chat_id).order_by(SecretMessage.timestamp).all()
    # Помечаем как прочитанные
    for m in msgs:
        if m.sender_id != uid and not m.is_read:
            m.is_read = True
    db.session.commit()
    return jsonify({'messages': [{
        'id': m.id, 'sender_id': m.sender_id,
        'content': m.content_encrypted,
        'timestamp': m.timestamp.strftime('%H:%M'),
        'self_destruct': m.self_destruct_seconds
    } for m in msgs]})

@app.route('/secret/<int:chat_id>/send', methods=['POST'])
def send_secret_message(chat_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    chat = SecretChat.query.get_or_404(chat_id)
    if uid not in (chat.user1_id, chat.user2_id):
        return jsonify({'error': 'Нет доступа'}), 403
    data = request.get_json()
    content = encrypt_msg(data.get('content', '').strip())
    if not content:
        return jsonify({'error': 'Пустое сообщение'}), 400
    self_destruct = int(data.get('self_destruct', 0))
    msg = SecretMessage(chat_id=chat_id, sender_id=uid,
                        content_encrypted=content,
                        self_destruct_seconds=self_destruct)
    db.session.add(msg)
    db.session.commit()
    other_id = chat.user2_id if uid == chat.user1_id else chat.user1_id
    sender = User.query.get(uid)
    socketio.emit('secret_message', {
        'chat_id': chat_id, 'id': msg.id,
        'sender_id': uid, 'content': content,
        'timestamp': msg.timestamp.strftime('%H:%M'),
        'self_destruct': self_destruct,
        'sender_name': sender.display_name or sender.username
    }, room=f'user_{other_id}')
    return jsonify({'success': True, 'id': msg.id})

@app.route('/secret/<int:chat_id>/close', methods=['POST'])
def close_secret_chat(chat_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    chat = SecretChat.query.get_or_404(chat_id)
    if uid not in (chat.user1_id, chat.user2_id):
        return jsonify({'error': 'Нет доступа'}), 403
    # Удаляем все сообщения и закрываем чат
    SecretMessage.query.filter_by(chat_id=chat_id).delete()
    chat.is_active = False
    db.session.commit()
    other_id = chat.user2_id if uid == chat.user1_id else chat.user1_id
    socketio.emit('secret_chat_closed', {'chat_id': chat_id}, room=f'user_{other_id}')
    return jsonify({'success': True})


# ═══════════════════════════════════════════════════════════════════════════════
# МАГАЗИН СТИКЕРОВ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/stickers/shop')
def sticker_shop():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    packs = StickerPack.query.all()
    my_pack_ids = {usp.pack_id for usp in UserStickerPack.query.filter_by(user_id=uid).all()}
    result = []
    for p in packs:
        stickers = Sticker.query.filter_by(pack_id=p.id).order_by(Sticker.order_index).limit(4).all()
        # Не отдаём base64 в preview — только file-path URLs (data: URLs слишком большие)
        preview_urls = [s.image_url for s in stickers if s.image_url and not s.image_url.startswith('data:')]
        result.append({
            'id': p.id, 'name': p.name,
            'cover_url': p.cover_url if p.cover_url and not p.cover_url.startswith('data:') else None,
            'sticker_count': Sticker.query.filter_by(pack_id=p.id).count(),
            'preview': preview_urls,
            'added': p.id in my_pack_ids
        })
    return jsonify({'packs': result})


# ═══════════════════════════════════════════════════════════════════════════════
# МАГАЗИН ПОДАРКОВ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/gifts/shop')
def gift_shop():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    balance_obj = SparkBalance.query.filter_by(user_id=uid).first()
    balance = balance_obj.balance if balance_obj else 0
    gifts = GiftType.query.filter_by(is_active=True).all()
    return jsonify({
        'balance': balance,
        'gifts': [{
            'id': g.id, 'name': g.name, 'emoji': g.emoji,
            'description': g.description, 'price': g.price_sparks,
            'rarity': g.rarity
        } for g in gifts]
    })

@app.route('/gifts/send', methods=['POST'])
def send_gift():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json()
    gift_type_id = data.get('gift_type_id')
    recipient_id = data.get('recipient_id')
    if not gift_type_id or not recipient_id:
        return jsonify({'error': 'Неверные данные'}), 400
    gift_type = GiftType.query.get(gift_type_id)
    if not gift_type or not gift_type.is_active:
        return jsonify({'error': 'Подарок не найден'}), 404
    recipient = User.query.get(recipient_id)
    if not recipient:
        return jsonify({'error': 'Пользователь не найден'}), 404
    # Проверяем баланс
    balance_obj = SparkBalance.query.filter_by(user_id=uid).first()
    if not balance_obj or balance_obj.balance < gift_type.price_sparks:
        return jsonify({'error': 'Недостаточно искр'}), 402
    # Списываем искры
    balance_obj.balance -= gift_type.price_sparks
    db.session.add(SparkTransaction(user_id=uid, amount=-gift_type.price_sparks,
                                    reason='gift_send', ref_id=gift_type_id))
    # Создаём подарок
    user_gift = UserGift(owner_id=recipient_id, gift_type_id=gift_type_id, sender_id=uid)
    db.session.add(user_gift)
    db.session.commit()
    # Отправляем сообщение в чат
    sender = User.query.get(uid)
    gift_msg_content = f"__GIFT__{gift_type_id}"
    msg = Message(sender_id=uid, receiver_id=recipient_id,
                  content=gift_msg_content, message_type='gift')
    db.session.add(msg)
    db.session.commit()
    # Socket уведомление — формат совместимый с handleNewMessage
    sender_info = {
        'id': sender.id, 'username': sender.username,
        'display_name': sender.display_name or sender.username,
        'avatar_color': sender.avatar_color,
        'avatar_letter': sender.get_avatar_letter(),
        'avatar_url': sender.avatar_url,
    }
    gift_data = {
        'id': gift_type.id, 'name': gift_type.name,
        'emoji': gift_type.emoji, 'price': gift_type.price_sparks,
        'rarity': gift_type.rarity, 'description': gift_type.description,
        'user_gift_id': user_gift.id
    }
    msg_payload = {
        'id': msg.id, 'sender_id': uid,
        'content': gift_msg_content, 'message_type': 'gift',
        'timestamp': msg.timestamp.strftime('%H:%M %d.%m'),
        'timestamp_iso': msg.timestamp.isoformat() + 'Z',
        'media_url': None, 'media_files': [], 'duration': None,
        'is_deleted': False, 'is_read': False, 'reply_to': None,
        'bot_buttons': [], 'sticker_pack_id': None,
        'gift': gift_data,
    }
    socketio.emit('new_message', {
        'message': {**msg_payload, 'is_mine': False},
        'other_user_id': uid,
        'sender_info': sender_info,
    }, room=f'user_{recipient_id}', namespace='/')
    socketio.emit('new_message', {
        'message': {**msg_payload, 'is_mine': True},
        'other_user_id': recipient_id,
        'sender_info': sender_info,
    }, room=f'user_{uid}', namespace='/')
    return jsonify({'success': True, 'new_balance': balance_obj.balance})

@app.route('/gifts/my')
def my_gifts():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    gifts = UserGift.query.filter_by(owner_id=uid).order_by(UserGift.received_at.desc()).all()
    result = []
    for g in gifts:
        gt = GiftType.query.get(g.gift_type_id)
        sender = User.query.get(g.sender_id) if g.sender_id else None
        result.append({
            'id': g.id,
            'name': gt.name, 'emoji': gt.emoji,
            'rarity': gt.rarity, 'price_sparks': gt.price_sparks,
            'sender_name': (sender.display_name or sender.username) if sender else None,
            'sender_username': sender.username if sender else None,
            'is_displayed': g.is_displayed,
            'received_at': g.received_at.strftime('%d.%m.%Y') if g.received_at else '',
        })
    return jsonify({'gifts': result})

@app.route('/gifts/<int:gift_id>/display', methods=['POST'])
def toggle_gift_display(gift_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    gift = UserGift.query.filter_by(id=gift_id, owner_id=session['user_id']).first()
    if not gift:
        return jsonify({'error': 'Не найдено'}), 404
    gift.is_displayed = not gift.is_displayed
    db.session.commit()
    return jsonify({'success': True, 'displayed': gift.is_displayed})

@app.route('/gifts/info/<int:gift_type_id>')
def gift_info(gift_type_id):
    g = GiftType.query.get_or_404(gift_type_id)
    total_sent = UserGift.query.filter_by(gift_type_id=gift_type_id).count()
    rarity_labels = {'common': 'Обычный', 'rare': 'Редкий', 'epic': 'Эпический', 'legendary': 'Легендарный'}
    return jsonify({
        'id': g.id, 'name': g.name, 'emoji': g.emoji,
        'description': g.description, 'price': g.price_sparks,
        'rarity': g.rarity, 'rarity_label': rarity_labels.get(g.rarity, g.rarity),
        'total_sent': total_sent
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ПРЕДПРОСМОТР ССЫЛОК
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/link_preview')
def api_link_preview():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    url = request.args.get('url', '').strip()
    if not url or not url.startswith('http'):
        return jsonify({'error': 'Неверный URL'}), 400
    try:
        import urllib.request as _ur
        import html as _html
        import re as _re
        req = _ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with _ur.urlopen(req, timeout=5) as resp:
            raw = resp.read(65536).decode('utf-8', errors='ignore')
        def _meta(name):
            patterns = [
                '<meta[^>]+property=["\']og:' + name + '["\'][^>]+content=["\']([^"\']+)["\'][^>]*/?>',
                '<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:' + name + '["\'][^>]*/?>',
                '<meta[^>]+name=["\']' + name + '["\'][^>]+content=["\']([^"\']+)["\'][^>]*/?>',
            ]
            for pat in patterns:
                m = _re.search(pat, raw, _re.IGNORECASE)
                if m:
                    return _html.unescape(m.group(1).strip())
            return None
        title_match = _re.search(r'<title[^>]*>([^<]+)</title>', raw, _re.IGNORECASE)
        title = _meta('title') or (title_match.group(1) if title_match else None)
        description = _meta('description')
        image = _meta('image')
        site_name = _meta('site_name')
        return jsonify({
            'url': url, 'title': title, 'description': description,
            'image': image, 'site_name': site_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ГОЛОСОВЫЕ СООБЩЕНИЯ (upload endpoint)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/upload/voice', methods=['POST'])
def upload_voice():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'Нет файла'}), 400
    f = request.files['file']
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'webm'
    if ext not in ALLOWED_AUDIO:
        ext = 'webm'
    fname = f"voice_{secrets.token_hex(8)}.{ext}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'voice', fname)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    f.save(save_path)
    return jsonify({'url': f'/static/media/voice/{fname}'})

# ═══════════════════════════════════════════════════════════════════════════════
# МЕДЛЕННЫЙ РЕЖИМ — проверка
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/slow_mode_check')
def slow_mode_check(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    group = Group.query.get_or_404(group_id)
    if not group.slow_mode_seconds:
        return jsonify({'allowed': True, 'wait': 0})
    tracker = SlowModeTracker.query.filter_by(group_id=group_id, user_id=uid).first()
    if not tracker:
        return jsonify({'allowed': True, 'wait': 0})
    elapsed = (datetime.utcnow() - tracker.last_message_at).total_seconds()
    wait = max(0, group.slow_mode_seconds - elapsed)
    return jsonify({'allowed': wait == 0, 'wait': int(wait)})

# ═══════════════════════════════════════════════════════════════════════════════
# ЗАКРЕПЛЁННЫЕ СООБЩЕНИЯ В ГРУППАХ
# ═══════════════════════════════════════════════════════════════════════════════

# generate_invite_link removed — handled by group_invite_link above


# ══════════════════════════════════════════════════════════════════════════════
# РОЛИ В ГРУППАХ
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/groups/<int:group_id>/members/<int:member_id>/role', methods=['POST'])
def assign_member_role(group_id, member_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    group = Group.query.get_or_404(group_id)
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    is_admin = group.creator_id == uid or (member and member.is_admin)
    if not is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    data = request.get_json()
    role_id = data.get('role_id')
    # Remove existing role
    GroupMemberRole.query.filter_by(group_id=group_id, user_id=member_id).delete()
    if role_id:
        mr = GroupMemberRole(group_id=group_id, user_id=member_id, role_id=role_id)
        db.session.add(mr)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/members_with_roles', methods=['GET'])
def get_members_with_roles(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    members = GroupMember.query.filter_by(group_id=group_id).all()
    result = []
    for m in members:
        user = User.query.get(m.user_id)
        if not user:
            continue
        mr = GroupMemberRole.query.filter_by(group_id=group_id, user_id=m.user_id).first()
        role = GroupRole.query.get(mr.role_id) if mr else None
        result.append({
            'user_id': user.id,
            'username': user.username,
            'display_name': user.display_name or user.username,
            'avatar_color': user.avatar_color,
            'avatar_url': user.avatar_url,
            'is_admin': m.is_admin,
            'role': {'id': role.id, 'name': role.name, 'color': role.color} if role else None
        })
    return jsonify({'members': result})

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/admin/test_cf_image')
def test_cf_image():
    """Debug: test Cloudflare image generation directly"""
    if 'user_id' not in session:
        return jsonify({'error': 'not logged in'}), 401
    user = User.query.get(session['user_id'])
    if not user or not user.is_admin:
        return jsonify({'error': 'admin only'}), 403
    import urllib.request, urllib.error, json as _json, os as _os
    account_id = _os.environ.get('CF_ACCOUNT_ID', '')
    api_token = _os.environ.get('CF_API_TOKEN', '')
    if not account_id or not api_token:
        return jsonify({'error': 'CF_ACCOUNT_ID or CF_API_TOKEN not set'})
    payload = _json.dumps({"prompt": "a lion", "num_steps": 5}).encode('utf-8')
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ct = resp.headers.get('Content-Type', '')
            data = resp.read()
            return jsonify({'status': 'ok', 'content_type': ct, 'bytes': len(data), 'first_bytes': data[:20].hex()})
    except urllib.error.HTTPError as he:
        body = he.read().decode('utf-8', errors='replace')
        return jsonify({'status': 'http_error', 'code': he.code, 'body': body[:500]})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})







# ── Admin NFT API ─────────────────────────────────────────────────────────────
@app.route('/admin/nft/collections', methods=['GET'])
def admin_nft_list():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    u = User.query.get(session['user_id'])
    if not u or not u.is_admin: return jsonify({'error': 'Forbidden'}), 403
    import json as _j
    cols = NFTCollection.query.order_by(NFTCollection.id).all()
    result = []
    for c in cols:
        minted = NFTItem.query.filter_by(collection_id=c.id, is_minted=True).count()
        total_items = NFTItem.query.filter_by(collection_id=c.id).count()
        result.append({
            'id': c.id, 'name': c.name, 'description': c.description,
            'total_supply': c.total_supply, 'minted': minted, 'items_generated': total_items,
            'price_sparks': c.price_sparks, 'image_url': c.image_url,
            'bg_color': c.bg_color, 'is_active': c.is_active,
        })
    return jsonify({'collections': result})

@app.route('/admin/nft/collections', methods=['POST'])
def admin_nft_create():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    u = User.query.get(session['user_id'])
    if not u or u.admin_role not in ('owner', 'senior_admin'): return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    col = NFTCollection(
        name=data.get('name', 'New Collection'),
        description=data.get('description', ''),
        total_supply=int(data.get('total_supply', 1000)),
        price_sparks=int(data.get('price_sparks', 100)),
        image_url=data.get('image_url', ''),
        bg_color=data.get('bg_color', '#2d3748'),
        is_active=data.get('is_active', True),
    )
    db.session.add(col)
    db.session.flush()
    # Генерируем первые 50 экземпляров
    import json as _j, random as _r
    attrs_template = data.get('attributes', [])
    for sn in range(1, min(51, col.total_supply + 1)):
        attrs = {}
        for at in attrs_template:
            vals = [v for v, _ in at.get('values', [('Default', 100)])]
            weights = [w for _, w in at.get('values', [('Default', 100)])]
            attrs[at['trait']] = _r.choices(vals, weights=weights, k=1)[0]
        item = NFTItem(collection_id=col.id, serial_number=sn,
                       attributes=_j.dumps(attrs, ensure_ascii=False),
                       value_sparks=col.price_sparks)
        db.session.add(item)
    db.session.commit()
    return jsonify({'success': True, 'id': col.id})

@app.route('/admin/nft/collections/<int:cid>', methods=['PUT'])
def admin_nft_update(cid):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    u = User.query.get(session['user_id'])
    if not u or u.admin_role not in ('owner', 'senior_admin'): return jsonify({'error': 'Forbidden'}), 403
    col = NFTCollection.query.get_or_404(cid)
    data = request.get_json() or {}
    for field in ('name', 'description', 'price_sparks', 'image_url', 'bg_color', 'is_active', 'total_supply'):
        if field in data:
            setattr(col, field, data[field])
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/nft/collections/<int:cid>', methods=['DELETE'])
def admin_nft_delete(cid):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    u = User.query.get(session['user_id'])
    if not u or u.admin_role != 'owner': return jsonify({'error': 'Forbidden'}), 403
    col = NFTCollection.query.get_or_404(cid)
    # Удаляем UserNFT → NFTItem → NFTCollection
    for item in NFTItem.query.filter_by(collection_id=cid).all():
        UserNFT.query.filter_by(nft_item_id=item.id).delete()
    NFTItem.query.filter_by(collection_id=cid).delete()
    db.session.delete(col)
    db.session.commit()
    return jsonify({'success': True})

# ── Gift Premium ──────────────────────────────────────────────────────────────
PREMIUM_GIFT_PLANS = {
    'premium_gift_90':  {'label': 'Premium 3 месяца', 'days': 90,  'price_sparks': 1500, 'price_rub': '299 ₽'},
    'premium_gift_180': {'label': 'Premium 6 месяцев', 'days': 180, 'price_sparks': 2500, 'price_rub': '499 ₽'},
    'premium_gift_365': {'label': 'Premium 1 год',     'days': 365, 'price_sparks': 4000, 'price_rub': '799 ₽'},
}

@app.route('/gifts/premium-plans')
def gift_premium_plans():
    return jsonify({'plans': [
        {'key': k, 'label': v['label'], 'days': v['days'],
         'price_sparks': v['price_sparks'], 'price_rub': v['price_rub']}
        for k, v in PREMIUM_GIFT_PLANS.items()
    ]})

@app.route('/gifts/send-premium', methods=['POST'])
def gift_send_premium():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    data = request.get_json() or {}
    plan_key = data.get('plan_key')
    recipient_id = data.get('recipient_id')
    plan = PREMIUM_GIFT_PLANS.get(plan_key)
    if not plan or not recipient_id:
        return jsonify({'error': 'Неверные данные'}), 400
    recipient = User.query.get(recipient_id)
    if not recipient:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if recipient.id == uid:
        return jsonify({'error': 'Нельзя дарить себе'}), 400
    # Списываем искры
    if not _spend_sparks(uid, plan['price_sparks'], 'premium_gift', recipient_id):
        return jsonify({'error': 'Недостаточно искр', 'required': plan['price_sparks']}), 402
    # Активируем Premium
    now = datetime.utcnow()
    base = recipient.premium_until if recipient.premium_until and recipient.premium_until > now else now
    recipient.is_premium = True
    recipient.premium_until = base + timedelta(days=plan['days'])
    db.session.commit()
    # Уведомление получателю
    try:
        sender = User.query.get(uid)
        sender_name = sender.display_name or sender.username if sender else 'Кто-то'
        msg = Message(
            sender_id=uid, receiver_id=recipient_id,
            content=encrypt_msg(f'🎁 {sender_name} подарил тебе {plan["label"]}! Premium активен до {recipient.premium_until.strftime("%d.%m.%Y")} 👑'),
            message_type='text'
        )
        db.session.add(msg)
        db.session.commit()
        socketio.emit('new_message', {
            'message': {'id': msg.id, 'sender_id': uid, 'content': msg.decrypted_content,
                        'timestamp': msg.timestamp.strftime('%H:%M %d.%m'), 'is_mine': False},
            'other_user_id': uid,
        }, room=f'user_{recipient_id}', namespace='/')
    except Exception:
        pass
    return jsonify({'success': True, 'premium_until': recipient.premium_until.isoformat()})

# ═══════════════════════════════════════════════════════════════════════════════
# NFT КОЛЛЕКЦИОННЫЕ ПРЕДМЕТЫ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/nft/collections')
def nft_collections():
    cols = NFTCollection.query.filter_by(is_active=True).all()
    result = []
    for c in cols:
        minted = NFTItem.query.filter_by(collection_id=c.id, is_minted=True).count()
        result.append({
            'id': c.id, 'name': c.name, 'description': c.description,
            'total_supply': c.total_supply, 'minted': minted,
            'price_sparks': c.price_sparks, 'image_url': c.image_url,
            'bg_color': c.bg_color,
        })
    return jsonify({'collections': result})

@app.route('/nft/buy/<int:collection_id>', methods=['POST'])
def nft_buy(collection_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    col = NFTCollection.query.get_or_404(collection_id)
    if not col.is_active:
        return jsonify({'error': 'Коллекция недоступна'}), 400
    # Найти свободный экземпляр
    item = NFTItem.query.filter_by(collection_id=collection_id, is_minted=False).first()
    if not item:
        return jsonify({'error': 'Все экземпляры разобраны'}), 400
    # Списать искры
    if col.price_sparks > 0:
        if not _spend_sparks(uid, col.price_sparks, 'nft_buy', item.id):
            return jsonify({'error': 'Недостаточно искр', 'required': col.price_sparks}), 402
    # Выдать NFT
    item.is_minted = True
    item.minted_at = datetime.utcnow()
    user_nft = UserNFT(owner_id=uid, nft_item_id=item.id, is_displayed=True)
    db.session.add(user_nft)
    db.session.commit()
    import json as _j
    attrs = _j.loads(item.attributes or '{}')
    return jsonify({
        'success': True,
        'nft': {
            'id': user_nft.id,
            'collection_name': col.name,
            'serial_number': item.serial_number,
            'total_supply': col.total_supply,
            'attributes': attrs,
            'value_sparks': item.value_sparks,
            'bg_color': col.bg_color,
            'image_url': col.image_url,
        }
    })

@app.route('/nft/my')
def nft_my():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    return _nft_list_for_user(session['user_id'])

@app.route('/nft/user/<int:user_id>')
def nft_user(user_id):
    return _nft_list_for_user(user_id, public=True)

def _nft_list_for_user(uid, public=False):
    import json as _j
    q = UserNFT.query.filter_by(owner_id=uid)
    if public:
        q = q.filter_by(is_displayed=True)
    nfts = q.order_by(UserNFT.acquired_at.desc()).all()
    result = []
    for un in nfts:
        item = un.nft_item
        col = item.collection
        attrs = _j.loads(item.attributes or '{}')
        result.append({
            'user_nft_id': un.id,
            'collection_id': col.id,
            'collection_name': col.name,
            'serial_number': item.serial_number,
            'total_supply': col.total_supply,
            'attributes': attrs,
            'value_sparks': item.value_sparks,
            'bg_color': col.bg_color,
            'image_url': col.image_url,
            'is_displayed': un.is_displayed,
            'acquired_at': un.acquired_at.isoformat(),
        })
    return jsonify({'nfts': result})

@app.route('/nft/<int:user_nft_id>/toggle-display', methods=['POST'])
def nft_toggle_display(user_nft_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    un = UserNFT.query.filter_by(id=user_nft_id, owner_id=session['user_id']).first_or_404()
    un.is_displayed = not un.is_displayed
    db.session.commit()
    return jsonify({'success': True, 'is_displayed': un.is_displayed})

@app.route('/nft/item/<int:nft_item_id>')
def nft_item_info(nft_item_id):
    import json as _j
    item = NFTItem.query.get_or_404(nft_item_id)
    col = item.collection
    # Статистика атрибутов
    all_items = NFTItem.query.filter_by(collection_id=col.id, is_minted=True).all()
    total_minted = len(all_items)
    attrs = _j.loads(item.attributes or '{}')
    attr_stats = {}
    if total_minted > 0:
        for k, v in attrs.items():
            count = sum(1 for i in all_items if _j.loads(i.attributes or '{}').get(k) == v)
            attr_stats[k] = round(count / total_minted * 100, 1)
    owner = UserNFT.query.filter_by(nft_item_id=item.id).first()
    owner_name = None
    if owner:
        u = User.query.get(owner.owner_id)
        owner_name = u.display_name or u.username if u else None
    return jsonify({
        'id': item.id,
        'collection_name': col.name,
        'serial_number': item.serial_number,
        'total_supply': col.total_supply,
        'attributes': attrs,
        'attr_rarity': attr_stats,
        'value_sparks': item.value_sparks,
        'bg_color': col.bg_color,
        'image_url': col.image_url,
        'owner': owner_name,
    })


@app.route('/api/payment/buy-nft', methods=['POST'])
def api_buy_nft():
    ok, data = _check_payment_secret()
    if not ok:
        return jsonify({'error': 'Forbidden'}), 403
    username = data.get('username', '').lstrip('@')
    collection_id = data.get('collection_id')
    if not username or not collection_id:
        return jsonify({'error': 'Bad params'}), 400
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    col = NFTCollection.query.get(collection_id)
    if not col or not col.is_active:
        return jsonify({'error': 'Collection not found'}), 404
    item = NFTItem.query.filter_by(collection_id=collection_id, is_minted=False).first()
    if not item:
        return jsonify({'error': 'Sold out'}), 400
    item.is_minted = True
    item.minted_at = datetime.utcnow()
    import json as _j
    user_nft = UserNFT(owner_id=user.id, nft_item_id=item.id, is_displayed=True)
    db.session.add(user_nft)
    db.session.commit()
    attrs = _j.loads(item.attributes or '{}')
    minted_count = NFTItem.query.filter_by(collection_id=col.id, is_minted=True).count()
    return jsonify({
        'success': True,
        'nft': {
            'collection_name': col.name,
            'serial_number': item.serial_number,
            'total_supply': col.total_supply,
            'minted': minted_count,
            'attributes': attrs,
            'value_sparks': item.value_sparks,
        }
    })

# ═══════════════════════════════════════════════════════════════════════════════
# PAYMENT BOT API
# ═══════════════════════════════════════════════════════════════════════════════
_PAYMENT_SECRET = os.environ.get('PAYMENT_SECRET', 'tabletone_payment_secret')

def _check_payment_secret():
    data = request.get_json() or {}
    return data.get('secret') == _PAYMENT_SECRET, data

@app.route('/api/payment/activate-premium', methods=['POST'])
def api_activate_premium():
    ok, data = _check_payment_secret()
    if not ok:
        return jsonify({'error': 'Forbidden'}), 403
    username = data.get('username', '').lstrip('@')
    days = int(data.get('days', 0))
    if not username or days <= 0:
        return jsonify({'error': 'Bad params'}), 400
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    now = datetime.utcnow()
    base = user.premium_until if user.premium_until and user.premium_until > now else now
    user.is_premium = True
    user.premium_until = base + timedelta(days=days)
    db.session.commit()
    return jsonify({'success': True, 'premium_until': user.premium_until.isoformat()})

@app.route('/api/payment/add-sparks', methods=['POST'])
def api_add_sparks():
    ok, data = _check_payment_secret()
    if not ok:
        return jsonify({'error': 'Forbidden'}), 403
    username = data.get('username', '').lstrip('@')
    sparks = int(data.get('sparks', 0))
    if not username or sparks == 0:
        return jsonify({'error': 'Bad params'}), 400
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    _add_sparks(user.id, sparks, 'payment_bot')
    sb = _get_spark_balance(user.id)
    return jsonify({'success': True, 'balance': sb.balance})

@app.route('/api/payment/give-gift', methods=['POST'])
def api_give_gift():
    ok, data = _check_payment_secret()
    if not ok:
        return jsonify({'error': 'Forbidden'}), 403
    username = data.get('username', '').lstrip('@')
    gift_type_id = data.get('gift_type_id')
    if not username or not gift_type_id:
        return jsonify({'error': 'Bad params'}), 400
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    gift_type = GiftType.query.get(gift_type_id)
    if not gift_type:
        return jsonify({'error': 'Gift type not found'}), 404
    gift = UserGift(owner_id=user.id, gift_type_id=gift_type.id, sender_id=None, is_displayed=True)
    db.session.add(gift)
    db.session.commit()
    return jsonify({'success': True, 'gift': gift_type.name, 'emoji': gift_type.emoji})

@app.route('/api/payment/gift-types', methods=['GET'])
def api_gift_types():
    """Список доступных типов подарков (для бота)."""
    secret = request.args.get('secret', '')
    if secret != _PAYMENT_SECRET:
        return jsonify({'error': 'Forbidden'}), 403
    gifts = GiftType.query.filter_by(is_active=True).all()
    return jsonify({'gifts': [{'id': g.id, 'name': g.name, 'emoji': g.emoji, 'rarity': g.rarity} for g in gifts]})

# Автозапуск миграций при старте
with app.app_context():
    try:
        db.create_all()
        from sqlalchemy import text, inspect as sa_inspect
        _insp = sa_inspect(db.engine)
        _auto_migrations = [
            ('group_message', 'is_deleted',    'BOOLEAN DEFAULT FALSE'),
            ('group_message', 'is_paid',        'BOOLEAN DEFAULT FALSE'),
            ('group_message', 'paid_price',     'INTEGER DEFAULT 0'),
            ('group_message', 'message_type',   "VARCHAR(50) DEFAULT 'text'"),
            ('group_message', 'reply_to_id',    'INTEGER'),
            ('message',       'is_deleted',     'BOOLEAN DEFAULT FALSE'),
            ('message',       'is_edited',      'BOOLEAN DEFAULT FALSE'),
            ('sticker',       'is_animated',    'BOOLEAN DEFAULT FALSE'),
            # user table
            ('user', 'auto_reply_text',         'VARCHAR(500)'),
            ('user', 'status_text',             'VARCHAR(200)'),
            ('user', 'theme_schedule',          'VARCHAR(200)'),
            ('user', 'hidden_chat_pin',         'VARCHAR(10)'),
            ('user', 'chat_folders',            'TEXT'),
            ('user', 'admin_apply_blocked_until', 'TIMESTAMP'),
            ('user', 'reputation',              'INTEGER DEFAULT 50'),
            ('user', 'premium_emoji',           'VARCHAR(10)'),
            ('user', 'timezone',                "VARCHAR(100) DEFAULT 'Europe/Moscow'"),
            ('user', 'telegram_chat_id',        'BIGINT'),
            ('user', 'telegram_link_code',      'VARCHAR(32)'),
        ]
        for _tbl, _col, _def in _auto_migrations:
            try:
                _existing = [c['name'] for c in _insp.get_columns(_tbl)]
                if _col not in _existing:
                    # PostgreSQL требует кавычки для зарезервированных слов
                    _tbl_quoted = f'"{_tbl}"' if db.engine.dialect.name == 'postgresql' else _tbl
                    db.session.execute(text(f'ALTER TABLE {_tbl_quoted} ADD COLUMN {_col} {_def}'))
                    db.session.commit()
                    print(f'[auto-migration] Added {_tbl}.{_col}')
            except Exception as _e:
                db.session.rollback()
    except Exception as _e:
        print(f'[auto-migration] Error: {_e}')




# ═══════════════════════════════════════════════════════════════════════════════
# LIVE-ТРАНСЛЯЦИИ В КАНАЛАХ
# ═══════════════════════════════════════════════════════════════════════════════

# Активные трансляции: {group_id: {broadcaster_sid, broadcaster_user_id, title, viewers_count}}
_active_streams = {}

@app.route('/groups/<int:group_id>/stream/start', methods=['POST'])
def stream_start(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Только администраторы могут начинать трансляцию'}), 403
    group = Group.query.get(group_id)
    if not group or not group.is_channel:
        return jsonify({'error': 'Трансляции доступны только в каналах'}), 400
    data = request.get_json() or {}
    title = data.get('title', 'Прямой эфир')[:100]
    _active_streams[group_id] = {
        'broadcaster_user_id': session['user_id'],
        'title': title,
        'viewers_count': 0,
        'started_at': datetime.utcnow().isoformat(),
    }
    user = User.query.get(session['user_id'])
    socketio.emit('stream_started', {
        'group_id': group_id,
        'title': title,
        'broadcaster_name': user.display_name or user.username,
    }, room=f'group_{group_id}', namespace='/')
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/stream/stop', methods=['POST'])
def stream_stop(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    _active_streams.pop(group_id, None)
    socketio.emit('stream_stopped', {'group_id': group_id}, room=f'group_{group_id}', namespace='/')
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/stream/status')
def stream_status(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    s = _active_streams.get(group_id)
    return jsonify({'active': bool(s), 'stream': s})

@socketio.on('stream_signal')
def on_stream_signal(data):
    """Пересылает WebRTC сигнал между broadcaster и viewer."""
    if 'user_id' not in session:
        return
    target_sid = data.get('target_sid')
    if target_sid:
        emit('stream_signal', {
            'from_sid': request.sid,
            'from_user_id': session['user_id'],
            'signal': data.get('signal'),
            'signal_type': data.get('signal_type'),
        }, room=target_sid, namespace='/')

@socketio.on('stream_viewer_join')
def on_stream_viewer_join(data):
    """Зритель присоединяется — уведомляем broadcaster."""
    if 'user_id' not in session:
        return
    group_id = data.get('group_id')
    s = _active_streams.get(group_id)
    if s:
        s['viewers_count'] = s.get('viewers_count', 0) + 1
        # Уведомляем broadcaster чтобы он создал offer для нового зрителя
        emit('stream_new_viewer', {
            'viewer_sid': request.sid,
            'viewer_user_id': session['user_id'],
            'group_id': group_id,
        }, room=f'group_{group_id}', namespace='/')

@socketio.on('stream_viewer_leave')
def on_stream_viewer_leave(data):
    group_id = data.get('group_id')
    s = _active_streams.get(group_id)
    if s:
        s['viewers_count'] = max(0, s.get('viewers_count', 1) - 1)

# ═══════════════════════════════════════════════════════════════════════════════
# ГРУППОВЫЕ ЗВОНКИ (WebRTC mesh через Socket.IO)
# ═══════════════════════════════════════════════════════════════════════════════

# Активные групповые звонки: {group_id: {user_id: {name, avatar_color, avatar_letter}}}
_active_group_calls = {}

@app.route('/groups/<int:group_id>/call/start', methods=['POST'])
def start_group_call(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Нет доступа'}), 403
    if not membership.is_admin:
        return jsonify({'error': 'Только администраторы могут начинать групповые звонки'}), 403
    user = User.query.get(session['user_id'])
    if group_id not in _active_group_calls:
        _active_group_calls[group_id] = {}
    _active_group_calls[group_id][session['user_id']] = {
        'name': user.display_name or user.username,
        'avatar_color': user.avatar_color,
        'avatar_letter': user.get_avatar_letter(),
    }
    socketio.emit('group_call_user_joined', {
        'group_id': group_id,
        'user_id': session['user_id'],
        'name': user.display_name or user.username,
        'avatar_color': user.avatar_color,
        'avatar_letter': user.get_avatar_letter(),
        'participants': list(_active_group_calls[group_id].keys()),
    }, room=f'group_{group_id}', namespace='/')
    return jsonify({'success': True, 'participants': [
        {'user_id': uid, **info} for uid, info in _active_group_calls[group_id].items()
    ]})

@app.route('/groups/<int:group_id>/call/leave', methods=['POST'])
def leave_group_call(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    if group_id in _active_group_calls:
        _active_group_calls[group_id].pop(uid, None)
        if not _active_group_calls[group_id]:
            del _active_group_calls[group_id]
    socketio.emit('group_call_user_left', {
        'group_id': group_id, 'user_id': uid,
    }, room=f'group_{group_id}', namespace='/')
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/call/status')
def group_call_status(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    participants = _active_group_calls.get(group_id, {})
    return jsonify({'active': bool(participants), 'participants': [
        {'user_id': uid, **info} for uid, info in participants.items()
    ]})

@socketio.on('group_call_signal')
def on_group_call_signal(data):
    """Пересылает WebRTC сигнал (offer/answer/ice) конкретному участнику."""
    if 'user_id' not in session:
        return
    target_sid = data.get('target_sid')
    if target_sid:
        emit('group_call_signal', {
            'from_user_id': session['user_id'],
            'signal': data.get('signal'),
            'signal_type': data.get('signal_type'),
        }, room=target_sid, namespace='/')

@socketio.on('group_call_request_peers')
def on_group_call_request_peers(data):
    """Новый участник запрашивает список SID всех в звонке."""
    if 'user_id' not in session:
        return
    group_id = data.get('group_id')
    emit('group_call_peers_list', {
        'group_id': group_id,
        'participants': list(_active_group_calls.get(group_id, {}).keys()),
    }, room=request.sid, namespace='/')

# ═══════════════════════════════════════════════════════════════════════════════
# РАСПИСАНИЕ СООБЩЕНИЙ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/message/schedule', methods=['POST'])
def schedule_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    content = (data.get('content') or '').strip()
    send_at_str = data.get('send_at')  # ISO string
    receiver_id = data.get('receiver_id')
    group_id = data.get('group_id')
    if not content or not send_at_str:
        return jsonify({'error': 'Нет текста или времени'}), 400
    try:
        send_at = datetime.fromisoformat(send_at_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return jsonify({'error': 'Неверный формат времени'}), 400
    if send_at <= datetime.utcnow():
        return jsonify({'error': 'Время должно быть в будущем'}), 400
    msg = ScheduledMessage(
        sender_id=session['user_id'],
        receiver_id=receiver_id,
        group_id=group_id,
        content=encrypt_msg(content),
        send_at=send_at
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({'success': True, 'id': msg.id, 'send_at': send_at.isoformat()})

@app.route('/message/schedule/list')
def list_scheduled():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    msgs = ScheduledMessage.query.filter_by(sender_id=session['user_id'], sent=False).order_by(ScheduledMessage.send_at).all()
    return jsonify({'scheduled': [{'id': m.id, 'content': decrypt_msg(m.content), 'send_at': m.send_at.isoformat(), 'receiver_id': m.receiver_id, 'group_id': m.group_id} for m in msgs]})

@app.route('/message/schedule/<int:msg_id>/cancel', methods=['POST'])
def cancel_scheduled(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    msg = ScheduledMessage.query.filter_by(id=msg_id, sender_id=session['user_id'], sent=False).first()
    if not msg:
        return jsonify({'error': 'Не найдено'}), 404
    db.session.delete(msg)
    db.session.commit()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# АВТООТВЕТ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/user/auto-reply', methods=['GET', 'POST'])
def auto_reply_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        data = request.get_json() or {}
        text = data.get('text', '').strip()[:200]
        user.auto_reply_text = text if text else None
        db.session.commit()
        return jsonify({'success': True, 'text': user.auto_reply_text})
    return jsonify({'text': getattr(user, 'auto_reply_text', None) or ''})

@app.route('/user/msg-price', methods=['GET', 'POST'])
def msg_price_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        data = request.get_json() or {}
        price = data.get('price')
        if price is None or price == '' or int(price) <= 0:
            user.msg_price = None
        else:
            user.msg_price = max(1, min(int(price), 15000))
        db.session.commit()
        return jsonify({'success': True, 'price': user.msg_price})
    return jsonify({'price': getattr(user, 'msg_price', None)})

# ═══════════════════════════════════════════════════════════════════════════════
# ПЕРЕВОД ЧЕРЕЗ GEMINI
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/translate', methods=['POST'])
def translate_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    target_lang = data.get('lang', 'ru')
    if not text:
        return jsonify({'error': 'Нет текста'}), 400
    gemini_key = _os.environ.get('GEMINI_API_KEY', '')
    if not gemini_key:
        return jsonify({'error': 'Gemini API не настроен'}), 503
    try:
        import urllib.request as _req
        import json as _json
        prompt = f"Translate the following text to {target_lang}. Reply with ONLY the translated text, no explanations:\n\n{text}"
        payload = _json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = _req.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        translated = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        return jsonify({'translated': translated})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════════════════════════════════════
# КТО ПРОЧИТАЛ СООБЩЕНИЕ В ГРУППЕ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/messages/<int:message_id>/readers')
def group_message_readers(group_id, message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Нет доступа'}), 403
    reads = GroupMessageRead.query.filter_by(message_id=message_id).all()
    readers = []
    for r in reads:
        u = r.user
        readers.append({'id': u.id, 'name': u.display_name or u.username, 'avatar_color': u.avatar_color, 'avatar_url': u.avatar_url, 'avatar_letter': u.get_avatar_letter()})
    return jsonify({'readers': readers, 'count': len(readers)})

@app.route('/groups/<int:group_id>/mark_read_msg/<int:message_id>', methods=['POST'])
def mark_group_message_read(group_id, message_id):
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    try:
        exists = GroupMessageRead.query.filter_by(message_id=message_id, user_id=session['user_id']).first()
        if not exists:
            r = GroupMessageRead(message_id=message_id, user_id=session['user_id'])
            db.session.add(r)
            db.session.commit()
            # Уведомляем группу об обновлении счётчика
            msg = GroupMessage.query.get(message_id)
            if msg:
                count = GroupMessageRead.query.filter_by(message_id=message_id).count()
                socketio.emit('group_message_read', {'message_id': message_id, 'group_id': group_id, 'count': count}, room=f'group_{group_id}', namespace='/')
    except Exception:
        db.session.rollback()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ЭКСПОРТ / РЕЗЕРВНОЕ КОПИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/chat/<int:other_id>/export')
def export_chat(other_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    msgs = Message.query.filter(
        ((Message.sender_id == uid) & (Message.receiver_id == other_id)) |
        ((Message.sender_id == other_id) & (Message.receiver_id == uid)),
        Message.is_deleted == False
    ).order_by(Message.timestamp.asc()).all()
    other = User.query.get(other_id)
    me = User.query.get(uid)
    data = {
        'export_type': 'private_chat',
        'exported_at': datetime.utcnow().isoformat(),
        'participants': [
            {'id': me.id, 'username': me.username, 'display_name': me.display_name},
            {'id': other.id, 'username': other.username, 'display_name': other.display_name} if other else {}
        ],
        'messages': [{
            'id': m.id,
            'from': m.sender.username,
            'text': m.decrypted_content,
            'type': m.message_type,
            'media_url': m.media_url,
            'timestamp': m.timestamp.isoformat(),
            'is_read': m.is_read
        } for m in msgs]
    }
    import json as _json
    resp = Response(_json.dumps(data, ensure_ascii=False, indent=2), mimetype='application/json')
    resp.headers['Content-Disposition'] = f'attachment; filename="chat_{other_id}.json"'
    return resp

@app.route('/groups/<int:group_id>/export')
def export_group(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
    if not membership:
        return jsonify({'error': 'Нет доступа'}), 403
    group = Group.query.get(group_id)
    msgs = GroupMessage.query.filter_by(group_id=group_id, is_deleted=False).order_by(GroupMessage.timestamp.asc()).all()
    data = {
        'export_type': 'group',
        'exported_at': datetime.utcnow().isoformat(),
        'group': {'id': group.id, 'name': group.name, 'is_channel': group.is_channel},
        'messages': [{
            'id': m.id,
            'from': m.sender.username,
            'text': m.decrypted_content,
            'type': m.message_type,
            'timestamp': m.timestamp.isoformat()
        } for m in msgs]
    }
    import json as _json
    resp = Response(_json.dumps(data, ensure_ascii=False, indent=2), mimetype='application/json')
    resp.headers['Content-Disposition'] = f'attachment; filename="group_{group_id}.json"'
    return resp



# ── Фоновый поток для отправки запланированных сообщений ─────────────────────
def _scheduled_messages_worker():
    import time as _time
    while True:
        _time.sleep(15)
        try:
            with app.app_context():
                now = datetime.utcnow()
                pending = ScheduledMessage.query.filter(
                    ScheduledMessage.sent == False,
                    ScheduledMessage.send_at <= now
                ).all()
                for sm in pending:
                    try:
                        if sm.receiver_id:
                            msg = Message(
                                sender_id=sm.sender_id,
                                receiver_id=sm.receiver_id,
                                content=sm.content,
                                message_type='text'
                            )
                            db.session.add(msg)
                            db.session.flush()
                            sender = User.query.get(sm.sender_id)
                            socketio.emit('new_message', {
                                'id': msg.id,
                                'sender_id': sm.sender_id,
                                'sender_name': sender.display_name or sender.username,
                                'content': decrypt_msg(sm.content),
                                'timestamp': msg.timestamp.strftime('%H:%M'),
                                'timestamp_iso': msg.timestamp.isoformat(),
                                'message_type': 'text',
                                'is_mine': False
                            }, room=f'user_{sm.receiver_id}', namespace='/')
                        elif sm.group_id:
                            gmsg = GroupMessage(
                                group_id=sm.group_id,
                                sender_id=sm.sender_id,
                                content=sm.content,
                                message_type='text'
                            )
                            db.session.add(gmsg)
                            db.session.flush()
                            sender = User.query.get(sm.sender_id)
                            socketio.emit('new_group_message', {
                                'group_id': sm.group_id,
                                'message': {
                                    'id': gmsg.id,
                                    'sender_id': sm.sender_id,
                                    'sender_name': sender.display_name or sender.username,
                                    'content': decrypt_msg(sm.content),
                                    'timestamp': gmsg.timestamp.strftime('%H:%M'),
                                    'timestamp_iso': gmsg.timestamp.isoformat(),
                                    'message_type': 'text',
                                    'is_mine': False
                                }
                            }, room=f'group_{sm.group_id}', namespace='/')
                        sm.sent = True
                        db.session.commit()
                    except Exception as e:
                        db.session.rollback()
                        print(f'[scheduler] Error sending msg {sm.id}: {e}')
        except Exception as e:
            print(f'[scheduler] Worker error: {e}')

import threading as _threading
_sched_thread = _threading.Thread(target=_scheduled_messages_worker, daemon=True)
_sched_thread.start()
# ─────────────────────────────────────────────────────────────────────────────

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


