import json
from datetime import datetime
from typing import Any

import pytest
from m3_helpers import StudentRef, make_class_with_students, publish_test

from conftest import Harness

OPEN = "2026-11-22T04:00:00Z"
DUE = "2026-11-23T04:00:00Z"
SECRETS = {1: ["zulu-secret-one"], 2: ["yankee-secret-two"]}


class Room:
    def __init__(self, h: Harness) -> None:
        h.create_user("att-teacher@test.local", "teacher")
        self.cookie = h.login("att-teacher@test.local")
        self.test_id = publish_test(h, self.cookie, answers=SECRETS)
        self.class_id, self.students = make_class_with_students(h, self.cookie, 2)
        _, self.outsiders = make_class_with_students(h, self.cookie, 1)

    def assign(self, h: Harness, **extra: Any) -> str:
        body = {
            "testId": self.test_id,
            "classId": self.class_id,
            "opensAt": OPEN,
            "dueAt": DUE,
            "maxAttempts": 2,
            "mode": "exam",
        } | extra
        res = h.call(self.cookie, "POST", "/api/assignments", body)
        assert res.status == 201
        return str(res.json["id"])


@pytest.fixture(scope="module")
def room(h: Harness) -> Room:
    return Room(h)


def item(number: int, value: str, seq: int, marked: bool = False) -> dict[str, Any]:
    return {"number": number, "value": value, "markedForReview": marked, "seq": seq}


def start(h: Harness, student: StudentRef, assignment: str) -> Any:
    return h.call(student.cookie, "POST", f"/api/my/assignments/{assignment}/attempts", {"device": "desktop"})


def enter(h: Harness, student: StudentRef, assignment: str) -> tuple[str, str]:
    started = start(h, student, assignment)
    assert started.status == 200
    attempt = str(started.json["attemptId"])
    claimed = h.call(student.cookie, "POST", f"/api/attempts/{attempt}/claim")
    assert claimed.status == 200
    return attempt, str(claimed.json["writerToken"])


def save(h: Harness, student: StudentRef, attempt: str, token: str, *items: dict[str, Any]) -> Any:
    return h.call(
        student.cookie,
        "PUT",
        f"/api/attempts/{attempt}/answers",
        {"writerToken": token, "items": list(items)},
    )


def answers(h: Harness, student: StudentRef, attempt: str) -> dict[int, dict[str, Any]]:
    res = h.call(student.cookie, "GET", f"/api/attempts/{attempt}")
    assert res.status == 200
    return {a["number"]: a for a in res.json["answers"]}


