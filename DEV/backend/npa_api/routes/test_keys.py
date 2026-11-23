import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from ..core.db import Conn, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, access, not_found
from ..exams.key_import import KeyLine, apply_drafts, parse_key_lines, pdf_lines
from ..exams.keys import draft_version, normalize_answers, parse_pasted_key, upsert_items
from ..exams.pdf import PdfRejected
from .tests import TEACHER, TEST_COLUMNS, blockers_of, lock_test, public

router = APIRouter(route_class=GuardedRoute)

MAX_QUESTIONS = 200
Number = Annotated[int, Field(ge=1, le=MAX_QUESTIONS)]
INVALID = HttpError(400, "invalid_input")


class KeyItemBody(Body):
    number: Number
    answers: Annotated[list[str], Field(max_length=100)]


class KeyItemsBody(Body):
    items: Annotated[list[KeyItemBody], Field(min_length=1, max_length=MAX_QUESTIONS)] | None = None
    pasted: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "KeyItemsBody":
        if (self.items is None) == (self.pasted is None):
            raise ValueError("send items or pasted, not both")
        return self


class ReviewBody(Body):
    numbers: Annotated[list[Number], Field(min_length=1, max_length=MAX_QUESTIONS)]


def parse_items(body: KeyItemsBody) -> dict[int, list[str]]:
    try:
        if body.pasted is not None:
            return parse_pasted_key(body.pasted)
        assert body.items is not None  # noqa: S101
        parsed = {i.number: normalize_answers(i.answers) for i in body.items}
        if len(parsed) != len(body.items):
            raise ValueError("repeated number")
        return parsed
    except ValueError as err:
        raise INVALID from err


def question_numbers(tx: Conn, test_id: UUID) -> set[int]:
    rows = tx.execute("select number from questions where test_id = %s", (test_id,)).fetchall()
    return {r["number"] for r in rows}


def load_key(tx: Conn, test_id: UUID) -> dict[str, Any]:
    version = draft_version(tx, test_id, create=False)
    if version is None:
        return {"version": None, "approvedAt": None, "items": []}
    items = tx.execute(
        """select question_number as number, answers, origin, flagged,
                  reviewed_at is not null as reviewed, reviewed_at as "reviewedAt",
                  source_page as "sourcePage", source_bbox as "sourceBbox"
           from keys.answer_key_items where version_id = %s order by question_number""",
        (version["id"],),
    ).fetchall()
    return {"version": version["version"], "approvedAt": version["approvedAt"], "items": items}


def key_scan_status(tx: Conn, test_id: UUID) -> dict[str, Any] | None:
    """State and fixed error code only: never OCR text, provider ids or lease data."""
    return tx.execute(
        """select j.state, j.error_code as "errorCode"
           from ocr_jobs j join test_assets a on a.id = j.asset_id
           where a.test_id = %s and a.kind = 'key_source'
           order by j.created_at desc limit 1""",
        (test_id,),
    ).fetchone()


