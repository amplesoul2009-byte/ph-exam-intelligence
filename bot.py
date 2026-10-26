import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from database import (
    Category,
    Question,
    SessionLocal,
    init_db,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "")


def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(
        user
        and OWNER_ID
        and str(user.id) == OWNER_ID
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "🎓 Welcome to 0xGREY!\n\n"
        "Premium Mock Test Platform\n\n"
        "📝 Question Bank\n"
        "📚 Categories\n"
        "🏆 Mock Tests and Leaderboards\n\n"
        "Use /help to see available commands."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "📌 0xGREY Commands\n\n"
        "General:\n"
        "/start — Start the bot\n"
        "/help — Show commands\n"
        "/status — Check bot status\n\n"
        "Owner-only Question Bank:\n"
        "/addcategory English\n"
        "/categories\n"
        "/addquestion English\n"
        "/questioncount"
    )

    await update.effective_message.reply_text(message)


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if is_owner(update):
        message = (
            "✅ 0xGREY is running.\n"
            "👑 Owner access recognised."
        )
    else:
        message = "✅ 0xGREY bot is running."

    await update.effective_message.reply_text(message)


async def add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await update.effective_message.reply_text(
            "⛔ Owner permission required."
        )
        return

    name = " ".join(context.args).strip()

    if not name:
        await update.effective_message.reply_text(
            "Usage:\n/addcategory English"
        )
        return

    if len(name) > 100:
        await update.effective_message.reply_text(
            "Category name must be 100 characters or fewer."
        )
        return

    with SessionLocal() as db:
        existing = db.query(Category).all()

        if any(c.name.casefold() == name.casefold()
               for c in existing):
            await update.effective_message.reply_text(
                "That category already exists."
            )
            return

        category = Category(name=name)
        db.add(category)
        db.commit()

    await update.effective_message.reply_text(
        f"✅ Category created: {name}"
    )


async def list_categories(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await update.effective_message.reply_text(
            "⛔ Owner permission required."
        )
        return

    with SessionLocal() as db:
        categories = (
            db.query(Category)
            .order_by(Category.name)
            .all()
        )

        if not categories:
            message = "No categories yet.\nUse /addcategory English"
        else:
            lines = ["📚 Question Bank Categories:\n"]

            for category in categories:
                count = (
                    db.query(Question)
                    .filter(
                        Question.category_id == category.id,
                        Question.active.is_(True),
                    )
                    .count()
                )
                lines.append(
                    f"• {category.name} — {count} questions"
                )

            message = "\n".join(lines)

    await update.effective_message.reply_text(message)


async def add_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await update.effective_message.reply_text(
            "⛔ Owner permission required."
        )
        return

    raw_text = update.effective_message.text or ""
    lines = raw_text.splitlines()

    if not lines:
        await update.effective_message.reply_text(
            "Usage: /addquestion English\n"
            "Question: What is 2 + 2?\n"
            "A: 3\nB: 4\nC: 5\nD: 6\n"
            "Correct: B\n"
            "Explanation: Two plus two equals four."
        )
        return

    command_parts = lines[0].split(maxsplit=1)

    if len(command_parts) != 2 or len(lines) < 7:
        await update.effective_message.reply_text(
            "Use this format:\n\n"
            "/addquestion English\n"
            "Question: What is 2 + 2?\n"
            "A: 3\n"
            "B: 4\n"
            "C: 5\n"
            "D: 6\n"
           
