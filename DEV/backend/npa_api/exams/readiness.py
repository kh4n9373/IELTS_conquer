"""Pure publish-readiness check. Returns ordered, de-duplicated blocker codes."""

from collections.abc import Mapping, Sequence
from typing import Any

from .types import QUESTION_GROUPS

Row = Mapping[str, Any]

IELTS_SECTIONS = 3
IELTS_QUESTIONS = 40
IELTS_MINUTES = 60
CHOICE_TYPES = {"multiple_choice", "multiple_choice_multi"}


def readiness(
    test: Row, assets: Sequence[Row], sections: Sequence[Row], questions: Sequence[Row], items: Sequence[Row]
) -> list[str]:
    """`items` are the latest key version's items; no version means an empty sequence."""
    blockers: list[str] = []

    def add(code: str) -> None:
        if code not in blockers:
            blockers.append(code)

    manual = test.get("authoring") == "manual"
    pdf = next((a for a in assets if a["kind"] == "student_pdf"), None)
    if pdf is None and not manual:
        add("missing_pdf")
    if not sections:
        add("no_sections")
    if manual and any(
        not q.get("prompt") or (q.get("question_type") in CHOICE_TYPES and len(q.get("options") or []) < 2)
        for q in questions
    ):
        add("incomplete_question")

    page_count = pdf.get("page_count") if pdf is not None else None
    if pdf is not None and not manual:
        for section in sections:
            pages = [*section["passage_pages"], *section["question_pages"]]
            # An unknown page count cannot confirm any mapped page.
            if any(p < 1 or page_count is None or p > page_count for p in pages):
                add("page_out_of_range")

    numbers = sorted(q["number"] for q in questions)
    if (sections and not questions) or numbers != list(range(1, len(numbers) + 1)):
        add("question_gap")
    if any(q.get("question_type") not in QUESTION_GROUPS for q in questions):
        add("missing_type")

    by_number = {i["question_number"]: i for i in items}
    for number in numbers:
        item = by_number.get(number)
        if item is None or not item["answers"]:
            add("missing_answer")
    for number in numbers:
        item = by_number.get(number)
        if item is not None and item["answers"] and (item.get("flagged") or item.get("reviewed_at") is None):
            add("unreviewed_answer")

    if test["test_type"] == "ielts_standard" and (
        len(sections) != IELTS_SECTIONS
        or len(questions) != IELTS_QUESTIONS
        or test["duration_minutes"] != IELTS_MINUTES
    ):
        add("ielts_shape")
    return blockers
