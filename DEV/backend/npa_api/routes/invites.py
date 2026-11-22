from datetime import timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from ..core.db import Role, with_identity, write_audit
from ..core.http import (
    Body,
    CtxDep,
    EmailField,
    GuardedRoute,
    HttpError,
    IdentityDep,
    InviteCode,
    NameField,
    PasswordField,
    access,
    domain_errors,
    not_found,
)
from ..core.security import hash_password, new_invite_code
from .classes import lock_active_class
from .consent import TERMS_VERSION

router = APIRouter(route_class=GuardedRoute)
LIMIT = 20
TEACHER: tuple[Role, ...] = ("teacher",)


class InviteBody(Body):
    expiresInDays: Annotated[int, Field(ge=1, le=90)]


class RegisterConsents(Body):
    core_learning: bool
    ai_processing: bool


class RegisterBody(Body):
    email: EmailField
    password: PasswordField
    displayName: NameField
    ageGroup: Literal["18_plus", "under_18"]
    consents: RegisterConsents


@router.post("/api/classes/{id}/invites", status_code=201)
@access(TEACHER)
def create_invite(id: UUID, body: InviteBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    expires_at = ctx.now() + timedelta(days=body.expiresInDays)
    with with_identity(ctx.pool, identity) as tx:
        cls = lock_active_class(tx, id)
        row = tx.execute(
            """insert into invites (class_id, teacher_id, code, expires_at) values (%s, %s, %s, %s)
               returning id, code, expires_at as "expiresAt\"""",
            (id, cls["teacher_id"], new_invite_code(), expires_at),
        ).fetchone()
        assert row is not None  # noqa: S101
        write_audit(
            tx, action="invite.create", entity="invite", entity_id=row["id"], after={"classId": str(id)}
        )
        return row


@router.post("/api/invites/{id}/disable", status_code=204)
@access(TEACHER)
def disable_invite(id: UUID, ctx: CtxDep, identity: IdentityDep) -> None:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute("select disabled_at from invites where id = %s for update", (id,)).fetchone()
        if row is None:
            raise not_found()
        if row["disabled_at"] is not None:
            return
        tx.execute("update invites set disabled_at = %s where id = %s", (ctx.now(), id))
        write_audit(tx, action="invite.disable", entity="invite", entity_id=id)


@router.get("/api/invites/{code}")
@access("public", limit=LIMIT)
def preview_invite(code: InviteCode, ctx: CtxDep) -> dict[str, str]:
    with ctx.pool.connection() as conn:
        row = conn.execute("select class_name from app.invite_preview(%s, %s)", (code, ctx.now())).fetchone()
    if row is None:
        raise not_found()
    return {"className": row["class_name"]}


@router.post("/api/invites/{code}/register", status_code=201)
@access("public", limit=LIMIT)
def register(code: InviteCode, body: RegisterBody, ctx: CtxDep) -> dict[str, Any]:
    if body.ageGroup != "18_plus":
        raise HttpError(403, "age_restricted")
    if not body.consents.core_learning:
        raise HttpError(400, "consent_required")
    password_hash = hash_password(body.password)
    with ctx.pool.connection() as conn, domain_errors():
        row = conn.execute(
            "select app.register_student(%s, %s, %s, %s, %s, %s, %s) as id",
            (
                code,
                body.email,
                password_hash,
                body.displayName,
                body.consents.ai_processing,
                TERMS_VERSION,
                ctx.now(),
            ),
        ).fetchone()
    assert row is not None  # noqa: S101
    return {"id": row["id"]}


@router.post("/api/invites/{code}/join", status_code=204)
@access(("student",), limit=LIMIT)
def join(code: InviteCode, ctx: CtxDep, identity: IdentityDep) -> None:
    with with_identity(ctx.pool, identity) as tx, domain_errors():
        tx.execute("select app.join_class(%s, %s)", (code, ctx.now()))