def test_cannot_start_outside_window(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    assert start(h, s, aid).json == {"error": "not_open"}
    with h.later(days=2):
        res = start(h, s, aid)
        assert (res.status, res.json) == (409, {"error": "not_open"})
    assert start(h, room.outsiders[0], aid).status == 404


def test_deadline_is_earlier_of_duration_and_due(h: Harness, room: Room) -> None:
    aid = room.assign(h, dueAt="2026-11-22T05:30:00Z")
    with h.later(hours=2):
        attempt, _ = enter(h, room.students[0], aid)
        deadline = h.call(room.students[0].cookie, "GET", f"/api/attempts/{attempt}").json["deadlineAt"]
    assert datetime.fromisoformat(deadline) == datetime.fromisoformat("2026-11-22T05:30:00+00:00")
    aid2 = room.assign(h)
    with h.later(hours=2):
        attempt2, _ = enter(h, room.students[0], aid2)
        deadline2 = h.call(room.students[0].cookie, "GET", f"/api/attempts/{attempt2}").json["deadlineAt"]
    assert datetime.fromisoformat(deadline2) == datetime.fromisoformat("2026-11-22T06:00:00+00:00")


def test_reload_keeps_deadline_and_answers(h: Harness, room: Room) -> None:  # S1
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        before = h.call(s.cookie, "GET", f"/api/attempts/{attempt}").json["deadlineAt"]
        assert save(h, s, attempt, token, item(1, "kept", 1, True)).status == 200
    with h.later(hours=2, minutes=20):
        again, _ = enter(h, s, aid)
        room_view = h.call(s.cookie, "GET", f"/api/attempts/{again}").json
    assert again == attempt
    assert room_view["deadlineAt"] == before
    assert room_view["answers"][0]["value"] == "kept" and room_view["answers"][0]["marked"] is True


def test_no_attempts_left(h: Harness, room: Room) -> None:
    aid, s = room.assign(h, maxAttempts=1), room.students[0]
    with h.later(hours=2):
        attempt, _ = enter(h, s, aid)
        h.owner_rows("update attempts set status = 'submitted' where id = %s", (attempt,))
        res = start(h, s, aid)
    assert (res.status, res.json) == (409, {"error": "no_attempts_left"})


def test_older_seq_never_overwrites_newer(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        assert save(h, s, attempt, token, item(1, "new", 5)).json["accepted"] == [{"number": 1, "seq": 5}]
        assert save(h, s, attempt, token, item(1, "old", 3)).status == 200
        assert answers(h, s, attempt)[1]["value"] == "new"


def test_second_device_takes_over_and_old_is_rejected(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, old = enter(h, s, aid)
        assert save(h, s, attempt, old, item(1, "first", 1)).status == 200
        new = h.call(s.cookie, "POST", f"/api/attempts/{attempt}/claim").json["writerToken"]
        stale = save(h, s, attempt, old, item(1, "stale", 9))
        assert (stale.status, stale.json) == (409, {"error": "stale_device"})
        assert answers(h, s, attempt)[1]["value"] == "first"
        assert save(h, s, attempt, new, item(1, "second", 2)).status == 200


def test_answer_in_grace_is_accepted(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        with h.later(minutes=60, seconds=30):
            res = save(h, s, attempt, token, item(1, "grace", 1))
            assert res.json["accepted"] == [{"number": 1, "seq": 1}] and res.json["late"] == []
        assert answers(h, s, attempt)[1]["value"] == "grace"


def test_answer_after_grace_goes_to_late_table(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        assert save(h, s, attempt, token, item(1, "kept", 1)).status == 200
        with h.later(minutes=60, seconds=31):
            res = save(h, s, attempt, token, item(1, "too late", 2))
            assert res.json["accepted"] == [] and res.json["late"] == [1]
        assert answers(h, s, attempt)[1]["value"] == "kept"
    rows = h.owner_rows("select value from late_answers where attempt_id = %s", (attempt,))
    assert [r["value"] for r in rows] == ["too late"]


def walk(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value] + [t for v in value.values() for t in walk(v)]
    if isinstance(value, list):
        return [t for v in value for t in walk(v)]
    return [str(value)]


def test_room_payload_never_contains_key(h: Harness, room: Room) -> None:  # S3
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        save(h, s, attempt, token, item(1, "mine", 1))
        view = h.call(s.cookie, "GET", f"/api/attempts/{attempt}").json
    tokens = walk(view)
    for secret in ("zulu-secret-one", "yankee-secret-two", "secret"):
        assert not any(secret in t for t in tokens)
    assert not any(w in t.lower() for t in tokens for w in ("key", "correct", "grading", "writer", "teacher"))


def test_room_uses_allowlisted_fields_only(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, _ = enter(h, s, aid)
        view = h.call(s.cookie, "GET", f"/api/attempts/{attempt}").json
    assert set(view) == {
        "title", "durationMinutes", "pageCount", "questionCount", "mode", "status", "deadlineAt",
        "serverNow", "sections", "questions", "answers",
    }  # fmt: skip
    assert set(view["questions"][0]) == {"number", "type", "wordLimit", "group"}


def test_pdf_url_only_for_assigned_student(h: Harness, room: Room) -> None:
    aid = room.assign(h, studentIds=[str(room.students[0].id)])
    with h.later(hours=2):
        attempt, _ = enter(h, room.students[0], aid)
        ok = h.call(room.students[0].cookie, "GET", f"/api/attempts/{attempt}/pdf-url")
        assert ok.status == 200 and ok.json["url"] and ok.json["expiresIn"] == 300
        for other in (room.students[1], room.outsiders[0]):
            assert h.call(other.cookie, "GET", f"/api/attempts/{attempt}/pdf-url").status == 404
            assert h.call(other.cookie, "GET", f"/api/attempts/{attempt}").status == 404
            assert h.call(other.cookie, "POST", f"/api/attempts/{attempt}/claim").status == 404
        assert h.call(room.cookie, "GET", f"/api/attempts/{attempt}").status == 403


def test_removed_student_cannot_reenter(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[1]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        assert save(h, s, attempt, token, item(1, "before", 1)).status == 200
        h.owner_rows(
            "update class_members set removed_at = %s where class_id = %s and student_id = %s",
            (h.now, room.class_id, s.id),
        )
        try:
            for method, url in (
                ("POST", f"/api/my/assignments/{aid}/attempts"),
                ("POST", f"/api/attempts/{attempt}/claim"),
                ("GET", f"/api/attempts/{attempt}"),
                ("GET", f"/api/attempts/{attempt}/pdf-url"),
                ("POST", f"/api/attempts/{attempt}/submit"),
            ):
                res = h.call(
                    s.cookie, method, url, {"device": "desktop"} if url.endswith("attempts") else None
                )
                assert (res.status, res.json) == (403, {"error": "removed_from_class"}), url
            res = save(h, s, attempt, token, item(1, "after", 2))
            assert (res.status, res.json) == (403, {"error": "removed_from_class"})
        finally:
            h.owner_rows(
                "update class_members set removed_at = null where class_id = %s and student_id = %s",
                (room.class_id, s.id),
            )
    saved = h.owner_rows("select value from attempt_answers where attempt_id = %s", (attempt,))
    assert [r["value"] for r in saved] == ["before"]


def test_archived_test_finishes_but_no_new_attempt(h: Harness, room: Room) -> None:
    tid = publish_test(h, room.cookie, answers=SECRETS)
    aid = room.assign(h, testId=tid)
    with h.later(hours=2):
        attempt, token = enter(h, room.students[0], aid)
        assert h.call(room.cookie, "POST", f"/api/tests/{tid}/archive").status in (200, 204)
        assert save(h, room.students[0], attempt, token, item(1, "still", 1)).status == 200
        assert h.call(room.students[0].cookie, "GET", f"/api/attempts/{attempt}").status == 200
        res = start(h, room.students[1], aid)
        assert (res.status, res.json) == (409, {"error": "test_archived"})


@pytest.mark.parametrize(
    "items",
    [
        [item(1, "nul\x00", 1)],
        [item(1, "x" * 201, 1)],
        [item(99, "outside", 1)],
        [item(0, "zero", 1)],
        [item(1, "neg", -1)],
        [item(1, "v", 1)] * 41,
        [{"number": 1, "value": "v", "seq": 1}],
        [{"number": "1", "value": "v", "markedForReview": False, "seq": 1}],
    ],
)
def test_oversized_or_invalid_items_rejected(h: Harness, room: Room, items: list[dict[str, Any]]) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        res = save(h, s, attempt, token, *items)
        assert (res.status, res.json) == (400, {"error": "invalid_input"})
        assert answers(h, s, attempt) == {}
        assert h.call(s.cookie, "PUT", f"/api/attempts/{attempt}/answers", {"items": []}).status == 400


def test_blank_clears_answer_but_keeps_review_mark(h: Harness, room: Room) -> None:
    aid, s = room.assign(h), room.students[0]
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        assert save(h, s, attempt, token, item(1, "abc", 1)).status == 200
        assert save(h, s, attempt, token, item(1, "", 2, True)).status == 200
        got = answers(h, s, attempt)[1]
    assert got["value"] == "" and got["marked"] is True
    assert json.dumps(got).count("abc") == 0
