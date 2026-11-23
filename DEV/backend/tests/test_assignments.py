import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from m3_helpers import StudentRef, make_class_with_students, publish_test

from conftest import Harness
from npa_api.routes.assignments import default_release

START = "2026-11-22T03:00:00Z"
OPEN = "2026-11-22T04:00:00Z"
DUE = "2026-11-23T04:00:00Z"
ANSWERS = {1: ["alpha"], 2: ["beta"]}


class Room:
    def __init__(self, h: Harness) -> None:
        h.create_user("m3-teacher@test.local", "teacher")
        h.create_user("m3-other@test.local", "teacher")
        self.cookie = h.login("m3-teacher@test.local")
        self.other = h.login("m3-other@test.local")
        self.test_id = publish_test(h, self.cookie, answers=ANSWERS)
        self.class_id, self.students = make_class_with_students(h, self.cookie, 2)
        self.class2, self.outsiders = make_class_with_students(h, self.cookie, 1)

    def body(self, **extra: Any) -> dict[str, Any]:
        return {
            "testId": self.test_id,
            "classId": self.class_id,
            "opensAt": OPEN,
            "dueAt": DUE,
            "maxAttempts": 1,
            "mode": "exam",
        } | extra


@pytest.fixture(scope="module")
def room(h: Harness) -> Room:
    return Room(h)


def create(h: Harness, room: Room, **extra: Any) -> Any:
    return h.call(room.cookie, "POST", "/api/assignments", room.body(**extra))


def start_attempt(h: Harness, assignment_id: str, student: StudentRef) -> None:
    teacher = h.owner_rows("select teacher_id from assignments where id = %s", (assignment_id,))[0][
        "teacher_id"
    ]
    h.owner_rows(
        """insert into attempts (assignment_id, teacher_id, student_id, number, mode, started_at, deadline_at)
           values (%s, %s, %s, 1, 'exam', %s, %s)""",
        (assignment_id, teacher, student.id, h.now, h.now + timedelta(hours=1)),
    )


def test_only_published_test_and_active_class(h: Harness, room: Room) -> None:
    draft = h.call(
        room.cookie,
        "POST",
        "/api/tests",
        {"title": "Draft", "sourceType": "self_authored", "testType": "course", "durationMinutes": 30},
    ).json["id"]
    assert create(h, room, testId=draft).status == 409
    archived = publish_test(h, room.cookie, answers=ANSWERS)
    assert h.call(room.cookie, "POST", f"/api/tests/{archived}/archive").status in (200, 204)
    assert create(h, room, testId=archived).status == 409
    gone, _ = make_class_with_students(h, room.cookie, 1)
    assert h.call(room.cookie, "POST", f"/api/classes/{gone}/archive").status == 204
    assert create(h, room, classId=gone).status == 409
    assert create(h, room).status == 201


def test_recipients_are_snapshotted(h: Harness, room: Room) -> None:
    made = create(h, room)
    assert made.status == 201
    assert made.json["recipientCount"] == 2
    teacher = h.call(room.cookie, "GET", "/api/me").json["id"]
    late = h.create_user(f"late-{uuid4().hex[:8]}@test.local", "student", UUID(str(teacher)))
    h.owner_rows(
        "insert into class_members (class_id, teacher_id, student_id) values (%s, %s, %s)",
        (room.class_id, teacher, late),
    )
    email = h.owner_rows("select email from users where id = %s", (late,))[0]["email"]
    assert h.call(h.login(email), "GET", "/api/my/assignments").json == []
    assert h.call(room.cookie, "GET", f"/api/assignments/{made.json['id']}").json["recipientCount"] == 2


def test_foreign_student_rejected(h: Harness, room: Room) -> None:
    res = create(h, room, studentIds=[str(room.outsiders[0].id)])
    assert res.status == 400
    one = create(h, room, studentIds=[str(room.students[0].id)])
    assert one.status == 201
    assert one.json["recipientCount"] == 1
    assert create(h, room, studentIds=[]).status == 400


def test_default_release_by_attempt_count(h: Harness, room: Room) -> None:
    assert default_release(1) == "after_submit"
    assert default_release(2) == "after_last"
    assert create(h, room).json["releasePolicy"] == "after_submit"
    assert create(h, room, maxAttempts=3).json["releasePolicy"] == "after_last"


def test_after_submit_rejected_for_multi_attempt_exam(h: Harness, room: Room) -> None:
    res = create(h, room, maxAttempts=2, releasePolicy="after_submit")
    assert (res.status, res.json) == (409, {"error": "invalid_release"})
    assert create(h, room, maxAttempts=2, mode="practice", releasePolicy="after_submit").status == 201
    assert create(h, room, maxAttempts=6).status == 400
    assert create(h, room, maxAttempts=0).status == 400


