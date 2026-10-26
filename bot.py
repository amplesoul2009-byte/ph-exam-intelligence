import os
import json
import time
import random
import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
)

from sqlalchemy import func

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

# ============================================================
# CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("0xGREY")

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
)

OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()

DEFAULT_DISPLAY_NAME = "0xGREY"

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash",
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile",
)

MOTIVATIONAL_QUOTES = [
    "Consistency creates progress.",
    "Every attempt is an opportunity to improve.",
    "Discipline turns preparation into performance.",
    "A mistake understood today becomes strength tomorrow.",
    "Focus on improvement, not perfection.",
    "Strong preparation is built one question at a time.",
    "Keep learning. Keep practising. Keep progressing.",
]


# ============================================================
# GENERAL HELPERS
# ============================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    return bool(
        user
        and OWNER_ID
        and str(user.id) == OWNER_ID
    )


async def owner_required(update: Update) -> bool:
    if is_owner(update):
        return True

    message = update.effective_message

    if message:
        await message.reply_text(
            "⛔ This command is restricted to the bot owner."
        )

    return False


def get_chat_display_name(chat_id: int) -> str:
    with SessionLocal() as db:
        setting = (
            db.query(ChatSetting)
            .filter_by(chat_id=str(chat_id))
            .first()
        )

        if setting and setting.display_name:
            return setting.display_name

    return DEFAULT_DISPLAY_NAME


def safe_display_name(chat_id: int) -> str:
    try:
        return get_chat_display_name(chat_id)
    except Exception:
        logger.exception("Could not load chat display name")
        return DEFAULT_DISPLAY_NAME


def get_approved_group(group_id: str):
    with SessionLocal() as db:
        return (
            db.query(ApprovedGroup)
            .filter(
                ApprovedGroup.group_id == str(group_id),
                ApprovedGroup.active.is_(True),
            )
            .first()
        )


async def can_publish_to(
    update: Update,
    target_chat_id: int,
) -> bool:
    if target_chat_id > 0:
        return True

    if get_approved_group(str(target_chat_id)):
        return True

    message = update.effective_message

    if message:
        await message.reply_text(
            "⛔ Publishing blocked.\n"
            "This group is not approved.\n"
            "Use /approved_groups to check access."
        )

    return False


# ============================================================
# START AND HELP
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    message = update.effective_message

    if chat is None or message is None:
        return

    name = safe_display_name(chat.id)

    await message.reply_text(
        f"🎓 Welcome to {name}!\n\n"
        "🏆 PREMIUM MOCK TEST PLATFORM\n\n"
        "📚 Question bank\n"
        "📝 Interactive Telegram quiz polls\n"
        "🔀 Shuffled options\n"
        "🔐 Owner-approved group publishing\n"
        "📊 Automatic scoring\n"
        "🏆 Premium leaderboard\n"
        "💡 Personalised performance feedback\n\n"
        "Use /help to see commands."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "📚 0xGREY COMMANDS\n\n"
        "GENERAL\n"
        "/start\n"
        "/help\n"
        "/status\n\n"
        "QUESTION BANK — OWNER ONLY\n"
        "/addcategory English\n"
        "/categories\n"
        "/addquestion English\n"
        "/questioncount\n\n"
        "QUIZ PUBLISHING — OWNER ONLY\n"
        "/testpoll\n"
        "/publishquiz English 10\n"
        "/publishquiz_to GROUP_ID 10 English\n\n"
        "QUIZ RESULTS — OWNER ONLY\n"
        "/activequizzes\n"
        "/finalizequiz QUIZ_ID\n"
        "/leaderboard QUIZ_ID\n\n"
        "GROUP ACCESS\n"
        "/request_access\n"
        "/pending_groups\n"
        "/approve_group GROUP_ID\n"
        "/revoke_group GROUP_ID\n"
        "/approved_groups\n\n"
        "CHAT NAME — OWNER ONLY\n"
        "/set_chat_name NAME\n"
        "/get_chat_name\n"
        "/reset_chat_name\n\n"
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


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "✅ 0xGREY is running.\n"
        f"Owner access: {'Yes' if is_owner(update) else 'No'}"
    )


