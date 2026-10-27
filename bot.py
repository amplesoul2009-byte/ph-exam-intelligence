"""
0xGREY Telegram bot — complete replacement.

Compatible with the current database.py schema in:
amplesoul2009-byte/ph-exam-intelligence

AI provider selection:
  AI_PROVIDER=gemini35  -> Gemini 3.5 Flash
  AI_PROVIDER=gemini38  -> Gemini 3.8 Flash
  AI_PROVIDER=groq      -> Groq model configured by GROQ_MODEL

This bot stores quiz definitions and submitted attempts in SQLAlchemy.
Active quiz sessions and submitted poll answers are persisted in the existing database,
so sessions can be restored after a normal bot-process restart.
"""

import os
import json
import time
import random
import asyncio
import logging
import re
import hashlib
import secrets
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import text

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from database import (
    Category,
    Question,
    Quiz,
    StudentAttempt,
    ApprovedGroup,
    GroupAccessRequest,
    ChatSetting,
    SessionLocal,
    init_db,
)

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("0xGREY")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()
DEFAULT_DISPLAY_NAME = "0xGREY"

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini38").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    {
        "gemini35": "gemini-3.5-flash",
        "gemini38": "gemini-3.8-flash",
    }.get(AI_PROVIDER, "gemini-3.8-flash"),
).strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

MOTIVATIONAL_QUOTES = [
    "Consistency creates progress.",
    "Every attempt is an opportunity to improve.",
    "Discipline turns preparation into performance.",
    "A mistake understood today becomes strength tomorrow.",
    "Focus on improvement, not perfection.",
    "Keep learning. Keep practising. Keep progressing.",
]


# ---------------------------------------------------------------------
# Permissions and database helpers
# ---------------------------------------------------------------------

def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(user and OWNER_ID and str(user.id) == OWNER_ID)


async def owner_required(update: Update) -> bool:
    # All control, import, preview, and approval commands are private-chat only.
    # Group chats are destinations for approved publishing, never admin consoles.
    chat = update.effective_chat
    if chat and chat.type != "private":
        if update.effective_message:
            await update.effective_message.reply_text(
                "🔒 For security, use the bot's private chat for owner controls and previews. "
                "Publishing is sent to an owner-approved group only."
            )
        return False
    if is_owner(update):
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ This command is restricted to the configured bot owner."
        )
    return False


def display_name_for(chat_id: int) -> str:
    try:
        with SessionLocal() as db:
            setting = (
                db.query(ChatSetting)
                .filter_by(chat_id=str(chat_id))
                .first()
            )
            if setting and setting.display_name:
                return setting.display_name
    except Exception:
        logger.exception("Could not read chat display name")
    return DEFAULT_DISPLAY_NAME


def active_approved_group(group_id: int | str):
    with SessionLocal() as db:
        return (
            db.query(ApprovedGroup)
            .filter(
                ApprovedGroup.group_id == str(group_id),
                ApprovedGroup.active.is_(True),
            )
            .first()
        )


async def check_target_permission(
    update: Update, target_chat_id: int
) -> bool:
    # Positive IDs are private user chats; never publish quizzes or leaderboards there.
    # Zero is a preview-only placeholder until the owner assigns an approved group.
    if target_chat_id == 0:
        return True
    if target_chat_id > 0:
        if update.effective_message:
            await update.effective_message.reply_text(
                "🔒 Private bot chats are preview/control only. Set an approved group target "
                "with /setquiztarget QUIZ_ID GROUP_ID before approving."
            )
        return False
    if active_approved_group(target_chat_id):
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Publishing blocked: the target group is not owner-approved.\n"
            "Use /approved_groups to review approved groups."
        )
    return False


