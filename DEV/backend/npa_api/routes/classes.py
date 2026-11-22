from typing import Any
from uuid import UUID

from fastapi import APIRouter

from ..core.db import Conn, Role, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, NameField, access, not_found

router = APIRouter(route_class=GuardedRoute)
TEACHER: tuple[Role, ...] = ("teacher",)
MEMBERS: tuple[Role, ...] = ("teacher", "student")
CLASS_COLUMNS = 'id, name, status, created_at as "createdAt"'


class ClassBody(Body):
    name: NameField


def lock_active_class(tx: Conn, class_id: UUID) -> dict[str, Any]:
    """Locks a class visible to the caller. RLS turns another tenant's class into a 404."""
    row = tx.execute(
        "select teacher_id, name, status from classes where id = %s for update", (class_id,)
    ).fetchone()
    if row is None:
        raise not_found()
    if row["status"] != "active":
        raise HttpError(409, "class_archived")
    return row


@router.get("/api/classes")
@access(MEMBERS)
def list_classes(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        return tx.execute(f"select {CLASS_COLUMNS} from classes order by created_at desc").fetchall()  # noqa: S608


@router.post("/api/classes", status_code=201)
@access(TEACHER)
def create_class(body: ClassBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute(
            f"insert into classes (teacher_id, name) values (%s, %s) returning {CLASS_COLUMNS}",  # noqa: S608
            (identity.user_id, body.name),
        ).fetchone()
        assert row is not None  # noqa: S101
        write_audit(tx, action="class.create", entity="class", entity_id=row["id"], after={"name": body.name})
        return row


@router.get("/api/classes/{id}")
@access(MEMBERS)
def get_class(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute(f"select {CLASS_COLUMNS} from classes where id = %s", (id,)).fetchone()  # noqa: S608
        if row is None:
            raise not_found()
        if identity.role != "teacher":
            return row
        members = tx.execute(
            """select u.id, u.email, u.display_name as "displayName", m.joined_at as "joinedAt"
               from class_members m join users u on u.id = m.student_id
               where m.class_id = %s and m.removed_at is null order by m.joined_at""",
            (id,),
        ).fetchall()
        invites = tx.execute(
            """select id, code, expires_at as "expiresAt" from invites
               where class_id = %s and disabled_at is null and expires_at > %s order by created_at desc""",
            (id, ctx.now()),
        ).fetchall()
        return {**row, "members": members, "invites": invites}


@router.patch("/api/classes/{id}")
@access(TEACHER)
def update_class(id: UUID, body: ClassBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        before = lock_active_class(tx, id)
        row = tx.execute(
            f"update classes set name = %s where id = %s returning {CLASS_COLUMNS}",  # noqa: S608
            (body.name, id),
        ).fetchone()
        write_audit(
            tx,
            action="class.update",
            entity="class",
            entity_id=id,
            before={"name": before["name"]},
            after={"name": body.name},
        )
        return row


@router.post("/api/classes/{id}/archive", status_code=204)
@access(TEACHER)
def archive_class(id: UUID, ctx: CtxDep, identity: IdentityDep) -> None:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute("select status from classes where id = %s for update", (id,)).fetchone()
        if row is None:
            raise not_found()
        if row["status"] == "archived":
            return
        tx.execute("update classes set status = 'archived' where id = %s", (id,))
        write_audit(tx, action="class.archive", entity="class", entity_id=id)


@router.delete("/api/classes/{id}/members/{studentId}", status_code=204)
@access(TEACHER)
def remove_member(id: UUID, studentId: UUID, ctx: CtxDep, identity: IdentityDep) -> None:
    with with_identity(ctx.pool, identity) as tx:
        result = tx.execute(
            """update class_members set removed_at = %s
               where class_id = %s and student_id = %s and removed_at is null""",
            (ctx.now(), id, studentId),
        )
        if not result.rowcount:
            raise not_found()
        write_audit(
            tx,
            action="class.remove_student",
            entity="class",
            entity_id=id,
            after={"studentId": str(studentId)},
        )
