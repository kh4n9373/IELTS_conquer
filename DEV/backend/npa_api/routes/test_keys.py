from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from ..core.db import Conn, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, access, not_found
from ..exams.keys import normalize_answers, parse_pasted_key, upsert_items
from .tests import TEACHER, TEST_COLUMNS, blockers_of, lock_test, public

router = APIRouter(route_class=GuardedRoute)

MAX_QUESTIONS = 200
Number = Annotated[int, Field(ge=1, le=MAX_QUESTIONS)]
INVALID = HttpError(400, "invalid_input")


class KeyItemBody(Body):
    number: Number
    answers: Annotated[list[str], Field(max_length=100)]


class KeyItemsBody(Body):
    items: Annotated[list[KeyItemBody], Field(min_length=1, max_length=MAX_QUESTIONS)] | None = None
    pasted: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "KeyItemsBody":
        if (self.items is None) == (self.pasted is None):
            raise ValueError("send items or pasted, not both")
        return self


class ReviewBody(Body):
    numbers: Annotated[list[Number], Field(min_length=1, max_length=MAX_QUESTIONS)]


def parse_items(body: KeyItemsBody) -> dict[int, list[str]]:
    try:
        if body.pasted is not None:
            return parse_pasted_key(body.pasted)
        assert body.items is not None  # noqa: S101
        parsed = {i.number: normalize_answers(i.answers) for i in body.items}
        if len(parsed) != len(body.items):
            raise ValueError("repeated number")
        return parsed
    except ValueError as err:
        raise INVALID from err


def draft_version(tx: Conn, test_id: UUID, *, create: bool) -> dict[str, Any] | None:
    """The test row must already be locked: that lock is what makes get-or-create race free."""
    version = tx.execute(
        """select id, version, approved_at as "approvedAt" from keys.answer_key_versions
           where test_id = %s order by version desc limit 1""",
        (test_id,),
    ).fetchone()
    if version is None and create:
        version = tx.execute(
            """insert into keys.answer_key_versions (test_id, teacher_id, version)
               select id, teacher_id, 1 from tests where id = %s
               returning id, version, approved_at as "approvedAt" """,
            (test_id,),
        ).fetchone()
    return version


def question_numbers(tx: Conn, test_id: UUID) -> set[int]:
    rows = tx.execute("select number from questions where test_id = %s", (test_id,)).fetchall()
    return {r["number"] for r in rows}


def load_key(tx: Conn, test_id: UUID) -> dict[str, Any]:
    version = draft_version(tx, test_id, create=False)
    if version is None:
        return {"version": None, "approvedAt": None, "items": []}
    items = tx.execute(
        """select question_number as number, answers, origin, flagged,
                  reviewed_at is not null as reviewed, reviewed_at as "reviewedAt"
           from keys.answer_key_items where version_id = %s order by question_number""",
        (version["id"],),
    ).fetchall()
    return {"version": version["version"], "approvedAt": version["approvedAt"], "items": items}


@router.get("/api/tests/{id}/key")
@access(TEACHER)
def get_key(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        if tx.execute("select 1 from tests where id = %s", (id,)).fetchone() is None:
            raise not_found()
        return load_key(tx, id)


@router.put("/api/tests/{id}/key/items")
@access(TEACHER)
def put_key_items(id: UUID, body: KeyItemsBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    items = parse_items(body)
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        if not set(items) <= question_numbers(tx, id):
            raise INVALID
        version = draft_version(tx, id, create=True)
        assert version is not None  # noqa: S101
        upsert_items(tx, version["id"], items, "manual")
        # Numbers and counts only: admins can read the audit log but must not see answers.
        write_audit(
            tx,
            action="key.update",
            entity="test",
            entity_id=id,
            after={"questions": sorted(items), "count": len(items)},
        )
        return load_key(tx, id)


@router.post("/api/tests/{id}/key/review")
@access(TEACHER)
def review_key(id: UUID, body: ReviewBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    numbers = sorted(set(body.numbers))
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        version = draft_version(tx, id, create=False)
        if version is None:
            raise INVALID
        updated = tx.execute(
            """update keys.answer_key_items
               set reviewed_at = %s, reviewed_by = %s, flagged = false
               where version_id = %s and question_number = any(%s)""",
            (ctx.now(), identity.user_id, version["id"], numbers),
        ).rowcount
        if updated != len(numbers):
            raise INVALID  # rolls back: no partial review
        write_audit(
            tx,
            action="key.review",
            entity="test",
            entity_id=id,
            after={"questions": numbers, "count": len(numbers)},
        )
        return load_key(tx, id)


@router.post("/api/tests/{id}/publish")
@access(TEACHER)
def publish_test(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        lock_test(tx, id, draft=True)
        blockers = blockers_of(tx, id)
        if blockers:
            return JSONResponse({"error": "not_ready", "blockers": blockers}, status_code=409)
        version = draft_version(tx, id, create=False)
        assert version is not None  # noqa: S101  # a ready test has a full key
        tx.execute(
            "update keys.answer_key_versions set approved_at = %s, approved_by = %s where id = %s",
            (ctx.now(), identity.user_id, version["id"]),
        )
        row = tx.execute(
            f"update tests set status = 'published', published_at = %s where id = %s returning {TEST_COLUMNS}",  # noqa: E501, S608
            (ctx.now(), id),
        ).fetchone()
        count = tx.execute("select count(*) as n from questions where test_id = %s", (id,)).fetchone()
        write_audit(
            tx,
            action="test.publish",
            entity="test",
            entity_id=id,
            after={"keyVersion": version["version"], "questions": count["n"] if count else 0},
        )
        return row


@router.post("/api/tests/{id}/archive")
@access(TEACHER)
def archive_test(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, id)
        if test["status"] == "archived":
            return public(test)
        if test["status"] != "published":
            raise HttpError(409, "test_not_published")
        row = tx.execute(
            f"update tests set status = 'archived' where id = %s returning {TEST_COLUMNS}",  # noqa: S608
            (id,),
        ).fetchone()
        write_audit(tx, action="test.archive", entity="test", entity_id=id, before={"status": "published"})
        return row
