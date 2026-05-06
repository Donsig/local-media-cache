from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from syncarr_server.config import get_settings

_settings = get_settings()
# NullPool: no connection pooling. For SQLite + aiosqlite this is correct — connections are
# cheap, WAL handles concurrency within SQLite's limits, and pooling only adds exhaustion risk.
engine = create_async_engine(_settings.database_url, echo=False, poolclass=NullPool)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn: sqlite3.Connection, _connection_record: object) -> None:
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    # busy_timeout: how long SQLite waits for a write lock before raising "database is locked".
    # 30 s gives background workers time to finish their commits under normal load.
    dbapi_conn.execute("PRAGMA busy_timeout=30000")


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
