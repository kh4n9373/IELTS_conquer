from typing import Any, Literal

from fastapi import APIRouter

from ..core.db import Conn, Role, with_identity
from ..core.http import Body, CtxDep, GuardedRoute, IdentityDep, access

router = APIRouter(route_class=GuardedRoute)

# Placeholder terms until PO supplies approved text (BA question 6).
TERMS_VERSION = "draft-2026-11"
ConsentPurpose = Literal["core_learning", "ai_processing"]
ALLOWED: tuple[Role, ...] = ("student", "teacher")


class ConsentBody(Body):
    purpose: ConsentPurpose
    granted: bool


def has_consent(tx: Conn, purpose: ConsentPurpose) -> bool:
    """The latest decision wins. No record means no consent."""
    row = tx.execute(
        """select granted from consents where user_id = app.user_id() and purpose = %s
           order by created_at desc limit 1""",
        (purpose,),
    ).fetchone()
    return bool(row and row["granted"])


@router.get("/api/consents")
@access(ALLOWED)
def list_consents(ctx: CtxDep, identity: IdentityDep) -> dict[str, Any]:
    with with_identity(ctx.pool, identity) as tx:
        rows = tx.execute(
            """select purpose, granted, terms_version as "termsVersion", created_at as "createdAt"
               from consents where user_id = app.user_id() order by created_at"""
        ).fetchall()
    current = {"core_learning": False, "ai_processing": False}
    for row in rows:
        current[row["purpose"]] = row["granted"]
    return {"termsVersion": TERMS_VERSION, "current": current, "history": rows}


@router.post("/api/consents", status_code=204)
@access(ALLOWED)
def record_consent(body: ConsentBody, ctx: CtxDep, identity: IdentityDep) -> None:
    with with_identity(ctx.pool, identity) as tx:
        tx.execute(
            "insert into consents (purpose, terms_version, granted) values (%s, %s, %s)",
            (body.purpose, TERMS_VERSION, body.granted),
        )