# ============================================================
# CHAT DISPLAY NAME
# ============================================================

async def set_chat_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat or not user or not message:
        return

    name = " ".join(context.args).strip()

    if not name or len(name) > 100:
        await message.reply_text(
            "Usage: /set_chat_name 0xGREY"
        )
        return

    with SessionLocal() as db:
        setting = (
            db.query(ChatSetting)
            .filter_by(chat_id=str(chat.id))
            .first()
        )

        if setting:
            setting.display_name = name
            setting.updated_by = str(user.id)
        else:
            db.add(
                ChatSetting(
                    chat_id=str(chat.id),
                    display_name=name,
                    updated_by=str(user.id),
                )
            )

        db.commit()

    await message.reply_text(
        f"✅ Display name updated to {name}."
    )


async def get_chat_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    chat = update.effective_chat

    if chat:
        await update.effective_message.reply_text(
            f"Display name: {safe_display_name(chat.id)}\n"
            f"Chat ID: {chat.id}"
        )


async def reset_chat_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    chat = update.effective_chat

    if not chat:
        return

    with SessionLocal() as db:
        setting = (
            db.query(ChatSetting)
            .filter_by(chat_id=str(chat.id))
            .first()
        )

        if setting:
            db.delete(setting)
            db.commit()

    await update.effective_message.reply_text(
        f"✅ Default name restored: {DEFAULT_DISPLAY_NAME}"
    )


# ============================================================
# GROUP ACCESS REQUESTS
# ============================================================

async def request_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat or not user or not message:
        return

    if chat.type not in {"group", "supergroup"}:
        await message.reply_text(
            "Run /request_access inside the Telegram group."
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat_id=chat.id,
            user_id=user.id,
        )

        if member.status not in {"administrator", "creator"}:
            await message.reply_text(
                "⛔ Only a group administrator can request access."
            )
            return

    except TelegramError:
        logger.exception("Could not verify group administrator")

        await message.reply_text(
            "Could not verify your administrator status."
        )
        return

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
                "✅ This group is already approved."
            )
            return

        request = (
            db.query(GroupAccessRequest)
            .filter(
                GroupAccessRequest.group_id == str(chat.id),
                GroupAccessRequest.status == "pending",
            )
            .first()
        )

        if request:
            await message.reply_text(
                "⏳ An access request is already pending."
            )
            return

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
        "📨 ACCESS REQUEST SUBMITTED\n\n"
        f"Group: {chat.title or 'Unnamed'}\n"
        f"ID: {chat.id}\n\n"
        "The owner must approve this group."
    )

    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(OWNER_ID),
                text=(
                    "📨 NEW GROUP REQUEST\n\n"
                    f"Group: {chat.title or 'Unnamed'}\n"
                    f"Group ID: {chat.id}\n"
                    f"Requested by: {user.id}\n"
                ),
            )
        except TelegramError:
            logger.exception("Could not notify owner")


async def pending_groups(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    with SessionLocal() as db:
        requests = (
            db.query(GroupAccessRequest)
            .filter(GroupAccessRequest.status == "pending")
            .order_by(GroupAccessRequest.requested_at.asc())
            .all()
        )

        lines = ["📨 PENDING GROUP REQUESTS\n"]

        for request in requests:
            lines.append(
                f"{request.group_title or 'Unnamed'}\n"
                f"ID: {request.group_id}\n"
                f"Use /approve_group {request.group_id}\n"
            )

    await update.effective_message.reply_text(
        "\n".join(lines)
        if requests
        else "No pending group requests."
    )


async def approve_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /approve_group GROUP_ID"
        )
        return

    group_id = context.args[0]

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

        group = (
            db.query(ApprovedGroup)
            .filter(ApprovedGroup.group_id == group_id)
            .first()
        )

        title = request.group_title if request else ""

        if group:
            group.active = True
            group.group_title = title or group.group_title
            group.approved_by = str(update.effective_user.id)
            group.approved_at = datetime.utcnow()
        else:
            db.add(
                ApprovedGroup(
                    group_id=group_id,
                    group_title=title,
                    approved_by=str(update.effective_user.id),
                    active=True,
                )
            )

        if request:
            request.status = "approved"
            request.reviewed_by = str(update.effective_user.id)
            request.reviewed_at = datetime.utcnow()

        db.commit()

    await update.effective_message.reply_text(
        f"✅ Group {group_id} approved."
    )


