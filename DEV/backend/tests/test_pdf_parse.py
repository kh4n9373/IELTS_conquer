"""PDF tests: upload with consent, OCR (or the text layer), then Gemini parsing into the shared schema."""

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from fakes import make_pdf
from test_ocr_worker import Clock, FakeProvider, page

from conftest import Harness
from npa_api.exams.structure import StructureBody
from npa_api.ocr.gemini import GeminiParser
from npa_api.ocr.provider import OcrError
from npa_api.ocr.worker import run_once

PARSED = StructureBody.model_validate(
    {"sections": [{"title": "Passage 1", "passagePages": [1], "questionPages": [2], "questions": [
        {"number": 1, "questionType": "tfng", "prompt": "Zorbs fly."},
        {"number": 2, "questionType": "multiple_choice", "prompt": "Where?", "options": ["a", "b", "c"]},
    ]}]},
    strict=False,
)  # fmt: skip


class FakeParser:
    def __init__(self, result: Any = PARSED) -> None:
        self.result = result
        self.seen: list[list[tuple[int, str]]] = []

    def __call__(self, rows: list[tuple[int, str]]) -> StructureBody:
        self.seen.append(rows)
        if isinstance(self.result, Exception):
            raise self.result
        assert isinstance(self.result, StructureBody)
        return self.result


@pytest.fixture(scope="module")
def cookie(h: Harness) -> str:
    h.create_user("pp-a@test.local", "teacher")
    return h.login("pp-a@test.local")


def create(h: Harness, cookie: str) -> str:
    body = {
        "title": "Secret Title",
        "sourceType": "self_authored",
        "testType": "course",
        "durationMinutes": 60,
    }
    return str(h.call(cookie, "POST", "/api/tests", body).json["id"])


def upload(h: Harness, cookie: str, tid: str, *, process: bool, text: str | None = None) -> None:
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/pdf{'?process=true' if process else ''}",
        content=make_pdf(2, text),
        headers={"content-type": "application/pdf", "cookie": cookie},
    )
    assert res.status_code == 200


def run(h: Harness, provider: Any, parser: Any) -> bool:
    clock = Clock(datetime.fromisoformat("2026-11-24T05:00:00+00:00"))
    h.owner_rows("update ocr_jobs set lease_until = null")
    return run_once(h.db.app_pool, h.store, provider, clock, sleep=clock.sleep, parser=parser)


def processing(h: Harness, cookie: str, tid: str) -> bool:
    return next(t for t in h.call(cookie, "GET", "/api/tests").json if t["id"] == tid)["processing"]


def test_upload_without_consent_queues_nothing(h: Harness, cookie: str) -> None:
    tid = create(h, cookie)
    upload(h, cookie, tid, process=False)
    jobs = "select 1 from ocr_jobs j join test_assets a on a.id = j.asset_id where a.test_id = %s"
    assert h.owner_rows(jobs, (tid,)) == []
    assert processing(h, cookie, tid) is False


def test_scanned_pdf_is_ocrd_then_parsed_into_questions(h: Harness, cookie: str) -> None:
    h.owner_rows("delete from ocr_jobs")
    tid = create(h, cookie)
    upload(h, cookie, tid, process=True, text=None)  # no text layer: needs OCR
    assert processing(h, cookie, tid) is True
    parser = FakeParser()
    assert run(h, FakeProvider(result=[page(1, "p1"), page(2, "p2")]), parser) is True
    assert parser.seen == [[(1, "p1"), (2, "p2")]]
    detail = h.call(cookie, "GET", f"/api/tests/{tid}").json
    assert [(q["number"], q["questionType"], q["options"]) for q in detail["sections"][0]["questions"]] == [
        (1, "tfng", []),
        (2, "multiple_choice", ["a", "b", "c"]),
    ]
    assert detail["sections"][0]["passagePages"] == [1] and detail["ocr"]["state"] == "done"
    assert processing(h, cookie, tid) is False
    key = h.call(cookie, "GET", f"/api/tests/{tid}/key").json
    assert key["items"] == []  # answers only ever come from the teacher


