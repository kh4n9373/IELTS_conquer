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

from ..core.db import Conn, Identity, Pool, open_pool, with_identity
from ..core.storage import FileStore
from ..exams.key_import import KeyLine, apply_drafts, parse_key_lines, pdf_lines
from ..exams.pdf import PdfRejected
from ..exams.structure import StructureBody, write_structure
from ..server import S3_NAMES, create_store
from .gemini import DEFAULT_MODEL, GeminiParser, Parser
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
REQUIRED = ("DATABASE_URL", *S3_NAMES, "BAIDU_OCR_ACCESS_TOKEN", "GEMINI_API_KEY")

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


def store_result(
    pool: Pool,
    job: Claim,
    test_id: UUID,
    rows: list[tuple[int, str]],
    now: datetime,
    structure: StructureBody | None = None,
) -> None:
    """The fence decides: a replaced PDF or a lost lease updates nothing and the result is dropped."""
    with with_identity(pool, job.identity) as tx:
        # The same lock order as a file swap and `store_key_result`, so they cannot deadlock.
        test = tx.execute("select status from tests where id = %s for update", (test_id,)).fetchone()
        if test is None:
            return
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
        # Parsed questions only fill an empty draft: they never replace what the teacher wrote.
        empty = (
            tx.execute("select 1 from questions where test_id = %s limit 1", (test_id,)).fetchone() is None
        )
        if structure is not None and test["status"] == "draft" and empty:
            write_structure(tx, test_id, job.teacher_id, structure)


def saved_rows(pool: Pool, job: Claim, page_count: int) -> list[tuple[int, str]] | None:
    """Text kept from an earlier attempt, so a retry after a failed parse does not pay for OCR again."""
    with with_identity(pool, job.identity) as tx:
        found = tx.execute(
            "select page, text from page_texts where asset_id = %s order by page", (job.asset_id,)
        ).fetchall()
    return [(r["page"], r["text"]) for r in found] if found and len(found) == page_count else None


def save_rows(pool: Pool, job: Claim, rows: list[tuple[int, str]]) -> bool:
    """Commits the page text before the parse call. False when this claim no longer owns the job."""
    with with_identity(pool, job.identity) as tx:
        owned = tx.execute(
            "select 1 from ocr_jobs where id = %s and state = 'running' and attempts = %s for update",
            (job.id, job.attempts),
        ).fetchone()
        if owned is None:
            return False
        tx.execute("delete from page_texts where asset_id = %s", (job.asset_id,))
        with tx.cursor() as cursor:
            cursor.executemany(
                "insert into page_texts (asset_id, teacher_id, page, text) values (%s, %s, %s, %s)",
                [(job.asset_id, job.teacher_id, page, text) for page, text in rows],
            )
    return True


