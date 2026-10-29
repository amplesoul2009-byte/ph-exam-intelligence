
"""Persistent live-poll tracking for PH Greybot."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import (
    AttemptAnswer,
    Quiz,
    QuizAttempt,
    QuizQuestion,
)


def record_poll_answer(
    db: Session,
    *,
    attempt_id: int,
    question_id: int,
    selected_option: str | None,
) -> dict:
    """Record or update one answer for a question.

    selected_option must be A/B/C/D, or None for an unanswered selection.
    Telegram poll handlers should resolve the user and poll to an attempt
    before calling this function.
    """

    if selected_option is not None:
        selected_option = selected_option.strip().upper()
        if selected_option not in {"A", "B", "C", "D"}:
            raise ValueError("Selected option must be A, B, C, D, or None.")

    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise ValueError("Attempt not found.")

    question = db.get(QuizQuestion, question_id)
    if question is None or question.quiz_id != attempt.quiz_id:
        raise ValueError("Question does not belong to this attempt's quiz.")

    if attempt.status != "in_progress":
        raise ValueError("This attempt is not accepting answers.")

    answer = db.scalar(
        select(AttemptAnswer).where(
            AttemptAnswer.attempt_id == attempt_id,
            AttemptAnswer.question_id == question_id,
        )
    )

    if answer is None:
        answer = AttemptAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
        )
        db.add(answer)

    answer.selected_option = selected_option
    answer.answered_at = datetime.now(timezone.utc)

    if selected_option is None or question.correct_option is None:
        answer.is_correct = None
    else:
        answer.is_correct = (
            selected_option == question.correct_option.upper()
        )

    db.commit()

    return {
        "attempt_id": attempt_id,
        "question_id": question_id,
        "selected_option": selected_option,
        "is_correct": answer.is_correct,
    }


def start_attempt(
    db: Session,
    *,
    quiz_id: int,
    student_id: int | str,
    student_name: str,
    group_id: int | str,
) -> QuizAttempt:
    """Create a new attempt; repeated attempts are preserved separately."""

    quiz = db.get(Quiz, quiz_id)
    if quiz is None:
        raise ValueError("Quiz not found.")

    if quiz.status not in {"approved", "published", "live"}:
        raise ValueError("This quiz is not approved for attempts.")

    attempt = QuizAttempt(
        quiz_id=quiz_id,
        student_id=str(student_id),
        student_name=student_name[:200],
        group_id=str(group_id),
        status="in_progress",
    )

    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def finalize_attempt(
    db: Session,
    *,
    attempt_id: int,
) -> QuizAttempt:
    """Finalize an attempt while retaining all submitted answers."""

    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise ValueError("Attempt not found.")

    if attempt.status in {"submitted", "completed", "finalized"}:
        return attempt

    attempt.status = "submitted"
    attempt.submitted_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(attempt)
    return attempt
