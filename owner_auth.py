
"""Owner authorization helpers for PH Practice Hub."""

from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import load_settings


def is_owner(user_id: int | None) -> bool:
    """Return True only for the configured Telegram owner."""
    if user_id is None:
        return False

    return user_id == load_settings().owner_id


async def owner_only(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Allow owner commands only in the owner's private chat.

    Group commands are silently rejected so owner-only controls
    do not expose administrative behaviour in group conversations.
    """

    chat = update.effective_chat
    user = update.effective_user

    # Never permit owner commands from groups, supergroups or channels.
    if chat is None or chat.type != "private":
        return False

    # Validate the actual sender, not merely the chat.
    if user is None or not is_owner(user.id):
        message = update.effective_message

        if message is not None:
            await message.reply_text(
                "⛔ Access denied. This command is restricted "
                "to the bot owner."
            )

        return False

    return True


def owner_command(func):
    """Decorator for owner-only Telegram command handlers."""

    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args,
        **kwargs,
    ):
        if not await owner_only(update, context):
            return None

        return await func(update, context, *args, **kwargs)

    return wrapper
