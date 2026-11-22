import os
import sys

import psycopg

from .core.db import migrate
from .core.http import normalize_email
from .core.security import hash_password


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is required")
    return value


def migrate_command() -> None:
    migrate(_required("DATABASE_OWNER_URL"))
    print("Migrations applied")


def create_admin_command() -> None:
    # The password comes from the environment so it never lands in shell history.
    url = _required("DATABASE_OWNER_URL")
    password = _required("ADMIN_PASSWORD")
    if not 12 <= len(password) <= 200:
        sys.exit("ADMIN_PASSWORD must be 12 to 200 characters")
    try:
        email = normalize_email(_required("ADMIN_EMAIL"))
    except ValueError:
        sys.exit("ADMIN_EMAIL is not a valid email")
    with psycopg.connect(url) as conn:
        conn.execute(
            "insert into users (email, password_hash, display_name, role) values (%s, %s, 'Admin', 'admin')",
            (email, hash_password(password)),
        )
    print(f"Admin created: {email}")
