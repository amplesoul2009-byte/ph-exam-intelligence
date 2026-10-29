
"""Quiz import service for PH Practice Hub.

Supports:
- Creating an unpublished quiz draft from numbered text.
- Appending question batches to an existing draft.
- Preserving existing questions when an appended batch fails.
- Reviewing the combined quiz draft and all import-batch findings.
- Exporting all questions into one ordered text document.
- A maximum of 200 questions per quiz.

This module never approves or publishes quizzes.
The calling bot must enforce owner-only access.
"""

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
MAX_QUIZ_QUESTIONS = 200
MAX_IMPORT_CHARACTERS = 1_000_000


class QuizImportError(ValueError):
    """Raised when an import cannot safely proceed."""


def _validate_question_limit(max_questions: int) -> None:
    """Validate a question limit supplied by the caller."""
    if (
        isinstance(max_questions, bool)
        or not isinstance(max_questions, int)
        or not 1 <= max_questions <= MAX_QUIZ_QUESTIONS
    ):
        raise QuizImportError(
            "Question limit must be an integer between 1 and 200."
        )


def _validate_import_text(text: str) -> None:
    """Validate raw question text before parsing."""
    if not isinstance(text, str) or not text.strip():
        raise QuizImportError("Question text cannot be empty.")

    if len(text) > MAX_IMPORT_CHARACTERS:
        raise QuizImportError(
            "Import text exceeds the 1,000,000-character limit."
        )


def _question_record(
    *,
    quiz_id: int,
    position: int,
    question: Any,
    batch_id: int,
    review_status: str = "needs_review",
) -> QuizQuestion:
    """Convert one parsed question into a database record."""
    options = question.options or {}

    return QuizQuestion(
        quiz_id=quiz_id,
        position=position,
        question_text=(question.question or "").strip(),
        option_a=options.get("A", ""),
        option_b=options.get("B", ""),
        option_c=options.get("C", ""),
        option_d=options.get("D", ""),
        correct_option=question.correct_option,
        explanation=question.explanation or "",
        marks=1.0,
        negative_marks=0.25,
        review_status=review_status,
        analysis_report=json.dumps(
            {
                "parser_warnings": question.warnings or [],
                "question_number_in_batch": question.number,
                "import_batch_id": batch_id,
            },
            ensure_ascii=False,
        ),
        timer_seconds=30,
    )


