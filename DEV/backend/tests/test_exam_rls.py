from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
import pytest

from npa_api.core.db import Identity, with_identity
from npa_api.testing import TestDb

TABLES = [
    "tests",
    "test_assets",
    "sections",
    "questions",
    "page_texts",
    "ocr_jobs",
    "keys.answer_key_versions",
    "keys.answer_key_items",
]


@dataclass
class World:
    a: Identity
    b: Identity
    student: Identity
    test_a: UUID
    asset_a: UUID
    section_a: UUID
    version_a: UUID


@pytest.fixture(scope="module")
def w(db: TestDb) -> World:
    a = db.insert_user("exam-a@test.local", "teacher")
    b = db.insert_user("exam-b@test.local", "teacher")
    s = db.insert_user("exam-s@test.local", "student", a)
    ident_a = Identity(a, "teacher", a)
    with with_identity(db.app_pool, ident_a) as tx:
        test_id = tx.execute(
            """insert into tests (teacher_id, title, test_type, source_type, duration_minutes)
               values (%s, 'T1', 'ielts_standard', 'self_authored', 60) returning id""",
            (a,),
        ).fetchone()["id"]
        asset = tx.execute(
            """insert into test_assets (test_id, teacher_id, kind, storage_key, sha256, byte_size)
               values (%s, %s, 'student_pdf', 'k', 'h', 10) returning id""",
            (test_id, a),
        ).fetchone()["id"]
        section = tx.execute(
            "insert into sections (test_id, teacher_id, position, title) "
            "values (%s, %s, 1, 'S1') returning id",
            (test_id, a),
        ).fetchone()["id"]
        tx.execute(
            """insert into questions (test_id, teacher_id, section_id, number, question_type)
               values (%s, %s, %s, 1, 'tfng')""",
            (test_id, a, section),
        )
        tx.execute(
            "insert into page_texts (asset_id, teacher_id, page, text) values (%s, %s, 1, 'x')",
            (asset, a),
        )
        tx.execute("insert into ocr_jobs (asset_id, teacher_id) values (%s, %s)", (asset, a))
        version = tx.execute(
            "insert into keys.answer_key_versions (test_id, teacher_id, version) "
            "values (%s, %s, 1) returning id",
            (test_id, a),
        ).fetchone()["id"]
        tx.execute(
            """insert into keys.answer_key_items
                 (version_id, teacher_id, question_number, answers, origin)
               values (%s, %s, 1, '{TRUE}', 'manual')""",
            (version, a),
        )
    student = Identity(s, "student", a)
    return World(ident_a, Identity(b, "teacher", b), student, test_id, asset, section, version)


def test_student_role_cannot_use_keys_schema(db: TestDb, w: World) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        with_identity(db.app_pool, w.student) as tx,
    ):
        tx.execute("select * from keys.answer_key_items")


def test_admin_role_cannot_use_keys_schema(db: TestDb) -> None:
    admin = Identity(db.insert_user("exam-admin@test.local", "admin"), "admin", None)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, admin) as tx:
        tx.execute("select * from keys.answer_key_versions")


@pytest.mark.parametrize("table", [t for t in TABLES if not t.startswith("keys.")])
def test_student_role_has_no_grant_on_tests(db: TestDb, w: World, table: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, w.student) as tx:
        tx.execute(f"select * from {table}".encode())


@pytest.mark.parametrize("table", TABLES)
def test_teacher_a_sees_own_rows(db: TestDb, w: World, table: str) -> None:
    with with_identity(db.app_pool, w.a) as tx:
        assert len(tx.execute(f"select 1 from {table}".encode()).fetchall()) == 1


@pytest.mark.parametrize("table", TABLES)
def test_teacher_b_sees_no_teacher_a_rows(db: TestDb, w: World, table: str) -> None:
    with with_identity(db.app_pool, w.b) as tx:
        assert tx.execute(f"select 1 from {table}".encode()).fetchall() == []


def test_teacher_b_cannot_update_or_delete_teacher_a_rows(db: TestDb, w: World) -> None:
    with with_identity(db.app_pool, w.b) as tx:
        assert tx.execute("update tests set title = 'x' where id = %s", (w.test_a,)).rowcount == 0
        assert tx.execute("delete from tests where id = %s", (w.test_a,)).rowcount == 0


def test_teacher_cannot_insert_for_other_teacher(db: TestDb, w: World) -> None:
    with (
        pytest.raises(psycopg.Error, match="row-level security"),
        with_identity(db.app_pool, w.b) as tx,
    ):
        tx.execute(
            """insert into tests (teacher_id, title, test_type, source_type, duration_minutes)
               values (%s, 'x', 'course', 'other', 10)""",
            (w.a.user_id,),
        )


def test_course_and_tenant_fks_reject_cross_tenant_section(db: TestDb, w: World) -> None:
    # Teacher B owns a test, but points a section at teacher A's test under B's own tenant id.
    with with_identity(db.app_pool, w.b) as tx:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            tx.execute(
                "insert into sections (test_id, teacher_id, position, title) values (%s, %s, 9, 'x')",
                (w.test_a, w.b.user_id),
            )


