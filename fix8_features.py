"""
Добавляет в app.py:
1. Модель ScheduledMessage (расписание сообщений)
2. Поле auto_reply_text в User
3. Маршруты: /message/schedule, /message/schedule/<id>/cancel, /user/auto-reply
4. Маршрут /group/<id>/messages/<mid>/readers (кто прочитал)
5. Маршрут /chat/export и /group/<id>/export (резервное копирование)
6. Маршрут /translate (перевод через Gemini)
7. Фоновый поток для отправки запланированных сообщений
"""
import re

MODELS = '''
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

'''

ROUTES = '''
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
        prompt = f"Translate the following text to {target_lang}. Reply with ONLY the translated text, no explanations:\\n\\n{text}"
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

'''

SCHEDULER_THREAD = '''
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
'''

AUTO_REPLY_FIELD = "    reputation = db.Column(db.Integer, default=100)  # Репутация 0-100"
AUTO_REPLY_ADD = """    reputation = db.Column(db.Integer, default=100)  # Репутация 0-100
    auto_reply_text = db.Column(db.String(200), nullable=True)  # Автоответ"""

# Также нужно добавить auto_reply в send route
AUTO_REPLY_HOOK = '''        # Автоответ
        receiver_user = User.query.get(receiver_id)
        if receiver_user and getattr(receiver_user, 'auto_reply_text', None):
            # Проверяем что не отвечали в последние 5 минут
            recent = Message.query.filter_by(
                sender_id=receiver_id, receiver_id=session['user_id']
            ).filter(Message.timestamp >= datetime.utcnow() - timedelta(minutes=5)).first()
            if not recent:
                auto_msg = Message(
                    sender_id=receiver_id,
                    receiver_id=session['user_id'],
                    content=encrypt_msg(receiver_user.auto_reply_text),
                    message_type='text'
                )
                db.session.add(auto_msg)
                db.session.flush()
                socketio.emit('new_message', {
                    'id': auto_msg.id,
                    'sender_id': receiver_id,
                    'sender_name': receiver_user.display_name or receiver_user.username,
                    'content': receiver_user.auto_reply_text,
                    'timestamp': auto_msg.timestamp.strftime('%H:%M'),
                    'timestamp_iso': auto_msg.timestamp.isoformat(),
                    'message_type': 'text',
                    'is_mine': False,
                    'is_auto_reply': True
                }, room=f'user_{session["user_id"]}', namespace='/')
                db.session.commit()
'''

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changes = 0

# 1. Добавить auto_reply_text в User
if 'auto_reply_text' not in src:
    src = src.replace(AUTO_REPLY_FIELD, AUTO_REPLY_ADD)
    changes += 1
    print('OK: auto_reply_text field added')
else:
    print('SKIP: auto_reply_text already present')

# 2. Добавить модели после GroupMessageMedia
model_anchor = 'class Group(db.Model):'
if 'class ScheduledMessage' not in src:
    src = src.replace(model_anchor, MODELS + model_anchor)
    changes += 1
    print('OK: ScheduledMessage + GroupMessageRead models added')
else:
    print('SKIP: models already present')

# 3. Добавить маршруты перед if __name__
main_anchor = "if __name__ == '__main__':"
if '/message/schedule' not in src:
    src = src.replace(main_anchor, ROUTES + '\n' + main_anchor)
    changes += 1
    print('OK: routes added')
else:
    print('SKIP: routes already present')

# 4. Добавить фоновый поток
if '_scheduled_messages_worker' not in src:
    src = src.replace(main_anchor, SCHEDULER_THREAD + '\n' + main_anchor)
    changes += 1
    print('OK: scheduler thread added')
else:
    print('SKIP: scheduler already present')

# 5. Добавить auto_reply hook в /send route
# Ищем место после сохранения сообщения в /send
send_anchor = "        return jsonify({'success': True, 'message': {"
if 'Автоответ' not in src and send_anchor in src:
    # Найдём первое вхождение (личный чат)
    idx = src.find(send_anchor)
    # Ищем db.session.commit() перед этим return
    commit_before = src.rfind('db.session.commit()', 0, idx)
    if commit_before > 0:
        insert_pos = commit_before + len('db.session.commit()')
        src = src[:insert_pos] + '\n' + AUTO_REPLY_HOOK + src[insert_pos:]
        changes += 1
        print('OK: auto_reply hook added to /send')
    else:
        print('SKIP: could not find commit anchor for auto_reply')
else:
    print('SKIP: auto_reply hook already present or anchor not found')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print(f'\nTotal changes: {changes}')
