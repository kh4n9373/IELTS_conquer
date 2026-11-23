from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field, model_validator

from ..core.db import Conn, Role, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, NameField, access, not_found
from ..exams.readiness import readiness
from ..exams.types import QUESTION_GROUPS, QuestionType

router = APIRouter(route_class=GuardedRoute)
TEACHER: tuple[Role, ...] = ("teacher",)

SourceType = Literal["self_authored", "licensed", "commercial", "other"]
TestType = Literal["ielts_standard", "course"]
Minutes = Annotated[int, Field(ge=1, le=300)]
Pages = Annotated[list[Annotated[int, Field(ge=1, le=5000)]], Field(max_length=50)]
Units = Annotated[list[NameField], Field(max_length=50)]
MAX_SECTIONS = 10
MAX_QUESTIONS = 200

TEST_COLUMNS = """id, title, test_type as "testType", source_type as "sourceType", status,
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


class RangeBody(Body):
    from_: Annotated[int, Field(ge=1, le=MAX_QUESTIONS, alias="from")]
    to: Annotated[int, Field(ge=1, le=MAX_QUESTIONS)]
    questionType: QuestionType  # noqa: N815
    wordLimit: Annotated[int, Field(ge=1, le=20)] | None = None  # noqa: N815

    @model_validator(mode="after")
    def check(self) -> "RangeBody":
        if self.from_ > self.to:
            raise ValueError("invalid range")
        if self.wordLimit is not None and QUESTION_GROUPS[self.questionType] != "completion":
            raise ValueError("word limit applies to completion types only")
        return self


class SectionBody(Body):
    title: NameField
    suggestedMinutes: Minutes | None = None  # noqa: N815
    passagePages: Pages = []  # noqa: N815
    questionPages: Pages = []  # noqa: N815
    unitsPrimary: Units = []  # noqa: N815
    unitsSecondary: Units = []  # noqa: N815
    ranges: Annotated[list[RangeBody], Field(max_length=50)] = []


class StructureBody(Body):
    sections: Annotated[list[SectionBody], Field(max_length=MAX_SECTIONS)]


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
        ):
            last["to"] = q["number"]
        else:
            out.append(
                {
                    "from": q["number"],
                    "to": q["number"],
                    "questionType": q["questionType"],
                    "wordLimit": q["wordLimit"],
                }
            )
    return out


def load_structure(tx: Conn, test_id: UUID) -> list[dict[str, Any]]:
    sections = tx.execute(
        """select id, position, title, suggested_minutes as "suggestedMinutes",
                  passage_pages as "passagePages", question_pages as "questionPages",
                  units_primary as "unitsPrimary", units_secondary as "unitsSecondary"
           from sections where test_id = %s order by position""",
        (test_id,),
    ).fetchall()
    questions = tx.execute(
        """select section_id, number, question_type as "questionType", word_limit as "wordLimit"
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


def ocr_summary(tx: Conn, test_id: UUID) -> dict[str, Any] | None:
    """State and fixed error code only: never OCR text, provider ids or lease data."""
    return tx.execute(
        """select j.state, j.error_code as "errorCode"
           from ocr_jobs j join test_assets a on a.id = j.asset_id
           where a.test_id = %s and a.kind = 'student_pdf'
           order by j.created_at desc limit 1""",
        (test_id,),
    ).fetchone()


def detail(tx: Conn, test: dict[str, Any]) -> dict[str, Any]:
    sections = load_structure(tx, test["id"])
    return {**public(test), "studentPdf": asset_summary(tx, test["id"]), "sections": sections}


