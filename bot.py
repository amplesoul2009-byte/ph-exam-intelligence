"""PH Practice Hub / 0xGREY Telegram bot entry point.

Owner-only quiz management; only owner-approved groups can receive quizzes.
Uses the repository's existing SQLAlchemy models and poll services.
"""
from __future__ import annotations

import logging
from datetime import date
from html import escape

from telegram import Update
from telegram.constants import PollType
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)
from sqlalchemy import select

from config import load_settings
from database import SessionLocal, Quiz, QuizQuestion, QuizAttempt
from database_init import initialize_database
from group_access import approve_group, is_group_approved, list_approved_groups, revoke_group
from owner_auth import owner_only
from poll_registry import PollRegistry, close_poll, register_poll
from participant_poll_attempt import register_participant_attempt
from poll_answer_handler import install_poll_answer_handler
from live_poll_service import finalize_attempt, start_attempt
from quiz_import_service import import_quiz_text, QuizImportError
from leaderboard_publisher import publish_group_leaderboard

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ph_practice_hub")


def _owner_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🪷 PH Practice Hub | 0xGREY\n\n"
        "Use /help to see commands.\n"
        "Students: quizzes appear only in owner-approved groups."
    )
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "PH Practice Hub commands\n\n"
        "Public: /start, /help\n\n"
        "Owner only:\n"
        "/approvegroup CHAT_ID — approve a group\n"
        "/revokegroup CHAT_ID — revoke a group\n"
        "/groups — list approved groups\n"
        "/importquiz TITLE | SUBJECT | LEVEL | YYYY-MM-DD\n"
        "Then send the question text in the next message.\n"
        "Subjects: quant, reasoning, english, computer. Levels: prelims, mains.\n"
        "/quizzes — list saved quizzes\n"
        "/approvequiz ID — approve after checking answer keys\n"
        "/publishquiz ID — publish polls to approved groups\n"
        "/endquiz ID CHAT_ID — close its polls and publish leaderboard\n\n"
        "Question format: 1. Question text; A. ...; B. ...; C. ...; D. ...; Answer: B; Explanation: ..."
    )
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def approvegroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /approvegroup CHAT_ID (add the bot to the group first).")
        return
    chat_id = context.args[0]
    try:
        with SessionLocal() as db:
            group = approve_group(db, chat_id, "")
        await update.effective_message.reply_text(f"Approved group {group.chat_id}. Only this allowlisted group can receive quizzes.")
    except Exception as exc:
        logger.exception("approvegroup failed")
        await update.effective_message.reply_text(f"Could not approve group: {exc}")


async def revokegroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /revokegroup CHAT_ID")
        return
    with SessionLocal() as db:
        changed = revoke_group(db, context.args[0])
    await update.effective_message.reply_text("Group access revoked." if changed else "Group was not active or was not found.")


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    with SessionLocal() as db:
        groups = list_approved_groups(db)
    if not groups:
        text = "No groups are currently approved."
    else:
        text = "Approved groups:\n" + "\n".join(f"• {g['chat_title'] or '(untitled)'} — {g['chat_id']}" for g in groups)
    await update.effective_message.reply_text(text)


async def importquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    raw = " ".join(context.args).strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != 4 or not all(parts):
        await update.effective_message.reply_text(
            "Usage:\n/importquiz TITLE | SUBJECT | LEVEL | YYYY-MM-DD\n\n"
            "Then send all numbered questions in your next message."
        )
        return
    title, subject, level, quiz_date = parts
    context.user_data["pending_quiz_import"] = {
        "title": title, "subject": subject, "level": level, "quiz_date": quiz_date
    }
    await update.effective_message.reply_text(
        "Now send the complete question text in one message. Import creates a draft only; it does not publish automatically."
    )


async def receive_import_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.get("pending_quiz_import")
    if not pending or not update.effective_message or not update.effective_message.text:
        return
    if not await owner_only(update, context):
        return
    body = update.effective_message.text
    settings = load_settings()
    try:
        with SessionLocal() as db:
            result = import_quiz_text(
                db, title=pending["title"], subject=pending["subject"],
                level=pending["level"], quiz_date=pending["quiz_date"],
                text=body, settings=settings, max_questions=200,
            )
        context.user_data.pop("pending_quiz_import", None)
        await update.effective_message.reply_text(
            f"Draft saved.\nQuiz ID: {result['quiz_id']}\n"
            f"Questions detected: {result['detected_questions']}\n"
            f"Analysis: {result['analysis_status']}\n\n"
            "Please verify every answer and explanation before /approvequiz ID."
        )
    except (QuizImportError, ValueError) as exc:
        await update.effective_message.reply_text(f"Import not completed: {exc}")
    except Exception as exc:
        logger.exception("quiz import failed")
        await update.effective_message.reply_text(f"Import failed safely: {exc}")


async def quizzes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    with SessionLocal() as db:
        rows = db.scalars(select(Quiz).order_by(Quiz.id.desc()).limit(30)).all()
        lines = [f"#{q.id} | {q.title} | {q.status} | {q.question_count} Q | {q.quiz_date}" for q in rows]
    await update.effective_message.reply_text("Saved quizzes:\n" + ("\n".join(lines) if lines else "None yet."))


