
"""Persistent Telegram poll registry for PH Greybot."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Base, engine
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class PollRegistry(Base):
    __tablename__ = "poll_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False
    )
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_questions.id"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    open: Mapped[bool] = mapped_column(Boolean, default=True)
    attempt_mode: Mapped[str] = mapped_column(
        String(30), default="group_quiz", nullable=False
    )


def init_poll_registry() -> None:
    """Create the registry table if it does not exist."""
    Base.metadata.create_all(bind=engine, tables=[PollRegistry.__table__])


def register_poll(
    db: Session,
    *,
    poll_id: str,
    quiz_id: int,
    question_id: int,
    group_id: int | str,
    message_id: int,
    attempt_mode: str = "group_quiz",
) -> PollRegistry:
    """Register a sent poll so incoming answers can be resolved."""

    if not poll_id:
        raise ValueError("Telegram poll ID is required.")

    if attempt_mode not in {"group_quiz", "practice"}:
        raise ValueError("Unsupported attempt mode.")

    existing = db.scalar(
        select(PollRegistry).where(
            PollRegistry.poll_id == str(poll_id)
        )
    )

    if existing:
        raise ValueError("This Telegram poll ID is already registered.")

    entry = PollRegistry(
        poll_id=str(poll_id),
        quiz_id=quiz_id,
        question_id=question_id,
        group_id=str(group_id),
        message_id=message_id,
        open=True,
        attempt_mode=attempt_mode,
    )

    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_poll(
    db: Session,
    poll_id: str,
) -> PollRegistry | None:
    """Find a poll by its Telegram poll ID."""
    return db.scalar(
        select(PollRegistry).where(
            PollRegistry.poll_id == str(poll_id)
        )
    )


def close_poll(
    db: Session,
    poll_id: str,
) -> bool:
    """Mark a poll closed without deleting its answer records."""

    entry = get_poll(db, poll_id)
    if entry is None:
        return False

    entry.open = False
    db.commit()
    return True
