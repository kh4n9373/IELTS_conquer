import asyncio
import io
import json
import struct
import tempfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest
from aes_pdf import make_aes_pdf
from fakes import make_pdf
from PIL import Image

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


@pytest.mark.parametrize(
    ("real", "declared", "code"),
    [
        (1, 5, "corrupt_pdf"),
        (1, 100, "corrupt_pdf"),
        (150, 1, "too_many_pages"),
        (150, 100, "too_many_pages"),
        (3, 2, "corrupt_pdf"),
    ],
)
def test_forged_page_count_is_rejected(tmp_path: Path, real: int, declared: int, code: str) -> None:
    path = tmp_path / "forged.pdf"
    path.write_bytes(make_pdf(real, declared=declared))
    with pytest.raises(PdfRejected) as err:
        inspect_pdf(path)
    assert err.value.code == code


def test_text_on_a_late_page_is_found_and_every_page_must_load(tmp_path: Path) -> None:
    path = tmp_path / "ok.pdf"
    path.write_bytes(make_pdf(4, declared=4))
    assert inspect_pdf(path).page_count == 4


def test_cyclic_page_tree_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cycle.pdf"
    data = make_pdf(1).replace(b"/Kids [4 0 R]", b"/Kids [2 0 R 4 0 R]")
    path.write_bytes(data)
    with pytest.raises(PdfRejected):
        inspect_pdf(path)


def test_aes128_empty_password_pdf_with_object_streams_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "aes128.pdf"
    path.write_bytes(make_aes_pdf())
    assert inspect_pdf(path).page_count == 1


