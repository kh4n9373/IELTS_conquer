from dataclasses import replace
from uuid import uuid4

from npa_api.explain.batch import (
    Explanation,
    Item,
    batches,
    build_prompt,
    chosen_reason,
    content_hash,
    marked_choices,
    parse_reply,
)


def item(number: int, passage: str = "P", **kw: object) -> Item:
    base = Item(uuid4(), number, "multiple_choice", f"Q{number}?", ["red", "blue"], ["B"], passage)
    return replace(base, **kw)  # type: ignore[arg-type]


def test_hash_changes_with_options() -> None:
    a = item(1)
    assert content_hash(a) != content_hash(replace(a, options=["red", "green"]))


def test_hash_ignores_question_id() -> None:
    a = item(1)
    assert content_hash(a) == content_hash(replace(a, question_id=uuid4()))


def test_batches_split_at_20_and_by_passage() -> None:
    items = [item(n, "P") for n in range(1, 26)] + [item(n, "Q") for n in range(26, 29)]
    out = batches(items)
    assert [len(b) for b in out] == [20, 5, 3]
    assert all(len({i.passage for i in b}) == 1 for b in out)


def test_prompt_lists_choices_and_key_once_per_passage() -> None:
    text = build_prompt([item(1, "The passage."), item(2, "The passage.", question_type="tfng", options=[])])
    assert text.count("The passage.") == 1
    assert "A. red" in text and "B. blue" in text
    assert "TRUE" in text and "NOT GIVEN" in text


def reply(*entries: tuple[int, str, list[tuple[str, str]]]) -> dict[str, object]:
    return {
        "items": [
            {"number": n, "correct": c, "options": [{"answer": a, "reason": r} for a, r in opts]}
            for n, c, opts in entries
        ]
    }


def test_parse_keeps_only_batch_numbers() -> None:
    batch = [item(1), item(2)]
    out = parse_reply(batch, reply((1, "first", []), (2, "two", []), (99, "x", []), (1, "dup", [])))
    assert [(e.question_id, e.correct) for e in out] == [
        (batch[0].question_id, "first"),
        (batch[1].question_id, "two"),
    ]


def test_parse_drops_empty_and_trims() -> None:
    batch = [item(1), item(2)]
    out = parse_reply(batch, reply((1, "", []), (2, "y" * 1000, [("A", "z" * 1000)])))
    assert len(out) == 1
    assert len(out[0].correct) == 600 and len(out[0].options["a"]) == 600


def test_parse_canonicalises_and_drops_key_and_unknown_choices() -> None:
    q = item(1, question_type="tfng", options=[], accepted=["TRUE"])
    out = parse_reply([q], reply((1, "ok", [("F", "no"), ("TRUE", "key"), ("maybe", "?")])))
    assert out[0].options == {"false": "no"}


def test_parse_survives_garbage() -> None:
    assert parse_reply([item(1)], {"items": "nope"}) == []
    assert parse_reply([item(1)], {"items": [None, {"number": "1"}, {"number": 1, "correct": 5}]}) == []


def test_chosen_reason_forms() -> None:
    e = Explanation(uuid4(), "c", {"false": "x", "not given": "ng"})
    assert chosen_reason(e.options, "tfng", ["TRUE"], "F") == "x"
    assert chosen_reason(e.options, "tfng", ["TRUE"], "NG") == "ng"
    assert chosen_reason(e.options, "tfng", ["TRUE"], None) is None
    assert chosen_reason(e.options, "tfng", ["TRUE"], "  ") is None
    assert chosen_reason(e.options, "tfng", ["TRUE"], "t") is None


def test_chosen_reason_multi_joins_wrong_letters() -> None:
    e = Explanation(uuid4(), "c", {"b": "not b", "d": "not d"})
    assert chosen_reason(e.options, "multiple_choice_multi", ["A, C"], "a, b, d") == "not b\nnot d"
    assert chosen_reason(e.options, "multiple_choice", ["B"], "b") is None


def test_parse_multi_drops_key_letters() -> None:
    q = item(1, question_type="multiple_choice_multi", options=["a", "b", "c"], accepted=["A, C"])
    out = parse_reply([q], reply((1, "ok", [("A", "k"), ("B", "no"), ("C", "k")])))
    assert out[0].options == {"b": "no"}


def test_matching_choices_are_the_option_keys() -> None:
    q = item(1, question_type="matching_headings", options=["i", "ii", "iii"], accepted=["ii"])
    text = build_prompt([q])
    assert "A. i" not in text and "Choices: i | ii | iii" in text
    out = parse_reply([q], reply((1, "ok", [("iii", "no"), ("C", "?")])))
    assert out[0].options == {"iii": "no"}


def test_marked_choices() -> None:
    mcq = marked_choices("multiple_choice", ["red", "blue", "green"], ["B"], "c")
    assert mcq == [
        {"label": "A", "text": "red", "mark": None},
        {"label": "B", "text": "blue", "mark": "key"},
        {"label": "C", "text": "green", "mark": "wrong"},
    ]
    tf = marked_choices("tfng", [], ["TRUE"], "F.")
    assert [c["mark"] for c in tf or []] == ["key", "wrong", None]
    feat = marked_choices("matching_features", ["A", "B", "F"], ["F"], "b")
    assert [(c["label"], c["mark"]) for c in feat or []] == [("", None), ("", "wrong"), ("", "key")]
    assert marked_choices("short_answer", [], ["1887"], None) is None
