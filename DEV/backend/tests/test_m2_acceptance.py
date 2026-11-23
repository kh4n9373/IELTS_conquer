"""M2 acceptance: a teacher authors and publishes a pilot-shaped test end to end (invented answers)."""

from collections import Counter
from typing import Any

from fakes import make_pdf

from conftest import Harness
from npa_api.exams.types import QUESTION_GROUPS

# (passage pages, question pages, [(first, last, type, word limit)])
PILOT: list[tuple[list[int], int, list[tuple[int, int, str, int | None]]]] = [
    ([2, 3], 4, [(1, 4, "matching_features", None), (5, 6, "multiple_choice", None), (7, 8, "tfng", None)]),
    ([5], 6, [(9, 12, "matching_headings", None), (13, 16, "table_completion", 2)]),
    (
        [7, 8],
        9,
        [
            (17, 20, "matching_information", None),
            (21, 22, "multiple_choice", None),
            (23, 24, "short_answer", 2),
        ],
    ),
    (
        [10],
        11,
        [(25, 28, "sentence_endings", None), (29, 30, "tfng", None), (31, 32, "summary_completion", 1)],
    ),
    (
        [12, 13],
        14,
        [
            (33, 36, "matching_features", None),
            (37, 38, "sentence_completion", 2),
            (39, 40, "multiple_choice", None),
        ],
    ),
]
SAMPLE = {
    "matching_features": "C",
    "multiple_choice": "B",
    "tfng": "NOT GIVEN",
    "matching_headings": "iv",
    "table_completion": "river",
    "matching_information": "D",
    "short_answer": "tall trees",
    "sentence_endings": "F",
    "summary_completion": "soil",
    "sentence_completion": "dry season",
}


def band_paths(value: Any, path: str = "$") -> list[str]:
    """Every place a key named `band` appears at any depth."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "band":
                found.append(f"{path}.{key}")
            found += band_paths(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += band_paths(item, f"{path}[{index}]")
    return found


def sections() -> list[dict[str, Any]]:
    out = []
    for i, (passage, question, ranges) in enumerate(PILOT, start=1):
        out.append(
            {
                "title": f"Passage {i}",
                "passagePages": passage,
                "questionPages": [question],
                "ranges": [
                    {"from": a, "to": b, "questionType": kind} | ({"wordLimit": w} if w else {})
                    for a, b, kind, w in ranges
                ],
            }
        )
    return out


def answers() -> list[dict[str, Any]]:
    return [
        {"number": n, "answers": [SAMPLE[kind]]}
        for _, _, ranges in PILOT
        for a, b, kind, _ in ranges
        for n in range(a, b + 1)
    ]


def test_teacher_authors_and_publishes_pilot_test(h: Harness) -> None:
    h.create_user("acc-a@test.local", "teacher")
    h.create_user("acc-b@test.local", "teacher")
    h.create_user("acc-s@test.local", "student", h.create_user("acc-c@test.local", "teacher"))
    a, b, s = (h.login(f"acc-{x}@test.local") for x in "abs")
    bodies: list[Any] = []

    def call(cookie: str, method: str, url: str, body: Any = None) -> Any:
        res = h.call(cookie, method, url, body)
        bodies.append(res.json)
        return res

    created = call(
        a,
        "POST",
        "/api/tests",
        {"title": "Pilot", "sourceType": "self_authored", "testType": "course", "durationMinutes": 120},
    )
    assert created.status == 201
    tid = created.json["id"]
    assert (created.json["testType"], created.json["durationMinutes"]) == ("course", 120)

    h.client.cookies.clear()
    upload = h.client.put(
        f"/api/tests/{tid}/pdf",
        content=make_pdf(15),
        headers={"content-type": "application/pdf", "cookie": a},
    )
    assert upload.status_code == 200
    bodies.append(upload.json())
    assert upload.json()["pageCount"] == 15

    structure = call(a, "PUT", f"/api/tests/{tid}/structure", {"sections": sections()})
    assert structure.status == 200

    # 1. Not ready without answers, and while one answer is unreviewed.
    res = call(a, "POST", f"/api/tests/{tid}/publish")
    assert (res.status, res.json["error"]) == (409, "not_ready")
    assert call(a, "PUT", f"/api/tests/{tid}/key/items", {"items": answers()}).status == 200
    assert call(a, "POST", f"/api/tests/{tid}/key/review", {"items": answers()[:39]}).status == 200
    res = call(a, "POST", f"/api/tests/{tid}/publish")
    assert (res.status, res.json["error"]) == (409, "not_ready")
    assert call(a, "POST", f"/api/tests/{tid}/key/review", {"items": answers()[39:]}).status == 200
    jobs = h.owner_rows(
        "select j.id from ocr_jobs j join test_assets x on x.id = j.asset_id where x.test_id = %s",
        (tid,),
    )
    assert jobs == []  # nothing was queued for OCR: no teacher asked for it
    approved = h.owner_rows(
        "select 1 from test_assets where test_id = %s and ocr_approved_at is not null", (tid,)
    )
    assert approved == []
    published = call(a, "POST", f"/api/tests/{tid}/publish")
    assert published.status == 200
    assert published.json["status"] == "published"

    # 2. Shape of the published test.
    detail = call(a, "GET", f"/api/tests/{tid}")
    assert detail.status == 200
    assert detail.json["status"] == "published"
    assert len(detail.json["sections"]) == 5
    questions = [q for sec in detail.json["sections"] for q in sec["questions"]]
    assert [q["number"] for q in questions] == list(range(1, 41))
    counts = Counter(QUESTION_GROUPS[q["questionType"]] for q in questions)
    assert dict(counts) == {"matching": 20, "completion": 10, "multiple_choice": 6, "tfng": 4}

    # 4. Other tenants and roles.
    for method, url in [
        ("GET", f"/api/tests/{tid}"),
        ("GET", f"/api/tests/{tid}/key"),
        ("POST", f"/api/tests/{tid}/publish"),
    ]:
        assert call(b, method, url).status == 404, (method, url)
        assert call(s, method, url).status == 403, (method, url)

    # 5. Locked after publishing.
    locked = call(a, "PUT", f"/api/tests/{tid}/key/items", {"items": answers()[:1]})
    assert (locked.status, locked.json["error"]) == (409, "test_locked")
    locked = call(a, "PUT", f"/api/tests/{tid}/structure", {"sections": sections()})
    assert (locked.status, locked.json["error"]) == (409, "test_locked")

    # 3. No response carries a band.
    assert len(bodies) > 10
    assert [p for body in bodies for p in band_paths(body)] == []


def test_band_helper_finds_nested_keys() -> None:
    assert band_paths({"a": [{"b": {"band": 1}}]}) == ["$.a[0].b.band"]
    assert band_paths({"a": [1, {"c": 2}]}) == []
