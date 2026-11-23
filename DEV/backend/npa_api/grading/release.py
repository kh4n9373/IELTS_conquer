"""Release policy and the shape of what a result may show. Pure: no I/O."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ReleaseFacts:
    mode: str
    policy: str
    release_at: datetime | None
    due_at: datetime
    max_attempts: int
    this_attempt_final: bool
    own_final_count: int
    active_recipients: int  # a removed student who never started is not counted
    recipients_done: int
    open_attempts: int


def is_released(f: ReleaseFacts, now: datetime) -> bool:
    if not f.this_attempt_final:
        return False
    if f.mode == "practice":
        return True
    # An exam with more attempts never shows answers before the next attempt is used up or due.
    if f.max_attempts > 1 and f.own_final_count < f.max_attempts and now < f.due_at:
        return False
    if f.policy == "scheduled":
        return f.release_at is not None and now >= f.release_at and f.open_attempts == 0
    group_done = f.active_recipients > 0 and f.recipients_done >= f.active_recipients
    return group_done or (now >= f.due_at and f.open_attempts == 0)


def facts_of(row: dict[str, Any]) -> ReleaseFacts:
    g, k = row["assignment"], row["facts"]
    release_at = g["releaseAt"]
    return ReleaseFacts(
        mode=row["mode"],
        policy=g["releasePolicy"],
        release_at=datetime.fromisoformat(release_at) if release_at else None,
        due_at=datetime.fromisoformat(g["dueAt"]),
        max_attempts=g["maxAttempts"],
        this_attempt_final=row["status"] != "in_progress",
        own_final_count=k["ownFinal"],
        active_recipients=k["activeRecipients"],
        recipients_done=k["recipientsDone"],
        open_attempts=k["openAttempts"],
    )


def pending(row: dict[str, Any], *, student: bool) -> dict[str, Any]:
    g = row["assignment"]
    status = row["status"]
    if student and status != "in_progress":
        status = "submitted"
    expected = g["releaseAt"] if g["releasePolicy"] == "scheduled" else g["dueAt"]
    at = datetime.fromisoformat(expected).astimezone(UTC).isoformat()
    done = row["finalizedAt"]
    submitted = datetime.fromisoformat(done).astimezone(UTC).isoformat() if done else None
    return {"released": False, "status": status, "expectedAt": at, "testTitle": row["testTitle"],
            "submittedAt": submitted}


def build_result(row: dict[str, Any], *, released: bool) -> dict[str, Any]:
    """Explicit allowlist. Only called for a graded attempt that may be shown."""
    run, items = row["run"], row["items"]
    by_number = {i["number"]: i for i in items}
    out: dict[str, Any] = {
        "released": released,
        "status": row["status"],
        "attemptNumber": row["number"],
        "maxAttempts": row["assignment"]["maxAttempts"],
        "testTitle": row["testTitle"],
        "submittedAt": datetime.fromisoformat(row["finalizedAt"]).astimezone(UTC).isoformat(),
        "correct": run["correct"],
        "total": run["total"],
        "percent": float(run["percent"]),
        "isTrial": row["isTrial"],
        "countsForProgress": row["number"] == 1 and row["mode"] == "exam" and not row["isTrial"],
        "sections": [
            {
                "position": s["position"],
                "title": s["title"],
                "correct": sum(1 for n in s["numbers"] if by_number.get(n, {}).get("correct")),
                "total": len(s["numbers"]),
            }
            for s in row["sections"]
        ],
        "items": [
            {"number": i["number"], "answer": i["given"], "correct": i["correct"], "key": i["accepted"]}
            for i in items
        ],
    }
    if run["band"] is not None:  # key absent, never null, for tests without a band
        out["band"] = run["band"]
        out["bandLabel"] = "estimated"
        out["bandTableVersion"] = run["bandTableVersion"]
    return out
