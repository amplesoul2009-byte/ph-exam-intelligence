import os
import logging
import random
import asyncio

from telegram import Update
from telegram.error import TelegramError
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


# ==================================================
# CONFIGURATION
# ==================================================

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


# ==================================================
# OWNER PERMISSION
# ==================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    return bool(
        user
        and OWNER_ID
        and str(user.id) == OWNER_ID
    )


async def owner_required(update: Update) -> bool:
    if not is_owner(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ This command is restricted to the bot owner."
            )
        return False

    return True


# ==================================================
# START
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "🎓 Welcome to 0xGREY!\n\n"
        "Premium Mock Test Platform\n\n"
        "📚 Question Bank\n"
        "📝 Telegram Quiz Polls\n"
        "🔀 Shuffled Answer Options\n"
        "📊 Quiz Publishing\n"
        "🏆 Leaderboards — planned\n\n"
        "Use /help to see available commands."
    )


# ==================================================
# HELP
# ==================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "📌 0xGREY COMMANDS\n\n"
        "GENERAL\n"
        "/start — Start the bot\n"
        "/help — Show commands\n"
        "/status — Bot status\n\n"
        "OWNER-ONLY QUESTION BANK\n"
        "/addcategory English\n"
        "/categories\n"
        "/addquestion English\n"
        "/questioncount\n\n"
        "QUIZ POLLS\n"
        "/testpoll — Send a demo poll\n"
        "/publishquiz English 5\n"
        "/publishquiz Banking Awareness 10\n\n"
        "QUESTION FORMAT\n"
        "/addquestion English\n"
        "Question: What is 2 + 2?\n"
        "A: 3\n"
        "B: 4\n"
        "C: 5\n"
        "D: 6\n"
        "Correct: B\n"
        "Explanation: Two plus two equals four.\n"
        "Difficulty: Easy"
    )


# ==================================================
# STATUS
# ==================================================

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
        message = "✅ 0xGREY is running."

    await update.effective_message.reply_text(message)


# ==================================================
# ADD CATEGORY
# ==================================================

async def add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    name = " ".join(context.args).strip()

    if not name:
        await update.effective_message.reply_text(
            "Usage: /addcategory English"
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

            db.add(Category(name=name))
            db.commit()

        await update.effective_message.reply_text(
            f"✅ Category created!\n📚 Name: {name}"
        )

    except Exception:
        logger.exception("Failed to create category")

        await update.effective_message.reply_text(
            "❌ Could not create the category. "
            "Check the hosting logs."
        )


# ==================================================
# LIST CATEGORIES
# ==================================================

async def list_categories(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    try:
        with SessionLocal() as db:
            categories = (
                db.query(Category)
                .order_by(Category.name)
                .all()
            )

            if not categories:
                message = "📚 No categories found."
            else:
                lines = ["📚 QUESTION BANK\n"]

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
            "❌ Could not retrieve categories."
        )


# ==================================================
# ADD QUESTION
# ==================================================

async def add_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    raw_text = update.effective_message.text or ""
    lines = raw_text.splitlines()

    if not lines:
        return

    command_parts = lines[0].split(maxsplit=1)

    if len(command_parts) != 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/addquestion English\n"
            "Question: What is 2 + 2?\n"
            "A: 3\n"
            "B: 4\n"
            "C: 5\n"
            "D: 6\n"
            "Correct: B\n"
            "Explanation: Two plus two equals four.\n"
            "Difficulty: Easy"
        )
        return

    category_name = command_parts[1].strip()

    fields = {}

    for line in lines[1:]:
        if ":" in line:
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

    if not all([
        question_text,
        option_a,
        option_b,
        option_c,
        option_d,
    ]):
        await update.effective_message.reply_text(
            "❌ The question and all four options are required."
        )
        return

    if correct_option not in {"A", "B", "C", "D"}:
        await update.effective_message.reply_text(
            "❌ Correct must be A, B, C, or D."
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
                    item for item in categories
                    if item.name.casefold() == category_name.casefold()
                ),
                None,
            )

            if category is None:
                await update.effective_message.reply_text(
                    f"❌ Category '{category_name}' not found.\n"
                    f"Create it first with /addcategory {category_name}"
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
            "✅ QUESTION SAVED\n\n"
            f"🆔 ID: {question_id}\n"
            f"📚 Category: {category_name}\n"
            f"🎯 Difficulty: {difficulty}\n"
            "➕ Correct: +1\n"
            "➖ Wrong: -0.25"
        )

    except Exception:
        logger.exception("Failed to save question")

        await update.effective_message.reply_text(
            "❌ Could not save the question. Check hosting logs."
        )


# ==================================================
# QUESTION COUNT
# ==================================================

