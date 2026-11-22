import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request, Response

from ..core.db import with_identity
from ..core.http import (
    SESSION_COOKIE,
    Body,
    Ctx,
    CtxDep,
    EmailField,
    GuardedRoute,
    HttpError,
    IdentityDep,
    LoginPassword,
    PasswordField,
    TokenField,
    access,
)
from ..core.mailer import Mail
from ..core.security import burn_password_check, hash_password, hash_token, new_token, verify_password

log = logging.getLogger("npa.auth")
router = APIRouter(route_class=GuardedRoute)

SESSION_TTL = timedelta(days=14)
RESET_TTL = timedelta(hours=1)
ACTIVATION_TTL = timedelta(hours=72)
AUTH_LIMIT = 10


def send_password_link(ctx: Ctx, email: str, ttl: timedelta, subject: str) -> bool:
    """Returns False when the email has no active account or the mail could not be sent."""
    token = new_token()
    now = ctx.now()
    with ctx.pool.connection() as conn:
        row = conn.execute(
            "select email from app.reset_create(%s, %s, %s, %s)", (email, hash_token(token), now, now + ttl)
        ).fetchone()
    if row is None:
        return False
    link = f"{ctx.public_url}/reset-password?token={token}"
    try:
        ctx.mailer.send(Mail(row["email"], subject, f"Mở liên kết sau để đặt mật khẩu:\n{link}"))
    except Exception:
        log.exception("Could not send password link")
        return False
    return True


class LoginBody(Body):
    email: EmailField
    password: LoginPassword


class ForgotBody(Body):
    email: EmailField


class ResetBody(Body):
    token: TokenField
    password: PasswordField


@router.post("/api/auth/login")
@access("public", limit=AUTH_LIMIT)
def login(body: LoginBody, ctx: CtxDep, response: Response) -> dict[str, Any]:
    with ctx.pool.connection() as conn:
        user = conn.execute("select * from app.login_lookup(%s)", (body.email,)).fetchone()
    if user is None:
        burn_password_check(body.password)
        raise HttpError(401, "invalid_credentials")
    if not verify_password(body.password, user["password_hash"]) or user["status"] != "active":
        raise HttpError(401, "invalid_credentials")

    token = new_token()
    with ctx.pool.connection() as conn:
        conn.execute(
            "select app.session_create(%s, %s, %s)", (user["id"], hash_token(token), ctx.now() + SESSION_TTL)
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        path="/",
        httponly=True,
        samesite="lax",
        secure=ctx.secure_cookies,
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return {"id": user["id"]}


@router.post("/api/auth/logout", status_code=204)
@access("public")
def logout(request: Request, ctx: CtxDep, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with ctx.pool.connection() as conn:
            conn.execute("select app.session_revoke(%s, %s)", (hash_token(token), ctx.now()))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/api/auth/forgot", status_code=204)
@access("public", limit=AUTH_LIMIT)
def forgot(body: ForgotBody, ctx: CtxDep) -> None:
    send_password_link(ctx, body.email, RESET_TTL, "Đặt lại mật khẩu")


@router.post("/api/auth/reset", status_code=204)
@access("public", limit=AUTH_LIMIT)
def reset(body: ResetBody, ctx: CtxDep) -> None:
    password_hash = hash_password(body.password)
    with ctx.pool.connection() as conn:
        row = conn.execute(
            "select app.reset_consume(%s, %s, %s) as ok", (hash_token(body.token), password_hash, ctx.now())
        ).fetchone()
    if row is None or not row["ok"]:
        raise HttpError(400, "invalid_token")


@router.get("/api/me")
@access(("student", "teacher", "admin"))
def me(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        return tx.execute(
            'select id, email, display_name as "displayName", role from users where id = %s',
            (identity.user_id,),
        ).fetchone()