def test_scheduled_release_not_before_due(h: Harness, room: Room) -> None:
    early = "2026-11-22T10:00:00Z"
    assert create(h, room, releasePolicy="scheduled", releaseAt=early).status == 409
    assert create(h, room, maxAttempts=2, releasePolicy="scheduled", releaseAt=early).status == 409
    solo = create(h, room, studentIds=[str(room.students[0].id)], releasePolicy="scheduled", releaseAt=early)
    assert solo.status == 201
    assert create(h, room, maxAttempts=2, releasePolicy="scheduled", releaseAt=DUE).status == 201
    assert create(h, room, releasePolicy="scheduled").status == 400
    assert create(h, room, releaseAt=DUE).status == 400
    assert create(h, room, dueAt=OPEN).status == 400
    assert create(h, room, opensAt="2026-11-22T04:00:00").status == 400


def test_edit_and_cancel_locked_after_first_start(h: Harness, room: Room) -> None:
    free = create(h, room).json["id"]
    patched = h.call(
        room.cookie, "PATCH", f"/api/assignments/{free}", {"maxAttempts": 2, "releasePolicy": "after_last"}
    )
    assert patched.status == 200
    assert patched.json["maxAttempts"] == 2
    assert h.call(room.cookie, "PATCH", f"/api/assignments/{free}", {}).status == 400
    assert (
        h.call(
            room.cookie,
            "PATCH",
            f"/api/assignments/{free}",
            {"maxAttempts": 3, "releasePolicy": "after_submit"},
        ).status
        == 409
    )
    assert h.call(room.cookie, "POST", f"/api/assignments/{free}/cancel").status == 204
    assert h.call(room.cookie, "GET", f"/api/assignments/{free}").json["status"] == "cancelled"
    assert h.call(room.cookie, "PATCH", f"/api/assignments/{free}", {"mode": "practice"}).status == 409

    used = create(h, room).json["id"]
    start_attempt(h, used, room.students[0])
    for res in (
        h.call(room.cookie, "PATCH", f"/api/assignments/{used}", {"mode": "practice"}),
        h.call(room.cookie, "POST", f"/api/assignments/{used}/cancel"),
    ):
        assert (res.status, res.json) == (409, {"error": "assignment_started"})
    assert h.call(room.other, "GET", f"/api/assignments/{used}").status == 404


def test_status_derived_from_time(h: Harness, room: Room) -> None:
    aid = create(h, room).json["id"]
    original = h.now
    try:
        for stamp, expected in (
            ("2026-11-22T03:00:00+00:00", "upcoming"),
            ("2026-11-22T05:00:00+00:00", "open"),
            ("2026-11-23T04:00:00+00:00", "closed"),
        ):
            h.now = type(original).fromisoformat(stamp)
            assert h.call(room.cookie, "GET", f"/api/assignments/{aid}").json["status"] == expected
            listed = {a["id"]: a["status"] for a in h.call(room.cookie, "GET", "/api/assignments").json}
            assert listed[aid] == expected
    finally:
        h.now = original


def test_student_list_has_no_key_fields(h: Harness, room: Room) -> None:
    aid = create(h, room, studentIds=[str(room.students[0].id)]).json["id"]
    mine = h.call(room.students[0].cookie, "GET", "/api/my/assignments").json
    row = next(a for a in mine if a["id"] == aid)
    assert set(row) == {
        "id", "testTitle", "className", "opensAt", "dueAt", "maxAttempts", "mode", "status", "attemptsUsed",
    }  # fmt: skip
    assert row["testTitle"] == "M3 test"
    dump = json.dumps(mine).lower()
    for word in ("answer", "key", "testid", "recipient", "release"):
        assert word not in dump
    other = h.call(room.students[1].cookie, "GET", "/api/my/assignments").json
    assert aid not in json.dumps(other)
    titles = h.owner_rows("select count(*) as n from app.my_assignment_titles()")
    assert titles[0]["n"] == 0  # no caller identity, no titles


def test_answer_group_round_trips_in_structure(h: Harness, room: Room) -> None:
    tid = h.call(
        room.cookie,
        "POST",
        "/api/tests",
        {"title": "G", "sourceType": "self_authored", "testType": "course", "durationMinutes": 30},
    ).json["id"]
    ranges = [
        {"from": 1, "to": 2, "questionType": "short_answer", "wordLimit": 2, "answerGroup": 1},
        {"from": 3, "to": 3, "questionType": "short_answer", "wordLimit": 2},
    ]
    put = h.call(
        room.cookie, "PUT", f"/api/tests/{tid}/structure", {"sections": [{"title": "S", "ranges": ranges}]}
    )
    assert put.status == 200
    got = h.call(room.cookie, "GET", f"/api/tests/{tid}").json["sections"][0]["ranges"]
    assert [(r["from"], r["to"], r["answerGroup"]) for r in got] == [(1, 2, 1), (3, 3, None)]
