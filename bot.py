"""PH Practice Hub / 0xGREY Telegram bot entry point.

Owner-controlled quiz, subject and author workflow management.
Only owner-approved groups receive quiz polls.
Participant attempts are created when students answer registered polls.
"""

from __future__ import annotations

import logging
import random
from io import BytesIO

from sqlalchemy import select
from telegram import InputFile, Update
from telegram.constants import PollType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import load_settings
from database import (
    SessionLocal,
    Quiz,
    QuizAttempt,
    QuizQuestion,
)
from database_init import initialize_database
from group_access import (
    approve_group,
    is_group_approved,
    list_approved_groups,
    revoke_group,
)
from owner_auth import owner_only
from poll_registry import (
    PollRegistry,
    close_poll,
    register_poll,
)
from poll_answer_handler import install_poll_answer_handler
from live_poll_service import finalize_attempt
from quiz_import_service import (
    QuizImportError,
    append_quiz_batch,
    build_combined_quiz_text,
    import_quiz_text,
    mark_draft_saved,
    review_quiz_draft,
)
from leaderboard_publisher import publish_group_leaderboard
from subjects import (
    add_subject,
    deactivate_subject,
    ensure_default_subjects,
    list_subjects,
    rename_subject,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ph_practice_hub")


def _message(update: Update):
    return update.effective_message


def _valid_id(value: str) -> bool:
    return value.isdigit() and int(value) > 0


# ---------------- BASIC COMMANDS ----------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if message:
        await message.reply_text(
            "🪷 PH Practice Hub | 0xGREY\n\n"
            "Use /help to view commands.\n"
            "Students: answer quiz polls in approved groups."
        )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message:
        return

    await message.reply_text(
        "🪷 PH PRACTICE HUB — COMMANDS\n\n"
        "PUBLIC:\n"
        "/start\n"
        "/help\n\n"
        "OWNER ONLY — GROUPS:\n"
        "/approvegroup CHAT_ID\n"
        "/revokegroup CHAT_ID\n"
        "/groups\n\n"
        "OWNER ONLY — SUBJECTS:\n"
        "/subjects\n"
        "/addsubject NAME\n"
        "/renamesubject SUBJECT_ID | NEW NAME\n"
        "/deletesubject SUBJECT_ID\n\n"
        "OWNER ONLY — QUIZZES:\n"
        "/importquiz TITLE | SUBJECT | LEVEL | YYYY-MM-DD\n"
        "/addquestions QUIZ_ID\n"
        "/cancelimport\n"
        "/quizzes\n"
        "/reviewquiz QUIZ_ID\n"
        "/savedraft QUIZ_ID\n"
        "/approvequiz QUIZ_ID\n"
        "/publishquiz QUIZ_ID\n"
        "/endquiz QUIZ_ID CHAT_ID\n\n"
        "Levels: prelims, mains\n\n"
        "AUTHOR WORKFLOW:\n"
        "1. Use /importquiz with the quiz details.\n"
        "2. Send the numbered questions in your next message.\n"
        "3. Use /addquestions QUIZ_ID to append another batch.\n"
        "4. Use /reviewquiz QUIZ_ID to validate the draft.\n"
        "5. Use /savedraft QUIZ_ID to export a TXT copy.\n"
        "6. Check all questions, options and answer keys yourself.\n"
        "7. Use /approvequiz QUIZ_ID only after your review.\n"
        "8. Use /publishquiz QUIZ_ID to publish to approved groups.\n\n"
        "QUESTION FORMAT:\n"
        "1. Question text\n"
        "A. First option\n"
        "B. Second option\n"
        "C. Third option\n"
        "D. Fourth option\n"
        "Answer: B\n"
        "Explanation: Explanation text"
    )


# ---------------- GROUP MANAGEMENT ----------------

async def approvegroup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if not context.args:
        await message.reply_text(
            "Usage: /approvegroup CHAT_ID\n"
            "Add the bot to the group first."
        )
        return

    chat_id = context.args[0]

    try:
        with SessionLocal() as db:
            group = approve_group(db, chat_id, "")

        await message.reply_text(
            f"Approved group: {group.chat_id}\n"
            "Only owner-approved groups may receive quizzes."
        )
    except Exception as exc:
        logger.exception("Failed to approve group")
        await message.reply_text(
            f"Could not approve group: {str(exc)[:500]}"
        )


async def revokegroup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if not context.args:
        await message.reply_text("Usage: /revokegroup CHAT_ID")
        return

    try:
        with SessionLocal() as db:
            changed = revoke_group(db, context.args[0])

        await message.reply_text(
            "Group access revoked."
            if changed
            else "Group is not active or was not found."
        )
    except Exception:
        logger.exception("Failed to revoke group")
        await message.reply_text(
            "Could not revoke group. Check the logs."
        )


async def groups_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    with SessionLocal() as db:
        groups = list_approved_groups(db)

    if not groups:
        await message.reply_text("No groups are currently approved.")
        return

    lines = ["APPROVED TELEGRAM GROUPS", ""]

    for group in groups:
        lines.append(
            f"{group['chat_title'] or '(untitled)'} — "
            f"{group['chat_id']}"
        )

    await message.reply_text("\n".join(lines)[:4000])


# ---------------- SUBJECT MANAGEMENT ----------------

async def subjects_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    try:
        with SessionLocal() as db:
            ensure_default_subjects(db)
            subjects = list_subjects(db)

            lines = ["PH PRACTICE HUB — SUBJECTS", ""]

            for item in subjects:
                status = "ACTIVE" if item.active else "DISABLED"
                lines.append(
                    f"{item.subject_id} — {item.name} [{status}]"
                )

        await message.reply_text(
            "\n".join(lines)[:4000]
            if subjects
            else "No subjects configured."
        )
    except Exception:
        logger.exception("Could not list subjects")
        await message.reply_text(
            "Could not list subjects. Check deployment logs."
        )


async def addsubject_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    name = " ".join(context.args).strip()

    if not name:
        await message.reply_text(
            "Usage: /addsubject Current Affairs"
        )
        return

    try:
        with SessionLocal() as db:
            ensure_default_subjects(db)
            item, action = add_subject(db, name)

        await message.reply_text(
            f"Subject {action}: {item.name}\n"
            f"Subject ID: {item.subject_id}\n"
            "Use this ID in /importquiz."
        )
    except ValueError as exc:
        await message.reply_text(str(exc))
    except Exception:
        logger.exception("Could not add subject")
        await message.reply_text(
            "Could not add subject. Check deployment logs."
        )


async def renamesubject_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    parts = " ".join(context.args).split("|", 1)

    if len(parts) != 2 or not all(part.strip() for part in parts):
        await message.reply_text(
            "Usage: /renamesubject SUBJECT_ID | NEW NAME\n"
            "Example: /renamesubject current_affairs | Daily CA"
        )
        return

    try:
        with SessionLocal() as db:
            item = rename_subject(
                db,
                parts[0].strip(),
                parts[1].strip(),
            )

        await message.reply_text(
            "Subject renamed successfully.\n"
            f"ID: {item.subject_id}\n"
            f"Name: {item.name}"
        )
    except ValueError as exc:
        await message.reply_text(str(exc))
    except Exception:
        logger.exception("Could not rename subject")
        await message.reply_text(
            "Could not rename subject. Check deployment logs."
        )


async def deletesubject_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if len(context.args) != 1:
        await message.reply_text(
            "Usage: /deletesubject SUBJECT_ID\n"
            "This disables the subject for new imports. "
            "Existing quizzes and results are preserved."
        )
        return

    try:
        with SessionLocal() as db:
            item = deactivate_subject(db, context.args[0])

        await message.reply_text(
            f"Subject disabled: {item.name} ({item.subject_id}).\n"
            "Existing quizzes and student results are preserved."
        )
    except ValueError as exc:
        await message.reply_text(str(exc))
    except Exception:
        logger.exception("Could not disable subject")
        await message.reply_text(
            "Could not disable subject. Check deployment logs."
        )


# ---------------- QUIZ IMPORT AND APPEND ----------------

async def importquiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if update.effective_user is None:
        return

    raw = " ".join(context.args).strip()
    parts = [part.strip() for part in raw.split("|")]

    if len(parts) != 4 or not all(parts):
        await message.reply_text(
            "Usage:\n"
            "/importquiz TITLE | SUBJECT | LEVEL | YYYY-MM-DD\n\n"
            "Example:\n"
            "/importquiz Banking Quiz | quant | prelims | 2026-10-29"
        )
        return

    title, subject, level, quiz_date = parts

    context.user_data.pop("pending_quiz_append", None)
    context.user_data["pending_quiz_import"] = {
        "title": title,
        "subject": subject,
        "level": level,
        "quiz_date": quiz_date,
    }

    await message.reply_text(
        "Send the complete numbered questions in your next message.\n\n"
        "This creates an unpublished draft only.\n"
        "Use /cancelimport if you want to cancel."
    )


async def addquestions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if len(context.args) != 1 or not _valid_id(context.args[0]):
        await message.reply_text(
            "Usage: /addquestions QUIZ_ID"
        )
        return

    quiz_id = int(context.args[0])

    try:
        with SessionLocal() as db:
            quiz = db.get(Quiz, quiz_id)

            if quiz is None:
                await message.reply_text("Quiz not found.")
                return

            if quiz.status not in {"draft", "draft_saved"}:
                await message.reply_text(
                    "Questions can only be added to an unpublished draft."
                )
                return

        context.user_data.pop("pending_quiz_import", None)
        context.user_data["pending_quiz_append"] = {
            "quiz_id": quiz_id,
        }

        await message.reply_text(
            f"Ready to append questions to draft #{quiz_id}.\n"
            "Send the next numbered question batch as a text message.\n"
            "Existing questions will be preserved.\n"
            "The quiz remains unpublished.\n"
            "Use /cancelimport to cancel this pending operation."
        )

    except Exception:
        logger.exception("Could not prepare question append")
        await message.reply_text(
            "Could not prepare the append operation. Check the logs."
        )


async def cancelimport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    context.user_data.pop("pending_quiz_import", None)
    context.user_data.pop("pending_quiz_append", None)

    await message.reply_text(
        "Pending import or append operation cancelled. "
        "Previously saved drafts and questions were not deleted."
    )


async def receive_import_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)

    if not message or not message.text:
        return

    if not await owner_only(update, context):
        return

    pending_append = context.user_data.get("pending_quiz_append")
    pending_import = context.user_data.get("pending_quiz_import")

    if not pending_append and not pending_import:
        return

    if pending_append:
        quiz_id = pending_append["quiz_id"]

        try:
            with SessionLocal() as db:
                result = append_quiz_batch(
                    db,
                    quiz_id=quiz_id,
                    text=message.text,
                    max_questions=200,
                )

            context.user_data.pop("pending_quiz_append", None)

            await message.reply_text(
                "QUESTION BATCH APPENDED\n\n"
                f"Quiz ID: {result['quiz_id']}\n"
                f"Questions added: {result['added_questions']}\n"
                f"Total questions: {result['total_questions']}\n"
                "Status: unpublished draft\n\n"
                f"Use /reviewquiz {quiz_id} to validate the draft.\n"
                f"Use /savedraft {quiz_id} to export a TXT copy."
            )

        except (QuizImportError, ValueError) as exc:
            await message.reply_text(
                f"Append not completed:\n{str(exc)[:1500]}\n\n"
                "Your existing saved questions were not intentionally "
                "replaced. Correct the batch and send it again, or "
                "use /cancelimport."
            )
        except Exception:
            logger.exception("Question append failed")
            await message.reply_text(
                "Append failed. Check deployment logs before retrying. "
                "The existing draft should be checked before another attempt."
            )

        return

    pending = pending_import

    try:
        settings = load_settings()

        with SessionLocal() as db:
            result = import_quiz_text(
                db,
                title=pending["title"],
                subject=pending["subject"],
                level=pending["level"],
                quiz_date=pending["quiz_date"],
                text=message.text,
                settings=settings,
                max_questions=200,
            )

        context.user_data.pop("pending_quiz_import", None)

        await message.reply_text(
            "DRAFT SAVED\n\n"
            f"Quiz ID: {result['quiz_id']}\n"
            f"Title: {result['title']}\n"
            f"Questions detected: {result['detected_questions']}\n"
            f"Analysis status: {result['analysis_status']}\n\n"
            "Review every question, option, answer key and explanation.\n"
            f"Use /reviewquiz {result['quiz_id']} to check the draft.\n"
            f"Use /savedraft {result['quiz_id']} to export a TXT copy.\n"
            "Approval and publishing are separate owner actions."
        )

    except (QuizImportError, ValueError) as exc:
        await message.reply_text(
            f"Import not completed:\n{str(exc)[:1500]}\n\n"
            "Correct the text and send it again, or use /cancelimport."
        )

    except Exception:
        logger.exception("Quiz import failed")
        await message.reply_text(
            "The import failed. Check the deployment logs before retrying."
        )