async def revoke_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /revoke_group GROUP_ID"
        )
        return

    group_id = context.args[0]

    with SessionLocal() as db:
        group = (
            db.query(ApprovedGroup)
            .filter(ApprovedGroup.group_id == group_id)
            .first()
        )

        if not group:
            await update.effective_message.reply_text(
                "Group not found."
            )
            return

        group.active = False
        db.commit()

    await update.effective_message.reply_text(
        f"🔒 Access revoked for {group_id}."
    )


async def approved_groups(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    with SessionLocal() as db:
        groups = (
            db.query(ApprovedGroup)
            .filter(ApprovedGroup.active.is_(True))
            .order_by(ApprovedGroup.group_title.asc())
            .all()
        )

        lines = ["🔐 APPROVED GROUPS\n"]

        for group in groups:
            lines.append(
                f"{group.group_title or 'Unnamed'}\n"
                f"ID: {group.group_id}\n"
            )

    await update.effective_message.reply_text(
        "\n".join(lines)
        if groups
        else "No approved groups."
    )


# ============================================================
# QUESTION BANK
# ============================================================

async def add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    name = " ".join(context.args).strip()

    if not name or len(name) > 100:
        await update.effective_message.reply_text(
            "Usage: /addcategory English"
        )
        return

    with SessionLocal() as db:
        categories = db.query(Category).all()

        if any(
            category.name.casefold() == name.casefold()
            for category in categories
        ):
            await update.effective_message.reply_text(
                "Category already exists."
            )
            return

        db.add(Category(name=name))
        db.commit()

    await update.effective_message.reply_text(
        f"✅ Category created: {name}"
    )


async def list_categories(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    with SessionLocal() as db:
        categories = db.query(Category).order_by(Category.name).all()

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
                f"{category.name}: {count} active questions"
            )

    await update.effective_message.reply_text(
        "\n".join(lines)
        if categories
        else "No categories found."
    )


async def add_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    message = update.effective_message
    lines = (message.text or "").splitlines()

    if not lines:
        return

    first_line = lines[0].split(maxsplit=1)

    if len(first_line) != 2:
        await message.reply_text(
            "Usage:\n"
            "/addquestion English\n"
            "Question: What is 2 + 2?\n"
            "A: 3\nB: 4\nC: 5\nD: 6\n"
            "Correct: B\n"
            "Explanation: Two plus two equals four.\n"
            "Difficulty: Easy"
        )
        return

    category_name = first_line[1].strip()
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
    correct = fields.get("correct", "").upper()
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
            "The question and all four options are required."
        )
        return

    if correct not in {"A", "B", "C", "D"}:
        await message.reply_text(
            "Correct must be A, B, C, or D."
        )
        return

    if difficulty not in {"Easy", "Moderate", "Difficult"}:
        await message.reply_text(
            "Difficulty must be Easy, Moderate, or Difficult."
        )
        return

    with SessionLocal() as db:
        categories = db.query(Category).all()

        category = next(
            (
                item for item in categories
                if item.name.casefold() == category_name.casefold()
            ),
            None,
        )

        if not category:
            await message.reply_text(
                f"Category '{category_name}' does not exist."
            )
            return

        question = Question(
            category_id=category.id,
            question_text=question_text,
            option_a=option_a,
            option_b=option_b,
            option_c=option_c,
            option_d=option_d,
            correct_option=correct,
            explanation=explanation,
            difficulty=difficulty,
            marks=1.0,
            negative_marks=0.25,
            active=True,
        )

        db.add(question)
        db.commit()
        db.refresh(question)

        question_id = question.id

    await message.reply_text(
        "✅ QUESTION SAVED\n\n"
        f"ID: {question_id}\n"
        f"Category: {category_name}\n"
        f"Difficulty: {difficulty}\n"
        "Correct: +1\n"
        "Wrong: -0.25"
    )


