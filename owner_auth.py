
"""Owner authorization helpers for PH Greybot."""

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
    """Check whether the person invoking a command is the owner."""

    user = update.effective_user

    if user is not None and is_owner(user.id):
        return True

    message = update.effective_message

    if message is not None:
        await message.reply_text(
            "⛔ Access denied. This command is restricted to the bot owner."
        )

    return False


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
