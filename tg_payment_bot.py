"""
Telegram-бот для приёма платежей Tabletone
Оплата на карту → скриншот → подтверждение владельцем
"""

import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("PAYMENT_BOT_TOKEN", "8705438057:AAEIeyFixNBr3eH4_4NIso57GKXOFvs3E_M")

# Твой Telegram ID — сюда будут приходить заявки на подтверждение
# Узнать свой ID: написать @userinfobot
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))

# Реквизиты
CARD_NUMBER = "+79519603466"
CARD_BANK = "по номеру телефона (СБП / любой банк)"

# URL сайта для автоактивации
SITE_URL = os.environ.get("SITE_URL", "https://hi-latest.onrender.com")
PAYMENT_SECRET = os.environ.get("PAYMENT_SECRET", "tabletone_payment_secret")

# Таймаут ожидания скриншота (секунды)
SCREENSHOT_TIMEOUT = 600  # 10 минут

PREMIUM_PLANS = {
    "premium_7":   {"label": "Premium 7 дней",    "price": "59 ₽",  "days": 7},
    "premium_14":  {"label": "Premium 14 дней",   "price": "99 ₽",  "days": 14},
    "premium_30":  {"label": "Premium 30 дней",   "price": "149 ₽", "days": 30},
    "premium_180": {"label": "Premium 6 месяцев", "price": "499 ₽", "days": 180},
    "premium_365": {"label": "Premium 1 год",     "price": "799 ₽", "days": 365},
}

SPARKS_PLANS = {
    "sparks_100":  {"label": "100 Искр ✨",  "price": "29 ₽",  "sparks": 100},
    "sparks_300":  {"label": "300 Искр ✨",  "price": "79 ₽",  "sparks": 300},
    "sparks_700":  {"label": "700 Искр ✨",  "price": "149 ₽", "sparks": 700},
    "sparks_1500": {"label": "1500 Искр ✨", "price": "299 ₽", "sparks": 1500},
    "sparks_5000": {"label": "5000 Искр ✨", "price": "799 ₽", "sparks": 5000},
}

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Купить Premium", callback_data="menu_premium")],
        [InlineKeyboardButton("✨ Купить Искры",   callback_data="menu_sparks")],
    ])
    await update.message.reply_text(
        "👋 Привет! Я бот оплаты *Tabletone*.\n\n"
        "Выбери что хочешь купить 👇",
        parse_mode="Markdown", reply_markup=kb
    )

# ── Меню Premium ──────────────────────────────────────────────────────────────
async def menu_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    buttons = [[InlineKeyboardButton(
        f"{p['label']} — {p['price']}", callback_data=f"buy_{k}"
    )] for k, p in PREMIUM_PLANS.items()]
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_main")])
    await q.edit_message_text(
        "👑 *Выберите срок Premium:*",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons)
    )

# ── Меню Искр ─────────────────────────────────────────────────────────────────
async def menu_sparks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    buttons = [[InlineKeyboardButton(
        f"{p['label']} — {p['price']}", callback_data=f"buy_{k}"
    )] for k, p in SPARKS_PLANS.items()]
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_main")])
    await q.edit_message_text(
        "✨ *Выберите количество Искр:*",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons)
    )

# ── Главное меню ──────────────────────────────────────────────────────────────
async def menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Купить Premium", callback_data="menu_premium")],
        [InlineKeyboardButton("✨ Купить Искры",   callback_data="menu_sparks")],
    ])
    await q.edit_message_text("Выбери что хочешь купить 👇", reply_markup=kb)

# ── Выбор товара → спрашиваем username ───────────────────────────────────────
async def buy_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data[4:]
    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
    if not plan:
        return
    context.user_data["pending_key"] = key
    context.user_data["awaiting_username"] = True
    context.user_data["awaiting_screenshot"] = False
    await q.edit_message_text(
        f"✅ Вы выбрали: *{plan['label']}* — *{plan['price']}*\n\n"
        "Введите ваш *username* в Tabletone (без @):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Отмена", callback_data="menu_main")
        ]])
    )

# ── Обработка текста (username → реквизиты, потом таймаут) ───────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_username"):
        return
    username = update.message.text.strip().lstrip("@")
    if len(username) < 3:
        await update.message.reply_text("❌ Слишком короткий username. Попробуйте ещё раз:")
        return

    key = context.user_data.get("pending_key")
    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
    if not plan:
        await update.message.reply_text("Что-то пошло не так. Напишите /start")
        return

    context.user_data["tabletone_username"] = username
    context.user_data["awaiting_username"] = False
    context.user_data["awaiting_screenshot"] = True

    await update.message.reply_text(
        f"💳 *Реквизиты для оплаты:*\n\n"
        f"📱 Номер: `{CARD_NUMBER}`\n"
        f"🏦 {CARD_BANK}\n"
        f"💰 Сумма: *{plan['price']}*\n\n"
        f"После перевода пришлите *скриншот* подтверждения оплаты.\n"
        f"⏳ У вас есть *10 минут*.",
        parse_mode="Markdown"
    )

    # Запускаем таймаут
    async def timeout_check():
        await asyncio.sleep(SCREENSHOT_TIMEOUT)
        if context.user_data.get("awaiting_screenshot"):
            context.user_data["awaiting_screenshot"] = False
            context.user_data["pending_key"] = None
            try:
                await update.message.reply_text(
                    "⏰ Время вышло. Скриншот не был получен.\n"
                    "Если вы оплатили — напишите /start и попробуйте снова."
                )
            except Exception:
                pass

    asyncio.create_task(timeout_check())

