"""Local-only S14: grades the real pilot key against itself. Failures name case indexes, never values."""

import re

import pytest
from test_pilot_key import TABLE, VARIANT_QUESTION, cells

from npa_api.grading.rules import GradableQuestion, grade, normalize

TFNG_LABEL = "True/False/Not Given"
LIMITS = {"Tối đa 2 từ": 2, "1 từ": 1}
SHORT_FORMS = {"true": "t", "t": "t", "false": "f", "f": "f", "not given": "ng", "ng": "ng"}


def load_questions() -> dict[int, GradableQuestion]:
    if not TABLE.is_file():
        pytest.skip("pilot key table is not available")
    key: dict[int, list[str]] = {}
    meta: dict[int, tuple[str, int | None]] = {}
    for row in TABLE.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", row):
            continue
        parts = cells(row)
        if len(parts) == 8:
            number = int(parts[0])
            kind = "tfng" if parts[4] == TFNG_LABEL else "short_answer"
            meta[number] = (kind, LIMITS.get(parts[6]))
            # The variant question's main cell is a summary; its accepted forms are the "Đúng" rows.
            if number != VARIANT_QUESTION:
                key.setdefault(number, []).append(parts[7])
        elif len(parts) == 4 and int(parts[0]) == VARIANT_QUESTION and parts[2].startswith("Đúng"):
            key.setdefault(VARIANT_QUESTION, []).append(parts[1].strip("`"))
    return {
        n: GradableQuestion(n, meta[n][0], meta[n][1], None, tuple(dict.fromkeys(key[n])))
        for n in sorted(meta)
    }


@pytest.mark.pilot
def test_s14_cases() -> None:
    by_number = load_questions()
    assert sorted(by_number) == list(range(1, 41))
    assert sum(q.question_type == "tfng" for q in by_number.values()) == 4
    assert sum(q.word_limit is not None for q in by_number.values()) == 10
    qs = list(by_number.values())
    cases: list[tuple[int, str, bool]] = []  # question, answer, expected correct
    unknown_forms: list[int] = []
    for q in qs:
        for variant in q.accepted:
            cases.append((q.number, variant, True))
            cases.append((q.number, f"  {variant.upper()}. ", True))
            if q.word_limit is not None:
                with_article = f"the {variant}"
                cases.append((q.number, with_article, len(with_article.split()) <= q.word_limit))
        if q.question_type == "tfng":
            short = SHORT_FORMS.get(normalize(q.accepted[0]))
            if short is None:
                unknown_forms.append(q.number)
            else:
                cases.append((q.number, short, True))
            cases.append((q.number, "yes", False))
        cases.append((q.number, q.accepted[0] + "zz", False))
        cases.append((q.number, "   ", False))
    failed = [
        index
        for index, (number, answer, expected) in enumerate(cases)
        if next(m for m in grade(qs, {number: answer}, "pilot-1") if m.number == number).correct
        != expected
    ]
    assert unknown_forms == []
    assert failed == []


@pytest.mark.pilot
def test_s14_full_marks_40_of_40() -> None:
    qs = list(load_questions().values())
    answers = {q.number: q.accepted[0] for q in qs}
    wrong = [m.number for m in grade(qs, answers, "pilot-1") if not m.correct]
    assert wrong == []
