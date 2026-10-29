
"""Unit tests for PH Practice Hub leaderboard calculations."""

import unittest

from leaderboard_service import _percentile, _format_time


class TestLeaderboardHelpers(unittest.TestCase):

    def test_percentile_single_participant(self):
        self.assertEqual(_percentile(10, [10]), 50.0)

    def test_percentile_highest_score(self):
        self.assertEqual(_percentile(30, [10, 20, 30]), 83.33)

    def test_percentile_lowest_score(self):
        self.assertEqual(_percentile(10, [10, 20, 30]), 16.67)

    def test_percentile_tied_scores(self):
        self.assertEqual(
            _percentile(20, [10, 20, 20, 30]),
            50.0,
        )

    def test_empty_percentile_population(self):
        self.assertEqual(_percentile(10, []), 0.0)

    def test_format_time_minutes(self):
        self.assertEqual(_format_time(485), "8:05")

    def test_format_time_hours(self):
        self.assertEqual(_format_time(3661), "1:01:01")

    def test_negative_time(self):
        self.assertEqual(_format_time(-10), "0:00")


if __name__ == "__main__":
    unittest.main()
