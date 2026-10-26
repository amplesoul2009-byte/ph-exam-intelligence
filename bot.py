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

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
)

OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()


# --------------------------------------------------
# PERMISSION CHECK
# --------------------------------------------------

def is_owner(update: Update) -> bool:
    user = update.effective_user

    return bool(
        user
        and OWNER_ID
        and str(user.id) == OWNER_ID
    )


# --------------------------------------------------
# START COMMAND
# --------------------------------------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "🎓 Welcome to 0xGREY!\n\n"
        "Premium Mock Test Platform\n\n"
        "📝 Question Bank\n"
        "📚 Unlimited Categories\n"
        "⏱️ Online Mock Tests\n"
        "📊 Performance Analytics\n"
        "🏆 Leaderboards\n\n"
        "The platform is under development.\n\n"
        "Use /help to see available commands."
    )

    await update.effective_message.reply_text(message)


# --------------------------------------------------
# HELP COMMAND
# --------------------------------------------------

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "📌 0xGREY COMMANDS\n\n"
        "GENERAL COMMANDS\n"
        "/start — Start the bot\n"
        "/help — Show available commands\n"
        "/status — Check bot status\n\n"
        "OWNER-ONLY QUESTION BANK\n"
        "/addcategory English\n"
        "/categories — List categories\n"
        "/addquestion English — Add a question\n"
        "/questioncount — Count questions\n\n"
        "Question format:\n"
        "/addquestion English\n"
        "Question: What is 2 + 2?\n"
        "A: 3\n"
        "B: 4\n"
        "C: 5\n"
        "D: 6\n"
        "Correct: B\n"
        "Explanation: Two plus two equals four."
    )

    await update.effective_message.reply_text(message)


# --------------------------------------------------
# STATUS COMMAND
# --------------------------------------------------

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


# --------------------------------------------------
# ADD CATEGORY
# --------------------------------------------------

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
            "❌ Category name must be 100 characters or fewer."
        )
        return

    try:
        with SessionLocal() as db:
            categories = db.query(Category).all()

            existing = next(
                (
                    category
                    for category in categories
                    if category.name.casefold() == name.casefold()
                ),
                None,
            )

            if existing:
                await update.effective_message.reply_text(
                    "⚠️ This category already exists."
                )
                return

            category = Category(name=name)

            db.add(category)
            db.commit()

        await update.effective_message.reply_text(
            f"✅ Category created successfully!\n\n"
            f"📚 Name: {name}"
        )

    except Exception:
        logger.exception("Failed to create category")

        await update.effective_message.reply_text(
            "❌ Could not create the category. "
            "Please check the hosting logs."
        )


# --------------------------------------------------
# LIST CATEGORIES
# --------------------------------------------------

async def list_categories(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await update.effective_message.reply_text(
            "⛔ Owner permission required."
        )
        return

    try:
        with SessionLocal() as db:
            categories = (
                db.query(Category)
                .order_by(Category.name)
                .all()
            )

            if not categories:
                message = (
                    "📚 No categories created yet.\n\n"
                    "Create one using:\n"
                    "/addcategory English"
                )
            else:
                lines = ["📚 QUESTION BANK CATEGORIES\n"]

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
                        f"• {category.name}: {count} questions"
                    )

                message = "\n".join(lines)

        await update.effective_message.reply_text(message)

    except Exception:
        logger.exception("Failed to list categories")

        await update.effective_message.reply_text(
            "❌ Could not retrieve categories. "
            "Please check the hosting logs."
        )


