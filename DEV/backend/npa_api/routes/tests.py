from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import model_validator

from ..core.db import Conn, Role, with_identity, write_audit
from ..core.http import (
    Body,
    CtxDep,
    GuardedRoute,
    HttpError,
    IdentityDep,
    NameField,
    access,
    not_found,
)
from ..exams.readiness import readiness
from ..exams.structure import Minutes, StructureBody, write_structure

router = APIRouter(route_class=GuardedRoute)
TEACHER: tuple[Role, ...] = ("teacher",)

SourceType = Literal["self_authored", "licensed", "commercial", "other"]
TestType = Literal["ielts_standard", "course"]
Authoring = Literal["pdf", "manual"]

TEST_COLUMNS = """id, title, authoring, test_type as "testType", source_type as "sourceType", status,
  duration_minutes as "durationMinutes", published_at as "publishedAt", created_at as "createdAt"
"""
PATCH_COLUMNS = {
    "title": "title",
    "sourceType": "source_type",
    "testType": "test_type",
    "durationMinutes": "duration_minutes",
}
PATCH_COLUMNS_REVERSE = {column: name for name, column in PATCH_COLUMNS.items()}


class CreateTestBody(Body):
    title: NameField
    sourceType: SourceType  # noqa: N815
    testType: TestType  # noqa: N815
    durationMinutes: Minutes  # noqa: N815
    authoring: Authoring = "pdf"


class PatchTestBody(Body):
    title: NameField | None = None
    sourceType: SourceType | None = None  # noqa: N815
    testType: TestType | None = None  # noqa: N815
    durationMinutes: Minutes | None = None  # noqa: N815

    @model_validator(mode="after")
    def check(self) -> "PatchTestBody":
        given = self.model_fields_set
        if not given or any(getattr(self, name) is None for name in given):
            raise ValueError("invalid patch")
        return self


def lock_test(tx: Conn, test_id: UUID, *, draft: bool = False) -> dict[str, Any]:
    """Loads (and locks) a test visible to the caller. RLS turns another tenant's test into a 404."""
    row = tx.execute(
        f"select {TEST_COLUMNS}, teacher_id from tests where id = %s for update",  # noqa: S608
        (test_id,),
    ).fetchone()
    if row is None:
        raise not_found()
    if draft and row["status"] != "draft":
        raise HttpError(409, "test_locked")
    return row


def public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "teacher_id"}


