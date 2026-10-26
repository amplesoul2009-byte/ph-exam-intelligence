import os
import logging
import random
import asyncio
from datetime import datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from database import (
    Category,
    Question,
    ApprovedGroup,
    GroupAccessRequest,
    ChatSetting,
    SessionLocal,
    init_db,
)


# ==================================================
# CONFIGURATION
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
)

OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()

DEFAULT_DISPLAY_NAME = "0xGREY"


# ==================================================
# OWNER PERMISSION
# ==================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    return bool(
        user
        and OWNER_ID
        and str(user.id) == OWNER_ID
    )


async def owner_required(update: Update) -> bool:
    if not is_owner(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ This command is restricted to the bot owner."
            )
        return False

    return True


# ==================================================
# CUSTOM CHAT DISPLAY NAME
# ==================================================

def get_chat_display_name(chat_id: int) -> str:
    """Return the saved display name or the default name."""

    with SessionLocal() as db:
        setting = (
            db.query(ChatSetting)
            .filter_by(chat_id=str(chat_id))
            .first()
        )

        if setting and setting.display_name:
            return setting.display_name

    return DEFAULT_DISPLAY_NAME


def get_display_name_safely(chat_id: int) -> str:
    """Avoid breaking ordinary bot replies if settings cannot be read."""

    try:
        return get_chat_display_name(chat_id)
    except Exception:
        logger.exception(
            "Could not retrieve display name for chat %s",
            chat_id,
        )
        return DEFAULT_DISPLAY_NAME


async def set_chat_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Set the display name for the current chat."""

    if not await owner_required(update):
        return

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if message is None or chat is None or user is None:
        return

    display_name = " ".join(context.args).strip()

    if not display_name:
        await message.reply_text(
            "Usage:\n"
            "/set_chat_name PH Banking Expert\n\n"
            "Examples:\n"
            "/set_chat_name PH Banking Expert\n"
            "/set_chat_name PH English Mentor\n"
            "/set_chat_name 0xGREY"
        )
        return

    if len(display_name) > 100:
        await message.reply_text(
            "❌ The display name must be 100 characters or fewer."
        )
        return

    try:
        with SessionLocal() as db:
            setting = (
                db.query(ChatSetting)
                .filter_by(chat_id=str(chat.id))
                .first()
            )

            if setting:
                setting.display_name = display_name
                setting.updated_by = str(user.id)
            else:
                db.add(
                    ChatSetting(
                        chat_id=str(chat.id),
                        display_name=display_name,
                        updated_by=str(user.id),
                    )
                )

            db.commit()

        await message.reply_text(
            "✅ CHAT DISPLAY NAME UPDATED\n\n"
            f"🏷️ Name: {display_name}\n"
            f"🆔 Chat ID: {chat.id}\n\n"
            "Supported bot messages will use this name."
        )

    except Exception:
        logger.exception("Failed to update chat display name")

        await message.reply_text(
            "❌ Could not save the display name. "
            "Check the hosting logs."
        )


async def get_chat_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Display the saved name for the current chat."""

    if not await owner_required(update):
        return

    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    display_name = get_display_name_safely(chat.id)

    await message.reply_text(
        "🏷️ CHAT DISPLAY NAME\n\n"
        f"Name: {display_name}\n"
        f"Chat ID: {chat.id}"
    )


