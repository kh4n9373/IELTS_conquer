"""PaddleOCR job API client. Response bodies, URLs and the token never leave this module in errors."""

import ipaddress
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn, cast

import httpx

from .provider import ErrorCode, OcrError, OcrJobExpired, OcrLine, OcrPage, OcrState, OcrStatus

API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.5"
MAX_RESULT_BYTES = 50 * 1024 * 1024
DOWNLOAD_SECONDS = 120
JOB_EXPIRED = 11002
TIMEOUT = httpx.Timeout(connect=10, read=60, write=120, pool=10)
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
HOSTNAME = re.compile(r"[a-z0-9._-]+")
STATES: frozenset[str] = frozenset({"pending", "running", "done", "failed"})


def fail(code: ErrorCode) -> NoReturn:
    # `from None` keeps httpx messages, which can hold URLs, out of any traceback.
    raise OcrError(code) from None


def check_result_url(url: object) -> httpx.URL:
    """Checks the host httpx will connect to: IDNA turns Unicode dots into ASCII ones first."""
    if not isinstance(url, str):
        fail("bad_result")
    try:
        parsed = httpx.URL(url)
        host = parsed.raw_host.decode("ascii").strip("[]").rstrip(".").lower()
    except (httpx.InvalidURL, ValueError):
        fail("bad_result")
    if parsed.scheme != "https" or not host or parsed.userinfo:
        fail("bad_result")
    if host == "localhost" or host.endswith(".localhost"):
        fail("bad_result")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        fail("bad_result")
    if not HOSTNAME.fullmatch(host):
        fail("bad_result")
    # Numeric hosts such as 2130706433 or 0x7f.0.0.1 resolve to IP addresses in many resolvers.
    last = host.rsplit(".", 1)[-1]
    if last.isdigit() or last.startswith("0x"):
        fail("bad_result")
    return parsed


def parse_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(n, int | float) and not isinstance(n, bool) for n in value):
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def parse_page(number: int, raw: object) -> OcrPage:
    if not isinstance(raw, dict):
        fail("bad_result")
    pruned = raw.get("prunedResult")
    blocks = pruned.get("parsing_res_list") if isinstance(pruned, dict) else None
    if not isinstance(blocks, list):
        fail("bad_result")
    lines: list[OcrLine] = []
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("block_content"), str):
            fail("bad_result")
        # The layout score is not a text confidence, so it is not reported as one.
        lines.append(OcrLine(block["block_content"], parse_bbox(block.get("block_bbox")), None))
    return OcrPage(page=number, text="\n".join(line.text for line in lines), lines=lines)


def parse_jsonl(body: bytes) -> list[OcrPage]:
    """Pages carry no number in the result, so they are numbered by their order across all lines."""
    try:
        # Split on \n only: str.splitlines would also break on U+2028 inside JSON strings.
        rows = [json.loads(line) for line in body.decode("utf-8").split("\n") if line.strip()]
    except (ValueError, RecursionError):
        fail("bad_result")
    if not rows:
        fail("bad_result")
    pages: list[OcrPage] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("errorCode", 0) != 0:
            fail("bad_result")
        result = row.get("result")
        items = result.get("layoutParsingResults") if isinstance(result, dict) else None
        if not isinstance(items, list):
            fail("bad_result")
        for item in items:
            pages.append(parse_page(len(pages) + 1, item))
    return pages


class PaddleOcrProvider:
    def __init__(self, token: str, client: httpx.Client) -> None:
        # Client-level credentials would also be sent to the result host.
        if "authorization" in client.headers or client.auth is not None:
            raise ValueError("Client must not carry Authorization")
        self._token = token
        self._client = client

    def __repr__(self) -> str:
        return "PaddleOcrProvider()"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("code") == JOB_EXPIRED:
            raise OcrJobExpired() from None
        if response.status_code in (401, 403):
            fail("auth_failed")
        if response.status_code == 429:
            fail("quota_exceeded")
        if response.status_code != 200 or not isinstance(body, dict) or body.get("code") != 0:
            fail("provider_error")
        return body

    def submit(self, path: Path) -> str:
        name = f"{uuid.uuid4()}.pdf"
        try:
            with path.open("rb") as handle:
                response = self._client.post(
                    API_URL,
                    headers=self._auth(),
                    data={"model": MODEL},
                    files={"file": (name, handle, "application/pdf")},
                    timeout=TIMEOUT,
                    follow_redirects=False,
                )
        except httpx.TimeoutException:
            fail("provider_timeout")
        except httpx.HTTPError:
            fail("provider_error")
        data = self._json(response).get("data")
        job_id = data.get("jobId") if isinstance(data, dict) else None
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            fail("provider_error")
        return job_id

    def status(self, job_id: str) -> OcrStatus:
        if not JOB_ID.fullmatch(job_id):
            fail("provider_error")
        try:
            response = self._client.get(
                f"{API_URL}/{job_id}", headers=self._auth(), timeout=TIMEOUT, follow_redirects=False
            )
        except httpx.TimeoutException:
            fail("provider_timeout")
        except httpx.HTTPError:
            fail("provider_error")
        data = self._json(response).get("data")
        state = data.get("state") if isinstance(data, dict) else None
        if not isinstance(data, dict) or state not in STATES:
            fail("provider_error")
        urls = data.get("resultUrl")
        url = urls.get("jsonUrl") if isinstance(urls, dict) else None
        return OcrStatus(job_id, cast(OcrState, state), url if isinstance(url, str) else None)

    def pages(self, status: OcrStatus) -> list[OcrPage]:
        url = check_result_url(status.result_url)
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        body = bytearray()
        try:
            # No token here: the result host is a separate storage service.
            with self._client.stream(
                "GET",
                url,
                headers={"Accept-Encoding": "identity"},
                auth=None,
                timeout=TIMEOUT,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    fail("provider_error")
                # httpx would honor stacked encodings despite the request, so a small body could inflate.
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    fail("bad_result")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_RESULT_BYTES:
                    fail("bad_result")
                for chunk in response.iter_raw(chunk_size=64 * 1024):
                    body += chunk
                    if len(body) > MAX_RESULT_BYTES:
                        fail("bad_result")
                    if time.monotonic() > deadline:
                        fail("provider_timeout")
        except httpx.TimeoutException:
            fail("provider_timeout")
        except httpx.HTTPError:
            fail("provider_error")
        return parse_jsonl(bytes(body))
