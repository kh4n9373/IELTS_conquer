import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from m3_helpers import StudentRef, make_class_with_students, publish_test
from test_attempts import enter, item, save, submit_with

from conftest import Harness
from npa_api.grading.release import ReleaseFacts, is_released

KEY = {1: ["alpha-secret"], 2: ["beta-secret"], 3: ["gamma-secret"]}
OPEN = "2026-11-22T04:00:00Z"
DUE = "2026-11-23T04:00:00Z"
T = datetime(2026, 11, 22, 12, tzinfo=UTC)
DUE_AT = T + timedelta(hours=10)


def facts(**kw: Any) -> ReleaseFacts:
    base: dict[str, Any] = {
        "mode": "exam",
        "policy": "after_submit",
        "release_at": None,
        "due_at": DUE_AT,
        "max_attempts": 1,
        "this_attempt_final": True,
        "own_final_count": 1,
        "active_recipients": 1,
        "recipients_done": 1,
        "open_attempts": 0,
    }
    return ReleaseFacts(**(base | kw))


def test_single_student_after_submit_releases_now() -> None:
    assert is_released(facts(), T)
    assert not is_released(facts(this_attempt_final=False, recipients_done=0), T)


def test_group_waits_for_everyone_or_due() -> None:
    waiting = facts(active_recipients=3, recipients_done=1, open_attempts=2)
    assert not is_released(waiting, T)
    assert is_released(facts(active_recipients=3, recipients_done=3), T)
    assert is_released(facts(active_recipients=3, recipients_done=1), DUE_AT)  # nobody still working


def test_removed_unstarted_student_does_not_block_group() -> None:
    # The SQL counts a removed student who never started out of active_recipients.
    assert is_released(facts(active_recipients=2, recipients_done=2), T)


def test_after_last_waits_for_final_attempt() -> None:
    mid = facts(policy="after_last", max_attempts=3, own_final_count=1, recipients_done=0)
    assert not is_released(mid, T)
    assert is_released(facts(policy="after_last", max_attempts=3, own_final_count=3), T)
    assert is_released(mid, DUE_AT)


def test_due_passed_but_attempt_open_waits_for_settle() -> None:
    open_one = facts(active_recipients=2, recipients_done=1, open_attempts=1)
    assert not is_released(open_one, DUE_AT + timedelta(minutes=1))


def test_scheduled_waits_for_time() -> None:
    at = DUE_AT + timedelta(hours=1)
    sched = facts(policy="scheduled", release_at=at)
    assert not is_released(sched, at - timedelta(seconds=1))
    assert is_released(sched, at)


def test_scheduled_release_waits_for_open_attempts() -> None:
    assert not is_released(facts(policy="scheduled", release_at=T, open_attempts=1), T)
    assert is_released(facts(policy="scheduled", release_at=T, open_attempts=0), T)


def test_practice_releases_after_each_submit() -> None:
    each = facts(
        mode="practice",
        max_attempts=3,
        own_final_count=1,
        active_recipients=4,
        recipients_done=0,
        open_attempts=3,
    )
    assert is_released(each, T)
    assert not is_released(facts(mode="practice", this_attempt_final=False), T)


def test_multi_attempt_exam_never_early() -> None:
    early = facts(policy="scheduled", release_at=T - timedelta(hours=1), max_attempts=2, own_final_count=1)
    assert not is_released(early, T)


@pytest.fixture(scope="module")
def teacher(h: Harness) -> str:
    h.create_user("rr-teacher@test.local", "teacher")
    return h.login("rr-teacher@test.local")


@pytest.fixture(scope="module")
def course_test(h: Harness, teacher: str) -> str:
    return publish_test(h, teacher, answers=KEY)


class Setup:
    def __init__(self, h: Harness, teacher: str, test_id: str, count: int) -> None:
        self.h, self.teacher, self.test_id = h, teacher, test_id
        self.class_id, self.students = make_class_with_students(h, teacher, count)

    def assign(self, **extra: Any) -> str:
        body = {
            "testId": self.test_id,
            "classId": self.class_id,
            "opensAt": OPEN,
            "dueAt": DUE,
            "maxAttempts": 1,
            "mode": "exam",
        } | extra
        res = self.h.call(self.teacher, "POST", "/api/assignments", body)
        assert res.status == 201, res.json
        return str(res.json["id"])

    def do(self, student: StudentRef, assignment: str, *answers: tuple[int, str], submit: bool = True) -> str:
        with self.h.later(hours=2):
            attempt, token = enter(self.h, student, assignment)
            for i, (n, v) in enumerate(answers, 1):
                assert save(self.h, student, attempt, token, item(n, v, i)).status == 200
            if submit:
                assert submit_with(self.h, student, attempt, token).status == 200
        return attempt

    def result(self, cookie: str, attempt: str) -> Any:
        with self.h.later(hours=2, minutes=30):
            return self.h.call(cookie, "GET", f"/api/attempts/{attempt}/result")


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def test_no_result_data_before_release(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 2)
    aid = s.assign()  # two recipients: waits for both
    attempt = s.do(s.students[0], aid, (1, "my-guess"))
    res = s.result(s.students[0].cookie, attempt)
    assert res.status == 200
    assert set(res.json) == {"released", "status", "expectedAt", "testTitle", "submittedAt"}
    assert res.json["released"] is False and res.json["submittedAt"] is not None
    assert res.json["expectedAt"] == "2026-11-23T04:00:00+00:00"
    with h.later(hours=2, minutes=30):
        others = [
            h.call(s.students[0].cookie, "GET", f"/api/attempts/{attempt}"),
            h.call(s.students[0].cookie, "GET", "/api/my/assignments"),
            submit_with(h, s.students[0], attempt, "x"),
        ]
    for r in (res, *others):
        text = json.dumps(r.json)
        leaked = {"correct", "total", "percent", "band", "key", "accepted", "items", "score"}
        assert not leaked & keys_of(r.json)
        assert "secret" not in text