def test_aes256_empty_password_pdf_is_accepted(tmp_path: Path) -> None:
    import io

    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("", "owner-secret", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "aes256.pdf"
    path.write_bytes(buffer.getvalue())
    assert inspect_pdf(path).page_count == 1


def test_missing_crypto_dependency_is_a_server_error_not_a_corrupt_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pypdf
    from pypdf.errors import DependencyError

    def broken(*_: Any, **__: Any) -> Any:
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    monkeypatch.setattr(pypdf, "PdfReader", broken)
    path = tmp_path / "x.pdf"
    path.write_bytes(make_pdf(1))
    with pytest.raises(DependencyError):
        inspect_pdf(path)


# ---- image key sources ----


def image_bytes(kind: str = "PNG", size: tuple[int, int] = (40, 20), **options: Any) -> bytes:
    image = Image.new("RGB", size, (255, 0, 0))
    image.paste((0, 0, 255), (size[0] // 2, 0, size[0], size[1]))
    out = io.BytesIO()
    image.save(out, kind, **options)
    return out.getvalue()


def forged_png(width: int, height: int) -> bytes:
    """A tiny file whose header claims a huge image."""

    def chunk(name: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"x")) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


def stored_pdf(h: Harness, before: set[str]) -> bytes:
    added = set(h.store.objects) - before
    assert len(added) == 1
    data: bytes = h.store.objects[added.pop()]
    return data


def render(data: bytes) -> Image.Image:
    document = pdfium.PdfDocument(data)
    try:
        assert len(document) == 1
        page = document[0]
        try:
            image: Image.Image = page.render(scale=1).to_pil().convert("RGB")
            return image
        finally:
            page.close()
    finally:
        document.close()


def is_close(pixel: Any, target: tuple[int, int, int]) -> bool:
    return all(abs(a - b) < 40 for a, b in zip(pixel, target, strict=True))


@pytest.mark.parametrize(("kind", "media"), [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_image_key_becomes_a_scannable_one_page_pdf(
    h: Harness, t: dict[str, str], kind: str, media: str
) -> None:
    tid = make(h, t["a"])
    before = set(h.store.objects)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", image_bytes(kind), media)
    assert res.status_code == 200
    body = res.json()
    assert (body["kind"], body["pageCount"], body["hasTextLayer"]) == ("key_source", 1, False)
    assert stored_pdf(h, before).startswith(b"%PDF-")
    assert h.call(t["a"], "POST", f"/api/tests/{tid}/key/scan").status == 202


def test_jpeg_exif_orientation_is_applied_and_metadata_dropped(h: Harness, t: dict[str, str]) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise: the left half ends up on top
    exif[0x010E] = "private-exif-marker"
    tid = make(h, t["a"])
    before = set(h.store.objects)
    data = image_bytes("JPEG", exif=exif, quality=95)
    assert put_raw(h, t["a"], f"/api/tests/{tid}/key-source", data, "image/jpeg").status_code == 200
    pdf = stored_pdf(h, before)
    assert b"private-exif-marker" not in pdf
    assert b"Exif" not in pdf
    page = render(pdf)
    assert page.size == (20, 40)
    assert is_close(page.getpixel((10, 5)), (255, 0, 0))
    assert is_close(page.getpixel((10, 35)), (0, 0, 255))


def test_transparent_png_lands_on_white(h: Harness, t: dict[str, str]) -> None:
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    out = io.BytesIO()
    image.save(out, "PNG")
    tid = make(h, t["a"])
    before = set(h.store.objects)
    assert put_raw(h, t["a"], f"/api/tests/{tid}/key-source", out.getvalue(), "image/png").status_code == 200
    assert is_close(render(stored_pdf(h, before)).getpixel((10, 10)), (255, 255, 255))


def test_16_bit_png_keeps_its_tones(h: Harness, t: dict[str, str]) -> None:
    image = Image.new("I;16", (20, 20), 0x4000)
    out = io.BytesIO()
    image.save(out, "PNG")
    tid = make(h, t["a"])
    before = set(h.store.objects)
    assert put_raw(h, t["a"], f"/api/tests/{tid}/key-source", out.getvalue(), "image/png").status_code == 200
    assert is_close(render(stored_pdf(h, before)).getpixel((10, 10)), (64, 64, 64))


def test_multi_picture_jpeg_is_accepted(h: Harness, t: dict[str, str]) -> None:
    first, second = Image.new("RGB", (20, 20), "white"), Image.new("RGB", (20, 20), "black")
    out = io.BytesIO()
    first.save(out, "MPO", save_all=True, append_images=[second])
    tid = make(h, t["a"])
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", out.getvalue(), "image/jpeg")
    assert (res.status_code, res.json()["pageCount"]) == (200, 1)


@pytest.mark.parametrize(
    ("data", "media"),
    [
        (image_bytes("PNG"), "image/jpeg"),
        (image_bytes("JPEG"), "image/png"),
        (make_pdf(1), "image/png"),
        (b"not an image", "image/png"),
        (image_bytes("PNG")[:40], "image/png"),
        (image_bytes("JPEG")[:200], "image/jpeg"),
        (forged_png(7000, 7000), "image/png"),
        (forged_png(30000, 30000), "image/png"),
    ],
    ids=["png-as-jpeg", "jpeg-as-png", "pdf-as-png", "garbage", "cut-png", "cut-jpeg", "over-40mp", "bomb"],
)
def test_bad_images_return_corrupt_image(h: Harness, t: dict[str, str], data: bytes, media: str) -> None:
    tid = make(h, t["a"])
    before = set(h.store.objects)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", data, media)
    assert (res.status_code, res.json()) == (422, {"error": "corrupt_image"})
    assert set(h.store.objects) == before


def test_real_image_over_40_megapixels_is_rejected(h: Harness, t: dict[str, str]) -> None:
    out = io.BytesIO()
    Image.new("L", (6325, 6325)).save(out, "PNG", compress_level=1)
    tid = make(h, t["a"])
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", out.getvalue(), "image/png")
    assert (res.status_code, res.json()) == (422, {"error": "corrupt_image"})


def test_unsupported_image_type_stays_not_pdf(h: Harness, t: dict[str, str]) -> None:
    tid = make(h, t["a"])
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", image_bytes("GIF"), "image/gif")
    assert (res.status_code, res.json()) == (415, {"error": "not_pdf"})


@pytest.mark.parametrize(("kind", "media"), [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_student_pdf_route_rejects_images(h: Harness, t: dict[str, str], kind: str, media: str) -> None:
    tid = make(h, t["a"])
    res = put_raw(h, t["a"], f"/api/tests/{tid}/pdf", image_bytes(kind), media)
    assert (res.status_code, res.json()) == (415, {"error": "not_pdf"})


@pytest.mark.parametrize("data", [image_bytes("PNG"), b"not an image"], ids=["valid", "corrupt"])
def test_image_upload_removes_temp_files(
    h: Harness, t: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    tid = make(h, t["a"])
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    put_raw(h, t["a"], f"/api/tests/{tid}/key-source", data, "image/png")
    assert list(tmp_path.iterdir()) == []


def test_converted_pdf_over_the_limit_returns_413(
    h: Harness, t: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = image_bytes("PNG")
    tid = make(h, t["a"])
    before = set(h.store.objects)
    monkeypatch.setattr(test_files, "MAX_BYTES", len(data) + 1)
    res = put_raw(h, t["a"], f"/api/tests/{tid}/key-source", data, "image/png")
    assert (res.status_code, res.json()) == (413, {"error": "file_too_large"})
    assert set(h.store.objects) == before
