import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .app import build_app
from .core.db import open_pool
from .core.google import GoogleVerifier, TokenInfoVerifier
from .core.mailer import ConsoleMailer
from .core.storage import FileStore, S3FileStore
from .grading.settle import SweepThread


def is_production() -> bool:
    return os.environ.get("APP_ENV") == "production"


S3_NAMES = ("S3_ENDPOINT", "S3_PUBLIC_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")


def create_store() -> FileStore | None:
    """Storage is optional: without all five settings the API starts and uploads return 503."""
    values = [os.environ.get(name, "") for name in S3_NAMES]
    if not all(values):
        return None
    endpoint, public_endpoint, bucket, access_key, secret_key = values
    return S3FileStore(
        endpoint=endpoint,
        public_endpoint=public_endpoint,
        bucket=bucket,
        access_key=access_key,
        secret_key=secret_key,
    )


def create_google() -> GoogleVerifier | None:
    """Optional: without GOOGLE_CLIENT_ID the Google button is hidden and its routes return 503."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    return TokenInfoVerifier(client_id) if client_id else None


def create_app() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required (connect as npa_app)")
    logging.basicConfig(level=logging.INFO)
    pool = open_pool(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Started here, not in build_app, so tests that build the app never get a background thread.
        sweeper = SweepThread(app.state.ctx)
        sweeper.start()
        try:
            yield
        finally:
            sweeper.stop()
            pool.close()

    return build_app(
        pool=pool,
        mailer=ConsoleMailer(),
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:5173"),
        secure_cookies=is_production(),
        lifespan=lifespan,
        store=create_store(),
        google=create_google(),
    )


def main() -> None:
    production = is_production()
    uvicorn.run(
        "npa_api.server:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "3000")),
        reload=os.environ.get("RELOAD") == "1",
        # Forwarded headers are trusted only from FORWARDED_ALLOW_IPS (default 127.0.0.1).
        proxy_headers=production,
        server_header=False,
    )
