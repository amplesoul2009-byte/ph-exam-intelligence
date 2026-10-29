
"""Map Telegram poll participants to their individual quiz attempts."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from database import Base, QuizAttempt
from poll_registry import PollRegistry


class ParticipantPollAttempt(Base):
    __tablename__ = "participant_poll_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    poll_id: Mapped[str] = mapped_column(
        ForeignKey("poll_registry.poll_id"),
        nullable=False,
    )

    student_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_attempts.id"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "poll_id",
            "student_id",
            name="uq_poll_participant_attempt",
        ),
    )


def register_participant_attempt(
    db: Session,
    *,
    poll_id: str,
    student_id: int | str,
    attempt_id: int,
) -> ParticipantPollAttempt:
    """Register which attempt owns a participant's poll answer."""

    poll = db.scalar(
        select(PollRegistry).where(
            PollRegistry.poll_id == str(poll_id)
        )
    )

    if poll is None:
        raise ValueError("Poll has not been registered.")

    attempt = db.get(QuizAttempt, attempt_id)

    if attempt is None:
        raise ValueError("Quiz attempt not found.")

    if attempt.quiz_id != poll.quiz_id:
        raise ValueError("Attempt belongs to a different quiz.")

    if str(attempt.group_id) != str(poll.group_id):
        raise ValueError("Attempt belongs to a different group.")

    student_id = str(student_id)

    if attempt.student_id != student_id:
        raise ValueError("Attempt belongs to a different student.")

    existing = db.scalar(
        select(ParticipantPollAttempt).where(
            ParticipantPollAttempt.poll_id == str(poll_id),
            ParticipantPollAttempt.student_id == student_id,
        )
    )

    if existing is not None:
        if existing.attempt_id != attempt_id:
            raise ValueError(
                "This participant already has an attempt mapped "
                "to this poll."
            )
        return existing

    mapping = ParticipantPollAttempt(
        poll_id=str(poll_id),
        student_id=student_id,
        attempt_id=attempt_id,
    )

    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    return mapping


def get_attempt_id(
    db: Session,
    *,
    poll_id: str,
    student_id: int | str,
) -> int | None:
    """Resolve the exact attempt for an incoming Telegram poll answer."""

    mapping = db.scalar(
        select(ParticipantPollAttempt).where(
            ParticipantPollAttempt.poll_id == str(poll_id),
            ParticipantPollAttempt.student_id == str(student_id),
        )
    )

    return mapping.attempt_id if mapping else None
