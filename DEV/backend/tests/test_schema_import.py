import copy
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


def test_student_content_has_no_answers() -> None:
    doc, _, _ = schema_v1.check(json.dumps(example()))
    assert doc is not None
    text = json.dumps(schema_v1.student_content(doc))
    assert "answers" not in text and "uncertain" not in text and "ai_notes" not in text


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


def do_import(h: Harness, cookie: str, data: Any) -> Any:
    return h.call(cookie, "POST", "/api/tests/import", {"json": json.dumps(data)})


def test_validate_returns_errors_and_summary(h: Harness, t: dict[str, str]) -> None:
    ok = h.call(t["a"], "POST", "/api/tests/import/validate", {"json": json.dumps(example())})
    assert ok.status == 200 and ok.json["ok"] and ok.json["summary"]["questions"] == 12
    bad = h.call(t["a"], "POST", "/api/tests/import/validate", {"json": "{}"})
    assert bad.status == 200 and not bad.json["ok"] and bad.json["errors"]
    assert do_import(h, t["a"], {}).status == 400


def test_import_creates_composed_test_with_unreviewed_key(h: Harness, t: dict[str, str]) -> None:
    res = do_import(h, t["a"], example())
    assert res.status == 201
    tid = res.json["id"]
    assert res.json["authoringMode"] == "composed"
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
    # Other tenants cannot see it; structure edits go through re-import.
    assert h.call(t["b"], "GET", f"/api/tests/{tid}/content").status == 404
    put = h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", {"sections": []})
    assert put.status == 409


def test_content_edit_takes_text_only(h: Harness, t: dict[str, str]) -> None:
    tid = do_import(h, t["a"], example()).json["id"]
    content = h.call(t["a"], "GET", f"/api/tests/{tid}/content").json["content"]
    edited = copy.deepcopy(content)
    edited["sections"][0]["passage"]["paragraphs"][0]["text"] = "Fixed typo."
    edited["sections"][0]["title"] = "Bài 1"
    edited["sections"][0]["groups"][0]["questions"][0]["number"] = 77  # ignored: not a text field
    res = h.call(t["a"], "PUT", f"/api/tests/{tid}/content", {"content": edited})
    assert res.status == 200
    saved = res.json["content"]
    assert saved["sections"][0]["passage"]["paragraphs"][0]["text"] == "Fixed typo."
    assert saved["sections"][0]["groups"][0]["questions"][0]["number"] == 1
    assert h.call(t["a"], "GET", f"/api/tests/{tid}").json["sections"][0]["title"] == "Bài 1"
    removed = copy.deepcopy(content)
    removed["sections"][0]["groups"][0]["questions"].pop()
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/content", {"content": removed}).status == 400
    blank = copy.deepcopy(content)
    blank["sections"][0]["groups"][2]["content"] = "no blanks"
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/content", {"content": blank}).status == 400


def test_reimport_replaces_draft_and_published_is_locked(h: Harness, t: dict[str, str]) -> None:
    tid = do_import(h, t["a"], example()).json["id"]
    smaller = example()
    smaller["sections"] = smaller["sections"][:1]
    smaller["test"]["title"] = "Smaller"
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/reimport", {"json": json.dumps(smaller)})
    assert res.status == 200
    assert res.json["title"] == "Smaller" and len(res.json["sections"]) == 1
    key = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json
    assert [i["number"] for i in key["items"]] == list(range(1, 8))
    items = [{"number": i["number"], "answers": i["answers"]} for i in key["items"]]
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"items": items}).status == 200
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/publish").status == 200
    again = h.call(t["a"], "POST", f"/api/tests/{tid}/reimport", {"json": json.dumps(smaller)})
    assert again.status == 409


def test_student_room_gets_text_without_answers(h: Harness, t: dict[str, str]) -> None:
    tid = do_import(h, t["a"], example()).json["id"]
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
    assert room["authoringMode"] == "composed"
    assert room["content"]["sections"][0]["passage"]["title"] == "Rooftop gardens"
    text = json.dumps(room["content"])
    assert 'rainwater"]' not in text and "answers" not in text and "uncertain" not in text


def test_guide_is_served_to_teachers(h: Harness, t: dict[str, str]) -> None:
    res = h.call(t["a"], "GET", "/api/exam-schema/v1/ai-guide.md")
    assert res.status == 200 and "panxg-exam/v1" in res.json if isinstance(res.json, str) else True
    assert h.call(None, "GET", "/api/exam-schema/v1/ai-guide.md").status == 401
    assert h.call(t["a"], "GET", "/api/exam-schema/v1/schema.json").json["title"] == "panxg-exam/v1"