def ranges_of(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in questions:
        last = out[-1] if out else None
        if (
            last is not None
            and last["to"] + 1 == q["number"]
            and last["questionType"] == q["questionType"]
            and last["wordLimit"] == q["wordLimit"]
            and last["answerGroup"] == q["answerGroup"]
        ):
            last["to"] = q["number"]
        else:
            out.append(
                {
                    "from": q["number"],
                    "to": q["number"],
                    "questionType": q["questionType"],
                    "wordLimit": q["wordLimit"],
                    "answerGroup": q["answerGroup"],
                }
            )
    return out


def load_structure(tx: Conn, test_id: UUID) -> list[dict[str, Any]]:
    sections = tx.execute(
        """select id, position, title, body, suggested_minutes as "suggestedMinutes",
                  passage_pages as "passagePages", question_pages as "questionPages",
                  units_primary as "unitsPrimary", units_secondary as "unitsSecondary"
           from sections where test_id = %s order by position""",
        (test_id,),
    ).fetchall()
    questions = tx.execute(
        """select section_id, number, question_type as "questionType", word_limit as "wordLimit",
                  answer_group as "answerGroup", prompt, options
           from questions where test_id = %s order by number""",
        (test_id,),
    ).fetchall()
    for section in sections:
        own = [q for q in questions if q["section_id"] == section["id"]]
        section["questions"] = [{k: v for k, v in q.items() if k != "section_id"} for q in own]
        section["ranges"] = ranges_of(own)
    return sections


def asset_summary(tx: Conn, test_id: UUID) -> dict[str, Any] | None:
    return tx.execute(
        """select page_count as "pageCount", has_text_layer as "hasTextLayer"
           from test_assets where test_id = %s and kind = 'student_pdf'""",
        (test_id,),
    ).fetchone()


# A row on an unapproved asset can never run, so it is not shown as pending. A finished attempt is
# still shown: that file may have been sent before approval existed.
VISIBLE_JOB = "(a.ocr_approved_at is not null or (j.state in ('done', 'failed') and j.attempts > 0))"


def ocr_summary(tx: Conn, test_id: UUID) -> dict[str, Any] | None:
    """State and fixed error code only: never OCR text, provider ids or lease data."""
    return tx.execute(
        f"""select j.state, j.error_code as "errorCode"
            from ocr_jobs j join test_assets a on a.id = j.asset_id
            where a.test_id = %s and a.kind = 'student_pdf' and {VISIBLE_JOB}
            order by j.created_at desc limit 1""",  # noqa: S608
        (test_id,),
    ).fetchone()


def detail(tx: Conn, test: dict[str, Any]) -> dict[str, Any]:
    sections = load_structure(tx, test["id"])
    return {**public(test), "studentPdf": asset_summary(tx, test["id"]), "sections": sections}


@router.get("/api/tests")
@access(TEACHER)
def list_tests(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        # `processing`: the PDF's OCR and question parsing have not finished yet.
        return tx.execute(
            f"""select {TEST_COLUMNS}, exists (
                  select 1 from ocr_jobs j join test_assets a on a.id = j.asset_id
                  where a.test_id = tests.id and a.kind = 'student_pdf' and j.state in ('queued', 'running')
                    and a.ocr_approved_at is not null) as processing
                from tests order by created_at desc"""  # noqa: S608
        ).fetchall()


@router.post("/api/tests", status_code=201)
@access(TEACHER)
def create_test(body: CreateTestBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute(
            f"""insert into tests (teacher_id, title, test_type, source_type, duration_minutes, authoring)
                values (%s, %s, %s, %s, %s, %s) returning {TEST_COLUMNS}""",  # noqa: S608
            (
                identity.user_id,
                body.title,
                body.testType,
                body.sourceType,
                body.durationMinutes,
                body.authoring,
            ),
        ).fetchone()
        assert row is not None  # noqa: S101
        write_audit(
            tx,
            action="test.create",
            entity="test",
            entity_id=row["id"],
            after={
                "title": body.title,
                "testType": body.testType,
                "sourceType": body.sourceType,
                "authoring": body.authoring,
            },
        )
        return row


@router.get("/api/tests/{id}")
@access(TEACHER)
def get_test(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = tx.execute(
            f"select {TEST_COLUMNS}, teacher_id from tests where id = %s",  # noqa: S608
            (id,),
        ).fetchone()
        if test is None:
            raise not_found()
        return {**detail(tx, test), "ocr": ocr_summary(tx, id)}


@router.patch("/api/tests/{id}")
@access(TEACHER)
def update_test(id: UUID, body: PatchTestBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    changes = {PATCH_COLUMNS[name]: getattr(body, name) for name in body.model_fields_set}
    with with_identity(ctx.pool, identity) as tx:
        before = lock_test(tx, id, draft=True)
        # Column names come from PATCH_COLUMNS only.
        assignments = ", ".join(f"{column} = %s" for column in changes)
        row = tx.execute(
            f"update tests set {assignments} where id = %s returning {TEST_COLUMNS}",  # noqa: S608
            (*changes.values(), id),
        ).fetchone()
        write_audit(
            tx,
            action="test.update",
            entity="test",
            entity_id=id,
            before={PATCH_COLUMNS_REVERSE[c]: before[PATCH_COLUMNS_REVERSE[c]] for c in changes},
            after={PATCH_COLUMNS_REVERSE[c]: v for c, v in changes.items()},
        )
        return row


@router.put("/api/tests/{id}/structure")
@access(TEACHER)
def put_structure(id: UUID, body: StructureBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        # The test is locked first: inserting before this would let unique-key errors
        # reveal another tenant's structure.
        test = lock_test(tx, id, draft=True)
        counts = write_structure(tx, id, test["teacher_id"], body)
        write_audit(tx, action="test.structure", entity="test", entity_id=id, after=counts)
        return detail(tx, test)


def blockers_of(tx: Conn, test_id: UUID) -> list[str]:
    """Publish blockers for a test, read inside the caller's transaction."""
    test = tx.execute(
        "select test_type, duration_minutes, authoring from tests where id = %s", (test_id,)
    ).fetchone()
    if test is None:
        raise not_found()
    assets = tx.execute("select kind, page_count from test_assets where test_id = %s", (test_id,)).fetchall()
    sections = tx.execute(
        "select passage_pages, question_pages from sections where test_id = %s", (test_id,)
    ).fetchall()
    questions = tx.execute(
        "select number, question_type, prompt, options from questions where test_id = %s", (test_id,)
    ).fetchall()
    version = tx.execute(
        "select id from keys.answer_key_versions where test_id = %s order by version desc limit 1", (test_id,)
    ).fetchone()
    items = (
        tx.execute(
            """select question_number, answers, flagged, reviewed_at
               from keys.answer_key_items where version_id = %s""",
            (version["id"],),
        ).fetchall()
        if version is not None
        else []
    )
    return readiness(test, assets, sections, questions, items)


@router.get("/api/tests/{id}/readiness")
@access(TEACHER)
def get_readiness(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        blockers = blockers_of(tx, id)
        return {"ready": not blockers, "blockers": blockers}
