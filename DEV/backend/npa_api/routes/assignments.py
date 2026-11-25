from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import AfterValidator, Field, model_validator

from ..core.db import Conn, Role, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, access, not_found
from ..grading import stats
from .classes import lock_active_class

router = APIRouter(route_class=GuardedRoute)
TEACHER: tuple[Role, ...] = ("teacher",)
STUDENT: tuple[Role, ...] = ("student",)

Mode = Literal["exam", "practice"]
Policy = Literal["after_submit", "after_last", "scheduled"]
Id = Annotated[UUID, Field(strict=False)]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("time zone required")
    return value


Instant = Annotated[datetime, Field(strict=False), AfterValidator(aware)]

SELECT = """select g.id, g.test_id as "testId", t.title as "testTitle", g.class_id as "classId",
  c.name as "className", g.opens_at as "opensAt", g.due_at as "dueAt", g.max_attempts as "maxAttempts",
  g.mode, g.release_policy as "releasePolicy", g.release_at as "releaseAt", g.cancelled_at as "cancelledAt",
  g.created_at as "createdAt", (g.is_trial or c.is_trial) as "isTrial",
  (select count(*) from assignment_recipients r where r.assignment_id = g.id)::int as "recipientCount"
  from assignments g join tests t on t.id = g.test_id join classes c on c.id = g.class_id"""


class CreateBody(Body):
    testId: Id  # noqa: N815
    classId: Id  # noqa: N815
    studentIds: Annotated[list[Id], Field(max_length=500)] | None = None  # noqa: N815
    opensAt: Instant  # noqa: N815
    dueAt: Instant  # noqa: N815
    maxAttempts: Annotated[int, Field(ge=1, le=5)]  # noqa: N815
    mode: Mode
    releasePolicy: Policy | None = None  # noqa: N815
    releaseAt: Instant | None = None  # noqa: N815


class PatchBody(Body):
    opensAt: Instant | None = None  # noqa: N815
    dueAt: Instant | None = None  # noqa: N815
    maxAttempts: Annotated[int, Field(ge=1, le=5)] | None = None  # noqa: N815
    mode: Mode | None = None
    releasePolicy: Policy | None = None  # noqa: N815
    releaseAt: Instant | None = None  # noqa: N815

    @model_validator(mode="after")
    def check(self) -> "PatchBody":
        given = self.model_fields_set
        if not given or any(getattr(self, name) is None for name in given - {"releaseAt"}):
            raise ValueError("invalid patch")
        return self


def default_release(max_attempts: int) -> str:
    return "after_submit" if max_attempts == 1 else "after_last"


def check_rules(
    *,
    opens: datetime,
    due: datetime,
    attempts: int,
    mode: str,
    policy: str,
    release_at: datetime | None,
    recipients: int,
) -> None:
    if due <= opens or (policy == "scheduled") != (release_at is not None):
        raise HttpError(400, "invalid_input")
    if policy == "after_submit" and mode == "exam" and attempts > 1:
        raise HttpError(409, "invalid_release")
    if release_at is not None and release_at < due and (attempts > 1 or recipients > 1):
        raise HttpError(409, "invalid_release")


def status_of(row: dict[str, Any], now: datetime) -> str:
    if row["cancelledAt"] is not None:
        return "cancelled"
    if now < row["opensAt"]:
        return "upcoming"
    return "open" if now < row["dueAt"] else "closed"


