"""Settles an attempt: locks the row, grades it and stores the result in one transaction."""

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from ..core.http import Ctx
from .band import BAND_TABLE_VERSION, band_for
from .rules import GradableQuestion, grade

log = logging.getLogger("npa.grading")
GRACE_SECONDS = 30
SWEEP_SECONDS = 5


def _grade_locked(data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if data["keyVersionId"] is None:
        raise RuntimeError("no approved key")
    questions = [
        GradableQuestion(q["number"], q["type"], q["wordLimit"], q["group"], tuple(q["accepted"]))
        for q in data["questions"]
    ]
    given = {a["number"]: a["value"] for a in data["answers"] if a["value"] is not None}
    marks = grade(questions, given, data["rules"])
    accepted = {q.number: list(q.accepted) for q in questions}
    correct = sum(m.correct for m in marks)
    total = len(marks)
    band = band_for(correct, total, data["testType"])
    run = {
        "keyVersionId": data["keyVersionId"],
        "rulesVersion": data["rules"],
        "testType": data["testType"],
        "correct": correct,
        "total": total,
        "percent": round(correct * 100 / total, 2) if total else 0,
        "band": band,
        "bandTableVersion": BAND_TABLE_VERSION if band else None,
    }
    items = [
        {"number": m.number, "given": m.given, "correct": m.correct, "reason": m.reason,
         "accepted": accepted[m.number]}
        for m in marks
    ]
    return run, items


def settle_attempt(ctx: Ctx, attempt_id: UUID, *, source: Literal["student", "auto"]) -> bool:
    """True when this call settled the attempt. An already settled attempt is left alone.

    `auto` only settles once the deadline and the grace period have passed.
    """
    now = ctx.now()
    with ctx.pool.connection() as conn:
        row = conn.execute("select app.attempt_lock_for_grading(%s) as data", (attempt_id,)).fetchone()
        assert row is not None  # noqa: S101
        data = row["data"]
        if data is None:
            return False
        if source == "auto":
            deadline = datetime.fromisoformat(data["deadlineAt"])
            if now <= deadline + timedelta(seconds=GRACE_SECONDS):
                return False
        run, items = _grade_locked(data)
        conn.execute(
            "select app.attempt_store_grade(%s, %s, %s, %s::jsonb, %s::jsonb)",
            (attempt_id, "submitted" if source == "student" else "auto_submitted", now,
             json.dumps(run), json.dumps(items)),
        )
    return True


def settle_if_due(ctx: Ctx, attempt_id: UUID) -> None:
    """Request-time check, so an attempt settles even when no sweep ran. Never fails the request."""
    try:
        settle_attempt(ctx, attempt_id, source="auto")
    except Exception as err:
        # Only the id and the error class: exception text may contain answers.
        log.error("settle failed attempt=%s error=%s", attempt_id, type(err).__name__)


def sweep_once(ctx: Ctx, limit: int = 50) -> int:
    cutoff = ctx.now() - timedelta(seconds=GRACE_SECONDS)
    with ctx.pool.connection() as conn:
        due = conn.execute("select app.attempt_due(%s, %s) as id", (cutoff, limit)).fetchall()
    ids = [r["id"] for r in due]
    settled = 0
    for attempt_id in ids:
        try:
            settled += settle_attempt(ctx, attempt_id, source="auto")
        except Exception as err:
            log.error("sweep failed attempt=%s error=%s", attempt_id, type(err).__name__)
    return settled


class SweepThread(threading.Thread):
    def __init__(self, ctx: Ctx, interval: float = SWEEP_SECONDS) -> None:
        super().__init__(name="attempt-sweep", daemon=True)
        self._ctx = ctx
        self._interval = interval
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                sweep_once(self._ctx)
            except Exception as err:
                log.error("sweep round failed error=%s", type(err).__name__)

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout=30)
