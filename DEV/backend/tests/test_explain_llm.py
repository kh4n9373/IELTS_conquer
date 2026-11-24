import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from npa_api.explain import llm
from npa_api.explain.llm import LlmError, from_env, groq, with_fallback

SCHEMA: dict[str, Any] = {"type": "object"}


def client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def groq_reply(content: str, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda req: httpx.Response(status, json={"choices": [{"message": {"content": content}}]})


def test_groq_parses_json_content() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return groq_reply('{"items":[]}')(req)

    assert groq("k", client(handler))("p", SCHEMA) == {"items": []}
    assert seen[0].headers["authorization"] == "Bearer k"
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


def test_groq_429_is_rate_limited() -> None:
    with pytest.raises(LlmError) as err:
        groq("k", client(groq_reply("", 429)))("p", SCHEMA)
    assert err.value.code == "rate_limited"


def test_invalid_json_is_bad_reply() -> None:
    with pytest.raises(LlmError) as err:
        groq("k", client(groq_reply("not json")))("p", SCHEMA)
    assert err.value.code == "bad_reply"


def test_timeout_is_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LlmError) as err:
        groq("k", client(handler))("p", SCHEMA)
    assert err.value.code == "provider_error"


def test_fallback_uses_second_on_error() -> None:
    def first(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise LlmError("rate_limited")

    assert with_fallback(first, lambda p, s: {"ok": 1})("p", SCHEMA) == {"ok": 1}


def test_fallback_raises_last_error() -> None:
    def fail(code: str) -> Any:
        def provider(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            raise LlmError(code)  # type: ignore[arg-type]

        return provider

    with pytest.raises(LlmError) as err:
        with_fallback(fail("rate_limited"), fail("quota_exceeded"))("p", SCHEMA)
    assert err.value.code == "quota_exceeded"


def test_from_env_without_groq_uses_gemini_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    hosts: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hosts.append(req.url.host)
        body = {"candidates": [{"content": {"parts": [{"text": '{"a":1}'}]}}]}
        return httpx.Response(200, json=body)

    assert from_env(client(handler))("p", SCHEMA) == {"a": 1}
    assert hosts == ["generativelanguage.googleapis.com"]
    assert llm.GROQ_URL.startswith("https://api.groq.com/")
