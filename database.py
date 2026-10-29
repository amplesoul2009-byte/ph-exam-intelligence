
"""Database models for PH Greybot / PH Practice Hub."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from config import load_database_url


DATABASE_URL = load_database_url()

engine_options = {"pool_pre_ping": True}

if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {
        "check_same_thread": False
    }

engine = create_engine(DATABASE_URL, **engine_options)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    quiz_date: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="draft", nullable=False
    )
    question_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    questions: Mapped[list["QuizQuestion"]] = relationship(
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="QuizQuestion.position",
    )


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        UniqueConstraint(
            "quiz_id",
            "position",
            name="uq_quiz_question_position",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_option: Mapped[str | None] = mapped_column(String(1))
    explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    marks: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    negative_marks: Mapped[float] = mapped_column(
        Float, default=0.25, nullable=False
    )
    review_status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False
    )
    analysis_report: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )
    timer_seconds: Mapped[int] = mapped_column(
        Integer, default=30, nullable=False
    )

    quiz: Mapped["Quiz"] = relationship(back_populates="questions")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(30), default="text", nullable=False
    )
    original_content: Mapped[str] = mapped_column(Text, nullable=False)
    detected_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    accepted_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    warning_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="analyzing", nullable=False
    )
    report_json: Mapped[str] = mapped_column(
        Text, default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ApprovedGroup(Base):
    __tablename__ = "approved_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    chat_title: Mapped[str] = mapped_column(
        String(200), default="", nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    student_name: Mapped[str] = mapped_column(
        String(200), default="", nullable=False
    )
    group_id: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    correct_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    wrong_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    unanswered_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(
        String(30), default="in_progress", nullable=False
    )

    answers: Mapped[list["AttemptAnswer"]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_question",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_questions.id"),
        nullable=False,
    )
    selected_option: Mapped[str | None] = mapped_column(String(1))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    attempt: Mapped["QuizAttempt"] = relationship(
        back_populates="answers"
    )


def init_db() -> None:
    """Create missing tables; does not delete existing records."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("PH Greybot database tables created successfully.")
