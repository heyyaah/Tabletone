web: gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app
worker: python tg_payment_bot.py
