import os
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
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)


# ==================================================
# DATABASE CONFIGURATION
# ==================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./0xgrey.db",
)

engine_options = {}

if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {
        "check_same_thread": False,
    }

engine = create_engine(
    DATABASE_URL,
    **engine_options,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ==================================================
# BASE MODEL
# ==================================================

class Base(DeclarativeBase):
    pass


# ==================================================
# CATEGORY
# ==================================================

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


# ==================================================
# QUESTION
# ==================================================

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


# ==================================================
# QUIZ
# ==================================================

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
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


# ==================================================
# STUDENT ATTEMPT
# ==================================================

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

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


# ==================================================
# APPROVED GROUPS
# ==================================================

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


# ==================================================
# GROUP ACCESS REQUESTS
# ==================================================

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


# ==================================================
# CUSTOM BOT DISPLAY NAME PER CHAT
# ==================================================

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


# ==================================================
# INITIALIZE DATABASE
# ==================================================

def init_db():
    """Create any missing tables without deleting existing data."""

    Base.metadata.create_all(bind=engine)


# ==================================================
# DATABASE SESSION HELPER
# ==================================================

def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# ==================================================
# DIRECT EXECUTION
# ==================================================

if __name__ == "__main__":
    init_db()

    print("✅ Database initialized successfully.")
    print("✅ Existing database tables are preserved.")
    print("✅ Custom chat-name settings are available.")
