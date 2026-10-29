
"""Quiz import service for PH Greybot."""

import json
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from database import ImportBatch, Quiz, QuizQuestion
from quiz_analyzer import analyze_import
from quiz_parser import parse_quiz_text


ALLOWED_SUBJECTS = {
    "quant",
    "reasoning",
    "english",
    "computer",
}

ALLOWED_LEVELS = {"prelims", "mains"}


class QuizImportError(ValueError):
    """Raised when an import cannot safely proceed."""


def import_quiz_text(
    db: Session,
    *,
    title: str,
    subject: str,
    level: str,
    quiz_date: str,
    text: str,
    settings: Any,
    max_questions: int = 200,
) -> dict:
    """Parse, analyze, and persist a quiz import.

    This function does not publish quizzes or approve answer keys.
    The caller must enforce owner-only access before invoking it.
    """

    title = title.strip()
    subject = subject.strip().lower()
    level = level.strip().lower()

    if not title:
        raise QuizImportError("Quiz title is required.")

    if len(title) > 200:
        raise QuizImportError("Quiz title cannot exceed 200 characters.")

    if subject not in ALLOWED_SUBJECTS:
        raise QuizImportError(
            "Subject must be quant, reasoning, english, or computer."
        )

    if level not in ALLOWED_LEVELS:
        raise QuizImportError("Level must be prelims or mains.")

    try:
        date.fromisoformat(quiz_date)
    except (TypeError, ValueError):
        raise QuizImportError(
            "Quiz date must use YYYY-MM-DD format."
        ) from None

    if not isinstance(text, str) or not text.strip():
        raise QuizImportError("Quiz text cannot be empty.")

    if len(text) > 1_000_000:
        raise QuizImportError(
            "Import text exceeds the 1,000,000-character limit."
        )

    if not 1 <= max_questions <= 200:
        raise QuizImportError(
            "Question limit must be between 1 and 200."
        )

    parsed = parse_quiz_text(text, max_questions=max_questions)

    if not parsed.questions:
        raise QuizImportError(
            "No numbered questions were detected. "
            + " ".join(parsed.errors)
        )

    # Create a draft. Imported questions are not published automatically.
    quiz = Quiz(
        title=title,
        subject=subject,
        level=level,
        quiz_date=quiz_date,
        status="draft",
        question_count=len(parsed.questions),
    )
    db.add(quiz)
    db.flush()

    batch = ImportBatch(
        quiz_id=quiz.id,
        source_type="text",
        original_content=text,
        detected_count=len(parsed.questions),
        accepted_count=0,
        warning_count=len(parsed.warnings) + len(parsed.errors),
        status="analyzing",
        report_json="{}",
    )
    db.add(batch)
    db.flush()

    # Persist the parsed records without altering their supplied answer keys.
    for position, question in enumerate(parsed.questions, start=1):
        options = question.options

        db.add(
            QuizQuestion(
                quiz_id=quiz.id,
                position=position,
                question_text=question.question.strip(),
                option_a=options.get("A", ""),
                option_b=options.get("B", ""),
                option_c=options.get("C", ""),
                option_d=options.get("D", ""),
                correct_option=question.correct_option,
                explanation=question.explanation,
                marks=1.0,
                negative_marks=0.25,
                review_status="pending",
                analysis_report=json.dumps(
                    {
                        "parser_warnings": question.warnings,
                        "question_number": question.number,
                    },
                    ensure_ascii=False,
                ),
                timer_seconds=30,
            )
        )

    db.flush()

    try:
        report = analyze_import(parsed, settings)

        # Store each AI result alongside its corresponding question.
        for question_record, analysis in zip(
            sorted(
                [
                    q for q in db.new
                    if isinstance(q, QuizQuestion)
                ],
                key=lambda q: q.position,
            ),
            report.get("questions", []),
        ):
            question_record.analysis_report = json.dumps(
                analysis,
                ensure_ascii=False,
            )
            question_record.review_status = analysis.get(
                "review_status", "needs_review"
            )

        batch.status = (
            "analysis_completed"
            if report.get("ai_status") == "completed"
            else "analysis_failed"
        )
        batch.accepted_count = len(parsed.questions)
        batch.warning_count = (
            len(report.get("warnings", []))
            + len(report.get("errors", []))
        )
        batch.report_json = json.dumps(
            report,
            ensure_ascii=False,
        )

    except Exception as exc:
        # Keep the draft and mark all questions for manual review.
        batch.status = "analysis_failed"
        batch.accepted_count = len(parsed.questions)
        batch.report_json = json.dumps(
            {
                "ai_status": "failed",
                "error": str(exc)[:1000],
                "message": (
                    "Questions were saved as a draft. "
                    "Owner review is required."
                ),
            },
            ensure_ascii=False,
        )

        for question_record in db.query(QuizQuestion).filter_by(
            quiz_id=quiz.id
        ):
            question_record.review_status = "needs_review"

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "quiz_id": quiz.id,
        "batch_id": batch.id,
        "title": title,
        "subject": subject,
        "level": level,
        "detected_questions": len(parsed.questions),
        "parser_errors": parsed.errors,
        "parser_warnings": parsed.warnings,
        "analysis_status": batch.status,
        "report": json.loads(batch.report_json or "{}"),
        "published": False,
    }
