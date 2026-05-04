from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from syncarr_server.config import get_settings

_settings = get_settings()
engine = create_async_engine(_settings.database_url, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_wal_mode(dbapi_conn: sqlite3.Connection, _connection_record: object) -> None:
    dbapi_conn.execute("PRAGMA journal_mode=WAL")


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
