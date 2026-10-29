
"""Telegram poll-answer handler for PH Greybot."""

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

logger = logging.getLogger(__name__)


async def handle_poll_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Persist a Telegram poll answer for the mapped attempt."""

    answer_update = update.poll_answer

    if answer_update is None or answer_update.user is None:
        return

    poll_id = str(answer_update.poll_id)
    user_id = str(answer_update.user.id)

    try:
        with SessionLocal() as db:
            poll = get_poll(db, poll_id)

            if poll is None:
                logger.debug("Ignoring unregistered poll %s", poll_id)
                return

            if not poll.open:
                logger.debug("Ignoring closed poll %s", poll_id)
                return

            # Resolve this participant's exact attempt.
            # The mapping is stored on the database, not inferred from name.
            from participant_poll_attempt import get_attempt_id

            attempt_id = get_attempt_id(
                db,
                poll_id=poll_id,
                student_id=user_id,
            )

            if attempt_id is None:
                logger.warning(
                    "No attempt mapping for poll %s and user %s",
                    poll_id,
                    user_id,
                )
                return

            option_ids = answer_update.option_ids

            if len(option_ids) == 0:
                selected_option = None
            elif len(option_ids) == 1 and option_ids[0] in range(4):
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
            "Failed to save poll answer for poll %s", poll_id
        )


def install_poll_answer_handler(app: Application) -> None:
    """Install this handler once on the main bot application."""
    app.add_handler(PollAnswerHandler(handle_poll_answer))
