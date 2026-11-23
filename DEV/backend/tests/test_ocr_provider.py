import gzip
import json
import logging
import re
import tracemalloc
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from npa_api.ocr.paddle import API_URL, MAX_RESULT_BYTES, MODEL, PaddleOcrProvider
from npa_api.ocr.provider import OcrError, OcrJobExpired, OcrStatus
from npa_api.ocr.worker import quiet_http_logs

TOKEN = "unit-test-token-value"  # noqa: S105
RESULT_URL = "https://results.example.test/r.jsonl?signature=sig-value"
FIXTURE = Path(__file__).parent / "fixtures" / "paddle_result.jsonl"
Handler = Callable[[httpx.Request], httpx.Response]


def served(body: bytes, headers: dict[str, str] | None = None) -> httpx.Response:
    """A streamed response, as a real download is: bytes content would arrive already read."""
    return httpx.Response(200, headers=headers, content=iter([body]))


def provider(handler: Handler) -> PaddleOcrProvider:
    return PaddleOcrProvider(TOKEN, httpx.Client(transport=httpx.MockTransport(handler)))


def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "Secret Teacher Title.pdf"
    path.write_bytes(b"%PDF-1.4 invented")
    return path


def done(url: str = RESULT_URL) -> OcrStatus:
    return OcrStatus(job_id="job-1", state="done", result_url=url)


def test_submit_sends_bearer_token_and_opaque_filename(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"jobId": "job-1"}})

    assert provider(handler).submit(pdf(tmp_path)) == "job-1"
    request = seen[0]
    body = request.content.decode("latin-1")
    assert str(request.url) == API_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert re.search(
        r'filename="[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.pdf"', body
    )
    assert "Secret" not in body
    assert MODEL in body


def test_each_submit_uses_a_new_filename(tmp_path: Path) -> None:
    names: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        names.extend(re.findall(r'filename="([^"]+)"', request.read().decode("latin-1")))
        return httpx.Response(200, json={"code": 0, "data": {"jobId": "job-1"}})

    p = provider(handler)
    p.submit(pdf(tmp_path))
    p.submit(pdf(tmp_path))
    assert len(set(names)) == 2


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "auth_failed"), (403, "auth_failed"), (429, "quota_exceeded"), (500, "provider_error")],
)
def test_provider_errors_never_contain_token(tmp_path: Path, status: int, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"msg": f"bad {TOKEN}", "url": RESULT_URL})

    with pytest.raises(OcrError) as caught:
        provider(handler).submit(pdf(tmp_path))
    assert caught.value.code == code
    text = f"{caught.value} {caught.value!r}"
    assert TOKEN not in text and "sig-value" not in text
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_network_errors_are_fixed_codes(tmp_path: Path) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out {TOKEN}", request=request)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {TOKEN}", request=request)

    for handler, code in ((timeout, "provider_timeout"), (broken, "provider_error")):
        with pytest.raises(OcrError) as caught:
            provider(handler).submit(pdf(tmp_path))
        assert caught.value.code == code
        assert TOKEN not in str(caught.value)
        assert caught.value.__suppress_context__


def test_requests_use_explicit_timeouts(tmp_path: Path) -> None:
    seen: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"code": 0, "data": {"jobId": "job-1"}})

    provider(handler).submit(pdf(tmp_path))
    assert all(
        seen[0][name] is not None and seen[0][name] > 5 for name in ("connect", "read", "write", "pool")
    )


def test_status_maps_states_and_rejects_unknown() -> None:
    answers = iter(["pending", "running", "failed", "weird"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"code": 0, "data": {"jobId": "job-1", "state": next(answers)}})

    p = provider(handler)
    assert [p.status("job-1").state for _ in range(3)] == ["pending", "running", "failed"]
    with pytest.raises(OcrError) as caught:
        p.status("job-1")
    assert caught.value.code == "provider_error"


def test_status_reads_result_url_and_hides_it_from_repr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = {"jobId": "job-1", "state": "done", "resultUrl": {"jsonUrl": RESULT_URL}}
        return httpx.Response(200, json={"code": 0, "data": data})

    status = provider(handler).status("job-1")
    assert status.result_url == RESULT_URL
    assert "sig-value" not in repr(status)


