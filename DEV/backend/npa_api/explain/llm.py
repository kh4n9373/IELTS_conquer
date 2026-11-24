"""LLM providers for explanations: Groq first, Gemini as fallback.

Only question text goes in. Prompts and replies are never logged.
"""

import json
import logging
import os
from collections.abc import Callable
from typing import Any, Literal

import httpx

from ..ocr.gemini import DEFAULT_MODEL as GEMINI_MODEL
from ..ocr.gemini import URL as GEMINI_URL

log = logging.getLogger("npa.explain")

Code = Literal["rate_limited", "provider_error", "bad_reply", "quota_exceeded"]
Llm = Callable[[str, dict[str, Any]], dict[str, Any]]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


class LlmError(Exception):
    def __init__(self, code: Code) -> None:
        super().__init__(code)
        self.code: Code = code


def _post(client: httpx.Client, url: str, headers: dict[str, str], body: Any, timeout: float) -> Any:
    try:
        reply = client.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.HTTPError:
        raise LlmError("provider_error") from None
    if reply.status_code == 429:
        raise LlmError("rate_limited")
    if reply.status_code != 200:
        raise LlmError("provider_error")
    try:
        return reply.json()
    except ValueError:
        raise LlmError("bad_reply") from None


def _object(text: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        raise LlmError("bad_reply") from None
    if not isinstance(parsed, dict):
        raise LlmError("bad_reply")
    return parsed


def groq(api_key: str, client: httpx.Client, model: str = GROQ_MODEL) -> Llm:
    def call(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        system = f"Reply with one JSON object matching this JSON schema:\n{json.dumps(schema)}"
        body = {
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        data = _post(client, GROQ_URL, {"Authorization": f"Bearer {api_key}"}, body, 60)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LlmError("bad_reply") from None
        return _object(text)

    call.__name__ = "groq"
    return call


def gemini(api_key: str, client: httpx.Client, model: str = GEMINI_MODEL) -> Llm:
    def call(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        # The key travels in a header, so it never appears in a logged URL.
        data = _post(client, GEMINI_URL.format(model=model), {"x-goog-api-key": api_key}, body, 120)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise LlmError("bad_reply") from None
        return _object(text)

    call.__name__ = "gemini"
    return call


def with_fallback(*providers: Llm) -> Llm:
    def call(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        error = LlmError("provider_error")
        for provider in providers:
            try:
                return provider(prompt, schema)
            except LlmError as e:
                log.warning("explain provider %s failed: %s", getattr(provider, "__name__", "?"), e.code)
                error = e
        raise error

    return call


def from_env(client: httpx.Client) -> Llm:
    fallback = gemini(os.environ["GEMINI_API_KEY"], client, os.environ.get("GEMINI_MODEL", GEMINI_MODEL))
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return fallback
    return with_fallback(groq(key, client, os.environ.get("GROQ_MODEL", GROQ_MODEL)), fallback)
