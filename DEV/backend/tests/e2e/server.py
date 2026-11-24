"""Test-only API: fresh database, one admin, and mail written to a file the test can read."""

import hashlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Request
from fastapi.responses import Response

from npa_api.app import build_app
from npa_api.core.mailer import Mail
from npa_api.core.security import hash_password
from npa_api.testing import create_test_db

sys.path.insert(0, str(Path(__file__).parents[1]))
from fakes import MemoryFileStore  # noqa: E402

MAIL_FILE = Path(os.environ["E2E_MAIL_FILE"])


class FileMailer:
    def send(self, mail: Mail) -> None:
        with MAIL_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{mail.text}\n")


class BrowserFileStore(MemoryFileStore):
    """In-memory store whose "signed" URLs point back at this API, so a browser can render the PDFs."""

    def signed_url(self, key: str, seconds: int) -> str:
        return f"/api/e2e-files/{hashlib.sha256(key.encode()).hexdigest()}"


MAIL_FILE.write_text("", encoding="utf-8")
db = create_test_db()
try:
    db.insert_user("admin@test.local", "admin", password_hash=hash_password("admin-password-123"))
    store = BrowserFileStore()
    app = build_app(
        pool=db.app_pool,
        mailer=FileMailer(),
        public_url=os.environ["PUBLIC_URL"],
        secure_cookies=False,
        rate_limit=False,
        store=store,
    )

    @app.get("/api/e2e-files/{name}")
    def e2e_file(name: str) -> Response:
        for key, data in store.objects.items():
            if hashlib.sha256(key.encode()).hexdigest() == name:
                return Response(data, media_type="application/pdf")
        return Response(status_code=404)

    @app.get("/api/e2e/whoami")
    def e2e_whoami() -> dict[str, str]:
        return {"nonce": os.environ["E2E_NONCE"]}

    @app.post("/api/e2e/seed-class")
    async def e2e_seed_class(request: Request) -> dict[str, Any]:
        """Invented teacher, class and students with a known password, so a room test skips sign-up."""
        body = await request.json()
        password_hash = hash_password(body["password"])
        teacher_id = db.insert_user(body["teacher"], "teacher", password_hash=password_hash)
        with db.owner_pool.connection() as conn:
            row = conn.execute(
                "insert into classes (teacher_id, name) values (%s, 'Room class') returning id", (teacher_id,)
            ).fetchone()
            assert row is not None
            for email in body["students"]:
                student_id = db.insert_user(email, "student", teacher_id, password_hash)
                conn.execute(
                    "insert into class_members (class_id, teacher_id, student_id) values (%s, %s, %s)",
                    (row["id"], teacher_id, student_id),
                )
        return {"classId": str(row["id"])}

    @app.post("/api/e2e/ocr-fail")
    async def e2e_ocr_fail(request: Request) -> dict[str, Any]:
        """Stands in for a failed provider call, so no worker and no OCR token are needed."""
        body = await request.json()
        with db.owner_pool.connection() as conn:
            conn.execute(
                """insert into ocr_jobs (asset_id, teacher_id, state, attempts, error_code)
                   select id, teacher_id, 'failed', 1, 'provider_error' from test_assets
                   where test_id = %s and kind = 'student_pdf'""",
                (body["testId"],),
            )
        return {"ok": True}

    @app.post("/api/e2e/run-worker")
    async def e2e_run_worker(request: Request) -> dict[str, Any]:
        """One worker pass. Text-layer PDFs need no OCR; the parser is a fixed reply unless `live`
        asks for the real Gemini call (only when GEMINI_API_KEY is set for this test server)."""
        import httpx

        from npa_api.exams.structure import StructureBody
        from npa_api.ocr.gemini import GeminiParser
        from npa_api.ocr.worker import run_once

        body = await request.json()

        from npa_api.ocr.provider import OcrPage, OcrStatus

        with db.owner_pool.connection() as conn:
            row = conn.execute(
                """select a.page_count from ocr_jobs j join test_assets a on a.id = j.asset_id
                   where j.state = 'queued' order by j.created_at limit 1"""
            ).fetchone()
        count = row["page_count"] if row else 1

        class BlankOcr:
            """Stands in for OCR on image-only PDFs: every page reads as empty text."""

            def submit(self, path: Any) -> str:
                return "e2e-job"

            def status(self, job_id: str) -> OcrStatus:
                return OcrStatus(job_id=job_id, state="done", result_url="https://e2e.invalid/r")

            def pages(self, status: OcrStatus) -> list[OcrPage]:
                return [OcrPage(page=n, text="", lines=[]) for n in range(1, count + 1)]

        def fixed(rows: Any) -> StructureBody:
            return StructureBody.model_validate(body["structure"], strict=False)

        with httpx.Client() as client:
            parser: Any = GeminiParser(os.environ["GEMINI_API_KEY"], client) if body.get("live") else fixed
            with db.owner_pool.connection() as conn:
                conn.execute("update ocr_jobs set lease_until = null")
            worked = run_once(db.app_pool, store, BlankOcr(), lambda: datetime.now(UTC), parser=parser)  # type: ignore[arg-type]
        return {"worked": worked}

    @app.post("/api/e2e/expire-attempts")
    def e2e_expire_attempts() -> dict[str, int]:
        """Test clock hook: moves every open attempt's deadline past the grace period.

        Lives only in this test server. The real API has no way to shorten a test.
        """
        with db.owner_pool.connection() as conn:
            cur = conn.execute(
                "update attempts set deadline_at = now() - interval '2 minutes' where status = 'in_progress'"
            )
            return {"expired": cur.rowcount}

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="warning")
finally:
    db.destroy()
