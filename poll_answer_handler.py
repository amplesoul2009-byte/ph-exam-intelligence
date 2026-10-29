
"""Save Telegram quiz poll answers to participant attempts."""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    PollAnswerHandler,
)

from database import SessionLocal, QuizAttempt
from live_poll_service import record_poll_answer, start_attempt
from poll_registry import get_poll
from participant_poll_attempt import (
    get_attempt_id,
    register_participant_attempt,
)

logger = logging.getLogger(__name__)


async def handle_poll_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    item = update.poll_answer

    if item is None or item.user is None:
        return

    poll_id = str(item.poll_id)
    student_id = str(item.user.id)

    try:
        with SessionLocal() as db:
            poll = get_poll(db, poll_id)

            if poll is None or not poll.open:
                return

            attempt_id = get_attempt_id(
                db,
                poll_id=poll_id,
                student_id=student_id,
            )

            if attempt_id is None:
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
                        student_name=(
                            item.user.full_name
                            or item.user.username
                            or "Student"
                        ),
                        group_id=poll.group_id,
                    )

                attempt_id = attempt.id

                register_participant_attempt(
                    db,
                    poll_id=poll_id,
                    student_id=student_id,
                    attempt_id=attempt_id,
                )

            option_ids = item.option_ids

            if not option_ids:
                selected = None
            elif len(option_ids) == 1 and option_ids[0] in range(4):
                selected = "ABCD"[option_ids[0]]
            else:
                logger.warning(
                    "Unexpected poll selection for poll %s",
                    poll_id,
                )
                return

            record_poll_answer(
                db,
                attempt_id=attempt_id,
                question_id=poll.question_id,
                selected_option=selected,
            )

    except Exception:
        logger.exception(
            "Failed to save answer for poll %s",
            poll_id,
        )


def install_poll_answer_handler(app: Application) -> None:
    app.add_handler(PollAnswerHandler(handle_poll_answer))