def get_sessions(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("quiz_sessions", {})


def get_poll_index(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("poll_index", {})


def ensure_live_session_table() -> None:
    """Create a small durable store in the same database used by database.py."""
    with SessionLocal() as db:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS live_quiz_sessions (
                quiz_id TEXT PRIMARY KEY,
                session_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        db.commit()


def persist_live_session(session: dict) -> None:
    """Save one live session after publication/answer/finalization changes."""
    quiz_id = str(session["quiz_id"])
    payload = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
    with SessionLocal() as db:
        db.execute(text("""
            INSERT INTO live_quiz_sessions (quiz_id, session_json, updated_at)
            VALUES (:quiz_id, :payload, :updated_at)
            ON CONFLICT(quiz_id) DO UPDATE SET
                session_json = excluded.session_json,
                updated_at = excluded.updated_at
        """), {
            "quiz_id": quiz_id,
            "payload": payload,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        })
        db.commit()


def restore_live_sessions(app: Application) -> None:
    """Restore active sessions and rebuild poll-id lookup after a process restart."""
    ensure_live_session_table()
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT quiz_id, session_json FROM live_quiz_sessions"
        )).fetchall()

    sessions = app.bot_data.setdefault("quiz_sessions", {})
    poll_index = app.bot_data.setdefault("poll_index", {})
    restored = 0
    for row in rows:
        try:
            session = json.loads(row[1])
            # JSON converts integer dictionary keys to strings; Telegram poll IDs are integers.
            for participant in session.get("participants", {}).values():
                participant["answers"] = {
                    int(poll_id): answer
                    for poll_id, answer in participant.get("answers", {}).items()
                }
            key = str(session.get("quiz_id", row[0]))
            sessions[key] = session
            if not session.get("finalized"):
                for poll_id, metadata in session.get("poll_metadata", {}).items():
                    poll_index[int(poll_id)] = {
                        **metadata,
                        "session_key": key,
                    }
            restored += 1
        except Exception:
            logger.exception("Could not restore persisted quiz session %s", row[0])
    logger.info("Restored %s persisted quiz session(s)", restored)


# ---------------------------------------------------------------------
# AI provider integration
# ---------------------------------------------------------------------

def _request_ai_text(prompt: str) -> str:
    """Synchronous provider call; called through asyncio.to_thread."""
    if AI_PROVIDER in {"gemini35", "gemini38", "gemini"}:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        result = (response.text or "").strip()
        if not result:
            raise RuntimeError("Gemini returned an empty response.")
        return result

    if AI_PROVIDER == "groq":
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not configured.")
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful exam-preparation coach. "
                        "Be accurate, concise, and do not invent results."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        result = (response.choices[0].message.content or "").strip()
        if not result:
            raise RuntimeError("Groq returned an empty response.")
        return result

    raise RuntimeError(
        "Invalid AI_PROVIDER. Use gemini35, gemini38, or groq."
    )


async def ai_text(prompt: str) -> str:
    return await asyncio.to_thread(_request_ai_text, prompt)


def fallback_feedback(correct: int, wrong: int, unanswered: int,
                      accuracy: float, score: float) -> dict:
    if accuracy >= 90:
        tagline = "Outstanding accuracy — excellent work!"
        insight = "Maintain this accuracy and continue practising difficult questions."
    elif accuracy >= 75:
        tagline = "Strong performance — keep building momentum."
        insight = "Review the questions you missed and strengthen weak concepts."
    elif accuracy >= 50:
        tagline = "Good progress — targeted practice can help."
        insight = "Study incorrect answers and practise similar questions carefully."
    elif correct:
        tagline = "Every correct answer is progress."
        insight = "Review explanations and build confidence topic by topic."
    else:
        tagline = "Your preparation journey starts here."
        insight = "Start by reviewing the explanations, then try another practice set."
    if unanswered:
        insight += f" {unanswered} questions were unanswered; review time management."
    return {
        "tagline": tagline,
        "insight": insight,
        "quote": random.choice(MOTIVATIONAL_QUOTES),
    }


async def make_feedback(stats: dict) -> dict:
    fallback = fallback_feedback(
        int(stats["correct"]),
        int(stats["wrong"]),
        int(stats["unanswered"]),
        float(stats["accuracy"]),
        float(stats["score"]),
    )
    prompt = (
        "Write concise, constructive exam feedback from these verified results only.\n"
        f"{json.dumps(stats, ensure_ascii=False)}\n"
        "Return only valid JSON with exactly three string keys: "
        "tagline, insight, quote. Keep tagline under 100 characters, "
        "insight under 300 characters, quote under 160 characters. "
        "Do not invent ranks, history, or improvements."
    )
    try:
        raw = await ai_text(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```")
            raw = raw.removesuffix("```").strip()
        data = json.loads(raw)
        return {
            "tagline": str(data["tagline"])[:300],
            "insight": str(data["insight"])[:1000],
            "quote": str(data["quote"])[:500],
        }
    except Exception:
        logger.exception("AI feedback unavailable; using local feedback")
        return fallback


# ---------------------------------------------------------------------
# General commands
# ---------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return
    name = display_name_for(chat.id)
    await message.reply_text(
        f"🎓 Welcome to {name}!\n\n"
        "Premium Mock Test Platform\n"
        "📚 Question bank\n"
        "📝 Telegram quiz polls\n"
        "🔀 Shuffled answer options\n"
        "🔐 Owner-approved group publishing\n"
        "📊 Scoring and leaderboard\n"
        "🤖 AI performance feedback\n\n"
        "Use /help to see commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "📌 0xGREY COMMANDS\n\n"
        "GENERAL\n/start\n/help\n/status\n/aistatus\n/aifeedback\n\n"
        "QUESTION BANK — OWNER ONLY\n"
        "/addcategory English\n/categories\n"
        "/addquestion English (then send the question fields)\n"
        "/questioncount\n\n"
        "QUIZZES — OWNER ONLY\n"
        "/testpoll\n/publishquiz English 5 [--time 10]\n/importquiz English [--time 10] (then upload TXT/PDF or paste questions)\n"
        "/publishquiz_to GROUP_ID COUNT CATEGORY\n"
        "/activequizzes\n/setquiztarget QUIZ_ID GROUP_ID\n/approvequiz QUIZ_ID (required before polls start)\n"
        "/finalizequiz QUIZ_ID\n/leaderboard QUIZ_ID\n\n"
        "GROUP ACCESS\n/request_access\n/pending_groups\n"
        "/approve_group GROUP_ID\n/revoke_group GROUP_ID\n"
        "/approved_groups\n\n"
        "CHAT DISPLAY NAME — OWNER ONLY\n"
        "/set_chat_name NAME\n/get_chat_name\n/reset_chat_name\n\n"
        "QUESTION FORMAT\n"
        "/addquestion English\n"
        "Question: What is 2 + 2?\nA: 3\nB: 4\nC: 5\nD: 6\n"
        "Correct: B\nExplanation: Two plus two equals four.\n"
        "Difficulty: Easy"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "✅ 0xGREY is running.\n"
        f"Owner access: {'enabled' if is_owner(update) else 'not enabled for this user'}"
    )


async def ai_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    provider = AI_PROVIDER
    model = GEMINI_MODEL if provider in {"gemini", "gemini35", "gemini38"} else GROQ_MODEL
    key_present = bool(
        GEMINI_API_KEY if provider in {"gemini", "gemini35", "gemini38"}
        else GROQ_API_KEY if provider == "groq"
        else False
    )
    await message.reply_text(
        "🤖 AI CONFIGURATION\n\n"
        f"Provider: {provider}\nModel: {model}\n"
        f"API key configured: {'Yes' if key_present else 'No'}\n\n"
        "Set AI_PROVIDER to gemini35, gemini38, or groq."
    )


async def ai_feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    message = update.effective_message
    if not message:
        return
    prompt = (
        "Give a short, useful study tip for a student preparing for "
        "banking prelims exams. Return plain text, not JSON."
    )
    try:
        result = await ai_text(prompt)
        await message.reply_text("🤖 AI TEST\n\n" + result[:3500])
    except Exception as exc:
        logger.exception("AI test failed")
        await message.reply_text(f"❌ AI test failed: {str(exc)[:500]}")


# ---------------------------------------------------------------------
# Chat display name
# ---------------------------------------------------------------------

async def set_chat_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    chat, user, message = (
        update.effective_chat, update.effective_user, update.effective_message
    )
    if not chat or not user or not message:
        return
    name = " ".join(context.args).strip()
    if not name or len(name) > 100:
        await message.reply_text("Usage: /set_chat_name 0xGREY")
        return
    with SessionLocal() as db:
        setting = db.query(ChatSetting).filter_by(chat_id=str(chat.id)).first()
        if setting:
            setting.display_name = name
            setting.updated_by = str(user.id)
        else:
            db.add(ChatSetting(
                chat_id=str(chat.id),
                display_name=name,
                updated_by=str(user.id),
            ))
        db.commit()
    await message.reply_text(f"✅ Display name set to: {name}")


async def get_chat_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    chat = update.effective_chat
    if chat:
        await update.effective_message.reply_text(
            f"Display name: {display_name_for(chat.id)}\nChat ID: {chat.id}"
        )


async def reset_chat_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    chat = update.effective_chat
    if not chat:
        return
    with SessionLocal() as db:
        setting = db.query(ChatSetting).filter_by(chat_id=str(chat.id)).first()
        if setting:
            db.delete(setting)
            db.commit()
    await update.effective_message.reply_text(
        f"✅ Default name restored: {DEFAULT_DISPLAY_NAME}"
    )


# ---------------------------------------------------------------------
# Group access allowlist
# ---------------------------------------------------------------------

async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user, message = (
        update.effective_chat, update.effective_user, update.effective_message
    )
    if not chat or not user or not message:
        return
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("Run /request_access inside the target group.")
        return
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in {"administrator", "creator"}:
            await message.reply_text("⛔ Only group administrators can request access.")
            return
    except TelegramError:
        logger.exception("Could not verify group administrator")
        await message.reply_text("Could not verify administrator status. Try again.")
        return

    with SessionLocal() as db:
        approved = db.query(ApprovedGroup).filter(
            ApprovedGroup.group_id == str(chat.id),
            ApprovedGroup.active.is_(True),
        ).first()
        if approved:
            await message.reply_text("✅ This group is already approved.")
            return
        req = db.query(GroupAccessRequest).filter(
            GroupAccessRequest.group_id == str(chat.id),
            GroupAccessRequest.status == "pending",
        ).first()
        if req:
            await message.reply_text("⏳ A request is already pending.")
            return
        previous = db.query(GroupAccessRequest).filter(
            GroupAccessRequest.group_id == str(chat.id)
        ).order_by(GroupAccessRequest.id.desc()).first()
        if previous:
            previous.group_title = chat.title or ""
            previous.requested_by = str(user.id)
            previous.requester_username = user.username or ""
            previous.status = "pending"
            previous.requested_at = datetime.utcnow()
            previous.reviewed_by = None
            previous.reviewed_at = None
        else:
            db.add(GroupAccessRequest(
                group_id=str(chat.id),
                group_title=chat.title or "",
                requested_by=str(user.id),
                requester_username=user.username or "",
                status="pending",
            ))
        db.commit()

    await message.reply_text(
        "📨 Access request submitted.\n"
        f"Group: {chat.title or 'Unnamed'}\nID: {chat.id}\n"
        "The bot owner must approve it."
    )
    if OWNER_ID:
        try:
            await context.bot.send_message(
                int(OWNER_ID),
                f"📨 GROUP ACCESS REQUEST\n{chat.title or 'Unnamed'}\n"
                f"Group ID: {chat.id}\nRequested by: {user.id}",
            )
        except TelegramError:
            logger.exception("Could not notify owner about access request")


async def pending_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    with SessionLocal() as db:
        rows = db.query(GroupAccessRequest).filter(
            GroupAccessRequest.status == "pending"
        ).order_by(GroupAccessRequest.requested_at.asc()).all()
        lines = ["📨 PENDING GROUP REQUESTS\n"]
        for row in rows:
            lines.append(
                f"{row.group_title or 'Unnamed'}\nID: {row.group_id}\n"
                f"/approve_group {row.group_id}\n"
            )
    await update.effective_message.reply_text(
        "\n".join(lines) if rows else "No pending requests."
    )


async def approve_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /approve_group GROUP_ID")
        return
    group_id = context.args[0]
    with SessionLocal() as db:
        req = db.query(GroupAccessRequest).filter(
            GroupAccessRequest.group_id == group_id,
            GroupAccessRequest.status == "pending",
        ).order_by(GroupAccessRequest.id.desc()).first()
        group = db.query(ApprovedGroup).filter(
            ApprovedGroup.group_id == group_id
        ).first()
        title = req.group_title if req else ""
        if group:
            group.active = True
            group.group_title = title or group.group_title
            group.approved_by = str(update.effective_user.id)
            group.approved_at = datetime.utcnow()
        else:
            db.add(ApprovedGroup(
                group_id=group_id,
                group_title=title,
                approved_by=str(update.effective_user.id),
                active=True,
            ))
        if req:
            req.status = "approved"
            req.reviewed_by = str(update.effective_user.id)
            req.reviewed_at = datetime.utcnow()
        db.commit()
    await update.effective_message.reply_text(f"✅ Group {group_id} approved.")


async def revoke_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /revoke_group GROUP_ID")
        return
    group_id = context.args[0]
    with SessionLocal() as db:
        group = db.query(ApprovedGroup).filter(
            ApprovedGroup.group_id == group_id
        ).first()
        if not group:
            await update.effective_message.reply_text("Group not found.")
            return
        group.active = False
        db.commit()
    await update.effective_message.reply_text(f"🔒 Access revoked for {group_id}.")


async def approved_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    with SessionLocal() as db:
        rows = db.query(ApprovedGroup).filter(
            ApprovedGroup.active.is_(True)
        ).order_by(ApprovedGroup.group_title.asc()).all()
        lines = ["🔐 APPROVED GROUPS\n"]
        for row in rows:
            lines.append(f"{row.group_title or 'Unnamed'}\nID: {row.group_id}\n")
    await update.effective_message.reply_text(
        "\n".join(lines) if rows else "No approved groups."
    )


# ---------------------------------------------------------------------
# Categories and questions
# ---------------------------------------------------------------------

async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    name = " ".join(context.args).strip()
    if not name or len(name) > 100:
        await update.effective_message.reply_text("Usage: /addcategory English")
        return
    with SessionLocal() as db:
        existing = db.query(Category).filter(
            Category.name.ilike(name)
        ).first()
        if existing:
            await update.effective_message.reply_text("Category already exists.")
            return
        db.add(Category(name=name, description=""))
        db.commit()
    await update.effective_message.reply_text(f"✅ Category created: {name}")


async def list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    with SessionLocal() as db:
        categories = db.query(Category).order_by(Category.name.asc()).all()
        lines = ["📚 CATEGORIES\n"]
        for cat in categories:
            count = db.query(Question).filter(
                Question.category_id == cat.id,
                Question.active.is_(True),
            ).count()
            lines.append(f"• {cat.name}: {count} active questions")
    await update.effective_message.reply_text(
        "\n".join(lines) if categories else "No categories found."
    )


async def save_question_fields(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category_name: str,
    raw_text: str,
):
    message = update.effective_message
    if not message:
        return

    fields = {}
    for line in raw_text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().casefold()] = value.strip()

    qtext = fields.get("question", "")
    opts = {letter: fields.get(letter.lower(), "") for letter in "ABCD"}
    correct = fields.get("correct", "").upper()
    explanation = fields.get("explanation", "")
    difficulty = fields.get("difficulty", "Moderate").title()

    if not qtext or not all(opts.values()):
        await message.reply_text(
            "❌ Question text and all options A, B, C, D are required.\n"
            "Please send the complete question block in one message."
        )
        return
    if correct not in "ABCD" or len(correct) != 1:
        await message.reply_text("❌ Correct must be A, B, C, or D.")
        return
    if difficulty not in {"Easy", "Moderate", "Difficult"}:
        await message.reply_text(
            "❌ Difficulty must be Easy, Moderate, or Difficult."
        )
        return

    with SessionLocal() as db:
        category = db.query(Category).filter(
            Category.name.ilike(category_name)
        ).first()
        if not category:
            await message.reply_text(
                f"Category '{category_name}' not found. "
                "Create it with /addcategory first."
            )
            return
        q = Question(
            category_id=category.id,
            question_text=qtext,
            option_a=opts["A"],
            option_b=opts["B"],
            option_c=opts["C"],
            option_d=opts["D"],
            correct_option=correct,
            explanation=explanation,
            difficulty=difficulty,
            marks=1.0,
            negative_marks=0.25,
            active=True,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        qid = q.id

    context.user_data.pop("pending_add_question_category", None)
    await message.reply_text(
        f"✅ Question saved.\nID: {qid}\nCategory: {category_name}\n"
        f"Difficulty: {difficulty}\nScoring: +1 correct, -0.25 wrong."
    )


async def add_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    message = update.effective_message
    if not message:
        return

    parts = (message.text or "").splitlines()
    first = parts[0].split(maxsplit=1) if parts else []
    if len(first) != 2:
        await message.reply_text(
            "Usage: /addquestion CATEGORY\n"
            "Then send the question, options, correct answer, explanation, and difficulty."
        )
        return

    category_name = first[1].strip()
    raw_text = "\n".join(parts[1:]).strip()
    if raw_text:
        await save_question_fields(update, context, category_name, raw_text)
        return

    context.user_data["pending_add_question_category"] = category_name
    await message.reply_text(
        "Send one question using this format:\n\n"
        "Question: What is 2 + 2?\n"
        "A: 3\nB: 4\nC: 5\nD: 6\n"
        "Correct: B\n"
        "Explanation: Two plus two equals four.\n"
        "Difficulty: Easy"
    )
              "Then send the question fields in your next message."
        )
        return

    category_name = first[1].strip()
    remaining_fields = "\n".join(parts[1:]).strip()
    if not remaining_fields:
        # Support the user's preferred two-message workflow:
        # command first, question fields in the next message.
        with SessionLocal() as db:
            category = db.query(Category).filter(
                Category.name.ilike(category_name)
            ).first()
        if not category:
            await message.reply_text(
                f"Category '{category_name}' not found. "
                "Create it with /addcategory first."
            )
            return
        context.user_data["pending_add_question_category"] = category.name
        await message.reply_text(
            f"📝 Send the question fields for category {category.name} "
            "in your next message, all together.\n\n"
            "Question: What is 2 + 2?\nA: 3\nB: 4\nC: 5\nD: 6\n"
            "Correct: B\nExplanation: Two plus two equals four.\n"
            "Difficulty: Easy"
        )
        return

    await save_question_fields(update, context, category_name, remaining_fields)


async def pending_question_fields(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("pending_add_question_category"):
        return
    if not await owner_required(update):
        context.user_data.pop("pending_add_question_category", None)
        return
    message = update.effective_message
    if not message or not message.text or message.text.startswith("/"):
        return
    category_name = context.user_data["pending_add_question_category"]
    await save_question_fields(update, context, category_name, message.text)


async def question_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    with SessionLocal() as db:
        total = db.query(Question).filter(Question.active.is_(True)).count()
        cats = db.query(Category).count()
    await update.effective_message.reply_text(
        f"📊 Question bank\nCategories: {cats}\nActive questions: {total}"
    )


# ---------------------------------------------------------------------
# Demo poll
# ---------------------------------------------------------------------

async def test_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    chat = update.effective_chat
    if not chat:
        return
    name = display_name_for(chat.id)
    await context.bot.send_poll(
        chat_id=chat.id,
        question=f"{name}: What is 2 + 2?",
        options=["3", "4", "5", "6"],
        type="quiz",
        correct_option_id=1,
        is_anonymous=False,
        explanation="Two plus two equals four.",
    )


# ---------------------------------------------------------------------
# Quiz publishing and answer collection
# ---------------------------------------------------------------------

def fetch_questions(category_name: str, count: int):
    with SessionLocal() as db:
        category = db.query(Category).filter(
            Category.name.ilike(category_name)
        ).first()
        if not category:
            return None, None
        pool = db.query(Question).filter(
            Question.category_id == category.id,
            Question.active.is_(True),
        ).all()
        if len(pool) < count:
            return category, None
        selected = random.sample(pool, count)
        result = []
        for q in selected:
            result.append({
                "id": q.id,
                "text": q.question_text,
                "options": {
                    "A": q.option_a,
                    "B": q.option_b,
                    "C": q.option_c,
                    "D": q.option_d,
                },
                "correct": q.correct_option.upper(),
                "explanation": q.explanation or "",
                "marks": float(q.marks or 1.0),
                "negative": float(q.negative_marks or 0.25),
            })
        return category, result


async def publish_quiz_to_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_chat_id: int,
    category_name: str,
    count: int,
    time_limit_seconds: int = 30,
    question_ids: list[int] | None = None,
):
    message = update.effective_message
    if not 5 <= time_limit_seconds <= 600:
        await message.reply_text("Per-question timer must be 5–600 seconds.")
        return
    if target_chat_id > 0:
        await message.reply_text(
            "🔒 Quiz polls cannot be published in private bot chats. Use /publishquiz_to "
            "GROUP_ID COUNT CATEGORY or import questions for preview, then set an approved group target."
        )
        return
    if target_chat_id < 0 and not await check_target_permission(update, target_chat_id):
        return
    if question_ids:
        with SessionLocal() as db:
            category = db.query(Category).filter(Category.name.ilike(category_name)).first()
            imported_rows = db.query(Question).filter(
                Question.id.in_(question_ids),
                Question.active.is_(True),
            ).order_by(Question.id.asc()).all()
            questions = [{
                "id": q.id,
                "text": q.question_text,
                "options": {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d},
                "correct": q.correct_option.upper(),
                "explanation": q.explanation or "",
                "marks": float(q.marks or 1.0),
                "negative": float(q.negative_marks or 0.25),
            } for q in imported_rows]
    else:
        category, questions = fetch_questions(category_name, count)
    if not category:
        await message.reply_text(f"Category '{category_name}' not found.")
        return
    if questions is None:
        await message.reply_text("Not enough active questions in that category.")
        return

    # Store the quiz definition in the existing database schema.
    with SessionLocal() as db:
        quiz = Quiz(
            title=f"{category.name} | {datetime.utcnow():%Y-%m-%d %H:%M}",
            category_id=category.id,
            question_count=len(questions),
            duration_minutes=max(1, (len(questions) * time_limit_seconds + 59) // 60),
            marks_per_correct=1.0,
            negative_marks=0.25,
            shuffle_questions=True,
            shuffle_options=True,
            status="pending_approval",
        )
        db.add(quiz)
        db.commit()
        db.refresh(quiz)
        quiz_id = quiz.id

    session_key = str(quiz_id)
    if target_chat_id == 0:
        group_title = "Not selected — preview only"
    else:
        try:
            target = await context.bot.get_chat(target_chat_id)
            group_title = target.title or str(target_chat_id)
        except TelegramError:
            group_title = str(target_chat_id)

    session = {
        "quiz_id": quiz_id,
        "title": f"{category.name} Quiz",
        "category": category.name,
        "group_id": str(target_chat_id),
        "group_title": group_title,
        "started_at": time.time(),
        "questions": questions,
        "participants": {},
        "poll_ids": [],
        "poll_metadata": {},
        "finalized": False,
        "awaiting_approval": True,
        "time_limit_seconds": time_limit_seconds,
    }
    get_sessions(context)[session_key] = session
    persist_live_session(session)
    poll_index = get_poll_index(context)

    # Every quiz requires explicit owner approval before any poll is published.
    session["sequential"] = True
    session["current_index"] = 0
    session["current_poll_end_at"] = None
    session["publish_failed"] = 0
    session["awaiting_approval"] = True
    persist_live_session(session)
    await message.reply_text(
        f"📝 Quiz {quiz_id} prepared for approval.\n"
        f"Category: {category.name}\nQuestions: {len(questions)}\n"
        f"Per-question timer: {time_limit_seconds}s\nTarget: {group_title} ({target_chat_id})\n\n"
        f"🔎 PRIVATE PREVIEW ONLY — no polls have been sent.\n"
        f"1. Choose an owner-approved group: /setquiztarget {quiz_id} GROUP_ID\n"
        f"2. Approve publishing: /approvequiz {quiz_id}\n"
        "Only the selected approved group receives polls and the final leaderboard."
    )
    # Include answer keys and explanations in the private bot preview only.
    preview_chunks = []
    current_chunk = f"🔎 PRIVATE QUIZ PREVIEW — Quiz {quiz_id}\nAnswer keys are visible only here.\n\n"
    for idx, item in enumerate(questions, 1):
        block = (
            f"Q{idx}. {item['text']}\n"
            f"A. {item['options'].get('A', '')}\n"
            f"B. {item['options'].get('B', '')}\n"
            f"C. {item['options'].get('C', '')}\n"
            f"D. {item['options'].get('D', '')}\n"
            f"Correct answer: {item['correct']}\n"
            f"Explanation: {item.get('explanation', '') or 'Not provided'}\n\n"
        )
        if len(current_chunk) + len(block) > 3500:
            preview_chunks.append(current_chunk)
            current_chunk = f"🔎 PRIVATE QUIZ PREVIEW — Quiz {quiz_id} (continued)\n\n"
        current_chunk += block
    if current_chunk.strip():
        preview_chunks.append(current_chunk)
    for chunk in preview_chunks:
        await message.reply_text(chunk)



def parse_bulk_question_blocks(raw_text: str) -> list[dict]:
    # Best-effort parser for common human-written MCQ layouts.
    text_value = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text_value:
        return []
    lines = []
    for raw_line in text_value.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        line = re.sub(r"^[•●▪◦*\-]+\s*", "", line)
        line = re.sub(r"^\[(?:Option\s*)?([A-D])\]\s*", r"\1: ", line, flags=re.I)
        line = re.sub(r"^\(([A-D])\)\s*", r"\1: ", line, flags=re.I)
        line = re.sub(r"^([A-D])[.)]\s+", r"\1: ", line, flags=re.I)
        line = re.sub(r"^(?:Correct\s+Answer|Answer\s+Key|Correct\s+Option)\s*[:\-]\s*", "Correct: ", line, flags=re.I)
        line = re.sub(r"^Q(?:uestion)?\s*\d+\s*[:.)\-]\s*", "Question: ", line, flags=re.I)
        line = re.sub(r"^Question\s+\d+\s*$", "Question: ", line, flags=re.I)
        if re.match(r"^Correct\s*:", line, re.I):
            line = re.sub(r"^Correct\s*:\s*\[?([A-D])\]?\s*", r"Correct: \1 ", line, flags=re.I)
            line = re.sub(r"^Correct\s*:\s*([A-D])\s*[-.)]\s*.*$", r"Correct: \1", line, flags=re.I)
        lines.append(line)
    normalized = "\n".join(lines)
    starts = [m.start() for m in re.finditer(r"(?im)^\s*Question\s*:\s*", normalized)]
    if not starts:
        # Numbered question headings such as "Question 1" or "1.".
        starts = [m.start() for m in re.finditer(r"(?im)^\s*(?:Question\s+)?\d+\s*[.):]\s*\S", text_value)]
        if starts:
            chunks = [text_value[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text_value)])]
            normalized = "\n".join(re.sub(r"(?im)^\s*(?:Question\s+)?\d+\s*[.):]\s*", "Question: ", c, count=1) for c in chunks)
            starts = [m.start() for m in re.finditer(r"(?im)^\s*Question\s*:\s*", normalized)]
    if not starts:
        return []
    blocks = [normalized[a:b].strip() for a, b in zip(starts, starts[1:] + [len(normalized)])]
    parsed = []
    for block in blocks:
        fields, current_key = {}, None
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.match(r"(?i)^(question|[a-d]|correct|explanation|difficulty|correct answer|answer key)\s*[:.)\-]\s*(.*)$", line)
            if match:
                key, value = match.group(1).casefold(), match.group(2).strip()
                if key in {"correct answer", "answer key"}:
                    key = "correct"
                if key == "correct":
                    answer_match = re.match(r"\[?([A-D])\]?\b", value, re.I)
                    if answer_match:
                        value = answer_match.group(1).upper()
                fields[key] = value
                current_key = key
            elif current_key in {"question", "explanation"}:
                fields[current_key] = (fields.get(current_key, "") + " " + line).strip()
        options = {letter: fields.get(letter.lower(), "") for letter in "ABCD"}
        correct = fields.get("correct", "").strip().upper()
        correct_match = re.match(r"\[?([A-D])\]?\b", correct, re.I)
        if correct_match:
            correct = correct_match.group(1).upper()
        if fields.get("question") and all(options.values()) and correct in {"A", "B", "C", "D"}:
            difficulty = fields.get("difficulty", "Moderate").title()
            if difficulty not in {"Easy", "Moderate", "Difficult"}:
                difficulty = "Moderate"
            parsed.append({"text": fields["question"], "options": options, "correct": correct,
                           "explanation": fields.get("explanation", ""), "difficulty": difficulty})
    return parsed