def import_quiz_text(
    db: Session,
    *,
    title: str,
    subject: str,
    level: str,
    quiz_date: str,
    text: str,
    settings: Any,
    max_questions: int = MAX_QUIZ_QUESTIONS,
) -> dict:
    """Parse, analyze, and save the first batch of a new draft.

    The quiz remains unpublished. Automated analysis never grants
    owner approval.
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

    _validate_import_text(text)
    _validate_question_limit(max_questions)

    ensure_default_subjects(db)
    selected_subject = resolve_active_subject(db, subject)

    if selected_subject is None:
        raise QuizImportError(
            f"Unknown or disabled subject: {subject}. "
            "Use /subjects to view available subjects, or "
            "/addsubject NAME to create one."
        )

    subject = selected_subject.subject_id

    if level not in ALLOWED_LEVELS:
        raise QuizImportError("Level must be prelims or mains.")

    try:
        parsed_date = date.fromisoformat(quiz_date)
    except (TypeError, ValueError):
        raise QuizImportError(
            "Quiz date must use YYYY-MM-DD format."
        ) from None

    parsed = parse_quiz_text(
        text,
        max_questions=max_questions,
    )

    if not parsed.questions:
        details = " ".join(parsed.errors)
        raise QuizImportError(
            "No numbered questions were detected."
            + (f" {details}" if details else "")
        )

    if len(parsed.questions) > max_questions:
        raise QuizImportError(
            f"The batch exceeds the limit of {max_questions} questions."
        )

    quiz = Quiz(
        title=title,
        subject=subject,
        level=level,
        # database.py stores quiz_date as a string.
        quiz_date=parsed_date.isoformat(),
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
            warning_count=len(parsed.warnings) + len(parsed.errors),
            status="analyzing",
            report_json="{}",
        )
        db.add(batch)
        db.flush()

        question_records = []

        for position, question in enumerate(
            parsed.questions,
            start=1,
        ):
            record = _question_record(
                quiz_id=quiz.id,
                position=position,
                question=question,
                batch_id=batch.id,
                review_status="pending",
            )
            db.add(record)
            question_records.append(record)

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

            if len(analyses) != len(question_records):
                raise ValueError(
                    "Analysis result count does not match the imported "
                    "question count."
                )

            for record, analysis in zip(
                question_records,
                analyses,
            ):
                if not isinstance(analysis, dict):
                    raise ValueError(
                        "Quiz analyzer returned an invalid question result."
                    )

                record.analysis_report = json.dumps(
                    analysis,
                    ensure_ascii=False,
                )

                review_status = analysis.get(
                    "review_status",
                    "needs_review",
                )

                allowed_statuses = {
                    "pending",
                    "needs_review",
                    "pending_owner_approval",
                    "approved",
                    "rejected",
                }

                if review_status not in allowed_statuses:
                    review_status = "needs_review"

                # Automated analysis must never approve a question.
                if review_status == "approved":
                    review_status = "pending_owner_approval"

                record.review_status = review_status

            ai_status = report.get("ai_status")

            batch.status = (
                "analysis_completed"
                if ai_status in {
                    "completed",
                    "manual_review_required",
                }
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
            # Keep imported questions even if analysis fails.
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
                    "parser_errors": parsed.errors,
                    "parser_warnings": parsed.warnings,
                },
                ensure_ascii=False,
            )

            for record in question_records:
                record.review_status = "needs_review"
                record.analysis_report = json.dumps(
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


def append_quiz_batch(
    db: Session,
    *,
    quiz_id: int,
    text: str,
    max_questions: int = MAX_QUIZ_QUESTIONS,
) -> dict:
    """Append a batch to an existing unpublished draft.

    Appended questions receive continuous positions and are stored
    in the same quiz. Existing batches are not intentionally deleted.
    """
    _validate_import_text(text)
    _validate_question_limit(max_questions)

    parsed = parse_quiz_text(
        text,
        max_questions=max_questions,
    )

    if not parsed.questions:
        details = " ".join(parsed.errors)
        raise QuizImportError(
            "No numbered questions were detected."
            + (f" {details}" if details else "")
        )

    try:
        quiz = (
            db.query(Quiz)
            .filter(Quiz.id == quiz_id)
            .with_for_update()
            .first()
        )

        if quiz is None:
            raise QuizImportError(
                f"Quiz ID {quiz_id} was not found."
            )

        if quiz.status not in {"draft", "draft_saved"}:
            raise QuizImportError(
                "Only an unpublished draft can accept more questions."
            )

        existing_count = (
            db.query(QuizQuestion)
            .filter(QuizQuestion.quiz_id == quiz_id)
            .count()
        )

        incoming_count = len(parsed.questions)

        if existing_count + incoming_count > MAX_QUIZ_QUESTIONS:
            raise QuizImportError(
                "This batch would exceed the 200-question limit. "
                f"Current questions: {existing_count}; "
                f"incoming questions: {incoming_count}."
            )

        batch = ImportBatch(
            quiz_id=quiz_id,
            source_type="text",
            original_content=text,
            detected_count=incoming_count,
            accepted_count=incoming_count,
            warning_count=(
                len(parsed.warnings) + len(parsed.errors)
            ),
            status="needs_owner_review",
            report_json=json.dumps(
                {
                    "source": "appended_text_batch",
                    "parser_errors": parsed.errors,
                    "parser_warnings": parsed.warnings,
                    "message": (
                        "Appended questions require owner review."
                    ),
                },
                ensure_ascii=False,
            ),
        )

        db.add(batch)
        db.flush()

        for offset, question in enumerate(
            parsed.questions,
            start=1,
        ):
            db.add(
                _question_record(
                    quiz_id=quiz_id,
                    position=existing_count + offset,
                    question=question,
                    batch_id=batch.id,
                    review_status="needs_review",
                )
            )

        quiz.question_count = existing_count + incoming_count
        quiz.status = "draft"

        db.commit()

        return {
            "quiz_id": quiz_id,
            "batch_id": batch.id,
            "added_questions": incoming_count,
            "total_questions": quiz.question_count,
            "parser_errors": parsed.errors,
            "parser_warnings": parsed.warnings,
            "published": False,
        }

    except Exception:
        db.rollback()
        raise


def review_quiz_draft(db: Session, quiz_id: int) -> dict:
    """Validate a draft and include findings from all import batches."""
    quiz = (
        db.query(Quiz)
        .filter(Quiz.id == quiz_id)
        .first()
    )

    if quiz is None:
        raise QuizImportError(
            f"Quiz ID {quiz_id} was not found."
        )

    questions = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.position.asc())
        .all()
    )

    problems = []
    seen_positions = set()

    for expected_position, question in enumerate(
        questions,
        start=1,
    ):
        if question.position in seen_positions:
            problems.append(
                f"Duplicate question position: {question.position}"
            )

        seen_positions.add(question.position)

        if question.position != expected_position:
            problems.append(
                f"Question order gap: expected position "
                f"{expected_position}, found {question.position}."
            )

        if not (question.question_text or "").strip():
            problems.append(
                f"Question {expected_position}: missing question text."
            )

        options = {
            "A": question.option_a,
            "B": question.option_b,
            "C": question.option_c,
            "D": question.option_d,
        }

        for label, value in options.items():
            if not (value or "").strip():
                problems.append(
                    f"Question {expected_position}: "
                    f"option {label} is empty."
                )

        answer = (question.correct_option or "").strip().upper()

        if answer not in {"A", "B", "C", "D"}:
            problems.append(
                f"Question {expected_position}: "
                "answer must be A, B, C, or D."
            )

    batches = (
        db.query(ImportBatch)
        .filter(ImportBatch.quiz_id == quiz_id)
        .order_by(ImportBatch.id.asc())
        .all()
    )

    # Read findings stored by the initial import and appended batches.
    report_fields = (
        ("errors", "error"),
        ("error", "analysis error"),
        ("parser_errors", "parser error"),
        ("warnings", "warning"),
        ("parser_warnings", "parser warning"),
    )

    for batch in batches:
        try:
            report = json.loads(batch.report_json or "{}")
        except (TypeError, ValueError):
            report = {}
            problems.append(
                f"Import batch {batch.id}: saved report is invalid JSON."
            )

        if not isinstance(report, dict):
            report = {}
            problems.append(
                f"Import batch {batch.id}: invalid report format."
            )

        for field_name, label in report_fields:
            findings = report.get(field_name, [])

            if isinstance(findings, str):
                findings = [findings]

            if not isinstance(findings, list):
                continue

            for finding in findings:
                finding_text = str(finding).strip()

                if finding_text:
                    problems.append(
                        f"Import batch {batch.id} {label}: "
                        f"{finding_text[:300]}"
                    )

        if batch.status == "analysis_failed":
            problems.append(
                f"Import batch {batch.id}: analysis failed; "
                "manually inspect this batch before approval."
            )

        if batch.accepted_count != batch.detected_count:
            problems.append(
                f"Import batch {batch.id}: accepted count "
                f"({batch.accepted_count}) differs from detected count "
                f"({batch.detected_count})."
            )

    question_count = len(questions)
    within_limit = 0 < question_count <= MAX_QUIZ_QUESTIONS

    if not within_limit:
        problems.append(
            f"Question count must be between 1 and "
            f"{MAX_QUIZ_QUESTIONS}; found {question_count}."
        )

    return {
        "quiz_id": quiz.id,
        "title": quiz.title,
        "status": quiz.status,
        "question_count": question_count,
        "batch_count": len(batches),
        "within_limit": within_limit,
        "problems": problems,
        "ready_for_owner_review": within_limit and not problems,
        "published": False,
    }


def build_combined_quiz_text(
    db: Session,
    quiz_id: int,
) -> str:
    """Build one text document from all questions in order."""
    quiz = (
        db.query(Quiz)
        .filter(Quiz.id == quiz_id)
        .first()
    )

    if quiz is None:
        raise QuizImportError(
            f"Quiz ID {quiz_id} was not found."
        )

    questions = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.position.asc())
        .all()
    )

    if not questions:
        raise QuizImportError(
            "This draft has no saved questions."
        )

    if len(questions) > MAX_QUIZ_QUESTIONS:
        raise QuizImportError(
            "Draft exceeds the 200-question limit."
        )

    # Supports both string dates and date/datetime objects.
    quiz_date = (
        quiz.quiz_date.isoformat()
        if hasattr(quiz.quiz_date, "isoformat")
        else str(quiz.quiz_date)
    )

    lines = [
        "PH PRACTICE HUB — QUIZ DRAFT",
        f"TITLE: {quiz.title}",
        f"SUBJECT: {quiz.subject}",
        f"LEVEL: {quiz.level}",
        f"DATE: {quiz_date}",
        f"TOTAL QUESTIONS: {len(questions)}",
        f"STATUS: {quiz.status}",
        "",
        "=" * 60,
        "",
    ]

    for index, question in enumerate(questions, start=1):
        lines.extend(
            [
                f"{index}. {question.question_text or ''}",
                f"A) {question.option_a or ''}",
                f"B) {question.option_b or ''}",
                f"C) {question.option_c or ''}",
                f"D) {question.option_d or ''}",
                f"Answer: {(question.correct_option or '').upper()}",
                f"Explanation: {question.explanation or ''}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def mark_draft_saved(
    db: Session,
    quiz_id: int,
) -> dict:
    """Mark a draft as saved, without approving or publishing it."""
    quiz = (
        db.query(Quiz)
        .filter(Quiz.id == quiz_id)
        .first()
    )

    if quiz is None:
        raise QuizImportError(
            f"Quiz ID {quiz_id} was not found."
        )

    if quiz.status not in {"draft", "draft_saved"}:
        raise QuizImportError(
            "Only an unpublished draft can be saved."
        )

    question_count = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .count()
    )

    if not 1 <= question_count <= MAX_QUIZ_QUESTIONS:
        raise QuizImportError(
            "The draft must contain between 1 and 200 questions."
        )

    quiz.status = "draft_saved"
    quiz.question_count = question_count
    db.commit()

    return {
        "quiz_id": quiz_id,
        "title": quiz.title,
        "question_count": question_count,
        "status": quiz.status,
        "published": False,
    }
