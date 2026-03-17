"""
fix13_nft_admin_gifts.py
1. Расширяет каталог подарков (20+ подарков)
2. Добавляет GiftPremium модель (подарить Premium за искры)
3. Добавляет API для NFT в боте (buy via bot)
4. Добавляет admin API для NFT (CRUD)
5. Добавляет маршрут /gifts/send-premium
"""
with open('app.py', encoding='utf-8') as f:
    src = f.read()

# ── 1. Расширить каталог подарков ─────────────────────────────────────────────
OLD_GIFTS = """            _default_gifts = [
                GiftType(name='Сердечко', emoji='❤️', description='Маленький знак внимания', price_sparks=5, rarity='common'),
                GiftType(name='Звезда', emoji='⭐', description='Ты звезда!', price_sparks=10, rarity='common'),
                GiftType(name='Огонь', emoji='🔥', description='Горячий подарок', price_sparks=20, rarity='common'),
                GiftType(name='Алмаз', emoji='💎', description='Редкий и ценный', price_sparks=50, rarity='rare'),
                GiftType(name='Корона', emoji='👑', description='Для настоящих королей', price_sparks=100, rarity='epic'),
                GiftType(name='Ракета', emoji='🚀', description='До луны и обратно', price_sparks=200, rarity='epic'),
                GiftType(name='Единорог', emoji='🦄', description='Легендарный подарок', price_sparks=500, rarity='legendary'),
            ]"""

NEW_GIFTS = """            _default_gifts = [
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
            ]"""

assert OLD_GIFTS in src, "Не найден старый каталог подарков"
src = src.replace(OLD_GIFTS, NEW_GIFTS, 1)

# ── 2. Добавить admin NFT API ─────────────────────────────────────────────────
NFT_ADMIN_API = '''
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

'''

# Вставляем перед NFT маршрутами
INSERT_BEFORE_NFT = '# ═══════════════════════════════════════════════════════════════════════════════\n# NFT КОЛЛЕКЦИОННЫЕ ПРЕДМЕТЫ'
assert INSERT_BEFORE_NFT in src, "Не найдено место для admin NFT API"
src = src.replace(INSERT_BEFORE_NFT, NFT_ADMIN_API + INSERT_BEFORE_NFT, 1)

# ── 3. Добавить NFT buy via bot API ──────────────────────────────────────────
NFT_BOT_API = '''
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

'''

INSERT_BEFORE_PAYMENT = '# ═══════════════════════════════════════════════════════════════════════════════\n# PAYMENT BOT API'
assert INSERT_BEFORE_PAYMENT in src, "Не найдено место для NFT bot API"
src = src.replace(INSERT_BEFORE_PAYMENT, NFT_BOT_API + INSERT_BEFORE_PAYMENT, 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print("✓ fix13_nft_admin_gifts.py применён")
