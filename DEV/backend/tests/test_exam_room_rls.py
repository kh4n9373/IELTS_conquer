from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from m3_helpers import make_class_with_students, publish_test

from conftest import Harness
from npa_api.core.db import Identity, with_identity
from npa_api.testing import TestDb

NOW = datetime(2026, 11, 23, 3, tzinfo=UTC)
ROOM_TABLES = ["assignments", "assignment_recipients", "attempts", "attempt_answers"]
TEACHER_TABLES = [*ROOM_TABLES, "late_answers", "keys.grading_runs", "keys.grading_items"]


@dataclass
class World:
    a: Identity
    b: Identity
    student: Identity
    test_id: UUID
    course_id: UUID
    class_id: UUID
    assignment: UUID
    attempt: UUID
    run: UUID


def one(db: TestDb, sql: str, params: tuple[Any, ...]) -> Any:
    with db.owner_pool.connection() as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row["id"]


@pytest.fixture(scope="module")
def w(db: TestDb) -> World:
    a = db.insert_user("room-a@test.local", "teacher")
    b = db.insert_user("room-b@test.local", "teacher")
    s = db.insert_user("room-s@test.local", "student", a)
    test_id = one(
        db,
        """insert into tests (teacher_id, title, test_type, source_type, duration_minutes, status)
           values (%s, 'T', 'ielts_standard', 'self_authored', 60, 'published') returning id""",
        (a,),
    )
    course_id = one(
        db,
        """insert into tests (teacher_id, title, test_type, source_type, duration_minutes, status)
           values (%s, 'C', 'course', 'self_authored', 60, 'published') returning id""",
        (a,),
    )
    section = one(
        db,
        "insert into sections (test_id, teacher_id, position, title) values (%s, %s, 1, 'S') returning id",
        (test_id, a),
    )
    with db.owner_pool.connection() as conn:
        conn.execute(
            """insert into questions (test_id, teacher_id, section_id, number, question_type)
               values (%s, %s, %s, 1, 'tfng')""",
            (test_id, a, section),
        )
    class_id = one(db, "insert into classes (teacher_id, name) values (%s, 'K') returning id", (a,))
    version = one(
        db,
        """insert into keys.answer_key_versions (test_id, teacher_id, version, approved_at, approved_by)
           values (%s, %s, 1, now(), %s) returning id""",
        (test_id, a, a),
    )
    assignment = one(
        db,
        """insert into assignments (teacher_id, test_id, class_id, opens_at, due_at)
           values (%s, %s, %s, %s, %s) returning id""",
        (a, test_id, class_id, NOW, NOW + timedelta(days=1)),
    )
    with db.owner_pool.connection() as conn:
        conn.execute(
            "insert into assignment_recipients (assignment_id, teacher_id, student_id) values (%s, %s, %s)",
            (assignment, a, s),
        )
    attempt = one(
        db,
        """insert into attempts (assignment_id, teacher_id, student_id, number, mode, started_at, deadline_at)
           values (%s, %s, %s, 1, 'exam', %s, %s) returning id""",
        (assignment, a, s, NOW, NOW + timedelta(hours=1)),
    )
    with db.owner_pool.connection() as conn:
        conn.execute(
            """insert into attempt_answers (attempt_id, teacher_id, question_number, value, seq)
               values (%s, %s, 1, 'true', 1)""",
            (attempt, a),
        )
        conn.execute(
            """insert into late_answers (attempt_id, teacher_id, question_number, value)
               values (%s, %s, 1, 'x')""",
            (attempt, a),
        )
    run = one(
        db,
        """insert into keys.grading_runs (attempt_id, teacher_id, run, key_version_id, rules_version,
             test_type, correct, total, percent, band, band_table_version)
           values (%s, %s, 1, %s, 'standard-1', 'ielts_standard', 1, 1, 100, '9.0', 'v1') returning id""",
        (attempt, a, version),
    )
    with db.owner_pool.connection() as conn:
        conn.execute(
            """insert into keys.grading_items
                 (run_id, teacher_id, question_number, given, correct, reason, accepted)
               values (%s, %s, 1, 'true', true, 'correct', '{TRUE}')""",
            (run, a),
        )
    return World(
        Identity(a, "teacher", a),
        Identity(b, "teacher", b),
        Identity(s, "student", a),
        test_id,
        course_id,
        class_id,
        assignment,
        attempt,
        run,
    )


def test_student_role_has_no_path_to_keys(db: TestDb, w: World) -> None:
    for table in ("keys.grading_runs", "keys.grading_items", "keys.answer_key_items"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, w.student) as tx:
            tx.execute(f"select * from {table}".encode())
    for table in ("late_answers",):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, w.student) as tx:
            tx.execute(f"select * from {table}".encode())


