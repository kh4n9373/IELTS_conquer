from dataclasses import dataclass

import psycopg
import pytest

from npa_api.core.db import Identity, with_identity
from npa_api.testing import TestDb


@dataclass
class World:
    teacher_a: Identity
    teacher_b: Identity
    student: Identity
    class_a: object


@pytest.fixture(scope="module")
def w(db: TestDb) -> World:
    a = db.insert_user("a@test.local", "teacher")
    b = db.insert_user("b@test.local", "teacher")
    s = db.insert_user("s@test.local", "student", a)
    teacher_a = Identity(a, "teacher", a)
    with with_identity(db.app_pool, teacher_a) as tx:
        row = tx.execute(
            "insert into classes (teacher_id, name) values (%s, 'Lop A') returning id", (a,)
        ).fetchone()
    return World(teacher_a, Identity(b, "teacher", b), Identity(s, "student", a), row["id"])


def test_denies_app_role_without_identity(db: TestDb) -> None:
    with pytest.raises(psycopg.Error, match="permission denied"), db.app_pool.connection() as conn:
        conn.execute("select * from classes")


def test_no_rows_for_role_without_user_id(db: TestDb, w: World) -> None:
    with db.app_pool.connection() as conn:
        conn.execute("set local role npa_teacher")
        assert conn.execute("select id from classes").fetchall() == []
        conn.rollback()


def test_never_exposes_password_hashes(db: TestDb, w: World) -> None:
    with (
        pytest.raises(psycopg.Error, match="permission denied"),
        with_identity(db.app_pool, w.teacher_a) as tx,
    ):
        tx.execute("select password_hash from users")


def test_sessions_unreadable(db: TestDb, w: World) -> None:
    with (
        pytest.raises(psycopg.Error, match="permission denied"),
        with_identity(db.app_pool, w.teacher_a) as tx,
    ):
        tx.execute("select * from sessions")


def test_isolates_classes_between_teachers(db: TestDb, w: World) -> None:
    with with_identity(db.app_pool, w.teacher_b) as tx:
        assert tx.execute("select id from classes where id = %s", (w.class_a,)).fetchall() == []


def test_stops_writing_class_under_another_teacher(db: TestDb, w: World) -> None:
    with (
        pytest.raises(psycopg.Error, match="row-level security"),
        with_identity(db.app_pool, w.teacher_b) as tx,
    ):
        tx.execute("insert into classes (teacher_id, name) values (%s, 'x')", (w.teacher_a.user_id,))


def test_hides_class_from_student_until_member(db: TestDb, w: World) -> None:
    def read() -> list[object]:
        with with_identity(db.app_pool, w.student) as tx:
            return tx.execute("select id from classes").fetchall()

    assert len(read()) == 0
    with with_identity(db.app_pool, w.teacher_a) as tx:
        tx.execute(
            "insert into class_members (class_id, teacher_id, student_id) values (%s, %s, %s)",
            (w.class_a, w.teacher_a.user_id, w.student.user_id),
        )
    assert len(read()) == 1
    with with_identity(db.app_pool, w.teacher_a) as tx:
        tx.execute("update class_members set removed_at = now() where student_id = %s", (w.student.user_id,))
    assert len(read()) == 0


def test_identity_does_not_leak_to_next_transaction(db: TestDb, w: World) -> None:
    with with_identity(db.app_pool, w.teacher_a) as tx:
        tx.execute("select 1")
    # Pool size is small, and every connection must come back clean.
    for _ in range(5):
        with db.app_pool.connection() as conn:
            row = conn.execute(
                "select current_user as who, current_setting('app.user_id', true) as uid"
            ).fetchone()
        assert row["who"] == "npa_app"
        assert not row["uid"]


def test_rolls_back_when_block_raises(db: TestDb, w: World) -> None:
    with pytest.raises(RuntimeError, match="boom"), with_identity(db.app_pool, w.teacher_a) as tx:
        tx.execute(
            "insert into classes (teacher_id, name) values (%s, 'rollback-me')", (w.teacher_a.user_id,)
        )
        raise RuntimeError("boom")
    with with_identity(db.app_pool, w.teacher_a) as tx:
        assert tx.execute("select id from classes where name = 'rollback-me'").fetchall() == []
