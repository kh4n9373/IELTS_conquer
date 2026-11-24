"""The `panxg-exam/v1` import format: a Reading test written by any AI from a teacher's source exam.

The Pydantic models are the single source of truth; `json_schema()` is generated from them. `check()`
turns untrusted JSON text into a validated document plus errors and warnings that a teacher can paste
back to the AI. Nothing in the document is ever executed or rendered as HTML.
"""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from ..core.http import storable
from .keys import MAX_ANSWER_LENGTH, MAX_ANSWERS
from .types import QUESTION_GROUPS

SCHEMA_ID = "panxg-exam/v1"
MAX_JSON_BYTES = 1_000_000
MAX_QUESTIONS = 100
MAX_PARAGRAPH = 10_000
MAX_PASSAGE = 30_000
MAX_TEXT = 2_000
MAX_CONTENT = 10_000

# One question per answer box: "choose two" is two multiple_choice questions with select_count 2.
UNSUPPORTED = ("diagram_label", "multiple_choice_multi")
SUPPORTED_TYPES = [t for t in QUESTION_GROUPS if t not in UNSUPPORTED]
TFNG = {"tfng": ["TRUE", "FALSE", "NOT GIVEN"], "ynng": ["YES", "NO", "NOT GIVEN"]}
PLACEHOLDER = re.compile(r"\{\{\s*(\d{1,3})\s*\}\}")


def _text(limit: int) -> Any:
    def check(value: str) -> str:
        value = storable(value).strip()
        if not value or len(value) > limit:
            raise ValueError(f"must be 1-{limit} characters")
        return value

    return AfterValidator(check)


