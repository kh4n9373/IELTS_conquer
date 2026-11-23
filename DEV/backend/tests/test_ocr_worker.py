import logging
import os
import signal
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fakes import make_pdf
from test_exam_keys import fill, structure

from conftest import Harness
from npa_api.ocr import worker
from npa_api.ocr.provider import OcrError, OcrJobExpired, OcrLine, OcrPage, OcrStatus
from npa_api.ocr.worker import LEASE, run_once

SECRET_TEXT = "invented-ocr-passage"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.at = start
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.at += timedelta(seconds=seconds)


def page(number: int, text: str = SECRET_TEXT) -> OcrPage:
    return OcrPage(page=number, text=text, lines=[OcrLine(text=text, bbox=None, confidence=None)])


class FakeProvider:
    def __init__(self, states: list[Any] | None = None, result: Any = None) -> None:
        self.submitted: list[bytes] = []
        self.polled: list[str] = []
        self.states = states if states is not None else ["done"]
        self.result = result if result is not None else [page(1), page(2)]
        self.submit_error: OcrError | None = None

    def submit(self, path: Path) -> str:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(path.read_bytes())
        return f"provider-{len(self.submitted)}"

    def status(self, job_id: str) -> OcrStatus:
        self.polled.append(job_id)
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return OcrStatus(job_id=job_id, state=state, result_url="https://r.example.test/x")

    def pages(self, status: OcrStatus) -> list[OcrPage]:
        if isinstance(self.result, Exception):
            raise self.result
        pages: list[OcrPage] = self.result
        return pages


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, Any]:
    a = h.create_user("ocr-a@test.local", "teacher")
    h.create_user("ocr-b@test.local", "teacher")
    return {"a": h.login("ocr-a@test.local"), "b": h.login("ocr-b@test.local"), "id": a}


@pytest.fixture(autouse=True)
def clean(h: Harness) -> Iterator[None]:
    h.owner_rows("delete from ocr_jobs")
    yield


def new_test(h: Harness, t: dict[str, Any], *, test_type: str = "course") -> str:
    body = {
        "title": "Private Title",
        "sourceType": "self_authored",
        "testType": test_type,
        "durationMinutes": 60,
    }
    return str(h.call(t["a"], "POST", "/api/tests", body).json["id"])


def upload(h: Harness, t: dict[str, Any], tid: str, pages: int = 2, route: str = "pdf") -> None:
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/{route}",
        content=make_pdf(pages),
        headers={"content-type": "application/pdf", "cookie": t["a"]},
    )
    assert res.status_code == 200


def job_of(h: Harness, tid: str) -> dict[str, Any]:
    rows = h.owner_rows(
        """select j.* from ocr_jobs j join test_assets a on a.id = j.asset_id
           where a.test_id = %s and a.kind = 'student_pdf'""",
        (tid,),
    )
    assert len(rows) == 1
    return rows[0]


def texts(h: Harness, asset: UUID) -> list[tuple[int, str]]:
    rows = h.owner_rows("select page, text from page_texts where asset_id = %s order by page", (asset,))
    return [(r["page"], r["text"]) for r in rows]


def once(h: Harness, provider: Any, clock: Clock) -> bool:
    return run_once(h.db.app_pool, h.store, provider, clock, sleep=clock.sleep)


def approve_student_pdf(h: Harness, tid: str) -> None:
    """M2 has no route for this. The worker's student path is exercised as a later milestone would use it."""
    h.owner_rows(
        """update test_assets set ocr_approved_at = %s, ocr_approved_by = teacher_id
           where test_id = %s and kind = 'student_pdf'""",
        (h.now, tid),
    )
    h.owner_rows(
        """insert into ocr_jobs (asset_id, teacher_id)
           select id, teacher_id from test_assets where test_id = %s and kind = 'student_pdf'""",
        (tid,),
    )


def setup_job(h: Harness, t: dict[str, Any]) -> tuple[str, Clock]:
    tid = new_test(h, t)
    upload(h, t, tid)
    approve_student_pdf(h, tid)
    return tid, Clock(h.now)