@router.get("/api/tests/{id}/key")
@access(TEACHER)
def get_key(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        if tx.execute("select 1 from tests where id = %s", (id,)).fetchone() is None:
            raise not_found()
        return {**load_key(tx, id), "scan": key_scan_status(tx, id)}


@router.put("/api/tests/{id}/key/items")
@access(TEACHER)
def put_key_items(id: UUID, body: KeyItemsBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    items = parse_items(body)
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        if not set(items) <= question_numbers(tx, id):
            raise INVALID
        version = draft_version(tx, id, create=True)
        assert version is not None  # noqa: S101
        upsert_items(tx, version["id"], items, "manual")
        # Numbers and counts only: admins can read the audit log but must not see answers.
        write_audit(
            tx,
            action="key.update",
            entity="test",
            entity_id=id,
            after={"questions": sorted(items), "count": len(items)},
        )
        return load_key(tx, id)


@router.post("/api/tests/{id}/key/review")
@access(TEACHER)
def review_key(id: UUID, body: ReviewBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    numbers = sorted(set(body.numbers))
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        version = draft_version(tx, id, create=False)
        if version is None:
            raise INVALID
        updated = tx.execute(
            """update keys.answer_key_items
               set reviewed_at = %s, reviewed_by = %s, flagged = false
               where version_id = %s and question_number = any(%s)""",
            (ctx.now(), identity.user_id, version["id"], numbers),
        ).rowcount
        if updated != len(numbers):
            raise INVALID  # rolls back: no partial review
        write_audit(
            tx,
            action="key.review",
            entity="test",
            entity_id=id,
            after={"questions": numbers, "count": len(numbers)},
        )
        return load_key(tx, id)


def key_source(tx: Conn, test_id: UUID) -> dict[str, Any]:
    asset = tx.execute(
        """select id, storage_key, has_text_layer from test_assets
           where test_id = %s and kind = 'key_source'""",
        (test_id,),
    ).fetchone()
    if asset is None:
        raise HttpError(409, "key_source_missing")
    return asset


def read_key_lines(ctx: CtxDep, storage_key: str) -> list[KeyLine]:
    if ctx.store is None:
        raise HttpError(503, "storage_unavailable")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "key.pdf"
        try:
            ctx.store.get(storage_key, path)
        except Exception as err:
            raise HttpError(503, "storage_unavailable") from err
        try:
            return pdf_lines(path)
        except PdfRejected as err:
            raise HttpError(422, err.code) from err


@router.post("/api/tests/{id}/key/import")
@access(TEACHER)
def import_key(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        asset = key_source(tx, id)
        if asset["has_text_layer"] is not True:
            raise HttpError(409, "key_no_text_layer")
    # The file is read outside the transaction so a slow download never holds the test lock.
    drafts = parse_key_lines(read_key_lines(ctx, asset["storage_key"]))
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        if key_source(tx, id)["id"] != asset["id"]:
            raise HttpError(409, "key_source_changed")
        counts = apply_drafts(tx, id, drafts, "text_layer")
        # Counts only: admins can read the audit log but must not see answers.
        write_audit(tx, action="key.import", entity="test", entity_id=id, after=counts)
    return counts


@router.post("/api/tests/{id}/key/scan", status_code=202)
@access(TEACHER)
def scan_key(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, id, draft=True)
        asset = key_source(tx, id)
        if asset["has_text_layer"] is True:
            raise HttpError(409, "key_has_text_layer")
        active = tx.execute(
            "select 1 from ocr_jobs where asset_id = %s and state in ('queued', 'running') for update",
            (asset["id"],),
        ).fetchone()
        if active is not None:
            raise HttpError(409, "ocr_in_progress")
        # A new row, not a reset: a stale worker's fence on the old id can then never match.
        tx.execute("delete from ocr_jobs where asset_id = %s", (asset["id"],))
        tx.execute(
            "insert into ocr_jobs (asset_id, teacher_id) values (%s, %s)", (asset["id"], test["teacher_id"])
        )
        write_audit(tx, action="key.scan", entity="test", entity_id=id)
    return {"state": "queued", "errorCode": None}


@router.post("/api/tests/{id}/publish")
@access(TEACHER)
def publish_test(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        blockers = blockers_of(tx, id)
        if blockers:
            return JSONResponse({"error": "not_ready", "blockers": blockers}, status_code=409)
        version = draft_version(tx, id, create=False)
        assert version is not None  # noqa: S101  # a ready test has a full key
        tx.execute(
            "update keys.answer_key_versions set approved_at = %s, approved_by = %s where id = %s",
            (ctx.now(), identity.user_id, version["id"]),
        )
        row = tx.execute(
            f"update tests set status = 'published', published_at = %s where id = %s returning {TEST_COLUMNS}",  # noqa: E501, S608
            (ctx.now(), id),
        ).fetchone()
        count = tx.execute("select count(*) as n from questions where test_id = %s", (id,)).fetchone()
        write_audit(
            tx,
            action="test.publish",
            entity="test",
            entity_id=id,
            after={"keyVersion": version["version"], "questions": count["n"] if count else 0},
        )
        return row


@router.post("/api/tests/{id}/archive")
@access(TEACHER)
def archive_test(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, id)
        if test["status"] == "archived":
            return public(test)
        if test["status"] != "published":
            raise HttpError(409, "test_not_published")
        row = tx.execute(
            f"update tests set status = 'archived' where id = %s returning {TEST_COLUMNS}",  # noqa: S608
            (id,),
        ).fetchone()
        write_audit(tx, action="test.archive", entity="test", entity_id=id, before={"status": "published"})
        return row
