"""Key source region: signed URL, source summary, normalized text boxes, and no stale regions."""

import json
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import make_pdf
from test_exam_keys import make

from conftest import Harness
from npa_api.exams.key_import import pdf_lines
from npa_api.exams.regions import fraction_box


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, Any]:
    a = h.create_user("reg-a@test.local", "teacher")
    h.create_user("reg-b@test.local", "teacher")
    h.create_user("reg-s@test.local", "student", a)
    return {
        "a": h.login("reg-a@test.local"),
        "b": h.login("reg-b@test.local"),
        "s": h.login("reg-s@test.local"),
        "id": a,
    }


@pytest.fixture(autouse=True)
def clean(h: Harness) -> Iterator[None]:
    h.owner_rows("delete from ocr_jobs")
    yield


def put_key(h: Harness, t: dict[str, Any], tid: str, pdf: bytes) -> dict[str, Any]:
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/key-source",
        content=pdf,
        headers={"content-type": "application/pdf", "cookie": t["a"]},
    )
    assert res.status_code == 200
    body: dict[str, Any] = res.json()
    return body


def boxes(pdf: bytes) -> list[Any]:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "key.pdf"
        path.write_bytes(pdf)
        return [line.bbox for line in pdf_lines(path)]


def items(h: Harness, tid: str) -> dict[tuple[int, int], dict[str, Any]]:
    rows = h.owner_rows(
        """select v.version, i.question_number, i.source_page, i.source_bbox, i.answers
           from keys.answer_key_items i join keys.answer_key_versions v on v.id = i.version_id
           where v.test_id = %s""",
        (tid,),
    )
    return {(r["version"], r["question_number"]): r for r in rows}


# ---- route ----


