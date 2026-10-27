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
from difflib import SequenceMatcher
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import String, inspect, text
from sqlalchemy.orm import mapped_column

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

# The repository's current database.py may predate cohort percentiles. Add the
# mapped field dynamically so this bot remains compatible, then migrate existing
# databases without deleting any saved attempts.
if not hasattr(StudentAttempt, "quiz_family_id"):
    setattr(
        StudentAttempt,
        "quiz_family_id",
        mapped_column(String(64), nullable=True, index=True),
    )


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("0xGREY")


def shuffled_option_entries(options, correct_letter):
    """Return shuffled (original_letter, text) entries and the correct display index.

    Correctness is always resolved against the original option label before display
    order changes. Raises ValueError for malformed question data rather than silently
    marking an answer incorrectly.
    """
    if not isinstance(options, dict):
        raise ValueError("options must be a mapping")
    normalized = {str(k).upper(): str(v).strip() for k, v in options.items()}
    if any(letter not in normalized or not normalized[letter] for letter in "ABCD"):
        raise ValueError("question must have four non-empty options A-D")
    if len({normalized[letter].casefold() for letter in "ABCD"}) != 4:
        raise ValueError("question options must be distinct")
    correct = str(correct_letter or "").strip().upper()
    if correct not in "ABCD" or len(correct) != 1:
        raise ValueError("correct answer must be A, B, C, or D")
    entries = [(letter, normalized[letter]) for letter in "ABCD"]
    random.shuffle(entries)
    correct_index = next(i for i, (letter, _) in enumerate(entries) if letter == correct)
    return entries, correct_index

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


def ensure_quiz_family_column() -> None:
    """Add the cohort fingerprint column when using an older database.py schema."""
    with SessionLocal() as db:
        bind = db.get_bind()
        inspector = inspect(bind)
        if "student_attempts" not in inspector.get_table_names():
            raise RuntimeError(
                "student_attempts table is missing; database.init_db() did not create it."
            )
        columns = {column["name"] for column in inspector.get_columns("student_attempts")}
        if "quiz_family_id" not in columns:
            db.execute(text("ALTER TABLE student_attempts ADD COLUMN quiz_family_id VARCHAR(64) NULL"))
            db.commit()


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