async def question_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    with SessionLocal() as db:
        total = (
            db.query(Question)
            .filter(Question.active.is_(True))
            .count()
        )

        categories = db.query(Category).count()

    await update.effective_message.reply_text(
        "📊 QUESTION BANK\n\n"
        f"Categories: {categories}\n"
        f"Active questions: {total}"
    )


# ============================================================
# AI PERFORMANCE FEEDBACK
# ============================================================

def _call_ai_feedback(stats: dict) -> dict:
    """
    Optional synchronous AI request.

    Uses Gemini or Groq when configured. Returns deterministic
    fallback feedback if no provider is configured or the request
    fails. Never fabricate historical performance.
    """

    prompt = f"""
You are a concise exam performance coach for banking exam students.

Use only these verified results:
{json.dumps(stats, ensure_ascii=False)}

Return ONLY valid JSON with exactly these keys:
tagline, insight, quote

Rules:
- Keep the tagline under 100 characters.
- Keep the insight under 350 characters.
- Keep the quote under 180 characters.
- Be constructive and specific to the supplied results.
- Do not invent previous scores, improvement, ranks, or history.
- Do not shame the student.
"""

    try:
        if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
            from google import genai

            client = genai.Client(api_key=GEMINI_API_KEY)

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            content = (response.text or "").strip()

        elif AI_PROVIDER == "groq" and GROQ_API_KEY:
            from groq import Groq

            client = Groq(api_key=GROQ_API_KEY)

            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Return only valid JSON. "
                            "Do not invent statistics."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.6,
            )

            content = response.choices[0].message.content or ""

        else:
            return fallback_feedback(stats)

        content = content.strip()

        if content.startswith("```"):
            content = content.replace("```json", "", 1)
            content = content.replace("```", "").strip()

        data = json.loads(content)

        return {
            "tagline": str(data["tagline"])[:300],
            "insight": str(data["insight"])[:1000],
            "quote": str(data["quote"])[:500],
        }

    except Exception:
        logger.exception("AI feedback failed; using fallback")
        return fallback_feedback(stats)


async def generate_feedback(stats: dict) -> dict:
    return await asyncio.to_thread(
        _call_ai_feedback,
        stats,
    )


def fallback_feedback(stats: dict) -> dict:
    accuracy = float(stats.get("accuracy", 0))
    correct = int(stats.get("correct", 0))
    wrong = int(stats.get("wrong", 0))
    unanswered = int(stats.get("unanswered", 0))

    if accuracy >= 90:
        tagline = "Outstanding accuracy — excellent work!"
        insight = (
            "Your attempted answers were highly accurate. "
            "Keep practising difficult questions."
        )
    elif accuracy >= 75:
        tagline = "Strong performance — keep building momentum."
        insight = (
            "Review the questions you missed and strengthen "
            "the concepts that need more practice."
        )
    elif accuracy >= 50:
        tagline = "Good progress — targeted practice can help."
        insight = (
            "Study the reasoning behind incorrect answers "
            "and focus on accuracy."
        )
    elif correct > 0:
        tagline = "Every correct answer is progress."
        insight = (
            "Identify weaker topics and practise similar questions."
        )
    else:
        tagline = "Your preparation journey starts here."
        insight = (
            "Review the explanations and build your understanding "
            "one topic at a time."
        )

    if unanswered:
        insight += (
            f" {unanswered} questions were unanswered; "
            "review your time management."
        )

    if wrong > correct and wrong:
        insight += (
            " Incorrect answers exceeded correct answers. "
            "Prioritise careful option analysis."
        )

    return {
        "tagline": tagline,
        "insight": insight,
        "quote": random.choice(MOTIVATIONAL_QUOTES),
    }


# ============================================================
# DEMO POLL
# ============================================================

