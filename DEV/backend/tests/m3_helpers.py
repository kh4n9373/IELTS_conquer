"""Builders for M3 tests. Only invented answers; nothing here reads real exam material."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fakes import make_pdf

from conftest import Harness

Range = tuple[int, int, str, int | None]


@dataclass(frozen=True)
class StudentRef:
    id: UUID
    email: str
    cookie: str


def publish_test(
    h: Harness,
    cookie: str,
    *,
    test_type: str = "course",
    answers: dict[int, list[str]],
    ranges: list[Range] | None = None,
    rules: str = "standard-1",
) -> str:
    """Publishes a one-section test through the API and returns its id."""
    last = max(answers)
    ranges = ranges or [(1, last, "short_answer", 3)]
    created = h.call(
        cookie,
        "POST",
        "/api/tests",
        {"title": "M3 test", "sourceType": "self_authored", "testType": test_type, "durationMinutes": 60},
    )
    assert created.status == 201
    tid: str = created.json["id"]
    h.client.cookies.clear()
    upload = h.client.put(
        f"/api/tests/{tid}/pdf",
        content=make_pdf(4),
        headers={"content-type": "application/pdf", "cookie": cookie},
    )
    assert upload.status_code == 200
    section: dict[str, Any] = {
        "title": "Passage 1",
        "passagePages": [1, 2],
        "questionPages": [3],
        "ranges": [
            {"from": a, "to": b, "questionType": kind} | ({"wordLimit": w} if w else {})
            for a, b, kind, w in ranges
        ],
    }
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/structure", {"sections": [section]}).status == 200
    items = [{"number": n, "answers": a} for n, a in sorted(answers.items())]
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/key/items", {"items": items}).status == 200
    assert h.call(cookie, "POST", f"/api/tests/{tid}/key/review", {"items": items}).status == 200
    if rules != "standard-1":
        h.owner_rows("update tests set grading_rules = %s where id = %s", (rules, tid))
    assert h.call(cookie, "POST", f"/api/tests/{tid}/publish").status == 200
    return tid


def make_class_with_students(h: Harness, teacher_cookie: str, count: int) -> tuple[str, list[StudentRef]]:
    """A class owned by the caller with `count` active students, each already logged in."""
    teacher = h.call(teacher_cookie, "GET", "/api/me").json["id"]
    cls = h.call(teacher_cookie, "POST", "/api/classes", {"name": "M3 class"})
    assert cls.status == 201
    class_id: str = cls.json["id"]
    students: list[StudentRef] = []
    for _ in range(count):
        email = f"m3-{uuid4().hex[:10]}@test.local"
        sid = h.create_user(email, "student", UUID(str(teacher)))
        h.owner_rows(
            "insert into class_members (class_id, teacher_id, student_id) values (%s, %s, %s)",
            (class_id, teacher, sid),
        )
        students.append(StudentRef(sid, email, h.login(email)))
    return class_id, students
