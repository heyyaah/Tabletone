"""
Telegram-бот для приёма платежей за Premium и Искры (Tabletone)
Использует Telegram Stars (XTR) — не нужен провайдер, работает сразу.

Запуск: python tg_payment_bot.py
"""

import os
import logging
import asyncio
from telegram import (
    Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, MessageHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("PAYMENT_BOT_TOKEN", "8705438057:AAEIeyFixNBr3eH4_4NIso57GKXOFvs3E_M")

# URL твоего сайта Tabletone на Render
SITE_URL = os.environ.get("SITE_URL", "https://hi-latest.onrender.com")

# Прайс-лист Premium (в Telegram Stars)
PREMIUM_PLANS = {
    "premium_7":   {"label": "Premium 7 дней",    "stars": 50,   "days": 7},
    "premium_14":  {"label": "Premium 14 дней",   "stars": 85,   "days": 14},
    "premium_30":  {"label": "Premium 30 дней",   "stars": 130,  "days": 30},
    "premium_180": {"label": "Premium 6 месяцев", "stars": 430,  "days": 180},
    "premium_365": {"label": "Premium 1 год",     "stars": 690,  "days": 365},
}

# Прайс-лист Искр (в Telegram Stars)
SPARKS_PLANS = {
    "sparks_100":  {"label": "100 Искр ✨",   "stars": 25,  "sparks": 100},
    "sparks_300":  {"label": "300 Искр ✨",   "stars": 68,  "sparks": 300},
    "sparks_700":  {"label": "700 Искр ✨",   "stars": 130, "sparks": 700},
    "sparks_1500": {"label": "1500 Искр ✨",  "stars": 260, "sparks": 1500},
    "sparks_5000": {"label": "5000 Искр ✨",  "stars": 690, "sparks": 5000},
}


# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "Я бот оплаты Tabletone. Здесь можно купить:\n"
        "👑 *Premium подписку* — расширенные возможности\n"
        "✨ *Искры* — валюта для реакций и подарков\n\n"
        "Оплата через Telegram Stars ⭐\n"
        "_(Stars можно купить прямо в Telegram)_"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Купить Premium", callback_data="menu_premium")],
        [InlineKeyboardButton("✨ Купить Искры",   callback_data="menu_sparks")],
        [InlineKeyboardButton("❓ Помощь",         callback_data="menu_help")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ── Меню Premium ──────────────────────────────────────────────────────────────
async def menu_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = []
    for key, plan in PREMIUM_PLANS.items():
        buttons.append([InlineKeyboardButton(
            f"{plan['label']} — {plan['stars']} ⭐",
            callback_data=f"buy_{key}"
        )])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_main")])
    await query.edit_message_text(
        "👑 *Выберите срок Premium подписки:*\n\n"
        "После оплаты Premium активируется автоматически на вашем аккаунте Tabletone.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ── Меню Искр ─────────────────────────────────────────────────────────────────
async def menu_sparks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = []
    for key, plan in SPARKS_PLANS.items():
        buttons.append([InlineKeyboardButton(
            f"{plan['label']} — {plan['stars']} ⭐",
            callback_data=f"buy_{key}"
        )])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_main")])
    await query.edit_message_text(
        "✨ *Выберите количество Искр:*\n\n"
        "Искры зачисляются на ваш аккаунт Tabletone сразу после оплаты.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ── Главное меню (назад) ──────────────────────────────────────────────────────
async def menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Купить Premium", callback_data="menu_premium")],
        [InlineKeyboardButton("✨ Купить Искры",   callback_data="menu_sparks")],
        [InlineKeyboardButton("❓ Помощь",         callback_data="menu_help")],
    ])
    await query.edit_message_text(
        "Выберите что хотите купить:",
        reply_markup=keyboard
    )


# ── Помощь ────────────────────────────────────────────────────────────────────
async def menu_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "❓ *Помощь*\n\n"
        "1. Выберите товар и нажмите кнопку\n"
        "2. Подтвердите оплату через Telegram Stars ⭐\n"
        "3. Укажите свой *username* в Tabletone когда бот спросит\n"
        "4. Товар зачислится автоматически\n\n"
        "Проблемы? Напишите @kotakbaslife",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_main")]
        ])
    )