async def normalize_bulk_questions_with_ai(raw_text: str) -> list[dict]:
    # Use the configured AI model to normalize varied pasted/PDF MCQ layouts.
    if not raw_text.strip():
        return []
    prompt = '''You are an exam-question format normalizer. Convert the supplied source text into a JSON array of multiple-choice questions. The source may use any numbering, bullets, option labels, answer-key wording, layout, or PDF line wrapping.

Rules:
- Extract only questions actually present in the source; preserve the original meaning and facts.
- Reconstruct line-wrapped question/options where clear. Accept labels such as [A], (A), A), Option A, 1), and similar patterns.
- Each item must have: text, options (object with A, B, C, D), correct (one letter A-D), explanation (string, may be empty), difficulty (Easy, Moderate, or Difficult; default Moderate).
- Map the source's correct answer to A-D after reading the option labels. If the source gives answer text rather than a letter, match it to the option. Do not invent a correct answer. If the key is missing or genuinely ambiguous, omit that question rather than guess.
- Do not add questions, options, explanations, facts, or answer keys not supported by the source. Do not answer from general knowledge to fill gaps.
- Ignore page headers, footers, instructions, and unrelated prose.
- Return ONLY valid JSON, no Markdown fences or commentary.

Expected JSON shape:
[{"text":"question text","options":{"A":"...","B":"...","C":"...","D":"..."},"correct":"C","explanation":"...","difficulty":"Moderate"}]

SOURCE TEXT:
''' + raw_text[:90000]
    response = await ai_text(prompt)
    candidate = response.strip()
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
    candidate = re.sub(r"\s*```$", "", candidate)
    first, last = candidate.find("["), candidate.rfind("]")
    if first < 0 or last < first:
        return []
    data = json.loads(candidate[first:last + 1])
    if not isinstance(data, list):
        return []
    parsed = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_options = item.get("options", {})
        options = {}
        if isinstance(raw_options, list):
            for option in raw_options:
                if isinstance(option, dict):
                    key = str(option.get("value", option.get("letter", ""))).strip().upper()
                    label = str(option.get("label", option.get("text", ""))).strip()
                    if key in {"A", "B", "C", "D"}:
                        options[key] = label
        elif isinstance(raw_options, dict):
            options = {str(k).strip().upper()[-1:]: str(v).strip() for k, v in raw_options.items()}
        correct = str(item.get("correct", item.get("answer", ""))).strip().upper()
        answer_match = re.match(r"\[?([A-D])\]?\b", correct)
        if answer_match:
            correct = answer_match.group(1)
        question_text = str(item.get("text", item.get("question", ""))).strip()
        if not (question_text and all(options.get(letter) for letter in "ABCD")
                and correct in {"A", "B", "C", "D"}):
            continue
        difficulty = str(item.get("difficulty", "Moderate")).title()
        if difficulty not in {"Easy", "Moderate", "Difficult"}:
            difficulty = "Moderate"
        parsed.append({"text": question_text, "options": {k: options[k] for k in "ABCD"},
                       "correct": correct, "explanation": str(item.get("explanation", "")).strip(),
                       "difficulty": difficulty})
    return parsed


