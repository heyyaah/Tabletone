"""Fix link_preview route regex syntax"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

old = '''@app.route('/api/link_preview')
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
        return jsonify({'error': str(e)}), 500'''

new = '''@app.route('/api/link_preview')
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
            patterns = [
                '<meta[^>]+property=["\\\']og:' + name + '["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\'][^>]*/?>',
                '<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+property=["\\\']og:' + name + '["\\\'][^>]*/?>',
                '<meta[^>]+name=["\\\']' + name + '["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\'][^>]*/?>',
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
        return jsonify({'error': str(e)}), 500'''

if old in src:
    src = src.replace(old, new, 1)
    print("replaced ok")
else:
    # Try to find and replace by function name
    import re
    # Find the function and replace it
    pattern = r"@app\.route\('/api/link_preview'\)\ndef link_preview\(\):.*?(?=\n@app\.route|\nif __name__)"
    match = re.search(pattern, src, re.DOTALL)
    if match:
        src = src[:match.start()] + new + '\n\n' + src[match.end():]
        print("regex replaced ok")
    else:
        print("NOT FOUND - manual fix needed")

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)
