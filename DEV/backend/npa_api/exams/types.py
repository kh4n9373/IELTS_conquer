"""Question types and the answer-input group each one belongs to."""

from typing import Literal, get_args

QuestionType = Literal[
    "matching_features",
    "matching_headings",
    "matching_information",
    "sentence_endings",
    "multiple_choice",
    "tfng",
    "ynng",
    "table_completion",
    "note_completion",
    "summary_completion",
    "sentence_completion",
    "short_answer",
    "diagram_label",
]
QuestionGroup = Literal["matching", "completion", "multiple_choice", "tfng"]

_FIXED: dict[str, QuestionGroup] = {
    "matching_features": "matching",
    "matching_headings": "matching",
    "matching_information": "matching",
    "sentence_endings": "matching",
    "multiple_choice": "multiple_choice",
    "tfng": "tfng",
    "ynng": "tfng",
}

QUESTION_GROUPS: dict[str, QuestionGroup] = {t: _FIXED.get(t, "completion") for t in get_args(QuestionType)}
