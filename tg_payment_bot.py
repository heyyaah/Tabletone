"""Telegram payment bot for Tabletone"""
import os, logging, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN         = os.environ.get("PAYMENT_BOT_TOKEN", "8705438057:AAEIeyFixNBr3eH4_4NIso57GKXOFvs3E_M")
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "8081350794"))
CARD_NUMBER       = "+79519603466"
CARD_BANK         = "po nomeru telefona (SBP / lyuboy bank)"
SITE_URL          = os.environ.get("SITE_URL", "https://hi-latest.onrender.com")
PAYMENT_SECRET    = os.environ.get("PAYMENT_SECRET", "tabletone_payment_secret")
SCREENSHOT_TIMEOUT = 600

PREMIUM_PLANS = {
    "premium_7":   {"label": "Premium 7 dney",    "price": "59 rub",  "days": 7},
    "premium_14":  {"label": "Premium 14 dney",   "price": "99 rub",  "days": 14},
    "premium_30":  {"label": "Premium 30 dney",   "price": "149 rub", "days": 30},
    "premium_180": {"label": "Premium 6 mesyacev", "price": "499 rub", "days": 180},
    "premium_365": {"label": "Premium 1 god",     "price": "799 rub", "days": 365},
}
SPARKS_PLANS = {
    "sparks_100":  {"label": "100 Iskr",  "price": "29 rub",  "sparks": 100},
    "sparks_300":  {"label": "300 Iskr",  "price": "79 rub",  "sparks": 300},
    "sparks_700":  {"label": "700 Iskr",  "price": "149 rub", "sparks": 700},
    "sparks_1500": {"label": "1500 Iskr", "price": "299 rub", "sparks": 1500},
    "sparks_5000": {"label": "5000 Iskr", "price": "799 rub", "sparks": 5000},
}

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_TELEGRAM_ID:
            await update.message.reply_text("No access.")
            return
        await func(update, context)
    return wrapper

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Kupit Premium", callback_data="menu_premium")],
        [InlineKeyboardButton("Kupit Iskry",   callback_data="menu_sparks")],
        [InlineKeyboardButton("Kupit NFT",     callback_data="menu_nft")],
    ])

async def _api_post(path, payload):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f"{SITE_URL}{path}",
            json={**payload, "secret": PAYMENT_SECRET},
            timeout=aiohttp.ClientTimeout(total=10)
        )
        return await r.json()

async def _get_nft_list():
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{SITE_URL}/nft/collections", timeout=aiohttp.ClientTimeout(total=10))
            d = await r.json()
            return d.get("collections", [])
    except Exception as e:
        logger.error(f"NFT list error: {e}")
        return []

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Deep link: /start gift_premium_7_username
    args = context.args
    if args:
        param = args[0]
        if param.startswith("gift_"):
            parts = param.split("_", 3)
            if len(parts) == 4 and parts[1] == "premium":
                plan_key = f"premium_{parts[2]}"
                recipient = parts[3]
                plan = PREMIUM_PLANS.get(plan_key)
                if plan:
                    context.user_data["pending_key"] = plan_key
                    context.user_data["gift_recipient"] = recipient
                    context.user_data["awaiting_screenshot"] = True
                    context.user_data["awaiting_username"] = False
                    context.user_data["tabletone_username"] = recipient
                    msg = (
                        "Podarok: " + plan["label"] + " dlya @" + recipient + "\n\n"
                        "Rekvizity oplaty:\n"
                        "Telefon: " + CARD_NUMBER + "\n"
                        "Bank: " + CARD_BANK + "\n"
                        "Summa: " + plan["price"] + "\n\n"
                        "Posle oplaty otprav skrinshot syuda. 10 minut."
                    )
                    await update.message.reply_text(
                        msg,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("Otmena", callback_data="menu_main")
                        ]])
                    )
                    async def timeout_check():
                        await asyncio.sleep(SCREENSHOT_TIMEOUT)
                        if context.user_data.get("awaiting_screenshot"):
                            context.user_data["awaiting_screenshot"] = False
                            try:
                                await update.message.reply_text("Vremya isteklo. Nazhmi /start.")
                            except Exception:
                                pass
                    asyncio.create_task(timeout_check())
                    return
    await update.message.reply_text(
        "Privet! Ya bot oplaty Tabletone.\n\nVyberi chto hochesh kupit:",
        reply_markup=main_kb()
    )

