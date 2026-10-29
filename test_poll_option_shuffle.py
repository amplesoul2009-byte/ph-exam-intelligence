
"""Tests for shuffled Telegram quiz options."""

import unittest

from poll_option_shuffle import shuffle_poll_options


class TestShufflePollOptions(unittest.TestCase):
    def test_correct_answer_mapping_after_shuffle(self):
        options = ["Alpha", "Bravo", "Charlie", "Delta"]

        displayed, order, correct_index = shuffle_poll_options(
            options,
            "B",
            shuffle=lambda labels: labels.__setitem__(
                slice(None), ["C", "A", "D", "B"]
            ),
        )

        self.assertEqual(displayed, ["Charlie", "Alpha", "Delta", "Bravo"])
        self.assertEqual(order, "CADB")
        self.assertEqual(displayed[correct_index], "Bravo")
        self.assertEqual(order[correct_index], "B")

    def test_invalid_correct_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            shuffle_poll_options(
                ["A", "B", "C", "D"],
                "E",
                shuffle=lambda labels: None,
            )

    def test_invalid_option_count_is_rejected(self):
        with self.assertRaises(ValueError):
            shuffle_poll_options(
                ["A", "B", "C"],
                "A",
                shuffle=lambda labels: None,
            )

    def test_invalid_shuffle_is_rejected(self):
        with self.assertRaises(ValueError):
            shuffle_poll_options(
                ["A", "B", "C", "D"],
                "A",
                shuffle=lambda labels: labels.__setitem__(
                    slice(None), ["A", "A", "C", "D"]
                ),
            )


if __name__ == "__main__":
    unittest.main()
