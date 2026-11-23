from typing import Any
from uuid import UUID

import psycopg
import pytest

from conftest import Harness
from npa_api.core.db import Identity, with_identity
from npa_api.exams.keys import parse_pasted_key

SECRET = "zebra"


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, Any]:
    a = h.create_user("k-a@test.local", "teacher")
    h.create_user("k-b@test.local", "teacher")
    h.create_user("k-s@test.local", "student", h.create_user("k-c@test.local", "teacher"))
    return {
        "a": h.login("k-a@test.local"),
        "b": h.login("k-b@test.local"),
        "s": h.login("k-s@test.local"),
        "id": a,
    }


def structure(count: int = 40, skip: int | None = None) -> list[dict[str, Any]]:
    numbers = [n for n in range(1, count + 1) if n != skip]
    parts = [numbers[:13], numbers[13:26], numbers[26:]]
    kinds = [("tfng", None), ("sentence_completion", 2), ("multiple_choice", None)]
    out = []
    for i, (part, (kind, limit)) in enumerate(zip(parts, kinds, strict=True)):
        ranges = []
        for n in part:  # one range per question keeps gaps representable
            r: dict[str, Any] = {"from": n, "to": n, "questionType": kind}
            if limit:
                r["wordLimit"] = limit
            ranges.append(r)
        out.append({"title": f"P{i + 1}", "passagePages": [1], "questionPages": [2], "ranges": ranges})
    return out


def make(h: Harness, t: dict[str, Any], count: int = 40, skip: int | None = None, pdf: bool = True) -> str:
    res = h.call(
        t["a"],
        "POST",
        "/api/tests",
        {"title": "K", "sourceType": "self_authored", "testType": "ielts_standard", "durationMinutes": 60},
    )
    tid = str(res.json["id"])
    put = h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", {"sections": structure(count, skip)})
    assert put.status == 200
    if pdf:
        h.owner_rows(
            """insert into test_assets (test_id, teacher_id, kind, storage_key, sha256, byte_size, page_count)
               values (%s, %s, 'student_pdf', 'k', 'h', 1, 8)""",
            (tid, t["id"]),
        )
    return tid


def key_items(numbers: Any) -> dict[str, Any]:
    return {"items": [{"number": n, "answers": [f"{SECRET}{n}"]} for n in numbers]}


def fill(h: Harness, t: dict[str, Any], tid: str, numbers: Any = range(1, 41), review: bool = True) -> None:
    numbers = list(numbers)
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", key_items(numbers)).status == 200
    if review:
        assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"numbers": numbers}).status == 200


def items_of(h: Harness, t: dict[str, Any], tid: str) -> dict[int, dict[str, Any]]:
    res = h.call(t["a"], "GET", f"/api/tests/{tid}/key")
    assert res.status == 200
    return {i["number"]: i for i in res.json["items"]}


def publish(h: Harness, t: dict[str, Any], tid: str) -> Any:
    return h.call(t["a"], "POST", f"/api/tests/{tid}/publish")


# ---- paste parsing ----


def test_paste_parses_variants_for_question_23() -> None:
    assert parse_pasted_key("23. three; platform three; 3; platform 3")[23] == [
        "three",
        "platform three",
        "3",
        "platform 3",
    ]


def test_paste_accepts_the_three_numbering_styles() -> None:
    assert parse_pasted_key("1. E\n2 F\n3) G\n\n  4.  not given ;; NG ") == {
        1: ["E"],
        2: ["F"],
        3: ["G"],
        4: ["not given", "NG"],
    }


