"""Guarantees the Fastify version got from the framework and Python must prove itself."""

import json

import pytest
from fastapi import APIRouter, FastAPI

from conftest import Harness
from npa_api.app import assert_all_guarded
from npa_api.core.http import GuardedRoute, RateLimiter
from npa_api.core.security import hash_password, verify_password


def test_endpoint_without_access_rule_cannot_register() -> None:
    router = APIRouter(route_class=GuardedRoute)

    def open_endpoint() -> None: ...

    with pytest.raises(RuntimeError, match="must declare @access"):
        router.get("/api/open")(open_endpoint)


def test_plain_route_added_to_app_is_refused() -> None:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.get("/api/open")(lambda: None)
    with pytest.raises(RuntimeError, match="must declare @access"):
        assert_all_guarded(app)


def test_auth_runs_before_validation(h: Harness) -> None:
    assert h.call(None, "GET", "/api/classes/not-a-uuid").status == 401
    assert h.call(None, "POST", "/api/classes", {"name": "   "}).status == 401


def test_docs_are_not_exposed(h: Harness) -> None:
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert h.call(None, "GET", url).status == 404


def test_errors_use_the_api_shape(h: Harness) -> None:
    assert h.call(None, "GET", "/api/nope").json == {"error": "not_found"}
    assert h.call(None, "POST", "/api/auth/login", {"email": "x"}).json == {"error": "invalid_input"}


def test_rejects_oversized_body(h: Harness) -> None:
    res = h.call(None, "POST", "/api/auth/login", {"email": "a@test.local", "password": "x" * 70_000})
    assert res.status == 413


@pytest.mark.parametrize(
    "body",
    [
        {"email": "a@test.local", "password": "nul\x00byte"},
        {"email": "a\x00@test.local", "password": "password-123"},
        {"email": "a@test.local", "password": "lone\ud800surrogate"},
        {"email": "a@test.local", "password": True},
        ["not", "an", "object"],
    ],
)
def test_bad_input_is_400_not_500(h: Harness, body: object) -> None:
    res = h.client.post(
        "/api/auth/login",
        content=json.dumps(body),
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 400


def test_nul_in_invite_code_is_400(h: Harness) -> None:
    assert h.call(None, "GET", "/api/invites/AB%00CD").status == 400


def test_malformed_json_is_400(h: Harness) -> None:
    res = h.client.post("/api/auth/login", content="{oops", headers={"content-type": "application/json"})
    assert res.status_code == 400


def test_rate_limiter_blocks_then_recovers() -> None:
    t = [0.0]
    limiter = RateLimiter(window_seconds=60, clock=lambda: t[0])
    assert [limiter.allow("ip", "/r", 2) for _ in range(3)] == [True, True, False]
    assert limiter.allow("other-ip", "/r", 2)
    t[0] = 61
    assert limiter.allow("ip", "/r", 2)


def test_password_hash_round_trip_and_malformed_hash() -> None:
    stored = hash_password("mật-khẩu-dài")
    assert verify_password("mật-khẩu-dài", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "unused")
    assert not verify_password("x", "scrypt$a$b$c$d$e")
