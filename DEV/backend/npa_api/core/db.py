"""Database access: per-transaction identity, audit writes and migrations."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

Role = Literal["student", "teacher", "admin"]
Conn = psycopg.Connection[DictRow]
Pool = ConnectionPool[Conn]

MIGRATIONS_DIR = Path(__file__).parents[1] / "migrations"
# Arbitrary constant: serializes concurrent migration runs.
MIGRATION_LOCK = 727401

# Fixed map: a role name is never interpolated from input.
SET_ROLE: dict[str, Any] = {
    "student": "set local role npa_student",
    "teacher": "set local role npa_teacher",
    "admin": "set local role npa_admin",
}


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    role: Role
    teacher_id: UUID | None


def open_pool(url: str, max_size: int = 20) -> Pool:
    # Auto-prepare is off so no server-side plan outlives a SET LOCAL ROLE.
    return ConnectionPool(
        url,
        connection_class=psycopg.Connection[DictRow],
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
        min_size=1,
        max_size=max_size,
        open=True,
    )


@contextmanager
def with_identity(pool: Pool, identity: Identity) -> Iterator[Conn]:
    """Runs a transaction under the caller's database role and identity.

    Settings are transaction-local, so they cannot leak through the pool. The pool
    commits on clean exit, rolls back on an exception, and discards broken connections.
    """
    set_role = SET_ROLE.get(identity.role)
    if set_role is None:
        raise ValueError("Unknown role")
    with pool.connection() as conn:
        conn.execute(
            "select set_config('app.user_id', %s, true), set_config('app.teacher_id', %s, true)",
            (str(identity.user_id), str(identity.teacher_id) if identity.teacher_id else ""),
        )
        conn.execute(set_role)
        yield conn


def write_audit(
    tx: Conn, *, action: str, entity: str, entity_id: UUID | str, before: Any = None, after: Any = None
) -> None:
    """The actor and tenant come from the transaction identity, never from the caller."""
    tx.execute(
        "insert into audit_log (action, entity, entity_id, before, after) values (%s, %s, %s, %s, %s)",
        (
            action,
            entity,
            str(entity_id),
            None if before is None else Jsonb(before),
            None if after is None else Jsonb(after),
        ),
    )


def migrate(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            """create table if not exists schema_migrations (
                 name text primary key,
                 applied_at timestamptz not null default now()
               )"""
        )
        applied = {row[0] for row in conn.execute("select name from schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            try:
                with conn.transaction():
                    # No parameters, so psycopg sends the whole multi-statement file as is.
                    conn.execute(path.read_text(encoding="utf-8").encode())
                    conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            except psycopg.Error as err:
                raise RuntimeError(f"Migration {path.name} failed: {err}") from err
        # Closing the connection releases the session-level advisory lock.
