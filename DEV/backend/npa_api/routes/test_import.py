"""Import a Reading test from `panxg-exam/v1` JSON written by an AI, and edit its text afterwards."""

import json
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import Field

from ..core.db import Conn, with_identity, write_audit
from ..core.http import Body, CtxDep, GuardedRoute, HttpError, IdentityDep, access, not_found, storable
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
    """Replaces sections, questions, key and content of a locked draft test in the caller's transaction."""
    tx.execute("delete from sections where test_id = %s", (test_id,))
    version = draft_version(tx, test_id, create=True)
    assert version is not None  # noqa: S101
    tx.execute("delete from keys.answer_key_items where version_id = %s", (version["id"],))
    for position, section in enumerate(doc.sections, start=1):
        created = tx.execute(
            """insert into sections (test_id, teacher_id, position, title, suggested_minutes,
                 units_primary, units_secondary)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (
                test_id,
                teacher_id,
                position,
                section.title,
                section.suggested_minutes,
                section.units_primary,
                section.units_secondary,
            ),
        ).fetchone()
        assert created is not None  # noqa: S101
        for group in section.groups:
            answer_group = group.questions[0].number if group.select_count > 1 else None
            for q in group.questions:
                tx.execute(
                    """insert into questions (test_id, teacher_id, section_id, number, question_type,
                         word_limit, answer_group)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        test_id,
                        teacher_id,
                        created["id"],
                        q.number,
                        group.type,
                        group.word_limit,
                        answer_group,
                    ),
                )
                tx.execute(
                    """insert into keys.answer_key_items
                         (version_id, teacher_id, question_number, answers, origin, flagged, note)
                       values (%s, %s, %s, %s, 'import', %s, %s)""",
                    (
                        version["id"],
                        teacher_id,
                        q.number,
                        schema_v1.canonical_answers(group, q),
                        q.uncertain,
                        (q.uncertain_reason or "AI không chắc đáp án.") if q.uncertain else None,
                    ),
                )
    tx.execute(
        "update tests set content = %s where id = %s",
        (json.dumps(schema_v1.student_content(doc)), test_id),
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
                  authoring_mode, content)
                values (%s, %s, %s, %s, %s, 'composed', '{{}}') returning {TEST_COLUMNS}""",  # noqa: S608
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
        if test["authoringMode"] != "composed":
            raise HttpError(409, "not_composed")
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
        return {**detail(tx, fresh), "content": content_of(tx, id)}


def content_of(tx: Conn, test_id: UUID) -> Any:
    row = tx.execute("select content from tests where id = %s", (test_id,)).fetchone()
    if row is None:
        raise not_found()
    return row["content"]


@router.get("/api/tests/{id}/content")
@access(TEACHER)
def get_content(id: UUID, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        return {"content": content_of(tx, id)}


# Text fields a teacher may edit in step 2, with their length limits. Everything else is structure.
TEXT_FIELDS = {
    "section": {"title": 200},
    "passage": {"title": schema_v1.MAX_TEXT},
    "paragraph": {"label": 10, "text": schema_v1.MAX_PARAGRAPH},
    "group": {
        "instructions": schema_v1.MAX_TEXT,
        "prompt": schema_v1.MAX_TEXT,
        "content": schema_v1.MAX_CONTENT,
    },
    "option": {"text": schema_v1.MAX_TEXT},
    "question": {"prompt": schema_v1.MAX_TEXT},
}
CHANGED = HttpError(400, "structure_changed")


def _list(new: Any, old: list[Any]) -> list[Any]:
    if not isinstance(new, list) or len(new) != len(old):
        raise CHANGED
    return new


def _copy_text(kind: str, new: Any, old: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(new, dict):
        raise CHANGED
    out = dict(old)
    for name, limit in TEXT_FIELDS[kind].items():
        value = new.get(name)
        if (old.get(name) is None) != (value is None):
            raise CHANGED  # optional fields keep their presence: blanks and labels are structure
        if value is None:
            continue
        if not isinstance(value, str):
            raise CHANGED
        value = storable(value).strip()
        if not value or len(value) > limit:
            raise HttpError(400, "invalid_input")
        out[name] = value
    return out


def merge_content(old: dict[str, Any], new: Any) -> dict[str, Any]:
    """Takes only text from `new`; counts, numbers, types, keys and blanks must stay as they are."""
    if not isinstance(new, dict):
        raise CHANGED
    sections = []
    for s_new, s_old in zip(_list(new.get("sections"), old["sections"]), old["sections"], strict=True):
        section = _copy_text("section", s_new, s_old)
        passage = _copy_text("passage", s_new.get("passage"), s_old["passage"])
        paragraphs = s_old["passage"]["paragraphs"]
        passage["paragraphs"] = [
            _copy_text("paragraph", p_new, p_old)
            for p_new, p_old in zip(
                _list(s_new["passage"].get("paragraphs"), paragraphs), paragraphs, strict=True
            )
        ]
        if sum(len(p["text"]) for p in passage["paragraphs"]) > schema_v1.MAX_PASSAGE:
            raise HttpError(400, "invalid_input")
        section["passage"] = passage
        groups = []
        for g_new, g_old in zip(_list(s_new.get("groups"), s_old["groups"]), s_old["groups"], strict=True):
            group = _copy_text("group", g_new, g_old)
            if group.get("content") is not None:
                own = sorted(q["number"] for q in g_old["questions"])
                before = sorted(int(n) for n in schema_v1.PLACEHOLDER.findall(g_old["content"]))
                after = sorted(int(n) for n in schema_v1.PLACEHOLDER.findall(group["content"]))
                if before != after or any(n not in own for n in after):
                    raise CHANGED
            group["options"] = [
                _copy_text("option", o_new, o_old)
                for o_new, o_old in zip(
                    _list(g_new.get("options"), g_old["options"]), g_old["options"], strict=True
                )
            ]
            questions = []
            for q_new, q_old in zip(
                _list(g_new.get("questions"), g_old["questions"]), g_old["questions"], strict=True
            ):
                q = _copy_text("question", q_new, q_old)
                if q_old.get("options") is not None:
                    q["options"] = [
                        _copy_text("option", o_new, o_old)
                        for o_new, o_old in zip(
                            _list(q_new.get("options"), q_old["options"]), q_old["options"], strict=True
                        )
                    ]
                questions.append(q)
            group["questions"] = questions
            groups.append(group)
        section["groups"] = groups
        sections.append(section)
    return {"sections": sections}


@router.put("/api/tests/{id}/content")
@access(TEACHER)
def put_content(id: UUID, body: ContentBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        test = lock_test(tx, id, draft=True)
        if test["authoringMode"] != "composed":
            raise HttpError(409, "not_composed")
        merged = merge_content(content_of(tx, id), body.content)
        tx.execute("update tests set content = %s where id = %s", (json.dumps(merged), id))
        titles = [s["title"] for s in merged["sections"]]
        for position, title in enumerate(titles, start=1):
            tx.execute(
                "update sections set title = %s where test_id = %s and position = %s", (title, id, position)
            )
        write_audit(
            tx, action="test.content_update", entity="test", entity_id=id, after={"sections": len(titles)}
        )
        return {"content": merged}
