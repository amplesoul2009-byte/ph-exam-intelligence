
"""Tests for the PH Practice Hub quiz import workflow."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, ImportBatch, Quiz, QuizQuestion
from quiz_import_service import (
    QuizImportError,
    append_quiz_batch,
    build_combined_quiz_text,
    import_quiz_text,
    mark_draft_saved,
    review_quiz_draft,
)


VALID_QUESTION = """1. What is 2 + 2?
A) 3
B) 4
C) 5
D) 6
Answer: B
Explanation: Two plus two equals four."""


class QuizImportServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )
        self.db = self.Session()

        self.analysis_patch = patch(
            "quiz_import_service.analyze_import",
            side_effect=self.fake_analysis,
        )
        self.analysis_patch.start()

    def tearDown(self):
        self.analysis_patch.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def fake_analysis(parsed, settings):
        return {
            "ai_status": "completed",
            "questions": [
                {"review_status": "pending_owner_approval"}
                for _ in parsed.questions
            ],
            "warnings": [],
            "errors": [],
        }

    def create_draft(self):
        return import_quiz_text(
            self.db,
            title="Test Quiz",
            subject="quant",
            level="prelims",
            quiz_date="2026-10-29",
            text=VALID_QUESTION,
            settings=None,
        )

    def test_import_creates_unpublished_draft(self):
        result = self.create_draft()

        quiz = self.db.get(Quiz, result["quiz_id"])
        questions = (
            self.db.query(QuizQuestion)
            .filter_by(quiz_id=quiz.id)
            .all()
        )

        self.assertEqual(quiz.status, "draft")
        self.assertEqual(quiz.question_count, 1)
        self.assertEqual(len(questions), 1)
        self.assertFalse(result["published"])

    def test_append_batch_keeps_continuous_positions(self):
        result = self.create_draft()

        second_question = VALID_QUESTION.replace(
            "1. What is 2 + 2?",
            "2. What is 3 + 3?",
        ).replace(
            "B) 4",
            "B) 6",
        ).replace(
            "C) 5",
            "C) 7",
        ).replace(
            "D) 6",
            "D) 8",
        )

        appended = append_quiz_batch(
            self.db,
            quiz_id=result["quiz_id"],
            text=second_question,
        )

        questions = (
            self.db.query(QuizQuestion)
            .filter_by(quiz_id=result["quiz_id"])
            .order_by(QuizQuestion.position)
            .all()
        )

        self.assertEqual(appended["total_questions"], 2)
        self.assertEqual(
            [question.position for question in questions],
            [1, 2],
        )

    def test_review_checks_all_batches(self):
        result = self.create_draft()

        append_quiz_batch(
            self.db,
            quiz_id=result["quiz_id"],
            text=VALID_QUESTION.replace(
                "1. What is 2 + 2?",
                "2. What is 3 + 3?",
            ),
        )

        review = review_quiz_draft(
            self.db,
            result["quiz_id"],
        )

        self.assertEqual(review["question_count"], 2)
        self.assertEqual(review["batch_count"], 2)
        self.assertTrue(review["ready_for_owner_review"])
        self.assertFalse(review["published"])

    def test_save_draft_does_not_approve_or_publish(self):
        result = self.create_draft()

        saved = mark_draft_saved(
            self.db,
            result["quiz_id"],
        )

        quiz = self.db.get(Quiz, result["quiz_id"])

        self.assertEqual(saved["status"], "draft_saved")
        self.assertEqual(quiz.status, "draft_saved")
        self.assertFalse(saved["published"])

    def test_combined_text_contains_question_and_answer(self):
        result = self.create_draft()

        combined = build_combined_quiz_text(
            self.db,
            result["quiz_id"],
        )

        self.assertIn("What is 2 + 2?", combined)
        self.assertIn("Answer: B", combined)
        self.assertIn("TOTAL QUESTIONS: 1", combined)

    def test_cannot_append_to_approved_quiz(self):
        result = self.create_draft()
        quiz = self.db.get(Quiz, result["quiz_id"])
        quiz.status = "approved"
        self.db.commit()

        with self.assertRaises(QuizImportError):
            append_quiz_batch(
                self.db,
                quiz_id=result["quiz_id"],
                text=VALID_QUESTION,
            )

    def test_invalid_question_limit_is_rejected(self):
        with self.assertRaises(QuizImportError):
            import_quiz_text(
                self.db,
                title="Invalid Limit Test",
                subject="quant",
                level="prelims",
                quiz_date="2026-10-29",
                text=VALID_QUESTION,
                settings=None,
                max_questions=201,
            )


if __name__ == "__main__":
    unittest.main()
