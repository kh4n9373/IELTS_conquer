"""Local-only S14: grades the real pilot key against itself. Failures name case indexes, never values."""

import re

import pytest
from test_pilot_key import TABLE, VARIANT_QUESTION, cells

from npa_api.grading.rules import GradableQuestion, grade


def load_key() -> dict[int, tuple[str, ...]]:
    if not TABLE.is_file():
        pytest.skip("pilot key table is not available")
    key: dict[int, list[str]] = {}
    for row in TABLE.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", row):
            continue
        parts = cells(row)
        if len(parts) == 8:
            key.setdefault(int(parts[0]), []).insert(0, parts[7])
        elif len(parts) == 4 and int(parts[0]) == VARIANT_QUESTION and parts[2].startswith("Đúng"):
            key.setdefault(VARIANT_QUESTION, []).append(parts[1].strip("`"))
    return {n: tuple(dict.fromkeys(a)) for n, a in key.items()}


def questions(key: dict[int, tuple[str, ...]]) -> list[GradableQuestion]:
    return [GradableQuestion(n, "short_answer", None, None, key[n]) for n in range(1, 41)]


@pytest.mark.pilot
def test_s14_cases() -> None:
    key = load_key()
    assert sorted(key) == list(range(1, 41))
    qs = questions(key)
    cases: list[tuple[dict[int, str], int, bool]] = []  # answers, question, expected correct
    for n in range(1, 41):
        for variant in key[n]:
            cases.append(({n: variant}, n, True))
            cases.append(({n: f"  {variant.upper()}. "}, n, True))
        cases.append(({n: key[n][0] + "zz"}, n, False))
        cases.append(({n: "   "}, n, False))
    failed = [
        index
        for index, (answers, n, expected) in enumerate(cases)
        if next(m for m in grade(qs, answers, "pilot-1") if m.number == n).correct != expected
    ]
    assert failed == []


@pytest.mark.pilot
def test_s14_full_marks_40_of_40() -> None:
    key = load_key()
    answers = {n: key[n][0] for n in range(1, 41)}
    wrong = [m.number for m in grade(questions(key), answers, "pilot-1") if not m.correct]
    assert wrong == []
