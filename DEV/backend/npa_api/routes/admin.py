import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter

from ..core.db import Identity, Role, with_identity, write_audit
from ..core.http import (
    Body,
    Ctx,
    CtxDep,
    EmailField,
    GuardedRoute,
    IdentityDep,
    NameField,
    access,
    domain_errors,
    not_found,
)
from ..core.security import hash_password
from .auth import ACTIVATION_TTL, send_password_link

router = APIRouter(route_class=GuardedRoute)
ADMIN: tuple[Role, ...] = ("admin",)


class TeacherBody(Body):
    email: EmailField
    displayName: NameField


@router.get("/api/admin/teachers")
@access(ADMIN)
def list_teachers(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        return tx.execute(
            """select id, email, display_name as "displayName", status, created_at as "createdAt"
               from users where role = 'teacher' order by created_at"""
        ).fetchall()


@router.post("/api/admin/teachers", status_code=201)
@access(ADMIN)
def create_teacher(body: TeacherBody, ctx: CtxDep, identity: IdentityDep) -> dict[str, Any]:
    # Unusable until the teacher sets a password through the emailed link.
    placeholder = hash_password(secrets.token_hex(32))
    with with_identity(ctx.pool, identity) as tx:
        with domain_errors():
            row = tx.execute(
                "select app.admin_create_teacher(%s, %s, %s) as id",
                (body.email, placeholder, body.displayName),
            ).fetchone()
        assert row is not None  # noqa: S101
        created = row["id"]
        write_audit(
            tx, action="teacher.create", entity="user", entity_id=created, after={"email": body.email}
        )
    sent = send_password_link(ctx, body.email, ACTIVATION_TTL, "Kích hoạt tài khoản giáo viên")
    return {
        "id": created,
        "email": body.email,
        "displayName": body.displayName,
        "status": "active",
        "activationSent": sent,
    }


def _set_status(ctx: Ctx, identity: Identity, teacher: UUID, verb: str, status: str) -> None:
    with with_identity(ctx.pool, identity) as tx:
        current = tx.execute(
            "select status from users where id = %s and role = 'teacher' for update", (teacher,)
        ).fetchone()
        if current is None:
            raise not_found()
        if current["status"] == status:
            return
        tx.execute("update users set status = %s where id = %s", (status, teacher))
        write_audit(
            tx,
            action=f"teacher.{verb}",
            entity="user",
            entity_id=teacher,
            before={"status": current["status"]},
            after={"status": status},
        )


@router.post("/api/admin/teachers/{id}/lock", status_code=204)
@access(ADMIN)
def lock_teacher(id: UUID, ctx: CtxDep, identity: IdentityDep) -> None:
    _set_status(ctx, identity, id, "lock", "locked")


@router.post("/api/admin/teachers/{id}/unlock", status_code=204)
@access(ADMIN)
def unlock_teacher(id: UUID, ctx: CtxDep, identity: IdentityDep) -> None:
    _set_status(ctx, identity, id, "unlock", "active")
