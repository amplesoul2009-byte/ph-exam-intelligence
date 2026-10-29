
"""Persistent Telegram poll registry for PH Greybot."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from database import Base, engine


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
    # Each character maps a displayed option index to its original A/B/C/D.
    # For example, "CADB" means displayed option 0 is original C.
    option_order: Mapped[str] = mapped_column(
        String(4), default="ABCD", nullable=False
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
    option_order: str = "ABCD",
) -> PollRegistry:
    """Register a sent poll and preserve its displayed option mapping."""

    if not poll_id:
        raise ValueError("Telegram poll ID is required.")

    if attempt_mode not in {"group_quiz", "practice"}:
        raise ValueError("Unsupported attempt mode.")

    option_order = option_order.strip().upper()
    if (
        len(option_order) != 4
        or set(option_order) != set("ABCD")
    ):
        raise ValueError(
            "Option order must contain A, B, C, and D exactly once."
        )

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
        option_order=option_order,
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
