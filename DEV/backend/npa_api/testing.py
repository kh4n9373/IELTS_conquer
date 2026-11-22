"""Throwaway databases for tests. Never imported by the running API."""

import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg

from .core.db import Pool, migrate, open_pool

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgres://npa_owner@127.0.0.1:54329/postgres")


def url_for(database: str, user: str | None = None) -> str:
    parts = urlsplit(ADMIN_URL)
    netloc = parts.netloc
    if user:
        netloc = f"{user}@{netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((parts.scheme, netloc, f"/{database}", parts.query, parts.fragment))


@dataclass
class TestDb:
    __test__ = False

    name: str
    url: str
    app_pool: Pool  # Connects as npa_app, exactly like the running API.
    owner_pool: Pool  # Connects as the owner. Only for arranging test data.

    def insert_user(
        self,
        email: str,
        role: str,
        teacher_id: UUID | None = None,
        password_hash: str = "unused",
        status: str = "active",
    ) -> UUID:
        with self.owner_pool.connection() as conn:
            row = conn.execute(
                """insert into users (email, password_hash, display_name, role, teacher_id, status)
                   values (%s, %s, %s, %s, %s, %s) returning id""",
                (email, password_hash, email.split("@")[0], role, teacher_id, status),
            ).fetchone()
        assert row is not None
        user_id: UUID = row["id"]
        return user_id

    def destroy(self) -> None:
        self.app_pool.close()
        self.owner_pool.close()
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"drop database if exists {self.name} with (force)".encode())


def create_test_db() -> TestDb:
    name = f"npa_test_{secrets.token_hex(6)}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"create database {name}".encode())
    url = url_for(name)
    migrate(url)
    return TestDb(
        name=name,
        url=url,
        app_pool=open_pool(url_for(name, "npa_app"), max_size=5),
        owner_pool=open_pool(url, max_size=2),
    )
