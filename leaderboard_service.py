
"""Leaderboard rendering and ranking service for PH Greybot."""

from collections import defaultdict
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import AttemptAnswer, Quiz, QuizAttempt, QuizQuestion
from quiz_scoring import (
    calculate_live_percentiles,
    calculate_score,
    format_time,
)


def _student_name(name: str | None, student_id: str) -> str:
    name = (name or "").strip()
    return escape(name or f"Student {student_id}")


def _attempt_time(attempt: QuizAttempt) -> int:
    """Calculate elapsed seconds, including completed attempts."""
    if not attempt.started_at:
        return 0

    end = attempt.submitted_at

    if end is None:
        from datetime import datetime, timezone
        end = datetime.now(timezone.utc)

    start = attempt.started_at

    # SQLite may return naive datetimes even for timezone-aware columns.
    if start.tzinfo is None:
        from datetime import timezone
        start = start.replace(tzinfo=timezone.utc)

    if end.tzinfo is None:
        from datetime import timezone
        end = end.replace(tzinfo=timezone.utc)

    return max(0, int((end - start).total_seconds()))


def _score_attempt(
    attempt: QuizAttempt,
    questions: list[QuizQuestion],
) -> dict:
    """Recalculate scores from saved answers rather than trusting stored totals."""

    answer_map = {
        answer.question_id: answer
        for answer in attempt.answers
    }

    correct = 0
    wrong = 0

    for question in questions:
        answer = answer_map.get(question.id)

        if answer is None or answer.selected_option is None:
            continue

        if question.correct_option is None:
            # Do not count answers against an unapproved/missing answer key.
            continue

        if answer.selected_option.upper() == question.correct_option.upper():
            correct += 1
        else:
            wrong += 1

    unanswered = max(0, len(questions) - correct - wrong)

    scorecard = calculate_score(
        total_questions=len(questions),
        correct=correct,
        wrong=wrong,
        unanswered=unanswered,
        marks_per_correct=1.0,
        negative_marks=0.25,
        time_used_seconds=_attempt_time(attempt),
    )

    return {
        **scorecard.to_dict(),
        "attempt_id": attempt.id,
        "quiz_id": attempt.quiz_id,
        "student_id": attempt.student_id,
        "student_name": attempt.student_name,
        "group_id": attempt.group_id,
        "status": attempt.status,
    }


def _load_attempts(
    db: Session,
    quiz_id: int,
    group_id: str | int | None = None,
) -> tuple[Quiz, list[dict]]:
    quiz = db.scalar(
        select(Quiz)
        .where(Quiz.id == quiz_id)
        .options(selectinload(Quiz.questions))
    )

    if quiz is None:
        raise ValueError(f"Quiz {quiz_id} was not found.")

    statement = (
        select(QuizAttempt)
        .where(QuizAttempt.quiz_id == quiz_id)
        .options(selectinload(QuizAttempt.answers))
    )

    if group_id is not None:
        statement = statement.where(
            QuizAttempt.group_id == str(group_id)
        )

    attempts = db.scalars(statement).all()

    results = [
        _score_attempt(attempt, quiz.questions)
        for attempt in attempts
    ]

    return quiz, results


def _percentile_for_attempt(
    attempt: dict,
    population: list[dict],
) -> float:
    scores = [
        row["net_score"]
        for row in population
        if row["attempt_id"] != attempt["attempt_id"]
        or row["student_id"] == attempt["student_id"]
    ]

    result = calculate_live_percentiles(
        student_score=attempt["net_score"],
        overall_scores=scores,
        group_scores=scores,
    )

    return result["overall_percentile"]