def shown(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {**row, "status": status_of(row, now)}


def load(tx: Conn, assignment_id: UUID, now: datetime) -> dict[str, Any]:
    row = tx.execute(f"{SELECT} where g.id = %s", (assignment_id,)).fetchone()  # noqa: S608
    if row is None:
        raise not_found()
    return shown(row, now)


def lock_editable(tx: Conn, assignment_id: UUID) -> dict[str, Any]:
    row = tx.execute("select * from assignments where id = %s for update", (assignment_id,)).fetchone()
    if row is None:
        raise not_found()
    if row["cancelled_at"] is not None:
        raise HttpError(409, "assignment_cancelled")
    if tx.execute("select 1 from attempts where assignment_id = %s limit 1", (assignment_id,)).fetchone():
        raise HttpError(409, "assignment_started")
    return row


@router.get("/api/assignments")
@access(TEACHER)
def list_assignments(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        rows = tx.execute(f"{SELECT} order by g.created_at desc").fetchall()  # noqa: S608
        return [shown(r, ctx.now()) for r in rows]


@router.post("/api/assignments", status_code=201)
@access(TEACHER)
def create_assignment(body: CreateBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = tx.execute("select status from tests where id = %s", (body.testId,)).fetchone()
        if test is None:
            raise not_found()
        lock_active_class(tx, body.classId)
        if test["status"] != "published":
            raise HttpError(409, "test_not_published")
        members = {
            r["student_id"]
            for r in tx.execute(
                "select student_id from class_members where class_id = %s and removed_at is null",
                (body.classId,),
            ).fetchall()
        }
        chosen = members if body.studentIds is None else set(body.studentIds)
        if not chosen:
            raise HttpError(400, "no_students")
        if not chosen <= members:
            raise HttpError(400, "invalid_student")
        policy = body.releasePolicy or default_release(body.maxAttempts)
        check_rules(
            opens=body.opensAt,
            due=body.dueAt,
            attempts=body.maxAttempts,
            mode=body.mode,
            policy=policy,
            release_at=body.releaseAt,
            recipients=len(chosen),
        )
        created = tx.execute(
            """insert into assignments
                 (teacher_id, test_id, class_id, opens_at, due_at, max_attempts, mode,
                  release_policy, release_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (
                identity.user_id,
                body.testId,
                body.classId,
                body.opensAt,
                body.dueAt,
                body.maxAttempts,
                body.mode,
                policy,
                body.releaseAt,
            ),
        ).fetchone()
        assert created is not None  # noqa: S101
        for student in sorted(chosen, key=str):
            tx.execute(
                "insert into assignment_recipients (assignment_id, teacher_id, student_id)"
                " values (%s, %s, %s)",
                (created["id"], identity.user_id, student),
            )
        # Explanations are written once per test, in the background; a queued job is not duplicated.
        tx.execute(
            "insert into explain_jobs (test_id, teacher_id) values (%s, %s)"
            " on conflict (test_id) where state in ('queued', 'running') do nothing",
            (body.testId, identity.user_id),
        )
        write_audit(
            tx,
            action="assignment.create",
            entity="assignment",
            entity_id=created["id"],
            after={"testId": str(body.testId), "classId": str(body.classId), "recipients": len(chosen)},
        )
        return load(tx, created["id"], ctx.now())


@router.get("/api/assignments/{id}")
@access(TEACHER)
def get_assignment(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        out = load(tx, id, ctx.now())
        rows = tx.execute(
            """select u.id, u.display_name as name,
                      (select count(*) from attempts a where a.assignment_id = r.assignment_id
                        and a.student_id = r.student_id)::int as attempts,
                      exists (select 1 from attempts a where a.assignment_id = r.assignment_id
                               and a.student_id = r.student_id and a.status = 'in_progress') as open,
                      (select count(*) from late_answers l join attempts a on a.id = l.attempt_id
                        where a.assignment_id = r.assignment_id
                        and a.student_id = r.student_id)::int as "lateCount",
                      coalesce((select jsonb_agg(jsonb_build_object('number', l.question_number,
                                'value', l.value) order by l.received_at, l.question_number)
                                from late_answers l join attempts a on a.id = l.attempt_id
                                where a.assignment_id = r.assignment_id and a.student_id = r.student_id),
                               '[]'::jsonb) as late
               from assignment_recipients r join users u on u.id = r.student_id
               where r.assignment_id = %s order by u.display_name, u.id""",
            (id,),
        ).fetchall()
        students = [
            {
                "id": r["id"],
                "name": r["name"],
                "state": "in_progress" if r["open"] else "submitted" if r["attempts"] else "not_started",
                "attempts": r["attempts"],
                "lateCount": r["lateCount"],
                "late": r["late"],
            }
            for r in rows
        ]
        return out | {"students": students}


@router.patch("/api/assignments/{id}")
@access(TEACHER)
def update_assignment(id: UUID, body: PatchBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        before = lock_editable(tx, id)
        given = body.model_fields_set
        merged: dict[str, Any] = {
            "opens_at": body.opensAt if "opensAt" in given else before["opens_at"],
            "due_at": body.dueAt if "dueAt" in given else before["due_at"],
            "max_attempts": body.maxAttempts if "maxAttempts" in given else before["max_attempts"],
            "mode": body.mode if "mode" in given else before["mode"],
            "release_policy": body.releasePolicy if "releasePolicy" in given else before["release_policy"],
            "release_at": body.releaseAt if "releaseAt" in given else before["release_at"],
        }
        if merged["release_policy"] != "scheduled" and "releaseAt" not in given:
            merged["release_at"] = None
        count = tx.execute(
            "select count(*) as n from assignment_recipients where assignment_id = %s", (id,)
        ).fetchone()
        assert count is not None  # noqa: S101
        check_rules(
            opens=merged["opens_at"],
            due=merged["due_at"],
            attempts=merged["max_attempts"],
            mode=merged["mode"],
            policy=merged["release_policy"],
            release_at=merged["release_at"],
            recipients=count["n"],
        )
        tx.execute(
            """update assignments set opens_at = %(opens_at)s, due_at = %(due_at)s,
                 max_attempts = %(max_attempts)s, mode = %(mode)s,
                 release_policy = %(release_policy)s, release_at = %(release_at)s where id = %(id)s""",
            merged | {"id": id},
        )
        write_audit(
            tx,
            action="assignment.update",
            entity="assignment",
            entity_id=id,
            before={k: str(before[k]) for k in merged},
            after={k: str(v) for k, v in merged.items()},
        )
        return load(tx, id, ctx.now())


@router.post("/api/assignments/{id}/cancel", status_code=204)
@access(TEACHER)
def cancel_assignment(id: UUID, ctx: CtxDep, identity: IdentityDep) -> None:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute("select cancelled_at from assignments where id = %s for update", (id,)).fetchone()
        if row is None:
            raise not_found()
        if row["cancelled_at"] is not None:
            return
        lock_editable(tx, id)
        tx.execute("update assignments set cancelled_at = %s where id = %s", (ctx.now(), id))
        write_audit(tx, action="assignment.cancel", entity="assignment", entity_id=id)


@router.get("/api/assignments/{id}/stats")
@access(TEACHER)
def assignment_stats(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    """Class statistics from each student's latest graded attempt."""
    with with_identity(ctx.pool, identity) as tx:
        head = tx.execute(
            """select g.test_id, t.duration_minutes from assignments g join tests t on t.id = g.test_id
               where g.id = %s""",
            (id,),
        ).fetchone()
        if head is None:
            raise not_found()
        qrows = tx.execute(
            """select q.number, q.question_type, q.prompt, q.options, s.title, s.units_primary,
                      e.correct as explanation, e.source
               from questions q join sections s on s.id = q.section_id
               left join keys.question_explanations e on e.question_id = q.id
               where q.test_id = %s order by q.number""",
            (head["test_id"],),
        ).fetchall()
        attempts = tx.execute(
            """select distinct on (a.student_id) a.id, a.student_id, u.display_name, a.started_at,
                      coalesce(a.finalized_at, a.deadline_at) as ended_at,
                      r.id as run_id, r.correct, r.total, r.percent, r.band
               from attempts a join users u on u.id = a.student_id
               join lateral (select * from keys.grading_runs x where x.attempt_id = a.id
                             order by x.run desc limit 1) r on true
               where a.assignment_id = %s and a.status <> 'in_progress'
               order by a.student_id, a.number desc""",
            (id,),
        ).fetchall()
        runs = [a["run_id"] for a in attempts]
        ids = [a["id"] for a in attempts]
        items = tx.execute(
            """select run_id, question_number, given, correct, accepted from keys.grading_items
               where run_id = any(%s)""",
            (runs,),
        ).fetchall()
        saves = tx.execute(
            """select attempt_id, question_number, saved_at, marked_for_review from attempt_answers
               where attempt_id = any(%s)""",
            (ids,),
        ).fetchall()
    questions = [
        stats.Question(
            number=q["number"],
            type=q["question_type"],
            prompt=q["prompt"],
            options=list(q["options"]),
            topic=(q["units_primary"][0] if q["units_primary"] else q["title"]).strip() or q["title"],
            explanation=q["explanation"],
            explanation_source=q["source"],
        )
        for q in qrows
    ]
    graded = [
        stats.Graded(
            student_id=str(a["student_id"]),
            name=a["display_name"],
            attempt_id=str(a["id"]),
            started_at=a["started_at"],
            ended_at=a["ended_at"],
            correct=a["correct"],
            total=a["total"],
            percent=float(a["percent"]),
            band=a["band"],
            items={
                i["question_number"]: stats.Item(i["given"], i["correct"], list(i["accepted"]))
                for i in items
                if i["run_id"] == a["run_id"]
            },
            saves={
                s["question_number"]: stats.Save(s["saved_at"], s["marked_for_review"])
                for s in saves
                if s["attempt_id"] == a["id"]
            },
        )
        for a in attempts
    ]
    return stats.build_stats(questions, graded, head["duration_minutes"] * 60)


@router.get("/api/my/assignments")
@access(STUDENT)
def my_assignments(ctx: CtxDep, identity: IdentityDep) -> Any:
    # Allowlist: no test id, key, release setting or other recipient ever reaches a student.
    with with_identity(ctx.pool, identity) as tx:
        rows = tx.execute(
            """select g.id, c.name as "className", g.opens_at as "opensAt", g.due_at as "dueAt",
                      g.max_attempts as "maxAttempts", g.mode, g.cancelled_at as "cancelledAt",
                      (select count(*) from attempts a
                        where a.assignment_id = g.id and a.student_id = app.user_id())::int as "attemptsUsed",
                      exists (select 1 from attempts a where a.assignment_id = g.id
                               and a.student_id = app.user_id() and a.status = 'in_progress') as "inProgress",
                      (select a.id from attempts a where a.assignment_id = g.id
                        and a.student_id = app.user_id() order by a.number desc limit 1) as "latestAttemptId"
               from assignments g left join classes c on c.id = g.class_id
               order by g.opens_at desc"""
        ).fetchall()
        titles = {t["assignment_id"]: t for t in tx.execute("select * from app.my_assignment_titles()")}
        out = []
        for r in rows:
            status = status_of(r, ctx.now())
            out.append(
                {k: v for k, v in r.items() if k != "cancelledAt"}
                | {
                    "status": status,
                    "testTitle": titles[r["id"]]["title"] if r["id"] in titles else None,
                    "durationMinutes": titles[r["id"]]["duration_minutes"] if r["id"] in titles else None,
                }
            )
        return out
