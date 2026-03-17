"""
Добавляет live-трансляции в каналах.
Backend: Socket.IO сигналинг + route для статуса трансляции.
"""

LIVESTREAM_CODE = '''
# ═══════════════════════════════════════════════════════════════════════════════
# LIVE-ТРАНСЛЯЦИИ В КАНАЛАХ
# ═══════════════════════════════════════════════════════════════════════════════

# Активные трансляции: {group_id: {broadcaster_sid, broadcaster_user_id, title, viewers_count}}
_active_streams = {}

@app.route(\'/groups/<int:group_id>/stream/start\', methods=[\'POST\'])
def stream_start(group_id):
    if \'user_id\' not in session:
        return jsonify({\'error\': \'Не авторизован\'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session[\'user_id\']).first()
    if not membership or not membership.is_admin:
        return jsonify({\'error\': \'Только администраторы могут начинать трансляцию\'}), 403
    group = Group.query.get(group_id)
    if not group or not group.is_channel:
        return jsonify({\'error\': \'Трансляции доступны только в каналах\'}), 400
    data = request.get_json() or {}
    title = data.get(\'title\', \'Прямой эфир\')[:100]
    _active_streams[group_id] = {
        \'broadcaster_user_id\': session[\'user_id\'],
        \'title\': title,
        \'viewers_count\': 0,
        \'started_at\': datetime.utcnow().isoformat(),
    }
    user = User.query.get(session[\'user_id\'])
    socketio.emit(\'stream_started\', {
        \'group_id\': group_id,
        \'title\': title,
        \'broadcaster_name\': user.display_name or user.username,
    }, room=f\'group_{group_id}\', namespace=\'/\')
    return jsonify({\'success\': True})

@app.route(\'/groups/<int:group_id>/stream/stop\', methods=[\'POST\'])
def stream_stop(group_id):
    if \'user_id\' not in session:
        return jsonify({\'error\': \'Не авторизован\'}), 401
    membership = GroupMember.query.filter_by(group_id=group_id, user_id=session[\'user_id\']).first()
    if not membership or not membership.is_admin:
        return jsonify({\'error\': \'Нет прав\'}), 403
    _active_streams.pop(group_id, None)
    socketio.emit(\'stream_stopped\', {\'group_id\': group_id}, room=f\'group_{group_id}\', namespace=\'/\')
    return jsonify({\'success\': True})

@app.route(\'/groups/<int:group_id>/stream/status\')
def stream_status(group_id):
    if \'user_id\' not in session:
        return jsonify({\'error\': \'Не авторизован\'}), 401
    s = _active_streams.get(group_id)
    return jsonify({\'active\': bool(s), \'stream\': s})

@socketio.on(\'stream_signal\')
def on_stream_signal(data):
    """Пересылает WebRTC сигнал между broadcaster и viewer."""
    if \'user_id\' not in session:
        return
    target_sid = data.get(\'target_sid\')
    if target_sid:
        emit(\'stream_signal\', {
            \'from_sid\': request.sid,
            \'from_user_id\': session[\'user_id\'],
            \'signal\': data.get(\'signal\'),
            \'signal_type\': data.get(\'signal_type\'),
        }, room=target_sid, namespace=\'/\')

@socketio.on(\'stream_viewer_join\')
def on_stream_viewer_join(data):
    """Зритель присоединяется — уведомляем broadcaster."""
    if \'user_id\' not in session:
        return
    group_id = data.get(\'group_id\')
    s = _active_streams.get(group_id)
    if s:
        s[\'viewers_count\'] = s.get(\'viewers_count\', 0) + 1
        # Уведомляем broadcaster чтобы он создал offer для нового зрителя
        emit(\'stream_new_viewer\', {
            \'viewer_sid\': request.sid,
            \'viewer_user_id\': session[\'user_id\'],
            \'group_id\': group_id,
        }, room=f\'group_{group_id}\', namespace=\'/\')

@socketio.on(\'stream_viewer_leave\')
def on_stream_viewer_leave(data):
    group_id = data.get(\'group_id\')
    s = _active_streams.get(group_id)
    if s:
        s[\'viewers_count\'] = max(0, s.get(\'viewers_count\', 1) - 1)

'''

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

marker = '# ═══════════════════════════════════════════════════════════════════════════════\n# ГРУППОВЫЕ ЗВОНКИ'
if 'LIVE-ТРАНСЛЯЦИИ' not in src:
    src = src.replace(marker, LIVESTREAM_CODE + marker)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(src)
    print('OK: livestream added')
else:
    print('SKIP: already present')