# ---------------- DRAFT REVIEW AND EXPORT ----------------

async def reviewquiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if len(context.args) != 1 or not _valid_id(context.args[0]):
        await message.reply_text("Usage: /reviewquiz QUIZ_ID")
        return

    quiz_id = int(context.args[0])

    try:
        with SessionLocal() as db:
            report = review_quiz_draft(db, quiz_id)

        problems = report["problems"]
        lines = [
            "DRAFT REVIEW",
            "",
            f"Quiz ID: {report['quiz_id']}",
            f"Title: {report['title']}",
            f"Status: {report['status']}",
            f"Questions: {report['question_count']}",
            f"Import batches: {report['batch_count']}",
            f"Within 200-question limit: "
            f"{'Yes' if report['within_limit'] else 'No'}",
            "",
        ]

        if problems:
            lines.append(f"Problems found: {len(problems)}")
            lines.extend(f"- {item}" for item in problems[:25])

            if len(problems) > 25:
                lines.append(
                    f"...and {len(problems) - 25} more. "
                    "Fix the issues and review again."
                )
        elif report["ready_for_owner_review"]:
            lines.append(
                "Structural checks passed. Manually verify every "
                "question, option, answer key and explanation."
            )
        else:
            lines.append(
                "Draft is not ready for owner review. "
                "Check the question count."
            )

        lines.extend(
            [
                "",
                "This command does not approve or publish the quiz.",
            ]
        )

        await message.reply_text("\n".join(lines)[:4000])

    except (QuizImportError, ValueError) as exc:
        await message.reply_text(str(exc)[:1500])
    except Exception:
        logger.exception("Draft review failed")
        await message.reply_text(
            "Draft review failed. Check deployment logs."
        )


