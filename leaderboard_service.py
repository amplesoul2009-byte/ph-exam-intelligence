
"""Leaderboard calculations for PH Greybot / PH Practice Hub.

Scoring:
    Correct answer: +1
    Wrong answer:   -0.25
    Unanswered:      0

Percentiles are calculated from attempts belonging to the same quiz.
The master leaderboard uses each student's best completed attempt.
"""

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import (
    AttemptAnswer,
    Quiz,
    QuizAttempt,
    QuizQuestion,
)


def _utc_datetime(value: datetime | None) -> datetime:
    """Normalize timestamps for duration calculations."""
    if value is None:
        return datetime.now(timezone.utc)

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _time_used(attempt: QuizAttempt) -> int:
    start = _utc_datetime(attempt.started_at)
    end = _utc_datetime(
        attempt.submitted_at
        or datetime.now(timezone.utc)
    )
    return max(0, int((end - start).total_seconds()))


def _percentile(score: float, population: list[float]) -> float:
    """Midrank percentile, from 0 to 100."""
    if not population:
        return 0.0

    below = sum(value < score for value in population)
    tied = sum(value == score for value in population)

    return round(
        ((below + 0.5 * tied) / len(population)) * 100,
        2,
    )


def _score_attempt(
    attempt: QuizAttempt,
    questions: list[QuizQuestion],
    answers_by_question: dict[int, AttemptAnswer],
) -> dict[str, Any]:
    """Recalculate an attempt from stored answers and approved answer keys."""

    total_questions = len(questions)
    correct = 0
    wrong = 0
    unanswered = 0
    attempted = 0
    score = 0.0
    maximum_score = sum(float(q.marks or 1.0) for q in questions)

    missing_keys = [
        q.position
        for q in questions
        if (q.correct_option or "").strip().upper()
        not in {"A", "B", "C", "D"}
    ]

    scoring_valid = not missing_keys

    for question in questions:
        answer = answers_by_question.get(question.id)
        selected = (
            (answer.selected_option or "").strip().upper()
            if answer
            else ""
        )

        if selected not in {"A", "B", "C", "D"}:
            unanswered += 1
            continue

        attempted += 1

        correct_option = (
            (question.correct_option or "").strip().upper()
        )

        # Never publish an apparently valid score with a missing key.
        if correct_option not in {"A", "B", "C", "D"}:
            continue

        if selected == correct_option:
            correct += 1
            score += float(question.marks or 1.0)
        else:
            wrong += 1
            score -= float(question.negative_marks or 0.0)

    accuracy = (
        round((correct / attempted) * 100, 2)
        if attempted
        else 0.0
    )

    score_percentage = (
        round((score / maximum_score) * 100, 2)
        if maximum_score > 0
        else 0.0
    )

    return {
        "attempt_id": attempt.id,
        "quiz_id": attempt.quiz_id,
        "student_id": str(attempt.student_id),
        "student_name": attempt.student_name or "Student",
        "group_id": (
            str(attempt.group_id)
            if attempt.group_id is not None
            else None
        ),
        "score": round(score, 2),
        "net_score": round(score, 2),
        "maximum_score": round(maximum_score, 2),
        "total_questions": total_questions,
        "score_percentage": score_percentage,
        "accuracy": accuracy,
        "correct_count": correct,
        "wrong_count": wrong,
        "unanswered_count": unanswered,
        "attempted_count": attempted,
        "time_used_seconds": _time_used(attempt),
        "status": attempt.status,
        "submitted_at": attempt.submitted_at,
        "overall_percentile": 0.0,
        "group_percentile": 0.0,
        "rank": None,
        "scoring_valid": scoring_valid,
        "missing_answer_keys": missing_keys,
    }


