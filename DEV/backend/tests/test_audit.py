import psycopg
import pytest

from npa_api.core.db import Identity, with_identity, write_audit
from npa_api.testing import TestDb


@pytest.fixture(scope="module")
def teachers(db: TestDb) -> tuple[Identity, Identity]:
    a = db.insert_user("a@test.local", "teacher")
    b = db.insert_user("b@test.local", "teacher")
    teacher_a = Identity(a, "teacher", a)
    with with_identity(db.app_pool, teacher_a) as tx:
        write_audit(tx, action="class.create", entity="class", entity_id="c1", after={"name": "A"})
    return teacher_a, Identity(b, "teacher", b)


def test_actor_comes_from_identity(db: TestDb, teachers: tuple[Identity, Identity]) -> None:
    with db.owner_pool.connection() as conn:
        rows = conn.execute("select actor_id, teacher_id, after from audit_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["actor_id"] == teachers[0].user_id
    assert rows[0]["teacher_id"] == teachers[0].user_id
    assert rows[0]["after"] == {"name": "A"}


def test_rejects_forged_actor(db: TestDb, teachers: tuple[Identity, Identity]) -> None:
    a, b = teachers
    with pytest.raises(psycopg.Error, match="row-level security"), with_identity(db.app_pool, b) as tx:
        tx.execute(
            "insert into audit_log (actor_id, action, entity, entity_id) values (%s, 'x', 'x', 'x')",
            (a.user_id,),
        )


@pytest.mark.parametrize(
    "sql", ["update audit_log set action = %s", "delete from audit_log where action <> %s"]
)
def test_denies_update_and_delete_to_app_roles(
    db: TestDb, teachers: tuple[Identity, Identity], sql: str
) -> None:
    with (
        pytest.raises(psycopg.Error, match="permission denied"),
        with_identity(db.app_pool, teachers[0]) as tx,
    ):
        tx.execute(sql, ("tampered",))


@pytest.mark.parametrize("sql", ["update audit_log set action = 'tampered'", "delete from audit_log"])
def test_blocks_changes_for_owner(db: TestDb, teachers: tuple[Identity, Identity], sql: str) -> None:
    with pytest.raises(psycopg.Error, match="append-only"), db.owner_pool.connection() as conn:
        conn.execute(sql)


def test_hides_entries_from_other_teachers(db: TestDb, teachers: tuple[Identity, Identity]) -> None:
    with with_identity(db.app_pool, teachers[1]) as tx:
        assert tx.execute("select id from audit_log").fetchall() == []
