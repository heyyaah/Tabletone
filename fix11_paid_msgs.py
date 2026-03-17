"""
fix11_paid_msgs.py
1. Добавляет поле msg_price в User модель
2. Добавляет миграцию msg_price
3. Добавляет проверку оплаты искрами при отправке сообщения romancev228
4. Добавляет API /api/payment/activate-premium, /api/payment/add-sparks, /api/payment/give-gift
"""
import re

with open('app.py', encoding='utf-8') as f:
    src = f.read()

# ── 1. Добавить msg_price в User модель ──────────────────────────────────────
OLD_MODEL = '    auto_reply_text = db.Column(db.String(200), nullable=True)  # Автоответ'
NEW_MODEL = (
    '    auto_reply_text = db.Column(db.String(200), nullable=True)  # Автоответ\n'
    '    msg_price = db.Column(db.Integer, nullable=True, default=None)  # Цена сообщения в искрах (None = бесплатно)'
)
assert OLD_MODEL in src, "Не найдено место для msg_price в модели User"
src = src.replace(OLD_MODEL, NEW_MODEL, 1)

# ── 2. Добавить миграцию msg_price (postgres) ─────────────────────────────────
OLD_MIG_PG = (
    '                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS auto_reply_text VARCHAR(500)",\n'
    '                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS premium_emoji VARCHAR(10)",'
)
NEW_MIG_PG = (
    '                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS auto_reply_text VARCHAR(500)",\n'
    '                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS premium_emoji VARCHAR(10)",\n'
    '                f"ALTER TABLE {user_table} ADD COLUMN IF NOT EXISTS msg_price INTEGER",'
)
assert OLD_MIG_PG in src, "Не найдено место для миграции msg_price (postgres)"
src = src.replace(OLD_MIG_PG, NEW_MIG_PG, 1)

# ── 3. Добавить миграцию msg_price (sqlite) ───────────────────────────────────
OLD_MIG_SQ = (
    '                f"ALTER TABLE {user_table} ADD COLUMN auto_reply_text VARCHAR(500)",\n'
    '                f"ALTER TABLE {user_table} ADD COLUMN premium_emoji VARCHAR(10)",'
)
NEW_MIG_SQ = (
    '                f"ALTER TABLE {user_table} ADD COLUMN auto_reply_text VARCHAR(500)",\n'
    '                f"ALTER TABLE {user_table} ADD COLUMN premium_emoji VARCHAR(10)",\n'
    '                f"ALTER TABLE {user_table} ADD COLUMN msg_price INTEGER",'
)
assert OLD_MIG_SQ in src, "Не найдено место для миграции msg_price (sqlite)"
src = src.replace(OLD_MIG_SQ, NEW_MIG_SQ, 1)

# ── 4. Добавить проверку оплаты искрами перед созданием сообщения ─────────────
OLD_SEND = '''    # Premium Support доступен только для Premium пользователей
    _sender_is_staff = sender.is_admin or bool(sender.admin_role)
    if receiver.username == 'premium_support' and not sender.is_premium and not _sender_is_staff:
        return jsonify({'error': 'premium_required', 'message': 'Premium Support доступен только для Premium пользователей'}), 403

    # Обычная поддержка недоступна для Premium пользователей (кроме стаффа)
    if receiver.username == 'tabletone_supportbot' and sender.is_premium and not _sender_is_staff:
        return jsonify({'error': 'premium_required', 'message': 'У вас Premium — используйте Premium Support (@premium_support)'}), 403
    message = Message('''

NEW_SEND = '''    # Premium Support доступен только для Premium пользователей
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

    message = Message('''

assert OLD_SEND in src, "Не найдено место для проверки оплаты искрами"
src = src.replace(OLD_SEND, NEW_SEND, 1)

# ── 5. Добавить API маршруты для бота оплаты ─────────────────────────────────
PAYMENT_API = '''

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

'''

# Вставляем перед блоком автозапуска миграций
INSERT_BEFORE = '# Автозапуск миграций при старте'
assert INSERT_BEFORE in src, "Не найдено место для вставки Payment API"
src = src.replace(INSERT_BEFORE, PAYMENT_API + INSERT_BEFORE, 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print("✓ fix11_paid_msgs.py применён")
