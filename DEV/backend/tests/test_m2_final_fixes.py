"""Final-review fixes for M2. All keys, answers and file names here are invented."""

import json
import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fakes import make_pdf
from test_exam_keys import items_of, make
from test_key_import import jobs, post, put_key, scan_page, scanned_test, stored, work
from test_ocr_worker import Clock, FakeProvider

from conftest import Harness
from npa_api.exams.key_import import KeyLine, parse_key_lines
from npa_api.ocr.paddle import parse_jsonl
from npa_api.ocr.provider import OcrLine, OcrPage
from npa_api.ocr.worker import run_once

LEAK = "tests/invented-storage-key.pdf"


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, Any]:
    a = h.create_user("fix-a@test.local", "teacher")
    return {"a": h.login("fix-a@test.local"), "id": a}


@pytest.fixture(autouse=True)
def clean(h: Harness) -> Iterator[None]:
    h.owner_rows("delete from ocr_jobs")
    yield


def once(h: Harness, provider: Any) -> bool:
    clock = Clock(h.now)
    return run_once(h.db.app_pool, h.store, provider, clock, sleep=clock.sleep)


def put_student(h: Harness, t: dict[str, Any], tid: str) -> None:
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/pdf",
        content=make_pdf(1),
        headers={"content-type": "application/pdf", "cookie": t["a"]},
    )
    assert res.status_code == 200


def asset(h: Harness, tid: str, kind: str) -> dict[str, Any]:
    rows = h.owner_rows("select * from test_assets where test_id = %s and kind = %s", (tid, kind))
    assert len(rows) == 1
    return rows[0]


def stray_job(h: Harness, row: dict[str, Any]) -> None:
    h.owner_rows(
        "insert into ocr_jobs (asset_id, teacher_id) values (%s, %s)", (row["id"], row["teacher_id"])
    )


def review(h: Harness, t: dict[str, Any], tid: str, *numbers: int) -> Any:
    rows = items_of(h, t, tid)
    body = {"items": [{"number": n, "answers": rows[n]["answers"]} for n in numbers]}
    return h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", body)


# ---- 1. No file reaches OCR without a recorded teacher action ----