async def import_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    try:
        args, timer = parse_timer_args(context.args)
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    if len(args) < 1:
        await update.effective_message.reply_text(
            "Usage: /importquiz Current Affairs [--time 5]\n"
            "Upload a TXT/PDF or paste questions in any readable MCQ layout. "
            "The bot will analyze and normalize the source before importing."
        )
        return
    category_name = " ".join(args).strip()
    with SessionLocal() as db:
        category = db.query(Category).filter(Category.name.ilike(category_name)).first()
    if not category:
        await update.effective_message.reply_text(
            f"Category '{category_name}' not found. Create it first with /addcategory {category_name}."
        )
              )
        return
    context.user_data["pending_bulk_import"] = {
        "category": category.name,
        "timer": timer,
    }
    await update.effective_message.reply_text(
        f"📥 Ready to import into {category.name}.\n"
        f"Per-question timer: {timer}s.\n"
        "Now upload a .txt/.pdf file or paste the full question set. "
        "After import, the quiz will wait for owner approval before any polls are sent."
    )


async def process_bulk_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    pending = context.user_data.get("pending_bulk_import")
    if not pending:
        return False
    if not await owner_required(update):
        context.user_data.pop("pending_bulk_import", None)
        return True
    # AI interprets varied layouts; compare with local parsing so a partial AI extraction
    # does not silently discard questions that the deterministic parser can recognize.
    parsed = []
    ai_error = None
    try:
        parsed = await normalize_bulk_questions_with_ai(raw_text)
    except Exception as exc:
        ai_error = exc
        logger.warning("AI bulk-question normalization failed; trying local parser: %s", exc)
    local_parsed = parse_bulk_question_blocks(raw_text)
    if len(local_parsed) > len(parsed):
        parsed = local_parsed
    if len(parsed) > 200:
        await update.effective_message.reply_text(
            "❌ Maximum 200 questions per imported quiz. Split the file into smaller sets."
        )
        return True
    if not parsed:
        await update.effective_message.reply_text(
            "❌ I couldn't reliably identify complete MCQs with four options and a correct answer.\n"
            "The source can use numbered questions, bullets, [A]/(A)/A) options, and labels like "
            "'Correct Answer: [C] NASA'. If no answer key is present, add one before importing.\n"
            + (f"AI normalization was unavailable: {str(ai_error)[:180]}" if ai_error else "Try a text-based PDF or paste the question set again.")
        )
        return True
    saved = 0
    saved_ids = []
    with SessionLocal() as db:
        category = db.query(Category).filter(Category.name.ilike(pending["category"])).first()
        if not category:
            await update.effective_message.reply_text("❌ Category no longer exists.")
            context.user_data.pop("pending_bulk_import", None)
            return True
        for item in parsed:
            question = Question(
                category_id=category.id,
                question_text=item["text"],
                option_a=item["options"]["A"],
                option_b=item["options"]["B"],
                option_c=item["options"]["C"],
                option_d=item["options"]["D"],
                correct_option=item["correct"],
                explanation=item["explanation"],
                difficulty=item["difficulty"],
                marks=1.0,
                negative_marks=0.25,
                active=True,
            )
            db.add(question)
            db.flush()
            saved_ids.append(question.id)
            saved += 1
        db.commit()
    category_name = pending["category"]
    timer = int(pending["timer"])
    context.user_data.pop("pending_bulk_import", None)
    await update.effective_message.reply_text(
        f"✅ Imported {saved} question(s) into {category_name}. Preparing quiz for owner approval."
    )
    # Imported content is previewed in the private bot chat; it is not published until
    # an approved group target is explicitly selected and the owner approves it.
    await publish_quiz_to_chat(
        update, context, 0, category_name, saved, timer,
        question_ids=saved_ids,
    )
    return True


