import json
import re
from pathlib import Path
from typing import Any

import pytest
from m3_helpers import make_class_with_students

from conftest import Harness
from npa_api.exams import schema_v1

GUIDE = Path(schema_v1.__file__).parent / "panxg-exam-v1-ai-guide.md"


def example() -> dict[str, Any]:
    block = re.search(r"```json\n(.*?)```", GUIDE.read_text("utf-8"), re.DOTALL)
    assert block is not None
    data: dict[str, Any] = json.loads(block.group(1))
    return data


def codes(raw: Any) -> list[str]:
    _, errors, _ = schema_v1.check(raw if isinstance(raw, str) else json.dumps(raw))
    return [e["code"] for e in errors]


# ---- schema, no database -------------------------------------------------


def test_guide_example_is_valid_and_flags_uncertain() -> None:
    doc, errors, warnings = schema_v1.check(json.dumps(example()))
    assert errors == []
    assert doc is not None
    assert [w["number"] for w in warnings if w["code"] == "uncertain_answer"] == [12]
    assert schema_v1.summary(doc)["questions"] == 12


def test_guide_names_every_supported_type() -> None:
    text = GUIDE.read_text("utf-8")
    assert all(f"`{t}`" in text for t in schema_v1.SUPPORTED_TYPES)


def test_ai_wrapping_is_tolerated() -> None:
    wrapped = "Here you go:\n```json\n" + json.dumps(example()) + "\n```\nHope it helps"
    assert codes(wrapped) == []
    assert codes("Sure! " + json.dumps(example()) + " Done.") == []


def test_errors_are_reported() -> None:
    assert codes("not json") == ["invalid_json"]
    bad = example()
    bad["sections"][0]["groups"][0]["type"] = "diagram_label"
    assert "unsupported_type" in codes(bad)
    bad = example()
    bad["sections"][0]["groups"][0]["questions"][0]["number"] = 99
    assert "number_gap" in codes(bad)
    bad = example()
    bad["sections"][0]["groups"][1]["questions"][0]["answers"] = ["Z"]
    assert "answer_not_option" in codes(bad)
    bad = example()
    bad["sections"][0]["groups"][2]["questions"][1]["answers"] = ["too many words"]
    assert "answer_too_long" in codes(bad)
    bad = example()
    bad["sections"][0]["extra"] = 1
    assert "unknown_field" in codes(bad)
    bad = example()
    bad["sections"][1]["groups"][1]["questions"][1]["answers"] = ["A"]
    assert "duplicate_answer" in codes(bad)
    bad = example()
    bad["sections"][0]["groups"][2]["content"] += " {{40}}"
    assert "bad_placeholder" in codes(bad)
    bad = example()
    bad["sections"][0]["groups"][0]["questions"][0]["answers"] = ["Maybe"]
    assert "invalid_answer" in codes(bad)


def test_to_manual_maps_groups_onto_questions() -> None:
    doc, _, _ = schema_v1.check(json.dumps(example()))
    assert doc is not None
    sections = schema_v1.to_manual(doc)
    qs = [q for s in sections for q in s["questions"]]
    assert [q["number"] for q in qs] == list(range(1, 13))
    assert sections[0]["body"].startswith("Rooftop gardens")
    for q in qs:
        assert len(q["prompt"]) <= schema_v1.MAX_PROMPT
        if schema_v1.QUESTION_GROUPS[q["type"]] == "multiple_choice":
            assert all(a in schema_v1.LETTERS[: len(q["options"])] for a in q["answers"])
        elif q["options"]:
            assert all(a in q["options"] for a in q["answers"])  # the student picks a key
    assert [(q["number"], q["answer_group"]) for q in qs if q["answer_group"]] == [(9, 9), (10, 9)]
    assert "{{" not in json.dumps([q["prompt"] for q in qs])
    assert [q["number"] for q in qs if q["uncertain"]] == [12]


def test_json_schema_is_generated() -> None:
    schema = schema_v1.json_schema()
    assert schema["title"] == "panxg-exam/v1"
    assert "schema" in schema["properties"]


# ---- API -----------------------------------------------------------------


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, str]:
    h.create_user("imp-a@test.local", "teacher")
    h.create_user("imp-b@test.local", "teacher")
    return {"a": h.login("imp-a@test.local"), "b": h.login("imp-b@test.local")}


def new_manual(h: Harness, cookie: str) -> str:
    body = {"title": "Typed", "sourceType": "self_authored", "testType": "course", "durationMinutes": 30,
            "authoring": "manual"}  # fmt: skip
    res = h.call(cookie, "POST", "/api/tests", body)
    assert res.status == 201
    return str(res.json["id"])


def import_into(h: Harness, cookie: str, tid: str, data: Any) -> Any:
    return h.call(cookie, "POST", f"/api/tests/{tid}/import", {"json": json.dumps(data)})


def imported(h: Harness, cookie: str) -> str:
    tid = new_manual(h, cookie)
    assert import_into(h, cookie, tid, example()).status == 200
    return tid


def test_validate_returns_errors_and_summary(h: Harness, t: dict[str, str]) -> None:
    ok = h.call(t["a"], "POST", "/api/tests/import/validate", {"json": json.dumps(example())})
    assert ok.status == 200 and ok.json["ok"] and ok.json["summary"]["questions"] == 12
    bad = h.call(t["a"], "POST", "/api/tests/import/validate", {"json": "{}"})
    assert bad.status == 200 and not bad.json["ok"] and bad.json["errors"]
    assert import_into(h, t["a"], new_manual(h, t["a"]), {}).status == 400


