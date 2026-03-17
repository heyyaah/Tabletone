"""
fix12_nft.py — Псевдо-NFT коллекционные предметы
Добавляет:
  - Модели NFTCollection, NFTItem, UserNFT
  - Миграции
  - Сид коллекций
  - API маршруты
"""
with open('app.py', encoding='utf-8') as f:
    src = f.read()

# ── 1. Модели после PaidPostPurchase ─────────────────────────────────────────
NFT_MODELS = '''
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

'''

INSERT_AFTER = '''class PaidPostPurchase(db.Model):
    """Факт покупки платного поста пользователем."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    paid_post_id = db.Column(db.Integer, db.ForeignKey('paid_post.id'), nullable=False)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'paid_post_id', name='_post_purchase_uc'),)
    user = db.relationship('User', foreign_keys=[user_id])
    paid_post = db.relationship('PaidPost', foreign_keys=[paid_post_id])
'''
assert INSERT_AFTER in src, "Не найдено место для NFT моделей"
src = src.replace(INSERT_AFTER, INSERT_AFTER + NFT_MODELS, 1)

# ── 2. Сид коллекций + миграции в _init_db ───────────────────────────────────
# Вставляем сид после сида GiftType
GIFT_SEED_MARKER = "        # ── Сид: бот Tabletone Premium ──────────────────────────────────────────"
assert GIFT_SEED_MARKER in src, "Не найден маркер для вставки NFT сида"

NFT_SEED = '''        # ── Сид: NFT коллекции ────────────────────────────────────────────────────
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

'''
src = src.replace(GIFT_SEED_MARKER, NFT_SEED + GIFT_SEED_MARKER, 1)

# ── 3. API маршруты ───────────────────────────────────────────────────────────
NFT_ROUTES = '''
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

'''

# Вставляем перед Payment Bot API
INSERT_BEFORE_PAYMENT = '# ═══════════════════════════════════════════════════════════════════════════════\n# PAYMENT BOT API'
assert INSERT_BEFORE_PAYMENT in src, "Не найдено место для NFT маршрутов"
src = src.replace(INSERT_BEFORE_PAYMENT, NFT_ROUTES + INSERT_BEFORE_PAYMENT, 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print("✓ fix12_nft.py применён")