async def test_poll(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    chat = update.effective_chat

    if not chat:
        return

    name = safe_display_name(chat.id)

    await context.bot.send_poll(
        chat_id=chat.id,
        question=f"{name}: What is 2 + 2?",
        options=["3", "4", "5", "6"],
        type="quiz",
        correct_option_id=1,
        is_anonymous=False,
        explanation="Two plus two equals four.",
    )


# ============================================================
# QUIZ SESSION TRACKING
# ============================================================

def get_sessions(context):
    return context.application.bot_data.setdefault(
        "quiz_sessions",
        {},
    )


def get_poll_map(context):
    return context.application.bot_data.setdefault(
        "poll_map",
        {},
    )


def load_questions(category_name: str, count: int):
    with SessionLocal() as db:
        categories = db.query(Category).all()

        category = next(
            (
                item for item in categories
                if item.name.casefold() == category_name.casefold()
            ),
            None,
        )

        if not category:
            return None, None

        questions = (
            db.query(Question)
            .filter(
                Question.category_id == category.id,
                Question.active.is_(True),
            )
            .all()
        )

        if len(questions) < count:
            return category, None

        selected = random.sample(questions, count)

        result = []

        for q in selected:
            result.append({
                "id": q.id,
                "question": q.question_text,
                "options": {
                    "A": q.option_a,
                    "B": q.option_b,
                    "C": q.option_c,
                    "D": q.option_d,
                },
                "correct": q.correct_option.upper(),
                "explanation": q.explanation or "",
            })

        return category, result


# ============================================================
# PUBLISH QUIZ
# ============================================================

async def publish_quiz_to_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_chat_id: int,
    category_name: str,
    count: int,
):
    message = update.effective_message

    if not await can_publish_to(update, target_chat_id):
        return

    category, questions = load_questions(category_name, count)

    if not category:
        await message.reply_text(
            f"Category '{category_name}' not found."
        )
        return

    if questions is None:
        await message.reply_text(
            "Not enough active questions in this category."
        )
        return

    name = safe_display_name(target_chat_id)

    # Create a persistent quiz record.
    with SessionLocal() as db:
        quiz = Quiz(
            title=f"{category.name} | {datetime.utcnow():%Y-%m-%d %H:%M}",
            category_id=category.id,
            question_count=len(questions),
            duration_minutes=max(1, len(questions)),
            marks_per_correct=1.0,
            negative_marks=0.25,
            shuffle_questions=True,
            shuffle_options=True,
            status="published",
        )

        db.add(quiz)
        db.commit()
        db.refresh(quiz)

        quiz_id = quiz.id

    session_id = str(quiz_id)

    session = {
        "quiz_id": quiz_id,
        "title": f"{category.name} Quiz",
        "category": category.name,
        "group_id": target_chat_id,
        "group_title": "",
        "started_at": time.time(),
        "questions": questions,
        "participants": {},
        "finalized": False,
        "poll_ids": [],
    }

    try:
        chat = await context.bot.get_chat(target_chat_id)
        session["group_title"] = chat.title or ""
    except TelegramError:
        session["group_title"] = str(target_chat_id)

    get_sessions(context)[session_id] = session

    await message.reply_text(
        "📣 QUIZ PUBLISHING STARTED\n\n"
        f"Quiz ID: {session_id}\n"
        f"Category: {category.name}\n"
        f"Questions: {len(questions)}\n"
        f"Target: {target_chat_id}\n\n"
        f"After the quiz, use:\n/finalizequiz {session_id}"
    )

    poll_map = get_poll_map(context)

    sent = 0
    failed = 0

    for index, item in enumerate(questions, start=1):
        options = list(item["options"].items())
        random.shuffle(options)

        option_texts = [
            text[:100]
            for _, text in options
        ]

        correct_index = next(
            i
            for i, (letter, _) in enumerate(options)
            if letter == item["correct"]
        )

        poll_question = (
            f"{name} | Q{index}/{len(questions)}: "
            f"{item['question']}"
        )[:300]

        try:
            poll_message = await context.bot.send_poll(
                chat_id=target_chat_id,
                question=poll_question,
                options=option_texts,
                type="quiz",
                correct_option_id=correct_index,
                is_anonymous=False,
                explanation=item["explanation"][:200] or None,
            )

            poll_id = poll_message.poll.id

            poll_map[poll_id] = {
                "session_id": session_id,
                "question_id": item["id"],
                "correct_index": correct_index,
                "question_number": index,
            }

            session["poll_ids"].append(poll_id)
            sent += 1

            await asyncio.sleep(1.1)

        except TelegramError:
            logger.exception("Failed to publish poll")
            failed += 1

    await message.reply_text(
        "🏁 PUBLISHING FINISHED\n\n"
        f"Quiz ID: {session_id}\n"
        f"Published: {sent}\n"
        f"Failed: {failed}\n\n"
        f"Finalize results with:\n/finalizequiz {session_id}"
    )


