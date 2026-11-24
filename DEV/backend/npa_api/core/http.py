"""Request guard, shared dependencies, input types and error translation."""

import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar

import psycopg
from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute
from pydantic import AfterValidator, BaseModel, ConfigDict
from starlette.responses import Response

from .db import Identity, Pool, Role
from .mailer import Mailer
from .security import hash_token
from .storage import FileStore

SESSION_COOKIE = "npa_session"

Access = Literal["public"] | tuple[Role, ...]
F = TypeVar("F", bound=Callable[..., Any])


class HttpError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def not_found() -> HttpError:
    return HttpError(404, "not_found")


class RateLimiter:
    """Fixed-window, in-process counter. Enough for one API process in the pilot."""

    MAX_KEYS = 10_000

    def __init__(self, window_seconds: float = 60, clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window_seconds
        self.clock = clock
        self.hits: dict[tuple[str, str], tuple[float, int]] = {}
        self.lock = threading.Lock()

    def allow(self, client: str, route: str, limit: int) -> bool:
        now = self.clock()
        key = (client, route)
        with self.lock:
            if len(self.hits) >= self.MAX_KEYS:
                self.hits = {k: v for k, v in self.hits.items() if now - v[0] < self.window}
            started, count = self.hits.get(key, (now, 0))
            if now - started >= self.window:
                started, count = now, 0
            self.hits[key] = (started, count + 1)
            return count < limit


@dataclass
class Ctx:
    pool: Pool
    mailer: Mailer
    public_url: str
    secure_cookies: bool
    now: Callable[[], datetime]
    limiter: RateLimiter | None
    store: FileStore | None = None


def access(rule: Access, limit: int | None = None) -> Callable[[F], F]:
    """Required on every endpoint. `limit` is requests per minute per client."""

    def mark(endpoint: F) -> F:
        endpoint.__npa_access__ = rule  # type: ignore[attr-defined]
        endpoint.__npa_limit__ = limit  # type: ignore[attr-defined]
        return endpoint

    return mark


def _authorize(request: Request, rule: Access) -> None:
    if rule == "public":
        return
    ctx: Ctx = request.app.state.ctx
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HttpError(401, "unauthenticated")
    with ctx.pool.connection() as conn:
        row = conn.execute(
            "select * from app.session_identity(%s, %s)", (hash_token(token), ctx.now())
        ).fetchone()
    if row is None:
        raise HttpError(401, "unauthenticated")
    if row["role"] not in rule:
        raise HttpError(403, "forbidden")
    request.state.identity = Identity(row["user_id"], row["role"], row["teacher_id"])


class GuardedRoute(APIRoute):
    """Deny by default: an endpoint without `@access` cannot be registered.

    The check runs before FastAPI parses the path or body, so an unauthorized caller
    learns nothing from validation errors.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        rule = getattr(endpoint, "__npa_access__", None)
        if rule is None:
            raise RuntimeError(f"Route {path} must declare @access")
        self.access_rule: Access = rule
        self.rate_limit: int | None = getattr(endpoint, "__npa_limit__", None)
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self) -> Callable[[Request], Any]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            ctx: Ctx = request.app.state.ctx
            if ctx.limiter is not None and self.rate_limit is not None:
                client = request.client.host if request.client else "unknown"
                if not ctx.limiter.allow(client, self.path, self.rate_limit):
                    raise HttpError(429, "rate_limited")
            await run_in_threadpool(_authorize, request, self.access_rule)
            response: Response = await handler(request)
            return response

        return guarded


def get_ctx(request: Request) -> Ctx:
    ctx: Ctx = request.app.state.ctx
    return ctx


def get_identity(request: Request) -> Identity:
    identity: Identity | None = getattr(request.state, "identity", None)
    if identity is None:
        raise HttpError(401, "unauthenticated")
    return identity


CtxDep = Annotated[Ctx, Depends(get_ctx)]
IdentityDep = Annotated[Identity, Depends(get_identity)]


class Body(BaseModel):
    """Strict: no coercion such as "true" to True. Unknown fields are dropped."""

    model_config = ConfigDict(strict=True)


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def storable(value: str) -> str:
    """PostgreSQL text cannot hold NUL, and lone surrogates cannot be encoded."""
    if "\x00" in value:
        raise ValueError("invalid text")
    try:
        value.encode()
    except UnicodeEncodeError as err:
        raise ValueError("invalid text") from err
    return value


def _sized(minimum: int, maximum: int, trim: bool = False) -> Callable[[str], str]:
    def check(value: str) -> str:
        value = storable(value.strip() if trim else value)
        if not minimum <= len(value) <= maximum:
            raise ValueError("invalid length")
        return value

    return check


def normalize_email(value: str) -> str:
    value = storable(value).strip().lower()
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise ValueError("invalid email")
    return value


EmailField = Annotated[str, AfterValidator(normalize_email)]
PasswordField = Annotated[str, AfterValidator(_sized(8, 200))]
LoginPassword = Annotated[str, AfterValidator(_sized(1, 200))]
TokenField = Annotated[str, AfterValidator(_sized(1, 200))]
NameField = Annotated[str, AfterValidator(_sized(1, 120, trim=True))]
PassageField = Annotated[str, AfterValidator(_sized(0, 50000))]
PromptField = Annotated[str, AfterValidator(_sized(0, 2000, trim=True))]
OptionField = Annotated[str, AfterValidator(_sized(1, 300, trim=True))]
InviteCode = Annotated[str, AfterValidator(_sized(1, 32, trim=True))]

DOMAIN_ERRORS: dict[str, tuple[int, str]] = {
    "invite_invalid": (404, "not_found"),
    "email_taken": (409, "email_taken"),
    "other_teacher": (409, "other_teacher"),
    "not_found": (404, "not_found"),
    "not_open": (409, "not_open"),
    "closed": (409, "not_open"),
    "no_attempts_left": (409, "no_attempts_left"),
    "removed_from_class": (403, "removed_from_class"),
    "test_archived": (409, "test_archived"),
    "attempt_closed": (409, "attempt_closed"),
    "invalid_items": (400, "invalid_input"),
}


@contextmanager
def domain_errors() -> Iterator[None]:
    """Translates the fixed error names raised by SQL functions."""
    try:
        yield
    except psycopg.errors.RaiseException as err:
        known = DOMAIN_ERRORS.get(err.diag.message_primary or "")
        if known is None:
            raise
        raise HttpError(*known) from err
