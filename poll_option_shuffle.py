
"""Shuffle quiz options without losing the original answer mapping."""

import random
from collections.abc import Callable, Sequence


def shuffle_poll_options(
    options: Sequence[str],
    correct_option: str,
    *,
    shuffle: Callable[[list[str]], None] | None = None,
) -> tuple[list[str], str, int]:
    """Return displayed options, their original labels, and correct index.

    The returned order maps each displayed index to its original A/B/C/D
    label. For example, order "CADB" means displayed option zero is C.
    """

    if len(options) != 4:
        raise ValueError("Exactly four options are required.")

    if not all(isinstance(option, str) and option.strip() for option in options):
        raise ValueError("All four options must be non-empty strings.")

    correct_option = correct_option.strip().upper()
    if correct_option not in "ABCD" or len(correct_option) != 1:
        raise ValueError("Correct option must be A, B, C, or D.")

    order = list("ABCD")

    if shuffle is None:
        random.shuffle(order)
    else:
        shuffle(order)

    if len(order) != 4 or set(order) != set("ABCD"):
        raise ValueError("Shuffle must preserve A, B, C, and D exactly once.")

    displayed_options = [
        options["ABCD".index(original_label)]
        for original_label in order
    ]
    correct_index = order.index(correct_option)

    return displayed_options, "".join(order), correct_index
