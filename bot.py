import os
import logging
from telegram import Update
from telegram.ext import (
Application,
CommandHandler,
ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(name)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "")

async def start(
update: Update,
context: ContextTypes.DEFAULT_TYPE
):
user = update.effective_user

await update.effective_message.reply_text(
    "🎓 Welcome to 0xGREY!\n\n"
    "Premium Mock Test Platform\n\n"
    "Features planned:\n"
    "📝 Online mock tests\n"
    "📊 Performance analytics\n"
    "🏆 Leaderboards\n"
    "📚 Question bank\n\n"
    "The platform is under development.\n\n"
    "Use /help to see available commands."
)

async def help_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE
):
await update.effective_message.reply_text(
"📌 Available commands\n\n"
"/start — Start the bot\n"
"/help — Show commands\n"
"/status — Check bot status"
)

async def status(
update: Update,
context: ContextTypes.DEFAULT_TYPE
):
user = update.effective_user

if user and OWNER_ID and str(user.id) == OWNER_ID:
    message = (
        "✅ Bot is running.\n"
        "👑 Owner access recognised."
    )
else:
    message = "✅ 0xGREY bot is running."

await update.effective_message.reply_text(message)

def main():
if not BOT_TOKEN:
raise RuntimeError(
"TELEGRAM_BOT_TOKEN is missing. "
"Configure it in your hosting environment."
)

application = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)

application.add_handler(
    CommandHandler("start", start)
)
application.add_handler(
    CommandHandler("help", help_command)
)
application.add_handler(
    CommandHandler("status", status)
)

logger.info("Starting 0xGREY Telegram bot...")

application.run_polling()

if name == "main":
main()
