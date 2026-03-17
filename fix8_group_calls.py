"""
Добавляет групповые голосовые/видео звонки через WebRTC mesh + Socket.IO сигналинг.
"""

GROUP_CALL_ROUTES = '''
# ═══════════════════════════════════════════════════════════════════════════════
# ГРУППОВЫЕ ЗВОНКИ (WebRTC mesh через Socket.IO)
# ═══════════════════════════════════════════════════════════════════════════════

# Активные групповые звонки: {group_id: {user_id: {name, avatar_color, avatar_letter}}}
_active_group_calls = {}

@app.route('/groups/<int:group_id>/call/start', methods=['POST'])
def start_group_call(group_id):
    if \'user_id\' not in session:
        return jsonify({\'error\': \'Не авторизован\'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session[\'user_id\']).first()
    if not membership:
        return jsonify({\'error\': \'Нет доступа\'}), 403
    user = User.query.get(session[\'user_id\'])
    if group_id not in _active_group_calls:
        _active_group_calls[group_id] = {}
    _active_group_calls[group_id][session[\'user_id\']] = {
        \'name\': user.display_name or user.username,
        \'avatar_color\': user.avatar_color,
        \'avatar_letter\': user.get_avatar_letter(),
    }
    socketio.emit(\'group_call_user_joined\', {
        \'group_id\': group_id,
        \'user_id\': session[\'user_id\'],
        \'name\': user.display_name or user.username,
        \'avatar_color\': user.avatar_color,
        \'avatar_letter\': user.get_avatar_letter(),
        \'participants\': list(_active_group_calls[group_id].keys()),
    }, room=f\'group_{group_id}\', namespace=\'/\')
    return jsonify({\'success\': True, \'participants\': [
        {\'user_id\': uid, **info} for uid, info in _active_group_calls[group_id].items()
    ]})

@app.route(\'/groups/<int:group_id>/call/leave\', methods=[\'POST\'])
def leave_group_call(group_id):
    if \'user_id\' not in session:
        return jsonify({\'error\': \'Не авторизован\'}), 401
    uid = session[\'user_id\']
    if group_id in _active_group_calls:
        _active_group_calls[group_id].pop(uid, None)
        if not _active_group_calls[group_id]:
            del _active_group_calls[group_id]
    socketio.emit(\'group_call_user_left\', {
        \'group_id\': group_id, \'user_id\': uid,
    }, room=f\'group_{group_id}\', namespace=\'/\')
    return jsonify({\'success\': True})

@app.route(\'/groups/<int:group_id>/call/status\')
def group_call_status(group_id):
    if \'user_id\' not in session:
        return jsonify({\'error\': \'Не авторизован\'}), 401
    participants = _active_group_calls.get(group_id, {})
    return jsonify({\'active\': bool(participants), \'participants\': [
        {\'user_id\': uid, **info} for uid, info in participants.items()
    ]})

@socketio.on(\'group_call_signal\')
def on_group_call_signal(data):
    """Пересылает WebRTC сигнал (offer/answer/ice) конкретному участнику."""
    if \'user_id\' not in session:
        return
    target_sid = data.get(\'target_sid\')
    if target_sid:
        emit(\'group_call_signal\', {
            \'from_user_id\': session[\'user_id\'],
            \'signal\': data.get(\'signal\'),
            \'signal_type\': data.get(\'signal_type\'),
        }, room=target_sid, namespace=\'/\')

@socketio.on(\'group_call_request_peers\')
def on_group_call_request_peers(data):
    """Новый участник запрашивает список SID всех в звонке."""
    if \'user_id\' not in session:
        return
    group_id = data.get(\'group_id\')
    emit(\'group_call_peers_list\', {
        \'group_id\': group_id,
        \'participants\': list(_active_group_calls.get(group_id, {}).keys()),
    }, room=request.sid, namespace=\'/\')

'''

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

marker = '# ═══════════════════════════════════════════════════════════════════════════════\n# РАСПИСАНИЕ СООБЩЕНИЙ'
if 'ГРУППОВЫЕ ЗВОНКИ' not in src:
    src = src.replace(marker, GROUP_CALL_ROUTES + marker)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(src)
    print('OK: group calls added')
else:
    print('SKIP: already present')