async def approvequiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /approvequiz ID")
        return
    quiz_id = int(context.args[0])
    with SessionLocal() as db:
        quiz = db.get(Quiz, quiz_id)
        if quiz is None:
            await update.effective_message.reply_text("Quiz not found.")
            return
        questions = db.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.position)).all()
        if not questions:
            await update.effective_message.reply_text("Cannot approve a quiz with no questions.")
            return
        invalid = [q.position for q in questions if (q.correct_option or "").strip().upper() not in set("ABCD") or not all([q.question_text.strip(), q.option_a.strip(), q.option_b.strip(), q.option_c.strip(), q.option_d.strip()])]
        if invalid:
            await update.effective_message.reply_text("Cannot approve: missing/invalid answer key or option in question(s): " + ", ".join(map(str, invalid)))
            return
        quiz.status = "approved"
        db.commit()
    await update.effective_message.reply_text(f"Quiz #{quiz_id} approved. Review the source answer keys manually before publishing.")


async def publishquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /publishquiz ID")
        return
    quiz_id = int(context.args[0])
    with SessionLocal() as db:
        quiz = db.get(Quiz, quiz_id)
        if quiz is None or quiz.status not in {"approved", "published", "live"}:
            await update.effective_message.reply_text("Quiz not found or not approved. Use /approvequiz ID after checking it.")
            return
        questions = db.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.position)).all()
        groups = list_approved_groups(db)
    if not groups:
        await update.effective_message.reply_text("No approved groups. Use /approvegroup CHAT_ID first.")
        return
    if not questions:
        await update.effective_message.reply_text("This quiz has no questions.")
        return
    sent_count = 0
    for group in groups:
        chat_id = int(group["chat_id"]) if group["chat_id"].lstrip("-").isdigit() else group["chat_id"]
        if not await _is_approved(chat_id):
            continue
        for q in questions:
            options = [q.option_a, q.option_b, q.option_c, q.option_d]
            correct_idx = "ABCD".find((q.correct_option or "").upper())
            if correct_idx < 0:
                continue
            msg = await context.bot.send_poll(
                chat_id=chat_id,
                question=f"{quiz.title} | Q{q.position}/{len(questions)}\n\n{q.question_text}"[:300],
                options=[x[:100] for x in options],
                type=PollType.QUIZ,
                correct_option_id=correct_idx,
                is_anonymous=False,
                explanation=(q.explanation or "")[0:200],
                open_period=max(5, min(int(q.timer_seconds or 30), 600)),
            )
            with SessionLocal() as db:
                register_poll(db, poll_id=msg.poll.id, quiz_id=quiz_id, question_id=q.id, group_id=chat_id, message_id=msg.message_id)
            sent_count += 1
    with SessionLocal() as db:
        quiz = db.get(Quiz, quiz_id)
        if quiz:
            quiz.status = "live"
            db.commit()
    await update.effective_message.reply_text(f"Published {sent_count} question polls across owner-approved groups for quiz #{quiz_id}.")


async def _is_approved(chat_id) -> bool:
    with SessionLocal() as db:
        return is_group_approved(db, chat_id)


async def endquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_only(update, context):
        return
    if len(context.args) != 2 or not all(arg.lstrip("-").isdigit() for arg in context.args):
        await update.effective_message.reply_text("Usage: /endquiz QUIZ_ID CHAT_ID")
        return
    quiz_id, chat_id = map(int, context.args)
    if not await _is_approved(chat_id):
        await update.effective_message.reply_text("Blocked: that group is not owner-approved.")
        return
    # Close this quiz's polls in the selected group and preserve all answers.
    with SessionLocal() as db:
        poll_rows = db.scalars(select(PollRegistry).where(
            PollRegistry.quiz_id == quiz_id,
            PollRegistry.group_id == str(chat_id),
            PollRegistry.open.is_(True),
        )).all()
        poll_details = [(row.poll_id, row.message_id) for row in poll_rows]
    for poll_id, message_id in poll_details:
        try:
            await context.bot.stop_poll(chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.exception("Could not stop poll %s", poll_id)
        with SessionLocal() as db:
            close_poll(db, poll_id)
    with SessionLocal() as db:
        attempts = db.scalars(select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz_id,
            QuizAttempt.group_id == str(chat_id),
            QuizAttempt.status == "in_progress",
        )).all()
        attempt_ids = [attempt.id for attempt in attempts]
    for attempt_id in attempt_ids:
        with SessionLocal() as db:
            finalize_attempt(db, attempt_id=attempt_id)
    await publish_group_leaderboard(context.bot, chat_id, quiz_id)
    await update.effective_message.reply_text(
        f"Quiz #{quiz_id} ended for group {chat_id}. Polls closed; submitted answers retained; leaderboard published if scoring data is valid."
    )


def main() -> None:
    settings = load_settings()
    initialize_database()
    app = Application.builder().token(settings.bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("approvegroup", approvegroup))
    app.add_handler(CommandHandler("revokegroup", revokegroup))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("importquiz", importquiz))
    app.add_handler(CommandHandler("quizzes", quizzes_command))
    app.add_handler(CommandHandler("approvequiz", approvequiz))
    app.add_handler(CommandHandler("publishquiz", publishquiz))
    app.add_handler(CommandHandler("endquiz", endquiz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_import_text))
    install_poll_answer_handler(app)
    logger.info("PH Practice Hub bot starting with polling.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
