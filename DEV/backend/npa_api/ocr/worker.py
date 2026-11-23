"""OCR worker. Claims through definer functions, then writes under the owning teacher's identity."""

import logging
import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from uuid import UUID

import httpx

from ..core.db import Identity, Pool, open_pool, with_identity
from ..core.storage import FileStore
from ..server import S3_NAMES, create_store
from .paddle import PaddleOcrProvider
from .provider import ErrorCode, OcrError, OcrJobExpired, OcrPage, OcrProvider

log = logging.getLogger("npa.ocr")

LEASE = timedelta(minutes=15)
POLL_SECONDS = 5
# Stops polling early enough to settle the job before the lease runs out.
LEASE_MARGIN = timedelta(seconds=60)
MAX_POLLS = int((LEASE - LEASE_MARGIN).total_seconds() // POLL_SECONDS)
MAX_ATTEMPTS = 3
RETRY_SECONDS = {1: 60, 2: 300}
QUOTA_RETRY_SECONDS = 3600
IDLE_SECONDS = 5
REQUIRED = ("DATABASE_URL", *S3_NAMES, "BAIDU_OCR_ACCESS_TOKEN")

Clock = Callable[[], datetime]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class Claim:
    id: UUID
    asset_id: UUID
    teacher_id: UUID
    attempts: int
    provider_job_id: str | None

    @property
    def identity(self) -> Identity:
        return Identity(self.teacher_id, "teacher", self.teacher_id)


def set_provider_id(pool: Pool, job: Claim, provider_id: str | None) -> bool:
    """Commits on its own, so a crash after submit never causes a second paid submission."""
    with with_identity(pool, job.identity) as tx:
        updated = tx.execute(
            "update ocr_jobs set provider_job_id = %s where id = %s and state = 'running' and attempts = %s",
            (provider_id, job.id, job.attempts),
        ).rowcount
    return updated == 1


def submit(pool: Pool, store: FileStore, provider: OcrProvider, job: Claim, key: str) -> str | None:
    """Returns the stored provider id, or None when this claim no longer owns the job."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "source.pdf"
        try:
            store.get(key, path)
        except Exception:
            raise OcrError("storage_error") from None
        provider_id = provider.submit(path)
    return provider_id if set_provider_id(pool, job, provider_id) else None


def clean_pages(pages: list[OcrPage], page_count: int) -> list[tuple[int, str]]:
    # A partial result must not look like a finished scan.
    if len(pages) != page_count:
        raise OcrError("bad_result")
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for page in pages:
        if page.page < 1 or page.page > page_count or page.page in seen:
            raise OcrError("bad_result")
        seen.add(page.page)
        text = page.text.replace("\x00", "")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise OcrError("bad_result") from None
        out.append((page.page, text))
    return out


def store_result(pool: Pool, job: Claim, rows: list[tuple[int, str]], now: datetime) -> None:
    """The fence decides: a replaced PDF or a lost lease updates nothing and the result is dropped."""
    with with_identity(pool, job.identity) as tx:
        updated = tx.execute(
            """update ocr_jobs set state = 'done', finished_at = %s, error_code = null, lease_until = null
               where id = %s and state = 'running' and attempts = %s""",
            (now, job.id, job.attempts),
        ).rowcount
        if updated != 1:
            return
        tx.execute("delete from page_texts where asset_id = %s", (job.asset_id,))
        with tx.cursor() as cursor:
            cursor.executemany(
                "insert into page_texts (asset_id, teacher_id, page, text) values (%s, %s, %s, %s)",
                [(job.asset_id, job.teacher_id, page, text) for page, text in rows],
            )


def process(
    pool: Pool,
    store: FileStore,
    provider: OcrProvider,
    job: Claim,
    now: Clock,
    sleep: Sleep,
    lease_end: datetime,
) -> None:
    with with_identity(pool, job.identity) as tx:
        asset = tx.execute(
            "select storage_key, page_count from test_assets where id = %s", (job.asset_id,)
        ).fetchone()
    if asset is None:
        return
    provider_id = job.provider_job_id
    if provider_id is None:
        provider_id = submit(pool, store, provider, job, asset["storage_key"])
        if provider_id is None:
            return
    resubmitted = False
    deadline = lease_end - LEASE_MARGIN
    for _ in range(MAX_POLLS + 1):
        try:
            status = provider.status(provider_id)
        except OcrJobExpired:
            if resubmitted:
                raise
            resubmitted = True
            if not set_provider_id(pool, job, None):
                return
            provider_id = submit(pool, store, provider, job, asset["storage_key"])
            if provider_id is None:
                return
            continue
        if status.state == "failed":
            set_provider_id(pool, job, None)
            raise OcrError("provider_error")
        if status.state == "done":
            try:
                rows = clean_pages(provider.pages(status), asset["page_count"] or 0)
            except OcrError as err:
                if err.code == "bad_result":
                    set_provider_id(pool, job, None)
                raise
            store_result(pool, job, rows, now())
            return
        if now() + timedelta(seconds=POLL_SECONDS) > deadline:
            break
        sleep(POLL_SECONDS)
    raise OcrError("provider_timeout")


def settle(pool: Pool, job: Claim, code: ErrorCode, now: datetime) -> None:
    if job.attempts >= MAX_ATTEMPTS:
        state, retry_at = "failed", None
    else:
        delay = QUOTA_RETRY_SECONDS if code == "quota_exceeded" else RETRY_SECONDS[job.attempts]
        state, retry_at = "queued", now + timedelta(seconds=delay)
    with pool.connection() as conn:
        conn.execute(
            "select app.ocr_finish(%s, %s, %s, %s, %s, %s)",
            (job.id, job.attempts, state, code, retry_at, now),
        )
    log.warning("OCR job %s: %s", job.id, code)


def run_once(
    pool: Pool, store: FileStore, provider: OcrProvider, now: Clock, sleep: Sleep = time.sleep
) -> bool:
    """Handles at most one job. Returns False when nothing was claimable."""
    started = now()
    lease_end = started + LEASE
    with pool.connection() as conn:
        row = conn.execute("select * from app.ocr_claim(%s, %s)", (started, lease_end)).fetchone()
    if row is None:
        return False
    job = Claim(row["id"], row["asset_id"], row["teacher_id"], row["attempts"], row["provider_job_id"])
    code: ErrorCode | None = None
    try:
        process(pool, store, provider, job, now, sleep, lease_end)
    except OcrError as err:
        code = err.code
    except Exception:  # Exception text can hold URLs or OCR content, so it is dropped.
        code = "provider_error"
    if code is not None:
        settle(pool, job, code, now())
    return True


def quiet_http_logs() -> None:
    """httpx logs each request URL at INFO, and result URLs carry signatures."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    values = {name: os.environ.get(name, "") for name in REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        sys.exit(f"Missing required setting: {', '.join(missing)}")
    logging.basicConfig(level=logging.INFO)
    quiet_http_logs()
    stop = threading.Event()

    def request_stop(_signal: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    store = create_store()
    if store is None:
        sys.exit("Storage settings are incomplete")
    pool = open_pool(values["DATABASE_URL"], max_size=2)
    try:
        with httpx.Client(follow_redirects=False) as client:
            provider = PaddleOcrProvider(values["BAIDU_OCR_ACCESS_TOKEN"], client)
            while not stop.is_set():
                try:
                    worked = run_once(pool, store, provider, lambda: datetime.now(UTC))
                except Exception as err:
                    log.error("OCR worker loop error: %s", type(err).__name__)
                    worked = False
                if not worked:
                    stop.wait(IDLE_SECONDS)
    finally:
        pool.close()