async def publish_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: /publishquiz English 10"
        )
        return

    try:
        count = int(context.args[-1])
    except ValueError:
        await update.effective_message.reply_text(
            "The final argument must be the question count."
        )
        return

    if not 1 <= count <= 200:
        await update.effective_message.reply_text(
            "Choose between 1 and 200 questions."
        )
        return

    category_name = " ".join(context.args[:-1]).strip()

    await publish_quiz_to_chat(
        update,
        context,
        update.effective_chat.id,
        category_name,
        count,
    )


async def publish_quiz_to(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage: /publishquiz_to GROUP_ID COUNT CATEGORY"
        )
        return

    try:
        group_id = int(context.args[0])
        count = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(
            "Invalid group ID or question count."
        )
        return

    if group_id >= 0:
        await update.effective_message.reply_text(
            "The target must be a group or supergroup ID."
        )
        return

    if not 1 <= count <= 200:
        await update.effective_message.reply_text(
            "Choose between 1 and 200 questions."
        )
        return

    if not get_approved_group(str(group_id)):
        await update.effective_message.reply_text(
            "This group is not approved."
        )
        return

    category_name = " ".join(context.args[2:]).strip()

    await publish_quiz_to_chat(
        update,
        context,
        group_id,
        category_name,
        count,
    )


# ============================================================
# RECEIVE POLL ANSWERS
# ============================================================

async def handle_poll_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    answer = update.poll_answer

    if not answer or not answer.user:
        return

    poll_map = get_poll_map(context)
    metadata = poll_map.get(answer.poll_id)

    if not metadata:
        return

    sessions = get_sessions(context)
    session = sessions.get(metadata["session_id"])

    if not session or session["finalized"]:
        return

    user = answer.user
    user_id = str(user.id)

    participant = session["participants"].setdefault(
        user_id,
        {
            "user_id": user_id,
            "name": user.full_name or "Student",
            "username": user.username or "",
            "answers": {},
        },
    )

    selected = (
        answer.option_ids[0]
        if answer.option_ids
        else None
    )

    participant["answers"][answer.poll_id] = {
        "selected_index": selected,
        "correct_index": metadata["correct_index"],
        "question_id": metadata["question_id"],
    }


# ============================================================
# LIST ACTIVE QUIZZES
# ============================================================

async def active_quizzes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    sessions = get_sessions(context)

    lines = ["📊 ACTIVE QUIZ SESSIONS\n"]

    for session_id, session in sessions.items():
        if session["finalized"]:
            continue

        lines.append(
            f"Quiz ID: {session_id}\n"
            f"Category: {session['category']}\n"
            f"Group: {session['group_title']}\n"
            f"Participants: {len(session['participants'])}\n"
            f"Use /finalizequiz {session_id}\n"
        )

    await update.effective_message.reply_text(
        "\n".join(lines)
        if len(lines) > 1
        else "No active quiz sessions."
    )


# ============================================================
# FINALIZE QUIZ AND SAVE RESULTS
# ============================================================