async def reset_chat_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Remove the custom name and restore the default."""

    if not await owner_required(update):
        return

    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    try:
        with SessionLocal() as db:
            setting = (
                db.query(ChatSetting)
                .filter_by(chat_id=str(chat.id))
                .first()
            )

            if setting:
                db.delete(setting)
                db.commit()

        await message.reply_text(
            "✅ Chat display name reset.\n"
            f"Default name: {DEFAULT_DISPLAY_NAME}"
        )

    except Exception:
        logger.exception("Failed to reset chat display name")

        await message.reply_text(
            "❌ Could not reset the display name."
        )


# ==================================================
# GROUP ACCESS HELPERS
# ==================================================

def get_approved_group(group_id: str):
    """Return an active approved group, or None."""

    with SessionLocal() as db:
        return (
            db.query(ApprovedGroup)
            .filter(
                ApprovedGroup.group_id == str(group_id),
                ApprovedGroup.active.is_(True),
            )
            .first()
        )


async def check_publish_permission(
    update: Update,
) -> bool:
    """Check whether the current group is approved."""

    chat = update.effective_chat

    if chat is None:
        return False

    if chat.type == "private":
        return True

    approved = get_approved_group(str(chat.id))

    if approved is None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ This group is not approved to receive quizzes.\n\n"
                "A group administrator can request access using:\n"
                "/request_access"
            )
        return False

    return True


async def check_publish_permission_for_target(
    update: Update,
    target_chat_id: int,
) -> bool:
    """Ensure a group target is actively approved."""

    # Positive Telegram chat IDs represent private chats.
    # Owner-only quiz publishing can be tested in private chat.
    if target_chat_id > 0:
        return True

    approved = get_approved_group(str(target_chat_id))

    if approved is None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ Publishing blocked.\n\n"
                "The target group is not currently approved.\n"
                "Use /approved_groups to check approved groups."
            )
        return False

    return True


# ==================================================
# START
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    display_name = get_display_name_safely(chat.id)

    await message.reply_text(
        f"🎓 Welcome to {display_name}!\n\n"
        "Premium Mock Test Platform\n\n"
        "📚 Question Bank\n"
        "📝 Telegram Quiz Polls\n"
        "🔀 Shuffled Answer Options\n"
        "🔐 Approved Group Publishing\n"
        "📊 Quiz Publishing\n"
        "🏆 Leaderboards — planned\n\n"
        "Use /help to see available commands."
    )


# ==================================================
# HELP
# ==================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    display_name = get_display_name_safely(chat.id)

    await message.reply_text(
        f"📌 {display_name.upper()} COMMANDS\n\n"

        "GENERAL\n"
        "/start — Start the bot\n"
        "/help — Show commands\n"
        "/status — Bot status\n\n"

        "CUSTOM CHAT DISPLAY NAME — OWNER ONLY\n"
        "/set_chat_name NAME — Set this chat's name\n"
        "/get_chat_name — Show this chat's name\n"
        "/reset_chat_name — Restore default name\n\n"

        "QUESTION BANK — OWNER ONLY\n"
        "/addcategory English\n"
        "/categories\n"
        "/addquestion English\n"
        "/questioncount\n\n"

        "QUIZ POLLS — OWNER ONLY\n"
        "/testpoll — Send a demo poll\n"
        "/publishquiz English 5\n"
        "/publishquiz Banking Awareness 10\n"
        "/publishquiz_to GROUP_ID COUNT CATEGORY\n\n"

        "GROUP ACCESS\n"
        "/request_access — Request group approval\n"
        "/pending_groups — View pending requests (owner)\n"
        "/approve_group GROUP_ID — Approve a group (owner)\n"
        "/revoke_group GROUP_ID — Revoke a group (owner)\n"
        "/approved_groups — List approved groups (owner)\n\n"

        "QUESTION FORMAT\n"
        "/addquestion English\n"
        "Question: What is 2 + 2?\n"
        "A: 3\n"
        "B: 4\n"
        "C: 5\n"
        "D: 6\n"
        "Correct: B\n"
        "Explanation: Two plus two equals four.\n"
        "Difficulty: Easy"
    )


# ==================================================
# STATUS
# ==================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    display_name = get_display_name_safely(chat.id)

    if is_owner(update):
        response = (
            f"✅ {display_name} is running.\n"
            "👑 Owner access recognised."
        )
    else:
        response = f"✅ {display_name} is running."

    await message.reply_text(response)


# ==================================================
# REQUEST GROUP ACCESS
# ==================================================

async def request_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if chat is None or user is None or message is None:
        return

    if chat.type not in {"group", "supergroup"}:
        await message.reply_text(
            "ℹ️ Run /request_access inside the Telegram group "
            "that wants to receive quizzes."
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat_id=chat.id,
            user_id=user.id,
        )

        if member.status not in {"administrator", "creator"}:
            await message.reply_text(
                "⛔ Only a group administrator or group creator "
                "can request access."
            )
            return

    except TelegramError:
        logger.exception("Could not verify group administrator")

        await message.reply_text(
            "❌ I could not verify your administrator status. "
            "Make sure the bot is in the group and try again."
        )
        return

    try:
        with SessionLocal() as db:
            approved = (
                db.query(ApprovedGroup)
                .filter(
                    ApprovedGroup.group_id == str(chat.id),
                    ApprovedGroup.active.is_(True),
                )
                .first()
            )

            if approved:
                await message.reply_text(
                    "✅ This group is already approved to receive quizzes."
                )
                return

            pending = (
                db.query(GroupAccessRequest)
                .filter(
                    GroupAccessRequest.group_id == str(chat.id),
                    GroupAccessRequest.status == "pending",
                )
                .first()
            )

            if pending:
                await message.reply_text(
                    "⏳ This group already has a pending access request."
                )
                return

            previous = (
                db.query(GroupAccessRequest)
                .filter(
                    GroupAccessRequest.group_id == str(chat.id)
                )
                .order_by(GroupAccessRequest.id.desc())
                .first()
            )

            if previous:
                previous.group_title = chat.title or ""
                previous.requested_by = str(user.id)
                previous.requester_username = user.username or ""
                previous.status = "pending"
                previous.requested_at = datetime.utcnow()
                previous.reviewed_by = None
                previous.reviewed_at = None
            else:
                db.add(
                    GroupAccessRequest(
                        group_id=str(chat.id),
                        group_title=chat.title or "",
                        requested_by=str(user.id),
                        requester_username=user.username or "",
                        status="pending",
                    )
                )

            db.commit()

        await message.reply_text(
            "📨 GROUP ACCESS REQUEST SUBMITTED\n\n"
            f"📚 Group: {chat.title or 'Unnamed group'}\n"
            f"🆔 Group ID: {chat.id}\n\n"
            "The bot owner must review this request.\n"
            "Approval is not automatic."
        )

        if OWNER_ID:
            try:
                username_text = (
                    f"Username: @{user.username}"
                    if user.username
                    else "Username: unavailable"
                )

                await context.bot.send_message(
                    chat_id=int(OWNER_ID),
                    text=(
                        "📨 NEW GROUP ACCESS REQUEST\n\n"
                        f"📚 Group: {chat.title or 'Unnamed group'}\n"
                        f"🆔 Group ID: {chat.id}\n"
                        f"👤 Requested by: {user.id}\n"
                        f"{username_text}"
                    ),
                )

            except TelegramError:
                logger.info(
                    "Could not DM owner about group access request."
                )

    except Exception:
        logger.exception("Failed to record group access request")

        await message.reply_text(
            "❌ Could not submit the request. "
            "Check the hosting logs."
        )


# ==================================================
# VIEW PENDING GROUP REQUESTS
# ==================================================

async def pending_groups(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    try:
        with SessionLocal() as db:
            requests = (
                db.query(GroupAccessRequest)
                .filter(GroupAccessRequest.status == "pending")
                .order_by(GroupAccessRequest.requested_at.asc())
                .all()
            )

            if not requests:
                response = "📭 There are no pending group requests."
            else:
                lines = ["📨 PENDING GROUP REQUESTS\n"]

                for request in requests:
                    lines.append(
                        f"📚 {request.group_title or 'Unnamed group'}\n"
                        f"🆔 ID: {request.group_id}\n"
                        f"👤 Requested by: {request.requested_by}\n"
                        f"Use: /approve_group {request.group_id}\n"
                    )

                response = "\n".join(lines)

        await update.effective_message.reply_text(response)

    except Exception:
        logger.exception("Failed to list pending group requests")

        await update.effective_message.reply_text(
            "❌ Could not retrieve pending requests."
        )


# ==================================================
# APPROVE GROUP
# ==================================================

async def approve_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage:\n/approve_group GROUP_ID\n\n"
            "Use /pending_groups to see group IDs."
        )
        return

    group_id = context.args[0].strip()

    try:
        with SessionLocal() as db:
            request = (
                db.query(GroupAccessRequest)
                .filter(
                    GroupAccessRequest.group_id == group_id,
                    GroupAccessRequest.status == "pending",
                )
                .order_by(GroupAccessRequest.id.desc())
                .first()
            )

            existing = (
                db.query(ApprovedGroup)
                .filter(ApprovedGroup.group_id == group_id)
                .first()
            )

            if existing and existing.active:
                await update.effective_message.reply_text(
                    "✅ This group is already approved."
                )
                return

            group_title = (
                request.group_title
                if request
                else (
                    existing.group_title
                    if existing
                    else ""
                )
            )

            if existing:
                existing.group_title = group_title
                existing.approved_by = str(update.effective_user.id)
                existing.approved_at = datetime.utcnow()
                existing.active = True
            else:
                db.add(
                    ApprovedGroup(
                        group_id=group_id,
                        group_title=group_title,
                        approved_by=str(update.effective_user.id),
                        approved_at=datetime.utcnow(),
                        active=True,
                    )
                )

            if request:
                request.status = "approved"
                request.reviewed_by = str(update.effective_user.id)
                request.reviewed_at = datetime.utcnow()

            db.commit()

        await update.effective_message.reply_text(
            "✅ GROUP APPROVED\n\n"
            f"🆔 Group ID: {group_id}\n"
            f"📚 Group: {group_title or 'Name unavailable'}\n\n"
            "This group can now receive quizzes."
        )

        try:
            target_display_name = get_display_name_safely(int(group_id))

            await context.bot.send_message(
                chat_id=int(group_id),
                text=(
                    f"✅ {target_display_name} GROUP ACCESS APPROVED\n\n"
                    "This group is now authorised to receive quizzes."
                ),
            )

        except (TelegramError, ValueError):
            logger.info(
                "Group approved, but notification could not be delivered."
            )

    except Exception:
        logger.exception("Failed to approve group")

        await update.effective_message.reply_text(
            "❌ Could not approve the group. Check the hosting logs."
        )


# ==================================================
# REVOKE GROUP ACCESS
# ==================================================

async def revoke_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage:\n/revoke_group GROUP_ID"
        )
        return

    group_id = context.args[0].strip()

    try:
        with SessionLocal() as db:
            group = (
                db.query(ApprovedGroup)
                .filter(ApprovedGroup.group_id == group_id)
                .first()
            )

            if group is None or not group.active:
                await update.effective_message.reply_text(
                    "ℹ️ This group is not currently approved."
                )
                return

            group.active = False

            (
                db.query(GroupAccessRequest)
                .filter(
                    GroupAccessRequest.group_id == group_id,
                    GroupAccessRequest.status == "pending",
                )
                .update(
                    {"status": "revoked"},
                    synchronize_session=False,
                )
            )

            group_title = group.group_title
            db.commit()

        await update.effective_message.reply_text(
            "🔒 GROUP ACCESS REVOKED\n\n"
            f"📚 Group: {group_title or 'Unknown'}\n"
            f"🆔 Group ID: {group_id}\n\n"
            "This group can no longer receive published quizzes."
        )

    except Exception:
        logger.exception("Failed to revoke group")

        await update.effective_message.reply_text(
            "❌ Could not revoke group access."
        )


# ==================================================
# LIST APPROVED GROUPS
# ==================================================

async def approved_groups(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    try:
        with SessionLocal() as db:
            groups = (
                db.query(ApprovedGroup)
                .filter(ApprovedGroup.active.is_(True))
                .order_by(ApprovedGroup.group_title.asc())
                .all()
            )

            if not groups:
                response = "📭 No groups are currently approved."
            else:
                lines = ["🔐 APPROVED GROUPS\n"]

                for group in groups:
                    lines.append(
                        f"📚 {group.group_title or 'Unnamed group'}\n"
                        f"🆔 ID: {group.group_id}\n"
                    )

                response = "\n".join(lines)

        await update.effective_message.reply_text(response)

    except Exception:
        logger.exception("Failed to list approved groups")

        await update.effective_message.reply_text(
            "❌ Could not retrieve approved groups."
        )


# ==================================================
# ADD CATEGORY
# ==================================================

async def add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    name = " ".join(context.args).strip()

    if not name:
        await update.effective_message.reply_text(
            "Usage: /addcategory English"
        )
        return

    if len(name) > 100:
        await update.effective_message.reply_text(
            "❌ Category name must be 100 characters or fewer."
        )
        return

    try:
        with SessionLocal() as db:
            categories = db.query(Category).all()

            existing = next(
                (
                    category
                    for category in categories
                    if category.name.casefold() == name.casefold()
                ),
                None,
            )

            if existing:
                await update.effective_message.reply_text(
                    "⚠️ This category already exists."
                )
                return

            db.add(Category(name=name))
            db.commit()

        await update.effective_message.reply_text(
            f"✅ Category created!\n📚 Name: {name}"
        )

    except Exception:
        logger.exception("Failed to create category")

        await update.effective_message.reply_text(
            "❌ Could not create the category. Check hosting logs."
        )


# ==================================================
# LIST CATEGORIES
# ==================================================

async def list_categories(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    try:
        with SessionLocal() as db:
            categories = (
                db.query(Category)
                .order_by(Category.name)
                .all()
            )

            if not categories:
                response = "📚 No categories found."
            else:
                lines = ["📚 QUESTION BANK\n"]

                for category in categories:
                    count = (
                        db.query(Question)
                        .filter(
                            Question.category_id == category.id,
                            Question.active.is_(True),
                        )
                        .count()
                    )

                    lines.append(
                        f"• {category.name}: {count} questions"
                    )

                response = "\n".join(lines)

        await update.effective_message.reply_text(response)

    except Exception:
        logger.exception("Failed to list categories")

        await update.effective_message.reply_text(
            "❌ Could not retrieve categories."
        )


# ==================================================
# ADD QUESTION
# ==================================================

async def add_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    message = update.effective_message
    raw_text = message.text or ""
    lines = raw_text.splitlines()

    if not lines:
        return

    command_parts = lines[0].split(maxsplit=1)

    if len(command_parts) != 2:
        await message.reply_text(
            "Usage:\n"
            "/addquestion English\n"
            "Question: What is 2 + 2?\n"
            "A: 3\n"
            "B: 4\n"
            "C: 5\n"
            "D: 6\n"
            "Correct: B\n"
            "Explanation: Two plus two equals four.\n"
            "Difficulty: Easy"
        )
        return

    category_name = command_parts[1].strip()
    fields = {}

    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().casefold()] = value.strip()

    question_text = fields.get("question", "")
    option_a = fields.get("a", "")
    option_b = fields.get("b", "")
    option_c = fields.get("c", "")
    option_d = fields.get("d", "")
    correct_option = fields.get("correct", "").upper()
    explanation = fields.get("explanation", "")
    difficulty = fields.get("difficulty", "Moderate").title()

    if not all([
        question_text,
        option_a,
        option_b,
        option_c,
        option_d,
    ]):
        await message.reply_text(
            "❌ The question and all four options are required."
        )
        return

    if correct_option not in {"A", "B", "C", "D"}:
        await message.reply_text(
            "❌ Correct must be A, B, C, or D."
        )
        return

    if difficulty not in {"Easy", "Moderate", "Difficult"}:
        await message.reply_text(
            "❌ Difficulty must be Easy, Moderate, or Difficult."
        )
        return

    try:
        with SessionLocal() as db:
            categories = db.query(Category).all()

            category = next(
                (
                    item for item in categories
                    if item.name.casefold() == category_name.casefold()
                ),
                None,
            )

            if category is None:
                await message.reply_text(
                    f"❌ Category '{category_name}' not found.\n"
                    f"Create it first with /addcategory {category_name}"
                )
                return

            question = Question(
                category_id=category.id,
                question_text=question_text,
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
                correct_option=correct_option,
                explanation=explanation,
                difficulty=difficulty,
                marks=1.0,
                negative_marks=0.25,
                active=True,
            )

            db.add(question)
            db.commit()
            question_id = question.id

        await message.reply_text(
            "✅ QUESTION SAVED\n\n"
            f"🆔 ID: {question_id}\n"
            f"📚 Category: {category_name}\n"
            f"🎯 Difficulty: {difficulty}\n"
            "➕ Correct: +1\n"
            "➖ Wrong: -0.25"
        )

    except Exception:
        logger.exception("Failed to save question")

        await message.reply_text(
            "❌ Could not save the question. Check hosting logs."
        )


# ==================================================
# QUESTION COUNT
# ==================================================

async def question_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    try:
        with SessionLocal() as db:
            total = (
                db.query(Question)
                .filter(Question.active.is_(True))
                .count()
            )

            categories = db.query(Category).count()

        await update.effective_message.reply_text(
            "📊 0xGREY QUESTION BANK\n\n"
            f"📚 Categories: {categories}\n"
            f"📝 Active questions: {total}"
        )

    except Exception:
        logger.exception("Failed to count questions")

        await update.effective_message.reply_text(
            "❌ Could not retrieve question-bank statistics."
        )


# ==================================================
# TEST POLL
# ==================================================

async def test_poll(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    display_name = get_display_name_safely(chat.id)

    await message.reply_text(
        f"📊 {display_name}: Publishing a sample quiz poll..."
    )

    try:
        await context.bot.send_poll(
            chat_id=chat.id,
            question=f"{display_name} Demo Quiz: What is 2 + 2?",
            options=["3", "4", "5", "6"],
            type="quiz",
            correct_option_id=1,
            is_anonymous=False,
            explanation="Two plus two equals four.",
        )

    except TelegramError:
        logger.exception("Failed to send demo poll")

        await message.reply_text(
            "❌ Could not send the demo poll. Check bot permissions."
        )


# ==================================================
# LOAD QUESTIONS FOR A QUIZ
# ==================================================

def load_quiz_questions(category_name: str, requested_count: int):
    """Return detached quiz data."""

    with SessionLocal() as db:
        categories = db.query(Category).all()

        category = next(
            (
                item for item in categories
                if item.name.casefold() == category_name.casefold()
            ),
            None,
        )

        if category is None:
            return None, None, "category_not_found"

        questions = (
            db.query(Question)
            .filter(
                Question.category_id == category.id,
                Question.active.is_(True),
            )
            .all()
        )

        if not questions:
            return category.name, None, "no_questions"

        count = min(requested_count, len(questions))
        selected = random.sample(questions, count)

        quiz_data = [
            {
                "question": question.question_text,
                "options": [
                    ("A", question.option_a),
                    ("B", question.option_b),
                    ("C", question.option_c),
                    ("D", question.option_d),
                ],
                "correct": question.correct_option,
                "explanation": question.explanation or "",
            }
            for question in selected
        ]

        return category.name, quiz_data, None


# ==================================================
# SEND QUIZ POLLS
# ==================================================

async def send_quiz_polls(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_chat_id: int,
    category_name: str,
    requested_count: int,
    announce_in_current_chat: bool = True,
):
    """Publish shuffled Telegram quiz polls."""

    if not await check_publish_permission_for_target(
        update,
        target_chat_id,
    ):
        return

    actual_category, quiz_data, error = load_quiz_questions(
        category_name,
        requested_count,
    )

    message = update.effective_message

    if error == "category_not_found":
        await message.reply_text(
            f"❌ Category '{category_name}' not found.\n"
            "Use /categories to check category names."
        )
        return

    if error == "no_questions":
        await message.reply_text(
            "❌ This category has no active questions."
        )
        return

    if not quiz_data:
        await message.reply_text(
            "❌ No questions are available."
        )
        return

    if len(quiz_data) < requested_count:
        await message.reply_text(
            f"⚠️ You requested {requested_count} questions, "
            f"but only {len(quiz_data)} are available.\n"
            f"Publishing the available {len(quiz_data)}."
        )

    display_name = get_display_name_safely(target_chat_id)

    if announce_in_current_chat:
        await message.reply_text(
            f"📢 {display_name}: Publishing {len(quiz_data)} quiz polls "
            f"for {actual_category}.\n\n"
            f"🆔 Target chat: {target_chat_id}\n"
            "Each question will appear as a separate Telegram quiz poll."
        )

    sent = 0
    failed = 0

    for item in quiz_data:
        options = item["options"].copy()
        random.shuffle(options)

        option_texts = [
            option_text[:100]
            for _, option_text in options
        ]

        correct_index = next(
            index
            for index, (label, _) in enumerate(options)
            if label == item["correct"]
        )

        question_text = (
            f"{display_name}: {item['question']}"
        )[:300]

        explanation = item["explanation"][:200] or None

        try:
            await context.bot.send_poll(
                chat_id=target_chat_id,
                question=question_text,
                options=option_texts,
                type="quiz",
                correct_option_id=correct_index,
                is_anonymous=False,
                explanation=explanation,
            )

            sent += 1
            await asyncio.sleep(1.1)

        except TelegramError:
            failed += 1
            logger.exception(
                "Failed to publish a quiz poll to chat %s",
                target_chat_id,
            )

    await message.reply_text(
        "🏁 QUIZ PUBLISHING FINISHED\n\n"
        f"📚 Category: {actual_category}\n"
        f"🆔 Target chat: {target_chat_id}\n"
        f"✅ Polls published: {sent}\n"
        f"❌ Failed: {failed}"
    )


# ==================================================
# PUBLISH IN CURRENT CHAT
# ==================================================

async def publish_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/publishquiz English 5\n"
            "/publishquiz Banking Awareness 10\n\n"
            "The final number is the question count.\n"
            "For another approved group, use /publishquiz_to."
        )
        return

    try:
        count = int(context.args[-1])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ The last argument must be a number.\n"
            "Example: /publishquiz English 5"
        )
        return

    if not 1 <= count <= 200:
        await update.effective_message.reply_text(
            "❌ Choose between 1 and 200 questions."
        )
        return

    if not await check_publish_permission(update):
        return

    category_name = " ".join(context.args[:-1]).strip()

    await send_quiz_polls(
        update=update,
        context=context,
        target_chat_id=update.effective_chat.id,
        category_name=category_name,
        requested_count=count,
    )


# ==================================================
# PUBLISH TO A SPECIFIC APPROVED GROUP
# ==================================================

async def publish_quiz_to(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/publishquiz_to GROUP_ID COUNT CATEGORY\n\n"
            "Examples:\n"
            "/publishquiz_to -1001234567890 5 English\n"
            "/publishquiz_to -1001234567890 10 Banking Awareness\n\n"
            "Use /approved_groups to see approved group IDs."
        )
        return

    try:
        group_id = int(context.args[0].strip())
        count = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid group ID or question count."
        )
        return

    if group_id >= 0:
        await update.effective_message.reply_text(
            "❌ The target must be a Telegram group or supergroup ID."
        )
        return

    if not 1 <= count <= 200:
        await update.effective_message.reply_text(
            "❌ Choose between 1 and 200 questions."
        )
        return

    category_name = " ".join(context.args[2:]).strip()

    approved = get_approved_group(str(group_id))

    if approved is None:
        await update.effective_message.reply_text(
            "⛔ This target group is not approved.\n"
            "Use /approved_groups to check active approvals."
        )
        return

    await send_quiz_polls(
        update=update,
        context=context,
        target_chat_id=group_id,
        category_name=category_name,
        requested_count=count,
    )


# ==================================================
# ERROR HANDLER
# ==================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Telegram update processing failed",
        exc_info=context.error,
    )


# ==================================================
# MAIN APPLICATION
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. "
            "Configure it in hosting environment variables."
        )

    if not OWNER_ID.isdigit():
        raise RuntimeError(
            "TELEGRAM_OWNER_ID must contain your numeric Telegram user ID."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # General commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))

    # Custom chat display name
    application.add_handler(
        CommandHandler("set_chat_name", set_chat_name)
    )
    application.add_handler(
        CommandHandler("get_chat_name", get_chat_name)
    )
    application.add_handler(
        CommandHandler("reset_chat_name", reset_chat_name)
    )

    # Question bank
    application.add_handler(
        CommandHandler("addcategory", add_category)
    )
    application.add_handler(
        CommandHandler("categories", list_categories)
    )
    application.add_handler(
        CommandHandler("addquestion", add_question)
    )
    application.add_handler(
        CommandHandler("questioncount", question_count)
    )

    # Quiz publishing
    application.add_handler(
        CommandHandler("testpoll", test_poll)
    )
    application.add_handler(
        CommandHandler("publishquiz", publish_quiz)
    )
    application.add_handler(
        CommandHandler("publishquiz_to", publish_quiz_to)
    )

    # Group access management
    application.add_handler(
        CommandHandler("request_access", request_access)
    )
    application.add_handler(
        CommandHandler("pending_groups", pending_groups)
    )
    application.add_handler(
        CommandHandler("approve_group", approve_group)
    )
    application.add_handler(
        CommandHandler("revoke_group", revoke_group)
    )
    application.add_handler(
        CommandHandler("approved_groups", approved_groups)
    )

    application.add_error_handler(error_handler)

    logger.info("Starting 0xGREY Telegram bot...")

    application.run_polling()


if __name__ == "__main__":
    main()
