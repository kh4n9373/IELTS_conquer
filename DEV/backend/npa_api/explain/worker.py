"""Explanation worker: one job per test, run inside the OCR worker process.

Claims through definer functions, reads and writes under the owning teacher's identity.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from ..core.db import Conn, Identity, Pool, with_identity
from ..ocr.worker import IDLE_SECONDS, MAX_ATTEMPTS, QUOTA_RETRY_SECONDS, RETRY_SECONDS, Clock
from .batch import REPLY_SCHEMA, Explanation, Item, batches, build_prompt, content_hash, parse_reply
from .llm import Llm, LlmError

log = logging.getLogger("npa.explain")

LEASE = timedelta(minutes=10)
PARALLEL = 3


@dataclass(frozen=True)
class Claim:
    id: UUID
    test_id: UUID
    teacher_id: UUID
    attempts: int

    @property
    def identity(self) -> Identity:
        return Identity(self.teacher_id, "teacher", self.teacher_id)


def load_items(tx: Conn, test_id: UUID) -> list[Item]:
    """Questions with text. Passage: the typed section body, else the OCR text of its passage pages."""
    found = tx.execute(
        """select q.id, q.number, q.question_type, q.prompt, q.options,
                  coalesce(nullif(s.body, ''), (
                    select string_agg(p.text, E'\n' order by p.page) from page_texts p
                    join test_assets a on a.id = p.asset_id and a.kind = 'student_pdf'
                    where a.test_id = q.test_id and p.page = any(s.passage_pages)), '') as passage,
                  coalesce((select i.answers from keys.answer_key_items i
                            where i.question_number = q.number and i.version_id = (
                              select v.id from keys.answer_key_versions v
                              where v.test_id = q.test_id and v.approved_at is not null
                              order by v.version desc limit 1)), '{}') as accepted
           from questions q join sections s on s.id = q.section_id
           where q.test_id = %s and btrim(q.prompt) <> ''
           order by q.number""",
        (test_id,),
    ).fetchall()
    return [
        Item(
            r["id"],
            r["number"],
            r["question_type"],
            r["prompt"],
            list(r["options"]),
            list(r["accepted"]),
            r["passage"],
        )
        for r in found
        if r["accepted"]
    ]


def stale(tx: Conn, items: list[Item]) -> list[Item]:
    """Items with no explanation, or an AI one written for different content. Teacher rows stay."""
    stored = {
        r["question_id"]: r
        for r in tx.execute(
            "select question_id, content_hash, source from keys.question_explanations"
            " where question_id = any(%s)",
            ([i.question_id for i in items],),
        ).fetchall()
    }
    out = []
    for i in items:
        row = stored.get(i.question_id)
        if row is None or (row["source"] == "ai" and row["content_hash"] != content_hash(i)):
            out.append(i)
    return out


def _explain(llm: Llm, batch: list[Item]) -> list[Explanation] | LlmError:
    try:
        return parse_reply(batch, llm(build_prompt(batch), REPLY_SCHEMA))
    except LlmError as err:
        return err
    except Exception:  # Exception text can hold reply content, so it is dropped.
        return LlmError("provider_error")


def _finish(pool: Pool, job: Claim, items: list[Item], found: list[Explanation], now: datetime) -> None:
    hashes = {i.question_id: content_hash(i) for i in items}
    with with_identity(pool, job.identity) as tx:
        owned = tx.execute(
            """update explain_jobs set state = 'done', finished_at = %s, error_code = null, lease_until = null
               where id = %s and state = 'running' and attempts = %s""",
            (now, job.id, job.attempts),
        ).rowcount
        if owned != 1:
            return  # lost the lease: another claim writes the result
        with tx.cursor() as cursor:
            cursor.executemany(
                """insert into keys.question_explanations
                     (question_id, test_id, teacher_id, content_hash, correct, options, source)
                   values (%s, %s, %s, %s, %s, %s, 'ai')
                   on conflict (question_id) do update
                     set content_hash = excluded.content_hash, correct = excluded.correct,
                         options = excluded.options, created_at = now()
                     where keys.question_explanations.source = 'ai'""",
                [
                    (
                        e.question_id,
                        job.test_id,
                        job.teacher_id,
                        hashes[e.question_id],
                        e.correct,
                        json.dumps(e.options, ensure_ascii=False),
                    )
                    for e in found
                ],
            )


def settle(pool: Pool, job: Claim, code: str, now: datetime) -> None:
    if job.attempts >= MAX_ATTEMPTS:
        state, retry_at = "failed", None
    else:
        delay = QUOTA_RETRY_SECONDS if code == "quota_exceeded" else RETRY_SECONDS[job.attempts]
        state, retry_at = "queued", now + timedelta(seconds=delay)
    with pool.connection() as conn:
        conn.execute(
            "select app.explain_finish(%s, %s, %s, %s, %s, %s)",
            (job.id, job.attempts, state, code, retry_at, now),
        )
    log.warning("explain job %s: %s", job.id, code)


def run_once(pool: Pool, llm: Llm, now: Clock) -> bool:
    """Handles at most one job. Returns False when nothing was claimable."""
    started = now()
    with pool.connection() as conn:
        row = conn.execute("select * from app.explain_claim(%s, %s)", (started, started + LEASE)).fetchone()
    if row is None:
        return False
    job = Claim(row["id"], row["test_id"], row["teacher_id"], row["attempts"])
    with with_identity(pool, job.identity) as tx:
        todo = stale(tx, load_items(tx, job.test_id))
    groups = batches(todo)
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool_threads:
        results = list(pool_threads.map(lambda b: _explain(llm, b), groups))
    found = [e for r in results if not isinstance(r, LlmError) for e in r]
    errors = [r for r in results if isinstance(r, LlmError)]
    if groups and len(errors) == len(groups):
        settle(pool, job, errors[-1].code, now())
    else:
        _finish(pool, job, todo, found, now())
    return True


def run_loop(pool: Pool, llm: Llm, stop: threading.Event, now: Clock) -> None:
    """Runs jobs until `stop` is set."""
    while not stop.is_set():
        try:
            worked = run_once(pool, llm, now)
        except Exception as err:
            log.error("explain worker loop error: %s", type(err).__name__)
            worked = False
        if not worked:
            stop.wait(IDLE_SECONDS)
