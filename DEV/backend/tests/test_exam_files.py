import asyncio
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import make_pdf

from conftest import Harness
from npa_api.exams.pdf import PdfRejected, inspect_pdf
from npa_api.exams.readiness import readiness
from npa_api.routes import test_files

MB = 1024 * 1024
LIMIT = 30 * MB


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, str]:
    h.create_user("fl-a@test.local", "teacher")
    h.create_user("fl-b@test.local", "teacher")
    h.create_user("fl-s@test.local", "student", h.create_user("fl-c@test.local", "teacher"))
    return {"a": h.login("fl-a@test.local"), "b": h.login("fl-b@test.local"), "s": h.login("fl-s@test.local")}


def make(h: Harness, cookie: str) -> str:
    body = {"title": "T", "sourceType": "self_authored", "testType": "course", "durationMinutes": 60}
    return str(h.call(cookie, "POST", "/api/tests", body).json["id"])


def put_raw(
    h: Harness, cookie: str | None, url: str, data: Any, content_type: str | None = "application/pdf"
) -> Any:
    h.client.cookies.clear()
    headers = {"content-type": content_type} if content_type else {}
    if cookie:
        headers["cookie"] = cookie
    return h.client.put(url, content=data, headers=headers)


def asgi_put(h: Harness, cookie: str | None, url: str, headers: dict[str, str]) -> tuple[int, int]:
    """Calls the app directly and counts how often it reads the request body."""
    reads = 0
    status = 0

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    if cookie:
        raw.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "path": url,
        "raw_path": url.encode(),
        "query_string": b"",
        "headers": raw,
        "client": ("1.2.3.4", 1),
        "server": ("test", 80),
        "scheme": "http",
    }
    asyncio.run(h.app(scope, receive, send))
    return status, reads


