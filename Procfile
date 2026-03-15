web: gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app
bot: python tg_payment_bot.py