def test_question_must_match_section_test(db: TestDb, w: World) -> None:
    with with_identity(db.app_pool, w.a) as tx:
        other = tx.execute(
            """insert into tests (teacher_id, title, test_type, source_type, duration_minutes)
               values (%s, 'T2', 'course', 'other', 30) returning id""",
            (w.a.user_id,),
        ).fetchone()["id"]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            tx.execute(
                """insert into questions (test_id, teacher_id, section_id, number, question_type)
                   values (%s, %s, %s, 5, 'tfng')""",
                (other, w.a.user_id, w.section_a),
            )


@pytest.mark.parametrize(("test_type", "minutes"), [("bad", 10), ("course", 301), ("course", 0)])
def test_test_check_constraints(db: TestDb, w: World, test_type: str, minutes: int) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), with_identity(db.app_pool, w.a) as tx:
        tx.execute(
            """insert into tests (teacher_id, title, test_type, source_type, duration_minutes)
               values (%s, 'x', %s, 'other', %s)""",
            (w.a.user_id, test_type, minutes),
        )


def test_question_type_check(db: TestDb, w: World) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), with_identity(db.app_pool, w.a) as tx:
        tx.execute(
            """insert into questions (test_id, teacher_id, section_id, number, question_type)
               values (%s, %s, %s, 7, 'essay')""",
            (w.test_a, w.a.user_id, w.section_a),
        )


def test_ocr_claim_not_executable_by_teacher_role(db: TestDb, w: World) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, w.a) as tx:
        tx.execute("select * from app.ocr_claim(now(), now() + interval '5 minutes')")


def test_ocr_finish_not_executable_by_teacher_role(db: TestDb, w: World) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), with_identity(db.app_pool, w.a) as tx:
        tx.execute("select app.ocr_finish(gen_random_uuid(), 1, 'failed', null, null, now())")


def _job(db: TestDb, w: World) -> UUID:
    with db.owner_pool.connection() as conn:
        conn.execute(
            "update test_assets set ocr_approved_at = now(), ocr_approved_by = teacher_id where id = %s",
            (w.asset_a,),
        )
        row = conn.execute(
            "insert into ocr_jobs (asset_id, teacher_id) values (%s, %s) returning id",
            (w.asset_a, w.a.user_id),
        ).fetchone()
    assert row is not None
    job: UUID = row["id"]
    return job


def _state(db: TestDb, job: UUID) -> dict[str, Any]:
    with db.owner_pool.connection() as conn:
        row = conn.execute("select * from ocr_jobs where id = %s", (job,)).fetchone()
    assert row is not None
    return row


def test_ocr_claim_and_finish_lifecycle(db: TestDb, w: World) -> None:
    from datetime import UTC, datetime, timedelta

    with db.owner_pool.connection() as conn:
        conn.execute("delete from ocr_jobs")
    job = _job(db, w)
    now = datetime(2026, 11, 22, 3, tzinfo=UTC)
    with db.app_pool.connection() as conn:
        claimed = conn.execute(
            "select * from app.ocr_claim(%s, %s)", (now, now + timedelta(minutes=5))
        ).fetchall()
    assert [c["id"] for c in claimed] == [job]
    assert claimed[0]["attempts"] == 1
    assert set(claimed[0]) == {"id", "asset_id", "teacher_id", "attempts", "provider_job_id"}
    assert _state(db, job)["state"] == "running"
    # Leased job is not claimable; after the lease expires it is.
    with db.app_pool.connection() as conn:
        assert conn.execute("select * from app.ocr_claim(%s, %s)", (now, now)).fetchall() == []
        again = conn.execute(
            "select * from app.ocr_claim(%s, %s)",
            (now + timedelta(minutes=6), now + timedelta(minutes=11)),
        ).fetchall()
    assert again[0]["attempts"] == 2
    retry_at = now + timedelta(minutes=20)
    with db.app_pool.connection() as conn:
        # The attempt that lost the lease is fenced out.
        stale = conn.execute(
            "select app.ocr_finish(%s, 1, 'failed', 'provider_error', null, %s) as ok", (job, now)
        ).fetchone()
        assert stale is not None and stale["ok"] is False
        current = conn.execute(
            "select app.ocr_finish(%s, 2, 'queued', 'provider_error', %s, %s) as ok", (job, retry_at, now)
        ).fetchone()
        assert current is not None and current["ok"] is True
    row = _state(db, job)
    assert (row["state"], row["finished_at"], row["lease_until"]) == ("queued", None, retry_at)
    with db.app_pool.connection() as conn:
        assert conn.execute("select * from app.ocr_claim(%s, %s)", (now, now)).fetchall() == []
        third = conn.execute("select * from app.ocr_claim(%s, %s)", (retry_at, retry_at)).fetchall()
        assert third[0]["attempts"] == 3
        conn.execute("select app.ocr_finish(%s, 3, 'failed', 'provider_timeout', null, %s)", (job, now))
    row = _state(db, job)
    assert (row["state"], row["error_code"], row["finished_at"], row["lease_until"]) == (
        "failed",
        "provider_timeout",
        now,
        None,
    )