def test_key_source_url_for_owner_only(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    url = f"/api/tests/{tid}/key-source-url"
    assert h.call(t["a"], "GET", url).status == 404
    put_key(h, t, tid, make_pdf(text="1. A"))
    res = h.call(t["a"], "GET", url)
    assert res.status == 200
    assert res.json.keys() == {"url", "expiresIn"} and res.json["expiresIn"] == 300
    assert res.headers["cache-control"] == "no-store"
    assert h.call(t["b"], "GET", url).status == 404
    assert h.call(t["s"], "GET", url).status == 403
    assert h.call(None, "GET", url).status == 401


def test_key_source_url_signs_the_key_file_not_the_student_pdf(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)  # has a student_pdf row only
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/key-source-url").status == 404


def test_key_source_url_without_storage(
    h: Harness, t: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(text="1. A"))
    monkeypatch.setattr(h.app.state.ctx, "store", None)
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/key-source-url").status == 503


# ---- GET /key source ----


def test_get_key_reports_source_without_storage_key(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["source"] is None
    put_key(h, t, tid, make_pdf(pages=2, text=None))
    body = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json
    asset = h.owner_rows(
        "select id, storage_key from test_assets where test_id = %s and kind = 'key_source'", (tid,)
    )[0]
    assert body["source"] == {
        "kind": "key_source",
        "pageCount": 2,
        "hasTextLayer": False,
        "revision": str(asset["id"]),
    }
    assert asset["storage_key"] not in json.dumps(body)
    assert body["source"]["revision"] not in asset["storage_key"]


def test_revision_changes_with_the_stored_file(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(text="1. A"))
    first = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["source"]["revision"]
    put_key(h, t, tid, make_pdf(text="1. A"))
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["source"]["revision"] != first


def test_url_for_a_replaced_revision_is_refused(h: Harness, t: dict[str, Any]) -> None:
    """The page's items and the file it opens must belong together, even if the file just changed."""
    tid = make(h, t)
    url = f"/api/tests/{tid}/key-source-url"
    put_key(h, t, tid, make_pdf(text="1. A"))
    old = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["source"]["revision"]
    assert h.call(t["a"], "GET", f"{url}?revision={old}").status == 200
    put_key(h, t, tid, make_pdf(text="1. B"))
    res = h.call(t["a"], "GET", f"{url}?revision={old}")
    assert (res.status, res.json) == (409, {"error": "key_source_changed"})
    new = h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["source"]["revision"]
    assert h.call(t["a"], "GET", f"{url}?revision={new}").status == 200
    assert h.call(t["b"], "GET", f"{url}?revision={new}").status == 404


def test_get_key_waits_for_a_file_swap_in_progress(h: Harness, t: dict[str, Any]) -> None:
    """Items and revision are read together, so a swap cannot land between the two reads."""
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(text="1. A"))
    results: list[int] = []
    reader = threading.Thread(
        target=lambda: results.append(h.call(t["a"], "GET", f"/api/tests/{tid}/key").status)
    )
    with h.db.owner_pool.connection() as conn:
        conn.execute("select 1 from tests where id = %s for update", (tid,))
        reader.start()
        reader.join(0.4)
        assert reader.is_alive()
    reader.join(5)
    assert results == [200]


# ---- normalized boxes ----


def test_fraction_box_rejects_bad_values() -> None:
    assert fraction_box(10, 20, 30, 40, 100, 100) == (0.1, 0.2, 0.3, 0.4)
    assert fraction_box(0, 0, 100, 100, 100, 100) == (0.0, 0.0, 1.0, 1.0)
    assert fraction_box(10, 20, 10, 40, 100, 100) is None
    assert fraction_box(10, 20, 130, 40, 100, 100) is None
    assert fraction_box(10, 20, 30, 40, 0, 100) is None
    assert fraction_box(float("nan"), 20, 30, 40, 100, 100) is None


def test_text_box_is_top_left_based() -> None:
    # Baseline at x=20, y=100 on a 200pt page.
    [box] = boxes(make_pdf(text="1. River"))
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 == pytest.approx(0.1, abs=0.01)
    assert y1 == pytest.approx(0.5, abs=0.015)  # the baseline is the bottom of the text
    assert y0 < y1 and x0 < x1 <= 0.5
    assert y1 - y0 < 0.08


def test_each_line_gets_its_own_box() -> None:
    first, second = boxes(make_pdf(text="1. A) Tj 0 -40 Td (2. B"))
    assert first is not None and second is not None
    assert second[3] - first[3] == pytest.approx(0.2, abs=0.01)  # 40pt lower on a 200pt page


def test_text_box_follows_page_rotation() -> None:
    # Turned 90 degrees clockwise: the text now runs down the page, from the top, at mid width.
    [box] = boxes(make_pdf(text="1. River", page_extra="/Rotate 90"))
    assert box is not None
    x0, y0, x1, y1 = box
    assert y0 == pytest.approx(0.1, abs=0.01)
    assert x0 == pytest.approx(0.5, abs=0.015)
    assert y1 - y0 > x1 - x0


def test_text_box_is_relative_to_the_crop_box() -> None:
    [box] = boxes(make_pdf(text="1. A", page_extra="/CropBox [10 50 110 150]"))
    assert box is not None
    x0, _, _, y1 = box
    assert x0 == pytest.approx(0.1, abs=0.02)  # (20 - 10) / 100, plus the side bearing of the "1" glyph
    assert y1 == pytest.approx(0.5, abs=0.02)  # (150 - 100) / 100


def test_text_outside_the_crop_box_has_no_box() -> None:
    assert boxes(make_pdf(text="1. A", page_extra="/CropBox [100 0 200 50]")) == [None]


# ---- no stale regions ----


def test_replacing_key_source_clears_draft_regions_only(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(text="1. TRUE"))
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/import").status == 200
    first = items(h, tid)[(1, 1)]
    assert first["source_page"] == 1 and first["source_bbox"] is not None

    h.owner_rows(
        """update keys.answer_key_versions set approved_at = now(), approved_by = teacher_id
           where test_id = %s""",
        (tid,),
    )
    h.owner_rows(
        """insert into keys.answer_key_versions (test_id, teacher_id, version)
           select id, teacher_id, 2 from tests where id = %s""",
        (tid,),
    )
    h.owner_rows(
        """insert into keys.answer_key_items
             (version_id, teacher_id, question_number, answers, origin, source_page, source_bbox)
           select id, teacher_id, 1, '{TRUE}', 'text_layer', 1, '[0.1, 0.1, 0.2, 0.2]'
           from keys.answer_key_versions where test_id = %s and version = 2""",
        (tid,),
    )

    put_key(h, t, tid, make_pdf(text="1. FALSE"))
    after = items(h, tid)
    assert (after[(2, 1)]["source_page"], after[(2, 1)]["source_bbox"]) == (None, None)
    assert after[(2, 1)]["answers"] == ["TRUE"]
    assert after[(1, 1)]["source_page"] == first["source_page"]
    assert after[(1, 1)]["source_bbox"] == first["source_bbox"]


def test_replacing_student_pdf_keeps_key_regions(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    put_key(h, t, tid, make_pdf(text="1. TRUE"))
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/import").status == 200
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/pdf",
        content=make_pdf(),
        headers={"content-type": "application/pdf", "cookie": t["a"]},
    )
    assert res.status_code == 200
    assert items(h, tid)[(1, 1)]["source_page"] == 1