@router.get("/api/tests")
@access(TEACHER)
def list_tests(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        return tx.execute(f"select {TEST_COLUMNS} from tests order by created_at desc").fetchall()  # noqa: S608


@router.post("/api/tests", status_code=201)
@access(TEACHER)
def create_test(body: CreateTestBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute(
            f"""insert into tests (teacher_id, title, test_type, source_type, duration_minutes)
                values (%s, %s, %s, %s, %s) returning {TEST_COLUMNS}""",  # noqa: S608
            (identity.user_id, body.title, body.testType, body.sourceType, body.durationMinutes),
        ).fetchone()
        assert row is not None  # noqa: S101
        write_audit(
            tx,
            action="test.create",
            entity="test",
            entity_id=row["id"],
            after={"title": body.title, "testType": body.testType, "sourceType": body.sourceType},
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


def sync_key_items(
    tx: Conn,
    test_id: UUID,
    old: Mapping[int, tuple[str, int | None]],
    new: Mapping[int, tuple[str, int | None]],
) -> tuple[int, int]:
    """Keeps a draft key in step with the structure: drops removed questions, un-reviews changed ones."""
    version = tx.execute(
        "select id from keys.answer_key_versions where test_id = %s order by version desc limit 1", (test_id,)
    ).fetchone()
    if version is None:
        return 0, 0
    removed = tx.execute(
        "delete from keys.answer_key_items where version_id = %s and not (question_number = any(%s))",
        (version["id"], list(new)),
    ).rowcount
    changed = [n for n, shape in old.items() if n in new and new[n] != shape]
    cleared = tx.execute(
        """update keys.answer_key_items set reviewed_at = null, reviewed_by = null
           where version_id = %s and question_number = any(%s) and reviewed_at is not null""",
        (version["id"], changed),
    ).rowcount
    return removed, cleared


@router.put("/api/tests/{id}/structure")
@access(TEACHER)
def put_structure(id: UUID, body: StructureBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    numbers = [n for s in body.sections for r in s.ranges for n in range(r.from_, r.to + 1)]
    if len(numbers) != len(set(numbers)) or len(numbers) > MAX_QUESTIONS:
        raise HttpError(400, "invalid_input")
    with with_identity(ctx.pool, identity) as tx:
        # The test is locked first: inserting before this would let unique-key errors
        # reveal another tenant's structure.
        test = lock_test(tx, id, draft=True)
        old_shape = {
            q["number"]: (q["question_type"], q["word_limit"])
            for q in tx.execute(
                "select number, question_type, word_limit from questions where test_id = %s", (id,)
            ).fetchall()
        }
        tx.execute("delete from sections where test_id = %s", (id,))
        for position, section in enumerate(body.sections, start=1):
            created = tx.execute(
                """insert into sections (test_id, teacher_id, position, title, suggested_minutes,
                     passage_pages, question_pages, units_primary, units_secondary)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                (
                    id,
                    test["teacher_id"],
                    position,
                    section.title,
                    section.suggestedMinutes,
                    section.passagePages,
                    section.questionPages,
                    section.unitsPrimary,
                    section.unitsSecondary,
                ),
            ).fetchone()
            assert created is not None  # noqa: S101
            for r in section.ranges:
                for number in range(r.from_, r.to + 1):
                    tx.execute(
                        """insert into questions
                             (test_id, teacher_id, section_id, number, question_type, word_limit)
                           values (%s, %s, %s, %s, %s, %s)""",
                        (id, test["teacher_id"], created["id"], number, r.questionType, r.wordLimit),
                    )
        new_shape = {
            n: (r.questionType, r.wordLimit)
            for s in body.sections
            for r in s.ranges
            for n in range(r.from_, r.to + 1)
        }
        removed, unreviewed = sync_key_items(tx, id, old_shape, new_shape)
        write_audit(
            tx,
            action="test.structure",
            entity="test",
            entity_id=id,
            after={
                "sections": len(body.sections),
                "questions": len(numbers),
                "keyItemsRemoved": removed,
                "keyReviewsCleared": unreviewed,
            },
        )
        return detail(tx, test)


def blockers_of(tx: Conn, test_id: UUID) -> list[str]:
    """Publish blockers for a test, read inside the caller's transaction."""
    test = tx.execute("select test_type, duration_minutes from tests where id = %s", (test_id,)).fetchone()
    if test is None:
        raise not_found()
    assets = tx.execute("select kind, page_count from test_assets where test_id = %s", (test_id,)).fetchall()
    sections = tx.execute(
        "select passage_pages, question_pages from sections where test_id = %s", (test_id,)
    ).fetchall()
    questions = tx.execute(
        "select number, question_type from questions where test_id = %s", (test_id,)
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