def build_leaderboard(
    db: Session,
    *,
    quiz_id: int,
    group_id: str | int,
    current_attempt_id: int | None = None,
) -> dict:
    """Build a group board and same-quiz overall board.

    Overall comparisons include attempts across groups. Each attempt remains
    independently recorded; repeated attempts are not deleted or overwritten.
    """

    quiz, all_attempts = _load_attempts(db, quiz_id)
    group_id = str(group_id)

    group_attempts = [
        row for row in all_attempts
        if row["group_id"] == group_id
    ]

    # Only submitted/completed attempts are included in the final master board.
    completed_overall = [
        row for row in all_attempts
        if row["status"] in {"submitted", "completed", "finalized"}
    ]

    live_overall = [
        row for row in all_attempts
        if row["status"] in {
            "in_progress", "submitted", "completed", "finalized"
        }
    ]

    def rank_key(row):
        return (
            -row["net_score"],
            -row["accuracy"],
            row["time_used_seconds"],
        )

    group_ranked = sorted(group_attempts, key=rank_key)
    overall_ranked = sorted(completed_overall, key=rank_key)

    # The group percentile is measured against all currently recorded
    # attempts in this group. The overall percentile spans approved groups.
    for row in group_ranked:
        row["group_percentile"] = _percentile_for_attempt(
            row, group_attempts
        )

    for row in live_overall:
        row["overall_percentile"] = _percentile_for_attempt(
            row, live_overall
        )

    # Optional best-attempt view: one best completed attempt per student.
    best_by_student = {}
    for row in overall_ranked:
        key = row["student_id"]
        existing = best_by_student.get(key)
        if existing is None or rank_key(row) < rank_key(existing):
            best_by_student[key] = row

    master_best = sorted(best_by_student.values(), key=rank_key)

    return {
        "quiz_id": quiz.id,
        "title": quiz.title,
        "total_questions": len(quiz.questions),
        "group_id": group_id,
        "group_attempts": group_ranked,
        "overall_attempts": sorted(live_overall, key=rank_key),
        "master_best_attempts": master_best,
        "current_attempt_id": current_attempt_id,
        "provisional": any(
            row["status"] == "in_progress"
            for row in live_overall
        ),
    }


def render_group_leaderboard(
    data: dict,
    *,
    limit: int = 30,
) -> str:
    """Render the requested PH-style leaderboard for a Telegram group."""

    total = data["total_questions"]
    group_id = escape(str(data["group_id"]))
    title = escape(data["title"])
    rows = data["group_attempts"][:limit]
    overall = data["overall_attempts"]

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        "🏆 <b>GRAMMAR FINAL LEADERBOARD</b>",
        "🪷 <b>PH | CURRENT AFFAIRS INTELLIGENCE</b>",
        "👥 Group ID:",
        f"<code>{group_id}</code>",
        "",
        f"📝 Mock: <b>{title}</b>",
        f"📝 Total Questions: {total}",
        "📌 Marking: +1 correct · −0.25 wrong · 0 unanswered",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if data.get("provisional"):
        lines.insert(0, "🟡 <b>LIVE RESULTS — PROVISIONAL</b>")

    for index, row in enumerate(rows, start=1):
        name = _student_name(
            row.get("student_name"),
            str(row["student_id"]),
        )

        # Find the same attempt in the overall population.
        overall_row = next(
            (
                candidate for candidate in overall
                if candidate["attempt_id"] == row["attempt_id"]
            ),
            None,
        )
        overall_percentile = (
            overall_row.get("overall_percentile", 0.0)
            if overall_row else 0.0
        )

        medal = medals[index - 1] if index <= 3 else f"{index}."
        time_used = format_time(row["time_used_seconds"])

        lines.extend([
            f"{medal} <b>{name}</b>",
            f"🎯 Net Score: <b>{row['net_score']:.2f}/{total}</b>",
            f"📊 Score Percentage: {row['score_percentage']:.2f}%",
            f"🌐 Overall Percentile: {overall_percentile:.1f}",
            f"👥 Group Percentile: {row.get('group_percentile', 0.0):.1f}",
            f"🎯 Accuracy: {row['accuracy']:.2f}%",
            f"✅ Correct: {row['correct']} · ❌ Wrong: {row['wrong']}",
            f"📝 Attempted: {row['attempted']}/{total} · "
            f"⏭️ Unanswered: {row['unanswered']}",
            f"⏱️ Time Used: {time_used}",
            "━━━━━━━━━━━━━━━━━━",
        ])

    if not rows:
        lines.append("No recorded attempts yet.")

    lines.append("✍️ Created by Ph")
    return "\n".join(lines)
