"""Turns key text into draft answers. Everything imported stays unreviewed until a teacher checks it."""

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pypdfium2 as pdfium
from psycopg.types.json import Jsonb

from ..core.db import Conn
from .keys import NUMBERED, Origin, draft_version, normalize_answers, upsert_items
from .pdf import PdfRejected

MIN_CONFIDENCE = 0.90
BBox = tuple[float, float, float, float]

START = re.compile(NUMBERED + r"(\S.*)")
NUMBER_ONLY = re.compile(r"([0-9]{1,3})\s*[.):-]")
NEXT_PAIR = re.compile(r"\s+" + NUMBERED + r"(?=\S)")
TRAILING_BARE = re.compile(r"\s([0-9]{1,3})\s*$")
TRAILING_MARKED = re.compile(r"(?:^|\s)[0-9]{1,3}\s*[.):-]\s*$")
LEADING_MARKED = re.compile(r"[0-9]{1,3}\s*[.):-]\s+\S")

TFNG = {"TRUE", "FALSE", "NOT GIVEN", "T", "F", "NG"}
YNNG = {"YES", "NO", "NOT GIVEN", "Y", "N", "NG"}
LETTER_TYPES = {"multiple_choice", "matching_features", "matching_information", "sentence_endings"}
LETTER = re.compile(r"[A-Za-z]")
ROMAN = re.compile(r"(?=.)M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", re.IGNORECASE)


@dataclass(frozen=True)
class KeyLine:
    text: str
    page: int | None
    bbox: BBox | None
    confidence: float | None
    scanned: bool = False


@dataclass(frozen=True)
class KeyDraft:
    number: int
    answers: list[str]
    flagged: bool
    page: int | None
    bbox: BBox | None


def split_pairs(text: str) -> list[tuple[int, str, bool]]:
    """Returns (number, raw answer, suspect). Only a larger number starts a new pair, so
    `23 platform 3` stays one answer. A numbered pair that is not larger marks the answer suspect."""
    text = text.strip()
    empty = NUMBER_ONLY.fullmatch(text)
    if empty is not None:
        return [(int(empty.group(1)), "", False)]
    match = START.fullmatch(text)
    if match is None:
        return []
    current, rest, start, suspect = int(match.group(1)), match.group(2), 0, False
    pairs: list[tuple[int, str, bool]] = []
    for found in NEXT_PAIR.finditer(rest):
        number = int(found.group(1))
        if number > current:
            pairs.append((current, rest[start : found.start()], suspect))
            current, start, suspect = number, found.end(), False
        else:
            suspect = True
    pairs.append((current, rest[start:], suspect))
    return pairs


def looks_misread(number: int, raw: str) -> bool:
    """A number with a separator at either end is a neighbouring question whose answer is missing.
    A bare trailing number is a real answer (`23 platform 3`) unless it is larger than the question."""
    bare = TRAILING_BARE.search(raw)
    return (
        LEADING_MARKED.match(raw) is not None
        or TRAILING_MARKED.search(raw) is not None
        or (bare is not None and int(bare.group(1)) > number)
    )


def clean_answers(raw: str) -> tuple[list[str], bool]:
    """Returns the usable answers and whether anything had to be dropped."""
    parts = [part.strip() for part in raw.replace("\x00", "").split(";")]
    try:
        return normalize_answers(parts), False
    except ValueError:
        pass
    try:
        return normalize_answers([part for part in parts if part]), True
    except ValueError:
        return [], True


def low_confidence(line: KeyLine) -> bool:
    if not line.scanned:
        return False
    # Written this way round so NaN counts as low.
    return line.confidence is None or not line.confidence >= MIN_CONFIDENCE


