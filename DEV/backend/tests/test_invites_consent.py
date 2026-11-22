from dataclasses import dataclass
from itertools import count
from typing import Any

import pytest

from conftest import PASSWORD, Harness

_n = count(1)


def registration(**overrides: Any) -> dict[str, Any]:
    return {
        "email": f"student{next(_n)}@test.local",
        "password": PASSWORD,
        "displayName": "Minh",
        "ageGroup": "18_plus",
        "consents": {"core_learning": True, "ai_processing": False},
        **overrides,
    }


def new_invite(h: Harness, cookie: str, cls: str, days: int = 7) -> dict[str, Any]:
    res = h.call(cookie, "POST", f"/api/classes/{cls}/invites", {"expiresInDays": days})
    assert res.status == 201
    return res.json


@dataclass
class World:
    a: str
    b: str
    class_id: str
    code: str


@pytest.fixture(scope="module")
def w(h: Harness) -> World:
    h.create_user("a@test.local", "teacher")
    h.create_user("b@test.local", "teacher")
    a, b = h.login("a@test.local"), h.login("b@test.local")
    class_id = h.call(a, "POST", "/api/classes", {"name": "Lop A"}).json["id"]
    return World(a, b, class_id, new_invite(h, a, class_id)["code"])


def enrolled(h: Harness, w: World) -> tuple[dict[str, Any], str, str]:
    body = registration()
    res = h.call(None, "POST", f"/api/invites/{w.code}/register", body)
    assert res.status == 201
    return body, res.json["id"], h.login(body["email"])


def test_preview_shows_only_class_name(h: Harness, w: World) -> None:
    res = h.call(None, "GET", f"/api/invites/{w.code}")
    assert res.status == 200
    assert res.json == {"className": "Lop A"}


def test_registers_student_into_right_class(h: Harness, w: World) -> None:
    body, _, cookie = enrolled(h, w)
    assert [c["id"] for c in h.call(cookie, "GET", "/api/classes").json] == [w.class_id]
    members = h.call(w.a, "GET", f"/api/classes/{w.class_id}").json["members"]
    assert body["email"] in [m["email"] for m in members]


def test_rejects_under_18_and_creates_no_account(h: Harness, w: World) -> None:
    body = registration(ageGroup="under_18")
    assert h.call(None, "POST", f"/api/invites/{w.code}/register", body).status == 403
    assert h.owner_rows("select 1 from users where email = %s", (body["email"],)) == []


def test_requires_core_learning_consent(h: Harness, w: World) -> None:
    body = registration(consents={"core_learning": False, "ai_processing": True})
    assert h.call(None, "POST", f"/api/invites/{w.code}/register", body).status == 400


def test_rejects_existing_email_in_any_case(h: Harness, w: World) -> None:
    body = registration()
    h.call(None, "POST", f"/api/invites/{w.code}/register", body)
    again = registration(email=body["email"].upper())
    assert h.call(None, "POST", f"/api/invites/{w.code}/register", again).status == 409


def test_rejects_expired_disabled_and_unknown_codes(h: Harness, w: World) -> None:
    expiring = new_invite(h, w.a, w.class_id, 1)
    with h.later(days=2):
        assert h.call(None, "GET", f"/api/invites/{expiring['code']}").status == 404
        assert h.call(None, "POST", f"/api/invites/{expiring['code']}/register", registration()).status == 404

    disabled = new_invite(h, w.a, w.class_id)
    assert h.call(w.a, "POST", f"/api/invites/{disabled['id']}/disable").status == 204
    assert h.call(None, "POST", f"/api/invites/{disabled['code']}/register", registration()).status == 404
    assert h.call(None, "GET", "/api/invites/NOPE1234").status == 404


def test_other_teacher_cannot_disable_invite(h: Harness, w: World) -> None:
    invite = new_invite(h, w.a, w.class_id)
    assert h.call(w.b, "POST", f"/api/invites/{invite['id']}/disable").status == 404


def test_rejects_codes_for_archived_class(h: Harness, w: World) -> None:
    cls = h.call(w.a, "POST", "/api/classes", {"name": "Closing"}).json["id"]
    invite = new_invite(h, w.a, cls)
    h.call(w.a, "POST", f"/api/classes/{cls}/archive")
    assert h.call(None, "GET", f"/api/invites/{invite['code']}").status == 404


def test_student_joins_second_class_of_same_teacher(h: Harness, w: World) -> None:
    _, _, cookie = enrolled(h, w)
    second = h.call(w.a, "POST", "/api/classes", {"name": "Lop A2"}).json["id"]
    invite = new_invite(h, w.a, second)
    assert h.call(cookie, "POST", f"/api/invites/{invite['code']}/join").status == 204
    assert len(h.call(cookie, "GET", "/api/classes").json) == 2


def test_rejects_student_of_another_teacher(h: Harness, w: World) -> None:
    _, _, cookie = enrolled(h, w)
    cls_b = h.call(w.b, "POST", "/api/classes", {"name": "Lop B"}).json["id"]
    invite = new_invite(h, w.b, cls_b)
    assert h.call(cookie, "POST", f"/api/invites/{invite['code']}/join").status == 409
    assert len(h.call(cookie, "GET", "/api/classes").json) == 1


def test_removes_student_keeps_record_and_allows_rejoin(h: Harness, w: World) -> None:
    _, sid, cookie = enrolled(h, w)
    assert h.call(w.b, "DELETE", f"/api/classes/{w.class_id}/members/{sid}").status == 404
    assert h.call(w.a, "DELETE", f"/api/classes/{w.class_id}/members/{sid}").status == 204
    assert h.call(cookie, "GET", "/api/classes").json == []
    assert h.call(cookie, "GET", f"/api/classes/{w.class_id}").status == 404
    rows = h.owner_rows("select removed_at from class_members where student_id = %s", (sid,))
    assert rows[0]["removed_at"] is not None

    assert h.call(cookie, "POST", f"/api/invites/{w.code}/join").status == 204
    assert len(h.call(cookie, "GET", "/api/classes").json) == 1


def test_never_shows_members_to_student(h: Harness, w: World) -> None:
    _, _, cookie = enrolled(h, w)
    detail = h.call(cookie, "GET", f"/api/classes/{w.class_id}")
    assert detail.status == 200
    assert "members" not in detail.json


def test_stores_registration_consent_with_terms_version(h: Harness, w: World) -> None:
    _, _, cookie = enrolled(h, w)
    res = h.call(cookie, "GET", "/api/consents").json
    assert res["current"] == {"core_learning": True, "ai_processing": False}
    assert len(res["history"]) == 2
    assert res["history"][0]["termsVersion"]


def test_latest_consent_wins_and_history_is_kept(h: Harness, w: World) -> None:
    _, _, cookie = enrolled(h, w)
    h.call(cookie, "POST", "/api/consents", {"purpose": "ai_processing", "granted": True})
    assert h.call(cookie, "GET", "/api/consents").json["current"]["ai_processing"] is True
    h.call(cookie, "POST", "/api/consents", {"purpose": "ai_processing", "granted": False})
    after = h.call(cookie, "GET", "/api/consents").json
    assert after["current"]["ai_processing"] is False
    assert len(after["history"]) == 4


def test_rejects_unknown_consent_purpose(h: Harness, w: World) -> None:
    _, _, cookie = enrolled(h, w)
    assert h.call(cookie, "POST", "/api/consents", {"purpose": "marketing", "granted": True}).status == 400