# --------------------------------------------------
# ADD QUESTION
# --------------------------------------------------

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
            "❌ Please provide the question details."
        )
        return

    command_parts = lines[0].split(maxsplit=1)

    if len(command_parts) != 2:
        await update.effective_message.reply_text(
            "Usage:\n/addquestion English\n"
            "Question: What is 2 + 2?\n"
            "A: 3\n"
            "B: 4\n"
            "C: 5\n"
            "D: 6\n"
            "Correct: B\n"
            "Explanation: Two plus two equals four."
        )
        return

    category_name = command_parts[1].strip()

    fields = {}

    for line in lines[1:]:
        if ":" not in line:
            continue

        key, value = line.split(":", 1)

        fields[key.strip().casefold()] = value.strip()

    question_text = fields.get("question", "")
    option_a = fields.get("a", "")
    option_b = fields.get("b", "")
    option_c = fields.get("c", "")
    option_d = fields.get("d", "")
    correct_option = fields.get("correct", "").upper()
    explanation = fields.get("explanation", "")
    difficulty = fields.get("difficulty", "Moderate").title()

    required_fields = [
        question_text,
        option_a,
        option_b,
        option_c,
        option_d,
    ]

    if not all(required_fields):
        await update.effective_message.reply_text(
            "❌ The question and all four options are required."
        )
        return

    if correct_option not in {"A", "B", "C", "D"}:
        await update.effective_message.reply_text(
            "❌ Correct option must be A, B, C, or D."
        )
        return

    if difficulty not in {"Easy", "Moderate", "Difficult"}:
        await update.effective_message.reply_text(
            "❌ Difficulty must be Easy, Moderate, or Difficult."
        )
        return

    try:
        with SessionLocal() as db:
            categories = db.query(Category).all()

            category = next(
                (
                    item
                    for item in categories
                    if item.name.casefold()
                    == category_name.casefold()
                ),
                None,
            )

            if category is None:
                await update.effective_message.reply_text(
                    f"❌ Category '{category_name}' was not found.\n\n"
                    "Create it first:\n"
                    f"/addcategory {category_name}"
                )
                return

            question = Question(
                category_id=category.id,
                question_text=question_text,
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
                correct_option=correct_option,
                explanation=explanation,
                difficulty=difficulty,
                marks=1.0,
                negative_marks=0.25,
                active=True,
            )

            db.add(question)
            db.commit()
            question_id = question.id

        await update.effective_message.reply_text(
            "✅ QUESTION SAVED SUCCESSFULLY!\n\n"
            f"🆔 Question ID: {question_id}\n"
            f"📚 Category: {category_name}\n"
            f"🎯 Difficulty: {difficulty}\n"
            "➕ Correct marks: +1\n"
            "➖ Wrong marks: -0.25"
        )

    except Exception:
        logger.exception("Failed to save question")

        await update.effective_message.reply_text(
            "❌ Could not save the question. "
            "Please check the hosting logs."
        )


# --------------------------------------------------
# QUESTION COUNT
# --------------------------------------------------

async def question_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await update.effective_message.reply_text(
            "⛔ Owner permission required."
        )
        return

    try:
        with SessionLocal() as db:
            total = (
                db.query(Question)
                .filter(Question.active.is_(True))
                .count()
            )

            categories = db.query(Category).count()

        message = (
            "📊 0xGREY QUESTION BANK\n\n"
            f"📚 Categories: {categories}\n"
            f"📝 Active questions: {total}"
        )

        await update.effective_message.reply_text(message)

    except Exception:
        logger.exception("Failed to count questions")

        await update.effective_message.reply_text(
            "❌ Could not retrieve question-bank statistics."
        )


# --------------------------------------------------
# ERROR HANDLER
# --------------------------------------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Telegram update processing failed",
        exc_info=context.error,
    )


# --------------------------------------------------
# MAIN APPLICATION
# --------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. "
            "Configure it in FadeHost environment variables."
        )

    if not OWNER_ID.isdigit():
        raise RuntimeError(
            "TELEGRAM_OWNER_ID must contain your numeric Telegram user ID."
        )

    # Create database tables before starting the bot.
    init_db()

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

    application.add_handler(
        CommandHandler("addcategory", add_category)
    )

    application.add_handler(
        CommandHandler("categories", list_categories)
    )

    application.add_handler(
        CommandHandler("addquestion", add_question)
    )

    application.add_handler(
        CommandHandler("questioncount", question_count)
    )

    application.add_error_handler(error_handler)

    logger.info("Starting 0xGREY Telegram bot...")

    application.run_polling()


if __name__ == "__main__":
    main()
