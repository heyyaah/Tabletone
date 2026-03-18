content = open('tg_payment_bot.py', 'r', encoding='utf-8').read()

old = """async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Privet! Ya bot oplaty Tabletone.\\n\\nVyberi chto hochesh kupit:",
        reply_markup=main_kb()
    )"""

new = """async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Deep link: /start gift_premium_7_username  or  /start gift_premium_30_username
    args = context.args
    if args:
        param = args[0]  # e.g. "gift_premium_7_romancev228"
        if param.startswith("gift_"):
            parts = param.split("_", 3)  # ["gift", "premium", "7", "romancev228"]
            if len(parts) == 4 and parts[1] == "premium":
                plan_key = f"premium_{parts[2]}"
                recipient = parts[3]
                plan = PREMIUM_PLANS.get(plan_key)
                if plan:
                    context.user_data["pending_key"] = plan_key
                    context.user_data["gift_recipient"] = recipient
                    context.user_data["awaiting_username"] = False
                    context.user_data["awaiting_screenshot"] = True
                    await update.message.reply_text(
                        f"🎁 Podarok: {plan['label']} dlya @{recipient}\\n\\n"
                        f"Summa: {plan['price']}\\n\\n"
                        f"Rekvizity oplaty:\\n"
                        f"📱 Telefon: {CARD_NUMBER}\\n"
                        f"🏦 Bank: {CARD_BANK}\\n"
                        f"💰 Summa: {plan['price']}\\n\\n"
                        f"Posle oplaty otprav skrinshot syuda. 10 minut.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("Otmena", callback_data="menu_main")
                        ]])
                    )

                    async def timeout_check():
                        await asyncio.sleep(SCREENSHOT_TIMEOUT)
                        if context.user_data.get("awaiting_screenshot"):
                            context.user_data["awaiting_screenshot"] = False
                            try:
                                await update.message.reply_text("Vremya isteklo. Nazhmi /start chtoby poprobovat snova.")
                            except Exception:
                                pass
                    asyncio.create_task(timeout_check())
                    return
    await update.message.reply_text(
        "Privet! Ya bot oplaty Tabletone.\\n\\nVyberi chto hochesh kupit:",
        reply_markup=main_kb()
    )"""

if old in content:
    content = content.replace(old, new)
    print('cmd_start updated with deep link handling')
else:
    print('Pattern not found, trying alternative...')
    # Try with actual newlines
    for i, line in enumerate(content.split('\n')):
        if 'cmd_start' in line and 'async def' in line:
            print(f'  Found at line {i}: {repr(line)}')

open('tg_payment_bot.py', 'w', encoding='utf-8', newline='\n').write(content)
print('Done')
