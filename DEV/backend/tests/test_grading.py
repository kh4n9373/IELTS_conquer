from collections.abc import Mapping, Sequence

import pytest

from npa_api.grading.band import band_for
from npa_api.grading.rules import GradableQuestion, RuleSet, grade, normalize


def mark(qtype, accepted, given, limit=None, rules: RuleSet = "standard-1"):
    q = GradableQuestion(1, qtype, limit, None, tuple(accepted))
    return grade([q], {1: given}, rules)[0]


def group_of(slots: Mapping[int, Sequence[str]]):
    return [GradableQuestion(n, "multi_choice", None, 1, tuple(a)) for n, a in slots.items()]


def correct_numbers(questions, answers):
    return {m.number for m in grade(questions, answers, "standard-1") if m.correct}


@pytest.mark.parametrize("given", ["t", "T", "TRUE", " true. "])
def test_tf_short_forms(given):
    assert mark("tfng", ["TRUE"], given).correct


def test_t_is_not_yes():
    assert not mark("ynng", ["YES"], "t").correct


@pytest.mark.parametrize("given", ["y", "YES"])
def test_yes_short_forms(given):
    assert mark("ynng", ["YES"], given).correct


def test_ng_only_under_pilot_rules():
    assert not mark("tfng", ["NOT GIVEN"], "NG", rules="standard-1").correct
    assert mark("tfng", ["NOT GIVEN"], "NG", rules="pilot-1").correct


def test_variants_only_when_approved():
    assert mark("note_completion", ["colour", "color"], "COLOR").correct
    assert not mark("note_completion", ["colour"], "color").correct


def test_misspelling_is_wrong():
    assert not mark("note_completion", ["river"], "rivr").correct


def test_hyphen_counts_as_one_word():
    assert mark("note_completion", ["well-known place"], "well-known place", limit=2).correct


def test_over_limit_is_wrong_even_if_it_contains_key():
    m = mark("note_completion", ["tall trees"], "the tall trees", limit=2, rules="standard-1")
    assert m.reason == "over_word_limit"
    assert not m.correct


def test_article_counts_before_stripping_under_pilot():
    assert mark("note_completion", ["river"], "the river", limit=2, rules="pilot-1").correct
    m = mark("note_completion", ["tall trees"], "the tall trees", limit=2, rules="pilot-1")
    assert m.reason == "over_word_limit"
    assert not mark("note_completion", ["river"], "the river", limit=2, rules="standard-1").correct


def test_only_one_leading_article_is_stripped():
    assert not mark("note_completion", ["river"], "the the river", limit=3, rules="pilot-1").correct


def test_trailing_punctuation_only():
    assert normalize("  Tall   Trees.! ") == "tall trees"
    assert normalize("U.K.") == "u.k"
    assert normalize("co-op") == "co-op"


def test_unicode_composed_forms_match():
    assert mark("short_answer", ["café"], "café").correct


def test_number_format_only_via_variant():
    assert not mark("short_answer", ["3"], "three").correct


def test_multi_answer_any_order_each_point():
    assert correct_numbers(group_of({1: ["A"], 2: ["C"]}), {1: "C", 2: "A"}) == {1, 2}
    assert correct_numbers(group_of({1: ["A"], 2: ["C"]}), {1: "A", 2: "B"}) == {1}


def test_multi_answer_duplicate_scores_once():
    assert len(correct_numbers(group_of({1: ["A"], 2: ["C"]}), {1: "A", 2: "a"})) == 1


def test_blank_is_blank_not_wrong_text():
    assert mark("tfng", ["TRUE"], "   ").reason == "blank"
    assert grade([GradableQuestion(1, "tfng", None, None, ("TRUE",))], {}, "standard-1")[0].reason == "blank"


def test_same_input_same_output():
    qs = group_of({1: ["A"], 2: ["C"]})
    answers = {1: "C", 2: "a"}
    assert grade(qs, answers, "pilot-1") == grade(qs, answers, "pilot-1")


@pytest.mark.parametrize(
    "correct,band",
    [(40, "9.0"), (37, "8.5"), (35, "8.0"), (33, "7.5"), (30, "7.0"), (27, "6.5"),
     (23, "6.0"), (19, "5.5"), (15, "5.0"), (13, "4.5"), (10, "4.0"), (9, "below_4.0"), (0, "below_4.0")],
)
def test_band_table(correct, band):
    assert band_for(correct, 40, "ielts_standard") == band


def test_no_band_for_course_or_partial_test():
    assert band_for(30, 40, "course") is None
    assert band_for(30, 39, "ielts_standard") is None