async def bulk_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("pending_bulk_import"):
        message = update.effective_message
        if message and message.text and not message.text.startswith("/"):
            await process_bulk_question_text(update, context, message.text)
        return
    # Preserve the existing /addquestion two-message workflow.
    await pending_question_fields(update, context)


async def bulk_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("pending_bulk_import"):
        return
    if not await owner_required(update):
        context.user_data.pop("pending_bulk_import", None)
        return
    message = update.effective_message
    document = message.document if message else None
    if not document:
        return
    filename = (document.file_name or "").lower()
    if not (filename.endswith(".txt") or filename.endswith(".pdf")):
        await message.reply_text("❌ Upload a .txt or .pdf file.")
        return
    try:
        tg_file = await context.bot.get_file(document.file_id)
        data = await tg_file.download_as_bytearray()
        if filename.endswith(".txt"):
            raw_text = bytes(data).decode("utf-8-sig", errors="replace")
        else:
            try:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(bytes(data)))
                raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                await message.reply_text("❌ PDF import needs the 'pypdf' package. Install it, or upload TXT.")
                return
        await process_bulk_question_text(update, context, raw_text)
    except Exception as exc:
        logger.exception("Bulk question file import failed")
        await message.reply_text(f"❌ Could not read that file: {str(exc)[:300]}")

async def run_sequential_quiz(context: ContextTypes.DEFAULT_TYPE, session_key: str):
    """Send one poll at a time; persisted timer/index allow restart recovery."""
    session = get_sessions(context).get(str(session_key))
    if not session or session.get("finalized") or session.get("awaiting_approval"):
        return
    running = context.application.bot_data.setdefault("sequential_running", set())
    if str(session_key) in running:
        return
    running.add(str(session_key))
    try:
        target_chat_id = int(session["group_id"])
        display = display_name_for(target_chat_id)
        timer = int(session.get("time_limit_seconds", 30))
        questions = session.get("questions", [])
        while not session.get("finalized"):
            index = int(session.get("current_index", 0))
            end_at = session.get("current_poll_end_at")
            if end_at:
                remaining = max(0, float(end_at) - time.time())
                if remaining:
                    await asyncio.sleep(remaining)
                session["current_poll_end_at"] = None
                persist_live_session(session)
                continue

            if index >= len(questions):
                await auto_finalize_quiz(context, str(session_key))
                return

            item = questions[index]
            choices = list(item["options"].items())
            random.shuffle(choices)
            option_texts = [str(value)[:100] for _, value in choices]
            correct_index = next(i for i, (letter, _) in enumerate(choices)
                                 if letter == item["correct"])
            poll_question = f"{display} | Q{index + 1}/{len(questions)}: {item['text']}"[:300]
            try:
                poll_message = await context.bot.send_poll(
                    chat_id=target_chat_id,
                    question=poll_question,
                    options=option_texts,
                    type="quiz",
                    correct_option_id=correct_index,
                    is_anonymous=False,
                    open_period=timer,
                    explanation=(item.get("explanation", "")[:200] or None),
                )
            except TelegramError:
                logger.exception("Could not send sequential quiz poll %s/%s", index + 1, len(questions))
                session["publish_failed"] = int(session.get("publish_failed", 0)) + 1
                try:
                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        text=f"⚠️ Could not publish question {index + 1}. Quiz paused; contact the owner.",
                    )
                except TelegramError:
                    pass
                return

            poll_id = poll_message.poll.id
            metadata = {
                "session_key": str(session_key),
                "question_id": item.get("id"),
                "correct_index": correct_index,
                "marks": float(item.get("marks", 1.0)),
                "negative": float(item.get("negative", 0.25)),
            }
            get_poll_index(context)[poll_id] = metadata
                      session.setdefault("poll_ids", []).append(poll_id)
            session.setdefault("poll_metadata", {})[str(poll_id)] = {
                k: v for k, v in metadata.items() if k != "session_key"
            }
            session["current_index"] = index + 1
            session["current_poll_end_at"] = time.time() + timer
            persist_live_session(session)
    finally:
        running.discard(str(session_key))


async def auto_finalize_quiz(context: ContextTypes.DEFAULT_TYPE, session_key: str):
    """Finalize completed sequential quiz and send leaderboard to the quiz chat."""
    session = get_sessions(context).get(str(session_key))
    if not session or session.get("finalized"):
        return
    target_chat_id = int(session["group_id"])

    async def send_to_target(text: str, **kwargs):
        return await context.bot.send_message(chat_id=target_chat_id, text=text, **kwargs)

    fake_update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_text=send_to_target),
        effective_user=None,
        effective_chat=SimpleNamespace(id=target_chat_id),
    )
    await finalize_quiz(fake_update, context, auto_key=str(session_key))


def parse_timer_args(args: list[str]):
    """Recognize an optional trailing --time SECONDS; default is 30 seconds."""
    values = list(args)
    timer = 30
    if len(values) >= 2 and values[-2].lower() == "--time":
        try:
            timer = int(values[-1])
        except ValueError:
            raise ValueError("Timer must be an integer from 5 to 600 seconds.")
        values = values[:-2]
    if not 5 <= timer <= 600:
        raise ValueError("Timer must be from 5 to 600 seconds.")
    return values, timer


async def publish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    try:
        args, timer = parse_timer_args(context.args)
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: /publishquiz Quant 2 [--time 15]\n"
            "Timer applies to each question (5–600 seconds; default 30)."
        )
        return
    try:
        count = int(args[-1])
    except ValueError:
        await update.effective_message.reply_text("Question count must be numeric.")
        return
    if not 1 <= count <= 200:
        await update.effective_message.reply_text("Question count must be from 1 to 200.")
        return
    category_name = " ".join(args[:-1]).strip()
    # Private bot chat is a control/preview surface only. Actual publication is group-only.
    await publish_quiz_to_chat(
        update, context, 0, category_name, count, timer
    )


