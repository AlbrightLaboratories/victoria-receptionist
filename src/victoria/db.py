"""Async Postgres connection pool.

We use asyncpg directly (not SQLAlchemy) for two reasons:
1. The schema is tiny — three tables.
2. pgvector parameters round-trip cleanly as native arrays without an ORM.

The pool is created during FastAPI lifespan and closed on shutdown.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from .config import Settings

log = logging.getLogger(__name__)


class Database:
    """Lifetime owner of the asyncpg pool."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Open the pool. Idempotent."""
        if self._pool is not None:
            return
        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._settings.database_url,
                min_size=1,
                max_size=10,
                command_timeout=30,
            )
            log.info("database pool opened")
        except Exception as e:  # noqa: BLE001
            # In local mode we can survive without a DB by no-op'ing
            # persistence. Production deploys should set MODE=production
            # and have a live DB.
            log.warning("database pool failed to open: %s", e)
            self._pool = None

    async def close(self) -> None:
        """Drain and close the pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def available(self) -> bool:
        """True if persistence is wired up."""
        return self._pool is not None

    async def execute(self, query: str, *args: Any) -> str:
        """Run a write query. Returns the asyncpg status string."""
        if self._pool is None:
            return "NO-DB"
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Run a read query. Returns rows (empty list if DB offline)."""
        if self._pool is None:
            return []
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Run a single-row read."""
        if self._pool is None:
            return None
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)
