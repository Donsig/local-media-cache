"""Core reconcile() function — dependency-injected, no global state."""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
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
    with suppress(FileNotFoundError):
        path.unlink()


def _delete_control_file(local_path: Path) -> None:
    """Delete the .aria2 control file alongside a download if present."""
    _delete_if_exists(local_path.parent / (local_path.name + ".aria2"))


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

    if server.transfer_mode == "stopped":
        for record in state.all():
            if record.status != "active":
                continue
            try:
                aria2.remove(record.gid)
            except Exception as exc:
                log.error(
                    "agent.stop_aria2_remove_failed",
                    asset_id=record.asset_id,
                    gid=record.gid,
                    error=str(exc),
                )
                continue
            _delete_control_file(record.local_path)
            state.delete(record.asset_id)
            log.info(
                "agent.stop_cancelled_download",
                asset_id=record.asset_id,
                gid=record.gid,
            )


def run_reconcile(
    state: StateDB,
    server: ServerClient,
    library_root: Path,
    log: structlog.stdlib.BoundLogger,
) -> None:
    delivered = state.all_delivered()
    assets_present = [record.asset_id for record in delivered if record.local_path.is_file()]
    result = server.reconcile(assets_present)

    for asset_id in result.orphans_to_delete:
        record = state.get(asset_id)
        if record is not None:
            _delete_if_exists(record.local_path)
            _delete_control_file(record.local_path)
            state.delete(asset_id)
        log.info("agent.reconcile_orphan_deleted", asset_id=asset_id)

    for asset_id in result.missing_to_redownload:
        state.delete(asset_id)
        log.info("agent.reconcile_missing_cleared", asset_id=asset_id)

    log.info(
        "agent.reconcile_done",
        assets_present=len(assets_present),
        orphans=len(result.orphans_to_delete),
        missing=len(result.missing_to_redownload),
        library_root=str(library_root),
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
        if record.status == "delivered":
            if local_path.is_file():
                actual_sha = _sha256_file(local_path) if assignment.sha256 is not None else ""
                local_size = local_path.stat().st_size
                sha_ok = assignment.sha256 is None or actual_sha == assignment.sha256
                size_ok = assignment.size_bytes is None or local_size == assignment.size_bytes
                if sha_ok and size_ok:
                    server.confirm_delivered(asset_id, actual_sha, local_size)
                    return
                _delete_if_exists(local_path)
                _delete_control_file(local_path)
            state.delete(asset_id)
        else:
            if record.status == "failed":
                # Transient failure. Clear state and let crash-recovery on the next
                # poll decide: confirm if complete, re-queue otherwise.
                _delete_control_file(local_path)
                state.delete(asset_id)
                return

            # status == 'active': check aria2
            info = aria2.get_status(record.gid)
            if info.status in (DownloadStatus.ACTIVE, DownloadStatus.WAITING):
                # Report bytes downloaded so far. completed_length=0 while in
                # aria2's WAITING queue — the server uses this as an
                # acknowledgement that the agent has accepted the assignment.
                server.report_progress(asset_id, info.completed_length)
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
                # Delete .aria2 control file so crash-recovery sees a clean file.
                # Next poll: confirm if complete, re-queue if partial or missing.
                _delete_control_file(local_path)
                state.delete(asset_id)
                return

            # OTHER (paused / removed) — stale entry; delete so we re-queue next poll.
            log.warning("agent.download_stale_gid", gid=record.gid, status=info.status)
            state.delete(asset_id)
            return

    # No state record — crash-recovery check.
    if local_path.exists():
        # For passthrough (sha256=None), verify size before the expensive sha256 hash.
        if (
            assignment.sha256 is None
            and assignment.size_bytes is not None
            and local_path.stat().st_size != assignment.size_bytes
        ):
            log.warning("agent.crash_recovery_size_mismatch", asset_id=asset_id)
            log.warning("agent.deleting_file", reason="size_mismatch")
            _delete_if_exists(local_path)
            _delete_control_file(local_path)
            return  # re-queue next poll
        actual_sha = _sha256_file(local_path) if assignment.sha256 is not None else ""
        local_size = local_path.stat().st_size
        # sha256=None means passthrough (no server-side hash); skip local verification.
        if assignment.sha256 is None or actual_sha == assignment.sha256:
            log.info("agent.crash_recovery_confirm", asset_id=asset_id)
            ok = server.confirm_delivered(
                asset_id,
                actual_sha,
                local_size,
            )
            if ok:
                state.upsert(
                    asset_id,
                    gid="crash-recovery",
                    local_path=local_path,
                    status="delivered",
                )
                return
            log.warning("agent.confirm_mismatch_on_recovery", asset_id=asset_id)
            log.warning("agent.deleting_file", reason="confirm_mismatch")
            _delete_if_exists(local_path)
            _delete_control_file(local_path)
        else:
            log.warning("agent.crash_recovery_corrupt", asset_id=asset_id)
            log.warning("agent.deleting_file", reason="corrupt")
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
    if not local_path.exists():
        log.warning("agent.complete_but_missing", asset_id=asset_id)
        state.delete(asset_id)
        return
    actual_sha = _sha256_file(local_path) if assignment.sha256 is not None else ""
    local_size = local_path.stat().st_size
    # sha256=None means passthrough; skip local sha256 verification.
    sha256_ok = assignment.sha256 is None or actual_sha == assignment.sha256
    if sha256_ok:
        ok = server.confirm_delivered(
            asset_id,
            actual_sha,
            local_size,
        )
        if ok:
            state.set_delivered(asset_id)
            log.info("agent.confirm_delivered", asset_id=asset_id)
        else:
            log.warning("agent.confirm_mismatch", asset_id=asset_id)
            log.warning("agent.deleting_file", reason="confirm_mismatch")
            _delete_if_exists(local_path)
            _delete_control_file(local_path)
            state.delete(asset_id)
    else:
        log.warning("agent.sha256_mismatch_local", asset_id=asset_id)
        log.warning("agent.deleting_file", reason="sha256_mismatch")
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
    if record is not None and record.status != "delivered":
        try:
            aria2.remove(record.gid)
        except Exception as exc:
            log.error("agent.evict_aria2_remove_failed", gid=record.gid, error=str(exc))
            # Safety: do NOT confirm eviction if removal failed — aria2 may still be writing.
            return

    # Delete local file and control file, then clean up empty parent dirs.
    log.warning("agent.deleting_file", reason="evict")
    _delete_if_exists(local_path)
    _delete_control_file(local_path)
    _cleanup_empty_parents(asset_dir, library_root)

    server.confirm_evicted(asset_id)
    state.delete(asset_id)
    log.info("agent.confirm_evicted", asset_id=asset_id)
