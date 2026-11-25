from typing import Any

from test_invites_consent import World, new_invite, w  # noqa: F401

from conftest import Harness

CONSENTS = {"core_learning": True, "ai_processing": False}


def test_config_exposes_client_id(h: Harness) -> None:
    assert h.call(None, "GET", "/api/auth/config").json == {"googleClientId": "test-client"}


def test_existing_account_signs_in_with_google(h: Harness) -> None:
    uid = h.create_user("gteacher@test.local", "teacher")
    res = h.call(None, "POST", "/api/auth/google", {"credential": "ok:GTeacher@Test.local:G"})
    assert res.status == 200
    assert res.json["id"] == str(uid)
    cookie = res.headers["set-cookie"].split(";")[0]
    assert h.call(cookie, "GET", "/api/me").json["email"] == "gteacher@test.local"


def test_locked_user_cannot_sign_in(h: Harness) -> None:
    h.create_user("glocked@test.local", "teacher", status="locked")
    assert h.call(None, "POST", "/api/auth/google", {"credential": "ok:glocked@test.local:L"}).status == 401


def test_bad_token_and_google_outage(h: Harness) -> None:
    assert h.call(None, "POST", "/api/auth/google", {"credential": "bad"}).status == 401
    assert h.call(None, "POST", "/api/auth/google", {"credential": "down:x"}).status == 503


def test_accepts_realistically_long_google_token(h: Harness) -> None:
    h.create_user("glong@test.local", "teacher")
    res = h.call(None, "POST", "/api/auth/google", {"credential": "ok:glong@test.local:L" + "x" * 1500})
    assert res.status == 200


def _ticket(h: Harness, email: str, name: str = "New Person") -> dict[str, Any]:
    res = h.call(None, "POST", "/api/auth/google", {"credential": f"ok:{email}:{name}"})
    assert res.status == 200
    assert res.json["next"] == "onboarding"
    assert "set-cookie" not in res.headers
    return res.json


def _student(ticket: str, code: str, **extra: Any) -> dict[str, Any]:
    return {
        "ticket": ticket,
        "role": "student",
        "displayName": "Kid Edited",
        "referralSource": "tiktok",
        "phone": "0912 345 678",
        "inviteCode": code,
        "currentBand": 5.5,
        "targetBand": 7,
        "ageGroup": "18_plus",
        "consents": CONSENTS,
        **extra,
    }


def test_unknown_email_gets_an_onboarding_ticket(h: Harness) -> None:
    t = _ticket(h, "Fresh@Test.local", "Fresh Face")
    assert (t["email"], t["name"], t["avatarUrl"]) == (
        "fresh@test.local",
        "Fresh Face",
        "https://pic.test/fresh@test.local.png",
    )
    assert len(t["ticket"]) > 20


def test_student_onboarding_creates_profile_and_joins_class(h: Harness, w: World) -> None:  # noqa: F811
    t = _ticket(h, "kid@test.local")
    res = h.call(None, "POST", "/api/onboarding", _student(t["ticket"], w.code))
    assert res.status == 201
    cookie = res.headers["set-cookie"].split(";")[0]
    me = h.call(cookie, "GET", "/api/me").json
    assert me["email"] == "kid@test.local"
    assert me["displayName"] == "Kid Edited"
    assert me["role"] == "student"
    assert me["avatarUrl"] == "https://pic.test/kid@test.local.png"
    assert (me["phone"], me["referralSource"], me["currentBand"], me["targetBand"]) == (
        "0912 345 678",
        "tiktok",
        5.5,
        7.0,
    )
    assert me["signInMethod"] == "google"
    assert h.call(cookie, "GET", f"/api/classes/{w.class_id}").status == 200
    # The ticket is single use.
    assert h.call(None, "POST", "/api/onboarding", _student(t["ticket"], w.code)).json == {
        "error": "ticket_invalid"
    }


