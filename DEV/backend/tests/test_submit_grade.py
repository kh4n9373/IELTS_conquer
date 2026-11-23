import threading
import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from m3_helpers import make_class_with_students, publish_test
from test_attempts import enter, item, save

from conftest import Harness
from npa_api.grading import settle
from npa_api.grading.settle import SweepThread, settle_attempt, sweep_once

KEY = {1: ["alpha"], 2: ["beta"], 3: ["gamma"]}
OPEN = "2026-11-22T04:00:00Z"
DUE = "2026-11-23T04:00:00Z"


class Room:
    def __init__(self, h: Harness) -> None:
        h.create_user("sg-teacher@test.local", "teacher")
        self.cookie = h.login("sg-teacher@test.local")
        self.test_id = publish_test(h, self.cookie, answers=KEY)
        self.class_id, self.students = make_class_with_students(h, self.cookie, 6)
        self.next = 0

    def student(self) -> Any:
        self.next += 1
        return self.students[(self.next - 1) % len(self.students)]

    def assign(self, h: Harness, **extra: Any) -> str:
        body = {"testId": self.test_id, "classId": self.class_id, "opensAt": OPEN, "dueAt": DUE,
                "maxAttempts": 1, "mode": "exam"} | extra
        res = h.call(self.cookie, "POST", "/api/assignments", body)
        assert res.status == 201
        return str(res.json["id"])


@pytest.fixture(scope="module")
def room(h: Harness) -> Room:
    return Room(h)


@pytest.fixture(autouse=True)
def quiet(h: Harness) -> Iterator[None]:
    # Attempts left open by an earlier test must not be picked up by this test's sweep.
    h.owner_rows("update attempts set status = 'submitted' where status = 'in_progress'")
    yield


def ctx(h: Harness) -> Any:
    return h.app.state.ctx


def open_attempt(h: Harness, room: Room, *answers: tuple[int, str], **extra: Any) -> tuple[Any, str, str]:
    """Starts an attempt at 05:00 (deadline 06:00) with saved answers."""
    aid, s = room.assign(h, **extra), room.student()
    with h.later(hours=2):
        attempt, token = enter(h, s, aid)
        for i, (n, v) in enumerate(answers, 1):
            assert save(h, s, attempt, token, item(n, v, i)).status == 200
    return s, attempt, token


def runs(h: Harness, attempt: str) -> list[dict[str, Any]]:
    return h.owner_rows("select * from keys.grading_runs where attempt_id = %s", (attempt,))


def status(h: Harness, attempt: str) -> str:
    return str(h.owner_rows("select status from attempts where id = %s", (attempt,))[0]["status"])


def test_submit_grades_in_same_transaction(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"), (2, "wrong"))
    with h.later(hours=2, minutes=10):
        res = h.call(s.cookie, "POST", f"/api/attempts/{attempt}/submit")
    assert res.status == 200 and res.json["status"] == "submitted"
    got = runs(h, attempt)
    assert len(got) == 1 and (got[0]["correct"], got[0]["total"]) == (1, 3)
    items = h.owner_rows("select * from keys.grading_items where run_id = %s", (got[0]["id"],))
    assert len(items) == 3


def test_double_submit_is_idempotent(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"))
    with h.later(hours=2, minutes=10):
        first = h.call(s.cookie, "POST", f"/api/attempts/{attempt}/submit")
        second = h.call(s.cookie, "POST", f"/api/attempts/{attempt}/submit")
    assert first.status == second.status == 200 and first.json == second.json
    assert len(runs(h, attempt)) == 1


def race(*jobs: Any) -> list[Any]:
    barrier = threading.Barrier(len(jobs))
    out: list[Any] = [None] * len(jobs)

    def go(i: int) -> None:
        barrier.wait()
        out[i] = jobs[i]()

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(jobs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_concurrent_submit_grades_once(h: Harness, room: Room) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "alpha"))
    c, aid = ctx(h), UUID(attempt)
    with h.later(hours=2, minutes=10):
        out = race(*[lambda: settle_attempt(c, aid, source="student")] * 4)
    assert out.count(True) == 1 and len(runs(h, attempt)) == 1


def test_concurrent_sweepers_grade_once(h: Harness, room: Room) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "alpha"))
    c = ctx(h)
    with h.later(days=2):
        out = race(*[lambda: sweep_once(c)] * 3)
    assert sum(out) == 1 and len(runs(h, attempt)) == 1


