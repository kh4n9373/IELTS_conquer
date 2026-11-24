"""Explanation tables and job queue (migration 0008)."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from m3_helpers import StudentRef, make_class_with_students
from test_attempts import enter
from test_manual_tests import create

from conftest import Harness
from npa_api.core.db import Identity, with_identity


def typed_test(
    h: Harness, email: str, questions: list[dict[str, Any]], key: list[dict[str, Any]], body: str = "Passage."
) -> tuple[str, str, UUID]:
    """A published manual test. Returns (teacher cookie, test id, teacher id)."""
    tid_user = h.create_user(email, "teacher")
    cookie = h.login(email)
    tid = create(h, cookie)
    section = {"title": "Bài 1", "body": body, "questions": questions}
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/structure", {"sections": [section]}).status == 200
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/key/items", {"items": key}).status == 200
    assert h.call(cookie, "POST", f"/api/tests/{tid}/key/review", {"items": key}).status == 200
    assert h.call(cookie, "POST", f"/api/tests/{tid}/publish").status == 200
    return cookie, tid, tid_user


def assign(
    h: Harness, cookie: str, tid: str, students: int = 1, **extra: Any
) -> tuple[str, list[StudentRef]]:
    class_id, refs = make_class_with_students(h, cookie, students)
    body = {
        "testId": tid,
        "classId": class_id,
        "opensAt": "2026-11-22T04:00:00Z",
        "dueAt": "2026-11-23T04:00:00Z",
        "maxAttempts": 1,
        "mode": "practice",
    } | extra
    res = h.call(cookie, "POST", "/api/assignments", body)
    assert res.status == 201
    return str(res.json["id"]), refs


MCQ = [{"number": 1, "questionType": "multiple_choice", "prompt": "Pick one", "options": ["a1", "b1"]}]
MCQ_KEY = [{"number": 1, "answers": ["B"]}]


def call(h: Harness, sql: str, *params: Any) -> list[dict[str, Any]]:
    with h.db.app_pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture(autouse=True)
def clean(h: Harness) -> None:
    h.owner_rows("delete from explain_jobs")


def test_student_cannot_read_explanations(h: Harness) -> None:
    teacher = h.create_user("xd-t0@test.local", "teacher")
    student = h.create_user("xd-s0@test.local", "student", teacher)
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        with_identity(h.db.app_pool, Identity(student, "student", teacher)) as tx,
    ):
        tx.execute("select * from keys.question_explanations")


def test_enqueue_is_idempotent(h: Harness) -> None:
    _, tid, _ = typed_test(h, "xd-t1@test.local", MCQ, MCQ_KEY)
    call(h, "select app.explain_enqueue(%s)", tid)
    call(h, "select app.explain_enqueue(%s)", tid)
    rows = h.owner_rows("select state from explain_jobs where test_id = %s", (tid,))
    assert [r["state"] for r in rows] == ["queued"]


def test_claim_then_finish_queued_again(h: Harness) -> None:
    _, tid, _ = typed_test(h, "xd-t2@test.local", MCQ, MCQ_KEY)
    call(h, "select app.explain_enqueue(%s)", tid)
    now = h.now
    lease = now + timedelta(minutes=10)
    job = call(h, "select * from app.explain_claim(%s, %s)", now, lease)[0]
    assert job["attempts"] == 1 and str(job["test_id"]) == tid
    later = now + timedelta(seconds=60)
    finish = "select app.explain_finish(%s, 1, 'queued', 'provider_error', %s, %s) as ok"
    ok = call(h, finish, job["id"], later, now)
    assert ok[0]["ok"] is True
    assert call(h, "select * from app.explain_claim(%s, %s)", now, lease) == []
    again = call(h, "select * from app.explain_claim(%s, %s)", later + timedelta(seconds=1), lease)
    assert again[0]["attempts"] == 2


def test_review_row_returns_prompt_and_options(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xd-t3@test.local", MCQ, MCQ_KEY)
    h.owner_rows("delete from explain_jobs")
    asg, students = assign(h, cookie, tid)
    h.owner_rows("delete from explain_jobs")  # assigning enqueues once Task 5 lands
    with h.later(hours=2):
        attempt, _ = enter(h, students[0], asg)
    row = call(h, "select app.attempt_review_row(%s) as r", attempt)[0]["r"]
    assert row["jobActive"] is False
    q = row["questions"][0]
    assert (q["number"], q["prompt"], q["options"], q["explanation"]) == (1, "Pick one", ["a1", "b1"], None)
    assert q["sectionTitle"] == "Bài 1"


def test_other_teacher_sees_no_jobs_or_explanations(h: Harness) -> None:
    _, tid, owner = typed_test(h, "xd-t4@test.local", MCQ, MCQ_KEY)
    call(h, "select app.explain_enqueue(%s)", tid)
    qid = h.owner_rows("select id from questions where test_id = %s", (tid,))[0]["id"]
    h.owner_rows(
        "insert into keys.question_explanations (question_id, test_id, teacher_id, content_hash, correct)"
        " values (%s, %s, %s, 'h', 'c')",
        (qid, tid, owner),
    )
    other = h.create_user("xd-t5@test.local", "teacher")
    with with_identity(h.db.app_pool, Identity(other, "teacher", other)) as tx:
        assert tx.execute("select * from explain_jobs").fetchall() == []
        assert tx.execute("select * from keys.question_explanations").fetchall() == []
    with with_identity(h.db.app_pool, Identity(owner, "teacher", owner)) as tx:
        assert len(tx.execute("select * from keys.question_explanations").fetchall()) == 1