def test_student_reads_only_own_room_rows(db: TestDb, w: World) -> None:
    with with_identity(db.app_pool, w.student) as tx:
        for table in ROOM_TABLES:
            assert len(tx.execute(f"select 1 from {table}".encode()).fetchall()) == 1, table


@pytest.mark.parametrize(
    "sql",
    [
        "insert into attempts (assignment_id, teacher_id, student_id, number, mode, started_at, deadline_at)"
        " values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 2, 'exam', now(), now())",
        "update attempts set status = 'submitted'",
        "insert into attempt_answers (attempt_id, teacher_id, question_number, seq)"
        " values (gen_random_uuid(), gen_random_uuid(), 1, 1)",
        "update attempt_answers set value = 'x'",
        "insert into assignments (teacher_id, test_id, class_id, opens_at, due_at)"
        " values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), now(), now())",
    ],
)
def test_student_cannot_write_attempts_directly(db: TestDb, w: World, sql: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, w.student) as tx:
        tx.execute(sql.encode())


@pytest.mark.parametrize(
    "call",
    [
        "app.attempt_store_grade(gen_random_uuid(), 'submitted', now(), '{}'::jsonb, '[]'::jsonb)",
        "app.attempt_lock_for_grading(gen_random_uuid())",
        "app.attempt_due(now(), 10)",
        "app.attempt_result_row(gen_random_uuid(), now())",
    ],
)
def test_student_and_teacher_cannot_call_grading_functions(db: TestDb, w: World, call: str) -> None:
    for who in (w.student, w.a):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, who) as tx:
            tx.execute(f"select {call}".encode())


def test_function_grants_are_explicit(db: TestDb) -> None:
    with db.owner_pool.connection() as conn:
        grants = {
            r["proname"]: r["acl"]
            for r in conn.execute(
                """select proname, proacl::text as acl from pg_proc
                   where pronamespace = 'app'::regnamespace and proname like 'attempt\\_%'"""
            ).fetchall()
        }

    def who(name: str) -> set[str]:
        return {e.split("=")[0] for e in grants[name].strip("{}").split(",")} - {"npa_owner"}

    for name in ("attempt_start", "attempt_claim", "attempt_room", "attempt_save", "attempt_pdf_key"):
        assert who(name) == {"npa_student"}, name  # an empty name would be PUBLIC
    for name in ("attempt_lock_for_grading", "attempt_due", "attempt_store_grade", "attempt_result_row"):
        assert who(name) == {"npa_app"}, name


@pytest.mark.parametrize("table", TEACHER_TABLES)
def test_teacher_a_sees_rows_and_b_sees_none(db: TestDb, w: World, table: str) -> None:
    with with_identity(db.app_pool, w.a) as tx:
        assert len(tx.execute(f"select 1 from {table}".encode()).fetchall()) == 1
    with with_identity(db.app_pool, w.b) as tx:
        assert tx.execute(f"select 1 from {table}".encode()).fetchall() == []


def test_composite_fk_blocks_cross_tenant_rows(db: TestDb, w: World) -> None:
    b = w.b.user_id
    with with_identity(db.app_pool, w.b) as tx:
        # B names A's test and class under B's own tenant id: the pair is not a real row.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            tx.execute(
                "insert into assignments (teacher_id, test_id, class_id, opens_at, due_at)"
                " values (%s, %s, %s, now(), now() + interval '1 day')",
                (b, w.test_id, w.class_id),
            )
    for sql, params in [
        (
            "insert into attempts (assignment_id, teacher_id, student_id, number, mode, status,"
            " started_at, deadline_at) values (%s, %s, %s, 9, 'exam', 'submitted', now(), now())",
            (w.assignment, b, w.student.user_id),
        ),
        (
            "insert into attempt_answers (attempt_id, teacher_id, question_number, seq)"
            " values (%s, %s, 2, 1)",
            (w.attempt, b),
        ),
        (
            "insert into assignment_recipients (assignment_id, teacher_id, student_id) values (%s, %s, %s)",
            (w.assignment, b, db.insert_user("room-s2@test.local", "student", w.a.user_id)),
        ),
    ]:
        with db.owner_pool.connection() as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(sql, params)


