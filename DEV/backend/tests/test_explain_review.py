import json
from datetime import timedelta
from typing import Any

import pytest
from m3_helpers import StudentRef, publish_test
from test_attempts import enter, item, save, submit_with
from test_explain_db import MCQ, MCQ_KEY, assign, typed_test

from conftest import Harness

TWO = [*MCQ, {"number": 2, "questionType": "multiple_choice", "prompt": "Pick two", "options": ["c", "d"]}]
TWO_KEY = [*MCQ_KEY, {"number": 2, "answers": ["A"]}]


@pytest.fixture(autouse=True)
def clean(h: Harness) -> None:
    h.owner_rows("delete from explain_jobs")


def submit(h: Harness, student: StudentRef, asg: str, answers: dict[int, str]) -> str:
    with h.later(hours=2):
        attempt, token = enter(h, student, asg)
        for seq, (n, v) in enumerate(answers.items(), 1):
            assert save(h, student, attempt, token, item(n, v, seq)).status == 200
        assert submit_with(h, student, attempt, token).status == 200
    return attempt


def review(h: Harness, student: StudentRef, attempt: str) -> Any:
    with h.later(hours=3):
        res = h.call(student.cookie, "GET", f"/api/attempts/{attempt}/review")
    assert res.status == 200 and res.headers["cache-control"] == "no-store"
    return res.json


def explain(h: Harness, tid: str, number: int, correct: str, options: dict[str, str]) -> None:
    h.owner_rows(
        """insert into keys.question_explanations
             (question_id, test_id, teacher_id, content_hash, correct, options)
           select q.id, q.test_id, q.teacher_id, 'h', %s, %s from questions q
           where q.test_id = %s and q.number = %s""",
        (correct, json.dumps(options), tid, number),
    )


def active_jobs(h: Harness, tid: str) -> int:
    rows = h.owner_rows(
        "select count(*) as n from explain_jobs where test_id = %s and state in ('queued', 'running')", (tid,)
    )
    return int(rows[0]["n"])


def test_assigning_enqueues_once(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t1@test.local", MCQ, MCQ_KEY)
    assign(h, cookie, tid)
    assign(h, cookie, tid)
    assert active_jobs(h, tid) == 1


def test_review_hidden_until_released(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t2@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid, students=2, mode="exam", releasePolicy="after_last")
    explain(h, tid, 1, "SECRET-WHY", {"a": "SECRET-A"})
    attempt = submit(h, students[0], asg, {1: "A"})
    body = review(h, students[0], attempt)
    assert body["released"] is False and "items" not in body
    assert "SECRET" not in json.dumps(body) and "Pick one" not in json.dumps(body)


def test_review_ready_with_chosen_reason(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t3@test.local", TWO, TWO_KEY)
    asg, students = assign(h, cookie, tid)
    h.owner_rows("delete from explain_jobs")
    explain(h, tid, 1, "vì B đúng", {"a": "vì A sai"})
    explain(h, tid, 2, "vì A đúng", {"b": "vì B sai"})
    attempt = submit(h, students[0], asg, {1: "A", 2: "a"})
    body = review(h, students[0], attempt)
    one, two = body["items"]
    assert one["prompt"] == "Pick one" and one["options"] == ["a1", "b1"] and one["sectionTitle"] == "Bài 1"
    assert (one["answer"], one["correct"], one["key"]) == ("A", False, ["B"])
    assert one["explanationState"] == "ready"
    assert one["explanation"] == {"correct": "vì B đúng", "chosen": "vì A sai"}
    assert two["correct"] is True and two["explanation"] == {"correct": "vì A đúng", "chosen": None}
    assert active_jobs(h, tid) == 0


def test_review_pending_while_job_active(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t4@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid)
    attempt = submit(h, students[0], asg, {1: "A"})
    q = review(h, students[0], attempt)["items"][0]
    assert q["explanationState"] == "pending" and q["explanation"] is None


def test_review_none_after_failed_job_requeues(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t5@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid)
    h.owner_rows("update explain_jobs set state = 'failed', created_at = %s where test_id = %s", (h.now, tid))
    attempt = submit(h, students[0], asg, {1: "A"})
    assert review(h, students[0], attempt)["items"][0]["explanationState"] == "pending"
    assert active_jobs(h, tid) == 1


def test_question_without_prompt_is_none(h: Harness) -> None:
    h.create_user("xr-t6@test.local", "teacher")
    cookie = h.login("xr-t6@test.local")
    tid = publish_test(h, cookie, answers={1: ["alpha"]})  # a PDF test: no prompt text
    asg, students = assign(h, cookie, tid)
    h.owner_rows("delete from explain_jobs")
    attempt = submit(h, students[0], asg, {1: "beta"})
    q = review(h, students[0], attempt)["items"][0]
    assert q["explanationState"] == "none" and q["prompt"] == ""
    assert active_jobs(h, tid) == 0


def test_other_student_gets_404(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t7@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid, students=2)
    attempt = submit(h, students[0], asg, {1: "A"})
    with h.later(hours=3):
        assert h.call(students[1].cookie, "GET", f"/api/attempts/{attempt}/review").status == 404


def test_teacher_sees_review(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t8@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid)
    attempt = submit(h, students[0], asg, {1: "A"})
    with h.later(hours=3):
        res = h.call(cookie, "GET", f"/api/attempts/{attempt}/review")
    assert res.status == 200 and res.json["items"][0]["answer"] == "A"


def test_review_does_not_requeue_within_cooldown(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t9@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid)
    # The worker finished 10 minutes before the student opens the page, and the LLM skipped Q1.
    h.owner_rows(
        "update explain_jobs set state = 'done', created_at = %s where test_id = %s",
        (h.now + timedelta(hours=3, minutes=-10), tid),
    )
    attempt = submit(h, students[0], asg, {1: "A"})
    q = review(h, students[0], attempt)["items"][0]
    assert q["explanationState"] == "none"
    assert active_jobs(h, tid) == 0


def test_review_marks_choices(h: Harness) -> None:
    cookie, tid, _ = typed_test(h, "xr-t10@test.local", MCQ, MCQ_KEY)
    asg, students = assign(h, cookie, tid)
    attempt = submit(h, students[0], asg, {1: "a"})
    q = review(h, students[0], attempt)["items"][0]
    assert q["choices"] == [
        {"label": "A", "text": "a1", "mark": "wrong"},
        {"label": "B", "text": "b1", "mark": "key"},
    ]