# ── Инициация покупки ─────────────────────────────────────────────────────────
async def buy_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data[4:]  # убираем "buy_"

    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
    if not plan:
        await query.answer("Неизвестный товар", show_alert=True)
        return

    # Сохраняем что покупает пользователь
    context.user_data["pending_purchase"] = key

    # Спрашиваем username на Tabletone
    await query.edit_message_text(
        f"✅ Вы выбрали: *{plan['label']}* — {plan['stars']} ⭐\n\n"
        "Введите ваш *username* в Tabletone (без @):\n"
        "_Например: romancev228_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Отмена", callback_data="menu_main")]
        ])
    )
    context.user_data["awaiting_username"] = True


# ── Получение username и отправка инвойса ─────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_username"):
        return

    username = update.message.text.strip().lstrip("@")
    if not username or len(username) < 3:
        await update.message.reply_text("❌ Некорректный username. Попробуйте ещё раз:")
        return

    key = context.user_data.get("pending_purchase")
    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
    if not plan:
        await update.message.reply_text("Что-то пошло не так. Напишите /start")
        return

    context.user_data["tabletone_username"] = username
    context.user_data["awaiting_username"] = False

    # Определяем описание
    if key.startswith("premium"):
        description = f"Premium подписка Tabletone на {plan['days']} дней для @{username}"
        title = plan["label"]
    else:
        description = f"{plan['sparks']} Искр в Tabletone для @{username}"
        title = plan["label"]

    # Отправляем инвойс (Telegram Stars)
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=title,
        description=description,
        payload=f"{key}:{username}",  # payload = тип:username
        provider_token="",            # пустой = Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(plan["label"], plan["stars"])],
    )


# ── Pre-checkout (обязательное подтверждение) ─────────────────────────────────
async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    # Всегда подтверждаем
    await query.answer(ok=True)


# ── Успешная оплата ───────────────────────────────────────────────────────────
async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload  # "premium_30:romancev228"
    stars = payment.total_amount

    parts = payload.split(":", 1)
    if len(parts) != 2:
        await update.message.reply_text("⚠️ Ошибка обработки платежа. Напишите @kotakbaslife")
        return

    key, username = parts[0], parts[1]
    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)

    # Активируем через API Tabletone
    import aiohttp
    success = False
    try:
        async with aiohttp.ClientSession() as session:
            if key.startswith("premium"):
                resp = await session.post(
                    f"{SITE_URL}/api/payment/activate-premium",
                    json={
                        "username": username,
                        "days": plan["days"],
                        "secret": os.environ.get("PAYMENT_SECRET", "tabletone_payment_secret"),
                        "stars": stars,
                        "payload": payload,
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                success = data.get("success", False)
            else:
                resp = await session.post(
                    f"{SITE_URL}/api/payment/add-sparks",
                    json={
                        "username": username,
                        "sparks": plan["sparks"],
                        "secret": os.environ.get("PAYMENT_SECRET", "tabletone_payment_secret"),
                        "stars": stars,
                        "payload": payload,
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                success = data.get("success", False)
    except Exception as e:
        logger.error(f"Ошибка активации: {e}")

    if success:
        if key.startswith("premium"):
            msg = (
                f"🎉 *Оплата прошла успешно!*\n\n"
                f"👑 Premium на *{plan['days']} дней* активирован для @{username}\n"
                f"Потрачено: {stars} ⭐"
            )
        else:
            msg = (
                f"🎉 *Оплата прошла успешно!*\n\n"
                f"✨ *{plan['sparks']} Искр* зачислено для @{username}\n"
                f"Потрачено: {stars} ⭐"
            )
    else:
        msg = (
            f"✅ Оплата получена ({stars} ⭐), но автоактивация не удалась.\n"
            f"Напишите @kotakbaslife — активируем вручную.\n"
            f"Ваш payload: `{payload}`"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Роутинг callback_query ────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "menu_premium":
        await menu_premium(update, context)
    elif data == "menu_sparks":
        await menu_sparks(update, context)
    elif data == "menu_main":
        await menu_main(update, context)
    elif data == "menu_help":
        await menu_help(update, context)
    elif data.startswith("buy_"):
        await buy_item(update, context)
    else:
        await update.callback_query.answer()


# ── Запуск ────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
