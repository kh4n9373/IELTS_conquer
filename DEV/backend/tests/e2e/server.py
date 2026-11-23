"""Test-only API: fresh database, one admin, and mail written to a file the test can read."""

import hashlib
import os
import sys
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

    @app.post("/api/e2e/ocr-fail")
    async def e2e_ocr_fail(request: Request) -> dict[str, Any]:
        """Stands in for a failed provider call, so no worker and no OCR token are needed."""
        body = await request.json()
        with db.owner_pool.connection() as conn:
            conn.execute(
                """update ocr_jobs set state = 'failed', error_code = 'provider_error'
                   where asset_id in (select id from test_assets
                                      where test_id = %s and kind = 'student_pdf')""",
                (body["testId"],),
            )
        return {"ok": True}

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="warning")
finally:
    db.destroy()