def test_no_upload_creates_an_ocr_job(h: Harness, t: dict[str, Any]) -> None:
    tid = new_test(h, t)
    upload(h, t, tid, route="key-source")
    upload(h, t, tid)
    assert h.owner_rows("select count(*) as n from ocr_jobs")[0]["n"] == 0


def test_replacing_pdf_removes_job_and_approval(h: Harness, t: dict[str, Any]) -> None:
    tid, _ = setup_job(h, t)
    upload(h, t, tid)
    assert h.owner_rows("select count(*) as n from ocr_jobs")[0]["n"] == 0
    rows = h.owner_rows("select ocr_approved_at from test_assets where test_id = %s", (tid,))
    assert [r["ocr_approved_at"] for r in rows] == [None]


def test_run_once_is_false_when_idle(h: Harness) -> None:
    assert once(h, FakeProvider(), Clock(h.now)) is False


def test_worker_stores_page_texts_on_success(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    provider = FakeProvider(["pending", "running", "done"], [page(1, "one\x00 here"), page(2, "two")])
    assert once(h, provider, clock) is True
    job = job_of(h, tid)
    assert (job["state"], job["error_code"], job["provider_job_id"]) == ("done", None, "provider-1")
    assert job["finished_at"] == clock.at
    assert texts(h, job["asset_id"]) == [(1, "one here"), (2, "two")]
    assert clock.sleeps == [5, 5]
    assert len(provider.submitted) == 1


def test_provider_id_is_committed_before_polling(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    seen: list[str | None] = []

    class Probe(FakeProvider):
        def status(self, job_id: str) -> OcrStatus:
            seen.append(job_of(h, tid)["provider_job_id"])
            return super().status(job_id)

    once(h, Probe(), clock)
    assert seen == ["provider-1"]


def test_expired_lease_is_reclaimed_without_second_submit(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    job = job_of(h, tid)
    h.owner_rows(
        """update ocr_jobs set state='running', attempts=1, provider_job_id='stored-id',
             lease_until=%s where id=%s""",
        (clock.at - timedelta(seconds=1), job["id"]),
    )
    provider = FakeProvider()
    assert once(h, provider, clock) is True
    assert provider.submitted == []
    assert provider.polled == ["stored-id"]
    after = job_of(h, tid)
    assert (after["state"], after["attempts"]) == ("done", 2)


def test_expired_provider_job_is_resubmitted_in_same_claim(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    job = job_of(h, tid)
    h.owner_rows(
        "update ocr_jobs set state='running', attempts=1, provider_job_id='old', lease_until=%s where id=%s",
        (clock.at - timedelta(seconds=1), job["id"]),
    )
    provider = FakeProvider([OcrJobExpired(), "done"])
    assert once(h, provider, clock) is True
    assert len(provider.submitted) == 1
    assert provider.polled == ["old", "provider-1"]
    after = job_of(h, tid)
    assert (after["state"], after["attempts"], after["provider_job_id"]) == ("done", 2, "provider-1")


def test_third_expired_lease_fails_without_claim(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    job = job_of(h, tid)
    h.owner_rows(
        "update ocr_jobs set state='running', attempts=3, provider_job_id='x', lease_until=%s where id=%s",
        (clock.at - timedelta(seconds=1), job["id"]),
    )
    provider = FakeProvider()
    assert once(h, provider, clock) is False
    after = job_of(h, tid)
    assert (after["state"], after["error_code"], after["attempts"]) == ("failed", "lease_expired", 3)
    assert after["finished_at"] == clock.at
    assert provider.polled == []


def test_retry_waits_60_then_300_then_fails(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    provider = FakeProvider()
    provider.submit_error = OcrError("provider_error")
    assert once(h, provider, clock) is True
    job = job_of(h, tid)
    assert (job["state"], job["attempts"], job["lease_until"]) == (
        "queued",
        1,
        clock.at + timedelta(seconds=60),
    )
    assert once(h, provider, clock) is False  # not due yet
    clock.at += timedelta(seconds=60)
    assert once(h, provider, clock) is True
    job = job_of(h, tid)
    assert (job["state"], job["attempts"], job["lease_until"]) == (
        "queued",
        2,
        clock.at + timedelta(seconds=300),
    )
    clock.at += timedelta(seconds=300)
    assert once(h, provider, clock) is True
    job = job_of(h, tid)
    assert (job["state"], job["attempts"], job["error_code"], job["lease_until"]) == (
        "failed",
        3,
        "provider_error",
        None,
    )
    clock.at += timedelta(days=1)
    assert once(h, provider, clock) is False


def test_third_failure_marks_failed_and_publish_still_works(h: Harness, t: dict[str, Any]) -> None:
    tid = new_test(h, t, test_type="ielts_standard")
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", {"sections": structure()}).status == 200
    upload(h, t, tid)
    approve_student_pdf(h, tid)
    fill(h, t, tid)
    clock = Clock(h.now)
    provider = FakeProvider()
    provider.submit_error = OcrError("provider_error")
    for _ in range(3):
        assert once(h, provider, clock) is True
        clock.at += timedelta(seconds=301)
    assert job_of(h, tid)["state"] == "failed"
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json == {"ready": True, "blockers": []}
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/publish").status == 200


def test_quota_429_is_retryable_with_error_code(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    provider = FakeProvider()
    provider.submit_error = OcrError("quota_exceeded")
    once(h, provider, clock)
    job = job_of(h, tid)
    assert (job["state"], job["error_code"], job["lease_until"]) == (
        "queued",
        "quota_exceeded",
        clock.at + timedelta(seconds=3600),
    )


@pytest.mark.parametrize(
    "bad",
    [
        [page(0), page(1)],
        [page(1), page(3)],
        [page(1), page(1)],
        [],
        [page(1), page(2, "lone \ud800")],
    ],
)
def test_invalid_result_is_bad_result(h: Harness, t: dict[str, Any], bad: list[OcrPage]) -> None:
    tid, clock = setup_job(h, t)
    once(h, FakeProvider(result=bad), clock)
    job = job_of(h, tid)
    assert (job["state"], job["error_code"], job["provider_job_id"]) == ("queued", "bad_result", None)
    assert texts(h, job["asset_id"]) == []


@pytest.mark.parametrize("result", [[page(1)], [page(1), page(2), page(3)]])
def test_page_count_mismatch_is_bad_result(h: Harness, t: dict[str, Any], result: list[OcrPage]) -> None:
    tid, clock = setup_job(h, t)
    once(h, FakeProvider(result=result), clock)
    job = job_of(h, tid)
    assert (job["state"], job["error_code"]) == ("queued", "bad_result")
    assert texts(h, job["asset_id"]) == []


def test_provider_failed_state_clears_id_for_resubmit(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    once(h, FakeProvider(["failed"]), clock)
    job = job_of(h, tid)
    assert (job["state"], job["error_code"], job["provider_job_id"]) == ("queued", "provider_error", None)


def test_storage_failure_is_storage_error(
    h: Harness, t: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, clock = setup_job(h, t)

    def boom(key: str, path: Path) -> None:
        raise RuntimeError("https://s3.internal/secret-key")

    monkeypatch.setattr(h.store, "get", boom)
    once(h, FakeProvider(), clock)
    assert job_of(h, tid)["error_code"] == "storage_error"


def test_polling_stops_within_the_lease(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    start = clock.at
    provider = FakeProvider(["running"])
    once(h, provider, clock)
    job = job_of(h, tid)
    assert (job["state"], job["error_code"], job["provider_job_id"]) == (
        "queued",
        "provider_timeout",
        "provider-1",
    )
    assert clock.at - start <= LEASE
    assert set(clock.sleeps) == {5}


def test_replaced_pdf_discards_the_result(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    old = job_of(h, tid)

    class Replacing(FakeProvider):
        def pages(self, status: OcrStatus) -> list[OcrPage]:
            upload(h, t, tid)
            return super().pages(status)

    assert once(h, Replacing(), clock) is True
    assert h.owner_rows("select count(*) as n from ocr_jobs")[0]["n"] == 0
    assert texts(h, old["asset_id"]) == []
    new = h.owner_rows("select id from test_assets where test_id = %s and kind = 'student_pdf'", (tid,))
    assert texts(h, new[0]["id"]) == []


def test_stale_attempt_cannot_write_or_finish(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    job = job_of(h, tid)

    class Stale(FakeProvider):
        def pages(self, status: OcrStatus) -> list[OcrPage]:
            h.owner_rows("update ocr_jobs set attempts = attempts + 1 where id = %s", (job["id"],))
            return super().pages(status)

    once(h, Stale(), clock)
    after = job_of(h, tid)
    assert (after["state"], after["attempts"]) == ("running", 2)
    assert texts(h, job["asset_id"]) == []


def test_logs_hold_only_job_id_and_code(
    h: Harness, t: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    tid, clock = setup_job(h, t)
    provider = FakeProvider()
    provider.submit_error = OcrError("provider_error")
    with caplog.at_level(logging.DEBUG, logger="npa.ocr"):
        once(h, provider, clock)
    records = [r for r in caplog.records if r.name == "npa.ocr"]
    assert records
    assert all(r.exc_info is None for r in records)
    text = caplog.text
    assert str(job_of(h, tid)["id"]) in text and "provider_error" in text
    assert "Private Title" not in text and SECRET_TEXT not in text


def test_error_code_constraint(h: Harness, t: dict[str, Any]) -> None:
    tid, _ = setup_job(h, t)
    with pytest.raises(psycopg.errors.CheckViolation):
        h.owner_rows("update ocr_jobs set error_code = 'Bad Code!' where id = %s", (job_of(h, tid)["id"],))


def test_retry_route_requeues_failed_job(h: Harness, t: dict[str, Any]) -> None:
    tid, _ = setup_job(h, t)
    job = job_of(h, tid)
    h.owner_rows(
        """update ocr_jobs set state='failed', attempts=3, error_code='provider_error',
             provider_job_id='x', finished_at=now() where id=%s""",
        (job["id"],),
    )
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/ocr")
    assert res.status == 200
    assert res.json == {"state": "queued", "errorCode": None}
    after = job_of(h, tid)
    assert after["id"] != job["id"]
    assert h.owner_rows("select 1 from ocr_jobs where id = %s", (job["id"],)) == []
    assert (after["state"], after["attempts"], after["lease_until"], after["provider_job_id"]) == (
        "queued",
        0,
        None,
        None,
    )
    assert (after["error_code"], after["finished_at"]) == (None, None)
    audit = h.owner_rows("select 1 from audit_log where action = 'test.ocr_retry' and entity_id = %s", (tid,))
    assert len(audit) == 1


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_stale_worker_after_retry_changes_nothing(h: Harness, t: dict[str, Any], outcome: str) -> None:
    tid, clock = setup_job(h, t)
    old = job_of(h, tid)

    class RetriedMeanwhile(FakeProvider):
        def pages(self, status: OcrStatus) -> list[OcrPage]:
            h.owner_rows("update ocr_jobs set state = 'failed' where id = %s", (old["id"],))
            assert h.call(t["a"], "POST", f"/api/tests/{tid}/ocr").status == 200
            # The new claim reaches the same attempt number the stale worker holds.
            with h.db.app_pool.connection() as conn:
                claimed = conn.execute(
                    "select * from app.ocr_claim(%s, %s)", (clock.at, clock.at + LEASE)
                ).fetchall()
            assert [c["attempts"] for c in claimed] == [1]
            h.owner_rows("update ocr_jobs set provider_job_id = 'new-claim' where state = 'running'")
            if outcome == "failure":
                raise OcrError("bad_result")
            return super().pages(status)

    assert once(h, RetriedMeanwhile(), clock) is True
    current = job_of(h, tid)
    assert current["id"] != old["id"]
    assert (current["state"], current["attempts"], current["error_code"], current["provider_job_id"]) == (
        "running",
        1,
        None,
        "new-claim",
    )
    assert texts(h, old["asset_id"]) == []


@pytest.mark.parametrize("state", ["queued", "running", "done"])
def test_retry_route_refuses_other_states(h: Harness, t: dict[str, Any], state: str) -> None:
    tid, _ = setup_job(h, t)
    h.owner_rows("update ocr_jobs set state = %s where id = %s", (state, job_of(h, tid)["id"]))
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/ocr")
    assert (res.status, res.json["error"]) == (409, "ocr_not_retryable")


def test_retry_route_404_without_pdf_and_for_other_teacher(h: Harness, t: dict[str, Any]) -> None:
    empty = new_test(h, t)
    assert h.call(t["a"], "POST", f"/api/tests/{empty}/ocr").status == 404
    tid, _ = setup_job(h, t)
    assert h.call(t["b"], "POST", f"/api/tests/{tid}/ocr").status == 404


def test_retry_route_refuses_archived_test(h: Harness, t: dict[str, Any]) -> None:
    tid, _ = setup_job(h, t)
    h.owner_rows("update tests set status = 'archived' where id = %s", (tid,))
    res = h.call(t["a"], "POST", f"/api/tests/{tid}/ocr")
    assert (res.status, res.json["error"]) == (409, "test_locked")


def test_detail_exposes_only_state_and_code(h: Harness, t: dict[str, Any]) -> None:
    empty = new_test(h, t)
    assert h.call(t["a"], "GET", f"/api/tests/{empty}").json["ocr"] is None
    tid, clock = setup_job(h, t)
    once(h, FakeProvider(["failed"]), clock)
    body = h.call(t["a"], "GET", f"/api/tests/{tid}").json
    assert body["ocr"] == {"state": "queued", "errorCode": "provider_error"}


def test_worker_main_names_missing_setting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in worker.REQUIRED:
        monkeypatch.setenv(name, "configured-value")
    monkeypatch.delenv("BAIDU_OCR_ACCESS_TOKEN")
    with pytest.raises(SystemExit) as caught:
        worker.main()
    assert "BAIDU_OCR_ACCESS_TOKEN" in str(caught.value)
    assert "configured-value" not in str(caught.value) + capsys.readouterr().err


def test_worker_loop_processes_a_job_and_sigterm_stops_it_between_jobs(h: Harness, t: dict[str, Any]) -> None:
    first, clock = setup_job(h, t)
    second = new_test(h, t)
    upload(h, t, second)
    approve_student_pdf(h, second)

    class Terminated(FakeProvider):
        def pages(self, status: OcrStatus) -> list[OcrPage]:
            os.kill(os.getpid(), signal.SIGTERM)  # arrives while the first job is in progress
            return super().pages(status)

    provider = Terminated()
    stop = threading.Event()
    previous = {name: signal.getsignal(name) for name in (signal.SIGTERM, signal.SIGINT)}
    try:
        worker.install_stop_handlers(stop)
        worker.run_loop(h.db.app_pool, h.store, provider, stop, clock, sleep=clock.sleep)
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)
    assert stop.is_set()
    assert len(provider.submitted) == 1
    assert sorted(job_of(h, tid)["state"] for tid in (first, second)) == ["done", "queued"]


def test_worker_loop_does_not_claim_once_stopped(h: Harness, t: dict[str, Any]) -> None:
    tid, clock = setup_job(h, t)
    stop = threading.Event()
    stop.set()
    provider = FakeProvider()
    worker.run_loop(h.db.app_pool, h.store, provider, stop, clock, sleep=clock.sleep)
    assert provider.submitted == []
    assert job_of(h, tid)["attempts"] == 0


def test_worker_loop_survives_an_error_and_logs_only_its_type(
    h: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop = threading.Event()

    def boom(*_args: Any, **_kwargs: Any) -> bool:
        stop.set()
        raise RuntimeError("https://r.example.test/x?signature=sig-value")

    monkeypatch.setattr(worker, "run_once", boom)
    with caplog.at_level(logging.ERROR, logger="npa.ocr"):
        worker.run_loop(h.db.app_pool, h.store, FakeProvider(), stop, Clock(h.now))
    assert "RuntimeError" in caplog.text
    assert "sig-value" not in caplog.text