def test_ocr_claim_fails_expired_job_at_three_attempts(db: TestDb, w: World) -> None:
    from datetime import UTC, datetime, timedelta

    with db.owner_pool.connection() as conn:
        conn.execute("delete from ocr_jobs")
    job = _job(db, w)
    now = datetime(2026, 11, 22, 3, tzinfo=UTC)
    with db.owner_pool.connection() as conn:
        conn.execute(
            "update ocr_jobs set state = 'running', attempts = 3, lease_until = %s where id = %s",
            (now - timedelta(seconds=1), job),
        )
    with db.app_pool.connection() as conn:
        assert conn.execute("select * from app.ocr_claim(%s, %s)", (now, now)).fetchall() == []
    row = _state(db, job)
    assert (row["state"], row["error_code"], row["attempts"]) == ("failed", "lease_expired", 3)


def test_ocr_finish_touches_only_given_job_and_validates_state(db: TestDb, w: World) -> None:
    from datetime import UTC, datetime, timedelta

    with db.owner_pool.connection() as conn:
        conn.execute("delete from ocr_jobs")
    one, two = _job(db, w), _job(db, w)
    now = datetime(2026, 11, 22, 3, tzinfo=UTC)
    with db.owner_pool.connection() as conn:
        conn.execute(
            "update ocr_jobs set state = 'running', attempts = 1, lease_until = %s",
            (now + timedelta(minutes=5),),
        )
    with db.app_pool.connection() as conn:
        conn.execute("select app.ocr_finish(%s, 1, 'failed', 'bad_result', null, %s)", (one, now))
    assert _state(db, one)["state"] == "failed"
    assert _state(db, two)["state"] == "running"
    for state in ("running", "done"):
        with pytest.raises(psycopg.Error, match="invalid_state"), db.app_pool.connection() as conn:
            conn.execute("select app.ocr_finish(%s, 1, %s, null, null, %s)", (two, state, now))
    with db.app_pool.connection() as conn:  # unknown job is a safe no-op
        row = conn.execute(
            "select app.ocr_finish(gen_random_uuid(), 1, 'failed', 'bad_result', null, %s) as ok", (now,)
        ).fetchone()
    assert row is not None and row["ok"] is False


def _version(tx: Any, w: World, number: int, approved: bool = False) -> UUID:
    row = tx.execute(
        """insert into keys.answer_key_versions (test_id, teacher_id, version, approved_at, approved_by)
           values (%s, %s, %s, %s, %s) returning id""",
        (
            w.test_a,
            w.a.user_id,
            number,
            "2026-11-22" if approved else None,
            w.a.user_id if approved else None,
        ),
    ).fetchone()
    return row["id"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("column", ["approved", "reviewed"])
@pytest.mark.parametrize("who", ["other_teacher", "student", "unpaired"])
def test_approval_and_review_provenance_checks(db: TestDb, w: World, column: str, who: str) -> None:
    by = {"other_teacher": w.b.user_id, "student": w.student.user_id, "unpaired": w.a.user_id}[who]
    at = None if who == "unpaired" and column == "approved" else "2026-11-22"
    if column == "approved":
        sql = (
            "insert into keys.answer_key_versions (test_id, teacher_id, version, approved_at, approved_by)"
            " values (%s, %s, 50, %s, %s)"
        )
        args: tuple[Any, ...] = (w.test_a, w.a.user_id, at, by)
        if who == "unpaired":
            args = (w.test_a, w.a.user_id, None, by)
    else:
        sql = (
            "insert into keys.answer_key_items"
            " (version_id, teacher_id, question_number, origin, reviewed_at, reviewed_by)"
            " values (%s, %s, 50, 'manual', %s, %s)"
        )
        args = (w.version_a, w.a.user_id, None if who == "unpaired" else at, by)
    with pytest.raises(psycopg.errors.CheckViolation), with_identity(db.app_pool, w.a) as tx:
        tx.execute(sql.encode(), args)


def test_approved_key_version_cannot_be_deleted(db: TestDb, w: World) -> None:
    with with_identity(db.app_pool, w.a) as tx:
        approved = _version(tx, w, 60, approved=True)
    with pytest.raises(psycopg.Error, match="immutable"), with_identity(db.app_pool, w.a) as tx:
        tx.execute("delete from keys.answer_key_versions where id = %s", (approved,))
    with with_identity(db.app_pool, w.a) as tx:  # an unapproved draft version can still go
        draft = _version(tx, w, 61)
        assert tx.execute("delete from keys.answer_key_versions where id = %s", (draft,)).rowcount == 1


def test_deleting_test_with_key_versions_is_restricted(db: TestDb, w: World) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation), with_identity(db.app_pool, w.a) as tx:
        tx.execute("delete from tests where id = %s", (w.test_a,))