Short = Annotated[str, _text(200)]
Text = Annotated[str, _text(MAX_TEXT)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Option(Model):
    key: Annotated[str, _text(10)] = Field(description="Letter or numeral exactly as printed, e.g. A or iv.")
    text: Text


class Question(Model):
    number: Annotated[int, Field(ge=1, le=MAX_QUESTIONS)]
    prompt: Text | None = Field(
        None, description="The question text. Omit when the group content holds {{n}}."
    )
    options: Annotated[list[Option], Field(max_length=26)] | None = Field(
        None, description="Only for single-answer multiple choice."
    )
    answers: Annotated[list[str], Field(min_length=1, max_length=MAX_ANSWERS)] = Field(
        description="Every accepted answer: option keys, TRUE/FALSE/NOT GIVEN, or words for completion."
    )
    uncertain: bool = False
    uncertain_reason: Text | None = None


class Group(Model):
    type: str = Field(description="One of: " + ", ".join(SUPPORTED_TYPES))
    instructions: Text
    word_limit: Annotated[int, Field(ge=1, le=20)] | None = None
    options: Annotated[list[Option], Field(max_length=26)] = []
    options_reusable: bool = False
    select_count: Annotated[int, Field(ge=1, le=5)] = 1
    prompt: Text | None = None
    content: Annotated[str, _text(MAX_CONTENT)] | None = Field(
        None,
        description="Notes/summary/table text with {{n}} where blank n goes. Tables use | between cells.",
    )
    questions: Annotated[list[Question], Field(min_length=1, max_length=40)]


class Paragraph(Model):
    label: Annotated[str, _text(10)] | None = None
    text: Annotated[str, _text(MAX_PARAGRAPH)]


class Passage(Model):
    title: Text | None = None
    paragraphs: Annotated[list[Paragraph], Field(min_length=1, max_length=80)]


class Section(Model):
    title: Short
    suggested_minutes: Annotated[int, Field(ge=1, le=300)] | None = None
    units_primary: Annotated[list[Short], Field(max_length=20)] = []
    units_secondary: Annotated[list[Short], Field(max_length=20)] = []
    passage: Passage
    groups: Annotated[list[Group], Field(min_length=1, max_length=20)]


class TestInfo(Model):
    title: Short
    test_type: Literal["course", "ielts_standard"]
    duration_minutes: Annotated[int, Field(ge=1, le=300)]
    source_type: Literal["self_authored", "licensed", "commercial", "other"]


class ExamDoc(Model):
    schema_: Literal["panxg-exam/v1"] = Field(alias="schema")
    skill: Literal["reading"]
    test: TestInfo
    sections: Annotated[list[Section], Field(min_length=1, max_length=10)]
    ai_notes: Annotated[list[Text], Field(max_length=50)] = []


def json_schema() -> dict[str, Any]:
    schema = ExamDoc.model_json_schema(by_alias=True)
    schema["$id"] = "https://panxg.app/schemas/panxg-exam-v1.json"
    schema["title"] = SCHEMA_ID
    return schema


def issue(code: str, path: str, message: str, number: int | None = None) -> dict[str, Any]:
    return {"code": code, "path": path, "number": number, "message": message}


def extract_json(text: str) -> str:
    """Takes the first ```json block, else the first `{` to the last `}`: AIs often wrap the output."""
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _pydantic_errors(err: ValidationError) -> list[dict[str, Any]]:
    out = []
    for e in err.errors()[:50]:
        path = ".".join(str(p) for p in e["loc"])
        code = "unknown_field" if e["type"] == "extra_forbidden" else "invalid_field"
        out.append(issue(code, path, f"{path}: {e['msg']}"))
    return out


def check(raw: str) -> tuple[ExamDoc | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (document, errors, warnings). A document is returned only when there are no errors."""
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_JSON_BYTES:
        return None, [issue("too_large", "", "Nội dung quá 1 MB.")], []
    try:
        data = json.loads(extract_json(raw))
    except ValueError as e:
        return None, [issue("invalid_json", "", f"Không đọc được JSON: {e}")], []
    try:
        doc = ExamDoc.model_validate(data)
    except ValidationError as e:
        return None, _pydantic_errors(e), []
    errors, warnings = _semantic(doc)
    return (None if errors else doc), errors, warnings


def _semantic(doc: ExamDoc) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    numbers: list[int] = []
    for si, section in enumerate(doc.sections):
        total = sum(len(p.text) for p in section.passage.paragraphs)
        if total > MAX_PASSAGE:
            errors.append(
                issue("too_large", f"sections.{si}.passage", f"Bài đọc {si + 1} dài quá {MAX_PASSAGE} ký tự.")
            )
        for gi, group in enumerate(section.groups):
            gpath = f"sections.{si}.groups.{gi}"
            errors_before = len(errors)
            _check_group(group, gpath, errors, warnings)
            if len(errors) == errors_before:
                _check_answers(group, gpath, errors, warnings)
            numbers.extend(q.number for q in group.questions)
    if len(set(numbers)) != len(numbers):
        dup = sorted({n for n in numbers if numbers.count(n) > 1})
        errors.append(issue("duplicate_number", "sections", f"Số câu bị trùng: {dup}."))
    elif numbers != list(range(1, len(numbers) + 1)):
        errors.append(
            issue("number_gap", "sections", "Số câu phải liên tục từ 1 và tăng dần theo thứ tự trong đề.")
        )
    if doc.test.test_type == "ielts_standard" and (len(doc.sections) != 3 or len(numbers) != 40):
        warnings.append(issue("ielts_shape", "test", "Đề IELTS chuẩn cần 3 passage và 40 câu."))
    for i, note in enumerate(doc.ai_notes):
        warnings.append(issue("ai_note", f"ai_notes.{i}", note))
    return errors, warnings


def _check_group(group: Group, path: str, errors: list[Any], warnings: list[Any]) -> None:
    kind = group.type
    if kind == "diagram_label":
        errors.append(
            issue(
                "unsupported_type", f"{path}.type", "Dạng diagram_label (cần ảnh) chưa hỗ trợ: bỏ nhóm này."
            )
        )
        return
    if kind == "multiple_choice_multi":
        message = "Dùng multiple_choice với select_count (mỗi câu một ô), không dùng multiple_choice_multi."
        errors.append(issue("unsupported_type", f"{path}.type", message))
        return
    if kind not in SUPPORTED_TYPES:
        errors.append(issue("unknown_type", f"{path}.type", f"Dạng câu '{kind}' không có trong danh sách."))
        return
    family = QUESTION_GROUPS[kind]
    keys = [o.key for o in group.options]
    if len(set(keys)) != len(keys):
        errors.append(issue("duplicate_option", f"{path}.options", "Trùng ký hiệu lựa chọn."))
    if family == "matching" and len(keys) < 2:
        errors.append(issue("missing_options", f"{path}.options", "Dạng nối cần danh sách lựa chọn ở nhóm."))
    if group.word_limit is not None and family != "completion":
        errors.append(
            issue("invalid_word_limit", f"{path}.word_limit", "word_limit chỉ dùng cho dạng điền từ.")
        )
    if group.select_count > 1:
        if kind != "multiple_choice":
            errors.append(
                issue(
                    "invalid_select_count",
                    f"{path}.select_count",
                    "select_count > 1 chỉ dùng cho multiple_choice.",
                )
            )
        elif len(group.questions) != group.select_count or len(keys) < group.select_count + 1:
            errors.append(
                issue(
                    "invalid_select_count",
                    path,
                    "Nhóm chọn nhiều đáp án cần đúng select_count câu và đủ lựa chọn.",
                )
            )
        if not group.prompt:
            errors.append(
                issue("missing_prompt", f"{path}.prompt", "Nhóm chọn nhiều đáp án cần prompt chung.")
            )
    if group.content is not None:
        refs = [int(n) for n in PLACEHOLDER.findall(group.content)]
        own = {q.number for q in group.questions}
        if any(n not in own for n in refs):
            errors.append(
                issue("bad_placeholder", f"{path}.content", "content có {{n}} không thuộc nhóm này.")
            )
    refs_set = {int(n) for n in PLACEHOLDER.findall(group.content or "")}
    for qi, q in enumerate(group.questions):
        qpath = f"{path}.questions.{qi}"
        if q.options is not None and (kind != "multiple_choice" or group.select_count > 1):
            errors.append(
                issue(
                    "invalid_field",
                    f"{qpath}.options",
                    "options riêng chỉ dùng cho trắc nghiệm một đáp án.",
                    q.number,
                )
            )
        if kind == "multiple_choice" and group.select_count == 1 and not (q.options or group.options):
            errors.append(issue("missing_options", qpath, "Câu trắc nghiệm thiếu lựa chọn.", q.number))
        if not q.prompt and q.number not in refs_set and group.select_count == 1:
            warnings.append(
                issue(
                    "missing_prompt", qpath, "Câu không có prompt và không có {{n}} trong content.", q.number
                )
            )


def _check_answers(group: Group, path: str, errors: list[Any], warnings: list[Any]) -> None:
    family = QUESTION_GROUPS[group.type]
    picked: list[str] = []
    for qi, q in enumerate(group.questions):
        qpath = f"{path}.questions.{qi}.answers"
        answers = [a.strip() for a in q.answers]
        if any(not a or len(a) > MAX_ANSWER_LENGTH for a in answers):
            errors.append(issue("invalid_answer", qpath, "Đáp án rỗng hoặc quá dài.", q.number))
            continue
        if family == "tfng":
            allowed = TFNG[group.type]
            if any(a.upper() not in allowed for a in answers):
                errors.append(
                    issue("invalid_answer", qpath, f"Đáp án phải là {' / '.join(allowed)}.", q.number)
                )
        elif family in ("matching", "multiple_choice"):
            keys = {o.key.casefold() for o in (q.options or group.options)}
            if any(a.casefold() not in keys for a in answers):
                errors.append(
                    issue("answer_not_option", qpath, "Đáp án phải là ký hiệu có trong options.", q.number)
                )
            if group.select_count > 1:
                if len(answers) != 1:
                    errors.append(
                        issue(
                            "invalid_answer",
                            qpath,
                            "Mỗi câu trong nhóm chọn nhiều chỉ có 1 đáp án.",
                            q.number,
                        )
                    )
                picked.extend(a.casefold() for a in answers)
        elif group.word_limit is not None:
            if any(len(a.split()) > group.word_limit for a in answers):
                errors.append(
                    issue("answer_too_long", qpath, f"Đáp án dài quá {group.word_limit} từ.", q.number)
                )
        if q.uncertain:
            reason = q.uncertain_reason or "AI không chắc đáp án."
            warnings.append(issue("uncertain_answer", qpath, reason, q.number))
    if len(picked) != len(set(picked)):
        errors.append(
            issue("duplicate_answer", path, "Các câu trong nhóm chọn nhiều phải có đáp án khác nhau.")
        )


def canonical_answers(group: Group, q: Question) -> list[str]:
    if QUESTION_GROUPS[group.type] == "tfng":
        return [a.strip().upper() for a in q.answers]
    if QUESTION_GROUPS[group.type] in ("matching", "multiple_choice"):
        keys = {o.key.casefold(): o.key for o in (q.options or group.options)}
        return [keys[a.strip().casefold()] for a in q.answers]
    return [a.strip() for a in q.answers]


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_PROMPT = 2000  # questions.prompt in the manual model
MAX_OPTION = 300  # one option in the manual model


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _blanks(text: str, offset: int = 0) -> str:
    return PLACEHOLDER.sub(lambda m: f"({int(m.group(1)) + offset}) ________", text)


def _line_of(content: str, number: int, offset: int = 0) -> str:
    for line in content.splitlines():
        if number in (int(n) for n in PLACEHOLDER.findall(line)):
            return _blanks(line, offset).strip(" |")
    return ""


def to_manual(doc: ExamDoc, offset: int = 0) -> list[dict[str, Any]]:
    """The test as manual-mode sections and questions, each question with its key.

    The manual model has no group level, so the group's instructions, shared option list and
    gapped text open its first question; later questions carry their own line or prompt.
    Multiple choice keeps its options and answers become letters by position. Other option lists
    become their keys (i, ii, A…), which is what the student picks and what the key holds.
    `offset` shifts every question number, so the questions can follow ones already in a test.
    """
    out = []
    for s in doc.sections:
        paragraphs = [f"{p.label}  {p.text}" if p.label else p.text for p in s.passage.paragraphs]
        body = "\n\n".join(([s.passage.title] if s.passage.title else []) + paragraphs)
        questions = []
        for g in s.groups:
            choice = QUESTION_GROUPS[g.type] == "multiple_choice"
            shared = [g.instructions]
            if g.prompt:
                shared.append(g.prompt)
            if g.options and not choice:
                shared.append("\n".join(f"{o.key}  {o.text}" for o in g.options))
            if g.content:
                shared.append(_blanks(g.content, offset))
            lead = g.questions[0].number + offset if g.select_count > 1 else None
            for i, q in enumerate(g.questions):
                if q.prompt:
                    own = _blanks(q.prompt, offset)
                elif g.content:
                    own = "" if i == 0 else _line_of(g.content, q.number, offset)
                else:
                    own = (g.prompt or "") if i > 0 else ""
                parts = (shared if i == 0 else []) + ([own] if own else [])
                opts = q.options or g.options
                if choice:
                    options = [_cut(o.text, MAX_OPTION) for o in opts]
                    position = {o.key.casefold(): LETTERS[k] for k, o in enumerate(opts)}
                    answers = [position[a.strip().casefold()] for a in q.answers]
                else:
                    options = [o.key for o in g.options]
                    answers = canonical_answers(g, q)
                questions.append(
                    {
                        "number": q.number + offset,
                        "type": g.type,
                        "word_limit": g.word_limit,
                        "answer_group": lead,
                        "prompt": _cut("\n\n".join(parts), MAX_PROMPT),
                        "options": options,
                        "answers": answers,
                        "uncertain": q.uncertain,
                        "note": (q.uncertain_reason or "AI không chắc đáp án.") if q.uncertain else None,
                    }
                )
        out.append({"title": s.title, "body": body, "section": s, "questions": questions})
    return out


def summary(doc: ExamDoc) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for s in doc.sections:
        for g in s.groups:
            by_type[g.type] = by_type.get(g.type, 0) + len(g.questions)
    return {
        "title": doc.test.title,
        "testType": doc.test.test_type,
        "sections": len(doc.sections),
        "questions": sum(by_type.values()),
        "byType": by_type,
        "uncertain": sum(q.uncertain for s in doc.sections for g in s.groups for q in g.questions),
    }
