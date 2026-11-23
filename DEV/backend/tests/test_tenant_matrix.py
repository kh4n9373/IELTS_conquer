import json
from dataclasses import dataclass
from typing import Any

import pytest

from conftest import PASSWORD, Harness
from npa_api.app import access_table

# Every route must appear here. The second value is the status Teacher B must get on
# Teacher A's resource; None means the route has no tenant-owned path resource.
MATRIX: dict[str, tuple[Any, int | None]] = {
    "POST /api/auth/login": (None, None),
    "POST /api/auth/logout": (None, None),
    "POST /api/auth/forgot": (None, None),
    "POST /api/auth/reset": (None, None),
    "GET /api/me": (None, None),
    "GET /api/admin/teachers": (None, None),
    "POST /api/admin/teachers": ({"email": "matrix@test.local", "displayName": "M"}, None),
    "POST /api/admin/teachers/{id}/lock": (None, 403),
    "POST /api/admin/teachers/{id}/unlock": (None, 403),
    "GET /api/classes": (None, None),
    "POST /api/classes": ({"name": "Matrix"}, None),
    "GET /api/classes/{id}": (None, 404),
    "PATCH /api/classes/{id}": ({"name": "Hacked"}, 404),
    "POST /api/classes/{id}/archive": (None, 404),
    "DELETE /api/classes/{id}/members/{studentId}": (None, 404),
    "POST /api/classes/{id}/invites": ({"expiresInDays": 7}, 404),
    "POST /api/invites/{id}/disable": (None, 404),
    "GET /api/invites/{code}": (None, None),
    "POST /api/invites/{code}/register": (None, None),
    "POST /api/invites/{code}/join": (None, 403),
    "GET /api/consents": (None, None),
    "POST /api/consents": ({"purpose": "ai_processing", "granted": True}, None),
    "GET /api/tests": (None, None),
    "POST /api/tests": ({"title": "Matrix", "sourceType": "self_authored"}, None),
    "GET /api/tests/{id}": (None, 404),
    "PATCH /api/tests/{id}": ({"title": "Hacked"}, 404),
    "PUT /api/tests/{id}/structure": (
        {"sections": [{"title": "S", "ranges": [{"from": 1, "to": 2, "questionType": "tfng"}]}]},
        404,
    ),
    "GET /api/tests/{id}/readiness": (None, 404),
}


@dataclass
class World:
    cookies: dict[str, str]
    teacher_b: str
    class_id: str
    invite_id: str
    code: str
    student_id: str
    teacher_id: str
    test_id: str

    def fill(self, url: str) -> str:
        return (
            url.replace("/api/tests/{id}", f"/api/tests/{self.test_id}")
            .replace("/api/invites/{id}", f"/api/invites/{self.invite_id}")
            .replace("/api/admin/teachers/{id}", f"/api/admin/teachers/{self.teacher_id}")
            .replace("{id}", self.class_id)
            .replace("{studentId}", self.student_id)
            .replace("{code}", self.code)
        )


@pytest.fixture(scope="module")
def w(h: Harness) -> World:
    h.create_user("admin@test.local", "admin")
    teacher_id = h.create_user("a@test.local", "teacher")
    h.create_user("b@test.local", "teacher")
    admin, teacher, teacher_b = h.login("admin@test.local"), h.login("a@test.local"), h.login("b@test.local")
    class_id = h.call(teacher, "POST", "/api/classes", {"name": "A"}).json["id"]
    test_id = h.call(teacher, "POST", "/api/tests", {"title": "T", "sourceType": "self_authored"}).json["id"]
    invite = h.call(teacher, "POST", f"/api/classes/{class_id}/invites", {"expiresInDays": 7}).json
    student = h.call(
        None,
        "POST",
        f"/api/invites/{invite['code']}/register",
        {
            "email": "student@test.local",
            "password": PASSWORD,
            "displayName": "S",
            "ageGroup": "18_plus",
            "consents": {"core_learning": True, "ai_processing": False},
        },
    )
    cookies = {"admin": admin, "teacher": teacher, "student": h.login("student@test.local")}
    return World(
        cookies,
        teacher_b,
        class_id,
        invite["id"],
        invite["code"],
        student.json["id"],
        str(teacher_id),
        test_id,
    )


def test_covers_every_registered_route(h: Harness, w: World) -> None:
    registered = sorted(f"{method} {path}" for method, path, _ in access_table(h.app))
    assert registered == sorted(MATRIX)


def test_rejects_anonymous_on_every_protected_route(h: Harness, w: World) -> None:
    for method, path, rule in access_table(h.app):
        if rule == "public":
            continue
        key = f"{method} {path}"
        assert h.call(None, method, w.fill(path), MATRIX[key][0]).status == 401, key


def test_rejects_roles_outside_access_rule(h: Harness, w: World) -> None:
    for method, path, rule in access_table(h.app):
        if rule == "public":
            continue
        for role in ("admin", "teacher", "student"):
            if role in rule:
                continue
            key = f"{method} {path}"
            assert h.call(w.cookies[role], method, w.fill(path), MATRIX[key][0]).status == 403, (
                f"{role} {key}"
            )


def test_teacher_b_gets_nothing_on_teacher_a_resources(h: Harness, w: World) -> None:
    for key, (body, expected) in MATRIX.items():
        if expected is None:
            continue
        method, url = key.split(" ")
        res = h.call(w.teacher_b, method, w.fill(url), body)
        assert res.status == expected, key
        assert "student@test.local" not in json.dumps(res.json), key


def test_teacher_a_data_unchanged_after_attempts(h: Harness, w: World) -> None:
    detail = h.call(w.cookies["teacher"], "GET", f"/api/classes/{w.class_id}").json
    assert detail["name"] == "A"
    assert detail["status"] == "active"
    assert [m["id"] for m in detail["members"]] == [w.student_id]
    assert w.invite_id in [i["id"] for i in detail["invites"]]


def test_never_lists_teacher_a_data_for_teacher_b(h: Harness, w: World) -> None:
    for url in ("/api/classes", "/api/consents", "/api/me"):
        body = json.dumps(h.call(w.teacher_b, "GET", url).json)
        assert w.class_id not in body, url
        assert "student@test.local" not in body, url