async def savedraft(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if len(context.args) != 1 or not _valid_id(context.args[0]):
        await message.reply_text("Usage: /savedraft QUIZ_ID")
        return

    quiz_id = int(context.args[0])

    try:
        with SessionLocal() as db:
            text_content = build_combined_quiz_text(db, quiz_id)
            quiz = db.get(Quiz, quiz_id)

            if quiz is None:
                await message.reply_text("Quiz not found.")
                return

            safe_title = "".join(
                ch if ch.isalnum() or ch in "-_" else "_"
                for ch in quiz.title
            ).strip("_")[:60] or "quiz"

            filename = f"quiz_{quiz_id}_{safe_title}.txt"

        file_buffer = BytesIO(text_content.encode("utf-8"))
        file_buffer.seek(0)

        await message.reply_document(
            document=InputFile(file_buffer, filename=filename),
            caption=(
                f"Draft export for quiz #{quiz_id}.\n"
                "This file is a copy; it does not approve or publish the quiz."
            ),
        )

        with SessionLocal() as db:
            result = mark_draft_saved(db, quiz_id)

        await message.reply_text(
            f"Draft #{quiz_id} saved.\n"
            f"Questions exported: {result['question_count']}\n"
            "The quiz remains unpublished."
        )

    except (QuizImportError, ValueError) as exc:
        await message.reply_text(str(exc)[:1500])
    except Exception:
        logger.exception("Draft export failed")
        await message.reply_text(
            "Could not export or mark this draft as saved. "
            "Check deployment logs before retrying."
        )


async def quizzes_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    with SessionLocal() as db:
        quizzes = db.scalars(
            select(Quiz).order_by(Quiz.id.desc()).limit(30)
        ).all()

        rows = [
            (
                quiz.id,
                quiz.title,
                quiz.status,
                quiz.question_count,
                quiz.quiz_date,
            )
            for quiz in quizzes
        ]

    if not rows:
        await message.reply_text("No saved quizzes yet.")
        return

    lines = ["SAVED QUIZZES", ""]

    for quiz_id, title, status, count, quiz_date in rows:
        lines.append(
            f"#{quiz_id} | {title} | {status} | "
            f"{count} questions | {quiz_date}"
        )

    await message.reply_text("\n".join(lines)[:4000])


# ---------------- QUIZ APPROVAL ----------------

async def approvequiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if len(context.args) != 1 or not _valid_id(context.args[0]):
        await message.reply_text("Usage: /approvequiz QUIZ_ID")
        return

    quiz_id = int(context.args[0])

    try:
        with SessionLocal() as db:
            quiz = db.get(Quiz, quiz_id)

            if quiz is None:
                await message.reply_text("Quiz not found.")
                return

            if quiz.status not in {"draft", "draft_saved", "approved"}:
                await message.reply_text(
                    "Only an unpublished draft can be approved."
                )
                return

            review = review_quiz_draft(db, quiz_id)

            if not review["ready_for_owner_review"]:
                problems = review["problems"]
                lines = [
                    "APPROVAL BLOCKED",
                    "",
                    f"Quiz ID: {quiz_id}",
                    f"Questions: {review['question_count']}",
                    f"Problems found: {len(problems)}",
                    "",
                    "Fix these issues and run /reviewquiz again:",
                ]

                lines.extend(
                    f"- {problem}" for problem in problems[:20]
                )

                if len(problems) > 20:
                    lines.append(
                        f"...and {len(problems) - 20} more issue(s)."
                    )

                lines.extend([
                    "",
                    "No approval status was changed.",
                ])

                await message.reply_text("\n".join(lines)[:4000])
                return

            questions = db.scalars(
                select(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz_id)
                .order_by(QuizQuestion.position)
            ).all()

            if not questions:
                await message.reply_text(
                    "Cannot approve a quiz without questions."
                )
                return

            invalid_positions = []

            for question in questions:
                valid_key = (
                    (question.correct_option or "").strip().upper()
                    in {"A", "B", "C", "D"}
                )

                valid_fields = all(
                    [
                        (question.question_text or "").strip(),
                        (question.option_a or "").strip(),
                        (question.option_b or "").strip(),
                        (question.option_c or "").strip(),
                        (question.option_d or "").strip(),
                    ]
                )

                if not valid_key or not valid_fields:
                    invalid_positions.append(question.position)

            if invalid_positions:
                await message.reply_text(
                    "Approval blocked. Check question(s): "
                    + ", ".join(map(str, invalid_positions))
                )
                return

            quiz.status = "approved"
            db.commit()

        await message.reply_text(
            f"Quiz #{quiz_id} approved.\n"
            "Structural and saved-batch checks passed.\n"
            "You must still personally verify every question, "
            "answer key and explanation."
        )

    except (QuizImportError, ValueError) as exc:
        await message.reply_text(str(exc)[:1500])
    except Exception:
        logger.exception("Quiz approval failed")
        await message.reply_text(
            "Quiz approval failed. Check the deployment logs."
        )


# ---------------- QUIZ PUBLISHING ----------------

async def _group_is_approved(chat_id: str | int) -> bool:
    with SessionLocal() as db:
        return is_group_approved(db, chat_id)


async def publishquiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if len(context.args) != 1 or not _valid_id(context.args[0]):
        await message.reply_text("Usage: /publishquiz QUIZ_ID")
        return

    quiz_id = int(context.args[0])

    with SessionLocal() as db:
        quiz = db.get(Quiz, quiz_id)

        if quiz is None:
            await message.reply_text("Quiz not found.")
            return

        if quiz.status not in {"approved", "published", "live"}:
            await message.reply_text(
                "This quiz is not approved. Check the answer keys "
                "and run /approvequiz QUIZ_ID first."
            )
            return

        questions = db.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.position)
        ).all()

        groups = list_approved_groups(db)

        title = quiz.title
        question_data = [
            {
                "id": q.id,
                "position": q.position,
                "question_text": q.question_text,
                "options": [
                    q.option_a,
                    q.option_b,
                    q.option_c,
                    q.option_d,
                ],
                "correct_option": (
                    (q.correct_option or "").strip().upper()
                ),
                "explanation": q.explanation or "",
                "timer_seconds": q.timer_seconds or 30,
            }
            for q in questions
        ]

    if not groups:
        await message.reply_text(
            "No approved groups. Add the bot to a group, then use "
            "/approvegroup CHAT_ID."
        )
        return

    if not question_data:
        await message.reply_text("This quiz has no questions.")
        return

    sent_count = 0
    failed_count = 0

    for group in groups:
        chat_id_text = str(group["chat_id"])

        if not await _group_is_approved(chat_id_text):
            logger.warning(
                "Skipped revoked group %s during publication",
                chat_id_text,
            )
            continue

        chat_id = (
            int(chat_id_text)
            if chat_id_text.lstrip("-").isdigit()
            else chat_id_text
        )

        for question in question_data:
            correct_option = question["correct_option"]

            if correct_option not in {"A", "B", "C", "D"}:
                failed_count += 1
                logger.error(
                    "Skipped question %s: invalid answer key",
                    question["position"],
                )
                continue

            options = question["options"]

            if not all(
                isinstance(option, str) and option.strip()
                for option in options
            ):
                failed_count += 1
                continue

            timer = max(
                5,
                min(int(question["timer_seconds"]), 600),
            )

            # Shuffle the original A/B/C/D labels together with their text.
            # option_order records which original answer each displayed
            # Telegram option represents, so answer tracking stays correct.
            displayed_pairs = [
                (label, option[:100])
                for label, option in zip("ABCD", options)
            ]
            random.shuffle(displayed_pairs)
            option_order = "".join(label for label, _ in displayed_pairs)
            displayed_options = [option for _, option in displayed_pairs]
            correct_option_id = option_order.index(correct_option)

            try:
                sent_poll = await context.bot.send_poll(
                    chat_id=chat_id,
                    question=(
                        f"{title} | Q{question['position']}/"
                        f"{len(question_data)}\n\n"
                        f"{question['question_text']}"
                    )[:300],
                    options=displayed_options,
                    type=PollType.QUIZ,
                    correct_option_id=correct_option_id,
                    is_anonymous=False,
                    explanation=question["explanation"][:200],
                    open_period=timer,
                )

                with SessionLocal() as db:
                    register_poll(
                        db,
                        poll_id=sent_poll.poll.id,
                        quiz_id=quiz_id,
                        question_id=question["id"],
                        group_id=chat_id_text,
                        message_id=sent_poll.message_id,
                        attempt_mode="group_quiz",
                        option_order=option_order,
                    )

                sent_count += 1

            except TelegramError:
                failed_count += 1
                logger.exception(
                    "Telegram failed to publish question %s to %s",
                    question["position"],
                    chat_id_text,
                )
            except Exception:
                failed_count += 1
                logger.exception(
                    "Failed to register poll for question %s in %s",
                    question["position"],
                    chat_id_text,
                )

    with SessionLocal() as db:
        quiz = db.get(Quiz, quiz_id)

        if quiz is not None and sent_count > 0:
            quiz.status = "live"
            db.commit()

    await message.reply_text(
        f"Quiz #{quiz_id}: publication finished.\n"
        f"Polls sent and registered: {sent_count}\n"
        f"Failed or skipped: {failed_count}\n"
        "Only owner-approved groups were targeted.\n\n"
        "Participant attempts are created when students first answer."
    )