@pytest.mark.parametrize("text", ["", "   \n", "12", "x. E", "1. ;", "1. A\n1. B", "0. A"])
def test_paste_rejects_malformed_text(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_pasted_key(text)


# ---- readiness gate ----


def test_publish_blocked_when_one_answer_missing(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid, range(1, 40))
    res = publish(h, t, tid)
    assert (res.status, res.json) == (409, {"error": "not_ready", "blockers": ["missing_answer"]})
    assert h.call(t["a"], "GET", f"/api/tests/{tid}").json["status"] == "draft"


def test_publish_blocked_when_one_type_is_missing(h: Harness, t: dict[str, Any]) -> None:
    # A question with no type cannot exist, so a lost question shows up as a numbering gap.
    tid = make(h, t, count=40, skip=20)
    fill(h, t, tid, [n for n in range(1, 41) if n != 20])
    res = publish(h, t, tid)
    assert res.status == 409
    assert "question_gap" in res.json["blockers"]


def test_publish_blocked_when_one_answer_unreviewed(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid, range(1, 41), review=False)
    h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"numbers": list(range(1, 40))})
    res = publish(h, t, tid)
    assert (res.status, res.json) == (409, {"error": "not_ready", "blockers": ["unreviewed_answer"]})


def test_publish_blocked_without_pdf(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    fill(h, t, tid)
    assert publish(h, t, tid).json == {"error": "not_ready", "blockers": ["missing_pdf"]}


def test_publish_succeeds_for_40_reviewed_questions_without_ocr(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid)
    assert h.owner_rows("select count(*) as n from ocr_jobs")[0]["n"] == 0
    res = publish(h, t, tid)
    assert res.status == 200
    assert res.json["status"] == "published"
    assert res.json["publishedAt"] is not None
    version = h.owner_rows(
        "select approved_at, approved_by from keys.answer_key_versions where test_id = %s", (tid,)
    )
    assert len(version) == 1
    assert version[0]["approved_at"] is not None
    assert version[0]["approved_by"] == t["id"]


def test_published_test_rejects_key_and_structure_edits(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid)
    assert publish(h, t, tid).status == 200
    locked = {"error": "test_locked"}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", key_items([1])).json == locked
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"numbers": [1]}).json == locked
    put = h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", {"sections": structure()})
    assert (put.status, put.json) == (409, locked)
    assert publish(h, t, tid).json == locked
    assert items_of(h, t, tid)[1]["answers"] == [f"{SECRET}1"]


# ---- key editing ----