# ── Получение скриншота ───────────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_screenshot"):
        return

    context.user_data["awaiting_screenshot"] = False
    key = context.user_data.get("pending_key")
    username = context.user_data.get("tabletone_username", "?")
    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
    if not plan:
        await update.message.reply_text("Что-то пошло не так. Напишите /start")
        return

    user = update.effective_user
    user_info = f"@{user.username}" if user.username else f"id:{user.id}"

    # Сохраняем данные для подтверждения
    photo_file_id = update.message.photo[-1].file_id
    context.user_data["pending_photo_id"] = photo_file_id
    context.user_data["pending_user_chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        "📨 Скриншот получен! Ожидайте подтверждения от администратора.\n"
        "Обычно это занимает до 15 минут."
    )

    # Отправляем владельцу
    if not OWNER_TELEGRAM_ID:
        logger.warning("OWNER_TELEGRAM_ID не задан! Установите переменную окружения.")
        return

    confirm_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Да, подтвердить",
            callback_data=f"confirm_{key}_{username}_{update.effective_chat.id}"
        ),
        InlineKeyboardButton(
            "❌ Нет, отклонить",
            callback_data=f"reject_{key}_{username}_{update.effective_chat.id}"
        ),
    ]])

    await context.bot.send_photo(
        chat_id=OWNER_TELEGRAM_ID,
        photo=photo_file_id,
        caption=(
            f"⚠️ *Внимание!*\n"
            f"Новая заявка на подтверждение оплаты!\n\n"
            f"👤 Пользователь TG: {user_info}\n"
            f"🎮 Username Tabletone: @{username}\n"
            f"🛒 Товар: {plan['label']}\n"
            f"💰 Сумма: {plan['price']}\n\n"
            f"Вы подтверждаете перевод?"
        ),
        parse_mode="Markdown",
        reply_markup=confirm_kb
    )

# ── Подтверждение / отклонение владельцем ────────────────────────────────────
async def handle_confirm_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Только владелец может подтверждать
    if update.effective_user.id != OWNER_TELEGRAM_ID:
        await q.answer("Нет доступа", show_alert=True)
        return

    parts = q.data.split("_", 3)
    # parts: ["confirm"/"reject", key_part1, key_part2(optional), username, chat_id]
    # callback_data формат: confirm_premium_30_username_chatid
    action = parts[0]  # confirm / reject
    # Восстанавливаем key и остальное
    raw = q.data[len(action)+1:]  # "premium_30_username_chatid"
    # Ищем key среди известных
    key = None
    for k in list(PREMIUM_PLANS.keys()) + list(SPARKS_PLANS.keys()):
        if raw.startswith(k + "_"):
            key = k
            remainder = raw[len(k)+1:]  # "username_chatid"
            break
    if not key:
        await q.edit_message_caption("⚠️ Не удалось определить товар.")
        return

    last_underscore = remainder.rfind("_")
    username = remainder[:last_underscore]
    user_chat_id = int(remainder[last_underscore+1:])
    plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)

    if action == "confirm":
        # Активируем через API
        activated = False
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                if key.startswith("premium"):
                    resp = await session.post(
                        f"{SITE_URL}/api/payment/activate-premium",
                        json={"username": username, "days": plan["days"], "secret": PAYMENT_SECRET},
                        timeout=aiohttp.ClientTimeout(total=10)
                    )
                else:
                    resp = await session.post(
                        f"{SITE_URL}/api/payment/add-sparks",
                        json={"username": username, "sparks": plan["sparks"], "secret": PAYMENT_SECRET},
                        timeout=aiohttp.ClientTimeout(total=10)
                    )
                data = await resp.json()
                activated = data.get("success", False)
        except Exception as e:
            logger.error(f"Ошибка активации: {e}")

        # Сообщаем пользователю
        if key.startswith("premium"):
            user_msg = (
                f"🎉 *Оплата подтверждена!*\n\n"
                f"👑 Premium на *{plan['days']} дней* активирован для @{username}!\n"
                f"Перезайдите в Tabletone если изменения не видны."
            )
        else:
            user_msg = (
                f"🎉 *Оплата подтверждена!*\n\n"
                f"✨ *{plan['sparks']} Искр* зачислено для @{username}!\n"
                f"Перезайдите в Tabletone если изменения не видны."
            )
        if not activated:
            user_msg += "\n\n_(Автоактивация не удалась — администратор активирует вручную)_"

        await context.bot.send_message(chat_id=user_chat_id, text=user_msg, parse_mode="Markdown")
        await q.edit_message_caption(
            q.message.caption + f"\n\n✅ *Подтверждено!* Активация: {'✓' if activated else 'вручную'}",
            parse_mode="Markdown"
        )

    else:  # reject
        await context.bot.send_message(
            chat_id=user_chat_id,
            text=(
                "❌ *Оплата отклонена.*\n\n"
                "Администратор не подтвердил перевод.\n"
                "Если вы уверены что оплатили — напишите @kotakbaslife."
            ),
            parse_mode="Markdown"
        )
        await q.edit_message_caption(
            q.message.caption + "\n\n❌ *Отклонено.*",
            parse_mode="Markdown"
        )


# ── Роутинг callback ──────────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "menu_premium":
        await menu_premium(update, context)
    elif data == "menu_sparks":
        await menu_sparks(update, context)
    elif data == "menu_main":
        await menu_main(update, context)
    elif data.startswith("buy_"):
        await buy_item(update, context)
    elif data.startswith("confirm_") or data.startswith("reject_"):
        await handle_confirm_reject(update, context)
    else:
        await update.callback_query.answer()


# ── Запуск ────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
