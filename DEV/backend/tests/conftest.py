from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fakes import FakeGoogle, MemoryFileStore
from fastapi.testclient import TestClient

from npa_api.app import build_app
from npa_api.core.mailer import Mail
from npa_api.core.security import hash_password
from npa_api.testing import TestDb, create_test_db

PASSWORD = "correct-horse-battery"
PASSWORD_HASH = hash_password(PASSWORD)


@dataclass
class Result:
    status: int
    json: Any
    headers: Any


@dataclass
class Harness:
    db: TestDb
    client: TestClient
    mail: list[Mail] = field(default_factory=list)
    store: MemoryFileStore = field(default_factory=MemoryFileStore)
    now: datetime = datetime(2026, 11, 22, 3, tzinfo=UTC)

    def send(self, mail: Mail) -> None:
        self.mail.append(mail)

    @property
    def app(self) -> Any:
        return self.client.app

    def create_user(
        self, email: str, role: str, teacher_id: UUID | None = None, status: str = "active"
    ) -> UUID:
        return self.db.insert_user(email, role, teacher_id, PASSWORD_HASH, status)

    def call(self, cookie: str | None, method: str, url: str, body: Any = None) -> Result:
        # The client must not remember cookies: each call acts only as the caller named.
        self.client.cookies.clear()
        headers = {"cookie": cookie} if cookie else {}
        kwargs: dict[str, Any] = {} if body is None else {"json": body}
        res = self.client.request(method, url, headers=headers, **kwargs)
        try:
            payload = res.json() if res.content else None
        except ValueError:
            payload = res.text
        return Result(res.status_code, payload, res.headers)

    def login(self, email: str, password: str = PASSWORD) -> str:
        res = self.call(None, "POST", "/api/auth/login", {"email": email, "password": password})
        assert res.status == 200, f"login failed: {res.status}"
        return res.headers["set-cookie"].split(";")[0]

    def last_token(self) -> str:
        return self.mail[-1].text.split("token=")[1].strip()

    @contextmanager
    def later(self, **delta: float) -> Iterator[None]:
        original = self.now
        self.now = original + timedelta(**delta)
        try:
            yield
        finally:
            self.now = original

    def owner_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.db.owner_pool.connection() as conn:
            result = conn.execute(sql.encode() if not params else sql, params or None)
            return result.fetchall() if result.description else []


@pytest.fixture(scope="module")
def db() -> Iterator[TestDb]:
    database = create_test_db()
    yield database
    database.destroy()


@pytest.fixture(scope="module")
def h(db: TestDb) -> Iterator[Harness]:
    harness = Harness(db=db, client=None)  # type: ignore[arg-type]
    app = build_app(
        pool=db.app_pool,
        mailer=harness,
        public_url="http://app.test",
        secure_cookies=False,
        now=lambda: harness.now,
        rate_limit=False,
        store=harness.store,
        google=FakeGoogle(),
    )
    with TestClient(app) as client:
        harness.client = client
        yield harness