@owner_only
async def cmd_owner_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Owner commands:\n/givepremium <user> <days>\n/givesparks <user> <amount>\n"
        "/givegift <user> <gift_id>\n/givenft <user> <nft_id>\n/giftlist\n/nftlist"
    )

@owner_only
async def cmd_give_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /givepremium <username> <days>"); return
    username = args[0].lstrip("@")
    try: days = int(args[1])
    except ValueError:
        await update.message.reply_text("Days must be a number."); return
    try:
        data = await _api_post("/api/payment/activate-premium", {"username": username, "days": days})
        if data.get("success"):
            await update.message.reply_text(f"Premium {days}d given to @{username}.")
        else:
            await update.message.reply_text(f"Error: {data.get('error','unknown')}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@owner_only
async def cmd_give_sparks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /givesparks <username> <amount>"); return
    username = args[0].lstrip("@")
    try: sparks = int(args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number."); return
    try:
        data = await _api_post("/api/payment/add-sparks", {"username": username, "sparks": sparks})
        if data.get("success"):
            await update.message.reply_text(f"{sparks:+} sparks for @{username}.")
        else:
            await update.message.reply_text(f"Error: {data.get('error','unknown')}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@owner_only
async def cmd_give_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /givegift <username> <gift_id>"); return
    username, gift_id = args[0].lstrip("@"), args[1]
    try:
        data = await _api_post("/api/payment/give-gift", {"username": username, "gift_type_id": gift_id})
        if data.get("success"):
            await update.message.reply_text(f"Gift {gift_id} sent to @{username}.")
        else:
            await update.message.reply_text(f"Error: {data.get('error','unknown')}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@owner_only
async def cmd_give_nft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /givenft <username> <nft_id>"); return
    username, nft_id = args[0].lstrip("@"), args[1]
    try:
        data = await _api_post("/api/payment/buy-nft", {"username": username, "collection_id": nft_id})
        if data.get("success"):
            await update.message.reply_text(f"NFT {nft_id} given to @{username}.")
        else:
            await update.message.reply_text(f"Error: {data.get('error','unknown')}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@owner_only
async def cmd_gift_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{SITE_URL}/api/payment/gift-types", timeout=aiohttp.ClientTimeout(total=10))
            d = await r.json()
        gifts = d.get("gifts", [])
        if not gifts:
            await update.message.reply_text("No gifts found."); return
        lines = [f"{g['id']} - {g['name']} ({g.get('price',0)} sparks)" for g in gifts]
        await update.message.reply_text("Gifts:\n\n" + "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@owner_only
async def cmd_nft_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cols = await _get_nft_list()
    if not cols:
        await update.message.reply_text("No NFT collections."); return
    lines = [f"{c['id']} - {c['name']} (limit: {c.get('max_supply','inf')})" for c in cols]
    await update.message.reply_text("NFT collections:\n\n" + "\n".join(lines))

async def menu_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    buttons = [[InlineKeyboardButton(f"{p['label']} - {p['price']}", callback_data=f"buy_{k}")] for k, p in PREMIUM_PLANS.items()]
    buttons.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    await q.edit_message_text("Choose Premium plan:", reply_markup=InlineKeyboardMarkup(buttons))

async def menu_sparks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    buttons = [[InlineKeyboardButton(f"{p['label']} - {p['price']}", callback_data=f"buy_{k}")] for k, p in SPARKS_PLANS.items()]
    buttons.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    await q.edit_message_text("Choose Sparks amount:", reply_markup=InlineKeyboardMarkup(buttons))

async def menu_nft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cols = await _get_nft_list()
    if not cols:
        await q.edit_message_text("No NFT collections yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_main")]]))
        return
    buttons = [[InlineKeyboardButton(f"{c['name']} - {c.get('price','?')} rub", callback_data=f"buy_nft_{c['id']}")] for c in cols]
    buttons.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    await q.edit_message_text("Choose NFT:", reply_markup=InlineKeyboardMarkup(buttons))

async def menu_main_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("Choose what to buy:", reply_markup=main_kb())

async def buy_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    key = q.data[4:]
    is_nft = key.startswith("nft_")
    if not is_nft:
        plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
        if not plan: return
        label, price = plan['label'], plan['price']
    else:
        nft_id = key[4:]
        cols = await _get_nft_list()
        nft = next((c for c in cols if str(c['id']) == str(nft_id)), None)
        if not nft: return
        label, price = nft['name'], f"{nft.get('price','?')} rub"
    context.user_data["pending_key"] = key
    context.user_data["awaiting_username"] = True
    context.user_data["awaiting_screenshot"] = False
    await q.edit_message_text(
        f"Selected: {label} - {price}\n\nEnter your Tabletone username (without @):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="menu_main")]])
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_username"): return
    username = update.message.text.strip().lstrip("@")
    if len(username) < 3:
        await update.message.reply_text("Username too short."); return
    key = context.user_data.get("pending_key", "")
    is_nft = key.startswith("nft_")
    if not is_nft:
        plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
        if not plan:
            await update.message.reply_text("Error. Type /start"); return
        price = plan['price']
    else:
        nft_id = key[4:]
        cols = await _get_nft_list()
        nft = next((c for c in cols if str(c['id']) == str(nft_id)), None)
        if not nft:
            await update.message.reply_text("Error. Type /start"); return
        price = f"{nft.get('price','?')} rub"
    context.user_data["tabletone_username"] = username
    context.user_data["awaiting_username"] = False
    context.user_data["awaiting_screenshot"] = True
    await update.message.reply_text(
        f"Payment details:\n\nPhone: {CARD_NUMBER}\nBank: {CARD_BANK}\nAmount: {price}\n\nSend screenshot after payment. 10 minutes."
    )
    async def timeout_check():
        await asyncio.sleep(SCREENSHOT_TIMEOUT)
        if context.user_data.get("awaiting_screenshot"):
            context.user_data["awaiting_screenshot"] = False
            try: await update.message.reply_text("Time is up. Type /start to retry.")
            except Exception: pass
    asyncio.create_task(timeout_check())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_screenshot"): return
    context.user_data["awaiting_screenshot"] = False
    key = context.user_data.get("pending_key", "")
    username = context.user_data.get("tabletone_username", "?")
    is_nft = key.startswith("nft_")
    if not is_nft:
        plan = PREMIUM_PLANS.get(key) or SPARKS_PLANS.get(key)
        label = plan['label'] if plan else key
        price = plan['price'] if plan else "?"
    else:
        nft_id = key[4:]
        cols = await _get_nft_list()
        nft = next((c for c in cols if str(c['id']) == str(nft_id)), None)
        label = nft['name'] if nft else key
        price = f"{nft.get('price','?')} rub" if nft else "?"
    user = update.effective_user
    user_info = f"@{user.username}" if user.username else f"id:{user.id}"
    photo_file_id = update.message.photo[-1].file_id
    context.user_data["pending_user_chat_id"] = update.effective_chat.id
    await update.message.reply_text("Screenshot received! Waiting for admin confirmation.")
    if not OWNER_TELEGRAM_ID:
        logger.warning("OWNER_TELEGRAM_ID not set!"); return
    confirm_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirm", callback_data=f"confirm_{key}_{username}_{update.effective_chat.id}"),
        InlineKeyboardButton("Reject",  callback_data=f"reject_{key}_{username}_{update.effective_chat.id}"),
    ]])
    await context.bot.send_photo(
        chat_id=OWNER_TELEGRAM_ID, photo=photo_file_id,
        caption=f"New payment!\nTG: {user_info}\nTabletone: @{username}\nItem: {label}\nPrice: {price}\n\nConfirm?",
        reply_markup=confirm_kb
    )