# ---------------- QUIZ END AND LEADERBOARD ----------------

async def endquiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = _message(update)
    if not message or not await owner_only(update, context):
        return

    if (
        len(context.args) != 2
        or not _valid_id(context.args[0])
        or not context.args[1].lstrip("-").isdigit()
    ):
        await message.reply_text("Usage: /endquiz QUIZ_ID CHAT_ID")
        return

    quiz_id = int(context.args[0])
    chat_id_text = context.args[1]
    chat_id = int(chat_id_text)

    if not await _group_is_approved(chat_id_text):
        await message.reply_text(
            "Blocked: this group is not owner-approved."
        )
        return

    with SessionLocal() as db:
        polls = db.scalars(
            select(PollRegistry).where(
                PollRegistry.quiz_id == quiz_id,
                PollRegistry.group_id == chat_id_text,
                PollRegistry.open.is_(True),
            )
        ).all()

        poll_details = [
            (poll.poll_id, poll.message_id)
            for poll in polls
        ]

    closed_count = 0

    for poll_id, message_id in poll_details:
        try:
            await context.bot.stop_poll(
                chat_id=chat_id,
                message_id=message_id,
            )
        except TelegramError:
            logger.info("Poll %s may already be closed", poll_id)
        except Exception:
            logger.exception(
                "Unexpected error stopping poll %s", poll_id
            )

        with SessionLocal() as db:
            if close_poll(db, poll_id):
                closed_count += 1

    with SessionLocal() as db:
        attempts = db.scalars(
            select(QuizAttempt).where(
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.group_id == chat_id_text,
                QuizAttempt.status == "in_progress",
            )
        ).all()

        attempt_ids = [attempt.id for attempt in attempts]

    finalized_count = 0

    for attempt_id in attempt_ids:
        try:
            with SessionLocal() as db:
                finalize_attempt(db, attempt_id=attempt_id)
            finalized_count += 1
        except Exception:
            logger.exception(
                "Failed to finalize attempt %s", attempt_id
            )

    try:
        await publish_group_leaderboard(
            context.bot,
            chat_id,
            quiz_id,
        )
    except Exception:
        logger.exception("Leaderboard publication failed")
        await message.reply_text(
            "Polls were closed and attempts finalized, but leaderboard "
            "publication failed. Check the deployment logs."
        )
        return

    await message.reply_text(
        f"Quiz #{quiz_id} ended for group {chat_id_text}.\n"
        f"Polls closed in registry: {closed_count}\n"
        f"Attempts finalized: {finalized_count}\n"
        "Stored answers were retained."
    )