def parse_key_lines(lines: list[KeyLine]) -> list[KeyDraft]:
    """One draft per number. A repeated number keeps its first value and is flagged."""
    drafts: dict[int, KeyDraft] = {}
    lines_of: dict[int, set[int]] = {}
    repeated: set[int] = set()
    for index, line in enumerate(lines):
        for number, raw, suspect_pair in split_pairs(line.text):
            answers, dropped = clean_answers(raw)
            flagged = dropped or suspect_pair or looks_misread(number, raw) or low_confidence(line)
            lines_of.setdefault(number, set()).add(index)
            if number not in drafts:
                drafts[number] = KeyDraft(number, answers, flagged, line.page, line.bbox)
                continue
            repeated.add(number)
            if not drafts[number].answers and answers:
                drafts[number] = KeyDraft(number, answers, True, line.page, line.bbox)
    # A repeat can mean a line was split wrongly, so its neighbours on that line are suspect too.
    suspect = {index for number in repeated for index in lines_of[number]}
    return [
        replace(draft, flagged=draft.flagged or bool(lines_of[number] & suspect))
        for number, draft in sorted(drafts.items())
    ]


def fits_value(value: str, kind: str) -> bool:
    if kind == "tfng":
        return value.upper() in TFNG
    if kind == "ynng":
        return value.upper() in YNNG
    if kind in LETTER_TYPES:
        return LETTER.fullmatch(value) is not None
    if kind == "matching_headings":
        return ROMAN.fullmatch(value) is not None
    return True


def fits_question(answers: list[str], kind: str, word_limit: int | None) -> bool:
    if not answers:
        return False
    return all(
        fits_value(answer, kind) and (word_limit is None or len(answer.split()) <= word_limit)
        for answer in answers
    )


def pdf_lines(path: Path) -> list[KeyLine]:
    """Reads the text layer locally. Text lines carry a page but no box or confidence."""
    out: list[KeyLine] = []
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range()
                    finally:
                        textpage.close()
                finally:
                    page.close()
                out.extend(KeyLine(row, index + 1, None, None) for row in text.splitlines() if row.strip())
        finally:
            document.close()
    except pdfium.PdfiumError as err:
        raise PdfRejected("corrupt_pdf") from err
    return out


def finite_bbox(bbox: BBox | None) -> BBox | None:
    """JSON cannot hold NaN or infinity."""
    return bbox if bbox is not None and all(math.isfinite(v) for v in bbox) else None


def mark_source(tx: Conn, version_id: UUID, draft: KeyDraft, *, flagged: bool) -> None:
    bbox = finite_bbox(draft.bbox)
    tx.execute(
        """update keys.answer_key_items
           set flagged = %s, source_page = %s, source_bbox = %s
           where version_id = %s and question_number = %s""",
        (flagged, draft.page, None if bbox is None else Jsonb(list(bbox)), version_id, draft.number),
    )


def apply_drafts(tx: Conn, test_id: UUID, drafts: list[KeyDraft], origin: Origin) -> dict[str, int]:
    """The test row must already be locked. Reviewed and manual answers are never overwritten."""
    questions = {
        r["number"]: r
        for r in tx.execute(
            "select number, question_type, word_limit from questions where test_id = %s", (test_id,)
        ).fetchall()
    }
    version = draft_version(tx, test_id, create=False)
    existing: dict[int, dict[str, Any]] = {}
    if version is not None:
        rows = tx.execute(
            "select question_number, origin, reviewed_at from keys.answer_key_items where version_id = %s",
            (version["id"],),
        ).fetchall()
        existing = {r["question_number"]: r for r in rows}
    counts = {"filled": 0, "flagged": 0, "kept": 0, "unmatched": 0}
    for draft in drafts:
        question = questions.get(draft.number)
        if question is None:
            counts["unmatched"] += 1
            continue
        current = existing.get(draft.number)
        if current is not None and (current["reviewed_at"] is not None or current["origin"] == "manual"):
            counts["kept"] += 1
            continue
        if not draft.answers:
            counts["flagged"] += 1
            if current is not None and version is not None:
                # The new source shows no answer, so the old draft must not look clean.
                mark_source(tx, version["id"], draft, flagged=True)
            continue
        flagged = draft.flagged or not fits_question(
            draft.answers, question["question_type"], question["word_limit"]
        )
        version = version or draft_version(tx, test_id, create=True)
        assert version is not None  # noqa: S101
        upsert_items(tx, version["id"], {draft.number: draft.answers}, origin)
        mark_source(tx, version["id"], draft, flagged=flagged)
        counts["filled"] += 1
        counts["flagged"] += int(flagged)
    return counts
