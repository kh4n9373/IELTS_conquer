"""Answer key parsing, normalization and storage."""

import re
from collections.abc import Mapping, Sequence
from typing import Literal
from uuid import UUID

from ..core.db import Conn
from ..core.http import storable

Origin = Literal["manual", "text_layer", "scan"]
MAX_ANSWER_LENGTH = 100
MAX_ANSWERS = 20

# "1. E", "1 E", "1) E": the separator is required so a bare "12" is not read as "1" + "2".
LINE = re.compile(r"([0-9]{1,3})(?:\s*[.)]\s*|\s+)(.+)")


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
        parsed[number] = normalize_answers([p for p in match.group(2).split(";") if p.strip()])
    if not parsed:
        raise ValueError("empty key")
    return parsed


def upsert_items(tx: Conn, version_id: UUID, items: Mapping[int, Sequence[str]], origin: Origin) -> None:
    """Stores answers. A changed answer loses its review and origin; an unchanged one keeps both."""
    for number, answers in items.items():
        tx.execute(
            """insert into keys.answer_key_items (version_id, teacher_id, question_number, answers, origin)
               select id, teacher_id, %s, %s, %s from keys.answer_key_versions where id = %s
               on conflict (version_id, question_number) do update set
                 answers = excluded.answers,
                 origin = case when keys.answer_key_items.answers = excluded.answers
                               then keys.answer_key_items.origin else excluded.origin end,
                 reviewed_at = case when keys.answer_key_items.answers = excluded.answers
                                    then keys.answer_key_items.reviewed_at end,
                 reviewed_by = case when keys.answer_key_items.answers = excluded.answers
                                    then keys.answer_key_items.reviewed_by end""",
            (number, list(answers), origin, version_id),
        )
