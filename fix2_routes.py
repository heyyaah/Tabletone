"""Add new routes to app.py"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

new_routes = '''

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

'''

new_routes += '''
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

@app.route('/groups/<int:group_id>/members/<int:user_id>/role', methods=['POST'])
def assign_group_role(group_id, user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get_or_404(group_id)
    if group.creator_id != session['user_id']:
        return jsonify({'error': 'Нет прав'}), 403
    data = request.get_json()
    role_id = data.get('role_id')
    existing = GroupMemberRole.query.filter_by(group_id=group_id, user_id=user_id).first()
    if role_id:
        if existing:
            existing.role_id = role_id
        else:
            db.session.add(GroupMemberRole(group_id=group_id, user_id=user_id, role_id=role_id))
    else:
        if existing:
            db.session.delete(existing)
    db.session.commit()
    return jsonify({'success': True})

'''

new_routes += '''
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
    content = data.get('content', '').strip()
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

'''

new_routes += '''
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
        result.append({
            'id': p.id, 'name': p.name,
            'cover_url': p.cover_url,
            'sticker_count': Sticker.query.filter_by(pack_id=p.id).count(),
            'preview': [s.image_url for s in stickers],
            'added': p.id in my_pack_ids
        })
    return jsonify({'packs': result})

@app.route('/stickers/pack/<int:pack_id>/add', methods=['POST'])
def add_sticker_pack(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    if not StickerPack.query.get(pack_id):
        return jsonify({'error': 'Пак не найден'}), 404
    existing = UserStickerPack.query.filter_by(user_id=uid, pack_id=pack_id).first()
    if not existing:
        db.session.add(UserStickerPack(user_id=uid, pack_id=pack_id))
        db.session.commit()
    return jsonify({'success': True})

@app.route('/stickers/pack/<int:pack_id>/remove', methods=['POST'])
def remove_sticker_pack(pack_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    UserStickerPack.query.filter_by(user_id=session['user_id'], pack_id=pack_id).delete()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/stickers/my')
def my_stickers():
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    user_packs = UserStickerPack.query.filter_by(user_id=uid).all()
    result = []
    for usp in user_packs:
        p = StickerPack.query.get(usp.pack_id)
        if not p:
            continue
        stickers = Sticker.query.filter_by(pack_id=p.id).order_by(Sticker.order_index).all()
        result.append({
            'id': p.id, 'name': p.name,
            'stickers': [{'id': s.id, 'image_url': s.image_url, 'emoji': s.emoji_hint} for s in stickers]
        })
    return jsonify({'packs': result})

'''

new_routes += '''
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
    # Socket уведомление
    socketio.emit('new_message', {
        'id': msg.id, 'sender_id': uid, 'receiver_id': recipient_id,
        'content': gift_msg_content, 'message_type': 'gift',
        'timestamp': msg.timestamp.strftime('%H:%M'),
        'gift': {'id': gift_type.id, 'name': gift_type.name,
                 'emoji': gift_type.emoji, 'price': gift_type.price_sparks,
                 'rarity': gift_type.rarity, 'description': gift_type.description,
                 'user_gift_id': user_gift.id}
    }, room=f'user_{recipient_id}')
    socketio.emit('new_message', {
        'id': msg.id, 'sender_id': uid, 'receiver_id': recipient_id,
        'content': gift_msg_content, 'message_type': 'gift',
        'timestamp': msg.timestamp.strftime('%H:%M'),
        'gift': {'id': gift_type.id, 'name': gift_type.name,
                 'emoji': gift_type.emoji, 'price': gift_type.price_sparks,
                 'rarity': gift_type.rarity, 'description': gift_type.description,
                 'user_gift_id': user_gift.id}
    }, room=f'user_{uid}')
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
            'id': g.id, 'gift_type': {'id': gt.id, 'name': gt.name, 'emoji': gt.emoji,
                                       'price': gt.price_sparks, 'rarity': gt.rarity},
            'sender': {'username': sender.username, 'display_name': sender.display_name or sender.username} if sender else None,
            'is_displayed': g.is_displayed,
            'received_at': g.received_at.strftime('%d.%m.%Y')
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

'''

new_routes += '''
# ═══════════════════════════════════════════════════════════════════════════════
# ПРЕДПРОСМОТР ССЫЛОК
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/link_preview')
def link_preview():
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
            for pat in [
                rf\'<meta[^>]+property=["\\\']og:{name}["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\'][^>]*/?>\'',
                rf\'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+property=["\\\']og:{name}["\\\'][^>]*/?>\'',
                rf\'<meta[^>]+name=["\\\'{name}["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\'][^>]*/?>\'',
            ]:
                m = _re.search(pat, raw, _re.IGNORECASE)
                if m:
                    return _html.unescape(m.group(1).strip())
            return None
        title = _meta('title') or (_re.search(r\'<title[^>]*>([^<]+)</title>\', raw, _re.IGNORECASE) or [None, None])[1]
        description = _meta('description')
        image = _meta('image')
        site_name = _meta('site_name')
        return jsonify({
            'url': url, 'title': title, 'description': description,
            'image': image, 'site_name': site_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

'''

new_routes += '''
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

@app.route('/groups/<int:group_id>/pin', methods=['POST'])
def pin_group_message(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    group = Group.query.get_or_404(group_id)
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    is_admin = group.creator_id == uid or (member and member.is_admin)
    if not is_admin:
        return jsonify({'error': 'Нет прав'}), 403
    data = request.get_json()
    msg_id = data.get('message_id')
    msg = GroupMessage.query.get_or_404(msg_id)
    # Удаляем старое закреплённое
    PinnedMessage.query.filter_by(group_id=group_id).delete()
    pin = PinnedMessage(group_id=group_id, group_message_id=msg_id,
                        pinned_by=uid, content_preview=msg.content[:100])
    db.session.add(pin)
    db.session.commit()
    socketio.emit('message_pinned', {
        'group_id': group_id, 'message_id': msg_id,
        'preview': msg.content[:100]
    }, room=f'group_{group_id}')
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/unpin', methods=['POST'])
def unpin_group_message(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    uid = session['user_id']
    group = Group.query.get_or_404(group_id)
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if group.creator_id != uid and not (member and member.is_admin):
        return jsonify({'error': 'Нет прав'}), 403
    PinnedMessage.query.filter_by(group_id=group_id).delete()
    db.session.commit()
    socketio.emit('message_unpinned', {'group_id': group_id}, room=f'group_{group_id}')
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ПРИГЛАШЕНИЕ В ГРУППУ ПО ССЫЛКЕ
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/groups/<int:group_id>/invite_link', methods=['POST'])
def generate_invite_link(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    group = Group.query.get_or_404(group_id)
    uid = session['user_id']
    member = GroupMember.query.filter_by(group_id=group_id, user_id=uid).first()
    if group.creator_id != uid and not (member and member.is_admin):
        return jsonify({'error': 'Нет прав'}), 403
    if not group.invite_link:
        group.invite_link = secrets.token_urlsafe(12)
        db.session.commit()
    return jsonify({'link': f'/join/{group.invite_link}'})

'''

# Insert before the privacy route (near end of file)
insert_before = "@app.route('/privacy')"
src = src.replace(insert_before, new_routes + insert_before, 1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print("fix2 done")
