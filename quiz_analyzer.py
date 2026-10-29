
"""Conservative quiz-import analysis for PH Practice Hub.

This module checks parsed questions without inventing answer keys.
Questions must be reviewed before publication.
"""

from __future__ import annotations


def analyze_import(parsed, settings) -> dict:
    """Validate parsed quiz questions and prepare a review report."""

    questions = []
    warnings = []

    for item in parsed.questions:
        question_text = getattr(item, "question", "") or ""
        options = getattr(item, "options", {}) or {}
        correct_option = getattr(item, "correct_option", None)
        item_warnings = list(getattr(item, "warnings", []) or [])
        question_number = getattr(item, "number", "?")

        valid = (
            bool(question_text.strip())
            and set(options.keys()) == set("ABCD")
            and correct_option in set("ABCD")
            and not item_warnings
        )

        if not valid:
            warnings.append(
                f"Question {question_number} needs manual review."
            )

        questions.append({
            "question_number": question_number,
            "review_status": (
                "needs_review"
                if not valid
                else "pending_owner_approval"
            ),
            "answer_key_present": correct_option in set("ABCD"),
            "warnings": item_warnings,
        })

    return {
        "ai_status": "manual_review_required",
        "message": (
            "The imported text was structurally checked. "
            "The owner must verify answer keys and explanations "
            "before publishing."
        ),
        "questions": questions,
        "warnings": warnings,
        "errors": list(getattr(parsed, "errors", []) or []),
    }