def test_text_layer_pdf_skips_ocr(h: Harness, cookie: str) -> None:
    h.owner_rows("delete from ocr_jobs")
    tid = create(h, cookie)
    upload(h, cookie, tid, process=True, text="Zorbs live in caves")
    provider, parser = FakeProvider(), FakeParser()
    run(h, provider, parser)
    assert provider.submitted == []
    assert "Zorbs live in caves" in parser.seen[0][0][1]
    assert len(h.call(cookie, "GET", f"/api/tests/{tid}").json["sections"][0]["questions"]) == 2


def test_failed_parse_keeps_text_and_retry_skips_ocr(h: Harness, cookie: str) -> None:
    h.owner_rows("delete from ocr_jobs")
    tid = create(h, cookie)
    upload(h, cookie, tid, process=True, text=None)
    provider = FakeProvider(result=[page(1, "p1"), page(2, "p2")])
    run(h, provider, FakeParser(OcrError("parse_failed")))
    job = h.owner_rows("select state, error_code from ocr_jobs")[0]
    assert (job["state"], job["error_code"]) == ("queued", "parse_failed")
    parser = FakeParser()
    run(h, provider, parser)
    assert len(provider.submitted) == 1 and parser.seen == [[(1, "p1"), (2, "p2")]]
    assert h.call(cookie, "GET", f"/api/tests/{tid}").json["ocr"]["state"] == "done"


def test_parse_never_overwrites_teacher_questions(h: Harness, cookie: str) -> None:
    h.owner_rows("delete from ocr_jobs")
    tid = create(h, cookie)
    upload(h, cookie, tid, process=True, text="x")
    mine = {"sections": [{"title": "Mine", "questions": [{"number": 1, "questionType": "short_answer"}]}]}
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/structure", mine).status == 200
    run(h, FakeProvider(), FakeParser())
    assert h.call(cookie, "GET", f"/api/tests/{tid}").json["sections"][0]["title"] == "Mine"


def test_structure_saves_and_clears_answers(h: Harness, cookie: str) -> None:
    tid = create(h, cookie)
    q = {"number": 1, "questionType": "short_answer", "prompt": "Name it", "answers": ["moss", " moss "]}
    body = {"sections": [{"title": "S", "questions": [q]}]}
    assert h.call(cookie, "PUT", f"/api/tests/{tid}/structure", body).status == 200
    item = h.call(cookie, "GET", f"/api/tests/{tid}/key").json["items"][0]
    assert (item["answers"], item["reviewed"]) == (["moss"], True)  # typed by the teacher: no second review
    body["sections"][0]["questions"][0]["answers"] = []
    h.call(cookie, "PUT", f"/api/tests/{tid}/structure", body)
    assert h.call(cookie, "GET", f"/api/tests/{tid}/key").json["items"] == []


def gemini_reply(payload: Any, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "k" and "k" not in str(request.url.query)
        sent = json.loads(request.content)
        assert "Secret Title" not in json.dumps(sent)
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_gemini_reply_is_validated_and_answers_dropped() -> None:
    reply = {"sections": [{"title": "P1" * 200, "passagePages": [1], "questionPages": [1], "questions": [
        {"number": 1, "questionType": "multiple_choice", "prompt": "Q", "options": ["x", "", "y"],
         "answers": ["A"]}]}]}  # fmt: skip
    body = GeminiParser("k", gemini_reply(reply))([(1, "text")])
    q = body.sections[0].questions[0]
    assert (q.options, q.answers, len(body.sections[0].title)) == (["x", "y"], None, 120)


@pytest.mark.parametrize(("payload", "status", "code"), [
    ({"sections": [{"title": "P", "passagePages": [], "questionPages": [], "questions": [
        {"number": 1, "questionType": "essay", "prompt": "", "options": []}]}]}, 200, "parse_failed"),
    ({"sections": []}, 200, "parse_failed"),
    ({}, 429, "quota_exceeded"),
    ({}, 403, "auth_failed"),
])  # fmt: skip
def test_gemini_bad_replies_become_fixed_codes(payload: Any, status: int, code: str) -> None:
    with pytest.raises(OcrError) as err:
        GeminiParser("k", gemini_reply(payload, status))([(1, "text")])
    assert err.value.code == code