async def question_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    try:
        with SessionLocal() as db:
            total = (
                db.query(Question)
                .filter(Question.active.is_(True))
                .count()
            )

            categories = db.query(Category).count()

        await update.effective_message.reply_text(
            "📊 0xGREY QUESTION BANK\n\n"
            f"📚 Categories: {categories}\n"
            f"📝 Active questions: {total}"
        )

    except Exception:
        logger.exception("Failed to count questions")

        await update.effective_message.reply_text(
            "❌ Could not retrieve question-bank statistics."
        )


# ==================================================
# TEST POLL
# ==================================================

async def test_poll(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    await update.effective_message.reply_text(
        "📊 Publishing a sample quiz poll..."
    )

    try:
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question="0xGREY Demo Quiz: What is 2 + 2?",
            options=["3", "4", "5", "6"],
            type="quiz",
            correct_option_id=1,
            is_anonymous=False,
            explanation="Two plus two equals four.",
        )

    except TelegramError:
        logger.exception("Failed to send demo poll")

        await update.effective_message.reply_text(
            "❌ Could not send the demo poll. Check bot permissions."
        )


# ==================================================
# PUBLISH SAVED QUESTIONS AS TELEGRAM QUIZ POLLS
# ==================================================

async def publish_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/publishquiz English 5\n"
            "/publishquiz Banking Awareness 10\n\n"
            "The final number is the question count."
        )
        return

    try:
        count = int(context.args[-1])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ The last argument must be a number.\n"
            "Example: /publishquiz English 5"
        )
        return

    if not 1 <= count <= 200:
        await update.effective_message.reply_text(
            "❌ Choose between 1 and 200 questions."
        )
        return

    category_name = " ".join(context.args[:-1]).strip()

    try:
        with SessionLocal() as db:
            categories = db.query(Category).all()

            category = next(
                (
                    item for item in categories
                    if item.name.casefold() == category_name.casefold()
                ),
                None,
            )

            if category is None:
                await update.effective_message.reply_text(
                    f"❌ Category '{category_name}' not found.\n"
                    "Use /categories to check category names."
                )
                return

            questions = (
                db.query(Question)
                .filter(
                    Question.category_id == category.id,
                    Question.active.is_(True),
                )
                .all()
            )

            if not questions:
                await update.effective_message.reply_text(
                    "❌ This category has no active questions."
                )
                return

            if len(questions) < count:
                await update.effective_message.reply_text(
                    f"⚠️ You requested {count} questions, "
                    f"but only {len(questions)} are available.\n"
                    f"Publishing the available {len(questions)}."
                )
                count = len(questions)

            selected_questions = random.sample(questions, count)

            # Copy database fields before the session closes.
            quiz_data = [
                {
                    "question": q.question_text,
                    "options": [
                        ("A", q.option_a),
                        ("B", q.option_b),
                        ("C", q.option_c),
                        ("D", q.option_d),
                    ],
                    "correct": q.correct_option,
                    "explanation": q.explanation or "",
                }
                for q in selected_questions
            ]

        await update.effective_message.reply_text(
            f"📢 Publishing {len(quiz_data)} quiz polls "
            f"for {category_name}.\n\n"
            "Each question will appear as a separate Telegram quiz poll."
        )

        sent = 0
        failed = 0

        for item in quiz_data:
            # Keep each option paired with its original answer label.
            options = item["options"].copy()
            random.shuffle(options)

            option_texts = [
                option_text[:100]
                for _, option_text in options
            ]

            correct_index = next(
                index
                for index, (label, _) in enumerate(options)
                if label == item["correct"]
            )

            question_text = item["question"][:300]
            explanation = item["explanation"][:200] or None

            try:
                await context.bot.send_poll(
                    chat_id=update.effective_chat.id,
                    question=question_text,
                    options=option_texts,
                    type="quiz",
                    correct_option_id=correct_index,
                    is_anonymous=False,
                    explanation=explanation,
                )

                sent += 1

                # Avoid sending a large burst of messages.
                await asyncio.sleep(1.1)

            except TelegramError:
                failed += 1
                logger.exception("Failed to publish a quiz question")

        await update.effective_message.reply_text(
            "🏁 QUIZ PUBLISHING FINISHED\n\n"
            f"📚 Category: {category_name}\n"
            f"✅ Polls published: {sent}\n"
            f"❌ Failed: {failed}"
        )

    except Exception:
        logger.exception("Quiz publishing failed")

        await update.effective_message.reply_text(
            "❌ Quiz publishing failed. Check the hosting logs."
        )


# ==================================================
# ERROR HANDLER
# ==================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Telegram update processing failed",
        exc_info=context.error,
    )


# ==================================================
# MAIN APPLICATION
# ==================================================

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

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))

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

    application.add_handler(
        CommandHandler("testpoll", test_poll)
    )

    application.add_handler(
        CommandHandler("publishquiz", publish_quiz)
    )

    application.add_error_handler(error_handler)

    logger.info("Starting 0xGREY Telegram bot...")

    application.run_polling()


if __name__ == "__main__":
    main()
