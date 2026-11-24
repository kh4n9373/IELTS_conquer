"""Import a Reading test from `panxg-exam/v1` JSON written by an AI, as a manual (typed) test."""

from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import Field

from ..core.db import Conn, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, access
from ..exams import schema_v1
from ..exams.keys import draft_version
from .tests import TEACHER, TEST_COLUMNS, detail, lock_test

router = APIRouter(route_class=GuardedRoute)
GUIDE = Path(__file__).parents[1] / "exams" / "panxg-exam-v1-ai-guide.md"


class ImportBody(Body):
    json_text: Annotated[str, Field(max_length=schema_v1.MAX_JSON_BYTES, alias="json")]


class ContentBody(Body):
    content: dict[str, Any]


def checked(body: ImportBody) -> tuple[schema_v1.ExamDoc | None, dict[str, Any]]:
    doc, errors, warnings = schema_v1.check(body.json_text)
    report = {
        "ok": doc is not None,
        "errors": errors,
        "warnings": warnings,
        "summary": schema_v1.summary(doc) if doc is not None else None,
    }
    return doc, report


def write_doc(tx: Conn, test_id: UUID, teacher_id: UUID, doc: schema_v1.ExamDoc) -> None:
    """Replaces sections, questions and key of a locked draft manual test in the caller's transaction."""
    tx.execute("delete from sections where test_id = %s", (test_id,))
    version = draft_version(tx, test_id, create=True)
    assert version is not None  # noqa: S101
    tx.execute("delete from keys.answer_key_items where version_id = %s", (version["id"],))
    for position, s in enumerate(schema_v1.to_manual(doc), start=1):
        src = s["section"]
        created = tx.execute(
            """insert into sections (test_id, teacher_id, position, title, body, suggested_minutes,
                 units_primary, units_secondary)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (test_id, teacher_id, position, s["title"], s["body"], src.suggested_minutes,
             src.units_primary, src.units_secondary),
        ).fetchone()  # fmt: skip
        assert created is not None  # noqa: S101
        for q in s["questions"]:
            tx.execute(
                """insert into questions (test_id, teacher_id, section_id, number, question_type,
                     word_limit, answer_group, prompt, options)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (test_id, teacher_id, created["id"], q["number"], q["type"], q["word_limit"],
                 q["answer_group"], q["prompt"], q["options"]),
            )  # fmt: skip
            tx.execute(
                """insert into keys.answer_key_items
                     (version_id, teacher_id, question_number, answers, origin, flagged, note)
                   values (%s, %s, %s, %s, 'import', %s, %s)""",
                (version["id"], teacher_id, q["number"], q["answers"], q["uncertain"], q["note"]),
            )


def audit_after(doc: schema_v1.ExamDoc) -> dict[str, Any]:
    s = schema_v1.summary(doc)
    return {"sections": s["sections"], "questions": s["questions"], "uncertain": s["uncertain"]}


@router.get("/api/exam-schema/v1/ai-guide.md")
@access(TEACHER)
def ai_guide(ctx: CtxDep, identity: IdentityDep) -> Any:
    return PlainTextResponse(GUIDE.read_text("utf-8"), media_type="text/markdown; charset=utf-8")


@router.get("/api/exam-schema/v1/schema.json")
@access(TEACHER)
def schema_json(ctx: CtxDep, identity: IdentityDep) -> Any:
    return schema_v1.json_schema()


@router.post("/api/tests/import/validate")
@access(TEACHER)
def validate_import(body: ImportBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    return checked(body)[1]


@router.post("/api/tests/import", status_code=201)
@access(TEACHER)
def create_import(body: ImportBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    doc, report = checked(body)
    if doc is None:
        raise HttpError(400, "import_invalid")
    t = doc.test
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute(
            f"""insert into tests (teacher_id, title, test_type, source_type, duration_minutes,
                  authoring)
                values (%s, %s, %s, %s, %s, 'manual') returning {TEST_COLUMNS}""",  # noqa: S608
            (identity.user_id, t.title, t.test_type, t.source_type, t.duration_minutes),
        ).fetchone()
        assert row is not None  # noqa: S101
        write_doc(tx, row["id"], identity.user_id, doc)
        write_audit(tx, action="test.import", entity="test", entity_id=row["id"], after=audit_after(doc))
        return {**row, "warnings": report["warnings"]}


@router.post("/api/tests/{id}/reimport")
@access(TEACHER)
def reimport(id: UUID, body: ImportBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    doc, _ = checked(body)
    if doc is None:
        raise HttpError(400, "import_invalid")
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, id, draft=True)
        if test["authoring"] != "manual":
            raise HttpError(409, "not_manual")
        version = draft_version(tx, id, create=False)
        if version is not None and version["approvedAt"] is not None:
            raise HttpError(409, "test_locked")
        t = doc.test
        tx.execute(
            """update tests set title = %s, test_type = %s, source_type = %s, duration_minutes = %s
               where id = %s""",
            (t.title, t.test_type, t.source_type, t.duration_minutes, id),
        )
        write_doc(tx, id, test["teacher_id"], doc)
        write_audit(tx, action="test.reimport", entity="test", entity_id=id, after=audit_after(doc))
        fresh = lock_test(tx, id)
        return detail(tx, fresh)
