
"""Unit tests for the PH Practice Hub plain-text quiz parser."""

import unittest

from quiz_parser import parse_quiz_text


VALID_QUESTION = """1. What is 2 + 2?
A) 3
B) 4
C) 5
D) 6
Answer: B
Explanation: Two plus two equals four."""


class QuizParserTests(unittest.TestCase):
    def test_parses_valid_question(self):
        result = parse_quiz_text(VALID_QUESTION)

        self.assertEqual(len(result.questions), 1)
        question = result.questions[0]

        self.assertEqual(question.question, "What is 2 + 2?")
        self.assertEqual(question.options["A"], "3")
        self.assertEqual(question.options["B"], "4")
        self.assertEqual(question.correct_option, "B")
        self.assertEqual(
            question.explanation,
            "Two plus two equals four.",
        )
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])

    def test_supports_q_prefix_and_different_option_formats(self):
        text = """Q1. Which is a prime number?
(A) 4
[B] 5
C. 6
D) 8
Correct Answer: B"""

        result = parse_quiz_text(text)

        self.assertEqual(len(result.questions), 1)
        self.assertEqual(
            result.questions[0].correct_option,
            "B",
        )
        self.assertEqual(result.questions[0].options["B"], "5")

    def test_missing_answer_is_reported(self):
        text = """1. What is the capital of France?
A) Paris
B) Rome
C) Madrid
D) Berlin"""

        result = parse_quiz_text(text)

        self.assertEqual(len(result.questions), 1)
        self.assertIsNone(
            result.questions[0].correct_option
        )
        self.assertTrue(
            any("Correct answer is missing" in warning
                for warning in result.warnings)
        )

    def test_missing_option_is_reported(self):
        text = """1. Choose the correct number.
A) One
B) Two
D) Four
Answer: A"""

        result = parse_quiz_text(text)

        self.assertEqual(len(result.questions), 1)
        self.assertTrue(
            any("Option C is missing" in warning
                for warning in result.warnings)
        )

    def test_parses_multiple_questions_in_order(self):
        second = VALID_QUESTION.replace(
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

        result = parse_quiz_text(
            VALID_QUESTION + "\n\n" + second
        )

        self.assertEqual(len(result.questions), 2)
        self.assertEqual(
            [q.number for q in result.questions],
            [1, 2],
        )

    def test_question_limit_is_enforced(self):
        second = VALID_QUESTION.replace(
            "1. What is 2 + 2?",
            "2. What is 3 + 3?",
        )

        result = parse_quiz_text(
            VALID_QUESTION + "\n\n" + second,
            max_questions=1,
        )

        self.assertEqual(len(result.questions), 1)
        self.assertTrue(
            any("maximum of 1 questions" in error
                for error in result.errors)
        )

    def test_empty_input_is_rejected(self):
        result = parse_quiz_text("   \n ")

        self.assertEqual(result.questions, [])
        self.assertTrue(result.errors)

    def test_invalid_question_limit_is_rejected(self):
        result = parse_quiz_text(
            VALID_QUESTION,
            max_questions=201,
        )

        self.assertEqual(result.questions, [])
        self.assertTrue(result.errors)

    def test_multiline_question_text_is_preserved(self):
        text = """1. Read the statement.
Which option is correct?
A) First
B) Second
C) Third
D) Fourth
Answer: C"""

        result = parse_quiz_text(text)

        self.assertEqual(len(result.questions), 1)
        self.assertIn(
            "Which option is correct?",
            result.questions[0].question,
        )

    def test_duplicate_option_is_flagged(self):
        text = """1. Select an answer.
A) First
A) Repeated
B) Second
C) Third
D) Fourth
Answer: B"""

        result = parse_quiz_text(text)

        self.assertEqual(len(result.questions), 1)
        self.assertTrue(
            any("appears more than once" in warning
                for warning in result.questions[0].warnings)
        )


if __name__ == "__main__":
    unittest.main()