def test_assignment_checks(db: TestDb, w: World) -> None:
    a = w.a.user_id
    bad = [
        ("now() + interval '1 day'", "now()", 1, "after_submit", "null"),
        ("now()", "now() + interval '1 day'", 6, "after_submit", "null"),
        ("now()", "now() + interval '1 day'", 1, "scheduled", "null"),
        ("now()", "now() + interval '1 day'", 1, "after_submit", "now()"),
    ]
    for opens, due, attempts, policy, release in bad:
        with db.owner_pool.connection() as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"""insert into assignments (teacher_id, test_id, class_id, opens_at, due_at, max_attempts,
                      release_policy, release_at) values (%s, %s, %s, {opens}, {due}, {attempts},
                      '{policy}', {release})""".encode(),
                (a, w.test_id, w.class_id),
            )


def test_db_rejects_band_on_course_test(db: TestDb, w: World) -> None:
    course_assignment = one(
        db,
        """insert into assignments (teacher_id, test_id, class_id, opens_at, due_at)
           values (%s, %s, %s, %s, %s) returning id""",
        (w.a.user_id, w.course_id, w.class_id, NOW, NOW + timedelta(days=1)),
    )
    with db.owner_pool.connection() as conn:
        conn.execute(
            "insert into assignment_recipients (assignment_id, teacher_id, student_id) values (%s, %s, %s)",
            (course_assignment, w.a.user_id, w.student.user_id),
        )
    attempt = one(
        db,
        """insert into attempts (assignment_id, teacher_id, student_id, number, mode, started_at, deadline_at)
           values (%s, %s, %s, 1, 'exam', %s, %s) returning id""",
        (course_assignment, w.a.user_id, w.student.user_id, NOW, NOW + timedelta(hours=1)),
    )
    version = one(db, "select id from keys.answer_key_versions where teacher_id = %s limit 1", (w.a.user_id,))
    sql = """insert into keys.grading_runs (attempt_id, teacher_id, run, key_version_id, rules_version,
               test_type, correct, total, percent, band, band_table_version)
             values (%s, %s, 1, %s, 'standard-1', %s, 1, 1, 100, %s, %s)"""
    # Honest course row with a band, then a row that lies about its test type.
    for test_type, band, table in [
        ("course", "7.0", "v1"),
        ("ielts_standard", "7.0", "v1"),
        ("course", None, "v1"),
    ]:
        with db.owner_pool.connection() as conn, pytest.raises(psycopg.Error):
            conn.execute(sql, (attempt, w.a.user_id, version, test_type, band, table))
    with db.owner_pool.connection() as conn:
        conn.execute(sql, (attempt, w.a.user_id, version, "course", None, None))


def test_grading_records_are_immutable(db: TestDb, w: World) -> None:
    for sql in [
        "update keys.grading_runs set correct = 0",
        "delete from keys.grading_runs",
        "update keys.grading_items set correct = false",
        "delete from keys.grading_items",
    ]:
        with db.owner_pool.connection() as conn, pytest.raises(psycopg.Error, match="immutable"):
            conn.execute(sql.encode())


def test_one_in_progress_attempt_per_student(db: TestDb, w: World) -> None:
    sql = """insert into attempts
               (assignment_id, teacher_id, student_id, number, mode, started_at, deadline_at)
             values (%s, %s, %s, %s, 'exam', now(), now())"""
    for number in (2, 1):  # a second open attempt, then a repeated number
        with db.owner_pool.connection() as conn, pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(sql, (w.assignment, w.a.user_id, w.student.user_id, number))


def test_m2_rows_survive_migration(db: TestDb, w: World) -> None:
    with db.owner_pool.connection() as conn:
        t = conn.execute("select grading_rules from tests where id = %s", (w.test_id,)).fetchone()
        q = conn.execute("select answer_group from questions where test_id = %s", (w.test_id,)).fetchone()
        c = conn.execute("select is_trial from classes where id = %s", (w.class_id,)).fetchone()
    assert t is not None and q is not None and c is not None
    assert (t["grading_rules"], q["answer_group"], c["is_trial"]) == ("standard-1", None, False)
    with db.owner_pool.connection() as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("update tests set grading_rules = 'other' where id = %s", (w.test_id,))


def test_m3_helpers_build_a_published_test_and_class(h: Harness) -> None:
    h.create_user("room-h@test.local", "teacher")
    cookie = h.login("room-h@test.local")
    tid = publish_test(h, cookie, answers={1: ["a"], 2: ["b"]}, rules="pilot-1")
    rows = h.owner_rows("select status, grading_rules from tests where id = %s", (tid,))
    assert (rows[0]["status"], rows[0]["grading_rules"]) == ("published", "pilot-1")
    _, students = make_class_with_students(h, cookie, 2)
    assert len(students) == 2 and all(s.cookie for s in students)
