"""Read-only readiness count for a test. Counts and booleans only: answer values are never selected."""

from uuid import UUID

from .core.db import Conn

# cardinality() reports how many accepted variants Q23 has without returning any of them.
_SQL = """
with k as (
  select id, approved_at from keys.answer_key_versions
  where test_id = %(t)s order by (approved_at is not null) desc, version desc limit 1
)
select
  coalesce((select status = 'published' from tests where id = %(t)s), false) as published,
  (select count(*) from questions where test_id = %(t)s)::int as questions,
  (select count(*) from keys.answer_key_items i join k on i.version_id = k.id
    where i.reviewed_at is not null)::int as reviewed_answers,
  coalesce((select approved_at is not null from k), false) as approved,
  coalesce((select cardinality(i.answers) from keys.answer_key_items i join k on i.version_id = k.id
    where i.question_number = 23), 0)::int as variants_at_q23
"""


def pilot_counts(tx: Conn, test_id: UUID | str) -> dict[str, int | bool]:
    row = tx.execute(_SQL, {"t": str(test_id)}).fetchone()
    if row is None:
        raise LookupError("readiness query returned no row")
    return {
        "published": bool(row["published"]),
        "questions": int(row["questions"]),
        "reviewedAnswers": int(row["reviewed_answers"]),
        "approved": bool(row["approved"]),
        "variantsAtQ23": int(row["variants_at_q23"]),
    }
