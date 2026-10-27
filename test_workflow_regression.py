"""Dependency-light regression tests for PH 0xGREY question processing.

Run from the project directory:
    python test_workflow_regression.py

These tests extract pure helper functions from bot.py's AST so they can run even
when Telegram/SQLAlchemy packages are not installed. They do not test Telegram,
AI-provider calls, database persistence, or FadeHost deployment.
"""
from __future__ import annotations

import ast
import random
import re
import unittest
from difflib import SequenceMatcher
from pathlib import Path

BOT_FILE = Path(__file__).with_name("bot.py")
FUNCTIONS = {
    "parse_bulk_question_blocks",
    "audit_question_quality",
    "shuffled_option_entries",
}

source = BOT_FILE.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(BOT_FILE))
selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
found = {node.name for node in selected}
missing = FUNCTIONS - found
if missing:
    raise RuntimeError(f"Could not find helper function(s) in bot.py: {sorted(missing)}")
namespace = {"re": re, "SequenceMatcher": SequenceMatcher, "random": random}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_FILE), "exec"), namespace)
parse_bulk_question_blocks = namespace["parse_bulk_question_blocks"]
audit_question_quality = namespace["audit_question_quality"]
shuffled_option_entries = namespace["shuffled_option_entries"]


def mcq(text="Which option is correct?", correct="B", options=None):
    return {
        "text": text,
        "options": options or {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
        "correct": correct,
        "explanation": "Test explanation.",
        "difficulty": "Moderate",
    }


class ImportParserTests(unittest.TestCase):
    def test_answer_letter(self):
        raw = """1. Which institution regulates monetary policy in India?
A) SEBI
B) RBI
C) NABARD
D) IRDAI
Ans: B"""
        questions = parse_bulk_question_blocks(raw)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["correct"], "B")

    def test_answer_option_text(self):
        raw = """Q1. Which institution regulates monetary policy in India?
Option A: SEBI
Option B: RBI
Option C: NABARD
Option D: IRDAI
Answer: RBI"""
        questions = parse_bulk_question_blocks(raw)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["correct"], "B")

    def test_mixed_numbering(self):
        raw = """Q1. First question?
A) Alpha
B) Beta
C) Gamma
D) Delta
Ans: A

2. Second question?
A) One
B) Two
C) Three
D) Four
Answer: Four"""
        questions = parse_bulk_question_blocks(raw)
        self.assertEqual(len(questions), 2)
        self.assertEqual([q["correct"] for q in questions], ["A", "D"])

    def test_ambiguous_answer_text_is_not_guessed(self):
        raw = """1. Pick an answer?
A) Same
B) Other
C) Same
D) Last
Answer: Same"""
        self.assertEqual(parse_bulk_question_blocks(raw), [])

    def test_empty_input(self):
        self.assertEqual(parse_bulk_question_blocks("  \n "), [])


class QualityControlTests(unittest.TestCase):
    def test_exact_duplicate_is_removed(self):
        items = [mcq("Which option is correct?"), mcq("  Which option is correct?  ")]
        accepted, issues = audit_question_quality(items)
        self.assertEqual(len(accepted), 1)
        self.assertTrue(any("exact duplicate" in issue for issue in issues))

    def test_near_duplicate_is_flagged_but_retained(self):
        items = [mcq("Which institution regulates monetary policy in India?"),
                 mcq("Which institution regulates monetary policy in India today?")]
        accepted, issues = audit_question_quality(items)
        self.assertEqual(len(accepted), 2)
        self.assertTrue(any("near-duplicate" in issue for issue in issues))

    def test_duplicate_options_are_excluded(self):
        bad = mcq(options={"A": "Same", "B": "Same", "C": "Third", "D": "Fourth"})
        accepted, issues = audit_question_quality([bad])
        self.assertEqual(accepted, [])
        self.assertTrue(any("duplicate options" in issue for issue in issues))

    def test_invalid_answer_is_excluded(self):
        accepted, issues = audit_question_quality([mcq(correct="E")])
        self.assertEqual(accepted, [])
        self.assertTrue(any("invalid answer key" in issue for issue in issues))


class AnswerMappingTests(unittest.TestCase):
    def test_correct_answer_stays_attached_when_shuffled(self):
        options = {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"}
        for correct in "ABCD":
            for _ in range(10):
                entries, index = shuffled_option_entries(options, correct)
                self.assertEqual(entries[index][0], correct)
                self.assertEqual(len(entries), 4)

    def test_duplicate_options_are_rejected(self):
        options = {"A": "same", "B": "same", "C": "third", "D": "fourth"}
        with self.assertRaises(ValueError):
            shuffled_option_entries(options, "A")

    def test_invalid_answer_label_is_rejected(self):
        options = {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"}
        with self.assertRaises(ValueError):
            shuffled_option_entries(options, "E")


if __name__ == "__main__":
    unittest.main(verbosity=2)