async def publish_quiz_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    try:
        args, timer = parse_timer_args(context.args)
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Usage: /publishquiz_to GROUP_ID COUNT CATEGORY [--time 15]"
        )
        return
    try:
        group_id = int(args[0])
        count = int(args[1])
    except ValueError:
        await update.effective_message.reply_text("GROUP_ID and COUNT must be numbers.")
        return
    if group_id >= 0:
        await update.effective_message.reply_text("Target must be a group/supergroup ID.")
        return
    if not 1 <= count <= 200:
        await update.effective_message.reply_text("Question count must be from 1 to 200.")
        return
    if not active_approved_group(group_id):
        await update.effective_message.reply_text("This group is not approved.")
        return
    category_name = " ".join(args[2:]).strip()
    await publish_quiz_to_chat(update, context, group_id, category_name, count, timer)


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not answer or not answer.user:
        return
    metadata = get_poll_index(context).get(answer.poll_id)
    if not metadata:
        return
    session = get_sessions(context).get(metadata["session_key"])
    if not session or session["finalized"]:
        return
    user = answer.user
    uid = str(user.id)
    participant = session["participants"].setdefault(
        uid,
        {
            "user_id": uid,
            "name": user.full_name or "Student",
            "answers": {},
            "last_answer_at": None,
        },
    )
    participant["name"] = user.full_name or participant.get("name") or "Student"
    participant["last_answer_at"] = time.time()
    context.application.bot_data.setdefault("participant_names", {})[uid] = participant["name"]
    selected = answer.option_ids[0] if answer.option_ids else None
    participant["answers"][answer.poll_id] = {
        "selected_index": selected,
        "correct_index": metadata["correct_index"],
        "marks": metadata["marks"],
        "negative": metadata["negative"],
    }
    persist_live_session(session)