def quiz_family_fingerprint(questions: list[dict]) -> str:
    """Stable identity for the same mock content across separate group publications.

    Question order and option letters are ignored; option text and the correct-answer
    meaning are retained so shuffled copies of the same mock share one cohort.
    """
    canonical_questions = []
    for item in questions or []:
        options = item.get("options") or {}
        correct_key = str(item.get("correct", "")).strip().upper()
        canonical_options = []
        for key, value in options.items():
            normalized_value = " ".join(str(value or "").casefold().split())
            canonical_options.append((normalized_value, str(key).strip().upper() == correct_key))
        canonical_options.sort()
        canonical_questions.append({
            "text": " ".join(str(item.get("text", "")).casefold().split()),
            "options": canonical_options,
            "marks": round(float(item.get("marks", 1.0) or 1.0), 4),
            "negative": round(float(item.get("negative", 0.25) or 0.0), 4),
        })
    canonical_questions.sort(key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
    payload = json.dumps(canonical_questions, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def percentile_for_score(score: float, population: list[StudentAttempt]) -> float | None:
    """Midrank percentile; returns None when the comparison pool is empty."""
    if not population:
        return None
    if len(population) == 1:
        return 100.0
    lower = sum(float(other.score or 0.0) < score for other in population)
    tied = sum(float(other.score or 0.0) == score for other in population)
    return round((lower + 0.5 * max(0, tied - 1)) / (len(population) - 1) * 100, 2)

def recalculate_family_percentiles(db, family_id: str) -> None:
    """Recompute overall cohort and per-group percentiles for one identical mock."""
    if not family_id:
        return
    cohort = db.query(StudentAttempt).filter(StudentAttempt.quiz_family_id == family_id).all()
    for attempt in cohort:
        attempt.overall_percentile = percentile_for_score(float(attempt.score or 0.0), cohort)
        if str(attempt.group_id or "").strip():
            group_population = [
                other for other in cohort
                if str(other.group_id or "").strip() == str(attempt.group_id).strip()
            ]
            attempt.group_percentile = percentile_for_score(float(attempt.score or 0.0), group_population)
        else:
            # Private-chat attempts have no Telegram group comparison pool.
            attempt.group_percentile = None

def ensure_private_attempt_table() -> None:
    """Create durable storage for private-link quiz attempts without changing existing tables."""
    with SessionLocal() as db:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS private_quiz_attempts (
                attempt_token TEXT PRIMARY KEY,
                attempt_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        db.commit()


def persist_private_attempt(attempt_token: str, attempt: dict) -> None:
    """Persist a private quiz attempt after each meaningful state change."""
    ensure_private_attempt_table()
    payload = json.dumps(attempt, ensure_ascii=False, separators=(",", ":"))
    with SessionLocal() as db:
        db.execute(text("""
            INSERT INTO private_quiz_attempts (attempt_token, attempt_json, updated_at)
            VALUES (:token, :payload, :updated_at)
            ON CONFLICT(attempt_token) DO UPDATE SET
                attempt_json = excluded.attempt_json,
                updated_at = excluded.updated_at
        """), {
            "token": str(attempt_token),
            "payload": payload,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        })
        db.commit()


def restore_private_attempts(app: Application) -> None:
    """Restore saved private attempts; resume active questions after a restart."""
    ensure_private_attempt_table()
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT attempt_token, attempt_json FROM private_quiz_attempts"
        )).fetchall()
    attempts = app.bot_data.setdefault("private_quiz_attempts", {})
    for row in rows:
        try:
            attempt = json.loads(row[1])
            attempts[str(row[0])] = attempt
            if not attempt.get("finished") and int(attempt.get("index", 0)) < len(attempt.get("questions", [])):
                # Keep the saved option order so existing inline buttons remain valid.
                app.create_task(private_question_timeout(
                    SimpleNamespace(application=app, bot=app.bot),
                    str(row[0]), int(attempt.get("index", 0)),
                    max(5, min(600, int(attempt.get("timer", 30)))),
                ))
            elif not attempt.get("finished"):
                app.create_task(finish_private_quiz(
                    SimpleNamespace(application=app, bot=app.bot), str(row[0])
                ))
        except Exception:
            logger.exception("Could not restore private quiz attempt %s", row[0])


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
            if not session.get("quiz_family_id") and session.get("questions"):
                session["quiz_family_id"] = quiz_family_fingerprint(session["questions"])
                # Persist the cohort identity so future restarts and publications use it.
                persist_live_session(session)
            key = str(session.get("quiz_id", row[0]))
            family_id = str(session.get("quiz_family_id") or "")
            if family_id and str(key).isdigit():
                # Backfill older group results for this quiz where the new cohort field is empty.
                with SessionLocal() as db:
                    legacy_attempts = db.query(StudentAttempt).filter(
                        StudentAttempt.quiz_id == int(key),
                        StudentAttempt.quiz_family_id.is_(None),
                    ).all()
                    for legacy_attempt in legacy_attempts:
                        legacy_attempt.quiz_family_id = family_id
                    if legacy_attempts:
                        recalculate_family_percentiles(db, family_id)
                        db.commit()
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
        "/testpoll\n/publishquiz English 5 [--time 10]\n/importquiz English [--time 10] (then upload TXT/PDF/DOCX or paste questions)\n"
        "/publishquiz_to GROUP_ID COUNT CATEGORY\n"
        "/activequizzes\n/setquiztarget QUIZ_ID GROUP_ID\n/approvequiz QUIZ_ID (required before polls start)\n"
        "/finalizequiz QUIZ_ID\n/leaderboard QUIZ_ID\n/drafts (review saved import drafts)\n"
        "/quizlibrary (view and rename saved quizzes)\n/previewquiz QUIZ_ID (show full private question set)\n/savelink and /links (manage password-protected quiz links)\n\n"
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
            "Usage: /addquestion CATEGORY\n/editquestion QUESTION_ID\n"
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
        "quiz_family_id": quiz_family_fingerprint(questions),
        "participants": {},
        "poll_ids": [],
        "poll_metadata": {},
        "finalized": False,
        "awaiting_approval": True,
        "paused": False,
        "current_poll_message_id": None,
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
        line = re.sub(r"^\((?:Option\s*)?([A-D])\)\s*", r"\1: ", line, flags=re.I)
        line = re.sub(r"^(?:Option\s*)?([A-D])[.)]\s+", r"\1: ", line, flags=re.I)
        line = re.sub(r"^Option\s+([A-D])\s*[:\-]\s*", r"\1: ", line, flags=re.I)
        line = re.sub(r"^(?:Correct\s+Answer|Answer\s+Key|Correct\s+Option|Ans(?:wer)?)\s*[:\-]\s*", "Correct: ", line, flags=re.I)
        line = re.sub(r"^Q(?:uestion)?\s*\d+\s*[:.)\-]\s*", "Question: ", line, flags=re.I)
        line = re.sub(r"^Question\s+\d+\s*$", "Question: ", line, flags=re.I)
        # Also recognize bare numbered headings (e.g. "2. Question text") even
        # when another question in the same file already uses an explicit Q1 label.
        line = re.sub(r"^\d+\s*[.):\-]\s+(?=\S)", "Question: ", line)
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
            match = re.match(r"(?i)^(question|[a-d]|correct|explanation|difficulty|correct answer|answer key|ans|answer)\s*[:.)\-]\s*(.*)$", line)
            if match:
                key, value = match.group(1).casefold(), match.group(2).strip()
                if key in {"correct answer", "answer key", "ans", "answer"}:
                    key = "correct"
                if key == "correct":
                    answer_match = re.match(r"\[?([A-D])\]?(?:\s*[-.)].*)?$", value, re.I)
                    if answer_match and len(answer_match.group(1)) == 1:
                        value = answer_match.group(1).upper()
                fields[key] = value
                current_key = key
            elif current_key in {"question", "explanation"}:
                fields[current_key] = (fields.get(current_key, "") + " " + line).strip()
        options = {letter: fields.get(letter.lower(), "") for letter in "ABCD"}
        correct_raw = fields.get("correct", "").strip()
        correct = correct_raw.upper()
        correct_match = re.match(r"^\[?([A-D])\]?(?:\s*[-.)].*)?$", correct_raw, re.I)
        if correct_match:
            correct = correct_match.group(1).upper()
        else:
            # Some sources provide the answer text (e.g. "Ans: RBI") instead of a letter.
            matches = [letter for letter, option in options.items()
                       if option.strip().casefold() == correct_raw.casefold()]
            correct = matches[0] if len(matches) == 1 else ""
        if fields.get("question") and all(options.values()) and correct in {"A", "B", "C", "D"}:
            difficulty = fields.get("difficulty", "Moderate").title()
            if difficulty not in {"Easy", "Moderate", "Difficult"}:
                difficulty = "Moderate"
            parsed.append({"text": fields["question"], "options": options, "correct": correct,
                           "explanation": fields.get("explanation", ""), "difficulty": difficulty})
    return parsed


def audit_question_quality(questions: list[dict], near_duplicate_threshold: float = 0.80) -> tuple[list[dict], list[str]]:
    """Validate question structure, remove exact duplicates, and flag near-duplicates.

    This is a deterministic quality check, not proof that an answer is factually correct.
    Questions with ambiguous or unsupported facts must still be reviewed by the owner.
    """
    accepted: list[dict] = []
    issues: list[str] = []
    seen_exact: dict[str, int] = {}
    normalized_texts: list[tuple[str, int]] = []

    for original_index, item in enumerate(questions, 1):
        if not isinstance(item, dict):
            issues.append(f"Item {original_index}: malformed record; excluded.")
            continue
        question_text = re.sub(r"\s+", " ", str(item.get("text", "")).strip()).casefold()
        options_raw = item.get("options", {})
        if not question_text or not isinstance(options_raw, dict):
            issues.append(f"Item {original_index}: missing question text or options; excluded.")
            continue
        options = {letter: re.sub(r"\s+", " ", str(options_raw.get(letter, "")).strip()) for letter in "ABCD"}
        correct = str(item.get("correct", "")).strip().upper()
        if not all(options.values()) or correct not in {"A", "B", "C", "D"} or len(correct) != 1:
            issues.append(f"Item {original_index}: missing options or invalid answer key; excluded.")
            continue
        if len({value.casefold() for value in options.values()}) != 4:
            issues.append(f"Item {original_index}: duplicate options; excluded for review.")
            continue
        if question_text in seen_exact:
            issues.append(f"Item {original_index}: exact duplicate of accepted question {seen_exact[question_text]}; excluded.")
            continue

        near_match = None
        for prior_text, prior_index in normalized_texts:
            if SequenceMatcher(None, question_text, prior_text).ratio() >= near_duplicate_threshold:
                near_match = prior_index
                break
        if near_match is not None:
            issues.append(f"Item {original_index}: possible near-duplicate of question {near_match}; retained for owner review.")

        cleaned = dict(item)
        cleaned["text"] = str(item.get("text", "")).strip()
        cleaned["options"] = options
        cleaned["correct"] = correct
        cleaned["explanation"] = str(item.get("explanation", "")).strip()
        accepted.append(cleaned)
        seen_exact[question_text] = original_index
        normalized_texts.append((question_text, original_index))

    return accepted, issues


async def normalize_bulk_questions_with_ai(raw_text: str) -> list[dict]:
    # Use the configured AI model to normalize varied pasted/PDF MCQ layouts.
    if not raw_text.strip():
        return []
    prompt = '''You are an exam-question format normalizer. Convert the supplied source text into a JSON array of multiple-choice questions. The source may use any numbering, bullets, option labels, layout, answer-key wording, or PDF line wrapping.

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


async def import_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start owner-only generation from notes, editorials, or passages."""
    if not await owner_required(update):
        return
    args = list(context.args or [])
    count, timer = 20, 30
    cleaned = []
    i = 0
    try:
        while i < len(args):
            token = args[i].lower()
            if token in {"--count", "--time"}:
                if i + 1 >= len(args):
                    raise ValueError(f"{token} requires a number.")
                value = int(args[i + 1])
                if token == "--count":
                    count = value
                else:
                    timer = value
                i += 2
            else:
                cleaned.append(args[i])
                i += 1
    except ValueError:
        await update.effective_message.reply_text("Use /importnotes CATEGORY --count 20 --time 30 (count 1–200; timer 5–600 seconds).")
        return
    if not 1 <= count <= 200 or not 5 <= timer <= 600 or not cleaned:
        await update.effective_message.reply_text("Usage: /importnotes Banking Awareness --count 20 --time 30\nCount: 1–200; timer: 5–600 seconds.")
        return
    category_name = " ".join(cleaned).strip()
    with SessionLocal() as db:
        category = db.query(Category).filter(Category.name.ilike(category_name)).first()
    if not category:
        await update.effective_message.reply_text(f"Category '{category_name}' not found. Create it first with /addcategory {category_name}.")
        return
    context.user_data["pending_study_import"] = {"category": category.name, "timer": timer, "count": count}
    await update.effective_message.reply_text(
        f"📚 Study-notes import ready for {category.name}.\nRequested maximum: {count} questions; timer: {timer}s.\n"
        "Upload a TXT/PDF/DOCX or paste the notes, editorial, or passage. Questions must be supported by the supplied source."
    )


async def process_study_notes_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Generate source-grounded MCQs; keep passage text attached to each related question."""
    pending = context.user_data.get("pending_study_import")
    if not pending:
        return False
    if not await owner_required(update):
        context.user_data.pop("pending_study_import", None)
        return True
    if not raw_text or not raw_text.strip():
        await update.effective_message.reply_text("No readable study text was found. Please try another file or paste the text.")
        return True
    status = await update.effective_message.reply_text("🔎 Stage 1/3: Analysing the source and identifying supported concepts…")
    count = int(pending["count"])
    prompt = f"""Create exam-preparation MCQs using ONLY the source text below. Requested maximum: {count} questions.
Return ONLY a JSON array. Each item must contain: text, passage, options (A/B/C/D), correct (A-D), explanation, difficulty.
Rules:
- Generate only questions answerable from the source. Do not use outside facts or guess.
- Generate fewer questions if the source does not support the requested count.
- Every question must have four distinct plausible options and exactly one source-supported correct answer.
- If a question depends on a passage, put the exact relevant passage excerpt in the `passage` field. Keep passage-based questions tied to their passage; do not paraphrase or invent source text. For general notes questions, passage may be empty.
- Explanation must be grounded in the source and briefly justify the answer. If unsupported, omit the question.
- No duplicate questions. Difficulty must be Easy, Moderate, or Difficult.
- No Markdown fences or commentary.
JSON shape: [{{"text":"question","passage":"exact relevant excerpt or empty","options":{{"A":"...","B":"...","C":"...","D":"..."}},"correct":"B","explanation":"...","difficulty":"Moderate"}}]
SOURCE TEXT:\n{raw_text[:80000]}"""
    try:
        response = await ai_text(prompt)
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.I)
        first, last = candidate.find("["), candidate.rfind("]")
        if first < 0 or last < first:
            raise ValueError("AI response was not a JSON array")
        data = json.loads(candidate[first:last + 1])
        parsed = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            options_raw = item.get("options", {})
            if not isinstance(options_raw, dict):
                continue
            options = {str(k).strip().upper()[-1:]: str(v).strip() for k, v in options_raw.items()}
            correct_raw = str(item.get("correct", "")).strip().upper()
            match = re.match(r"^\[?([A-D])\]?\b", correct_raw)
            correct = match.group(1) if match else ""
            question_text = str(item.get("text", "")).strip()
            passage = str(item.get("passage", "")).strip()
            if passage:
                question_text = f"Read the following passage:\n{passage}\n\nQuestion: {question_text}"
            if not question_text or not all(options.get(k) for k in "ABCD") or correct not in "ABCD":
                continue
            if len({options[k].casefold() for k in "ABCD"}) != 4:
                continue
            difficulty = str(item.get("difficulty", "Moderate")).title()
            if difficulty not in {"Easy", "Moderate", "Difficult"}:
                difficulty = "Moderate"
            parsed.append({"text": question_text, "options": {k: options[k] for k in "ABCD"}, "correct": correct,
                           "explanation": str(item.get("explanation", "")).strip(), "difficulty": difficulty})
            if len(parsed) >= count:
                break
        parsed, quality_issues = audit_question_quality(parsed)
        if not parsed:
            raise ValueError("No complete, source-grounded questions passed validation")
    except Exception as exc:
        logger.exception("Study-notes question generation failed")
        try:
            await status.edit_text("❌ Could not create reliable questions from this source. No questions were saved.\n" + str(exc)[:250])
        except TelegramError:
            await update.effective_message.reply_text("❌ Generation failed; no questions were saved. Check AI configuration and retry.")
        return True
    try:
        draft_id = store_question_draft(str(update.effective_user.id), pending["category"], int(pending["timer"]), parsed)
    except Exception:
        logger.exception("Could not persist study-notes draft")
        await update.effective_message.reply_text("❌ Could not save the draft. No question-bank records were changed.")
        return True
    context.user_data.pop("pending_study_import", None)
    try:
        await status.edit_text(f"🧩 Stage 2/3: Validated {len(parsed)} source-grounded question(s).\nStage 3/3: Saved as private draft #{draft_id}; not yet in the question bank.")
    except TelegramError:
        pass
    preview = [f"📝 PRIVATE STUDY-NOTES DRAFT #{draft_id} — {pending['category']}", f"Questions passed validation: {len(parsed)} (requested max {count})", f"Timer: {pending['timer']}s", "Status: NOT SAVED TO QUESTION BANK / NOT PUBLISHED", ""]
    if quality_issues:
        preview.extend([f"⚠️ Quality review flags: {len(quality_issues)}", *[f"• {issue}" for issue in quality_issues[:5]], "Review flagged items before approving.", ""])
    else:
        preview.extend(["✅ No structural or duplicate issues detected by automated checks.", "Note: factual correctness still requires owner review.", ""])
    for n, item in enumerate(parsed[:3], 1):
        preview.extend([f"Q{n}. {item['text']}", *(f"{k}. {item['options'][k]}" for k in "ABCD"), f"Answer: {item['correct']}", f"Explanation: {item.get('explanation') or 'Not supplied'}", ""])
    if len(parsed) > 3:
        preview.append(f"…and {len(parsed)-3} more questions.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve & Save Draft", callback_data=f"draft:{draft_id}:approve"), InlineKeyboardButton("🗑 Reject Draft", callback_data=f"draft:{draft_id}:reject")]])
    await update.effective_message.reply_text("\n".join(preview)[:3900], reply_markup=keyboard)
    return True


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
            "Upload TXT/PDF/DOCX or paste questions in any readable MCQ layout. "
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
        return
    context.user_data["pending_bulk_import"] = {
        "category": category.name,
        "timer": timer,
    }
    await update.effective_message.reply_text(
        f"📥 Ready to import into {category.name}.\n"
        f"Per-question timer: {timer}s.\n"
        "Now upload a .txt/.pdf/.docx file or paste the full question set. "
        "After import, the quiz will wait for owner approval before any polls are sent."
    )


QUESTION_DRAFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS question_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    category_name TEXT NOT NULL,
    timer_seconds INTEGER NOT NULL DEFAULT 30,
    questions_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    saved_question_ids TEXT
)
"""


def init_question_drafts_table() -> None:
    """Create durable, owner-only import drafts without altering existing quiz tables."""
    with SessionLocal() as db:
        db.execute(text(QUESTION_DRAFT_SCHEMA))
        db.commit()


def store_question_draft(owner_id: str, category: str, timer: int, questions: list[dict]) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    with SessionLocal() as db:
        result = db.execute(text("""
            INSERT INTO question_drafts
                (owner_id, category_name, timer_seconds, questions_json, status, created_at, updated_at)
            VALUES (:owner, :category, :timer, :questions, 'pending', :created, :updated)
        """), {
            "owner": str(owner_id), "category": category, "timer": int(timer),
            "questions": json.dumps(questions, ensure_ascii=False),
            "created": now, "updated": now,
        })
        db.commit()
        return int(result.lastrowid)


def get_question_draft(draft_id: int, owner_id: str) -> dict | None:
    init_question_drafts_table()
    with SessionLocal() as db:
        row = db.execute(text("""
            SELECT * FROM question_drafts WHERE id=:id AND owner_id=:owner
        """), {"id": int(draft_id), "owner": str(owner_id)}).mappings().first()
    if not row:
        return None
    draft = dict(row)
    draft["questions"] = json.loads(draft.pop("questions_json"))
    return draft


async def drafts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    init_question_drafts_table()
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT id, category_name, timer_seconds, status, created_at
            FROM question_drafts WHERE owner_id=:owner ORDER BY id DESC LIMIT 20
        """), {"owner": str(update.effective_user.id)}).mappings().all()
    if not rows:
        await update.effective_message.reply_text("📂 No saved import drafts yet. Use /importquiz CATEGORY to create one.")
        return
    lines = ["📂 YOUR SAVED QUESTION DRAFTS"]
    for row in rows:
        lines.append(f"#{row['id']} | {row['category_name']} | {row['status']} | {row['created_at']} | {row['timer_seconds']}s")
    lines.append("Pending drafts survive bot restarts. Use the original draft buttons to approve or reject.")
    await update.effective_message.reply_text("\n".join(lines)[:3900])


async def process_bulk_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str):
    pending = context.user_data.get("pending_bulk_import")
    if not pending:
        return False
    if not await owner_required(update):
        context.user_data.pop("pending_bulk_import", None)
        return True
    if not raw_text or not raw_text.strip():
        await update.effective_message.reply_text(
            "❌ No readable text was extracted. If this is a scanned PDF, upload a clearer copy "
            "or ensure OCR support is installed on the host."
        )
        return True

    # Show genuine processing stages to the owner rather than leaving the upload unexplained.
    status_message = await update.effective_message.reply_text(
        "🔎 Stage 1/3: Reading and analysing the supplied question text…"
    )
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
    parsed, quality_issues = audit_question_quality(parsed)
    try:
        await status_message.edit_text(
            f"🧩 Stage 2/3: Validating question structure…\n"
            f"AI/local parser detected {len(parsed)} complete question(s)."
        )
    except TelegramError:
        pass
    if len(parsed) > 200:
        await update.effective_message.reply_text(
            "❌ Maximum 200 questions per imported quiz. Split the file into smaller sets."
        )
        return True
    if not parsed:
        detail = (f"AI normalization was unavailable: {str(ai_error)[:180]}" if ai_error
                  else "Try a text-based PDF or paste the question set again.")
        try:
            await status_message.edit_text(
                "❌ I couldn't reliably identify complete MCQs with four options and a valid answer.\n"
                "Accepted layouts include numbered questions, bullets, [A]/(A)/A) options, and "
                "answers written as a letter or option text. Missing or ambiguous answers are not guessed.\n"
                + detail
            )
        except TelegramError:
            await update.effective_message.reply_text("❌ No complete, valid questions detected.\n" + detail)
        return True
    category_name = pending["category"]
    timer = int(pending["timer"])
    context.user_data.pop("pending_bulk_import", None)
    # Keep parsed questions as a private draft. Nothing is written to the question bank
    # until the owner explicitly approves this draft.
    try:
        draft_id = store_question_draft(
            str(update.effective_user.id), category_name, timer, parsed
        )
    except Exception:
        logger.exception("Could not persist imported question draft")
        await update.effective_message.reply_text(
            "❌ I could not save the draft to the database. Nothing was added to the question bank; please retry."
        )
        return True
    context.user_data["pending_question_draft_id"] = draft_id
    try:
        await status_message.edit_text(
            f"✅ Stage 3/3 complete: {len(parsed)} question(s) validated.\n"
            "They are held as a private draft and have NOT been saved to the question bank."
        )
    except TelegramError:
        pass

    preview_lines = [
        f"📝 PRIVATE DRAFT REVIEW — {category_name}",
        f"Questions passed structural checks: {len(parsed)}",
        f"Per-question timer: {timer}s",
        "Status: NOT SAVED — NOT PUBLISHED",
        "",
    ]
    if quality_issues:
        preview_lines.extend([f"⚠️ Quality review flags: {len(quality_issues)}", *[f"• {issue}" for issue in quality_issues[:5]], "Review flagged items before approving.", ""])
    else:
        preview_lines.extend(["✅ No structural or duplicate issues detected by automated checks.", "Note: factual correctness still requires owner review.", ""])
    for idx, item in enumerate(parsed[:3], 1):
        preview_lines.extend([
            f"Q{idx}. {item['text']}",
            *(f"{letter}. {item['options'][letter]}" for letter in "ABCD"),
            f"Correct answer: {item['correct']}",
            f"Explanation: {item.get('explanation') or 'Not provided'}",
            "",
        ])
    if len(parsed) > 3:
        preview_lines.append(f"…and {len(parsed) - 3} more question(s).")
    preview_lines.extend([
        "\nApprove to save these questions and prepare the full private quiz preview.",
        "Reject to discard this draft. No questions will be saved if rejected.",
    ])
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve & Save Draft", callback_data=f"draft:{draft_id}:approve"),
         InlineKeyboardButton("🗑 Reject Draft", callback_data=f"draft:{draft_id}:reject")]
    ])
    await update.effective_message.reply_text(
        "\n".join(preview_lines)[:3900], reply_markup=keyboard
    )
    return True


async def question_draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not await owner_required(update):
        await query.answer("Owner-only action.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await query.answer("Invalid draft action.", show_alert=True)
        return
    draft_id, action = int(parts[1]), parts[2]
    draft = get_question_draft(draft_id, str(update.effective_user.id))
    if not draft:
        await query.answer("Draft not found or not owned by you.", show_alert=True)
        return
    if draft.get("status") != "pending":
        await query.answer(f"This draft is already {draft.get('status')}.", show_alert=True)
        return
    if action == "reject":
        with SessionLocal() as db:
            db.execute(text("UPDATE question_drafts SET status='rejected', updated_at=:now WHERE id=:id AND owner_id=:owner AND status='pending'"), {
                "now": datetime.utcnow().isoformat(timespec="seconds"), "id": draft_id,
                "owner": str(update.effective_user.id),
            })
            db.commit()
        context.user_data.pop("pending_question_draft_id", None)
        await query.answer("Draft rejected.")
        try:
            await query.edit_message_text(f"🗑 Draft #{draft_id} rejected. No questions were saved.")
        except TelegramError:
            pass
        return
    if action != "approve":
        await query.answer("Unknown draft action.", show_alert=True)
        return

    # get_question_draft returns database column names; use those exact keys.
    # The older aliases silently produced an empty category and default timer,
    # which caused valid persisted drafts to fail approval after retrieval.
    questions = draft.get("questions", [])
    category_name = str(draft.get("category_name", "")).strip()
    timer = int(draft.get("timer_seconds", 30))
    if not category_name:
        await query.answer("Draft has no category; nothing was saved.", show_alert=True)
        return
    if not 5 <= timer <= 600:
        await query.answer("Draft timer is invalid; nothing was saved.", show_alert=True)
        return
    if not questions or not 1 <= len(questions) <= 200:
        await query.answer("Draft is empty or exceeds the 200-question limit.", show_alert=True)
        return

    saved_ids = []
    try:
        with SessionLocal() as db:
            category = db.query(Category).filter(Category.name.ilike(category_name)).first()
            if not category:
                await query.answer("Category no longer exists.", show_alert=True)
                return
            for item in questions:
                options = item.get("options", {})
                correct = str(item.get("correct", "")).upper()
                if not item.get("text") or not all(options.get(letter) for letter in "ABCD") or correct not in "ABCD" or len(correct) != 1:
                    await query.answer("Draft validation failed; nothing was saved.", show_alert=True)
                    return
            for item in questions:
                options = item["options"]
                row = Question(
                    category_id=category.id,
                    question_text=item["text"],
                    option_a=options["A"], option_b=options["B"],
                    option_c=options["C"], option_d=options["D"],
                    correct_option=item["correct"],
                    explanation=item.get("explanation", ""),
                    difficulty=item.get("difficulty", "Moderate"),
                    marks=1.0, negative_marks=0.25, active=True,
                )
                db.add(row)
                db.flush()
                saved_ids.append(row.id)
            db.execute(text("""
                UPDATE question_drafts SET status='approved', saved_question_ids=:ids, updated_at=:now
                WHERE id=:id AND owner_id=:owner AND status='pending'
            """), {
                "ids": json.dumps(saved_ids), "now": datetime.utcnow().isoformat(timespec="seconds"),
                "id": draft_id, "owner": str(update.effective_user.id),
            })
            db.commit()
    except Exception:
        logger.exception("Failed to approve and save imported question draft")
        await query.answer("Save failed. Draft remains available; check bot logs.", show_alert=True)
        return

    context.user_data.pop("pending_question_draft_id", None)
    await query.answer("Draft approved and saved.")
    try:
        await query.edit_message_text(
            f"✅ Draft #{draft_id} approved. Saved {len(saved_ids)} question(s) to {category_name}.\n"
            "Preparing the full private quiz preview; no group polls have been sent yet."
        )
    except TelegramError:
        pass
    await publish_quiz_to_chat(
        update, context, 0, category_name, len(saved_ids), timer,
        question_ids=saved_ids,
    )


async def bulk_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("pending_study_import"):
        message = update.effective_message
        if message and message.text and not message.text.startswith("/"):
            await process_study_notes_text(update, context, message.text)
        return
    if context.user_data.get("pending_bulk_import"):
        message = update.effective_message
        if message and message.text and not message.text.startswith("/"):
            await process_bulk_question_text(update, context, message.text)
        return
    # Preserve the existing /addquestion two-message workflow.
    await pending_question_fields(update, context)


async def _extract_document_text(filename: str, data: bytes) -> tuple[str, str]:
    """Extract text from supported documents; OCR is optional and fails gracefully."""
    lower_name = filename.lower()
    if lower_name.endswith(".txt"):
        return bytes(data).decode("utf-8-sig", errors="replace"), "TXT text extraction"

    if lower_name.endswith(".docx"):
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("DOCX support needs python-docx installed in requirements.txt.") from exc
        import io
        document = Document(io.BytesIO(data))
        chunks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    chunks.append(" | ".join(cells))
        return "\n".join(chunks), "DOCX text extraction"

    if lower_name.endswith(".pdf"):
        import io
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF support needs pypdf installed in requirements.txt.") from exc

        reader = PdfReader(io.BytesIO(data))
        extracted_pages = [(page.extract_text() or "").strip() for page in reader.pages]
        # A PDF can mix selectable-text pages and scanned/image-only pages. Keep
        # the text layer where available and OCR only the pages that are empty.
        missing_pages = [index for index, page_text in enumerate(extracted_pages) if not page_text]
        if not missing_pages:
            return "\n".join(extracted_pages).strip(), "PDF text extraction"

        try:
            import fitz  # PyMuPDF renders pages without requiring Poppler
            import pytesseract
            from PIL import Image
            pdf = fitz.open(stream=data, filetype="pdf")
            for page_index in missing_pages:
                page = pdf.load_page(page_index)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image_obj = Image.open(io.BytesIO(pix.tobytes("png")))
                ocr_text = (pytesseract.image_to_string(image_obj) or "").strip()
                if ocr_text:
                    extracted_pages[page_index] = ocr_text
        except ImportError as exc:
            if any(extracted_pages):
                # Preserve usable text rather than failing the whole document;
                # make the partial extraction visible to the caller/logs.
                logger.warning(
                    "PDF has %d image-only page(s); optional OCR dependencies are unavailable. "
                    "Importing only selectable text.", len(missing_pages)
                )
                text_value = "\n".join(extracted_pages).strip()
                return text_value, "PDF text extraction (partial; OCR unavailable)"
            raise RuntimeError(
                "This PDF appears scanned and has no selectable text. OCR support requires "
                "pymupdf, pytesseract, Pillow, and the Tesseract OCR engine on the host. "
                "Or upload a text-based PDF/TXT/DOCX."
            ) from exc
        except Exception as exc:
            logger.warning("OCR failed for one or more PDF pages: %s", str(exc)[:200])
            if not any(extracted_pages):
                raise RuntimeError(
                    "This scanned PDF could not be read by OCR. Check that Tesseract is "
                    f"installed and the scan is clear. Details: {str(exc)[:160]}"
                ) from exc

        text_value = "\n".join(extracted_pages).strip()
        if text_value:
            method = "PDF text extraction + OCR" if any(extracted_pages[i] for i in missing_pages) else "PDF text extraction (partial; some pages unreadable)"
            if any(not extracted_pages[i] for i in missing_pages):
                method += " (some pages unreadable)"
            return text_value, method
        raise RuntimeError("No readable text found in this PDF.")

    raise RuntimeError("Unsupported file type. Upload .txt, .pdf, or .docx.")


async def bulk_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("pending_bulk_import") and not context.user_data.get("pending_study_import"):
        return
    if not await owner_required(update):
        context.user_data.pop("pending_bulk_import", None)
        return
    message = update.effective_message
    document = message.document if message else None
    if not document:
        return
    filename = (document.file_name or "").lower()
    if not filename.endswith((".txt", ".pdf", ".docx")):
        await message.reply_text("❌ Upload a .txt, .pdf, or .docx file.")
        return
    try:
        await message.reply_text(f"📄 Reading {document.file_name or 'uploaded file'}…")
        tg_file = await context.bot.get_file(document.file_id)
        data = bytes(await tg_file.download_as_bytearray())
        raw_text, method = await asyncio.to_thread(_extract_document_text, filename, data)
        if not raw_text.strip():
            await message.reply_text(
                "❌ No readable text found. If this is a scanned PDF, check OCR support and scan quality."
            )
            return
        logger.info("Bulk import extracted %s characters via %s from %s", len(raw_text), method, filename)
        if context.user_data.get("pending_study_import"):
            await process_study_notes_text(update, context, raw_text)
        else:
            await process_bulk_question_text(update, context, raw_text)
    except Exception as exc:
        logger.exception("Bulk question file import failed")
        await message.reply_text(f"❌ Could not read that file: {str(exc)[:400]}")

async def run_sequential_quiz(context: ContextTypes.DEFAULT_TYPE, session_key: str):
    """Send one poll at a time; persisted timer/index allow restart recovery."""
    session = get_sessions(context).get(str(session_key))
    if not session or session.get("finalized") or session.get("awaiting_approval"):
        return
    running = context.application.bot_data.setdefault("sequential_running", set())
    task_registry = context.application.bot_data.setdefault("sequential_tasks", {})
    if str(session_key) in running:
        return
    running.add(str(session_key))
    task_registry[str(session_key)] = asyncio.current_task()
    try:
        target_chat_id = int(session["group_id"])
        display = display_name_for(target_chat_id)
        timer = int(session.get("time_limit_seconds", 30))
        questions = session.get("questions", [])
        while not session.get("finalized"):
            if session.get("paused"):
                await asyncio.sleep(1)
                continue

            # Re-check the owner-managed allowlist at delivery time, not only at
            # approval time. Revoking a group must stop an already-live quiz too.
            if not active_approved_group(target_chat_id):
                session["paused"] = True
                session["current_poll_end_at"] = None
                current_message_id = session.get("current_poll_message_id")
                if current_message_id:
                    try:
                        await context.bot.stop_poll(
                            chat_id=target_chat_id,
                            message_id=int(current_message_id),
                        )
                    except TelegramError:
                        logger.info("Poll already closed while target group was revoked.")
                session["current_poll_message_id"] = None
                persist_live_session(session)
                if OWNER_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=int(OWNER_ID),
                            text=(f"🔒 Quiz {session_key} was paused because target group "
                                  f"{target_chat_id} is no longer owner-approved. "
                                  "Re-approve the group before resuming."),
                        )
                    except TelegramError:
                        logger.exception("Could not notify owner about revoked quiz target")
                return

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
            try:
                choices, correct_index = shuffled_option_entries(
                    item.get("options", {}), item.get("correct", "")
                )
            except ValueError:
                logger.exception("Invalid option mapping in quiz session %s question %s", session_key, index + 1)
                session["paused"] = True
                session["pause_reason"] = "invalid_question_options"
                persist_live_session(session)
                try:
                    await context.bot.send_message(
                        chat_id=int(OWNER_ID),
                        text=(f"Quiz {session_key} paused: question {index + 1} has invalid or duplicate options "
                              "or an invalid correct-answer label. Edit the question and resume."),
                    )
                except (TelegramError, ValueError):
                    logger.exception("Could not notify owner about invalid quiz question")
                return
            option_texts = [value[:100] for _, value in choices]
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
            session["current_poll_message_id"] = poll_message.message_id
            session.setdefault("poll_metadata", {})[str(poll_id)] = {
                k: v for k, v in metadata.items() if k != "session_key"
            }
            session["current_index"] = index + 1
            session["current_poll_end_at"] = time.time() + timer
            persist_live_session(session)
    finally:
        running.discard(str(session_key))
        if task_registry.get(str(session_key)) is asyncio.current_task():
            task_registry.pop(str(session_key), None)


async def pause_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /pausequiz QUIZ_ID")
        return
    key = context.args[0]
    session = get_sessions(context).get(key)
    if not session or session.get("finalized"):
        await update.effective_message.reply_text("Active quiz session not found.")
        return
    session["paused"] = True
    poll_message_id = session.get("current_poll_message_id")
    if poll_message_id:
        try:
            await context.bot.stop_poll(
                chat_id=int(session["group_id"]),
                message_id=int(poll_message_id),
            )
        except TelegramError:
            logger.info("Current poll was already closed or could not be stopped.")
    session["current_poll_end_at"] = None
    session["current_poll_message_id"] = None
    persist_live_session(session)
    task = context.application.bot_data.setdefault("sequential_tasks", {}).get(key)
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()
        await asyncio.sleep(0)
    await update.effective_message.reply_text(
        f"⏸ Quiz {key} paused. Submitted answers are preserved. "
        f"Use /resumequiz {key} to continue or /finalizequiz {key} to publish results so far."
    )


async def resume_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /resumequiz QUIZ_ID")
        return
    key = context.args[0]
    session = get_sessions(context).get(key)
    if not session or session.get("finalized"):
        await update.effective_message.reply_text("Unfinalized quiz session not found.")
        return
    if session.get("awaiting_approval"):
        await update.effective_message.reply_text(
            "This quiz is awaiting approval. Set an approved group and use /approvequiz first."
        )
        return
    if not active_approved_group(session.get("group_id", "")):
        await update.effective_message.reply_text(
            "⛔ The target group is no longer approved. Quiz delivery remains stopped."
        )
        return
    session["paused"] = False
    persist_live_session(session)
    context.application.create_task(run_sequential_quiz(context, key))
    await update.effective_message.reply_text(
        f"▶️ Quiz {key} resumed from its saved position. Submitted answers are preserved."
    )


async def stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop delivery without deleting the session or participant answers."""
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /stopquiz QUIZ_ID")
        return
    key = context.args[0]
    session = get_sessions(context).get(key)
    if not session or session.get("finalized"):
        await update.effective_message.reply_text("Unfinalized quiz session not found.")
        return
    session["paused"] = True
    poll_message_id = session.get("current_poll_message_id")
    if poll_message_id:
        try:
            await context.bot.stop_poll(
                chat_id=int(session["group_id"]),
                message_id=int(poll_message_id),
            )
        except TelegramError:
            logger.info("Current poll was already closed or could not be stopped.")
    session["current_poll_end_at"] = None
    session["current_poll_message_id"] = None
    persist_live_session(session)
    task = context.application.bot_data.setdefault("sequential_tasks", {}).get(key)
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()
        await asyncio.sleep(0)
    await update.effective_message.reply_text(
        f"⏹ Quiz {key} stopped safely. Submitted answers are preserved.\n"
        f"Publish results so far: /finalizequiz {key}\n"
        f"Continue later: /resumequiz {key}"
    )


async def restart_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart delivery at the saved next-question position without wiping answers."""
    if not await owner_required(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /restartquiz QUIZ_ID")
        return
    key = context.args[0]
    session = get_sessions(context).get(key)
    if not session or session.get("finalized"):
        await update.effective_message.reply_text("Unfinalized quiz session not found.")
        return
    if session.get("awaiting_approval"):
        await update.effective_message.reply_text(
            "This quiz is awaiting approval. Set an approved group and use /approvequiz first."
        )
        return
    if not active_approved_group(session.get("group_id", "")):
        await update.effective_message.reply_text("⛔ The target group is no longer approved.")
        return
    poll_message_id = session.get("current_poll_message_id")
    if poll_message_id:
        try:
            await context.bot.stop_poll(
                chat_id=int(session["group_id"]),
                message_id=int(poll_message_id),
            )
        except TelegramError:
            logger.info("Current poll was already closed or could not be stopped.")
    task = context.application.bot_data.setdefault("sequential_tasks", {}).get(key)
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()
        await asyncio.sleep(0)
    session["paused"] = False
    session["current_poll_end_at"] = None
    session["current_poll_message_id"] = None
    persist_live_session(session)
    context.application.create_task(run_sequential_quiz(context, key))
    await update.effective_message.reply_text(
        f"🔄 Quiz {key} delivery restarted from the saved next-question position. "
        "Submitted answers have not been deleted."
    )


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
        await update.effective_message.reply_text("⛔ This group is not approved.")
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
        await update.effective_message.reply_text("⛔ Target must be a group/supergroup ID (normally starts with -100).")
        return
    approved = active_approved_group(group_id)
    if not approved:
        await update.effective_message.reply_text(
            "⛔ This group is not currently owner-approved. Approve it first with /approve_group GROUP_ID."
        )
        return
    session = get_sessions(context).get(key)
    if not session or session.get("finalized"):
        await update.effective_message.reply_text("Quiz session not found or already finalized. Check /activequizzes.")
        return
    if not session.get("awaiting_approval"):
        await update.effective_message.reply_text("⛔ Target cannot be changed after publishing has been approved or started.")
        return
    session["group_id"] = str(group_id)
    session["group_title"] = str(getattr(approved, "group_title", "") or group_id)
    persist_live_session(session)
    await update.effective_message.reply_text(
        f"✅ Preview target set for quiz {key}.\n"
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
            "❌ Quiz session not found. Check /activequizzes."
        )
        return
    if session.get("finalized"):
        await update.effective_message.reply_text("This quiz is already finalized.")
        return
    if not session.get("awaiting_approval"):
        await update.effective_message.reply_text(
            f"✅ Quiz {key} has already been approved or started."
        )
        return
    target_chat_id = int(session["group_id"])
    if target_chat_id >= 0:
        await update.effective_message.reply_text(
            "🔒 Preview only: select an owner-approved group first with "
            f"/setquiztarget {key} GROUP_ID. Private bot chats never receive quiz polls."
        )
        return
    if not active_approved_group(target_chat_id):
        await update.effective_message.reply_text(
            "⛔ The target group is not currently owner-approved. Set an approved group with /setquiztarget."
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
        f"✅ Quiz {key} approved. Sequential publishing is starting now."
    )
    context.application.create_task(run_sequential_quiz(context, key))


async def active_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    sessions = get_sessions(context)
    lines = ["📊 ACTIVE QUIZZES\n"]
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
            "Persisted quiz session was not found. If this quiz was created by an older bot version, "
            "its live session may not be recoverable. Saved results remain available with /leaderboard QUIZ_ID."
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
        total_possible_marks = sum(
            float(session.get("poll_metadata", {}).get(str(poll_id), {}).get("marks", 1.0))
            for poll_id in session["poll_ids"]
        )
        score_pct = score / total_possible_marks * 100 if total_possible_marks else 0.0
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

        # Save the finalized marker in the same transaction as result rows. If the
        # process crashes after commit, a restored session cannot insert duplicates.
        finalized_session = dict(session)
        finalized_session["finalized"] = True
        db.execute(text("""
            INSERT INTO live_quiz_sessions (quiz_id, session_json, updated_at)
            VALUES (:quiz_id, :payload, :updated_at)
            ON CONFLICT(quiz_id) DO UPDATE SET
                session_json = excluded.session_json,
                updated_at = excluded.updated_at
        """), {
            "quiz_id": str(session["quiz_id"]),
            "payload": json.dumps(finalized_session, ensure_ascii=False, separators=(",", ":")),
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        })

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
                quiz_family_id=str(session.get("quiz_family_id") or quiz_family_fingerprint(session.get("questions", []))),
                score=float(result["score"]),
                correct_count=int(result["correct"]),
                wrong_count=int(result["wrong"]),
                unanswered_count=int(result["unanswered"]),
                started_at=started_at,
                submitted_at=now,
                duration_seconds=duration,
                accuracy_percent=float(result["accuracy"]),
                score_percent=float(result["score_percent"]),
                group_percentile=None,
                overall_percentile=None,
                performance_tagline=feedback["tagline"],
                performance_insight=feedback["insight"],
                motivational_quote=feedback["quote"],
                leaderboard_published=True,
            ))
        db.commit()
        # The result rows and finalized marker are now durable; block any in-process retry too.
        session["finalized"] = True

        # Percentiles compare only participants taking the same mock content.
        # Group percentile is calculated within the participant's own group; overall
        # percentile includes every group and private attempt for this quiz family.
        family_id = str(session.get("quiz_family_id") or quiz_family_fingerprint(session.get("questions", [])))
        recalculate_family_percentiles(db, family_id)
        db.commit()

    session["finalized"] = True
    await update.effective_message.reply_text(
        f"✅ Quiz {key} finalized.\n"
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
    session = get_sessions(context).get(str(quiz_id))
    selected_group_id = str(group_id or (session or {}).get("group_id") or "").strip()
    with SessionLocal() as db:
        query = db.query(StudentAttempt).filter(StudentAttempt.quiz_id == quiz_id)
        # Group leaderboard contains group-poll participants only; private attempts
        # are included in the overall comparison pool, not exposed in group results.
        if selected_group_id and selected_group_id.startswith("-"):
            query = query.filter(StudentAttempt.group_id == selected_group_id)
        else:
            query = query.filter(StudentAttempt.group_id != "")
        attempts = query.order_by(
            StudentAttempt.score.desc(),
            StudentAttempt.correct_count.desc(),
            StudentAttempt.submitted_at.asc(),
            StudentAttempt.id.asc(),
        ).all()
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not attempts:
            await update.effective_message.reply_text("No saved group-poll results for this quiz.")
            return

        title = quiz.title if quiz else f"Quiz {quiz_id}"
        family_id = str((session or {}).get("quiz_family_id") or attempts[0].quiz_family_id or "")
        overall_pool_size = (
            db.query(StudentAttempt).filter(StudentAttempt.quiz_family_id == family_id).count()
            if family_id else len(attempts)
        )
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
    first_row = rows[0]
    target_id = str(
        (session or {}).get("group_id") or first_row["group_id"] or group_id or "not stored"
    )
    target_title = str(
        (session or {}).get("group_title") or first_row["group_title"] or target_id
    )
    total_possible_marks = sum(
        float((session or {}).get("poll_metadata", {}).get(str(poll_id), {}).get("marks", 1.0))
        for poll_id in (session or {}).get("poll_ids", [])
    ) or float(total_questions or 0)
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
        "Scoring: per-question marks and penalties configured for this quiz; unanswered = 0.",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 Group Participants: {n}",
        f"🌐 Overall Comparison Pool (same mock, all groups + private attempts): {overall_pool_size}",
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
        score_pct = row["score_percent"]
        name = row["name"] or f"Telegram user {attempt.student_telegram_id}"
        duration = row["duration_seconds"]
        time_used = f"{duration // 60}m {duration % 60}s" if duration > 0 else "Not available"
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "🏅"
        lines.extend([
            f"{medal} RANK #{rank} — {name}",
            f"🎯 Net Score: {row['score']:.2f} / {total_possible_marks:g} possible marks",
            f"📈 Score Percentage: {score_pct:.1f}%",
            f"👥 Group Percentile: {float(row['group_percentile'] if row.get('group_percentile') is not None else percentile):.1f}%",
            f"🌐 Overall Percentile: {float(row['overall_percentile'] if row.get('overall_percentile') is not None else 0.0):.1f}%",
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
    """Resume persisted attempts, but never resume delivery to a revoked group."""
    restore_private_attempts(app)
    for key, session in list(app.bot_data.get("quiz_sessions", {}).items()):
        if (not session.get("sequential") or session.get("finalized")
                or session.get("awaiting_approval")):
            continue
        if not active_approved_group(session.get("group_id", "")):
            session["paused"] = True
            session["current_poll_end_at"] = None
            persist_live_session(session)
            logger.warning(
                "Kept quiz %s paused because target group %s is not approved",
                key, session.get("group_id"),
            )
            continue
        if not session.get("paused"):
            app.create_task(run_sequential_quiz(app, str(key)))




# ---------------------------------------------------------------------
# Question-bank editor (owner-only)
# ---------------------------------------------------------------------


async def edit_question_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Begin a safe full-record edit of one question-bank entry by ID."""
    if not await owner_required(update):
        return
    message = update.effective_message
    if not message:
        return
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Usage: /editquestion QUESTION_ID")
        return
    question_id = int(context.args[0])
    with SessionLocal() as db:
        q = db.query(Question).filter(Question.id == question_id).first()
        if not q:
            await message.reply_text(f"Question #{question_id} was not found.")
            return
        current = (
            f"Question: {q.question_text or ''}\n"
            f"A: {q.option_a or ''}\nB: {q.option_b or ''}\n"
            f"C: {q.option_c or ''}\nD: {q.option_d or ''}\n"
            f"Correct: {str(q.correct_option or '').upper()}\n"
            f"Explanation: {q.explanation or ''}\n"
            f"Difficulty: {q.difficulty or 'Moderate'}"
        )
    context.user_data["question_edit_id"] = question_id
    await message.reply_text(
        f"✏️ Editing question #{question_id}. Send the COMPLETE updated block below.\n"
        "Required fields: Question, A, B, C, D, Correct. Explanation and Difficulty are optional.\n"
        "Correct must be A, B, C, or D. Difficulty: Easy, Moderate, or Difficult.\n"
        "Send /cancel to cancel.\n\nCurrent record:\n" + current
    )


async def edit_question_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question_id = context.user_data.get("question_edit_id")
    message = update.effective_message
    if question_id is None or not message or not message.text:
        return
    if message.text.strip().lower() == "/cancel":
        context.user_data.pop("question_edit_id", None)
        await message.reply_text("Question edit cancelled; no changes were made.")
        return
    if not await owner_required(update):
        context.user_data.pop("question_edit_id", None)
        return

    fields = {}
    for line in message.text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().casefold()] = value.strip()
    qtext = fields.get("question", "")
    opts = {letter: fields.get(letter.lower(), "") for letter in "ABCD"}
    correct_raw = fields.get("correct", "").strip().upper()
    # Accept a letter or the exact text of one of the four options.
    if correct_raw in {"A", "B", "C", "D"}:
        correct = correct_raw
    else:
        matches = [letter for letter, value in opts.items() if value and value.casefold() == correct_raw.casefold()]
        correct = matches[0] if len(matches) == 1 else ""
    difficulty = fields.get("difficulty", "Moderate").title()
    if not qtext or not all(opts.values()):
        await message.reply_text("❌ Question and all four options A, B, C, D are required. No changes were made.")
        return
    if len({v.casefold() for v in opts.values()}) != 4:
        await message.reply_text("❌ The four options must be distinct. No changes were made.")
        return
    if not correct:
        await message.reply_text("❌ Correct must be A/B/C/D or match exactly one option. No changes were made.")
        return
    if difficulty not in {"Easy", "Moderate", "Difficult"}:
        await message.reply_text("❌ Difficulty must be Easy, Moderate, or Difficult. No changes were made.")
        return

    with SessionLocal() as db:
        q = db.query(Question).filter(Question.id == int(question_id)).first()
        if not q:
            context.user_data.pop("question_edit_id", None)
            await message.reply_text("That question no longer exists; no changes were made.")
            return
        q.question_text = qtext
        q.option_a, q.option_b, q.option_c, q.option_d = opts["A"], opts["B"], opts["C"], opts["D"]
        q.correct_option = correct
        q.explanation = fields.get("explanation", "")
        q.difficulty = difficulty
        db.commit()
    context.user_data.pop("question_edit_id", None)
    await message.reply_text(
        f"✅ Question #{question_id} updated. Correct option: {correct}. Difficulty: {difficulty}.\n"
        "Only this question-bank record was changed; no quiz was published."
    )


# ---------------------------------------------------------------------
# Full private quiz preview (owner-only)
# ---------------------------------------------------------------------


async def preview_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the complete persisted session question set privately, including answer keys."""
    if not await owner_required(update):
        return
    message = update.effective_message
    if not message:
        return
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Usage: /previewquiz QUIZ_ID")
        return

    quiz_id = str(int(context.args[0]))
    try:
        with SessionLocal() as db:
            row = db.execute(
                text("SELECT session_json FROM live_quiz_sessions WHERE quiz_id=:quiz_id"),
                {"quiz_id": quiz_id},
            ).fetchone()
    except Exception:
        logger.exception("Could not load persisted quiz preview for quiz %s", quiz_id)
        await message.reply_text("Could not read the saved quiz session. Check the bot logs.")
        return

    if not row:
        await message.reply_text(
            f"No persisted question set was found for quiz #{quiz_id}. "
            "The database stores quiz metadata separately, so this command cannot reconstruct "
            "question order for older quizzes that have no saved live session."
        )
        return
    try:
        session = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        await message.reply_text("The saved quiz session is unreadable; no changes were made.")
        return

    questions = session.get("questions")
    if not isinstance(questions, list) or not questions:
        await message.reply_text("This saved session has no question list to preview.")
        return

    title = str(session.get("title") or session.get("category") or f"Quiz #{quiz_id}")
    header = (
        f"🔎 PH 0xGREY // Intelligence — FULL PRIVATE PREVIEW\n"
        f"Quiz ID: {quiz_id}\nTitle: {title}\nQuestions: {len(questions)}\n"
        f"Timer: {session.get('time_limit_seconds', 'Not set')} seconds per question\n"
        "Answer keys are shown only in this owner-only private chat.\n\n"
    )
    chunks = []
    current = header
    for index, item in enumerate(questions, 1):
        if not isinstance(item, dict):
            block = f"Q{index}. [Invalid saved question record — review required]\n\n"
        else:
            options = item.get("options") or {}
            block = (
                f"Q{index}. {item.get('text', '[Missing question text]')}\n"
                f"A. {options.get('A', '')}\n"
                f"B. {options.get('B', '')}\n"
                f"C. {options.get('C', '')}\n"
                f"D. {options.get('D', '')}\n"
                f"Correct answer: {item.get('correct', '[MISSING]')}\n"
                f"Explanation: {item.get('explanation') or 'Not provided'}\n\n"
            )
        if len(current) + len(block) > 3500:
            chunks.append(current)
            current = f"🔎 FULL PRIVATE PREVIEW — Quiz {quiz_id} (continued)\n\n"
        current += block
    if current.strip():
        chunks.append(current)
    for chunk in chunks:
        await message.reply_text(chunk)


# ---------------------------------------------------------------------
# Safe add/remove questions for an unpublished quiz (owner-only)
# ---------------------------------------------------------------------


def _load_editable_pending_session(quiz_id: int):
    """Return a pending, not-yet-published session or a user-safe reason."""
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT session_json FROM live_quiz_sessions WHERE quiz_id=:qid"),
            {"qid": str(quiz_id)},
        ).fetchone()
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not quiz:
            return None, None, "Quiz ID was not found in the quiz database."
        if not row:
            return None, quiz, "No saved question session exists for this quiz."
        try:
            session = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None, quiz, "The saved quiz session is unreadable; nothing was changed."
        # Editing after publication could invalidate poll answers and student results.
        if (str(quiz.status or "").casefold() not in {"pending_approval", "draft", "pending"}
                or not session.get("awaiting_approval")
                or session.get("poll_ids")
                or int(session.get("current_index") or 0) != 0
                or session.get("finalized")):
            return None, quiz, (
                "This quiz is no longer an unpublished pending quiz. To protect submitted answers "
                "and leaderboard accuracy, questions cannot be added or removed after publishing starts."
            )
        questions = session.get("questions")
        if not isinstance(questions, list) or not questions:
            return None, quiz, "The saved quiz has no valid question list."
        return session, quiz, None


async def add_quiz_question_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Append an active question-bank entry to a pending quiz by IDs."""
    if not await owner_required(update):
        return
    message = update.effective_message
    if not message:
        return
    if len(context.args) != 2 or not all(arg.isdigit() for arg in context.args):
        await message.reply_text("Usage: /addquizquestion QUIZ_ID QUESTION_ID")
        return
    quiz_id, question_id = map(int, context.args)
    session, quiz, error = _load_editable_pending_session(quiz_id)
    if error:
        await message.reply_text(f"❌ {error}")
        return
    with SessionLocal() as db:
        q = db.query(Question).filter(Question.id == question_id, Question.active.is_(True)).first()
        if not q:
            await message.reply_text("Active question ID not found. No changes were made.")
            return
        if q.category_id != quiz.category_id:
            await message.reply_text(
                "That question belongs to a different category. Choose a question from the quiz's category."
            )
            return
        new_item = {
            "id": int(q.id), "text": q.question_text,
            "options": {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d},
            "correct": str(q.correct_option or "").upper(),
            "explanation": q.explanation or "", "marks": float(q.marks or 1.0),
            "negative": float(q.negative_marks or 0.25),
        }
        if not new_item["text"] or not all(new_item["options"].values()) or new_item["correct"] not in "ABCD" or len(new_item["correct"]) != 1:
            await message.reply_text("This question-bank record is incomplete or has an invalid answer. No changes were made.")
            return
        if any(str(item.get("id")) == str(question_id) for item in session["questions"] if isinstance(item, dict)):
            await message.reply_text("That question is already in this quiz. No changes were made.")
            return
        session["questions"].append(new_item)
        session["quiz_family_id"] = quiz_family_fingerprint(session["questions"])
        quiz.question_count = len(session["questions"])
        timer = int(session.get("time_limit_seconds") or 30)
        quiz.duration_minutes = max(1, (len(session["questions"]) * timer + 59) // 60)
        db.commit()
    persist_live_session(session)
    await message.reply_text(
        f"✅ Added question #{question_id} to pending quiz #{quiz_id}.\n"
        f"Question count: {len(session['questions'])}.\n"
        "The quiz remains unpublished and still requires owner approval."
    )


async def remove_quiz_question_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a question by its 1-based position from a pending quiz only."""
    if not await owner_required(update):
        return
    message = update.effective_message
    if not message:
        return
    if len(context.args) != 2 or not all(arg.isdigit() for arg in context.args):
        await message.reply_text("Usage: /removequizquestion QUIZ_ID QUESTION_NUMBER\nQuestion numbers start at 1; use /previewquiz QUIZ_ID first.")
        return
    quiz_id, question_number = map(int, context.args)
    session, quiz, error = _load_editable_pending_session(quiz_id)
    if error:
        await message.reply_text(f"❌ {error}")
        return
    questions = session["questions"]
    if question_number < 1 or question_number > len(questions):
        await message.reply_text(f"Question number must be between 1 and {len(questions)}. No changes were made.")
        return
    if len(questions) <= 1:
        await message.reply_text("A quiz must retain at least one question. No changes were made.")
        return
    removed = questions.pop(question_number - 1)
    session["quiz_family_id"] = quiz_family_fingerprint(questions)
    with SessionLocal() as db:
        current_quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not current_quiz:
            await message.reply_text("Quiz record disappeared; no changes were made.")
            return
        current_quiz.question_count = len(questions)
        timer = int(session.get("time_limit_seconds") or 30)
        current_quiz.duration_minutes = max(1, (len(questions) * timer + 59) // 60)
        db.commit()
    persist_live_session(session)
    removed_text = str(removed.get("text", "[question text unavailable]")) if isinstance(removed, dict) else "[invalid question record]"
    if len(removed_text) > 180:
        removed_text = removed_text[:177] + "..."
    await message.reply_text(
        f"✅ Removed question {question_number} from pending quiz #{quiz_id}.\n"
        f"Removed: {removed_text}\nRemaining questions: {len(questions)}.\n"
        "The quiz remains unpublished and requires owner approval."
    )


# ---------------------------------------------------------------------
# Persistent quiz library (owner-only metadata management)
# ---------------------------------------------------------------------


def quiz_library_keyboard(quiz_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 View details", callback_data=f"qlib:view:{quiz_id}")],
        [InlineKeyboardButton("✏️ Rename quiz", callback_data=f"qlib:rename:{quiz_id}")],
        [InlineKeyboardButton("⬅️ Quiz library", callback_data="qlib:list")],
    ])


async def quiz_library_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List persisted quiz definitions without changing their publication state."""
    if not await owner_required(update):
        return
    await show_quiz_library(update.effective_message, context)


async def show_quiz_library(message, context):
    with SessionLocal() as db:
        quizzes = db.query(Quiz).order_by(Quiz.id.desc()).limit(50).all()
        rows = []
        for quiz in quizzes:
            category = db.query(Category).filter(Category.id == quiz.category_id).first()
            rows.append({
                "id": int(quiz.id),
                "title": str(quiz.title or f"Quiz #{quiz.id}"),
                "category": str(category.name if category else "Unknown category"),
                "count": int(quiz.question_count or 0),
                "duration": int(quiz.duration_minutes or 0),
                "status": str(quiz.status or "unknown"),
            })
    if not rows:
        await message.reply_text("📚 Quiz library is empty. Create or import a quiz first.")
        return
    buttons = []
    for row in rows:
        title = row["title"]
        if len(title) > 42:
            title = title[:39] + "..."
        buttons.append([InlineKeyboardButton(
            f"#{row['id']} · {title} · {row['status']}",
            callback_data=f"qlib:view:{row['id']}"
        )])
    await message.reply_text(
        "📚 PH 0xGREY // Intelligence — Quiz Library\n"
        "Choose a saved quiz to inspect or rename it. Showing the latest 50.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def quiz_library_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not is_owner(update) or not update.effective_chat or update.effective_chat.type != "private":
        await query.answer("Owner-only control in private chat.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    if data == "qlib:list":
        await show_quiz_library(query.message, context)
        return
    parts = data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    action, quiz_id = parts[1], int(parts[2])
    with SessionLocal() as db:
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not quiz:
            await query.message.reply_text("That quiz no longer exists in the database.")
            return
        category = db.query(Category).filter(Category.id == quiz.category_id).first()
        details = {
            "id": int(quiz.id),
            "title": str(quiz.title or f"Quiz #{quiz.id}"),
            "category": str(category.name if category else "Unknown category"),
            "count": int(quiz.question_count or 0),
            "duration": int(quiz.duration_minutes or 0),
            "status": str(quiz.status or "unknown"),
            "shuffle_questions": bool(quiz.shuffle_questions),
            "shuffle_options": bool(quiz.shuffle_options),
            "created_at": str(quiz.created_at or "Not recorded"),
        }
    if action == "view":
        await query.message.reply_text(
            f"📚 {details['title']}\n"
            f"Quiz ID: {details['id']}\nCategory: {details['category']}\n"
            f"Questions: {details['count']}\nDuration: {details['duration']} minute(s)\n"
            f"Status: {details['status']}\n"
            f"Shuffle questions: {'Yes' if details['shuffle_questions'] else 'No'}\n"
            f"Shuffle options: {'Yes' if details['shuffle_options'] else 'No'}\n"
            f"Created: {details['created_at']}\n\n"
            "Note: the current database schema stores quiz metadata separately from a fixed question-ID list; "
            "this screen does not claim to reconstruct the exact original question order.",
            reply_markup=quiz_library_keyboard(quiz_id),
        )
    elif action == "rename":
        context.user_data["quiz_library_rename_id"] = quiz_id
        await query.message.reply_text(
            f"Send the new title for quiz #{quiz_id} (1–200 characters). Send /cancel to cancel."
        )


async def quiz_library_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz_id = context.user_data.get("quiz_library_rename_id")
    message = update.effective_message
    if quiz_id is None or not message or not message.text:
        return
    if message.text.strip().lower() == "/cancel":
        context.user_data.pop("quiz_library_rename_id", None)
        await message.reply_text("Quiz rename cancelled.")
        return
    if not await owner_required(update):
        context.user_data.pop("quiz_library_rename_id", None)
        return
    title = message.text.strip()
    if not title or len(title) > 200:
        await message.reply_text("Title must contain between 1 and 200 characters.")
        return
    with SessionLocal() as db:
        quiz = db.query(Quiz).filter(Quiz.id == int(quiz_id)).first()
        if not quiz:
            context.user_data.pop("quiz_library_rename_id", None)
            await message.reply_text("That quiz no longer exists.")
            return
        quiz.title = title
        db.commit()
    context.user_data.pop("quiz_library_rename_id", None)
    await message.reply_text(f"✅ Quiz #{quiz_id} renamed to: {title}")
    await show_quiz_library(message, context)

# ---------------------------------------------------------------------
# Quiz configuration panel (owner-only)
# ---------------------------------------------------------------------


def quiz_settings_keyboard(quiz_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Per-question timer", callback_data=f"qcfg:timer:{quiz_id}")],
        [InlineKeyboardButton("➕ Marks per correct answer", callback_data=f"qcfg:marks:{quiz_id}")],
        [InlineKeyboardButton("➖ Negative marks per wrong answer", callback_data=f"qcfg:negative:{quiz_id}")],
        [InlineKeyboardButton("🕒 Total duration (minutes)", callback_data=f"qcfg:duration:{quiz_id}")],
        [InlineKeyboardButton("🔄 Refresh settings", callback_data=f"qcfg:view:{quiz_id}")],
    ])


async def quiz_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_required(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /quizsettings QUIZ_ID")
        return
    await show_quiz_settings(update.effective_message, context, int(context.args[0]))


async def show_quiz_settings(message, context, quiz_id: int):
    with SessionLocal() as db:
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not quiz:
            await message.reply_text(f"Quiz #{quiz_id} was not found.")
            return
        title = str(quiz.title or f"Quiz #{quiz_id}")
        duration = int(quiz.duration_minutes or 0)
        marks = float(quiz.marks_per_correct or 1.0)
        negative = float(quiz.negative_marks or 0.0)
    sessions = get_sessions(context)
    session = sessions.get(str(quiz_id))
    timer = int(session.get("time_limit_seconds", 30)) if session else None
    timer_text = f"{timer} seconds (active/pending session)" if timer is not None else "Not stored on this quiz record"
    await message.reply_text(
        f"⚙️ Quiz settings — {title}\nQuiz ID: {quiz_id}\n"
        f"Per-question timer: {timer_text}\n"
        f"Marks per correct answer: {marks:g}\n"
        f"Negative marks per wrong answer: {negative:g}\n"
        f"Total duration: {duration} minute(s)\n\n"
        "Choose a setting to change it. Timer and marks are applied to an existing persisted session when available.",
        reply_markup=quiz_settings_keyboard(quiz_id),
    )


async def quiz_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not is_owner(update) or not update.effective_chat or update.effective_chat.type != "private":
        await query.answer("Owner-only control in private chat.", show_alert=True)
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    field, quiz_id = parts[1], int(parts[2])
    if field == "view":
        await show_quiz_settings(query.message, context, quiz_id)
        return
    prompts = {
        "timer": ("Send the per-question timer in seconds (5–600).", "timer"),
        "marks": ("Send marks awarded for a correct answer (0–100).", "marks"),
        "negative": ("Send negative marks for a wrong answer (0–100; use 0 to disable).", "negative"),
        "duration": ("Send total quiz duration in minutes (1–1440).", "duration"),
    }
    if field not in prompts:
        return
    context.user_data["quiz_settings_flow"] = {"quiz_id": quiz_id, "field": prompts[field][1]}
    await query.message.reply_text(prompts[field][0] + " Send /cancel to cancel.")


async def quiz_settings_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("quiz_settings_flow")
    message = update.effective_message
    if not flow or not message or not message.text:
        return
    if not await owner_required(update):
        context.user_data.pop("quiz_settings_flow", None)
        return
    raw = message.text.strip()
    if raw.lower() == "/cancel":
        context.user_data.pop("quiz_settings_flow", None)
        await message.reply_text("Quiz settings change cancelled.")
        return
    try:
        value = float(raw)
    except ValueError:
        await message.reply_text("Please send a valid number, or /cancel.")
        return
    field = flow["field"]
    bounds = {"timer": (5, 600), "marks": (0, 100), "negative": (0, 100), "duration": (1, 1440)}
    low, high = bounds[field]
    if value < low or value > high:
        await message.reply_text(f"Value must be between {low} and {high}.")
        return
    if field in {"timer", "duration"} and not value.is_integer():
        await message.reply_text("This setting must be a whole number.")
        return
    quiz_id = int(flow["quiz_id"])
    with SessionLocal() as db:
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if not quiz:
            context.user_data.pop("quiz_settings_flow", None)
            await message.reply_text("That quiz no longer exists.")
            return
        if field == "marks":
            quiz.marks_per_correct = value
        elif field == "negative":
            quiz.negative_marks = value
        elif field == "duration":
            quiz.duration_minutes = int(value)
        db.commit()
    # A currently persisted session stores the actual question objects and timer.
    # Update that session too, so this setting affects its scoring/timer immediately.
    session = get_sessions(context).get(str(quiz_id))
    if session and not session.get("finalized"):
        if field == "timer":
            session["time_limit_seconds"] = int(value)
        elif field == "marks":
            for item in session.get("questions", []):
                item["marks"] = value
        elif field == "negative":
            for item in session.get("questions", []):
                item["negative"] = value
        persist_live_session(session)
    context.user_data.pop("quiz_settings_flow", None)
    await message.reply_text("✅ Setting saved. Existing active/pending session updated when applicable.")
    await show_quiz_settings(message, context, quiz_id)


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
        await update.effective_message.reply_text(f"✅ Saved '{file_name}' as link #{new_id}. You can manage its name, URL, password and access with /links.")
        await show_saved_links(update.effective_message, context)
    elif step == "rename":
        if not raw or len(raw) > 150:
            await update.effective_message.reply_text("File name must be between 1 and 150 characters.")
            return
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET file_name=:name, updated_at=:now WHERE id=:id"), {"name": raw, "now": datetime.utcnow().isoformat(), "id": flow["link_id"]})
            db.commit()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text("✅ File name updated.")
        record = get_saved_link(flow["link_id"])
        await update.effective_message.reply_text(f"📚 {record['file_name']}", reply_markup=link_keyboard(flow["link_id"], bool(record["enabled"])))
    elif step == "url":
        if not raw.startswith("https://"):
            await update.effective_message.reply_text("For security, the URL must start with https://")
            return
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET url=:url, updated_at=:now WHERE id=:id"), {"url": raw, "now": datetime.utcnow().isoformat(), "id": flow["link_id"]})
            db.commit()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text("✅ URL updated.")
    elif step == "password":
        if len(raw) < 8:
            await update.effective_message.reply_text("Password must be at least 8 characters.")
            return
        salt, digest = hash_link_password(raw)
        with SessionLocal() as db:
            db.execute(text("UPDATE saved_quiz_links SET password_hash=:hash, password_salt=:salt, updated_at=:now WHERE id=:id"), {"hash": digest, "salt": salt, "now": datetime.utcnow().isoformat(), "id": flow["link_id"]})
            db.commit()
        context.user_data.pop("saved_link_flow", None)
        await update.effective_message.reply_text("🔐 Password changed. The bot does not display or store it in plain text.")
    elif step == "delete_confirm":
        if raw.upper() == "YES":
            with SessionLocal() as db:
                db.execute(text("DELETE FROM saved_quiz_links WHERE id=:id"), {"id": flow["link_id"]})
                db.commit()
            await update.effective_message.reply_text("🗑 Saved link deleted from the bot's library.")
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
    link_id = int(context.args[0])
    record = get_saved_link(link_id)
    if not record or not record["enabled"]:
        await update.effective_message.reply_text("⛔ This quiz link is unavailable or disabled.")
        return
    context.user_data["quizlink_password_check"] = link_id
    await update.effective_message.reply_text("🔐 Enter the password provided by the quiz owner. Do not send it in a group.")


async def quizlink_password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_id = context.user_data.get("quizlink_password_check")
    if not link_id or not update.effective_message or not update.effective_message.text:
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    record = get_saved_link(int(link_id))
    supplied = update.effective_message.text.strip()
    context.user_data.pop("quizlink_password_check", None)
    if not record or not record["enabled"] or not link_password_matches(supplied, record["password_salt"], record["password_hash"]):
        await update.effective_message.reply_text("⛔ Incorrect password or link disabled. Ask the owner if you need access.")
        return
    # Track successful verification for this Telegram user. The start callback
    # checks this state too, preventing a forwarded keyboard from bypassing the password.
    unlocked = context.user_data.setdefault("unlocked_quiz_links", [])
    if int(link_id) not in {int(value) for value in unlocked}:
        unlocked.append(int(link_id))
    # Password unlocks the actual Telegram quiz when this saved link is associated
    # with a prepared quiz session. No external website is required for this flow.
    quiz_id = str(record.get("quiz_id") or "").strip()
    quiz_session = get_sessions(context).get(quiz_id) if quiz_id else None
    if quiz_session and quiz_session.get("questions"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Start Telegram Quiz", callback_data=f"pq:start:{int(link_id)}")],
            [InlineKeyboardButton("🔒 Close", callback_data=f"sl:memberclose:{int(link_id)}")],
        ])
        await update.effective_message.reply_text(
            f"✅ Access granted\n📚 {record['file_name']}\n"
            f"📝 Questions: {len(quiz_session['questions'])}\n\n"
            "Tap Start Telegram Quiz to answer privately in this bot chat. "
            "Your answers and score are separate from group polls.",
            reply_markup=keyboard,
        )
        return

    await update.effective_message.reply_text(
        "🔐 Password accepted, but this saved link is not connected to an available quiz session.\n\n"
        "Ask the owner to edit the saved link and enter the correct Quiz ID from /activequizzes. "
        "The external URL is not used as a substitute for a Telegram quiz."
    )


async def send_private_quiz_question(context: ContextTypes.DEFAULT_TYPE, attempt_token: str):
    attempts = context.application.bot_data.setdefault("private_quiz_attempts", {})
    attempt = attempts.get(attempt_token)
    if not attempt or attempt.get("finished"):
        return
    questions = attempt["questions"]
    index = int(attempt["index"])
    if index >= len(questions):
        await finish_private_quiz(context, attempt_token)
        return

    item = questions[index]
    try:
        choices, _correct_display_index = shuffled_option_entries(
            item.get("options", {}), item.get("correct", "")
        )
    except ValueError:
        logger.exception("Invalid option mapping in private quiz attempt %s question %s", attempt_token, index + 1)
        persist_private_attempt(attempt_token, attempt)
        await context.bot.send_message(
            chat_id=attempt["chat_id"],
            text="This question has invalid answer data, so the quiz was stopped to protect scoring accuracy. Please contact the quiz owner.",
        )
        await finish_private_quiz(context, attempt_token)
        return
    attempt["current_choices"] = [letter for letter, _ in choices]
    attempt["answered_current"] = False
    attempt["current_message_id"] = None
    keyboard_rows = []
    for pos, (letter, value) in enumerate(choices):
        keyboard_rows.append([InlineKeyboardButton(
            f"{chr(65 + pos)}. {str(value)[:70]}",
            callback_data=f"pq:ans:{attempt_token}:{index}:{pos}"
        )])
    timer = max(5, min(600, int(attempt.get("timer", 30))))
    msg = await context.bot.send_message(
        chat_id=attempt["chat_id"],
        text=(f"📚 {attempt['title']}\nQuestion {index + 1}/{len(questions)}\n\n"
              f"{item['text']}\n\n⏱ Time: {timer} seconds"),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )
    attempt["current_message_id"] = msg.message_id
    persist_private_attempt(attempt_token, attempt)
    context.application.create_task(private_question_timeout(context, attempt_token, index, timer))


async def private_question_timeout(context: ContextTypes.DEFAULT_TYPE, attempt_token: str, question_index: int, timer: int):
    await asyncio.sleep(timer)
    attempts = context.application.bot_data.setdefault("private_quiz_attempts", {})
    attempt = attempts.get(attempt_token)
    if not attempt or attempt.get("finished") or attempt.get("index") != question_index or attempt.get("answered_current"):
        return
    attempt["answered_current"] = True
    attempt["unanswered"] = int(attempt.get("unanswered", 0)) + 1
    message_id = attempt.get("current_message_id")
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=attempt["chat_id"], message_id=message_id,
                text=f"⏱ Time is up for question {question_index + 1}. No marks awarded."
            )
        except TelegramError:
            pass
    attempt["index"] += 1
    persist_private_attempt(attempt_token, attempt)
    await send_private_quiz_question(context, attempt_token)


async def finish_private_quiz(context: ContextTypes.DEFAULT_TYPE, attempt_token: str):
    attempts = context.application.bot_data.setdefault("private_quiz_attempts", {})
    attempt = attempts.get(attempt_token)
    if not attempt or attempt.get("finished"):
        return
    attempt["finished"] = True
    total = len(attempt["questions"])
    total_possible_marks = sum(float(item.get("marks", 1.0)) for item in attempt["questions"])
    attempted = int(attempt.get("attempted", 0))
    unanswered = max(int(attempt.get("unanswered", 0)), total - attempted)
    correct = int(attempt.get("correct", 0))
    wrong = int(attempt.get("wrong", 0))
    score = float(attempt.get("score", 0.0))
    accuracy = correct / attempted * 100 if attempted else 0.0
    score_percent = score / total_possible_marks * 100 if total_possible_marks else 0.0
    try:
        started_at = datetime.fromisoformat(str(attempt.get("started_at") or datetime.utcnow().isoformat()))
    except ValueError:
        started_at = datetime.utcnow()
    duration = max(0, int((datetime.utcnow() - started_at).total_seconds()))

    # Save private attempts in the same SQL analytics table as group attempts so
    # the overall percentile can include both channels for the same mock.
    if not attempt.get("result_saved"):
        try:
            with SessionLocal() as db:
                quiz = db.query(Quiz).filter(Quiz.id == int(attempt.get("quiz_id"))).first()
                if quiz:
                    feedback = fallback_feedback(correct, wrong, unanswered, accuracy, score)
                    saved_attempt = StudentAttempt(
                        quiz_id=int(attempt["quiz_id"]),
                        student_telegram_id=str(attempt.get("user_id", "")),
                        student_name=str(attempt.get("user_name") or "Student")[:200],
                        group_id="",
                        group_title="Private Telegram",
                        quiz_family_id=str(attempt.get("quiz_family_id") or quiz_family_fingerprint(attempt["questions"])),
                        score=score, correct_count=correct, wrong_count=wrong,
                        unanswered_count=unanswered, started_at=started_at,
                        submitted_at=datetime.utcnow(), duration_seconds=duration,
                        accuracy_percent=accuracy, score_percent=score_percent,
                        group_percentile=None, overall_percentile=None,
                        performance_tagline=feedback["tagline"],
                        performance_insight=feedback["insight"],
                        motivational_quote=feedback["quote"],
                        leaderboard_published=False,
                    )
                    db.add(saved_attempt)
                    db.flush()
                    recalculate_family_percentiles(db, str(attempt.get("quiz_family_id") or quiz_family_fingerprint(attempt["questions"])))
                    db.commit()
                    attempt["result_saved"] = True
                    attempt["overall_percentile"] = saved_attempt.overall_percentile
                    attempt["overall_pool_size"] = db.query(StudentAttempt).filter(
                        StudentAttempt.quiz_family_id == saved_attempt.quiz_family_id
                    ).count()
                    persist_private_attempt(attempt_token, attempt)
                else:
                    logger.error("Cannot save private quiz attempt %s: quiz %s not found", attempt_token, attempt.get("quiz_id"))
        except Exception:
            logger.exception("Failed to save private quiz attempt %s", attempt_token)
            attempt["finished"] = False
            persist_private_attempt(attempt_token, attempt)
            return
    persist_private_attempt(attempt_token, attempt)
    await context.bot.send_message(
        chat_id=attempt["chat_id"],
        text=(f"🏁 QUIZ COMPLETED\n{attempt['title']}\n\n"
              f"Questions: {total}\nAttempted: {attempted}\nUnanswered: {unanswered}\n"
              f"Correct: {correct}\nWrong: {wrong}\n"
              f"Net score: {score:.2f} / {total_possible_marks:g} possible marks\n"
              f"Overall percentile: {float(attempt.get('overall_percentile') or 0.0):.1f}%\n"
              f"Overall comparison pool: {int(attempt.get('overall_pool_size') or 0)} attempt(s)\n\n"
              "Overall percentile compares this same mock across groups and private accounts.\n"
              "Group percentile is not applicable to a private-chat attempt."),
    )


async def private_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not update.effective_user:
        return
    data = query.data or ""
    if not update.effective_chat or update.effective_chat.type != "private":
        await query.answer("Open the bot privately to take this quiz.", show_alert=True)
        return

    parts = data.split(":")
    if len(parts) == 3 and parts[1] == "start":
        await query.answer()
        link_id = int(parts[2])
        record = get_saved_link(link_id)
        if not record or not record["enabled"]:
            await query.message.reply_text("⛔ This quiz link is disabled or unavailable.")
            return
        quiz_id = str(record.get("quiz_id") or "")
        session = get_sessions(context).get(quiz_id)
        if not session or not session.get("questions"):
            await query.message.reply_text("⛔ Quiz session unavailable. Ask the owner to check the saved Quiz ID.")
            return
        # The callback button must not be enough to bypass password verification
        # if the message is forwarded or an old keyboard is reused.
        unlocked = context.user_data.get("unlocked_quiz_links", [])
        if int(link_id) not in {int(value) for value in unlocked}:
            await query.answer("Verify this quiz link's password first.", show_alert=True)
            return
        token = secrets.token_hex(5)
        attempts = context.application.bot_data.setdefault("private_quiz_attempts", {})
        attempts[token] = {
            "user_id": str(update.effective_user.id), "chat_id": update.effective_chat.id,
            "link_id": link_id, "quiz_id": quiz_id, "title": record["file_name"],
            "user_name": str(update.effective_user.full_name or "Student")[:200],
            "quiz_family_id": str(session.get("quiz_family_id") or quiz_family_fingerprint(session["questions"])),
            "started_at": datetime.utcnow().isoformat(timespec="seconds"),
            "questions": list(session["questions"]), "index": 0, "score": 0.0,
            "correct": 0, "wrong": 0, "attempted": 0, "unanswered": 0,
            "timer": int(session.get("time_limit_seconds", 30)), "finished": False,
        }
        persist_private_attempt(token, attempts[token])
        await send_private_quiz_question(context, token)
        return

    if len(parts) == 5 and parts[1] == "ans":
        token = parts[2]
        try:
            question_index, choice_pos = int(parts[3]), int(parts[4])
        except ValueError:
            await query.answer("Invalid answer button.", show_alert=True)
            return
        attempts = context.application.bot_data.setdefault("private_quiz_attempts", {})
        attempt = attempts.get(token)
        if (not attempt or attempt.get("finished")
                or attempt.get("user_id") != str(update.effective_user.id)
                or attempt.get("index") != question_index
                or attempt.get("answered_current")):
            await query.answer("This question is no longer active.", show_alert=True)
            return
        choices = attempt.get("current_choices", [])
        if choice_pos < 0 or choice_pos >= len(choices):
            await query.answer("Invalid option.", show_alert=True)
            return
        await query.answer()
        attempt["answered_current"] = True
        item = attempt["questions"][question_index]
        selected_letter = choices[choice_pos]
        correct_letter = str(item.get("correct", "")).upper()
        attempt["attempted"] += 1
        if selected_letter == correct_letter:
            attempt["score"] += float(item.get("marks", 1.0))
            attempt["correct"] += 1
            result = f"✅ Correct! +{float(item.get('marks', 1.0)):g} marks"
        else:
            attempt["score"] -= float(item.get("negative", 0.25))
            attempt["wrong"] += 1
            result = f"❌ Incorrect. −{float(item.get('negative', 0.25)):g} mark"
        explanation = str(item.get("explanation", "") or "")
        try:
            await query.message.edit_text(
                f"{result}\n\nCorrect answer: {correct_letter}. {item['options'].get(correct_letter, '')}"
                + (f"\n\nExplanation: {explanation}" if explanation else "")
            )
        except TelegramError:
            pass
        attempt["index"] += 1
        persist_private_attempt(token, attempt)
        await send_private_quiz_question(context, token)
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
    ensure_quiz_family_column()
    init_saved_links_table()
    init_question_drafts_table()
    ensure_live_session_table()
    ensure_private_attempt_table()

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
        ("editquestion", edit_question_command),
        ("questioncount", question_count),
        ("testpoll", test_poll),
        ("publishquiz", publish_quiz),
        ("importquiz", import_quiz_command),
        ("importnotes", import_notes_command),
        ("drafts", drafts_command),
        ("quizlibrary", quiz_library_command),
        ("previewquiz", preview_quiz_command),
        ("addquizquestion", add_quiz_question_command),
        ("removequizquestion", remove_quiz_question_command),
        ("quizsettings", quiz_settings_command),
        ("setquiztarget", set_quiz_target),
        ("approvequiz", approve_quiz),
        ("publishquiz_to", publish_quiz_to),
        ("activequizzes", active_quizzes),
        ("finalizequiz", finalize_quiz),
        ("pausequiz", pause_quiz),
        ("resumequiz", resume_quiz),
        ("stopquiz", stop_quiz),
        ("restartquiz", restart_quiz),
        ("leaderboard", leaderboard),
        ("savelink", save_link_command),
        ("links", saved_links_command),
        ("quizlink", member_quizlink_command),
    ]
    for command, callback in handlers:
        app.add_handler(CommandHandler(command, callback))
    # Process link/password manager state before the generic bulk-import text handler.
    app.add_handler(CallbackQueryHandler(private_quiz_callback, pattern=r"^pq:"))
    app.add_handler(CallbackQueryHandler(quiz_library_callback, pattern=r"^qlib:"))
    app.add_handler(CallbackQueryHandler(quiz_settings_callback, pattern=r"^qcfg:"))
    app.add_handler(CallbackQueryHandler(saved_link_callback, pattern=r"^sl:"))
    app.add_handler(CallbackQueryHandler(question_draft_callback, pattern=r"^draft:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, edit_question_text_handler), group=-5)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_settings_text_handler), group=-4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_library_text_handler), group=-3)
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
