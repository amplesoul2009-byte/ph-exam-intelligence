
"""Quiz import service for PH Practice Hub."""

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from database import ImportBatch, Quiz, QuizQuestion
from quiz_analyzer import analyze_import
from quiz_parser import parse_quiz_text
from subjects import (
    ensure_default_subjects,
    resolve_active_subject,
)


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
    """Parse, analyze, and save a quiz draft.

    This function does not publish quizzes or approve answer keys.
    The caller must enforce owner-only access before invoking it.
    """

    title = title.strip() if isinstance(title, str) else ""
    subject = subject.strip() if isinstance(subject, str) else ""
    level = level.strip().lower() if isinstance(level, str) else ""

    if not title:
        raise QuizImportError("Quiz title is required.")

    if len(title) > 200:
        raise QuizImportError(
            "Quiz title cannot exceed 200 characters."
        )

    # Resolve the subject from the persistent subject registry.
    # This allows owner-created subjects without editing this file.
    ensure_default_subjects(db)
    selected_subject = resolve_active_subject(db, subject)

    if selected_subject is None:
        raise QuizImportError(
            f"Unknown or disabled subject: {subject}. "
            "Use /subjects to view available subjects, "
            "or /addsubject NAME to create one."
        )

    # Keep the stable subject ID in the quiz record.
    subject = selected_subject.subject_id

    if level not in ALLOWED_LEVELS:
        raise QuizImportError(
            "Level must be prelims or mains."
        )

    try:
        parsed_date = date.fromisoformat(quiz_date)
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

    if (
        isinstance(max_questions, bool)
        or not isinstance(max_questions, int)
        or not 1 <= max_questions <= 200
    ):
        raise QuizImportError(
            "Question limit must be an integer between 1 and 200."
        )

    parsed = parse_quiz_text(
        text,
        max_questions=max_questions,
    )

    if not parsed.questions:
        details = " ".join(parsed.errors)
        message = "No numbered questions were detected."
        if details:
            message += " " + details
        raise QuizImportError(message)

    quiz = Quiz(
        title=title,
        subject=subject,
        level=level,
        quiz_date=parsed_date,
        status="draft",
        question_count=len(parsed.questions),
    )

    try:
        db.add(quiz)
        db.flush()

        batch = ImportBatch(
            quiz_id=quiz.id,
            source_type="text",
            original_content=text,
            detected_count=len(parsed.questions),
            accepted_count=0,
            warning_count=(
                len(parsed.warnings) + len(parsed.errors)
            ),
            status="analyzing",
            report_json="{}",
        )

        db.add(batch)
        db.flush()

        # Keep references to records after flush().
        question_records = []

        for position, question in enumerate(
            parsed.questions,
            start=1,
        ):
            options = question.options or {}

            question_record = QuizQuestion(
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
                        "parser_warnings": (
                            question.warnings or []
                        ),
                        "question_number": question.number,
                    },
                    ensure_ascii=False,
                ),
                timer_seconds=30,
            )

            db.add(question_record)
            question_records.append(question_record)

        db.flush()

        try:
            report = analyze_import(parsed, settings)

            if not isinstance(report, dict):
                raise ValueError(
                    "Quiz analyzer returned an invalid report."
                )

            analyses = report.get("questions", [])

            if not isinstance(analyses, list):
                raise ValueError(
                    "Quiz analyzer returned an invalid questions list."
                )

            # Never associate analysis with the wrong question.
            if len(analyses) != len(question_records):
                raise ValueError(
                    "The number of analysis results does not match "
                    "the number of imported questions."
                )

            for question_record, analysis in zip(
                question_records,
                analyses,
            ):
                if not isinstance(analysis, dict):
                    raise ValueError(
                        "Quiz analyzer returned an invalid question result."
                    )

                question_record.analysis_report = json.dumps(
                    analysis,
                    ensure_ascii=False,
                )

                review_status = analysis.get(
                    "review_status",
                    "needs_review",
                )

                if review_status not in {
                    "pending",
                    "needs_review",
                    "pending_owner_approval",
                    "approved",
                    "rejected",
                }:
                    review_status = "needs_review"

                # Analysis alone must never approve a question.
                if review_status == "approved":
                    review_status = "pending_owner_approval"

                question_record.review_status = review_status

            analysis_status = report.get("ai_status")

            batch.status = (
                "analysis_completed"
                if analysis_status == "completed"
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
            # Keep the draft and questions if analysis fails.
            batch.status = "analysis_failed"
            batch.accepted_count = len(parsed.questions)
            batch.warning_count = (
                len(parsed.warnings)
                + len(parsed.errors)
                + 1
            )

            batch.report_json = json.dumps(
                {
                    "ai_status": "failed",
                    "error": str(exc)[:1000],
                    "message": (
                        "Questions were saved as a draft. "
                        "Owner review is required before publication."
                    ),
                },
                ensure_ascii=False,
            )

            for question_record in question_records:
                question_record.review_status = "needs_review"
                question_record.analysis_report = json.dumps(
                    {
                        "status": "needs_review",
                        "message": (
                            "Analysis did not complete successfully."
                        ),
                    },
                    ensure_ascii=False,
                )

        db.commit()

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

    except Exception:
        db.rollback()
        raise
