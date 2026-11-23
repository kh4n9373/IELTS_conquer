import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, Field

from ..core.db import Role, with_identity
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, access, domain_errors, storable
from .test_files import URL_SECONDS, unavailable

router = APIRouter(route_class=GuardedRoute)
STUDENT: tuple[Role, ...] = ("student",)
GRACE_SECONDS = 30
Text = Annotated[str, AfterValidator(storable)]


class StartBody(Body):
    device: Annotated[Text, Field(max_length=20)]


class Item(Body):
    number: Annotated[int, Field(ge=1, le=1000)]
    value: Annotated[Text, Field(max_length=200)]
    markedForReview: bool  # noqa: N815
    seq: Annotated[int, Field(ge=0, le=2**53)]


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
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        row = tx.execute("select app.attempt_claim(%s, %s) as token", (id, ctx.now())).fetchone()
        assert row is not None  # noqa: S101
        return JSONResponse({"writerToken": row["token"]}, headers={"Cache-Control": "no-store"})


@router.get("/api/attempts/{id}")
@access(STUDENT)
def room(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
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
