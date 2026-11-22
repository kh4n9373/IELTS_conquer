"""Test-only API: fresh database, one admin, and mail written to a file the test can read."""

import os
from pathlib import Path

import uvicorn

from npa_api.app import build_app
from npa_api.core.mailer import Mail
from npa_api.core.security import hash_password
from npa_api.testing import create_test_db

MAIL_FILE = Path(os.environ["E2E_MAIL_FILE"])


class FileMailer:
    def send(self, mail: Mail) -> None:
        with MAIL_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{mail.text}\n")


MAIL_FILE.write_text("", encoding="utf-8")
db = create_test_db()
try:
    db.insert_user("admin@test.local", "admin", password_hash=hash_password("admin-password-123"))
    app = build_app(
        pool=db.app_pool,
        mailer=FileMailer(),
        public_url=os.environ["PUBLIC_URL"],
        secure_cookies=False,
        rate_limit=False,
    )
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="warning")
finally:
    db.destroy()
