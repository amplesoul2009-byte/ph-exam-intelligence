
"""Basic tests for PH Greybot scoring and percentiles."""

import unittest

from quiz_scoring import (
    calculate_live_percentiles,
    calculate_score,
    format_time,
    percentile_rank,
    rank_attempts,
)


class TestQuizScoring(unittest.TestCase):

    def test_negative_marking(self):
        result = calculate_score(
            total_questions=60,
            correct=26,
            wrong=21,
            time_used_seconds=485,
        )

        self.assertEqual(result.net_score, 20.75)
        self.assertEqual(result.attempted, 47)
        self.assertEqual(result.unanswered, 13)
        self.assertAlmostEqual(result.accuracy, 55.3191, places=3)
        self.assertEqual(format_time(485), "8:05")

    def test_all_unanswered(self):
        result = calculate_score(
            total_questions=20,
            correct=0,
            wrong=0,
        )

        self.assertEqual(result.net_score, 0)
        self.assertEqual(result.attempted, 0)
        self.assertEqual(result.unanswered, 20)
        self.assertEqual(result.accuracy, 0)

    def test_negative_total_score_is_allowed(self):
        result = calculate_score(
            total_questions=10,
            correct=1,
            wrong=6,
        )

        self.assertEqual(result.net_score, -0.5)

    def test_inconsistent_unanswered_count_fails(self):
        with self.assertRaises(ValueError):
            calculate_score(
                total_questions=10,
                correct=3,
                wrong=2,
                unanswered=2,
            )

    def test_percentile_ties(self):
        scores = [10, 20, 20, 30]

        # Midrank percentile: half credit for tied scores.
        self.assertEqual(percentile_rank(20, scores), 50.0)
        self.assertEqual(percentile_rank(30, scores), 87.5)

    def test_group_and_overall_percentiles(self):
        result = calculate_live_percentiles(
            student_score=20,
            overall_scores=[10, 20, 30],
            group_scores=[10, 20],
        )

        self.assertEqual(result["overall_percentile"], 50.0)
        self.assertEqual(result["group_percentile"], 75.0)
        self.assertTrue(result["provisional"])

    def test_rank_order_and_ties(self):
        attempts = [
            {
                "student_id": "1",
                "net_score": 5,
                "accuracy": 50,
                "time_used_seconds": 100,
            },
            {
                "student_id": "2",
                "net_score": 8,
                "accuracy": 70,
                "time_used_seconds": 120,
            },
            {
                "student_id": "3",
                "net_score": 8,
                "accuracy": 70,
                "time_used_seconds": 120,
            },
        ]

        ranked = rank_attempts(attempts)

        self.assertEqual(ranked[0]["student_id"], "2")
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[1]["rank"], 1)
        self.assertEqual(ranked[2]["rank"], 3)


if __name__ == "__main__":
    unittest.main()