async def set_quiz_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Assign a prepared quiz to an owner-approved group; private chat only."""
    if not await owner_required(update):
        return
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Usage: /setquiztarget QUIZ_ID GROUP_ID\n"
            "Example: /setquiztarget 9 -1001234567890\n"
            "Use /approved_groups to see approved destinations."
        )
        return
    key = str(int(context.args[0]))
    try:
        group_id = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("GROUP_ID must be a numeric Telegram group ID.")
        return
    if group_id >= 0:
        await update.effective_message.reply_text("Target must be a group/supergroup ID (normally starts with -100).")
        return
    approved = active_approved_group(group_id)
    if not approved:
        await update.effective_message.reply_text(
            "This group is not currently owner-approved. Approve it first with /approve_group GROUP_ID."
        )
        return
    session = get_sessions(context).get(key)
    if not session or session.get("finalized"):
        await update.effective_message.reply_text("Quiz session not found or already finalized. Check /activequizzes.")
        return
    if not session.get("awaiting_approval"):
        await update.effective_message.reply_text("Target cannot be changed after publishing has been approved or started.")
        return
    session["group_id"] = str(group_id)
    session["group_title"] = str(getattr(approved, "group_title", "") or group_id)
    persist_live_session(session)
    await update.effective_message.reply_text(
        f"Preview target set for quiz {key}.\n"
        f"Group: {session['group_title']}\n"
        f"Group ID: {group_id}\n"
        f"No polls sent yet. Review, then use /approvequiz {key}."
    )


async def approve_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner explicitly approves a prepared quiz before Telegram polls begin."""
    if not await owner_required(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /approvequiz QUIZ_ID")
        return
    key = str(int(context.args[0]))
    session = get_sessions(context).get(key)
    if not session:
        await update.effective_message.reply_text(
            "Quiz session not found. Check /activequizzes."
        )
        return
    if session.get("finalized"):
        await update.effective_message.reply_text("This quiz is already finalized.")
        return
          if not session.get("awaiting_approval"):
        await update.effective_message.reply_text(
            f"Quiz {key} has already been approved or started."
        )
        return
    target_chat_id = int(session["group_id"])
    if target_chat_id >= 0:
        await update.effective_message.reply_text(
            "Preview only: select an owner-approved group first with "
            f"/setquiztarget {key} GROUP_ID. Private bot chats never receive quiz polls."
        )
        return
    if not active_approved_group(target_chat_id):
        await update.effective_message.reply_text(
            "The target group is not currently owner-approved. Set an approved group with /setquiztarget."
        )
        return
    session["awaiting_approval"] = False
    session["started_at"] = time.time()
    persist_live_session(session)
    with SessionLocal() as db:
        quiz = db.query(Quiz).filter(Quiz.id == int(key)).first()
        if quiz:
            quiz.status = "published"
            db.commit()
    await update.effective_message.reply_text(
        f"Quiz {key} approved. Sequential publishing is starting now."
    )
    context.application.create_task(run_sequential_quiz(context, key))


async def active_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    sessions = get_sessions(context)
    lines = ["ACTIVE QUIZZES\n"]
    for key, session in sessions.items():
        if not session["finalized"]:
            if session.get("awaiting_approval"):
                lines.append(
                    f"ID: {key} | {session['category']} | PENDING APPROVAL\n"
                    f"Target: {session.get('group_title', session.get('group_id', 'unknown'))}\n"
                    f"/approvequiz {key}\n"
                )
            else:
                lines.append(
                    f"ID: {key} | {session['category']} | LIVE\n"
                    f"Group: {session['group_title']} | "
                    f"Participants: {len(session['participants'])}\n"
                    f"/finalizequiz {key}\n"
                )
    await update.effective_message.reply_text(
        "\n".join(lines) if len(lines) > 1 else "No active quiz sessions."
    )


async def finalize_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    auto_key: str | None = None,
):
    automatic = auto_key is not None
    if not automatic and not await owner_required(update):
        return
    if automatic:
        key = str(auto_key)
    else:
        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /finalizequiz QUIZ_ID")
            return
        key = context.args[0]
    session = get_sessions(context).get(key)
    if not session:
        await update.effective_message.reply_text(
            "Session not found. Live poll answers are held in memory, so a bot restart "
            "prevents finalizing that live session. Saved results remain available with /leaderboard QUIZ_ID."
        )
        return
    if session["finalized"]:
        await update.effective_message.reply_text("This quiz is already finalized.")
        return

    # Count only polls that were actually sent successfully.
    total_questions = len(session.get("poll_ids", []))
    if total_questions == 0:
        await update.effective_message.reply_text(
            "No quiz polls were published, so this quiz cannot be finalized."
        )
        return

    results = []
    for participant in session["participants"].values():
        correct = wrong = unanswered = 0
        score = 0.0
        for poll_id in session["poll_ids"]:
            answer = participant["answers"].get(poll_id)
            if not answer or answer["selected_index"] is None:
                unanswered += 1
            elif answer["selected_index"] == answer["correct_index"]:
                correct += 1
                score += answer["marks"]
            else:
                wrong += 1
                score -= answer["negative"]

        attempted = correct + wrong
        accuracy = correct / attempted * 100 if attempted else 0.0
        score_pct = score / total_questions * 100 if total_questions else 0.0
        results.append({
            **participant,
            "correct": correct,
            "wrong": wrong,
            "unanswered": max(unanswered, total_questions - attempted),
            "score": round(score, 2),
            "accuracy": round(accuracy, 2),
            "score_percent": round(score_pct, 2),
        })

    results.sort(
        key=lambda row: (row["score"], row["accuracy"], row["correct"]),
        reverse=True,
    )
    n = len(results)
    start_epoch = float(session.get("started_at", time.time()))
    started_at = datetime.utcfromtimestamp(start_epoch)
    now = datetime.utcnow()
    group_id = str(session.get("group_id", ""))
    group_title = str(session.get("group_title", ""))

    # Persist the complete attempt record so names and analytics survive restarts.
    with SessionLocal() as db:
        quiz = db.query(Quiz).filter(Quiz.id == int(session["quiz_id"])).first()
        if not quiz:
            await update.effective_message.reply_text(
                "The quiz record was not found in the database; results were not saved."
            )
            return
        # Persist the actual number of polls sent, including any send failures.
        quiz.question_count = total_questions

        for index, result in enumerate(results):
            lower_count = sum(other["score"] < result["score"] for other in results)
            tied_count = sum(other["score"] == result["score"] for other in results)
            percentile = 100.0 if n <= 1 else (
                (lower_count + 0.5 * max(0, tied_count - 1)) / (n - 1) * 100
            )
            duration = 0
            if result.get("last_answer_at") is not None:
                duration = max(0, int(result["last_answer_at"] - start_epoch))
            feedback = fallback_feedback(
                result["correct"], result["wrong"], result["unanswered"],
                result["accuracy"], result["score"],
            )
            db.add(StudentAttempt(
                quiz_id=int(session["quiz_id"]),
                student_telegram_id=str(result["user_id"]),
                student_name=str(result.get("name") or "Student")[:200],
                group_id=group_id[:50],
                group_title=group_title[:200],
                score=float(result["score"]),
                correct_count=int(result["correct"]),
                wrong_count=int(result["wrong"]),
                unanswered_count=int(result["unanswered"]),
                started_at=started_at,
                submitted_at=now,
                duration_seconds=duration,
                accuracy_percent=float(result["accuracy"]),
                score_percent=float(result["score_percent"]),
                group_percentile=float(percentile),
                overall_percentile=0.0,
                performance_tagline=feedback["tagline"],
                performance_insight=feedback["insight"],
                motivational_quote=feedback["quote"],
                leaderboard_published=True,
            ))
        db.commit()

        # Overall percentile compares normalized score percentages across all saved quiz attempts.
        all_attempts = db.query(StudentAttempt).all()
        for current in all_attempts:
            current_pct = float(current.score_percent or 0.0)
            lower = sum(float(other.score_percent or 0.0) < current_pct for other in all_attempts)
            tied = sum(float(other.score_percent or 0.0) == current_pct for other in all_attempts)
            current.overall_percentile = (
                100.0 if len(all_attempts) <= 1 else
                (lower + 0.5 * max(0, tied - 1)) / (len(all_attempts) - 1) * 100
            )
        db.commit()

    session["finalized"] = True
    persist_live_session(session)
    await update.effective_message.reply_text(
        f"Quiz {key} finalized.\n"
        f"Participants recorded: {n}\n"
        "Results and student display names saved to the database.\n"
        "Publishing leaderboard..."
    )
    await show_leaderboard(
        update, context, int(session["quiz_id"]), session["group_id"]
    )
 async def show_leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: int,
    group_id: str | None = None,
):
    with SessionLocal() as db:
        attempts = (
            db.query(StudentAttempt)
            .filter(StudentAttempt.quiz_id == quiz_id)
            .order_by(
                StudentAttempt.score.desc(),
                StudentAttempt.correct_count.desc(),
                StudentAttempt.submitted_at.asc(),
            )
            .all()
        )
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not attempts:
            await update.effective_message.reply_text("No saved results for this quiz.")
            return

        title = quiz.title if quiz else f"Quiz {quiz_id}"
        total_questions = int(quiz.question_count or 0) if quiz else 0
        if not total_questions:
            total_questions = max(
                (int(a.correct_count or 0) + int(a.wrong_count or 0) + int(a.unanswered_count or 0)
                 for a in attempts), default=0
            )
        rows = [{
            "attempt": a,
            "score": float(a.score or 0),
            "correct": int(a.correct_count or 0),
            "wrong": int(a.wrong_count or 0),
            "unanswered": int(a.unanswered_count or 0),
            "name": str(a.student_name or "Student"),
            "group_id": str(a.group_id or ""),
            "group_title": str(a.group_title or ""),
            "duration_seconds": int(a.duration_seconds or 0),
            "accuracy": float(a.accuracy_percent or 0),
            "score_percent": float(a.score_percent or 0),
            "group_percentile": a.group_percentile,
            "overall_percentile": a.overall_percentile,
        } for a in attempts]

    # Use session data when available; persisted attempt metadata is the fallback.
    session = get_sessions(context).get(str(quiz_id))
    first_row = rows[0]
    target_id = str(
        (session or {}).get("group_id") or first_row["group_id"] or group_id or "not stored"
    )
    target_title = str(
        (session or {}).get("group_title") or first_row["group_title"] or target_id
    )
    n = len(rows)
    lines = [
        "🏆 0xGREY — FINAL LEADERBOARD",
        "━━━━━━━━━━━━━━━━━━━━",
        "PH | 0xGREY // Intelligence",
        "",
        f"📚 Quiz: {title}",
        f"🆔 Quiz ID: {quiz_id}",
        f"🎯 Target: {target_title}",
        f"🆔 Group/Chat ID: {target_id}",
        f"📝 Total Questions: {total_questions}",
        "",
        "📊 MARKING SCHEME",
        "✅ Correct: +1  |  ❌ Wrong: −0.25  |  ➖ Unanswered: 0",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 Total Participants: {n}",
        "",
    ]

    for index, row in enumerate(rows):
        attempt = row["attempt"]
        rank = index + 1
        lower_count = sum(other["score"] < row["score"] for other in rows)
        tied_count = sum(other["score"] == row["score"] for other in rows)
        percentile = (
            100.0 if n == 1 else
            (lower_count + 0.5 * max(0, tied_count - 1)) / (n - 1) * 100
        )
        attempted = row["correct"] + row["wrong"]
        accuracy = row["correct"] / attempted * 100 if attempted else 0.0
        score_pct = row["score"] / total_questions * 100 if total_questions else 0.0
        name = row["name"] or f"Telegram user {attempt.student_telegram_id}"
        duration = row["duration_seconds"]
        time_used = f"{duration // 60}m {duration % 60}s" if duration > 0 else "Not available"
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "🏅"
        lines.extend([
            f"{medal} RANK #{rank} — {name}",
            f"🎯 Net Score: {row['score']:.2f} / {total_questions}",
            f"📈 Score Percentage: {score_pct:.1f}%",
            f"👥 Group Percentile: {float(row.get('group_percentile') or percentile):.1f}%",
            f"🌐 Overall Percentile: {float(row.get('overall_percentile') or 0.0):.1f}%",
            f"✅ Correct: {row['correct']}  |  ❌ Wrong: {row['wrong']}",
            f"📝 Attempted: {attempted} / {total_questions}",
            f"➖ Unanswered: {row['unanswered']}",
            f"🎯 Accuracy: {accuracy:.1f}%",
            f"⏱️ Time Used: {time_used}",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ])

    quiz_date = title.split("|", 1)[-1].strip() if "|" in title else datetime.utcnow().strftime("%Y-%m-%d")
    lines.extend([
        f"📅 Quiz Date: {quiz_date}",
        "✍️ Created by PH",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
    output = "\n".join(lines)
    # Split into Telegram-safe messages without cutting individual lines.
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 3500 and current:
            chunks.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    for chunk in chunks:
        await update.effective_message.reply_text(chunk)


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /leaderboard QUIZ_ID")
        return
    try:
        quiz_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Quiz ID must be numeric.")
        return
    await show_leaderboard(update, context, quiz_id)


# ---------------------------------------------------------------------
# Error handler and application
# ---------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled Telegram update error: %r", context.error)



async def resume_sequential_sessions(app: Application):
    """Resume active sequential quizzes restored from the database."""
    for key, session in list(app.bot_data.get("quiz_sessions", {}).items()):
        if (session.get("sequential") and not session.get("finalized")
                and not session.get("awaiting_approval")):
            app.create_task(run_sequential_quiz(app, str(key)))


# ---------------------------------------------------------------------
# Saved quiz-link library (persistent, owner-managed)
# ---------------------------------------------------------------------

LINK_MANAGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_quiz_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    url TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    quiz_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def init_saved_links_table():
    with SessionLocal() as db:
        db.execute(text(LINK_MANAGER_SCHEMA))
        db.commit()


def hash_link_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 250_000).hex()
    return salt, digest


def link_password_matches(password: str, salt: str, expected_hash: str) -> bool:
    _, actual = hash_link_password(password, salt)
    return secrets.compare_digest(actual, expected_hash)


def get_saved_link(link_id: int):
    with SessionLocal() as db:
        row = db.execute(text("SELECT * FROM saved_quiz_links WHERE id=:id"), {"id": link_id}).mappings().first()
        return dict(row) if row else None
def link_keyboard(link_id: int, enabled: bool):
    status_text = "Disable access" if enabled else "Enable access"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Rename file", callback_data=f"sl:rename:{link_id}"),
         InlineKeyboardButton("🔗 Edit link", callback_data=f"sl:url:{link_id}")],
        [InlineKeyboardButton("🔐 Change password", callback_data=f"sl:pass:{link_id}"),
         InlineKeyboardButton(status_text, callback_data=f"sl:toggle:{link_id}")],
        [InlineKeyboardButton("🌐 Open saved link", callback_data=f"sl:open:{link_id}"),
         InlineKeyboardButton("🗑 Delete", callback_data=f"sl:delete:{link_id}")],
        [InlineKeyboardButton("⬅️ All saved links", callback_data="sl:list")],
    ])


async def save_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    context.user_data["saved_link_flow"] = {"step": "details"}
    await update.effective_message.reply_text(
        "📚 Save a password-protected Telegram quiz. Send one line in this format:\n"
        "File name | TELEGRAM | password | Quiz ID\n\n"
        "Example: October CA Set 1 | TELEGRAM | StrongPass2026! | 9\n"
        "Use the Quiz ID shown by the bot. No website is needed for Telegram quizzes.\n"
        "The bot stores only a salted password hash."
    )


async def saved_links_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    await show_saved_links(update.effective_message, context)


async def show_saved_links(message, context):
    with SessionLocal() as db:
        rows = db.execute(text("SELECT id, file_name, enabled FROM saved_quiz_links ORDER BY id DESC LIMIT 50")).mappings().all()
    if not rows:
        await message.reply_text("📚 No saved quiz links yet. Use /savelink to store one.")
        return
    buttons = []
    for row in rows:
        status = "🟢" if row["enabled"] else "🔴"
        buttons.append([InlineKeyboardButton(f"{status} {row['file_name']} (#{row['id']})", callback_data=f"sl:view:{row['id']}")])
    buttons.append([InlineKeyboardButton("➕ Save a link", callback_data="sl:new")])
    await message.reply_text("📚 Saved quiz links — choose one to manage:", reply_markup=InlineKeyboardMarkup(buttons))