def test_stray_queued_row_on_unapproved_asset_is_never_claimed(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    put_student(h, t, tid)
    stray_job(h, asset(h, tid, "student_pdf"))
    provider = FakeProvider()
    assert once(h, provider) is False
    assert provider.submitted == []


def test_expired_running_row_on_unapproved_asset_is_not_reclaimed(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    put_student(h, t, tid)
    row = asset(h, tid, "student_pdf")
    h.owner_rows(
        """insert into ocr_jobs (asset_id, teacher_id, state, attempts, lease_until)
           values (%s, %s, 'running', 1, %s)""",
        (row["id"], row["teacher_id"], h.now - timedelta(minutes=1)),
    )
    provider = FakeProvider()
    assert once(h, provider) is False
    assert provider.submitted == []


def test_scan_records_approval_and_the_job_can_then_be_claimed(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    assert asset(h, tid, "key_source")["ocr_approved_at"] is None
    assert post(h, t, tid, "scan").status == 202
    row = asset(h, tid, "key_source")
    assert row["ocr_approved_at"] is not None
    assert row["ocr_approved_by"] == t["id"]
    audit = h.owner_rows("select after from audit_log where action = 'key.scan' and entity_id = %s", (tid,))
    assert audit[-1]["after"] == {"ocrApproved": True}
    provider = FakeProvider(result=[scan_page(("1. TRUE", 0.99))])
    assert once(h, provider) is True
    assert len(provider.submitted) == 1


def test_scan_refused_for_text_key_records_no_approval(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    assert post(h, t, tid, "scan").status == 409
    assert asset(h, tid, "key_source")["ocr_approved_at"] is None


def test_replacing_a_key_drops_approval(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    assert post(h, t, tid, "scan").status == 202
    put_key(h, t, tid, make_pdf(1, text=None))
    assert asset(h, tid, "key_source")["ocr_approved_at"] is None
    assert jobs(h, tid) == []
    assert once(h, FakeProvider()) is False


def test_claim_refuses_a_job_on_an_archived_test(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    assert post(h, t, tid, "scan").status == 202
    h.owner_rows("update tests set status = 'archived' where id = %s", (tid,))
    provider = FakeProvider()
    assert once(h, provider) is False
    assert provider.submitted == []


def test_retry_cannot_create_a_job_for_an_unapproved_student_pdf(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    put_student(h, t, tid)
    row = asset(h, tid, "student_pdf")
    h.owner_rows(
        """insert into ocr_jobs (asset_id, teacher_id, state, attempts, error_code)
           values (%s, %s, 'failed', 3, 'provider_error')""",
        (row["id"], row["teacher_id"]),
    )
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/ocr")
    assert (res.status, res.json["error"]) == (409, "ocr_not_retryable")
    states = h.owner_rows("select state from ocr_jobs where asset_id = %s", (row["id"],))
    assert [s["state"] for s in states] == ["failed"]


def test_inert_rows_are_not_reported_as_pending(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    put_student(h, t, tid)
    stray_job(h, asset(h, tid, "key_source"))
    stray_job(h, asset(h, tid, "student_pdf"))
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["scan"] is None
    assert h.call(t["a"], "GET", f"/api/tests/{tid}").json["ocr"] is None
    # The stray row does not block the teacher's explicit action.
    assert post(h, t, tid, "scan").status == 202
    assert [j["state"] for j in jobs(h, tid)] == ["queued"]


# ---- 2. Replacing the key ----


def test_replacing_the_key_flags_unreviewed_imported_rows(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE 2. TRUE"))
    post(h, t, tid, "import")
    typed = {"items": [{"number": 3, "answers": ["TRUE"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", typed).status == 200
    assert review(h, t, tid, 2).status == 200
    put_key(h, t, tid, make_pdf(1, text=None))
    rows = stored(h, tid)
    assert rows[1]["flagged"] is True  # imported, unreviewed
    assert rows[2]["flagged"] is False  # reviewed
    assert rows[3]["flagged"] is False  # typed by the teacher


def test_import_lists_reviewed_answers_that_differ(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    body = {"items": [{"number": n, "answers": ["TRUE"]} for n in (1, 2)]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    assert review(h, t, tid, 1, 2).status == 200
    put_key(h, t, tid, make_pdf(1, text="1. TRUE 2. FALSE"))
    res = post(h, t, tid, "import")
    assert res.json["conflicts"] == [2]
    rows = stored(h, tid)
    assert rows[2]["answers"] == ["TRUE"]  # the teacher's reviewed value is kept
    assert (rows[1]["flagged"], rows[2]["flagged"]) == (False, True)
    blockers = h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]
    assert "unreviewed_answer" in blockers


# ---- 3. Review only what the teacher saw ----


def test_review_of_a_changed_answer_is_refused_whole(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    body = {"items": [{"number": n, "answers": ["TRUE"]} for n in (1, 2)]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    seen = {
        "items": [{"number": 1, "answers": ["TRUE"]}, {"number": 2, "answers": ["FALSE"]}],
    }
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/key/review", seen)
    assert (res.status, res.json) == (409, {"error": "key_changed"})
    rows = items_of(h, t, tid)
    assert (rows[1]["reviewed"], rows[2]["reviewed"]) == (False, False)


def test_review_refused_while_a_scan_is_active(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    typed = {"items": [{"number": 1, "answers": ["TRUE"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", typed).status == 200
    assert post(h, t, tid, "scan").status == 202
    res = review(h, t, tid, 1)
    assert (res.status, res.json["error"]) == (409, "ocr_in_progress")
    assert items_of(h, t, tid)[1]["reviewed"] is False


def test_review_audit_holds_no_answers(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    typed = {"items": [{"number": 1, "answers": ["invented-audit-answer"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", typed).status == 200
    assert review(h, t, tid, 1).status == 200
    audit = h.owner_rows("select after from audit_log where action = 'key.review' and entity_id = %s", (tid,))
    assert "invented-audit-answer" not in json.dumps(audit[-1]["after"])


# ---- 4. A number inside an answer ----


def test_number_inside_an_answer_flags_the_line(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    # Question 14 takes free text, so only the line rule can catch the cut-off answer.
    put_key(h, t, tid, make_pdf(1, text="14 about 200 years"))
    res = post(h, t, tid, "import")
    assert res.json["unmatched"] == 1
    row = stored(h, tid)[14]
    assert (row["answers"], row["flagged"]) == (["about"], True)


def test_every_draft_on_a_line_with_an_unknown_number_is_flagged(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1 TRUE 2 FALSE 99 TRUE"))
    post(h, t, tid, "import")
    rows = stored(h, tid)
    assert (rows[1]["flagged"], rows[2]["flagged"]) == (True, True)


def test_lines_of_known_numbers_stay_unflagged() -> None:
    drafts = parse_key_lines([KeyLine("1 TRUE 2 FALSE", 1, None, None)])
    assert [d.flagged for d in drafts] == [False, False]
    assert drafts[0].line_numbers == frozenset({1, 2})


# ---- 5. Multi-line and table OCR blocks ----


def paddle_body(*contents: str) -> bytes:
    blocks = [{"block_content": c, "block_bbox": [0, 0, 10, 10]} for c in contents]
    pruned = {"parsing_res_list": blocks, "width": 100, "height": 100}
    result = {"layoutParsingResults": [{"prunedResult": pruned}]}
    return json.dumps({"result": result, "errorCode": 0}).encode()


def test_multi_line_block_becomes_lines() -> None:
    page = parse_jsonl(paddle_body("1. TRUE\n2. FALSE\r\n3. NOT GIVEN"))[0]
    assert [line.text for line in page.lines] == ["1. TRUE", "2. FALSE", "3. NOT GIVEN"]
    assert all(line.bbox == page.lines[0].bbox for line in page.lines)


def test_table_block_is_read_as_plain_cells() -> None:
    table = (
        "<table><tr><th>No</th><th>Answer</th></tr>"
        "<tr><td>1</td><td>river &amp; bank</td></tr>"
        "<tr><td>2</td><td><b>tall</b><br>trees</td></tr>"
        "<tr><td>3</td><td>A</td><td>4</td><td>B</td></tr></table>"
    )
    page = parse_jsonl(paddle_body(table))[0]
    assert [line.text for line in page.lines] == ["No Answer", "1 river & bank", "2 tall trees", "3 A 4 B"]


def test_table_markup_never_reaches_a_line() -> None:
    table = "<table><tr><td>1</td><td><script>x()</script>A</td></tr></table>"
    page = parse_jsonl(paddle_body(table))[0]
    assert [line.text for line in page.lines] == ["1 A"]


def test_scan_with_no_usable_answers_fails_with_a_fixed_code(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    assert post(h, t, tid, "scan").status == 202
    work(h, FakeProvider(result=[scan_page(("Answer key", 0.99))]))
    job = jobs(h, tid)[0]
    assert (job["state"], job["error_code"]) == ("failed", "no_answers_found")
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/key").json["scan"] == {
        "state": "failed",
        "errorCode": "no_answers_found",
    }


def test_invented_table_key_fills_the_draft(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    assert post(h, t, tid, "scan").status == 202
    table = "<table><tr><td>1</td><td>TRUE</td></tr><tr><td>2</td><td>FALSE</td></tr></table>"
    lines = parse_jsonl(paddle_body(table))[0].lines
    page = OcrPage(page=1, text=table, lines=[OcrLine(x.text, x.bbox, 0.99) for x in lines])
    work(h, FakeProvider(result=[page]))
    assert jobs(h, tid)[0]["state"] == "done"
    rows = stored(h, tid)
    assert (rows[1]["answers"], rows[2]["answers"]) == (["TRUE"], ["FALSE"])


# ---- 7. Storage keys stay out of logs ----


def boom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(f"Failed to upload /tmp/x to bucket/{LEAK}")


@pytest.mark.parametrize("route", ["pdf", "key-source"])
def test_failed_upload_hides_the_storage_key(
    h: Harness,
    t: dict[str, Any],
    route: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tid = make(h, t, pdf=False)
    monkeypatch.setattr(h.store, "put", boom)
    h.client.cookies.clear()
    with caplog.at_level(logging.DEBUG):
        res = h.client.put(
            f"/api/tests/{tid}/{route}",
            content=make_pdf(1),
            headers={"content-type": "application/pdf", "cookie": t["a"]},
        )
    assert (res.status_code, res.json()) == (503, {"error": "storage_unavailable"})
    assert LEAK not in caplog.text


def test_failed_signing_hides_the_storage_key(
    h: Harness, t: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tid = make(h, t, pdf=False)
    put_student(h, t, tid)
    monkeypatch.setattr(h.store, "signed_url", boom)
    with caplog.at_level(logging.DEBUG):
        res = h.call(t["a"], "GET", f"/api/tests/{tid}/pdf-url")
    assert (res.status, res.json) == (503, {"error": "storage_unavailable"})
    assert LEAK not in caplog.text


def test_failed_key_download_drops_the_cause(
    h: Harness, t: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from npa_api.core.http import HttpError
    from npa_api.routes.test_keys import read_key_lines

    class Ctx:
        store = type("S", (), {"get": staticmethod(boom)})()

    with pytest.raises(HttpError) as caught:
        read_key_lines(Ctx(), "any-key")  # type: ignore[arg-type]
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


# ---- 8. Audit ----


def test_key_source_audit_has_no_hash_but_student_pdf_keeps_it(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    put_student(h, t, tid)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    rows = h.owner_rows(
        "select after from audit_log where action = 'test.asset_upload' and entity_id = %s", (tid,)
    )
    by_kind = {r["after"]["kind"]: r["after"] for r in rows}
    assert "sha256" in by_kind["student_pdf"]
    assert "sha256" not in by_kind["key_source"]


# ---- Worker lock order ----


def test_store_result_locks_the_test_row_first(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    put_student(h, t, tid)
    row = asset(h, tid, "student_pdf")
    h.owner_rows(
        "update test_assets set ocr_approved_at = %s, ocr_approved_by = %s where id = %s",
        (h.now, t["id"], row["id"]),
    )
    stray_job(h, row)
    statements: list[str] = []

    class Spy:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
            statements.append(" ".join(query.split()))
            return self.inner.execute(query, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    from contextlib import contextmanager

    from npa_api.ocr import worker

    real = worker.with_identity

    @contextmanager
    def spying(pool: Any, identity: Any) -> Iterator[Any]:
        with real(pool, identity) as tx:
            yield Spy(tx)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker, "with_identity", spying)
    try:
        assert once(h, FakeProvider(result=[OcrPage(page=1, text="x", lines=[])])) is True
    finally:
        monkeypatch.undo()
    done = next(i for i, s in enumerate(statements) if s.startswith("update ocr_jobs set state = 'done'"))
    assert statements[done - 1].startswith("select status from tests where id = %s for update")
