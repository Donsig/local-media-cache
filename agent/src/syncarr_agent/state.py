"""Local SQLite state: tracks asset_id -> aria2 gid + local path + status."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class DownloadRecord:
    asset_id: int
    gid: str
    local_path: Path
    status: str  # 'active' | 'failed' | 'delivered'
    started_at: str  # ISO 8601


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS downloads (
    asset_id   INTEGER PRIMARY KEY,
    gid        TEXT NOT NULL,
    local_path TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    started_at TEXT NOT NULL
);
"""


class StateDB:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        conn = self._connect()
        conn.execute(_CREATE_TABLE)
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, asset_id: int) -> DownloadRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT asset_id, gid, local_path, status, started_at
                FROM downloads
                WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                return None
            return DownloadRecord(
                asset_id=int(row["asset_id"]),
                gid=row["gid"],
                local_path=Path(row["local_path"]),
                status=row["status"],
                started_at=row["started_at"],
            )
        finally:
            conn.close()

    def upsert(
        self,
        asset_id: int,
        gid: str,
        local_path: Path,
        status: str = "active",
    ) -> None:
        started_at = datetime.now(tz=UTC).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO downloads (asset_id, gid, local_path, status, started_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    gid        = excluded.gid,
                    local_path = excluded.local_path,
                    status     = excluded.status,
                    started_at = excluded.started_at
                """,
                (asset_id, gid, str(local_path), status, started_at),
            )
            conn.commit()
        finally:
            conn.close()

    def set_failed(self, asset_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE downloads SET status = 'failed' WHERE asset_id = ?",
                (asset_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def set_delivered(self, asset_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE downloads SET status = 'delivered' WHERE asset_id = ?",
                (asset_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, asset_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM downloads WHERE asset_id = ?", (asset_id,))
            conn.commit()
        finally:
            conn.close()

    def all(self) -> list[DownloadRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT asset_id, gid, local_path, status, started_at FROM downloads"
            ).fetchall()
            return [
                DownloadRecord(
                    asset_id=int(row["asset_id"]),
                    gid=row["gid"],
                    local_path=Path(row["local_path"]),
                    status=row["status"],
                    started_at=row["started_at"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def all_delivered(self) -> list[DownloadRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT asset_id, gid, local_path, status, started_at
                FROM downloads
                WHERE status = 'delivered'
                """
            ).fetchall()
            return [
                DownloadRecord(
                    asset_id=int(row["asset_id"]),
                    gid=row["gid"],
                    local_path=Path(row["local_path"]),
                    status=row["status"],
                    started_at=row["started_at"],
                )
                for row in rows
            ]
        finally:
            conn.close()