def test_editing_reviewed_answer_clears_review_and_unchanged_keeps_it(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid, range(1, 4))
    before = items_of(h, t, tid)
    assert all(before[n]["reviewed"] for n in (1, 2, 3))
    body = {"items": [{"number": 1, "answers": [f"{SECRET}1"]}, {"number": 2, "answers": ["changed"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    after = items_of(h, t, tid)
    assert after[1]["reviewed"] is True
    assert after[2]["reviewed"] is False
    assert after[3]["reviewed"] is True
    rows = h.owner_rows(
        """select question_number, reviewed_at, reviewed_by from keys.answer_key_items i
           join keys.answer_key_versions v on v.id = i.version_id where v.test_id = %s""",
        (tid,),
    )
    by = {r["question_number"]: r for r in rows}
    assert by[2]["reviewed_at"] is None
    assert by[2]["reviewed_by"] is None
    assert by[1]["reviewed_by"] == t["id"]


def test_put_items_accepts_pasted_text(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    res = h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", {"pasted": "1. A\n2) B; b\n3 C"})
    assert res.status == 200
    got = items_of(h, t, tid)
    assert got[2]["answers"] == ["B"]
    assert got[3]["answers"] == ["C"]
    assert got[1]["origin"] == "manual"


def test_put_items_normalizes_answers(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    body = {"items": [{"number": 5, "answers": ["  Three ", "three", "THREE", "3"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    assert items_of(h, t, tid)[5]["answers"] == ["Three", "3"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": [{"number": 1, "answers": ["A"]}], "pasted": "1. A"},
        {"items": []},
        {"pasted": ""},
        {"items": [{"number": 41, "answers": ["A"]}]},
        {"items": [{"number": 1, "answers": []}]},
        {"items": [{"number": 1, "answers": ["  "]}]},
        {"items": [{"number": 1, "answers": ["a" * 101]}]},
        {"items": [{"number": 1, "answers": [f"v{i}" for i in range(21)]}]},
        {"items": [{"number": 1, "answers": ["A"]}, {"number": 1, "answers": ["B"]}]},
        {"items": [{"number": 1, "answers": "A"}]},
        {"pasted": "99. A"},
        {"pasted": "nonsense"},
    ],
)
def test_put_items_rejects_bad_input(h: Harness, t: dict[str, Any], body: dict[str, Any]) -> None:
    tid = make(h, t)
    res = h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body)
    assert (res.status, res.json) == (400, {"error": "invalid_input"})
    assert items_of(h, t, tid) == {}


def test_answer_limits_are_inclusive(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    body = {"items": [{"number": 1, "answers": ["a" * 100] + [f"v{i}" for i in range(19)]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200


def test_review_requires_stored_items_and_clears_flag(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid, [1], review=False)
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"numbers": [1, 2]})
    assert (res.status, res.json) == (400, {"error": "invalid_input"})
    assert items_of(h, t, tid)[1]["reviewed"] is False
    for bad in ({"numbers": []}, {}, {"numbers": [0]}):
        assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", bad).status == 400
    h.owner_rows(
        "update keys.answer_key_items set flagged = true where version_id in "
        "(select id from keys.answer_key_versions where test_id = %s)",
        (tid,),
    )
    assert items_of(h, t, tid)[1]["flagged"] is True
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", {"numbers": [1]}).status == 200
    item = items_of(h, t, tid)[1]
    assert (item["flagged"], item["reviewed"]) == (False, True)


def test_get_key_on_new_test_is_empty(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    res = h.call(t["a"], "GET", f"/api/tests/{tid}/key")
    assert (res.status, res.json["items"], res.json["approvedAt"]) == (200, [], None)
    assert (
        h.owner_rows("select count(*) as n from keys.answer_key_versions where test_id = %s", (tid,))[0]["n"]
        == 0
    )


# ---- structure interaction ----


def test_structure_removes_items_of_removed_questions(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid, range(1, 41))
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", {"sections": structure(30)}).status == 200
    assert sorted(items_of(h, t, tid)) == list(range(1, 31))


def test_structure_clears_review_when_type_or_word_limit_changes(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid, [1, 2, 20, 21, 30])
    changed = structure()
    changed[0]["ranges"][0]["questionType"] = "ynng"  # question 1: type changed
    changed[1]["ranges"][7]["wordLimit"] = 3  # question 21: word limit changed (14..26, index 7)
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", {"sections": changed}).status == 200
    got = items_of(h, t, tid)
    assert {n: got[n]["reviewed"] for n in got} == {1: False, 2: True, 20: True, 21: False, 30: True}
    assert got[1]["answers"] == [f"{SECRET}1"]


# ---- audit and leakage ----


def test_key_edit_review_publish_write_audit_rows(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid)
    publish(h, t, tid)
    h.call(t["a"], "POST", f"/api/tests/{tid}/archive")
    rows = h.owner_rows(
        "select action, before, after from audit_log where entity_id = %s order by created_at", (tid,)
    )
    actions = [r["action"] for r in rows]
    assert actions == [
        "test.create",
        "test.structure",
        "key.update",
        "key.review",
        "test.publish",
        "test.archive",
    ]
    assert SECRET not in str(rows)


def test_failed_publish_and_idempotent_archive_write_no_extra_audit(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    assert publish(h, t, tid).status == 409
    fill(h, t, tid)
    publish(h, t, tid)
    h.call(t["a"], "POST", f"/api/tests/{tid}/archive")
    h.call(t["a"], "POST", f"/api/tests/{tid}/archive")
    actions = [r["action"] for r in h.owner_rows("select action from audit_log where entity_id = %s", (tid,))]
    assert actions.count("test.publish") == 1
    assert actions.count("test.archive") == 1


def test_key_never_appears_in_non_key_routes(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid)
    publish(h, t, tid)
    for url in ("/api/tests", f"/api/tests/{tid}", f"/api/tests/{tid}/readiness"):
        assert SECRET not in str(h.call(t["a"], "GET", url).json), url
    assert SECRET not in str(h.call(t["a"], "POST", f"/api/tests/{tid}/archive").json)
    assert SECRET not in str(h.call(t["a"], "PATCH", f"/api/tests/{tid}", {"title": "x"}).json)


# ---- archive ----


def test_archive_only_from_published_and_is_idempotent(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/archive")
    assert (res.status, res.json) == (409, {"error": "test_not_published"})
    fill(h, t, tid)
    publish(h, t, tid)
    first = h.call(t["a"], "POST", f"/api/tests/{tid}/archive")
    assert (first.status, first.json["status"]) == (200, "archived")
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/archive").json["status"] == "archived"
    assert publish(h, t, tid).json == {"error": "test_locked"}


# ---- tenant and role ----


def test_other_tenant_and_students_get_nothing(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    fill(h, t, tid)
    calls = [
        ("GET", f"/api/tests/{tid}/key", None),
        ("PUT", f"/api/tests/{tid}/key/items", key_items([1])),
        ("POST", f"/api/tests/{tid}/key/review", {"numbers": [1]}),
        ("POST", f"/api/tests/{tid}/publish", None),
        ("POST", f"/api/tests/{tid}/archive", None),
    ]
    for method, url, body in calls:
        res = h.call(t["b"], method, url, body)
        assert (res.status, res.json) == (404, {"error": "not_found"}), url
        assert SECRET not in str(res.json)
        assert h.call(t["s"], method, url, body).status == 403, url
    assert items_of(h, t, tid)[1]["answers"] == [f"{SECRET}1"]
    assert h.call(t["a"], "GET", f"/api/tests/{tid}").json["status"] == "draft"


# ---- database immutability, through the teacher role ----


def approved_version(h: Harness, t: dict[str, Any]) -> tuple[str, UUID]:
    tid = make(h, t)
    fill(h, t, tid, [1, 2], review=False)
    return tid, h.owner_rows("select id from keys.answer_key_versions where test_id = %s", (tid,))[0]["id"]


def approve(h: Harness, t: dict[str, Any], version: UUID) -> None:
    ident = Identity(t["id"], "teacher", t["id"])
    with with_identity(h.db.app_pool, ident) as tx:
        tx.execute(
            "update keys.answer_key_versions set approved_at = now(), approved_by = %s where id = %s",
            (t["id"], version),
        )


@pytest.mark.parametrize(
    "sql",
    [
        "update keys.answer_key_versions set approved_at = null, approved_by = null where id = %(v)s",
        "update keys.answer_key_versions set version = 9 where id = %(v)s",
        "update keys.answer_key_versions set approved_at = now() where id = %(v)s",
        "update keys.answer_key_items set answers = '{x}' where version_id = %(v)s",
        "update keys.answer_key_items set reviewed_at = now(), reviewed_by = %(t)s where version_id = %(v)s",
        "delete from keys.answer_key_items where version_id = %(v)s",
        "insert into keys.answer_key_items (version_id, teacher_id, question_number, answers, origin)"
        " select id, teacher_id, 77, '{x}', 'manual' from keys.answer_key_versions where id = %(v)s",
    ],
)
def test_approved_key_version_and_items_are_immutable(h: Harness, t: dict[str, Any], sql: str) -> None:
    _, version = approved_version(h, t)
    approve(h, t, version)
    ident = Identity(t["id"], "teacher", t["id"])
    with pytest.raises(psycopg.Error, match="immutable"), with_identity(h.db.app_pool, ident) as tx:
        tx.execute(sql.encode(), {"v": version, "t": t["id"]})


def test_unapproved_version_stays_editable_and_approval_is_one_way(h: Harness, t: dict[str, Any]) -> None:
    _, version = approved_version(h, t)
    ident = Identity(t["id"], "teacher", t["id"])
    with with_identity(h.db.app_pool, ident) as tx:
        n = tx.execute("update keys.answer_key_items set answers = '{y}' where version_id = %s", (version,))
        assert n.rowcount == 2
    approve(h, t, version)


def test_key_trigger_functions_are_not_executable_by_public(h: Harness) -> None:
    rows = h.owner_rows(
        """select p.proname, has_function_privilege('npa_teacher', p.oid, 'execute') as teacher,
                  has_function_privilege('npa_app', p.oid, 'execute') as app
           from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'app' and p.proname like 'reject_approved_key%%'"""
    )
    assert len(rows) >= 3
    assert not any(r["teacher"] or r["app"] for r in rows)