def chunks(total: int) -> Iterator[bytes]:
    for _ in range(total // MB):
        yield b"0" * MB


def assets_of(h: Harness, tid: str) -> list[dict[str, Any]]:
    return h.owner_rows("select * from test_assets where test_id = %s", (tid,))


def test_anonymous_large_upload_gets_401_without_reading_body(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    headers = {"content-type": "application/pdf", "content-length": str(LIMIT * 2)}
    assert asgi_put(h, None, f"/api/tests/{tid}/pdf", headers) == (401, 0)
    assert asgi_put(h, t["s"], f"/api/tests/{tid}/pdf", headers) == (403, 0)
    assert asgi_put(h, t["b"], f"/api/tests/{tid}/pdf", headers) == (404, 0)


def test_non_draft_test_is_rejected_before_the_body_is_read(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    h.owner_rows("update tests set status = 'archived' where id = %s", (tid,))
    headers = {"content-type": "application/pdf"}
    assert asgi_put(h, t["a"], f"/api/tests/{tid}/pdf", headers) == (409, 0)


def test_upload_over_30mb_returns_413_and_stores_nothing(
    h: Harness, t: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    tid = make(h, t["a"])
    before = dict(h.store.objects)
    # No Content-Length (chunked), so only the streaming limit can stop it.
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", chunks(LIMIT + MB))
    assert (res.status_code, res.json()) == (413, {"error": "file_too_large"})
    assert h.store.objects == before
    assert assets_of(h, tid) == []
    assert list(tmp_path.iterdir()) == []


def test_declared_oversize_is_rejected_without_reading(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    headers = {"content-type": "application/pdf", "content-length": str(LIMIT + 1)}
    assert asgi_put(h, t["a"], f"/api/tests/{tid}/pdf", headers) == (413, 0)


def test_streamed_body_larger_than_declared_is_still_stopped(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    headers = {"content-type": "application/pdf", "content-length": "10"}
    status, reads = asgi_put(h, t["a"], f"/api/tests/{tid}/pdf", headers)
    assert status == 413
    assert reads <= LIMIT // 1024 + 2


def test_non_pdf_returns_not_pdf(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    wrong_type = asgi_put(h, t["a"], f"/api/tests/{tid}/pdf", {"content-type": "text/plain"})
    assert wrong_type == (415, 0)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", b"MZ not a pdf at all", "text/plain")
    assert (res.status_code, res.json()) == (415, {"error": "not_pdf"})
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", b"MZ not a pdf at all")
    assert (res.status_code, res.json()) == (422, {"error": "not_pdf"})
    assert assets_of(h, tid) == []


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (make_pdf(encrypted=True), "encrypted_pdf"),
        (make_pdf(101), "too_many_pages"),
        (b"%PDF-1.4\n garbage with no structure", "corrupt_pdf"),
    ],
)
def test_rejected_pdfs_report_their_code(h: Harness, t: dict[str, str], data: bytes, code: str) -> None:
    tid = make(h, t["a"])
    before = dict(h.store.objects)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", data)
    assert (res.status_code, res.json()) == (422, {"error": code})
    assert h.store.objects == before
    assert assets_of(h, tid) == []


def test_image_pdf_has_no_text_layer_and_text_pdf_does(tmp_path: Path) -> None:
    image, text = tmp_path / "image.pdf", tmp_path / "text.pdf"
    image.write_bytes(make_pdf(2, None))
    text.write_bytes(make_pdf(3))
    assert (inspect_pdf(image).page_count, inspect_pdf(image).has_text_layer) == (2, False)
    info = inspect_pdf(text)
    assert (info.page_count, info.has_text_layer) == (3, True)
    assert len(info.sha256) == 64


def test_inspect_rejects_with_codes(tmp_path: Path) -> None:
    path = tmp_path / "x.pdf"
    path.write_bytes(b"hello")
    with pytest.raises(PdfRejected) as err:
        inspect_pdf(path)
    assert err.value.code == "not_pdf"


def test_upload_stores_private_object_row_and_audit(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    data = make_pdf(3)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", data)
    assert res.status_code == 200
    assert res.json() == {"kind": "student_pdf", "pageCount": 3, "hasTextLayer": True, "byteSize": len(data)}
    (row,) = assets_of(h, tid)
    assert row["storage_key"].startswith(f"tests/{tid}/") and row["storage_key"].endswith(".pdf")
    assert h.store.objects[row["storage_key"]] == data
    assert (row["page_count"], row["has_text_layer"], row["byte_size"]) == (3, True, len(data))
    assert row["storage_key"] not in res.text
    detail = h.call(t["a"], "GET", f"/api/tests/{tid}").json
    assert detail["studentPdf"] == {"pageCount": 3, "hasTextLayer": True}
    audit = h.owner_rows(
        "select after from audit_log where action = 'test.asset_upload' and entity_id = %s", (tid,)
    )
    assert audit[0]["after"].keys() == {"kind", "size", "pageCount", "sha256"}
    assert row["storage_key"] not in json.dumps(audit)


def test_key_source_is_a_separate_asset_kind(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(1)).status_code == 200
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", make_pdf(2, None))
    assert res.status_code == 200 and res.json()["kind"] == "key_source"
    assert sorted(a["kind"] for a in assets_of(h, tid)) == ["key_source", "student_pdf"]


def test_replacing_pdf_reports_page_out_of_range_for_old_mapping(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(5)).status_code == 200
    structure = {
        "sections": [
            {"title": "S", "passagePages": [4], "ranges": [{"from": 1, "to": 1, "questionType": "tfng"}]}
        ]
    }
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/structure", structure).status == 200
    assert "page_out_of_range" not in h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]
    (old,) = assets_of(h, tid)
    h.owner_rows(
        "insert into page_texts (asset_id, teacher_id, page, text) values (%s, %s, 1, 'ocr')",
        (old["id"], old["teacher_id"]),
    )
    assert put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(2)).status_code == 200
    (new,) = assets_of(h, tid)
    assert new["id"] != old["id"] and new["storage_key"] != old["storage_key"]
    assert old["storage_key"] not in h.store.objects and new["storage_key"] in h.store.objects
    assert h.owner_rows("select 1 from page_texts where asset_id = %s", (old["id"],)) == []
    assert "page_out_of_range" in h.call(t["a"], "GET", f"/api/tests/{tid}/readiness").json["blockers"]


def test_failed_commit_removes_the_new_object(
    h: Harness, t: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = make(h, t["a"])
    before = dict(h.store.objects)

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(test_files, "commit_asset", boom)
    with pytest.raises(RuntimeError, match="db down"):
        put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(1))
    assert h.store.objects == before


def test_old_object_delete_failure_does_not_fail_the_upload(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(1)).status_code == 200
    h.store.fail_delete = True
    try:
        assert put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(2)).status_code == 200
    finally:
        h.store.fail_delete = False
    assert [a["page_count"] for a in assets_of(h, tid)] == [2]


def test_without_a_store_upload_and_url_return_503_after_authorization(
    h: Harness, t: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = make(h, t["a"])
    monkeypatch.setattr(h.app.state.ctx, "store", None)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(1))
    assert (res.status_code, res.json()) == (503, {"error": "storage_unavailable"})
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/pdf-url").status == 503
    assert put_raw(h, None, f"/api/tests/{tid}/pdf", b"x").status_code == 401
    assert put_raw(h, t["b"], f"/api/tests/{tid}/pdf", b"x").status_code == 404
    assert h.call(t["b"], "GET", f"/api/tests/{tid}/pdf-url").status == 404


def test_pdf_url_is_teacher_only_short_lived_and_hides_keys(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    assert h.call(t["a"], "GET", f"/api/tests/{tid}/pdf-url").status == 404
    assert put_raw(h, t["a"], f"/api/tests/{tid}/pdf", make_pdf(1)).status_code == 200
    res = h.call(t["a"], "GET", f"/api/tests/{tid}/pdf-url")
    assert res.status == 200
    assert res.json.keys() == {"url", "expiresIn"} and res.json["expiresIn"] == 300
    assert res.json["url"].startswith("https://files.test/signed/")
    assert res.headers["cache-control"] == "no-store"
    assert h.call(t["s"], "GET", f"/api/tests/{tid}/pdf-url").status == 403
    assert h.call(t["b"], "GET", f"/api/tests/{tid}/pdf-url").status == 404
    assert h.call(None, "GET", f"/api/tests/{tid}/pdf-url").status == 401


def test_other_routes_keep_64kb_limit(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    big = b"x" * (70 * 1024)
    for method, url in (
        ("PATCH", f"/api/tests/{tid}"),
        ("PUT", f"/api/tests/{tid}/structure"),
        ("POST", f"/api/tests/{tid}/pdf"),
        ("PUT", f"/api/tests/{tid}/pdf/"),
        ("PUT", f"/api/tests/{tid}/pdf/extra"),
        ("PUT", "/api/tests/not-a-uuid/pdf"),
    ):
        h.client.cookies.clear()
        res = h.client.request(method, url, content=big, headers={"cookie": t["a"]})
        assert res.status_code == 413, (method, url)


def test_s3_store_signs_with_the_public_endpoint() -> None:
    from npa_api.core.storage import S3FileStore

    store = S3FileStore(
        endpoint="http://minio-internal:9000",
        public_endpoint="https://files.example.test",
        bucket="b",
        access_key="ak",
        secret_key="sk",
    )
    url = store.signed_url("tests/x/y.pdf", 300)
    assert url.startswith("https://files.example.test/b/tests/x/y.pdf?")
    assert "X-Amz-Expires=300" in url and "minio-internal" not in url
    assert "sk" not in url.replace("X-Amz-Signature", "")


def test_readiness_pure_null_page_count_with_mapped_pages_is_out_of_range() -> None:
    test = {"test_type": "course", "duration_minutes": 60}
    sections = [{"passage_pages": [1], "question_pages": []}]
    questions = [{"number": 1, "question_type": "tfng"}]
    items = [{"question_number": 1, "answers": ["TRUE"], "flagged": False, "reviewed_at": "x"}]
    unknown = [{"kind": "student_pdf", "page_count": None}]
    assert readiness(test, unknown, sections, questions, items) == ["page_out_of_range"]
    unmapped = [{"passage_pages": [], "question_pages": []}]
    assert readiness(test, unknown, unmapped, questions, items) == []