def test_concurrent_submit_and_sweep_grade_once(h: Harness, room: Room) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "alpha"))
    c, aid = ctx(h), UUID(attempt)
    with h.later(days=2):
        race(lambda: settle_attempt(c, aid, source="student"), lambda: sweep_once(c))
    assert len(runs(h, attempt)) == 1


def test_timeout_auto_submits_saved_answers(h: Harness, room: Room) -> None:  # S2
    _, attempt, _ = open_attempt(h, room, (1, "alpha"), (3, "gamma"))
    with h.later(hours=3, minutes=1):
        assert sweep_once(ctx(h)) == 1
    assert status(h, attempt) == "auto_submitted"
    assert runs(h, attempt)[0]["correct"] == 2


def test_not_settled_before_grace_ends(h: Harness, room: Room) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "alpha"))
    with h.later(hours=3):  # 06:00:00 is the deadline
        assert sweep_once(ctx(h)) == 0
    with h.later(hours=3, seconds=30):
        assert sweep_once(ctx(h)) == 0
    assert status(h, attempt) == "in_progress"
    with h.later(hours=3, seconds=31):
        assert sweep_once(ctx(h)) == 1


def test_request_after_grace_settles_without_sweeper(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"))
    with h.later(hours=3, minutes=5):
        res = h.call(s.cookie, "GET", f"/api/attempts/{attempt}")
    assert res.json["status"] == "auto_submitted"
    assert len(runs(h, attempt)) == 1


def test_late_answers_are_not_graded_but_kept(h: Harness, room: Room) -> None:
    s, attempt, token = open_attempt(h, room, (1, "alpha"))
    with h.later(hours=3, seconds=20):  # within grace: counted
        assert save(h, s, attempt, token, item(2, "beta", 10)).status == 200
    with h.later(hours=3, seconds=31):  # after grace: kept, not graded
        res = save(h, s, attempt, token, item(3, "gamma", 11))
        assert res.json["late"] == [3]  # this request also settled the attempt
        assert sweep_once(ctx(h)) == 0
    assert runs(h, attempt)[0]["correct"] == 2
    late = h.owner_rows("select question_number, value from late_answers where attempt_id = %s", (attempt,))
    assert late == [{"question_number": 3, "value": "gamma"}]


def test_deadline_is_earlier_of_duration_and_due(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"), dueAt="2026-11-22T05:30:00Z")
    with h.later(hours=2, minutes=40):  # 05:40: past due 05:30 + grace, before start + 60 min
        assert sweep_once(ctx(h)) == 1
    assert status(h, attempt) == "auto_submitted"


def test_removed_student_saved_answers_still_graded(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"))
    h.owner_rows("update class_members set removed_at = now() where student_id = %s", (s.id,))
    with h.later(hours=2, minutes=5):
        assert h.call(s.cookie, "POST", f"/api/attempts/{attempt}/claim").status == 403
        assert h.call(s.cookie, "GET", f"/api/attempts/{attempt}").status == 403
        assert h.call(s.cookie, "POST", f"/api/attempts/{attempt}/submit").status == 403
        assert status(h, attempt) == "in_progress"
    with h.later(hours=3, minutes=5):
        assert sweep_once(ctx(h)) == 1
    assert status(h, attempt) == "auto_submitted" and runs(h, attempt)[0]["correct"] == 1


def test_failure_rolls_back_whole_settle(h: Harness, room: Room, monkeypatch: pytest.MonkeyPatch) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "alpha"))
    real = settle.grade

    def boom(*_: Any) -> Any:
        raise ValueError("boom")

    monkeypatch.setattr(settle, "grade", boom)
    with h.later(days=2):
        assert sweep_once(ctx(h)) == 0
        assert status(h, attempt) == "in_progress" and runs(h, attempt) == []
        monkeypatch.setattr(settle, "grade", real)  # "restart": a later sweep grades it
        assert sweep_once(ctx(h)) == 1
    assert len(runs(h, attempt)) == 1


def test_one_failing_attempt_does_not_stop_sweep(
    h: Harness, room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, a1, _ = open_attempt(h, room, (1, "alpha"))
    _, a2, _ = open_attempt(h, room, (1, "alpha"))
    real, calls = settle.grade, []

    def flaky(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("boom")
        return real(*args)

    monkeypatch.setattr(settle, "grade", flaky)
    with h.later(days=2):
        assert sweep_once(ctx(h)) == 1
    assert sorted([status(h, a1), status(h, a2)]) == ["auto_submitted", "in_progress"]


def test_run_records_key_and_rules_versions(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"))
    with h.later(hours=2, minutes=10):
        h.call(s.cookie, "POST", f"/api/attempts/{attempt}/submit")
    run = runs(h, attempt)[0]
    key = h.owner_rows(
        "select id from keys.answer_key_versions where test_id = %s and approved_at is not null "
        "order by version desc limit 1",
        (room.test_id,),
    )[0]["id"]
    assert run["key_version_id"] == key and run["rules_version"] == "standard-1"
    assert run["test_type"] == "course" and run["band"] is None and run["band_table_version"] is None


def test_submit_response_has_no_score(h: Harness, room: Room) -> None:
    s, attempt, _ = open_attempt(h, room, (1, "alpha"))
    with h.later(hours=2, minutes=10):
        res = h.call(s.cookie, "POST", f"/api/attempts/{attempt}/submit")
    assert set(res.json) == {"status", "finalizedAt"}
    text = str(res.json).lower()
    assert "alpha" not in text and "correct" not in text and "band" not in text
    other = room.student()
    assert h.call(other.cookie, "POST", f"/api/attempts/{attempt}/submit").status == 404


def test_grading_failure_log_has_no_answer_values(
    h: Harness, room: Room, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "secret-typed-value"))

    def boom(*_: Any) -> Any:
        raise ValueError("secret-typed-value")

    monkeypatch.setattr(settle, "grade", boom)
    with h.later(days=2), caplog.at_level("DEBUG"):
        sweep_once(ctx(h))
    assert attempt in caplog.text
    assert "secret-typed-value" not in caplog.text


def test_sweep_thread_settles_and_stops_cleanly(h: Harness, room: Room) -> None:
    _, attempt, _ = open_attempt(h, room, (1, "alpha"))
    thread = SweepThread(ctx(h), interval=0.05)
    with h.later(days=2):
        thread.start()
        for _ in range(100):
            if status(h, attempt) != "in_progress":
                break
            time.sleep(0.05)
    thread.stop()
    assert status(h, attempt) == "auto_submitted" and not thread.is_alive()


def test_sweep_thread_survives_a_failing_round(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rounds: list[int] = []

    def flaky(_: Any) -> int:
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("db down")
        return 0

    monkeypatch.setattr(settle, "sweep_once", flaky)
    thread = SweepThread(ctx(h), interval=0.02)
    thread.start()
    for _ in range(100):
        if len(rounds) >= 3:
            break
        time.sleep(0.02)
    thread.stop()
    assert len(rounds) >= 3 and not thread.is_alive()


def test_harness_app_starts_no_sweep_thread(h: Harness) -> None:
    assert not [t for t in threading.enumerate() if t.name == "attempt-sweep" and t.is_alive()]
