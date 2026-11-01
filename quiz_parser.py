
"""Conservative quiz text parser for PH Practice Hub.

Supports numbered questions, A-D options, answer keys and explanations.
Recognises common quiz headings and section labels without treating them
as malformed questions.

Question-level structural problems remain attached to the affected
question for manual review. The parser never invents or changes answers.
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

IGNORABLE_LINE = re.compile(
    r"""
    ^\s*(
        PH\s+PRACTICE\s+HUB
        |PH\s+MOCK(?:ORBIT)?
        |SEPTEMBER\s+\d{4}\s+CURRENT\s+AFFAIRS(?:\s*[-—:].*)?
        |TOTAL\s+QUESTIONS?\s*[:=-]?.*
        |LEVEL\s*[:=-]?.*
        |MARKS?\s*[:=-]?.*
        |NEGATIVE\s+MARKS?\s*[:=-]?.*
        |IMPORTANT\s+NOTE\s*[:=-]?.*
        |SSC\s*\|.*
        |SECTION\s+[A-Z0-9IVX]+(?:\s*[-—:].*)?
        |END\s+OF\s+SET
        |STATUS\s*[:=-]?.*
        |TITLE\s*[:=-]?.*
        |SUBJECT\s*[:=-]?.*
        |DATE\s*[:=-]?.*
        |BATCH\s*[:=-]?.*
        |={3,}
        |-{3,}
        |\*{3,}
        |_ {3,}
    )\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
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


def _is_ignorable_line(line: str) -> bool:
    """Return True for recognised document headings and separators."""
    return bool(IGNORABLE_LINE.match(line.strip()))


def _finish_question(question: ParsedQuestion) -> None:
    """Attach structural validation warnings to one question."""
    if not question.question.strip():
        question.warnings.append("Question text is missing.")

    for option in "ABCD":
        if option not in question.options:
            question.warnings.append(f"Option {option} is missing.")
        elif not question.options[option].strip():
            question.warnings.append(f"Option {option} is empty.")

    if question.correct_option not in set("ABCD"):
        question.warnings.append("A valid answer key (A-D) is missing.")

    if not question.explanation.strip():
        question.warnings.append("Explanation is missing.")


def parse_quiz_text(
    text: str,
    max_questions: int = 200,
) -> ParseResult:
    """Parse pasted quiz text into question records.

    The parser preserves question content and never guesses answer keys.
    Text outside question blocks is recorded as a global warning.
    """

    result = ParseResult()

    if not isinstance(text, str) or not text.strip():
        result.errors.append("Quiz text is empty.")
        return result

    if not isinstance(max_questions, int) or not 1 <= max_questions <= 200:
        result.errors.append("max_questions must be between 1 and 200.")
        return result

    current: Optional[ParsedQuestion] = None
    seen_numbers = set()
    outside_lines = []

    def finish_current() -> None:
        nonlocal current
        if current is not None:
            _finish_question(current)
            result.questions.append(current)
            current = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        if _is_ignorable_line(line):
            continue

        question_match = QUESTION_START.match(line)

        if question_match:
            finish_current()

            number = int(question_match.group(1))
            question_text = question_match.group(2).strip()

            if number in seen_numbers:
                result.warnings.append(
                    f"Question number {number} appears more than once."
                )

            seen_numbers.add(number)

            current = ParsedQuestion(
                number=number,
                question=question_text,
            )

            if len(result.questions) >= max_questions:
                result.errors.append(
                    f"Question limit of {max_questions} exceeded."
                )
                current = None
                break

            continue

        if current is None:
            outside_lines.append((line_number, line))
            continue

        option_match = OPTION_LINE.match(line)

        if option_match:
            option_letter = (
                option_match.group(1)
                or option_match.group(2)
                or option_match.group(3)
            ).upper()
            option_text = option_match.group(4).strip()

            if option_letter in current.options:
                current.warnings.append(
                    f"Option {option_letter} is duplicated."
                )
            else:
                current.options[option_letter] = option_text

            continue

        answer_match = ANSWER_LINE.match(line)

        if answer_match:
            answer = answer_match.group(1).upper()

            if current.correct_option is not None:
                current.warnings.append("Answer key is duplicated.")
            else:
                current.correct_option = answer

            continue

        explanation_match = EXPLANATION_LINE.match(line)

        if explanation_match:
            explanation_text = explanation_match.group(1).strip()

            if current.explanation:
                current.explanation += "\n" + explanation_text
            else:
                current.explanation = explanation_text

            continue

        # Preserve unrecognised lines as part of the question or explanation.
        if current.explanation:
            current.explanation += "\n" + line
        elif not current.options:
            current.question += "\n" + line
        else:
            # Do not silently discard text after the options begin.
            current.warnings.append(
                f"Unrecognised content at line {line_number}: {line[:120]}"
            )

    finish_current()

    if outside_lines:
        result.warnings.append(
            f"{len(outside_lines)} line(s) outside recognised question "
            "blocks were ignored as document preamble or formatting."
        )

    if not result.questions and not result.errors:
        result.errors.append("No numbered questions were detected.")

    if len(result.questions) > max_questions:
        result.errors.append(
            f"Question limit of {max_questions} exceeded."
        )

    return result
