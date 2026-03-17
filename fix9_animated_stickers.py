"""
Добавляет поддержку анимированных стикеров (Lottie JSON).
1. Расширяет ALLOWED_STICKER_EXT добавляя 'json'
2. В sticker_pack_create — сохраняет JSON как есть (не base64 image)
3. Добавляет поле is_animated в Sticker модель через авто-миграцию
"""

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

# 1. Расширить ALLOWED_STICKER_EXT
old_ext = "ALLOWED_STICKER_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}"
new_ext = "ALLOWED_STICKER_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'json'}"
if old_ext in src:
    src = src.replace(old_ext, new_ext)
    print('OK: ALLOWED_STICKER_EXT updated')
else:
    print('SKIP: ALLOWED_STICKER_EXT not found or already updated')

# 2. Обновить sticker_pack_create чтобы JSON сохранялся как data:application/json;base64
old_create = '''    for i, f in enumerate(files[:20]):
        if not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        ext = f.filename.rsplit('.', 1)[1].lower()
        mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'image/png')
        import base64
        data = base64.b64encode(f.read()).decode('utf-8')
        url = f"data:{mime};base64,{data}"
        sticker = Sticker(pack_id=pack.id, image_url=url, order_index=i)
        db.session.add(sticker)
        if i == 0:
            pack.cover_url = url'''

new_create = '''    import base64 as _b64stk
    for i, f in enumerate(files[:20]):
        ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else ''
        is_animated = ext == 'json'
        if not is_animated and not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        if is_animated:
            raw = f.read()
            url = 'data:application/json;base64,' + _b64stk.b64encode(raw).decode('utf-8')
        else:
            mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'image/png')
            url = f"data:{mime};base64," + _b64stk.b64encode(f.read()).decode('utf-8')
        sticker = Sticker(pack_id=pack.id, image_url=url, order_index=i)
        db.session.add(sticker)
        if i == 0:
            pack.cover_url = url'''

if old_create in src:
    src = src.replace(old_create, new_create)
    print('OK: sticker_pack_create updated')
else:
    print('SKIP: sticker_pack_create block not found')

# 3. Добавить is_animated в авто-миграцию
old_mig = "('message',       'is_edited',     'BOOLEAN DEFAULT FALSE'),"
new_mig = """('message',       'is_edited',     'BOOLEAN DEFAULT FALSE'),
            ('sticker',        'is_animated',   'BOOLEAN DEFAULT FALSE'),"""
if old_mig in src and "sticker.*is_animated" not in src:
    src = src.replace(old_mig, new_mig)
    print('OK: is_animated migration added')

# 4. Обновить stickers/my и stickers/pack/<id> чтобы возвращали is_animated
old_pack_resp = "{'id': s.id, 'image_url': s.image_url, 'emoji_hint': s.emoji_hint or '😊', 'order_index': s.order_index}"
new_pack_resp = "{'id': s.id, 'image_url': s.image_url, 'emoji_hint': s.emoji_hint or '😊', 'order_index': s.order_index, 'is_animated': s.image_url.startswith('data:application/json')}"
if old_pack_resp in src:
    src = src.replace(old_pack_resp, new_pack_resp)
    print('OK: sticker response updated')
else:
    print('SKIP: sticker response not found')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print('Done.')
