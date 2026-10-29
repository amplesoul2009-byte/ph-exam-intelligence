
"""Scoring and percentile calculations for PH Greybot."""

from dataclasses import dataclass, asdict
from math import isfinite
from typing import Iterable, Optional


DEFAULT_MARKS = 1.0
DEFAULT_NEGATIVE_MARKS = 0.25


@dataclass(frozen=True)
class ScoreCard:
    total_questions: int
    correct: int
    wrong: int
    unanswered: int
    attempted: int
    net_score: float
    maximum_score: float
    score_percentage: float
    accuracy: float
    time_used_seconds: int

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_score(
    *,
    total_questions: int,
    correct: int,
    wrong: int,
    unanswered: Optional[int] = None,
    marks_per_correct: float = DEFAULT_MARKS,
    negative_marks: float = DEFAULT_NEGATIVE_MARKS,
    time_used_seconds: int = 0,
) -> ScoreCard:
    """Calculate a score without rounding intermediate values."""

    if total_questions < 0:
        raise ValueError("Total questions cannot be negative.")

    if min(correct, wrong, time_used_seconds) < 0:
        raise ValueError("Counts and time cannot be negative.")

    if not all(
        isfinite(value) and value >= 0
        for value in (marks_per_correct, negative_marks)
    ):
        raise ValueError("Marking values must be finite and non-negative.")

    attempted = correct + wrong

    if attempted > total_questions:
        raise ValueError("Correct plus wrong exceeds total questions.")

    calculated_unanswered = total_questions - attempted

    if unanswered is not None and unanswered != calculated_unanswered:
        raise ValueError(
            "Unanswered count does not match total minus attempted."
        )

    unanswered = calculated_unanswered
    maximum_score = total_questions * marks_per_correct
    net_score = (
        correct * marks_per_correct
        - wrong * negative_marks
    )

    score_percentage = (
        net_score / maximum_score * 100
        if maximum_score > 0
        else 0.0
    )

    accuracy = (
        correct / attempted * 100
        if attempted > 0
        else 0.0
    )

    return ScoreCard(
        total_questions=total_questions,
        correct=correct,
        wrong=wrong,
        unanswered=unanswered,
        attempted=attempted,
        net_score=round(net_score, 4),
        maximum_score=maximum_score,
        score_percentage=round(score_percentage, 4),
        accuracy=round(accuracy, 4),
        time_used_seconds=time_used_seconds,
    )


def percentile_rank(
    score: float,
    peer_scores: Iterable[float],
) -> float:
    """Calculate midrank percentile from 0 to 100.

    Equal scores receive the same percentile.
    Scores are compared against the supplied peer population,
    including the participant whose score is being calculated.

    Formula:
        (number below + 0.5 * number tied) / population * 100
    """

    if not isfinite(score):
        raise ValueError("Score must be finite.")

    scores = list(peer_scores)

    if not scores:
        return 0.0

    if any(not isfinite(value) for value in scores):
        raise ValueError("Peer scores must all be finite.")

    below = sum(value < score for value in scores)
    tied = sum(value == score for value in scores)

    return round((below + 0.5 * tied) / len(scores) * 100, 1)


def calculate_live_percentiles(
    *,
    student_score: float,
    overall_scores: Iterable[float],
    group_scores: Iterable[float],
) -> dict:
    """Calculate overall and current-group live percentiles."""

    overall = list(overall_scores)
    group = list(group_scores)

    return {
        "overall_percentile": percentile_rank(
            student_score, overall
        ),
        "group_percentile": percentile_rank(
            student_score, group
        ),
        "overall_participants": len(overall),
        "group_participants": len(group),
        "provisional": True,
    }


def format_time(seconds: int) -> str:
    """Format elapsed time as M:SS or H:MM:SS."""

    if seconds < 0:
        raise ValueError("Time cannot be negative.")

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    return f"{minutes}:{seconds:02d}"


def rank_attempts(attempts: list[dict]) -> list[dict]:
    """Sort attempts by score, accuracy, then faster completion.

    Expected fields: net_score, accuracy, time_used_seconds.
    Every attempt remains a separate row.
    """

    ranked = sorted(
        attempts,
        key=lambda item: (
            -float(item.get("net_score", 0)),
            -float(item.get("accuracy", 0)),
            int(item.get("time_used_seconds", 0)),
        ),
    )

    result = []
    previous_key = None
    competition_rank = 0

    for position, attempt in enumerate(ranked, start=1):
        key = (
            float(attempt.get("net_score", 0)),
            float(attempt.get("accuracy", 0)),
            int(attempt.get("time_used_seconds", 0)),
        )

        if key != previous_key:
            competition_rank = position
            previous_key = key

        result.append({
            **attempt,
            "rank": competition_rank,
        })

    return result