# ---------------- APPLICATION ENTRY POINT ----------------

def main() -> None:
    settings = load_settings()

    initialize_database()

    with SessionLocal() as db:
        ensure_default_subjects(db)

    app = (
        Application.builder()
        .token(settings.bot_token)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("approvegroup", approvegroup))
    app.add_handler(CommandHandler("revokegroup", revokegroup))
    app.add_handler(CommandHandler("groups", groups_command))

    app.add_handler(CommandHandler("subjects", subjects_command))
    app.add_handler(CommandHandler("addsubject", addsubject_command))
    app.add_handler(CommandHandler("renamesubject", renamesubject_command))
    app.add_handler(CommandHandler("deletesubject", deletesubject_command))

    app.add_handler(CommandHandler("importquiz", importquiz))
    app.add_handler(CommandHandler("addquestions", addquestions))
    app.add_handler(CommandHandler("cancelimport", cancelimport))
    app.add_handler(CommandHandler("quizzes", quizzes_command))
    app.add_handler(CommandHandler("reviewquiz", reviewquiz))
    app.add_handler(CommandHandler("savedraft", savedraft))
    app.add_handler(CommandHandler("approvequiz", approvequiz))
    app.add_handler(CommandHandler("publishquiz", publishquiz))
    app.add_handler(CommandHandler("endquiz", endquiz))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_import_text,
        )
    )

    install_poll_answer_handler(app)

    logger.info("PH Practice Hub bot starting in polling mode.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
