from dataclasses import dataclass
from uuid import UUID

import pytest

from conftest import Harness


@dataclass
class Actors:
    admin: str
    a: str
    b: str
    a_id: UUID


@pytest.fixture(scope="module")
def u(h: Harness) -> Actors:
    h.create_user("admin@test.local", "admin")
    a_id = h.create_user("a@test.local", "teacher")
    h.create_user("b@test.local", "teacher")
    return Actors(h.login("admin@test.local"), h.login("a@test.local"), h.login("b@test.local"), a_id)


def test_creates_teacher_who_activates_by_link(h: Harness, u: Actors) -> None:
    res = h.call(u.admin, "POST", "/api/admin/teachers", {"email": "New@Test.local", "displayName": "Co Lan"})
    assert res.status == 201
    assert res.json["email"] == "new@test.local"
    body = {"token": h.last_token(), "password": "teacher-first-password"}
    assert h.call(None, "POST", "/api/auth/reset", body).status == 204
    assert h.login("new@test.local", "teacher-first-password")


def test_rejects_duplicate_email(h: Harness, u: Actors) -> None:
    res = h.call(u.admin, "POST", "/api/admin/teachers", {"email": "A@test.local", "displayName": "Dup"})
    assert res.status == 409


def test_admin_only(h: Harness, u: Actors) -> None:
    assert h.call(u.a, "GET", "/api/admin/teachers").status == 403
    body = {"email": "x@test.local", "displayName": "X"}
    assert h.call(u.a, "POST", "/api/admin/teachers", body).status == 403
    assert h.call(None, "GET", "/api/admin/teachers").status == 401


def test_locks_immediately_unlocks_and_audits(h: Harness, u: Actors) -> None:
    h.create_user("target@test.local", "teacher")
    teachers = h.call(u.admin, "GET", "/api/admin/teachers").json
    target = next(t for t in teachers if t["email"] == "target@test.local")
    cookie = h.login("target@test.local")

    assert h.call(u.admin, "POST", f"/api/admin/teachers/{target['id']}/lock").status == 204
    assert h.call(cookie, "GET", "/api/me").status == 401
    assert h.call(u.admin, "POST", f"/api/admin/teachers/{target['id']}/unlock").status == 204
    assert h.call(cookie, "GET", "/api/me").status == 200

    rows = h.owner_rows(
        "select action from audit_log where entity_id = %s order by created_at", (target["id"],)
    )
    assert [r["action"] for r in rows] == ["teacher.lock", "teacher.unlock"]


def test_lock_returns_404_for_non_teacher(h: Harness, u: Actors) -> None:
    admin_id = h.owner_rows("select id from users where email = 'admin@test.local'")[0]["id"]
    assert h.call(u.admin, "POST", f"/api/admin/teachers/{admin_id}/lock").status == 404


def test_creates_lists_reads_and_edits_class(h: Harness, u: Actors) -> None:
    created = h.call(u.a, "POST", "/api/classes", {"name": "IELTS 6.5"})
    assert created.status == 201
    cid = created.json["id"]
    assert cid in [c["id"] for c in h.call(u.a, "GET", "/api/classes").json]
    assert h.call(u.a, "PATCH", f"/api/classes/{cid}", {"name": "IELTS 7.0"}).json["name"] == "IELTS 7.0"
    detail = h.call(u.a, "GET", f"/api/classes/{cid}").json
    assert detail["name"] == "IELTS 7.0"
    assert detail["members"] == []


def test_rejects_blank_names(h: Harness, u: Actors) -> None:
    assert h.call(u.a, "POST", "/api/classes", {"name": "   "}).status == 400


def test_returns_400_for_malformed_id(h: Harness, u: Actors) -> None:
    assert h.call(u.a, "GET", "/api/classes/not-a-uuid").status == 400


def test_archived_class_readable_but_closed(h: Harness, u: Actors) -> None:
    cid = h.call(u.a, "POST", "/api/classes", {"name": "Old"}).json["id"]
    assert h.call(u.a, "POST", f"/api/classes/{cid}/archive").status == 204
    assert h.call(u.a, "GET", f"/api/classes/{cid}").json["status"] == "archived"
    assert h.call(u.a, "PATCH", f"/api/classes/{cid}", {"name": "New"}).status == 409
    assert h.call(u.a, "POST", f"/api/classes/{cid}/invites", {"expiresInDays": 7}).status == 409


def test_other_teacher_gets_404_on_every_class_action(h: Harness, u: Actors) -> None:
    cid = h.call(u.a, "POST", "/api/classes", {"name": "Private"}).json["id"]
    assert h.call(u.b, "GET", f"/api/classes/{cid}").status == 404
    assert h.call(u.b, "PATCH", f"/api/classes/{cid}", {"name": "Hacked"}).status == 404
    assert h.call(u.b, "POST", f"/api/classes/{cid}/archive").status == 404
    assert h.call(u.b, "POST", f"/api/classes/{cid}/invites", {"expiresInDays": 7}).status == 404
    assert cid not in [c["id"] for c in h.call(u.b, "GET", "/api/classes").json]


def test_ignores_teacher_id_from_client(h: Harness, u: Actors) -> None:
    res = h.call(u.b, "POST", "/api/classes", {"name": "Mine", "teacherId": str(u.a_id)})
    assert res.status == 201
    rows = h.owner_rows("select teacher_id from classes where id = %s", (res.json["id"],))
    assert rows[0]["teacher_id"] != u.a_id
