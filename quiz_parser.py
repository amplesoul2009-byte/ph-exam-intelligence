"""Tolerant question parser for PH Practice Hub.

Document titles, section headings and notes before the first numbered question
are treated as preamble. Content inside a question is preserved; no answer is
invented by the parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

QUESTION_START = re.compile(
    r"^\s*(?:Q(?:UESTION)?\s*)?(\d{1,3})\s*[.)]\s*(.*?)\s*$",
    re.IGNORECASE,
)
OPTION_LINE = re.compile(
    r"^\s*(?:\(([A-D])\)|\[([A-D])\]|([A-D])\s*[.)])\s*(.*?)\s*$",
    re.IGNORECASE,
)
ANSWER_LINE = re.compile(
    r"^\s*(?:correct\s+answer|answer|ans|key)\s*[:.)-]\s*"
    r"(?:option\s*)?[\(\[]?([A-D])[\)\]]?\s*$",
    re.IGNORECASE,
)
EXPLANATION_LINE = re.compile(
    r"^\s*(?:explanation|solution|reason)\s*[:.)-]\s*(.*?)\s*$",
    re.IGNORECASE,
)

DOCUMENT_HEADING = re.compile(
    r"^\s*(?:PH\s+PRACTICE\s+HUB|PH\s+MOCK(?:ORBIT)?|"
    r"SECTION\s+[A-Z0-9IVX]+(?:\s*[-—:].*)?|IMPORTANT\s+NOTE\s*[:=-]?.*|"
    r"TOTAL\s+QUESTIONS?\s*[:=-]?.*|LEVEL\s*[:=-]?.*|MARKS?\s*[:=-]?.*|"
    r"NEGATIVE\s+MARKS?\s*[:=-]?.*|SSC\s*\|.*|END\s+OF\s+SET|"
    r"STATUS\s*[:=-]?.*|TITLE\s*[:=-]?.*|SUBJECT\s*[:=-]?.*|DATE\s*[:=-]?.*)\s*$",
    re.IGNORECASE,
)

@dataclass
class ParsedQuestion:
    number: int
    question: str = ""
    options: Dict[str, str] = field(default_factory=dict)
    correct_option: Optional[str] = None
    explanation: str = ""
    warnings: List[str] = field(default_factory=list)

@dataclass
class ParseResult:
    questions: List[ParsedQuestion] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _finish_question(q: ParsedQuestion) -> None:
    if not q.question.strip():
        q.warnings.append("Question text is missing.")
    for label in "ABCD":
        if label not in q.options:
            q.warnings.append(f"Option {label} is missing.")
        elif not q.options[label].strip():
            q.warnings.append(f"Option {label} is empty.")
    if q.correct_option not in set("ABCD"):
        q.warnings.append("Correct answer is missing or invalid.")
    if not q.explanation.strip():
        q.warnings.append("Explanation is missing.")


def parse_quiz_text(text: str, max_questions: int = 200) -> ParseResult:
    result = ParseResult()
    if not isinstance(text, str) or not text.strip():
        result.errors.append("Quiz text is empty.")
        return result
    if isinstance(max_questions, bool) or not isinstance(max_questions, int) or not 1 <= max_questions <= 200:
        result.errors.append("max_questions must be between 1 and 200.")
        return result

    current: Optional[ParsedQuestion] = None
    seen_numbers = set()
    preamble_lines = []
    last_section = "question"

    def finish():
        nonlocal current, last_section
        if current is not None:
            _finish_question(current)
            result.warnings.extend(
                f"Question {current.number}: {warning}"
                for warning in current.warnings
            )
            result.questions.append(current)
            current = None
        last_section = "question"

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue

        match = QUESTION_START.match(line)
        if match:
            finish()
            if len(result.questions) >= max_questions:
                result.errors.append(
                    f"maximum of {max_questions} questions was reached at line {line_no}."
                )
                break
            number = int(match.group(1))
            if number in seen_numbers:
                result.warnings.append(f"Question number {number} appears more than once.")
            seen_numbers.add(number)
            current = ParsedQuestion(number=number, question=match.group(2).strip())
            continue

        if DOCUMENT_HEADING.match(line):
            continue

        if current is None:
            # Titles, notes, metadata, section headings and other front matter
            # are common in exam-set documents. Preserve no false parser error.
            preamble_lines.append((line_no, line))
            continue

        option = OPTION_LINE.match(line)
        if option:
            label = (option.group(1) or option.group(2) or option.group(3)).upper()
            value = option.group(4).strip()
            if label in current.options:
                current.warnings.append(f"Option {label} appears more than once.")
            else:
                current.options[label] = value
            last_section = "options"
            continue

        answer = ANSWER_LINE.match(line)
        if answer:
            if current.correct_option is not None:
                current.warnings.append("Answer key is duplicated.")
            else:
                current.correct_option = answer.group(1).upper()
            last_section = "answer"
            continue

        explanation = EXPLANATION_LINE.match(line)
        if explanation:
            value = explanation.group(1).strip()
            current.explanation = (current.explanation + "\n" + value).strip()
            last_section = "explanation"
            continue

        # Preserve wrapped question text and multi-line explanations.
        if last_section == "explanation":
            current.explanation = (current.explanation + "\n" + line).strip()
        elif last_section in {"answer", "options"}:
            current.warnings.append(f"Unrecognised content at line {line_no}: {line[:120]}")
        else:
            current.question = (current.question + "\n" + line).strip()

    finish()
    if preamble_lines:
        result.warnings.append(
            f"Ignored {len(preamble_lines)} document heading/metadata line(s) before the first question."
        )
    if not result.questions and not result.errors:
        result.errors.append("No numbered questions were detected.")
    return result