async def handle_confirm_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if update.effective_user.id != OWNER_TELEGRAM_ID:
        await q.answer("No access", show_alert=True); return
    action = "confirm" if q.data.startswith("confirm_") else "reject"
    raw = q.data[len(action)+1:]
    key = None
    if raw.startswith("nft_"):
        parts = raw.split("_")
        key = f"nft_{parts[1]}"
        remainder = "_".join(parts[2:])
    else:
        for k in list(PREMIUM_PLANS.keys()) + list(SPARKS_PLANS.keys()):
            if raw.startswith(k + "_"):
                key = k; remainder = raw[len(k)+1:]; break
    if not key:
        await q.edit_message_caption("Could not determine item."); return
    last_ = remainder.rfind("_")
    username = remainder[:last_]
    user_chat_id = int(remainder[last_+1:])
    if action == "confirm":
        activated = False
        try:
            is_nft = key.startswith("nft_")
            if is_nft:
                data = await _api_post("/api/payment/buy-nft", {"username": username, "collection_id": key[4:]})
            elif key.startswith("premium"):
                plan = PREMIUM_PLANS[key]
                data = await _api_post("/api/payment/activate-premium", {"username": username, "days": plan["days"]})
            else:
                plan = SPARKS_PLANS[key]
                data = await _api_post("/api/payment/add-sparks", {"username": username, "sparks": plan["sparks"]})
            activated = data.get("success", False)
        except Exception as e:
            logger.error(f"Activation error: {e}")
        is_nft = key.startswith("nft_")
        if is_nft: user_msg = f"Payment confirmed! NFT given to @{username}."
        elif key.startswith("premium"):
            plan = PREMIUM_PLANS[key]; user_msg = f"Payment confirmed! Premium {plan['days']}d for @{username}."
        else:
            plan = SPARKS_PLANS[key]; user_msg = f"Payment confirmed! {plan['sparks']} sparks for @{username}."
        if not activated: user_msg += " (manual activation needed)"
        await context.bot.send_message(chat_id=user_chat_id, text=user_msg)
        await q.edit_message_caption((q.message.caption or "") + f"\n\nConfirmed! {'auto' if activated else 'manual'}")
    else:
        await context.bot.send_message(chat_id=user_chat_id, text="Payment rejected. Contact @kotakbaslife.")
        await q.edit_message_caption((q.message.caption or "") + "\n\nRejected.")

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "menu_premium":    await menu_premium(update, context)
    elif data == "menu_sparks":   await menu_sparks(update, context)
    elif data == "menu_nft":      await menu_nft(update, context)
    elif data == "menu_main":     await menu_main_cb(update, context)
    elif data.startswith("buy_"): await buy_item(update, context)
    elif data.startswith("confirm_") or data.startswith("reject_"):
        await handle_confirm_reject(update, context)
    else: await update.callback_query.answer()

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("ownerhelp",   cmd_owner_help))
    app.add_handler(CommandHandler("givepremium", cmd_give_premium))
    app.add_handler(CommandHandler("givesparks",  cmd_give_sparks))
    app.add_handler(CommandHandler("givegift",    cmd_give_gift))
    app.add_handler(CommandHandler("givenft",     cmd_give_nft))
    app.add_handler(CommandHandler("giftlist",    cmd_gift_list))
    app.add_handler(CommandHandler("nftlist",     cmd_nft_list))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
