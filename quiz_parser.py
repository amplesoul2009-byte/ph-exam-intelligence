
"""Parse and validate the structure of imported quiz text."""

import re
from dataclasses import asdict, dataclass, field
from typing import Optional


QUESTION_START = re.compile(
    r"^\s*(?:Q(?:UESTION)?\s*)?(\d{1,3})\s*[.)]\s*(.*?)\s*$",
    re.IGNORECASE,
)

OPTION_LINE = re.compile(
    r"^\s*(?:\(([A-D])\)|\[([A-D])\]|([A-D])\s*[.)])\s*(.*?)\s*$",
    re.IGNORECASE,
)

ANSWER_LINE = re.compile(
    r"^\s*(?:correct\s+answer|answer|ans|key)\s*[:=-]\s*"
    r"([A-D])\b",
    re.IGNORECASE,
)

EXPLANATION_LINE = re.compile(
    r"^\s*(?:explanation|solution|reason)\s*[:=-]\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class ParsedQuestion:
    number: int
    question: str
    options: dict[str, str] = field(default_factory=dict)
    correct_option: Optional[str] = None
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class ParseResult:
    questions: list[ParsedQuestion]
    errors: list[str]
    warnings: list[str]

    def to_dict(self):
        return {
            "questions": [q.to_dict() for q in self.questions],
            "errors": self.errors,
            "warnings": self.warnings,
            "detected_count": len(self.questions),
            "valid_count": sum(
                not q.warnings
                and len(q.options) == 4
                and q.correct_option is not None
                for q in self.questions
            ),
        }


def parse_quiz_text(text: str, max_questions: int = 200) -> ParseResult:
    """Parse plain-text MCQs without inventing missing information."""

    if not isinstance(text, str) or not text.strip():
        return ParseResult([], ["No quiz text provided."], [])

    if not 1 <= max_questions <= 200:
        return ParseResult(
            [], ["max_questions must be between 1 and 200."], []
        )

    questions: list[ParsedQuestion] = []
    errors: list[str] = []
    global_warnings: list[str] = []
    current: Optional[ParsedQuestion] = None
    mode = "question"
    last_option: Optional[str] = None

    def finish_question():
        nonlocal current, mode, last_option

        if current is None:
            return

        current.question = current.question.strip()

        if not current.question:
            current.warnings.append("Question text is missing.")

        for label in "ABCD":
            if label not in current.options:
                current.warnings.append(
                    f"Option {label} is missing."
                )

        if current.correct_option is None:
            current.warnings.append("Correct answer is missing.")
        elif current.correct_option not in current.options:
            current.warnings.append(
                "Answer key points to an option that is missing."
            )

        if len(current.options) != 4:
            current.warnings.append(
                f"Expected four options; found {len(current.options)}."
            )

        if len(current.warnings) > 0:
            global_warnings.append(
                f"Question {current.number}: "
                + "; ".join(current.warnings)
            )

        questions.append(current)
        current = None
        mode = "question"
        last_option = None

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        if not line:
            continue

        q_match = QUESTION_START.match(line)

        # A new numbered question begins a new record.
        if q_match:
            number = int(q_match.group(1))
            body = q_match.group(2).strip()

            if current is not None:
                finish_question()

            if len(questions) >= max_questions:
                errors.append(
                    f"Import stopped at line {line_number}: "
                    f"maximum of {max_questions} questions reached."
                )
                break

            current = ParsedQuestion(number=number, question=body)
            mode = "question"
            last_option = None
            continue

        if current is None:
            errors.append(
                f"Line {line_number} is outside a recognised question: "
                f"{line[:100]}"
            )
            continue

        option_match = OPTION_LINE.match(line)

        if option_match:
            label = next(
                group for group in option_match.groups()[:3]
                if group is not None
            ).upper()
            value = option_match.group(4).strip()

            if not value:
                current.warnings.append(f"Option {label} is empty.")
            elif label in current.options:
                current.warnings.append(
                    f"Option {label} appears more than once."
                )
            else:
                current.options[label] = value

            mode = "option"
            last_option = label
            continue

        answer_match = ANSWER_LINE.match(line)

        if answer_match:
            answer = answer_match.group(1).upper()

            if current.correct_option is not None:
                current.warnings.append(
                    "Multiple answer keys were provided."
                )
            else:
                current.correct_option = answer

            mode = "answer"
            last_option = None
            continue

        explanation_match = EXPLANATION_LINE.match(line)

        if explanation_match:
            current.explanation = explanation_match.group(1).strip()
            mode = "explanation"
            last_option = None
            continue

        # Unlabelled lines are appended only to the active field.
        if mode == "option" and last_option:
            current.options[last_option] += "\n" + line
        elif mode == "explanation":
            current.explanation += "\n" + line
        elif mode == "question":
            current.question += "\n" + line
        else:
            current.warnings.append(
                f"Unrecognised line {line_number}: {line[:100]}"
            )

    finish_question()

    if not questions and not errors:
        errors.append(
            "No numbered questions were detected. "
            "Use question numbers such as '1.' or 'Q1.'"
        )

    # Duplicate question numbers are flagged, not automatically renumbered.
    seen_numbers = set()
    for question in questions:
        if question.number in seen_numbers:
            warning = (
                f"Question number {question.number} is duplicated."
            )
            question.warnings.append(warning)
            global_warnings.append(warning)
        seen_numbers.add(question.number)

    return ParseResult(
        questions=questions,
        errors=errors,
        warnings=global_warnings,
    )