def test_import_fills_manual_test_with_unreviewed_key(h: Harness, t: dict[str, str]) -> None:
    tid = new_manual(h, t["a"])
    res = import_into(h, t["a"], tid, example())
    assert res.status == 200
    assert res.json["authoring"] == "manual" and res.json["title"] == "Typed"  # details stay the teacher's
    test = h.call(t["a"], "GET", f"/api/tests/{tid}").json
    assert sum(len(s["questions"]) for s in test["sections"]) == 12
    grouped = [q for s in test["sections"] for q in s["questions"] if q["answerGroup"]]
    assert [(q["number"], q["answerGroup"]) for q in grouped] == [(9, 9), (10, 9)]
    key = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json
    assert {i["origin"] for i in key["items"]} == {"import"}
    assert [i["number"] for i in key["items"] if i["flagged"]] == [12]
    assert key["items"][11]["note"].startswith("The answer key page")
    assert key["items"][5]["answers"] == ["four", "4"]
    ready = h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json
    assert ready["blockers"] == ["unreviewed_answer"]  # no PDF needed
    assert h.call(t["b"], "GET", f"/api/tests/{tid}").status == 404


def test_manual_editor_save_keeps_answer_groups(h: Harness, t: dict[str, str]) -> None:
    tid = imported(h, t["a"])
    sections = h.call(t["a"], "GET", f"/api/tests/{tid}").json["sections"]
    keep = ("number", "questionType", "wordLimit", "prompt", "options", "answerGroup")
    body = {
        "sections": [
            {
                "title": s["title"],
                "body": s["body"],
                "questions": [{k: q[k] for k in keep if q[k] is not None} for q in s["questions"]],
            }
            for s in sections
        ]
    }
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", body).status == 200
    again = h.call(t["a"], "GET", f"/api/tests/{tid}").json["sections"]
    grouped = [(q["number"], q["answerGroup"]) for s in again for q in s["questions"] if q["answerGroup"]]
    assert grouped == [(9, 9), (10, 9)]


def test_import_again_appends_after_existing_questions(h: Harness, t: dict[str, str]) -> None:
    tid = imported(h, t["a"])
    res = import_into(h, t["a"], tid, example())
    assert res.status == 200
    numbers = [q["number"] for s in res.json["sections"] for q in s["questions"]]
    assert numbers == list(range(1, 25))
    assert [s["position"] for s in res.json["sections"]] == [1, 2, 3, 4]
    questions = [q for s in res.json["sections"] for q in s["questions"]]
    grouped = [(q["number"], q["answerGroup"]) for q in questions if q["answerGroup"]]
    assert grouped == [(9, 9), (10, 9), (21, 21), (22, 21)]
    blanks = next(q["prompt"] for q in questions if q["number"] == 19)
    assert "(19) ________" in blanks and "(7)" not in blanks
    key = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json
    assert [i["number"] for i in key["items"]] == list(range(1, 25))
    assert [i["number"] for i in key["items"] if i["flagged"]] == [12, 24]


def test_import_refused_on_pdf_test_and_published(h: Harness, t: dict[str, str]) -> None:
    body = {"title": "P", "sourceType": "self_authored", "testType": "course", "durationMinutes": 30}
    pdf = h.call(t["a"], "POST", "/api/tests", body).json["id"]
    assert import_into(h, t["a"], pdf, example()).status == 409
    tid = imported(h, t["a"])
    key = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json
    items = [{"number": i["number"], "answers": i["answers"]} for i in key["items"]]
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"items": items}).status == 200
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/publish").status == 200
    assert import_into(h, t["a"], tid, example()).status == 409
    assert import_into(h, t["b"], tid, example()).status == 404


def test_student_room_gets_text_without_answers(h: Harness, t: dict[str, str]) -> None:
    tid = imported(h, t["a"])
    key = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json
    items = [{"number": i["number"], "answers": i["answers"]} for i in key["items"]]
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"items": items}).status == 200
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/publish").status == 200
    class_id, students = make_class_with_students(h, t["a"], 1)
    body = {
        "testId": tid,
        "classId": class_id,
        "opensAt": "2026-11-22T00:00:00Z",
        "dueAt": "2026-11-23T04:00:00Z",
        "maxAttempts": 1,
        "mode": "exam",
    }
    assignment = h.call(t["a"], "POST", "/api/assignments", body)
    assert assignment.status == 201
    student = students[0]
    started = h.call(
        student.cookie, "POST", f"/api/my/assignments/{assignment.json['id']}/attempts", {"device": "desktop"}
    )
    assert started.status == 200
    room = h.call(student.cookie, "GET", f"/api/attempts/{started.json['attemptId']}").json
    assert room["authoring"] == "manual"
    assert room["sections"][0]["body"].startswith("Rooftop gardens")
    assert room["questions"][0]["prompt"]
    text = json.dumps(room)
    assert "uncertain" not in text and "The answer key page" not in text


def test_guide_is_served_to_teachers(h: Harness, t: dict[str, str]) -> None:
    res = h.call(t["a"], "GET", "/api/exam-schema/v1/ai-guide.md")
    assert res.status == 200 and "panxg-exam/v1" in res.json if isinstance(res.json, str) else True
    assert h.call(None, "GET", "/api/exam-schema/v1/ai-guide.md").status == 401
    assert h.call(t["a"], "GET", "/api/exam-schema/v1/schema.json").json["title"] == "panxg-exam/v1"
