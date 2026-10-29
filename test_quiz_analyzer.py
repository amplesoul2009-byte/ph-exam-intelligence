
"""Unit tests for conservative quiz-import analysis."""

import unittest

from quiz_analyzer import analyze_import
from quiz_parser import parse_quiz_text


VALID_TEXT = """1. What is 2 + 2?
A) 3
B) 4
C) 5
D) 6
Answer: B
Explanation: Two plus two equals four."""


class QuizAnalyzerTests(unittest.TestCase):
    def test_valid_question_still_requires_owner_approval(self):
        parsed = parse_quiz_text(VALID_TEXT)

        report = analyze_import(parsed, settings=None)

        self.assertEqual(
            report["ai_status"],
            "manual_review_required",
        )
        self.assertEqual(len(report["questions"]), 1)
        self.assertEqual(
            report["questions"][0]["review_status"],
            "pending_owner_approval",
        )

    def test_analyzer_does_not_invent_missing_answer(self):
        text = """1. What is 2 + 2?
A) 3
B) 4
C) 5
D) 6"""

        parsed = parse_quiz_text(text)
        report = analyze_import(parsed, settings=None)

        question_report = report["questions"][0]

        self.assertFalse(
            question_report["answer_key_present"]
        )
        self.assertEqual(
            question_report["review_status"],
            "needs_review",
        )

    def test_incomplete_options_need_manual_review(self):
        text = """1. Choose the correct answer.
A) First
B) Second
Answer: A"""

        parsed = parse_quiz_text(text)
        report = analyze_import(parsed, settings=None)

        self.assertEqual(
            report["questions"][0]["review_status"],
            "needs_review",
        )
        self.assertTrue(report["warnings"])

    def test_parser_errors_are_preserved(self):
        parsed = parse_quiz_text(
            "This line is outside a recognised question."
        )

        report = analyze_import(parsed, settings=None)

        self.assertEqual(
            report["errors"],
            parsed.errors,
        )

    def test_multiple_questions_receive_individual_reports(self):
        second_question = VALID_TEXT.replace(
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

        parsed = parse_quiz_text(
            VALID_TEXT + "\n\n" + second_question
        )
        report = analyze_import(parsed, settings=None)

        self.assertEqual(len(report["questions"]), 2)
        self.assertEqual(
            [
                item["question_number"]
                for item in report["questions"]
            ],
            [1, 2],
        )

    def test_report_explains_owner_review_requirement(self):
        parsed = parse_quiz_text(VALID_TEXT)
        report = analyze_import(parsed, settings=None)

        self.assertIn("owner", report["message"].lower())
        self.assertIn("before publishing", report["message"].lower())


if __name__ == "__main__":
    unittest.main()
  
