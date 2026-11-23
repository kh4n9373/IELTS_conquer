"""S4 (mixed inputs) and an IELTS-standard test through the real publish route. Invented answers."""

from typing import Any

import pytest
from fakes import make_pdf
from m3_helpers import make_class_with_students
from test_attempts import enter, item, save

from conftest import Harness

OPEN = "2026-11-22T04:00:00Z"
DUE = "2026-11-23T04:00:00Z"


@pytest.fixture(scope="module")
def teacher(h: Harness) -> str:
    h.create_user("acc-teacher@test.local", "teacher")
    return h.login("acc-teacher@test.local")


def publish_sections(
    h: Harness, cookie: str, test_type: str, sections: list[dict[str, Any]], answers: dict[int, list[str]]
) -> str:
    created = h.call(
        cookie,
        "POST",
        "/api/tests",
        {"title": "Acceptance", "sourceType": "self_authored", "testType": test_type, "durationMinutes": 60},
    )
    assert created.status == 201
    tid: str = created.json["id"]
    h.client.cookies.clear()
    upload = h.client.put(
        f"/api/tests/{tid}/pdf",
        content=make_pdf(8),
        headers={"content-type": "application/pdf", "cookie": cookie},
    )
    assert upload.status_code == 200
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/structure", {"sections": sections}).status == 200
    items = [{"number": n, "answers": a} for n, a in sorted(answers.items())]
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/key/items", {"items": items}).status == 200
    assert h.call(cookie, "POST", f"/api/tests/{tid}/key/review", {"items": items}).status == 200
    published = h.call(cookie, "POST", f"/api/tests/{tid}/publish")
    assert published.status == 200, published.json
    return tid


def take(h: Harness, teacher: str, tid: str, given: dict[int, str]) -> dict[str, Any]:
    class_id, students = make_class_with_students(h, teacher, 1)
    body = {"testId": tid, "classId": class_id, "opensAt": OPEN, "dueAt": DUE, "maxAttempts": 1,
            "mode": "exam"}
    assigned = h.call(teacher, "POST", "/api/assignments", body)
    assert assigned.status == 201, assigned.json
    student = students[0]
    with h.later(hours=2):
        attempt, token = enter(h, student, str(assigned.json["id"]))
        rows = [item(n, v, i) for i, (n, v) in enumerate(given.items(), 1)]
        saved = save(h, student, attempt, token, *rows)
        assert saved.status == 200
        assert h.call(student.cookie, "POST", f"/api/attempts/{attempt}/submit").status == 200
    with h.later(hours=2, minutes=30):
        res = h.call(student.cookie, "GET", f"/api/attempts/{attempt}/result")
    assert res.status == 200 and res.json["released"] is True
    result: dict[str, Any] = res.json
    return result


def test_s4_mixed_inputs(h: Harness, teacher: str) -> None:
    section = {
        "title": "Passage 1",
        "passagePages": [1, 2],
        "questionPages": [3],
        "ranges": [
            {"from": 1, "to": 2, "questionType": "tfng"},
            {"from": 3, "to": 3, "questionType": "ynng"},
            {"from": 4, "to": 7, "questionType": "short_answer", "wordLimit": 2},
            {"from": 8, "to": 9, "questionType": "multiple_choice", "answerGroup": 1},
        ],
    }
    key = {
        1: ["True"], 2: ["False"], 3: ["Yes"], 4: ["colour"], 5: ["colour"], 6: ["red house"],
        7: ["necessary"], 8: ["B", "D"], 9: ["B", "D"],
    }
    tid = publish_sections(h, teacher, "course", [section], key)
    given = {1: "t", 2: "FALSE", 3: "t", 4: "colour", 5: "color", 6: "big red house", 7: "neccessary",
             8: "D", 9: "B"}
    body = take(h, teacher, tid, given)
    verdict = {i["number"]: i["correct"] for i in body["items"]}
    assert verdict == {1: True, 2: True, 3: False, 4: True, 5: False, 6: False, 7: False, 8: True, 9: True}
    assert (body["correct"], body["total"]) == (5, 9)
    assert "band" not in body


def test_ielts_standard_published_through_real_route_gets_estimated_band(h: Harness, teacher: str) -> None:
    spans = [(1, 13, [1], [2]), (14, 26, [3], [4]), (27, 40, [5], [6])]
    sections = [
        {
            "title": f"Passage {n}",
            "passagePages": passage,
            "questionPages": questions,
            "ranges": [{"from": a, "to": b, "questionType": "short_answer", "wordLimit": 3}],
        }
        for n, (a, b, passage, questions) in enumerate(spans, 1)
    ]
    tid = publish_sections(h, teacher, "ielts_standard", sections, {n: [f"w{n}"] for n in range(1, 41)})
    body = take(h, teacher, tid, {n: f"w{n}" if n <= 30 else "wrong" for n in range(1, 41)})
    assert (body["correct"], body["total"]) == (30, 40)
    assert (body["band"], body["bandLabel"], body["bandTableVersion"]) == (
        "7.0", "estimated", "academic-reading-2026-1")
    assert len(body["sections"]) == 3
