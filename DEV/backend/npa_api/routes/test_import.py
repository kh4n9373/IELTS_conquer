"""Add questions from `panxg-exam/v1` JSON written by an AI to a manual (typed) test."""

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
from .tests import TEACHER, detail, lock_test

router = APIRouter(route_class=GuardedRoute)
GUIDE = Path(__file__).parents[1] / "exams" / "panxg-exam-v1-ai-guide.md"


class ImportBody(Body):
    json_text: Annotated[str, Field(max_length=schema_v1.MAX_JSON_BYTES, alias="json")]


def checked(body: ImportBody) -> tuple[schema_v1.ExamDoc | None, dict[str, Any]]:
    doc, errors, warnings = schema_v1.check(body.json_text)
    report = {
        "ok": doc is not None,
        "errors": errors,
        "warnings": warnings,
        "summary": schema_v1.summary(doc) if doc is not None else None,
    }
    return doc, report


def append_doc(tx: Conn, test_id: UUID, teacher_id: UUID, doc: schema_v1.ExamDoc) -> None:
    """Adds the doc's sections after a locked draft's own, numbering its questions after the last one."""
    last = tx.execute(
        """select (select coalesce(max(number), 0) from questions where test_id = %s) as number,
                  (select coalesce(max(position), 0) from sections where test_id = %s) as position""",
        (test_id, test_id),
    ).fetchone()
    assert last is not None  # noqa: S101
    if last["number"] + schema_v1.summary(doc)["questions"] > schema_v1.MAX_QUESTIONS:
        raise HttpError(409, "too_many_questions")
    version = draft_version(tx, test_id, create=True)
    assert version is not None  # noqa: S101
    # Answers left behind by deleted questions must not land on the new numbers.
    tx.execute(
        "delete from keys.answer_key_items where version_id = %s and question_number > %s",
        (version["id"], last["number"]),
    )
    for position, s in enumerate(schema_v1.to_manual(doc, last["number"]), start=last["position"] + 1):
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


@router.post("/api/tests/{id}/import")
@access(TEACHER)
def import_into(id: UUID, body: ImportBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    """Appends AI-filled questions to a manual draft; the test's own details stay as the teacher set them."""
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
        append_doc(tx, id, test["teacher_id"], doc)
        write_audit(tx, action="test.import", entity="test", entity_id=id, after=audit_after(doc))
        return detail(tx, lock_test(tx, id))
