
"""Save Telegram poll answers to the correct quiz attempt."""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    PollAnswerHandler,
)

from database import SessionLocal
from live_poll_service import record_poll_answer
from poll_registry import get_poll
from participant_poll_attempt import get_attempt_id

logger = logging.getLogger(__name__)


async def handle_poll_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    answer = update.poll_answer

    if answer is None or answer.user is None:
        return

    poll_id = str(answer.poll_id)
    student_id = str(answer.user.id)

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

            # Ignore answers until the bot has associated
            # this participant with an active attempt.
            if attempt_id is None:
                logger.warning(
                    "No attempt registered for poll %s and student %s",
                    poll_id,
                    student_id,
                )
                return

            option_ids = answer.option_ids

            if not option_ids:
                selected_option = None
            elif len(option_ids) == 1 and 0 <= option_ids[0] <= 3:
                selected_option = "ABCD"[option_ids[0]]
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
            "Could not save answer for poll %s",
            poll_id,
        )


def install_poll_answer_handler(app: Application) -> None:
    app.add_handler(PollAnswerHandler(handle_poll_answer))
