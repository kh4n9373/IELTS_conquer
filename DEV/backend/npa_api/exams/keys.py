"""Answer key parsing, normalization and storage."""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from ..core.db import Conn
from ..core.http import storable

Origin = Literal["manual", "text_layer", "scan"]
MAX_ANSWER_LENGTH = 100
MAX_ANSWERS = 20

# "1. E", "1) E", "1: E", "1 - E", "1 E". Whitespace must follow, so "12" and "1.5 km" are not pairs.
NUMBERED = r"([0-9]{1,3})(?:\s*[.):-]\s+|\s+)(?![.):\-–—])"
LINE = re.compile(NUMBERED + r"(.+)")


def normalize_answers(answers: Sequence[str]) -> list[str]:
    """Trims, drops case-insensitive duplicates (first spelling wins) and enforces the limits."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in answers:
        answer = storable(raw).strip()
        if not answer or len(answer) > MAX_ANSWER_LENGTH:
            raise ValueError("invalid answer")
        if answer.casefold() not in seen:
            seen.add(answer.casefold())
            out.append(answer)
    if not out or len(out) > MAX_ANSWERS:
        raise ValueError("invalid answer count")
    return out


def parse_pasted_key(text: str) -> dict[int, list[str]]:
    """Parses lines like `23. three; platform three; 3`. Raises ValueError on anything else."""
    parsed: dict[int, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LINE.fullmatch(line.strip())
        if match is None:
            raise ValueError("unreadable line")
        number = int(match.group(1))
        if number < 1 or number in parsed:
            raise ValueError("invalid or repeated number")
        parsed[number] = normalize_answers(match.group(2).split(";"))
    if not parsed:
        raise ValueError("empty key")
    return parsed


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


def upsert_items(tx: Conn, version_id: UUID, items: Mapping[int, Sequence[str]], origin: Origin) -> None:
    """Stores answers under `origin`. A changed answer loses its review; an unchanged one keeps it."""
    for number, answers in items.items():
        tx.execute(
            """insert into keys.answer_key_items (version_id, teacher_id, question_number, answers, origin)
               select id, teacher_id, %s, %s, %s from keys.answer_key_versions where id = %s
               on conflict (version_id, question_number) do update set
                 answers = excluded.answers,
                 origin = excluded.origin,
                 reviewed_at = case when keys.answer_key_items.answers = excluded.answers
                                    then keys.answer_key_items.reviewed_at end,
                 reviewed_by = case when keys.answer_key_items.answers = excluded.answers
                                    then keys.answer_key_items.reviewed_by end,
                 source_page = case when keys.answer_key_items.answers = excluded.answers
                                    then keys.answer_key_items.source_page end,
                 source_bbox = case when keys.answer_key_items.answers = excluded.answers
                                    then keys.answer_key_items.source_bbox end""",
            (number, list(answers), origin, version_id),
        )