@pytest.mark.parametrize("http_status", [200, 404])
def test_status_11002_is_job_expired(http_status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(http_status, json={"code": 11002, "msg": "expired"})

    with pytest.raises(OcrJobExpired):
        provider(handler).status("job-1")


def test_status_rejects_unsafe_job_id_without_calling() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(OcrError):
        provider(handler).status("../../other")
    assert calls == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://results.example.test/r.jsonl",
        "https://127.0.0.1/r.jsonl",
        "https://[::1]/r.jsonl",
        "https://localhost/r.jsonl",
        "https://LOCALHOST./r.jsonl",
        "https://api.localhost/r.jsonl",
        "https://2130706433/r.jsonl",
        "https://0x7f.0.0.1/r.jsonl",
        "https://user:pw@results.example.test/r.jsonl",
        "https:///r.jsonl",
        "https://127\u30020\u30020\u30021/x",
        "https://127\uff0e0\uff0e0\uff0e1/x",
        "https://127\uff610\uff610\uff611/x",
        "https://169\u3002254\u3002169\u3002254/x",
        "https://localhost\u3002/x",
        "https://api\u3002localhost/x",
        "https://127%2e0%2e0%2e1/x",
        "https://[bad/x",
    ],
)
def test_result_url_must_be_https_and_not_redirect(url: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return served(FIXTURE.read_bytes())

    with pytest.raises(OcrError) as caught:
        provider(handler).pages(done(url))
    assert caught.value.code == "bad_result"
    assert calls == 0


def test_result_redirect_is_not_followed() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example.test/x"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(OcrError) as caught:
        PaddleOcrProvider(TOKEN, client).pages(done())
    assert caught.value.code == "provider_error"
    assert urls == [RESULT_URL]


def test_result_download_sends_no_authorization() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return served(FIXTURE.read_bytes())

    provider(handler).pages(done())
    assert "authorization" not in seen[0].headers


def test_client_with_default_authorization_is_refused() -> None:
    client = httpx.Client(headers={"Authorization": "Bearer x"})
    with pytest.raises(ValueError, match="Authorization"):
        PaddleOcrProvider(TOKEN, client)


def test_result_download_stops_at_size_limit() -> None:
    produced = 0
    chunk = b"x" * (1024 * 1024)

    def body() -> Iterator[bytes]:
        nonlocal produced
        while produced < 200:
            produced += 1
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    with pytest.raises(OcrError) as caught:
        provider(handler).pages(done())
    assert caught.value.code == "bad_result"
    assert produced <= MAX_RESULT_BYTES // len(chunk) + 2


def test_declared_oversize_is_refused_before_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(MAX_RESULT_BYTES + 1)}, content=b"{}")

    with pytest.raises(OcrError) as caught:
        provider(handler).pages(done())
    assert caught.value.code == "bad_result"


def test_parses_the_sanitized_live_fixture() -> None:
    pages = provider(lambda _: served(FIXTURE.read_bytes())).pages(done())
    assert [p.page for p in pages] == [1]
    assert pages[0].text == "Zorvian lanterns drift above the"
    assert pages[0].lines[0].bbox == (15 / 400, 178 / 400, 397 / 400, 210 / 400)
    assert pages[0].lines[0].confidence is None


def page_with(bbox: object, **size: object) -> bytes:
    pruned = {"parsing_res_list": [{"block_content": "1. A", "block_bbox": bbox}], **size}
    row = {"result": {"layoutParsingResults": [{"prunedResult": pruned}]}, "errorCode": 0}
    return json.dumps(row).encode()


def test_box_becomes_fractions_of_the_page() -> None:
    body = page_with([100, 50, 300, 150], width=400, height=200)
    box = provider(lambda _: served(body)).pages(done())[0].lines[0].bbox
    assert box == (0.25, 0.25, 0.75, 0.75)