async def finalize_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /finalizequiz QUIZ_ID"
        )
        return

    session_id = context.args[0]
    session = get_sessions(context).get(session_id)

    if not session:
        await update.effective_message.reply_text(
            "Quiz session not found. Active sessions are held in memory."
        )
        return

    if session["finalized"]:
        await update.effective_message.reply_text(
            "This quiz has already been finalized."
        )
        return

    questions = session["questions"]
    poll_ids = session["poll_ids"]

    results = []

    for participant in session["participants"].values():
        correct = 0
        wrong = 0

        for poll_id in poll_ids:
            answer = participant["answers"].get(poll_id)

            if not answer or answer["selected_index"] is None:
                continue

            if answer["selected_index"] == answer["correct_index"]:
                correct += 1
            else:
                wrong += 1

        total = len(questions)
        attempted = correct + wrong
        unanswered = max(0, total - attempted)

        score = correct - 0.25 * wrong
        accuracy = (
            correct / attempted * 100
            if attempted
            else 0.0
        )

        score_percent = (
            score / total * 100
            if total
            else 0.0
        )

        results.append({
            **participant,
            "correct": correct,
            "wrong": wrong,
            "unanswered": unanswered,
            "attempted": attempted,
            "score": score,
            "accuracy": accuracy,
            "score_percent": score_percent,
        })

    results.sort(
        key=lambda item: (
            item["score"],
            item["accuracy"],
            item["correct"],
        ),
        reverse=True,
    )

    now = datetime.utcnow()
    elapsed = max(
        0,
        int(time.time() - session["started_at"]),
    )

    # Save each participant's result.
    with SessionLocal() as db:
        for result in results:
            stats = {
                "correct": result["correct"],
                "wrong": result["wrong"],
                "unanswered": result["unanswered"],
                "attempted": result["attempted"],
                "accuracy": round(result["accuracy"], 2),
                "score": round(result["score"], 2),
                "question_count": len(questions),
                "quiz_title": session["title"],
            }

            feedback = await generate_feedback(stats)

            attempt = StudentAttempt(
                quiz_id=session["quiz_id"],
                student_telegram_id=result["user_id"],
                student_name=result["name"],
                group_id=str(session["group_id"]),
                group_title=session["group_title"],
                score=result["score"],
                correct_count=result["correct"],
                wrong_count=result["wrong"],
                unanswered_count=result["unanswered"],
                started_at=now,
                submitted_at=now,
                duration_seconds=elapsed,
                accuracy_percent=result["accuracy"],
                score_percent=result["score_percent"],
                performance_tagline=feedback["tagline"],
                performance_insight=feedback["insight"],
                motivational_quote=feedback["quote"],
                leaderboard_published=False,
            )

            db.add(attempt)

        db.commit()

        saved_attempts = (
            db.query(StudentAttempt)
            .filter(
                StudentAttempt.quiz_id == session["quiz_id"],
                StudentAttempt.group_id == str(session["group_id"]),
            )
            .order_by(
                StudentAttempt.score.desc(),
                StudentAttempt.accuracy_percent.desc(),
            )
            .all()
        )

        total_participants = len(saved_attempts)

        for attempt in saved_attempts:
            lower = sum(
                1
                for other in saved_attempts
                if other.score < attempt.score
            )

            attempt.group_percentile = (
                lower / (total_participants - 1) * 100
                if total_participants > 1
                else None
            )

            attempt.leaderboard_published = True

        db.commit()

    session["finalized"] = True

    await update.effective_message.reply_text(
        "✅ QUIZ FINALIZED\n\n"
        f"Quiz ID: {session_id}\n"
        f"Participants recorded: {len(results)}\n"
        "Scores saved to the database.\n\n"
        f"View results with:\n/leaderboard {session_id}"
    )

    await publish_leaderboard(
        update,
        context,
        session["quiz_id"],
        str(session["group_id"]),
    )


# ============================================================
# PREMIUM LEADERBOARD
# ============================================================

