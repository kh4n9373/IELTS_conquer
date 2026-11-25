from typing import Any

from test_invites_consent import World, enrolled, w  # noqa: F401

from conftest import Harness

STUDENT = {
    "displayName": "Minh Anh",
    "age": 19,
    "occupation": "university",
    "referralSource": "instagram",
    "targetBand": 7.5,
    "cefrLevel": "B2",
    "skillRank": ["reading", "listening", "speaking", "writing"],
    "studyMinutes": 30,
    "studyTime": "evening",
    "studyStyles": ["flashcard", "video", "flashcard"],
    "reminders": True,
}
# Smallest well-formed JPEG framing the endpoint accepts: SOI marker, padding, EOI marker.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"


def _raw(h: Harness, cookie: str, method: str, url: str, data: bytes = b"", kind: str = "image/jpeg") -> Any:
    h.client.cookies.clear()
    return h.client.request(method, url, headers={"cookie": cookie, "content-type": kind}, content=data)


def test_new_accounts_start_without_setup(h: Harness, w: World) -> None:  # noqa: F811
    me = h.call(enrolled(h, w)[2], "GET", "/api/me").json
    assert me["onboardedAt"] is None
    assert me["age"] is None


def test_student_setup_saves_every_answer(h: Harness, w: World) -> None:  # noqa: F811
    cookie = enrolled(h, w)[2]
    res = h.call(cookie, "POST", "/api/me/setup", STUDENT)
    assert res.status == 200
    me = res.json
    assert me["displayName"] == "Minh Anh"
    assert (me["age"], me["occupation"], me["referralSource"]) == (19, "university", "instagram")
    assert (me["targetBand"], me["cefrLevel"]) == (7.5, "B2")
    assert me["skillRank"] == ["reading", "listening", "speaking", "writing"]
    assert (me["studyMinutes"], me["studyTime"], me["reminders"]) == (30, "evening", True)
    assert me["studyStyles"] == ["flashcard", "video"]
    assert me["onboardedAt"] is not None
    assert h.call(cookie, "GET", "/api/me").json["onboardedAt"] == me["onboardedAt"]


def test_student_must_answer_everything(h: Harness, w: World) -> None:  # noqa: F811
    cookie = enrolled(h, w)[2]
    partial = {k: v for k, v in STUDENT.items() if k != "cefrLevel"}
    assert h.call(cookie, "POST", "/api/me/setup", partial).status == 400
    dupes = {**STUDENT, "skillRank": ["reading", "reading", "writing", "speaking"]}
    assert h.call(cookie, "POST", "/api/me/setup", dupes).status == 400
    assert h.call(cookie, "POST", "/api/me/setup", {**STUDENT, "age": 15}).status == 400
    assert h.call(cookie, "POST", "/api/me/setup", {**STUDENT, "targetBand": 6.3}).status == 400
    assert h.call(cookie, "GET", "/api/me").json["onboardedAt"] is None


def test_teacher_setup_needs_only_name_and_age(h: Harness) -> None:
    h.create_user("setup-teacher@test.local", "teacher")
    cookie = h.login("setup-teacher@test.local")
    res = h.call(cookie, "POST", "/api/me/setup", {"displayName": "Co Linh", "age": 31, "cefrLevel": "C2"})
    assert res.status == 200
    assert (res.json["displayName"], res.json["age"], res.json["cefrLevel"]) == ("Co Linh", 31, None)
    assert res.json["onboardedAt"] is not None


def test_photo_round_trip_and_rules(h: Harness, w: World) -> None:  # noqa: F811
    cookie = enrolled(h, w)[2]
    assert _raw(h, cookie, "GET", "/api/me/photo").status_code == 404
    assert _raw(h, cookie, "PUT", "/api/me/photo", JPEG, "image/png").status_code == 415
    assert _raw(h, cookie, "PUT", "/api/me/photo", b"not a jpeg" * 20).status_code == 415
    big = b"\xff\xd8\xff" + b"\x00" * (301 * 1024) + b"\xff\xd9"
    assert _raw(h, cookie, "PUT", "/api/me/photo", big).status_code == 413

    res = _raw(h, cookie, "PUT", "/api/me/photo", JPEG)
    assert res.status_code == 200
    assert res.json()["avatarUrl"].startswith("/api/me/photo?v=")
    got = _raw(h, cookie, "GET", "/api/me/photo")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"
    assert got.content == JPEG


def test_photo_is_private_to_its_owner(h: Harness, w: World) -> None:  # noqa: F811
    owner, other = enrolled(h, w)[2], enrolled(h, w)[2]
    assert _raw(h, owner, "PUT", "/api/me/photo", JPEG).status_code == 200
    assert _raw(h, other, "GET", "/api/me/photo").status_code == 404
    assert h.owner_rows("select count(*) as n from user_photos")[0]["n"] >= 1


def test_anonymous_cannot_set_up(h: Harness) -> None:
    assert h.call(None, "POST", "/api/me/setup", STUDENT).status == 401
    assert h.call(None, "GET", "/api/me/photo").status == 401
