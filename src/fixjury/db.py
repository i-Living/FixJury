"""Async database plumbing (PLAN §5).

One engine per process, one transaction per unit of work. Nothing in
:mod:`fixjury.sandbox` or :mod:`fixjury.agents` may import this module: adapters
are stateless and the platform writes the rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fixjury.config import settings

type SessionMaker = async_sessionmaker[AsyncSession]


def make_engine(url: str | None = None) -> AsyncEngine:
    """Build the engine for ``url`` (defaults to ``FIXJURY_DATABASE_URL``)."""

    return create_async_engine(url or settings.database_url, pool_pre_ping=True)


def make_session_maker(engine: AsyncEngine) -> SessionMaker:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(maker: SessionMaker) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on any exception, always close."""

    session = maker()
    try:
        async with session.begin():
            yield session
    finally:
        await session.close()
