"""The one question schema. Teachers send it, the PDF parser returns it, and both write through here."""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ..core.db import Conn
from ..core.http import Body, NameField, OptionField, PassageField, PromptField
from .keys import draft_version, normalize_answers, upsert_items
from .types import QUESTION_GROUPS, QuestionType

MAX_SECTIONS = 10
MAX_QUESTIONS = 200
Minutes = Annotated[int, Field(ge=1, le=300)]
Pages = Annotated[list[Annotated[int, Field(ge=1, le=5000)]], Field(max_length=50)]
Units = Annotated[list[NameField], Field(max_length=50)]
Number = Annotated[int, Field(ge=1, le=MAX_QUESTIONS)]


class RangeBody(Body):
    """PDF tests mapped by hand: a run of numbers sharing one type."""

    from_: Annotated[int, Field(ge=1, le=MAX_QUESTIONS, alias="from")]
    to: Number
    questionType: QuestionType  # noqa: N815
    wordLimit: Annotated[int, Field(ge=1, le=20)] | None = None  # noqa: N815
    answerGroup: Number | None = None  # noqa: N815

    @model_validator(mode="after")
    def check(self) -> "RangeBody":
        if self.from_ > self.to:
            raise ValueError("invalid range")
        if self.wordLimit is not None and QUESTION_GROUPS[self.questionType] != "completion":
            raise ValueError("word limit applies to completion types only")
        return self


class QuestionBody(Body):
    """One question. `answers` None leaves the key alone; [] clears this question's answer."""

    number: Number
    questionType: QuestionType  # noqa: N815
    wordLimit: Annotated[int, Field(ge=1, le=20)] | None = None  # noqa: N815
    prompt: PromptField = ""
    options: Annotated[list[OptionField], Field(max_length=26)] = []
    answerGroup: Number | None = None  # noqa: N815  # "choose two": graded together, either order
    answers: Annotated[list[str], Field(max_length=20)] | None = None

    @field_validator("answers")
    @classmethod
    def clean(cls, value: list[str] | None) -> list[str] | None:
        return normalize_answers(value) if value else value


class SectionBody(Body):
    title: NameField
    body: PassageField = ""
    questions: Annotated[list[QuestionBody], Field(max_length=MAX_QUESTIONS)] = []
    suggestedMinutes: Minutes | None = None  # noqa: N815
    passagePages: Pages = []  # noqa: N815
    questionPages: Pages = []  # noqa: N815
    unitsPrimary: Units = []  # noqa: N815
    unitsSecondary: Units = []  # noqa: N815
    ranges: Annotated[list[RangeBody], Field(max_length=50)] = []


class StructureBody(Body):
    sections: Annotated[list[SectionBody], Field(max_length=MAX_SECTIONS)]

    @model_validator(mode="after")
    def unique_numbers(self) -> "StructureBody":
        numbers = [n for _, n, *_ in self.rows()]
        if len(numbers) != len(set(numbers)) or len(numbers) > MAX_QUESTIONS:
            raise ValueError("repeated question number")
        return self

    def rows(self) -> list[tuple[int, int, str, int | None, int | None, str, list[str]]]:
        """Every question as (section index, number, type, word limit, answer group, prompt, options)."""
        return [
            (i, n, r.questionType, r.wordLimit, r.answerGroup, "", [])
            for i, s in enumerate(self.sections)
            for r in s.ranges
            for n in range(r.from_, r.to + 1)
        ] + [
            (i, q.number, q.questionType, q.wordLimit, q.answerGroup, q.prompt, q.options)
            for i, s in enumerate(self.sections)
            for q in s.questions
        ]


Shape = Mapping[int, tuple[str, int | None, list[str]]]


def sync_key_items(tx: Conn, test_id: UUID, old: Shape, new: Shape) -> tuple[int, int]:
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


def write_structure(tx: Conn, test_id: UUID, teacher_id: UUID, body: StructureBody) -> dict[str, Any]:
    """Replaces sections and questions. The caller has locked the draft test. Returns audit counts."""
    rows = body.rows()
    old_shape = {
        q["number"]: (q["question_type"], q["word_limit"], q["options"])
        for q in tx.execute(
            "select number, question_type, word_limit, options from questions where test_id = %s", (test_id,)
        ).fetchall()
    }
    tx.execute("delete from sections where test_id = %s", (test_id,))
    section_ids = []
    for position, s in enumerate(body.sections, start=1):
        created = tx.execute(
            """insert into sections (test_id, teacher_id, position, title, body, suggested_minutes,
                 passage_pages, question_pages, units_primary, units_secondary)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (test_id, teacher_id, position, s.title, s.body, s.suggestedMinutes, s.passagePages,
             s.questionPages, s.unitsPrimary, s.unitsSecondary),
        ).fetchone()  # fmt: skip
        assert created is not None  # noqa: S101
        section_ids.append(created["id"])
    for index, number, kind, word_limit, group, prompt, options in rows:
        tx.execute(
            """insert into questions (test_id, teacher_id, section_id, number, question_type, word_limit,
                 answer_group, prompt, options)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (test_id, teacher_id, section_ids[index], number, kind, word_limit, group, prompt, options),
        )
    # Changed options can change what a reviewed letter means, so they clear that review too.
    new_shape = {number: (kind, limit, options) for _, number, kind, limit, _, _, options in rows}
    removed, cleared = sync_key_items(tx, test_id, old_shape, new_shape)

    given = {q.number: q.answers for s in body.sections for q in s.questions if q.answers is not None}
    if given:
        version = draft_version(tx, test_id, create=True)
        assert version is not None  # noqa: S101
        typed = {n: a for n, a in given.items() if a}
        upsert_items(tx, version["id"], typed, "manual")
        # The teacher typed these beside their questions, so they need no second review in step 3.
        tx.execute(
            """update keys.answer_key_items set reviewed_at = now(), reviewed_by = %s, flagged = false
               where version_id = %s and question_number = any(%s)""",
            (teacher_id, version["id"], list(typed)),
        )
        tx.execute(
            "delete from keys.answer_key_items where version_id = %s and question_number = any(%s)",
            (version["id"], [n for n, a in given.items() if not a]),
        )
    return {
        "sections": len(body.sections),
        "questions": len(rows),
        "keyItemsRemoved": removed,
        "keyReviewsCleared": cleared,
        "answersSet": sum(1 for a in given.values() if a),
    }
