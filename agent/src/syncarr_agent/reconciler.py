"""Core reconcile() function — dependency-injected, no global state."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from syncarr_agent.aria2_client import Aria2Client, DownloadStatus
from syncarr_agent.client import AssignmentItem, ServerClient
from syncarr_agent.state import StateDB

if TYPE_CHECKING:
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _delete_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _delete_control_file(local_path: Path) -> None:
    """Delete the .aria2 control file alongside a download if present."""
    _delete_if_exists(local_path.with_suffix(".aria2"))


def _cleanup_empty_parents(path: Path, root: Path) -> None:
    """Remove empty parent dirs walking up from path, stopping before root."""
    current = path
    while current != root and current != current.parent:
        try:
            os.rmdir(current)
        except OSError:
            break
        current = current.parent


def reconcile(
    assignments: list[AssignmentItem],
    state: StateDB,
    aria2: Aria2Client,
    server: ServerClient,
    library_root: Path,
    server_token: str,
    log: structlog.stdlib.BoundLogger,
) -> None:
    for assignment in assignments:
        asset_id = assignment.asset_id
        local_path = library_root / assignment.relative_path
        asset_dir = local_path.parent
        bound = log.bind(asset_id=asset_id, filename=local_path.name)

        if assignment.state == "queued":
            # Asset not ready yet — nothing to do.
            continue

        elif assignment.state == "ready":
            _handle_ready(
                assignment=assignment,
                asset_id=asset_id,
                asset_dir=asset_dir,
                local_path=local_path,
                state=state,
                aria2=aria2,
                server=server,
                server_token=server_token,
                log=bound,
            )

        elif assignment.state == "evict":
            _handle_evict(
                asset_id=asset_id,
                asset_dir=asset_dir,
                local_path=local_path,
                library_root=library_root,
                state=state,
                aria2=aria2,
                server=server,
                log=bound,
            )


def _handle_ready(
    *,
    assignment: AssignmentItem,
    asset_id: int,
    asset_dir: Path,
    local_path: Path,
    state: StateDB,
    aria2: Aria2Client,
    server: ServerClient,
    server_token: str,
    log: structlog.stdlib.BoundLogger,
) -> None:
    record = state.get(asset_id)

    if record is not None:
        if record.status == "failed":
            log.warning("agent.download_failed_skip", note="operator must clear state.db and free disk")
            return

        # status == 'active': check aria2
        info = aria2.get_status(record.gid)
        if info.status in (DownloadStatus.ACTIVE, DownloadStatus.WAITING):
            # In-progress — nothing to do.
            return

        if info.status == DownloadStatus.COMPLETE:
            _confirm_or_requeue(
                assignment=assignment,
                asset_id=asset_id,
                asset_dir=asset_dir,
                local_path=local_path,
                state=state,
                aria2=aria2,
                server=server,
                server_token=server_token,
                log=log,
            )
            return

        if info.status == DownloadStatus.ERROR:
            log.warning("agent.download_aria2_error", gid=record.gid)
            state.set_failed(asset_id)
            return

        # OTHER (paused / removed) — stale entry; delete so we re-queue next poll.
        log.warning("agent.download_stale_gid", gid=record.gid, status=info.status)
        state.delete(asset_id)
        return

    # No state record — crash-recovery check.
    if local_path.exists():
        actual_sha = _sha256_file(local_path)
        # sha256=None means passthrough (no server-side hash); skip local verification.
        if assignment.sha256 is None or actual_sha == assignment.sha256:
            log.info("agent.crash_recovery_confirm", asset_id=asset_id)
            ok = server.confirm_delivered(
                asset_id,
                actual_sha,
                assignment.size_bytes or 0,
            )
            if ok:
                return
            log.warning("agent.confirm_mismatch_on_recovery", asset_id=asset_id)
            _delete_if_exists(local_path)
            _delete_control_file(local_path)
        else:
            log.warning("agent.crash_recovery_corrupt", asset_id=asset_id)
            _delete_if_exists(local_path)
            _delete_control_file(local_path)

    # Queue the download.
    assert assignment.download_url is not None, "ready assignment must have download_url"
    asset_dir.mkdir(parents=True, exist_ok=True)
    gid = aria2.add_download(
        url=assignment.download_url,
        filename=local_path.name,
        directory=asset_dir,
        sha256=assignment.sha256,
        auth_token=server_token,
    )
    state.upsert(asset_id, gid, local_path, status="active")
    log.info("agent.download_queued", asset_id=asset_id, gid=gid)


def _confirm_or_requeue(
    *,
    assignment: AssignmentItem,
    asset_id: int,
    asset_dir: Path,
    local_path: Path,
    state: StateDB,
    aria2: Aria2Client,
    server: ServerClient,
    server_token: str,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Called when aria2 reports COMPLETE. Verify sha256 (if provided) and confirm or re-queue."""
    actual_sha = _sha256_file(local_path)
    # sha256=None means passthrough; skip local sha256 verification.
    sha256_ok = assignment.sha256 is None or actual_sha == assignment.sha256
    if sha256_ok:
        ok = server.confirm_delivered(
            asset_id,
            actual_sha,
            assignment.size_bytes or 0,
        )
        if ok:
            state.delete(asset_id)
            log.info("agent.confirm_delivered", asset_id=asset_id)
        else:
            log.warning("agent.confirm_mismatch", asset_id=asset_id)
            _delete_if_exists(local_path)
            _delete_control_file(local_path)
            state.delete(asset_id)
    else:
        log.warning("agent.sha256_mismatch_local", asset_id=asset_id)
        _delete_if_exists(local_path)
        _delete_control_file(local_path)
        state.delete(asset_id)
        assert assignment.download_url is not None
        asset_dir.mkdir(parents=True, exist_ok=True)
        gid = aria2.add_download(
            url=assignment.download_url,
            filename=local_path.name,
            directory=asset_dir,
            sha256=assignment.sha256,
            auth_token=server_token,
        )
        state.upsert(asset_id, gid, local_path, status="active")
        log.info("agent.download_requeued", asset_id=asset_id, gid=gid)


def _handle_evict(
    *,
    asset_id: int,
    asset_dir: Path,
    local_path: Path,
    library_root: Path,
    state: StateDB,
    aria2: Aria2Client,
    server: ServerClient,
    log: structlog.stdlib.BoundLogger,
) -> None:
    record = state.get(asset_id)
    if record is not None:
        try:
            aria2.remove(record.gid)
        except Exception as exc:
            log.error("agent.evict_aria2_remove_failed", gid=record.gid, error=str(exc))
            # Safety: do NOT confirm eviction if removal failed — aria2 may still be writing.
            return

    # Delete local file and control file, then clean up empty parent dirs.
    _delete_if_exists(local_path)
    _delete_control_file(local_path)
    _cleanup_empty_parents(asset_dir, library_root)

    server.confirm_evicted(asset_id)
    state.delete(asset_id)
    log.info("agent.confirm_evicted", asset_id=asset_id)
