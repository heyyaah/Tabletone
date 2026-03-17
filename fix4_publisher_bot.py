"""
Fix 4: Add tabletone_publisher bot
- Seeds bot in _init_db
- Seeds tabletone_official channel and adds bot as admin
- Handles messages from romancev228: formats via Gemini, posts to channel
"""
import re

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

# ── 1. Seed the publisher bot + channel after the Nexus bot seed ──────────────
NEXUS_SEED_END = '''            db.session.commit()
            print("✓ Бот Nexus AI создан")

        # ── Сид: каталог подарков'''

PUBLISHER_SEED = '''            db.session.commit()
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
                        "📢 *Tabletone Publisher*\\n\\n"
                        "Напиши мне заметки об обновлении — я оформлю их красиво и опубликую в канале @tabletone_official.\\n\\n"
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

        # ── Сид: каталог подарков'''

assert NEXUS_SEED_END in src, "NEXUS_SEED_END not found"
src = src.replace(NEXUS_SEED_END, PUBLISHER_SEED, 1)

# ── 2. Add publisher bot handler before the Nexus handler ─────────────────────
NEXUS_HANDLER = '''            # ── Nexus AI ────────────────────────────────────────────
            is_nexus = bot_user_obj and bot_user_obj.username == 'nexus'
            if is_nexus:
                _handle_nexus_bot(bot.user_id, sender_id, text)
                return'''

PUBLISHER_HANDLER = '''            # ── tabletone_publisher — только для romancev228 ─────────────────
            is_publisher = bot_user_obj and bot_user_obj.username == 'tabletone_publisher'
            if is_publisher:
                _handle_publisher_bot(bot.user_id, sender_id, text)
                return

            # ── Nexus AI ────────────────────────────────────────────
            is_nexus = bot_user_obj and bot_user_obj.username == 'nexus'
            if is_nexus:
                _handle_nexus_bot(bot.user_id, sender_id, text)
                return'''

assert NEXUS_HANDLER in src, "NEXUS_HANDLER not found"
src = src.replace(NEXUS_HANDLER, PUBLISHER_HANDLER, 1)

# ── 3. Add _handle_publisher_bot function right before _handle_nexus_bot ──────
BEFORE_NEXUS_FN = '''def _handle_nexus_bot(bot_user_id, sender_id, text):'''

PUBLISHER_FN = '''def _handle_publisher_bot(bot_user_id, sender_id, text):
    """Обрабатывает сообщения для бота-публикатора. Только romancev228."""
    import threading, urllib.request, json as _json, os as _os

    owner = User.query.filter_by(username='romancev228').first()
    if not owner or sender_id != owner.id:
        _bot_send_message(bot_user_id, sender_id,
            "⛔ Этот бот доступен только администратору.")
        return

    if text.strip().lower() == '/start':
        _bot_send_message(bot_user_id, sender_id,
            "📢 *Tabletone Publisher*\\n\\n"
            "Напиши заметки об обновлении — я оформлю их красиво и опубликую в @tabletone_official.\\n\\n"
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
                    "Пиши на русском языке. Верни ТОЛЬКО готовый текст поста, без пояснений.\\n\\n"
                    f"Заметки:\\n{text}"
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
                    f"✅ Пост опубликован в @tabletone_official!\\n\\n"
                    f"📝 *Текст поста:*\\n{formatted[:300]}{'...' if len(formatted) > 300 else ''}")
        except Exception as e:
            app.logger.error(f"Publisher bot error: {e}")
            with app.app_context():
                _bot_send_message(bot_user_id, sender_id, f"⚠️ Ошибка публикации: {str(e)[:200]}")

    threading.Thread(target=_do_publish, daemon=True).start()


def _handle_nexus_bot(bot_user_id, sender_id, text):'''

assert BEFORE_NEXUS_FN in src, "BEFORE_NEXUS_FN not found"
src = src.replace(BEFORE_NEXUS_FN, PUBLISHER_FN, 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print("✓ fix4_publisher_bot.py applied")
