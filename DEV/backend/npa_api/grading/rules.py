import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

RuleSet = Literal["standard-1", "pilot-1"]
Reason = Literal["correct", "wrong", "blank", "over_word_limit"]

_TRAILING = ".,;:!?"
_ARTICLES = ("the ", "a ", "an ")
_FORMS = {
    "tfng": {"t": "true", "f": "false"},
    "ynng": {"y": "yes", "n": "no"},
}


@dataclass(frozen=True)
class GradableQuestion:
    number: int
    question_type: str
    word_limit: int | None
    answer_group: int | None
    accepted: tuple[str, ...]


@dataclass(frozen=True)
class Mark:
    number: int
    given: str | None
    correct: bool
    reason: Reason


def normalize(text: str) -> str:
    s = " ".join(unicodedata.normalize("NFC", text).split())
    return s.rstrip(_TRAILING + " ").casefold()


def _canonical(q: GradableQuestion, text: str, rules: RuleSet) -> str:
    s = normalize(text)
    forms = dict(_FORMS.get(q.question_type, {}))
    if rules == "pilot-1" and q.question_type in _FORMS:
        forms["ng"] = "not given"
    return forms.get(s, s)


def _accepted(q: GradableQuestion, rules: RuleSet) -> set[str]:
    return {_canonical(q, a, rules) for a in q.accepted}


def _strip_article(s: str) -> str:
    for art in _ARTICLES:
        if s.startswith(art):
            return s[len(art):]
    return s


def _given(answers: Mapping[int, str], number: int) -> str | None:
    raw = answers.get(number)
    return raw if raw is not None and normalize(raw) else None


def _grade_single(q: GradableQuestion, given: str, rules: RuleSet) -> Mark:
    s = _canonical(q, given, rules)
    if q.word_limit is not None and len(s.split()) > q.word_limit:
        return Mark(q.number, given, False, "over_word_limit")
    accepted = _accepted(q, rules)
    ok = s in accepted or (rules == "pilot-1" and _strip_article(s) in accepted)
    return Mark(q.number, given, ok, "correct" if ok else "wrong")


def _over_limit(q: GradableQuestion, given: str, rules: RuleSet) -> bool:
    return q.word_limit is not None and len(_canonical(q, given, rules).split()) > q.word_limit


def _grade_group(
    group: Sequence[GradableQuestion], answers: Mapping[int, str], rules: RuleSet
) -> dict[int, Mark]:
    # distinct typed answers (first question to type one owns it) vs answer slots
    owners: dict[str, int] = {}
    for q in sorted(group, key=lambda x: x.number):
        g = _given(answers, q.number)
        if g is not None and not _over_limit(q, g, rules):
            owners.setdefault(_canonical(q, g, rules), q.number)
    slots = {q.number: _accepted(q, rules) for q in group}
    taken: dict[int, str] = {}  # slot -> input

    def place(inp: str, seen: set[int]) -> bool:
        for n, acc in slots.items():
            if (inp in acc or (rules == "pilot-1" and _strip_article(inp) in acc)) and n not in seen:
                seen.add(n)
                if n not in taken or place(taken[n], seen):
                    taken[n] = inp
                    return True
        return False

    for inp in owners:
        place(inp, set())
    scored = set(taken.values())
    marks: dict[int, Mark] = {}
    for q in group:
        g = _given(answers, q.number)
        if g is None:
            marks[q.number] = Mark(q.number, None, False, "blank")
        elif _over_limit(q, g, rules):
            marks[q.number] = Mark(q.number, g, False, "over_word_limit")
        else:
            ok = owners[_canonical(q, g, rules)] == q.number and _canonical(q, g, rules) in scored
            marks[q.number] = Mark(q.number, g, ok, "correct" if ok else "wrong")
    return marks


def grade(questions: Sequence[GradableQuestion], answers: Mapping[int, str], rules: RuleSet) -> list[Mark]:
    groups: dict[int, list[GradableQuestion]] = {}
    for q in questions:
        if q.answer_group is not None:
            groups.setdefault(q.answer_group, []).append(q)
    grouped = {n: m for g in groups.values() for n, m in _grade_group(g, answers, rules).items()}
    out: list[Mark] = []
    for q in questions:
        if q.number in grouped:
            out.append(grouped[q.number])
            continue
        given = _given(answers, q.number)
        out.append(Mark(q.number, None, False, "blank") if given is None else _grade_single(q, given, rules))
    return out
