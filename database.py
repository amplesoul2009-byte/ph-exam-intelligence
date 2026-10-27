"""
SQLAlchemy database models for the 0xGREY Telegram quiz bot.

Compatible with the existing ph-exam-intelligence database schema.
Existing records are preserved; init_db() creates missing tables
and adds missing StudentAttempt analytics columns where possible.
"""

import logging
import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------
# DATABASE CONFIGURATION
# --------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./0xgrey.db",
)

_engine_options = {}

if DATABASE_URL.startswith("sqlite"):
    _engine_options["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30,
    }

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    **_engine_options,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


# --------------------------------------------------
# BASE MODEL
# --------------------------------------------------

class Base(DeclarativeBase):
    pass


# --------------------------------------------------
# CATEGORY MODEL
# --------------------------------------------------

class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )

    description: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    questions: Mapped[list["Question"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
    )


# --------------------------------------------------
# QUESTION MODEL
# --------------------------------------------------

class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"),
        nullable=False,
        index=True,
    )

    question_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    option_a: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    option_b: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    option_c: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    option_d: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    correct_option: Mapped[str] = mapped_column(
        String(1),
        nullable=False,
    )

    explanation: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    difficulty: Mapped[str] = mapped_column(
        String(20),
        default="Moderate",
        nullable=False,
    )

    marks: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        nullable=False,
    )

    negative_marks: Mapped[float] = mapped_column(
        Float,
        default=0.25,
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    category: Mapped["Category"] = relationship(
        back_populates="questions",
    )


# --------------------------------------------------
# QUIZ MODEL
# --------------------------------------------------

class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id"),
        nullable=True,
        index=True,
    )

    question_count: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
    )

    marks_per_correct: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        nullable=False,
    )

    negative_marks: Mapped[float] = mapped_column(
        Float,
        default=0.25,
        nullable=False,
    )

    shuffle_questions: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    shuffle_options: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


# --------------------------------------------------
# IMMUTABLE QUIZ QUESTION ASSIGNMENTS
# --------------------------------------------------

class QuizQuestion(Base):
    """Persist the exact question set and order assigned to a quiz."""
    __tablename__ = "quiz_questions"
    __table_args__ = (
        UniqueConstraint("quiz_id", "question_id", name="uq_quiz_question_pair"),
        UniqueConstraint("quiz_id", "position", name="uq_quiz_question_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


# --------------------------------------------------
# ONE-TIME WEB TEST SESSIONS
# --------------------------------------------------

class WebTestSession(Base):
    """Server-side record for each signed web-test token and its consumption."""
    __tablename__ = "web_test_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"), nullable=False, index=True)
    started_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


# --------------------------------------------------
# STUDENT ATTEMPT MODEL
# --------------------------------------------------

class StudentAttempt(Base):
    __tablename__ = "student_attempts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id"),
        nullable=False,
        index=True,
    )

    student_telegram_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    student_name: Mapped[str] = mapped_column(
        String(200),
        default="Student",
        nullable=False,
    )

    group_id: Mapped[str] = mapped_column(
        String(50),
        default="",
        nullable=False,
        index=True,
    )

    group_title: Mapped[str] = mapped_column(
        String(200),
        default="",
        nullable=False,
    )

    score: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    correct_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    wrong_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    unanswered_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    duration_seconds: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    accuracy_percent: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    score_percent: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    group_percentile: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    overall_percentile: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    performance_tagline: Mapped[str] = mapped_column(
        String(300),
        default="Keep practising and improving.",
        nullable=False,
    )

    performance_insight: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    motivational_quote: Mapped[str] = mapped_column(
        String(500),
        default="Consistency creates progress.",
        nullable=False,
    )

    leaderboard_published: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Fingerprint used by the Telegram bot to compare attempts for the same question set.
    quiz_family_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )


# --------------------------------------------------
# APPROVED GROUP MODEL
# --------------------------------------------------

class ApprovedGroup(Base):
    __tablename__ = "approved_groups"

    group_id: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
    )

    group_title: Mapped[str] = mapped_column(
        String(200),
        default="",
        nullable=False,
    )

    approved_by: Mapped[str] = mapped_column(
        String(50),
        default="",
        nullable=False,
    )

    approved_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )


# --------------------------------------------------
# GROUP ACCESS REQUEST MODEL
# --------------------------------------------------

class GroupAccessRequest(Base):
    __tablename__ = "group_access_requests"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    group_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    group_title: Mapped[str] = mapped_column(
        String(200),
        default="",
        nullable=False,
    )

    requested_by: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    requester_username: Mapped[str] = mapped_column(
        String(100),
        default="",
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    reviewed_by: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


# --------------------------------------------------
# CHAT SETTINGS MODEL
# --------------------------------------------------

class ChatSetting(Base):
    __tablename__ = "chat_settings"

    chat_id: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
    )

    display_name: Mapped[str] = mapped_column(
        String(100),
        default="0xGREY",
        nullable=False,
    )

    updated_by: Mapped[str] = mapped_column(
        String(50),
        default="",
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


# --------------------------------------------------
# DATABASE MIGRATION
# --------------------------------------------------

# Add analytics columns to older student_attempts tables
# without deleting existing rows.

_NEW_ATTEMPT_COLUMNS = {
    "student_name": (
        "VARCHAR(200) NOT NULL DEFAULT 'Student'"
    ),
    "group_id": (
        "VARCHAR(50) NOT NULL DEFAULT ''"
    ),
    "group_title": (
        "VARCHAR(200) NOT NULL DEFAULT ''"
    ),
    "started_at": "TIMESTAMP NULL",
    "duration_seconds": (
        "INTEGER NOT NULL DEFAULT 0"
    ),
    "accuracy_percent": (
        "FLOAT NOT NULL DEFAULT 0"
    ),
    "score_percent": (
        "FLOAT NOT NULL DEFAULT 0"
    ),
    "group_percentile": "FLOAT NULL",
    "overall_percentile": "FLOAT NULL",
    "performance_tagline": (
        "VARCHAR(300) NOT NULL DEFAULT "
        "'Keep practising and improving.'"
    ),
    "performance_insight": (
        "TEXT NOT NULL DEFAULT ''"
    ),
    "motivational_quote": (
        "VARCHAR(500) NOT NULL DEFAULT "
        "'Consistency creates progress.'"
    ),
    "leaderboard_published": (
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ),
    "quiz_family_id": "VARCHAR(64) NULL",
}


def migrate_existing_database():
    """Add missing StudentAttempt fields to an existing database."""

    inspector = inspect(engine)

    if "student_attempts" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns(
            "student_attempts"
        )
    }

    with engine.begin() as connection:
        for column_name, column_definition in (
            _NEW_ATTEMPT_COLUMNS.items()
        ):
            if column_name in existing_columns:
                continue

            statement = (
                "ALTER TABLE student_attempts "
                f"ADD COLUMN {column_name} "
                f"{column_definition}"
            )

            logger.info(
                "Adding database column: %s",
                column_name,
            )

            connection.execute(text(statement))

    logger.info("Database migration completed.")


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

def init_db():
    """Create missing tables and migrate existing attempt columns."""

    Base.metadata.create_all(bind=engine)

    migrate_existing_database()

    logger.info("0xGREY database initialized.")


# --------------------------------------------------
# DATABASE SESSION
# --------------------------------------------------

def get_db():
    """Yield a database session and always close it."""

    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------
# STANDALONE INITIALIZATION
# --------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    init_db()

    print("0xGREY database initialized successfully.")
    print("Existing tables and records are preserved.")
    print("Student attempt analytics fields are available.")