async def saved_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data.startswith("sl:memberclose:"):
        if update.effective_chat and update.effective_chat.type == "private":
            try:
                await query.message.edit_text("Quiz link access panel closed.")
            except TelegramError:
                pass
            return
        await query.answer("Open the bot privately to manage quiz access.", show_alert=True)
        return
    if not is_owner(update) or (update.effective_chat and update.effective_chat.type != "private"):
        await query.answer("Owner-only control in private chat.", show_alert=True)
        return
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if data == "sl:list":
        await show_saved_links(query.message, context)
        return
    if data == "sl:new":
        context.user_data["saved_link_flow"] = {"step": "details"}
        await query.message.reply_text("Send: File name | TELEGRAM | password | Quiz ID (no external website needed)")
        return
    if len(parts) < 3:
        return
    try:
        link_id = int(parts[2])
    except ValueError:
        return
    record = get_saved_link(link_id)
    if not record:
        await query.message.reply_text("That saved link no longer exists.")
        return
    if action == "view":
        status = "Enabled" if record["enabled"] else "Disabled"
        await query.message.reply_text(
            f"📚 {record['file_name']}\nID: {link_id}\nAccess: {status}\n"
            f"Quiz ID: {record['quiz_id'] or 'Not linked'}\nURL: {record['url']}\n\n"
            "Password is never displayed. Use the buttons to edit settings.",
            reply_markup=link_keyboard(link_id, bool(record["enabled"]))
        )
    elif action in {"rename", "url", "pass"}:
        step = {"rename": "rename", "url": "url", "pass": "password"}[action]
        context.user_data["saved_link_flow"] = {"step": step, "link_id": link_id}
        prompt = {"rename": "Send the new file name.", "url": "Send the new quiz URL (must start with https://).", "pass": "Send the new password (minimum 8 characters)."}[action]
        await query.message.reply_text(prompt)
    elif action == "toggle":
        new_value = 0 if record["enabled"] else 1
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET enabled=:enabled, updated_at=:now WHERE id=:id"), {"enabled": new_value, "now": datetime.utcnow().isoformat(), "id": link_id})
            db.commit()
        updated = get_saved_link(link_id)
        await query.message.reply_text(f"Access {'enabled' if new_value else 'disabled'} for {updated['file_name']}.", reply_markup=link_keyboard(link_id, bool(new_value)))
    elif action == "open":
        if not record["enabled"]:
            await query.message.reply_text("🔒 This link is disabled. Enable it before opening.")
        else:
            await query.message.reply_text(f"🔗 {record['file_name']}\n{record['url']}")
    elif action == "delete":
        context.user_data["saved_link_flow"] = {"step": "delete_confirm", "link_id": link_id}
        await query.message.reply_text(f"Delete saved link #{link_id} ({record['file_name']})? Send YES to confirm or NO to cancel.")


async def saved_link_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("saved_link_flow")
    if not flow or not update.effective_message or not update.effective_message.text:
        return
    if not await owner_required(update):
        context.user_data.pop("saved_link_flow", None)
        return
    raw = update.effective_message.text.strip()
    step = flow.get("step")
    if step == "details":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) not in (3, 4):
            await update.effective_message.reply_text("Format: File name | TELEGRAM | password | Quiz ID")
            return
        file_name, url, password = parts[:3]
        quiz_id = parts[3] if len(parts) == 4 and parts[3] else None
        telegram_mode = url.upper() in {"TELEGRAM", "-"}
        if (not file_name or len(password) < 8
                or (telegram_mode and not quiz_id)
                or (not telegram_mode and not url.startswith("https://"))):
            await update.effective_message.reply_text(
                "Use a file name, either TELEGRAM plus a Quiz ID or a valid https:// URL, "
                "and a password of at least 8 characters."
            )
            return
        if telegram_mode:
            url = "telegram://quiz"
        salt, digest = hash_link_password(password)
        now = datetime.utcnow().isoformat()
        with SessionLocal() as db:
            db.execute(text("INSERT INTO saved_quiz_links(file_name,url,password_hash,password_salt,enabled,quiz_id,created_at,updated_at) VALUES(:name,:url,:hash,:salt,1,:quiz,:created,:updated)"), {"name": file_name[:150], "url": url, "hash": digest, "salt": salt, "quiz": quiz_id, "created": now, "updated": now})
            db.commit()
            new_id = db.execute(text("SELECT MAX(id) FROM saved_quiz_links")).scalar()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text(f"Saved '{file_name}' as link #{new_id}. You can manage its name, URL, password and access with /links.")
        await show_saved_links(update.effective_message, context)
    elif step == "rename":
        if not raw or len(raw) > 150:
            await update.effective_message.reply_text("File name must be between 1 and 150 characters.")
            return
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET file_name=:name, updated_at=:now WHERE id=:id"), {"name": raw, "now": datetime.utcnow().isoformat(), "id": flow["link_id"]})
            db.commit()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text("File name updated.")
        record = get_saved_link(flow["link_id"])
        await update.effective_message.reply_text(f"{record['file_name']}", reply_markup=link_keyboard(flow["link_id"], bool(record["enabled"])))
    elif step == "url":
        if not raw.startswith("https://"):
            await update.effective_message.reply_text("For security, the URL must start with https://")
            return
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET url=:url, updated_at=:now WHERE id=:id"), {"url": raw, "now": datetime.utcnow().isoformat(), "id": flow["link_id"]})
            db.commit()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text("URL updated.")
    elif step == "password":
        if len(raw) < 8:
            await update.effective_message.reply_text("Password must be at least 8 characters.")
            return
        salt, digest = hash_link_password(raw)
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET password_hash=:hash, password_salt=:salt, updated_at=:now WHERE id=:id"), {"hash": digest, "salt": salt, "now": datetime.utcnow().isoformat(), "id": flow["link_id"]})
            db.commit()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text("Password changed. The bot does not display or store it in plain text.")
    elif step == "delete_confirm":
        if raw.upper() == "YES":
            with SessionLocal() as db:
                db.execute(text("DELETE FROM saved_quiz_links WHERE id=:id"), {"id": flow["link_id"]})
                db.commit()
            await update.effective_message.reply_text("Saved link deleted from the bot's library.")
        else:
            await update.effective_message.reply_text("Deletion cancelled.")
        context.user_data.pop("saved_link_flow", None)


async def member_quizlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Members must request the link in private chat; only the bot reveals it after password verification.
    if not update.effective_chat or update.effective_chat.type != "private":
        await update.effective_message.reply_text("For privacy, open the bot privately and send /quizlink LINK_ID.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /quizlink LINK_ID")
        return
         await query.answer()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN or BOT_TOKEN.")
    if not OWNER_ID.isdigit():
        raise RuntimeError("Set TELEGRAM_OWNER_ID to your numeric Telegram user ID.")
    if AI_PROVIDER not in {"gemini", "gemini35", "gemini38", "groq"}:
        raise RuntimeError("AI_PROVIDER must be gemini35, gemini38, or groq.")
    init_db()
    init_saved_links_table()

    app = Application.builder().token(BOT_TOKEN).post_init(resume_sequential_sessions).build()
    restore_live_sessions(app)

    handlers = [
        ("start", start),
        ("help", help_command),
        ("status", status),
        ("aistatus", ai_status),
        ("aifeedback", ai_feedback_command),
        ("set_chat_name", set_chat_name),
        ("get_chat_name", get_chat_name),
        ("reset_chat_name", reset_chat_name),
        ("request_access", request_access),
        ("pending_groups", pending_groups),
        ("approve_group", approve_group),
        ("revoke_group", revoke_group),
        ("approved_groups", approved_groups),
        ("addcategory", add_category),
        ("categories", list_categories),
        ("addquestion", add_question),
        ("questioncount", question_count),
        ("testpoll", test_poll),
        ("publishquiz", publish_quiz),
        ("importquiz", import_quiz_command),
        ("setquiztarget", set_quiz_target),
        ("approvequiz", approve_quiz),
        ("publishquiz_to", publish_quiz_to),
        ("activequizzes", active_quizzes),
        ("finalizequiz", finalize_quiz),
        ("leaderboard", leaderboard),
        ("savelink", save_link_command),
        ("links", saved_links_command),
        ("quizlink", member_quizlink_command),
    ]
    for command, callback in handlers:
        app.add_handler(CommandHandler(command, callback))
    # Process link/password manager state before the generic bulk-import text handler.
    app.add_handler(CallbackQueryHandler(private_quiz_callback, pattern=r"^pq:"))
    app.add_handler(CallbackQueryHandler(saved_link_callback, pattern=r"^sl:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, saved_link_text_handler), group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quizlink_password_handler), group=-2)
    # Process the second message in the /addquestion CATEGORY workflow.
    app.add_handler(MessageHandler(filters.Document.ALL, bulk_document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_text_handler))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_error_handler(error_handler)

    logger.info(
        "Starting 0xGREY. AI provider=%s; model=%s",
        AI_PROVIDER,
        GEMINI_MODEL if AI_PROVIDER.startswith("gemini") or AI_PROVIDER == "gemini"
        else GROQ_MODEL,
    )
    app.run_polling()


if __name__ == "__main__":
    main() 
