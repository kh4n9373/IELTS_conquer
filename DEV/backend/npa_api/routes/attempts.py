import json
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, Field

from ..core.db import Identity, Role, with_identity
from ..core.http import (
    Body,
    Ctx,
    CtxDep,
    GuardedRoute,
    HttpError,
    IdentityDep,
    access,
    domain_errors,
    storable,
)
from ..grading.release import build_result, facts_of, is_released, pending
from ..grading.settle import GRACE_SECONDS, settle_attempt, settle_if_due
from .test_files import URL_SECONDS, unavailable

router = APIRouter(route_class=GuardedRoute)
STUDENT: tuple[Role, ...] = ("student",)
BOTH: tuple[Role, ...] = ("student", "teacher")
Text = Annotated[str, AfterValidator(storable)]


class StartBody(Body):
    device: Annotated[Text, Field(max_length=20)]


class Item(Body):
    number: Annotated[int, Field(ge=1, le=1000)]
    value: Annotated[Text, Field(max_length=200)]
    markedForReview: bool  # noqa: N815
    seq: Annotated[int, Field(ge=0, le=2**53)]


class SubmitBody(Body):
    writerToken: Annotated[Text, Field(min_length=1, max_length=200)]  # noqa: N815


class SaveBody(Body):
    writerToken: Annotated[Text, Field(min_length=1, max_length=200)]  # noqa: N815
    items: Annotated[list[Item], Field(max_length=40)]


@router.post("/api/my/assignments/{id}/attempts")
@access(STUDENT)
def start_attempt(id: UUID, body: StartBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        row = tx.execute(
            "select app.attempt_start(%s, %s, %s) as id", (id, ctx.now(), body.device)
        ).fetchone()
        assert row is not None  # noqa: S101
        return {"attemptId": row["id"]}


@router.post("/api/attempts/{id}/claim")
@access(STUDENT)
def claim(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    settle_if_due(ctx, id)
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        row = tx.execute("select app.attempt_claim(%s, %s) as token", (id, ctx.now())).fetchone()
        assert row is not None  # noqa: S101
        return JSONResponse({"writerToken": row["token"]}, headers={"Cache-Control": "no-store"})


@router.get("/api/attempts/{id}")
@access(STUDENT)
def room(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    settle_if_due(ctx, id)
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        row = tx.execute("select app.attempt_room(%s, %s) as room", (id, ctx.now())).fetchone()
        assert row is not None  # noqa: S101
        return row["room"]


@router.get("/api/attempts/{id}/pdf-url")
@access(STUDENT)
def pdf_url(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        row = tx.execute("select app.attempt_pdf_key(%s, %s) as key", (id, ctx.now())).fetchone()
    assert row is not None  # noqa: S101
    if ctx.store is None:
        raise unavailable()
    try:
        url = ctx.store.signed_url(row["key"], URL_SECONDS)
    except Exception:
        raise unavailable() from None
    return JSONResponse({"url": url, "expiresIn": URL_SECONDS}, headers={"Cache-Control": "no-store"})


@router.put("/api/attempts/{id}/answers")
@access(STUDENT)
def save_answers(id: UUID, body: SaveBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    items = [
        {"number": i.number, "value": i.value, "marked": i.markedForReview, "seq": i.seq} for i in body.items
    ]
    settle_if_due(ctx, id)
    now = ctx.now()
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        row = tx.execute(
            "select app.attempt_save(%s, %s, %s::jsonb, %s, %s) as result",
            (id, body.writerToken, json.dumps(items), now, GRACE_SECONDS),
        ).fetchone()
    assert row is not None  # noqa: S101
    result = row["result"]
    if result["status"] == "superseded":
        raise HttpError(409, "stale_device")
    late = result["late"] > 0
    return {
        "accepted": [] if late else [{"number": i["number"], "seq": i["seq"]} for i in items],
        "late": [i["number"] for i in items] if late else [],
        "serverNow": now,
    }


def _state(ctx: Ctx, identity: Identity, id: UUID) -> dict[str, Any]:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute("select status, finalized_at from attempts where id = %s", (id,)).fetchone()
    if row is None:
        raise HttpError(404, "not_found")
    return row


@router.post("/api/attempts/{id}/submit")
@access(STUDENT)
def submit(id: UUID, body: SubmitBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        tx.execute("select app.attempt_assert_active(%s)", (id,))  # 404 not own, 403 removed
        held = tx.execute("select writer_token from attempts where id = %s", (id,)).fetchone()
    if held is None or held["writer_token"] != body.writerToken:
        raise HttpError(409, "stale_device")
    settle_if_due(ctx, id)
    settle_attempt(ctx, id, source="student")
    state = _state(ctx, identity, id)
    return {"status": state["status"], "finalizedAt": state["finalized_at"]}


def _result_row(ctx: Ctx, identity: Identity, id: UUID) -> dict[str, Any]:
    cutoff = ctx.now() - timedelta(seconds=GRACE_SECONDS)
    with ctx.pool.connection() as conn:
        found = conn.execute("select app.attempt_result_row(%s, %s) as row", (id, cutoff)).fetchone()
    assert found is not None  # noqa: S101
    row = found["row"]
    if row is None:
        raise HttpError(404, "not_found")
    owner = row["studentId"] if identity.role == "student" else row["teacherId"]
    if str(owner) != str(identity.user_id):
        raise HttpError(404, "not_found")
    return dict(row)


@router.get("/api/attempts/{id}/result")
@access(BOTH)
def result(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    _result_row(ctx, identity, id)  # ownership first: settling must not reveal anything to strangers
    settle_if_due(ctx, id)
    row = _result_row(ctx, identity, id)
    student = identity.role == "student"
    released = is_released(facts_of(row), ctx.now())
    if row["run"] is None or (student and not released):
        body = pending(row, student=student)
    else:
        body = build_result(row, released=released)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