def local_rows(store: FileStore, key: str, page_count: int) -> list[tuple[int, str]]:
    """A PDF with a text layer is read here and never sent to OCR."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "source.pdf"
        try:
            store.get(key, path)
            lines = pdf_lines(path)
        except PdfRejected:
            raise OcrError("bad_result") from None
        except Exception:
            raise OcrError("storage_error") from None
    pages: dict[int, list[str]] = {}
    for line in lines:
        pages.setdefault(line.page or 0, []).append(line.text.replace("\x00", ""))
    return [(n, "\n".join(pages.get(n, []))) for n in range(1, page_count + 1)]


def key_lines(pages: list[OcrPage]) -> list[KeyLine]:
    out: list[KeyLine] = []
    for page in sorted(pages, key=lambda p: p.page):
        for line in page.lines:
            text = line.text.replace("\x00", "")
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                raise OcrError("bad_result") from None
            out.append(KeyLine(text, page.page, line.bbox, line.confidence, scanned=True))
    return out


def fail_locked(tx: Conn, job: Claim, now: datetime) -> None:
    """Final, not retried: a test that left draft does not come back."""
    tx.execute(
        """update ocr_jobs set state = 'failed', error_code = 'test_locked', finished_at = %s,
             lease_until = null
           where id = %s and state = 'running' and attempts = %s""",
        (now, job.id, job.attempts),
    )


def store_key_result(pool: Pool, job: Claim, test_id: UUID, lines: list[KeyLine], now: datetime) -> None:
    """Answers go to the draft key only, never to page_texts, which later feeds AI features."""
    drafts = parse_key_lines(lines)
    with with_identity(pool, job.identity) as tx:
        test = tx.execute("select status from tests where id = %s for update", (test_id,)).fetchone()
        if test is None:
            return
        if test["status"] != "draft":
            fail_locked(tx, job, now)
            return
        updated = tx.execute(
            """update ocr_jobs set state = 'done', finished_at = %s, error_code = null, lease_until = null
               where id = %s and state = 'running' and attempts = %s""",
            (now, job.id, job.attempts),
        ).rowcount
        if updated != 1:
            return
        counts, _ = apply_drafts(tx, test_id, drafts, "scan")
        if not (counts["filled"] or counts["flagged"] or counts["kept"]):
            # Final, not retried: another paid call on the same file would read the same nothing.
            tx.execute(
                "update ocr_jobs set state = 'failed', error_code = 'no_answers_found' where id = %s",
                (job.id,),
            )


def process(
    pool: Pool,
    store: FileStore,
    provider: OcrProvider,
    job: Claim,
    now: Clock,
    sleep: Sleep,
    lease_end: datetime,
    parser: Parser | None = None,
) -> None:
    with with_identity(pool, job.identity) as tx:
        asset = tx.execute(
            """select a.storage_key, a.page_count, a.kind, a.test_id, a.has_text_layer, t.status
               from test_assets a join tests t on t.id = a.test_id where a.id = %s""",
            (job.asset_id,),
        ).fetchone()
        if asset is not None and (
            asset["status"] == "archived" or (asset["kind"] == "key_source" and asset["status"] != "draft")
        ):
            # Stops before the paid provider call.
            fail_locked(tx, job, now())
            return
    if asset is None:
        return
    if asset["kind"] == "student_pdf" and parser is not None:
        count = asset["page_count"] or 0
        rows = saved_rows(pool, job, count)
        if rows is None and asset["has_text_layer"]:
            rows = local_rows(store, asset["storage_key"], count)
        if rows is not None:
            parse_and_store(pool, job, asset["test_id"], rows, parser, now)
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
                pages = provider.pages(status)
                rows = clean_pages(pages, asset["page_count"] or 0)
                lines = key_lines(pages) if asset["kind"] == "key_source" else []
            except OcrError as err:
                if err.code == "bad_result":
                    set_provider_id(pool, job, None)
                raise
            if asset["kind"] == "key_source":
                store_key_result(pool, job, asset["test_id"], lines, now())
            elif parser is not None:
                parse_and_store(pool, job, asset["test_id"], rows, parser, now)
            else:
                store_result(pool, job, asset["test_id"], rows, now())
            return
        if now() + timedelta(seconds=POLL_SECONDS) > deadline:
            break
        sleep(POLL_SECONDS)
    raise OcrError("provider_timeout")


def parse_and_store(
    pool: Pool, job: Claim, test_id: UUID, rows: list[tuple[int, str]], parser: Parser, now: Clock
) -> None:
    """The job stays running (the test shows as processing) until the questions are in."""
    if save_rows(pool, job, rows):
        store_result(pool, job, test_id, rows, now(), parser(rows))


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
    pool: Pool,
    store: FileStore,
    provider: OcrProvider,
    now: Clock,
    sleep: Sleep = time.sleep,
    parser: Parser | None = None,
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
        process(pool, store, provider, job, now, sleep, lease_end, parser)
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


def install_stop_handlers(stop: threading.Event) -> None:
    def request_stop(_signal: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def run_loop(
    pool: Pool,
    store: FileStore,
    provider: OcrProvider,
    stop: threading.Event,
    now: Clock,
    sleep: Sleep = time.sleep,
    parser: Parser | None = None,
) -> None:
    """Runs jobs until `stop` is set. A job in progress finishes first."""
    while not stop.is_set():
        try:
            worked = run_once(pool, store, provider, now, sleep, parser)
        except Exception as err:
            log.error("OCR worker loop error: %s", type(err).__name__)
            worked = False
        if not worked:
            stop.wait(IDLE_SECONDS)


def main() -> None:
    values = {name: os.environ.get(name, "") for name in REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        sys.exit(f"Missing required setting: {', '.join(missing)}")
    logging.basicConfig(level=logging.INFO)
    quiet_http_logs()
    stop = threading.Event()
    install_stop_handlers(stop)
    store = create_store()
    if store is None:
        sys.exit("Storage settings are incomplete")
    from ..explain import llm  # imported here: explain.worker imports this module
    from ..explain import worker as explain

    pool = open_pool(values["DATABASE_URL"], max_size=4)
    clock = lambda: datetime.now(UTC)  # noqa: E731
    try:
        with httpx.Client(follow_redirects=False) as client:
            provider = PaddleOcrProvider(values["BAIDU_OCR_ACCESS_TOKEN"], client)
            model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
            parser = GeminiParser(values["GEMINI_API_KEY"], client, model)
            explainer = threading.Thread(
                target=explain.run_loop, args=(pool, llm.from_env(client), stop, clock), daemon=True
            )
            explainer.start()
            run_loop(pool, store, provider, stop, clock, parser=parser)
            explainer.join(timeout=30)
    finally:
        pool.close()
