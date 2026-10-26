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
        "check_same_thread": False
    }

engine = create_engine(
    DATABASE_URL,
    **engine_options,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


# ==================================================
# BASE MODEL
# ==================================================

class Base(DeclarativeBase):
    pass


# ==================================================
# CATEGORIES
# ==================================================

class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    name: Mapped[str] = mapped_column(
        String(100), unique=True, index=True
    )

    description: Mapped[str] = mapped_column(
        Text, default=""
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    questions: Mapped[list["Question"]] = relationship(
        back_populates="category"
    )


# ==================================================
# QUESTION BANK
# ==================================================

class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), index=True
    )

    question_text: Mapped[str] = mapped_column(Text)
    option_a: Mapped[str] = mapped_column(Text)
    option_b: Mapped[str] = mapped_column(Text)
    option_c: Mapped[str] = mapped_column(Text)
    option_d: Mapped[str] = mapped_column(Text)

    correct_option: Mapped[str] = mapped_column(
        String(1)
    )

    explanation: Mapped[str] = mapped_column(
        Text, default=""
    )

    difficulty: Mapped[str] = mapped_column(
        String(20), default="Moderate"
    )

    marks: Mapped[float] = mapped_column(
        Float, default=1.0
    )

    negative_marks: Mapped[float] = mapped_column(
        Float, default=0.25
    )

    active: Mapped[bool] = mapped_column(
        Boolean, default=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    category: Mapped["Category"] = relationship(
        back_populates="questions"
    )


# ==================================================
# QUIZZES
# ==================================================

class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    title: Mapped[str] = mapped_column(
        String(200)
    )

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id"),
        nullable=True,
    )

    question_count: Mapped[int] = mapped_column(
        Integer, default=0
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer, default=20
    )

    marks_per_correct: Mapped[float] = mapped_column(
        Float, default=1.0
    )

    negative_marks: Mapped[float] = mapped_column(
        Float, default=0.25
    )

    shuffle_questions: Mapped[bool] = mapped_column(
        Boolean, default=True
    )

    shuffle_options: Mapped[bool] = mapped_column(
        Boolean, default=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default="draft", index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


# ==================================================
# STUDENT ATTEMPTS
# ==================================================

class StudentAttempt(Base):
    __tablename__ = "student_attempts"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id"), index=True
    )

    student_telegram_id: Mapped[str] = mapped_column(
        String(50), index=True
    )

    score: Mapped[float] = mapped_column(
        Float, default=0.0
    )

    correct_count: Mapped[int] = mapped_column(
        Integer, default=0
    )

    wrong_count: Mapped[int] = mapped_column(
        Integer, default=0
    )

    unanswered_count: Mapped[int] = mapped_column(
        Integer, default=0
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


# ==================================================
# APPROVED TELEGRAM GROUPS
# ==================================================

class ApprovedGroup(Base):
    __tablename__ = "approved_groups"

    # Telegram group IDs are stored as strings.
    group_id: Mapped[str] = mapped_column(
        String(50), primary_key=True
    )

    group_title: Mapped[str] = mapped_column(
        String(200), default=""
    )

    approved_by: Mapped[str] = mapped_column(
        String(50)
    )

    approved_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True
    )


# ==================================================
# GROUP ACCESS REQUESTS
# ==================================================

class GroupAccessRequest(Base):
    __tablename__ = "group_access_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    group_id: Mapped[str] = mapped_column(
        String(50), index=True
    )

    group_title: Mapped[str] = mapped_column(
        String(200), default=""
    )

    requested_by: Mapped[str] = mapped_column(
        String(50)
    )

    requester_username: Mapped[str] = mapped_column(
        String(100), default=""
    )

    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    reviewed_by: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


# ==================================================
# DATABASE INITIALIZATION
# ==================================================

def init_db():
    """
    Create missing database tables.

    Existing tables and their records are preserved.
    """
    Base.metadata.create_all(bind=engine)


def get_db():
    """Provide a database session."""
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    init_db()

    print("0xGREY database initialized successfully.")
    print("Question bank and quiz tables are available.")
    print("Group approval tables are available.")
