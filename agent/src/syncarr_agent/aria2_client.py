"""aria2p wrapper — add/status/remove downloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import aria2p


class DownloadStatus(str, Enum):
    ACTIVE = "active"
    WAITING = "waiting"  # queued behind max-concurrent-downloads=1 — treat as in-progress
    COMPLETE = "complete"
    ERROR = "error"
    OTHER = "other"  # paused / removed


@dataclass
class DownloadInfo:
    gid: str
    status: DownloadStatus
    completed_length: int
    total_length: int


_NOT_FOUND_PHRASES = (
    "not found",
    "is not found",
    "gid#",  # e.g. "GID#xxx is not found"
    "removed",
)


def _is_not_found(exc: aria2p.ClientException) -> bool:
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _NOT_FOUND_PHRASES)


class Aria2Client:
    def __init__(self, host: str, port: int, secret: str) -> None:
        rpc_client = aria2p.Client(
            host=host,
            port=port,
            secret=secret or None,
        )
        self._api = aria2p.API(rpc_client)

    def add_download(
        self,
        url: str,
        filename: str,
        directory: Path,
        sha256: str,
        auth_token: str,
    ) -> str:
        """Add a download to aria2 and return the gid."""
        options = {
            "header": f"Authorization: Bearer {auth_token}",
            "out": filename,
            "dir": str(directory),
            "checksum": f"sha-256={sha256}",
            "auto-file-renaming": "false",
        }
        downloads = self._api.add_uris([url], options=options)
        return str(downloads[0].gid)

    def get_status(self, gid: str) -> DownloadInfo:
        dl = self._api.get_download(gid)
        raw = dl.status
        if raw == "complete":
            status = DownloadStatus.COMPLETE
        elif raw == "error":
            status = DownloadStatus.ERROR
        elif raw == "active":
            status = DownloadStatus.ACTIVE
        elif raw == "waiting":
            status = DownloadStatus.WAITING
        else:
            # paused, removed, etc.
            status = DownloadStatus.OTHER
        return DownloadInfo(
            gid=gid,
            status=status,
            completed_length=int(dl.completed_length),
            total_length=int(dl.total_length),
        )

    def remove(self, gid: str) -> None:
        """Remove a download from aria2.

        Suppresses 'not found' / 'already removed' errors.
        Re-raises on RPC connection failures — caller must not confirm eviction
        if removal failed.
        """
        try:
            dl = self._api.get_download(gid)
            dl.remove(force=True)
        except aria2p.ClientException as exc:
            if _is_not_found(exc):
                return
            raise