def test_full_result_after_release(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    attempt = s.do(s.students[0], s.assign(), (1, "alpha-secret"), (2, "nope"))
    res = s.result(s.students[0].cookie, attempt)
    assert res.status == 200 and res.json["released"] is True
    body = res.json
    assert (body["correct"], body["total"], body["percent"]) == (1, 3, 33.33)
    assert body["sections"] == [{"position": 1, "title": "Passage 1", "correct": 1, "total": 3}]
    first = {"number": 1, "answer": "alpha-secret", "correct": True, "key": ["alpha-secret"]}
    assert body["items"][0] == first
    assert body["items"][1]["correct"] is False and body["items"][1]["key"] == ["beta-secret"]
    assert body["items"][2]["answer"] is None
    assert "band" not in body


def test_result_has_test_title_and_submit_time(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    attempt = s.do(s.students[0], s.assign(), (1, "alpha-secret"))
    body = s.result(s.students[0].cookie, attempt).json
    assert body["testTitle"] == "M3 test"
    assert body["attemptNumber"] == 1
    # Submitted two hours after the harness start, as an aware UTC instant.
    submitted = datetime.fromisoformat(body["submittedAt"])
    assert submitted.tzinfo is not None and submitted.utcoffset() == timedelta(0)
    assert submitted == h.now + timedelta(hours=2)


def ielts_result(h: Harness, teacher: str, test_type: str) -> tuple[Setup, str]:
    answers = {n: [f"a{n}"] for n in range(1, 41)}
    tid = publish_test(h, teacher, answers=answers)
    # The publish check wants three sections for an IELTS test; the type is set after publishing instead.
    h.owner_rows("update tests set test_type = %s where id = %s", (test_type, tid))
    s = Setup(h, teacher, tid, 1)
    given = tuple((n, f"a{n}" if n <= 30 else "wrong") for n in range(1, 41))
    with h.later(hours=2):
        attempt, token = enter(h, s.students[0], s.assign())
        saved = save(h, s.students[0], attempt, token, *[item(n, v, n) for n, v in given])
        assert saved.status == 200
        assert submit_with(h, s.students[0], attempt, token).status == 200
    return s, attempt


def test_s5_ielts_30_of_40_is_band_7(h: Harness, teacher: str) -> None:
    s, attempt = ielts_result(h, teacher, "ielts_standard")
    body = s.result(s.students[0].cookie, attempt).json
    assert (body["correct"], body["total"]) == (30, 40)
    assert (body["band"], body["bandLabel"], body["bandTableVersion"]) == (
        "7.0",
        "estimated",
        "academic-reading-2026-1",
    )


def test_s11_course_30_of_40_has_no_band_anywhere(h: Harness, teacher: str) -> None:
    s, attempt = ielts_result(h, teacher, "course")
    for cookie in (s.students[0].cookie, teacher):
        res = s.result(cookie, attempt)
        assert res.json["correct"] == 30
        assert not {"band", "bandLabel", "bandTableVersion"} & keys_of(res.json)
        assert "band" not in json.dumps(res.json).lower()


def test_teacher_tracking_states_and_late_count(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 3)
    aid = s.assign()
    done = s.do(s.students[0], aid, (1, "alpha-secret"))
    s.do(s.students[1], aid, (1, "x"), submit=False)
    # A late save: after the deadline and grace the attempt settles and the answer is kept, not graded.
    late_student = s.students[2]
    with h.later(hours=2):
        attempt, token = enter(h, late_student, aid)
        assert save(h, late_student, attempt, token, item(1, "ok", 1)).status == 200
    with h.later(hours=3, minutes=5):
        res = save(h, late_student, attempt, token, item(2, "too late", 2))
        assert res.status == 200 and res.json["late"] == [2]
    with h.later(hours=2, minutes=30):
        detail = h.call(teacher, "GET", f"/api/assignments/{aid}").json
    got = {r["id"]: r for r in detail["students"]}
    assert got[str(s.students[0].id)]["state"] == "submitted"
    assert got[str(s.students[1].id)]["state"] == "in_progress"
    assert got[str(late_student.id)]["state"] == "submitted"
    assert got[str(late_student.id)]["lateCount"] == 1 and got[str(s.students[0].id)]["lateCount"] == 0
    assert got[str(late_student.id)]["late"] == [{"number": 2, "value": "too late"}]
    assert got[str(s.students[0].id)]["late"] == []
    assert done
    not_started = Setup(h, teacher, course_test, 1)
    detail = h.call(teacher, "GET", f"/api/assignments/{not_started.assign()}").json
    assert [(r["state"], r["attempts"]) for r in detail["students"]] == [("not_started", 0)]


def test_teacher_sees_result_before_release(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 2)
    attempt = s.do(s.students[0], s.assign(), (1, "alpha-secret"))
    body = s.result(teacher, attempt).json
    assert body["released"] is False and body["correct"] == 1 and body["items"][0]["key"]
    assert s.result(s.students[0].cookie, attempt).json["released"] is False


def test_other_student_cannot_read_result(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 2)
    attempt = s.do(s.students[0], s.assign(), (1, "alpha-secret"))
    assert s.result(s.students[1].cookie, attempt).status == 404
    h.create_user("rr-other@test.local", "teacher")
    assert s.result(h.login("rr-other@test.local"), attempt).status == 404
    assert s.result(s.students[0].cookie, attempt).status == 200


def test_removed_unstarted_student_does_not_block_release(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 2)
    aid = s.assign()
    h.owner_rows("update class_members set removed_at = now() where student_id = %s", (s.students[1].id,))
    attempt = s.do(s.students[0], aid, (1, "alpha-secret"))
    assert s.result(s.students[0].cookie, attempt).json["released"] is True


def test_multi_attempt_exam_reveals_nothing_until_last(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    aid = s.assign(maxAttempts=2)
    first = s.do(s.students[0], aid, (1, "alpha-secret"))
    assert s.result(s.students[0].cookie, first).json["released"] is False
    second = s.do(s.students[0], aid, (2, "beta-secret"))
    assert s.result(s.students[0].cookie, second).json["released"] is True
    again = s.result(s.students[0].cookie, first).json
    assert again["released"] is True and again["countsForProgress"] is True
    assert s.result(s.students[0].cookie, second).json["countsForProgress"] is False


def test_practice_group_releases_after_each_submit(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 2)
    aid = s.assign(mode="practice", maxAttempts=2, releasePolicy="after_submit")
    attempt = s.do(s.students[0], aid, (1, "alpha-secret"))
    assert s.result(s.students[0].cookie, attempt).json["released"] is True


def test_trial_flag_is_kept_and_excluded_from_progress(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    aid = s.assign()
    h.owner_rows("update assignments set is_trial = true where id = %s", (aid,))
    attempt = s.do(s.students[0], aid, (1, "alpha-secret"))
    assert s.result(s.students[0].cookie, attempt).json["countsForProgress"] is False
    assert h.owner_rows("select is_trial from assignments where id = %s", (aid,))[0]["is_trial"] is True
    normal = Setup(h, teacher, course_test, 1)
    other = normal.do(normal.students[0], normal.assign(), (1, "alpha-secret"))
    assert normal.result(normal.students[0].cookie, other).json["countsForProgress"] is True


def test_expired_open_attempt_does_not_hold_release(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 2)
    aid = s.assign(releasePolicy="scheduled", releaseAt="2026-11-23T05:00:00Z")
    first = s.do(s.students[0], aid, (1, "alpha-secret"))
    s.do(s.students[1], aid, (1, "x"), submit=False)  # never submits; no sweep runs in this test
    with h.later(hours=27):
        assert h.call(s.students[0].cookie, "GET", f"/api/attempts/{first}/result").json["released"] is True


def test_trial_is_labelled_and_never_counts_for_progress(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    aid = s.assign()
    h.owner_rows("update assignments set is_trial = true where id = %s", (aid,))
    attempt = s.do(s.students[0], aid, (1, "alpha-secret"))
    body = s.result(s.students[0].cookie, attempt).json
    assert body["isTrial"] is True and body["countsForProgress"] is False
    assert h.call(teacher, "GET", f"/api/assignments/{aid}").json["isTrial"] is True
    assert h.call(teacher, "GET", "/api/assignments").json[0]["isTrial"] is True


def test_practice_attempt_never_counts_for_progress(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    attempt = s.do(s.students[0], s.assign(mode="practice"), (1, "alpha-secret"))
    assert s.result(s.students[0].cookie, attempt).json["countsForProgress"] is False


def test_unknown_attempt_result_is_404_for_a_student(h: Harness, teacher: str, course_test: str) -> None:
    s = Setup(h, teacher, course_test, 1)
    res = h.call(s.students[0].cookie, "GET", "/api/attempts/00000000-0000-4000-8000-0000000000aa/result")
    assert (res.status, res.json) == (404, {"error": "not_found"})
