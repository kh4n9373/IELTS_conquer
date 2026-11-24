"""Tests typed in the app (no PDF): passage text, prompts and options, through publish and grading."""

from typing import Any

from m3_helpers import make_class_with_students
from test_attempts import enter, item, save, submit_with

from conftest import Harness
from npa_api.grading.rules import GradableQuestion, grade

QUESTIONS: list[dict[str, Any]] = [
    {
        "number": 1,
        "questionType": "multiple_choice",
        "prompt": "Pick one",
        "options": ["red", "blue", "green"],
    },
    {
        "number": 2,
        "questionType": "multiple_choice_multi",
        "prompt": "Pick two",
        "options": ["a", "b", "c", "d"],
    },
    {"number": 3, "questionType": "sentence_completion", "wordLimit": 2, "prompt": "The sky is ___."},
    {
        "number": 4,
        "questionType": "summary_completion",
        "prompt": "Cats like ___.",
        "options": ["fish", "rain"],
    },
]
KEY = [
    {"number": 1, "answers": ["B"]},
    {"number": 2, "answers": ["A, C"]},
    {"number": 3, "answers": ["blue"]},
    {"number": 4, "answers": ["fish"]},
]


def create(h: Harness, cookie: str) -> str:
    body = {
        "title": "Typed",
        "sourceType": "self_authored",
        "testType": "course",
        "durationMinutes": 60,
        "authoring": "manual",
    }
    res = h.call(cookie, "POST", "/api/tests", body)
    assert res.status == 201 and res.json["authoring"] == "manual"
    return str(res.json["id"])


def structure(h: Harness, cookie: str, tid: str, questions: list[dict[str, Any]]) -> Any:
    section = {"title": "Bài 1", "body": "A short passage.\nSecond line.", "questions": questions}
    return h.call(cookie, "PUT", f"/api/tests/{tid}/structure", {"sections": [section]})


def blockers(h: Harness, cookie: str, tid: str) -> list[str]:
    return list(h.call(cookie, "GET", f"/api/tests/{tid}/readiness").json["blockers"])


def test_manual_test_round_trips_content_and_needs_no_pdf(h: Harness) -> None:
    h.create_user("mt-a@test.local", "teacher")
    cookie = h.login("mt-a@test.local")
    tid = create(h, cookie)
    res = structure(h, cookie, tid, QUESTIONS)
    assert res.status == 200
    sec = res.json["sections"][0]
    assert sec["body"] == "A short passage.\nSecond line."
    assert [(q["prompt"], q["options"]) for q in sec["questions"]][1] == ("Pick two", ["a", "b", "c", "d"])
    assert "missing_pdf" not in blockers(h, cookie, tid)
    assert "page_out_of_range" not in blockers(h, cookie, tid)


def test_choice_question_without_options_blocks_publish(h: Harness) -> None:
    h.create_user("mt-b@test.local", "teacher")
    cookie = h.login("mt-b@test.local")
    tid = create(h, cookie)
    assert structure(h, cookie, tid, [QUESTIONS[0] | {"options": ["only"]}]).status == 200
    assert "incomplete_question" in blockers(h, cookie, tid)
    assert structure(h, cookie, tid, [QUESTIONS[0] | {"prompt": " "}]).status == 200
    assert "incomplete_question" in blockers(h, cookie, tid)


def test_changing_options_clears_that_answer_review(h: Harness) -> None:
    h.create_user("mt-c@test.local", "teacher")
    cookie = h.login("mt-c@test.local")
    tid = create(h, cookie)
    structure(h, cookie, tid, QUESTIONS)
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/key/items", {"items": KEY}).status == 200
    assert h.call(cookie, "POST", f"/api/tests/{tid}/key/review", {"items": KEY}).status == 200
    assert blockers(h, cookie, tid) == []
    changed = [QUESTIONS[0] | {"options": ["blue", "red", "green"]}, *QUESTIONS[1:]]
    structure(h, cookie, tid, changed)
    assert blockers(h, cookie, tid) == ["unreviewed_answer"]


def test_pdf_test_still_requires_pdf(h: Harness) -> None:
    h.create_user("mt-d@test.local", "teacher")
    cookie = h.login("mt-d@test.local")
    body = {"title": "PDF", "sourceType": "self_authored", "testType": "course", "durationMinutes": 60}
    tid = str(h.call(cookie, "POST", "/api/tests", body).json["id"])
    assert "missing_pdf" in blockers(h, cookie, tid)


def test_student_room_shows_content_but_no_key_and_grades_multi_choice(h: Harness) -> None:
    h.create_user("mt-e@test.local", "teacher")
    cookie = h.login("mt-e@test.local")
    tid = create(h, cookie)
    structure(h, cookie, tid, QUESTIONS)
    h.call(cookie, "PUT", f"/api/tests/{tid}/key/items", {"items": KEY})
    h.call(cookie, "POST", f"/api/tests/{tid}/key/review", {"items": KEY})
    assert h.call(cookie, "POST", f"/api/tests/{tid}/publish").status == 200
    class_id, students = make_class_with_students(h, cookie, 1)
    assign = h.call(
        cookie,
        "POST",
        "/api/assignments",
        {
            "testId": tid,
            "classId": class_id,
            "opensAt": "2026-11-22T04:00:00Z",
            "dueAt": "2026-11-23T04:00:00Z",
            "maxAttempts": 1,
            "mode": "exam",
        },
    )
    assert assign.status == 201
    s = students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, str(assign.json["id"]))
        room = h.call(s.cookie, "GET", f"/api/attempts/{attempt}").json
        assert room["authoring"] == "manual" and room["pageCount"] is None
        assert room["sections"][0]["body"] == "A short passage.\nSecond line."
        assert room["questions"][3]["options"] == ["fish", "rain"]
        assert "fish" not in str(room["answers"]) and "answers" not in room["questions"][0]
        for i, (n, v) in enumerate([(1, "b"), (2, "C, A"), (3, "Blue"), (4, "rain")], 1):
            assert save(h, s, attempt, token, item(n, v, i)).status == 200
        assert submit_with(h, s, attempt, token).status == 200
    run = h.owner_rows("select correct, total from keys.grading_runs where attempt_id = %s", (attempt,))
    assert (run[0]["correct"], run[0]["total"]) == (3, 4)


def test_multi_choice_needs_the_exact_set() -> None:
    q = GradableQuestion(1, "multiple_choice_multi", None, None, ("A, C",))
    assert [grade([q], {1: g}, "standard-1")[0].correct for g in ["c,a", "A C", "A", "A, B, C"]] == [
        True,
        True,
        False,
        False,
    ]
