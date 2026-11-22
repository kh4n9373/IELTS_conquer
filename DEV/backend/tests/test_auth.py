import json
import re

import pytest

from conftest import PASSWORD, Harness


@pytest.fixture(scope="module")
def teacher_id(h: Harness):
    return h.create_user("teacher@test.local", "teacher")


def test_signs_in_and_returns_profile_without_secrets(h: Harness, teacher_id) -> None:
    me = h.call(h.login("teacher@test.local"), "GET", "/api/me")
    assert me.status == 200
    assert me.json["id"] == str(teacher_id)
    assert me.json["role"] == "teacher"
    assert me.json["email"] == "teacher@test.local"
    assert "password" not in json.dumps(me.json)


def test_sets_httponly_cookie(h: Harness, teacher_id) -> None:
    res = h.call(None, "POST", "/api/auth/login", {"email": "teacher@test.local", "password": PASSWORD})
    assert re.search("HttpOnly", res.headers["set-cookie"], re.I)
    assert re.search("SameSite=Lax", res.headers["set-cookie"], re.I)


def test_email_is_case_insensitive(h: Harness, teacher_id) -> None:
    assert "npa_session=" in h.login("  TEACHER@Test.Local ")


def test_same_error_for_wrong_password_and_unknown_email(h: Harness, teacher_id) -> None:
    wrong = h.call(
        None, "POST", "/api/auth/login", {"email": "teacher@test.local", "password": "wrong-password"}
    )
    unknown = h.call(
        None, "POST", "/api/auth/login", {"email": "nobody@test.local", "password": "wrong-password"}
    )
    assert wrong.status == unknown.status == 401
    assert wrong.json == unknown.json


def test_rejects_locked_user_at_login(h: Harness) -> None:
    h.create_user("locked@test.local", "teacher", status="locked")
    res = h.call(None, "POST", "/api/auth/login", {"email": "locked@test.local", "password": PASSWORD})
    assert res.status == 401


def test_rejects_live_session_after_locking(h: Harness) -> None:
    h.create_user("soon@test.local", "teacher")
    cookie = h.login("soon@test.local")
    assert h.call(cookie, "GET", "/api/me").status == 200
    h.owner_rows("update users set status = 'locked' where email = 'soon@test.local'")
    assert h.call(cookie, "GET", "/api/me").status == 401


def test_requires_session_on_protected_routes(h: Harness) -> None:
    assert h.call(None, "GET", "/api/me").status == 401
    assert h.call("npa_session=forged", "GET", "/api/me").status == 401


def test_logout_revokes_session_on_server(h: Harness, teacher_id) -> None:
    cookie = h.login("teacher@test.local")
    assert h.call(cookie, "POST", "/api/auth/logout").status == 204
    assert h.call(cookie, "GET", "/api/me").status == 401


def test_expires_sessions(h: Harness) -> None:
    h.create_user("expiry@test.local", "teacher")
    cookie = h.login("expiry@test.local")
    with h.later(days=15):
        assert h.call(cookie, "GET", "/api/me").status == 401


def test_forgot_answers_204_for_unknown_email_and_sends_nothing(h: Harness) -> None:
    before = len(h.mail)
    assert h.call(None, "POST", "/api/auth/forgot", {"email": "nobody@test.local"}).status == 204
    assert len(h.mail) == before


def test_resets_once_and_revokes_sessions(h: Harness) -> None:
    h.create_user("reset@test.local", "teacher")
    old_cookie = h.login("reset@test.local")
    h.call(None, "POST", "/api/auth/forgot", {"email": "reset@test.local"})
    token = h.last_token()

    assert (
        h.call(None, "POST", "/api/auth/reset", {"token": token, "password": "a-brand-new-password"}).status
        == 204
    )
    assert h.call(old_cookie, "GET", "/api/me").status == 401
    assert h.login("reset@test.local", "a-brand-new-password")

    again = h.call(None, "POST", "/api/auth/reset", {"token": token, "password": "another-password-1"})
    assert again.status == 400


def test_rejects_expired_reset_token(h: Harness) -> None:
    h.create_user("late@test.local", "teacher")
    h.call(None, "POST", "/api/auth/forgot", {"email": "late@test.local"})
    token = h.last_token()
    with h.later(hours=2):
        res = h.call(None, "POST", "/api/auth/reset", {"token": token, "password": "a-brand-new-password"})
    assert res.status == 400


def test_reset_does_not_unlock_locked_account(h: Harness) -> None:
    h.create_user("stay-locked@test.local", "teacher", status="locked")
    before = len(h.mail)
    h.call(None, "POST", "/api/auth/forgot", {"email": "stay-locked@test.local"})
    assert len(h.mail) == before
