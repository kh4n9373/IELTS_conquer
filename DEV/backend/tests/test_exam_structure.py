from collections import Counter
from typing import Any

import pytest

from conftest import Harness
from npa_api.exams.readiness import readiness
from npa_api.exams.types import QUESTION_GROUPS


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, str]:
    h.create_user("st-a@test.local", "teacher")
    h.create_user("st-b@test.local", "teacher")
    h.create_user("st-s@test.local", "student", h.create_user("st-c@test.local", "teacher"))
    return {"a": h.login("st-a@test.local"), "b": h.login("st-b@test.local"), "s": h.login("st-s@test.local")}


def make(h: Harness, cookie: str, **extra: Any) -> str:
    body = {"title": "Test", "sourceType": "self_authored", **extra}
    res = h.call(cookie, "POST", "/api/tests", body)
    assert res.status == 201
    return str(res.json["id"])


def rng(a: int, b: int, kind: str = "tfng", **extra: Any) -> dict[str, Any]:
    return {"from": a, "to": b, "questionType": kind, **extra}


def section(*ranges: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"title": "Passage", "ranges": list(ranges), **extra}


def put(h: Harness, cookie: str, test_id: str, *sections: dict[str, Any]) -> Any:
    return h.call(cookie, "PUT", f"/api/tests/{test_id}/structure", {"sections": list(sections)})


def test_range_assignment_sets_type_for_questions_1_to_6(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    res = put(h, t["a"], tid, section(rng(1, 6, "sentence_completion", wordLimit=2)))
    assert res.status == 200
    questions = res.json["sections"][0]["questions"]
    assert [q["number"] for q in questions] == [1, 2, 3, 4, 5, 6]
    assert {q["questionType"] for q in questions} == {"sentence_completion"}
    assert {q["wordLimit"] for q in questions} == {2}
    assert res.json["sections"][0]["ranges"] == [rng(1, 6, "sentence_completion", wordLimit=2)]
    assert "band" not in str(res.json)


def test_structure_rejects_overlapping_question_ranges(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    res = put(h, t["a"], tid, section(rng(1, 6)), section(rng(6, 10)))
    assert (res.status, res.json) == (400, {"error": "invalid_input"})
    assert put(h, t["a"], tid, section(rng(1, 6, wordLimit=1))).status == 400


def test_word_limit_rejected_outside_completion(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert put(h, t["a"], tid, section(rng(1, 2, "matching_headings", wordLimit=2))).status == 400


def test_course_test_allows_5_passages_120_minutes(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"], testType="course", durationMinutes=120)
    sections = [section(rng(i * 8 + 1, i * 8 + 8)) for i in range(5)]
    assert put(h, t["a"], tid, *sections).status == 200
    assert "ielts_shape" not in h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]


def test_ielts_test_with_5_passages_reports_ielts_shape(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"], testType="ielts_standard", durationMinutes=60)
    assert put(h, t["a"], tid, *[section(rng(i * 8 + 1, i * 8 + 8)) for i in range(5)]).status == 200
    blockers = h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]
    assert "ielts_shape" in blockers
    assert blockers[0] == "missing_pdf"


def test_pilot_structure_groups_to_20_10_6_4() -> None:
    ranges = [(20, "sentence_completion"), (10, "matching_headings"), (6, "multiple_choice"), (4, "ynng")]
    counts = Counter(QUESTION_GROUPS[kind] for n, kind in ranges for _ in range(n))
    assert [counts[g] for g in ("completion", "matching", "multiple_choice", "tfng")] == [20, 10, 6, 4]


def test_every_test_requires_a_source_type(h: Harness, t: dict[str, str]) -> None:
    assert h.call(t["a"], "POST", "/api/tests", {"title": "No source"}).status == 400
    assert h.call(t["a"], "POST", "/api/tests", {"title": "Bad", "sourceType": "stolen"}).status == 400


def test_gap_is_saved_and_reported(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert put(h, t["a"], tid, section(rng(1, 3)), section(rng(5, 6))).status == 200
    assert "question_gap" in h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]


def test_patch_edits_draft_only_and_audits(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    res = h.call(t["a"], "PATCH", f"/api/tests/{tid}", {"title": "New", "durationMinutes": 90})
    assert (res.status, res.json["title"], res.json["durationMinutes"]) == (200, "New", 90)
    assert h.call(t["a"], "PATCH", f"/api/tests/{tid}", {}).status == 400
    assert h.call(t["a"], "PATCH", f"/api/tests/{tid}", {"status": "published"}).status == 400
    h.owner_rows("update tests set status = 'published' where id = %s", (tid,))
    assert h.call(t["a"], "PATCH", f"/api/tests/{tid}", {"title": "X"}).json == {"error": "test_locked"}
    assert put(h, t["a"], tid, section(rng(1, 2))).json == {"error": "test_locked"}
    actions = h.owner_rows("select action from audit_log where entity_id = %s order by created_at", (tid,))
    assert [r["action"] for r in actions] == ["test.create", "test.update"]


def test_other_tenant_gets_404_before_any_insert(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert put(h, t["a"], tid, section(rng(1, 2))).status == 200
    for res in (
        put(h, t["b"], tid, section(rng(1, 2))),
        h.call(t["b"], "GET", f"/api/tests/{tid}"),
        h.call(t["b"], "GET", f"/api/tests/{tid}/readiness"),
    ):
        assert (res.status, res.json) == (404, {"error": "not_found"})
    assert h.call(t["b"], "GET", "/api/tests").json == []


def test_students_cannot_use_test_routes(h: Harness, t: dict[str, str]) -> None:
    assert h.call(t["s"], "GET", "/api/tests").status == 403


def test_readiness_page_checks_follow_the_pdf(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    put(h, t["a"], tid, section(rng(1, 2), passagePages=[1, 9]))
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"][:2] == [
        "missing_pdf",
        "missing_answer",
    ]
    owner = h.owner_rows("select teacher_id from tests where id = %s", (tid,))[0]["teacher_id"]
    h.owner_rows(
        """insert into test_assets (test_id, teacher_id, kind, storage_key, sha256, byte_size, page_count)
           values (%s, %s, 'student_pdf', 'k', 'h', 1, 4)""",
        (tid, owner),
    )
    blockers = h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]
    assert "page_out_of_range" in blockers
    assert "missing_pdf" not in blockers


def test_readiness_pure_answer_states() -> None:
    test = {"test_type": "course", "duration_minutes": 60}
    assets = [{"kind": "student_pdf", "page_count": 3}]
    sections = [{"passage_pages": [1], "question_pages": [2]}]
    questions = [{"number": n, "question_type": "tfng"} for n in (1, 2, 3)]
    items = [
        {"question_number": 1, "answers": ["TRUE"], "flagged": False, "reviewed_at": "x"},
        {"question_number": 2, "answers": ["FALSE"], "flagged": False, "reviewed_at": None},
    ]
    assert readiness(test, assets, sections, questions, items) == ["missing_answer", "unreviewed_answer"]
    assert readiness(test, assets, sections, questions, []) == ["missing_answer"]
