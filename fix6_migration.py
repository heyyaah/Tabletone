"""
Добавляет недостающие колонки в БД (безопасно — проверяет наличие перед добавлением).
"""
import re

FIX = '''
# ── авто-миграция при старте ──────────────────────────────────────────────────
def _run_migrations():
    """Добавляет недостающие колонки без удаления данных."""
    with app.app_context():
        db.create_all()
        try:
            from sqlalchemy import text, inspect as sa_inspect
            insp = sa_inspect(db.engine)
            migrations = [
                ('group_message', 'is_deleted',    'BOOLEAN DEFAULT FALSE'),
                ('group_message', 'is_paid',        'BOOLEAN DEFAULT FALSE'),
                ('group_message', 'paid_price',     'INTEGER DEFAULT 0'),
                ('group_message', 'message_type',   "VARCHAR(50) DEFAULT \\'text\\'"),
                ('group_message', 'reply_to_id',    'INTEGER'),
                ('message',       'is_deleted',     'BOOLEAN DEFAULT FALSE'),
                ('message',       'is_edited',      'BOOLEAN DEFAULT FALSE'),
            ]
            for table, col, col_def in migrations:
                try:
                    existing = [c['name'] for c in insp.get_columns(table)]
                    if col not in existing:
                        db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {col_def}'))
                        db.session.commit()
                        print(f'[migration] Added {table}.{col}')
                except Exception as e:
                    db.session.rollback()
                    print(f'[migration] Skip {table}.{col}: {e}')
        except Exception as e:
            print(f'[migration] Error: {e}')

_run_migrations()
# ─────────────────────────────────────────────────────────────────────────────
'''

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

# Вставляем перед if __name__ == '__main__':
marker = "if __name__ == '__main__':"
if '_run_migrations' not in src:
    src = src.replace(marker, FIX + '\n' + marker)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(src)
    print('OK: migration code added')
else:
    print('SKIP: already present')
