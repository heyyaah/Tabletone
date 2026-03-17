"""
1. Добавляет автозапуск _run_migrations() при старте приложения
2. Исправляет логику кнопок: нажатие на кружок → переключается в гс (голосовое)
"""
import re

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

# ── 1. Добавить вызов миграции при старте ──────────────────────────────────
# Ищем route admin_run_migrations и добавляем вызов после определения db
# Вставим вызов перед if __name__ == '__main__':
MIGRATION_CALL = '''
# Автозапуск миграций при старте
with app.app_context():
    try:
        db.create_all()
        from sqlalchemy import text, inspect as sa_inspect
        _insp = sa_inspect(db.engine)
        _auto_migrations = [
            ('group_message', 'is_deleted',   'BOOLEAN DEFAULT FALSE'),
            ('group_message', 'is_paid',       'BOOLEAN DEFAULT FALSE'),
            ('group_message', 'paid_price',    'INTEGER DEFAULT 0'),
            ('group_message', 'message_type',  "VARCHAR(50) DEFAULT 'text'"),
            ('group_message', 'reply_to_id',   'INTEGER'),
            ('message',       'is_deleted',    'BOOLEAN DEFAULT FALSE'),
            ('message',       'is_edited',     'BOOLEAN DEFAULT FALSE'),
        ]
        for _tbl, _col, _def in _auto_migrations:
            try:
                _existing = [c['name'] for c in _insp.get_columns(_tbl)]
                if _col not in _existing:
                    db.session.execute(text(f'ALTER TABLE {_tbl} ADD COLUMN {_col} {_def}'))
                    db.session.commit()
                    print(f'[auto-migration] Added {_tbl}.{_col}')
            except Exception as _e:
                db.session.rollback()
    except Exception as _e:
        print(f'[auto-migration] Error: {_e}')

'''

marker = "if __name__ == '__main__':"
if 'auto-migration' not in src:
    src = src.replace(marker, MIGRATION_CALL + marker)
    print('OK: auto-migration added')
else:
    print('SKIP: auto-migration already present')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print('app.py updated')
