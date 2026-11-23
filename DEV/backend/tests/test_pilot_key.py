"""Local-only check of the real pilot key. Failures name question numbers, never answers."""

import re
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from npa_api.exams.key_import import parse_key_lines, pdf_lines

ROOT = Path(__file__).resolve().parents[3]
EXAM = ROOT / "DEV" / "exam"
TABLE = ROOT / "BA" / "PILOT_EXAM_KEY.md"
VARIANT_QUESTION = 23


def cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def mismatched_numbers() -> list[int]:
    answers: dict[int, str] = {}
    variants: set[str] = set()
    for row in TABLE.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", row):
            continue
        parts = cells(row)
        if len(parts) == 8:
            answers[int(parts[0])] = parts[7].casefold()
        elif len(parts) == 4 and int(parts[0]) == VARIANT_QUESTION and parts[2].startswith("Đúng"):
            variants.add(parts[1].strip("`").casefold())
    source = next((EXAM / "key").glob("*Teacher_Key.pdf"))
    drafts = {d.number: d for d in parse_key_lines(pdf_lines(source))}
    wrong = []
    for number in range(1, 41):
        draft = drafts.get(number)
        value = draft.answers[0].casefold() if draft is not None and len(draft.answers) == 1 else None
        allowed = variants if number == VARIANT_QUESTION else {answers.get(number)}
        if value is None or value not in allowed or draft is None or draft.flagged:
            wrong.append(number)
    return wrong


@pytest.mark.pilot
def test_pilot_key_matches_ba_table() -> None:
    if not EXAM.is_dir() or not TABLE.is_file():
        pytest.skip("pilot exam files are not available")
    assert mismatched_numbers() == []


@pytest.mark.pilot
def test_pilot_student_pdf_has_15_pages() -> None:
    if not EXAM.is_dir():
        pytest.skip("pilot exam files are not available")
    document = pdfium.PdfDocument(str(next((EXAM / "exam").glob("*Student.pdf"))))
    try:
        assert len(document) == 15
    finally:
        document.close()
