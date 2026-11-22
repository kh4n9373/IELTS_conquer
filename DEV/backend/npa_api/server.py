import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .app import build_app
from .core.db import open_pool
from .core.mailer import ConsoleMailer


def is_production() -> bool:
    return os.environ.get("APP_ENV") == "production"


def create_app() -> FastAPI:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required (connect as npa_app)")
    logging.basicConfig(level=logging.INFO)
    pool = open_pool(database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        pool.close()

    return build_app(
        pool=pool,
        mailer=ConsoleMailer(),
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:5173"),
        secure_cookies=is_production(),
        lifespan=lifespan,
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
