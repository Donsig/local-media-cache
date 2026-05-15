"""HTTP client for the syncarr server API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class AssignmentItem:
    asset_id: int
    state: str  # 'ready' | 'queued' | 'evict'
    source_media_id: str
    relative_path: str  # POSIX path relative to library root, mirrors server library structure
    sha256: str | None  # only when state='ready'
    size_bytes: int | None  # only when state='ready'
    download_url: str | None  # absolute URL — normalised in get_assignments()


@dataclass
class AssignmentsStats:
    total_assigned_bytes: int
    ready_count: int
    queued_count: int
    evict_count: int


@dataclass
class AssignmentsResponse:
    client_id: str
    server_time: str  # ISO 8601
    assignments: list[AssignmentItem] = field(default_factory=list)
    stats: AssignmentsStats = field(
        default_factory=lambda: AssignmentsStats(0, 0, 0, 0)
    )
    transfer_mode: str = "running"


@dataclass
class ReconcileResponse:
    orphans_to_delete: list[int]
    missing_to_redownload: list[int]


class ServerClient:
    def __init__(
        self,
        server_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self.transfer_mode = "running"
        kwargs: dict[str, Any] = {
            "base_url": self._server_url,
            "headers": {"Authorization": f"Bearer {token}"},
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def get_assignments(self) -> AssignmentsResponse:
        resp = self._client.get("/assignments")
        resp.raise_for_status()
        data = resp.json()

        raw_stats = data.get("stats", {})
        stats = AssignmentsStats(
            total_assigned_bytes=int(raw_stats.get("total_assigned_bytes", 0)),
            ready_count=int(raw_stats.get("ready_count", 0)),
            queued_count=int(raw_stats.get("queued_count", 0)),
            evict_count=int(raw_stats.get("evict_count", 0)),
        )
        self.transfer_mode = str(data.get("transfer_mode", "running"))

        assignments: list[AssignmentItem] = []
        for raw in data.get("assignments", []):
            raw_url: str | None = raw.get("download_url")
            absolute_url: str | None = None
            if raw_url is not None:
                absolute_url = f"{self._server_url}{raw_url}"
            rel_path = str(raw["relative_path"])
            _p = Path(rel_path)
            if _p.is_absolute() or ".." in _p.parts:
                raise ValueError(f"Unsafe relative_path in assignment: {rel_path!r}")
            assignments.append(
                AssignmentItem(
                    asset_id=int(raw["asset_id"]),
                    state=raw["state"],
                    source_media_id=str(raw.get("source_media_id", "")),
                    relative_path=rel_path,
                    sha256=raw.get("sha256"),
                    size_bytes=raw.get("size_bytes"),
                    download_url=absolute_url,
                )
            )

        return AssignmentsResponse(
            client_id=data["client_id"],
            server_time=data["server_time"],
            assignments=assignments,
            stats=stats,
            transfer_mode=self.transfer_mode,
        )

    def confirm_delivered(
        self,
        asset_id: int,
        sha256: str,
        size_bytes: int,
    ) -> bool:
        """Return True on ok=true, False on checksum mismatch."""
        resp = self._client.post(
            f"/confirm/{asset_id}",
            json={
                "state": "delivered",
                "actual_sha256": sha256,
                "actual_size_bytes": size_bytes,
            },
        )
        resp.raise_for_status()
        return bool(resp.json().get("ok", False))

    def confirm_evicted(self, asset_id: int) -> None:
        """Confirm eviction. 404 treated as success (idempotent)."""
        resp = self._client.post(
            f"/confirm/{asset_id}",
            json={"state": "evicted"},
        )
        if resp.status_code == 404:
            return
        resp.raise_for_status()

    def report_progress(self, asset_id: int, bytes_downloaded: int) -> None:
        """Report bytes downloaded so far. Best-effort — ignores non-fatal errors."""
        try:
            resp = self._client.patch(
                f"/assignments/{asset_id}/progress",
                json={"bytes_downloaded": bytes_downloaded},
            )
            resp.raise_for_status()
        except Exception:
            # Progress reporting is best-effort; never let it abort the poll loop.
            pass

    def reconcile(self, assets_present: list[int]) -> ReconcileResponse:
        resp = self._client.post("/reconcile", json={"assets_present": assets_present})
        resp.raise_for_status()
        data = resp.json()
        return ReconcileResponse(
            orphans_to_delete=data.get("orphans_to_delete", []),
            missing_to_redownload=data.get("missing_to_redownload", []),
        )