@pytest.mark.parametrize(
    ("bbox", "size"),
    [
        ([1, 2, 3, 4], {}),
        ([1, 2, 3, 4], {"width": 400}),
        ([1, 2, 3, 4], {"width": 0, "height": 400}),
        ([1, 2, 3, 4], {"width": -400, "height": 400}),
        ([1, 2, 3, 4], {"width": "400", "height": 400}),
        ([1, 2, 3, 4], {"width": True, "height": 400}),
        ([1, 2, 3, 4], {"width": float("inf"), "height": 400}),
        ([1, 2, 3, 4], {"width": float("nan"), "height": 400}),
        ([1, 2, 3, 4], {"width": 10**400, "height": 400}),
        ([10**400, 2, 3, 4], {"width": 400, "height": 400}),
        ([1, 2, 500, 4], {"width": 400, "height": 400}),
        ([-1, 2, 3, 4], {"width": 400, "height": 400}),
        ([3, 2, 3, 4], {"width": 400, "height": 400}),
        ([1, 4, 3, 4], {"width": 400, "height": 400}),
        ([3, 2, 1, 4], {"width": 400, "height": 400}),
    ],
)
def test_unusable_box_or_page_size_stores_no_box(bbox: object, size: dict[str, object]) -> None:
    body = page_with(bbox, **size)
    assert provider(lambda _: served(body)).pages(done())[0].lines[0].bbox is None


def test_fixture_holds_no_identifiers_or_urls() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    assert not re.search(r"https?://|jobId|logId|traceId|bearer", raw, re.I)


def test_pages_number_across_jsonl_lines() -> None:
    def row(*texts: str) -> str:
        results = [
            {"prunedResult": {"parsing_res_list": [{"block_content": t, "block_bbox": [0, 0, 1, 1]}]}}
            for t in texts
        ]
        return json.dumps({"result": {"layoutParsingResults": results}, "errorCode": 0})

    body = f"{row('one', 'two')}\n\n{row('three')}\n".encode()
    pages = provider(lambda _: served(body)).pages(done())
    assert [(p.page, p.text) for p in pages] == [(1, "one"), (2, "two"), (3, "three")]


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b"",
        b'{"result": {}, "errorCode": 0}',
        b'{"result": {"layoutParsingResults": []}, "errorCode": 7}',
        b'{"result": {"layoutParsingResults": [{"prunedResult": '
        b'{"parsing_res_list": [{"block_content": 5}]}}]}}',
    ],
)
def test_malformed_results_are_bad_result(body: bytes) -> None:
    with pytest.raises(OcrError) as caught:
        provider(lambda _: served(body)).pages(done())
    assert caught.value.code == "bad_result"


def test_worker_silences_httpx_url_logging() -> None:
    quiet_http_logs()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_unicode_line_separators_inside_text_do_not_split_records() -> None:
    block = {"block_content": "before after", "block_bbox": [0, 0, 1, 1]}
    row = {
        "result": {"layoutParsingResults": [{"prunedResult": {"parsing_res_list": [block]}}]},
        "errorCode": 0,
    }
    body = json.dumps(row, ensure_ascii=False).encode()
    pages = provider(lambda _: served(body)).pages(done())
    assert pages[0].text == "before after"


def test_deeply_nested_result_is_bad_result() -> None:
    body = b"[" * 200_000
    with pytest.raises(OcrError) as caught:
        provider(lambda _: served(body)).pages(done())
    assert caught.value.code == "bad_result"


def test_result_is_requested_at_the_normalized_host() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return served(FIXTURE.read_bytes())

    provider(handler).pages(done("https://results\u3002example\u3002test/r.jsonl?signature=sig-value"))
    assert seen[0].host == "results.example.test"
    assert seen[0].query == b"signature=sig-value"


@pytest.mark.parametrize("encoding", ["gzip", "gzip, gzip", "deflate", "identity, gzip", "br"])
def test_compressed_result_is_refused(encoding: str) -> None:
    body = gzip.compress(FIXTURE.read_bytes())
    with pytest.raises(OcrError) as caught:
        provider(lambda _: served(body, {"content-encoding": encoding})).pages(done())
    assert caught.value.code == "bad_result"


def test_identity_encoding_is_accepted() -> None:
    body = FIXTURE.read_bytes()
    pages = provider(lambda _: served(body, {"content-encoding": "identity"})).pages(done())
    assert [p.page for p in pages] == [1]


def test_nested_gzip_does_not_inflate_in_memory() -> None:
    bomb = gzip.compress(gzip.compress(b"x" * (MAX_RESULT_BYTES + 10 * 1024 * 1024)))
    assert len(bomb) < 10_000
    tracemalloc.start()
    try:
        with pytest.raises(OcrError) as caught:
            provider(lambda _: served(bomb, {"content-encoding": "gzip, gzip"})).pages(done())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.code == "bad_result"
    assert peak < 5 * 1024 * 1024