def _rank_rows(
    rows: list[dict[str, Any]],
    percentile_population: list[float] | None = None,
    group_population: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Assign ranks and percentiles without changing the input list."""
    ranked = [dict(row) for row in rows]

    ranked.sort(
        key=lambda row: (
            not row["scoring_valid"],
            -row["score"],
            -row["accuracy"],
            row["time_used_seconds"],
            row["attempt_id"],
        )
    )

    valid_population = (
        percentile_population
        if percentile_population is not None
        else [
            row["score"]
            for row in ranked
            if row["scoring_valid"]
        ]
    )

    valid_group_population = (
        group_population
        if group_population is not None
        else [
            row["score"]
            for row in ranked
            if row["scoring_valid"]
        ]
    )

    previous_key = None
    competition_rank = 0

    for index, row in enumerate(ranked, start=1):
        if not row["scoring_valid"]:
            row["rank"] = None
            row["overall_percentile"] = 0.0
            row["group_percentile"] = 0.0
            continue

        tie_key = (
            row["score"],
            row["accuracy"],
            row["time_used_seconds"],
        )

        if tie_key != previous_key:
            competition_rank = index
            previous_key = tie_key

        row["rank"] = competition_rank
        row["overall_percentile"] = _percentile(
            row["score"], valid_population
        )
        row["group_percentile"] = _percentile(
            row["score"], valid_group_population
        )

    return ranked


def _best_completed_attempts(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one best completed attempt per Telegram user ID."""
    best_by_student: dict[str, dict[str, Any]] = {}

    for row in rows:
        if row["status"] not in {"submitted", "completed", "finalized"}:
            continue

        if not row["scoring_valid"]:
            continue

        student_id = row["student_id"]
        existing = best_by_student.get(student_id)

        candidate_key = (
            row["score"],
            row["accuracy"],
            -row["time_used_seconds"],
        )

        if existing is None:
            best_by_student[student_id] = row
            continue

        existing_key = (
            existing["score"],
            existing["accuracy"],
            -existing["time_used_seconds"],
        )

        if candidate_key > existing_key:
            best_by_student[student_id] = row

    return list(best_by_student.values())


def build_leaderboard(
    db: Session,
    quiz_id: int,
    group_id: str | int,
    current_attempt_id: int | None = None,
) -> dict[str, Any]:
    """Build group, live overall, and master leaderboard data.

    Overall percentile:
        All valid attempts for this quiz across all groups.

    Group percentile:
        Valid attempts for this quiz in the requested group.

    Master leaderboard:
        Best completed attempt per student across all groups.
    """
    quiz = db.get(Quiz, quiz_id)

    if quiz is None:
        raise ValueError(f"Quiz {quiz_id} does not exist.")

    questions = list(
        db.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.position)
        ).all()
    )

    attempts = list(
        db.scalars(
            select(QuizAttempt)
            .where(QuizAttempt.quiz_id == quiz_id)
        ).all()
    )

    attempt_ids = [attempt.id for attempt in attempts]

    answers_by_attempt: dict[int, dict[int, AttemptAnswer]] = defaultdict(dict)

    if attempt_ids:
        answers = db.scalars(
            select(AttemptAnswer).where(
                AttemptAnswer.attempt_id.in_(attempt_ids)
            )
        ).all()

        for answer in answers:
            answers_by_attempt[answer.attempt_id][
                answer.question_id
            ] = answer

    all_rows = [
        _score_attempt(
            attempt,
            questions,
            answers_by_attempt[attempt.id],
        )
        for attempt in attempts
    ]

    valid_overall_scores = [
        row["score"]
        for row in all_rows
        if row["scoring_valid"]
    ]

    target_group = str(group_id)

    group_rows = [
        row
        for row in all_rows
        if row["group_id"] == target_group
    ]

    valid_group_scores = [
        row["score"]
        for row in group_rows
        if row["scoring_valid"]
    ]

    overall_ranked = _rank_rows(
        all_rows,
        percentile_population=valid_overall_scores,
        group_population=valid_overall_scores,
    )

    group_ranked = _rank_rows(
        group_rows,
        percentile_population=valid_overall_scores,
        group_population=valid_group_scores,
    )

    overall_by_attempt = {
        row["attempt_id"]: row
        for row in overall_ranked
    }

    # Ensure group rows retain their quiz-wide percentile and their
    # within-group percentile.
    group_attempts = []
    for row in group_ranked:
        merged = dict(row)
        overall_row = overall_by_attempt.get(row["attempt_id"])

        if overall_row:
            merged["overall_percentile"] = (
                overall_row["overall_percentile"]
            )

        group_attempts.append(merged)

    master_candidates = _best_completed_attempts(all_rows)

    master_best_attempts = _rank_rows(
        master_candidates,
        percentile_population=[
            row["score"]
            for row in master_candidates
        ],
        group_population=[
            row["score"]
            for row in master_candidates
        ],
    )

    current_attempt = next(
        (
            row
            for row in group_attempts
            if row["attempt_id"] == current_attempt_id
        ),
        None,
    )

    return {
        "quiz_id": quiz.id,
        "quiz_title": quiz.title,
        "subject": quiz.subject,
        "total_questions": len(questions),
        "group_id": target_group,
        "group_attempts": group_attempts,
        "overall_attempts": overall_ranked,
        "master_best_attempts": master_best_attempts,
        "current_attempt_id": current_attempt_id,
        "current_attempt": current_attempt,
        "provisional": any(
            row["status"] == "in_progress"
            for row in all_rows
        ),
        "scoring_valid": all(
            row["scoring_valid"] for row in all_rows
        ),
        "missing_answer_keys": sorted(
            {
                position
                for row in all_rows
                for position in row["missing_answer_keys"]
            }
        ),
    }