def test_bad_invite_code_keeps_the_ticket_usable(h: Harness, w: World) -> None:  # noqa: F811
    t = _ticket(h, "typo@test.local")
    bad = h.call(None, "POST", "/api/onboarding", _student(t["ticket"], "NOPE2222"))
    assert (bad.status, bad.json) == (404, {"error": "invite_invalid"})
    assert h.call(None, "POST", "/api/onboarding", _student(t["ticket"], w.code)).status == 201


def test_student_onboarding_checks_code_age_and_consent(h: Harness) -> None:
    t = _ticket(h, "rules@test.local")["ticket"]
    url = "/api/onboarding"
    assert h.call(None, "POST", url, _student(t, "X", inviteCode=None)).json == {"error": "invite_required"}
    assert h.call(None, "POST", url, _student(t, "X", ageGroup="under_18")).status == 403
    no_consent = {"core_learning": False, "ai_processing": False}
    assert h.call(None, "POST", url, _student(t, "X", consents=no_consent)).status == 400
    assert h.call(None, "POST", url, _student(t, "X", targetBand=7.25)).status == 400
    assert h.call(None, "POST", url, _student(t, "X", phone="call me")).status == 400


def test_expired_ticket_is_refused(h: Harness, w: World) -> None:  # noqa: F811
    t = _ticket(h, "slow@test.local")
    with h.later(minutes=31):
        res = h.call(None, "POST", "/api/onboarding", _student(t["ticket"], w.code))
    assert (res.status, res.json) == (400, {"error": "ticket_invalid"})


def test_teacher_signup_waits_for_admin_approval(h: Harness) -> None:
    h.create_user("boss@test.local", "admin")
    t = _ticket(h, "newteacher@test.local", "Cô Lan")
    body = {"ticket": t["ticket"], "role": "teacher", "displayName": "Cô Lan", "referralSource": "friend"}
    res = h.call(None, "POST", "/api/onboarding", body)
    assert (res.status, res.json) == (202, {"status": "pending"})
    assert "set-cookie" not in res.headers

    google = {"credential": "ok:newteacher@test.local:Cô Lan"}
    assert h.call(None, "POST", "/api/auth/google", google).json == {"error": "teacher_pending"}

    admin = h.login("boss@test.local")
    teachers = h.call(admin, "GET", "/api/admin/teachers").json
    pending = next(x for x in teachers if x["email"] == "newteacher@test.local")
    assert (pending["status"], pending["referralSource"]) == ("pending", "friend")
    assert h.call(admin, "POST", f"/api/admin/teachers/{pending['id']}/approve").status == 204
    assert h.call(admin, "POST", f"/api/admin/teachers/{pending['id']}/approve").status == 409

    res = h.call(None, "POST", "/api/auth/google", google)
    assert (res.status, res.json["next"]) == (200, "home")
    cookie = res.headers["set-cookie"].split(";")[0]
    assert h.call(cookie, "GET", "/api/me").json["role"] == "teacher"


def test_google_login_refreshes_avatar_but_keeps_name(h: Harness) -> None:
    h.create_user("dev@test.local", "teacher")
    res = h.call(None, "POST", "/api/auth/google", {"credential": "ok:dev@test.local:Google Name"})
    cookie = res.headers["set-cookie"].split(";")[0]
    me = h.call(cookie, "GET", "/api/me").json
    assert me["avatarUrl"] == "https://pic.test/dev@test.local.png"
    assert me["displayName"] != "Google Name"


def test_user_edits_own_profile(h: Harness) -> None:
    h.create_user("edit@test.local", "teacher")
    cookie = h.login("edit@test.local")
    body = {
        "displayName": "  Thầy Minh  ",
        "phone": "+84 912 345 678",
        "currentBand": None,
        "targetBand": 8.5,
    }
    res = h.call(cookie, "PATCH", "/api/me", body)
    assert res.status == 200
    assert (res.json["displayName"], res.json["phone"], res.json["targetBand"]) == (
        "Thầy Minh",
        "+84 912 345 678",
        8.5,
    )
    assert h.call(cookie, "PATCH", "/api/me", {"displayName": ""}).status == 400
    assert h.call(cookie, "PATCH", "/api/me", {"displayName": "X", "currentBand": 9.5}).status == 400
