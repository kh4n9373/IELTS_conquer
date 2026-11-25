import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .core.db import Pool
from .core.google import GoogleVerifier
from .core.http import Access, Ctx, GuardedRoute, HttpError, RateLimiter
from .core.mailer import Mailer
from .core.storage import FileStore
from .routes import (
    admin,
    assignments,
    attempts,
    auth,
    classes,
    consent,
    invites,
    profile,
    test_files,
    test_import,
    test_keys,
    tests,
)

log = logging.getLogger("npa.api")
BODY_LIMIT = 64 * 1024
# Only these exact paths take a large raw PDF body; the upload handler enforces its own limit.
UPLOAD_PATH = re.compile(
    r"/api/tests/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/(pdf|key-source)"
)

# Imported exam JSON (1 MB of text, escaped inside a JSON string) and its step-2 edits.
IMPORT_LIMIT = 3 * 1024 * 1024
IMPORT_PATH = re.compile(
    r"/api/tests/(import/validate|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/import)"
)


class BodyLimit:
    """Rejects oversized requests before any handler buffers them."""

    def __init__(self, app: ASGIApp, limit: int = BODY_LIMIT) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] == "PUT" and UPLOAD_PATH.fullmatch(scope["path"]):
            await self.app(scope, receive, send)
            return
        limit = IMPORT_LIMIT if IMPORT_PATH.fullmatch(scope["path"]) else self.limit
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > limit:
                response = JSONResponse({"error": "bad_request"}, status_code=413)
                await response(scope, receive, send)
                return
            if not message.get("more_body"):
                break

        async def replay() -> Message:
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def build_app(
    *,
    pool: Pool,
    mailer: Mailer,
    public_url: str,
    secure_cookies: bool,
    now: Callable[[], datetime] | None = None,
    rate_limit: bool = True,
    lifespan: Any = None,
    store: FileStore | None = None,
    google: GoogleVerifier | None = None,
) -> FastAPI:
    # Docs routes are off: they would be endpoints without an access rule.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = Ctx(
        pool=pool,
        mailer=mailer,
        public_url=public_url,
        secure_cookies=secure_cookies,
        now=now or (lambda: datetime.now(UTC)),
        limiter=RateLimiter() if rate_limit else None,
        store=store,
        google=google,
    )
    app.add_middleware(BodyLimit)

    @app.exception_handler(HttpError)
    async def on_http_error(_: Request, err: HttpError) -> JSONResponse:
        return _error(err.status, err.code)

    @app.exception_handler(RequestValidationError)
    async def on_invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error(400, "invalid_input")

    @app.exception_handler(HTTPException)
    async def on_framework_error(_: Request, err: HTTPException) -> JSONResponse:
        return _error(err.status_code, "not_found" if err.status_code == 404 else "bad_request")

    @app.exception_handler(Exception)
    async def on_crash(_: Request, err: Exception) -> JSONResponse:
        log.error("Unhandled error", exc_info=err)
        return _error(500, "internal_error")

    # Added route by route: include_router wraps them, which would hide them from the guard check.
    for module in (
        auth,
        profile,
        admin,
        classes,
        invites,
        consent,
        tests,
        test_import,
        test_keys,
        test_files,
        assignments,
        attempts,
    ):
        app.router.routes.extend(module.router.routes)

    assert_all_guarded(app)
    return app


def assert_all_guarded(app: FastAPI) -> None:
    """Deny by default, including routes added outside the guarded routers."""
    for route in app.routes:
        if not isinstance(route, GuardedRoute):
            raise RuntimeError(f"Route {getattr(route, 'path', route)} must declare @access")


def access_table(app: FastAPI) -> list[tuple[str, str, Access]]:
    """Every registered route and its access rule. The tenant matrix test reads this."""
    return [
        (method, route.path, route.access_rule)
        for route in app.routes
        if isinstance(route, GuardedRoute)
        for method in sorted(route.methods or ())
        if method != "HEAD"
    ]
