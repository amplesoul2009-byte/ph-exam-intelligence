
"""Telegram leaderboard publishing for PH Greybot."""

import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from sqlalchemy.orm import Session

from leaderboard_service import (
    build_leaderboard,
    render_group_leaderboard,
)

logger = logging.getLogger(__name__)


async def publish_group_leaderboard(
    bot: Bot,
    db: Session,
    *,
    quiz_id: int,
    group_id: int | str,
    message_id: int | None = None,
    limit: int = 30,
) -> dict:
    """Send a group leaderboard or edit its existing message.

    The caller must verify that the group is approved before publishing.
    Persist the returned message_id in the database if it must survive
    a bot restart.
    """

    if not 1 <= limit <= 100:
        raise ValueError("Leaderboard limit must be between 1 and 100.")

    data = build_leaderboard(
        db,
        quiz_id=quiz_id,
        group_id=group_id,
    )

    text = render_group_leaderboard(data, limit=limit)

    # Telegram text messages have a 4096-character limit.
    if len(text) > 4000:
        text = text[:3900] + "\n\n<i>Leaderboard shortened.</i>"

    if message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=int(group_id),
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return {
                "success": True,
                "action": "updated",
                "message_id": message_id,
            }

        except BadRequest as exc:
            # Telegram returns this when the content is unchanged.
            if "message is not modified" in str(exc).lower():
                return {
                    "success": True,
                    "action": "unchanged",
                    "message_id": message_id,
                }

            logger.warning(
                "Could not edit leaderboard message %s in group %s: %s",
                message_id,
                group_id,
                str(exc)[:200],
            )

        except TelegramError:
            logger.exception(
                "Failed to update leaderboard in group %s",
                group_id,
            )
            raise

    sent = await bot.send_message(
        chat_id=int(group_id),
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    return {
        "success": True,
        "action": "created",
        "message_id": sent.message_id,
    }


async def publish_master_leaderboard(
    bot: Bot,
    db: Session,
    *,
    quiz_id: int,
    destination_chat_id: int | str,
    limit: int = 30,
) -> dict:
    """Publish the best-completed-attempt master leaderboard.

    This version sends a separate master board. It does not publish to
    groups automatically; callers must choose authorized destinations.
    """

    if not 1 <= limit <= 100:
        raise ValueError("Leaderboard limit must be between 1 and 100.")

    data = build_leaderboard(
        db,
        quiz_id=quiz_id,
        group_id=destination_chat_id,
    )

    rows = data["master_best_attempts"][:limit]
    total = data["total_questions"]

    lines = [
        "🏆 <b>PH MASTER LEADERBOARD</b>",
        f"📝 Mock: <b>{data['title']}</b>",
        f"📝 Total Questions: {total}",
        "📌 Marking: +1 correct · −0.25 wrong · 0 unanswered",
        "━━━━━━━━━━━━━━━━━━",
    ]

    medals = ["🥇", "🥈", "🥉"]

    for rank, row in enumerate(rows, start=1):
        name = str(row.get("student_name") or "Student")
        # Escape participant-controlled names before HTML formatting.
        from html import escape
        name = escape(name)

        medal = medals[rank - 1] if rank <= 3 else f"{rank}."

        lines.extend([
            f"{medal} <b>{name}</b>",
            f"🎯 Net Score: <b>{row['net_score']:.2f}/{total}</b>",
            f"📊 Score Percentage: {row['score_percentage']:.2f}%",
            f"🎯 Accuracy: {row['accuracy']:.2f}%",
            f"✅ Correct: {row['correct']} · ❌ Wrong: {row['wrong']}",
            f"📝 Attempted: {row['attempted']}/{total} · "
            f"⏭️ Unanswered: {row['unanswered']}",
            f"⏱️ Time Used: {__import__('quiz_scoring').format_time(row['time_used_seconds'])}",
            "━━━━━━━━━━━━━━━━━━",
        ])

    if not rows:
        lines.append("No completed attempts recorded yet.")

    lines.append("✍️ Created by Ph")

    text = "\n".join(lines)

    if len(text) > 4000:
        text = text[:3900] + "\n\n<i>Leaderboard shortened.</i>"

    sent = await bot.send_message(
        chat_id=int(destination_chat_id),
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    return {
        "success": True,
        "action": "created",
        "message_id": sent.message_id,
    }