def _format_time(seconds: int) -> str:
    minutes, remaining_seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"

    return f"{minutes}:{remaining_seconds:02d}"


def render_group_leaderboard(
    data: dict[str, Any],
    limit: int = 30,
) -> str:
    """Render the group leaderboard as Telegram-compatible HTML."""
    quiz_title = escape(str(data.get("quiz_title", "Quiz")))
    group_id = escape(str(data.get("group_id", "Unknown")))
    total_questions = int(data.get("total_questions", 0))

    rows = data.get("group_attempts", [])
    valid_rows = [
        row for row in rows if row.get("scoring_valid", False)
    ]

    valid_rows.sort(
        key=lambda row: (
            row.get("rank") is None,
            row.get("rank") or 10**9,
            -row.get("score", 0.0),
        )
    )

    if not data.get("scoring_valid", True):
        positions = ", ".join(
            str(position)
            for position in data.get("missing_answer_keys", [])
        )
        return (
            "⚠️ <b>LEADERBOARD NOT READY</b>\n"
            f"<b>Quiz:</b> {quiz_title}\n"
            "Some questions do not have valid answer keys.\n"
            f"<b>Question positions:</b> {escape(positions)}\n"
            "Correct the answer keys before publishing scores."
        )

    mode = (
        "LIVE / PROVISIONAL"
        if data.get("provisional")
        else "FINAL"
    )

    lines = [
        f"🏆 <b>GROUP {mode} LEADERBOARD</b>",
        "🪷 <b>PH | PRACTICE HUB</b>",
        f"📝 <b>Quiz:</b> {quiz_title}",
        f"👥 <b>Group ID:</b> <code>{group_id}</code>",
        f"📝 <b>Total Questions:</b> {total_questions}",
        "📌 <b>Marking:</b> +1 correct · −0.25 wrong · 0 unanswered",
        "━━━━━━━━━━━━━━━━━━",
    ]

    medals = ["🥇", "🥈", "🥉"]

    for index, row in enumerate(valid_rows[:max(0, limit)]):
        rank = row.get("rank") or index + 1
        medal = medals[rank - 1] if 1 <= rank <= 3 else f"{rank}."

        name = escape(str(row.get("student_name") or "Student"))
        score = float(row.get("score", 0.0))
        score_percentage = float(row.get("score_percentage", 0.0))
        accuracy = float(row.get("accuracy", 0.0))
        overall_percentile = float(
            row.get("overall_percentile", 0.0)
        )
        group_percentile = float(
            row.get("group_percentile", 0.0)
        )

        correct = int(row.get("correct_count", 0))
        wrong = int(row.get("wrong_count", 0))
        unanswered = int(row.get("unanswered_count", 0))
        attempted = int(row.get("attempted_count", 0))
        time_used = _format_time(
            int(row.get("time_used_seconds", 0))
        )

        lines.extend([
            f"{medal} <b>{name}</b>",
            f"🎯 Net Score: <b>{score:.2f}/{total_questions}</b>",
            (
                f"📊 Score Percentage: {score_percentage:.2f}%"
                f" · 🏅 Overall Percentile: {overall_percentile:.2f}"
            ),
            f"👥 Group Percentile: {group_percentile:.2f}",
            f"🎯 Accuracy: {accuracy:.2f}%",
            f"✅ Correct: {correct} · ❌ Wrong: {wrong}",
            (
                f"📝 Attempted: {attempted}/{total_questions}"
                f" · ⏭️ Unanswered: {unanswered}"
            ),
            f"⏱️ Time Used: {time_used}",
            "━━━━━━━━━━━━━━━━━━",
        ])

    if not valid_rows:
        lines.append("No valid scored attempts are available yet.")

    return "\n".join(lines)
