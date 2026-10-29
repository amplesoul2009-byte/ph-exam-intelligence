
"""Telegram leaderboard publishing for PH Practice Hub."""

import logging
import re
from html import unescape

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from database import SessionLocal
from group_access import is_group_approved
from leaderboard_service import (
    build_leaderboard,
    render_group_leaderboard,
)

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


def _plain_text(html_text: str) -> str:
    """Convert leaderboard HTML to readable plain text."""
    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _split_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT):
    """Split long plain-text messages without cutting words unnecessarily."""
    chunks = []
    remaining = text.strip()

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit)

        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)

        if split_at < limit // 2:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return [chunk for chunk in chunks if chunk]


async def _send_leaderboard(
    bot: Bot,
    chat_id: int | str,
    html_text: str,
    message_id: int | None = None,
):
    """Edit an existing message when possible; otherwise send new messages."""
    if len(html_text) <= TELEGRAM_MESSAGE_LIMIT:
        if message_id is not None:
            try:
                return await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=html_text,
                    parse_mode=ParseMode.HTML,
                )
            except BadRequest as exc:
                # Telegram reports this when the message is unchanged.
                if "message is not modified" in str(exc).lower():
                    return None

                logger.warning(
                    "Could not edit leaderboard message: %s", exc
                )

        return await bot.send_message(
            chat_id=chat_id,
            text=html_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # Long messages are converted to plain text before splitting, so
    # HTML tags cannot be cut in half and cause Telegram parse errors.
    plain = _plain_text(html_text)
    sent_messages = []

    for chunk in _split_text(plain):
        sent_messages.append(
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                disable_web_page_preview=True,
            )
        )

    return sent_messages


async def publish_group_leaderboard(
    bot: Bot,
    chat_id: int | str,
    quiz_id: int,
    message_id: int | None = None,
    current_attempt_id: int | None = None,
):
    """Publish a leaderboard only to an owner-approved group."""
    normalized_chat_id = str(chat_id)

    with SessionLocal() as db:
        if not is_group_approved(db, normalized_chat_id):
            logger.warning(
                "Leaderboard blocked for unapproved group %s",
                normalized_chat_id,
            )
            return None

        data = build_leaderboard(
            db=db,
            quiz_id=quiz_id,
            group_id=normalized_chat_id,
            current_attempt_id=current_attempt_id,
        )

        html_text = render_group_leaderboard(data)

    try:
        return await _send_leaderboard(
            bot=bot,
            chat_id=chat_id,
            html_text=html_text,
            message_id=message_id,
        )
    except TelegramError:
        logger.exception(
            "Failed to publish leaderboard for quiz %s to group %s",
            quiz_id,
            normalized_chat_id,
        )
        raise


async def publish_master_leaderboard(
    bot: Bot,
    quiz_id: int,
    destination_chat_id: int | str,
    message_id: int | None = None,
):
    """Publish the best completed attempt per student across groups."""
    normalized_chat_id = str(destination_chat_id)

    with SessionLocal() as db:
        if not is_group_approved(db, normalized_chat_id):
            logger.warning(
                "Master leaderboard blocked for unapproved group %s",
                normalized_chat_id,
            )
            return None

        data = build_leaderboard(
            db=db,
            quiz_id=quiz_id,
            group_id=normalized_chat_id,
        )

        if not data.get("scoring_valid", False):
            html_text = render_group_leaderboard(data)
        else:
            quiz_title = data.get("quiz_title", "Quiz")
            rows = data.get("master_best_attempts", [])
            total_questions = data.get("total_questions", 0)

            lines = [
                "🏆 <b>OVERALL MASTER LEADERBOARD</b>",
                "🪷 <b>PH | PRACTICE HUB</b>",
                f"📝 <b>Quiz:</b> {quiz_title}",
                f"📝 <b>Total Questions:</b> {total_questions}",
                "📌 <b>Marking:</b> +1 correct · −0.25 wrong · 0 unanswered",
                "━━━━━━━━━━━━━━━━━━",
            ]

            medals = ["🥇", "🥈", "🥉"]

            for index, row in enumerate(rows[:30], start=1):
                rank = row.get("rank") or index
                medal = (
                    medals[rank - 1]
                    if 1 <= rank <= 3
                    else f"{rank}."
                )

                name = str(row.get("student_name") or "Student")
                # Escape user-provided names before using Telegram HTML.
                from html import escape
                name = escape(name)

                score = float(row.get("score", 0.0))
                accuracy = float(row.get("accuracy", 0.0))
                overall_percentile = float(
                    row.get("overall_percentile", 0.0)
                )

                lines.extend([
                    f"{medal} <b>{name}</b>",
                    f"🎯 Net Score: <b>{score:.2f}/{total_questions}</b>",
                    (
                        f"📊 Score Percentage: "
                        f"{float(row.get('score_percentage', 0.0)):.2f}%"
                        f" · 🏅 Overall Percentile: "
                        f"{overall_percentile:.2f}"
                    ),
                    f"🎯 Accuracy: {accuracy:.2f}%",
                    (
                        f"✅ Correct: {row.get('correct_count', 0)}"
                        f" · ❌ Wrong: {row.get('wrong_count', 0)}"
                    ),
                    (
                        f"⏭️ Unanswered: "
                        f"{row.get('unanswered_count', 0)}"
                    ),
                    "━━━━━━━━━━━━━━━━━━",
                ])

            if not rows:
                lines.append("No completed, valid attempts are available.")

            html_text = "\n".join(lines)

    try:
        return await _send_leaderboard(
            bot=bot,
            chat_id=destination_chat_id,
            html_text=html_text,
            message_id=message_id,
        )
    except TelegramError:
        logger.exception(
            "Failed to publish master leaderboard for quiz %s",
            quiz_id,
        )
        raise
