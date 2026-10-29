
"""Save Telegram poll answers to participant quiz attempts."""

import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    PollAnswerHandler,
)

from database import SessionLocal, QuizAttempt
from group_access import is_group_approved
from live_poll_service import record_poll_answer, start_attempt
from participant_poll_attempt import (
    get_attempt_id,
    register_participant_attempt,
)
from poll_registry import get_poll

logger = logging.getLogger(__name__)


def _original_option(
    displayed_index: int,
    option_order: str,
) -> str | None:
    """Translate Telegram's displayed option index to original A/B/C/D."""

    option_order = (option_order or "ABCD").strip().upper()

    if len(option_order) != 4 or set(option_order) != set("ABCD"):
        logger.error("Invalid stored poll option mapping: %r", option_order)
        return None

    if not 0 <= displayed_index < 4:
        return None

    return option_order[displayed_index]


async def handle_poll_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    answer = update.poll_answer

    if answer is None or answer.user is None:
        return

    poll_id = str(answer.poll_id)
    student_id = str(answer.user.id)
    student_name = (
        answer.user.full_name
        or answer.user.username
        or "Student"
    )

    try:
        with SessionLocal() as db:
            poll = get_poll(db, poll_id)

            if poll is None or not poll.open:
                return

            # Only accept answers for owner-approved groups.
            if not is_group_approved(db, poll.group_id):
                logger.warning(
                    "Ignored poll answer from unapproved group %s",
                    poll.group_id,
                )
                return

            attempt_id = get_attempt_id(
                db,
                poll_id=poll_id,
                student_id=student_id,
            )

            if attempt_id is None:
                # Reuse an active attempt for this student, quiz and group.
                attempt = db.scalar(
                    select(QuizAttempt)
                    .where(
                        QuizAttempt.quiz_id == poll.quiz_id,
                        QuizAttempt.student_id == student_id,
                        QuizAttempt.group_id == str(poll.group_id),
                        QuizAttempt.status == "in_progress",
                    )
                    .order_by(QuizAttempt.id.desc())
                )

                if attempt is None:
                    attempt = start_attempt(
                        db,
                        quiz_id=poll.quiz_id,
                        student_id=student_id,
                        student_name=student_name,
                        group_id=poll.group_id,
                    )

                attempt_id = attempt.id

                register_participant_attempt(
                    db,
                    poll_id=poll_id,
                    student_id=student_id,
                    attempt_id=attempt_id,
                )

            # Telegram reports the selected displayed option index.
            option_ids = answer.option_ids

            if not option_ids:
                selected_option = None
            elif len(option_ids) == 1:
                selected_option = _original_option(
                    option_ids[0],
                    poll.option_order,
                )

                if selected_option is None:
                    logger.warning(
                        "Invalid option selection or mapping for poll %s",
                        poll_id,
                    )
                    return
            else:
                logger.warning(
                    "Unexpected option selection for poll %s",
                    poll_id,
                )
                return

            record_poll_answer(
                db,
                attempt_id=attempt_id,
                question_id=poll.question_id,
                selected_option=selected_option,
            )

    except Exception:
        logger.exception(
            "Failed to save poll answer: poll_id=%s student_id=%s",
            poll_id,
            student_id,
        )


def install_poll_answer_handler(app: Application) -> None:
    """Install Telegram's poll-answer handler."""
    app.add_handler(PollAnswerHandler(handle_poll_answer))
