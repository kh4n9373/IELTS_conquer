"""PDF upload and signed download. The body is untrusted: authorize and check the test first."""

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..core.db import Role, with_identity, write_audit
from ..core.http import CtxDep, GuardedRoute, HttpError, IdentityDep, access, not_found
from ..core.storage import FileStore
from ..exams.pdf import PdfInfo, PdfRejected, inspect_pdf
from .tests import lock_test

log = logging.getLogger("npa.files")
router = APIRouter(route_class=GuardedRoute)
TEACHER: tuple[Role, ...] = ("teacher",)

MAX_BYTES = 30 * 1024 * 1024
URL_SECONDS = 300
Kind = Literal["student_pdf", "key_source"]


def unavailable() -> HttpError:
    return HttpError(503, "storage_unavailable")


async def receive_body(request: Request, path: Path) -> int:
    """Streams the body to `path`, stopping at the limit even if Content-Length is missing or false."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HttpError(413, "file_too_large")
    size = 0
    with path.open("wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BYTES:
                raise HttpError(413, "file_too_large")
            await run_in_threadpool(out.write, chunk)
    return size


def check_draft(ctx: CtxDep, identity: IdentityDep, test_id: UUID) -> None:
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, test_id, draft=True)


def commit_asset(
    ctx: CtxDep, identity: IdentityDep, test_id: UUID, kind: Kind, key: str, size: int, info: PdfInfo
) -> str | None:
    """Swaps the asset row of this kind in one transaction. Returns the old storage key."""
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, test_id, draft=True)
        old = tx.execute(
            "delete from test_assets where test_id = %s and kind = %s returning storage_key", (test_id, kind)
        ).fetchone()
        asset = tx.execute(
            """insert into test_assets
                 (test_id, teacher_id, kind, storage_key, sha256, byte_size, page_count, has_text_layer)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (test_id, test["teacher_id"], kind, key, info.sha256, size, info.page_count, info.has_text_layer),
        ).fetchone()
        assert asset is not None  # noqa: S101
        if kind == "student_pdf":
            tx.execute(
                "insert into ocr_jobs (asset_id, teacher_id) values (%s, %s)",
                (asset["id"], test["teacher_id"]),
            )
        write_audit(
            tx,
            action="test.asset_upload",
            entity="test",
            entity_id=test_id,
            after={"kind": kind, "size": size, "pageCount": info.page_count, "sha256": info.sha256},
        )
    return None if old is None else str(old["storage_key"])


def forget(store: FileStore, key: str, test_id: UUID, kind: str) -> None:
    try:
        store.delete(key)
    except Exception as err:
        # Only identifiers the API already exposes are logged; the key and endpoint are not.
        log.warning("Object cleanup failed for test %s (%s): %s", test_id, kind, type(err).__name__)


async def upload(request: Request, ctx: CtxDep, identity: IdentityDep, test_id: UUID, kind: Kind) -> Any:
    await run_in_threadpool(check_draft, ctx, identity, test_id)
    store = ctx.store
    if store is None:
        raise unavailable()
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/pdf":
        raise HttpError(415, "not_pdf")

    handle, name = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    path = Path(name)
    key = f"tests/{test_id}/{uuid.uuid4()}.pdf"
    stored = False
    try:
        size = await receive_body(request, path)
        try:
            info = await run_in_threadpool(inspect_pdf, path)
        except PdfRejected as err:
            raise HttpError(422, err.code) from err
        await run_in_threadpool(store.put, key, path)
        stored = True
        old_key = await run_in_threadpool(commit_asset, ctx, identity, test_id, kind, key, size, info)
        stored = False
    finally:
        path.unlink(missing_ok=True)
        if stored:
            await run_in_threadpool(forget, store, key, test_id, kind)
    if old_key is not None:
        await run_in_threadpool(forget, store, old_key, test_id, kind)
    return {"kind": kind, "pageCount": info.page_count, "hasTextLayer": info.has_text_layer, "byteSize": size}


@router.put("/api/tests/{id}/pdf")
@access(TEACHER)
async def put_pdf(id: UUID, request: Request, ctx: CtxDep, identity: IdentityDep) -> Any:
    return await upload(request, ctx, identity, id, "student_pdf")


@router.put("/api/tests/{id}/key-source")
@access(TEACHER)
async def put_key_source(id: UUID, request: Request, ctx: CtxDep, identity: IdentityDep) -> Any:
    return await upload(request, ctx, identity, id, "key_source")


@router.get("/api/tests/{id}/pdf-url")
@access(TEACHER)
def pdf_url(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        if tx.execute("select 1 from tests where id = %s", (id,)).fetchone() is None:
            raise not_found()
        if ctx.store is None:
            raise unavailable()
        asset = tx.execute(
            "select storage_key from test_assets where test_id = %s and kind = 'student_pdf'", (id,)
        ).fetchone()
    if asset is None:
        raise not_found()
    url = ctx.store.signed_url(asset["storage_key"], URL_SECONDS)
    return JSONResponse({"url": url, "expiresIn": URL_SECONDS}, headers={"Cache-Control": "no-store"})


@router.post("/api/tests/{id}/ocr")
@access(TEACHER)
def retry_ocr(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, id)
        if test["status"] == "archived":
            raise HttpError(409, "test_locked")
        asset = tx.execute(
            "select id from test_assets where test_id = %s and kind = 'student_pdf'", (id,)
        ).fetchone()
        if asset is None:
            raise not_found()
        job = tx.execute(
            "select id, state from ocr_jobs where asset_id = %s order by created_at desc limit 1 for update",
            (asset["id"],),
        ).fetchone()
        if job is None or job["state"] != "failed":
            raise HttpError(409, "ocr_not_retryable")
        tx.execute(
            """update ocr_jobs
               set state = 'queued', attempts = 0, lease_until = null, provider_job_id = null,
                   error_code = null, finished_at = null
               where id = %s""",
            (job["id"],),
        )
        write_audit(tx, action="test.ocr_retry", entity="test", entity_id=id)
    return {"state": "queued", "errorCode": None}
