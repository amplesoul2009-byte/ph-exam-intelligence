import os
import logging
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
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

# ============================================================
# CONFIGURATION
# ============================================================

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./0xgrey.db",
)

engine_options = {}

if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30,
    }

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    **engine_options,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


# ============================================================
# BASE MODEL
# ============================================================

class Base(DeclarativeBase):
    pass


# ============================================================
# CATEGORY
# ============================================================

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


# ============================================================
# QUESTION
# ============================================================

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


# ============================================================
# QUIZ
# ============================================================

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


# ============================================================
# STUDENT ATTEMPT
# ============================================================

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

    # Display name captured at submission time.
    student_name: Mapped[str] = mapped_column(
        String(200),
        default="Student",
        nullable=False,
    )

    # Telegram group/chat ID. Empty means no group recorded.
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

    # Timing information.
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

    # Calculated results.
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

    # Personalised feedback generated after scoring.
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

    # Indicates whether this attempt has been published.
    leaderboard_published: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )


# ============================================================
# APPROVED GROUPS
# ============================================================

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


# ============================================================
# GROUP ACCESS REQUESTS
# ============================================================

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


# ============================================================
# CUSTOM BOT DISPLAY NAME PER CHAT
# ============================================================

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


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_existing_database():
    """
    Add new StudentAttempt columns to an existing database.

    Existing rows and tables are preserved. New columns use
    nullable or server-default-compatible definitions so old
    records remain readable.
    """

    inspector = inspect(engine)

    if "student_attempts" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("student_attempts")
    }

    # The new columns and their SQL types.
    #
    # Use SQL defaults for populated existing tables. This avoids
    # failures when adding NOT NULL columns to existing records.
    new_columns = {
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
            "VARCHAR(300) NOT NULL "
            "DEFAULT 'Keep practising and improving.'"
        ),
        "performance_insight": (
            "TEXT NOT NULL DEFAULT ''"
        ),
        "motivational_quote": (
            "VARCHAR(500) NOT NULL "
            "DEFAULT 'Consistency creates progress.'"
        ),
        "leaderboard_published": (
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ),
    }

    with engine.begin() as connection:
        for column_name, column_definition in new_columns.items():
            if column_name in existing_columns:
                continue

            statement = (
                f"ALTER TABLE student_attempts "
                f"ADD COLUMN {column_name} {column_definition}"
            )

            logger.info(
                "Adding database column: %s",
                column_name,
            )

            connection.execute(text(statement))

    logger.info("Database migration completed.")


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def init_db():
    """
    Create missing tables and add missing attempt columns.

    This does not delete existing records.
    """

    Base.metadata.create_all(bind=engine)
    migrate_existing_database()

    logger.info("0xGREY database initialized.")


# ============================================================
# DATABASE SESSION HELPER
# ============================================================

def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    init_db()

    print("0xGREY database initialized successfully.")
    print("Existing tables and records are preserved.")
    print("Student attempt analytics fields are available.")