async def publish_leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: int,
    group_id: str,
):
    with SessionLocal() as db:
        attempts = (
            db.query(StudentAttempt)
            .filter(
                StudentAttempt.quiz_id == quiz_id,
                StudentAttempt.group_id == str(group_id),
            )
            .order_by(
                StudentAttempt.score.desc(),
                StudentAttempt.accuracy_percent.desc(),
                StudentAttempt.submitted_at.asc(),
            )
            .all()
        )

        quiz = db.get(Quiz, quiz_id)

        if not attempts or not quiz:
            await update.effective_message.reply_text(
                "No saved results are available."
            )
            return

        total_questions = quiz.question_count
        group_title = attempts[0].group_title or str(group_id)

        lines = [
            "🌟🏆 PH MOCK TEST RESULTS 🏆🌟",
            f"📚 {quiz.title}",
            "💎 FINAL SCOREBOARD 💎",
            "",
            "📊 QUIZ SNAPSHOT",
            f"📝 Questions: {total_questions}",
            f"👥 Group: {group_title}",
            f"👤 Participants: {len(attempts)}",
            "➕ Correct: +1",
            "➖ Wrong: -0.25",
            "⭕ Unanswered: 0",
            "",
        ]

        for rank, attempt in enumerate(attempts, start=1):
            attempted = (
                attempt.correct_count + attempt.wrong_count
            )

            percentile = (
                f"{attempt.group_percentile:.1f}%"
                if attempt.group_percentile is not None
                else "N/A"
            )

            lines.extend([
                f"🏅 RANK #{rank} — {attempt.student_name}",
                f"🎯 Score: {attempt.score:.2f}/{total_questions}",
                f"📈 Score Percentage: {attempt.score_percent:.1f}%",
                f"🎯 Accuracy: {attempt.accuracy_percent:.1f}%",
                f"📊 Group Percentile: {percentile}",
                f"✅ Correct: {attempt.correct_count}",
                f"❌ Wrong: {attempt.wrong_count}",
                f"⭕ Unanswered: {attempt.unanswered_count}",
                f"📝 Attempted: {attempted}/{total_questions}",
                f"⏱️ Time: {attempt.duration_seconds // 60}m "
                f"{attempt.duration_seconds % 60}s",
                "",
                f"💡 {attempt.performance_tagline}",
                f"🧠 {attempt.performance_insight}",
                f"✨ “{attempt.motivational_quote}”",
                "",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
            ])

        lines.extend([
            "📌 PERFORMANCE KEY",
            "Accuracy = Correct ÷ Attempted × 100",
            "Score = Correct − (Wrong × 0.25)",
            "",
            "✍️ Created by PH",
        ])

    text = "\n".join(lines)

    # Telegram messages have a maximum message length.
    # Split the leaderboard into safe-sized chunks.
    chunks = []

    while len(text) > 3500:
        split_at = text.rfind("\n", 0, 3500)

        if split_at <= 0:
            split_at = 3500

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    if text:
        chunks.append(text)

    for chunk in chunks:
        await update.effective_message.reply_text(chunk)


async def leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_required(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /leaderboard QUIZ_ID"
        )
        return

    try:
        quiz_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "Quiz ID must be a number."
        )
        return

    session = get_sessions(context).get(str(quiz_id))

    if session:
        group_id = str(session["group_id"])
    else:
        with SessionLocal() as db:
            attempt = (
                db.query(StudentAttempt)
                .filter(StudentAttempt.quiz_id == quiz_id)
                .first()
            )

            if not attempt:
                await update.effective_message.reply_text(
                    "No saved results found for this quiz."
                )
                return

            group_id = attempt.group_id

    await publish_leaderboard(
        update,
        context,
        quiz_id,
        group_id,
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Telegram update processing failed",
        exc_info=context.error,
    )


# ============================================================
# MAIN APPLICATION
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN or BOT_TOKEN."
        )

    if not OWNER_ID.isdigit():
        raise RuntimeError(
            "Set TELEGRAM_OWNER_ID to your numeric Telegram user ID."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # General
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))

    # Chat names
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

    # Group access
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

    # Quiz publishing
    application.add_handler(
        CommandHandler("publishquiz", publish_quiz)
    )
    application.add_handler(
        CommandHandler("publishquiz_to", publish_quiz_to)
    )
    application.add_handler(
        CommandHandler("testpoll", test_poll)
    )

    # Quiz results
    application.add_handler(
        CommandHandler("activequizzes", active_quizzes)
    )
    application.add_handler(
        CommandHandler("finalizequiz", finalize_quiz)
    )
    application.add_handler(
        CommandHandler("leaderboard", leaderboard)
    )

    # Collect non-anonymous Telegram poll answers.
    application.add_handler(
        PollAnswerHandler(handle_poll_answer)
    )

    application.add_error_handler(error_handler)

    logger.info("Starting 0xGREY Telegram bot...")

    application.run_polling()


if __name__ == "__main__":
    main()
