"""Shared test fixtures and mock implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from syncarr_agent.aria2_client import DownloadInfo, DownloadStatus
from syncarr_agent.client import AssignmentItem, AssignmentsResponse, AssignmentsStats
from syncarr_agent.state import DownloadRecord


# ---------------------------------------------------------------------------
# Mock ServerClient
# ---------------------------------------------------------------------------


class MockServerClient:
    """Configurable mock; records all confirm calls."""

    def __init__(
        self,
        assignments_response: AssignmentsResponse | None = None,
        confirm_delivered_result: bool = True,
    ) -> None:
        self._assignments_response = assignments_response or AssignmentsResponse(
            client_id="test",
            server_time="2026-01-01T00:00:00Z",
            assignments=[],
            stats=AssignmentsStats(0, 0, 0, 0),
        )
        self._confirm_delivered_result = confirm_delivered_result
        self.delivered_confirms: list[dict[str, Any]] = []
        self.evicted_confirms: list[int] = []

    def get_assignments(self) -> AssignmentsResponse:
        return self._assignments_response

    def confirm_delivered(self, asset_id: int, sha256: str, size_bytes: int) -> bool:
        self.delivered_confirms.append(
            {"asset_id": asset_id, "sha256": sha256, "size_bytes": size_bytes}
        )
        return self._confirm_delivered_result

    def confirm_evicted(self, asset_id: int) -> None:
        self.evicted_confirms.append(asset_id)


# ---------------------------------------------------------------------------
# Mock Aria2Client
# ---------------------------------------------------------------------------


@dataclass
class MockAria2Client:
    """Tracks add_download calls; returns configurable DownloadInfo per gid."""

    _statuses: dict[str, DownloadInfo] = field(default_factory=dict)
    _add_calls: list[dict[str, Any]] = field(default_factory=list)
    _next_gid: str = "gid001"
    _remove_calls: list[str] = field(default_factory=list)
    _remove_raises: Exception | None = None

    def set_status(self, gid: str, status: DownloadStatus) -> None:
        self._statuses[gid] = DownloadInfo(
            gid=gid,
            status=status,
            completed_length=1024,
            total_length=1024,
        )

    def add_download(
        self,
        url: str,
        filename: str,
        directory: Path,
        sha256: str,
        auth_token: str,
    ) -> str:
        gid = self._next_gid
        self._add_calls.append(
            {
                "url": url,
                "filename": filename,
                "directory": directory,
                "sha256": sha256,
                "auth_token": auth_token,
                "gid": gid,
            }
        )
        return gid

    def get_status(self, gid: str) -> DownloadInfo:
        return self._statuses[gid]

    def remove(self, gid: str) -> None:
        if self._remove_raises is not None:
            raise self._remove_raises
        self._remove_calls.append(gid)


# ---------------------------------------------------------------------------
# Mock StateDB
# ---------------------------------------------------------------------------


class MockStateDB:
    """Dict-backed; no SQLite. Supports status field."""

    def __init__(self) -> None:
        self._records: dict[int, DownloadRecord] = {}
        self._failed: set[int] = set()
        self.deleted: list[int] = []
        self.upserted: list[dict[str, Any]] = []
        self.set_failed_calls: list[int] = []

    def get(self, asset_id: int) -> DownloadRecord | None:
        return self._records.get(asset_id)

    def upsert(
        self,
        asset_id: int,
        gid: str,
        local_path: Path,
        status: str = "active",
    ) -> None:
        self.upserted.append(
            {"asset_id": asset_id, "gid": gid, "local_path": local_path, "status": status}
        )
        self._records[asset_id] = DownloadRecord(
            asset_id=asset_id,
            gid=gid,
            local_path=local_path,
            status=status,
            started_at="2026-01-01T00:00:00+00:00",
        )

    def set_failed(self, asset_id: int) -> None:
        self.set_failed_calls.append(asset_id)
        if asset_id in self._records:
            rec = self._records[asset_id]
            self._records[asset_id] = DownloadRecord(
                asset_id=rec.asset_id,
                gid=rec.gid,
                local_path=rec.local_path,
                status="failed",
                started_at=rec.started_at,
            )

    def delete(self, asset_id: int) -> None:
        self.deleted.append(asset_id)
        self._records.pop(asset_id, None)

    def all(self) -> list[DownloadRecord]:
        return list(self._records.values())

    def add_record(self, record: DownloadRecord) -> None:
        """Helper: inject a record directly (test setup)."""
        self._records[record.asset_id] = record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_library_root(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    return root


@pytest.fixture
def mock_server() -> MockServerClient:
    return MockServerClient()


@pytest.fixture
def mock_aria2() -> MockAria2Client:
    return MockAria2Client()


@pytest.fixture
def mock_state() -> MockStateDB:
    return MockStateDB()
